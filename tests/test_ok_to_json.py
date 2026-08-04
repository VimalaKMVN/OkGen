"""Convert an .OK file into a Calgary JSON file (test-data generation).

Covers: the mapping produces a file OkGen itself detects and opens; provenance
is reported for every field; unmapped fields keep their TEMPLATE value (the
"default to the sample" rule); an empty .OK section leaves the template's single
placeholder row alone; keys stay unique across a batch; the output is SCAN by
folder name; and the source .OK files are never written.
"""
import json
import shutil
from pathlib import Path

import pytest

from okgen import detect, okjson
from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import parse_okfile

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(
    not (FIXTURE_CONFIG / "ok_to_json.yaml").is_file(),
    reason="no conversion fixture config")


@pytest.fixture
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture
def config():
    return Config.load(FIXTURE_CONFIG)


@pytest.fixture
def sources(tmp_path):
    """Three copies of the reference StyleHeader.OK in a working folder."""
    out = []
    for i in range(3):
        p = tmp_path / f"SH_{i}.OK"
        shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
        out.append(str(p))
    return out


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
def test_converted_file_is_a_valid_calgary_styleheader(tmp_path, registry, config, sources):
    res = service.convert_apply(sources, registry, config)
    assert res["written"] == 3 and res["errors"] == []
    out = sorted(Path(res["folder"]).glob("*.json"))
    assert len(out) == 3
    for f in out:
        assert detect.detect_layout(f).layout == "CalgaryStyleHeader"
        okf = parse_okfile(f, registry=registry)
        assert okf.layout.name == "CalgaryStyleHeader"
        # and it round-trips byte-exact, like any other JSON file OkGen opens
        assert okf.to_bytes() == f.read_bytes()


def test_values_come_from_the_ok_file(registry, config):
    doc, _ = _convert(registry, config)
    h = doc["data"]["header"]
    assert h["chain"] == "03"                    # .OK chain, not the template's '04'
    assert h["keytrol"] == "550000"
    assert h["style"] == "244983"
    assert h["department"] == "78"
    assert h["description"] == "0619 2AT10 KCUP PPR 20CT"


def test_transforms(registry, config):
    doc, _ = _convert(registry, config)
    h, d = doc["data"]["header"], doc["data"]["details"][0]
    assert h["transmitDate"] == "2017-07-17"     # 20170717 -> ISO
    assert h["retailPrice"] == "20.99"           # 002099 implied 2dp
    assert h["totalQuantity"] == "22"            # 0000022 zero-stripped
    assert d["retailPrice"] == "000002099"       # detail keeps 9-digit form
    assert d["compareAtUp"] == "false"           # comp_up 'N'
    assert d["ladderPlan"] == "20170801"         # 0817 MMYY -> first of month
    assert d["quantity"] == "0000022"            # raw: padding preserved


def test_details_are_built_from_the_ok_header(registry, config):
    """The reshape: an .OK StyleHeader has NO detail rows, so details[0] is
    assembled from header fields."""
    doc, _ = _convert(registry, config)
    d = doc["data"]["details"][0]
    assert d["messageCode1"] == "MESSAGES01"     # .OK header message1
    assert d["fact1"] == "FACT1FACT1FACT1FACT1"
    assert d["item"] == "ITEMITEMITEMITEMITEM"
    assert d["style"] == "244983"


def test_nested_arrays_come_from_ok_sections(registry, config):
    doc, _ = _convert(registry, config)
    h = doc["data"]["header"]
    assert len(h["lanes"]) == 10                 # .OK Lane section
    assert len(h["sizes"]) == 4                  # .OK Size section
    assert h["sizes"][0]["quantity"] == "2"      # qty 00002 stripped
    assert len(h["stores"]) == 1                 # no .OK store section -> template


def test_unmapped_fields_keep_the_template_value(registry, config):
    """The 'default to the JSON sample' rule — an incomplete mapping must
    degrade to a realistic file, never to a blank where data should be."""
    doc, report = _convert(registry, config)
    template = json.loads(
        (FIXTURE_CONFIG / "templates" / "CalgaryStyleHeader.json").read_text())
    h, th = doc["data"]["header"], template["data"]["header"]
    assert h["purchaseOrderNumber"] == th["purchaseOrderNumber"]
    assert h["headerASNid"] == th["headerASNid"]
    assert h["coordinateIndicator"] == th["coordinateIndicator"]
    assert any(r["provenance"] == "template" for r in report)


def test_every_field_is_reported_with_provenance(registry, config):
    """Nothing is silently invented — the coverage report names each source."""
    doc, report = _convert(registry, config)
    provs = {r["provenance"] for r in report}
    assert {"ok", "derived", "template"} <= provs
    fields = {r["field"] for r in report}
    assert "chain" in fields and "purchaseOrderNumber" in fields
    for r in report:
        assert r["field"] and r["provenance"]


def test_empty_ok_section_keeps_the_template_placeholder(tmp_path, registry, config):
    """Real samples always carry ONE blank row, never []. An .OK file whose Lane
    rows are all blank must not produce ten blank lanes."""
    spec = config.conversion_for("StyleHeader")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "StyleHeader.OK", registry["StyleHeader"])
    for rec in okf.sections()["Lane"]:
        rec.set("lane1", " " * 8)                # blank every lane row
    doc, _ = okjson.convert(okf, registry["StyleHeader"], spec, template)
    assert doc["data"]["header"]["lanes"] == template["data"]["header"]["lanes"]
    assert len(doc["data"]["header"]["lanes"]) == 1


# --------------------------------------------------------------------------- #
# Batch behaviour
# --------------------------------------------------------------------------- #
def test_keys_stay_unique_across_the_batch(registry, config, sources):
    """Three identical .OK files must not produce three identical keys."""
    res = service.convert_apply(sources, registry, config)
    keys = [json.loads(f.read_text())["data"]["header"]["keytrol"]
            for f in sorted(Path(res["folder"]).glob("*.json"))]
    assert keys == ["550000", "550001", "550002"]     # digit width preserved
    assert len(set(keys)) == 3


def test_output_folder_declares_SCAN(registry, config, sources):
    """The folder name is what makes these SCAN files, so `keytrol` is the key
    — no new mechanism, just D27's existing resolution."""
    res = service.convert_apply(sources, registry, config)
    folder = Path(res["folder"])
    assert "SCAN" in folder.name.split("_")
    first = sorted(folder.glob("*.json"))[0]
    assert service.json_source_for(first, config).get("source") == "SCAN"
    assert config.unique_field("CalgaryStyleHeader", "SCAN") == "keytrol"


def test_source_files_are_never_written(registry, config, sources):
    before = {p: Path(p).read_bytes() for p in sources}
    service.convert_apply(sources, registry, config)
    assert all(Path(p).read_bytes() == before[p] for p in sources)


def test_preview_writes_nothing(tmp_path, registry, config, sources):
    before = sorted(p.name for p in Path(sources[0]).parent.iterdir())
    pv = service.convert_preview(sources, registry, config)
    assert len(pv["samples"]) == 3 and pv["total"] == 3
    assert pv["samples"][0]["coverage"]["ok"] > 0
    assert sorted(p.name for p in Path(sources[0]).parent.iterdir()) == before


def test_preview_and_apply_agree(registry, config, sources):
    """What you preview is what gets written (D13)."""
    pv = service.convert_preview(sources, registry, config)
    res = service.convert_apply(sources, registry, config)
    written = sorted(Path(res["folder"]).glob("*.json"))[0].read_text()
    assert json.loads(pv["samples"][0]["preview"]) == json.loads(written)


def test_layout_without_a_target_is_blocked(registry, config):
    """Gating is server-side, not just in the client (the D12 lesson)."""
    sc = service.convert_scope([str(DATA_DIR / "Preticket.OK")], registry, config)
    assert sc["convertible"] == 0
    assert "no JSON target" in sc["blocked"][0]["error"]
    with pytest.raises(service.EditError):
        service.convert_apply([str(DATA_DIR / "Preticket.OK")], registry, config)


def test_scope_reports_target_and_source(registry, config):
    sc = service.convert_scope([str(DATA_DIR / "StyleHeader.OK")], registry, config)
    assert sc["convertible"] == 1
    assert sc["target"] == "CalgaryStyleHeader" and sc["source"] == "SCAN"
    assert sc["mixed"] is False


def _convert(registry, config):
    spec = config.conversion_for("StyleHeader")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "StyleHeader.OK", registry["StyleHeader"])
    return okjson.convert(okf, registry["StyleHeader"], spec, template)
