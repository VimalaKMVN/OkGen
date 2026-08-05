"""Chain isolation must hold when the chain is carried by NAME.

The isolated groups in `config/chains.yaml` are written as 2-char CODES, but a
Calgary JSON file may carry its chain as a NAME — D41 established the real
samples do both (`04`/`06` on style headers and dist labels, `Winners`/
`HomeSense` on carton labels).

`_chain_group_of` matched the raw value against those groups, so a name matched
nothing, read as ungrouped, and the D9/D30 rule silently passed. On a Calgary
carton label, `chain = "05"` was correctly refused while `chain = "Europe"` was
ACCEPTED — writing an EU chain onto an NA file, which is the exact crossing the
rule exists to prevent.

.OK layouts always carry a 2-char code, so they never reached the gap and are
unaffected by the fix.
"""
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


def test_isolation_resolves_names_on_both_sides(config):
    assert config.can_change_chain("Winners", "Europe") is False
    assert config.can_change_chain("Europe", "Winners") is False
    assert config.can_change_chain("04", "05") is False
    assert config.can_change_chain("Winners", "HomeSense") is True
    assert config.can_change_chain("Europe", "05") is True    # same chain, two forms


@pytest.mark.parametrize("value", ["05", "Europe", "europe"])
def test_europe_is_refused_by_name_as_well_as_by_code(tmp_path, registry, config,
                                                      value):
    """The live bypass: `05` refused, `Europe` accepted."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "cartonlabel_minified.json", p)
    original = p.read_bytes()
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]

    with pytest.raises(Exception):
        service.apply_edits(p, [{"record_index": ri, "field": "chain",
                                 "value": value}],
                            registry, config=config, backup=False)

    assert p.read_bytes() == original


def test_an_na_chain_still_moves_freely_by_either_form(tmp_path, registry, config):
    """Isolation blocks the Europe boundary only — the NA banners still swap."""
    for value in ("04", "06", "Winners", "HomeSense"):
        assert config.can_change_chain("Winners", value) is True
        assert config.can_change_chain("04", value) is True


def test_chains_like_is_unchanged_for_codes(config):
    assert config.chains_like("04") == ["01", "02", "03", "04", "06"]
    assert config.chains_like("05") == ["05"]
