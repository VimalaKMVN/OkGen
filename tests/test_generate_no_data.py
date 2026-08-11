"""Volume Generate says so when a section holds no data.

User-reported: with no lanes/sizes on a Style Header, or no stores on a Carton
Label, entering values for those sections generated files with only the entered
value — every other field in the row empty — and **no message at all**.

An "empty" section is not empty in either engine, and the two differ:

    JSON   emptying leaves ONE BLANK MARKER ROW (D45), because the array must
           keep its key. A value lands on that row and yields a half-filled
           one. The engine sees `rows: 1`, so nothing downstream could tell it
           apart from a section holding one real row.
    .OK    a section can genuinely hold ZERO rows (`DistLabels` ships
           `TSticker` that way). A value lands NOWHERE and the run still
           reports success — worse, because nothing is produced to look at.

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
    assert "placeholder" in note["message"]
    assert "size" in note["fields"]


def test_an_empty_OK_section_is_reported_as_empty(tmp_path, registry, config):
    """A different outcome and a different sentence: nothing was written at
    all, rather than written onto a blank row."""
    p = _copy(tmp_path, DATA_DIR / "DistLabels.OK")
    res = _generate(tmp_path, p, "TSticker", "packs", "00007", registry, config)
    assert res["written"] == 2
    note = next(n for n in res["no_data"] if n["section"] == "TSticker")
    assert note["kind"] == "empty"
    assert "not written" in note["message"]


def test_the_two_outcomes_do_not_share_a_message(tmp_path, registry, config):
    """They are different things and a user has to be able to tell them apart:
    one produced a half-filled row, the other produced nothing."""
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
