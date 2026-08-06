"""A row added to an empty JSON section starts with usable values.

A `.OK` layout gets this for free: the compiler lifts a real record line out of
the reference file into `sample_raw`, so seeding an empty `.OK` section yields a
realistic row. JSON layouts are hand-authored specs with no reference file, and
`seed_record` filled every field with "" — including fields that cannot legally
be blank (a Calgary store's `date` is an RFC 3339 nanosecond stamp).

`config/json_seed_rows.yaml` is the JSON equivalent of `sample_raw`. Its values
started as the vendor sample rows and are the USER'S to choose — the file is the
one place a seed is decided, and no value below is known to any code.

A field the config does not list falls back to "". A `pad_zeros` field is still
padded, and a temporal field is resolved through the same forgiving parser a
typed value uses, so config may declare an exact stamp or `now`.

JSON only. `.OK` seeding goes through `sample_raw` and must not move.
"""
import datetime
import json
import re
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

RFC3339_NANO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}Z$")


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _with_empty(tmp_path, fixture, *arrays):
    """A copy of ``fixture`` whose named header arrays are [] on disk."""
    doc = json.loads((FIX / fixture).read_text(encoding="utf-8"))
    for a in arrays:
        doc["data"]["header"][a] = []
    p = tmp_path / "f.json"
    p.write_text(json.dumps(doc, indent=4), encoding="utf-8")
    return p


def _si(path, name, registry, config):
    view = service.parse_file_view(path, registry, config)
    return next(i for i, s in enumerate(view["sections"]) if s["name"] == name)


def _rows(path, key):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]["header"][key]


# --------------------------------------------------------------------------- #
# The reported bug
# --------------------------------------------------------------------------- #
def test_a_seeded_row_is_not_blank(tmp_path, registry, config):
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    row = _rows(p, "stores")[0]
    assert any(v not in (None, "") for v in row.values()), row


def test_declared_seed_values_are_used(tmp_path, registry, config):
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    row = _rows(p, "stores")[0]
    assert row["store"] == "0115"
    assert row["units"] == "20"
    assert row["storeQuantity"] == "5"
    assert row["qtyToPrint"] == "600"


def test_a_seeded_row_looks_like_a_real_row(tmp_path, registry, config):
    """The user's call: an added row should look like the rows their real files
    carry. An earlier cut used invented placeholders (0001) to keep a real
    order's numbers out of generated files — that is D46's rule for CONVERSION,
    where the values arrive silently. Here the user asked for them, and the row
    is an editable starting point rather than inherited data.

    The seed STARTED as the sample row and the user has since chosen their own
    values for some fields, so this asserts the shape the sample establishes —
    same keys, and the sample's own value wherever config does not override —
    rather than equality with the sample."""
    sample = json.loads((FIX / "distlabel.json").read_text(
        encoding="utf-8"))["data"]["header"]["stores"][0]
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    row = _rows(p, "stores")[0]
    assert row.keys() == sample.keys()
    unchanged = ["units", "distroType", "storeQuantity", "cartonSequence",
                 "numberOfPacks", "adDate", "suppress", "puertoRicoFlag",
                 "puertoRicoStoreSeq", "puertoRicoStore"]
    assert {k: row[k] for k in unchanged} == {k: sample[k] for k in unchanged}


def test_size_is_seeded_null_by_choice(tmp_path, registry, config):
    """`size` is declared a real null, as the vendor sample carries it — not ""
    (D34/D39: absent and empty are different things). A size code is
    order-specific, so there is nothing meaningful to seed."""
    p = _with_empty(tmp_path, "styleheader_fmtB.json", "sizes")

    service.add_record(p, _si(p, "Sizes", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert _rows(p, "sizes")[0] == {"size": None, "quantity": "100"}


def test_lanes_seed_the_samples_blank_lane(tmp_path, registry, config):
    """The user's chosen value, matching the vendor sample. Its one consequence
    is pinned by `test_a_blank_seed_makes_a_section_read_as_having_no_data`."""
    p = _with_empty(tmp_path, "styleheader_fmtB.json", "lanes")

    service.add_record(p, _si(p, "Lanes", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert _rows(p, "lanes")[0] == {"lane": ""}


# --------------------------------------------------------------------------- #
# The fallback for fields config does not declare
# --------------------------------------------------------------------------- #
def test_a_date_declared_now_is_stamped_when_the_row_is_added(tmp_path, registry,
                                                              config):
    """CONFIG decides, not the code. `CalgaryDistLabel.Stores.date` is declared
    `now` at the user's request, so the seed carries the current instant in the
    field's declared RFC 3339 nanosecond format. An earlier cut did this to ANY
    temporal field automatically, which was wrong on the other two layouts — the
    rule is opt-in per field, per layout, which the next two tests pin."""
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    stamped = _rows(p, "stores")[0]["date"]
    assert RFC3339_NANO.match(stamped), stamped
    assert stamped.startswith(
        datetime.datetime.utcnow().strftime("%Y-%m-%d")), stamped


def test_a_date_declared_as_an_exact_stamp_passes_through(registry, config):
    """The other form config may use: an exact stamp is written byte-for-byte,
    so a seed can be reproducible where that is what the user wants."""
    from okgen.api.service import _coerce_date
    exact = "2026-02-10T14:47:45.107359353Z"

    assert _coerce_date("CalgaryDistLabel", "date", exact, config) == exact


def test_a_pad_zeros_field_is_padded(tmp_path, registry, config):
    """The D34 padder must still run over a seed, so a value written in config
    as "115" could not slip through to a 4-character field unpadded."""
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    store = _rows(p, "stores")[0]["store"]
    assert store == "0115" and len(store) == 4


def test_free_text_stays_blank(tmp_path, registry, config):
    """A message or fact has no neutral placeholder — the user fills it in."""
    p = _with_empty(tmp_path, "styleheader_fmtB.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    row = _rows(p, "stores")[0]
    # the StyleHeader sample carries a single space in these, and the seed is
    # the sample row verbatim
    assert row["distroType"] == " "
    assert row["adDate"] == " "


# --------------------------------------------------------------------------- #
# Every add path — the D16/D30 rule
# --------------------------------------------------------------------------- #
def test_bulk_add_seeds_too(tmp_path, registry, config):
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    service.bulk_op_apply([str(p)], "CalgaryDistLabel", "Stores",
                          {"type": "add", "count": 2}, registry, config, backup=False)

    rows = _rows(p, "stores")
    assert len(rows) == 2
    assert all(r["store"] == "0115" for r in rows), rows


def test_volume_generate_seeds_too(tmp_path, registry, config):
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    res = service.generate_apply(
        [str(p)], {"count": 1,
                   "row_counts": [{"section": "Stores", "min": 3, "max": 3}]},
        registry, config)

    out = sorted(Path(res["folder"]).iterdir())[0]
    rows = json.loads(out.read_text(encoding="utf-8"))["data"]["header"]["stores"]
    assert len(rows) == 3
    assert all(r["store"] == "0115" for r in rows), rows


def test_adding_after_a_bulk_empty_seeds_rather_than_cloning_the_skeleton(
        tmp_path, registry, config):
    """D45 leaves ONE all-null row when a section is emptied, and clone_record
    reads values through _display, which maps null -> "". Without this, adding
    after an empty gave another blank row — the same complaint by another
    route. The skeleton is LEFT IN PLACE: it may equally be a blank row the file
    genuinely ships, and deleting the user's row to tidy up is not our call."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "distlabel.json", p)
    service.bulk_op_apply([str(p)], "CalgaryDistLabel", "Stores",
                          {"type": "keep", "count": 0}, registry, config, backup=False)

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    rows = _rows(p, "stores")
    seeded = [r for r in rows if any(v not in (None, "") for v in r.values())]
    assert len(seeded) == 1, rows
    assert seeded[0]["store"] == "0115"


def test_the_seeded_file_still_opens(tmp_path, registry, config):
    p = _with_empty(tmp_path, "distlabel.json", "stores")
    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    view = service.parse_file_view(p, registry, config)
    sec = next(s for s in view["sections"] if s["name"] == "Stores")
    assert len(sec["records"]) == 1


# --------------------------------------------------------------------------- #
# .OK must not move
# --------------------------------------------------------------------------- #
def test_ok_seeding_still_comes_from_sample_raw(tmp_path, registry, config):
    """.OK was already correct and is explicitly out of scope."""
    p = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", p)
    service.bulk_op_apply([str(p)], "DistLabels", "Store",
                          {"type": "keep", "count": 0}, registry, config, backup=False)

    service.add_record(p, _si(p, "Store", registry, config), [], registry, config,
                       preview=False, backup=False)

    row = parse_okfile(p, registry=registry).sections()["Store"][0]
    assert row.get("store") == "0090", "the .OK seed must still come from sample_raw"


def test_no_ok_layout_declares_a_json_seed(config):
    """A seed row is a JSON-only concept; a `.OK` entry here would be a sign the
    two engines are being conflated."""
    for layout in ("StyleHeader", "Preticket", "CartonLabel", "DistLabels",
                   "EUPreticket", "EUStyleHeader", "EUCartonLabel"):
        for section in ("Header", "Store", "Lane", "Detail", "Size"):
            assert config.json_seed_row(layout, section) == {}, (layout, section)


def test_the_date_rule_is_scoped_to_the_json_layouts(tmp_path, registry, config):
    """`date` had to be declared in date_fields.yaml so a seeded stamp is valid.
    Six `.OK` layouts also have a header field called `date`, and theirs is an
    8-character fixed-width value — a "*" entry would have started normalizing
    those into RFC 3339 stamps that cannot fit the field."""
    for ok_layout in ("StyleHeader", "DistLabels", "Preticket", "EUPreticket",
                      "EUStyleHeader", "EUCartonLabel"):
        assert config.date_format(ok_layout, "date") is None, ok_layout
    for json_layout in ("CalgaryStyleHeader", "CalgaryDistLabel",
                        "CalgaryCartonLabel"):
        assert config.date_format(json_layout, "date") == "rfc3339_nano"


def test_an_ok_date_is_still_written_verbatim(tmp_path, registry, config):
    p = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]

    service.apply_edits(p, [{"record_index": ri, "field": "date", "value": "20260115"}],
                        registry, config=config, backup=False)

    assert parse_okfile(p, registry=registry).records[0].get("date") == "20260115"


# --------------------------------------------------------------------------- #
# The seed mirrors the sample — including null, and including no timestamp
# --------------------------------------------------------------------------- #
SEED_CASES = [
    ("distlabel.json", "CalgaryDistLabel", "Stores", ("header", "stores")),
    ("cartonlabel_minified.json", "CalgaryCartonLabel", "Stores", ("header", "stores")),
    ("styleheader_fmtB.json", "CalgaryStyleHeader", "Details", ("details",)),
]


def _sample_row(fixture, jpath):
    cur = json.loads((FIX / fixture).read_text(encoding="utf-8"))["data"]
    for step in jpath:
        cur = cur.get(step)
    return cur[0]


@pytest.mark.parametrize("fixture,layout,section,jpath", SEED_CASES)
def test_a_seeded_row_is_the_declared_row_exactly(tmp_path, registry, config,
                                                  fixture, layout, section, jpath):
    """Field for field, including nulls and blanks — not merely 'similar'. The
    expectation comes from the config, since the values are the user's to change
    and a test that restates them would just be a second copy to keep in sync;
    what must hold is that every declared value reaches the file with its TYPE
    intact, which is the class D49 and D53 were both about."""
    doc = json.loads((FIX / fixture).read_text(encoding="utf-8"))
    node = doc["data"]
    for step in jpath[:-1]:
        node = node[step]
    node[jpath[-1]] = []                      # empty it so the add SEEDS
    p = tmp_path / "f.json"
    p.write_text(json.dumps(doc, indent=4), encoding="utf-8")
    okf = parse_okfile(p, registry=registry)
    sec = next(s for s in okf.layout.sections if s.name == section)
    expected = service._json_seed_values(okf.layout, sec, config)

    service.add_record(p, _si(p, section, registry, config), [], registry, config,
                       preview=False, backup=False)

    got = json.loads(p.read_text(encoding="utf-8"))["data"]
    for step in jpath:
        got = got[step]
    # `date: now` is stamped per call, so compare it by shape, not by value.
    stamped = [k for k, v in expected.items() if RFC3339_NANO.match(str(v))]
    for k in stamped:
        assert RFC3339_NANO.match(got[0][k]), got[0][k]
    assert {k: v for k, v in got[0].items() if k not in stamped} == \
           {k: v for k, v in expected.items() if k not in stamped}


@pytest.mark.parametrize("fixture,layout,section,jpath", SEED_CASES)
def test_a_null_in_the_sample_is_seeded_as_a_real_null(tmp_path, registry, config,
                                                       fixture, layout, section, jpath):
    """`null` and `""` are different things (D34/D39). The seed used to write ""
    for both, collapsing the distinction on every added row."""
    sample = _sample_row(fixture, jpath)
    nulls = [k for k, v in sample.items() if v is None]
    if not nulls:
        pytest.skip(f"{layout}.{section} sample has no null fields")

    doc = json.loads((FIX / fixture).read_text(encoding="utf-8"))
    node = doc["data"]
    for step in jpath[:-1]:
        node = node[step]
    node[jpath[-1]] = []
    p = tmp_path / "f.json"
    p.write_text(json.dumps(doc, indent=4), encoding="utf-8")

    service.add_record(p, _si(p, section, registry, config), [], registry, config,
                       preview=False, backup=False)

    got = json.loads(p.read_text(encoding="utf-8"))["data"]
    for step in jpath:
        got = got[step]
    for k in nulls:
        assert got[0][k] is None, f"{k} should be null, got {got[0][k]!r}"


def test_cartonlabel_date_is_seeded_null_not_a_stamp(tmp_path, registry, config):
    """The clearest case of the old bug: this layout's samples never carry a
    date, yet every added row got a fresh RFC 3339 timestamp."""
    p = _with_empty(tmp_path, "cartonlabel_minified.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert _rows(p, "stores")[0]["date"] is None


def test_only_a_field_declared_now_becomes_time_dependent(tmp_path, registry, config):
    """The guard that keeps `now` opt-in. DistLabel asked for it; nothing else
    may quietly acquire a stamp — a seed that is time-dependent by accident
    makes two runs a day apart produce different files."""
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    for fixture, section, key in (
            ("cartonlabel_minified.json", "Stores", "stores"),
            ("styleheader_fmtB.json", "Stores", "stores"),
            ("styleheader_fmtB.json", "Sizes", "sizes"),
            ("styleheader_fmtB.json", "Lanes", "lanes")):
        p = _with_empty(tmp_path, fixture, key)
        service.add_record(p, _si(p, section, registry, config), [], registry,
                           config, preview=False, backup=False)
        assert today not in json.dumps(_rows(p, key)), f"{fixture}/{section}"


def test_date_is_still_editable_and_validated(tmp_path, registry, config):
    """Removing the auto-fill must not un-declare the field: typing a date must
    still normalize, and rubbish must still be refused (D29)."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "distlabel.json", p)
    view = service.parse_file_view(p, registry, config)
    sec = next(s for s in view["sections"] if s["name"] == "Stores")
    ri = sec["records"][0]["index"]

    service.apply_edits(p, [{"record_index": ri, "field": "date",
                             "value": "2026-01-08"}],
                        registry, config=config, backup=False)
    assert RFC3339_NANO.match(_rows(p, "stores")[0]["date"])

    with pytest.raises(Exception):
        service.apply_edits(p, [{"record_index": ri, "field": "date",
                                 "value": "not-a-date"}],
                            registry, config=config, backup=False)
