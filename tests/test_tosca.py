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
    assert res["written"] == len(res["rows"]) == 4       # 3 Winners SH + 1 dist, deduped
    # Winners/Carton Label has no Key column mapped -> reported, not guessed
    assert any("Carton Label" in e["error"] for e in res["errors"])
    # every row carries the constant Status/Source + a date
    for r in res["rows"]:
        assert r["status"] == "Work Pending" and r["source"] == "Online" and r["date"]


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


def test_non_json_file_is_reported(tmp_path, registry, config):
    ok = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", ok)
    wb = _copy_wb(tmp_path)
    _point_at(config, "Thermal", wb)
    res = tosca.run([str(ok)], "Thermal", registry, config)
    assert res["written"] == 0
    assert any("JSON" in e["error"] for e in res["errors"])


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
    ok = tmp_path / "StyleHeader.OK"                    # non-JSON -> 0 rows
    shutil.copy2(DATA_DIR / "StyleHeader.OK", ok)
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
