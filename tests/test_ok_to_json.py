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
    # SIX digits, not nine: a converted file is always a SCAN file, and a SCAN
    # price is 6 characters. The 9-digit form is the WMS one, which conversion
    # never produces (see test_a_converted_price_is_six_digits_not_the_wms_nine).
    assert d["retailPrice"] == "002099"
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
    assert h["coordinateIndicator"] == th["coordinateIndicator"]
    # headerASNid is NOT template-inherited: it decides the source, and a .OK
    # StyleHeader has no ASN field, so the converted file is genuinely SCAN.
    assert h["headerASNid"] is None
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


def test_empty_ok_section_emits_one_empty_row(tmp_path, registry, config):
    """A section with no real rows produces exactly ONE row, carrying the
    section's tags and no values — never ten blank lanes, and never ``[]``.

    Was `..._keeps_the_template_placeholder`: the template's row used to be kept
    verbatim. That is safe only for CalgaryStyleHeader, whose row really is
    blank; CalgaryDistLabel and CalgaryCartonLabel ship 10 and 5 REAL stores,
    so keeping them emitted another order's data (see the module docstring).
    """
    spec = config.conversion_for("StyleHeader")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "StyleHeader.OK", registry["StyleHeader"])
    for rec in okf.sections()["Lane"]:
        rec.set("lane1", " " * 8)                # blank every lane row
    doc, _ = okjson.convert(okf, registry["StyleHeader"], spec, template)

    lanes = doc["data"]["header"]["lanes"]
    assert len(lanes) == 1
    assert set(lanes[0]) == set(template["data"]["header"]["lanes"][0]), \
        "the kept row must carry the same field tags a real row has"
    assert all(v in (None, "") for v in lanes[0].values())


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


# --------------------------------------------------------------------------- #
# DistLabels -> distributionLabels (a different SHAPE from StyleHeader)
# --------------------------------------------------------------------------- #
def _convert_dl(registry, config):
    spec = config.conversion_for("DistLabels")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "DistLabels.OK", registry["DistLabels"])
    return okjson.convert(okf, registry["DistLabels"], spec, template)


@pytest.fixture
def dl_sources(tmp_path):
    out = []
    for i in range(2):
        p = tmp_path / f"DL_{i}.OK"
        shutil.copy2(DATA_DIR / "DistLabels.OK", p)
        out.append(str(p))
    return out


def test_distlabels_converts_to_a_valid_calgary_distlabel(registry, config, dl_sources):
    res = service.convert_apply(dl_sources, registry, config)
    assert res["written"] == 2 and res["errors"] == []
    for f in sorted(Path(res["folder"]).glob("*.json")):
        assert detect.detect_layout(f).layout == "CalgaryDistLabel"
        okf = parse_okfile(f, registry=registry)
        assert okf.to_bytes() == f.read_bytes()          # byte-exact round-trip


def test_distlabels_header_values(registry, config):
    doc, _ = _convert_dl(registry, config)
    h = doc["data"]["header"]
    assert h["chain"] == "01" and h["format"] == "7"
    assert h["keytrol"] == "550034" and h["headerSuffix"] == "AAA"
    assert h["style"] == "304678" and h["department"] == "61"
    assert h["transmitDate"] == "2017-04-04"             # ISO
    assert h["distroDate"] == "20170404"                 # raw 8-digit
    assert h["description"] == "#NAVY LATTICE"
    # retailPrice is SIX digits un-dotted and is HEADER-ONLY. This REVERSES the
    # earlier dotted '16.99' (PLAN D85): it was taken on the user's word over
    # the sample files, and they re-confirmed 6 digits with the data in front of
    # them, so the value agrees with every sample again.
    assert h["retailPrice"] == "001699"
    assert not any("retailPrice" in s for s in h["stores"])
    assert doc["data"]["details"] == []


def test_distlabels_stores_carry_every_ok_row(registry, config):
    doc, _ = _convert_dl(registry, config)
    stores = doc["data"]["header"]["stores"]
    assert len(stores) == 10
    assert stores[0]["store"] == "0090" and stores[0]["cartonSequence"] == "76418"
    assert stores[1]["cartonSequence"] == "08011"        # per-row .OK data
    assert stores[0]["puertoRicoFlag"] == "N"
    assert stores[0]["units"] == "1"                     # 00001 zero-stripped
    assert stores[0]["adDate"] == "0000"


def test_distlabels_shape_differs_from_styleheader(registry, config):
    """lanes/sizes are null (not a placeholder row) and details[] is EMPTY —
    the opposite of styleHeaders on both counts."""
    doc, _ = _convert_dl(registry, config)
    h = doc["data"]["header"]
    assert h["lanes"] is None and h["sizes"] is None
    assert doc["data"]["details"] == []


def test_store_number_pads_to_four(registry, config):
    """A 3-digit store gets a leading zero (user rule)."""
    spec = config.conversion_for("DistLabels")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "DistLabels.OK", registry["DistLabels"])
    okf.sections()["Store"][0].set("store", " 202")
    doc, _ = okjson.convert(okf, registry["DistLabels"], spec, template)
    assert doc["data"]["header"]["stores"][0]["store"] == "0202"


def test_distlabels_keys_unique_across_batch(registry, config, dl_sources):
    res = service.convert_apply(dl_sources, registry, config)
    keys = [json.loads(f.read_text())["data"]["header"]["keytrol"]
            for f in sorted(Path(res["folder"]).glob("*.json"))]
    assert keys == ["550034", "550035"]


def test_layout_max_lengths_are_declared(registry):
    """Sizes supplied by the user for fields the specs left as None. They are a
    VALIDATION change on three existing layouts, so they are pinned here."""
    want = {"puertoRicoFlag": 1, "cartonSequence": 5, "qtyToPrint": 5}
    for name in ("CalgaryDistLabel", "CalgaryStyleHeader", "CalgaryCartonLabel"):
        stores = next(s for s in registry[name].sections if s.name == "Stores")
        got = {f.name: f.size for f in stores.fields if f.name in want}
        assert got == want, f"{name} store sizes: {got}"
    details = next(s for s in registry["CalgaryStyleHeader"].sections
                   if s.name == "Details")
    for fld in ("item", "fact1", "fact2", "fact3"):
        assert next(f for f in details.fields if f.name == fld).size == 20


# --------------------------------------------------------------------------- #
# null_when_blank — present as a field, but JSON null when the .OK has no value
# --------------------------------------------------------------------------- #
def test_unsourced_store_fields_are_null_not_template_values(registry, config):
    """A StyleHeader .OK has no store section, so nothing in the placeholder can
    have come from the file — these must read as null, not carry values borrowed
    from the unrelated order the template came from."""
    doc, _ = _convert(registry, config)
    store = doc["data"]["header"]["stores"][0]
    for fld in ("puertoRicoFlag", "cartonSequence", "qtyToPrint"):
        assert fld in store, f"{fld} must still be PRESENT as a field"
        assert store[fld] is None, f"{fld} should be null, got {store[fld]!r}"


def test_distlabels_keeps_real_store_values(registry, config):
    """puertoRicoFlag, cartonSequence and qtyToPrint all come from the .OK here,
    so they keep their values rather than reading as null."""
    doc, _ = _convert_dl(registry, config)
    store = doc["data"]["header"]["stores"][0]
    assert store["puertoRicoFlag"] == "N"
    assert store["cartonSequence"] == "76418"
    assert store["qtyToPrint"] == store["storeQuantity"] != None


def test_distlabels_qty_to_print_mirrors_store_quantity(registry, config):
    """The .OK store quantity feeds BOTH storeQuantity and qtyToPrint, on every
    row — qtyToPrint used to have no source and always read as null."""
    doc, _ = _convert_dl(registry, config)
    stores = doc["data"]["header"]["stores"]
    assert stores, "the .OK carries store rows"
    for s in stores:
        assert s["qtyToPrint"] == s["storeQuantity"], s


def test_distlabels_qty_to_print_follows_a_changed_store_qty(registry, config):
    """It tracks the .OK value per row, and nulls with it when that row is
    blank — it is a mapped field, not a copy of the template."""
    spec = config.conversion_for("DistLabels")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "DistLabels.OK", registry["DistLabels"])
    rows = okf.sections()["Store"]
    width = len(rows[0].get("store_qty"))
    rows[0].set("store_qty", "7".rjust(width, "0"))
    rows[1].set("store_qty", " " * width)
    doc, _ = okjson.convert(okf, registry["DistLabels"], spec, template)
    out = doc["data"]["header"]["stores"]
    assert out[0]["qtyToPrint"] == out[0]["storeQuantity"] == "7"
    # qtyToPrint is null_when_blank, so a blank .OK quantity reports no value
    # rather than an invented one. (storeQuantity keeps its existing '0'.)
    assert out[1]["qtyToPrint"] is None


def test_blank_ok_value_becomes_null_per_row(registry, config):
    """Blanking a value in the .OK nulls that row only — the others keep data."""
    spec = config.conversion_for("DistLabels")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "DistLabels.OK", registry["DistLabels"])
    okf.sections()["Store"][0].set("prflag", " ")
    okf.sections()["Store"][0].set("cartseq", "     ")
    doc, _ = okjson.convert(okf, registry["DistLabels"], spec, template)
    rows = doc["data"]["header"]["stores"]
    assert rows[0]["puertoRicoFlag"] is None and rows[0]["cartonSequence"] is None
    assert rows[1]["puertoRicoFlag"] == "N" and rows[1]["cartonSequence"] == "08011"


def test_item_and_facts_null_when_the_ok_has_none(registry, config):
    """They carry .OK values when present, and null when not — never the
    template's placeholder text."""
    spec = config.conversion_for("StyleHeader")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "StyleHeader.OK", registry["StyleHeader"])
    hdr = okf.sections()["Header"][0]
    assert okjson.convert(okf, registry["StyleHeader"], spec, template)[0] \
        ["data"]["details"][0]["item"] == "ITEMITEMITEMITEMITEM"
    for fld, width in (("item", 20), ("fact1", 20), ("fact2", 20), ("fact3", 20)):
        hdr.set(fld, " " * width)
    doc, _ = okjson.convert(okf, registry["StyleHeader"], spec, template)
    d = doc["data"]["details"][0]
    for fld in ("item", "fact1", "fact2", "fact3"):
        assert fld in d and d[fld] is None, f"{fld} should be null, got {d[fld]!r}"


# --------------------------------------------------------------------------- #
# pad_zeros — a 3-digit store must be stored as 4 digits on EVERY write path
# --------------------------------------------------------------------------- #
FIXJ = Path(__file__).resolve().parent / "fixtures" / "calgary"


@pytest.fixture
def json_file(tmp_path):
    p = tmp_path / "dl.json"
    shutil.copy2(FIXJ / "distlabel.json", p)
    return p


def _store(p, key="store", row=0):
    return json.loads(p.read_text())["data"]["header"]["stores"][row][key]


def test_single_edit_pads_a_three_digit_store(registry, config, json_file):
    view = service.parse_file_view(str(json_file), registry, config)
    sec = next(s for s in view["sections"] if s["name"] == "Stores")
    idx = sec["records"][0]["index"]
    service.apply_edits(str(json_file),
                        [{"record_index": idx, "field": "store", "value": "202"}],
                        registry=registry, config=config)
    assert _store(json_file) == "0202"


def test_bulk_set_pads_a_three_digit_store(registry, config, json_file):
    service.bulk_op_apply([str(json_file)], "CalgaryDistLabel", "Stores",
                          {"type": "set", "field": "store", "value": "202"},
                          registry, config)
    assert _store(json_file) == "0202"


def test_bulk_list_pads_a_three_digit_store(registry, config, json_file):
    """Bulk must not bypass what single editing enforces (the D30 lesson)."""
    service.bulk_op_apply([str(json_file)], "CalgaryDistLabel", "Stores",
                          {"type": "list", "field": "store", "values": "202"},
                          registry, config)
    assert _store(json_file) == "0202"


def test_blank_store_stays_blank(registry, config, json_file):
    """Padding an empty field to '0000' would invent a store nobody entered."""
    service.bulk_op_apply([str(json_file)], "CalgaryDistLabel", "Stores",
                          {"type": "set", "field": "store", "value": "' '"},
                          registry, config)
    assert _store(json_file) == ""


def test_four_digit_store_is_untouched(registry, config, json_file):
    service.bulk_op_apply([str(json_file)], "CalgaryDistLabel", "Stores",
                          {"type": "set", "field": "store", "value": "0345"},
                          registry, config)
    assert _store(json_file) == "0345"


def test_only_declared_fields_are_padded(registry, config, json_file):
    """`units` is not in pad_zeros, so it must stay exactly as typed."""
    service.bulk_op_apply([str(json_file)], "CalgaryDistLabel", "Stores",
                          {"type": "set", "field": "units", "value": "5"},
                          registry, config)
    assert _store(json_file, "units") == "5"


def test_conversion_also_pads_a_three_digit_store(registry, config):
    """The other half of the ask: a store arriving as 3 digits in the .OK."""
    spec = config.conversion_for("DistLabels")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "DistLabels.OK", registry["DistLabels"])
    okf.sections()["Store"][0].set("store", " 202")
    doc, _ = okjson.convert(okf, registry["DistLabels"], spec, template)
    assert doc["data"]["header"]["stores"][0]["store"] == "0202"


def test_padding_is_digits_only(registry, config, json_file):
    """Non-numeric values must be left EXACTLY as typed — the existing padding
    rules (literal fields, preserved spaces, no zeros on free text) own that
    case on every edit path, and this must not reach into them."""
    service.bulk_op_apply([str(json_file)], "CalgaryDistLabel", "Stores",
                          {"type": "set", "field": "store", "value": "AB"},
                          registry, config)
    assert _store(json_file) == "AB", "a non-numeric store must not become '00AB'"


def _generated_stores(tmp_path, registry, config, values, count=3):
    """Generate `count` files from the distlabel fixture, varying `store` from
    a value list, and return every store number written."""
    src = tmp_path / "dl.json"
    shutil.copy2(FIXJ / "distlabel.json", src)
    spec = {"count": count,
            "detail_fields": [{"section": "Stores", "name": "store",
                               "values": values}]}
    res = service.generate_apply([str(src)], spec, registry, config)
    out = []
    for p in sorted(Path(res["folder"]).glob("*.json")):
        doc = json.loads(p.read_text())
        out += [s["store"] for s in doc["data"]["header"]["stores"]]
    return out


def test_generate_pads_a_three_digit_store_from_a_value_list(
        registry, config, tmp_path):
    """The last write path that bypassed the rule. A random number was already
    zfilled by its own padder, but a value the user LISTED arrived as typed."""
    stores = _generated_stores(tmp_path, registry, config, "202")
    assert stores, "generation produced no store rows to check"
    assert set(stores) == {"0202"}


def test_generate_pads_every_listed_width(registry, config, tmp_path):
    stores = set(_generated_stores(tmp_path, registry, config,
                                   "7, 202, 1234", count=6))
    assert stores <= {"0007", "0202", "1234"}
    assert not any(len(s) != 4 for s in stores), f"unpadded store in {stores}"


def test_generate_padding_is_digits_only(registry, config, tmp_path):
    """Same guard as every other write path — free text must not become 00AB."""
    stores = _generated_stores(tmp_path, registry, config, "AB")
    assert set(stores) == {"AB"}


def test_generate_does_not_pad_an_undeclared_field(registry, config, tmp_path):
    """`units` is not in pad_zeros, so generation must leave it as typed."""
    src = tmp_path / "dl.json"
    shutil.copy2(FIXJ / "distlabel.json", src)
    res = service.generate_apply(
        [str(src)],
        {"count": 2, "detail_fields": [{"section": "Stores", "name": "units",
                                        "values": "5"}]},
        registry, config)
    for p in sorted(Path(res["folder"]).glob("*.json")):
        doc = json.loads(p.read_text())
        assert {s["units"] for s in doc["data"]["header"]["stores"]} == {"5"}


def test_generate_leaves_ok_layouts_alone(registry, config, tmp_path):
    """DistLabels.OK has a `store` field too, but pads by construction — the
    JSON-only rule must not reach it."""
    src = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", src)
    assert config.pad_zero_fields("DistLabels") == set()
    res = service.generate_apply(
        [str(src)],
        {"count": 2, "detail_fields": [{"section": "Store", "name": "store",
                                        "values": "202"}]},
        registry, config)
    for p in sorted(Path(res["folder"]).glob("*.OK")):
        okf = parse_okfile(p, registry=registry)
        assert okf.sections()["Store"][0].get("store") == "0202"   # engine


def test_ok_layouts_are_not_touched_by_pad_zeros(registry, config, tmp_path):
    """.OK store padding was already correct via the fixed-width engine — the
    JSON-only rule must not change it."""
    p = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", p)
    assert config.pad_zero_fields("DistLabels") == set()
    service.bulk_op_apply([str(p)], "DistLabels", "Store",
                          {"type": "set", "field": "store", "value": "202"},
                          registry, config)
    okf = parse_okfile(p, registry=registry)
    assert okf.sections()["Store"][0].get("store") == "0202"   # engine, not us


# --------------------------------------------------------------------------- #
# CartonLabel -> cartonLabels
# --------------------------------------------------------------------------- #
def _convert_cl(registry, config):
    spec = config.conversion_for("CartonLabel")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "CartonLabel.OK", registry["CartonLabel"])
    return okjson.convert(okf, registry["CartonLabel"], spec, template)


@pytest.fixture
def cl_sources(tmp_path):
    out = []
    for i in range(3):
        p = tmp_path / f"CL_{i}.OK"
        shutil.copy2(DATA_DIR / "CartonLabel.OK", p)
        out.append(str(p))
    return out


def test_cartonlabel_converts_to_a_valid_calgary_cartonlabel(registry, config, cl_sources):
    res = service.convert_apply(cl_sources, registry, config)
    assert res["written"] == 3 and res["errors"] == []
    for f in sorted(Path(res["folder"]).glob("*.json")):
        assert detect.detect_layout(f).layout == "CalgaryCartonLabel"
        okf = parse_okfile(f, registry=registry)
        assert okf.to_bytes() == f.read_bytes()


def test_cartonlabel_header_values(registry, config):
    doc, _ = _convert_cl(registry, config)
    h = doc["data"]["header"]
    assert h["pickListPrefix"] == "C:" and h["pickListId"] == "00144"
    assert h["pickListSequence"] == "0002"
    assert h["transmitDate"] == "2019-05-21"      # ISO of the distro date
    assert h["distroDate"] == "20190521"          # raw 8 digits
    assert h["numberOfpacks"] == "38"                 # from the .OK header
    assert h["numberOfStores"] == "91"                # COUNTED from the 91 rows
    assert h["packSize"] == "1"
    assert h["aisle"] == "AAA" and h["slot"] == "003" and h["tier"] == "06"


def test_locator_and_lpn_are_the_same_value(registry, config):
    """Every vendor sample carries them identically."""
    doc, _ = _convert_cl(registry, config)
    h = doc["data"]["header"]
    assert h["locator"] == "0014345" and h["lpn"] == h["locator"]


def test_locator_and_lpn_are_null_when_the_ok_has_none(registry, config):
    """The .OK carries only `locator`, and both JSON fields come from it — so a
    blank one nulls BOTH rather than inheriting the template's bin/LPN, which
    belongs to an unrelated order."""
    spec = config.conversion_for("CartonLabel")
    template = okjson.load_template(spec, Path(config.config_dir))
    tpl_locator = template["data"]["header"]["locator"]
    assert tpl_locator, "the template must carry a locator for this to bite"

    okf = parse_okfile(DATA_DIR / "CartonLabel.OK", registry["CartonLabel"])
    hdr = okf.sections()["Header"][0]
    hdr.set("locator", " " * len(hdr.get("locator")))
    doc, report = okjson.convert(okf, registry["CartonLabel"], spec, template)
    h = doc["data"]["header"]
    for fld in ("locator", "lpn"):
        assert fld in h, f"{fld} must still be PRESENT as a field"
        assert h[fld] is None, f"{fld} should be null, got {h[fld]!r}"
    assert {r["provenance"] for r in report
            if r["field"] in ("locator", "lpn")} == {"null"}


def test_cartonlabel_stores_carry_every_ok_row(registry, config):
    doc, _ = _convert_cl(registry, config)
    stores = doc["data"]["header"]["stores"]
    assert len(stores) == 91                       # the .OK has 91 store rows
    assert stores[0]["store"] == "0003" and stores[0]["cartonSequence"] == "00224"
    assert stores[1]["cartonSequence"] == "00290"  # per-row .OK data
    assert stores[0]["adDate"] == "0531" and stores[0]["suppress"] == "N"
    # storeQuantity mirrors numberOfPacks in every sample
    assert stores[0]["storeQuantity"] == stores[0]["numberOfPacks"] == "1"


def test_cartonlabel_shape_matches_the_samples(registry, config):
    doc, _ = _convert_cl(registry, config)
    h = doc["data"]["header"]
    assert h["lanes"] is None and h["sizes"] is None
    assert doc["data"]["details"] == []


def test_blank_ok_price_is_null_not_a_transformed_zero(registry, config):
    """ret_price is blank in the .OK. Blankness is judged BEFORE the transform,
    else implied_2dp would invent '0.00' where there is no price at all."""
    doc, _ = _convert_cl(registry, config)
    assert doc["data"]["header"]["retailPrice"] is None


def test_cartonlabel_key_is_picklistid_not_keytrol(registry, config, cl_sources):
    """A carton label is identified by pickListId — and by the same field for
    SCAN and WMS alike (keys.yaml)."""
    assert config.unique_field("CalgaryCartonLabel", "SCAN") == "pickListId"
    res = service.convert_apply(cl_sources, registry, config)
    docs = [json.loads(f.read_text()) for f in sorted(Path(res["folder"]).glob("*.json"))]
    ids = [d["data"]["header"]["pickListId"] for d in docs]
    assert ids == ["00144", "00145", "00146"]      # digit width preserved
    assert len({d["data"]["header"]["keytrol"] for d in docs}) == 1   # keytrol untouched


def test_all_three_layouts_are_convertible(registry, config):
    for lay, target in (("StyleHeader", "CalgaryStyleHeader"),
                        ("DistLabels", "CalgaryDistLabel"),
                        ("CartonLabel", "CalgaryCartonLabel")):
        sc = service.convert_scope([str(DATA_DIR / f"{lay}.OK")], registry, config)
        assert sc["convertible"] == 1 and sc["target"] == target
    for lay in ("Preticket", "EUPreticket", "EUStyleHeader", "EUCartonLabel"):
        sc = service.convert_scope([str(DATA_DIR / f"{lay}.OK")], registry, config)
        assert sc["convertible"] == 0, f"{lay} must have no JSON target"


# --------------------------------------------------------------------------- #
# Row counts — computed at conversion from the rows actually emitted
# --------------------------------------------------------------------------- #
def test_cartonlabel_store_count_corrects_a_stale_ok_header(registry, config):
    """The .OK header declares 38 stores while the file carries 91 rows. The
    emitted rows are the truth, which is the point of counting rather than
    copying."""
    doc, report = _convert_cl(registry, config)
    h = doc["data"]["header"]
    assert len(h["stores"]) == 91
    assert h["numberOfStores"] == "91"
    # `storeLines` is deliberately NOT computed (the user's call): conversion
    # leaves it alone whenever the .OK carries data, so it keeps the template's
    # value rather than tracking the rows.
    assert h["storeLines"] is None
    assert any(r["provenance"] == "count" for r in report)


def test_distlabels_store_counts_track_the_rows(registry, config):
    doc, _ = _convert_dl(registry, config)
    h = doc["data"]["header"]
    assert len(h["stores"]) == 10
    assert h["numberOfStores"] == "10"
    assert h["storeLines"] is None      # not computed — see the CartonLabel test


def test_styleheader_counts_details_but_not_the_store_placeholder(registry, config):
    """`stores` is a template placeholder here, not real rows — counting it
    would claim a store that does not exist."""
    doc, _ = _convert(registry, config)
    h = doc["data"]["header"]
    # `lineCount` is not computed either — with a details row present it keeps
    # the template's own value, which on this layout is a single space.
    assert h["lineCount"] == " "
    assert h["numberOfStores"] == " " and h["storeLines"] == " "   # untouched


def test_lane_and_size_counts_are_not_recalculated(registry, config):
    """Excluded for now by the user's call — laneRecords still comes from the
    .OK header, and no size count is written at all."""
    doc, report = _convert(registry, config)
    h = doc["data"]["header"]
    assert len(h["lanes"]) == 10 and len(h["sizes"]) == 4
    assert h["laneRecords"] == "10"                   # from .OK lane_rec, not counted
    counted = {r["field"] for r in report if r["provenance"] == "count"}
    assert "laneRecords" not in counted and not any("size" in c.lower() for c in counted)


def test_counts_follow_the_rows_when_the_ok_changes(registry, config):
    """Deleting .OK store rows must move the count with them."""
    spec = config.conversion_for("DistLabels")
    template = okjson.load_template(spec, Path(config.config_dir))
    okf = parse_okfile(DATA_DIR / "DistLabels.OK", registry["DistLabels"])
    for rec in okf.sections()["Store"][6:]:            # blank out the last 4 rows
        for f in registry["DistLabels"].sections[1].fields:
            rec.set(f.name, " " * (f.size or 1))
    doc, _ = okjson.convert(okf, registry["DistLabels"], spec, template)
    assert len(doc["data"]["header"]["stores"]) == 6
    assert doc["data"]["header"]["numberOfStores"] == "6"


def test_converted_files_are_scan_by_their_own_payload(registry, config):
    """The folder name used to be what made these SCAN while the file carried a
    borrowed ASN from the template — three signals, two of them wrong. Now the
    payload says SCAN, so the folder name is merely a label."""
    from okgen.jsonsource import source_from_header
    for lay in ("StyleHeader", "DistLabels", "CartonLabel"):
        spec = config.conversion_for(lay)
        template = okjson.load_template(spec, Path(config.config_dir))
        okf = parse_okfile(DATA_DIR / f"{lay}.OK", registry[lay])
        doc, _ = okjson.convert(okf, registry[lay], spec, template)
        header = doc["data"]["header"]
        assert header["headerASNid"] is None, lay
        resolved = source_from_header(header, config.json_sources,
                                      config.json_source_default)
        assert resolved.source == "SCAN", lay
        # ...and that is what makes keytrol (not a borrowed ASN) the key.
        assert config.unique_field(spec["target"], "SCAN") == spec["key"]


# --------------------------------------------------------------------------- #
# Keys are unique ACROSS batches, not just within one
# --------------------------------------------------------------------------- #
def test_a_second_batch_does_not_reproduce_the_first_batch_keys(
        tmp_path, registry, config, sources):
    """Each run used to start numbering from scratch, so converting the same
    sources twice produced the same keys again — invisible while the batches sat
    in separate folders, and a pile of duplicates the moment they were merged.
    Keys now start above everything already used nearby (as D13 does for
    volume generation)."""
    runs = [service.convert_apply(sources, registry, config) for _ in range(3)]
    keys = []
    for res in runs:
        keys += [json.loads(f.read_text())["data"]["header"]["keytrol"]
                 for f in sorted(Path(res["folder"]).glob("*.json"))]
    assert len(keys) == 9
    assert len(set(keys)) == 9, f"keys repeated across batches: {keys}"
    assert keys == sorted(keys), "each batch should continue the sequence"


def test_cross_batch_uniqueness_uses_the_right_field_per_layout(
        tmp_path, registry, config):
    """CartonLabel numbers pickListId, not keytrol."""
    srcs = []
    for i in range(2):
        p = tmp_path / f"CL_{i}.OK"
        shutil.copy2(DATA_DIR / "CartonLabel.OK", p)
        srcs.append(str(p))
    ids = []
    for _ in range(2):
        res = service.convert_apply(srcs, registry, config)
        ids += [json.loads(f.read_text())["data"]["header"]["pickListId"]
                for f in sorted(Path(res["folder"]).glob("*.json"))]
    assert ids == ["00144", "00145", "00146", "00147"]


def test_numbering_spaces_stay_separate_from_the_source_ok_files(
        tmp_path, registry, config, sources):
    """A converted CalgaryStyleHeader must not be pushed along by the .OK
    StyleHeader it came from — different layouts, separate key spaces (D14)."""
    res = service.convert_apply(sources, registry, config)
    keys = [json.loads(f.read_text())["data"]["header"]["keytrol"]
            for f in sorted(Path(res["folder"]).glob("*.json"))]
    assert keys[0] == "550000", "started above the .OK file's own key space"
