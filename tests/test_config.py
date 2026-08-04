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
