"""The Calgary JSON Pre-Ticket layout — the 4th `json_mode` layout.

The whole point of this layout is that it is NOT new machinery: its header is
the Style Header's 53-field union header plus ``lpn``, and its detail row is the
Style Header's 23 fields plus ``size``. So the tests that matter are the ones
that prove the SHAPE is right and that the two extra fields are reachable on
every write path — the rest of the JSON engine is already covered by
``test_json_engine.py``, which now includes ``preticket.json`` in its CASES.

Two things here are specific to Pre-Tickets rather than inherited:

* **Multiple detail lines.** Every other Calgary sample carries ONE detail row
  (or none). This is the first with two, and D47 is the reason that is tested in
  BOTH directions: the splice engine's failure mode is a silent no-op, so
  growing a section is a separate risk from shrinking one.
* **A plain unique key.** ``purchaseOrderNumber`` is the key whichever source a
  folder is, unlike the source-keyed pairs the other three use. D62 is why that
  is asserted for a MISSING source too: a source-less lookup that silently
  returns a default is exactly how Bulk Edit once greyed the wrong field.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.detect import detect_layout
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(
    os.environ.get("OKGEN_DATA_DIR",
                   str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions")))
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"
SAMPLE = FIX / "preticket.json"

pytestmark = pytest.mark.skipif(not SAMPLE.is_file(), reason="no preticket fixture")


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _copy(tmp_path, name="pt.json"):
    """A Pre-Ticket in a folder named SCAN, so source resolution has an answer."""
    folder = tmp_path / "SCAN"
    folder.mkdir(exist_ok=True)
    dst = folder / name
    shutil.copy2(SAMPLE, dst)
    return dst


def _doc(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]


# --------------------------------------------------------------- the shape ---

def test_detected_by_its_own_document_type():
    assert detect_layout(SAMPLE).layout == "CalgaryPreticket"


def test_the_sample_is_valid_json():
    """The file the user supplied was NOT: `"houseNumber": 000000` is a
    leading-zero number, which no strict parser accepts, so OkGen could not have
    opened it at all. The value is a 9-wide STRING (user-confirmed) and the
    template carries it quoted. Asserted rather than assumed, because a template
    that cannot be parsed breaks conversion and Generate, not just this file."""
    d = _doc(SAMPLE)
    for row in d["details"]:
        assert isinstance(row["houseNumber"], str), "houseNumber must be a string"


def test_header_is_the_style_header_union_plus_lpn_MINUS_vendorStyle(registry):
    """The two layouts no longer share a header exactly.

    The Pre-Ticket was built as "the Style Header's 53-field header + `lpn`",
    which is what made it nearly free. `vendorStyle` then MOVED to the detail
    section (the user's call), because the .OK carries `vendor_style` per Lane
    row — so the headers genuinely diverge now, and the relationship is stated
    rather than deleted.

    The Style Header KEEPS its header `vendorStyle`, and that is correct: the
    .OK Style Header carries `ven_style` in its own header.
    """
    pt = {f.name for s in registry.get("CalgaryPreticket").sections
          if s.name == "Header" for f in s.fields}
    sh = {f.name for s in registry.get("CalgaryStyleHeader").sections
          if s.name == "Header" for f in s.fields}
    assert pt - sh == {"lpn"}
    assert sh - pt == {"vendorStyle"}
    assert "vendorStyle" in sh, "the Style Header must keep its header field"


def test_detail_row_is_the_style_header_row_plus_size_AND_vendorStyle(registry):
    pt = {f.name for s in registry.get("CalgaryPreticket").sections
          if s.name == "Details" for f in s.fields}
    sh = {f.name for s in registry.get("CalgaryStyleHeader").sections
          if s.name == "Details" for f in s.fields}
    assert pt - sh == {"size", "vendorStyle"}
    assert sh - pt == set()


def test_the_detail_field_ORDER_is_the_users(registry):
    """`size` immediately after `type`, `vendorStyle` last — asserted on the
    SPEC, because the spec drives the editor's field order while the template
    drives the JSON key order, and the two must not drift apart."""
    names = [f.name for s in registry.get("CalgaryPreticket").sections
             if s.name == "Details" for f in s.fields]
    assert names[names.index("type") + 1] == "size"
    assert names[-1] == "vendorStyle"


def test_the_spec_and_the_template_agree_on_detail_order(registry):
    """The spec orders the editor, the template orders the file. A mismatch is
    invisible until someone compares a converted row with the panel."""
    spec = [f.name for s in registry.get("CalgaryPreticket").sections
            if s.name == "Details" for f in s.fields]
    tpl = list(_doc(SAMPLE)["details"][0])
    assert spec == tpl, "layout spec and template disagree on detail field order"


def test_it_adds_no_NEW_field_without_a_width(registry):
    """A Calgary field with no declared width is refused by Bulk Edit, omitted
    from the Volume Generate panel and SILENTLY SKIPPED by Generate through the
    API while the run still reports success (v0.97.0 / v0.108.0).

    The bar here is PARITY, not zero: the Style Header still has 10 such fields
    (the remaining structural counts and the `lanes`/`sizes` header duplicates,
    the open §6 thread), and a Pre-Ticket inherits exactly those. What must not
    happen is this layout adding an ELEVENTH — in particular its own two fields,
    `size` and `lpn`, which is what a copied spec could easily have left
    undeclared.

    *Was 11 until `lineCount` was declared 2; the count is pinned precisely so
    that closing one of these is FELT here rather than passing unnoticed.*
    """
    def sizeless(name):
        return {f"{s.name}.{f.name}" for s in registry.get(name).sections
                for f in s.fields if f.size is None}
    pt, sh = sizeless("CalgaryPreticket"), sizeless("CalgaryStyleHeader")
    assert pt - sh == set(), f"Pre-Ticket adds sizeless fields: {sorted(pt - sh)}"
    assert len(pt) == 10         # pins the count, so closing the §6 thread is felt here
    assert "Header.lineCount" not in pt


def test_the_two_new_fields_carry_the_ok_pretickets_own_widths(registry):
    """`size` and `lpn` are the only two fields this layout adds, so their
    widths are the only ones not settled by the Style Header. `size` is 6 —
    the .OK Pre-Ticket's own width for the same field (CSV position 18) — and
    `lpn` is 20, asserted against CalgaryDistLabel rather than hard-coded twice,
    so if one moves the other is revisited with it."""
    size = next(f for s in registry.get("CalgaryPreticket").sections
                if s.name == "Details" for f in s.fields if f.name == "size")
    assert size.size == 6
    ok_size = next(f for s in registry.get("Preticket").sections
                   if s.name == "Lane" for f in s.fields if f.name == "size")
    assert size.size == ok_size.size

    lpn = next(f for s in registry.get("CalgaryPreticket").sections
               if s.name == "Header" for f in s.fields if f.name == "lpn")
    dl_lpn = next(f for s in registry.get("CalgaryDistLabel").sections
                  if s.name == "Header" for f in s.fields if f.name == "lpn")
    assert lpn.size == dl_lpn.size == 20


# ----------------------------------------------------------------- the key ---

def test_the_key_is_the_po_number_whatever_the_source(config):
    """PO# either way (the user's call), written as a SCALAR in keys.yaml.

    The `source=None` case is the one that matters: `unique_field` with no
    source silently returns the WMS branch of a source-keyed map, which is how
    Bulk Edit greyed the wrong field (D62). A scalar cannot have that bug, and
    this asserts the scalar shape by its behaviour rather than by reading YAML.
    """
    for source in (None, "SCAN", "WMS"):
        assert config.unique_field("CalgaryPreticket", source) == "purchaseOrderNumber"
    # the source-keyed layouts still differ, so this is not testing a global
    assert config.unique_field("CalgaryStyleHeader", "SCAN") == "keytrol"
    assert config.unique_field("CalgaryStyleHeader", "WMS") == "headerASNid"


# ------------------------------------------------------- multiple details ---

def test_the_sample_carries_more_than_one_detail_line():
    """Everything below about growing and shrinking rests on this, and every
    other Calgary sample has exactly one detail row — so if the fixture were
    ever replaced with a single-row file, those tests would still pass while
    testing nothing."""
    assert len(_doc(SAMPLE)["details"]) == 2


def test_editing_one_detail_row_leaves_the_other_alone(tmp_path, registry, config):
    p = _copy(tmp_path)
    view = service.parse_file_view(p, registry, config)
    det = next(s for s in view["sections"] if s["name"] == "Details")
    before = _doc(p)["details"]
    assert before[0]["style"] != "999999"

    service.apply_edits(p, [{"section_index": det["index"],
                             "record_index": det["records"][0]["index"],
                             "field": "style", "value": "999999"}],
                        registry, config=config, backup=False)
    after = _doc(p)["details"]
    assert after[0]["style"] == "999999"
    assert after[1] == before[1], "the second detail row moved"


def test_the_new_size_field_is_writable_on_a_detail_row(tmp_path, registry, config):
    """`size` is the one field the Style Header has no counterpart for, so it is
    the one that could have been declared and still not reachable."""
    p = _copy(tmp_path)
    view = service.parse_file_view(p, registry, config)
    det = next(s for s in view["sections"] if s["name"] == "Details")
    service.apply_edits(p, [{"section_index": det["index"],
                             "record_index": det["records"][1]["index"],
                             "field": "size", "value": "XL"}],
                        registry, config=config, backup=False)
    rows = _doc(p)["details"]
    assert rows[1]["size"] == "XL"
    assert rows[0]["size"] == "SM", "the first row's size moved"


@pytest.mark.parametrize("keep", [0, 1, 2])
def test_keeping_n_rows_shrinks_to_exactly_n(tmp_path, registry, config, keep):
    """D47: row counts were only ever tested SHRINKING, so "Generate cannot grow
    a JSON section at all" sat unnoticed — the splice engine's failure mode is a
    silent no-op, which no amount of green suite surfaces. 2 -> 5 grows, 2 -> 1
    shrinks, 2 -> 0 is D43's boundary (the array keeps one blank marker row
    rather than becoming a bare []), and 2 -> 2 must be a no-op.
    """
    p = _copy(tmp_path, f"pt{keep}.json")
    service.bulk_op_apply([str(p)], "CalgaryPreticket", "Details",
                          {"type": "keep", "count": keep}, registry, config,
                          backup=False)
    rows = _doc(p)["details"]
    if keep == 0:
        # emptied, but the section keeps ONE blank row so the shape survives
        assert len(rows) == 1
        assert not any(str(v).strip() for v in rows[0].values() if v is not None)
    else:
        assert len(rows) == keep
        if keep == 2:
            # a no-op GUARD, and it passes on a build with no such layout too
            # (nothing happens either way) — so it is here to prove `keep` does
            # not disturb a section already at the right size, never as evidence
            # that the op works. The 0 and 1 cases carry that.
            assert rows == _doc(SAMPLE)["details"]


def test_adding_rows_seeds_them_from_the_vendor_sample(tmp_path, registry, config):
    """A new Pre-Ticket detail row is the vendor sample's OWN first line (the
    user's call), so an added row reads as real data rather than as blanks —
    which also keeps the section reading as HAVING data, so D75 does not then
    skip a field op aimed at it."""
    p = _copy(tmp_path)
    service.bulk_op_apply([str(p)], "CalgaryPreticket", "Details",
                          {"type": "add", "count": 2}, registry, config,
                          backup=False)
    rows = _doc(p)["details"]
    assert len(rows) == 4
    assert rows[-1]["size"] == "SM"
    assert rows[-1]["houseNumber"] == "000000"
    assert isinstance(rows[-1]["houseNumber"], str)


# -------------------------------------------------------------- the panels ---

def test_generate_offers_every_field_including_the_new_one(tmp_path, registry, config):
    """The Volume Generate panel OMITS a field with no width, so this is the
    panel-side half of the width test above."""
    p = _copy(tmp_path)
    scope = service.generate_scope([str(p)], registry, config)
    det = next(s for s in scope["sections"] if s["name"] == "Details")
    names = {f["name"] for f in det["fields"]}
    assert "size" in names
    assert {f["name"] for f in scope["header_fields"]} >= {"lpn"}
    assert scope["key_field"] == "purchaseOrderNumber"


def test_generate_grows_details_and_sets_the_new_field(tmp_path, registry, config):
    p = _copy(tmp_path)
    res = service.generate_apply(
        [str(p)],
        {"count": 2, "folder": str(tmp_path / "out"),
         "row_counts": [{"section": "Details", "min": 4, "max": 4}],
         "detail_fields": [{"section": "Details", "name": "size",
                            "type": "list", "values": ["LG"]}]},
        registry, config)
    made = sorted(Path(res["folder"]).glob("*.json"))
    assert len(made) == 2
    keys = set()
    for m in made:
        d = _doc(m)
        assert len(d["details"]) == 4, "Generate did not grow the section"
        assert {r["size"] for r in d["details"]} == {"LG"}
        keys.add(d["header"]["purchaseOrderNumber"])
    assert len(keys) == 2, "generated files share a PO number"


def test_bulk_field_values_reach_a_detail_field(tmp_path, registry, config):
    p = _copy(tmp_path)
    res = service.bulk_multi_apply(
        [str(p)], "CalgaryPreticket",
        [{"section": "Details", "field": "size", "type": "set", "value": "MD"}],
        registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    assert {r["size"] for r in _doc(p)["details"]} == {"MD"}


def test_an_over_long_value_is_refused_BY_THE_WIDTH(tmp_path, registry, config):
    """`size` is 6, so a 7-character value must be REFUSED with the file left
    untouched — not truncated, and not written through.

    The assertion is on the MESSAGE, not just on "nothing was written". On a
    build with no CalgaryPreticket layout at all nothing is written either, so
    a bytes-unchanged check passes there too and proves nothing (it did — this
    test passed against the previous tag as first written). Naming the field and
    its width is what distinguishes *refused because it is too long* from
    *refused because the layout does not exist*. The 6-character control then
    proves the path is live rather than uniformly broken.
    """
    p = _copy(tmp_path)
    before = p.read_bytes()
    res = service.bulk_multi_apply(
        [str(p)], "CalgaryPreticket",
        [{"section": "Details", "field": "size", "type": "set", "value": "TOOLONG"}],
        registry, config, backup=False)
    row = res["results"][0]
    assert row["status"] != "changed"
    reason = row.get("error") or ""
    assert "too long" in reason and "size" in reason, \
        f"refused, but not for being too long: {reason!r}"
    assert p.read_bytes() == before

    # …and exactly 6 characters DOES write, on the same field of the same file
    res = service.bulk_multi_apply(
        [str(p)], "CalgaryPreticket",
        [{"section": "Details", "field": "size", "type": "set", "value": "XXLARG"}],
        registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    assert {r["size"] for r in _doc(p)["details"]} == {"XXLARG"}


# ------------------------------------------------------------------ TOSCA ---

def test_tosca_resolves_a_preticket_row(tmp_path, registry, config):
    """A JSON Pre-Ticket must resolve to the SAME process name as the .OK one:
    rows are deduped by (chain, process, format), so a mixed selection writes
    one row rather than two, and input staging addresses the folder by exactly
    that triple."""
    names = config.tosca().get("process_names") or {}
    assert names.get("CalgaryPreticket") == "Pre-Ticket"
    assert names.get("CalgaryPreticket") == names.get("Preticket")
    # and the format column that serves "Pre-Ticket" already exists for the NA
    # chains, which is why this one line is the whole TOSCA change
    cols = config.tosca().get("format_columns") or {}
    assert cols["Marshalls"]["Pre-Ticket"]


# --------------------------------------------------------- chain isolation ---

def test_europe_is_refused_like_every_other_na_layout(config):
    """No new machinery: the existing isolated-chain rule is what keeps Europe
    off a Pre-Ticket, exactly as it does for the other NA layouts (the user's
    call, reversing an earlier plan for a per-layout restriction). Asserted by
    NAME as well as by code — `05` was correctly refused while `Europe` was
    accepted, until the Calgary chain list was written."""
    assert config.can_change_chain("02", "01") is True
    assert config.can_change_chain("02", "05") is False
    assert config.can_change_chain("05", "02") is False

# ------------------------------- the moved fields reach EVERY write path ---

def _det(path):
    """(size, vendorStyle) per detail row — the two fields that moved."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["data"]["details"]
    return [(r.get("size"), r.get("vendorStyle")) for r in rows]


def test_a_single_edit_lands_on_the_edited_ROW_only(tmp_path, registry, config):
    """`vendorStyle` moved from the header to the detail row, so the failure to
    guard against is the value going to the wrong row — or nowhere — while the
    control still renders."""
    p = _copy(tmp_path)
    view = service.parse_file_view(p, registry, config)
    di = next(i for i, s in enumerate(view["sections"]) if s["name"] == "Details")
    rec = view["sections"][di]["records"][1]["index"]
    for field, value in (("vendorStyle", "ZZ99"), ("size", "XL")):
        service.apply_edits(p, [{"section_index": di, "record_index": rec,
                                 "field": field, "value": value}],
                            registry, config=config, backup=False)
    assert _det(p) == [("SM", "BDGB"), ("XL", "ZZ99")]


def test_both_bulk_panels_write_the_moved_fields(tmp_path, registry, config):
    p = _copy(tmp_path)
    service.bulk_multi_apply([str(p)], "CalgaryPreticket",
                             [{"section": "Details", "field": "vendorStyle",
                               "type": "set", "value": "BULKVS"},
                              {"section": "Details", "field": "size",
                               "type": "set", "value": "LG"}],
                             registry, config, backup=False)
    assert _det(p) == [("LG", "BULKVS"), ("LG", "BULKVS")]

    q = _copy(tmp_path, "op.json")
    service.bulk_op_apply([str(q)], "CalgaryPreticket", "Details",
                          {"type": "set", "field": "vendorStyle", "value": "OPVS"},
                          registry, config, backup=False)
    assert [r[1] for r in _det(q)] == ["OPVS", "OPVS"]


def test_a_SEEDED_row_carries_both_moved_fields(tmp_path, registry, config):
    """The seed is an EXPLICIT field list in `json_seed_rows.yaml`, so a moved
    field does NOT arrive there on its own — this is the one path the
    layout-driven engine does not cover for free, and it shipped broken until
    the seed row was updated.

    The section is emptied first: adding to a section that still has rows CLONES
    (D53), which would carry the fields whatever the seed said, and prove
    nothing.
    """
    p = _copy(tmp_path)
    service.bulk_op_apply([str(p)], "CalgaryPreticket", "Details",
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)
    service.bulk_op_apply([str(p)], "CalgaryPreticket", "Details",
                          {"type": "add", "count": 2}, registry, config,
                          backup=False)
    assert _det(p) == [("SM", "BDGB"), ("SM", "BDGB")], "seed lost a moved field"
    keys = list(json.loads(p.read_text(encoding="utf-8"))["data"]["details"][0])
    assert keys[keys.index("type") + 1] == "size"
    assert keys[-1] == "vendorStyle"


def test_an_added_row_CLONES_and_keeps_both_fields(tmp_path, registry, config):
    """The other add path: with rows present the new row copies an existing one
    (D53), so distinctive values must survive rather than reverting to the seed."""
    p = _copy(tmp_path)
    service.bulk_multi_apply([str(p)], "CalgaryPreticket",
                             [{"section": "Details", "field": "vendorStyle",
                               "type": "set", "value": "CLONED"}],
                             registry, config, backup=False)
    service.bulk_op_apply([str(p)], "CalgaryPreticket", "Details",
                          {"type": "add", "count": 1}, registry, config,
                          backup=False)
    assert [r[1] for r in _det(p)] == ["CLONED"] * 3


def test_volume_generate_writes_the_moved_fields(tmp_path, registry, config):
    """Generate SILENTLY SKIPS a field with no declared width while still
    reporting success (v0.97.0/v0.108.0), so this asserts the values landed —
    not merely that the run said `written: 2`."""
    p = _copy(tmp_path)
    res = service.generate_apply(
        [str(p)],
        {"count": 2, "folder": str(tmp_path / "gen"),
         "detail_fields": [
             {"section": "Details", "name": "vendorStyle",
              "type": "list", "values": ["GENVS"]},
             {"section": "Details", "name": "size",
              "type": "list", "values": ["GN"]}]},
        registry, config)
    assert res["written"] == 2
    assert not res.get("no_data"), res.get("no_data")
    for f in sorted(Path(res["folder"]).glob("*.json")):
        assert _det(f) == [("GN", "GENVS"), ("GN", "GENVS")], f.name


def test_an_emptied_section_keeps_both_fields_present(tmp_path, registry, config):
    """D45: an emptied array keeps ONE blank marker row with every field
    present — including the field that just moved into the section."""
    p = _copy(tmp_path)
    service.bulk_op_apply([str(p)], "CalgaryPreticket", "Details",
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)
    row = json.loads(p.read_text(encoding="utf-8"))["data"]["details"][0]
    assert {"size", "vendorStyle"} <= set(row)
    assert _det(p) == [(None, None)]


def test_the_scopes_OFFER_both_fields_on_the_detail_section(registry, config):
    """A field omitted from a panel reads as "OkGen forgot it" rather than "you
    may not set it" (D61) — and the header must stop offering `vendorStyle`."""
    scope = service.bulk_scope([str(SAMPLE)], registry, config)
    det = next(s for s in scope["detail_sections"]["CalgaryPreticket"]
               if s["name"] == "Details")
    names = [f["name"] for f in det["fields"]]
    assert {"size", "vendorStyle"} <= set(names)
    hdr = [f["name"] for f in scope["header_fields"]["CalgaryPreticket"]]
    assert "vendorStyle" not in hdr, "the header still offers the moved field"

    gen = service.generate_scope([str(SAMPLE)], registry, config)
    gdet = next(s for s in gen["sections"] if s["name"] == "Details")
    assert {"size", "vendorStyle"} <= {f["name"] for f in gdet["fields"]}
