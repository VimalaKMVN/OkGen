"""What a user may enter or select in the Calgary JSON editable fields.

Four separate rules, all JSON-only:

* **prices** (`retailPrice`, `price`, `compareAtPrice`) are stored exactly as
  typed — no zero-padding. Only `store` is a `pad_zeros` field (D34);
* **`compareAtUp`** allows `true`/`false`/`Yes`/`No`/`Y`/`N` and BLANK, in any
  capitalisation. Blank is a real value, not a synonym for `false`;
* **`chain`** allows a code (`04`) or a name (`Winners`), in any capitalisation
  — the real samples carry both (D41);
* **`type`** allows its OWN document word in any capitalisation — never
  another layout's, since the document's shape does not change with it.

Two safety rules fall out of the last two, and both were broken before:

* chain ISOLATION (D9/D30) must hold for the name form. It did not: `05` was
  correctly refused on a Calgary carton label while `Europe` was accepted,
  because the isolation groups are written as codes and a name matched none of
  them, reading as ungrouped;
* `type` is the detection signature. It was marked read-only in the layout spec
  and hidden from bulk — but `apply_edits` does not enforce `readonly`, so a
  direct write left the file detecting as NO layout, i.e. unopenable in OkGen.
  `_assert_layout_stable` skipped JSON entirely on the assumption the field was
  unreachable. It no longer does.
"""
import json
import shutil
from pathlib import Path

import pytest

from okgen import detect
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


def _edit(tmp_path, registry, config, fixture, section, field, value):
    """Write one field; return the stored value, or raise."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    view = service.parse_file_view(p, registry, config)
    sec = next(s for s in view["sections"] if s["name"] == section)
    service.apply_edits(p, [{"record_index": sec["records"][0]["index"],
                             "field": field, "value": value}],
                        registry, config=config, backup=False)
    doc = json.loads(p.read_text(encoding="utf-8"))
    if field == "type":
        return doc["data"]["type"]
    node = doc["data"]["header"] if section == "Header" else doc["data"]["details"][0]
    return node[field]


# --------------------------------------------------------------------------- #
# Prices — stored exactly as typed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("section,field", [
    ("Header", "retailPrice"), ("Header", "price"),
    ("Details", "retailPrice"), ("Details", "compareAtPrice"),
])
@pytest.mark.parametrize("value", ["19.99", "7.5", "1", "0", "999999", "000799999"])
def test_a_price_is_stored_exactly_as_typed(tmp_path, registry, config,
                                            section, field, value):
    assert _edit(tmp_path, registry, config,
                 "styleheader_fmtB.json", section, field, value) == value


def test_no_price_field_is_zero_padded(config):
    """Only `store` pads. A price that grew leading zeros would change its
    value, not just its presentation."""
    for layout in ("CalgaryStyleHeader", "CalgaryDistLabel", "CalgaryCartonLabel"):
        assert config.pad_zero_fields(layout) == {"store"}, layout


# --------------------------------------------------------------------------- #
# compareAtUp
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["true", "false", "Yes", "No", "Y", "N", "",
                                   "TRUE", "False", "yes", "n"])
def test_compare_at_up_accepts_its_allowed_values(tmp_path, registry, config, value):
    assert _edit(tmp_path, registry, config,
                 "styleheader_fmtB.json", "Details", "compareAtUp", value) == value


def test_compare_at_up_offers_blank_as_a_real_choice(registry, config):
    opts = config.options("compareAtUp", layout="CalgaryStyleHeader",
                          section="Details")
    assert "" in opts, "blank must be selectable, not just typeable"
    assert set(opts) == {"", "true", "false", "Yes", "No", "Y", "N"}


def test_compare_at_up_options_are_strings_not_yaml_booleans(config):
    """YAML 1.1 reads bare true/false/Yes/No/Y/N as booleans. Unquoted, the list
    would load as Python True/False and offer "True"/"False" — values no Calgary
    file contains."""
    opts = config.options("compareAtUp", layout="CalgaryStyleHeader",
                          section="Details")
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in opts.items())
    assert "True" not in opts and "False" not in opts


# --------------------------------------------------------------------------- #
# chain — code or name
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["04", "06", "Winners", "HomeSense",
                                   "winners", "HOMESENSE"])
def test_chain_accepts_a_code_or_a_name(tmp_path, registry, config, value):
    assert _edit(tmp_path, registry, config,
                 "cartonlabel_minified.json", "Header", "chain", value) == value


def test_chain_offers_both_forms(registry, config):
    opts = config.options("chain", layout="CalgaryCartonLabel", section="Header")
    assert {"01", "04", "06"} <= set(opts)
    assert {"TJMAXX", "Winners", "HomeSense"} <= set(opts)


def test_the_ok_chain_list_is_unchanged(config):
    """The Calgary rule is scoped by layout; .OK keeps the code-only registry."""
    ok = config.options("chain", layout="StyleHeader", section="Header")
    assert set(ok) == {"01", "02", "03", "04", "05", "06"}
    assert not any(c.isalpha() for c in ok)


# --------------------------------------------------------------------------- #
# type — the detection signature
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["styleHeaders", "StyleHeaders", "STYLEHEADERS",
                                   "styleheaders"])
def test_type_accepts_any_capitalisation(tmp_path, registry, config, value):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "styleheader_fmtB.json", p)
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]

    service.apply_edits(p, [{"record_index": ri, "field": "type", "value": value}],
                        registry, config=config, backup=False)

    assert json.loads(p.read_text(encoding="utf-8"))["data"]["type"] == value
    assert detect.detect_layout(p).layout == "CalgaryStyleHeader", \
        "a re-cased type must still detect — otherwise the file is unopenable"


# (fixture, its own type word, the other two layouts' words)
TYPE_CASES = [
    ("styleheader_fmtB.json", "styleHeaders",
     ["cartonLabels", "distributionLabels"]),
    ("distlabel.json", "distributionLabels",
     ["styleHeaders", "cartonLabels"]),
    ("cartonlabel_minified.json", "cartonLabels",
     ["styleHeaders", "distributionLabels"]),
]


@pytest.mark.parametrize("fixture,own,others", TYPE_CASES)
def test_every_layout_accepts_any_casing_of_its_own_type(tmp_path, registry, config,
                                                         fixture, own, others):
    for value in (own, own.upper(), own.lower(), own.capitalize()):
        p = tmp_path / "f.json"
        shutil.copy2(FIX / fixture, p)
        view = service.parse_file_view(p, registry, config)
        layout = view["layout"]
        ri = view["sections"][0]["records"][0]["index"]

        service.apply_edits(p, [{"record_index": ri, "field": "type",
                                 "value": value}],
                            registry, config=config, backup=False)

        assert json.loads(p.read_text(encoding="utf-8"))["data"]["type"] == value
        assert detect.detect_layout(p).layout == layout


@pytest.mark.parametrize("fixture,own,others", TYPE_CASES)
def test_a_cross_type_change_is_refused(tmp_path, registry, config,
                                        fixture, own, others):
    """The document's shape does not change with its discriminator, so re-typing
    a style header as a carton label leaves the two contradicting each other."""
    for value in others:
        p = tmp_path / "f.json"
        shutil.copy2(FIX / fixture, p)
        original = p.read_bytes()
        view = service.parse_file_view(p, registry, config)
        ri = view["sections"][0]["records"][0]["index"]

        with pytest.raises(Exception):
            service.apply_edits(p, [{"record_index": ri, "field": "type",
                                     "value": value}],
                                registry, config=config, backup=False)

        assert p.read_bytes() == original


@pytest.mark.parametrize("fixture,own,others", TYPE_CASES)
def test_a_cross_type_change_is_refused_in_bulk_too(tmp_path, registry, config,
                                                    fixture, own, others):
    """Bulk is where D12 said a signature change matters most — one apply would
    otherwise re-type a whole selection."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    original = p.read_bytes()
    layout = service.parse_file_view(p, registry, config)["layout"]

    res = service.bulk_apply([str(p)], layout, "type", others[0],
                             registry, config, backup=False)

    assert res["results"][0]["status"] == "error"
    assert p.read_bytes() == original


@pytest.mark.parametrize("fixture,own,others", TYPE_CASES)
def test_only_the_layouts_own_word_is_offered(tmp_path, registry, config,
                                              fixture, own, others):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    header = service.parse_file_view(p, registry, config)["sections"][0]
    field = next(f for f in header["fields"] if f["name"] == "type")

    assert set(field["options"]) == {own}
    assert not set(field["options"]) & set(others)


@pytest.mark.parametrize("value", ["nonsense", "", "styleHeader", "style Headers"])
def test_an_unknown_type_is_refused(tmp_path, registry, config, value):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "styleheader_fmtB.json", p)
    original = p.read_bytes()
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]

    with pytest.raises(Exception):
        service.apply_edits(p, [{"record_index": ri, "field": "type", "value": value}],
                            registry, config=config, backup=False)

    assert p.read_bytes() == original


def test_detection_is_case_insensitive_for_every_type():
    for word in detect.canonical_json_types():
        for variant in (word, word.upper(), word.lower(), word.capitalize()):
            assert detect.json_type_is_known(variant), variant
    assert not detect.json_type_is_known("styleHeader")     # singular, not a type
    assert not detect.json_type_is_known("")


# --------------------------------------------------------------------------- #
# .OK must not move
# --------------------------------------------------------------------------- #
def test_ok_detection_is_still_exact(tmp_path, registry, config):
    """Case-insensitivity is a JSON rule. A fixed-width marker is a BYTE — 'n'
    is not 'N', and treating them alike would re-detect files wrongly."""
    p = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
    assert detect.detect_layout(p).layout == "StyleHeader"

    raw = p.read_bytes()
    head, rest = raw.split(b"\n", 1)
    p.write_bytes(head.replace(b"N", b"n", 1) + b"\n" + rest)
    assert detect.detect_layout(p).layout != "StyleHeader"


def test_the_ok_type_field_is_a_different_field_entirely(registry, config):
    """`.OK` StyleHeader also has a header field called `type` — but it is the
    1-character coded TICKET type, not the JSON document kind. The document-word
    list must not reach it, or the editor would offer 'styleHeaders' for a
    single-char field and the value would not even fit."""
    header = registry["StyleHeader"].sections[0]
    ok_type = next(f for f in header.fields if f.name == "type")
    assert ok_type.size == 1, "the .OK `type` is a 1-char code"

    opts = config.options("type", layout="StyleHeader", chain="03",
                          section=header.name)
    assert "styleHeaders" not in opts
    assert "distributionLabels" not in opts


# --------------------------------------------------------------------------- #
# `type` must be TYPEABLE, not just legally editable
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fixture,layout", [
    ("styleheader_fmtB.json", "CalgaryStyleHeader"),
    ("distlabel.json", "CalgaryDistLabel"),
    ("cartonlabel_minified.json", "CalgaryCartonLabel"),
])
def test_type_is_offered_as_a_freeform_field(tmp_path, registry, config,
                                             fixture, layout):
    """User-reported: "type is not allowing me to edit". It was editable and a
    re-casing already saved fine — but the editor renders a field with options
    as a dropdown, and this field's list is the ONE word its layout carries, so
    the control offered a single choice and no way to type another casing.

    The descriptor now says `freeform`, which is what makes the client render a
    text box (with the known values suggested). Asserted per layout: a flag set
    for one and forgotten for the others would be the same bug, two thirds of
    the time."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)

    view = service.parse_file_view(p, registry, config)
    f = next(x for x in view["sections"][0]["fields"] if x["name"] == "type")

    assert f["editable"] is True
    assert f["freeform"] is True
    assert f["options"], "the known value must still be offered as a suggestion"


def test_an_ordinary_coded_field_is_not_freeform(tmp_path, registry, config):
    """The flag has to be opt-in, or every dropdown in the app quietly becomes a
    text box. An `.OK` StyleHeader is the check: it carries the same field NAMES
    (`chain`, `type`, `format`) as the Calgary layouts, all coded, and none of
    them may pick up the JSON layouts' freeform rendering — a fixed-width field
    IS its width, so free text there is a different and much worse idea."""
    p = tmp_path / "s.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)

    view = service.parse_file_view(p, registry, config)
    coded = [x for x in view["sections"][0]["fields"] if x["options"]]

    assert coded, "fixture must carry coded fields for this to mean anything"
    assert all(x["freeform"] is False for x in coded), \
        [x["name"] for x in coded if x["freeform"]]


@pytest.mark.parametrize("typed", ["STYLEHEADERS", "styleheaders", "StyleHeaders"])
def test_any_capitalisation_the_user_types_is_saved(tmp_path, registry, config, typed):
    """What the text box allows must actually reach the file, verbatim."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "styleheader_fmtB.json", p)
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]

    service.apply_edits(p, [{"section_index": 0, "record_index": ri,
                             "field": "type", "value": typed}],
                        registry, config=config, backup=False)

    assert json.loads(p.read_text(encoding="utf-8"))["data"]["type"] == typed
    # ...and the file still opens as the same layout
    assert service.parse_file_view(p, registry, config)["layout"] == "CalgaryStyleHeader"


def test_a_cross_layout_type_is_still_refused(tmp_path, registry, config):
    """Making the field typeable must not widen what may be saved."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "styleheader_fmtB.json", p)
    before = p.read_bytes()
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]

    for bad in ("cartonLabels", "CARTONLABELS", "distributionLabels", "nonsense"):
        with pytest.raises(Exception):
            service.apply_edits(p, [{"section_index": 0, "record_index": ri,
                                     "field": "type", "value": bad}],
                                registry, config=config, backup=False)
    assert p.read_bytes() == before, "a refused write must leave the file untouched"

