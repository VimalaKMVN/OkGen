"""Converting an .OK file whose section has NO rows must not borrow the
template's rows.

User-reported: deleting every store from a DistLabels or CartonLabel `.OK`,
saving, then converting produced a JSON still carrying stores. They were not
the file's old stores — they were the **vendor sample's**, from the template:

    DistLabels.OK own stores : 0090, 0101, 0102, 0133, 0154, 0173 …
    converted output         : 0100, 0005, 0176, 0166, 0142, 0115 …
    CalgaryDistLabel template: 0100, 0005, 0176, 0166, 0142, 0115 …

D33's rule was "an .OK section with no real rows leaves the template's SINGLE
placeholder row alone — every vendor sample carries exactly one." That premise
holds only for CalgaryStyleHeader, which is where the rule was worked through.
CalgaryDistLabel's template ships **10** real stores and CalgaryCartonLabel's
**5**, so on those two layouts the rule emitted a different order's store
numbers, units and quantities — with `numberOfStores` agreeing, so the document
was internally consistent and nothing downstream would flag it. That is D34's
borrowed-data failure, reaching through the array path instead of the header.

An empty section now emits ONE row with the section's tags and no values — the
same shape the JSON engine writes when a bulk op empties a section, so the two
paths cannot drift.
"""
import json
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(
    not (FIXTURE_CONFIG / "ok_to_json.yaml").is_file(),
    reason="no conversion fixture config")

# (.OK file, its layout, the section holding stores)
CASES = [
    ("DistLabels.OK", "DistLabels", "Store"),
    ("CartonLabel.OK", "CartonLabel", "store"),
]


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _emptied_and_converted(tmp_path, name, layout, section, registry, config):
    """The user's exact flow: delete every store row, save, convert."""
    p = tmp_path / name
    shutil.copy2(DATA_DIR / name, p)
    res = service.bulk_op_apply([str(p)], layout, section,
                                {"type": "keep", "count": 0},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    cres = service.convert_apply([str(p)], registry, config)
    assert cres["errors"] == [], cres["errors"]
    out = sorted(Path(cres["folder"]).glob("*.json"))[0]
    return json.loads(out.read_text(encoding="utf-8"))


def _template_stores(config, layout):
    spec = config.conversion_for(layout)
    tpl = json.loads((Path(config.config_dir) / spec["template"]).read_text())
    return tpl["data"]["header"]["stores"]


# --------------------------------------------------------------------------- #
# The reported bug
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,layout,section", CASES)
def test_no_store_rows_means_no_store_data(tmp_path, registry, config,
                                           name, layout, section):
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    stores = doc["data"]["header"]["stores"]

    assert len(stores) == 1, f"expected one empty row, got {len(stores)}"
    assert all(v is None or not str(v).strip() for v in stores[0].values()), stores[0]


@pytest.mark.parametrize("name,layout,section", CASES)
def test_the_templates_store_numbers_never_reach_the_output(
        tmp_path, registry, config, name, layout, section):
    """The sharpest form of the bug: real store numbers from another order."""
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    emitted = {s.get("store") for s in doc["data"]["header"]["stores"]}
    borrowed = {s.get("store") for s in _template_stores(config, layout)}

    assert borrowed, "template must carry stores for this test to mean anything"
    assert not (emitted & borrowed), f"template stores leaked: {emitted & borrowed}"


@pytest.mark.parametrize("name,layout,section", CASES)
def test_the_store_count_never_claims_a_store(tmp_path, registry, config,
                                              name, layout, section):
    """The count agreeing with the borrowed rows is what made the document look
    legitimate. One tag-carrying row is not one store — whatever form the empty
    count takes, it must not be a number of stores."""
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    h = doc["data"]["header"]

    for field in ("numberOfStores", "storeLines"):
        v = h.get(field)
        assert v is None or not str(v).strip() or int(str(v)) == 0, f"{field}={v!r}"


@pytest.mark.parametrize("name,layout,section", CASES)
def test_the_empty_counts_are_the_declared_ones(tmp_path, registry, config,
                                                name, layout, section):
    """The user's chosen forms for a store-less distribution/carton label. These
    are what a downstream system reads, so they are pinned literally rather than
    read back out of the config they come from."""
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    h = doc["data"]["header"]

    assert h["numberOfStores"] == ""
    assert h["storeLines"] is None
    assert h["laneRecords"] is None


@pytest.mark.parametrize("name,layout,section", CASES)
def test_the_kept_row_still_carries_every_field_tag(tmp_path, registry, config,
                                                    name, layout, section):
    """Tags are the reason a row is kept at all — a bare {} would be no better
    than []."""
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    kept = doc["data"]["header"]["stores"][0]
    expected = set(_template_stores(config, layout)[0])

    assert set(kept) == expected


@pytest.mark.parametrize("name,layout,section", CASES)
def test_the_converted_file_still_opens_in_okgen(tmp_path, registry, config,
                                                 name, layout, section):
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    assert doc["data"]["header"]                       # parsed as valid JSON


# --------------------------------------------------------------------------- #
# What must NOT change
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,layout,section", CASES)
def test_a_file_that_still_has_stores_converts_them_normally(
        tmp_path, registry, config, name, layout, section):
    """The fix must fire ONLY on an empty section. With rows present the output
    is built from the .OK exactly as before."""
    p = tmp_path / name
    shutil.copy2(DATA_DIR / name, p)
    cres = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(cres["folder"]).glob("*.json"))[0]
    h = json.loads(out.read_text())["data"]["header"]

    assert len(h["stores"]) > 1
    assert any(s.get("store") for s in h["stores"]), "real store data was wiped"
    assert h.get("numberOfStores") == str(len(h["stores"]))


def test_a_null_array_stays_null(tmp_path, registry, config):
    """DistLabel's template has `lanes`/`sizes` as JSON null — not an emptied
    array. Inventing a row would claim a section the layout does not have."""
    doc = _emptied_and_converted(tmp_path, "DistLabels.OK", "DistLabels",
                                 "Store", registry, config)
    h = doc["data"]["header"]

    assert h.get("lanes") is None
    assert h.get("sizes") is None


def test_styleheader_is_unaffected_by_layout(tmp_path, registry, config):
    """StyleHeader has no store section at all, so its stores row was already
    tags-only. It must stay one row and stay empty."""
    p = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
    cres = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(cres["folder"]).glob("*.json"))[0]
    h = json.loads(out.read_text())["data"]["header"]

    assert len(h["stores"]) == 1
    assert all(v is None or not str(v).strip() for v in h["stores"][0].values())
    # Its store counts keep the single-space form this layout has always
    # carried — declared in `empty_counts` rather than inherited, but the same
    # value, so a StyleHeader conversion is unchanged by that config.
    assert h["numberOfStores"] == " " and h["storeLines"] == " "
    # ...and `laneRecords` still reports the lanes the file DOES have.
    assert h["laneRecords"] == "10" and len(h["lanes"]) == 10


def test_an_emptied_lane_section_nulls_the_lane_count(tmp_path, registry, config):
    """`laneRecords` is not a computed count (lanes/sizes are excluded on
    purpose, D35) — it is copied from the .OK header, which reports `00` once
    the section is emptied. The user's call is that a section with no lanes
    reports null rather than a zero-ish string."""
    p = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
    service.bulk_op_apply([str(p)], "StyleHeader", "Lane",
                          {"type": "keep", "count": 0}, registry, config, backup=False)

    cres = service.convert_apply([str(p)], registry, config)
    h = json.loads(sorted(Path(cres["folder"]).glob("*.json"))[0].read_text())["data"]["header"]

    assert h["laneRecords"] is None
    assert h["lanes"] == [{"lane": ""}], h["lanes"]
    # sizes untouched by emptying lanes
    assert len(h["sizes"]) == 4


def test_the_report_no_longer_calls_them_placeholders(tmp_path, registry, config):
    """The coverage report DID mention this — as '10 placeholder row(s)', which
    is exactly what made ten real stores read as harmless."""
    p = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", p)
    service.bulk_op_apply([str(p)], "DistLabels", "Store",
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)

    pv = service.convert_preview([str(p)], registry, config)
    rows = [r for r in pv["samples"][0]["report"] if r["field"] == "stores[]"]

    assert rows, "the report must still account for the section"
    assert "placeholder" not in str(rows[0]).lower(), rows[0]


# --------------------------------------------------------------------------- #
# Counts conversion must NOT touch when the .OK carries data
# --------------------------------------------------------------------------- #
def _converted(tmp_path, name, registry, config):
    p = tmp_path / name
    shutil.copy2(DATA_DIR / name, p)
    cres = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(cres["folder"]).glob("*.json"))[0]
    return json.loads(out.read_text(encoding="utf-8"))["data"]["header"]


@pytest.mark.parametrize("name,stores", [("DistLabels.OK", 10), ("CartonLabel.OK", 91)])
def test_storelines_is_left_alone_when_the_ok_has_stores(tmp_path, registry, config,
                                                         name, stores):
    """The user's call: `storeLines` is never recalculated. `numberOfStores`
    still is, so this also pins that the two are deliberately different."""
    h = _converted(tmp_path, name, registry, config)

    assert len(h["stores"]) == stores
    assert h["numberOfStores"] == str(stores)
    assert h["storeLines"] is None


def test_linecount_is_left_alone_when_details_are_built(tmp_path, registry, config):
    """StyleHeader always emits one details row (built from the .OK header), and
    `lineCount` must still not be written for it."""
    h = _converted(tmp_path, "StyleHeader.OK", registry, config)

    assert len(json.dumps(h)) and h["lineCount"] == " "


def test_a_count_left_alone_is_reported_as_the_templates(tmp_path, registry, config):
    """Provenance has to say so, or 'not updated' is indistinguishable from
    'updated to the same value' in the coverage report."""
    p = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", p)

    pv = service.convert_preview([str(p)], registry, config)
    rows = {r["field"]: r for r in pv["samples"][0]["report"]}

    assert rows["storeLines"]["provenance"] == "template"
    assert rows["numberOfStores"]["provenance"] == "count"


# --------------------------------------------------------------------------- #
# A field the .OK cannot supply, declared outright in config
# --------------------------------------------------------------------------- #
def test_a_distlabel_store_date_is_stamped_not_inherited(tmp_path, registry, config):
    """`date` has no .OK source. It used to fall through to the TEMPLATE, so
    every converted store carried the vendor sample's timestamp — another
    order's data (D34/D39's class, wearing a date). Config now declares
    `value: now`, so it is stamped per conversion."""
    import datetime
    import re
    h = _converted(tmp_path, "DistLabels.OK", registry, config)
    template = json.loads(
        (Path(config.config_dir) /
         config.conversion_for("DistLabels")["template"]).read_text())
    borrowed = {s.get("date") for s in template["data"]["header"]["stores"]}

    stamps = {s["date"] for s in h["stores"]}
    assert not (stamps & borrowed), f"template timestamp leaked: {stamps & borrowed}"
    for s in stamps:
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}Z$", s), s
        assert s.startswith(datetime.datetime.utcnow().strftime("%Y-%m-%d")), s


def test_the_other_layouts_keep_their_own_date_rule(tmp_path, registry, config):
    """Opt-in per layout, per field: CartonLabel's samples carry `date: null`
    and it declares nothing, so nothing is stamped there."""
    h = _converted(tmp_path, "CartonLabel.OK", registry, config)

    assert all(s["date"] is None for s in h["stores"])


def test_a_generated_field_is_reported_as_generated(tmp_path, registry, config):
    """Provenance must distinguish it from `ok` and from `template` — the whole
    point of the coverage report is that nothing is invented silently."""
    p = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", p)

    pv = service.convert_preview([str(p)], registry, config)
    row = next(r for r in pv["samples"][0]["report"]
               if r["field"] == "stores[].date")

    assert row["provenance"] == "generated"
    assert row["source"] == "declared in config"


# --------------------------------------------------------------------------- #
# A StyleHeader with NO size lines: the empty size row carries `tot_qty`
# --------------------------------------------------------------------------- #
# User-reported. The empty-row rule above is right about not borrowing another
# order's data, but on this one field "no rows" does not mean "no value": a
# StyleHeader with no size lines still has a printed quantity, and it is
# `tot_qty`. That is the same thing D58 settled on the .OK side — with no rows
# the header total is AUTHORITATIVE rather than a sum of rows that do not exist
# — so writing null here threw away the file's only quantity.
#
# Declared as `empty_row:` on the `sizes` array in ok_to_json.yaml, so it is
# per layout and per field: `size` stays null (there is no size to name), and
# DistLabel/CartonLabel declare nothing and are untouched.
def _stripped_size_rows(tmp_path, name, registry, config):
    """Delete every Size row, save, convert — the user's exact flow."""
    p = tmp_path / name
    shutil.copy2(DATA_DIR / name, p)
    res = service.bulk_op_apply([str(p)], "StyleHeader", "Size",
                                {"type": "keep", "count": 0},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    return p


def _ok_header_value(path, registry, config, field):
    view = service.parse_file_view(path, registry, config)
    return view["sections"][0]["records"][0]["values"][field]


def test_an_empty_size_section_carries_the_total_quantity(tmp_path, registry, config):
    p = _stripped_size_rows(tmp_path, "StyleHeader.OK", registry, config)
    # D58: deleting the last Size row KEEPS the total byte-for-byte.
    assert _ok_header_value(p, registry, config, "tot_qty") == "0000022"

    cres = service.convert_apply([str(p)], registry, config)
    assert cres["errors"] == [], cres["errors"]
    h = json.loads(sorted(Path(cres["folder"]).glob("*.json"))[0]
                   .read_text(encoding="utf-8"))["data"]["header"]

    assert h["sizes"] == [{"size": None, "quantity": "22"}]
    # The same string form a populated row writes — not the raw padded value.
    assert h["totalQuantity"] == "22"


def test_the_size_itself_stays_null(tmp_path, registry, config):
    """Only the quantity is recoverable. A size the .OK never carried would be
    invented, which is the one thing conversion may not do (D33)."""
    p = _stripped_size_rows(tmp_path, "StyleHeader.OK", registry, config)
    h = json.loads(sorted(Path(service.convert_apply([str(p)], registry, config)["folder"])
                          .glob("*.json"))[0].read_text(encoding="utf-8"))["data"]["header"]
    assert h["sizes"][0]["size"] is None


def _force_total(path, registry, total):
    """Write `tot_qty` STRAIGHT to the bytes, bypassing the save path.

    Deliberate: D58 seeds a blank or zero total with a random 5-10 whenever a
    no-size-lines file is saved, so OkGen's own editor can never leave a
    `0000000` on disk. A file that arrives carrying one (PLAN section 6's open
    thread) still converts, and that is the case under test — going through
    apply_edits would silently test the seed instead.
    """
    okf = service.parse_okfile(path, registry=registry)
    okf.records[0].set("tot_qty", total)
    path.write_bytes(okf.to_bytes())


@pytest.mark.parametrize("total,expected", [
    ("0000022", "22"), ("0000500", "500"),
    # The user's explicit call: a zero or blank total is copied through as "0"
    # rather than reverting to null, so the rule has no exception to remember.
    ("0000000", "0"), ("       ", "0"),
])
def test_whatever_tot_qty_says_the_size_row_says(tmp_path, registry, config,
                                                 total, expected):
    p = _stripped_size_rows(tmp_path, "StyleHeader.OK", registry, config)
    _force_total(p, registry, total)
    assert _ok_header_value(p, registry, config, "tot_qty") == total

    h = json.loads(sorted(Path(service.convert_apply([str(p)], registry, config)["folder"])
                          .glob("*.json"))[0].read_text(encoding="utf-8"))["data"]["header"]
    assert h["sizes"] == [{"size": None, "quantity": expected}]


def test_saving_a_zero_total_seeds_it_rather_than_converting_a_zero(
        tmp_path, registry, config):
    """The other half, stated so the case above is not misread: OkGen's own SAVE
    never leaves a zero on a no-size-lines file — D58 seeds it 5-10 — so a
    converted "0" can only come from a file that arrived that way."""
    p = _stripped_size_rows(tmp_path, "StyleHeader.OK", registry, config)
    _force_total(p, registry, "0000000")

    service.apply_edits(p, [{"section_index": 0, "record_index": 0,
                             "field": "tot_qty", "value": "0000000"}],
                        registry, backup=False, config=config)

    seeded = _ok_header_value(p, registry, config, "tot_qty")
    assert seeded != "0000000"
    assert 5 <= int(seeded) <= 10, seeded
    h = json.loads(sorted(Path(service.convert_apply([str(p)], registry, config)["folder"])
                          .glob("*.json"))[0].read_text(encoding="utf-8"))["data"]["header"]
    assert h["sizes"] == [{"size": None, "quantity": str(int(seeded))}]


def test_a_file_that_HAS_size_lines_is_completely_unaffected(tmp_path, registry, config):
    """The whole point of `empty_row:` — it fires only when there are no rows.
    A populated file keeps each row's OWN quantity; the header total is not
    injected anywhere, and the four rows here sum to 8 against a stale 22."""
    h = _converted(tmp_path, "StyleHeader.OK", registry, config)

    assert h["sizes"] == [{"size": "EA", "quantity": "2"},
                          {"size": "XL", "quantity": "2"},
                          {"size": "XXL", "quantity": "2"},
                          {"size": "P65", "quantity": "2"}]
    assert h["totalQuantity"] == "22"          # stale on purpose (D58's sample)


@pytest.mark.parametrize("name,layout,section", [
    ("DistLabels.OK", "DistLabels", "Store"),
    ("CartonLabel.OK", "CartonLabel", "store"),
])
def test_the_other_layouts_declare_no_empty_row_and_are_untouched(
        tmp_path, registry, config, name, layout, section):
    """Per layout, per field. Neither of these declares `empty_row:`, so an
    emptied section still writes the shared json_empty_rows.yaml values."""
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    stores = doc["data"]["header"]["stores"]
    assert len(stores) == 1
    assert all(v in (None, "", " ") for v in stores[0].values()), stores[0]


def test_the_borrowed_quantity_is_reported_not_silent(tmp_path, registry, config):
    """D33: conversion may invent nothing SILENTLY. This value is read from the
    .OK, so the coverage report must name where it came from — otherwise the one
    field that crosses sections is the only one not accounted for."""
    p = _stripped_size_rows(tmp_path, "StyleHeader.OK", registry, config)

    pv = service.convert_preview([str(p)], registry, config)
    row = next(r for r in pv["samples"][0]["report"]
               if r["field"] == "sizes[].quantity")

    assert row["value"] == "22"
    assert "Header.tot_qty" in row["source"], row["source"]
    assert "no rows" in row["source"], row["source"]


def test_a_misdeclared_empty_row_field_fails_loudly(tmp_path, registry, config):
    """A typo must not write a stray key into the row (or vanish). The section's
    own keys come from the template, so anything else is a config error."""
    from okgen import okjson
    with pytest.raises(okjson.ConvertError, match="not a field of that section"):
        okjson._empty_row([{"size": None, "quantity": None}], set(), set(), {},
                          "sizes", {"qty": {"from": "tot_qty"}},
                          {"Header": [{"tot_qty": "0000022"}]}, {}, None)


def test_an_empty_row_reading_a_missing_section_fails_loudly(tmp_path, registry, config):
    from okgen import okjson
    with pytest.raises(okjson.ConvertError, match="which the .OK does not have"):
        okjson._empty_row([{"size": None, "quantity": None}], set(), set(), {},
                          "sizes", {"quantity": {"from": "tot_qty",
                                                 "from_section": "Nope"}},
                          {"Header": [{"tot_qty": "0000022"}]}, {}, None)
