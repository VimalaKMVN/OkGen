"""A row added to a JSON section that ALREADY HAS ROWS is a faithful clone.

The `.OK` engine gets this for free: a cloned record copies the template's raw
bytes, so the new line is indistinguishable from the one above it. The JSON
clone went through the editor's display layer instead —

    clone.pending = dict(template.values())      # values() renders via _display

— and `_display` maps JSON `null` to `""`. Every field of the clone was then
spliced back over the copied source text, so a template's `null` reached the
file as `""`: six fields on a DistLabel store, two on a StyleHeader detail row.
That collapses the absent/empty distinction D34 and D39 went to real trouble to
keep (`null_when_blank` exists for exactly it), on EVERY add path — single add,
bulk "add N rows", and Volume Generate's row counts, all of which funnel through
`service._clone_record`.

A clone now INHERITS its template's raw values and splices only what was
actually `set` on it, so an unedited clone is a byte-identical copy of its
template and a non-string scalar keeps its type. Seeding an EMPTY section (D49)
is a different path and must not move.
"""
import json
import shutil
from pathlib import Path

import pytest

from okgen import jsonengine
from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import parse_okfile

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="no calgary fixtures")

# (fixture, layout, section, the JSON array it maps to) — every Calgary section
# that ships with rows to clone from.
POPULATED = [
    ("distlabel.json", "CalgaryDistLabel", "Stores", "stores"),
    ("cartonlabel_minified.json", "CalgaryCartonLabel", "Stores", "stores"),
    ("styleheader_fmtB.json", "CalgaryStyleHeader", "Details", "details"),
    ("styleheader_fmtS.json", "CalgaryStyleHeader", "Details", "details"),
    ("styleheader_fmtT.json", "CalgaryStyleHeader", "Details", "details"),
]


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _copy(tmp_path, fixture):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    return p


def _rows(path, key):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return doc["data"]["details"] if key == "details" else doc["data"]["header"][key]


def _si(path, name, registry, config):
    view = service.parse_file_view(path, registry, config)
    return next(i for i, s in enumerate(view["sections"]) if s["name"] == name)


def _add(path, section, registry, config):
    service.add_record(path, _si(path, section, registry, config), [],
                       registry, config, preview=False, backup=False)


# --------------------------------------------------------------------------- #
# The reported gap: a clone must match the rows already there
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fixture,layout,section,key", POPULATED)
def test_a_cloned_row_equals_its_template_exactly(tmp_path, registry, config,
                                                  fixture, layout, section, key):
    p = _copy(tmp_path, fixture)
    before = _rows(p, key)
    assert before, "fixture must already have rows for this to be a clone"

    _add(p, section, registry, config)

    after = _rows(p, key)
    assert len(after) == len(before) + 1
    assert after[-1] == before[-1], "the added row differs from the row it was cloned from"


@pytest.mark.parametrize("fixture,layout,section,key", POPULATED)
def test_the_existing_rows_are_untouched(tmp_path, registry, config,
                                         fixture, layout, section, key):
    p = _copy(tmp_path, fixture)
    before = _rows(p, key)

    _add(p, section, registry, config)

    assert _rows(p, key)[:len(before)] == before


def test_a_null_field_is_cloned_as_a_real_null(tmp_path, registry, config):
    """The concrete report. These six DistLabel store fields are `null` in the
    vendor sample and used to arrive as `""` on every added row."""
    p = _copy(tmp_path, "distlabel.json")
    nulled = [k for k, v in _rows(p, "stores")[-1].items() if v is None]
    assert set(nulled) >= {"cartonSequence", "numberOfPacks", "puertoRicoFlag",
                           "puertoRicoStore", "puertoRicoStoreSeq", "suppress"}

    _add(p, "Stores", registry, config)

    added = _rows(p, "stores")[-1]
    assert [added[k] for k in nulled] == [None] * len(nulled)
    assert "" not in [added[k] for k in nulled]


def test_a_blank_string_stays_a_blank_string(tmp_path, registry, config):
    """The other direction, which is what makes the distinction meaningful: a
    field the sample carries as "" must not become null."""
    p = _copy(tmp_path, "distlabel.json")
    empties = [k for k, v in _rows(p, "stores")[-1].items() if v == ""]
    assert empties, "fixture must carry at least one empty-string field"

    _add(p, "Stores", registry, config)

    added = _rows(p, "stores")[-1]
    assert [added[k] for k in empties] == [""] * len(empties)


def test_a_non_string_scalar_keeps_its_type(tmp_path, registry, config):
    """No Calgary sample carries a number today, but `_display` rendered every
    non-string scalar as its JSON token and the splice wrote that token back as
    a quoted STRING. Splicing only real edits closes the class, not just the
    null case."""
    doc = json.loads((FIX / "distlabel.json").read_text(encoding="utf-8"))
    doc["data"]["header"]["stores"][-1]["units"] = 20            # a real number
    p = tmp_path / "f.json"
    p.write_text(json.dumps(doc, indent=4), encoding="utf-8")

    _add(p, "Stores", registry, config)

    assert _rows(p, "stores")[-1]["units"] == 20


def test_the_cloned_row_is_byte_identical_source(tmp_path, registry, config):
    """Formatting too, not just values — the clone copies the template's own
    source text, so the two rows render the same way."""
    p = _copy(tmp_path, "distlabel.json")
    _add(p, "Stores", registry, config)

    text = p.read_text(encoding="utf-8")
    spans = jsonengine.scan_spans(text)
    arr = ("data", "header", "stores")
    n = len(_rows(p, "stores"))
    template = text[slice(*spans[arr + (n - 2,)])]
    clone = text[slice(*spans[arr + (n - 1,)])]
    assert clone == template


# --------------------------------------------------------------------------- #
# All three add paths (single, bulk, Volume Generate)
# --------------------------------------------------------------------------- #
def test_bulk_add_clones_faithfully(tmp_path, registry, config):
    p = _copy(tmp_path, "distlabel.json")
    template = _rows(p, "stores")[-1]
    n = len(_rows(p, "stores"))

    service.bulk_op_apply([str(p)], "CalgaryDistLabel", "Stores",
                          {"type": "add", "count": 3}, registry, config, backup=False)

    rows = _rows(p, "stores")
    assert len(rows) == n + 3
    assert rows[-3:] == [template] * 3


def test_volume_generate_clones_faithfully(tmp_path, registry, config):
    p = _copy(tmp_path, "distlabel.json")
    template = _rows(p, "stores")[-1]
    target = len(_rows(p, "stores")) + 4

    res = service.generate_apply(
        [str(p)], {"count": 1,
                   "row_counts": [{"section": "Stores", "min": target, "max": target}]},
        registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]

    rows = _rows(out, "stores")
    assert len(rows) == target
    assert rows[-4:] == [template] * 4


def test_generate_still_varies_the_fields_it_was_asked_to(tmp_path, registry, config):
    """Inheriting rather than restating must not stop a real edit landing: a
    field Generate randomizes is `set` on the row, so it is still spliced."""
    p = _copy(tmp_path, "distlabel.json")
    target = len(_rows(p, "stores")) + 2

    res = service.generate_apply(
        [str(p)],
        {"count": 1,
         "row_counts": [{"section": "Stores", "min": target, "max": target}],
         "detail_fields": [{"section": "Stores", "name": "store", "values": "0777"}]},
        registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]

    rows = _rows(out, "stores")
    assert len(rows) == target
    assert {r["store"] for r in rows} == {"0777"}, "the generated value must reach every row"
    # ...and the fields it was NOT asked about keep their nulls.
    assert rows[-1]["cartonSequence"] is None


# --------------------------------------------------------------------------- #
# Editing a clone, and cloning a clone
# --------------------------------------------------------------------------- #
def test_editing_a_clone_changes_only_that_field(tmp_path, registry, config):
    p = _copy(tmp_path, "distlabel.json")
    template = _rows(p, "stores")[-1]
    idx = _si(p, "Stores", registry, config)

    # The editor's own flow: stage the add, then type into the new row, then
    # Save — so the edit and the inherited values meet on the same record.
    _add(p, "Stores", registry, config)
    view = service.parse_file_view(p, registry, config)
    rec_index = view["sections"][idx]["records"][-1]["index"]
    service.apply_edits(p, [{"section_index": idx, "record_index": rec_index,
                             "field": "store", "value": "0555"}],
                        registry, config=config, backup=False)

    row = _rows(p, "stores")[-1]
    assert row["store"] == "0555"
    assert {k: v for k, v in row.items() if k != "store"} == \
           {k: v for k, v in template.items() if k != "store"}


def test_cloning_a_clone_keeps_the_nulls(tmp_path, registry, config):
    """Two adds in one session: the second clones a row that is itself new and
    has no source text to copy, so it renders from its values. That path used to
    flatten every value with `v or ""`, re-introducing the same collapse one
    level down."""
    p = _copy(tmp_path, "distlabel.json")
    template = _rows(p, "stores")[-1]

    for _ in range(2):
        _add(p, "Stores", registry, config)

    rows = _rows(p, "stores")
    assert rows[-2:] == [template, template]


# --------------------------------------------------------------------------- #
# What must NOT move
# --------------------------------------------------------------------------- #
def test_seeding_an_empty_section_is_unchanged(tmp_path, registry, config):
    """D49's path: an EMPTY section has nothing to clone, so it still seeds from
    `json_seed_rows.yaml` — including its declared nulls, which is the same
    guarantee this whole file is about, reached the other way."""
    doc = json.loads((FIX / "distlabel.json").read_text(encoding="utf-8"))
    doc["data"]["header"]["stores"] = []
    p = tmp_path / "f.json"
    p.write_text(json.dumps(doc, indent=4), encoding="utf-8")
    declared = config.json_seed_row("CalgaryDistLabel", "Stores")

    _add(p, "Stores", registry, config)

    rows = _rows(p, "stores")
    assert len(rows) == 1
    nulls = [k for k, v in declared.items() if v is None]
    assert nulls and all(rows[0][k] is None for k in nulls), rows[0]


def test_an_ok_clone_is_unaffected(tmp_path, registry, config):
    """The fixed-width engine copies raw bytes and never reaches this code."""
    src = DATA_DIR / "DistLabels.OK"
    p = tmp_path / "d.OK"
    shutil.copy2(src, p)
    okf = parse_okfile(p, registry=registry)
    section = next(n for n, recs in okf.sections().items()
                   if recs and n != okf.layout.sections[0].name)
    before = [r.raw for r in okf.sections()[section]]

    _add(p, section, registry, config)

    after = [r.raw for r in parse_okfile(p, registry=registry).sections()[section]]
    assert len(after) == len(before) + 1
    assert after[-1] == before[-1]
    assert after[:len(before)] == before
