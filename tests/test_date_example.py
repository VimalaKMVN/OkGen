"""A temporal field ships a SPECIMEN of what it stores, in its own format.

User-reported: `timestamp (date)` in both bulk panels and Volume Generate, when
every other field reads `name (width)`. The panels now label by width, which
means the "this takes a date" cue has to come from somewhere else — the hint in
the value box. That hint used to be a hardcoded `2024-06-30` in the client,
which was simply wrong for `timestamp`: it stores a 30-character nanosecond
stamp, not a plain date.

So the specimen is rendered SERVER-side, by pushing one fixed reference instant
through whichever format the field declares in ``config/date_fields.yaml``. Two
consequences that these tests pin:

* a field declared ``rfc3339_nano`` reads ``2026-01-08T11:36:21.944107946Z`` —
  30 characters, exactly the width the panel now shows beside it, which is the
  point of the whole change;
* a field declared with a strftime pattern reads ITS OWN shape. A client-side
  constant could never do that, and ``date_fields.yaml`` documents strftime as
  supported, so this is a real path rather than a hypothetical one.

The instant is FIXED rather than ``now`` so the hint is identical on every
render and can be asserted at all.

A field with no date format must carry no specimen — the six ``.OK`` header
fields called ``date`` are plain 8-character values (D29/`v0.111.0`), and a
specimen on those would advertise a normalization that does not happen.
"""
import pytest

from okgen import datetimes
from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

STAMP = "2026-01-08T11:36:21.944107946Z"

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="no calgary fixtures")


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def test_rfc3339_specimen_is_the_real_shape():
    assert datetimes.example("rfc3339_nano") == STAMP


def test_specimen_length_equals_the_declared_width():
    """30 is not a coincidence — the label says (30) right beside this value."""
    assert len(datetimes.example("rfc3339_nano")) == 30


@pytest.mark.parametrize("fmt,expected", [
    ("%Y%m%d", "20260108"),
    ("%Y-%m-%d", "2026-01-08"),
    ("%m%y", "0126"),
])
def test_specimen_follows_the_field_s_own_format(fmt, expected):
    """A client-side constant could not do this — the reason it moved server-side."""
    assert datetimes.example(fmt) == expected


def test_specimen_is_stable_across_calls():
    """Fixed instant, not `now`: a hint that changed per render is untestable."""
    assert datetimes.example("rfc3339_nano") == datetimes.example("rfc3339_nano")


def test_specimen_round_trips_through_the_field_s_own_validator():
    """The hint must be a value the field would actually ACCEPT.

    A specimen the write path would refuse is worse than no specimen: it invites
    the user to type back something that is then rejected.
    """
    assert datetimes.is_valid(datetimes.example("rfc3339_nano"), "rfc3339_nano")
    assert datetimes.normalize(datetimes.example("rfc3339_nano"), "rfc3339_nano") == STAMP


def _header_field(entries, name):
    return next((e for e in entries if e["name"] == name), None)


def test_bulk_scope_carries_the_specimen(registry, config):
    scope = service.bulk_scope([str(FIX / "styleheader_fmtB.json")], registry, config)
    fields = scope["header_fields"]["CalgaryStyleHeader"]
    ts = _header_field(fields, "timestamp")
    assert ts is not None, "timestamp missing from the bulk scope"
    assert ts["date"] is True
    assert ts["date_example"] == STAMP
    # The width the label now shows must be there too — the two are read side
    # by side, and a specimen beside a `(?)` would be the old bug half-fixed.
    assert ts["size"] == 30


def test_generate_scope_carries_the_specimen(registry, config):
    scope = service.generate_scope([str(FIX / "styleheader_fmtB.json")], registry, config)
    ts = _header_field(scope["header_fields"], "timestamp")
    assert ts is not None, "timestamp missing from the generate scope"
    assert ts["date"] is True
    assert ts["date_example"] == STAMP
    assert ts["size"] == 30


def test_a_plain_field_carries_no_specimen(registry, config):
    scope = service.bulk_scope([str(FIX / "styleheader_fmtB.json")], registry, config)
    dept = _header_field(scope["header_fields"]["CalgaryStyleHeader"], "department")
    assert dept is not None
    assert "date" not in dept
    assert "date_example" not in dept


def test_ok_date_fields_get_no_specimen(registry, config):
    """The six `.OK` `date` fields are 8-char values with no date format.

    Same name as the Calgary one, different engine — the collision v0.111.0
    recorded. A specimen here would claim a normalization that never runs.
    """
    scope = service.bulk_scope([str(DATA_DIR / "StyleHeader.OK")], registry, config)
    fields = scope["header_fields"]["StyleHeader"]
    date_f = _header_field(fields, "date")
    # Asserted present, not guarded with an `if` — a skip-if-absent here would
    # pass silently the day the field is renamed, which is exactly when it
    # matters most.
    assert date_f is not None, "StyleHeader.OK should carry a `date` header field"
    assert date_f["size"] == 8, "the .OK date is a plain 8-char fixed-width value"
    assert "date" not in date_f, "it carries no date FORMAT, so it takes no range"
    assert "date_example" not in date_f, "an .OK date field must not claim a format"
