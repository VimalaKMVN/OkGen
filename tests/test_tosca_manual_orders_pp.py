"""Run TOSCA Script — "Manual Orders Print Preview".

The SAME Manual Orders process as WorkPending (test_tosca_manual_orders.py
pins that one), configured with three differences, which is what is tested
here — plus that sharing the engine leaves WorkPending exactly as it was:

  * its tabs are ``ManualOrder_Header_PP`` / ``ManualOrder_Detail_PP_N``;
  * the header's key column is ``Key/ASN#`` — the KeyValue, by the same rule;
  * an extra ``OrderProcessing`` tab: one row per unique Chain/Process/Format,
    Work Pending, Manual, today's date as a REAL Excel date shown mm/dd/yyyy,
    or dd/mm/yyyy on a Europe row (as the user's sample has it).

Runs against a copy of the user's real PP sample workbook and the SHIPPED
config/, like its WorkPending sibling.
"""
import datetime
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from okgen import tosca, tosca_manual
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_tosca_manual_orders as wp                      # noqa: E402  (helpers only)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "OkFileDefinitions"
FIXJ = Path(__file__).resolve().parent / "fixtures" / "calgary"
WB_FIX = Path(__file__).resolve().parent / "fixtures" / "tosca" / "manual_orders_pp.xlsm"
SCRIPT = "Manual Orders Print Preview"

pytestmark = [
    pytest.mark.skipif(not WB_FIX.exists(), reason="no print-preview workbook fixture"),
    pytest.mark.filterwarnings("ignore:Data Validation extension"),
]

NA_SH, NA_PT = DATA / "StyleHeader.OK", DATA / "Preticket.OK"
EU_SH, EU_PT = DATA / "EUStyleHeader.OK", DATA / "EUPreticket.OK"
J_SH, J_PT = FIXJ / "styleheader_fmtB.json", FIXJ / "preticket.json"
CL = DATA / "CartonLabel.OK"
SIX = [NA_SH, NA_PT, EU_SH, EU_PT, J_SH, J_PT]


@pytest.fixture
def registry():
    return LayoutRegistry.from_dir(DATA)


@pytest.fixture
def env(tmp_path):
    config = Config.load(ROOT / "config")
    wb = tmp_path / "FUN_EU_SH_PT_ManualOrders_PP_TestData.xlsm"
    shutil.copy2(WB_FIX, wb)
    marker = tmp_path / "bat_ran"
    bat = tmp_path / "MO_PP_Automation_RunScript.bat"
    bat.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    bat.chmod(0o755)
    s = config.tosca_script(SCRIPT)
    s["workbook"], s["bat"] = str(wb), str(bat)
    s["input_folder"] = str(tmp_path / "Manual_PP_OkGen_Selected_Input_Files")
    config.tosca()["log_folder"] = str(tmp_path / "logs")
    return {"config": config, "wb": wb, "marker": marker,
            "folder": Path(s["input_folder"]), "tmp": tmp_path}


def _run(env, registry, files):
    return tosca.run([str(f) for f in files], SCRIPT, registry, env["config"])


def _book(path):
    return openpyxl.load_workbook(path, keep_vba=True)


def _rows(ws, first=2, cols=None):
    return [list(r) for r in ws.iter_rows(min_row=first, max_row=ws.max_row,
                                          max_col=cols, values_only=True)]


# --------------------------------------------------------------------------- #
def test_configured_below_workpending_with_the_users_paths():
    config = Config.load(ROOT / "config")
    names = [s["name"] for s in config.tosca_scripts()]
    assert names.index(SCRIPT) == names.index("Manual Orders WorkPending") + 1
    s = config.tosca_script(SCRIPT)
    base = "D:\\ToscaAutomation\\Functional_Testing_Automation\\FUN_EU_SH_PT_ManualOrders_PP\\"
    assert s["workbook"] == base + "FUN_EU_SH_PT_ManualOrders_PP_TestData.xlsm"
    assert s["bat"] == base + "MO_PP_Automation_RunScript.bat"
    assert s["input_folder"] == base + "Manual_PP_OkGen_Selected_Input_Files"
    assert tosca_manual.is_manual(s)


def test_tabs_are_the_pp_names_and_nothing_else_is_touched(env, registry):
    before = wp._zip(env["wb"])
    res = _run(env, registry, SIX)
    assert res["written"] == 6
    book = _book(env["wb"])
    assert book.sheetnames == ["Login", "OrderProcessing", "ManualOrder_Header_PP",
                               *[f"ManualOrder_Detail_PP_{i}" for i in range(1, 7)], "Key"]
    after = wp._zip(env["wb"])
    for part in ("xl/worksheets/sheet1.xml", "xl/vbaProject.bin", "xl/styles.xml",
                 "xl/sharedStrings.xml"):
        assert after[part] == before[part], part
    # The Key tab, found by NAME (it is not sheet12 in this workbook).
    wbx = after["xl/workbook.xml"].decode()
    rid = re.search(r'name="Key"[^>]*r:id="([^"]+)"', wbx).group(1)
    tgt = re.search(r'Id="' + rid + r'"[^>]*Target="([^"]+)"',
                    after["xl/_rels/workbook.xml.rels"].decode()).group(1)
    assert after["xl/" + tgt] == before["xl/" + tgt]
    template = [c.value for c in book["ManualOrder_Detail_PP_1"][1]]
    for i in range(2, 7):
        assert [c.value for c in book[f"ManualOrder_Detail_PP_{i}"][1]] == template


def test_key_asn_column_is_filled_as_keyvalue(env, registry, tmp_path):
    scan = wp._json_copy(tmp_path, J_SH, "scan.json", headerASNid="")
    _run(env, registry, SIX + [scan])
    ws = _book(env["wb"])["ManualOrder_Header_PP"]
    assert ws["G1"].value == "Key/ASN#"
    assert [r[6] for r in _rows(ws, 2, 11) if r[0]] == [
        "550000P1A", "33001P3A", "126539Q", "10021888",
        "V403804208940582A", "40644701", "140038A"]


def test_detail_data_is_the_same_engine_as_workpending(env, registry, tmp_path):
    """Same mapping, same rules: padding rows skipped, Page/Line padded, the
    CBC zones, Europe U/Q UPP price blank and random weights."""
    eu_u = tmp_path / "EU_PT_U.OK"
    eu_u.write_bytes(EU_PT.read_bytes().replace(b"P|05|A|", b"P|05|U|", 1))
    res = _run(env, registry, [NA_PT, EU_SH, eu_u])
    assert [r["lines"] for r in res["records"]] == [4, 10, 5]
    book = _book(env["wb"])
    names = [c.value for c in book["ManualOrder_Detail_PP_1"][1]]
    col = lambda n, c: [r[names.index(c)] for r in _rows(book[f"ManualOrder_Detail_PP_{n}"])]
    assert col(1, "Page")[:2] == ["001", "002"]
    assert col(2, "Zone") == ["01", "01", "05", "05", "03", "03", "03", "10", "10", "10"]
    assert set(col(2, "UPPPrice")) == {None}               # EU SH Q
    assert set(col(3, "UPPPrice")) == {None}               # EU PT U
    assert set(col(3, "TotalWeight")) <= {"0.1", "9999.99", "1000"}


def test_order_processing_one_row_per_combination(env, registry, tmp_path):
    """Two files with the same Chain/Process/Format make ONE row; order follows
    the selection; nothing below the last row."""
    twin = wp._json_copy(tmp_path, J_PT, "pt_twin.json", purchaseOrderNumber="40644702")
    _run(env, registry, [J_PT, NA_SH, twin, EU_PT, CL])
    ws = _book(env["wb"])["OrderProcessing"]
    rows = _rows(ws, 2, 6)
    assert [r[:5] for r in rows] == [
        ["Marshalls", "Pre-Ticket", "B - Regular Gum Label", "Work Pending", "Manual"],
        ["Homegoods", "Style Header", "A - Regular Tag", "Work Pending", "Manual"],
        ["TJX Europe", "Pre-Ticket", "A - Standard White Swift", "Work Pending", "Manual"],
    ]
    assert ws.max_row == 4


def test_processed_date_is_today_as_a_real_date_formatted_by_chain(env, registry):
    _run(env, registry, [NA_SH, EU_PT])
    ws = _book(env["wb"])["OrderProcessing"]
    today = datetime.datetime.combine(datetime.date.today(), datetime.time())
    assert ws["F2"].value == today and ws["F2"].number_format == "mm/dd/yyyy"
    assert ws["F3"].value == today and ws["F3"].number_format == "dd/mm/yyyy"


def test_order_processing_matches_the_header_tab_exactly(env, registry):
    """The Chain/Process/Format TEXT is the header's, so TOSCA can join them."""
    _run(env, registry, SIX)
    book = _book(env["wb"])
    head = {tuple(r[:3]) for r in _rows(book["ManualOrder_Header_PP"], 2, 3) if r[0]}
    op = {tuple(r[:3]) for r in _rows(book["OrderProcessing"], 2, 3)}
    assert op == head


def test_package_stays_consistent_adding_and_deleting_tabs(env, registry, tmp_path):
    for n in (10, 1):
        files = [wp._json_copy(tmp_path, J_PT, f"pt_{n}_{i}.json") for i in range(n)]
        _run(env, registry, files)
        wp._assert_package_consistent(env["wb"])
        details = [s for s in _book(env["wb"]).sheetnames if s.startswith("ManualOrder_Detail_PP_")]
        assert len(details) == n


def test_the_same_files_always_give_the_same_sheet(env, registry):
    _run(env, registry, [J_PT, NA_SH])
    first = {n: _rows(_book(env["wb"])[n], 1) for n in _book(env["wb"]).sheetnames
             if n != "Login" and n != "Key"}
    _run(env, registry, [EU_SH, EU_PT, J_SH])
    _run(env, registry, [J_PT, NA_SH])
    again = {n: _rows(_book(env["wb"])[n], 1) for n in _book(env["wb"]).sheetnames
             if n != "Login" and n != "Key"}
    assert again == first


def test_run_stops_for_review_and_launch_starts_the_pp_bat(env, registry):
    import time
    res = _run(env, registry, [NA_SH])
    assert res["review"] is True and not env["marker"].exists()
    assert "ORDERPROCESSING (1 row(s)" in res["report"]
    out = tosca_manual.launch(SCRIPT, env["config"], res["log"])
    assert out["bat"].endswith("MO_PP_Automation_RunScript.bat")
    for _ in range(50):
        if env["marker"].exists():
            break
        time.sleep(0.05)
    assert env["marker"].exists()
    assert sorted(p.name for p in env["folder"].iterdir()) == ["StyleHeader.OK"]


def test_preview_lists_the_order_processing_rows(env, registry):
    pv = tosca.preview([str(f) for f in SIX], SCRIPT, registry, env["config"])
    assert pv["order_sheet"] == "OrderProcessing" and len(pv["order_rows"]) == 6
    assert "ORDERPROCESSING (6 row(s)" in pv["report"]


def test_the_shipped_pp_sample_is_the_users_untouched_file():
    shipped = ROOT / "config" / "templates" / "ManualOrders_PP_Sample.xlsm"
    assert shipped.read_bytes() == WB_FIX.read_bytes()


def test_workpending_has_no_order_processing_and_keeps_its_names():
    config = Config.load(ROOT / "config")
    st = tosca_manual.settings(config, config.tosca_script("Manual Orders WorkPending"))
    assert not st.get("order_processing") and not st.get("column_names")
    assert (st["header_sheet"], st["template_sheet"]) == ("ManualOrder_Header",
                                                          "ManualOrder_Detail_1")
