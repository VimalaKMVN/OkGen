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


@pytest.mark.skipif(not DATA_DIR.is_dir(), reason="sample data not present")
def test_read_chain(cfg):
    assert read_chain(DATA_DIR / "StyleHeader.OK") == "03"
    assert read_chain(DATA_DIR / "CartonLabel.OK") == "01"
