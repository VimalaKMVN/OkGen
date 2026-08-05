"""Calgary JSON engine (3rd parse/serialize mode) — Stage 1.

Covers: registry loads the 3 Calgary layouts, detection by ``data.type``,
the rendered view (sections/fields/records) reusing the existing pipeline,
byte-exact round-trip on an untouched save (pretty AND minified files), and a
single-field edit that changes only that field's value, reads back, and stays
byte-exact everywhere else. Fixtures are real vendor samples.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.detect import detect_layout
from okgen.jsonengine import scan_spans
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(
    os.environ.get("OKGEN_DATA_DIR",
                   str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions")))
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

CASES = {
    "styleheader_fmtB.json": "CalgaryStyleHeader",
    "styleheader_fmtS.json": "CalgaryStyleHeader",
    "styleheader_fmtT.json": "CalgaryStyleHeader",
    "cartonlabel_minified.json": "CalgaryCartonLabel",
    "distlabel.json": "CalgaryDistLabel",
}
ALL_JSON = sorted(CASES)

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="no calgary fixtures")


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def test_registry_loads_three_calgary_layouts(registry):
    for name in ("CalgaryStyleHeader", "CalgaryCartonLabel", "CalgaryDistLabel"):
        lay = registry.get(name)
        assert lay is not None and lay.json_mode is True
        assert lay.sections[0].name == "Header" and lay.sections[0].json_kind == "object"


@pytest.mark.parametrize("fname,expected", sorted(CASES.items()))
def test_detection_by_data_type(fname, expected):
    assert detect_layout(FIX / fname).layout == expected


@pytest.mark.parametrize("fname", ALL_JSON)
def test_view_has_header_and_records(fname, registry, config):
    view = service.parse_file_view(FIX / fname, registry, config)
    assert view["layout"] == CASES[fname]
    names = [s["name"] for s in view["sections"]]
    assert names[0] == "Header"
    hdr = view["sections"][0]
    assert hdr["records"] and hdr["fields"]
    # `type` is editable now (all three document words); an out-of-set value
    # is refused by _assert_layout_stable rather than by a spec flag
    tfield = next(f for f in hdr["fields"] if f["name"] == "type")
    assert tfield["editable"] is True
    # header carries a real value read from data.header
    assert any(v for v in hdr["records"][0]["values"].values())


@pytest.mark.parametrize("fname", ALL_JSON)
def test_untouched_save_is_byte_exact(fname, tmp_path, registry, config):
    """Open + Save As with no edits reproduces the file byte-for-byte — pretty
    and minified alike (the D3 guarantee, JSON edition)."""
    src = tmp_path / fname
    shutil.copy2(FIX / fname, src)
    out = tmp_path / "out.json"
    res = service.apply_edits(src, [], registry, target_path=str(out), config=config)
    assert res["roundtrip_ok"]
    assert out.read_bytes() == (FIX / fname).read_bytes()


@pytest.mark.parametrize("fname", ALL_JSON)
def test_single_field_edit_is_surgical(fname, tmp_path, registry, config):
    """Editing one header field changes ONLY that field's value: it reads back,
    every other scalar in the document is byte-identical, and reverting the edit
    reproduces the original bytes."""
    src = tmp_path / fname
    shutil.copy2(FIX / fname, src)
    original = src.read_bytes()
    view = service.parse_file_view(src, registry, config)
    hdr = view["sections"][0]
    rec_index = hdr["records"][0]["index"]
    hvals = hdr["records"][0]["values"]
    # a non-empty string field with room, so reverting restores the exact bytes
    # (a null field shows as "" and reverting to "" would write "" != null).
    # Temporal fields are excluded: they only accept a parseable instant, so
    # "AB1" is (correctly) refused there — they have their own tests.
    target = next(f for f in hdr["fields"]
                  if f["editable"] and not f["options"]
                  and (f["size"] is None or f["size"] >= 3)
                  and not config.date_format(view["layout"], f["name"])
                  and hvals[f["name"]].strip() != "")
    orig_val = hvals[target["name"]]

    service.apply_edits(src, [{"section_index": 0, "record_index": rec_index,
                               "field": target["name"], "value": "AB1"}],
                        registry, config=config, backup=False)
    v2 = service.parse_file_view(src, registry, config)
    h2 = v2["sections"][0]
    assert h2["records"][0]["values"][target["name"]] == "AB1"      # reads back

    # every OTHER header field unchanged
    for f in hdr["fields"]:
        if f["name"] == target["name"]:
            continue
        assert (h2["records"][0]["values"][f["name"]]
                == hdr["records"][0]["values"][f["name"]]), f"{f['name']} moved"

    # revert -> byte-identical to the original file
    service.apply_edits(src, [{"section_index": 0, "record_index": rec_index,
                               "field": target["name"], "value": orig_val}],
                        registry, config=config, backup=False)
    assert src.read_bytes() == original


@pytest.mark.parametrize("fname", ALL_JSON)
def test_width_validation_reused(fname, tmp_path, registry, config):
    """A value longer than a field's max-length is rejected (not truncated),
    reusing the existing width guard."""
    src = tmp_path / fname
    shutil.copy2(FIX / fname, src)
    view = service.parse_file_view(src, registry, config)
    hdr = view["sections"][0]
    small = next((f for f in hdr["fields"]
                  if f["editable"] and not f["options"] and f["size"] and f["size"] <= 3), None)
    if small is None:
        pytest.skip("no small bounded field in this layout's header")
    with pytest.raises(service.EditError):
        service.apply_edits(src, [{"section_index": 0,
                                   "record_index": hdr["records"][0]["index"],
                                   "field": small["name"], "value": "X" * (small["size"] + 5)}],
                            registry, config=config, backup=False)


def test_tree_lists_json_files(registry, config):
    """Calgary JSON files appear in the file tree with the ``json`` flag, the
    detected layout, and chain resolved whether it is a code or a name."""
    tree = service.build_tree(FIX, config, registry)
    files = {c["name"]: c for c in tree["children"] if c["type"] == "file"}
    assert set(CASES).issubset(files), "JSON files missing from tree"
    for fname, layout in CASES.items():
        node = files[fname]
        assert node["json"] is True and node["layout"] == layout
    # chain resolves from a NAME ("Winners") and a code ("06") alike
    assert files["cartonlabel_minified.json"]["chain_info"] is not None    # name "Winners"
    assert files["distlabel.json"]["chain_info"] is not None               # code "06"
    # the layout-spec files themselves never leak into the tree
    assert not any(n.endswith(".layout.json") for n in files)


def test_key_fields_configured(config):
    """The Calgary unique keys: ASN ID for StyleHeader/DistLabel, pickListId for
    CartonLabel (per the vendor rules)."""
    assert config.unique_field("CalgaryStyleHeader") == "headerASNid"
    assert config.unique_field("CalgaryDistLabel") == "headerASNid"
    assert config.unique_field("CalgaryCartonLabel") == "pickListId"


def _hkey(path, field):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]["header"][field]


@pytest.mark.parametrize("fname,keyfield", [
    ("styleheader_fmtB.json", "headerASNid"),
    ("distlabel.json", "headerASNid"),
    ("cartonlabel_minified.json", "pickListId"),
])
def test_make_unique_rekeys_json_duplicates(fname, keyfield, tmp_path, registry, config):
    """Make Unique re-keys a duplicate JSON file: the first keeps its key, the
    second is renumbered to a distinct value, and both stay valid, detectable,
    byte-exact JSON."""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    shutil.copy2(FIX / fname, a)
    shutil.copy2(FIX / fname, b)                       # identical -> duplicate key
    assert _hkey(a, keyfield) == _hkey(b, keyfield)

    res = service.make_unique_in_folder(tmp_path, registry, config, backup=False)
    rekeyed = [r for r in res["rekeyed"] if r.get("field") == keyfield]
    assert rekeyed, f"no re-key happened: {res['rekeyed']}"

    ka, kb = _hkey(a, keyfield), _hkey(b, keyfield)
    assert ka != kb, "keys still collide after Make Unique"
    # renumbered value keeps any literal prefix/suffix (ASN 'V...A')
    if not keyfield == "pickListId":
        assert kb[0] == ka[0] and kb[-1] == ka[-1]
    # both files remain valid, detectable, round-trippable
    for f in (a, b):
        assert service.detect_layout(f).layout is not None
        assert service.parse_file_view(f, registry, config)["roundtrip_ok"]


@pytest.mark.parametrize("fname", ALL_JSON)
def test_raw_view_is_pretty_but_file_stays_byte_exact(fname, tmp_path, registry, config):
    """The Raw tab always shows pretty (multi-line) JSON — even for a minified
    file — for readability, WITHOUT changing what's on disk: an untouched save
    is still byte-identical (the reformat is display-only)."""
    src = tmp_path / fname
    shutil.copy2(FIX / fname, src)
    view = service.parse_file_view(src, registry, config)
    lines = view["raw_text"].count("\n") + 1
    assert lines > 5, f"{fname}: raw view not pretty-printed ({lines} lines)"
    assert view["raw_text"].lstrip().startswith("{")
    # saving does NOT adopt the pretty formatting — disk stays byte-exact
    out = tmp_path / "out.json"
    service.apply_edits(src, [], registry, target_path=str(out), config=config)
    assert out.read_bytes() == (FIX / fname).read_bytes()


def test_scan_spans_matches_real_values():
    """The span scanner maps every scalar to its exact source token."""
    for fname in ALL_JSON:
        text = (FIX / fname).read_text(encoding="utf-8")
        data = json.loads(text)
        spans = scan_spans(text)
        checked = 0
        for path, (s, e) in spans.items():
            cur = data
            ok = True
            for p in path:
                cur = cur[p] if (isinstance(cur, list) or p in cur) else None
                if cur is None and p not in (path[-1],):
                    ok = False
                    break
            if not ok:
                continue
            assert json.loads(text[s:e]) == cur, f"{fname} {path}"
            checked += 1
        assert checked > 20, f"{fname}: only {checked} scalars scanned"
