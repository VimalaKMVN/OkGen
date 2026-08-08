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


EXPECTED_COMPARE_AT_UP = ["", "TRUE", "FALSE", "YES", "NO", "Y", "N",
                          "true", "false", "yes", "no", "y", "n"]


def test_compare_at_up_offers_blank_as_a_real_choice(registry, config):
    opts = config.options("compareAtUp", layout="CalgaryStyleHeader",
                          section="Details")
    assert "" in opts, "blank must be selectable, not just typeable"
    assert set(opts) == set(EXPECTED_COMPARE_AT_UP)


def test_compare_at_up_offers_every_casing_the_documents_use(registry, config):
    """The field accepts all of these, so the dropdown must offer all of them.

    A list that carried only some casings left the rest unreachable from the
    editor — the `type` problem (D56), where a value the save would accept
    could not be selected. This is a DROPDOWN, so what is listed is exactly
    what can be picked.
    """
    opts = config.options("compareAtUp", layout="CalgaryStyleHeader",
                          section="Details")
    assert list(opts) == EXPECTED_COMPARE_AT_UP, "order matters: blank, upper, lower"


def test_every_offered_compare_at_up_value_fits_the_field(registry, config):
    """A dropdown may not offer a value too long to SAVE (D48). `FALSE` is 5
    characters and the field holds exactly 5 — there is no spare room."""
    layout = registry["CalgaryStyleHeader"]
    sec = next(s for s in layout.sections if s.name == "Details")
    field = next(f for f in sec.fields if f.name == "compareAtUp")
    for value in config.options("compareAtUp", layout="CalgaryStyleHeader",
                                section="Details"):
        assert len(value) <= field.size, f"{value!r} exceeds size {field.size}"


@pytest.mark.parametrize("value", EXPECTED_COMPARE_AT_UP)
def test_every_offered_value_actually_saves(tmp_path, registry, config, value):
    """Offering it and accepting it must not drift apart — the whole reason the
    list can be widened without touching the write path."""
    assert _edit(tmp_path, registry, config,
                 "styleheader_fmtB.json", "Details", "compareAtUp", value) == value


def test_compare_at_up_options_are_strings_not_yaml_booleans(config):
    """YAML 1.1 reads bare true/false/Yes/No/Y/N — and TRUE/FALSE/YES/NO/y/n —
    as booleans. Unquoted, the list would load as Python True/False and offer
    "True"/"False", values no Calgary file contains.

    Quoting is also what lets twelve near-identical entries coexist: unquoted,
    six of them would collapse onto the other two and the list would silently
    lose half its options.
    """
    opts = config.options("compareAtUp", layout="CalgaryStyleHeader",
                          section="Details")
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in opts.items())
    assert "True" not in opts and "False" not in opts
    assert len(opts) == 13, "a collapsed casing means a key lost its quotes"


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


# --------------------------------------------------------------------------- #
# Chain by NAME on the JSON layouts
# --------------------------------------------------------------------------- #
JSON_LAYOUTS = [
    ("styleheader_fmtB.json", "CalgaryStyleHeader"),
    ("distlabel.json", "CalgaryDistLabel"),
    ("cartonlabel_minified.json", "CalgaryCartonLabel"),
]
# Every declared North-America brand name, in the casings a user actually types.
NAMES = ["Winners", "winners", "WINNERS", "HomeSense", "homesense",
         "Marshalls", "MARSHALLS", "TJMAXX", "tjmaxx", "Homegoods", "homegoods"]
# ...and the codes. BOTH forms are valid input — widening the field for names
# must not quietly make a code the second-class citizen, so the codes are
# parametrized alongside the names rather than spot-checked.
CODES = ["01", "02", "03", "04", "06"]
# Europe, by code and by name — refused on every JSON layout, these being NA.
EUROPE = ["05", "Europe", "europe", "EUROPE"]


@pytest.mark.parametrize("fixture,layout", JSON_LAYOUTS)
def test_chain_is_wide_enough_for_every_brand_name(tmp_path, registry, config,
                                                   fixture, layout):
    """User-reported: bulk edit and Volume Generate failed with "too long" for
    the field. D48 widened `chain` to 9 on CalgaryCartonLabel — whose samples
    carry a NAME — and the other two layouts, whose samples carry a CODE, were
    left at 2. So a name overflowed on two of the three, on every write path."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)

    field = next(x for x in service.parse_file_view(p, registry, config)
                 ["sections"][0]["fields"] if x["name"] == "chain")

    assert field["size"] >= max(len(n) for n in NAMES)
    assert field["freeform"] is True, "a dropdown cannot be typed in another casing"


@pytest.mark.parametrize("fixture,layout", JSON_LAYOUTS)
@pytest.mark.parametrize("name", NAMES + CODES)
def test_a_chain_name_or_code_saves_in_the_single_editor(tmp_path, registry, config,
                                                         fixture, layout, name):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]

    service.apply_edits(p, [{"section_index": 0, "record_index": ri,
                             "field": "chain", "value": name}],
                        registry, config=config, backup=False)

    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["data"]["header"]["chain"] == name, "stored exactly as typed"


@pytest.mark.parametrize("fixture,layout", JSON_LAYOUTS)
@pytest.mark.parametrize("value", ["homesense", "HomeSense", "06", "01"])
def test_a_chain_name_or_code_saves_through_bulk_edit(tmp_path, registry, config,
                                                      fixture, layout, value):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)

    res = service.bulk_apply([str(p)], layout, "chain", value,
                             registry, config, backup=False)["results"][0]

    # `unchanged` is a legitimate outcome, not a failure: distlabel.json already
    # carries chain 06, and a bulk write of the value a file already has is a
    # no-op by design. What must hold either way is that the value was ACCEPTED
    # (never an error) and that the file ends up carrying it.
    assert res["status"] in ("changed", "unchanged"), res
    assert json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["chain"] == value


@pytest.mark.parametrize("fixture,layout", JSON_LAYOUTS)
@pytest.mark.parametrize("listed", [
    "Winners, HomeSense, Marshalls",          # names only
    "01, 03, 06",                             # codes only
    "04, HomeSense, 01, marshalls",           # both forms in ONE list
])
def test_volume_generate_accepts_names_and_codes(tmp_path, registry, config,
                                                 fixture, layout, listed):
    """A generated batch may mix the two forms — the user picks per value, not
    per run."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    wanted = {v.strip() for v in listed.split(",")}

    res = service.generate_apply(
        [str(p)],
        {"count": 8, "dest": str(tmp_path / "out"),
         "header_fields": [{"name": "chain", "values": listed}]},
        registry, config)

    chains = {json.loads(f.read_text(encoding="utf-8"))["data"]["header"]["chain"]
              for f in sorted(Path(res["folder"]).glob("*.json"))}
    assert chains, "generate produced no files"
    assert chains <= wanted, chains


# --------------------------------------------------------------------------- #
# ...but Europe is still out of reach. These layouts are North America.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fixture,layout", JSON_LAYOUTS)
@pytest.mark.parametrize("value", EUROPE)
def test_europe_is_refused_in_the_single_editor(tmp_path, registry, config,
                                                fixture, layout, value):
    """Widening the field must not open the isolation boundary (D9/D30/D50) —
    by code OR by name, in any casing."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    before = p.read_bytes()
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]

    with pytest.raises(Exception):
        service.apply_edits(p, [{"section_index": 0, "record_index": ri,
                                 "field": "chain", "value": value}],
                            registry, config=config, backup=False)
    assert p.read_bytes() == before


@pytest.mark.parametrize("fixture,layout", JSON_LAYOUTS)
@pytest.mark.parametrize("value", EUROPE)
def test_europe_is_refused_by_bulk_edit(tmp_path, registry, config,
                                        fixture, layout, value):
    """The bulk path is where this rule has been bypassed before (D30, D50), so
    it is asserted separately rather than assumed from the editor."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    before = p.read_bytes()

    res = service.bulk_apply([str(p)], layout, "chain", value,
                             registry, config, backup=False)["results"][0]

    assert res["status"] == "error", res
    assert p.read_bytes() == before


@pytest.mark.parametrize("fixture,layout", JSON_LAYOUTS)
def test_generate_never_offers_europe(tmp_path, registry, config, fixture, layout):
    """Randomizing `chain` picks from the template's own isolation group."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)

    res = service.generate_apply(
        [str(p)], {"count": 12, "dest": str(tmp_path / "out"),
                   "header_fields": [{"name": "chain"}]},
        registry, config)

    chains = {json.loads(f.read_text(encoding="utf-8"))["data"]["header"]["chain"]
              for f in sorted(Path(res["folder"]).glob("*.json"))}
    assert chains, "generate produced no files"
    assert "05" not in chains and "Europe" not in chains, chains


@pytest.mark.parametrize("fixture,layout", JSON_LAYOUTS)
def test_the_editor_is_told_which_values_are_codes_and_which_are_names(
        tmp_path, registry, config, fixture, layout):
    """A chain may be stored either way, so the rendered view says which form
    the file on disk is using. The client must not have to guess that from the
    text — `Config.chain()` is authoritative here, and it is what decides."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)

    field = next(x for x in service.parse_file_view(p, registry, config)
                 ["sections"][0]["fields"] if x["name"] == "chain")

    forms = field["value_forms"]
    assert forms, "no form map — the editor cannot label the value"
    assert set(forms) == set(field["options"]), "every offered value must be labelled"
    assert {v for v in forms.values()} == {"code", "name"}
    for code in CODES:
        assert forms.get(code) == "code", code
    for name in ("Winners", "HomeSense", "Marshalls", "TJMAXX", "Homegoods"):
        assert forms.get(name) == "name", name


@pytest.mark.parametrize("fixture,layout", JSON_LAYOUTS)
def test_europe_is_never_offered_to_the_editor(tmp_path, registry, config,
                                               fixture, layout):
    """Isolation filters the OFFER as well as policing the write, so Europe is
    absent from the dropdown and from the form map — a value the user cannot
    save should not be presented as a choice."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)

    field = next(x for x in service.parse_file_view(p, registry, config)
                 ["sections"][0]["fields"] if x["name"] == "chain")

    assert "05" not in field["options"] and "Europe" not in field["options"]
    assert "05" not in field["value_forms"] and "Europe" not in field["value_forms"]


def test_an_ok_layout_gets_no_form_map(tmp_path, registry, config):
    """It is a JSON question: an `.OK` chain is a fixed-width 2-char code and
    can only ever be a code."""
    p = tmp_path / "s.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)

    fields = {x["name"]: x for x in
              service.parse_file_view(p, registry, config)["sections"][0]["fields"]}

    assert fields["chain"]["value_forms"] is None
