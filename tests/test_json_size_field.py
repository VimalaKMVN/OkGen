"""A Calgary StyleHeader's `size` declares a width, so every path can write it.

`CalgaryStyleHeader.Sizes.size` declared NO size at all, because the spec was
built from vendor samples and every one of them carries `"size": null` — there
was no value to measure. A JSON `Field.size` is a maximum length, not a pad
width (D20/D48), so "no size" is not "unlimited": it is *undeclared*, and the
three write paths each answered that differently.

    panel label      `size (?)`   — the descriptor carried `size: null`
    Bulk Edit        REFUSED      — "size has no fixed width"
    Volume Generate  SILENTLY SKIPPED — reported `written: 1`, wrote nothing;
                                   the field was not even LISTED in the panel
    single editor    accepted ANY length, no cap whatsoever

The width is **6**, matching the `.OK` StyleHeader's own `Size.size` — the same
field in the fixed-width engine, where a size IS the format. That is asserted
here rather than assumed, so the two engines cannot drift apart silently.

Note what this deliberately tightens: the editor used to save a 7+ character
size, alone among the paths. It is refused now, which is the point — a value
the `.OK` side could never hold should not be reachable from the JSON side, and
a field accepted in one panel and refused in another reads as a bug (D63).
Refused, never truncated (D40): an over-long value leaves the file untouched.

Volume Generate is the case that matters most and the reason this is a defect
rather than a papercut — it reported success while dropping the value, so a
whole generated batch would carry `size: null` with nothing on screen saying so
(the D28/D43/D47 silent-no-op class, in the path that CREATES files).
"""
import json
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.detect import detect_layout
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="no calgary fixtures")

LAYOUT = "CalgaryStyleHeader"
SAMPLES = ["styleheader_fmtB.json", "styleheader_fmtS.json", "styleheader_fmtT.json"]
SIZE_WIDTH = 6
FITS = ["S", "MED", "XSMALL", "123456"]      # 1, 3, 6, 6 characters
TOO_LONG = ["XXLARGE", "1234567"]            # 7 characters


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _copy(tmp_path, sample):
    p = tmp_path / sample
    shutil.copy2(FIX / sample, p)
    return p


def _with_data(path, layout, section, registry, config):
    """Ensure `section` holds data, so a WIDTH check measures the width.

    Since v0.116.0 a field op on a dataless section is skipped by design, so on
    a section that ships blank the check would be measuring the skip instead.
    Rows are added where the seed produces real values; where it does not
    (`CalgaryStyleHeader.Lanes` seeds `lane: ""`), the single editor is used —
    it still writes into a placeholder, which is the documented way out.
    """
    okf = service.parse_okfile(path, registry=registry)
    sec = next((x for x in okf.layout.sections if x.name == section), None)
    if sec is None or service._section_has_data(okf, sec, config):
        return path
    service.bulk_op_apply([str(path)], layout, section,
                          {"type": "add", "count": 1}, registry, config,
                          backup=False)
    okf = service.parse_okfile(path, registry=registry)
    sec = next(x for x in okf.layout.sections if x.name == section)
    if service._section_has_data(okf, sec, config):
        return path
    # Still blank: its seed is blank too. Type a value in, as a user would.
    ri = next(r.index for r in okf.records if r.section.name == section)
    service.apply_edits(path, [{"record_index": ri,
                                "field": sec.fields[0].name, "value": "x"}],
                        registry, config=config, backup=False)
    return path


def _sizes(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]["header"]["sizes"]


def _size_record_index(path, registry, config):
    view = service.parse_file_view(path, registry, config)
    sec = next(s for s in view["sections"] if s["name"] == "Sizes")
    return sec["records"][0]["index"]


# --------------------------------------------------------------- the width

def test_ok_styleheader_size_is_six_characters(registry):
    """The source of the number. If the `.OK` layout ever changes width, this
    fails and the JSON declaration must be revisited with it."""
    layout = registry["StyleHeader"]
    sec = next(s for s in layout.sections if s.name == "Size")
    field = next(f for f in sec.fields if f.name == "size")
    assert field.size == SIZE_WIDTH


def test_json_size_declares_the_same_width(registry):
    layout = registry[LAYOUT]
    sec = next(s for s in layout.sections if s.name == "Sizes")
    field = next(f for f in sec.fields if f.name == "size")
    assert field.size == SIZE_WIDTH


# ------------------------------------------------- what the panels are told

def test_bulk_edit_shows_a_width_not_a_question_mark(registry, config):
    """`size: null` in the descriptor is what the panel renders as `size (?)`."""
    scope = service.bulk_scope([str(FIX / SAMPLES[0])], registry, config)
    sec = next(s for s in scope["detail_sections"][LAYOUT] if s["name"] == "Sizes")
    field = next(f for f in sec["fields"] if f["name"] == "size")
    assert field["size"] == SIZE_WIDTH


def test_volume_generate_offers_the_field_at_all(registry, config):
    """Generate did not merely mislabel `size` — it OMITTED it, and a field
    left out reads as "OkGen forgot it" rather than "you may not set it" (D61)."""
    scope = service.generate_scope([str(FIX / SAMPLES[0])], registry, config)
    sec = next(s for s in scope["sections"] if s["name"] == "Sizes")
    names = [f["name"] for f in sec["fields"]]
    assert "size" in names
    field = next(f for f in sec["fields"] if f["name"] == "size")
    assert field["size"] == SIZE_WIDTH


# ------------------------------------------------- every path can write it

@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("value", FITS)
def test_editor_writes_size(tmp_path, registry, config, sample, value):
    p = _copy(tmp_path, sample)
    ri = _size_record_index(p, registry, config)
    service.apply_edits(p, [{"record_index": ri, "field": "size", "value": value}],
                        registry, config=config, backup=False)
    assert _sizes(p)[0]["size"] == value


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("value", FITS)
def test_bulk_rows_and_sequences_writes_size(tmp_path, registry, config,
                                             sample, value):
    p = _copy(tmp_path, sample)
    res = service.bulk_op_apply([str(p)], LAYOUT, "Sizes",
                                {"type": "set", "field": "size", "value": value},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "changed", res["results"][0]
    assert _sizes(p)[0]["size"] == value


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("value", FITS)
def test_bulk_field_values_writes_size(tmp_path, registry, config, sample, value):
    p = _copy(tmp_path, sample)
    res = service.bulk_multi_apply(
        [str(p)], LAYOUT,
        [{"section": "Sizes", "type": "list", "field": "size", "values": [value]}],
        registry, config, backup=False)
    assert res["results"][0]["status"] == "changed", res["results"][0]
    assert _sizes(p)[0]["size"] == value


@pytest.mark.parametrize("value", FITS)
def test_volume_generate_writes_size(tmp_path, registry, config, value):
    """The silent skip: this reported `written: 1` and produced `size: null`."""
    p = _copy(tmp_path, SAMPLES[1])
    spec = {"count": 2, "folder": str(tmp_path / "out"),
            "detail_fields": [{"section": "Sizes", "name": "size",
                               "type": "list", "values": [value]}]}
    res = service.generate_apply([str(p)], spec, registry, config)
    made = sorted(Path(res["folder"]).glob("*.json"))
    assert len(made) == 2, res
    for m in made:
        assert _sizes(m)[0]["size"] == value


def test_generate_reporting_matches_what_it_wrote(tmp_path, registry, config):
    """A success report with nothing written is the failure this closes."""
    p = _copy(tmp_path, SAMPLES[1])
    spec = {"count": 1, "folder": str(tmp_path / "out"),
            "detail_fields": [{"section": "Sizes", "name": "size",
                               "type": "list", "values": ["MED"]}]}
    res = service.generate_apply([str(p)], spec, registry, config)
    assert res["written"] == 1
    made = sorted(Path(res["folder"]).glob("*.json"))
    assert [_sizes(m)[0]["size"] for m in made] == ["MED"]


# ------------------------------------- over-long is refused, never truncated

@pytest.mark.parametrize("value", TOO_LONG)
def test_editor_refuses_an_over_long_size(tmp_path, registry, config, value):
    p = _copy(tmp_path, SAMPLES[1])
    ri = _size_record_index(p, registry, config)
    before = p.read_bytes()
    with pytest.raises(service.EditError) as exc:
        service.apply_edits(p, [{"record_index": ri, "field": "size",
                                 "value": value}],
                            registry, config=config, backup=False)
    assert "size" in str(exc.value)
    assert p.read_bytes() == before          # refused, not truncated (D40)


@pytest.mark.parametrize("value", TOO_LONG)
def test_bulk_refuses_an_over_long_size(tmp_path, registry, config, value):
    p = _copy(tmp_path, SAMPLES[1])
    before = p.read_bytes()
    res = service.bulk_multi_apply(
        [str(p)], LAYOUT,
        [{"section": "Sizes", "type": "list", "field": "size", "values": [value]}],
        registry, config, backup=False)
    entry = res["results"][0]
    assert entry["status"] == "error"
    assert str(SIZE_WIDTH) in entry["error"] and "size" in entry["error"]
    assert p.read_bytes() == before


def test_the_refusal_names_the_width(tmp_path, registry, config):
    """A user given a limit can act on it; "has no fixed width" could not be."""
    p = _copy(tmp_path, SAMPLES[1])
    res = service.bulk_multi_apply(
        [str(p)], LAYOUT,
        [{"section": "Sizes", "type": "list", "field": "size",
          "values": ["XXLARGE"]}], registry, config, backup=False)
    msg = res["results"][0]["error"]
    assert "no fixed width" not in msg
    assert "too long" in msg and f"({SIZE_WIDTH})" in msg


# ------------------------------------------------------------- the boundary

def test_exactly_six_characters_is_accepted(tmp_path, registry, config):
    p = _copy(tmp_path, SAMPLES[1])
    ri = _size_record_index(p, registry, config)
    service.apply_edits(p, [{"record_index": ri, "field": "size",
                             "value": "XSMALL"}],
                        registry, config=config, backup=False)
    assert _sizes(p)[0]["size"] == "XSMALL"


def test_every_sample_size_value_still_fits(registry):
    """Widening cannot be claimed safe without checking what the files hold.
    Every shipped sample carries `size: null`, so nothing is squeezed."""
    checked = 0
    for path in sorted(FIX.glob("styleheader*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for row in (doc["data"]["header"].get("sizes") or []):
            val = row.get("size")
            checked += 1
            if isinstance(val, str):
                assert len(val) <= SIZE_WIDTH, f"{path.name}: {val!r}"
    assert checked, "no size rows found — the assertion would be vacuous"


# ---------------------------------------------- the same class, four more fields

# `v0.97.0` declared `Sizes.size` and fixed ONE field. The class remained: any
# Calgary field with no declared width is refused by Bulk Edit ("has no fixed
# width"), omitted from the Volume Generate panel, and SILENTLY SKIPPED by
# Generate through the API — while the single editor accepts it. 44 fields were
# in that state. These four are declared at the user's widths; the rest stay
# open because their widths are not knowable from the samples (see PLAN §6).
_SAMPLE_FOR = {"CalgaryStyleHeader": "styleheader_fmtS.json",
               "CalgaryDistLabel": "distlabel.json",
               "CalgaryCartonLabel": "cartonlabel_minified.json"}

MORE = [
    ("CalgaryStyleHeader", "Lanes", "lane", 8),
    ("CalgaryStyleHeader", "Details", "pageNumber", 3),
    ("CalgaryStyleHeader", "Details", "lineNumber", 3),
    ("CalgaryStyleHeader", "Details", "ladderPlan", 8),
    ("CalgaryStyleHeader", "Header", "locator", 20),
    ("CalgaryDistLabel", "Header", "locator", 20),
    ("CalgaryCartonLabel", "Header", "locator", 20),
]


@pytest.mark.parametrize("layout,section,field,width", MORE)
def test_the_width_is_declared(registry, layout, section, field, width):
    lay = registry[layout]
    sec = next(s for s in lay.sections if s.name == section)
    f = next(x for x in sec.fields if x.name == field)
    assert f.size == width


def test_json_locator_is_twenty_and_the_OK_one_stays_seven(registry):
    """`locator` is 7 on the FIXED-WIDTH CartonLabel and 20 on all three JSON
    layouts. Same name, different field — and the difference is load-bearing:
    the Calgary carton label's own sample carries a 15-character value, so 7
    would have made OkGen refuse a value it had just read (D48). 20 matches
    `lpn`, which the JSON locator mirrors, and is asserted against it here so
    the two cannot drift.
    """
    for lay in ("CalgaryStyleHeader", "CalgaryDistLabel", "CalgaryCartonLabel"):
        sec = next(s for s in registry[lay].sections if s.name == "Header")
        assert next(x for x in sec.fields if x.name == "locator").size == 20

    # ...and the fixed-width one is untouched: there a size IS the format.
    sec = next(s for s in registry["CartonLabel"].sections if s.name == "Header")
    assert next(x for x in sec.fields if x.name == "locator").size == 7


def test_the_json_locator_matches_lpn(registry):
    """The user's reasoning — in JSON a locator is usually the LPN — so if one
    ever moves the other should be revisited with it."""
    for lay in ("CalgaryDistLabel", "CalgaryCartonLabel"):
        sec = next(s for s in registry[lay].sections if s.name == "Header")
        lpn = next(x for x in sec.fields if x.name == "lpn").size
        loc = next(x for x in sec.fields if x.name == "locator").size
        assert loc == lpn == 20


def test_the_carton_labels_own_15_char_locator_still_saves(tmp_path, registry, config):
    """The value that made 7 impossible. It must round-trip, not merely fit."""
    p = _copy(tmp_path, "cartonlabel_minified.json")
    doc = json.loads(Path(p).read_text(encoding="utf-8"))
    original = doc["data"]["header"]["locator"]
    assert len(str(original)) == 15, original
    res = service.bulk_multi_apply(
        [str(p)], "CalgaryCartonLabel",
        [{"section": "Header", "type": "list", "field": "locator",
          "values": [str(original)]}], registry, config, backup=False)
    assert res["results"][0]["status"] in ("changed", "unchanged"), res["results"][0]
    after = json.loads(Path(p).read_text(encoding="utf-8"))["data"]["header"]["locator"]
    assert str(after) == str(original)


def test_every_declared_width_holds_every_sample_value(registry):
    """D48's rule, re-run for the new declarations: no shipped sample or
    template may carry a value that its own declared size refuses."""
    import glob
    widths = {(lay, field): w for lay, _sec, field, w in MORE}
    checked = 0
    for path in sorted(glob.glob(str(FIX / "*.json"))) + \
            sorted(glob.glob(str(FIXTURE_CONFIG / "templates" / "Calgary*.json"))):
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        layout = {"styleHeaders": "CalgaryStyleHeader",
                  "distributionLabels": "CalgaryDistLabel",
                  "cartonLabels": "CalgaryCartonLabel"}.get((doc.get("data") or {}).get("type"))
        if not layout:
            continue

        def walk(node):
            nonlocal checked
            if isinstance(node, dict):
                for k, v in node.items():
                    w = widths.get((layout, k))
                    if w is not None and not isinstance(v, (dict, list)):
                        s = "" if v is None else str(v)
                        assert len(s) <= w, f"{Path(path).name}: {k}={s!r} exceeds {w}"
                        checked += 1
                    walk(v)
            elif isinstance(node, list):
                for x in node:
                    walk(x)
        walk(doc)
    assert checked, "no values checked — the assertion would be vacuous"


@pytest.mark.parametrize("layout,section,field,width", MORE)
def test_bulk_edit_can_now_write_it(tmp_path, registry, config,
                                    layout, section, field, width):
    """It used to answer `<field> has no fixed width` — offered in the panel,
    then refused on apply, which is exactly what was reported for `size`."""
    p = _with_data(_copy(tmp_path, _SAMPLE_FOR[layout]), layout, section,
                   registry, config)
    val = "7" * width
    res = service.bulk_multi_apply(
        [str(p)], layout,
        [{"section": section, "type": "list", "field": field, "values": [val]}],
        registry, config, backup=False)
    entry = res["results"][0]
    assert entry["status"] == "changed", entry
    assert "no fixed width" not in str(entry)


@pytest.mark.parametrize("layout,section,field,width", MORE)
def test_volume_generate_offers_AND_writes_it(tmp_path, registry, config,
                                              layout, section, field, width):
    """Both halves. It was omitted from the panel (so it read as forgotten) and
    silently skipped by the API path while reporting success."""
    p = _with_data(_copy(tmp_path, _SAMPLE_FOR[layout]), layout, section,
                   registry, config)
    scope = service.generate_scope([str(p)], registry, config)
    if section == "Header":
        offered = [x["name"] for x in scope["header_fields"]]
    else:
        offered = [x["name"] for s in scope["sections"] if s["name"] == section
                   for x in s["fields"]]
    assert field in offered, f"{field} not offered by the Generate panel"

    val = "5" * width
    key = "header_fields" if section == "Header" else "detail_fields"
    entry = {"name": field, "type": "list", "values": [val]}
    if section != "Header":
        entry["section"] = section
    res = service.generate_apply(
        [str(p)], {"count": 1, "folder": str(tmp_path / "out"), key: [entry]},
        registry, config)
    made = sorted(Path(res["folder"]).glob("*.json"))
    assert len(made) == 1
    doc = json.loads(made[0].read_text(encoding="utf-8"))["data"]
    node = (doc["header"]["lanes"][0] if section == "Lanes"
            else doc["details"][0] if section == "Details" else doc["header"])
    assert node.get(field) == val, "reported success but did not write the value"


# --------------------------------------------------- the temporal fields

# `timestamp` (all three Calgary layouts) and `date` on the DistLabel store row
# are declared 30. Unlike `lane`/`locator` these were NEVER broken — a temporal
# field carries a date FORMAT, and both the Generate skip (`not f.size and not
# dfmt`) and the bulk write path let a formatted field through without a width.
# The only fault was the panel rendering `timestamp (?)`.
#
# That makes the width a NEW CAP on a path that had none, which is why it is 30
# and not the 29 first proposed: the samples carry BOTH forms, and the
# Z-terminated one is the majority.
#
#     2026-05-01T10:44:53.161740778     29 — CalgaryCartonLabel, no Z
#     2026-02-10T14:47:45.394277988Z    30 — DistLabel + StyleHeader, with Z
#
# 29 would have refused 26 of the 28 real values in the shipped samples and
# templates — D48 again, and the third time in this session that a proposed
# width was shorter than the data it had to hold.
TEMPORAL = [
    ("CalgaryStyleHeader", "Header", "timestamp"),
    ("CalgaryDistLabel", "Header", "timestamp"),
    ("CalgaryCartonLabel", "Header", "timestamp"),
    ("CalgaryDistLabel", "Stores", "date"),
    ("CalgaryStyleHeader", "Stores", "date"),
    ("CalgaryCartonLabel", "Stores", "date"),
]
RFC3339_NANO_Z = "2026-03-04T05:06:07.123456789Z"      # 30
RFC3339_NANO = "2026-05-01T10:44:53.161740778"         # 29


@pytest.mark.parametrize("layout,section,field", TEMPORAL)
def test_temporal_width_is_thirty(registry, layout, section, field):
    sec = next(s for s in registry[layout].sections if s.name == section)
    assert next(f for f in sec.fields if f.name == field).size == 30


def test_thirty_is_what_the_Z_FORM_needs():
    """The number is not arbitrary: it is the length of the form the vendor
    actually sends. If this ever reads 29 again, 26 shipped values break."""
    assert len(RFC3339_NANO_Z) == 30
    assert len(RFC3339_NANO) == 29


@pytest.mark.parametrize("layout,section,field", TEMPORAL)
def test_both_stamp_forms_are_accepted_and_stored_canonically(
        tmp_path, registry, config, layout, section, field):
    """Both forms save — and a temporal field NORMALISES on write (D29:
    forgiving input, exact output), so the no-Z form is stored WITH the Z.

    That is the sharpest argument for 30 over 29: the canonical output is
    always 30 characters, so even a file whose sample carries the 29-char form
    becomes 30 the moment anyone edits it. A 29 cap would have refused the
    field's own normalised value.
    """
    p = _copy(tmp_path, _SAMPLE_FOR[layout])
    okf = service.parse_okfile(p, registry=registry)
    ri = next(r.index for r in okf.records if r.section.name == section)
    for stamp in (RFC3339_NANO_Z, RFC3339_NANO):
        service.apply_edits(p, [{"record_index": ri, "field": field,
                                 "value": stamp}],
                            registry, config=config, backup=False)
        okf2 = service.parse_okfile(p, registry=registry)
        stored = next(r for r in okf2.records
                      if r.section.name == section).get(field)
        assert stored.startswith(stamp.rstrip("Z")), (
            f"{layout}.{field}: {stamp!r} -> {stored!r}")
        assert len(stored) <= 30
    # the canonical form is the 30-char one, which is what makes 29 impossible
    assert len(stored) == 30


@pytest.mark.parametrize("layout,section,field", TEMPORAL)
def test_the_panel_shows_a_width_not_a_question_mark(registry, config,
                                                     layout, section, field):
    """The actual report — these fields worked, they just read as `(?)`."""
    scope = service.bulk_scope([str(FIX / _SAMPLE_FOR[layout])], registry, config)
    if section == "Header":
        fd = next(f for f in scope["header_fields"][layout] if f["name"] == field)
    else:
        fd = next(f for s in scope["detail_sections"][layout]
                  if s["name"] == section
                  for f in s["fields"] if f["name"] == field)
    assert fd["size"] == 30


def test_the_stamp_is_still_treated_as_a_DATE(config):
    """Declaring a width must not turn a temporal field into a plain one — the
    format is what makes `now` and date ranges work (D29/D54)."""
    for layout, _sec, field in TEMPORAL:
        assert config.date_format(layout, field), f"{layout}.{field} lost its format"


def test_declaring_a_width_moves_no_VALUE(tmp_path, registry, config):
    """The user's condition, in the user's words: "leave the values like ' '
    and null how they were before. Just we are removing ?".

    `CalgaryStyleHeader` store rows carry `" "` and `CalgaryCartonLabel`'s
    carry `null` — two forms that D34/D39 exist to keep distinct — so the risk
    of declaring a width on a temporal field is that a save starts COERCING
    them into a stamp. Open and save every sample with no edits and require the
    bytes back unchanged.
    """
    checked = 0
    for src in sorted(FIX.glob("*.json")):
        p = tmp_path / src.name
        shutil.copy2(src, p)
        before = p.read_bytes()
        service.apply_edits(p, [], registry, config=config, backup=False)
        after = p.read_bytes()
        if after != before:
            # The ONLY writes a no-op save is allowed to make are a roll-up
            # total and a section COUNT correcting themselves against their own
            # detail rows (preticket.json ships both blank). Every other value
            # must be untouched — which is what this test is really about: a
            # declared WIDTH must not coerce ' ' or null into a stamp.
            b = json.loads(before.decode("utf-8"))
            a = json.loads(after.decode("utf-8"))
            lay = registry.get(detect_layout(p).layout)
            moved = [f for f in (a["data"]["header"].keys() | b["data"]["header"].keys())
                     if a["data"]["header"].get(f) != b["data"]["header"].get(f)]
            allowed = {r["field"] for r in (config.rollups(lay.name) or [])}
            allowed |= {config.count_field(lay.name, s)
                        for s in (config.fill_sections(lay.name) or {})}
            allowed.discard(None)
            assert set(moved) <= allowed, f"{src.name}: {moved} moved on a no-op save"
            for f in moved:
                a["data"]["header"][f] = b["data"]["header"][f]
            assert a == b, f"{src.name}: something outside the roll-up moved"
        checked += 1
    assert checked >= 5, "not enough samples exercised"


def test_the_blank_and_null_store_dates_survive_specifically(tmp_path, registry, config):
    """The byte check above would also pass if these files had no date at all;
    this names the two values so the assertion cannot go vacuous."""
    seen = {}
    for src, want in (("styleheader_fmtS.json", " "),
                      ("cartonlabel_minified.json", None)):
        p = tmp_path / src
        shutil.copy2(FIX / src, p)
        service.apply_edits(p, [], registry, config=config, backup=False)
        doc = json.loads(p.read_text(encoding="utf-8"))
        stores = (doc.get("data", {}).get("header", {}) or {}).get("stores") or []
        assert stores, f"{src} has no store rows to check"
        assert all(s.get("date") == want for s in stores), (
            f"{src}: store date is no longer {want!r}")
        seen[src] = want
    assert seen == {"styleheader_fmtS.json": " ",
                    "cartonlabel_minified.json": None}


@pytest.mark.parametrize("layout", ["CalgaryStyleHeader", "CalgaryDistLabel",
                                    "CalgaryCartonLabel"])
def test_no_calgary_date_or_timestamp_still_reads_as_a_question_mark(
        registry, config, layout):
    """`size: null` in the descriptor is what the panel renders as `(?)`."""
    scope = service.bulk_scope([str(FIX / _SAMPLE_FOR[layout])], registry, config)
    ts = next(f for f in scope["header_fields"][layout] if f["name"] == "timestamp")
    dt = next(f for s in scope["detail_sections"][layout] if s["name"] == "Stores"
              for f in s["fields"] if f["name"] == "date")
    assert ts["size"] == 30 and dt["size"] == 30


def test_the_OK_date_fields_are_untouched(registry):
    """Six `.OK` layouts also have a field called `date`. It is a plain 8-char
    fixed-width header field with NO date format — a different thing entirely,
    and on a fixed-width layout the size IS the format, so widening it would
    shift every field after it."""
    for lay in ("StyleHeader", "Preticket", "DistLabels", "EUPreticket",
                "EUStyleHeader", "EUCartonLabel"):
        sec = next(s for s in registry[lay].sections if s.name == "Header")
        assert next(f for f in sec.fields if f.name == "date").size == 8


def test_ladderplan_is_eight_and_holds_its_own_values(registry):
    """`ladderPlan` carries a YYYYMMDD date as a plain string — `20260401` —
    so 8 is exactly its width. It is NOT declared in `date_fields.yaml`, so
    unlike `timestamp` nothing else was policing its length: before this it was
    refused by Bulk Edit and skipped by Generate like `lane` was.

    Note it is a different field from `ladderPlanMMYY` (4, `0426`), which sits
    beside it — the two are easy to conflate by name.
    """
    sec = next(s for s in registry["CalgaryStyleHeader"].sections
               if s.name == "Details")
    assert next(f for f in sec.fields if f.name == "ladderPlan").size == 8
    assert next(f for f in sec.fields if f.name == "ladderPlanMMYY").size == 4

    import glob
    checked = 0
    for path in sorted(glob.glob(str(FIX / "styleheader*.json"))):
        for det in json.loads(Path(path).read_text(encoding="utf-8"))["data"].get("details", []):
            v = det.get("ladderPlan")
            if isinstance(v, str):
                assert len(v) <= 8, f"{Path(path).name}: {v!r}"
                checked += 1
    assert checked, "no ladderPlan values checked"


def test_ladderplan_is_not_treated_as_a_date(config):
    """It looks like one and is not declared as one — so it must not acquire
    date coercion by accident, which would rewrite `20260401` into a stamp."""
    assert not config.date_format("CalgaryStyleHeader", "ladderPlan")


# ------------------------------------------- every declared width, every path

# A consolidated guard over ALL the widths declared for the Calgary layouts, so
# a future spec edit that drops one is caught wherever it would show. The gap
# this closes is the rows & sequences bulk panel (`bulk_op_apply`, set/list):
# the other paths were already covered field by field above, that one was not.
_STAMP = "2026-03-04T05:06:07.123456789Z"
DECLARED = [
    # layout, section, field, width, a value that fits (≠ the sample's own)
    ("CalgaryStyleHeader", "Sizes",   "size",       6,  "MED"),
    ("CalgaryStyleHeader", "Lanes",   "lane",       8,  "LANE0007"),
    ("CalgaryStyleHeader", "Details", "pageNumber", 3,  "007"),
    ("CalgaryStyleHeader", "Details", "lineNumber", 3,  "042"),
    ("CalgaryStyleHeader", "Details", "ladderPlan", 8,  "20251231"),
    ("CalgaryStyleHeader", "Header",  "locator",    20, "LOC1234567890123456"),
    ("CalgaryDistLabel",   "Header",  "locator",    20, "LOC1234567890123456"),
    ("CalgaryCartonLabel", "Header",  "locator",    20, "LOC1234567890123456"),
    ("CalgaryStyleHeader", "Header",  "timestamp",  30, _STAMP),
    ("CalgaryDistLabel",   "Header",  "timestamp",  30, _STAMP),
    ("CalgaryCartonLabel", "Header",  "timestamp",  30, _STAMP),
    ("CalgaryStyleHeader", "Stores",  "date",       30, _STAMP),
    ("CalgaryDistLabel",   "Stores",  "date",       30, _STAMP),
    ("CalgaryCartonLabel", "Stores",  "date",       30, _STAMP),
]


@pytest.mark.parametrize("layout,section,field,width,value", DECLARED)
def test_declared_width_is_visible_everywhere(registry, config,
                                              layout, section, field, width, value):
    """The editor view and BOTH scope payloads must carry the number — a null
    is what the panels render as `(?)`, which is what the whole run was about."""
    sample = str(FIX / _SAMPLE_FOR[layout])

    view = service.parse_file_view(sample, registry, config)
    vsec = next(s for s in view["sections"] if s["name"] == section)
    vfd = next(f for f in vsec["fields"] if f["name"] == field)
    assert vfd["size"] == width
    assert vfd.get("editable") is not False and not vfd.get("hidden")

    scope = service.bulk_scope([sample], registry, config)
    if section == "Header":
        bfd = next(f for f in scope["header_fields"][layout] if f["name"] == field)
    else:
        bfd = next(f for s in scope["detail_sections"][layout] if s["name"] == section
                   for f in s["fields"] if f["name"] == field)
    assert bfd["size"] == width

    gs = service.generate_scope([sample], registry, config)
    if section == "Header":
        assert any(f["name"] == field for f in gs["header_fields"])
    else:
        assert any(f["name"] == field for s in gs["sections"]
                   if s["name"] == section for f in s["fields"])


@pytest.mark.parametrize("op", ["set", "list"])
@pytest.mark.parametrize("layout,section,field,width,value", DECLARED)
def test_rows_and_sequences_panel_can_write_it(tmp_path, registry, config, op,
                                               layout, section, field, width, value):
    """The single-op bulk panel — the path the other tests here did not cover.
    Before a width was declared this answered `<field> has no fixed width`."""
    p = tmp_path / f"{op}_{field}_{layout}.json"
    shutil.copy2(FIX / _SAMPLE_FOR[layout], p)
    _with_data(p, layout, section, registry, config)
    spec = ({"type": "set", "field": field, "value": value} if op == "set"
            else {"type": "list", "field": field, "values": [value]})
    res = service.bulk_op_apply([str(p)], layout, section, spec,
                                registry, config, backup=False)
    entry = res["results"][0]
    # `unchanged` is legitimate if the sample already holds the value.
    assert entry["status"] in ("changed", "unchanged"), entry
    assert "no fixed width" not in str(entry)

    okf = service.parse_okfile(p, registry=registry)
    stored = next(r for r in okf.records if r.section.name == section).get(field)
    if field in ("date", "timestamp"):
        assert stored.startswith(value.rstrip("Z"))   # normalised on write
    else:
        assert stored == value
