"""Service layer for the editor backend — pure functions over the OkGen core.

Kept HTTP-free so it can be unit-tested directly. The FastAPI app in
``app.py`` is a thin wrapper over these.
"""

from __future__ import annotations

import errno
import os
import random
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from okgen.config import Config
from okgen import detect
from okgen.detect import detect_from_header, detect_layout, read_chain, read_header_line
from okgen.jsonsource import resolve_source, source_from_header
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import ENCODING, OkFile, Record, new_record, parse_okfile
from okgen import paths as fs

OK_SUFFIX = ".ok"  # compared case-insensitively


def is_ok_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == OK_SUFFIX


def _is_json_file(path: Path) -> bool:
    # Calgary layout specs (*.layout.json) live in the data dir, not the tree.
    return (path.is_file() and path.suffix.lower() == ".json"
            and not path.name.endswith(".layout.json"))


# --------------------------------------------------------------------------- #
# File tree
# --------------------------------------------------------------------------- #
def build_tree(root, config: Config, registry=None,
               source: Optional[str] = None) -> dict:
    """List ONE level of a folder (lazy tree): immediate subfolders + .OK files.

    Subfolders are returned unexpanded (``children: None``) so the UI can fetch
    them on demand. Only ``.OK`` files are listed; each file node carries its
    chain (for the icon), layout, key field/value, and a ``duplicate`` flag set
    when another same-layout file in this folder shares its key value.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"not a folder: {root}")

    children: List[dict] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        entries = []
    for entry in entries:
        if entry.is_dir():
            children.append({
                "type": "folder",
                "name": entry.name,
                "path": str(entry),
                "children": None,   # not loaded yet (lazy)
            })
        elif is_ok_file(entry):
            children.append(_file_node(entry, config, registry))
        elif _is_json_file(entry):
            node = _file_node(entry, config, registry, source)
            if node.get("layout"):        # only list JSON that detects as a layout
                children.append(node)

    _flag_duplicate_keys(children)
    return {
        "type": "folder",
        "name": root.name or str(root),
        "path": str(root),
        "children": children,
        # SCAN/WMS for this folder. Only present when it actually decides
        # something — i.e. the folder holds JSON files of a layout whose key
        # differs between sources. The client asks once when unresolved and
        # sends the answer back as `source`.
        "json_source": _folder_json_source(root, children, config, source),
    }


def _folder_json_source(root: Path, children: List[dict], config: Config,
                        source: Optional[str]) -> Optional[dict]:
    """The folder's SCAN/WMS resolution, or None when it doesn't matter here.

    A folder with no JSON files — or one holding only CalgaryCartonLabel, whose
    key is ``pickListId`` under both sources — is never asked about.
    """
    layouts = {c.get("layout") for c in children if c.get("json")}
    if not any(config.source_dependent(l) for l in layouts):
        return None
    # "_" has no suffix, so only the folder chain is examined, never a file
    # name — and ancestors count, so a parent named SCAN covers its subfolders.
    info = json_source_for(root / "_", config, source)
    info["layouts"] = sorted(l for l in layouts if config.source_dependent(l))
    if not info["resolved"]:
        # Don't ask about a folder whose FILES all name their own source — the
        # question would be pointless (nothing about the folder is used) and its
        # "being read as WMS" wording would be plain wrong.
        named = [json_source_for(c["path"], config, source) for c in children
                 if c.get("json") and config.source_dependent(c.get("layout"))]
        if named and all(n["reason"] == "file name" for n in named):
            info.update(resolved=True, reason="file names",
                        source=named[0]["source"] if len({n["source"] for n in named}) == 1
                        else "mixed")
    # Only second-guess a source we ASSUMED. Once a name or the user has said
    # so, trust it — a folder that keeps questioning a settled answer is noise.
    info["hint"] = (None if info["resolved"] else _source_mismatch_hint(
        children, {c.get("layout"): c.get("key_field") for c in children}))
    return info


def _flag_duplicate_keys(children: List[dict]) -> None:
    """Mark files whose (layout, key_value) repeats within this folder.

    Only the RESOLVED key is compared, deliberately. Comparing every candidate
    field as well would light up a warning on every file of a correct WMS
    folder, because WMS ships a constant ``keytrol`` (``'0'`` on real carton
    labels) that is not its identity and that Make Unique would never clear —
    a permanent alarm nobody can silence. The wrong-source case is caught
    instead by :func:`_source_mismatch_hint`, which says what to DO about it.
    """
    counts: Dict[tuple, int] = {}
    for c in children:
        if c.get("type") == "file" and c.get("layout") and c.get("key_value") is not None:
            k = (c["layout"], c["key_value"])
            counts[k] = counts.get(k, 0) + 1
    for c in children:
        if c.get("type") == "file" and c.get("layout") and c.get("key_value") is not None:
            c["duplicate"] = counts[(c["layout"], c["key_value"])] > 1


def _source_mismatch_hint(children: List[dict], resolved_field_by_layout: dict):
    """"These files share a <other key> — are they really SCAN?", or None.

    The safety net for a folder whose source was ASSUMED (no SCAN/WMS token in
    any name, no answer yet). If the key we are NOT using collides across files
    while the one we are using doesn't, the assumption is probably wrong — the
    files are likely from the other source, where that field IS the identity.
    Advisory only: one folder-level line, never a per-file warning, and nothing
    about how files are written.
    """
    by_field: Dict[tuple, int] = {}
    for c in children:
        if c.get("type") != "file" or not c.get("json"):
            continue
        for field, val in (c.get("key_values") or {}).items():
            if field != resolved_field_by_layout.get(c.get("layout")) and val is not None:
                by_field[(c["layout"], field, val)] = by_field.get((c["layout"], field, val), 0) + 1
    dupes = {(lay, f) for (lay, f, _v), n in by_field.items() if n > 1}
    if not dupes:
        return None
    lay, field = sorted(dupes)[0]
    return {"field": field,
            "message": (f"several files here share the same {field} — if these are "
                        f"from the other source, set it so keys are checked correctly")}


def _header_field(layout, name):
    """Header-section Field object by name, or None."""
    if layout is None or not layout.sections or not name:
        return None
    return next((f for f in layout.sections[0].fields if f.name == name), None)


def _key_from_header(header: str, layout, field) -> Optional[str]:
    """Slice a header field value from the raw header line (marker offset 1)."""
    if field is None or field.start is None or field.size is None:
        return None
    start = 1 + field.start - 1
    return header[start:start + field.size]


def _delim_header_value(header: str, layout, field_name) -> Optional[str]:
    """Value of a named header field from a delimited header line.

    Strips the BOM + broken-bar marker, then indexes the pipe-delimited tokens
    by the field's position in the header section.
    """
    if layout is None or not layout.sections or not field_name:
        return None
    fields = layout.sections[0].fields
    idx = next((i for i, f in enumerate(fields) if f.name == field_name), None)
    if idx is None:
        return None
    h = header
    if h.startswith("\xef\xbb\xbf"):       # UTF-8 BOM (read as Latin-1)
        h = h[3:]
    if h.startswith("\xc2\xa6"):           # ¦ marker (UTF-8)
        h = h[2:]
    elif h.startswith("\xa6"):             # ¦ marker (single Latin-1 byte)
        h = h[1:]
    h = h.rstrip("\r")
    if h.endswith("\\"):                    # record terminator
        h = h[:-1]
    toks = h.split("|")
    return toks[idx] if idx < len(toks) else None


def _json_header(path: Path) -> dict:
    """The flat ``data.header`` object of a Calgary JSON file (cheap, no parse)."""
    import json
    data = json.loads(fs.read_text(path, "utf-8")).get("data", {})
    return data.get("header", {}) or {}


def json_source_for(path, config: Config, override: Optional[str] = None,
                    root=None, header: Optional[dict] = None) -> dict:
    """Which source (SCAN/WMS) a Calgary JSON file came from.

    Read from the FILE'S OWN header: a populated ``headerASNid`` means WMS, an
    empty or absent one means SCAN (see :mod:`okgen.jsonsource`). ``header`` is
    accepted so callers that have already read it — the tree does, for every
    file — pay nothing extra.

    ``override`` still wins if a caller passes one, and a name-based match is
    the last resort before the default. Nothing in the UI sends either any
    more: the file answers for itself.
    """
    if override:
        return resolve_source(path, config.json_sources,
                              config.json_source_default, override, root).to_dict()
    if header is None:
        try:
            header = _json_header(path)
        except Exception:                       # noqa: BLE001 — unreadable file
            header = None
    if header is not None:
        return source_from_header(header, config.json_sources,
                                  config.json_source_default).to_dict()
    return resolve_source(path, config.json_sources, config.json_source_default,
                          override, root).to_dict()


def _source_name_for(path, layout: Optional[str], config: Config,
                     source: Optional[str] = None) -> Optional[str]:
    """The source a file resolved to, or None when the layout has none.

    Reported per re-keyed file so a bulk Make Unique over a MIXED folder can
    say which field it renumbered for which files — otherwise that is invisible
    (no file is open, so the editor's key row can't show it).

    Note this answers for EVERY Calgary JSON layout, including CartonLabel:
    the source is worth knowing there even though the key (``pickListId``) is
    the same either way.
    """
    if not config.has_source(layout):
        return None
    return json_source_for(path, config, source).get("source")


def _unique_field_for(path, layout: Optional[str], config: Config,
                      source: Optional[str] = None) -> Optional[str]:
    """The key field for ONE file, resolving SCAN/WMS where it matters.

    Only the Calgary JSON layouts key off the source; for every other layout
    (and for CalgaryCartonLabel, whose key is ``pickListId`` either way) this is
    exactly ``config.unique_field(layout)`` and the file is never read for it.
    """
    if not config.source_dependent(layout):
        return config.unique_field(layout)
    return config.unique_field(
        layout, json_source_for(path, config, source).get("source"))


def _editable_file(path: Path) -> bool:
    """A file OkGen can open and operate on — a fixed-width/delimited ``.OK``
    file OR a Calgary ``.json`` file.

    Copy, paste, delete and rename used to test ``is_ok_file`` alone, which
    silently excluded every JSON file. Send to NiceLabel still tests the two
    kinds separately — not because JSON is excluded, but because the hand-offs
    differ: ``.OK`` is copied to a hot folder, ``.json`` is POSTed to an
    endpoint (``_send_mode`` / ``okgen.nicelabel_post``).
    """
    return is_ok_file(path) or _is_json_file(path)


def _file_node(path: Path, config: Config, registry=None,
               source: Optional[str] = None) -> dict:
    chain = ""
    layout = None
    key_field = None
    key_value = None
    key_values: Dict[str, Optional[str]] = {}   # every candidate key (JSON only)
    is_json = False
    json_source = None          # SCAN / WMS, from the file's own headerASNid
    source_reason = None
    try:
        det = detect_layout(path)          # handles .OK (positional) and .json (data.type)
        layout = det.layout
        reg_layout = registry.get(layout) if (layout and registry is not None) else None
        if layout and _is_json_file(path):
            # JSON: chain/key are named keys in data.header, not byte slices.
            is_json = True
            header = _json_header(path)
            chain = (header.get("chain") or "").strip()
            # Free: the header is already in hand, and the source is one field
            # of it. Reported for EVERY Calgary layout — CartonLabel included,
            # where it is informational rather than key-changing.
            if config.has_source(layout):
                src_info = json_source_for(path, config, source, header=header)
                json_source = src_info.get("source")
                source_reason = src_info.get("reason")
            key_field = _unique_field_for(path, layout, config, source or json_source)
            if key_field:
                v = header.get(key_field)
                key_value = None if v is None else str(v).strip()
            # Every field that is a key under ANY source, so duplicate detection
            # can flag a collision even if this folder's source was answered
            # wrong (see _flag_duplicate_keys).
            for cand in config.unique_field_candidates(layout):
                cv = header.get(cand)
                key_values[cand] = None if cv is None else str(cv).strip()
        else:
            # An .OK format is emitted by exactly one system, so its source is
            # a property of the LAYOUT and costs no read (contrast the Calgary
            # layouts above, where only the payload can answer). Display only —
            # an .OK key is a single field and never depends on this.
            json_source = config.layout_source(layout)
            if json_source:
                source_reason = "the layout's own source"
            header = read_header_line(path)
            delimited = bool(reg_layout is not None and getattr(reg_layout, "delimited", False))
            # Delimited (EU) headers carry the chain in a token, not a fixed slice.
            chain = (_delim_header_value(header, reg_layout, "chain") if delimited
                     else header[1:3]) or ""
            if reg_layout is not None:
                key_field = config.unique_field(layout)
                if key_field:
                    key_value = (_delim_header_value(header, reg_layout, key_field) if delimited
                                 else _key_from_header(header, reg_layout,
                                                       _header_field(reg_layout, key_field)))
    except Exception:
        pass
    info = config.chain(chain)
    chain_info = info.to_dict() if info else None
    return {
        "type": "file",
        "name": path.name,
        "path": str(path),
        "chain": chain,
        "chain_info": chain_info,
        "layout": layout,
        "json": is_json,
        "source": json_source,
        "source_reason": source_reason,
        "key_field": key_field,
        "key_value": key_value,
        "key_values": key_values,
        "duplicate": False,
        # True when this layout declares a roll-up and the section it sums has
        # no rows — the header total is then the quantity itself. Shown as a
        # quiet marker, NOT a warning: in the new system an empty size section
        # is a normal file shape, and flagging it red would train people to
        # ignore the badge. None when the layout declares no roll-up.
        "no_rollup_rows": _rollup_section_empty(path, layout, config, registry),
    }


def _rollup_section_empty(path: Path, layout, config: Config, registry) -> Optional[bool]:
    """Does this file's rolled-up section have no real rows?

    Deliberately NOT a full parse: the tree renders every file in a folder, and
    `_file_node` otherwise reads only the header line. This reads the file only
    for a layout that actually declares a roll-up (StyleHeader today), and then
    only scans for lines carrying the section's marker — so the cost lands on
    the handful of layouts that need it rather than on every tree node.
    """
    if not layout or registry is None or config is None:
        return None
    specs = config.rollups(layout)
    if not specs:
        return None
    reg_layout = registry.get(layout)
    if reg_layout is None or getattr(reg_layout, "delimited", False):
        return None
    sec = next((s for s in reg_layout.sections
                if s.name == specs[0].get("section")), None)
    marker = getattr(sec, "marker", None) if sec else None
    if not marker:
        return None
    try:
        raw = fs.read_bytes(path)
    except OSError:
        return None
    mb = marker.encode(ENCODING, errors="ignore")
    for line in raw.split(b"\n"):
        if not line.startswith(mb):
            continue
        # Same test as _is_blank_row: zeros and spaces only is not a real row.
        body = line[len(mb):].rstrip(b"\r").rstrip(b"\\")
        if body.strip(b"0 ") != b"":
            return False
    return True


# --------------------------------------------------------------------------- #
# Parse a file into an editor view
# --------------------------------------------------------------------------- #
def parse_file_view(path, registry: LayoutRegistry, config: Config,
                    source: Optional[str] = None) -> dict:
    """The editor view of a file **as it is on disk**."""
    path = Path(path)
    okf = parse_okfile(path, registry=registry)
    return _build_file_view(okf, path, config, disk_bytes=fs.read_bytes(path),
                            source=source)


def _build_file_view(okf: OkFile, path, config: Config, disk_bytes: bytes = None,
                     source: Optional[str] = None) -> dict:
    """Render the editor view for a parsed file.

    ``disk_bytes`` is the on-disk content when the view reflects a saved file.
    Omit it to render an **unsaved, in-memory** file (a staged-op preview): the
    view is then built from ``okf.to_bytes()``, so the editor and the Raw verify
    tab show what a Save would write without anything having been written yet.
    """
    path = Path(path)
    layout_name = okf.layout.name
    chain = okf.records[0].get("chain") if okf.records else ""
    # NA layouts name the format-code field "format"; the EU GTA layouts call
    # it "process" (D/H). Fall back so the display context stays populated.
    fmt = _header_value(okf, "format") or _header_value(okf, "process")
    content = okf.to_bytes()
    roundtrip_ok = content == disk_bytes if disk_bytes is not None else True

    sections_out: List[dict] = []
    grouped = okf.sections()
    # Resolve each section's definition from the layout (not from its records)
    # so an empty section still renders its columns + a "None" state.
    layout_secs = {s.name: s for s in okf.layout.sections}
    for sec_index, (sec_name, recs) in enumerate(grouped.items()):
        sec = layout_secs.get(sec_name) or (recs[0].section if recs else None)
        field_meta = []
        if sec:
            hidden = config.hidden_fields(layout_name)
            readonly = config.readonly_fields(layout_name)
            for f in sec.fields:
                opts = config.options(f.name, chain=chain, layout=layout_name,
                                      fmt=fmt, section=sec.name)
                editable = f.name not in readonly and not getattr(f, "readonly", False)
                # Chain edits can't cross an isolation boundary (e.g. Europe):
                # offer only chains the current one may become; lock it when the
                # only option is itself.
                value_forms = None
                if f.name == "chain" and opts:
                    opts = {c: n for c, n in opts.items() if config.can_change_chain(chain, c)}
                    if len(opts) <= 1:
                        editable = False
                    # A chain may be written as a CODE (`04`) or as a brand NAME
                    # (`Winners`) — D41, and both are offered. Which form a file
                    # actually carries is invisible in the editor otherwise: the
                    # box just shows a value. Resolve each offered value once
                    # here, where `Config.chain()` is authoritative, so the
                    # client can label the current one instead of guessing from
                    # its shape.
                    # Only for a field the editor renders freeform: the badge
                    # is the sole consumer, and an `.OK` chain is a fixed-width
                    # 2-char code that can only ever BE a code. Emitting it
                    # there would add a key to every `.OK` descriptor that
                    # nothing reads.
                    if config.is_freeform(layout_name, f.name):
                        value_forms = {}
                        for key in opts:
                            info = config.chain(key)
                            value_forms[key] = ("code" if info and key == info.code
                                                else "name")
                field_meta.append({
                    "value_forms": value_forms,
                    "name": f.name,
                    "start": f.start,
                    "size": f.size,
                    "type": f.field_type,
                    "options": opts or None,
                    "hidden": f.name in hidden,
                    "editable": editable,
                    # Literal fields are shown padded exactly as stored, so the
                    # user can see and keep their spaces (the editor strips pad
                    # spaces from ordinary fields for comfortable typing).
                    "literal": config.is_literal(layout_name, f.name),
                    # Options are SUGGESTIONS, not the whole list: render a text
                    # box the user can type into. `type` needs this — its list
                    # is the one word this layout's documents carry, so a
                    # dropdown offered a single choice and no way to re-case it.
                    # Saving is still policed by _assert_layout_stable.
                    "freeform": config.is_freeform(layout_name, f.name),
                })
        # Derived (computed) fields: not in the raw file — inject their meta
        # (read-only, carrying the rules so the client can recompute live) and
        # their computed value into each record.
        derived_specs = [
            s for s in config.derived_fields(layout_name)
            if s.get("section", "Header") == sec_name
        ]
        for spec in derived_specs:
            meta = {
                "name": spec["name"],
                "start": None,
                "size": None,
                "type": "derived",
                "options": None,
                "hidden": False,
                "editable": False,
                "derived": True,
                "inputs": list(spec.get("inputs", [])),
                "rules": spec.get("rules", []),
                "default": spec.get("default", ""),
            }
            after = spec.get("after")
            pos = next((i for i, m in enumerate(field_meta) if m["name"] == after), None)
            if pos is None:
                field_meta.append(meta)
            else:
                field_meta.insert(pos + 1, meta)

        records_out = []
        for r in recs:
            vals = r.values()
            if derived_specs:
                vals = dict(vals)
                for spec in derived_specs:
                    vals[spec["name"]] = config.eval_derived(spec, vals)
            records_out.append({"index": r.index, "marker": r.marker, "values": vals})
        sections_out.append({
            "index": sec_index,
            "name": sec_name,
            "tab": sec.tab if sec else sec_name,
            "is_header": sec_index == 0,
            "record_length": sec.record_length if sec else None,
            "ignored_fields": sec.ignored_fields if sec else [],
            "max_records": config.max_records(layout_name, sec_name),
            "fields": field_meta,
            "records": records_out,
        })

    chain_info = config.chain(chain)
    chain_info_dict = chain_info.to_dict() if chain_info else None
    # Raw verify tab: NA files show the byte-exact Latin-1 view; delimited (EU)
    # files are UTF-8, so decode them as UTF-8 and drop the BOM — otherwise the
    # marker shows as Latin-1 mojibake ("ï»¿Â¦" instead of "¦"). The on-disk
    # bytes are untouched; this only affects what the verify tab displays.
    raw_bytes = content
    if getattr(okf.layout, "json_mode", False):
        # Raw tab is DISPLAY-ONLY: always pretty-print (indent) so a minified
        # file reads vertically instead of one long line. The file on disk keeps
        # its own formatting and still saves byte-exact — this reformats only the
        # view. Reflects staged edits (raw_bytes == okf.to_bytes()).
        import json as _json
        try:
            raw_text = _json.dumps(_json.loads(raw_bytes.decode("utf-8")),
                                   indent=2, ensure_ascii=False)
        except ValueError:
            raw_text = raw_bytes.decode("utf-8", errors="replace")
    elif getattr(okf.layout, "delimited", False):
        raw_text = raw_bytes.decode("utf-8-sig", errors="replace")
    else:
        raw_text = raw_bytes.decode(ENCODING)
    return {
        "path": str(path),
        "name": path.name,
        "layout": layout_name,
        "chain": chain,
        "format": fmt,
        "chain_info": chain_info_dict,
        "roundtrip_ok": roundtrip_ok,
        # >0 when opening this file removed junk that a Save (or the "Clean up
        # files" bulk action) will write out: blank junk lines dropped, and/or
        # lines whose padding AFTER the terminator was trimmed. Field data is
        # never touched, so the editor already shows the clean result.
        "blank_lines_removed": getattr(okf, "blank_lines_removed", 0),
        "lines_space_trimmed": getattr(okf, "lines_space_trimmed", 0),
        # Unique field for this layout — SCAN/WMS resolved for the JSON layouts.
        "key_field": _unique_field_for(path, layout_name, config, source),
        # How that was decided, so the editor can show "key: keytrol (SCAN)"
        # and ask once when a folder carries no SCAN/WMS token. None when the
        # layout's key doesn't depend on the source.
        "json_source": (json_source_for(path, config, source)
                        if config.source_dependent(layout_name) else None),
        "raw_text": raw_text,  # for the Raw verify tab
        # Roll-up totals (config/rollup_fields.yaml) as they stand RIGHT NOW —
        # read-only. A mismatch is shown on open and corrected on save; opening
        # a file never writes. `authoritative` marks the empty-section case,
        # where the header total is the real quantity rather than a sum.
        "rollups": rollup_state(okf, config),
        "sections": sections_out,
    }


def _header_value(okf: OkFile, field: str) -> Optional[str]:
    if not okf.records:
        return None
    header = okf.records[0]
    try:
        return header.get(field)
    except KeyError:
        return None


# --------------------------------------------------------------------------- #
# Save edits
# --------------------------------------------------------------------------- #
class EditError(ValueError):
    """Raised when an edit violates a field's width or addresses a bad field."""


def apply_edits(
    path,
    edits: List[dict],
    registry: LayoutRegistry,
    target_path=None,
    backup: bool = True,
    config: Config = None,
    ops: List[dict] = None,
) -> dict:
    """Apply staged row ops + field edits and write the file.

    ``ops``: the staged journal (see :func:`replay_ops`) — row add/delete/move
    plus the field edits that were pending when each row op was made, in the
    order the user made them. ``edits``: the still-pending field edits
    {section_index, record_index, field, value}, applied last.

    ``target_path``: if given, everything is written **there** (Save As) and the
    source file is left untouched; else ``path`` is overwritten (Save). Nothing
    is written until every op and edit has validated, so a failure leaves both
    files as they were.
    """
    src = Path(path)
    okf = parse_okfile(src, registry=registry)
    replay_ops(okf, ops, config)
    _apply_edits_to_okf(okf, edits, config)
    _apply_detail_fill(okf, config)
    _apply_json_empty_rows(okf, config)   # keep an emptied JSON array's tags
    rollups = _apply_rollups(okf, config)   # header totals follow their detail rows

    _assert_layout_stable(okf)
    out = Path(target_path) if target_path else src
    _atomic_write_okf(okf, out, backup=(backup and target_path is None))

    return {
        "path": str(out),
        "written": True,
        "edits_applied": len(edits),
        "ops_applied": len(ops or []),
        "roundtrip_ok": okf.to_bytes() == fs.read_bytes(out),
        # What the save corrected on its own, so the UI can say so rather than
        # the user finding a changed total by accident.
        "rollups": rollups,
    }


def replay_ops(okf: OkFile, ops: List[dict], config: Config = None) -> Optional[str]:
    """Replay a staged op journal onto a parsed file, in order, in memory.

    Row edits stay *pending* in the browser until Save/Save As, so the client
    keeps an ordered journal instead of the server writing each row op straight
    to disk (which used to mutate the original file even when the user went on
    to Save As). Entry shapes:

    - ``{"type": "edit", "edits": [...]}`` — field edits pending at that moment
    - ``{"type": "add", "after_index": i}`` / ``{"type": "add", "section_index": s}``
    - ``{"type": "delete", "record_index": i}``
    - ``{"type": "move", "record_index": i, "direction": "up"|"down"}``

    Each entry's indices are relative to the view produced by the entries before
    it, so records are renumbered after every row op exactly as a reparse would
    (:func:`_reindex`). Returns the section name of the last ``add``, if any.
    """
    added = None
    for op in ops or []:
        kind = op.get("type")
        if kind == "edit":
            _apply_edits_to_okf(okf, op.get("edits") or [], config)
            continue
        if kind == "add":
            si = op.get("section_index")
            added = _op_add(
                okf, config,
                section_index=None if si is None else int(si),
                after_index=op.get("after_index"),
            )
        elif kind == "delete":
            _op_delete(okf, int(op["record_index"]))
        elif kind == "move":
            _op_move(okf, int(op["record_index"]), op.get("direction"))
        else:
            raise EditError(f"unknown pending op type {kind!r}")
        _normalize_eols(okf)
        _reindex(okf)
    return added


def _reindex(okf: OkFile) -> None:
    """Renumber records by position — what a save-then-reparse would produce."""
    for i, rec in enumerate(okf.records):
        rec.index = i


def _unwrap_quoted(s):
    """If ``s`` is wrapped in a matching pair of quotes, return the interior
    VERBATIM (leading/middle/trailing spaces preserved); otherwise return None.

    This is how a user protects significant spaces in a comma-separated list,
    where every unquoted entry is trimmed — ``'   msg01'`` -> ``   msg01``. The
    all-spaces interior (``' '``, ``''``) is the explicit-blank case."""
    if isinstance(s, str) and len(s) >= 2 and s[0] in "'\"" and s[-1] in "'\"":
        return s[1:-1]
    return None


def _is_blank_token(s: str) -> bool:
    """True when ``s`` is the explicit-blank token: a pair of quotes wrapping
    only spaces — ``''``, ``' '``, ``'  '`` (or the double-quote forms). This
    is how a user ASKS for a blank value where an empty entry would be ambiguous
    or dropped."""
    inner = _unwrap_quoted(s.strip())
    return inner is not None and inner.strip() == ""


def _unquote_blank(value):
    """Map the explicit-blank token (see :func:`_is_blank_token`) to an empty
    string; return any other value unchanged."""
    if isinstance(value, str) and _is_blank_token(value):
        return ""
    return value


def _pad_zero_value(value, layout_name, field, size, config: Config = None):
    """Left-pad a `pad_zeros` field with zeros to its declared size.

    Fixed-width layouts pad by construction; JSON values are trimmed strings, so
    a store typed as ``202`` would be stored as ``202`` and rejected downstream,
    which expects ``0202``. Applied on EVERY write path rather than in the
    editor, so bulk cannot bypass what single-file editing enforces (D30's
    lesson — the rule was right, the parallel write paths skipped it).

    DIGITS ONLY, and deliberately so. The existing padding rules (D15 literal
    fields, preserved leading/trailing spaces, never zero-padding free text)
    already cover every other case on every edit path — this must not reach
    them. A non-numeric value is left exactly as typed, so it fails width
    validation loudly instead of being silently turned into ``00AB``.

    A blank value stays blank too: padding an empty field to ``0000`` would
    invent a store number nobody entered.
    """
    if config is None or not config.is_pad_zero(layout_name, field):
        return value
    if not isinstance(value, str) or size is None:
        return value
    core = value.strip()
    if core == "" or not core.isdigit():
        return value
    return core.rjust(int(size), "0")


def _apply_edits_to_okf(okf, edits: List[dict], config: Config = None) -> None:
    """Validate field widths, then apply edits in place. Raises EditError."""
    for e in edits:                     # ' ' / '' / "" -> explicit blank (spaces)
        if isinstance(e.get("value"), str) and _is_blank_token(e["value"]):
            e["value"] = ""
            e["_blank"] = True          # force a SPACE-filled blank, not zeros
    by_index = {r.index: r for r in okf.records}
    errors: List[dict] = []
    for e in edits:
        rec = by_index.get(e["record_index"])
        if rec is None:
            errors.append({"edit": e, "error": "record_index out of range"})
            continue
        try:
            f = rec._field(e["field"])  # noqa: SLF001 — internal lookup
        except KeyError as exc:
            errors.append({"edit": e, "error": str(exc)})
            continue
        # The record's OWN capacity, not the layout's declared size: a short
        # record (Canada .OK files often stop at the last field they carry)
        # holds fewer characters than the layout allows, and a field it does
        # not reach at all holds none. Without this the write escapes as a raw
        # ValueError from Record.set instead of a per-field message.
        # JSON records address values by key path, not by span, so they have no
        # line to run out of — the layout size is the only limit there.
        span_of = getattr(rec, "_span", None)
        span = span_of(f) if span_of is not None else None
        room = (span[1] - span[0]) if span is not None else f.size
        if f.size is not None and len(e["value"]) > f.size:
            errors.append({
                "edit": e,
                "error": f"value '{e['value']}' exceeds field '{e['field']}' size {f.size}",
            })
        elif room is not None and len(e["value"]) > room:
            errors.append({
                "edit": e,
                "error": (f"value '{e['value']}' does not fit field '{e['field']}' "
                          f"in this record: the line ends after {room} character(s) "
                          f"of it, so writing more would move the record terminator"
                          if room else
                          f"field '{e['field']}' is not present in this record — "
                          f"the line ends before it, so it cannot be edited"),
            })
        # Chain edits cannot cross an isolation boundary (e.g. Europe <-> NA).
        if config is not None and e["field"] == "chain":
            old = (rec.get("chain") or "").strip()
            new = (e["value"] or "").strip()
            if old and new and new != old and not config.can_change_chain(old, new):
                errors.append({
                    "edit": e,
                    "error": (f"chain cannot change from {old} to {new}: "
                              f"Europe is isolated from the other chains"),
                })
    if errors:
        raise EditError(str(errors))
    for e in edits:
        rec = by_index[e["record_index"]]
        # An explicit blank token writes spaces (a visually blank field) on ANY
        # field, numeric included — the user typed spaces and asked for blank.
        literal = e.get("_blank") or (config is not None
                                      and config.is_literal(okf.layout.name, e["field"]))
        value = e["value"]
        if not e.get("_blank"):        # an explicit blank stays blank
            value = _coerce_date(okf.layout.name, e["field"], value, config)
            value = _pad_zero_value(value, okf.layout.name, e["field"],
                                    rec._field(e["field"]).size, config)  # noqa: SLF001
        rec.set(e["field"], value, literal=literal)


# --------------------------------------------------------------------------- #
# Add a record to a repeating section
# --------------------------------------------------------------------------- #
def add_record(
    path,
    section_index=None,
    edits=None,
    registry: LayoutRegistry = None,
    config: Config = None,
    backup: bool = True,
    after_index=None,
    ops: List[dict] = None,
    preview: bool = False,
) -> dict:
    """Replay staged ops + pending edits, add a copy of a record, return the view.

    If ``after_index`` is given, a copy of THAT row is inserted right below it
    (row-level "duplicate here"). Otherwise a copy of the section's last record
    is appended (``section_index``). Enforces the section's ``max_records`` limit.

    With ``preview`` (what the editor uses) nothing is written — the caller
    stages the op and the file changes only on Save/Save As. Without it the
    result is saved straight to ``path``.
    """
    view, added = _record_op(
        path, registry, config, ops, edits, backup, preview,
        lambda okf: _op_add(okf, config, section_index=section_index, after_index=after_index),
    )
    view["added_section"] = added
    return view


def delete_record(
    path,
    record_index: int,
    edits: List[dict],
    registry: LayoutRegistry,
    config: Config,
    backup: bool = True,
    ops: List[dict] = None,
    preview: bool = False,
) -> dict:
    """Replay staged ops + pending edits, delete one record by its line index.

    The header record (index 0) cannot be deleted. See :func:`add_record` for
    ``preview`` semantics.
    """
    view, _ = _record_op(
        path, registry, config, ops, edits, backup, preview,
        lambda okf: _op_delete(okf, record_index),
    )
    return view


def move_record(path, record_index, direction, edits, registry, config, backup=True,
                ops: List[dict] = None, preview: bool = False) -> dict:
    """Replay staged ops + pending edits, move one record up/down in its section.

    Reordering stays inside the record's own section (it can't jump into the
    header or another section). The header record can't be moved. See
    :func:`add_record` for ``preview`` semantics.
    """
    view, _ = _record_op(
        path, registry, config, ops, edits, backup, preview,
        lambda okf: _op_move(okf, record_index, direction),
    )
    return view


def _record_op(path, registry, config, ops, edits, backup, preview, mutate) -> tuple:
    """Shared plumbing for the three row ops.

    Parses the file **as it is on disk**, replays the staged journal, applies the
    still-pending field edits, then runs ``mutate``. Previews render the result
    from memory; otherwise it is saved to the source path.
    """
    src = Path(path)
    okf = parse_okfile(src, registry=registry)
    replay_ops(okf, ops, config)
    _apply_edits_to_okf(okf, edits or [], config)

    result = mutate(okf)
    _normalize_eols(okf)
    _reindex(okf)
    _apply_detail_fill(okf, config)
    _apply_json_empty_rows(okf, config)   # keep an emptied JSON array's tags
    _apply_rollups(okf, config)     # header totals follow their detail rows

    if preview:
        return _build_file_view(okf, src, config), result
    _backup_and_save(okf, src, backup)
    return parse_file_view(src, registry, config), result


def _op_add(okf: OkFile, config: Config, section_index=None, after_index=None) -> str:
    """Insert a copy of a record in memory. Returns the section name added to."""
    seeded = False
    if after_index is not None:
        anchor = next((r for r in okf.records if r.index == after_index), None)
        if anchor is None or anchor.section is None:
            raise EditError(f"record_index {after_index} not found")
        if okf.records and anchor.section is okf.records[0].section:
            raise EditError("cannot add rows to the header section")
        template = anchor
    else:
        grouped = list(okf.sections().items())
        if section_index is None or section_index < 0 or section_index >= len(grouped):
            raise EditError(f"section_index {section_index} out of range")
        _name, recs = grouped[section_index]
        if section_index == 0:
            raise EditError("cannot add rows to the header section")
        if recs:
            template = recs[-1]
        else:
            # Empty section: seed the first row from the section's reference line
            # and drop it into the file in canonical section order.
            sec = next((s for s in okf.layout.sections if s.name == _name), None)
            if sec is None:
                raise EditError(f"section '{_name}' cannot receive rows")
            template = _seed_record(okf, sec, config)
            if template is None:
                raise EditError(f"section '{sec.name}' has no template to seed a row")
            seeded = True

    sec = template.section
    # Fill-managed section (Preticket Lane): never clone a filler (all-zero) row —
    # whether it was the chosen anchor or the section's last row, the fill pass
    # re-absorbs an all-zero clone on save, so nothing is actually added. Redirect
    # to the last REAL row (or seed a fresh one when the section holds only filler).
    # Same redirect for a JSON section holding nothing but blank rows. D45
    # leaves ONE all-null row when a section is emptied, and clone_record reads
    # values through _display, which maps null -> "" — so adding after an empty
    # produced another blank row, the very complaint this seeding fixes, just
    # reached by cloning instead of seeding.
    if not seeded and config is not None and sec is not None \
            and (config.zero_fill(okf.layout.name, sec.name)
                 or getattr(okf.layout, "json_mode", False)):
        hidden = config.hidden_fields(okf.layout.name)
        if _is_blank_row(template, hidden):
            real = [r for r in okf.records if r.section is sec and not _is_blank_row(r, hidden)]
            if real:
                template = real[-1]
            else:
                template = _seed_record(okf, sec, config)
                if template is None:
                    raise EditError(f"section '{sec.name}' has no template to seed a row")
                seeded = True

    # A JSON section holding NOTHING but blank rows has no data in it, so the
    # row being added is its first real one and the blanks are replaced rather
    # than kept. Without this, adding to a section emptied earlier left D45's
    # marker row sitting above the new row, and every later add stacked on top
    # of it. Dropped only when EVERY row is blank — a blank row among real ones
    # is left alone, since that is the user's own row and not a placeholder.
    #
    # Note this cannot be decided by looking at the row: an emptied `lanes`
    # marker is `{"lane": ""}`, byte-identical to the one the vendor ships. So
    # the rule is about blankness, not about recognising a marker — which does
    # mean a genuinely blank vendor row is replaced by the first row added.
    blanks = _json_blank_rows_to_replace(okf, sec, config)

    sec_count = sum(1 for r in okf.records if r.section is sec) - len(blanks)
    limit = config.max_records(okf.layout.name, sec.name) if config else None
    if limit is not None and sec_count >= limit:
        raise EditError(f"section '{sec.name}' is at its limit of {limit} records")

    # A seeded JSON row is already a standalone new row — cloning it again would
    # just lose the blank values it was built with.
    clone = template if (seeded and getattr(okf.layout, "json_mode", False)) \
        else _clone_record(okf, template)
    if seeded:
        _insert_in_section_order(okf, clone, sec)
    else:
        okf.records.insert(okf.records.index(template) + 1, clone)
    for r in blanks:                       # after the insert, so indexes hold
        okf.records.remove(r)
    return sec.name


def _op_delete(okf: OkFile, record_index: int) -> None:
    """Remove one record by its line index, in memory."""
    target = next((r for r in okf.records if r.index == record_index), None)
    if target is None:
        raise EditError(f"record_index {record_index} not found")
    if target.index == 0:
        raise EditError("the header record cannot be deleted")
    okf.records.remove(target)


def _op_move(okf: OkFile, record_index, direction) -> None:
    """Swap one record with its neighbour inside its own section, in memory."""
    record_index = int(record_index)
    recs = okf.records
    idx = next((i for i, r in enumerate(recs) if r.index == record_index), None)
    if idx is None:
        raise EditError(f"record_index {record_index} not found")
    target = recs[idx]
    if target.index == 0:
        raise EditError("the header record cannot be moved")
    if direction == "up":
        j = idx - 1
    elif direction == "down":
        j = idx + 1
    else:
        raise EditError(f"invalid direction '{direction}'")
    if j < 0 or j >= len(recs) or recs[j].section is not target.section:
        raise EditError("row is already at the edge of its section")
    recs[idx], recs[j] = recs[j], recs[idx]


def _assert_layout_stable(okf: OkFile) -> None:
    """Refuse to write a file that would no longer detect as its own layout.

    Some header fields ARE the layout's detection signature (StyleHeader/
    Preticket ``indicator``, DistLabels ``format``, CartonLabel ``picklist_pre``,
    EUPreticket ``indicator``, EU GTA ``process``). Changing one makes the saved
    file unopenable — or worse, silently detect as a *different* layout, so every
    field then parses at the wrong offset.

    ``config/field_display.yaml`` locks those fields in the editor and in bulk;
    this is the backstop that holds if that list ever drifts (a new layout, a
    renamed field), and it covers every write path rather than every UI.
    """
    # JSON layouts detect on the ``data.type`` discriminator rather than a
    # positional header signature, so the header-line check below cannot see
    # them. This branch used to `return` outright, on the assumption that
    # `type` was unreachable because the spec marks it readonly — but readonly
    # is a UI hint that `apply_edits` does not enforce, so a write straight to
    # `type` sailed through and left the file detecting as NO layout, i.e.
    # unopenable in OkGen. That is exactly the D12 failure this function exists
    # to prevent, in the one engine it skipped.
    #
    # The value must still resolve to THIS layout. Re-casing is free
    # (`STYLEHEADERS` is the same document type as `styleHeaders`), but a
    # CROSS-TYPE change is refused: the rest of the document is still shaped
    # like the layout it was parsed as, so re-typing it would leave a style
    # header claiming to be a carton label. That is the same "silently detects
    # as a different layout" failure D12 blocks on the fixed-width side.
    if getattr(okf.layout, "json_mode", False):
        header = okf.records[0] if okf.records else None
        value = header.get("type") if header is not None else None
        if value is None:
            return
        became = detect.layout_for_json_type(value)
        if became == okf.layout.name:
            return
        own = getattr(okf.layout, "json_type", None)
        allowed = f" Use '{own}' (any capitalisation)." if own else ""
        if became is None:
            raise EditError(
                f"'{value}' is not a Calgary document type — the file would no "
                f"longer be openable.{allowed}")
        raise EditError(
            f"this file is a {okf.layout.name}, so its type cannot be changed "
            f"to '{value}' — that would make it detect as {became} while the "
            f"rest of the document keeps its current shape.{allowed}")
    head = okf.to_bytes().split(b"\n", 1)[0].decode(ENCODING).rstrip("\r\n")
    det = detect_from_header(head)
    if det.layout == okf.layout.name:
        return
    became = f"as {det.layout}" if det.layout else "as no known layout"
    raise EditError(
        f"this change would make the file detect {became} instead of "
        f"{okf.layout.name} — the edited field is part of the layout's "
        f"detection signature ({det.reason})"
    )


def _write_failed_message(out: Path, exc: OSError) -> str:
    """A user-facing reason for a failed write. A locked / in-use / read-only
    target is the common one (the file is open in Excel or another editor)."""
    busy = (isinstance(exc, PermissionError)
            or getattr(exc, "winerror", None) in (32, 33)      # sharing/lock violation
            or getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM, errno.EROFS))
    if busy:
        return (f"Couldn't save '{out.name}' — it looks like it's open in another "
                f"program (e.g. Excel) or is read-only. Close it there and try again.")
    # Windows reports a too-long path as "path not found", which reads as a
    # missing folder. Say what it actually is, and by how much.
    if fs.is_too_long_error(exc) or fs.is_long(out):
        return (f"Couldn't save '{out.name}' — the full path is "
                f"{len(str(out))} characters, over Windows' {fs.MAX_PATH}-character "
                f"limit. Use a shorter folder or file name, or a folder closer "
                f"to the drive root.")
    return f"Couldn't save '{out.name}': {getattr(exc, 'strerror', None) or exc}"


def _atomic_write_okf(okf, out: Path, backup: bool) -> None:
    """Write ``okf`` to ``out`` ATOMICALLY, with an optional ``.bak`` of the
    previous content written only AFTER the save succeeds.

    Serialize to a sibling ``.tmp`` then ``os.replace`` it in, so ``out`` is
    never left half-written and a locked / read-only / open-elsewhere target
    fails **without leaving a stray ``.tmp`` or ``.bak``** and raises a friendly
    :class:`EditError` (the routes turn it into a clear message) instead of a raw
    500. Callers that need the layout-stability check first should go through
    :func:`_backup_and_save`.
    """
    out = Path(out)
    tmp = out.with_suffix(out.suffix + ".tmp")
    prev = fs.read_bytes(out) if (backup and fs.exists(out)) else None
    try:
        try:
            data = okf.to_bytes()
        except ValueError as exc:
            # A change the engine cannot represent (e.g. rows added to a JSON
            # array the file doesn't contain). Refuse LOUDLY — writing what we
            # could and dropping the rest is how edits vanish silently.
            raise EditError(str(exc)) from exc
        fs.write_bytes(tmp, data)
        fs.replace(tmp, out)                 # atomic; fails cleanly if out is locked
    except OSError as exc:
        try:
            fs.unlink(tmp)                   # no stray temp file left behind
        except OSError:
            pass
        raise EditError(_write_failed_message(out, exc)) from exc
    if prev is not None:
        try:
            fs.write_bytes(out.with_suffix(out.suffix + ".bak"), prev)
        except OSError:
            pass                             # best-effort; the save already succeeded


def _backup_and_save(okf, out: Path, backup: bool) -> None:
    _assert_layout_stable(okf)              # reject a bad write before touching disk
    _atomic_write_okf(okf, out, backup)


def _eol_of(okf) -> str:
    """The per-line terminator that precedes the '\\n' separator ('\\r' or '')."""
    return "\r" if any(r.raw.endswith("\r") for r in okf.records) else ""


def _normalize_eols(okf) -> None:
    """Re-apply consistent line endings after an insert.

    Interior lines (and the last line when the file ends with a newline) carry
    the EOL; an unterminated final line does not. Idempotent on well-formed
    files, so it only changes bytes around an inserted record.
    """
    eol = _eol_of(okf)
    n = len(okf.records)
    for i, rec in enumerate(okf.records):
        content = rec.raw.rstrip("\r")
        is_last = i == n - 1
        if is_last and not okf.trailing_newline:
            rec.raw = content
        else:
            rec.raw = content + eol


def _is_blank_row(rec, hidden: set) -> bool:
    """True when a record carries no real data: every visible field is only
    zeros and/or spaces. Hidden structural fields (the '#'/'&' marker) are
    ignored, so an all-zero detail row still counts as blank."""
    for f in (rec.section.fields if rec.section else []):
        if f.name in hidden:
            continue
        v = rec.get(f.name)
        if v is not None and v.strip("0 ") != "":
            return False
    return True


def _json_blank_rows_to_replace(okf, sec, config: Config = None) -> list:
    """The rows a JSON section should shed when its first real row is added.

    Empty list unless this is a JSON layout whose ``sec`` holds rows and EVERY
    one of them is blank — the state a section is in after D45 wrote its
    tag-carrying marker, or after a vendor shipped a placeholder row. Either
    way the section has no data, so the row about to be added is its first.

    Deliberately all-or-nothing: a blank row sitting among real rows is the
    user's own and is never touched.
    """
    if config is None or sec is None or not getattr(okf.layout, "json_mode", False):
        return []
    rows = [r for r in okf.records if r.section is sec]
    if not rows:
        return []
    hidden = config.hidden_fields(okf.layout.name)
    return rows if all(_is_blank_row(r, hidden) for r in rows) else []


def _apply_json_empty_rows(okf: OkFile, config: Config) -> None:
    """Keep a JSON array section's TAGS when an operation removes all its rows.

    Since D43 an emptied array really does write, so bulk "keep 0 rows", a bulk
    delete, Generate and deleting the last row in the editor all produce
    ``"lanes": []``. A bare ``[]`` tells the consuming system nothing about the
    shape it should have had, so one row is kept with every field present and
    empty — values from ``json_empty_rows.yaml``, defaulting to JSON null.

    Only fires for an array that HAD rows and now has none. An array already
    stored as ``[]`` (or as ``null``, which several layouts use for
    ``lanes``/``sizes``) is untouched, so opening and saving an unmodified file
    still round-trips byte-for-byte (D20).

    Lives here, beside :func:`_apply_detail_fill` and called from the same write
    sites, for the D16/D30 reason: a write rule applied in one path and skipped
    by the parallel ones is invisible for months. `.OK` files have no JSON
    arrays, so this is a no-op for them.
    """
    state = getattr(okf, "json_state", None)
    if config is None or state is None:
        return
    from okgen import jsonengine

    layout = okf.layout
    for sec in layout.sections:
        if getattr(sec, "json_kind", None) != "array":
            continue
        base = ("data",) + tuple(sec.json_path or [])
        if base not in state.array_paths:
            continue                       # absent or null in this file — leave it
        original = jsonengine._at(state.data, base)
        if not original:
            continue                       # already [] on disk; keep it byte-exact
        if any(r.section is sec for r in okf.records):
            continue                       # still has rows; nothing to do
        rec = jsonengine.seed_record(state, sec, base)
        rec.inherited = config.json_empty_row(layout.name, sec.name,
                                              [f.name for f in sec.fields])
        okf.records.append(rec)


def rollup_state(okf: OkFile, config: Config) -> List[dict]:
    """What each roll-up field WOULD be, without changing anything.

    One entry per configured roll-up on this layout:
    ``{field, section, source, rows, current, expected, matches, authoritative}``.
    ``rows`` is the number of REAL detail rows; when it is 0 the section is
    empty, the header field is the authoritative quantity (``authoritative``
    True) and ``expected`` is None — there is nothing to sum it against.

    Read-only on purpose: it feeds the editor's live warning, the tree badge and
    the bulk preview, none of which may write. :func:`_apply_rollups` is the
    single place a roll-up is actually applied.
    """
    out: List[dict] = []
    if config is None or not okf.records:
        return out
    layout = okf.layout
    hidden = config.hidden_fields(layout.name)
    header = okf.records[0]
    for spec in config.rollups(layout.name):
        fname, sec_name = spec.get("field"), spec.get("section")
        src = spec.get("source")
        if not (fname and sec_name and src):
            continue
        sec = next((x for x in layout.sections if x.name == sec_name), None)
        if sec is None:
            continue
        try:
            f = header._field(fname)               # noqa: SLF001
        except KeyError:
            continue
        rows = [r for r in okf.records
                if r.section is sec and not _is_blank_row(r, hidden)]
        current = header.get(fname)
        entry = {"field": fname, "section": sec_name, "source": src,
                 "rows": len(rows), "current": current, "expected": None,
                 "matches": True, "authoritative": not rows}
        if rows:
            try:
                total = _rollup_total(rows, src, sec_name)
            except EditError as exc:
                entry.update({"error": str(exc), "matches": False})
                out.append(entry)
                continue
            expected = str(total).zfill(f.size or 0)
            entry["expected"] = expected
            entry["matches"] = (current == expected)
            if f.size is not None and len(str(total)) > f.size:
                entry["error"] = _rollup_overflow_msg(fname, total, f.size)
                entry["matches"] = False
        out.append(entry)
    return out


def _rollup_total(rows, src: str, sec_name: str) -> int:
    """Sum ``src`` across ``rows``. Blank counts as 0; non-numeric is an error.

    A blank is genuinely zero (an unfilled quantity prints nothing), but a value
    that is neither blank nor a number cannot be silently read as 0 — that would
    under-report the total with no sign anything went wrong.
    """
    total = 0
    for r in rows:
        raw = (r.get(src) or "").strip()
        if raw == "":
            continue
        if not raw.isdigit():
            raise EditError(
                f"{sec_name} row {r.index + 1}: {src} is {raw!r}, which is not a "
                f"number — the total cannot be calculated")
        total += int(raw)
    return total


def _rollup_overflow_msg(fname: str, total: int, size: int) -> str:
    return (f"{fname} holds {size} digits, but the rows total {total} "
            f"({len(str(total))} digits) — refusing to write a truncated total")


def _apply_rollups(okf: OkFile, config: Config) -> List[dict]:
    """Write each roll-up header field from the rows it totals.

    The rule, per config/rollup_fields.yaml:

    - **rows present** — the sum WINS. Whatever the header carried is corrected,
      because with rows on disk the total is knowable and a disagreeing header
      is simply wrong. A sum too wide for the field raises rather than writing a
      truncated total (D40).
    - **no rows** — the header field is the real quantity and is left EXACTLY as
      it is, so deleting the last detail row keeps the total that was there.
      Only a blank/zero value is seeded, from ``seed_when_empty``.

    Returns one note per field it changed (``reason`` ``"sum"`` or ``"seeded"``),
    so callers can tell the user what happened instead of writing silently.
    Runs on writes only, never on plain open, so an untouched file that predates
    the rule round-trips byte-for-byte until someone saves it.
    """
    notes: List[dict] = []
    if config is None or not okf.records:
        return notes
    header = okf.records[0]
    for st in rollup_state(okf, config):
        fname = st["field"]
        spec = config.rollup_for_field(okf.layout.name, fname) or {}
        if st.get("error"):
            raise EditError(st["error"])
        if st["rows"]:
            if not st["matches"]:
                header.set(fname, st["expected"])
                notes.append({"field": fname, "section": st["section"],
                              "from": st["current"], "to": st["expected"],
                              "rows": st["rows"], "reason": "sum"})
            continue
        # No rows: authoritative. Seed ONLY a blank/zero value.
        rng = spec.get("seed_when_empty")
        cur = (st["current"] or "").strip()
        if not rng or (cur and cur.strip("0") != ""):
            continue
        try:
            f = header._field(fname)               # noqa: SLF001
        except KeyError:
            continue
        val = str(random.randint(int(rng[0]), int(rng[1]))).zfill(f.size or 0)
        header.set(fname, val)
        notes.append({"field": fname, "section": st["section"],
                      "from": st["current"], "to": val, "rows": 0,
                      "reason": "seeded"})
    return notes


def _apply_detail_fill(okf: OkFile, config: Config) -> None:
    """Keep configured detail sections' count field (and, for some, filler rows)
    in sync with the number of REAL (non-blank) detail rows.

    Two modes, per ``config.fill_sections`` entry:

    - ``zeros > 0`` (Preticket's Lane): keep the real rows, follow them with
      exactly N all-zero filler rows, and set the header count field to the real
      count. Filler rows are COPIED, never synthesised — from an existing blank
      row in the file, else the compile-time ``filler_raw`` — because the
      zero/space byte layout is not derivable from the field spec.

    - ``zeros == 0`` (EUPreticket's Lane): COUNT ONLY — the format has no filler
      rows, so nothing is added or removed; just set the header count field to
      the number of real rows.

    A section with no real rows sets its count field to 0. Idempotent, and runs
    only on writes (not on plain open), so an unmodified file round-trips exact.
    """
    if config is None or not okf.records:
        return
    layout = okf.layout
    for sec_name, zeros in config.fill_sections(layout.name).items():
        sec = next((x for x in layout.sections if x.name == sec_name), None)
        if sec is None:
            continue
        hidden = config.hidden_fields(layout.name)
        rows = [r for r in okf.records if r.section is sec]
        real = [r for r in rows if not _is_blank_row(r, hidden)]
        blanks = [r for r in rows if _is_blank_row(r, hidden)]

        # Count-only mode: no filler rows, just sync the count to real rows.
        if zeros == 0:
            _set_count_field(okf, layout.name, sec_name, len(real), config)
            continue

        if not real:
            # no real detail lines -> line count is 0 (leave any zero rows as-is)
            _set_count_field(okf, layout.name, sec_name, 0, config)
            continue
        template_raw = (blanks[0].raw if blanks
                        else getattr(sec, "filler_raw", None))
        if not template_raw:
            continue                              # no filler example to copy
        offset = 0 if getattr(layout, "delimited", False) else len(sec.marker or "")
        fillers = [new_record(template_raw.rstrip("\r"), sec, layout,
                              offset=offset, index=-1) for _ in range(zeros)]
        new_rows = real + fillers

        # splice the rebuilt section back where its rows were, keeping order
        result, inserted = [], False
        for r in okf.records:
            if r.section is sec:
                if not inserted:
                    result.extend(new_rows)
                    inserted = True
                continue                          # drop the originals
            result.append(r)
        okf.records = result
        _normalize_eols(okf)
        _reindex(okf)

        # header count field = number of REAL rows (not counting filler)
        _set_count_field(okf, layout.name, sec_name, len(real), config)

    # Trim junk pad after the record terminator on detail lines (Preticket).
    # Done AFTER fill so freshly-copied filler rows are trimmed too.
    _trim_trailing_pad(okf, config)


def _trim_trailing_pad(okf: OkFile, config: Config) -> None:
    """Remove padding spaces that follow the record terminator on detail lines,
    for layouts configured in ``trim_trailing`` (e.g. Preticket, whose lines end
    ``...VENDORST\\   `` where the format needs no trailing pad).

    The terminator (``\\``) is a non-space char, so ``rstrip`` stops at it: every
    field — including a space-padded trailing field like ``vendor_style`` — is
    preserved, and only the pad after the terminator is dropped. The header line
    (index 0) is never touched, and a line whose stripped content doesn't end at
    the terminator is left alone (defensive: never eat a real field's spaces).
    Called only from the write path, so a plain open still round-trips exact.
    """
    if not config.trims_trailing(okf.layout.name):
        return
    term = okf.layout.record_terminator or ""
    for i, rec in enumerate(okf.records):
        if i == 0:                                   # header keeps its own bytes
            continue
        cr = "\r" if rec.raw.endswith("\r") else ""
        content = rec.raw[:-1] if cr else rec.raw
        stripped = content.rstrip(" ")
        if stripped == content:
            continue                                 # nothing to trim
        if term and not stripped.endswith(term):
            continue                                 # guard: don't eat field pad
        rec.raw = stripped + cr


def _set_count_field(okf: OkFile, layout_name: str, section_name: str,
                     count: int, config: Config) -> None:
    """Write ``count`` (zero-padded) into the section's header count field.

    If ``count`` does not fit the field's width, the field is LEFT UNCHANGED
    rather than truncated or overflowed — e.g. Preticket/EUPreticket keep a
    2-digit ``line_count``, so once the real detail rows reach 100+ the count is
    no longer updated (its existing value stands). Overwriting would corrupt the
    header, and truncating would silently misreport the count.
    """
    cf = config.count_field(layout_name, section_name)
    if not cf or not okf.records:
        return
    header = okf.records[0]
    try:
        f = header._field(cf)                     # noqa: SLF001
    except KeyError:
        return
    if f.size is None:
        return
    val = str(count).zfill(f.size)
    if len(val) <= f.size:                        # fits -> update; else leave as-is
        header.set(cf, val)


def _json_array_path(sec) -> tuple:
    """Absolute path to a JSON section's array, e.g. ``("data","header","stores")``."""
    return ("data",) + tuple(sec.json_path or [])


def _coerce_date(layout_name, field, value, config):
    """Normalize a temporal field's value to its configured format.

    Returns ``value`` unchanged for a field that isn't temporal. A value the
    date parser can't understand raises :class:`EditError` rather than being
    written through malformed — the same stance as the width and
    layout-stability checks.
    """
    if config is None:
        return value
    fmt = config.date_format(layout_name, field)
    if not fmt:
        return value
    from okgen import datetimes
    try:
        return datetimes.normalize(value, fmt)
    except datetimes.DateError as exc:
        raise EditError(f"{field}: {exc}") from exc


def _clone_record(okf, template):
    """A duplicate of ``template``, in whichever engine this file uses."""
    if getattr(okf.layout, "json_mode", False):
        from okgen import jsonengine
        return jsonengine.clone_record(template)
    return new_record(
        template.raw.rstrip("\r"),            # copy values; EOL fixed up below
        template.section,
        okf.layout,
        offset=template.offset,
        index=template.index,                 # placeholder; reassigned on reload
    )


def _json_seed_values(layout, sec, config: Config = None) -> Dict[str, str]:
    """The values a newly seeded JSON row starts with.

    A `.OK` section gets this from ``sample_raw`` — a real record line the
    compiler lifted out of the reference file. JSON layouts are hand-authored
    and have no reference file, so every seeded row used to be blank, including
    fields that cannot legally be empty (a Calgary store's ``date`` is an RFC
    3339 stamp).

    Resolution:

    1. ``json_seed_rows.yaml`` — the value the vendor sample carries for that
       field, so an added row looks like the rows a real file holds. A declared
       ``null`` is written as a real JSON null, NOT as an empty string: the
       samples use both and they mean different things (D34/D39);
    2. anything the config does not name — blank.

    A `pad_zeros` field is still padded (D34), so a seed written as ``1`` cannot
    reach the file as an unpadded store.

    There is deliberately NO automatic timestamp. An earlier cut filled any
    temporal field with the current time, which was wrong on two of the three
    layouts — CalgaryCartonLabel's sample carries ``date: null`` and
    CalgaryStyleHeader's ``" "`` — so it invented a stamp those files never
    have. ``date`` is an ordinary seed value now; it stays declared in
    ``date_fields.yaml``, so editing it, its validation and Bulk/Generate's
    random-date range are unaffected.

    JSON only — `.OK` never reaches here.
    """
    declared = config.json_seed_row(layout.name, sec.name) if config else {}
    out: Dict[str, object] = {}
    for f in sec.fields:
        if f.name not in declared:
            out[f.name] = ""
            continue
        value = declared[f.name]
        if value is None:                    # a DECLARED null, kept as null
            out[f.name] = None
            continue
        value = str(value)
        if value and config is not None:
            # A temporal field's seed is resolved through the same forgiving
            # parser the editor uses (D29), so config can declare either an
            # exact stamp — which passes through byte-for-byte — or `now`, and
            # the row is stamped when it is added. Per FIELD, per LAYOUT: this
            # is opt-in config, not D49's withdrawn blanket auto-`now`, so
            # CartonLabel's `date: null` and StyleHeader's blank stay as they
            # are because that is what those sections declare.
            # ...but only for a value with something in it. CalgaryStyleHeader's
            # store `date` is declared `" "`, exactly as its sample carries it —
            # a blank the parser rightly refuses, and a seed OkGen must be able
            # to write. Blank in, blank out.
            if value.strip():
                value = _coerce_date(layout.name, f.name, value, config)
            value = _pad_zero_value(value, layout.name, f.name, f.size, config)
        out[f.name] = value
    return out


def _seed_record(okf, sec, config: Config = None):
    """Build the first row for an EMPTY section from the section's ``sample_raw``.

    ``sample_raw`` is a real reference line, so it carries both realistic sample
    values AND the correct structure (marker, delimiters, terminator, field
    widths). We keep it as-is — the sample values are useful, and a copy of an
    existing row (the non-empty case) behaves the same way.

    The one thing that must be right is the leading-marker ``offset``: it drives
    where every fixed-width field is read from. It is the marker length — 0 for
    delimited layouts and for marker-less fixed-width detail lines (e.g.
    Preticket's Lane), 1 for a '#'/'&'-marked line. Hardcoding 1 shifted every
    field of a marker-less row one character right in the staged (pre-save)
    view; a reparse corrected it, so it only showed while the added row was
    still unsaved.

    Returns None when no seed structure is known for the section.
    """
    if getattr(okf.layout, "json_mode", False):
        # JSON layouts carry no reference line, so the row is built from config
        # (see _json_seed_values) rather than learned at compile time.
        from okgen import jsonengine
        rec = jsonengine.seed_record(okf.json_state, sec, _json_array_path(sec))
        # `inherited`, not `pending`: these are the values the row STARTS with,
        # not edits made to it — the same slot a clone fills from its template,
        # so both kinds of new row behave identically from here on.
        rec.inherited = _json_seed_values(okf.layout, sec, config)
        return rec
    seed = getattr(sec, "sample_raw", None)
    if not seed:
        return None
    marker = getattr(sec, "marker", "") or ""
    offset = 0 if getattr(okf.layout, "delimited", False) else len(marker)
    return new_record(seed.rstrip("\r"), sec, okf.layout, offset=offset, index=-1)


def _insert_in_section_order(okf, clone, sec) -> int:
    """Insert a record so the file keeps its canonical section order (e.g. a new
    Size row lands after the Lane rows, before Detail). Returns the insert index.
    """
    order = [s.name for s in okf.layout.sections]
    target = order.index(sec.name) if sec.name in order else len(order)
    insert_at = 1  # default: right after the header
    for i, r in enumerate(okf.records):
        nm = r.section.name if r.section else None
        if nm in order and order.index(nm) <= target:
            insert_at = i + 1
    okf.records.insert(insert_at, clone)
    return insert_at


# --------------------------------------------------------------------------- #
# Native folder picker (local app convenience)
# --------------------------------------------------------------------------- #
import threading

# Only ONE native folder dialog may be open at a time. The dialog is a blocking
# subprocess; without this, a race (or a client with no guard) could launch a
# pile of them that then linger and re-surface one after another. The client
# also guards the button, but this is the server-side backstop.
_BROWSE_LOCK = threading.Lock()


def browse_folder(initial: Optional[str] = None) -> dict:
    """Open the OS-native folder chooser and return the selected path.

    Uses each platform's own dialog (no embedded Python GUI, which can crash):
      * macOS   -> AppleScript ``choose folder`` via ``osascript``
      * Windows -> .NET ``FolderBrowserDialog`` via PowerShell
      * Linux   -> ``zenity --file-selection --directory``

    Returns {"path": None} when cancelled or when no GUI dialog is available.
    Refused (``already_open``) if a dialog is already open — never launches a
    second one.
    """
    import platform
    import subprocess

    if not _BROWSE_LOCK.acquire(blocking=False):
        return {"path": None, "already_open": True,
                "error": "a folder chooser is already open"}

    system = platform.system()
    try:
        if system == "Darwin":
            prompt = "Select the folder with your OK files"
            script = f'POSIX path of (choose folder with prompt "{prompt}")'
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=120,
            )
        elif system == "Windows":
            import base64

            start = (initial or "").replace("'", "''")
            set_start = f"$d.InitialDirectory = '{start}';" if start else ""
            # Modern Explorer-style window via OpenFileDialog in folder-select
            # mode (the user goes INTO the folder and clicks Open). A real
            # top-most owner + the Alt-key trick releases Windows' foreground
            # lock so it appears IN FRONT of Edge (a background process
            # otherwise can't take foreground).
            #
            # AttachThreadInput: bringing the window to the FRONT is not the same
            # as giving it input FOCUS. Without focus the dialog is topmost but
            # inactive, so Windows spends the user's FIRST click just activating
            # it (nothing gets selected) and only the 2nd click registers. A
            # background process can't grab focus on its own, so we attach to the
            # current foreground window's input queue for the hand-off, force
            # focus onto the owner (which the modal dialog then inherits), and
            # detach — so the dialog opens already active and the first click
            # selects.
            ps = f'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Fg {{
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr SetActiveWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr h);
}}
"@
$o = New-Object System.Windows.Forms.Form
$o.TopMost = $true
$o.ShowInTaskbar = $false
$o.FormBorderStyle = 'None'
$o.Width = 1; $o.Height = 1
$o.StartPosition = 'Manual'
$o.Left = -32000; $o.Top = -32000
$o.Show()
$fg = [Fg]::GetForegroundWindow()
$fgTid = [Fg]::GetWindowThreadProcessId($fg, [IntPtr]::Zero)
$myTid = [Fg]::GetCurrentThreadId()
$attached = $false
if ($fgTid -ne 0 -and $fgTid -ne $myTid) {{ $attached = [Fg]::AttachThreadInput($myTid, $fgTid, $true) }}
[Fg]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
[Fg]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[Fg]::BringWindowToTop($o.Handle) | Out-Null
[Fg]::SetForegroundWindow($o.Handle) | Out-Null
[Fg]::SetActiveWindow($o.Handle) | Out-Null
[Fg]::SetFocus($o.Handle) | Out-Null
$o.Activate()
if ($attached) {{ [Fg]::AttachThreadInput($myTid, $fgTid, $false) | Out-Null }}
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = 'Go INTO the folder with your OK files, then click Open'
$d.ValidateNames = $false
$d.CheckFileExists = $false
$d.CheckPathExists = $true
$d.FileName = 'Select this folder'
{set_start}
$r = $d.ShowDialog($o)
$o.Close()
if ($r -eq [System.Windows.Forms.DialogResult]::OK) {{ [Console]::Out.Write([System.IO.Path]::GetDirectoryName($d.FileName)) }}
'''
            enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-EncodedCommand", enc],
                capture_output=True, text=True, timeout=120,
            )
        else:
            proc = subprocess.run(
                ["zenity", "--file-selection", "--directory",
                 "--title=Select the folder with your OK files"],
                capture_output=True, text=True, timeout=120,
            )
        chosen = (proc.stdout or "").strip()
        return {"path": chosen or None}
    except subprocess.TimeoutExpired:
        return {"path": None, "error": "folder chooser timed out — please try again"}
    except FileNotFoundError:
        return {"path": None, "error": "no native folder dialog available on this system"}
    except Exception as exc:  # pragma: no cover - depends on desktop session
        return {"path": None, "error": str(exc)}
    finally:
        _BROWSE_LOCK.release()


# --------------------------------------------------------------------------- #
# File operations (tree actions)
# --------------------------------------------------------------------------- #
def delete_file(path) -> dict:
    p = Path(path)
    if not _editable_file(p):
        raise EditError(f"not an editable file: {p}")
    fs.unlink(p)
    return {"deleted": str(p)}


def tosca_scripts(config: Config, paths=None, registry=None) -> dict:
    """The configured TOSCA scripts + the run warning, for the picker.

    With ``paths`` (and a registry) each script also carries ``applies_to`` and
    ``matches`` — how many of the selected files it would actually run — so the
    picker can offer only the scripts that fit. .OK and JSON have separate
    workbooks, and aiming a selection at the wrong one is the mistake worth
    making impossible rather than merely reporting.
    """
    from okgen import tosca
    warning = (config.tosca() or {}).get("run_warning", "")
    if paths and registry is not None:
        out = tosca.scripts_for(paths, registry, config)
        out["warning"] = warning
        return out
    return {"scripts": tosca.list_scripts(config), "warning": warning}


def run_tosca_script(paths, script, registry, config: Config) -> dict:
    """Populate the chosen TOSCA workbook from the selected files."""
    from okgen import tosca
    try:
        return tosca.run(paths, script, registry, config)
    except tosca.ToscaError as exc:
        raise EditError(str(exc))


def _send_mode(paths) -> str:
    """Which NiceLabel hand-off a selection wants: 'copy' (.OK) or 'post' (JSON).

    'post' ONLY when every selected file is a ``.json``. Anything else — all
    ``.OK``, or a mix — stays on the original hot-folder copy, which reports a
    non-``.OK`` file as a per-file error exactly as it always has. The JSON
    hand-off is strictly additive: it never changes what a selection containing
    an ``.OK`` file does.
    """
    paths = list(paths or [])
    all_json = bool(paths) and all(Path(p).suffix.lower() == ".json" for p in paths)
    return "post" if all_json else "copy"


def send_scope(paths, config: Config) -> dict:
    """What a Send would do with this selection — for the confirmation dialog.

    Returns the mode, a human destination, the warning to show and (for JSON)
    whether the endpoint is configured at all, so the UI can say what to fix
    instead of letting the user confirm a send that cannot run.
    """
    from okgen import nicelabel_post as nlp

    mode = _send_mode(paths)
    if mode == "copy":
        dest = config.nicelabel_path() or ""
        return {"mode": "copy", "count": len(paths or []), "configured": bool(dest),
                "destination": dest, "warning": config.nicelabel_warning(),
                "error": "" if dest else
                         "NiceLabel path is not configured (config/nicelabel.yaml)"}
    broken = config.nicelabel_post_error()
    if broken:
        # The file exists and was edited, but could not be parsed — say THAT,
        # rather than sending the user back to fill in a file they just filled.
        return {"mode": "post", "count": len(paths or []), "configured": False,
                "destination": "", "folder": "", "username": "",
                "warning": "", "error": broken}
    info = nlp.describe(config.nicelabel_post())
    return {"mode": "post", "count": len(paths or []),
            "configured": info.get("configured", False),
            "destination": info.get("endpoint", ""),
            "folder": info.get("folder", ""),
            "username": info.get("username", ""),
            "warning": info.get("warning", ""),
            "error": info.get("error", "")}


def send_to_nicelabel(paths, config: Config) -> dict:
    """Copy selected .OK files into NiceLabel's incoming folder (overwriting).

    Deliberately UNCHANGED by the JSON POST hand-off (D31): an all-``.json``
    selection is routed to ``start_send_job`` by the caller, but anything that
    reaches here behaves exactly as it always did, down to reporting a
    non-``.OK`` file as a per-file error rather than refusing the batch.
    """
    dest = config.nicelabel_path()
    if not dest:
        raise EditError("NiceLabel path is not configured (config/nicelabel.yaml)")
    dd = Path(dest)
    if not dd.is_dir():
        raise EditError(f"NiceLabel folder not found or unreachable: {dest}")
    sent, errors = [], []
    for path in paths or []:
        sp = Path(path)
        if not is_ok_file(sp):
            errors.append({"path": str(path), "error": "not an .OK file"})
            continue
        try:
            fs.copy2(sp, dd / sp.name)       # overwrite any same-name file
            sent.append(sp.name)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
    return {"sent": sent, "errors": errors, "dest": str(dd)}


# --------------------------------------------------------------------------- #
# Send jobs (JSON POST runs in the background)
# --------------------------------------------------------------------------- #
# Posting 500 files one request at a time takes minutes — far longer than a
# browser or a corporate proxy will hold a request open — so the run happens on
# a worker thread and the client polls for live counters.

_SEND_JOBS: Dict[str, dict] = {}
_SEND_JOBS_LOCK = threading.Lock()
_SEND_JOBS_KEEP = 20        # finished jobs retained, newest first


def _job_update(job_id: str, **fields) -> None:
    with _SEND_JOBS_LOCK:
        job = _SEND_JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _prune_jobs() -> None:
    """Keep the most recent finished jobs; running ones are never dropped."""
    finished = sorted((j["seq"], jid) for jid, j in _SEND_JOBS.items()
                      if j["state"] != "running")
    for _, jid in finished[:max(0, len(finished) - _SEND_JOBS_KEEP)]:
        _SEND_JOBS.pop(jid, None)


def start_send_job(paths, config: Config) -> dict:
    """Validate the JSON send, start it on a worker thread, return a job handle.

    Configuration problems (an unset endpoint, bad credentials config) are
    raised HERE, synchronously, so the user is told immediately instead of
    having to poll a job that was doomed before it started.
    """
    from okgen import nicelabel_post as nlp

    if _send_mode(paths) != "post":
        raise EditError(
            "The POST hand-off takes .json files only — a selection holding an "
            ".OK file is copied to the NiceLabel hot folder instead.")
    paths = [str(p) for p in (paths or [])]
    broken = config.nicelabel_post_error()
    if broken:
        raise EditError(broken)
    try:
        settings = nlp.settings_from(config.nicelabel_post())
    except nlp.PostError as exc:
        raise EditError(str(exc))

    job_id = uuid.uuid4().hex
    with _SEND_JOBS_LOCK:
        _SEND_JOBS[job_id] = {
            "id": job_id, "seq": len(_SEND_JOBS), "state": "running", "mode": "post",
            "total": len(paths), "done": 0, "posted": 0, "failed": 0, "skipped": 0,
            "result": None, "error": "",
        }
        _prune_jobs()

    raw = config.nicelabel_post()

    def worker():
        try:
            res = nlp.run(paths, raw,
                          progress=lambda p: _job_update(job_id, **p))
            _job_update(job_id, state="done", result=res)
        except nlp.PostError as exc:
            _job_update(job_id, state="error", error=str(exc))
        except Exception as exc:                # pragma: no cover - safety net
            _job_update(job_id, state="error",
                        error=f"unexpected failure during send: {exc}")

    threading.Thread(target=worker, name=f"okgen-send-{job_id[:8]}",
                     daemon=True).start()
    return {"job": job_id, "mode": "post", "total": len(paths),
            "endpoint": nlp.redact_url(settings.endpoint_url)}


def send_job_status(job_id: str) -> dict:
    """Live counters for a send job; the full report once it finishes."""
    with _SEND_JOBS_LOCK:
        job = _SEND_JOBS.get(str(job_id))
        if job is None:
            raise EditError("that send job is unknown (it may have finished long ago)")
        return dict(job)


def delete_files(paths) -> dict:
    """Delete several .OK files; report per-file outcomes."""
    deleted, errors = [], []
    for path in paths or []:
        p = Path(path)
        if not _editable_file(p):
            errors.append({"path": str(path), "error": "not an editable file"})
            continue
        try:
            fs.unlink(p)
            deleted.append(str(p))
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
    return {"deleted": deleted, "errors": errors}


def copy_file(src, dst) -> dict:
    s, d = Path(src), Path(dst)
    if not _editable_file(s):
        raise EditError(f"not an editable file: {s}")
    if fs.exists(d):
        raise EditError(f"destination exists: {d}")
    fs.mkdir(d.parent, parents=True, exist_ok=True)
    fs.copy2(s, d)
    return {"copied": str(s), "to": str(d)}


def _unique_path(dst_dir: Path, name: str) -> Path:
    """A non-existing path in ``dst_dir`` for ``name``, adding ' (1)', ' (2)'…

    Mirrors how a browser's Downloads folder de-duplicates: the suffix goes
    before the extension, e.g. 'Style.OK' -> 'Style (1).OK'.
    """
    p = Path(name)
    stem, suffix = p.stem, p.suffix
    candidate = dst_dir / name
    i = 1
    while fs.exists(candidate):
        candidate = dst_dir / f"{stem} ({i}){suffix}"
        i += 1
    return candidate


def copy_files(srcs, dst_dir, registry=None, config=None,
               source: Optional[str] = None) -> dict:
    """Paste .OK files and/or whole folders into a folder.

    Files are copied; folders are copied recursively with all their contents.
    Never overwrites: if a name already exists, the copy is auto-renamed with a
    ' (N)' suffix (Downloads-style). When registry+config are given, any pasted
    .OK file whose unique key collides in the destination is re-keyed to the
    next free value. Returns per-item outcomes plus ``rekeyed``.
    """
    dd = Path(dst_dir)
    if not dd.is_dir():
        raise EditError(f"not a folder: {dd}")
    copied, renamed, errors = [], [], []
    new_ok_files: List[Path] = []
    for src in srcs or []:
        sp = Path(src)
        is_dir = sp.is_dir()
        if not is_dir and not _editable_file(sp):
            errors.append({"src": str(src), "error": "not an editable file or folder"})
            continue
        if is_dir and (dd == sp or sp in dd.parents):
            errors.append({"src": str(src), "error": "cannot paste a folder into itself"})
            continue
        target = dd / sp.name
        if fs.exists(target):
            target = _unique_path(dd, sp.name)
            renamed.append({"from": sp.name, "to": target.name})
        try:
            if is_dir:
                fs.copytree(sp, target)
            else:
                fs.copy2(sp, target)
                new_ok_files.append(target)
            copied.append(str(target))
        except OSError as exc:
            errors.append({"src": str(src), "error": str(exc)})

    rekeyed = []
    if registry is not None and config is not None and new_ok_files:
        rekeyed = _uniquify_new_files(dd, new_ok_files, registry, config, source)
    return {"copied": copied, "renamed": renamed, "rekeyed": rekeyed, "errors": errors}


# --------------------------------------------------------------------------- #
# Unique key field
# --------------------------------------------------------------------------- #
class KeyParts(NamedTuple):
    """A key decomposed as ``prefix + number + suffix``.

    Only ``number`` is ours to renumber; the literal prefix and suffix identify
    the file and must survive every operation:

        'C:88813'    -> ('C:', 88813, '',    5)   EUCartonLabel literal prefix
        '126539Q'    -> ('',   126539, 'Q',  6)   EUStyleHeader trailing suffix
        '33001P3A'   -> ('',   33001,  'P3A', 5)
        '0008881334' -> ('',   8881334, '',  10)
        'ABC'        -> ('ABC', None,  '',    0)  nothing to renumber
    """
    prefix: str
    value: "Optional[int]"
    suffix: str
    width: int          # how many characters the digit run occupied

    @property
    def space(self) -> tuple:
        """Numbering space: keys sharing a prefix+suffix are renumbered together."""
        return (self.prefix, self.suffix)


def _split_key(raw) -> KeyParts:
    """Split a key value into prefix + number + suffix.

    The number is the FIRST digit run; whatever precedes it is the prefix and
    whatever follows is the suffix. EUStyleHeader keytrols are 6 leading digits
    with an optional letter suffix (``126539Q``), EUCartonLabel keytrols may
    carry a ``C:`` prefix — both are preserved and only the digits move.
    """
    if raw is None:
        return KeyParts("", None, "", 0)
    core = str(raw).strip()
    m = re.search(r"\d+", core) if core else None
    if m is None:
        return KeyParts(core, None, "", 0)
    return KeyParts(core[:m.start()], int(m.group()), core[m.end():], len(m.group()))


def _format_key(prefix: str, value: int, size: int, suffix: str = "",
                width: "Optional[int]" = None) -> str:
    """Render ``prefix`` + zero-padded ``value`` + ``suffix`` into ``size`` chars.

    ``width`` is the digit-run width to keep — passing the original keeps the
    suffix at the same offset (``126539Q`` -> ``126540Q``, not ``0000126540Q``).
    It grows only if the number no longer fits, and only into free space.
    """
    room = size - len(prefix) - len(suffix)
    if room < 1:
        raise EditError(
            f"key {prefix!r}+{suffix!r} leaves no room in a {size}-char field")
    body = str(value)
    if len(body) > room:
        raise EditError(f"key {prefix}{value}{suffix} overflows the {size}-char field")
    pad = room if width is None else max(min(width, room), len(body))
    return prefix + body.zfill(pad) + suffix


def _read_key_int(path: Path, registry, config, source: Optional[str] = None):
    """(layout, key_field, int_value | None, KeyParts) for a file's unique key."""
    try:
        layout = detect_layout(path).layout        # .OK (positional) or .json (data.type)
    except Exception:
        return (None, None, None, KeyParts("", None, "", 0))
    if not layout:
        return (None, None, None, KeyParts("", None, "", 0))
    kf = _unique_field_for(path, layout, config, source)
    if not kf:
        return (layout, None, None, KeyParts("", None, "", 0))
    reg_layout = registry.get(layout)
    if getattr(reg_layout, "json_mode", False):
        # JSON: the key is a named header value, not a header-line slice.
        raw = _json_header(path).get(kf)
    elif getattr(reg_layout, "delimited", False):
        raw = _delim_header_value(read_header_line(path), reg_layout, kf)
    else:
        raw = _key_from_header(read_header_line(path), reg_layout,
                               _header_field(reg_layout, kf))
    parts = _split_key(raw)
    return (layout, kf, parts.value, parts)


def _next_key_int(maxv: Dict[tuple, int], layout: str, space: tuple = ("", "")) -> int:
    """Next free key value for a (layout, prefix, suffix): max-seen + 1, never 0.

    Numbering is per prefix AND suffix, so 'C:00007', '00007' and '00007Q' are
    three different keys in three independent spaces — none blocks the others.

    Keys are always assigned a non-zero value (an all-zero key reads as a blank
    sentinel), so the first key handed out in an empty/keyless folder is 1.
    """
    return max(maxv.get((layout,) + tuple(space), -1) + 1, 1)


def _set_key(path: Path, registry, key_field: str, new_int: int, size: int, backup: bool,
             parts: "Optional[KeyParts]" = None):
    """Write a new key into a file's header, KEEPING its prefix and suffix."""
    parts = parts or KeyParts("", None, "", 0)
    try:
        new_str = _format_key(parts.prefix, new_int, size, parts.suffix, parts.width)
    except EditError:
        raise EditError(f"no available key for {path.name} (width {size} overflow)")
    okf = parse_okfile(path, registry=registry)
    okf.records[0].set(key_field, new_str)
    _backup_and_save(okf, path, backup)
    return new_str


def _folder_key_state(folder: Path, registry, config, exclude: set,
                      source: Optional[str] = None):
    """Per (layout, key-prefix) (used-int-set, max-int) from a folder's OK files.

    Keyed by prefix as well as layout so a literal prefix like ``C:`` gets its
    own numbering space (see :func:`_next_key_int`).
    """
    used: Dict[tuple, set] = {}
    maxv: Dict[tuple, int] = {}
    for entry in folder.iterdir():
        if not (is_ok_file(entry) or _is_json_file(entry)) or entry.resolve() in exclude:
            continue
        layout, kf, val, parts = _read_key_int(entry, registry, config, source)
        if layout and kf and val is not None:
            space = (layout,) + parts.space
            used.setdefault(space, set()).add(val)
            maxv[space] = max(maxv.get(space, -1), val)
    return used, maxv


def _uniquify_new_files(folder: Path, new_files: List[Path], registry, config,
                        source: Optional[str] = None) -> List[dict]:
    """Re-key pasted files that collide with existing/earlier keys (max+1)."""
    used, maxv = _folder_key_state(folder, registry, config,
                                   {p.resolve() for p in new_files}, source)
    rekeyed = []
    for p in new_files:
        layout, kf, val, parts = _read_key_int(p, registry, config, source)
        if not (layout and kf):
            continue
        space = (layout,) + parts.space
        u = used.setdefault(space, set())
        if val is not None and val not in u:
            u.add(val)
            maxv[space] = max(maxv.get(space, -1), val)
            continue
        f = _header_field(registry.get(layout), kf)
        new_int = _next_key_int(maxv, layout, parts.space)
        try:
            # keep the file's own prefix ('C:') and suffix ('Q') — renumber the digits
            new_str = _set_key(p, registry, kf, new_int, f.size, backup=False, parts=parts)
        except (EditError, Exception) as exc:  # noqa: BLE001
            rekeyed.append({"file": p.name, "error": str(exc)})
            continue
        u.add(new_int)
        maxv[space] = new_int
        rekeyed.append({"file": p.name, "field": kf,
                        "from": (f"{parts.prefix}{val}{parts.suffix}"
                                 if val is not None else None), "to": new_str})
    return rekeyed


def make_unique_in_folder(folder, registry, config, backup=True,
                          source: Optional[str] = None) -> dict:
    """Fix duplicate keys in a folder (keep first occurrence, re-key the rest)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise EditError(f"not a folder: {folder}")
    files = sorted([p for p in folder.iterdir() if is_ok_file(p) or _is_json_file(p)],
                   key=lambda p: p.name.lower())
    used: Dict[tuple, set] = {}
    maxv: Dict[tuple, int] = {}
    rekeyed = []
    for p in files:
        layout, kf, val, parts = _read_key_int(p, registry, config, source)
        if not (layout and kf):
            continue
        space = (layout,) + parts.space
        u = used.setdefault(space, set())
        if val is not None and val not in u:
            u.add(val)
            maxv[space] = max(maxv.get(space, -1), val)
            continue
        f = _header_field(registry.get(layout), kf)
        new_int = _next_key_int(maxv, layout, parts.space)
        try:
            # keep the file's own prefix ('C:') and suffix ('Q') — renumber the digits
            new_str = _set_key(p, registry, kf, new_int, f.size, backup=backup, parts=parts)
        except (EditError, Exception) as exc:  # noqa: BLE001
            rekeyed.append({"file": p.name, "error": str(exc)})
            continue
        u.add(new_int)
        maxv[space] = new_int
        rekeyed.append({"file": p.name, "field": kf,
                        "source": _source_name_for(p, layout, config, source),
                        "from": (f"{parts.prefix}{val}{parts.suffix}"
                                 if val is not None else None),
                        "to": new_str})
    return {"folder": str(folder), "rekeyed": rekeyed}


def make_unique_files(paths, registry, config, backup=True,
                      source: Optional[str] = None) -> dict:
    """Run Make Unique on every folder that contains a selected file."""
    folders = []
    seen = set()
    for p in paths or []:
        parent = Path(p).parent
        key = str(parent.resolve())
        if key not in seen:
            seen.add(key)
            folders.append(parent)
    results = [make_unique_in_folder(f, registry, config, backup=backup, source=source)
               for f in folders]
    return {"folders": results}


def total_qty_scan(paths, registry, config: Config, apply: bool = False,
                   backup: bool = True) -> dict:
    """Inventory (and optionally fix) the roll-up totals across a selection.

    The backlog answer: files written before the rule existed carry a header
    total that disagrees with their rows. ``apply=False`` is a pure PREVIEW —
    it writes nothing and reports what a fix WOULD do, which is the point, since
    this is the first bulk action that rewrites field CONTENT rather than junk.

    Per-file status:

    - ``fixed`` / ``would_fix`` — has rows, total corrected (``from`` -> ``to``)
    - ``ok``       — has rows and already agrees; **nothing is written**, so a
                     correct file keeps its timestamp
    - ``no_rows``  — the detail section is empty, so the header total is the
                     real quantity: it is REPORTED with its current value and
                     left completely alone. Zeroing these would silently destroy
                     the print quantity on every file of the shape the new
                     system produces most.
    - ``skipped``  — layout declares no roll-up (or the file is JSON)
    - ``error``    — unreadable, locked, or a total that will not fit (D40)

    ``no_rows`` entries come back sorted by their current total, descending, so
    the implausibly large legacy values are the first thing the user sees.
    """
    results, no_rows = [], []
    fixed = 0
    for p in paths or []:
        p = Path(p)
        entry = {"path": str(p), "name": p.name}
        try:
            if _is_json_file(p):
                entry.update(status="skipped",
                             detail="JSON layouts declare no roll-up")
                results.append(entry)
                continue
            okf = parse_okfile(p, registry=registry)
            state = rollup_state(okf, config)
            if not state:
                entry.update(status="skipped",
                             detail=f"{okf.layout.name} declares no roll-up")
                results.append(entry)
                continue
            st = state[0]                     # one roll-up per layout today
            entry.update(field=st["field"], section=st["section"],
                         rows=st["rows"], current=st["current"])
            if st.get("error"):
                entry.update(status="error", error=st["error"])
            elif not st["rows"]:
                entry.update(status="no_rows", detail="no detail rows — this "
                             "total is the quantity and was left as it is")
                no_rows.append(entry)
            elif st["matches"]:
                entry.update(status="ok")
            else:
                entry.update({"from": st["current"], "to": st["expected"]})
                if not apply:
                    entry["status"] = "would_fix"
                else:
                    _apply_rollups(okf, config)
                    _atomic_write_okf(okf, p, backup=backup)
                    entry["status"] = "fixed"
                    fixed += 1
        except Exception as exc:              # unreadable / locked / won't fit
            entry.update(status="error", error=str(exc))
        results.append(entry)

    order = {"error": 0, "would_fix": 1, "fixed": 1, "no_rows": 2,
             "ok": 3, "skipped": 4}
    no_rows.sort(key=lambda e: -_as_int(e.get("current")))
    results.sort(key=lambda e: (order.get(e["status"], 9),
                                -_as_int(e.get("current"))
                                if e["status"] == "no_rows" else 0))
    summary = {
        "total": len(paths or []),
        "fixed": fixed,
        "would_fix": sum(1 for r in results if r["status"] == "would_fix"),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "no_rows": len(no_rows),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "applied": apply,
    }
    report = build_total_qty_report(summary, results)
    return {"results": results, "summary": summary, "report": report,
            "log": _write_total_qty_log(report) if apply else None}


def _as_int(v) -> int:
    v = (v or "").strip()
    return int(v) if v.isdigit() else 0


def build_total_qty_report(summary: dict, results: List[dict]) -> str:
    """The scan/fix report as plain text.

    One function feeds the UI and the log file (the D36 rule), so what the user
    pastes into a ticket is exactly what the log on disk says.
    """
    verb = "FIX" if summary.get("applied") else "PREVIEW"
    lines = [
        f"OkGen — Total Qty {verb}",
        f"Result : {summary['fixed']} fixed, {summary['would_fix']} to fix, "
        f"{summary['ok']} already correct, {summary['no_rows']} with no detail "
        f"rows, {summary['errors']} errors, {summary['skipped']} skipped "
        f"({summary['total']} selected)",
    ]
    buckets = [
        ("would_fix", "TO FIX (total does not match its rows)"),
        ("fixed", "FIXED"),
        ("no_rows", "NO DETAIL ROWS — total is the quantity, LEFT AS IT IS "
                    "(largest first; update these yourself if they look wrong)"),
        ("error", "ERRORS"),
        ("ok", "ALREADY CORRECT"),
    ]
    for status, title in buckets:
        rows = [r for r in results if r["status"] == status]
        if not rows:
            continue
        lines += ["", title]
        for r in rows:
            if status in ("would_fix", "fixed"):
                lines.append(f"  {r['name']:<40} {r['from']} -> {r['to']}"
                             f"   ({r['rows']} rows)")
            elif status == "no_rows":
                lines.append(f"  {r['name']:<40} {r.get('current')}")
            elif status == "error":
                lines.append(f"  {r['name']:<40} {r.get('error')}")
            else:
                lines.append(f"  {r['name']:<40} {r.get('current')}")
    return "\n".join(lines) + "\n"


def _write_total_qty_log(report: str) -> Optional[str]:
    """Write the report beside OkGen. A log we cannot write must never fail the
    run — the report is returned to the UI either way."""
    from datetime import datetime

    from okgen.nicelabel_post import default_log_folder
    try:
        folder = default_log_folder()
        fs.mkdir(folder, parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = folder / f"okgen_total_qty_{stamp}.log"
        fs.write_text(path, report)
        return str(path)
    except Exception:
        return None


def clean_files(paths, registry) -> dict:
    """Remove stray trailing blank lines from each selected file (the "Clean up
    files" bulk action).

    Parsing already drops trailing empty/space-only lines (see
    :func:`okgen.okfile.parse_okfile`), and a plain parse leaves every real
    record's bytes untouched, so this is simply "parse -> serialize -> write if
    the bytes changed". Nothing else is normalized (Preticket filler, trailing
    pad, line counts are NOT touched), so a file with no trailing blanks is
    reported ``clean`` and left byte-for-byte as it was.

    Per-file status: ``cleaned`` (blank lines removed, with how many), ``clean``
    (already fine), or ``error`` (couldn't detect/parse the file).
    """
    results = []
    cleaned = 0
    for p in paths or []:
        p = Path(p)
        if _is_json_file(p):
            # Blank junk lines are a LINE-based problem; a JSON document has no
            # such thing. Say so plainly instead of reporting a file "clean"
            # after a check that never applied to it.
            results.append({"path": str(p), "name": p.name, "status": "skipped",
                            "detail": "JSON files have no blank lines to clean"})
            continue
        try:
            original = fs.read_bytes(p)
            okf = parse_okfile(p, registry=registry)      # strips trailing blanks
            new_bytes = okf.to_bytes()
            if new_bytes != original:
                removed = max(original.count(b"\n") - new_bytes.count(b"\n"), 1)
                # Atomic write with the same friendly-on-lock handling as every
                # other save (a locked file -> EditError, caught below as an
                # "error" result; original untouched, no stray files).
                _atomic_write_okf(okf, p, backup=False)
                cleaned += 1
                results.append({"path": str(p), "name": p.name,
                                "status": "cleaned", "removed": removed})
            else:
                results.append({"path": str(p), "name": p.name, "status": "clean"})
        except Exception as exc:
            results.append({"path": str(p), "name": p.name,
                            "status": "error", "error": str(exc)})
    return {"results": results, "cleaned": cleaned, "total": len(paths or [])}


# --------------------------------------------------------------------------- #
# Bulk edit (B1: Header fields, one layout, set value)
# --------------------------------------------------------------------------- #
def _header_fields_for_layout(layout, config: Config) -> List[dict]:
    """Header-section field metadata for a layout, for the bulk-edit dropdowns.

    Excludes the layout's unique key field — bulk set-value would make every
    file's key identical, which violates uniqueness (use Make Unique instead) —
    plus anything hidden or read-only for this layout, so a field locked in the
    single-file editor (notably the layout's detection signature) cannot be
    changed in bulk either. Bulk is where that would hurt most: one apply across
    a selection would otherwise brick every file in it.
    """
    if not layout.sections:
        return []
    key = config.unique_field(layout.name)
    locked = config.readonly_fields(layout.name) | config.hidden_fields(layout.name)
    out = []
    for f in layout.sections[0].fields:
        # `readonly` may come from config OR from the layout spec itself (the
        # Calgary JSON `type`). Both must keep a field out of bulk — that is
        # the D12 rule, and `type` is a detection signature.
        if f.name == key or f.name in locked or getattr(f, "readonly", False):
            continue
        opts = config.options(f.name, layout=layout.name,
                              section=layout.sections[0].name)
        entry = {"name": f.name, "size": f.size, "type": f.field_type,
                 "options": opts or None}
        if config.date_format(layout.name, f.name):
            entry["date"] = True        # offer a date range, not a numeric one
        out.append(entry)
    return out


def bulk_scope(paths, registry: LayoutRegistry, config: Config) -> dict:
    """Summarize a selection for bulk edit: per-file layout + header fields."""
    files, layouts = [], {}
    for p in paths or []:
        sp = Path(p)
        layout = chain = None
        try:
            layout = detect_layout(sp).layout
            chain = read_chain(sp)
        except Exception:
            pass
        files.append({"path": str(sp), "name": sp.name, "layout": layout, "chain": chain})
        if layout:
            layouts[layout] = layouts.get(layout, 0) + 1
    header_fields = {}
    detail_sections = {}
    for name in layouts:
        lay = registry.get(name)
        if lay:
            header_fields[name] = _header_fields_for_layout(lay, config)
            detail_sections[name] = _detail_sections_for_layout(lay, config)
    return {
        "files": files, "layouts": layouts,
        "header_fields": header_fields, "detail_sections": detail_sections,
    }


def _detail_sections_for_layout(layout, config: Config) -> List[dict]:
    """Metadata for each non-header section, for the bulk record/field ops."""
    out = []
    locked = config.readonly_fields(layout.name) | config.hidden_fields(layout.name)
    for sec in layout.sections[1:]:
        fields = [{
            "name": f.name, "size": f.size, "type": f.field_type,
            "options": config.options(f.name, layout=layout.name,
                                      section=sec.name) or None,
            **({"date": True} if config.date_format(layout.name, f.name) else {}),
        } for f in sec.fields
            if f.name not in locked and not getattr(f, "readonly", False)]
        out.append({
            "name": sec.name,
            "fields": fields,
            "max_records": config.max_records(layout.name, sec.name),
            "count_field": config.count_field(layout.name, sec.name),
        })
    return out


def _sync_count(okf, layout_name, section_name, count, config):
    """Keep the header count field in sync with a section's record count."""
    cf = config.count_field(layout_name, section_name)
    if not cf or not okf.records:
        return
    header = okf.records[0]
    try:
        f = header._field(cf)  # noqa: SLF001
    except KeyError:
        return
    if f.size is None:
        return
    val = str(count).zfill(f.size)
    if len(val) <= f.size:
        header.set(cf, val)


def _bulk_op_eval(sp: Path, layout_name, section_name, op, registry, config):
    """Evaluate a detail/header bulk op on one file (no write). Carries 'okf'
    on a real change so apply can save it."""
    name = sp.name
    try:
        if detect_layout(sp).layout != layout_name:
            return {"name": name, "status": "skipped"}
        okf = parse_okfile(sp, registry=registry)
    except Exception as exc:
        return {"name": name, "status": "error", "error": str(exc)}
    grouped = okf.sections()
    sec = next((s for s in okf.layout.sections if s.name == section_name), None)
    if sec is None:
        return {"name": name, "status": "no_section"}
    recs = grouped.get(section_name, [])
    header_name = okf.layout.sections[0].name if okf.layout.sections else None
    t = op.get("type")
    # In a zero-filled section (Preticket's Lane) the trailing all-zero rows are
    # structural padding, not data — field ops must not write into them (that
    # would turn filler into "real" rows). Count/keep ops still see all rows.
    if config is not None and config.zero_fill(layout_name, section_name) \
            and t in ("set", "random", "unique", "list"):
        hidden = config.hidden_fields(layout_name)
        recs = [r for r in recs if not _is_blank_row(r, hidden)]
    before = len(recs)
    # Every op except "add" needs existing rows to work on; "add" can seed the
    # first row into an empty section from its reference line.
    if not recs and t != "add":
        return {"name": name, "status": "no_section"}

    _is_date = (op.get("field") and config is not None
                and bool(config.date_format(layout_name, op.get("field"))))
    if t == "random_date" or (t in ("set", "list") and _is_date):
        # ---- temporal fields (config/date_fields.yaml) ----
        # Handled before the width gate below: a date field need not declare a
        # size (the Calgary JSON `timestamp` has none) — its FORMAT decides the
        # length. A sized field is still width-checked by Record.set(), which is
        # what turns an over-long stamp into a clean `too_wide` result.
        field = op.get("field")
        if not next((x for x in sec.fields if x.name == field), None):
            return {"name": name, "status": "missing_field"}
        if not _is_date:
            # Only a configured temporal field has a format to render into —
            # say that plainly rather than falling through to "unknown op".
            return {"name": name, "status": "error",
                    "error": f"{field} is not a date field "
                             f"(add it to config/date_fields.yaml)"}
        fmt = config.date_format(layout_name, field)
        from okgen import datetimes
        try:
            if t == "random_date":
                # A date RANGE, not a numeric one: each row gets its OWN instant.
                for r in recs:
                    r.set(field, datetimes.random_between(op.get("from"),
                                                          op.get("to"), fmt),
                          literal=True)
                detail = (f"random {field} between {op.get('from')} and "
                          f"{op.get('to')} on {before} row(s)")
            elif t == "list":
                # Every listed value is normalized, so a list of plain dates is
                # written in the field's full format.
                values = _clean_values(op.get("values"))
                if not values:
                    return {"name": name, "status": "error", "error": "no values given"}
                for r in recs:
                    r.set(field, datetimes.normalize(random.choice(values), fmt),
                          literal=True)
                detail = f"{field} from {len(values)} listed date(s) on {before} row(s)"
            else:                                   # set
                value = datetimes.normalize(op.get("value", ""), fmt)
                for r in recs:
                    r.set(field, value, literal=True)
                detail = f"{field} = {value} on {before} row(s)"
        except datetimes.DateError as exc:
            return {"name": name, "status": "error", "error": str(exc)}
        except ValueError as exc:                   # too long for a sized field
            return {"name": name, "status": "too_wide", "detail": str(exc)}
        return {"name": name, "status": "change", "detail": detail, "okf": okf}

    if t in ("set", "random", "unique", "list"):
        field = op.get("field")
        fdef = next((x for x in sec.fields if x.name == field), None)
        if fdef is None:
            return {"name": name, "status": "missing_field"}
        # A temporal field's typed value is normalized to its format first.
        if t == "set" and config is not None and config.date_format(layout_name, field):
            try:
                op = dict(op, value=_coerce_date(layout_name, field,
                                                 op.get("value", ""), config))
            except EditError as exc:
                return {"name": name, "status": "error", "error": str(exc)}
        size = fdef.size
        if size is None:
            return {"name": name, "status": "error", "error": f"{field} has no fixed width"}

        if t == "set":
            raw = op.get("value", "")
            blank = isinstance(raw, str) and _is_blank_token(raw)   # ' ' -> blank spaces
            value = "" if blank else raw
            if len(value) > size:
                return {"name": name, "status": "too_wide", "detail": f"value too long for {field}"}
            changed = 0
            first_old = recs[0].get(field) if recs else None
            # a blank token writes spaces on any field (numeric included)
            literal = blank or (config is not None and config.is_literal(layout_name, field))
            if not blank:
                value = _pad_zero_value(value, layout_name, field, size, config)
            for r in recs:
                cur = r.get(field)
                r.set(field, value, literal=literal)
                if r.get(field) != cur:
                    changed += 1
            if changed == 0:
                return {"name": name, "status": "unchanged", "detail": f"{before} row(s)"}
            detail = (f"{field}: {first_old!r} -> {value!r}" if before == 1
                      else f"set {field} on {changed}/{before} row(s)")
            return {"name": name, "status": "change", "detail": detail, "okf": okf}

        if t == "list":
            # Pick from a user-supplied set of allowed values — one pick per
            # ROW for a detail section, and (since the header section holds a
            # single record) one pick per FILE for the header.
            values = _clean_values(op.get("values"))
            if not values:
                return {"name": name, "status": "error",
                        "error": "give at least one value to choose from"}
            too_wide = [v for v in values if len(v) > size]
            if too_wide:
                return {"name": name, "status": "too_wide",
                        "detail": f"value(s) too long for {field} ({size}): "
                                  + ", ".join(repr(v) for v in too_wide[:3])}
            base_literal = config is not None and config.is_literal(layout_name, field)
            for r in recs:
                pick = random.choice(values)
                # a blank ("") choice writes spaces on any field
                # a blank ("") or space-padded pick writes literal spaces on any
                # field (numeric included, which would otherwise zero-fill)
                padded = _pad_zero_value(pick, layout_name, field, size, config)
                r.set(field, padded,
                      literal=base_literal or pick != pick.strip() or pick == "")
            return {"name": name, "status": "change",
                    "detail": f"{field} from {len(values)} allowed value(s) "
                              f"on {before} row(s)", "okf": okf}

        if t == "random":
            hi = 10 ** size - 1
            rmin = op.get("min")
            rmax = op.get("max")
            lo = 0 if rmin in (None, "") else int(rmin)
            high = hi if rmax in (None, "") else int(rmax)
            if lo < 0:
                lo = 0
            if high > hi:
                return {"name": name, "status": "too_wide",
                        "detail": f"max {high} exceeds field width {size}"}
            if lo > high:
                return {"name": name, "status": "error", "error": "min is greater than max"}
            for r in recs:
                r.set(field, str(random.randint(lo, high)).zfill(size))
            rng = f" in [{lo}..{high}]" if (rmin not in (None, "") or rmax not in (None, "")) else ""
            return {"name": name, "status": "change",
                    "detail": f"random {field}{rng} on {before} row(s)", "okf": okf}

        # unique: sequential from a start value, per file (each file restarts)
        start = int(op.get("start", 1))
        last = start + before - 1
        if len(str(last)) > size:
            return {"name": name, "status": "too_wide",
                    "detail": f"start {start} + {before} rows overflows width {size}"}
        for i, r in enumerate(recs):
            r.set(field, str(start + i).zfill(size))
        return {"name": name, "status": "change",
                "detail": f"{field}: {str(start).zfill(size)}..{str(last).zfill(size)} ({before} rows)",
                "okf": okf}

    if t in ("add", "keep"):
        if section_name == header_name:
            return {"name": name, "status": "error", "error": "Add/Keep not valid on Header"}
        n = int(op.get("count", 0))

        if t == "add":
            # In a FILL-MANAGED section (Preticket Lane) the trailing all-zero
            # rows are structural filler, not data. `recs` here still includes
            # them (the filler exclusion above only covers set/random/unique/list),
            # so cloning the last row would clone a FILLER row — another all-zero
            # row that `_apply_detail_fill` re-absorbs, adding nothing real. Clone
            # the last REAL row instead, and count/limit against real rows; the
            # fill pass re-establishes the filler block (real + N filler) after.
            # A JSON section is treated the same way, for D45's skeleton: the
            # one all-null row an emptied section keeps is not a real row, and
            # cloning it hands back the blank row this seeding exists to avoid.
            fill_managed = config is not None and config.zero_fill(layout_name, section_name)
            json_mode = getattr(okf.layout, "json_mode", False)
            if fill_managed or (json_mode and config is not None):
                hidden = config.hidden_fields(layout_name)
                real = [r for r in recs if not _is_blank_row(r, hidden)]
            else:
                real = recs
            # An all-blank JSON section is replaced by the rows being added, so
            # `add 3` yields 3 real rows rather than a leftover marker plus 3.
            drop_blanks = _json_blank_rows_to_replace(okf, sec, config)
            base = len(real)
            limit = config.max_records(layout_name, section_name)
            room = max(0, (limit - base)) if limit is not None else n
            to_add = min(n, room)
            if to_add <= 0:
                note = "at limit" if (limit is not None and base >= limit) else "nothing to add"
                return {"name": name, "status": "unchanged", "detail": f"{base} row(s); {note}"}
            if real:
                template = real[-1]                       # clone the last REAL row
                insert_at = okf.records.index(template) + 1
                seeded = 0
            else:
                # No real rows (empty section, or only filler): seed the first row
                # from the reference line and place it in canonical section order.
                template = _seed_record(okf, sec, config)
                if template is None:
                    return {"name": name, "status": "error",
                            "error": f"section '{section_name}' has no template to seed a row"}
                insert_at = _insert_in_section_order(okf, template, sec) + 1
                seeded = 1
            for r in drop_blanks:
                okf.records.remove(r)
                if okf.records.index(template) + 1 < insert_at:
                    insert_at -= 1
            for i in range(to_add - seeded):
                okf.records.insert(insert_at + i, _clone_record(okf, template))
            _normalize_eols(okf)
            new_count = base + to_add
            _sync_count(okf, layout_name, section_name, new_count, config)
            capped = n - to_add
            detail = f"{base} -> {new_count}" + (f"  (capped, {capped} not added)" if capped else "")
            return {"name": name, "status": "change", "detail": detail, "okf": okf}

        # keep first N
        target = max(0, min(n, before))
        if target >= before:
            return {"name": name, "status": "unchanged", "detail": f"{before} row(s) (<= {n})"}
        for r in recs[target:]:
            okf.records.remove(r)
        _normalize_eols(okf)
        _sync_count(okf, layout_name, section_name, target, config)
        return {"name": name, "status": "change", "detail": f"{before} -> {target}", "okf": okf}

    return {"name": name, "status": "error", "error": f"unknown op '{t}'"}


def bulk_op_preview(paths, layout, section, op, registry, config) -> dict:
    results = []
    for p in paths or []:
        r = _bulk_op_eval(Path(p), layout, section, op, registry, config)
        r.pop("okf", None)
        r["path"] = str(p)
        results.append(r)
    return {"results": results}


def bulk_op_apply(paths, layout, section, op, registry, config, backup=True) -> dict:
    results = []
    for p in paths or []:
        sp = Path(p)
        r = _bulk_op_eval(sp, layout, section, op, registry, config)
        okf = r.pop("okf", None)
        r["path"] = str(p)
        if r["status"] == "change" and okf is not None:
            try:
                _apply_detail_fill(okf, config)   # keep Preticket-style filler rows
                _apply_json_empty_rows(okf, config)
                _apply_rollups(okf, config)     # header totals follow their detail rows
                _backup_and_save(okf, sp, backup)
                r["status"] = "changed"
            except (OSError, EditError) as exc:
                r["status"] = "error"
                r["error"] = str(exc)
        results.append(r)
    return {"results": results}


def _bulk_eval(sp: Path, layout_name: str, field: str, value: str, registry,
               config: Config = None):
    """Evaluate the header-field set for one file (no write). Returns a result
    dict; on a real change it also carries the in-memory OkFile under 'okf'."""
    name = sp.name
    try:
        if detect_layout(sp).layout != layout_name:
            return {"name": name, "status": "skipped"}
        okf = parse_okfile(sp, registry=registry)
    except Exception as exc:
        return {"name": name, "status": "error", "error": str(exc)}
    if not okf.records:
        return {"name": name, "status": "error", "error": "no records"}
    header = okf.records[0]
    try:
        f = header._field(field)  # noqa: SLF001
    except KeyError:
        return {"name": name, "status": "missing"}
    current = header.get(field)
    if f.size is not None and len(value) > f.size:
        return {"name": name, "status": "too_wide", "current": current, "new": value}
    # Chain isolation (D9) applies HERE too, not just to a single-file save.
    # Bulk wrote straight through, so one apply could move a whole selection
    # across the Europe boundary in either direction — the one place it would
    # hurt most, and invisible because chain is not a detection signature.
    if field == "chain" and config is not None \
            and not config.can_change_chain((current or "").strip(), value.strip()):
        return {"name": name, "status": "error", "current": current, "new": value,
                "error": (f"chain cannot change from {(current or '').strip()} to "
                          f"{value.strip()}: Europe is isolated from the other chains")}
    value = _pad_zero_value(value, okf.layout.name, field, f.size, config)
    header.set(field, value,
               literal=config is not None and config.is_literal(okf.layout.name, field))
    new = header.get(field)
    if new == current:
        return {"name": name, "status": "unchanged", "current": current, "new": new}
    # Some fields are only PARTIAL detection signatures — legitimate to edit, but
    # a few values collide with another layout's rule (CartonLabel 'format' set
    # to N/Y/7/9 reads as StyleHeader/Preticket/DistLabels). Surface that in the
    # preview instead of letting the user apply and hit a per-file error.
    try:
        _assert_layout_stable(okf)
    except EditError as exc:
        return {"name": name, "status": "error", "current": current, "new": new,
                "error": str(exc)}
    return {"name": name, "status": "change", "current": current, "new": new, "okf": okf}


def bulk_preview(paths, layout_name, field, value, registry, config) -> dict:
    results = []
    for p in paths or []:
        r = _bulk_eval(Path(p), layout_name, field, value, registry, config)
        r.pop("okf", None)
        r["path"] = str(p)
        results.append(r)
    return {"results": results}


def bulk_apply(paths, layout_name, field, value, registry, config, backup=True) -> dict:
    results = []
    for p in paths or []:
        sp = Path(p)
        r = _bulk_eval(sp, layout_name, field, value, registry, config)
        okf = r.pop("okf", None)
        r["path"] = str(p)
        if r["status"] == "change" and okf is not None:
            try:
                _apply_detail_fill(okf, config)   # keep Preticket-style filler rows
                _apply_json_empty_rows(okf, config)
                _apply_rollups(okf, config)     # header totals follow their detail rows
                _backup_and_save(okf, sp, backup)
                r["status"] = "changed"
            except (OSError, EditError) as exc:
                r["status"] = "error"
                r["error"] = str(exc)
        results.append(r)
    return {"results": results}


# --------------------------------------------------------------------------- #
# Bulk rename
# --------------------------------------------------------------------------- #
DERIVED_TOKENS = ["brand", "format_label", "region", "layout", "key", "seq", "orig"]
_INVALID_NAME = set('\\/:*?"<>|')


def _strip_invalid(s: str) -> str:
    return "".join(c for c in str(s) if c not in _INVALID_NAME)


def _sanitize_label(s: str) -> str:
    return _strip_invalid(str(s).replace(" ", "_"))


def _file_tokens(path: Path, registry, config, custom: dict) -> dict:
    """Resolve all rename-token values for one file.

    Field values come from the header first; any field not in the header is
    taken from the first record of the detail section that has it (e.g. for
    Preticket, comp_price/comp_up/ret_price/type live in the Lane section).
    """
    return _tokens_from_okf(parse_okfile(path, registry=registry), config,
                            orig_stem=path.stem, custom=custom)


def _tokens_from_okf(okf: OkFile, config: Config, orig_stem: str, custom: dict) -> dict:
    """Resolve rename-token values from an ALREADY-PARSED file.

    Split out of :func:`_file_tokens` so volume generation can name a file it
    holds in memory — the values must come from the generated record, not from
    whatever is on disk (nothing is on disk yet).
    """
    layout = okf.layout.name
    toks: dict = {}
    for _secname, recs in okf.sections().items():   # header section comes first
        if not recs or recs[0].section is None:
            continue
        first = recs[0]
        for f in first.section.fields:
            v = first.get(f.name)
            if v is not None:
                toks.setdefault(f.name, v.strip())   # don't override a header value
    chain = toks.get("chain", "")
    toks["chain"] = chain
    toks["brand"] = config.chain_name(chain)
    toks["layout"] = layout
    fmt = toks.get("format", "")
    toks["format_label"] = config.label("format", fmt, chain=chain, layout=layout, fmt=fmt) if fmt else ""
    kf = config.unique_field(layout)
    toks["key"] = toks.get(kf, "") if kf else ""
    toks["region"] = config.region(toks.get("zone", ""))
    toks["orig"] = orig_stem
    # Derived (computed) fields — e.g. EUCartonLabel's `format` — aren't in the
    # raw record; resolve them from the driving fields already in ``toks``.
    for spec in config.derived_fields(layout):
        toks[spec["name"]] = config.eval_derived(spec, toks)
    for cname, cval in (custom or {}).items():
        toks[cname] = cval
    return toks


def _build_name(parts, toks, separator, seq, seq_pad=4, label_names=None) -> str:
    """Join ordered parts into a filename stem. A {'type': 'glue'} part means the
    next value attaches with NO separator. Empty values are skipped.

    ``label_names`` are tokens whose values may contain spaces (brand,
    format_label, derived fields) and so are space-to-underscore sanitized."""
    label_names = label_names or {"brand", "format_label"}
    out = ""
    glue = False
    for part in parts or []:
        ptype = part.get("type")
        if ptype == "glue":
            glue = True
            continue
        if ptype == "text":
            v = _strip_invalid(part.get("value", ""))
        else:
            name = part.get("name") or part.get("value", "")
            if name == "seq":
                v = str(seq).zfill(seq_pad)
            elif name in label_names:
                v = _sanitize_label(toks.get(name, ""))
            else:
                v = _strip_invalid(toks.get(name, ""))
        if v == "":
            continue
        out = v if out == "" else out + ("" if glue else separator) + v
        glue = False
    return out


def rename_scope(paths, registry, config) -> dict:
    """Files + the token palette (filtered by config) + sample values (file 1)."""
    files = []
    layouts = []
    for p in paths or []:
        try:
            layout = detect_layout(p).layout
        except Exception:
            layout = None
        files.append({"path": str(p), "name": Path(p).name, "layout": layout})
        if layout and layout not in layouts:
            layouts.append(layout)

    # Union of fields across ALL sections (header + detail), so detail-only
    # fields (e.g. Preticket comp_price/comp_up/ret_price/type) are offered too.
    header_union, seen = [], set()
    for lay in layouts:
        L = registry.get(lay)
        if not L:
            continue
        for sec in L.sections:
            for f in sec.fields:
                if f.name not in seen:
                    seen.add(f.name)
                    header_union.append(f.name)
        # Derived fields (e.g. EUCartonLabel `format`) aren't layout fields but
        # are valid rename tokens — offer them in the palette too.
        for spec in config.derived_fields(lay):
            if spec.get("name") and spec["name"] not in seen:
                seen.add(spec["name"])
                header_union.append(spec["name"])

    groups = config.rename_token_groups()
    if groups is None:
        palette = {"derived": list(DERIVED_TOKENS), "header_fields": header_union, "custom": {}}
    else:
        ad, ah = set(groups["derived"]), set(groups["header_fields"])
        palette = {
            "derived": [t for t in DERIVED_TOKENS if t in ad],
            "header_fields": [f for f in header_union if f in ah],
            "custom": dict(groups["custom"]),
        }

    sample = {}
    if paths:
        try:
            sample = _file_tokens(Path(paths[0]), registry, config, palette["custom"])
        except Exception:
            sample = {}
    return {"files": files, "palette": palette, "sample": sample,
            "presets": config.rename_presets()}


def bulk_rename_preview(paths, parts, separator, registry, config) -> dict:
    groups = config.rename_token_groups()
    custom = groups["custom"] if groups else {}
    from collections import defaultdict

    by_folder = defaultdict(list)
    for p in paths or []:
        by_folder[str(Path(p).parent)].append(Path(p))

    label_names = {"brand", "format_label"} | config.all_derived_names()
    results = []
    for folder, files in by_folder.items():
        folder = Path(folder)
        selected = {f.name for f in files}
        used = {e.name for e in folder.iterdir() if e.is_file()} - selected
        seq = 0
        for f in files:
            seq += 1
            try:
                toks = _file_tokens(f, registry, config, custom)
            except Exception as exc:
                results.append({"path": str(f), "old": f.name, "new": None, "status": "error", "error": str(exc)})
                continue
            base = _build_name(parts, toks, separator, seq, label_names=label_names)
            if not base:
                results.append({"path": str(f), "old": f.name, "new": None, "status": "empty"})
                continue
            ext = f.suffix or ".OK"      # a Calgary file stays .json
            cand = base + ext
            i = 1
            while cand in used:
                cand = f"{base}_{i:03d}{ext}"
                i += 1
            used.add(cand)
            results.append({"path": str(f), "old": f.name, "new": cand,
                            "status": "unchanged" if cand == f.name else "rename"})
    return {"results": results}


def bulk_rename_apply(paths, parts, separator, registry, config) -> dict:
    """Two-phase rename (temp names first) so in-batch name swaps can't clobber."""
    from collections import defaultdict

    pv = bulk_rename_preview(paths, parts, separator, registry, config)["results"]
    results = []
    by_folder = defaultdict(list)
    for r in pv:
        if r["status"] == "rename":
            by_folder[str(Path(r["path"]).parent)].append(r)
        else:
            results.append(r)

    for folder, rs in by_folder.items():
        folder = Path(folder)
        staged = []
        for idx, r in enumerate(rs):
            src = Path(r["path"])
            tmp = folder / f".okgentmp_{idx}_{r['new']}"
            try:
                fs.rename(src, tmp)
                staged.append((tmp, folder / r["new"], r))
            except OSError as exc:
                results.append({**r, "status": "error", "error": str(exc)})
        for tmp, final, r in staged:
            try:
                fs.rename(tmp, final)
                results.append({**r, "status": "renamed"})
            except OSError as exc:
                results.append({**r, "status": "error", "error": str(exc)})
    return {"results": results}


# --------------------------------------------------------------------------- #
# Folder operations
# --------------------------------------------------------------------------- #
_BAD_NAME_CHARS = set('\\/:*?"<>|')


def create_folder(parent, name) -> dict:
    pp = Path(parent)
    if not pp.is_dir():
        raise EditError(f"not a folder: {pp}")
    name = (name or "").strip()
    if not name or any(c in _BAD_NAME_CHARS for c in name):
        raise EditError("invalid folder name")
    target = pp / name
    if fs.exists(target):
        raise EditError(f"already exists: {target}")
    fs.mkdir(target)
    return {"created": str(target)}


def rename_folder(src, dst) -> dict:
    s, d = Path(src), Path(dst)
    if not s.is_dir():
        raise EditError(f"not a folder: {s}")
    if fs.exists(d):
        raise EditError(f"destination exists: {d}")
    fs.rename(s, d)
    return {"renamed": str(s), "to": str(d)}


def delete_folder(path) -> dict:
    p = Path(path)
    if not p.is_dir():
        raise EditError(f"not a folder: {p}")
    shutil.rmtree(p)
    return {"deleted": str(p)}


def rename_file(src, dst) -> dict:
    s, d = Path(src), Path(dst)
    if not _editable_file(s):
        raise EditError(f"not an editable file: {s}")
    if fs.exists(d):
        raise EditError(f"destination exists: {d}")
    fs.mkdir(d.parent, parents=True, exist_ok=True)
    fs.rename(s, d)
    return {"renamed": str(s), "to": str(d)}


# --------------------------------------------------------------------------- #
# Volume generation — many files from one template
# --------------------------------------------------------------------------- #
GENERATE_MAX = 5000        # hard ceiling; the UI offers 100/200/500/1000


def _as_paths(paths) -> List[Path]:
    """Normalise a single path or a list of paths to a list of Path."""
    if paths is None:
        return []
    if isinstance(paths, (str, Path)):
        return [Path(paths)]
    return [Path(p) for p in paths]


def _templates_layout(paths: List[Path], registry, config):
    """The single shared layout of a template set. Raises if they differ, since
    one batch draws from one field set / key space."""
    layouts = []
    for p in paths:
        try:
            lay = detect_layout(p).layout
        except Exception:
            lay = None
        if lay and lay not in layouts:
            layouts.append(lay)
    if not layouts:
        raise EditError("could not detect a layout for the selected template(s)")
    if len(layouts) > 1:
        raise EditError(
            "all selected templates must be the same layout to generate together "
            f"(got {', '.join(layouts)})")
    return layouts[0]


def generate_scope(paths, registry: LayoutRegistry, config: Config,
                   source: Optional[str] = None) -> dict:
    """What can be varied when generating volume files from one OR MORE templates.

    With several templates each generated file draws its base record from a
    RANDOM template of the set — but they must all be the same layout, since the
    randomizable fields, key space and name palette come from that one layout.

    Locked fields (a layout's detection signature, hidden markers) and the key
    field are excluded: the key is always assigned uniquely, and a randomized
    signature byte would make every generated file undetectable.
    """
    tpaths = _as_paths(paths)
    if not tpaths:
        raise FileNotFoundError("no template selected")
    layout_name = _templates_layout(tpaths, registry, config)
    sp = tpaths[0]
    view = parse_file_view(sp, registry, config, source)
    layout = registry.get(layout_name)
    key_field = _unique_field_for(sp, layout_name, config, source)
    locked = config.readonly_fields(layout_name) | config.hidden_fields(layout_name)

    def numeric_fields(fields):
        """Fields a generated value can be produced for.

        Fixed-width fields get a zero-padded number; a TEMPORAL field (see
        config/date_fields.yaml) gets a random instant in a date range instead,
        and is included even though it declares no size — its format sets the
        length. `date` tells the client which input to show.
        """
        out = []
        for f in fields:
            if f.get("derived") or f["name"] in locked or f["name"] == key_field:
                continue
            if f.get("editable") is False:      # spec-level readonly (JSON `type`)
                continue
            dfmt = config.date_format(layout_name, f["name"])
            if not f.get("size") and not dfmt:
                continue
            entry = {"name": f["name"], "size": f["size"]}
            if dfmt:
                entry["date"] = True
            out.append(entry)
        return out

    sections = []
    for sec in view["sections"]:
        if sec["is_header"]:
            continue
        sections.append({
            "name": sec["name"],
            "rows": len(sec["records"]),
            "max_records": sec["max_records"],
            "fields": numeric_fields(sec["fields"]),
        })

    return {
        "path": str(sp),
        "name": sp.name,
        "paths": [str(p) for p in tpaths],
        "template_count": len(tpaths),
        "layout": layout_name,
        "key_field": key_field,
        "key_size": getattr(_header_field(layout, key_field), "size", None),
        "header_fields": numeric_fields(view["sections"][0]["fields"]),
        "sections": sections,
        "palette": rename_scope([str(sp)], registry, config)["palette"],
        "max_count": GENERATE_MAX,
        "default_folder": str(_generate_folder(tpaths, 0, layout_name, dry=True)),
    }


def _generate_folder(templates, count: int, layout_name: str, dest=None, dry=False) -> Path:
    """Destination folder for a batch: an auto-named subfolder beside the first
    template. Named for the single template's stem, or the layout when several
    templates feed the batch. Suffixed if it exists."""
    templates = _as_paths(templates)
    if dest:
        return Path(dest)
    first = templates[0]
    label = first.stem if len(templates) == 1 else layout_name
    base = first.parent / f"generated_{label}_{count}"
    if dry:
        return base
    out, n = base, 2
    while fs.exists(out):
        out = base.with_name(f"{base.name}_{n}")
        n += 1
    return out


def _clean_values(raw) -> List[str]:
    """Normalise a user-supplied value list.

    Accepts either a real list or one comma-separated string, trims each bare
    entry and preserves order. A bare empty entry is dropped (so ``a, , b`` is
    two values). A QUOTE-WRAPPED entry keeps its interior spaces verbatim:
    ``'   msg01'`` -> ``   msg01`` (leading spaces kept), and the all-spaces
    case ``''`` / ``' '`` -> ``""`` (the explicit blank choice). Quoting is the
    only way to carry significant spaces through the comma split, which would
    otherwise trim them.
    """
    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    out = []
    for v in items:
        s = str(v).strip()
        inner = _unwrap_quoted(s)
        if inner is not None:
            out.append("" if inner.strip() == "" else inner)   # blank, or spaces kept
        elif s != "":
            out.append(s)             # bare empties dropped
    return out


def _pick_value(values: List[str], size: int, field: str) -> str:
    """One random choice from ``values``, validated against the field width."""
    too_wide = [v for v in values if len(v) > size]
    if too_wide:
        raise EditError(
            f"value(s) too long for {field} (max {size}): "
            + ", ".join(repr(v) for v in too_wide[:3]))
    return random.choice(values)


def _rand_padded(size: int, lo, hi) -> str:
    """A random number as a zero-padded string that fits ``size`` characters."""
    ceiling = 10 ** size - 1
    low = 0 if lo in (None, "") else max(0, int(lo))
    high = ceiling if hi in (None, "") else min(ceiling, int(hi))
    if low > high:
        raise EditError(f"min {low} is greater than max {high}")
    return str(random.randint(low, high)).zfill(size)


def _spec_value(spec: dict, size: int, field: str, date_format: str = None,
                chains: "Optional[List[str]]" = None) -> str:
    """The value for one generated field.

    Precedence: the user's value list, then — for a temporal field — a random
    instant in their date range, then a random number in their min/max range.
    A date field varies per generated file (or per row) exactly like a numeric
    one; only the way a value is produced differs.
    """
    values = _clean_values(spec.get("values"))
    if chains is not None and not values:
        # `chain` is not a free number: only a real banner is meaningful, and
        # crossing an isolation boundary is forbidden (D9). Randomizing 1..99
        # used to emit codes like 08/15/51 that are not banners at all, and
        # could land on Europe's 05 in a North-America file.
        return random.choice(chains)
    if values and date_format:
        from okgen import datetimes
        return datetimes.normalize(random.choice(values), date_format)
    if values:
        return _pick_value(values, size, field)
    if date_format:
        from okgen import datetimes
        lo, hi = spec.get("from"), spec.get("to")
        if not (lo and hi):
            raise EditError(f"{field}: give a date range (from and to) to vary it")
        return datetimes.random_between(lo, hi, date_format)
    return _rand_padded(size, spec.get("min"), spec.get("max"))


def _field_in_section(layout, section_name: str, field_name: str):
    sec = next((s for s in layout.sections if s.name == section_name), None)
    if sec is None:
        return None
    return next((f for f in sec.fields if f.name == field_name), None)


def _set_row_count(okf: OkFile, config: Config, section_name: str, target: int) -> None:
    """Grow or shrink a section to ``target`` rows, respecting max_records."""
    sec = next((s for s in okf.layout.sections if s.name == section_name), None)
    if sec is None:
        return
    limit = config.max_records(okf.layout.name, section_name)
    if limit is not None:
        target = min(target, limit)
    rows = [r for r in okf.records if r.section is sec]

    # An all-blank JSON section holds no data, so a requested count is a count
    # of REAL rows: drop the blanks and build the whole section fresh. This has
    # to happen BEFORE the grow/shrink comparison, not inside the grow branch —
    # a section of one blank row asked for one row is neither growing nor
    # shrinking, so it would keep its blank and satisfy the target with it.
    if target > 0:
        for r in _json_blank_rows_to_replace(okf, sec, config):
            okf.records.remove(r)
            rows.remove(r)

    while len(rows) > target:                     # shrink from the end
        okf.records.remove(rows.pop())
    if len(rows) < target:
        if rows:
            template, at = rows[-1], okf.records.index(rows[-1]) + 1
        else:
            template = _seed_record(okf, sec, config)
            if template is None:
                return                            # nothing to seed from — leave empty
            at = _insert_in_section_order(okf, template, sec) + 1
            rows.append(template)
        while len(rows) < target:
            # Clone through the engine that owns the record. This used to build
            # a fixed-width Record from ``template.raw``, which is '' on a
            # JsonRecord — so the clone carried no array_path and the JSON
            # serializer ignored it. Volume Generate could therefore not add a
            # row to ANY JSON section, populated or empty, while reporting
            # success: asking for 15 stores on a 10-store file wrote 10. Only
            # the SHRINK direction worked, which is why it went unnoticed.
            clone = _clone_record(okf, template)
            okf.records.insert(at, clone)
            rows.append(clone)
            at += 1
    _normalize_eols(okf)
    _reindex(okf)


def _generate_one(okf: OkFile, spec: dict, config: Config,
                  key_field: str, key_size: int, key_int: int,
                  key_parts: "Optional[KeyParts]" = None) -> None:
    """Apply one generated file's variations to a freshly parsed template."""
    # 1. row counts first — new rows then get their own random values
    for rc in spec.get("row_counts") or []:
        lo, hi = rc.get("min"), rc.get("max")
        lo = 1 if lo in (None, "") else max(0, int(lo))
        hi = lo if hi in (None, "") else int(hi)
        if lo > hi:
            raise EditError(f"row count min {lo} is greater than max {hi}")
        _set_row_count(okf, config, rc["section"], random.randint(lo, hi))

    header = okf.records[0] if okf.records else None
    # Banners this file may legally become — its own group only (D9).
    chain_choices = (config.chains_like((header.get("chain") or "").strip())
                     if (header is not None and config is not None
                         and "chain" in [h.get("name") for h in
                                         (spec.get("header_fields") or [])])
                     else None)

    # 2. the unique key — always, so no two generated files collide
    if header is not None and key_field and key_size:
        kp = key_parts or KeyParts("", None, "", 0)
        header.set(key_field, _format_key(kp.prefix, key_int, key_size,
                                          kp.suffix, kp.width))

    # 3. user-chosen header fields — one value per FILE
    for hf in spec.get("header_fields") or []:
        f = _header_field(okf.layout, hf["name"])
        dfmt = config.date_format(okf.layout.name, hf["name"]) if config else None
        # A temporal field need not declare a size — its format sets the length.
        if f is None or header is None or (not f.size and not dfmt):
            continue
        val = _spec_value(hf, f.size, hf["name"], dfmt,
                          chains=chain_choices if hf["name"] == "chain" else None)
        # A random number is already zfilled by `_rand_padded`, but a value the
        # user LISTED arrives exactly as typed — so `202` reached a JSON store
        # unpadded. Pad here, like every other write path (D34).
        val = _pad_zero_value(val, okf.layout.name, hf["name"], f.size, config)
        base = config is not None and config.is_literal(okf.layout.name, hf["name"])
        header.set(hf["name"], val,                              # blank/spaced -> spaces
                   literal=base or val != val.strip() or val == "")

    # 4. user-chosen detail fields — a fresh value per ROW, not per file. In a
    # zero-filled section (Preticket Lane) the trailing all-zero filler rows are
    # structural padding, not data, so they are SKIPPED — otherwise randomizing
    # a field would "activate" the filler rows into real ones.
    hidden = config.hidden_fields(okf.layout.name) if config else set()
    for df in spec.get("detail_fields") or []:
        f = _field_in_section(okf.layout, df["section"], df["name"])
        dfmt = config.date_format(okf.layout.name, df["name"]) if config else None
        if f is None or (not f.size and not dfmt):
            continue
        sec = next(s for s in okf.layout.sections if s.name == df["section"])
        fm = config is not None and config.zero_fill(okf.layout.name, df["section"])
        base = config is not None and config.is_literal(okf.layout.name, df["name"])
        for r in (r for r in okf.records if r.section is sec):
            if fm and _is_blank_row(r, hidden):
                continue                          # leave filler rows untouched
            val = _spec_value(df, f.size, df["name"], dfmt)      # varies per ROW
            val = _pad_zero_value(val, okf.layout.name, df["name"], f.size, config)
            r.set(df["name"], val,                               # blank/spaced -> spaces
                  literal=base or bool(dfmt) or val != val.strip() or val == "")


def _generate_batch(paths, spec: dict, registry, config, count: int, dest_folder=None,
                    write: bool = False, source: Optional[str] = None):
    """Build ``count`` generated files from ONE OR MORE templates. Yields
    (filename, okf, notes) per file.

    Each file draws its base record from a RANDOM template of the set. Shared by
    preview (write=False, small count) and apply, so what you preview matches
    what gets written (bar the randomness, which differs run to run).
    """
    tpaths = _as_paths(paths)
    layout_name = _templates_layout(tpaths, registry, config)
    layout = registry.get(layout_name)
    key_field = _unique_field_for(tpaths[0], layout_name, config, source)
    key_size = getattr(_header_field(layout, key_field), "size", None)

    # Each template's key parts (prefix/suffix) — a generated file inherits its
    # picked template's, and keys are numbered per (layout, prefix, suffix) space.
    key_parts_by_path = {p: _read_key_int(p, registry, config, source)[3] for p in tpaths}

    # Start keys above anything already used near the templates — each template's
    # source folder AND its immediate subfolders (earlier batches live in
    # generated_* subfolders), else a later batch collides with an earlier one.
    maxv: Dict[tuple, int] = {}
    scan_dirs = set()
    for p in tpaths:
        scan_dirs.add(p.parent)
        scan_dirs.update(d for d in p.parent.iterdir() if d.is_dir())
    if dest_folder:
        scan_dirs.add(Path(dest_folder))
    for folder in scan_dirs:
        if not folder.is_dir():
            continue
        _u, m = _folder_key_state(folder, registry, config, set(), source)
        for key, hi in m.items():
            maxv[key] = max(maxv.get(key, -1), hi)
    next_key: Dict[tuple, int] = {}     # (layout, prefix, suffix) -> next int

    parts = spec.get("name_parts") or [{"type": "token", "name": "orig"},
                                       {"type": "token", "name": "seq"}]
    separator = spec.get("separator", "_")
    label_names = {"brand", "format_label"} | config.all_derived_names()
    seen_names: set = set()

    for i in range(count):
        src = random.choice(tpaths) if len(tpaths) > 1 else tpaths[0]
        key_parts = key_parts_by_path[src]
        space = key_parts.space
        if space not in next_key:
            next_key[space] = _next_key_int(maxv, layout_name, space)
        k = next_key[space]
        next_key[space] += 1

        okf = parse_okfile(src, layout=layout, registry=registry)
        _generate_one(okf, spec, config, key_field, key_size, k, key_parts=key_parts)
        _apply_detail_fill(okf, config)          # keep Preticket-style filler rows
        _apply_json_empty_rows(okf, config)
        _apply_rollups(okf, config)     # header totals follow their detail rows
        _assert_layout_stable(okf)          # a random value must never break detection

        toks = _tokens_from_okf(okf, config, orig_stem=src.stem, custom={})
        stem = _build_name(parts, toks, separator, i + 1, label_names=label_names) or src.stem
        ext = src.suffix or ".OK"       # generated JSON must stay .json
        name = f"{stem}{ext}"
        if name.lower() in seen_names:      # names need not be unique — keys are
            name = f"{stem}_{i + 1}{ext}"
        seen_names.add(name.lower())

        if write:
            _atomic_write_okf(okf, Path(dest_folder) / name, backup=False)
        yield name, okf, {"key": _format_key(key_parts.prefix, k, key_size,
                                             key_parts.suffix, key_parts.width)
                          if key_size else "", "template": src.name}


def generate_preview(paths, spec, registry: LayoutRegistry, config: Config,
                     sample: int = 5, source: Optional[str] = None) -> dict:
    """Build the first few files IN MEMORY so the user can check names/values."""
    count = int(spec.get("count") or 0)
    if count < 1:
        raise EditError("count must be at least 1")
    if count > GENERATE_MAX:
        raise EditError(f"count {count} exceeds the {GENERATE_MAX}-file limit")

    tpaths = _as_paths(paths)
    layout_name = _templates_layout(tpaths, registry, config)
    rows = []
    for name, okf, note in _generate_batch(tpaths, spec, registry, config,
                                           min(sample, count), write=False,
                                           source=source):
        header = okf.records[0] if okf.records else None
        shown = {}
        for hf in spec.get("header_fields") or []:
            if header is not None:
                shown[hf["name"]] = (header.get(hf["name"]) or "").strip()
        counts = {s.name: sum(1 for r in okf.records if r.section is s)
                  for s in okf.layout.sections[1:]}
        rows.append({"name": name, "key": note["key"], "values": shown,
                     "rows": counts, "template": note.get("template")})

    return {
        "count": count,
        "templates": len(tpaths),
        "folder": str(_generate_folder(tpaths, count, layout_name, spec.get("dest"), dry=True)),
        "sample": rows,
        "truncated": count > len(rows),
    }


def generate_apply(paths, spec, registry: LayoutRegistry, config: Config,
                   source: Optional[str] = None) -> dict:
    """Write ``count`` generated files into a fresh folder, drawing each file's
    base record from a random one of the templates. All-or-nothing on
    validation: the spec is checked by building the first file before any IO."""
    count = int(spec.get("count") or 0)
    if count < 1:
        raise EditError("count must be at least 1")
    if count > GENERATE_MAX:
        raise EditError(f"count {count} exceeds the {GENERATE_MAX}-file limit")

    tpaths = _as_paths(paths)
    if not tpaths:
        raise FileNotFoundError("no template selected")
    for p in tpaths:
        if not p.is_file():
            raise FileNotFoundError(f"not found: {p}")
    layout_name = _templates_layout(tpaths, registry, config)
    next(_generate_batch(tpaths, spec, registry, config, 1, write=False,
                         source=source))                                   # validate

    folder = _generate_folder(tpaths, count, layout_name, spec.get("dest"))
    fs.mkdir(folder, parents=True, exist_ok=True)

    written, errors = [], []
    for name, _okf, note in _generate_batch(tpaths, spec, registry, config, count,
                                            dest_folder=folder, write=True,
                                            source=source):
        written.append({"name": name, "key": note["key"]})
    return {
        "folder": str(folder),
        "written": len(written),
        "files": written[:50],          # a sample for the results table
        "errors": errors,
        "templates": [p.name for p in tpaths],
    }


# --------------------------------------------------------------------------- #
# .OK -> Calgary JSON conversion (test data generation) — see okgen/okjson.py
# --------------------------------------------------------------------------- #
def _conversion_for(path: Path, registry, config: Config):
    """(layout name, spec) for a file, or (layout, None) when it has no target."""
    try:
        layout = detect_layout(path).layout
    except Exception:                                   # noqa: BLE001
        return None, None
    return layout, config.conversion_for(layout)


def convert_scope(paths, registry, config: Config) -> dict:
    """Which of the selected files can be converted, and to what.

    Gating lives HERE as well as in the client: a client-only check is what let
    Bulk Edit reach detection-signature fields (D12), so the server decides.
    """
    files, blocked, targets = [], [], set()
    for p in _as_paths(paths):
        layout, spec = _conversion_for(p, registry, config)
        if spec is None:
            blocked.append({"file": p.name, "layout": layout,
                            "error": f"{layout or 'unknown layout'} has no JSON target"})
            continue
        files.append({"path": str(p), "name": p.name, "layout": layout,
                      "target": spec.get("target")})
        targets.add(spec.get("target"))
    return {
        "files": files,
        "blocked": blocked,
        "convertible": len(files),
        "target": sorted(targets)[0] if len(targets) == 1 else None,
        "mixed": len(targets) > 1,
        "source": (config.conversion_for(files[0]["layout"]) or {}).get("source") if files else None,
    }


def _convert_scan_dirs(sources: List[Path], out_dir: Optional[Path]) -> set:
    """Folders whose keys a new batch must not collide with.

    Each source's own folder AND its immediate subfolders — earlier batches live
    in ``converted_*`` subfolders beside the sources — plus the destination.
    Same reasoning as volume generation (D13): without it, a second run of the
    same sources reproduces the first run's keys exactly.
    """
    dirs = set()
    for p in sources:
        parent = p.parent
        dirs.add(parent)
        try:
            dirs.update(d for d in parent.iterdir() if d.is_dir())
        except OSError:                             # unreadable folder — skip
            pass
    if out_dir is not None:
        dirs.add(Path(out_dir))
    return dirs


def _convert_used_keys(sources: List[Path], out_dir: Optional[Path],
                       registry, config: Config) -> Dict[tuple, set]:
    """{(layout, prefix, suffix): {ints already taken}} near this batch.

    Keys are per numbering SPACE, so a converted CalgaryStyleHeader never
    collides with the .OK StyleHeader it came from — different layouts, separate
    spaces (D14).
    """
    used: Dict[tuple, set] = {}
    for folder in _convert_scan_dirs(sources, out_dir):
        if not folder.is_dir():
            continue
        try:
            found, _max = _folder_key_state(folder, registry, config, set())
        except OSError:                             # unreadable folder — skip
            continue
        for space, ints in found.items():
            used.setdefault(space, set()).update(ints)
    return used


def _json_empty_rows_by_array(target_layout, registry, config: Config) -> Dict[str, dict]:
    """``json_empty_rows.yaml`` re-keyed by JSON ARRAY NAME for the converter.

    The config is written per Calgary layout SECTION (``Sizes``), while
    conversion works in the template's own terms (``sizes``). The target
    layout's sections carry the JSON path, so the two are matched through it
    rather than by lowercasing a name — that would break the moment a section
    and its JSON key stop looking alike.

    Sharing this with :func:`_apply_json_empty_rows` is the point: an emptied
    section must look the same whether it was emptied by a bulk op or arrived
    empty through conversion.
    """
    layout = registry.get(target_layout) if (registry and target_layout) else None
    if layout is None or config is None:
        return {}
    out: Dict[str, dict] = {}
    for sec in layout.sections:
        jp = tuple(sec.json_path or ())
        if getattr(sec, "json_kind", None) != "array" or not jp:
            continue
        declared = config.json_empty_row(layout.name, sec.name,
                                         [f.name for f in sec.fields])
        out[jp[-1]] = declared
    return out


def _convert_one(path: Path, registry, config: Config, used_keys: Dict[tuple, set]):
    """Convert one file in memory. Returns (name, document, report)."""
    from okgen import okjson
    layout_name, spec = _conversion_for(path, registry, config)
    if spec is None:
        raise EditError(f"{layout_name or 'unknown layout'} has no JSON target")
    layout = registry.get(layout_name)
    if layout is None:
        raise EditError(f"unknown layout {layout_name!r}")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(path, layout)
    doc, report = okjson.convert(okf, layout, spec, template,
                                 empty_rows=_json_empty_rows_by_array(
                                     spec.get("target"), registry, config),
                                 # the TARGET layout's temporal fields, so a
                                 # config `value: now` is stamped in the format
                                 # that field is declared with (D29)
                                 date_formats=config.date_fields(spec.get("target")))

    # Keys stay unique across the batch. These files are SCAN because the
    # conversion emits `headerASNid: null` — the file's OWN content decides it
    # (D38, which superseded D27's folder/file-name matching). So the key is
    # `keytrol`, real .OK data rather than a fabricated ASN. A collision only
    # bumps the digit run, keeping any literal prefix/suffix intact (D14).
    key_field = spec.get("key")
    if key_field:
        header = doc["data"]["header"]
        raw = header.get(key_field)
        parts = _split_key(raw)
        if parts.value is not None:
            # Numbering space is per (layout, prefix, suffix) — 'C:00144' and
            # '00144' never displace each other (D14).
            space = (spec.get("target"),) + parts.space
            taken = used_keys.setdefault(space, set())
            n = parts.value
            while n in taken:
                n += 1
            # JSON values are trimmed strings, so render directly rather than
            # via _format_key (which pads into a FIXED-width .OK field). The
            # digit run keeps its width, so a literal suffix stays put.
            value = f"{parts.prefix}{str(n).zfill(parts.width)}{parts.suffix}"
            if value != str(raw):
                header[key_field] = value
                report.append({"field": key_field, "provenance": "unique",
                               "value": value, "source": f"re-keyed from {raw!r}"})
            taken.add(n)
    return doc, report


def convert_preview(paths, registry, config: Config, limit: int = 5) -> dict:
    """Build the first ``limit`` files IN MEMORY and write nothing.

    The preview and the apply share :func:`_convert_one`, so what you see is
    what gets written (the D13 rule that keeps a sample honest).
    """
    from okgen import okjson
    scope = convert_scope(paths, registry, config)
    sel = [Path(f["path"]) for f in scope["files"]]
    used = _convert_used_keys(sel, None, registry, config)
    samples, errors = [], list(scope["blocked"])
    for p in sel[:limit]:
        try:
            doc, report = _convert_one(p, registry, config, used)
        except (EditError, Exception) as exc:           # noqa: BLE001
            errors.append({"file": p.name, "error": str(exc)})
            continue
        counts: Dict[str, int] = {}
        for r in report:
            counts[r["provenance"]] = counts.get(r["provenance"], 0) + 1
        samples.append({
            "source": p.name,
            "name": p.with_suffix(".json").name,
            "coverage": counts,
            "report": report,
            "preview": okjson.dumps(doc).decode("utf-8"),
        })
    return {"scope": scope, "samples": samples, "errors": errors,
            "total": len(sel)}


def convert_apply(paths, registry, config: Config, dest=None) -> dict:
    """Convert every selected file into a NEW folder.

    The source files are never written. Output goes to a new auto-named folder.

    That folder's name carries a SCAN token, but it is only a LABEL: since D38
    a Calgary JSON file's source is read from its own payload, and conversion
    emits ``headerASNid: null``, so the batch resolves as SCAN whatever the
    folder is called. (Under D27 the name was load-bearing; it no longer is.)
    """
    from okgen import okjson
    scope = convert_scope(paths, registry, config)
    if scope["mixed"]:
        raise EditError("selection mixes layouts with different JSON targets — "
                        "convert one layout at a time")
    sel = [Path(f["path"]) for f in scope["files"]]
    if not sel:
        raise EditError("nothing to convert — no selected file has a JSON target")

    layout_name = scope["files"][0]["layout"]
    spec = config.conversion_for(layout_name) or {}
    out_dir = Path(dest) if dest else sel[0].parent / okjson.output_folder_name(
        spec.get("target") or layout_name, spec.get("source"), len(sel))
    if not dest:
        base, n = out_dir, 2
        while fs.exists(out_dir):
            out_dir = base.with_name(f"{base.name}_{n}")
            n += 1
    fs.mkdir(out_dir, parents=True, exist_ok=True)

    # Start above every key already used nearby, so a second batch of the same
    # sources cannot reproduce the first batch's keys.
    used = _convert_used_keys(sel, out_dir, registry, config)
    written, errors, long_paths = [], list(scope["blocked"]), []
    for p in sel:
        try:
            doc, report = _convert_one(p, registry, config, used)
            target = _unique_path(out_dir, p.with_suffix(".json").name)
            _atomic_write_bytes(target, okjson.dumps(doc))
            written.append({"source": p.name, "name": target.name})
            # Conversion LENGTHENS the path (the .OK's name plus a new
            # converted_… folder), so a source that opens fine can land beyond
            # Windows' 260-char limit. OkGen writes it either way, but Explorer
            # and whatever consumes the file may not be able to open it — say
            # so rather than let it fail somewhere with no explanation.
            if fs.is_long(target):
                long_paths.append({"name": target.name, "length": len(str(target))})
        except Exception as exc:                        # noqa: BLE001
            errors.append({"file": p.name, "error": str(exc)})
    return {"folder": str(out_dir), "written": len(written),
            "files": written[:50], "errors": errors, "long_paths": long_paths[:50],
            "max_path": fs.MAX_PATH,
            "source": spec.get("source"), "target": spec.get("target")}


def _atomic_write_bytes(out: Path, data: bytes) -> None:
    """Write bytes via a sibling .tmp + os.replace (D25), so a locked or
    read-only target fails cleanly and never leaves a half-written file."""
    tmp = out.with_suffix(out.suffix + ".tmp")
    try:
        fs.write_bytes(tmp, data)
        fs.replace(tmp, out)
    except PermissionError:
        fs.unlink(tmp, missing_ok=True)
        raise EditError(f"could not write {out.name} — the file is open in "
                        f"another program, or the folder is read-only.")
    except OSError as exc:
        fs.unlink(tmp, missing_ok=True)
        raise EditError(_write_failed_message(out, exc))


