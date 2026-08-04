"""Run TOSCA Script — populate a TOSCA input workbook from selected JSON files.

Covers: config loads the scripts; (Chain, Process, Format) resolution from the
Key sheet with dedupe + graceful per-file errors; per-chain date format (Europe
D/M/Y vs M/D/Y); and the targeted .xlsm write — rows contiguous from the top,
everything below cleared (the gap rule), and macros/dropdowns preserved.
"""
import datetime
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from okgen import tosca
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(os.environ.get(
    "OKGEN_DATA_DIR",
    str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions")))
FIXJ = Path(__file__).resolve().parent / "fixtures" / "calgary"
TOSCA_FIX = Path(__file__).resolve().parent / "fixtures" / "tosca"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(
    not (TOSCA_FIX / "thermal.xlsm").exists() or not FIXJ.is_dir(),
    reason="no tosca/calgary fixtures")


@pytest.fixture
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture
def config():
    return Config.load(FIXTURE_CONFIG)


def _point_at(config, script_name, workbook):
    for s in config.tosca()["scripts"]:
        if s["name"] == script_name:
            s["workbook"] = str(workbook)


def _copy_wb(tmp_path, name="thermal.xlsm"):
    dst = tmp_path / name
    shutil.copy2(TOSCA_FIX / name, dst)
    return dst


def _read_rows(wb_path, sheet, n=8):
    wb = openpyxl.load_workbook(wb_path, keep_vba=True, data_only=True)
    ws = wb[sheet]
    out = [[ws[f"{c}{r}"].value for c in "ABCDEF"] for r in range(2, 2 + n)]
    wb.close()
    return out


def test_config_loads_scripts(config):
    scripts = config.tosca_scripts()
    names = [s["name"] for s in scripts]
    assert "Laser Compare" in names and "Thermal" in names
    for s in scripts:                                  # workbook paths resolved absolute
        assert Path(s["workbook"]).is_absolute()


def test_locked_workbook_gives_friendly_error(tmp_path, registry, config, monkeypatch):
    """A locked workbook (open in Excel / held by TOSCA) surfaces a clear
    ToscaError, not an unhandled 500."""
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)

    def boom(*a, **k):
        raise PermissionError("[WinError 32] used by another process")
    monkeypatch.setattr(tosca, "write_data_sheet", boom)

    with pytest.raises(tosca.ToscaError) as ei:
        tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config)
    assert "LOCKED" in str(ei.value)


def test_malformed_tosca_yaml_does_not_crash_startup(tmp_path, capsys):
    """A bad tosca.yaml (e.g. a double-quoted Windows path with backslashes) must
    NOT stop the app from loading — TOSCA is disabled and a warning printed."""
    from okgen.config import Config
    cfgdir = tmp_path / "cfg"
    cfgdir.mkdir()
    (cfgdir / "tosca.yaml").write_text(
        'scripts:\n  - name: X\n    workbook: "C:\\TOSCA\\Thermal"\n', encoding="utf-8")
    cfg = Config.load(cfgdir)                            # must not raise
    assert cfg.tosca_scripts() == []                    # TOSCA disabled
    assert "TOSCA disabled" in capsys.readouterr().err


def test_absolute_workbook_path_used_as_given(tmp_path):
    """Regression: an absolute path that exists must be used EXACTLY as given —
    never prepended with the config dir."""
    from okgen.config import Config
    wb = tmp_path / "real.xlsm"
    wb.write_text("x")                                  # just needs to exist
    cfgdir = tmp_path / "cfg"
    cfgdir.mkdir()
    (cfgdir / "tosca.yaml").write_text(
        "scripts:\n"
        f"  - name: X\n    workbook: {wb}\n    data_sheet: S\n    bat: {wb}\n",
        encoding="utf-8")
    cfg = Config.load(cfgdir)
    script = cfg.tosca_script("X")
    assert script["workbook"] == str(wb)                # unchanged, not cfgdir/…
    assert str(cfgdir) not in script["workbook"]


def test_resolution_dedup_and_errors(tmp_path, registry, config):
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    paths = [str(FIXJ / f) for f in [
        "styleheader_fmtB.json", "styleheader_fmtS.json", "styleheader_fmtT.json",
        "distlabel.json", "cartonlabel_minified.json"]]
    res = tosca.run(paths, "Thermal", registry, config)

    combos = {(r["chain"], r["process"], r["format"]) for r in res["rows"]}
    assert ("Winners", "Style Header", "B - Blue Gum") in combos
    assert ("HomeSense", "Distribution Label", "7 - Distribution Label") in combos
    # Winners/Carton Label now maps to Key!M (Winners_HS_CL_Fmt) -> resolves
    assert ("Winners", "Carton Label", "1 - Carton Label") in combos
    assert res["written"] == len(res["rows"]) == 5       # 3 Winners SH deduped to 3 + dist + carton
    assert res["errors"] == []
    # every row carries the constant Status/Source + a date
    for r in res["rows"]:
        assert r["status"] == "Work Pending" and r["source"] == "Online" and r["date"]


def test_unmapped_combo_is_reported_not_guessed(tmp_path, registry, config):
    """An unmapped (chain, process) must error per file, never pick a column."""
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    del config.tosca()["format_columns"]["Winners"]["Carton Label"]
    res = tosca.run([str(FIXJ / "cartonlabel_minified.json")], "Thermal", registry, config)
    assert res["rows"] == []
    assert any("no Format column mapped" in e["error"] for e in res["errors"])


def test_rows_written_contiguously_and_cleared_below(tmp_path, registry, config):
    wb = _copy_wb(tmp_path)                             # sample has data in rows 2,3,5 (gap at 4)
    _point_at(config, "Thermal", wb)
    paths = [str(FIXJ / f) for f in ["styleheader_fmtB.json", "distlabel.json"]]
    res = tosca.run(paths, "Thermal", registry, config)
    assert res["written"] == 2

    rows = _read_rows(wb, "REG_JSON_THERMAL_Compare")
    assert rows[0][0] and rows[1][0]                    # rows 2,3 have a Chain
    for r in rows[2:]:                                  # everything below is cleared -> gap
        assert all(c is None for c in r), f"stale row not cleared: {r}"


def test_macros_and_dropdowns_preserved(tmp_path, registry, config):
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config)
    with zipfile.ZipFile(wb) as z:
        names = z.namelist()
        sheet = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert "xl/vbaProject.bin" in names                 # macros kept
    assert "dataValidation" in sheet and "x14:dataValidation" in sheet   # dropdowns kept
    # and it still opens as a valid workbook
    openpyxl.load_workbook(wb, keep_vba=True).close()


def test_europe_uses_day_first_date(tmp_path, registry, config):
    # synthesize a Europe (chain 05) Style Header from a real sample
    src = json.loads((FIXJ / "styleheader_fmtB.json").read_text(encoding="utf-8"))
    src["data"]["header"]["chain"] = "05"               # -> TJX Europe
    src["data"]["header"]["format"] = "B"               # Europe SH 'B' = 'B - Single Gum'
    eu = tmp_path / "eu.json"
    eu.write_text(json.dumps(src, indent=2), encoding="utf-8")

    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    res = tosca.run([str(eu)], "Thermal", registry, config)
    assert res["written"] == 1
    row = res["rows"][0]
    assert row["chain"] == "TJX Europe"
    assert row["format"] == "B - Single Gum"
    assert row["date"] == datetime.date.today().strftime("%d/%m/%Y")   # day-first for Europe


def test_non_europe_uses_month_first_date(tmp_path, registry, config):
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config)
    assert res["rows"][0]["date"] == datetime.date.today().strftime("%m/%d/%Y")


def test_ok_file_is_accepted_not_rejected(tmp_path, registry, config):
    """Regression for the old JSON-only gate: a .OK file now resolves a row."""
    ok = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", ok)
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    res = tosca.run([str(ok)], "Thermal", registry, config)
    assert res["written"] == 1 and res["errors"] == []
    assert not any("JSON" in e["error"] for e in res["errors"])


def test_undetectable_file_is_reported(tmp_path, registry, config):
    """A file of no known layout is still reported per file, not crashed on."""
    junk = tmp_path / "junk.OK"
    junk.write_text("not an OK file at all\n", encoding="utf-8")
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    res = tosca.run([str(junk)], "Thermal", registry, config)
    assert res["written"] == 0 and len(res["errors"]) == 1


def _set_bat(config, script_name, bat):
    for s in config.tosca()["scripts"]:
        if s["name"] == script_name:
            s["bat"] = str(bat)


def test_bat_fires_after_write(tmp_path, registry, config):
    """After a successful write, the script's .bat is launched (fire-and-forget).
    Uses an executable stub that touches a marker file to prove the launch."""
    marker = tmp_path / "ran.marker"
    stub = tmp_path / "run.sh"
    stub.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    stub.chmod(0o755)
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    _set_bat(config, "Thermal", stub)

    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config)
    assert res["written"] == 1 and res["launched"] is True and res["launch_error"] is None
    # fire-and-forget -> poll briefly for the marker
    for _ in range(50):
        if marker.exists():
            break
        import time
        time.sleep(0.05)
    assert marker.exists(), "stub .bat did not run"


def test_bat_not_run_when_zero_rows(tmp_path, registry, config):
    marker = tmp_path / "ran.marker"
    stub = tmp_path / "run.sh"
    stub.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    stub.chmod(0o755)
    ok = tmp_path / "junk.OK"                           # undetectable -> 0 rows
    ok.write_text("not an OK file at all\n", encoding="utf-8")
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    _set_bat(config, "Thermal", stub)

    res = tosca.run([str(ok)], "Thermal", registry, config)
    assert res["written"] == 0 and res["launched"] is False
    import time
    time.sleep(0.2)
    assert not marker.exists(), "bat ran despite 0 rows written"


def test_missing_bat_reports_error_but_writes_sheet(tmp_path, registry, config):
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    _set_bat(config, "Thermal", tmp_path / "does_not_exist.bat")
    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config)
    assert res["written"] == 1                          # sheet still updated
    assert res["launched"] is False and "not found" in (res["launch_error"] or "")


def test_workbook_and_bat_accept_folder_paths(tmp_path, registry, config):
    """A script may point `workbook`/`bat` at a FOLDER holding exactly one file;
    OkGen finds the single .xlsm / .bat inside."""
    wb_dir = tmp_path / "wbdir"; wb_dir.mkdir()
    shutil.copy2(TOSCA_FIX / "thermal.xlsm", wb_dir / "thermal.xlsm")
    bat_dir = tmp_path / "batdir"; bat_dir.mkdir()
    marker = tmp_path / "ran.marker"
    # .bat name so the folder resolver finds it; a shebang makes it run on Unix
    stub = bat_dir / "RunThermal.bat"
    stub.write_text(f"#!/bin/sh\ntouch '{marker}'\n"); stub.chmod(0o755)

    for s in config.tosca()["scripts"]:
        if s["name"] == "Thermal":
            s["workbook"] = str(wb_dir)          # folder, not file
            s["bat"] = str(bat_dir)              # folder, not file

    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config)
    assert res["written"] == 1 and res["launched"] is True
    assert res["bat"].endswith("RunThermal.bat")   # resolved to the single file
    for _ in range(50):
        if marker.exists():
            break
        import time; time.sleep(0.05)
    assert marker.exists()


def test_bat_glob_picks_named_file(tmp_path, registry, config):
    """A glob pattern picks the .bat by name even when the folder holds several
    (e.g. point at *ExecutionScript*.bat)."""
    bat_dir = tmp_path / "batdir"; bat_dir.mkdir()
    (bat_dir / "Setup.bat").write_text("x")
    marker = tmp_path / "ran.marker"
    target = bat_dir / "ThermalExecutionScript.bat"
    target.write_text(f"#!/bin/sh\ntouch '{marker}'\n"); target.chmod(0o755)

    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    _set_bat(config, "Thermal", bat_dir / "*ExecutionScript*.bat")   # glob

    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config)
    assert res["launched"] is True
    assert res["bat"].endswith("ThermalExecutionScript.bat")


def test_folder_with_multiple_bats_is_ambiguous(tmp_path, registry, config):
    bat_dir = tmp_path / "batdir"; bat_dir.mkdir()
    for n in ("a.bat", "b.bat"):
        (bat_dir / n).write_text("x")
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    _set_bat(config, "Thermal", bat_dir)
    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config)
    assert res["written"] == 1                      # sheet still written
    assert res["launched"] is False and "multiple" in (res["launch_error"] or "")


def test_launch_false_skips_bat(tmp_path, registry, config):
    stub = tmp_path / "run.sh"
    stub.write_text("#!/bin/sh\nexit 0\n"); stub.chmod(0o755)
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    _set_bat(config, "Thermal", stub)
    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config, launch=False)
    assert res["written"] == 1 and res["launched"] is False and res["launch_error"] is None


# --------------------------------------------------------------------------- #
# Line-based .OK layouts (TOSCA is no longer JSON-only)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("okfile,process,fmt", [
    ("StyleHeader.OK",   "Style Header",       "A - Regular Tag"),      # chain 03 Homegoods, format A
    ("CartonLabel.OK",   "Carton Label",       "1 - Carton Label"),     # chain 01 T.J. Maxx, format 1
    ("DistLabels.OK",    "Distribution Label", "7 - Distributio Label"),# chain 01, format 7 (sheet typo)
    ("Preticket.OK",     "Pre-Ticket",         "A - Purple Tag"),       # chain 01, format A
    ("EUPreticket.OK",   "Pre-Ticket",         "A - Standard White Swift"),  # chain 05 Europe, Key!H
    # EUCartonLabel has no `format` FIELD — its format is DERIVED from
    # distribution/pack type (D8) and resolves against Key!N (Europe_CL_Fmt).
    ("EUCartonLabel.OK", "Carton Label",       "1 - Carton Label"),
    # EUStyleHeader's ticket format is its `format` field (renamed from
    # `ticket_format` so it matches every other layout) -> Key!G Europe_SH_Format.
    ("EUStyleHeader.OK", "Style Header",       "Q - Small Merch UPP Ticket"),
])
def test_ok_file_resolves_a_row(tmp_path, registry, config, okfile, process, fmt):
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    res = tosca.run([str(DATA_DIR / okfile)], "Thermal", registry, config)
    assert res["errors"] == [], res["errors"]
    assert len(res["rows"]) == 1
    row = res["rows"][0]
    assert (row["process"], row["format"]) == (process, fmt)


def test_ok_and_json_same_combo_dedupe_to_one_row(tmp_path, registry, config):
    """A .OK file and the JSON for the same (Chain, Process, Format) write ONE
    row — the same dedupe the JSON-only path already applied."""
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    # Build a JSON styleHeader that matches DistLabels.OK's chain 01 / format 7.
    src = json.loads((FIXJ / "distlabel.json").read_text(encoding="utf-8"))
    src["data"]["header"]["chain"] = "01"
    src["data"]["header"]["format"] = "7"
    twin = tmp_path / "twin.json"
    twin.write_text(json.dumps(src), encoding="utf-8")

    res = tosca.run([str(DATA_DIR / "DistLabels.OK"), str(twin)], "Thermal", registry, config)
    assert res["errors"] == [], res["errors"]
    assert res["written"] == len(res["rows"]) == 1
    assert res["rows"][0]["process"] == "Distribution Label"


def test_eu_gta_process_letter_is_never_used_as_a_format(tmp_path, registry, config):
    """EU GTA `process` (D/H) is the LAYOUT discriminator, not a ticket format.
    'H' is a real format code elsewhere ('H - Piggy Back Gum Label'), so falling
    back to it could silently resolve a WRONG row. Each EU GTA layout must
    resolve from its own format instead: EUStyleHeader from its `format` field,
    EUCartonLabel from its DERIVED format."""
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    res = tosca.run([str(DATA_DIR / "EUStyleHeader.OK"), str(DATA_DIR / "EUCartonLabel.OK")],
                    "Thermal", registry, config)
    assert res["errors"] == [], res["errors"]
    fmts = {r["format"] for r in res["rows"]}
    assert fmts == {"Q - Small Merch UPP Ticket", "1 - Carton Label"}
    assert not any(f.split(" -", 1)[0].strip() in {"D", "H"} for f in fmts)


def test_eu_styleheader_format_field_is_named_format(registry):
    """Pins the rename: EUStyleHeader's ticket format is `format` (not
    `ticket_format`), which is what the editor renders and TOSCA resolves.
    EUCartonLabel keeps `ticket_format` AND gains a derived `format` — the two
    are different things there, so it is deliberately NOT renamed."""
    sh = {f.name for f in registry["EUStyleHeader"].sections[0].fields}
    cl = {f.name for f in registry["EUCartonLabel"].sections[0].fields}
    assert "format" in sh and "ticket_format" not in sh
    assert "ticket_format" in cl and "format" not in cl      # cl's `format` is derived


def test_europe_dist_label_has_no_column_and_is_reported(tmp_path, registry, config):
    """Europe/Distribution Label is genuinely absent from the Key sheet."""
    assert "Distribution Label" not in config.tosca()["format_columns"]["TJX Europe"]


# --------------------------------------------------------------------------- #
# Engine routing — .OK and JSON have SEPARATE workbooks/.bats
# --------------------------------------------------------------------------- #
def test_script_without_applies_to_accepts_both(tmp_path, registry, config):
    """Back-compat: a script predating the split runs everything."""
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    res = tosca.run([str(DATA_DIR / "DistLabels.OK"), str(FIXJ / "styleheader_fmtB.json")],
                    "Thermal", registry, config)
    assert res["skipped"] == [] and res["written"] == 2


def test_json_script_skips_ok_files_and_reports_them(tmp_path, registry, config):
    wb = _copy_wb(tmp_path)
    _point_at(config, "JSON Only", wb)
    res = tosca.run([str(FIXJ / "styleheader_fmtB.json"),
                     str(DATA_DIR / "DistLabels.OK"),
                     str(DATA_DIR / "CartonLabel.OK")], "JSON Only", registry, config)
    assert res["written"] == 1                          # only the JSON file
    assert len(res["skipped"]) == 2
    assert {s["file"] for s in res["skipped"]} == {"DistLabels.OK", "CartonLabel.OK"}
    assert all(s["engine"] == "ok" for s in res["skipped"])
    assert res["errors"] == []                          # skipped is NOT an error


def test_ok_script_skips_json_files_and_reports_them(tmp_path, registry, config):
    wb = _copy_wb(tmp_path)
    _point_at(config, "OK Only", wb)
    res = tosca.run([str(DATA_DIR / "DistLabels.OK"), str(FIXJ / "styleheader_fmtB.json")],
                    "OK Only", registry, config)
    assert res["written"] == 1
    assert [s["file"] for s in res["skipped"]] == ["styleheader_fmtB.json"]
    assert res["skipped"][0]["engine"] == "json"


def test_skipped_files_never_reach_the_workbook(tmp_path, registry, config):
    """The whole point: a selection that is entirely non-applicable writes NOTHING
    — the wrong workbook is not opened, cleared or touched at all."""
    wb = _copy_wb(tmp_path)
    _point_at(config, "OK Only", wb)
    before = wb.read_bytes()
    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "OK Only", registry, config)
    assert res["written"] == 0 and len(res["skipped"]) == 1
    assert wb.read_bytes() == before, "the workbook was modified by a skipped-only run"


def test_picker_offers_only_applicable_scripts(registry, config):
    """An .OK selection must not be offered a JSON workbook (and vice versa)."""
    ok = tosca.scripts_for([str(DATA_DIR / "DistLabels.OK")], registry, config)
    by = {s["name"]: s["matches"] for s in ok["scripts"]}
    assert by["OK Only"] == 1 and by["JSON Only"] == 0 and by["Thermal"] == 1
    assert ok["counts"] == {"ok": 1, "json": 0, "unknown": 0}

    js = tosca.scripts_for([str(FIXJ / "styleheader_fmtB.json")], registry, config)
    by = {s["name"]: s["matches"] for s in js["scripts"]}
    assert by["JSON Only"] == 1 and by["OK Only"] == 0


def test_per_script_sheet_layout_override(tmp_path, registry, config):
    """A script whose sheet is laid out differently can override the globals
    without a code change (the Delete/Reprint sheets may differ)."""
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    for s in config.tosca()["scripts"]:
        if s["name"] == "Thermal":
            s["first_data_row"] = 4                     # write lower down
    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal", registry, config)
    assert res["written"] == 1
    rows = _read_rows(wb, "REG_JSON_THERMAL_Compare")   # rows[0] == sheet row 2
    assert rows[2][0] == "Winners", "the row should land on sheet row 4, not row 2"
    assert rows[2][1] == "Style Header"


# --------------------------------------------------------------------------- #
# Releasing a workbook a stopped TOSCA run left open in Excel
# --------------------------------------------------------------------------- #
def test_a_locked_workbook_is_closed_in_excel_and_the_write_retried(
        tmp_path, registry, config, monkeypatch):
    """The reported problem: stopping a run mid-way leaves the workbook open in
    Excel, and the lock outlives it. OkGen closes THAT workbook and tries again."""
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    calls = {"writes": 0, "closed": []}
    real_write = tosca.write_data_sheet

    def flaky(*a, **k):
        calls["writes"] += 1
        if calls["writes"] == 1:
            raise PermissionError("[WinError 32] used by another process")
        return real_write(*a, **k)

    monkeypatch.setattr(tosca, "write_data_sheet", flaky)
    monkeypatch.setattr(tosca, "close_open_workbook",
                        lambda p, **k: calls["closed"].append(p) or tosca.CLOSE_OK)

    res = tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal",
                    registry, config, launch=False)
    assert res["written"] == 1                       # the run succeeded
    assert calls["writes"] == 2                      # first failed, retry worked
    assert [Path(p).name for p in calls["closed"]] == [wb.name]


def test_a_workbook_with_unsaved_changes_is_never_closed(
        tmp_path, registry, config, monkeypatch):
    """Option (a): refuse rather than discard. The likeliest unsaved change is
    the PowerForms link someone just set by hand (D21)."""
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    monkeypatch.setattr(tosca, "write_data_sheet",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    monkeypatch.setattr(tosca, "close_open_workbook", lambda p, **k: tosca.CLOSE_UNSAVED)
    with pytest.raises(tosca.ToscaError) as ei:
        tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal",
                  registry, config, launch=False)
    msg = str(ei.value)
    assert "UNSAVED CHANGES" in msg
    assert "did not close it" in msg
    assert "PowerForms" in msg                       # says what you might lose


@pytest.mark.parametrize("outcome,expect", [
    (tosca.CLOSE_NO_EXCEL, "stopped part-way"),
    (tosca.CLOSE_NOT_OPEN, "stopped part-way"),
    (tosca.CLOSE_OK, "still"),                       # closed, yet STILL locked
    (tosca.CLOSE_ERROR, "Close it in"),
])
def test_each_close_outcome_explains_itself(tmp_path, registry, config, monkeypatch,
                                            outcome, expect):
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    monkeypatch.setattr(tosca, "write_data_sheet",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    monkeypatch.setattr(tosca, "close_open_workbook", lambda p, **k: outcome)
    with pytest.raises(tosca.ToscaError, match=expect):
        tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal",
                  registry, config, launch=False)


def test_closing_can_be_switched_off(tmp_path, registry, config, monkeypatch):
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    config.tosca()["close_open_workbook"] = False
    called = []
    monkeypatch.setattr(tosca, "write_data_sheet",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    monkeypatch.setattr(tosca, "close_open_workbook",
                        lambda p, **k: called.append(p) or tosca.CLOSE_OK)
    with pytest.raises(tosca.ToscaError, match="LOCKED"):
        tosca.run([str(FIXJ / "styleheader_fmtB.json")], "Thermal",
                  registry, config, launch=False)
    assert called == [], "closing was disabled but OkGen tried anyway"


def test_close_is_a_no_op_off_windows():
    assert tosca.close_open_workbook(Path("x.xlsm")) == tosca.CLOSE_UNSUPPORTED


def test_the_powershell_closes_one_workbook_and_never_kills_excel():
    """Killing EXCEL.EXE would take down the user's other open workbooks."""
    ps = tosca._CLOSE_PS.format(path=r"D:\TOSCA\FUN_LASER_TestData.xlsm")
    assert "GetActiveObject('Excel.Application')" in ps
    assert "$wb.Close($false)" in ps                  # close one workbook
    assert "$wb.Saved" in ps                          # gated on having no edits
    assert "Quit" not in ps and "Stop-Process" not in ps and "taskkill" not in ps
    assert r"D:\TOSCA\FUN_LASER_TestData.xlsm" in ps  # path survives verbatim


def test_a_path_with_a_quote_cannot_break_out_of_the_script():
    ps = tosca._CLOSE_PS.format(path="D:\\it's\\book.xlsm".replace("'", "''"))
    assert "$target = 'D:\\it''s\\book.xlsm'" in ps   # doubled, still one literal
