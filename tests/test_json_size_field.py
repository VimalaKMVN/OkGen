"""A Calgary StyleHeader's `size` declares a width, so every path can write it.

`CalgaryStyleHeader.Sizes.size` declared NO size at all, because the spec was
built from vendor samples and every one of them carries `"size": null` — there
was no value to measure. A JSON `Field.size` is a maximum length, not a pad
width (D20/D48), so "no size" is not "unlimited": it is *undeclared*, and the
three write paths each answered that differently.

    panel label      `size (?)`   — the descriptor carried `size: null`
    Bulk Edit        REFUSED      — "size has no fixed width"
    Volume Generate  SILENTLY SKIPPED — reported `written: 1`, wrote nothing;
                                   the field was not even LISTED in the panel
    single editor    accepted ANY length, no cap whatsoever

The width is **6**, matching the `.OK` StyleHeader's own `Size.size` — the same
field in the fixed-width engine, where a size IS the format. That is asserted
here rather than assumed, so the two engines cannot drift apart silently.

Note what this deliberately tightens: the editor used to save a 7+ character
size, alone among the paths. It is refused now, which is the point — a value
the `.OK` side could never hold should not be reachable from the JSON side, and
a field accepted in one panel and refused in another reads as a bug (D63).
Refused, never truncated (D40): an over-long value leaves the file untouched.

Volume Generate is the case that matters most and the reason this is a defect
rather than a papercut — it reported success while dropping the value, so a
whole generated batch would carry `size: null` with nothing on screen saying so
(the D28/D43/D47 silent-no-op class, in the path that CREATES files).
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

LAYOUT = "CalgaryStyleHeader"
SAMPLES = ["styleheader_fmtB.json", "styleheader_fmtS.json", "styleheader_fmtT.json"]
SIZE_WIDTH = 6
FITS = ["S", "MED", "XSMALL", "123456"]      # 1, 3, 6, 6 characters
TOO_LONG = ["XXLARGE", "1234567"]            # 7 characters


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _copy(tmp_path, sample):
    p = tmp_path / sample
    shutil.copy2(FIX / sample, p)
    return p


def _sizes(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]["header"]["sizes"]


def _size_record_index(path, registry, config):
    view = service.parse_file_view(path, registry, config)
    sec = next(s for s in view["sections"] if s["name"] == "Sizes")
    return sec["records"][0]["index"]


# --------------------------------------------------------------- the width

def test_ok_styleheader_size_is_six_characters(registry):
    """The source of the number. If the `.OK` layout ever changes width, this
    fails and the JSON declaration must be revisited with it."""
    layout = registry["StyleHeader"]
    sec = next(s for s in layout.sections if s.name == "Size")
    field = next(f for f in sec.fields if f.name == "size")
    assert field.size == SIZE_WIDTH


def test_json_size_declares_the_same_width(registry):
    layout = registry[LAYOUT]
    sec = next(s for s in layout.sections if s.name == "Sizes")
    field = next(f for f in sec.fields if f.name == "size")
    assert field.size == SIZE_WIDTH


# ------------------------------------------------- what the panels are told

def test_bulk_edit_shows_a_width_not_a_question_mark(registry, config):
    """`size: null` in the descriptor is what the panel renders as `size (?)`."""
    scope = service.bulk_scope([str(FIX / SAMPLES[0])], registry, config)
    sec = next(s for s in scope["detail_sections"][LAYOUT] if s["name"] == "Sizes")
    field = next(f for f in sec["fields"] if f["name"] == "size")
    assert field["size"] == SIZE_WIDTH


def test_volume_generate_offers_the_field_at_all(registry, config):
    """Generate did not merely mislabel `size` — it OMITTED it, and a field
    left out reads as "OkGen forgot it" rather than "you may not set it" (D61)."""
    scope = service.generate_scope([str(FIX / SAMPLES[0])], registry, config)
    sec = next(s for s in scope["sections"] if s["name"] == "Sizes")
    names = [f["name"] for f in sec["fields"]]
    assert "size" in names
    field = next(f for f in sec["fields"] if f["name"] == "size")
    assert field["size"] == SIZE_WIDTH


# ------------------------------------------------- every path can write it

@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("value", FITS)
def test_editor_writes_size(tmp_path, registry, config, sample, value):
    p = _copy(tmp_path, sample)
    ri = _size_record_index(p, registry, config)
    service.apply_edits(p, [{"record_index": ri, "field": "size", "value": value}],
                        registry, config=config, backup=False)
    assert _sizes(p)[0]["size"] == value


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("value", FITS)
def test_bulk_rows_and_sequences_writes_size(tmp_path, registry, config,
                                             sample, value):
    p = _copy(tmp_path, sample)
    res = service.bulk_op_apply([str(p)], LAYOUT, "Sizes",
                                {"type": "set", "field": "size", "value": value},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "changed", res["results"][0]
    assert _sizes(p)[0]["size"] == value


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("value", FITS)
def test_bulk_field_values_writes_size(tmp_path, registry, config, sample, value):
    p = _copy(tmp_path, sample)
    res = service.bulk_multi_apply(
        [str(p)], LAYOUT,
        [{"section": "Sizes", "type": "list", "field": "size", "values": [value]}],
        registry, config, backup=False)
    assert res["results"][0]["status"] == "changed", res["results"][0]
    assert _sizes(p)[0]["size"] == value


@pytest.mark.parametrize("value", FITS)
def test_volume_generate_writes_size(tmp_path, registry, config, value):
    """The silent skip: this reported `written: 1` and produced `size: null`."""
    p = _copy(tmp_path, SAMPLES[1])
    spec = {"count": 2, "folder": str(tmp_path / "out"),
            "detail_fields": [{"section": "Sizes", "name": "size",
                               "type": "list", "values": [value]}]}
    res = service.generate_apply([str(p)], spec, registry, config)
    made = sorted(Path(res["folder"]).glob("*.json"))
    assert len(made) == 2, res
    for m in made:
        assert _sizes(m)[0]["size"] == value


def test_generate_reporting_matches_what_it_wrote(tmp_path, registry, config):
    """A success report with nothing written is the failure this closes."""
    p = _copy(tmp_path, SAMPLES[1])
    spec = {"count": 1, "folder": str(tmp_path / "out"),
            "detail_fields": [{"section": "Sizes", "name": "size",
                               "type": "list", "values": ["MED"]}]}
    res = service.generate_apply([str(p)], spec, registry, config)
    assert res["written"] == 1
    made = sorted(Path(res["folder"]).glob("*.json"))
    assert [_sizes(m)[0]["size"] for m in made] == ["MED"]


# ------------------------------------- over-long is refused, never truncated

@pytest.mark.parametrize("value", TOO_LONG)
def test_editor_refuses_an_over_long_size(tmp_path, registry, config, value):
    p = _copy(tmp_path, SAMPLES[1])
    ri = _size_record_index(p, registry, config)
    before = p.read_bytes()
    with pytest.raises(service.EditError) as exc:
        service.apply_edits(p, [{"record_index": ri, "field": "size",
                                 "value": value}],
                            registry, config=config, backup=False)
    assert "size" in str(exc.value)
    assert p.read_bytes() == before          # refused, not truncated (D40)


@pytest.mark.parametrize("value", TOO_LONG)
def test_bulk_refuses_an_over_long_size(tmp_path, registry, config, value):
    p = _copy(tmp_path, SAMPLES[1])
    before = p.read_bytes()
    res = service.bulk_multi_apply(
        [str(p)], LAYOUT,
        [{"section": "Sizes", "type": "list", "field": "size", "values": [value]}],
        registry, config, backup=False)
    entry = res["results"][0]
    assert entry["status"] == "error"
    assert str(SIZE_WIDTH) in entry["error"] and "size" in entry["error"]
    assert p.read_bytes() == before


def test_the_refusal_names_the_width(tmp_path, registry, config):
    """A user given a limit can act on it; "has no fixed width" could not be."""
    p = _copy(tmp_path, SAMPLES[1])
    res = service.bulk_multi_apply(
        [str(p)], LAYOUT,
        [{"section": "Sizes", "type": "list", "field": "size",
          "values": ["XXLARGE"]}], registry, config, backup=False)
    msg = res["results"][0]["error"]
    assert "no fixed width" not in msg
    assert "too long" in msg and f"({SIZE_WIDTH})" in msg


# ------------------------------------------------------------- the boundary

def test_exactly_six_characters_is_accepted(tmp_path, registry, config):
    p = _copy(tmp_path, SAMPLES[1])
    ri = _size_record_index(p, registry, config)
    service.apply_edits(p, [{"record_index": ri, "field": "size",
                             "value": "XSMALL"}],
                        registry, config=config, backup=False)
    assert _sizes(p)[0]["size"] == "XSMALL"


def test_every_sample_size_value_still_fits(registry):
    """Widening cannot be claimed safe without checking what the files hold.
    Every shipped sample carries `size: null`, so nothing is squeezed."""
    checked = 0
    for path in sorted(FIX.glob("styleheader*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for row in (doc["data"]["header"].get("sizes") or []):
            val = row.get("size")
            checked += 1
            if isinstance(val, str):
                assert len(val) <= SIZE_WIDTH, f"{path.name}: {val!r}"
    assert checked, "no size rows found — the assertion would be vacuous"
