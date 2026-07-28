"""Temporal fields — friendly input, strict output, and random ranges.

The Calgary JSON ``timestamp`` is an RFC 3339 stamp with nanosecond precision
(``2026-01-08T11:36:21.944107946Z``). Users need to put arbitrary instants there
— past, present and future — for test batches, so it is editable rather than
stamped automatically (an auto-stamp could only ever write "now").

Two properties matter most and are covered hardest here:

* precision you SUPPLY is never silently truncated — Python's datetime carries
  only microseconds, so a naive round-trip would turn a vendor's 9-digit
  fraction into 6; and
* a value the parser cannot understand is REFUSED, not written through
  malformed, because whatever consumes these files parses the stamp.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from okgen import datetimes
from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(
    os.environ.get("OKGEN_DATA_DIR",
                   str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions")))
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

RFC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}Z$")

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="no calgary fixtures")


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _copy(tmp_path, name="f.json", fixture="styleheader_fmtB.json"):
    p = tmp_path / name
    p.write_bytes((FIX / fixture).read_bytes())
    return p


def _stamp(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]["timestamp"]


# --------------------------------------------------------------------------- #
# The date engine
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("typed,expected", [
    ("2026-01-08", "2026-01-08T00:00:00.000000000Z"),
    ("2026-01-08 14:30", "2026-01-08T14:30:00.000000000Z"),
    ("2026-01-08T14:30", "2026-01-08T14:30:00.000000000Z"),
    ("2026-01-08 14:30:22", "2026-01-08T14:30:22.000000000Z"),
    ("2026-01-08T14:30:22Z", "2026-01-08T14:30:22.000000000Z"),
    # a supplied fraction is kept EXACTLY — this is the truncation guard
    ("2026-01-08T14:30:22.944107946Z", "2026-01-08T14:30:22.944107946Z"),
    ("2026-01-08T14:30:22.5Z", "2026-01-08T14:30:22.500000000Z"),
])
def test_shorthand_normalizes_to_the_full_stamp(typed, expected):
    assert datetimes.normalize(typed, datetimes.RFC3339_NANO) == expected


@pytest.mark.parametrize("typed", [
    "now", "2026-01-08", "2026-01-08 14:30", "2026-01-08T14:30:22.5Z",
    "2026-01-08T14:30:22.12345Z", "2026-01-08T14:30:22.944107946Z",
])
def test_every_stamp_is_written_at_full_width(typed):
    """Always exactly 9 fractional digits, zero-filled at the END.

    Short precision is padded, never left ragged; supplied precision is never
    cut. A generated "now" carries the 6 digits the clock knows plus zeros.
    """
    out = datetimes.normalize(typed, datetimes.RFC3339_NANO)
    assert RFC.match(out), out
    assert len(out) == 30
    assert len(out.split(".")[1][:-1]) == 9


def test_random_values_are_full_width_too():
    for _ in range(20):
        out = datetimes.random_between("2024-01-01", "2024-12-31",
                                       datetimes.RFC3339_NANO)
        assert len(out.split(".")[1][:-1]) == 9


def test_over_precision_is_refused_rather_than_rounded_off():
    """More than 9 digits cannot be held, so say so — don't silently drop them."""
    with pytest.raises(datetimes.DateError, match="fractional digits"):
        datetimes.normalize("2026-01-08T14:30:22.1234567891Z",
                            datetimes.RFC3339_NANO)


def test_nanosecond_precision_survives_a_round_trip():
    """datetime only holds microseconds, so the fraction must bypass it."""
    stamp = "2026-01-08T11:36:21.944107946Z"
    out = datetimes.normalize(stamp, datetimes.RFC3339_NANO)
    assert out == stamp
    assert out[-4:-1] == "946", "the last three digits are beyond microseconds"


def test_now_is_the_current_instant():
    out = datetimes.normalize("now", datetimes.RFC3339_NANO)
    assert RFC.match(out)
    parsed = datetime.strptime(out[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 120


@pytest.mark.parametrize("bad", [
    "", "   ", "garbage", "2026-13-45", "not a date", "08-Jan-2026", "20260108",
])
def test_an_unparseable_value_is_refused(bad):
    with pytest.raises(datetimes.DateError):
        datetimes.normalize(bad, datetimes.RFC3339_NANO)


def test_a_strftime_format_is_honoured():
    assert datetimes.normalize("2026-01-08", "%Y-%m-%d") == "2026-01-08"
    assert datetimes.normalize("2026-01-08", "%Y%m%d") == "20260108"


def test_random_lands_inside_the_range():
    for _ in range(50):
        out = datetimes.random_between("2024-03-01", "2024-03-31",
                                       datetimes.RFC3339_NANO)
        assert RFC.match(out)
        assert "2024-03-01" <= out[:10] <= "2024-03-31"


def test_reversed_bounds_are_accepted():
    out = datetimes.random_between("2024-12-31", "2024-01-01",
                                   datetimes.RFC3339_NANO)
    assert out[:4] == "2024"


def test_a_single_instant_range_is_that_instant():
    out = datetimes.random_between("2024-03-01", "2024-03-01",
                                   datetimes.RFC3339_NANO)
    assert out == "2024-03-01T00:00:00.000000000Z"


def test_random_values_actually_vary():
    picks = {datetimes.random_between("2000-01-01", "2030-01-01",
                                      datetimes.RFC3339_NANO) for _ in range(20)}
    assert len(picks) > 15, "a random range must not keep returning one value"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_config_knows_which_fields_are_temporal(config):
    assert config.date_format("CalgaryStyleHeader", "timestamp") == \
        datetimes.RFC3339_NANO
    assert config.date_format("CalgaryStyleHeader", "chain") is None
    assert config.date_format(None, None) is None


def test_a_layout_entry_overrides_the_shared_one():
    cfg = Config(chains={}, rules=[], date_fields={
        "*": {"stamp": datetimes.RFC3339_NANO},
        "OddLayout": {"stamp": "%Y-%m-%d"},
    })
    assert cfg.date_format("Anything", "stamp") == datetimes.RFC3339_NANO
    assert cfg.date_format("OddLayout", "stamp") == "%Y-%m-%d"


# --------------------------------------------------------------------------- #
# Editing one file
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("typed,expected_prefix", [
    ("1999-12-31", "1999-12-31T00:00:00"),        # past
    ("2026-01-08 14:30", "2026-01-08T14:30:00"),  # a specific moment
    ("2099-06-15", "2099-06-15T00:00:00"),        # future
])
def test_any_instant_can_be_saved(tmp_path, registry, config, typed, expected_prefix):
    p = _copy(tmp_path)
    service.apply_edits(p, [{"record_index": 0, "field": "timestamp",
                             "value": typed}],
                        registry, config=config, backup=False)
    assert _stamp(p).startswith(expected_prefix)
    assert RFC.match(_stamp(p))


def test_a_full_stamp_is_stored_verbatim(tmp_path, registry, config):
    p = _copy(tmp_path)
    stamp = "2021-07-04T08:09:10.123456789Z"
    service.apply_edits(p, [{"record_index": 0, "field": "timestamp",
                             "value": stamp}],
                        registry, config=config, backup=False)
    assert _stamp(p) == stamp


def test_a_bad_stamp_is_refused_and_the_file_is_untouched(tmp_path, registry, config):
    p = _copy(tmp_path)
    original = p.read_bytes()
    with pytest.raises(service.EditError, match="timestamp"):
        service.apply_edits(p, [{"record_index": 0, "field": "timestamp",
                                 "value": "sometime last tuesday"}],
                            registry, config=config, backup=False)
    assert p.read_bytes() == original


def test_editing_the_stamp_leaves_every_other_byte_alone(tmp_path, registry, config):
    p = _copy(tmp_path)
    before = json.loads(p.read_text(encoding="utf-8"))
    service.apply_edits(p, [{"record_index": 0, "field": "timestamp",
                             "value": "2001-01-01"}],
                        registry, config=config, backup=False)
    after = json.loads(p.read_text(encoding="utf-8"))
    before["data"].pop("timestamp"), after["data"].pop("timestamp")
    assert before == after


# --------------------------------------------------------------------------- #
# Bulk Edit
# --------------------------------------------------------------------------- #
def _bulk_files(tmp_path, n=5):
    return [str(_copy(tmp_path, f"f{i}.json")) for i in range(n)]


def test_bulk_random_date_gives_each_file_its_own_instant(tmp_path, registry, config):
    paths = _bulk_files(tmp_path)
    res = service.bulk_op_apply(
        paths, "CalgaryStyleHeader", "Header",
        {"type": "random_date", "field": "timestamp",
         "from": "2024-01-01", "to": "2024-12-31"},
        registry, config, backup=False)

    assert all(r["status"] == "changed" for r in res["results"])
    stamps = [_stamp(p) for p in paths]
    assert all(s.startswith("2024-") and RFC.match(s) for s in stamps)
    assert len(set(stamps)) > 1, "every file got the same instant"


def test_bulk_set_accepts_shorthand(tmp_path, registry, config):
    paths = _bulk_files(tmp_path, 3)
    service.bulk_op_apply(paths, "CalgaryStyleHeader", "Header",
                          {"type": "set", "field": "timestamp",
                           "value": "2019-05-05"},
                          registry, config, backup=False)
    assert {_stamp(p) for p in paths} == {"2019-05-05T00:00:00.000000000Z"}


def test_bulk_list_normalizes_every_listed_date(tmp_path, registry, config):
    paths = _bulk_files(tmp_path, 6)
    service.bulk_op_apply(paths, "CalgaryStyleHeader", "Header",
                          {"type": "list", "field": "timestamp",
                           "values": "2021-01-01, 2022-02-02, 2023-03-03"},
                          registry, config, backup=False)
    allowed = {"2021-01-01T00:00:00.000000000Z",
               "2022-02-02T00:00:00.000000000Z",
               "2023-03-03T00:00:00.000000000Z"}
    assert {_stamp(p) for p in paths} <= allowed


def test_a_bad_bulk_range_is_reported_not_written(tmp_path, registry, config):
    paths = _bulk_files(tmp_path, 2)
    before = [_stamp(p) for p in paths]
    res = service.bulk_op_apply(paths, "CalgaryStyleHeader", "Header",
                                {"type": "random_date", "field": "timestamp",
                                 "from": "whenever", "to": "2024-12-31"},
                                registry, config, backup=False)
    assert all(r["status"] == "error" for r in res["results"])
    assert [_stamp(p) for p in paths] == before


def test_random_date_refuses_a_field_that_is_not_temporal(tmp_path, registry, config):
    paths = _bulk_files(tmp_path, 1)
    res = service.bulk_op_apply(paths, "CalgaryStyleHeader", "Header",
                                {"type": "random_date", "field": "chain",
                                 "from": "2024-01-01", "to": "2024-12-31"},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "error"
    assert "date field" in res["results"][0]["error"]


# --------------------------------------------------------------------------- #
# Volume Generate
# --------------------------------------------------------------------------- #
def test_generate_spreads_stamps_across_a_range(tmp_path, registry, config):
    p = _copy(tmp_path, "tpl.json")
    res = service.generate_apply(
        [str(p)],
        {"count": 8, "header_fields": [{"name": "timestamp",
                                        "from": "2023-01-01", "to": "2023-12-31"}]},
        registry, config)

    stamps = [json.loads(f.read_text(encoding="utf-8"))["data"]["timestamp"]
              for f in sorted(Path(res["folder"]).iterdir())]
    assert len(stamps) == 8
    assert all(s.startswith("2023-") and RFC.match(s) for s in stamps)
    assert len(set(stamps)) > 1, "generated files should not share one instant"


def test_generate_accepts_a_list_of_dates(tmp_path, registry, config):
    p = _copy(tmp_path, "tpl.json")
    res = service.generate_apply(
        [str(p)],
        {"count": 5, "header_fields": [{"name": "timestamp",
                                        "values": "2020-01-01, 2021-01-01"}]},
        registry, config)
    stamps = {json.loads(f.read_text(encoding="utf-8"))["data"]["timestamp"]
              for f in Path(res["folder"]).iterdir()}
    assert stamps <= {"2020-01-01T00:00:00.000000000Z",
                      "2021-01-01T00:00:00.000000000Z"}


def test_generate_says_so_when_a_date_field_has_no_range(tmp_path, registry, config):
    p = _copy(tmp_path, "tpl.json")
    with pytest.raises(service.EditError, match="date range"):
        service.generate_preview([str(p)],
                                 {"count": 2,
                                  "header_fields": [{"name": "timestamp"}]},
                                 registry, config)


def test_scopes_flag_the_field_as_a_date(tmp_path, registry, config):
    """So the client can offer a date range instead of a numeric one."""
    p = _copy(tmp_path)
    paths = [str(p)]

    bulk = service.bulk_scope(paths, registry, config)
    field = next(f for f in bulk["header_fields"]["CalgaryStyleHeader"]
                 if f["name"] == "timestamp")
    assert field["date"] is True

    gen = service.generate_scope(paths, registry, config)
    field = next(f for f in gen["header_fields"] if f["name"] == "timestamp")
    assert field["date"] is True
    # ...and an ordinary field is not flagged
    other = next(f for f in gen["header_fields"] if f["name"] != "timestamp")
    assert "date" not in other


def test_ok_layouts_have_no_temporal_fields_configured(config):
    """This shipped enabled for the JSON timestamp only; the .OK date fields
    need their formats confirmed against the consumer first."""
    for layout in ("StyleHeader", "Preticket", "CartonLabel", "DistLabels",
                   "EUPreticket", "EUStyleHeader", "EUCartonLabel"):
        assert config.date_format(layout, "date") is None
        assert config.date_format(layout, "transmit_date") is None
