"""Volume Generate: the guards Bulk Edit has, applied to the path that CREATES.

Generation is the riskiest write path in OkGen — it produces many files from
values nobody typed one by one — so a rule missing here is a rule missing at
scale. Three things were:

- **Chain isolation could be bypassed by an explicit value list.** Randomizing
  already picked from the template's own group (D30) and an isolation-locked
  chain was not offered (v0.40.1), but a LISTED `05` went straight onto a North
  America template and the whole generated batch shipped as Europe. Fifth
  appearance of this class, and the first in a path that creates rather than
  edits.
- **An unknown field name escaped as a raw ``KeyError``** — a 500 and a Python
  repr on screen — after being silently skipped, which is worse: you asked to
  vary a field and nothing would have.
- **Locked fields were omitted from the panel entirely**, so a user could not
  tell "OkGen forgot this field" from "you may not vary it".
"""
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.api.service import EditError
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _copy(tmp_path, name):
    src = DATA_DIR / name
    if not src.is_file():
        pytest.skip(f"no sample for {name}")
    p = tmp_path / name
    shutil.copy(src, p)
    return p


def _preview(p, spec, registry, config, sample=3):
    return service.generate_preview([str(p)], spec, registry, config, sample=sample)


# --------------------------------------------------------------------------- #
# Chain isolation on the value-LIST path
# --------------------------------------------------------------------------- #
def test_europe_cannot_be_listed_onto_a_north_america_template(tmp_path, registry, config):
    p = _copy(tmp_path, "StyleHeader.OK")
    with pytest.raises(EditError) as exc:
        _preview(p, {"count": 2, "header_fields": [
            {"name": "chain", "mode": "list", "values": ["05"]}]}, registry, config)
    assert "isolated" in str(exc.value)


def test_a_north_america_chain_cannot_be_listed_onto_a_europe_template(tmp_path, registry, config):
    """Both directions — D9 is a boundary, not a one-way door."""
    p = _copy(tmp_path, "EUStyleHeader.OK")
    with pytest.raises(EditError) as exc:
        _preview(p, {"count": 2, "header_fields": [
            {"name": "chain", "mode": "list", "values": ["01"]}]}, registry, config)
    assert "isolated" in str(exc.value)


def test_a_banner_inside_the_group_is_still_allowed(tmp_path, registry, config):
    """The guard must refuse the boundary, not ordinary banner changes."""
    p = _copy(tmp_path, "StyleHeader.OK")
    pv = _preview(p, {"count": 2, "header_fields": [
        {"name": "chain", "mode": "list", "values": ["04"]}]}, registry, config)
    assert {r["values"]["chain"] for r in pv["sample"]} == {"04"}


def test_randomizing_an_isolated_chain_leaves_it_alone(tmp_path, registry, config):
    """The older half of the rule, still holding: a range cannot move it."""
    p = _copy(tmp_path, "EUStyleHeader.OK")
    pv = _preview(p, {"count": 3, "header_fields": [
        {"name": "chain", "mode": "random", "min": 1, "max": 99}]}, registry, config)
    assert {r["values"]["chain"] for r in pv["sample"]} == {"05"}


def test_nothing_is_written_when_a_chain_is_refused(tmp_path, registry, config):
    """A refusal must not leave a half-generated folder behind."""
    p = _copy(tmp_path, "StyleHeader.OK")
    before = set(tmp_path.iterdir())
    with pytest.raises(EditError):
        service.generate_apply([str(p)], {"count": 5, "header_fields": [
            {"name": "chain", "mode": "list", "values": ["05"]}]}, registry, config)
    assert set(tmp_path.iterdir()) == before


# --------------------------------------------------------------------------- #
# A field the layout does not have
# --------------------------------------------------------------------------- #
def test_an_unknown_field_is_named_not_a_keyerror(tmp_path, registry, config):
    p = _copy(tmp_path, "StyleHeader.OK")
    with pytest.raises(EditError) as exc:
        _preview(p, {"count": 1, "header_fields": [
            {"name": "nosuch", "mode": "list", "values": ["1"]}]}, registry, config)
    assert "no field 'nosuch' on StyleHeader" in str(exc.value)


def test_an_unknown_field_is_not_silently_skipped(tmp_path, registry, config):
    """Skipping it would mean asking to vary a field and nothing happening."""
    p = _copy(tmp_path, "StyleHeader.OK")
    with pytest.raises(EditError):
        _preview(p, {"count": 1, "header_fields": [
            {"name": "dept", "mode": "list", "values": ["42"]},
            {"name": "nosuch", "mode": "list", "values": ["1"]}]}, registry, config)


# --------------------------------------------------------------------------- #
# Locked fields are SHOWN, greyed, with an accurate reason
# --------------------------------------------------------------------------- #
# DistLabels `format` is deliberately absent: it is a signature byte, but both
# of its values are DistLabels, so it is editable everywhere (see
# tests/test_api.py::test_distlabels_format_is_editable_in_bulk_and_generate).
@pytest.mark.parametrize("sample,layout,field", [
    ("StyleHeader.OK", "StyleHeader", "indicator"),
    ("Preticket.OK", "Preticket", "indicator"),
    ("EUStyleHeader.OK", "EUStyleHeader", "process"),
])
def test_a_signature_field_is_listed_but_not_editable(tmp_path, registry, config,
                                                      sample, layout, field):
    p = _copy(tmp_path, sample)
    scope = service.generate_scope([str(p)], registry, config)
    entry = next((f for f in scope["header_fields"] if f["name"] == field), None)
    assert entry is not None, f"{field} must be shown, not hidden"
    assert entry["editable"] is False
    assert "identifies the layout" in entry["locked_reason"]


def test_the_key_field_says_it_is_ASSIGNED_not_read_only(tmp_path, registry, config):
    """The key is not read-only — it is handed a unique value per file. A wrong
    reason is worse than none."""
    p = _copy(tmp_path, "StyleHeader.OK")
    scope = service.generate_scope([str(p)], registry, config)
    entry = next(f for f in scope["header_fields"] if f["name"] == scope["key_field"])
    assert entry["editable"] is False
    assert "unique key" in entry["locked_reason"]


def test_an_isolated_chain_says_ISOLATION_not_read_only(tmp_path, registry, config):
    """`read-only — it identifies the layout` would be plainly wrong: the chain
    is fixed because Europe is isolated (D9), and detection does not key on it."""
    p = _copy(tmp_path, "EUStyleHeader.OK")
    scope = service.generate_scope([str(p)], registry, config)
    entry = next(f for f in scope["header_fields"] if f["name"] == "chain")
    assert entry["editable"] is False
    assert "isolated" in entry["locked_reason"]
    assert "identifies the layout" not in entry["locked_reason"]


def test_ordinary_fields_are_still_offered(tmp_path, registry, config):
    """Listing locked fields must not quietly lock the rest."""
    p = _copy(tmp_path, "StyleHeader.OK")
    scope = service.generate_scope([str(p)], registry, config)
    editable = [f["name"] for f in scope["header_fields"] if f.get("editable", True)]
    assert "dept" in editable
    assert len(editable) >= 5
