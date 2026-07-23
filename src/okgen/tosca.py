"""Run TOSCA Script — populate a TOSCA input workbook from selected JSON files.

For the selected files, resolve each file's (Chain, Process, Format) into the
exact strings the TOSCA workbook expects (chain/process via config name maps;
Format via the workbook's own ``Key`` sheet, which is what the cell dropdowns
reference), dedupe to unique combinations, and write them into the chosen
workbook's data sheet — CONTIGUOUS from the top, clearing everything below so
TOSCA (which stops at the first blank row) processes exactly the selected set.

The workbooks are macro-enabled (.xlsm) with x14 dropdown validations that an
openpyxl round-trip would silently drop, so the write is a TARGETED edit of just
the data-sheet cells inside the .xlsm zip — macros, dropdowns, formatting and the
~180 helper columns are left byte-untouched (same philosophy as the fixed-width
byte-span edits). Reading (the Key sheet) uses openpyxl; only writing is surgical.

Config: ``config/tosca.yaml`` (see that file). JSON layouts only, for now.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape

from okgen.detect import detect_layout


class ToscaError(Exception):
    """A configuration/workbook-level failure (not a per-file issue)."""


_WORKBOOK_EXTS = (".xlsm", ".xlsx")
_BAT_EXTS = (".bat", ".cmd")


def _resolve_one(raw: str, exts, kind: str) -> Path:
    """Resolve a config path that may be a full FILE path OR a FOLDER holding a
    single matching file (each TOSCA folder has exactly one .xlsm / one .bat).
    Raises ToscaError with a clear message if it's missing or ambiguous."""
    p = Path(raw)
    if p.is_file():
        return p
    if p.is_dir():
        hits = sorted(f for f in p.iterdir()
                      if f.is_file() and f.suffix.lower() in exts
                      and not f.name.startswith("~$"))
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ToscaError(f"no {kind} ({'/'.join(exts)}) in folder: {p}")
        raise ToscaError(f"multiple {kind} files in {p} — give the full file path: "
                         + ", ".join(h.name for h in hits))
    raise ToscaError(f"{kind} path not found: {raw}")


# --------------------------------------------------------------------------- #
# Value resolution
# --------------------------------------------------------------------------- #
def _json_header(path: Path) -> dict:
    import json
    data = json.loads(Path(path).read_text(encoding="utf-8")).get("data", {})
    return data.get("header", {}) or {}


def _format_string(key_ws, column: str, code: str) -> Optional[str]:
    """The exact ``"code - description"`` from a Key column whose value starts
    with ``code`` (e.g. Key!F for Winners Style Header, code 'B' -> 'B - Blue Gum')."""
    code = str(code).strip()
    for row in range(2, (key_ws.max_row or 2) + 1):
        v = key_ws[f"{column}{row}"].value
        if v is None:
            continue
        head = str(v).split(" -", 1)[0].strip()   # part before ' -'
        if head == code:
            return str(v)
    return None


def build_rows(paths, registry, config, key_ws) -> Tuple[List[dict], List[dict]]:
    """(unique rows, per-file errors). Each row: chain/process/format/status/
    source/date + is_europe. Deduped by (chain, process, format)."""
    t = config.tosca()
    chain_names = t.get("chain_names", {}) or {}
    process_names = t.get("process_names", {}) or {}
    format_columns = t.get("format_columns", {}) or {}
    defaults = t.get("defaults", {}) or {}
    europe = set(t.get("europe_chain_names", []) or [])
    date_eu = t.get("date_format_europe", "%d/%m/%Y")
    date_def = t.get("date_format_default", "%m/%d/%Y")
    today = datetime.date.today()

    seen = set()
    rows: List[dict] = []
    errors: List[dict] = []
    for p in paths or []:
        p = Path(p)
        name = p.name
        try:
            layout = detect_layout(p).layout
        except Exception as exc:                       # noqa: BLE001
            errors.append({"file": name, "error": f"could not read: {exc}"})
            continue
        reg_layout = registry.get(layout) if layout else None
        if reg_layout is None or not getattr(reg_layout, "json_mode", False):
            errors.append({"file": name, "error": "not a JSON layout (TOSCA is JSON-only for now)"})
            continue

        header = _json_header(p)
        raw_chain = header.get("chain")
        info = config.chain(raw_chain)                 # resolves a code OR a name
        chain_code = info.code if info else None
        chain_name = chain_names.get(chain_code)
        process_name = process_names.get(layout)
        fmt_code = header.get("format")

        if not chain_name:
            errors.append({"file": name, "error": f"no TOSCA chain name for chain {raw_chain!r}"})
            continue
        if not process_name:
            errors.append({"file": name, "error": f"no TOSCA process name for layout {layout}"})
            continue
        column = (format_columns.get(chain_name) or {}).get(process_name)
        if not column:
            errors.append({"file": name,
                           "error": f"no Format column mapped for {chain_name} / {process_name}"})
            continue
        if fmt_code in (None, ""):
            errors.append({"file": name, "error": "file has no 'format' value"})
            continue
        fmt_str = _format_string(key_ws, column, fmt_code)
        if fmt_str is None:
            errors.append({"file": name,
                           "error": f"format {fmt_code!r} not in Key column {column} "
                                    f"for {chain_name}/{process_name}"})
            continue

        key = (chain_name, process_name, fmt_str)
        if key in seen:
            continue                                    # dedupe unique combinations
        seen.add(key)
        is_eu = chain_name in europe
        rows.append({
            "chain": chain_name,
            "process": process_name,
            "format": fmt_str,
            "status": defaults.get("status", "Work Pending"),
            "source": defaults.get("source", "Online"),
            "date": today.strftime(date_eu if is_eu else date_def),
        })
    return rows, errors


# --------------------------------------------------------------------------- #
# Targeted .xlsm data-sheet write (macros/dropdowns preserved)
# --------------------------------------------------------------------------- #
def _col_num(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


def _cell_xml(ref: str, style_attr: str, value: Optional[str]) -> str:
    if value is None:
        return f'<c r="{ref}"{style_attr}/>'
    return (f'<c r="{ref}"{style_attr} t="inlineStr">'
            f'<is><t xml:space="preserve">{escape(value)}</t></is></c>')


def _set_cell(xml: str, ref: str, value: Optional[str]) -> str:
    """Set (or clear, value=None) one cell in a worksheet XML string, inserting
    the cell/row if absent. Only touches that cell — everything else is byte-exact."""
    col = re.match(r"[A-Z]+", ref).group(0)
    rownum = ref[len(col):]
    cell_pat = re.compile(r'<c r="' + re.escape(ref) + r'"(?:\s[^>]*?)?(?:/>|>.*?</c>)', re.S)
    m = cell_pat.search(xml)
    if m:
        s = re.search(r'\ss="\d+"', m.group(0))
        return xml[:m.start()] + _cell_xml(ref, s.group(0) if s else "", value) + xml[m.end():]
    if value is None:
        return xml                                      # already absent == empty
    new_cell = _cell_xml(ref, "", value)
    row_pat = re.compile(r'(<row r="' + rownum + r'"[^>]*>)(.*?)(</row>)', re.S)
    rm = row_pat.search(xml)
    if rm:                                              # row exists, cell doesn't -> insert in order
        body = rm.group(2)
        at = len(body)
        for cm in re.finditer(r'<c r="([A-Z]+)\d+"', body):
            if _col_num(cm.group(1)) > _col_num(col):
                at = cm.start()
                break
        return xml[:rm.start(2)] + body[:at] + new_cell + body[at:] + xml[rm.end(2):]
    # row missing -> insert a new <row> in row-number order
    new_row = f'<row r="{rownum}">{new_cell}</row>'
    sd = re.search(r'(<sheetData>)(.*?)(</sheetData>)', xml, re.S)
    body = sd.group(2)
    at = len(body)
    for rr in re.finditer(r'<row r="(\d+)"', body):
        if int(rr.group(1)) > int(rownum):
            at = rr.start()
            break
    return xml[:sd.start(2)] + body[:at] + new_row + body[at:] + xml[sd.end(2):]


def _sheet_xml_path(names, wb_xml: str, rels_xml: str, sheet_name: str) -> str:
    rid = None
    for sm in re.finditer(r"<sheet\b[^>]*/?>", wb_xml):
        if f'name="{sheet_name}"' in sm.group(0):
            r = re.search(r'r:id="([^"]+)"', sm.group(0))
            rid = r.group(1) if r else None
            break
    if rid is None:
        raise ToscaError(f"data sheet {sheet_name!r} not found in workbook")
    rm = re.search(r'<Relationship[^>]*Id="' + re.escape(rid) + r'"[^>]*Target="([^"]+)"', rels_xml)
    if rm is None:
        raise ToscaError(f"could not resolve sheet target for {sheet_name!r}")
    target = rm.group(1)
    return ("xl/" + target) if not target.startswith("/") else target[1:]


def write_data_sheet(workbook: Path, data_sheet: str, rows: List[dict],
                     first_data_row: int, columns: dict, max_clear_row: int) -> None:
    """Write ``rows`` into the data sheet contiguously from ``first_data_row`` and
    clear the field columns of every row below, editing only the sheet XML inside
    the .xlsm zip (all other parts — macros, validations, styles — preserved)."""
    workbook = Path(workbook)
    with zipfile.ZipFile(workbook) as zf:
        names = zf.namelist()
        data = {n: zf.read(n) for n in names}
    wb_xml = data["xl/workbook.xml"].decode("utf-8")
    rels_xml = data["xl/_rels/workbook.xml.rels"].decode("utf-8")
    sheet_path = _sheet_xml_path(names, wb_xml, rels_xml, data_sheet)
    xml = data[sheet_path].decode("utf-8")

    for i, row in enumerate(rows):
        rnum = first_data_row + i
        for field, col in columns.items():
            xml = _set_cell(xml, f"{col}{rnum}", row.get(field))
    for rnum in range(first_data_row + len(rows), max_clear_row + 1):
        for col in columns.values():
            xml = _set_cell(xml, f"{col}{rnum}", None)

    data[sheet_path] = xml.encode("utf-8")
    tmp = workbook.with_name(workbook.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in names:                                 # preserve entry order
            zf.writestr(n, data[n])
    tmp.replace(workbook)


# --------------------------------------------------------------------------- #
# Launch the TOSCA .bat (fire-and-forget)
# --------------------------------------------------------------------------- #
def _launch_bat(bat: Path) -> None:
    """Start the .bat and return immediately (fire-and-forget — TOSCA runs long
    and does its own reporting)."""
    folder = str(bat.parent)
    if os.name == "nt":
        # Open the .bat in its OWN console window — like double-clicking it — with
        # its folder as the working directory, so the user SEES TOSCA run (and any
        # error). ``start "" /D <dir> <bat>``: the empty "" is the window title so
        # a quoted path isn't mistaken for it. The launching cmd exits at once.
        subprocess.Popen(["cmd", "/c", "start", "", "/D", folder, str(bat)],
                         close_fds=True)
    else:
        # Non-Windows (dev/CI): run the script directly in a new session. A real
        # .bat won't execute here, but an executable stub does — enough to prove
        # the launch path without a Windows box.
        subprocess.Popen([str(bat)], cwd=folder, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, close_fds=True)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def list_scripts(config) -> List[dict]:
    return [{"name": s.get("name")} for s in config.tosca_scripts()]


def run(paths, script_name, registry, config, launch=True) -> dict:
    """Populate the chosen script's workbook from the selected files, then fire
    the script's .bat (fire-and-forget) when rows were written. ``launch=False``
    updates the sheet only (used by tests that don't want to spawn a process)."""
    import openpyxl

    t = config.tosca()
    script = config.tosca_script(script_name)
    if script is None:
        raise ToscaError(f"no TOSCA script named {script_name!r} in config/tosca.yaml")
    workbook = _resolve_one(script.get("workbook", ""), _WORKBOOK_EXTS, "workbook")
    data_sheet = script["data_sheet"]
    key_sheet = t.get("key_sheet", "Key")
    first_data_row = int(t.get("first_data_row", 2))
    columns = t.get("columns") or {}

    wb = openpyxl.load_workbook(workbook, data_only=True, read_only=True)
    try:
        if key_sheet not in wb.sheetnames:
            raise ToscaError(f"Key sheet {key_sheet!r} not in workbook")
        key_ws = wb[key_sheet]
        rows, errors = build_rows(paths, registry, config, key_ws)
        data_ws = wb[data_sheet] if data_sheet in wb.sheetnames else None
        max_row = (data_ws.max_row if data_ws else 0) or 0
    finally:
        wb.close()

    max_clear_row = max(max_row, first_data_row + len(rows))
    if rows:
        write_data_sheet(workbook, data_sheet, rows, first_data_row, columns, max_clear_row)

    # Fire the selected script's .bat — only when we actually wrote rows (nothing
    # to run otherwise). Fire-and-forget: launch detached and return at once.
    launched = False
    launch_error = None
    bat_cfg = script.get("bat")
    bat_file = None
    if rows and launch:
        if not bat_cfg:
            launch_error = "no .bat configured for this script (sheet updated only)"
        else:
            try:
                bat_file = _resolve_one(bat_cfg, _BAT_EXTS, "TOSCA .bat")
                _launch_bat(bat_file)
                launched = True
            except ToscaError as exc:
                launch_error = str(exc)
            except Exception as exc:                    # noqa: BLE001
                launch_error = f"failed to start .bat: {exc}"

    return {
        "workbook": str(workbook),
        "script": script_name,
        "data_sheet": data_sheet,
        "written": len(rows),
        "rows": rows,
        "errors": errors,
        "launched": launched,
        "bat": str(bat_file) if bat_file else bat_cfg,
        "launch_error": launch_error,
    }
