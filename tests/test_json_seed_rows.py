"""A row added to an empty JSON section starts with usable values.

A `.OK` layout gets this for free: the compiler lifts a real record line out of
the reference file into `sample_raw`, so seeding an empty `.OK` section yields a
realistic row. JSON layouts are hand-authored specs with no reference file, and
`seed_record` filled every field with "" — including fields that cannot legally
be blank (a Calgary store's `date` is an RFC 3339 nanosecond stamp).

`config/json_seed_rows.yaml` is the JSON equivalent of `sample_raw`. Values are
deliberate PLACEHOLDERS (0001, RCD001), never lifted from a real vendor order —
D46's lesson, where conversion inherited the template's rows and handed users
another order's store numbers.

A field not listed still gets a usable value: a temporal field gets `now` in its
declared format, a `pad_zeros` field is zero-padded, everything else is blank.

JSON only. `.OK` seeding goes through `sample_raw` and must not move.
"""
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
    assert row["store"] == "0001"
    assert row["units"] == "1"
    assert row["storeQuantity"] == "1"
    assert row["qtyToPrint"] == "1"


def test_seed_values_are_placeholders_not_a_real_order(tmp_path, registry, config):
    """D46: the vendor template's rows are a specific real order's data. A seed
    must never reproduce one of its store numbers."""
    p = _with_empty(tmp_path, "distlabel.json", "stores")
    spec = config.conversion_for("DistLabels")
    template = json.loads((Path(config.config_dir) / spec["template"]).read_text())
    borrowed = {s.get("store") for s in template["data"]["header"]["stores"]}

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert borrowed, "template must carry stores for this to mean anything"
    assert _rows(p, "stores")[0]["store"] not in borrowed


def test_size_is_seeded_blank_by_choice(tmp_path, registry, config):
    """`size` is declared "" deliberately — a size code is order-specific."""
    p = _with_empty(tmp_path, "styleheader_fmtB.json", "sizes")

    service.add_record(p, _si(p, "Sizes", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert _rows(p, "sizes")[0] == {"size": "", "quantity": "1"}


def test_lanes_seed_a_lane_id(tmp_path, registry, config):
    p = _with_empty(tmp_path, "styleheader_fmtB.json", "lanes")

    service.add_record(p, _si(p, "Lanes", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert _rows(p, "lanes")[0] == {"lane": "RCD001"}


# --------------------------------------------------------------------------- #
# The fallback for fields config does not declare
# --------------------------------------------------------------------------- #
def test_a_temporal_field_seeds_the_current_time(tmp_path, registry, config):
    """`date` is an RFC 3339 nanosecond stamp — blank is not a valid value, and
    config does not declare it, so the date_fields rule must fill it."""
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert RFC3339_NANO.match(_rows(p, "stores")[0]["date"] or "")


def test_a_pad_zeros_field_is_padded(tmp_path, registry, config):
    """`store` is declared "0001"; the D34 padder must still run over a seed so
    a value written as "1" could not slip through unpadded."""
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    store = _rows(p, "stores")[0]["store"]
    assert store == "0001" and len(store) == 4


def test_free_text_stays_blank(tmp_path, registry, config):
    """A message or fact has no neutral placeholder — the user fills it in."""
    p = _with_empty(tmp_path, "styleheader_fmtB.json", "stores")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    row = _rows(p, "stores")[0]
    assert row["distroType"] == ""
    assert row["adDate"] == ""


# --------------------------------------------------------------------------- #
# Every add path — the D16/D30 rule
# --------------------------------------------------------------------------- #
def test_bulk_add_seeds_too(tmp_path, registry, config):
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    service.bulk_op_apply([str(p)], "CalgaryDistLabel", "Stores",
                          {"type": "add", "count": 2}, registry, config, backup=False)

    rows = _rows(p, "stores")
    assert len(rows) == 2
    assert all(r["store"] == "0001" for r in rows), rows


def test_volume_generate_seeds_too(tmp_path, registry, config):
    p = _with_empty(tmp_path, "distlabel.json", "stores")

    res = service.generate_apply(
        [str(p)], {"count": 1,
                   "row_counts": [{"section": "Stores", "min": 3, "max": 3}]},
        registry, config)

    out = sorted(Path(res["folder"]).iterdir())[0]
    rows = json.loads(out.read_text(encoding="utf-8"))["data"]["header"]["stores"]
    assert len(rows) == 3
    assert all(r["store"] == "0001" for r in rows), rows


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
    assert seeded[0]["store"] == "0001"


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
