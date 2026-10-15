"""Run TOSCA Script — the "Manual Orders WorkPending" kind.

One header record per selected Style Header / Pre-Ticket, each with its own
``ManualOrder_Detail_N`` tab holding that file's detail lines; the input folder
emptied and refilled; the .bat started only by a SECOND call, after review.

Runs against a copy of the user's real sample workbook
(``fixtures/tosca/manual_orders_wp.xlsm``) and the SHIPPED ``config/`` — the
column mapping in ``config/tosca.yaml`` is the behaviour, so it is what is
tested, not a fixture copy of it that could drift.

Covers, in both directions:
  * values: line before header before sample; price/yn/number/mmdd; the key
    per engine AND per SCAN/WMS source; the EU Style Header CBC zone per line,
    and an unlisted CBC reported rather than guessed;
  * the workbook: exactly N detail tabs for N records whether that ADDS tabs
    (9+) or DELETES them (fewer than 8), every tab carrying Detail_1's column
    names; rows below the data deleted; the tabs we do not own byte-identical;
    the package still consistent (content types, rels, app.xml, codeNames);
  * staging: folder created, every old file deleted, selected files copied,
    one file name selected twice refused rather than one silently kept;
  * the review gap: run never launches; launch does, and refuses nothing it
    should not; the log append only ever writes inside logs/.
"""
import json
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from okgen import tosca, tosca_manual
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "OkFileDefinitions"
FIXJ = Path(__file__).resolve().parent / "fixtures" / "calgary"
WB_FIX = Path(__file__).resolve().parent / "fixtures" / "tosca" / "manual_orders_wp.xlsm"
SCRIPT = "Manual Orders WorkPending"

pytestmark = [
    pytest.mark.skipif(not WB_FIX.exists(), reason="no manual-orders workbook fixture"),
    pytest.mark.filterwarnings("ignore:Data Validation extension"),
]

NA_SH = DATA / "StyleHeader.OK"
NA_PT = DATA / "Preticket.OK"
EU_SH = DATA / "EUStyleHeader.OK"
EU_PT = DATA / "EUPreticket.OK"
J_SH = FIXJ / "styleheader_fmtB.json"
J_PT = FIXJ / "preticket.json"
CL = DATA / "CartonLabel.OK"
SIX = [NA_SH, NA_PT, EU_SH, EU_PT, J_SH, J_PT]


@pytest.fixture
def registry():
    return LayoutRegistry.from_dir(DATA)


@pytest.fixture
def env(tmp_path):
    """The shipped config, pointed at a temp copy of the workbook, a temp input
    folder, a stub .bat and a temp log folder."""
    config = Config.load(ROOT / "config")
    wb = tmp_path / "FUN_EU_SH_PT_ManualOrders_WP_TestData.xlsm"
    shutil.copy2(WB_FIX, wb)
    marker = tmp_path / "bat_ran"
    bat = tmp_path / "MO_WP_Automation_RunScript.bat"
    bat.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    bat.chmod(0o755)
    s = config.tosca_script(SCRIPT)
    s["workbook"] = str(wb)
    s["bat"] = str(bat)
    s["input_folder"] = str(tmp_path / "Manual_WorkPending_OkGen_Selected_Input_Files")
    config.tosca()["log_folder"] = str(tmp_path / "logs")
    return {"config": config, "wb": wb, "bat": bat, "marker": marker,
            "folder": Path(s["input_folder"]), "tmp": tmp_path}


def _run(env, registry, files, **kw):
    return tosca.run([str(f) for f in files], SCRIPT, registry, env["config"], **kw)


def _book(path):
    return openpyxl.load_workbook(path, keep_vba=True)


def _rows(ws, first=1):
    return [list(r) for r in ws.iter_rows(min_row=first, max_row=ws.max_row, values_only=True)]


def _zip(path):
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _json_copy(tmp_path, src, name, **header):
    d = json.loads(Path(src).read_text("utf-8"))
    d["data"]["header"].update(header)
    p = tmp_path / name
    p.write_text(json.dumps(d), "utf-8")
    return p


# --------------------------------------------------------------------------- #
# The script is offered, and routed to its own path
# --------------------------------------------------------------------------- #
def test_script_is_configured_as_its_own_kind():
    config = Config.load(ROOT / "config")
    s = config.tosca_script(SCRIPT)
    assert tosca_manual.is_manual(s)
    assert s["workbook"].endswith("FUN_EU_SH_PT_ManualOrders_WorkPending\\"
                                  "FUN_EU_SH_PT_ManualOrders_WP_TestData.xlsm")
    assert s["bat"].endswith("\\MO_WP_Automation_RunScript.bat")
    assert s["input_folder"].endswith("\\Manual_WorkPending_OkGen_Selected_Input_Files")
    # Every other script is untouched by the new kind.
    # Print Preview (v0.137.0) is the second script of this kind; the ten
    # plain ones are still plain.
    assert [x["name"] for x in config.tosca_scripts() if tosca_manual.is_manual(x)] == [
        SCRIPT, "Manual Orders Print Preview"]


def test_offered_for_ok_and_json_selections(env, registry):
    for f in (NA_SH, J_SH):
        out = tosca.scripts_for([str(f)], registry, env["config"])
        mine = next(s for s in out["scripts"] if s["name"] == SCRIPT)
        assert mine["matches"] == 1


# --------------------------------------------------------------------------- #
# Values — what lands in each column
# --------------------------------------------------------------------------- #
def test_one_record_per_file_in_selection_order(env, registry):
    res = _run(env, registry, SIX)
    assert res["written"] == 6
    assert [r["tab"] for r in res["records"]] == [f"ManualOrder_Detail_{i}" for i in range(1, 7)]
    assert [r["file"] for r in res["records"]] == [f.name for f in SIX]
    assert [r["process"] for r in res["records"]] == [
        "Style Header", "Pre-Ticket", "Style Header", "Pre-Ticket", "Style Header", "Pre-Ticket"]


def test_header_tab_values(env, registry):
    _run(env, registry, SIX)
    ws = _book(env["wb"])["ManualOrder_Header"]
    rows = _rows(ws, 2)
    assert len(rows) == 6                                   # nothing past the last record
    chain, process, fmt, status, source, past, key, ott, asn, zone = range(10)
    assert [r[chain] for r in rows] == ["Homegoods", "T.J. Maxx", "TJX Europe",
                                        "TJX Europe", "Winners", "Marshalls"]
    assert {r[status] for r in rows} == {"Work Pending"}
    assert {r[source] for r in rows} == {"Manual"}
    # Format is the Key sheet's exact text, as the TOSCA dropdowns need it.
    assert rows[0][fmt] == "A - Regular Tag"
    assert rows[2][fmt] == "Q - Small Merch UPP Ticket"
    # The key is the Make Unique field, per layout AND per source.
    assert rows[0][key] == "550000P1A"                      # NA .OK SH is SCAN -> keytrol+suffix
    assert rows[1][key] == "33001P3A"                       # .OK PT -> po
    assert rows[2][key] == "126539Q"                        # EU SH -> keytrol
    assert rows[3][key] == "10021888"                       # EU PT -> po
    assert rows[4][key] == "V403804208940582A"              # JSON SH, WMS -> headerASNid
    assert rows[5][key] == "40644701"                       # JSON PT -> purchaseOrderNumber
    # EU-only columns come from the file; elsewhere the sample stays.
    assert rows[2][ott] == "U" and rows[2][asn] == "S502786688062384C"
    assert rows[4][asn] == "V403804208940582A"
    assert rows[2][zone] == "01"                            # first line's CBC 311
    assert rows[3][zone] == "10"                            # EU PT header zone
    assert rows[0][past] == "Y"                             # NA SH psi


def test_scan_style_header_key_is_keytrol_plus_suffix(env, registry, tmp_path):
    """The user's rule: a SCAN Style Header's KeyValue is keytrol + header
    suffix — for JSON (SCAN read from the file) and NA .OK (SCAN by layout)."""
    scan = _json_copy(tmp_path, J_SH, "scan_sh.json", headerASNid="")
    res = _run(env, registry, [scan, NA_SH])
    assert [r["key_field"] for r in res["records"]] == ["keytrol+headerSuffix",
                                                         "keytrol+suffix"]
    ws = _book(env["wb"])["ManualOrder_Header"]
    assert ws["G2"].value == "140038A"
    assert ws["G3"].value == "550000P1A"


def test_a_blank_suffix_leaves_the_keytrol_alone(env, registry, tmp_path):
    scan = _json_copy(tmp_path, J_SH, "scan_nosfx.json", headerASNid="", headerSuffix="")
    _run(env, registry, [scan])
    assert _book(env["wb"])["ManualOrder_Header"]["G2"].value == "140038"


def test_suffix_rule_reaches_no_other_key(env, registry):
    """WMS Style Headers (JSON and EU .OK) and every Pre-Ticket keep their key,
    and Make Unique's own key field is untouched by the sheet's rule."""
    res = _run(env, registry, [J_SH, EU_SH, NA_PT, J_PT])
    assert [r["key"] for r in res["records"]] == [
        "V403804208940582A", "126539Q", "33001P3A", "40644701"]
    assert env["config"].unique_field("StyleHeader") == "keytrol"
    assert env["config"].unique_field("CalgaryStyleHeader", "SCAN") == "keytrol"


def test_detail_values_and_transforms(env, registry):
    _run(env, registry, SIX)
    book = _book(env["wb"])
    names = [c.value for c in book["ManualOrder_Detail_1"][1]]
    col = {n: i for i, n in enumerate(names)}
    na_sh = _rows(book["ManualOrder_Detail_1"], 2)
    assert len(na_sh) == 4                                  # 4 Size lines
    assert [r[col["Size"]] for r in na_sh] == ["EA", "XL", "XXL", "P65"]
    first = na_sh[0]
    assert first[col["Department"]] == "78"                 # header, under every line
    assert first[col["RetailPrice"]] == "20.99"             # 002099 -> dollars
    assert first[col["ComparePrice"]] == "0.00"
    assert first[col["LadderMM"]] == "16" and first[col["LadderMMDD"]] == "0817"
    assert first[col["VendorStyle"]] == "01104215"
    assert first[col["EnglishDesc"]] == "0619 2AT10 KCUP PPR 20CT"
    assert first[col["Quantity"]] == "2"                    # 00002 -> 2

    j_sh = _rows(book["ManualOrder_Detail_5"], 2)[0]
    assert j_sh[col["RetailPrice"]] == "7999.99"            # detail 000799999, not header
    assert j_sh[col["CompareUp"]] == "Y"                    # 'true' -> Y
    assert j_sh[col["LadderMMDD"]] == "0301"                # ladderPlan 20260301 -> MMDD
    assert j_sh[col["Type"]] == "1"                         # details.type

    eu_sh = _rows(book["ManualOrder_Detail_3"], 2)
    assert len(eu_sh) == 10
    assert eu_sh[0][col["TotalWeight"]] == "1000"           # upp_weight
    assert eu_sh[0][col["UPPWeight"]] == "M"                # upp_weight_uom
    assert eu_sh[0][col["MsgCode1"]] == "01"


def test_eu_style_header_zone_is_per_line_from_cbc(env, registry):
    res = _run(env, registry, [EU_SH])
    ws = _book(env["wb"])["ManualOrder_Detail_1"]
    names = [c.value for c in ws[1]]
    zi = names.index("Zone")
    zones = [r[zi] for r in _rows(ws, 2)]
    # 311 -> 01, 312 -> 05, 313 -> 03, 441 -> 10 (config/cbc_zones.yaml)
    assert zones == ["01", "01", "05", "05", "03", "03", "03", "10", "10", "10"]
    assert res["records"][0]["warnings"] == []
    assert "CBC" not in res["report"]


def test_an_unlisted_cbc_is_reported_not_guessed(env, registry, tmp_path):
    """A CBC the table does not list sets no zone — its lines keep the sample
    Zone — and is named in the report so the table can be extended."""
    odd = tmp_path / "EU_SH_unlisted.OK"
    odd.write_bytes(EU_SH.read_bytes().replace(b"|#|4410425", b"|#|9990425"))
    res = _run(env, registry, [odd])
    ws = _book(env["wb"])["ManualOrder_Detail_1"]
    zi = [c.value for c in ws[1]].index("Zone")
    zones = [r[zi] for r in _rows(ws, 2)]
    assert zones[7:] == ["01", "01", "10"]       # rows 9-10 = template samples; 441 still 10
    assert any("CBC 999" in w for w in res["records"][0]["warnings"])
    assert "CBC 999" in res["report"]


def test_a_column_with_no_value_keeps_the_exact_sample_cell(env, registry):
    """Not just the sample TEXT — the sample CELL: a numeric sample stays
    numeric, a shared string stays shared, so an untouched column reads exactly
    as it did."""
    _run(env, registry, [NA_SH])
    ws = _book(env["wb"])["ManualOrder_Detail_1"]
    names = [c.value for c in ws[1]]
    row2 = {n: ws.cell(row=2, column=i + 1).value for i, n in enumerate(names)}
    assert row2["UPPPrice"] == 999.99                       # never mapped
    assert row2["Fiber"] == "FIBER"                         # no layout has it yet
    # Same-row sample where the template has that row, else row 2's.
    row3 = {n: ws.cell(row=3, column=i + 1).value for i, n in enumerate(names)}
    assert row3["UPPPrice"] == 99.99


def test_calgary_header_type_is_never_used_as_the_ticket_type(env, registry, tmp_path):
    """Same name, different field: a Calgary header ``type`` is the document
    discriminator. With the detail type blank, the sample stays."""
    d = json.loads(J_PT.read_text("utf-8"))
    d["type"] = "preTickets"
    for row in d["data"]["details"]:
        row["type"] = ""
    d["data"]["type"] = "preTickets"
    p = tmp_path / "pt_notype.json"
    p.write_text(json.dumps(d), "utf-8")
    _run(env, registry, [p])
    ws = _book(env["wb"])["ManualOrder_Detail_1"]
    ti = [c.value for c in ws[1]].index("Type")
    assert [r[ti] for r in _rows(ws, 2)] == [3, 7]           # the sample's own


def test_other_layouts_are_skipped_and_reported(env, registry):
    res = _run(env, registry, [NA_SH, CL])
    assert res["written"] == 1
    assert [s["file"] for s in res["skipped"]] == ["CartonLabel.OK"]
    assert "NOT APPLICABLE" in res["report"]


def test_a_format_missing_from_the_key_sheet_is_an_error_not_a_guess(env, registry, tmp_path):
    bad = _json_copy(tmp_path, J_PT, "pt_fmt9.json", format="9")
    res = _run(env, registry, [bad, NA_SH])
    assert res["written"] == 1
    assert res["errors"][0]["file"] == "pt_fmt9.json"
    assert "Key sheet" in res["errors"][0]["error"]


# --------------------------------------------------------------------------- #
# The workbook — tabs, rows, and everything we must NOT touch
# --------------------------------------------------------------------------- #
UNTOUCHED = ("xl/worksheets/sheet1.xml",      # Login
             "xl/worksheets/sheet11.xml",     # Help_Guide
             "xl/worksheets/sheet12.xml",     # Key
             "xl/vbaProject.bin", "xl/styles.xml", "xl/sharedStrings.xml",
             "xl/theme/theme1.xml")


def _assert_package_consistent(path):
    z = _zip(path)
    for n, b in z.items():
        if n.endswith(".xml") or n.endswith(".rels"):
            ET.fromstring(b)                                # well-formed
    ct = z["[Content_Types].xml"].decode()
    wb = z["xl/workbook.xml"].decode()
    rels = z["xl/_rels/workbook.xml.rels"].decode()
    targets = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
    sheets = re.findall(r'<sheet name="([^"]+)" sheetId="(\d+)" r:id="([^"]+)"/>', wb)
    assert len({s[1] for s in sheets}) == len(sheets), "duplicate sheetId"
    for _, _, rid in sheets:
        part = "xl/" + targets[rid]
        assert part in z, f"{rid} -> missing {part}"
        assert f'PartName="/{part}"' in ct, f"no content type for {part}"
    for part in re.findall(r'PartName="/([^"]+)"', ct):
        assert part in z, f"content type for a part that is gone: {part}"
    for n in z:                                             # every rels target exists
        if n.endswith(".rels") and n != "[Content_Types].xml":
            base = n.rsplit("/_rels/", 1)[0] if "/_rels/" in n else ""
            for tgt in re.findall(r'Target="([^"]+)"', z[n].decode()):
                full = str(Path(base, tgt)).replace("\\", "/")
                parts = []
                for seg in full.split("/"):
                    if seg == "..":
                        parts.pop()
                    elif seg not in ("", "."):
                        parts.append(seg)
                assert "/".join(parts) in z, f"{n} -> missing {tgt}"
    codes = re.findall(r'codeName="([^"]+)"',
                       "".join(z[n].decode() for n in z if n.startswith("xl/worksheets/sheet")))
    assert len(codes) == len(set(codes)), "duplicate VBA codeName"
    sel = sum(z[n].decode().count('tabSelected="1"') for n in z
              if n.startswith("xl/worksheets/sheet"))
    assert sel <= 1, "more than one selected tab = grouped sheets in Excel"
    app = z["docProps/app.xml"].decode()
    names = [s[0] for s in sheets]
    assert re.findall(r"<vt:lpstr>([^<]+)</vt:lpstr>", app)[1:] == names
    assert f"<vt:i4>{len(names)}</vt:i4>" in app
    return z


@pytest.mark.parametrize("n", [1, 6, 8, 10])
def test_exactly_one_detail_tab_per_record(env, registry, tmp_path, n):
    files = [_json_copy(tmp_path, J_PT, f"pt_{i:02d}.json", purchaseOrderNumber=f"4064{i:04d}")
             for i in range(n)]
    before = _zip(env["wb"])
    res = _run(env, registry, files)
    assert res["written"] == n
    book = _book(env["wb"])
    details = [s for s in book.sheetnames if s.startswith("ManualOrder_Detail_")]
    assert details == [f"ManualOrder_Detail_{i}" for i in range(1, n + 1)]
    # Tab order: Login, Header, the detail tabs, then Help_Guide and Key.
    assert book.sheetnames == ["Login", "ManualOrder_Header", *details, "Help_Guide", "Key"]
    template = [c.value for c in book["ManualOrder_Detail_1"][1]]
    for name in details:                                    # every tab: Detail_1's names
        assert [c.value for c in book[name][1]] == template
        assert book[name].max_row == 3                      # 2 lines, nothing below
    # Rows past the records may survive only for the L:Q format lists.
    assert [r[6] for r in _rows(book["ManualOrder_Header"], 2) if r[0]] == \
        [f"4064{i:04d}" for i in range(n)]
    after = _assert_package_consistent(env["wb"])
    for part in UNTOUCHED:
        assert after[part] == before[part], f"{part} changed"
    assert res["tabs"]["added"] == [f"ManualOrder_Detail_{i}" for i in range(9, n + 1)]
    assert res["tabs"]["removed"] == [f"ManualOrder_Detail_{i}" for i in range(n + 1, 9)]


def test_existing_tabs_keep_their_own_identity_and_new_ones_get_none(env, registry, tmp_path):
    """A rebuilt tab keeps its own codeName (the VBA project names it) and its
    own printer-settings link; a NEW tab has no codeName for Excel to collide
    with and is not selected."""
    before = _zip(env["wb"])
    files = [_json_copy(tmp_path, J_PT, f"pt_{i:02d}.json") for i in range(10)]
    _run(env, registry, files)
    after = _zip(env["wb"])
    code = lambda b: (re.search(rb'codeName="([^"]+)"', b) or [None, None])[1]
    for i in range(2, 11):
        part = f"xl/worksheets/sheet{i}.xml"
        assert code(after[part]) == code(before[part])
    for part in ("xl/worksheets/sheet13.xml", "xl/worksheets/sheet14.xml"):
        assert code(after[part]) is None
        assert b'tabSelected' not in after[part] and b'r:id=' not in after[part]
    # Detail_2 had a printer-settings rels; it still resolves.
    assert b'r:id="rId1"' in after["xl/worksheets/sheet4.xml"]
    assert "xl/worksheets/_rels/sheet4.xml.rels" in after


def test_deleted_tabs_take_their_parts_with_them(env, registry):
    before = _zip(env["wb"])
    _run(env, registry, [NA_SH])
    after = _assert_package_consistent(env["wb"])
    for i in range(4, 11):                                  # Detail_2..8 = sheet4..10
        assert f"xl/worksheets/sheet{i}.xml" not in after
    assert "xl/worksheets/_rels/sheet4.xml.rels" not in after
    # printerSettings4.bin was only Detail_2's — gone; the others' stay.
    assert "xl/printerSettings/printerSettings4.bin" not in after
    for keep in ("printerSettings1.bin", "printerSettings2.bin", "printerSettings3.bin",
                 "printerSettings5.bin"):
        assert f"xl/printerSettings/{keep}" in after
    assert len(after["xl/worksheets/sheet3.xml"]) < len(before["xl/worksheets/sheet3.xml"]) * 2


def test_header_rows_below_the_records_are_deleted_but_the_format_lists_stay(env, registry):
    _run(env, registry, [NA_SH, NA_PT])
    ws = _book(env["wb"])["ManualOrder_Header"]
    assert ws.max_row == 7                                  # L:Q lists run to row 7
    for r in range(4, 8):                                   # no record data past row 3
        assert all(ws.cell(row=r, column=c).value is None for c in range(1, 11))
    assert ws["L7"].value == "6 - Master Pack Coordinate"
    assert ws["Q5"].value == "5 - AD Masterpack"


def test_a_rerun_overwrites_the_previous_run(env, registry):
    _run(env, registry, SIX)
    _run(env, registry, [EU_PT])
    book = _book(env["wb"])
    assert [s for s in book.sheetnames if s.startswith("ManualOrder_Detail_")] == \
        ["ManualOrder_Detail_1"]
    assert len(_rows(book["ManualOrder_Detail_1"], 2)) == 5
    _assert_package_consistent(env["wb"])


# --------------------------------------------------------------------------- #
# The input folder
# --------------------------------------------------------------------------- #
def test_input_folder_is_created_emptied_and_refilled(env, registry):
    res = _run(env, registry, [NA_SH, J_PT])
    assert sorted(p.name for p in env["folder"].iterdir()) == ["StyleHeader.OK", "preticket.json"]
    assert res["staging"]["created"] == 1
    (env["folder"] / "old.OK").write_text("x")
    (env["folder"] / "notes.txt").write_text("x")           # ANY file goes, per the user
    res = _run(env, registry, [EU_PT])
    assert sorted(p.name for p in env["folder"].iterdir()) == ["EUPreticket.OK"]
    assert res["staging"]["removed"] == 4
    assert "notes.txt" in res["report"]


def test_preview_shows_the_delete_before_anything_happens(env, registry):
    env["folder"].mkdir()
    (env["folder"] / "old.OK").write_text("x")
    before = _zip(env["wb"])
    pv = tosca.preview([str(NA_SH)], SCRIPT, registry, env["config"])
    assert pv["kind"] == "manual_orders"
    assert pv["remove_total"] == 1 and pv["targets"][0]["remove"] == ["old.OK"]
    assert [r["file"] for r in pv["records"]] == ["StyleHeader.OK"]
    assert (env["folder"] / "old.OK").exists() and _zip(env["wb"]) == before


def test_a_file_already_in_the_input_folder_survives_its_own_run(env, registry):
    env["folder"].mkdir()
    inside = env["folder"] / "StyleHeader.OK"
    shutil.copy2(NA_SH, inside)
    _run(env, registry, [inside])
    assert inside.read_bytes() == NA_SH.read_bytes()


def test_two_files_with_one_name_are_refused_not_one_kept(env, registry, tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    shutil.copy2(J_PT, a / "same.json"); shutil.copy2(J_PT, b / "same.json")
    res = _run(env, registry, [a / "same.json", b / "same.json", NA_SH])
    assert res["written"] == 1
    assert [e["file"] for e in res["errors"]] == ["same.json", "same.json"]


# --------------------------------------------------------------------------- #
# The review gap: write, stop, then launch on request
# --------------------------------------------------------------------------- #
def test_run_writes_but_never_starts_tosca(env, registry):
    res = _run(env, registry, [NA_SH])
    assert res["review"] is True and res["launched"] is False
    assert not env["marker"].exists()
    assert "waiting for the review" in res["report"]
    assert res["log"] and Path(res["log"]).read_text("utf-8") == res["report"]


def test_launch_starts_the_bat_and_appends_to_the_runs_log(env, registry):
    import time
    res = _run(env, registry, [NA_SH])
    out = tosca_manual.launch(SCRIPT, env["config"], res["log"])
    assert out["launched"] is True and out["bat"].endswith("MO_WP_Automation_RunScript.bat")
    for _ in range(50):
        if env["marker"].exists():
            break
        time.sleep(0.05)
    assert env["marker"].exists(), "stub .bat did not run"
    assert "LAUNCH (after review)" in Path(res["log"]).read_text("utf-8")


def test_launch_never_appends_outside_the_log_folder(env, registry, tmp_path):
    victim = tmp_path / "okgen_tosca_elsewhere.log"
    victim.write_text("keep")
    tosca_manual.launch(SCRIPT, env["config"], str(victim))
    assert victim.read_text() == "keep"


def test_launch_refuses_unsaved_edits(env, registry, monkeypatch):
    monkeypatch.setattr(tosca, "close_open_workbook", lambda p: tosca.CLOSE_UNSAVED)
    with pytest.raises(tosca.ToscaError, match="UNSAVED"):
        tosca_manual.launch(SCRIPT, env["config"])
    assert not env["marker"].exists()


def test_launch_is_only_for_manual_scripts(env):
    with pytest.raises(tosca.ToscaError):
        tosca_manual.launch("OK Functional Laser", env["config"])


def test_nothing_usable_writes_nothing(env, registry):
    before = _zip(env["wb"])
    res = _run(env, registry, [CL])
    assert res["written"] == 0 and res["review"] is False
    assert _zip(env["wb"]) == before
    assert not env["folder"].exists()


def test_the_other_scripts_are_unchanged_by_the_new_kind(env, registry, tmp_path):
    """A plain script still takes the per-combination path."""
    s = env["config"].tosca_script("JSON Functional Laser")
    shutil.copy2(Path(__file__).parent / "fixtures" / "tosca" / "laser_compare.xlsm",
                 tmp_path / "l.xlsm")
    s["workbook"] = str(tmp_path / "l.xlsm")
    s.pop("input_folders", None)
    res = tosca.run([str(J_SH)], "JSON Functional Laser", registry, env["config"],
                    launch=False, stage=False)
    assert "kind" not in res and res["written"] == 1


def test_pre_ticket_page_and_line_keep_their_leading_zeros(env, registry, tmp_path):
    """Pre-Tickets only: Page/Line as the file and the rendered view show them
    (001 001, 001 002 ...) — .OK, EU .OK and JSON alike, and padded even when a
    JSON file sends them short. Style Headers are untouched (still unpadded)."""
    short = tmp_path / "pt_short.json"
    d = json.loads(J_PT.read_text("utf-8"))
    d["data"]["details"][0]["pageNumber"] = "6"
    d["data"]["details"][0]["lineNumber"] = "12"
    short.write_text(json.dumps(d), "utf-8")
    _run(env, registry, [NA_PT, EU_PT, J_PT, short, J_SH])
    book = _book(env["wb"])
    names = [c.value for c in book["ManualOrder_Detail_1"][1]]
    pg, ln = names.index("Page"), names.index("Line")
    pl = lambda tab: [(r[pg], r[ln]) for r in _rows(book[tab], 2)]
    assert pl("ManualOrder_Detail_1")[:3] == [("001", "001"), ("002", "002"), ("003", "003")]
    assert pl("ManualOrder_Detail_2")[:3] == [("001", "001"), ("001", "002"), ("001", "003")]
    assert pl("ManualOrder_Detail_3") == [("006", "006"), ("008", "008")]
    assert pl("ManualOrder_Detail_4")[0] == ("006", "012")
    assert pl("ManualOrder_Detail_5") == [("1", "1")]          # Style Header: unchanged


def _eu_pt(tmp_path, fmt):
    p = tmp_path / f"EU_PT_{fmt}.OK"
    p.write_bytes(EU_PT.read_bytes().replace(b"P|05|A|", f"P|05|{fmt}|".encode(), 1))
    return p


def test_upp_price_is_blank_only_for_europe_u_and_q(env, registry, tmp_path):
    """UPP price is computed by the application — EMPTY on every line of a
    Europe Style Header or Pre-Ticket in format U or Q. Every other chain and
    format keeps its sample, as before."""
    eu_sh_u = tmp_path / "EU_SH_U.OK"
    eu_sh_u.write_bytes(EU_SH.read_bytes().replace(b"|D|Q|05|", b"|D|U|05|", 1))
    files = [EU_SH, eu_sh_u, _eu_pt(tmp_path, "U"), _eu_pt(tmp_path, "Q"), EU_PT, NA_PT, J_PT]
    res = _run(env, registry, files)
    assert [r["format"][0] for r in res["records"]] == ["Q", "U", "U", "Q", "A", "A", "B"]
    book = _book(env["wb"])
    ui = [c.value for c in book["ManualOrder_Detail_1"][1]].index("UPPPrice")
    col = lambda n: [r[ui] for r in _rows(book[f"ManualOrder_Detail_{n}"], 2)]
    for n in (1, 2, 3, 4):                                  # EU SH Q, EU SH U, EU PT U, EU PT Q
        assert set(col(n)) == {None}, n
    for n in (5, 6, 7):                                     # EU PT A, NA PT, JSON PT: sample
        assert col(n)[:2] == [999.99, 99.99], n
    # Blank = an empty CELL keeping the column's formatting.
    z = _zip(env["wb"])["xl/worksheets/sheet3.xml"].decode()
    assert re.search(r'<c r="AQ2" s="\d+"/>', z)


def test_total_weight_random_only_for_europe_pre_ticket_u_and_q(env, registry, tmp_path):
    allowed = {"0.1", "9999.99", "1000"}
    files = [_eu_pt(tmp_path, "U"), _eu_pt(tmp_path, "Q"), EU_PT, EU_SH, NA_PT]
    seen = set()
    for _ in range(4):                       # random: the values vary between runs
        _run(env, registry, files)
        book = _book(env["wb"])
        wi = [c.value for c in book["ManualOrder_Detail_1"][1]].index("TotalWeight")
        col = lambda n: [r[wi] for r in _rows(book[f"ManualOrder_Detail_{n}"], 2)]
        for n in (1, 2):                                    # EU PT U, EU PT Q
            vals = col(n)
            assert len(vals) == 5 and set(vals) <= allowed, vals
            seen |= set(vals)
        # Everything else exactly as before — and identical on EVERY pass, so
        # a random weight from one run can never become another file's sample.
        assert col(3) == [0.1, 0.25, 0.1, 0.1, 0.1]         # EU PT format A: sample
        assert set(col(4)) == {"1000"}                      # EU SH Q: upp_weight from file
        assert col(5)[:3] == [0.1, 0.25, 0.1]               # NA PT: sample
    assert len(seen) > 1, "every line got the same weight across 4 runs — not random"


def _sheet_values(path):
    book = _book(path)
    return {n: _rows(book[n], 1) for n in book.sheetnames
            if n.startswith("ManualOrder_")}


def test_the_same_files_always_give_the_same_sheet(env, registry):
    """The v0.136 defect: samples were read from the LIVE workbook, which every
    run rewrites, so a value one file lacked came from the PREVIOUS run. Now
    they come from the frozen sample workbook: run A, run something else, run
    A again — byte-for-byte the same values."""
    _run(env, registry, [J_PT, NA_SH])
    first = _sheet_values(env["wb"])
    _run(env, registry, [EU_SH, EU_PT, J_SH])             # fills OTT, ASN, zones, Message2...
    _run(env, registry, [J_PT, NA_SH])
    assert _sheet_values(env["wb"]) == first


def test_a_missing_sample_workbook_stops_the_run_and_says_so(env, registry, tmp_path):
    env["config"].tosca()["manual_orders"]["sample_workbook"] = str(tmp_path / "gone.xlsm")
    before = _zip(env["wb"])
    with pytest.raises(tosca.ToscaError, match="SAMPLE workbook is missing"):
        _run(env, registry, [NA_SH])
    assert _zip(env["wb"]) == before


def test_the_shipped_sample_workbook_is_the_users_untouched_sample():
    shipped = ROOT / "config" / "templates" / "ManualOrders_WP_Sample.xlsm"
    assert shipped.read_bytes() == WB_FIX.read_bytes()


def _zero_row(row):
    return {k: ("0" * len(v) if isinstance(v, str) and v else "") for k, v in row.items()}


def test_pre_ticket_trailing_zero_padding_rows_are_left_out(env, registry):
    """The NA sample Pre-Ticket has 4 real Lane rows and 19 rows of 000…
    padding (detail_fill.yaml keeps Lane at 10+). Only the 4 reach the sheet —
    the rule conversion and the editor already apply."""
    res = _run(env, registry, [NA_PT])
    assert res["records"][0]["lines"] == 4
    ws = _book(env["wb"])["ManualOrder_Detail_1"]
    assert ws.max_row == 5
    si = [c.value for c in ws[1]].index("Style")
    assert all(r[si] not in (None, "000000") for r in _rows(ws, 2))


def test_a_zero_row_between_real_lines_is_kept(env, registry, tmp_path):
    """Only TRAILING padding goes: a zero row in the middle is somebody's data."""
    d = json.loads(J_PT.read_text("utf-8"))
    real1, real2 = d["data"]["details"]
    z = _zero_row(real1)
    d["data"]["details"] = [real1, z, real2, z, z]
    p = tmp_path / "pt_zeros.json"
    p.write_text(json.dumps(d), "utf-8")
    res = _run(env, registry, [p])
    assert res["records"][0]["lines"] == 3
    ws = _book(env["wb"])["ManualOrder_Detail_1"]
    si = [c.value for c in ws[1]].index("Style")
    assert [r[si] for r in _rows(ws, 2)] == ["182517", "000000", "123456"]


def test_an_all_padding_pre_ticket_is_reported_not_written_as_zeros(env, registry, tmp_path):
    d = json.loads(J_PT.read_text("utf-8"))
    d["data"]["details"] = [_zero_row(r) for r in d["data"]["details"]]
    p = tmp_path / "pt_allzero.json"
    p.write_text(json.dumps(d), "utf-8")
    res = _run(env, registry, [p])
    assert res["records"][0]["lines"] == 1                   # one row, from the header
    assert any("no detail lines" in w for w in res["records"][0]["warnings"])
    ws = _book(env["wb"])["ManualOrder_Detail_1"]
    si = [c.value for c in ws[1]].index("Style")
    assert _rows(ws, 2)[0][si] != "000000"


def test_style_headers_keep_every_line_as_before(env, registry, tmp_path):
    """The padding rule is for Pre-Tickets (as everywhere else in OkGen);
    Style Header lines are written exactly as before."""
    d = json.loads(J_SH.read_text("utf-8"))
    d["data"]["header"]["sizes"] = [{"size": "XL", "quantity": "5"},
                                    {"size": "00", "quantity": "0000"}]
    p = tmp_path / "sh_zero_size.json"
    p.write_text(json.dumps(d), "utf-8")
    res = _run(env, registry, [p])
    assert res["records"][0]["lines"] == 2
