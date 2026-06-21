"""Service layer for the editor backend — pure functions over the OkGen core.

Kept HTTP-free so it can be unit-tested directly. The FastAPI app in
``app.py`` is a thin wrapper over these.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional

from okgen.config import Config
from okgen.detect import detect_layout, read_chain
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import ENCODING, OkFile, parse_okfile

OK_SUFFIX = ".ok"  # compared case-insensitively


def is_ok_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == OK_SUFFIX


# --------------------------------------------------------------------------- #
# File tree
# --------------------------------------------------------------------------- #
def build_tree(root, config: Config) -> dict:
    """List ONE level of a folder (lazy tree): immediate subfolders + .OK files.

    Subfolders are returned unexpanded (``children: None``) so the UI can fetch
    them on demand when the user expands them — this keeps opening deep/large
    structures fast. Only ``.OK`` files are listed (other files are hidden);
    each file node carries its chain + chain info (for the icon) and layout.
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
            children.append(_file_node(entry, config))

    return {
        "type": "folder",
        "name": root.name or str(root),
        "path": str(root),
        "children": children,
    }


def _file_node(path: Path, config: Config) -> dict:
    chain = ""
    layout = None
    try:
        chain = read_chain(path)
        layout = detect_layout(path).layout
    except Exception:
        pass
    info = config.chain(chain)
    return {
        "type": "file",
        "name": path.name,
        "path": str(path),
        "chain": chain,
        "chain_info": info.to_dict() if info else None,
        "layout": layout,
    }


# --------------------------------------------------------------------------- #
# Parse a file into an editor view
# --------------------------------------------------------------------------- #
def parse_file_view(path, registry: LayoutRegistry, config: Config) -> dict:
    path = Path(path)
    okf = parse_okfile(path, registry=registry)
    layout_name = okf.layout.name
    chain = okf.records[0].get("chain") if okf.records else ""
    fmt = _header_value(okf, "format")
    roundtrip_ok = okf.to_bytes() == path.read_bytes()

    sections_out: List[dict] = []
    grouped = okf.sections()
    for sec_index, (sec_name, recs) in enumerate(grouped.items()):
        sec = recs[0].section if recs and recs[0].section else None
        field_meta = []
        if sec:
            for f in sec.fields:
                opts = config.options(f.name, chain=chain, layout=layout_name, fmt=fmt)
                field_meta.append({
                    "name": f.name,
                    "start": f.start,
                    "size": f.size,
                    "type": f.field_type,
                    "options": opts or None,
                })
        records_out = [
            {"index": r.index, "marker": r.marker, "values": r.values()}
            for r in recs
        ]
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
    return {
        "path": str(path),
        "name": path.name,
        "layout": layout_name,
        "chain": chain,
        "format": fmt,
        "chain_info": chain_info.to_dict() if chain_info else None,
        "roundtrip_ok": roundtrip_ok,
        "raw_text": path.read_bytes().decode(ENCODING),  # for the Raw verify tab
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
) -> dict:
    """Apply field edits and write the file (byte-exact for untouched spans).

    ``edits``: list of {section_index, record_index, field, value}.
    ``target_path``: if given, writes there (Save As); else overwrites ``path``.
    Validates each value against its field width before writing anything.
    """
    src = Path(path)
    okf = parse_okfile(src, registry=registry)
    _apply_edits_to_okf(okf, edits)

    out = Path(target_path) if target_path else src
    if backup and out.exists() and target_path is None:
        shutil.copy2(out, out.with_suffix(out.suffix + ".bak"))
    okf.save(out)

    return {
        "path": str(out),
        "written": True,
        "edits_applied": len(edits),
        "roundtrip_ok": okf.to_bytes() == out.read_bytes(),
    }


def _apply_edits_to_okf(okf, edits: List[dict]) -> None:
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
    if errors:
        raise EditError(str(errors))
    for e in edits:
        by_index[e["record_index"]].set(e["field"], e["value"])


# --------------------------------------------------------------------------- #
# Add a record to a repeating section
# --------------------------------------------------------------------------- #
def add_record(
    path,
    section_index: int,
    edits: List[dict],
    registry: LayoutRegistry,
    config: Config,
    backup: bool = True,
) -> dict:
    """Apply pending edits, append a copy of the section's last record, and save.

    The new record duplicates the last existing record of the section (its
    field values included) — users typically copy a row and tweak a few fields.
    Enforces the section's ``max_records`` limit and keeps the file well-formed.
    """
    from okgen.okfile import Record

    src = Path(path)
    okf = parse_okfile(src, registry=registry)
    _apply_edits_to_okf(okf, edits)

    grouped = list(okf.sections().items())
    if section_index < 0 or section_index >= len(grouped):
        raise EditError(f"section_index {section_index} out of range")
    sec_name, recs = grouped[section_index]
    if not recs or recs[0].section is None:
        raise EditError(f"section '{sec_name}' has no record to copy")

    limit = config.max_records(okf.layout.name, sec_name)
    if limit is not None and len(recs) >= limit:
        raise EditError(f"section '{sec_name}' is at its limit of {limit} records")

    template = recs[-1]
    clone = Record(
        raw=template.raw.rstrip("\r"),       # copy values; EOL fixed up below
        offset=template.offset,
        section=template.section,
        index=template.index,                # placeholder; reassigned on reload
    )
    insert_at = okf.records.index(template) + 1
    okf.records.insert(insert_at, clone)
    _normalize_eols(okf)

    _backup_and_save(okf, src, backup)
    view = parse_file_view(src, registry, config)
    view["added_section"] = sec_name
    return view


def delete_record(
    path,
    record_index: int,
    edits: List[dict],
    registry: LayoutRegistry,
    config: Config,
    backup: bool = True,
) -> dict:
    """Apply pending edits, delete one record by its line index, and save.

    The header record (index 0) cannot be deleted.
    """
    src = Path(path)
    okf = parse_okfile(src, registry=registry)
    _apply_edits_to_okf(okf, edits)

    target = next((r for r in okf.records if r.index == record_index), None)
    if target is None:
        raise EditError(f"record_index {record_index} not found")
    if target.index == 0:
        raise EditError("the header record cannot be deleted")

    okf.records.remove(target)
    _normalize_eols(okf)

    _backup_and_save(okf, src, backup)
    return parse_file_view(src, registry, config)


def _backup_and_save(okf, out: Path, backup: bool) -> None:
    if backup and out.exists():
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
            start = (initial or "").replace("'", "''")
            # Give the dialog a hidden top-most owner window so it appears in
            # front of the browser instead of behind it.
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
                "$d.Description = 'Select the folder with your OK files';"
                + (f"$d.SelectedPath = '{start}';" if start else "")
                + "$o = New-Object System.Windows.Forms.Form;"
                "$o.TopMost = $true; $o.ShowInTaskbar = $false; $o.Opacity = 0;"
                "$null = $o.Show(); $o.Activate();"
                "$r = $d.ShowDialog($o); $o.Close();"
                "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps],
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


def copy_files(srcs, dst_dir) -> dict:
    """Paste .OK files and/or whole folders into a folder.

    Files are copied; folders are copied recursively with all their contents.
    Never overwrites: if a name already exists, the copy is auto-renamed with a
    ' (N)' suffix (Downloads-style). Returns per-item outcomes.
    """
    dd = Path(dst_dir)
    if not dd.is_dir():
        raise EditError(f"not a folder: {dd}")
    copied, renamed, errors = [], [], []
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
            copied.append(str(target))
        except OSError as exc:
            errors.append({"src": str(src), "error": str(exc)})
    return {"copied": copied, "renamed": renamed, "errors": errors}


# --------------------------------------------------------------------------- #
# Bulk edit (B1: Header fields, one layout, set value)
# --------------------------------------------------------------------------- #
def _header_fields_for_layout(layout, config: Config) -> List[dict]:
    """Header-section field metadata for a layout, for the bulk-edit dropdowns."""
    if not layout.sections:
        return []
    header = layout.sections[0]
    out = []
    for f in header.fields:
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
    for name in layouts:
        lay = registry.get(name)
        if lay:
            header_fields[name] = _header_fields_for_layout(lay, config)
    return {"files": files, "layouts": layouts, "header_fields": header_fields}


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
                if backup and sp.exists():
                    shutil.copy2(sp, sp.with_suffix(sp.suffix + ".bak"))
                okf.save(sp)
                r["status"] = "changed"
            except OSError as exc:
                r["status"] = "error"
                r["error"] = str(exc)
        results.append(r)
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
