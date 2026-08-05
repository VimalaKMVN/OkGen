"""Volume Generate's per-section row count must GROW a section, not only shrink.

`_set_row_count` cloned with the fixed-width constructor:

    clone = new_record(template.raw.rstrip("\\r"), sec, okf.layout, ...)

A `JsonRecord` carries ``raw == ''``, so that built an empty fixed-width record
with no ``array_path`` — which the JSON serializer then ignores. Volume Generate
could therefore not add a row to ANY JSON section while reporting success:
asking for 15 stores on a 10-store file wrote 10, and asking for 3 on an empty
section wrote 1 (the seeded row, which does carry an array_path).

Only the SHRINK direction was ever exercised, which is how it went unnoticed —
the D28/D43 silent-no-op class in the one path still using the fixed-width
clone. Both directions are now covered on both engines.
"""
import json
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import parse_okfile

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


def _generate(path, section, target, registry, config):
    res = service.generate_apply(
        [str(path)],
        {"count": 1, "row_counts": [{"section": section, "min": target, "max": target}]},
        registry, config)
    return sorted(Path(res["folder"]).iterdir())[0]


def _json_rows(path, key="stores"):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]["header"][key]


def _ok_rows(path, section, registry):
    return parse_okfile(path, registry=registry).sections()[section]


# --------------------------------------------------------------------------- #
# JSON — the broken engine
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("target", [3, 10, 15, 25])
def test_a_json_section_reaches_the_requested_count(tmp_path, registry, config, target):
    """Both directions from a 10-row fixture: 3 shrinks, 15 and 25 grow."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "distlabel.json", p)
    assert len(_json_rows(p)) == 10, "fixture must start at 10 for this to mean anything"

    out = _generate(p, "Stores", target, registry, config)

    assert len(_json_rows(out)) == target


def test_growing_a_json_section_writes_real_rows(tmp_path, registry, config):
    """The rows the old code dropped were empty fixed-width records. The added
    rows must be real JSON objects carrying the section's fields."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "distlabel.json", p)
    original = _json_rows(p)[0]

    out = _generate(p, "Stores", 15, registry, config)
    rows = _json_rows(out)

    assert len(rows) == 15
    for r in rows:
        assert isinstance(r, dict) and set(r) == set(original), r
    assert json.loads(Path(out).read_text(encoding="utf-8"))    # still valid JSON


def test_a_json_section_grows_from_empty(tmp_path, registry, config):
    """The reported shape: the section starts with no rows at all."""
    doc = json.loads((FIX / "distlabel.json").read_text(encoding="utf-8"))
    doc["data"]["header"]["stores"] = []
    p = tmp_path / "f.json"
    p.write_text(json.dumps(doc, indent=4), encoding="utf-8")

    out = _generate(p, "Stores", 3, registry, config)

    assert len(_json_rows(out)) == 3


def test_the_generated_file_reopens(tmp_path, registry, config):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "distlabel.json", p)

    out = _generate(p, "Stores", 15, registry, config)

    view = service.parse_file_view(out, registry, config)
    sec = next(s for s in view["sections"] if s["name"] == "Stores")
    assert len(sec["records"]) == 15


# --------------------------------------------------------------------------- #
# .OK — must be unchanged; it was the engine that already worked
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("target", [3, 15])
def test_an_ok_section_still_reaches_the_requested_count(
        tmp_path, registry, config, target):
    p = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", p)

    out = _generate(p, "Store", target, registry, config)

    assert len(_ok_rows(out, "Store", registry)) == target


def test_an_ok_grown_row_keeps_real_values(tmp_path, registry, config):
    """.OK clones a real line, so a grown row carries values — this is the
    behaviour JSON is measured against."""
    p = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", p)

    out = _generate(p, "Store", 15, registry, config)
    rows = _ok_rows(out, "Store", registry)

    assert len(rows) == 15
    assert rows[-1].get("store"), "a grown .OK row must not be blank"


def test_max_records_still_caps_the_count(tmp_path, registry, config):
    """Growing must not walk past a configured limit — the cap is applied
    before the clone loop, and the loop is what changed."""
    p = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
    limit = config.max_records("StyleHeader", "Lane")
    if limit is None:
        pytest.skip("no max_records configured for StyleHeader/Lane")

    out = _generate(p, "Lane", limit + 5, registry, config)

    assert len(_ok_rows(out, "Lane", registry)) == limit
