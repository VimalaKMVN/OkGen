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
from okgen.okfile import OkFile, parse_okfile

OK_SUFFIX = ".ok"  # compared case-insensitively


def is_ok_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == OK_SUFFIX


# --------------------------------------------------------------------------- #
# File tree
# --------------------------------------------------------------------------- #
def build_tree(root, config: Config, max_depth: int = 12) -> dict:
    """Folder tree containing ONLY .OK files (empty folders pruned).

    Each file node carries its chain code + chain info (for the icon) and the
    detected layout. Folders with no .OK file anywhere beneath them are omitted.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"not a folder: {root}")

    def walk(d: Path, depth: int) -> Optional[dict]:
        children: List[dict] = []
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            entries = []
        for entry in entries:
            if entry.is_dir():
                if depth < max_depth:
                    node = walk(entry, depth + 1)
                    if node is not None:
                        children.append(node)
            elif is_ok_file(entry):
                children.append(_file_node(entry, config))
        if not children and depth > 0:
            return None  # prune empty folder (but keep the root itself)
        return {
            "type": "folder",
            "name": d.name or str(d),
            "path": str(d),
            "children": children,
        }

    return walk(root, 0) or {"type": "folder", "name": root.name, "path": str(root), "children": []}


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
    by_index = {r.index: r for r in okf.records}

    errors: List[dict] = []
    for e in edits:
        rec = by_index.get(e["record_index"])
        if rec is None:
            errors.append({"edit": e, "error": "record_index out of range"})
            continue
        field = e["field"]
        value = e["value"]
        try:
            f = rec._field(field)  # noqa: SLF001 — internal lookup
        except KeyError as exc:
            errors.append({"edit": e, "error": str(exc)})
            continue
        if f.size is not None and len(value) > f.size:
            errors.append({
                "edit": e,
                "error": f"value '{value}' exceeds field '{field}' size {f.size}",
            })

    if errors:
        raise EditError(str(errors))

    # All valid — apply.
    for e in edits:
        by_index[e["record_index"]].set(e["field"], e["value"])

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


def rename_file(src, dst) -> dict:
    s, d = Path(src), Path(dst)
    if not is_ok_file(s):
        raise EditError(f"not an .OK file: {s}")
    if d.exists():
        raise EditError(f"destination exists: {d}")
    d.parent.mkdir(parents=True, exist_ok=True)
    s.rename(d)
    return {"renamed": str(s), "to": str(d)}
