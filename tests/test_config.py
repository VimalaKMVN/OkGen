"""Tests for the chain registry, display-label resolution, and chain reader."""

import os
from pathlib import Path

import pytest

from okgen.config import Config
from okgen.detect import read_chain

DATA_DIR = Path(
    os.environ.get(
        "OKGEN_DATA_DIR",
        str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"),
    )
)
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"


@pytest.fixture(scope="module")
def cfg():
    return Config.load(FIXTURE_CONFIG)


def test_chain_registry(cfg):
    assert cfg.chain_name("01") == "TJMAXX"
    assert cfg.chain_name("02") == "Marshalls"
    assert cfg.chain_name("03") == "Homegoods"
    assert cfg.chain_name("04") == "Winners"
    assert cfg.chain_name("06") == "HomeSense"
    # Unknown code falls back to the raw value.
    assert cfg.chain_name("99") == "99"


def test_chain_field_options_from_registry(cfg):
    opts = cfg.options("chain")
    assert opts["03"] == "Homegoods"


def test_generic_label(cfg):
    assert cfg.label("indicator", "N") == "No"
    assert cfg.label("indicator", "Y") == "Yes"
    # Unmapped code returns itself.
    assert cfg.label("indicator", "Z") == "Z"


def test_specificity_resolution(cfg):
    # Generic StyleHeader rule.
    assert cfg.label("format", "A", layout="StyleHeader") == "Format A"
    # More specific chain+format rule wins.
    assert (
        cfg.label("format", "A", chain="03", layout="StyleHeader", fmt="A")
        == "Regular Tag (Homegoods)"
    )


def test_unmapped_field_returns_empty_options(cfg):
    assert cfg.options("keytrol") == {}


def test_field_colors(cfg):
    colors = cfg.field_colors()
    assert colors["chain"] == "#f06a6a"
    assert colors["format"] == "#5aa9ff"


def test_list_matching(cfg):
    # Rule matches chain 03 OR 04, layout StyleHeader OR Preticket.
    assert cfg.label("type", "1", chain="04", layout="Preticket") == "Type One"
    assert cfg.label("type", "2", chain="03", layout="StyleHeader") == "Type Two"
    # Chain outside the list -> no match, returns the raw code.
    assert cfg.label("type", "1", chain="05", layout="Preticket") == "1"
    # Layout outside the list -> no match.
    assert cfg.label("type", "1", chain="03", layout="CartonLabel") == "1"


def test_region_mapping(cfg):
    # Zones map to their configured region label.
    assert cfg.region("01") == "Reg1"
    assert cfg.region("33") == "Reg1"
    assert cfg.region("10") == "Reg2"
    assert cfg.region("07") == "Reg2"
    # Padding is stripped before lookup.
    assert cfg.region(" 05 ") == "Reg1"
    # Unmapped zone / blank / None -> empty string (no region).
    assert cfg.region("99") == ""
    assert cfg.region("") == ""
    assert cfg.region(None) == ""


@pytest.mark.skipif(not DATA_DIR.is_dir(), reason="sample data not present")
def test_read_chain(cfg):
    assert read_chain(DATA_DIR / "StyleHeader.OK") == "03"
    assert read_chain(DATA_DIR / "CartonLabel.OK") == "01"


# --------------------------------------------------------------------------- #
# A chain may be given by NAME (Calgary JSON) where rules are written by CODE
# --------------------------------------------------------------------------- #
def test_chain_name_matches_a_code_based_rule(cfg):
    """Calgary JSON files carry the chain as 'Winners'/'HomeSense' rather than
    '04'/'06', but display rules are written against the 2-char code. The name
    must resolve to the code so ONE rule set serves both engines."""
    by_code = cfg.label("format", "A", chain="03", layout="StyleHeader", fmt="A")
    by_name = cfg.label("format", "A", chain="Homegoods", layout="StyleHeader", fmt="A")
    assert by_code == "Regular Tag (Homegoods)"
    assert by_name == by_code, "a chain NAME must resolve like its code"


def test_chain_name_matching_is_case_insensitive(cfg):
    assert (cfg.label("format", "A", chain="homegoods", layout="StyleHeader", fmt="A")
            == "Regular Tag (Homegoods)")


def test_an_unknown_chain_still_falls_through(cfg):
    """A value that is neither a code nor a name must not crash or match — it
    just fails the chain criterion and a less specific rule applies."""
    assert cfg.label("format", "A", chain="99", layout="StyleHeader") == "Format A"


# --------------------------------------------------------------------------- #
# The SHIPPED config wires the Calgary JSON layouts into the .OK format lists
# --------------------------------------------------------------------------- #
SHIPPED_CONFIG = Path(__file__).resolve().parents[1] / "config"

shipped = pytest.mark.skipif(not (SHIPPED_CONFIG / "display.yaml").is_file(),
                             reason="shipped config not present")


@pytest.fixture(scope="module")
def prod():
    return Config.load(SHIPPED_CONFIG)


@shipped
@pytest.mark.parametrize("layout,chain,code,label", [
    # Every (layout, chain, format) combination present in the real Calgary
    # samples, resolved from the SAME lists the .OK layouts use.
    ("CalgaryStyleHeader", "04", "B", "Blue Gum"),
    ("CalgaryStyleHeader", "04", "C", "Coordinate Hard"),
    ("CalgaryStyleHeader", "04", "F", "Tough Tag"),
    ("CalgaryStyleHeader", "04", "S", "Small Gum"),
    ("CalgaryStyleHeader", "03", "A", "Regular Tag"),
    ("CalgaryCartonLabel", "Winners", "1", "Carton Label"),
    ("CalgaryCartonLabel", "HomeSense", "1", "Carton Label"),
    ("CalgaryCartonLabel", "01", "2", "Carton Ad Label"),
    ("CalgaryDistLabel", "06", "7", "Distribution Label"),
    ("CalgaryDistLabel", "04", "7", "Distribution Label"),
])
def test_calgary_json_layouts_resolve_format_names(prod, layout, chain, code, label):
    assert prod.label("format", code, chain=chain, layout=layout) == label


@shipped
@pytest.mark.parametrize("json_layout,ok_layout,chain", [
    ("CalgaryStyleHeader", "StyleHeader", "04"),
    ("CalgaryCartonLabel", "CartonLabel", "04"),
    ("CalgaryDistLabel", "DistLabels", "06"),
])
def test_json_format_options_are_the_same_list_as_the_ok_layout(
        prod, json_layout, ok_layout, chain):
    """The point of the change: no second list to keep in sync."""
    assert (prod.options("format", chain=chain, layout=json_layout)
            == prod.options("format", chain=chain, layout=ok_layout) != {})


@shipped
def test_an_unmapped_format_code_is_shown_verbatim(prod):
    """A code outside the list must render as itself, never be remapped."""
    assert prod.label("format", "Q", chain="Winners", layout="CalgaryStyleHeader") == "Q"


@shipped
@pytest.mark.parametrize("layout,chain,code,label", [
    ("StyleHeader", "03", "A", "Regular Tag"),
    ("Preticket", "01", "B", "Regular Gum Label"),
    ("CartonLabel", "04", "1", "Carton Label"),
    ("DistLabels", "06", "7", "Distribution Label"),
])
def test_the_ok_layouts_are_unaffected(prod, layout, chain, code, label):
    assert prod.label("format", code, chain=chain, layout=layout) == label


# --------------------------------------------------------------------------- #
# New ticket formats (added at the user's request)
# --------------------------------------------------------------------------- #
# The vendor added four ticket formats. They are STYLE HEADER only (the user's
# word) and valid on both engines' style-header layouts — `display.yaml` scopes
# a format list by CHAIN and names the `.OK` and Calgary layouts together, so
# one entry serves both (D41). Pre-Ticket, which shared those lists, must NOT
# gain them: it now has its own rule carrying the base list, which the Style
# Header rule merges via a YAML anchor so there is still only ONE copy of the
# shared entries. Note `X` means DIFFERENT things on the two chain groups —
# which is exactly why these lists are per chain and not global.
NEW_FORMATS = [
    ("03", "Q", "Two Part Tag"),           # Homegoods
    ("03", "X", "Dumbbell Gum Label"),     # Homegoods
    ("04", "W", "Purple Tuff Tag"),        # Winners
    ("04", "X", "Purple Rat Tail"),        # Winners
    ("06", "W", "Purple Tuff Tag"),        # HomeSense
    ("06", "X", "Purple Rat Tail"),        # HomeSense
]


@shipped
@pytest.mark.parametrize("chain,code,label", NEW_FORMATS)
@pytest.mark.parametrize("layout", ["StyleHeader", "CalgaryStyleHeader"])
def test_new_formats_resolve_on_both_engines(prod, layout, chain, code, label):
    assert prod.label("format", code, chain=chain, layout=layout) == label


@shipped
@pytest.mark.parametrize("chain,code,label", NEW_FORMATS)
def test_the_new_formats_are_style_header_only(prod, chain, code, label):
    """Pre-Ticket shares these lists and must not have picked them up. It is the
    same rule that serves Style Header, so the only thing keeping them apart is
    the split — assert it rather than trust it."""
    assert prod.label("format", code, chain=chain, layout="Preticket") == code, \
        "Pre-Ticket resolved a Style-Header-only format"
    assert code not in prod.options("format", chain=chain, layout="Preticket")


@shipped
@pytest.mark.parametrize("chain,base_count", [("03", 11), ("04", 20), ("06", 20)])
def test_preticket_keeps_exactly_its_original_list(prod, chain, base_count):
    """The split must SUBTRACT nothing from Pre-Ticket. Its list is the shared
    base, so it is also the thing the anchor has to reproduce faithfully."""
    pt = prod.options("format", chain=chain, layout="Preticket")
    sh = prod.options("format", chain=chain, layout="StyleHeader")

    assert len(pt) == base_count
    # every Pre-Ticket entry survives on Style Header, identically
    assert all(sh[k] == v for k, v in pt.items())
    # ...and Style Header is exactly that plus the new ones. Parenthesised on
    # purpose: `assert x == a if c else b` parses as `assert (x == a) if c
    # else b`, and a non-empty set is truthy — so the 04/06 cases would have
    # asserted nothing at all.
    expected_new = {"Q", "X"} if chain == "03" else {"W", "X"}
    assert set(sh) - set(pt) == expected_new


@shipped
@pytest.mark.parametrize("chain,code,label", NEW_FORMATS)
def test_new_formats_are_offered_in_the_editor(prod, chain, code, label):
    """`label()` resolving is not enough — the value has to be in the OPTIONS
    map too, or the dropdown will not offer it."""
    for layout in ("StyleHeader", "CalgaryStyleHeader"):
        assert prod.options("format", chain=chain, layout=layout).get(code) == label


@shipped
@pytest.mark.parametrize("chain,code,label", NEW_FORMATS)
def test_new_formats_resolve_when_the_chain_is_given_by_name(prod, chain, code, label):
    """A Calgary file may carry its chain as a brand NAME (D41/D57), and the
    rules are written against the code — so the name form has to resolve too."""
    name = prod.chain(chain).name
    assert prod.label("format", code, chain=name, layout="CalgaryStyleHeader") == label


@shipped
def test_x_means_different_things_on_different_chains(prod):
    """The sharpest property of the new set, and the reason a global format list
    would be wrong: one letter, two meanings, decided by the banner."""
    assert (prod.label("format", "X", chain="03", layout="StyleHeader")
            == "Dumbbell Gum Label")
    assert (prod.label("format", "X", chain="04", layout="StyleHeader")
            == "Purple Rat Tail")
    assert (prod.label("format", "X", chain="06", layout="CalgaryStyleHeader")
            == "Purple Rat Tail")


@shipped
@pytest.mark.parametrize("chain,code", [
    ("01", "Q"), ("01", "W"), ("01", "X"),      # TJMAXX gained nothing
    ("02", "Q"), ("02", "W"), ("02", "X"),      # Marshalls gained nothing
    ("03", "W"),                                # Homegoods already had W
    ("04", "Q"), ("06", "Q"),                   # Q is Homegoods-only
])
def test_the_new_formats_did_not_leak_onto_other_chains(prod, chain, code):
    """A format list is per banner. `W` on Homegoods is its own pre-existing
    LeadDuraTG and must not become the Winners label. None of the pairs below
    is one of the four new entries, so none may carry a new label — stated as a
    flat exclusion rather than an `or`, which is how this kind of assertion goes
    vacuous."""
    new_labels = {"Two Part Tag", "Dumbbell Gum Label",
                  "Purple Tuff Tag", "Purple Rat Tail"}

    assert prod.label("format", code, chain=chain, layout="StyleHeader") \
        not in new_labels


@shipped
def test_homegoods_w_still_means_what_it_did(prod):
    """The one collision worth naming: Homegoods already used `W`."""
    assert prod.label("format", "W", chain="03", layout="StyleHeader") == "LeadDuraTG"


@shipped
@pytest.mark.parametrize("chain,expected", [
    ("01", 18), ("02", 20), ("03", 13), ("04", 22), ("06", 22),
])
def test_no_existing_format_was_lost(prod, chain, expected):
    """Adding to a list is the easy way to drop something from it — and this one
    was also SPLIT and re-assembled through a YAML anchor, which is a second way
    to lose an entry. Counted per chain so neither can hide behind an
    addition."""
    assert len(prod.options("format", chain=chain, layout="StyleHeader")) == expected
