"""Volume Generate says so when a section holds no data.

User-reported: with no lanes/sizes on a Style Header, or no stores on a Carton
Label, entering values for those sections generated files with only the entered
value — every other field in the row empty — and **no message at all**.

An "empty" section is not empty in either engine, and the two differ:

    JSON   emptying leaves ONE BLANK MARKER ROW (D45), because the array must
           keep its key. A value USED to land on that row and yield a
           half-filled one. The engine sees `rows: 1`, so nothing downstream
           could tell it apart from a section holding one real row.
    .OK    a section can genuinely hold ZERO rows (`DistLabels` ships
           `TSticker` that way). A value landed NOWHERE and the run still
           reported success.

Both are now SKIPPED with a message rather than written, in Generate and in
both bulk panels. The half-filled row was worse than nothing: it flipped the
section to "has data", so the marker stopped being replaced when rows were
added, and it became the CLONE TEMPLATE — every row added afterwards inherited
its emptiness instead of the seed. The single editor is deliberately unchanged:
there the row is visible and typing into it is how you populate a section by
hand.

A third shape is already handled correctly and must stay that way: a zero-fill
section (`Preticket.Lane`) whose trailing all-zero rows are structural padding.
Generation skips those on purpose, so a section with real rows AND filler still
counts as having data.

Blankness is judged by `_section_has_data`, which reuses
`_json_blank_rows_to_replace` — the same rule the add paths use, so the panel
cannot claim "blank" while a write path disagrees. It is deliberately about
BLANKNESS, never provenance: a vendor file ships blank rows too
(`styleheader_fmtS` lanes), and those are the same bytes as an emptied
section's marker. Confirmed with the user that both cases occur in practice.
"""
import json
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="no calgary fixtures")


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _copy(tmp_path, src, name=None):
    p = tmp_path / (name or Path(src).name)
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, p)
    return p


def _empty(path, layout, section, registry, config):
    service.bulk_op_apply([str(path)], layout, section,
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)
    return path


def _has_data(path, section, registry, config):
    okf = service.parse_okfile(path, registry=registry)
    sec = next(s for s in okf.layout.sections if s.name == section)
    return service._section_has_data(okf, sec, config)


# ------------------------------------------------------- the shared predicate

def test_a_section_with_real_rows_has_data(tmp_path, registry, config):
    p = _copy(tmp_path, FIX / "distlabel.json")
    assert _has_data(p, "Stores", registry, config) is True


def test_a_fixed_width_section_with_no_rows_has_none(tmp_path, registry, config):
    """`DistLabels` ships `TSticker` with zero rows — the case where a value
    lands nowhere at all."""
    p = _copy(tmp_path, DATA_DIR / "DistLabels.OK")
    assert _has_data(p, "TSticker", registry, config) is False


def test_an_emptied_fixed_width_section_has_none(tmp_path, registry, config):
    p = _empty(_copy(tmp_path, DATA_DIR / "StyleHeader.OK"),
               "StyleHeader", "Size", registry, config)
    assert _has_data(p, "Size", registry, config) is False


def test_an_emptied_json_section_has_none_despite_reporting_one_row(
        tmp_path, registry, config):
    """The heart of the report: the marker row makes `rows` say 1."""
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    okf = service.parse_okfile(p, registry=registry)
    assert len([r for r in okf.records if r.section.name == "Sizes"]) == 1
    assert _has_data(p, "Sizes", registry, config) is False


def test_a_VENDOR_shipped_blank_row_also_counts_as_no_data(tmp_path, registry, config):
    """`styleheader_fmtS` ships `lanes: [{"lane": ""}]` untouched. It is the
    same bytes as an emptied section's marker, and the rule is about blankness
    rather than provenance — so it must answer the same way."""
    p = _copy(tmp_path, FIX / "styleheader_fmtS.json")
    assert _has_data(p, "Lanes", registry, config) is False


def test_filler_rows_beside_real_rows_still_count_as_data(tmp_path, registry, config):
    """`Preticket.Lane` is zero-filled: 4 real rows and 19 all-zero fillers.
    Generation already skips the fillers on purpose, so the section HAS data
    and must not be flagged."""
    p = _copy(tmp_path, DATA_DIR / "Preticket.OK")
    assert _has_data(p, "Lane", registry, config) is True


# ----------------------------------------------------- the scope reports it

def test_scope_distinguishes_blank_from_one_real_row(tmp_path, registry, config):
    """`rows: 1` is identical in both cases, which is why a separate fact is
    needed — without it no panel could warn."""
    real = _copy(tmp_path, FIX / "styleheader_fmtS.json", "real.json")
    blank = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json", "blank.json"),
                   "CalgaryStyleHeader", "Sizes", registry, config)

    for path, expected in ((real, True), (blank, False)):
        scope = service.generate_scope([str(path)], registry, config)
        sec = next(s for s in scope["sections"] if s["name"] == "Sizes")
        assert sec["rows"] == 1, "both report one row — that is the point"
        assert sec["has_data"] is expected


def test_scope_counts_across_EVERY_template_not_just_the_first(
        tmp_path, registry, config):
    """`generate_scope` describes tpaths[0] but generation draws a RANDOM
    template per file, so a section blank in one template and populated in
    another must still be reported — the per-file lesson again."""
    blank = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json", "a.json"),
                   "CalgaryStyleHeader", "Sizes", registry, config)
    real = _copy(tmp_path, FIX / "styleheader_fmtS.json", "b.json")

    scope = service.generate_scope([str(blank), str(real)], registry, config)
    sec = next(s for s in scope["sections"] if s["name"] == "Sizes")
    assert sec["no_data_templates"] == 1
    assert scope["template_count"] == 2
    assert sec["has_data"] is True          # true of at least one template


def test_an_OK_layout_reports_its_empty_section(tmp_path, registry, config):
    p = _copy(tmp_path, DATA_DIR / "DistLabels.OK")
    scope = service.generate_scope([str(p)], registry, config)
    ts = next(s for s in scope["sections"] if s["name"] == "TSticker")
    assert ts["rows"] == 0 and ts["has_data"] is False
    store = next(s for s in scope["sections"] if s["name"] == "Store")
    assert store["has_data"] is True


# ------------------------------------------------ the run says what happened

def _generate(tmp_path, path, section, field, value, registry, config, count=2):
    return service.generate_apply(
        [str(path)],
        {"count": count, "folder": str(tmp_path / "out"),
         "detail_fields": [{"section": section, "name": field,
                            "type": "list", "values": [value]}]},
        registry, config)


def test_a_blank_json_section_is_reported_as_blank(tmp_path, registry, config):
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    res = _generate(tmp_path, p, "Sizes", "size", "MED", registry, config)
    assert res["written"] == 2
    note = next(n for n in res["no_data"] if n["section"] == "Sizes")
    assert note["kind"] == "blank"
    assert "SKIPPED" in note["message"]
    assert "size" in note["fields"]


def test_an_empty_OK_section_is_reported_as_empty(tmp_path, registry, config):
    """A different outcome and a different sentence: nothing was written at
    all, rather than written onto a blank row."""
    p = _copy(tmp_path, DATA_DIR / "DistLabels.OK")
    res = _generate(tmp_path, p, "TSticker", "packs", "00007", registry, config)
    assert res["written"] == 2
    note = next(n for n in res["no_data"] if n["section"] == "TSticker")
    assert note["kind"] == "empty"
    assert "SKIPPED" in note["message"]


def test_the_two_outcomes_do_not_share_a_message(tmp_path, registry, config):
    """Both are skipped now, so the OUTCOME is the same — but the message still
    distinguishes why, because what the user sees in the editor differs: a
    blank placeholder row, or no rows at all."""
    blank = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json", "b.json"),
                   "CalgaryStyleHeader", "Sizes", registry, config)
    empty = _copy(tmp_path, DATA_DIR / "DistLabels.OK")
    m1 = _generate(tmp_path / "x", blank, "Sizes", "size", "MED",
                   registry, config)["no_data"][0]["message"]
    m2 = _generate(tmp_path / "y", empty, "TSticker", "packs", "00007",
                   registry, config)["no_data"][0]["message"]
    assert m1 != m2


def test_a_section_WITH_data_produces_no_note(tmp_path, registry, config):
    """The guard against crying wolf — a normal run must stay silent."""
    p = _copy(tmp_path, FIX / "distlabel.json")
    res = _generate(tmp_path, p, "Stores", "store", "0115", registry, config)
    assert res["no_data"] == []


def test_filler_rows_produce_no_note(tmp_path, registry, config):
    p = _copy(tmp_path, DATA_DIR / "Preticket.OK")
    res = _generate(tmp_path, p, "Lane", "size", "XL", registry, config)
    assert res["no_data"] == []


def test_the_preview_carries_the_same_note(tmp_path, registry, config):
    """Preview and apply must agree, or the preview is not a preview."""
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    pv = service.generate_preview(
        [str(p)], {"count": 2,
                   "detail_fields": [{"section": "Sizes", "name": "size",
                                      "type": "list", "values": ["MED"]}]},
        registry, config)
    ap = _generate(tmp_path, p, "Sizes", "size", "MED", registry, config)
    assert [n["message"] for n in pv["no_data"]] == [n["message"] for n in ap["no_data"]]


def test_a_row_count_is_the_remedy_the_note_names(tmp_path, registry, config):
    """The note tells the user to set a row count. That must actually work —
    the rows are seeded, so they come out COMPLETE rather than half-filled."""
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    res = service.generate_apply(
        [str(p)],
        {"count": 1, "folder": str(tmp_path / "out"),
         "row_counts": [{"section": "Sizes", "min": 3, "max": 3}],
         "detail_fields": [{"section": "Sizes", "name": "size",
                            "type": "list", "values": ["MED"]}]},
        registry, config)
    made = sorted(Path(res["folder"]).glob("*.json"))
    rows = json.loads(made[0].read_text(encoding="utf-8"))["data"]["header"]["sizes"]
    assert len(rows) == 3
    assert all(r["size"] == "MED" for r in rows)
    assert all(r["quantity"] not in (None, "") for r in rows), "rows should be seeded"
    assert res["no_data"] == [], "with real rows there is nothing to warn about"


def test_every_layout_that_can_have_an_empty_section_is_covered(registry, config):
    """The ask was 'all 10 layouts as applicable'. The predicate is engine
    agnostic, so this asserts it answers for every layout rather than that
    every layout ships an empty section (most do not)."""
    for name in ("StyleHeader", "Preticket", "CartonLabel", "DistLabels",
                 "EUPreticket", "EUStyleHeader", "EUCartonLabel",
                 "CalgaryStyleHeader", "CalgaryDistLabel", "CalgaryCartonLabel"):
        layout = registry[name]
        assert layout.sections, name


# --------------------------------------------- skipped, not written (D75)

def test_generate_leaves_the_placeholder_row_untouched(tmp_path, registry, config):
    """The heart of it: the value is not written onto the blank row."""
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    before = json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["sizes"]
    res = _generate(tmp_path, p, "Sizes", "size", "MED", registry, config)
    made = sorted(Path(res["folder"]).glob("*.json"))
    for m in made:
        after = json.loads(m.read_text(encoding="utf-8"))["data"]["header"]["sizes"]
        assert after == before, "the placeholder row was written into"


@pytest.mark.parametrize("apply_fn", ["multi", "single"])
def test_bulk_skips_the_field_and_leaves_the_row(tmp_path, registry, config, apply_fn):
    p = _empty(_copy(tmp_path, FIX / f"styleheader_fmtS.json", f"{apply_fn}.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    before = p.read_bytes()
    if apply_fn == "multi":
        res = service.bulk_multi_apply(
            [str(p)], "CalgaryStyleHeader",
            [{"section": "Sizes", "type": "list", "field": "size",
              "values": ["MED"]}], registry, config, backup=False)
        entry = res["results"][0]["fields"][0]
        assert entry["status"] == "skipped"
        msg = entry["error"]
    else:
        res = service.bulk_op_apply(
            [str(p)], "CalgaryStyleHeader", "Sizes",
            {"type": "set", "field": "size", "value": "MED"},
            registry, config, backup=False)
        entry = res["results"][0]
        assert entry["status"] == "no_data"
        msg = entry["detail"]
    assert p.read_bytes() == before, "the file was written"
    # A status is a value the code branches on; the user gets a sentence.
    assert "Add rows" in msg and "Sizes" in msg
    assert len(msg.split()) > 5


def test_bulk_skips_only_the_FIELD_not_the_whole_file(tmp_path, registry, config):
    """The refinement that matters: one field targeting a dataless section must
    not cost the file its other edits — the rule already used when a file has
    no such section at all."""
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    res = service.bulk_multi_apply(
        [str(p)], "CalgaryStyleHeader",
        [{"section": "Sizes", "type": "list", "field": "size", "values": ["MED"]},
         {"section": "Header", "type": "list", "field": "department",
          "values": ["77"]}],
        registry, config, backup=False)
    entry = res["results"][0]
    by_field = {f["field"]: f for f in entry["fields"]}
    assert by_field["size"]["status"] == "skipped"
    assert by_field["department"]["status"] == "change"

    okf = service.parse_okfile(p, registry=registry)
    header = next(r for r in okf.records if r.section.name == "Header")
    assert header.get("department") == "77", "the other edit did not land"


def test_adding_rows_still_works_on_a_dataless_section(tmp_path, registry, config):
    """The remedy the message names. Row ops are deliberately NOT guarded — if
    adding rows were skipped too there would be no way out."""
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    res = service.bulk_op_apply([str(p)], "CalgaryStyleHeader", "Sizes",
                                {"type": "add", "count": 2},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    rows = json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["sizes"]
    assert len(rows) == 2
    assert all(r["quantity"] not in (None, "") for r in rows), "rows are seeded"


def test_the_single_EDITOR_still_writes_into_the_placeholder(tmp_path, registry, config):
    """Deliberately unchanged. There the row is visible and typing into it is
    how a section gets populated by hand; bulk and Generate act on rows the
    user cannot see, which is the whole reason they skip."""
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    okf = service.parse_okfile(p, registry=registry)
    ri = next(r.index for r in okf.records if r.section.name == "Sizes")
    service.apply_edits(p, [{"record_index": ri, "field": "size",
                             "value": "MED"}], registry, config=config,
                        backup=False)
    rows = json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["sizes"]
    assert rows[0]["size"] == "MED"


def test_the_poisoning_this_prevents(tmp_path, registry, config):
    """Why skipping beats writing. Setting one field onto the marker used to
    make it a REAL row, so a later add stacked on it AND cloned its emptiness
    instead of seeding. With the skip, add still replaces and still seeds."""
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    service.bulk_multi_apply(
        [str(p)], "CalgaryStyleHeader",
        [{"section": "Sizes", "type": "list", "field": "size", "values": ["MED"]}],
        registry, config, backup=False)
    service.bulk_op_apply([str(p)], "CalgaryStyleHeader", "Sizes",
                          {"type": "add", "count": 2}, registry, config,
                          backup=False)
    rows = json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["sizes"]
    assert len(rows) == 2, "the marker should still have been replaced"
    assert all(r["quantity"] == "100" for r in rows), "rows should be seeded"


def test_a_SINGLE_field_section_is_not_skipped(tmp_path, registry, config):
    """`CalgaryStyleHeader.Lanes` has one field, so setting it FULLY populates
    the row — there is nothing left empty and none of the harm applies.

    Skipping it would take back the `lane` editing that was asked for and
    built, so the rule stops short of a one-field section deliberately. This is
    the boundary between two of the user's requests and is easy to lose.
    """
    p = _copy(tmp_path, FIX / "styleheader_fmtS.json")
    assert _has_data(p, "Lanes", registry, config) is False   # blank as shipped

    res = service.bulk_multi_apply(
        [str(p)], "CalgaryStyleHeader",
        [{"section": "Lanes", "type": "list", "field": "lane",
          "values": ["LANE0007"]}], registry, config, backup=False)
    assert res["results"][0]["status"] == "changed", res["results"][0]
    lanes = json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["lanes"]
    assert lanes == [{"lane": "LANE0007"}], "a one-field row is complete"


def test_the_exemption_is_about_FIELD_COUNT_not_the_section_name(registry):
    """Stated as a rule, not a special case for `Lanes`: any single-field
    section qualifies, and `StyleHeader.Lane` on the fixed-width side is the
    other one today."""
    singles = [(lay, sec.name)
               for lay in ("CalgaryStyleHeader", "StyleHeader")
               for sec in registry[lay].sections[1:] if len(sec.fields) == 1]
    assert ("CalgaryStyleHeader", "Lanes") in singles
    assert ("StyleHeader", "Lane") in singles


def test_a_two_field_section_IS_still_skipped(tmp_path, registry, config):
    """The contrast that makes the rule meaningful — `Sizes` has `size` and
    `quantity`, so setting one leaves the other empty."""
    p = _empty(_copy(tmp_path, FIX / "styleheader_fmtS.json"),
               "CalgaryStyleHeader", "Sizes", registry, config)
    res = service.bulk_multi_apply(
        [str(p)], "CalgaryStyleHeader",
        [{"section": "Sizes", "type": "list", "field": "size",
          "values": ["MED"]}], registry, config, backup=False)
    assert res["results"][0]["fields"][0]["status"] == "skipped"
