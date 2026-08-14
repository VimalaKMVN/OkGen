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
  `.OK` splits the transmit stamp across date+time and the JSON joins them back
  together. *The ladder plan used to be the second user of this and is not any
  more: the `.OK` carries MMDD with no year at all, so the rule is
  single-source and the year comes from the clock.*
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
from okgen.okjson import ConvertError, _is_filler, _ok_datetime_to_stamp

# Probed rather than imported directly, so this module still LOADS on a build
# that predates a symbol. A missing name raises while the module is importing,
# which makes pytest report one collection ERROR having run ZERO checks — the
# truncated-run failure this repo has now hit repeatedly when diffing against an
# older tag. Each test then fails on its own terms instead.
import okgen.okjson as _oj
_mmdd_to_mmyy = getattr(_oj, "_mmdd_to_mmyy", None)
_mmdd_to_plan = getattr(_oj, "_mmdd_to_plan", None)
_current_century = getattr(_oj, "_current_century", None)
_current_year = getattr(_oj, "_current_year", None)

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


def test_the_ladder_is_MMDD_plus_the_CURRENT_year():
    """`ladder_mmdd` is **MMDD with no year at all**, and `ladder` — the 2-char
    field beside it — is the number of MONTHS the merchandise may stay in
    holdings (3/8/12/24), NOT a year.

    ***This corrects an earlier user correction*** recorded in PLAN D83, which
    had `ladder` as the 2-digit year. Under that reading the conversion turned
    "12 months in holding" into "the year 2012".

    The vendor files settle it: they carry `ladderPlanMMYY: '0426'` beside
    `ladderPlan: '20260401'`, which only the MMDD + current-year rule
    reproduces — see test_it_reproduces_the_vendor_ladder_values.
    """
    yy = _current_year()[2:4]
    assert _mmdd_to_mmyy("0829") == f"08{yy}"
    assert _mmdd_to_plan("0829") == f"{_current_year()}0829"


def test_the_year_is_todays_not_a_literal():
    """The `.OK` supplies no year at all, so it comes from the clock. Asserted
    against the clock rather than against '2026', which is what makes this fail
    when the calendar turns instead of writing a wrong date.

    Note the consequence, which is inherent to the rule: converting the SAME
    `.OK` next year produces a different `ladderPlan`.
    """
    from datetime import datetime, timezone
    assert _current_year() == str(datetime.now(timezone.utc).year)
    assert _mmdd_to_plan("0829").startswith(_current_year())
    assert _current_century() == str(datetime.now(timezone.utc).year // 100)


def test_it_reproduces_the_vendor_ladder_values():
    """The load-bearing check: the rule is right because it reproduces real
    files, not because it was asserted.

    All 13 vendor detail rows carry `('0426', '20260401')` or
    `('0326', '20260301')`. Both fall out of an `.OK` MMDD of `0401` / `0301`
    under this rule — and neither falls out of the two rules this replaces,
    which produced `20010401` (day read as a year) and `20120401` (holding
    period read as a year).
    """
    yy, yyyy = _current_year()[2:4], _current_year()
    assert (_mmdd_to_mmyy("0401"), _mmdd_to_plan("0401")) == (f"04{yy}", f"{yyyy}0401")
    assert (_mmdd_to_mmyy("0301"), _mmdd_to_plan("0301")) == (f"03{yy}", f"{yyyy}0301")


def test_the_day_is_NOT_forced_to_the_first_of_the_month():
    """Every vendor row is a first-of-month, which is exactly what made the old
    first-of-month rule look correct. The day is the `.OK`'s own DD; the vendor
    rows simply carry `01` there. A mid-month plan must survive."""
    assert _mmdd_to_plan("0815").endswith("0815")
    assert _mmdd_to_plan("1231").endswith("1231")


def test_an_all_zero_ladder_becomes_all_zeros_not_blank():
    """The user's explicit call, and the SHIPPED case — both Pre-Ticket samples
    carry `0000` on every row. The two fields go all-zero TOGETHER, so "no
    ladder plan" reads the same way in each."""
    assert _mmdd_to_mmyy("0000") == "0000"
    assert _mmdd_to_plan("0000") == "00000000"


@pytest.mark.parametrize("bad", ["", "   ", "4542", "1345", "0230x", "99"])
def test_an_unusable_ladder_is_BLANK_on_both_fields(bad):
    """Blank, not zero-filled: `0000` means "no plan" and is written as zeros,
    while junk means "this is not a date" and must not be dressed up as one.

    `4542` is not hypothetical — it is the Pre-Ticket layout spec's own
    sample_value, and month 45 is not a month.
    """
    assert _mmdd_to_mmyy(bad) == ""
    assert _mmdd_to_plan(bad) == ""


def test_the_shipped_reference_file_converts_with_ZEROED_ladders(tmp_path, registry,
                                                                 config):
    """The end-to-end half, so the rule is not only proven at unit level.

    Both shipped Pre-Ticket samples carry `ladder_mmdd = '0000'` on every row,
    so the zero form is the one the reference file actually exercises — the
    real MMDD path has no sample and is covered by
    :func:`test_a_real_ladder_converts_end_to_end`.
    """
    _, d = _convert(tmp_path, registry, config)
    assert d["details"], "no detail rows to check"
    assert all(r["ladderPlanMMYY"] == "0000" and r["ladderPlan"] == "00000000"
               for r in d["details"])


def test_a_real_ladder_converts_end_to_end(tmp_path, registry, config):
    """A non-zero MMDD, through a real conversion rather than the transform.

    No shipped `.OK` carries one, so the value is written in first — otherwise
    the only end-to-end coverage would be the all-zeros case, and a rule that
    zeroed EVERYTHING would pass it.
    """
    src = tmp_path / "in"
    src.mkdir(exist_ok=True)
    p = src / "Preticket.OK"
    shutil.copy2(SOURCE_OK, p)
    view = service.parse_file_view(p, registry, config)
    lane = next(s for s in view["sections"] if s["name"] == "Lane")
    service.apply_edits(p, [{"record_index": lane["records"][0]["index"],
                             "field": "ladder_mmdd", "value": "0829"}],
                        registry, config=config, backup=False)
    res = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]
    d = json.loads(out.read_text(encoding="utf-8"))["data"]
    row = d["details"][0]
    yy, yyyy = _current_year()[2:4], _current_year()
    assert row["ladderPlanMMYY"] == f"08{yy}"
    assert row["ladderPlan"] == f"{yyyy}0829"
    # the OTHER rows still carry the zero form, so this did not blanket-apply
    assert d["details"][1]["ladderPlan"] == "00000000"


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


def test_line_count_is_COUNTED_from_the_rows_not_copied(tmp_path, registry,
                                                        config):
    """`lineCount` equals the detail lines actually emitted.

    ***This REVERSES the original "copy from the .OK files for now"***, which
    the user confirmed with their team. The shipped reference file is why it
    mattered: its header claims 5 detail lines while carrying 4 real ones, so
    the copy put `lineCount: "05"` beside 4 details in every converted file.

    Counting happens AFTER `skip_filler_rows` drops the trailing all-zero
    padding, so this is the number of real order lines and not the block size —
    the distinction that makes 4 the right answer rather than 14.
    """
    _, d = _convert(tmp_path, registry, config)
    assert d["header"]["lineCount"] == "4"
    assert len(d["details"]) == 4


def test_line_count_follows_a_TRIMMED_detail_section(tmp_path, registry, config):
    """The count tracks the rows, rather than happening to match once.

    Converting a file whose detail section was trimmed to 2 must say 2 — a test
    that only ever saw the reference file would pass just as well on a build
    that hard-coded 4, or on one that still copied a header that happened to
    agree.
    """
    _, d = _convert(tmp_path, registry, config, keep=2)
    assert len(d["details"]) == 2
    assert d["header"]["lineCount"] == "2"


def test_a_style_header_line_count_stays_BLANK(tmp_path, registry, config):
    """Scoped to the Pre-Ticket, deliberately.

    All 13 vendor Calgary Style Headers carry `lineCount` as a single space, and
    that layout's conversion writes it through `empty_counts`. Counting there
    would contradict every real file, so the guard belongs beside the change.
    """
    src = tmp_path / "sh"
    src.mkdir()
    p = src / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
    res = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]
    d = json.loads(out.read_text(encoding="utf-8"))["data"]
    assert d["header"]["lineCount"] == " "
    assert len(d["details"]) == 1        # it HAS a detail row, and still says ' '


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
