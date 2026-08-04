"""Run TOSCA Script — populate a TOSCA input workbook from selected files.

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

Works on BOTH engines: the Calgary JSON layouts and the line-based ``.OK`` ones.
Only two values are read per file — Chain and ticket Format — so the difference
is confined to ``_json_header`` vs ``_okfile_header``; everything downstream
(dedupe, Key-sheet resolution, the workbook write, the .bat launch) is
layout-agnostic. A ``.OK`` file and the JSON for the same combination therefore
collapse to ONE row, which is the point of deduping by (Chain, Process, Format).

Config: ``config/tosca.yaml`` (see that file).
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import time
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
    """Resolve a config path that may be:
      * a full FILE path (used as-is), or
      * a GLOB pattern (e.g. ``…\\*ExecutionScript*.bat``) — the single match, or
      * a FOLDER holding one matching file (``.xlsm`` / ``.bat``).
    Raises ToscaError with a clear message if it's missing or ambiguous."""
    p = Path(raw)
    if p.is_file():
        return p

    def _pick(hits, where):
        hits = sorted(h for h in hits
                      if h.is_file() and h.suffix.lower() in exts
                      and not h.name.startswith("~$"))
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ToscaError(f"no {kind} ({'/'.join(exts)}) {where}")
        raise ToscaError(f"multiple {kind} files {where} — narrow it with a full "
                         f"path or a name pattern: " + ", ".join(h.name for h in hits))

    if any(ch in p.name for ch in "*?[") and not p.is_dir():   # glob pattern
        parent = p.parent
        return _pick(parent.glob(p.name) if parent.is_dir() else [], f"matching {raw}")
    if p.is_dir():
        return _pick(p.iterdir(), f"in folder: {p}")
    raise ToscaError(f"{kind} path not found: {raw}")


# --------------------------------------------------------------------------- #
# Value resolution
# --------------------------------------------------------------------------- #
def _json_header(path: Path) -> dict:
    import json
    data = json.loads(Path(path).read_text(encoding="utf-8")).get("data", {})
    return data.get("header", {}) or {}


def _okfile_header(path: Path, layout, config) -> dict:
    """``chain``/``format`` for a line-based (.OK) layout, read from its header
    record — the same two values ``_json_header`` yields for a JSON file.

    ``format`` is the file's TICKET format (the Key-sheet code). It is read from
    a real header field when the layout has one, else from a DERIVED field of
    that name (EUCartonLabel computes its format from distribution/pack type,
    see D8). The EU GTA ``process`` field is deliberately NOT used as a fallback:
    it is the layout discriminator (``D``->StyleHeader, ``H``->CartonLabel), not
    a ticket format, and 'H' collides with a real format code in other columns.
    """
    from okgen.okfile import parse_okfile

    okf = parse_okfile(path, layout)
    header = (okf.layout.sections or [None])[0]
    if header is None:
        return {}
    recs = okf.sections().get(header.name) or []
    if not recs:
        return {}
    rec = recs[0]
    values = {f.name: rec.get(f.name) for f in header.fields}

    out = {"chain": values.get("chain")}
    if "format" in values:
        out["format"] = values["format"]
    else:
        for spec in config.derived_fields(okf.layout.name):
            if spec.get("name") == "format":
                out["format"] = _format_code(config.eval_derived(spec, values))
                break
    return out


def _format_code(value) -> Optional[str]:
    """The bare code from a value that may already be a ``"1 - Carton Label"``
    display string (derived fields carry the full label)."""
    if value in (None, ""):
        return value
    return str(value).split(" -", 1)[0].strip()


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
        if reg_layout is None:
            errors.append({"file": name, "error": f"unknown layout {layout!r}"})
            continue

        try:
            header = (_json_header(p) if getattr(reg_layout, "json_mode", False)
                      else _okfile_header(p, reg_layout, config))
        except Exception as exc:                       # noqa: BLE001
            errors.append({"file": name, "error": f"could not read header: {exc}"})
            continue
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
            errors.append({"file": name,
                           "error": f"{layout} has no ticket 'format' value to map "
                                    f"(no format field, and none derived)"})
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
    tmp = workbook.with_name(workbook.name + ".okgen.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in names:                                 # preserve entry order
            zf.writestr(n, data[n])
    # Atomically swap in the new workbook. On Windows this fails if the file is
    # open (Excel) or held by a running TOSCA — retry briefly for a transient
    # handle, then give up cleanly (caller turns it into a friendly message).
    for attempt in range(5):
        try:
            tmp.replace(workbook)
            return
        except PermissionError:
            if attempt == 4:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.25)



# --------------------------------------------------------------------------- #
# Releasing a workbook left open by a stopped TOSCA run
# --------------------------------------------------------------------------- #
CLOSE_OK = "closed"             # we closed it; nothing was lost
CLOSE_UNSAVED = "unsaved"       # it has unsaved edits — deliberately NOT closed
CLOSE_NO_EXCEL = "no_excel"     # no Excel we can talk to in this session
CLOSE_NOT_OPEN = "not_open"     # Excel is running but does not have this file
CLOSE_UNSUPPORTED = "unsupported"   # not Windows
CLOSE_ERROR = "error"

# Ask Excel to close ONE workbook. Deliberately not "kill EXCEL.EXE": that would
# take down every other workbook the user has open, unsaved work included.
#
# `Saved` is the safety gate. If the workbook has unsaved changes we refuse and
# say so, because the likeliest unsaved change is the PowerForms link someone
# just set by hand (D21) — discarding that would be worse than the lock.
_CLOSE_PS = """
$ErrorActionPreference = 'Stop'
$target = '{path}'
try {{ $xl = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application') }}
catch {{ exit 2 }}
foreach ($wb in $xl.Workbooks) {{
  if ($wb.FullName -ieq $target) {{
    if ($wb.Saved) {{ $wb.Close($false); exit 0 }} else {{ exit 3 }}
  }}
}}
exit 4
"""

_CLOSE_EXITS = {0: CLOSE_OK, 2: CLOSE_NO_EXCEL, 3: CLOSE_UNSAVED, 4: CLOSE_NOT_OPEN}


def close_open_workbook(path: Path, timeout: float = 20.0) -> str:
    """Try to close ``path`` in a running Excel. Returns one of CLOSE_*.

    Only reaches Excel in the SAME interactive session — a TOSCA run under a
    different account, or an Excel that crashed hard enough to leave COM, is
    beyond it. Those come back as CLOSE_NO_EXCEL/CLOSE_NOT_OPEN and the caller
    explains rather than pretending.
    """
    if os.name != "nt":
        return CLOSE_UNSUPPORTED
    script = _CLOSE_PS.format(path=str(Path(path).resolve()).replace("'", "''"))
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return CLOSE_ERROR
    return _CLOSE_EXITS.get(proc.returncode, CLOSE_ERROR)


def _locked_message(workbook: Path, outcome: str) -> str:
    """Say what is actually wrong, and what will fix it."""
    name = workbook.name
    if outcome == CLOSE_UNSAVED:
        return (f"{name} is open in Excel WITH UNSAVED CHANGES, so OkGen did not "
                f"close it — that could have discarded work (the PowerForms link, "
                f"for instance). Save or discard it in Excel, then run again.")
    if outcome == CLOSE_OK:
        return (f"{name} was open in Excel; OkGen closed it, but the file is still "
                f"locked. Something else is holding it — most likely a TOSCA run "
                f"that is still going. Wait for it to finish, then run again.")
    if outcome in (CLOSE_NO_EXCEL, CLOSE_NOT_OPEN):
        return (f"could not update the workbook — it looks LOCKED: {name}. No Excel "
                f"in this session has it open, so the holder is probably a TOSCA run "
                f"that was stopped part-way. End that process (or sign out and back "
                f"in), then run again.")
    return (f"could not update the workbook — it looks LOCKED: {name}. Close it in "
            f"Excel (and make sure no earlier TOSCA run still has it open), then "
            f"run again.")


# --------------------------------------------------------------------------- #
# Launch the TOSCA .bat (fire-and-forget)
# --------------------------------------------------------------------------- #
def _launch_bat(bat: Path) -> None:
    """Start the .bat and return immediately (fire-and-forget — TOSCA runs long
    and does its own reporting)."""
    folder = str(bat.parent)
    if os.name == "nt":
        # Force a NEW, VISIBLE console window for the .bat so the user sees the
        # "TOSCA started" screen (and any error). CREATE_NEW_CONSOLE makes Windows
        # allocate a fresh console regardless of whether OkGen itself has one;
        # ``cmd /k`` keeps the window open so a quick failure doesn't vanish. cwd
        # = the bat's folder. Fire-and-forget. Falls back to the shell "open"
        # (like double-clicking) if that ever fails.
        CREATE_NEW_CONSOLE = 0x00000010
        try:
            subprocess.Popen(["cmd", "/k", str(bat)], cwd=folder,
                             creationflags=CREATE_NEW_CONSOLE, close_fds=True)
        except Exception:                               # noqa: BLE001
            os.startfile(str(bat))                      # last resort
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
def _script_engines(script: dict) -> set:
    """Engines a script accepts. Absent ``applies_to`` means BOTH, so a config
    written before the .OK/JSON split keeps working unchanged."""
    raw = script.get("applies_to") or ["ok", "json"]
    if isinstance(raw, str):
        raw = [raw]
    return {str(e).strip().lower() for e in raw}


def _engine_of(path: Path, registry) -> Optional[str]:
    """``'json'`` / ``'ok'`` for a file, or None when it can't be detected (those
    are left for build_rows to report as a normal per-file error)."""
    try:
        layout = detect_layout(path).layout
    except Exception:                                   # noqa: BLE001
        return None
    reg_layout = registry.get(layout) if layout else None
    if reg_layout is None:
        return None
    return "json" if getattr(reg_layout, "json_mode", False) else "ok"


def list_scripts(config) -> List[dict]:
    return [{"name": s.get("name"), "applies_to": sorted(_script_engines(s))}
            for s in config.tosca_scripts()]


def scripts_for(paths, registry, config) -> dict:
    """The pickable scripts for a selection: each annotated with how many of the
    selected files it would actually run. The client offers the applicable ones
    so a user can't silently aim an .OK selection at a JSON workbook."""
    counts = {"ok": 0, "json": 0, None: 0}
    for p in paths or []:
        counts[_engine_of(Path(p), registry)] += 1
    out = []
    for s in config.tosca_scripts():
        engines = _script_engines(s)
        out.append({"name": s.get("name"),
                    "applies_to": sorted(engines),
                    "matches": sum(counts.get(e, 0) for e in engines)})
    return {"scripts": out, "counts": {"ok": counts.get("ok", 0),
                                       "json": counts.get("json", 0),
                                       "unknown": counts.get(None, 0)}}


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
    key_sheet = script.get("key_sheet") or t.get("key_sheet", "Key")
    # Sheet layout falls back to the global block, but a script whose data sheet
    # is laid out differently can override it without touching code.
    first_data_row = int(script.get("first_data_row", t.get("first_data_row", 2)))
    columns = script.get("columns") or t.get("columns") or {}

    # Engine routing: .OK and JSON have separate workbooks/.bats, so a run only
    # takes the files its script accepts and REPORTS the rest as skipped rather
    # than silently dropping them (or aiming them at the wrong workbook).
    engines = _script_engines(script)
    use_paths, skipped = [], []
    for p in paths or []:
        eng = _engine_of(Path(p), registry)
        if eng is None or eng in engines:
            use_paths.append(p)                         # undetectable -> normal error
        else:
            skipped.append({"file": Path(p).name, "engine": eng,
                            "reason": f"{'JSON' if eng == 'json' else '.OK'} file — "
                                      f"not applicable to this script"})

    wb = openpyxl.load_workbook(workbook, data_only=True, read_only=True)
    try:
        if key_sheet not in wb.sheetnames:
            raise ToscaError(f"Key sheet {key_sheet!r} not in workbook")
        key_ws = wb[key_sheet]
        rows, errors = build_rows(use_paths, registry, config, key_ws)
        data_ws = wb[data_sheet] if data_sheet in wb.sheetnames else None
        max_row = (data_ws.max_row if data_ws else 0) or 0
    finally:
        wb.close()

    max_clear_row = max(max_row, first_data_row + len(rows))
    if rows:
        try:
            write_data_sheet(workbook, data_sheet, rows, first_data_row, columns, max_clear_row)
        except PermissionError:
            # A TOSCA run stopped part-way leaves the workbook open in Excel, and
            # the lock outlives it by however long that Excel sits there. Ask
            # Excel to close THAT workbook (never kill Excel), then try once
            # more. A workbook with unsaved changes is left alone — see
            # close_open_workbook.
            outcome = (close_open_workbook(workbook)
                       if bool(t.get("close_open_workbook", True)) else "disabled")
            if outcome != CLOSE_OK:
                raise ToscaError(_locked_message(workbook, outcome))
            try:
                write_data_sheet(workbook, data_sheet, rows, first_data_row,
                                 columns, max_clear_row)
            except PermissionError:
                raise ToscaError(_locked_message(workbook, CLOSE_OK))
            except OSError as exc:
                raise ToscaError(f"could not write the workbook {workbook}: {exc}")
        except OSError as exc:
            raise ToscaError(f"could not write the workbook {workbook}: {exc}")

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
        "skipped": skipped,
        "applies_to": sorted(engines),
        "launched": launched,
        "bat": str(bat_file) if bat_file else bat_cfg,
        "launch_error": launch_error,
    }
