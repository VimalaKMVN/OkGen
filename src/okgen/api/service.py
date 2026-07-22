"""Service layer for the editor backend — pure functions over the OkGen core.

Kept HTTP-free so it can be unit-tested directly. The FastAPI app in
``app.py`` is a thin wrapper over these.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from okgen.config import Config
from okgen.detect import detect_from_header, detect_layout, read_chain, read_header_line
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import ENCODING, OkFile, Record, parse_okfile

OK_SUFFIX = ".ok"  # compared case-insensitively


def is_ok_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == OK_SUFFIX


# --------------------------------------------------------------------------- #
# File tree
# --------------------------------------------------------------------------- #
def build_tree(root, config: Config, registry=None) -> dict:
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

    _flag_duplicate_keys(children)
    return {
        "type": "folder",
        "name": root.name or str(root),
        "path": str(root),
        "children": children,
    }


def _flag_duplicate_keys(children: List[dict]) -> None:
    """Mark files whose (layout, key_value) repeats within this folder."""
    counts: Dict[tuple, int] = {}
    for c in children:
        if c.get("type") == "file" and c.get("layout") and c.get("key_value") is not None:
            k = (c["layout"], c["key_value"])
            counts[k] = counts.get(k, 0) + 1
    for c in children:
        if c.get("type") == "file" and c.get("layout") and c.get("key_value") is not None:
            c["duplicate"] = counts[(c["layout"], c["key_value"])] > 1


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


def _file_node(path: Path, config: Config, registry=None) -> dict:
    chain = ""
    layout = None
    key_field = None
    key_value = None
    try:
        header = read_header_line(path)
        layout = detect_from_header(header).layout
        reg_layout = registry.get(layout) if (layout and registry is not None) else None
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
        "key_field": key_field,
        "key_value": key_value,
        "duplicate": False,
    }


# --------------------------------------------------------------------------- #
# Parse a file into an editor view
# --------------------------------------------------------------------------- #
def parse_file_view(path, registry: LayoutRegistry, config: Config) -> dict:
    """The editor view of a file **as it is on disk**."""
    path = Path(path)
    okf = parse_okfile(path, registry=registry)
    return _build_file_view(okf, path, config, disk_bytes=path.read_bytes())


def _build_file_view(okf: OkFile, path, config: Config, disk_bytes: bytes = None) -> dict:
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
                opts = config.options(f.name, chain=chain, layout=layout_name, fmt=fmt)
                editable = f.name not in readonly
                # Chain edits can't cross an isolation boundary (e.g. Europe):
                # offer only chains the current one may become; lock it when the
                # only option is itself.
                if f.name == "chain" and opts:
                    opts = {c: n for c, n in opts.items() if config.can_change_chain(chain, c)}
                    if len(opts) <= 1:
                        editable = False
                field_meta.append({
                    "name": f.name,
                    "start": f.start,
                    "size": f.size,
                    "type": f.field_type,
                    "options": opts or None,
                    "hidden": f.name in hidden,
                    "editable": editable,
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
    if getattr(okf.layout, "delimited", False):
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
        "key_field": config.unique_field(layout_name),  # unique field for this layout
        "raw_text": raw_text,  # for the Raw verify tab
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

    _assert_layout_stable(okf)
    out = Path(target_path) if target_path else src
    if backup and out.exists() and target_path is None:
        shutil.copy2(out, out.with_suffix(out.suffix + ".bak"))
    okf.save(out)

    return {
        "path": str(out),
        "written": True,
        "edits_applied": len(edits),
        "ops_applied": len(ops or []),
        "roundtrip_ok": okf.to_bytes() == out.read_bytes(),
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


def _apply_edits_to_okf(okf, edits: List[dict], config: Config = None) -> None:
    """Validate field widths, then apply edits in place. Raises EditError."""
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
        if f.size is not None and len(e["value"]) > f.size:
            errors.append({
                "edit": e,
                "error": f"value '{e['value']}' exceeds field '{e['field']}' size {f.size}",
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
        by_index[e["record_index"]].set(e["field"], e["value"])


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
            template = _seed_record(okf, sec)
            if template is None:
                raise EditError(f"section '{sec.name}' has no template to seed a row")
            seeded = True

    sec = template.section
    sec_count = sum(1 for r in okf.records if r.section is sec)
    limit = config.max_records(okf.layout.name, sec.name) if config else None
    if limit is not None and sec_count >= limit:
        raise EditError(f"section '{sec.name}' is at its limit of {limit} records")

    clone = Record(
        raw=template.raw.rstrip("\r"),       # copy values; EOL fixed up below
        offset=template.offset,
        section=template.section,
        index=template.index,                # placeholder; reassigned on reload
    )
    if seeded:
        _insert_in_section_order(okf, clone, sec)
    else:
        okf.records.insert(okf.records.index(template) + 1, clone)
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


def _backup_and_save(okf, out: Path, backup: bool) -> None:
    _assert_layout_stable(okf)          # check before the .bak, so a rejected
    if backup and out.exists():         # write leaves nothing behind at all
        shutil.copy2(out, out.with_suffix(out.suffix + ".bak"))
    okf.save(out)


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


def _seed_record(okf, sec):
    """Build a new record to start an EMPTY section, from the section's
    ``sample_raw`` seed (a real reference line). Returns None when no seed is
    known for the section (e.g. an ambiguous shared-marker section)."""
    seed = getattr(sec, "sample_raw", None)
    if not seed:
        return None
    offset = 0 if getattr(okf.layout, "delimited", False) else 1
    return Record(raw=seed.rstrip("\r"), offset=offset, section=sec, index=-1)


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
def browse_folder(initial: Optional[str] = None) -> dict:
    """Open the OS-native folder chooser and return the selected path.

    Uses each platform's own dialog (no embedded Python GUI, which can crash):
      * macOS   -> AppleScript ``choose folder`` via ``osascript``
      * Windows -> .NET ``FolderBrowserDialog`` via PowerShell
      * Linux   -> ``zenity --file-selection --directory``

    Returns {"path": None} when cancelled or when no GUI dialog is available.
    """
    import platform
    import subprocess

    system = platform.system()
    try:
        if system == "Darwin":
            prompt = "Select the folder with your OK files"
            script = f'POSIX path of (choose folder with prompt "{prompt}")'
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=600,
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
            ps = f'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Fg {{
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
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
[Fg]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
[Fg]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[Fg]::SetForegroundWindow($o.Handle) | Out-Null
$o.Activate()
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
                capture_output=True, text=True, timeout=600,
            )
        else:
            proc = subprocess.run(
                ["zenity", "--file-selection", "--directory",
                 "--title=Select the folder with your OK files"],
                capture_output=True, text=True, timeout=600,
            )
        chosen = (proc.stdout or "").strip()
        return {"path": chosen or None}
    except FileNotFoundError:
        return {"path": None, "error": "no native folder dialog available on this system"}
    except Exception as exc:  # pragma: no cover - depends on desktop session
        return {"path": None, "error": str(exc)}


# --------------------------------------------------------------------------- #
# File operations (tree actions)
# --------------------------------------------------------------------------- #
def delete_file(path) -> dict:
    p = Path(path)
    if not is_ok_file(p):
        raise EditError(f"not an .OK file: {p}")
    p.unlink()
    return {"deleted": str(p)}


def send_to_nicelabel(paths, config: Config) -> dict:
    """Copy selected .OK files into NiceLabel's incoming folder (overwriting)."""
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
            shutil.copy2(sp, dd / sp.name)   # overwrite any same-name file
            sent.append(sp.name)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
    return {"sent": sent, "errors": errors, "dest": str(dd)}


def delete_files(paths) -> dict:
    """Delete several .OK files; report per-file outcomes."""
    deleted, errors = [], []
    for path in paths or []:
        p = Path(path)
        if not is_ok_file(p):
            errors.append({"path": str(path), "error": "not an .OK file"})
            continue
        try:
            p.unlink()
            deleted.append(str(p))
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
    return {"deleted": deleted, "errors": errors}


def copy_file(src, dst) -> dict:
    s, d = Path(src), Path(dst)
    if not is_ok_file(s):
        raise EditError(f"not an .OK file: {s}")
    if d.exists():
        raise EditError(f"destination exists: {d}")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s, d)
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
    while candidate.exists():
        candidate = dst_dir / f"{stem} ({i}){suffix}"
        i += 1
    return candidate


def copy_files(srcs, dst_dir, registry=None, config=None) -> dict:
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
        if not is_dir and not is_ok_file(sp):
            errors.append({"src": str(src), "error": "not an .OK file or folder"})
            continue
        if is_dir and (dd == sp or sp in dd.parents):
            errors.append({"src": str(src), "error": "cannot paste a folder into itself"})
            continue
        target = dd / sp.name
        if target.exists():
            target = _unique_path(dd, sp.name)
            renamed.append({"from": sp.name, "to": target.name})
        try:
            if is_dir:
                shutil.copytree(sp, target)
            else:
                shutil.copy2(sp, target)
                new_ok_files.append(target)
            copied.append(str(target))
        except OSError as exc:
            errors.append({"src": str(src), "error": str(exc)})

    rekeyed = []
    if registry is not None and config is not None and new_ok_files:
        rekeyed = _uniquify_new_files(dd, new_ok_files, registry, config)
    return {"copied": copied, "renamed": renamed, "rekeyed": rekeyed, "errors": errors}


# --------------------------------------------------------------------------- #
# Unique key field
# --------------------------------------------------------------------------- #
def _read_key_int(path: Path, registry, config):
    """(layout, key_field, int_value | None) for a file's unique key."""
    try:
        header = read_header_line(path)
        layout = detect_from_header(header).layout
    except Exception:
        return (None, None, None)
    if not layout:
        return (None, None, None)
    kf = config.unique_field(layout)
    if not kf:
        return (layout, None, None)
    reg_layout = registry.get(layout)
    if getattr(reg_layout, "delimited", False):
        raw = _delim_header_value(header, reg_layout, kf)
    else:
        f = _header_field(reg_layout, kf)
        raw = _key_from_header(header, reg_layout, f)
    try:
        return (layout, kf, int(raw))
    except (TypeError, ValueError):
        return (layout, kf, None)


def _next_key_int(maxv: Dict[str, int], layout: str) -> int:
    """Next free key value for a layout: max-seen + 1, but never 0.

    Keys are always assigned a non-zero value (an all-zero key reads as a blank
    sentinel), so the first key handed out in an empty/keyless folder is 1.
    """
    return max(maxv.get(layout, -1) + 1, 1)


def _set_key(path: Path, registry, key_field: str, new_int: int, size: int, backup: bool):
    """Write a new zero-padded key value into a file's header (byte-exact)."""
    new_str = str(new_int).zfill(size)
    if len(new_str) > size:
        raise EditError(f"no available key for {path.name} (width {size} overflow)")
    okf = parse_okfile(path, registry=registry)
    okf.records[0].set(key_field, new_str)
    _assert_layout_stable(okf)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    okf.save(path)
    return new_str


def _folder_key_state(folder: Path, registry, config, exclude: set):
    """Per-layout (used-int-set, max-int) from a folder's OK files, minus excludes."""
    used: Dict[str, set] = {}
    maxv: Dict[str, int] = {}
    for entry in folder.iterdir():
        if not is_ok_file(entry) or entry.resolve() in exclude:
            continue
        layout, kf, val = _read_key_int(entry, registry, config)
        if layout and kf and val is not None:
            used.setdefault(layout, set()).add(val)
            maxv[layout] = max(maxv.get(layout, -1), val)
    return used, maxv


def _uniquify_new_files(folder: Path, new_files: List[Path], registry, config) -> List[dict]:
    """Re-key pasted files that collide with existing/earlier keys (max+1)."""
    used, maxv = _folder_key_state(folder, registry, config, {p.resolve() for p in new_files})
    rekeyed = []
    for p in new_files:
        layout, kf, val = _read_key_int(p, registry, config)
        if not (layout and kf):
            continue
        u = used.setdefault(layout, set())
        if val is not None and val not in u:
            u.add(val)
            maxv[layout] = max(maxv.get(layout, -1), val)
            continue
        f = _header_field(registry.get(layout), kf)
        new_int = _next_key_int(maxv, layout)
        try:
            new_str = _set_key(p, registry, kf, new_int, f.size, backup=False)
        except (EditError, Exception) as exc:  # noqa: BLE001
            rekeyed.append({"file": p.name, "error": str(exc)})
            continue
        u.add(new_int)
        maxv[layout] = new_int
        rekeyed.append({"file": p.name, "field": kf,
                        "from": (str(val) if val is not None else None), "to": new_str})
    return rekeyed


def make_unique_in_folder(folder, registry, config, backup=True) -> dict:
    """Fix duplicate keys in a folder (keep first occurrence, re-key the rest)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise EditError(f"not a folder: {folder}")
    files = sorted([p for p in folder.iterdir() if is_ok_file(p)], key=lambda p: p.name.lower())
    used: Dict[str, set] = {}
    maxv: Dict[str, int] = {}
    rekeyed = []
    for p in files:
        layout, kf, val = _read_key_int(p, registry, config)
        if not (layout and kf):
            continue
        u = used.setdefault(layout, set())
        if val is not None and val not in u:
            u.add(val)
            maxv[layout] = max(maxv.get(layout, -1), val)
            continue
        f = _header_field(registry.get(layout), kf)
        new_int = _next_key_int(maxv, layout)
        try:
            new_str = _set_key(p, registry, kf, new_int, f.size, backup=backup)
        except (EditError, Exception) as exc:  # noqa: BLE001
            rekeyed.append({"file": p.name, "error": str(exc)})
            continue
        u.add(new_int)
        maxv[layout] = new_int
        rekeyed.append({"file": p.name, "field": kf,
                        "from": (str(val) if val is not None else None), "to": new_str})
    return {"folder": str(folder), "rekeyed": rekeyed}


def make_unique_files(paths, registry, config, backup=True) -> dict:
    """Run Make Unique on every folder that contains a selected file."""
    folders = []
    seen = set()
    for p in paths or []:
        parent = Path(p).parent
        key = str(parent.resolve())
        if key not in seen:
            seen.add(key)
            folders.append(parent)
    results = [make_unique_in_folder(f, registry, config, backup=backup) for f in folders]
    return {"folders": results}


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
        if f.name == key or f.name in locked:
            continue
        opts = config.options(f.name, layout=layout.name)
        out.append({
            "name": f.name, "size": f.size, "type": f.field_type,
            "options": opts or None,
        })
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
            "options": config.options(f.name, layout=layout.name) or None,
        } for f in sec.fields if f.name not in locked]
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
    before = len(recs)
    # Every op except "add" needs existing rows to work on; "add" can seed the
    # first row into an empty section from its reference line.
    if not recs and t != "add":
        return {"name": name, "status": "no_section"}

    if t in ("set", "random", "unique"):
        field = op.get("field")
        fdef = next((x for x in sec.fields if x.name == field), None)
        if fdef is None:
            return {"name": name, "status": "missing_field"}
        size = fdef.size
        if size is None:
            return {"name": name, "status": "error", "error": f"{field} has no fixed width"}

        if t == "set":
            value = op.get("value", "")
            if len(value) > size:
                return {"name": name, "status": "too_wide", "detail": f"value too long for {field}"}
            changed = 0
            first_old = recs[0].get(field) if recs else None
            for r in recs:
                cur = r.get(field)
                r.set(field, value)
                if r.get(field) != cur:
                    changed += 1
            if changed == 0:
                return {"name": name, "status": "unchanged", "detail": f"{before} row(s)"}
            detail = (f"{field}: {first_old!r} -> {value!r}" if before == 1
                      else f"set {field} on {changed}/{before} row(s)")
            return {"name": name, "status": "change", "detail": detail, "okf": okf}

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
            limit = config.max_records(layout_name, section_name)
            room = max(0, (limit - before)) if limit is not None else n
            to_add = min(n, room)
            if to_add <= 0:
                note = "at limit" if (limit is not None and before >= limit) else "nothing to add"
                return {"name": name, "status": "unchanged", "detail": f"{before} row(s); {note}"}
            if recs:
                template = recs[-1]
                insert_at = okf.records.index(template) + 1
            else:
                # Empty section: seed the first row from the reference line and
                # place it in canonical section order.
                template = _seed_record(okf, sec)
                if template is None:
                    return {"name": name, "status": "error",
                            "error": f"section '{section_name}' has no template to seed a row"}
                insert_at = _insert_in_section_order(okf, template, sec) + 1
            for i in range(to_add if recs else to_add - 1):
                clone = Record(raw=template.raw.rstrip("\r"), offset=template.offset,
                               section=template.section, index=template.index)
                okf.records.insert(insert_at + i, clone)
            _normalize_eols(okf)
            new_count = before + to_add
            _sync_count(okf, layout_name, section_name, new_count, config)
            capped = n - to_add
            detail = f"{before} -> {new_count}" + (f"  (capped, {capped} not added)" if capped else "")
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
                _assert_layout_stable(okf)
                if backup and sp.exists():
                    shutil.copy2(sp, sp.with_suffix(sp.suffix + ".bak"))
                okf.save(sp)
                r["status"] = "changed"
            except (OSError, EditError) as exc:
                r["status"] = "error"
                r["error"] = str(exc)
        results.append(r)
    return {"results": results}


def _bulk_eval(sp: Path, layout_name: str, field: str, value: str, registry):
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
    header.set(field, value)
    new = header.get(field)
    if new == current:
        return {"name": name, "status": "unchanged", "current": current, "new": new}
    return {"name": name, "status": "change", "current": current, "new": new, "okf": okf}


def bulk_preview(paths, layout_name, field, value, registry, config) -> dict:
    results = []
    for p in paths or []:
        r = _bulk_eval(Path(p), layout_name, field, value, registry)
        r.pop("okf", None)
        r["path"] = str(p)
        results.append(r)
    return {"results": results}


def bulk_apply(paths, layout_name, field, value, registry, config, backup=True) -> dict:
    results = []
    for p in paths or []:
        sp = Path(p)
        r = _bulk_eval(sp, layout_name, field, value, registry)
        okf = r.pop("okf", None)
        r["path"] = str(p)
        if r["status"] == "change" and okf is not None:
            try:
                _assert_layout_stable(okf)
                if backup and sp.exists():
                    shutil.copy2(sp, sp.with_suffix(sp.suffix + ".bak"))
                okf.save(sp)
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
    okf = parse_okfile(path, registry=registry)
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
    toks["orig"] = path.stem
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
            cand = base + ".OK"
            i = 1
            while cand in used:
                cand = f"{base}_{i:03d}.OK"
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
                src.rename(tmp)
                staged.append((tmp, folder / r["new"], r))
            except OSError as exc:
                results.append({**r, "status": "error", "error": str(exc)})
        for tmp, final, r in staged:
            try:
                tmp.rename(final)
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
    if target.exists():
        raise EditError(f"already exists: {target}")
    target.mkdir()
    return {"created": str(target)}


def rename_folder(src, dst) -> dict:
    s, d = Path(src), Path(dst)
    if not s.is_dir():
        raise EditError(f"not a folder: {s}")
    if d.exists():
        raise EditError(f"destination exists: {d}")
    s.rename(d)
    return {"renamed": str(s), "to": str(d)}


def delete_folder(path) -> dict:
    p = Path(path)
    if not p.is_dir():
        raise EditError(f"not a folder: {p}")
    shutil.rmtree(p)
    return {"deleted": str(p)}


def rename_file(src, dst) -> dict:
    s, d = Path(src), Path(dst)
    if not is_ok_file(s):
        raise EditError(f"not an .OK file: {s}")
    if d.exists():
        raise EditError(f"destination exists: {d}")
    d.parent.mkdir(parents=True, exist_ok=True)
    s.rename(d)
    return {"renamed": str(s), "to": str(d)}
