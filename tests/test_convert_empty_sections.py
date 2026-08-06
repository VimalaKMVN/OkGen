"""Converting an .OK file whose section has NO rows must not borrow the
template's rows.

User-reported: deleting every store from a DistLabels or CartonLabel `.OK`,
saving, then converting produced a JSON still carrying stores. They were not
the file's old stores — they were the **vendor sample's**, from the template:

    DistLabels.OK own stores : 0090, 0101, 0102, 0133, 0154, 0173 …
    converted output         : 0100, 0005, 0176, 0166, 0142, 0115 …
    CalgaryDistLabel template: 0100, 0005, 0176, 0166, 0142, 0115 …

D33's rule was "an .OK section with no real rows leaves the template's SINGLE
placeholder row alone — every vendor sample carries exactly one." That premise
holds only for CalgaryStyleHeader, which is where the rule was worked through.
CalgaryDistLabel's template ships **10** real stores and CalgaryCartonLabel's
**5**, so on those two layouts the rule emitted a different order's store
numbers, units and quantities — with `numberOfStores` agreeing, so the document
was internally consistent and nothing downstream would flag it. That is D34's
borrowed-data failure, reaching through the array path instead of the header.

An empty section now emits ONE row with the section's tags and no values — the
same shape the JSON engine writes when a bulk op empties a section, so the two
paths cannot drift.
"""
import json
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(
    not (FIXTURE_CONFIG / "ok_to_json.yaml").is_file(),
    reason="no conversion fixture config")

# (.OK file, its layout, the section holding stores)
CASES = [
    ("DistLabels.OK", "DistLabels", "Store"),
    ("CartonLabel.OK", "CartonLabel", "store"),
]


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _emptied_and_converted(tmp_path, name, layout, section, registry, config):
    """The user's exact flow: delete every store row, save, convert."""
    p = tmp_path / name
    shutil.copy2(DATA_DIR / name, p)
    res = service.bulk_op_apply([str(p)], layout, section,
                                {"type": "keep", "count": 0},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    cres = service.convert_apply([str(p)], registry, config)
    assert cres["errors"] == [], cres["errors"]
    out = sorted(Path(cres["folder"]).glob("*.json"))[0]
    return json.loads(out.read_text(encoding="utf-8"))


def _template_stores(config, layout):
    spec = config.conversion_for(layout)
    tpl = json.loads((Path(config.config_dir) / spec["template"]).read_text())
    return tpl["data"]["header"]["stores"]


# --------------------------------------------------------------------------- #
# The reported bug
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,layout,section", CASES)
def test_no_store_rows_means_no_store_data(tmp_path, registry, config,
                                           name, layout, section):
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    stores = doc["data"]["header"]["stores"]

    assert len(stores) == 1, f"expected one empty row, got {len(stores)}"
    assert all(v is None or not str(v).strip() for v in stores[0].values()), stores[0]


@pytest.mark.parametrize("name,layout,section", CASES)
def test_the_templates_store_numbers_never_reach_the_output(
        tmp_path, registry, config, name, layout, section):
    """The sharpest form of the bug: real store numbers from another order."""
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    emitted = {s.get("store") for s in doc["data"]["header"]["stores"]}
    borrowed = {s.get("store") for s in _template_stores(config, layout)}

    assert borrowed, "template must carry stores for this test to mean anything"
    assert not (emitted & borrowed), f"template stores leaked: {emitted & borrowed}"


@pytest.mark.parametrize("name,layout,section", CASES)
def test_the_store_count_says_zero(tmp_path, registry, config, name, layout, section):
    """The count agreeing with the borrowed rows is what made the document look
    legitimate. One tag-carrying row is not one store."""
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    h = doc["data"]["header"]

    assert h.get("numberOfStores") == "0", h.get("numberOfStores")
    if "storeLines" in h and h["storeLines"] is not None:
        assert h["storeLines"] in ("0", None, " "), h["storeLines"]


@pytest.mark.parametrize("name,layout,section", CASES)
def test_the_kept_row_still_carries_every_field_tag(tmp_path, registry, config,
                                                    name, layout, section):
    """Tags are the reason a row is kept at all — a bare {} would be no better
    than []."""
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    kept = doc["data"]["header"]["stores"][0]
    expected = set(_template_stores(config, layout)[0])

    assert set(kept) == expected


@pytest.mark.parametrize("name,layout,section", CASES)
def test_the_converted_file_still_opens_in_okgen(tmp_path, registry, config,
                                                 name, layout, section):
    doc = _emptied_and_converted(tmp_path, name, layout, section, registry, config)
    assert doc["data"]["header"]                       # parsed as valid JSON


# --------------------------------------------------------------------------- #
# What must NOT change
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,layout,section", CASES)
def test_a_file_that_still_has_stores_converts_them_normally(
        tmp_path, registry, config, name, layout, section):
    """The fix must fire ONLY on an empty section. With rows present the output
    is built from the .OK exactly as before."""
    p = tmp_path / name
    shutil.copy2(DATA_DIR / name, p)
    cres = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(cres["folder"]).glob("*.json"))[0]
    h = json.loads(out.read_text())["data"]["header"]

    assert len(h["stores"]) > 1
    assert any(s.get("store") for s in h["stores"]), "real store data was wiped"
    assert h.get("numberOfStores") == str(len(h["stores"]))


def test_a_null_array_stays_null(tmp_path, registry, config):
    """DistLabel's template has `lanes`/`sizes` as JSON null — not an emptied
    array. Inventing a row would claim a section the layout does not have."""
    doc = _emptied_and_converted(tmp_path, "DistLabels.OK", "DistLabels",
                                 "Store", registry, config)
    h = doc["data"]["header"]

    assert h.get("lanes") is None
    assert h.get("sizes") is None


def test_styleheader_is_unaffected_by_layout(tmp_path, registry, config):
    """StyleHeader has no store section at all, so its stores row was already
    tags-only. It must stay one row and stay empty."""
    p = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
    cres = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(cres["folder"]).glob("*.json"))[0]
    h = json.loads(out.read_text())["data"]["header"]

    assert len(h["stores"]) == 1
    assert all(v is None or not str(v).strip() for v in h["stores"][0].values())


def test_the_report_no_longer_calls_them_placeholders(tmp_path, registry, config):
    """The coverage report DID mention this — as '10 placeholder row(s)', which
    is exactly what made ten real stores read as harmless."""
    p = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", p)
    service.bulk_op_apply([str(p)], "DistLabels", "Store",
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)

    pv = service.convert_preview([str(p)], registry, config)
    rows = [r for r in pv["samples"][0]["report"] if r["field"] == "stores[]"]

    assert rows, "the report must still account for the section"
    assert "placeholder" not in str(rows[0]).lower(), rows[0]
