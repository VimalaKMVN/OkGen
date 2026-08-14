"""`.OK` Pre-Ticket -> Calgary JSON Pre-Ticket conversion.

This is the FOURTH conversion and the first of a different shape. The other
three synthesize ONE JSON detail row out of the `.OK` HEADER, because a Style
Header, Dist Label and Carton Label have no repeating detail section. A
Pre-Ticket does — the section confusingly named ``Lane`` — and its rows map to
``details[]`` one for one. The per-row machinery already existed
(``details: {from_section: …}``); this is the first config to point it at a
section that actually has rows, so the row-count boundaries are the risk.

Three mechanisms are new and are tested here rather than assumed:

* a **multi-source rule** (``from:`` naming a LIST of `.OK` fields), because the
  `.OK` splits the transmit stamp across date+time and the ladder plan across
  MMDD+YY, and the JSON joins each back together;
* a **``document:`` block**, because ``data.timestamp`` sits beside
  ``data.header`` and the header block cannot reach it;
* **``skip_filler_rows``**, because a fixed-width detail section is padded to a
  block size with all-zero rows that are structure, not order lines.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.detect import detect_layout
from okgen.layout.registry import LayoutRegistry
from okgen.okjson import (ConvertError, _is_filler, _ladder_mmyy, _ladder_plan,
                          _ok_datetime_to_stamp, _current_century)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("OKGEN_DATA_DIR", str(ROOT / "data" / "OkFileDefinitions")))
SOURCE_OK = DATA_DIR / "Preticket.OK"

pytestmark = pytest.mark.skipif(not SOURCE_OK.is_file(), reason="no Preticket.OK")


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    # the SHIPPED config: conversion is defined there, and the fixture config
    # deliberately carries a trimmed ok_to_json.yaml
    return Config.load(ROOT / "config")


def _convert(tmp_path, registry, config, keep=None):
    """Convert Preticket.OK, optionally after trimming its detail section."""
    src = tmp_path / "in"
    src.mkdir(exist_ok=True)
    p = src / "Preticket.OK"
    shutil.copy2(SOURCE_OK, p)
    if keep is not None:
        service.bulk_op_apply([str(p)], "Preticket", "Lane",
                              {"type": "keep", "count": keep},
                              registry, config, backup=False)
    res = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]
    return out, json.loads(out.read_text(encoding="utf-8"))["data"]


# ------------------------------------------------------- it converts at all ---

def test_a_preticket_is_now_convertible(tmp_path, registry, config):
    scope = service.convert_scope([str(SOURCE_OK)], registry, config)
    assert scope["target"] == "CalgaryPreticket"
    assert scope["convertible"] == 1


def test_the_output_reopens_as_a_preticket(tmp_path, registry, config):
    """A converted file that OkGen cannot read back is not a conversion. This
    also proves the template's `type` survived — writing it from the .OK would
    make the file undetectable, which is why `document:` refuses to set it."""
    out, d = _convert(tmp_path, registry, config)
    assert d["type"] == "preTickets"
    assert detect_layout(out).layout == "CalgaryPreticket"
    view = service.parse_file_view(out, registry, config)
    assert [s["name"] for s in view["sections"]] == \
        ["Header", "Lanes", "Sizes", "Stores", "Details"]


# ------------------------------------------------ one JSON row per .OK row ---

def test_the_reference_file_has_filler_rows_to_skip():
    """Everything below about row counts rests on the fixture actually carrying
    padding. Preticket.OK holds 23 detail rows of which only 4 are real; if that
    ever changes, the filler tests would still pass while testing nothing."""
    from okgen.okfile import parse_okfile
    from okgen.okjson import _section_values
    reg = LayoutRegistry.from_dir(DATA_DIR)
    okf = parse_okfile(SOURCE_OK, registry=reg)
    rows = _section_values(okf, reg.get("Preticket"), "Lane")
    assert len(rows) == 23
    assert sum(1 for r in rows if not _is_filler(r)) == 4


def test_detail_rows_map_one_for_one_skipping_the_filler(tmp_path, registry, config):
    """The user's requirement in their own words: "OK file detail lines will
    match with JSON detail lines"."""
    _, d = _convert(tmp_path, registry, config)
    assert len(d["details"]) == 4
    assert [r["pageNumber"] for r in d["details"]] == ["001", "002", "003", "004"]
    assert [r["style"] for r in d["details"]] == \
        ["719418", "819418", "719418", "819418"]


@pytest.mark.parametrize("keep,expected", [(1, 1), (2, 2), (3, 3)])
def test_the_row_count_follows_the_ok(tmp_path, registry, config, keep, expected):
    """Trimming the .OK to N real lines must give N JSON details — NOT N plus
    the ten filler rows `detail_fill.yaml` writes back. Before `skip_filler_rows`
    this produced ELEVEN details for one real line, ten of them all-zero and
    indistinguishable from data at a glance."""
    _, d = _convert(tmp_path, registry, config, keep=keep)
    assert len(d["details"]) == expected


def test_no_detail_rows_keeps_one_blank_row_not_an_empty_array(tmp_path, registry,
                                                               config):
    """D43's boundary, arriving through conversion. A bare `"details": []` tells
    the consuming system nothing about the shape it should have had; every other
    Calgary array keeps ONE row with each field present and empty, and details
    is an array like the others.

    Only reachable on a layout whose details come from a repeating section — the
    other three build their row from the header, which always exists, which is
    why this boundary has never been hit before.
    """
    _, d = _convert(tmp_path, registry, config, keep=0)
    assert d["details"] != []
    assert len(d["details"]) == 1
    row = d["details"][0]
    assert set(row) >= {"pageNumber", "style", "size"}, "the row lost its shape"
    assert not any(str(v).strip() for v in row.values() if v is not None)


# ------------------------------------------------- the multi-source fields ---

def test_the_timestamp_is_the_ok_date_and_time_joined(tmp_path, registry, config):
    """The user's call, over stamping the conversion moment: the .OK's HHMM has
    nowhere else to go, since `transmitDate` is the date alone. Seconds and
    nanoseconds are zero-filled rather than invented."""
    _, d = _convert(tmp_path, registry, config)
    assert d["timestamp"] == "2025-11-24T07:18:00.000000000Z"
    assert len(d["timestamp"]) == 30            # the declared width
    assert d["header"]["transmitDate"] == "2025-11-24"


def test_the_timestamp_transform_directly():
    assert _ok_datetime_to_stamp("20260804", "0718") == \
        "2026-08-04T07:18:00.000000000Z"
    # a missing time is 0000 rather than a refusal — the DATE is still known
    assert _ok_datetime_to_stamp("20260804", "    ").endswith("T00:00:00.000000000Z")
    # an unusable date gives BLANK, so the caller keeps the template's stamp
    # rather than writing a half-formed one the date validator would refuse
    assert _ok_datetime_to_stamp("", "0718") == ""
    assert _ok_datetime_to_stamp("2026-08", "0718") == ""


def test_the_ladder_pair_reads_both_ok_fields():
    """`ladder_mmdd` is MMDD and `ladder` is the 2-digit YEAR (user-corrected —
    the source CSV calls the second one "Ladder Plan", which reads as neither).
    The JSON joins them two different ways, so both rules read both fields."""
    assert _ladder_mmyy("0801", "26") == "0826"
    assert _ladder_plan("0801", "26") == f"{_current_century()}260801"


def test_the_century_is_todays_not_a_literal():
    """The .OK carries YY and the JSON wants CCYY, and nothing in the file says
    which century — so it comes from today. Asserted against the clock rather
    than against '20', which is what makes this fail in 2100 instead of writing
    a wrong date."""
    from datetime import datetime, timezone
    assert _current_century() == str(datetime.now(timezone.utc).year // 100)
    assert _ladder_plan("0801", "26").startswith(_current_century())


@pytest.mark.parametrize("mmdd,yy", [("0000", "12"), ("", "26"), ("0801", "")])
def test_an_unusable_ladder_is_blank_on_both_fields(mmdd, yy):
    """The user's call: blank, not null. `0000` is the SHIPPED case — the
    reference Preticket.OK really does carry it — and `00` is not a month or a
    day, so `20260000` would be a date that does not exist."""
    assert _ladder_mmyy(mmdd, yy) == ""
    assert _ladder_plan(mmdd, yy) == ""


def test_each_ladder_field_is_judged_on_its_OWN_inputs():
    """A missing DAY blanks the full plan but NOT the MMYY field, which does not
    carry a day at all — so `0800` + `26` gives `ladderPlanMMYY: "0826"` beside
    `ladderPlan: ""`.

    Stated as a test because it is the one place the two fields can disagree,
    and it was a choice: blanking both would be tidier to explain, and would
    throw away a month the .OK really does supply. One line in `_ladder_mmyy`
    reverses it if the tidier rule is preferred.
    """
    assert _ladder_mmyy("0800", "26") == "0826"
    assert _ladder_plan("0800", "26") == ""


def test_the_shipped_reference_file_converts_with_blank_ladders(tmp_path, registry,
                                                                config):
    """The end-to-end half of the case above, so the guard is not only unit-level."""
    _, d = _convert(tmp_path, registry, config)
    assert all(r["ladderPlan"] == "" and r["ladderPlanMMYY"] == ""
               for r in d["details"])


# ------------------------------------- header fields lifted from a detail row ---

def test_vendor_style_and_category_come_from_the_first_detail_row(tmp_path,
                                                                  registry, config):
    """Both live on the DETAIL line in the .OK and on the HEADER in the JSON
    (the user's call for both). Without this they would inherit the TEMPLATE —
    i.e. the vendor sample's own product data — in every converted file, which
    is D46 exactly.

    Asserted against the .OK's real values, so inheriting the template would
    fail rather than merely look plausible: the template carries `BDGB`/`7715`.
    """
    _, d = _convert(tmp_path, registry, config)
    assert d["header"]["vendorStyle"] == "VENDORST"
    assert d["header"]["category"] == "0121"
    assert d["header"]["vendorStyle"] != "BDGB", "inherited the template"
    assert d["header"]["category"] != "7715", "inherited the template"


# --------------------------------------------------- the rest of the header ---

def test_the_header_carries_the_ok_values(tmp_path, registry, config):
    _, d = _convert(tmp_path, registry, config)
    h = d["header"]
    assert h["chain"] == "01"
    assert h["format"] == "A"
    assert h["purchaseOrderNumber"] == "33001P3A"      # the key
    assert h["department"] == "06"
    assert h["blockNumber"] == "005"
    assert h["numberOfBlocks"] == "001"
    # no ASN field on this .OK layout -> null, which is what makes the converted
    # file a SCAN file and therefore keyed by the PO
    assert h["headerASNid"] is None


def test_prices_are_zero_padded_like_the_style_header(tmp_path, registry, config):
    """The user's call: "similar to StyleHeader with 0s in the front" — and
    SIX characters wide, not nine.

    A converted file is always a SCAN file, and a SCAN price is 6 digits; the
    9-digit form belongs to WMS files, which conversion never produces. The
    `.OK` field is itself 6 wide, so the front zeros are already there and the
    transform normally passes the value straight through.
    """
    _, d = _convert(tmp_path, registry, config)
    row = d["details"][0]
    assert row["compareAtPrice"] == "999999"
    assert row["retailPrice"] == "599999"
    assert len(row["compareAtPrice"]) == len(row["retailPrice"]) == 6


def test_line_count_is_copied_and_may_disagree_with_the_rows(tmp_path, registry,
                                                             config):
    """The user chose "copy from the .OK files for now" over counting the rows.

    This asserts the CONSEQUENCE rather than only the rule, because the shipped
    reference file already shows it: its header claims 5 detail lines and it
    carries 4 real ones, so the converted JSON says `lineCount: "05"` beside 4
    details. That is the documented behaviour today — if it is ever wrong, this
    test is the one to change, and the fix is one line of ok_to_json.yaml.
    """
    _, d = _convert(tmp_path, registry, config)
    assert d["header"]["lineCount"] == "05"
    assert len(d["details"]) == 4


# -------------------------------------------------------- config misuse ---

def test_a_list_from_needs_a_multi_source_transform(tmp_path, registry, config):
    """A `from:` naming two fields with a single-source transform must be an
    ERROR naming both, not a silent wrong value — the whole class this file's
    layout keeps producing (a no-op that reports success)."""
    from okgen import okjson
    from okgen.okfile import parse_okfile
    okf = parse_okfile(SOURCE_OK, registry=registry)
    spec = {"header": {"style": {"from": ["date", "time"], "transform": "iso_date"}}}
    template = json.loads((ROOT / "config" / "templates"
                           / "CalgaryPreticket.json").read_text(encoding="utf-8"))
    with pytest.raises(ConvertError) as exc:
        okjson.convert(okf, registry.get("Preticket"), spec, template)
    assert "iso_date" in str(exc.value) and "multi-source" in str(exc.value)


def test_the_document_block_refuses_to_set_type(tmp_path, registry, config):
    """`type` is the detection discriminator: writing it from the .OK could only
    ever make the file unopenable, so config is refused rather than obeyed."""
    from okgen import okjson
    from okgen.okfile import parse_okfile
    okf = parse_okfile(SOURCE_OK, registry=registry)
    template = json.loads((ROOT / "config" / "templates"
                           / "CalgaryPreticket.json").read_text(encoding="utf-8"))
    with pytest.raises(ConvertError) as exc:
        okjson.convert(okf, registry.get("Preticket"),
                       {"document": {"type": {"from": "format"}}}, template)
    assert "type" in str(exc.value)


def test_the_document_block_refuses_an_unknown_field(tmp_path, registry, config):
    from okgen import okjson
    from okgen.okfile import parse_okfile
    okf = parse_okfile(SOURCE_OK, registry=registry)
    template = json.loads((ROOT / "config" / "templates"
                           / "CalgaryPreticket.json").read_text(encoding="utf-8"))
    with pytest.raises(ConvertError) as exc:
        okjson.convert(okf, registry.get("Preticket"),
                       {"document": {"nosuchfield": {"from": "format"}}}, template)
    assert "nosuchfield" in str(exc.value)


# ------------------------------------------------ the other three are intact ---

def test_the_other_conversions_still_declare_no_document_block(config):
    """The `document:` block is new and OPTIONAL. All three shipped conversions
    still inherit the template's timestamp exactly as they did — asserted here
    so that adding one to them becomes a deliberate act with a visible diff,
    rather than something this layout dragged in behind it."""
    convs = config.conversions()
    for name in ("StyleHeader", "DistLabels", "CartonLabel"):
        assert "document" not in convs[name]
    assert "document" in convs["Preticket"]
