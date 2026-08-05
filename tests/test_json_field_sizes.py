"""Calgary JSON fields must be wide enough to hold the values they already hold.

Six fields on two JSON layouts declared a size narrower than the value the real
vendor file carries, so OkGen refused to write back a value it had just read:

    CalgaryCartonLabel Header  chain           2 -> 9   'Winners' / 'HomeSense'
    CalgaryStyleHeader Header  retailPrice     6 -> 9   '7999.99'
    CalgaryStyleHeader Header  price           6 -> 9   '9999.99'
    CalgaryStyleHeader Details retailPrice     6 -> 9   '000799999'
    CalgaryStyleHeader Details compareAtPrice  6 -> 9   '000999999'
    CalgaryStyleHeader Details compareAtUp     1 -> 5   'false'

`chain` is the sharpest: D41 established that Calgary carton labels carry the
chain by NAME, and the editor offers a dropdown of names — every one of which
was too long to save, including the file's own value.

`Field.size` on a JSON layout is a maximum length, not a pad width (D20), so
widening only permits; it cannot change how any existing value is written.

Fixed-width `.OK` layouts are NOT touched — their widths are the file format.
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

# (fixture, section, field, a real value that must be writable)
CASES = [
    ("cartonlabel_minified.json", "Header", "chain", "HomeSense"),
    ("cartonlabel_minified.json", "Header", "chain", "Winners"),
    ("styleheader_fmtB.json", "Header", "retailPrice", "7999.99"),
    ("styleheader_fmtB.json", "Header", "price", "9999.99"),
    ("styleheader_fmtB.json", "Details", "retailPrice", "000799999"),
    ("styleheader_fmtB.json", "Details", "compareAtPrice", "000999999"),
    ("styleheader_fmtB.json", "Details", "compareAtUp", "false"),
]


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


@pytest.mark.parametrize("fixture,section,field,value", CASES)
def test_a_real_value_can_be_written_back(tmp_path, registry, config,
                                          fixture, section, field, value):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    view = service.parse_file_view(p, registry, config)
    sec = next(s for s in view["sections"] if s["name"] == section)
    ri = sec["records"][0]["index"]

    service.apply_edits(p, [{"record_index": ri, "field": field, "value": value}],
                        registry, config=config, backup=False)

    doc = json.loads(p.read_text(encoding="utf-8"))
    node = doc["data"]["header"] if section == "Header" else doc["data"]["details"][0]
    assert node[field] == value


def test_every_value_in_every_sample_fits_its_field(registry, config):
    """The general rule, so a future spec edit cannot reintroduce the class:
    no value in any Calgary sample may exceed its declared size."""
    type_to_layout = {"styleHeaders": "CalgaryStyleHeader",
                      "distributionLabels": "CalgaryDistLabel",
                      "cartonLabels": "CalgaryCartonLabel"}
    sources = sorted(FIX.glob("*.json")) + sorted(
        (Path(config.config_dir) / "templates").glob("Calgary*.json"))
    assert sources, "no Calgary samples found"

    offenders = []
    for path in sources:
        doc = json.loads(path.read_text(encoding="utf-8"))
        layout_name = type_to_layout.get((doc.get("data") or {}).get("type"))
        if not layout_name:
            continue
        layout = registry[layout_name]
        for sec in layout.sections:
            node = doc.get("data") or {}
            for step in (sec.json_path or []):
                node = node.get(step) if isinstance(node, dict) else None
            rows = node if isinstance(node, list) else (
                [node] if isinstance(node, dict) else [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for f in sec.fields:
                    v = row.get(f.name)
                    if f.size and isinstance(v, str) and len(v) > f.size:
                        offenders.append(
                            f"{layout_name}.{sec.name}.{f.name}: size {f.size} "
                            f"but {path.name} holds {v!r} ({len(v)})")
    assert not offenders, "\n".join(sorted(set(offenders)))


def test_ok_layout_widths_are_untouched(registry):
    """The `.OK` widths ARE the file format — a fixed-width field's size decides
    where every later field starts. This change must not have reached them."""
    expected = {
        ("StyleHeader", "Header", "date"): 8,
        ("DistLabels", "Header", "date"): 8,
        ("Preticket", "Header", "date"): 8,
        ("EUPreticket", "Header", "date"): 8,
    }
    for (layout_name, section_name, field_name), size in expected.items():
        sec = next(s for s in registry[layout_name].sections if s.name == section_name)
        f = next(x for x in sec.fields if x.name == field_name)
        assert f.size == size, f"{layout_name}.{field_name} width moved"
