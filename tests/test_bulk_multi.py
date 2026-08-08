"""Bulk Edit — many fields, across sections, in ONE apply.

Shaped like Volume Generate but applied to the SELECTED files. Every control
maps onto a bulk op that already existed, so nothing new can be written:

    one value or a comma list  -> `list`         (a one-item list IS "set")
    a min/max range            -> `random`
    a date from/to             -> `random_date`

Two properties the single-op path never needed:

- **One file, one write.** The file is opened once, every op is applied to that
  copy, and it is saved once — 12 files x 3 fields is 12 writes and 12 .bak
  backups, not 36.
- **All-or-nothing per file.** A single failing op abandons that whole file
  rather than leaving it carrying some of the changes: a half-updated file looks
  exactly like a correct one. Files stay independent, so one bad file never
  blocks the rest of the selection.

Also pins the chain-isolation hole this work uncovered — see the last section.
"""
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import parse_okfile

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"
SAMPLE = DATA_DIR / "StyleHeader.OK"


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


@pytest.fixture
def files(tmp_path):
    out = []
    for i in range(3):
        p = tmp_path / f"SH{i}.OK"
        shutil.copy(SAMPLE, p)
        out.append(p)
    return out


def _hdr(p, registry, field):
    return parse_okfile(p, registry=registry).records[0].get(field)


def _rows(p, registry, section, field):
    return [r.get(field) for r in parse_okfile(p, registry=registry).records
            if r.section and r.section.name == section]


def _apply(paths, ops, registry, config, backup=False):
    return service.bulk_multi_apply([str(p) for p in paths], "StyleHeader", ops,
                                    registry, config, backup=backup)["results"]


# --------------------------------------------------------------------------- #
# Several fields, several sections, one apply
# --------------------------------------------------------------------------- #
def test_fields_across_sections_are_applied_together(files, registry, config):
    ops = [
        {"section": "Header", "type": "list", "field": "dept", "values": "42"},
        {"section": "Header", "type": "list", "field": "chain", "values": "03"},
        {"section": "Size", "type": "list", "field": "qty", "values": "125"},
    ]
    res = _apply(files, ops, registry, config)
    assert all(r["status"] == "changed" for r in res)
    for p in files:
        assert _hdr(p, registry, "dept") == "42"
        assert _hdr(p, registry, "chain") == "03"
        assert _rows(p, registry, "Size", "qty") == ["00125"] * 4


def test_a_single_value_sets_it_everywhere(files, registry, config):
    """A one-item list is how "set this value" is expressed — no separate op."""
    _apply(files, [{"section": "Header", "type": "list", "field": "dept",
                    "values": "42"}], registry, config)
    assert {_hdr(p, registry, "dept") for p in files} == {"42"}


def test_the_file_is_written_once_not_once_per_field(files, registry, config):
    """Three fields must produce ONE .bak, not three — the whole reason the ops
    travel together instead of as three requests."""
    p = files[0]
    ops = [{"section": "Header", "type": "list", "field": "dept", "values": "42"},
           {"section": "Header", "type": "list", "field": "chain", "values": "03"},
           {"section": "Size", "type": "list", "field": "qty", "values": "125"}]
    service.bulk_multi_apply([str(p)], "StyleHeader", ops, registry, config,
                             backup=True)
    baks = sorted({b.name for b in p.parent.glob("*.bak")})
    assert baks == [f"{p.name}.bak"], baks


def test_the_rollup_runs_once_after_every_field_is_in_place(files, registry, config):
    """Setting the Size qty and the header total in one apply must end with the
    total agreeing with the rows — not with a total computed mid-batch."""
    p = files[0]
    res = _apply([p], [
        {"section": "Header", "type": "list", "field": "tot_qty", "values": "9999"},
        {"section": "Size", "type": "list", "field": "qty", "values": "125"},
    ], registry, config)
    assert _hdr(p, registry, "tot_qty") == "0000500"        # 4 rows x 125
    assert res[0]["rollups"], "the correction must be reported, never silent"


# --------------------------------------------------------------------------- #
# All-or-nothing, per file
# --------------------------------------------------------------------------- #
def test_one_bad_field_abandons_that_whole_file(files, registry, config):
    """`dept` would have succeeded; because `chain` cannot, NEITHER is written."""
    p = files[0]
    before = p.read_bytes()
    res = _apply([p], [
        {"section": "Header", "type": "list", "field": "dept", "values": "42"},
        {"section": "Header", "type": "list", "field": "chain", "values": "TOOLONG"},
    ], registry, config)
    assert res[0]["status"] == "error"
    assert p.read_bytes() == before, "a half-updated file is the failure to avoid"


def test_a_bad_file_does_not_block_the_others(tmp_path, registry, config):
    good = tmp_path / "good.OK"
    bad = tmp_path / "bad.OK"
    shutil.copy(SAMPLE, good)
    bad.write_bytes(b"not an OK file at all\r\n")
    res = _apply([good, bad], [{"section": "Header", "type": "list",
                                "field": "dept", "values": "42"}], registry, config)
    by_name = {r["name"]: r for r in res}
    assert by_name["good.OK"]["status"] == "changed"
    assert by_name["bad.OK"]["status"] in ("error", "skipped")
    assert _hdr(good, registry, "dept") == "42"


def test_the_preview_writes_nothing(files, registry, config):
    before = [p.read_bytes() for p in files]
    service.bulk_multi_preview([str(p) for p in files], "StyleHeader",
                               [{"section": "Header", "type": "list",
                                 "field": "dept", "values": "42"}], registry, config)
    assert [p.read_bytes() for p in files] == before


def test_the_preview_reports_each_field_separately(files, registry, config):
    """One summary line cannot say which field moved and which did not."""
    res = service.bulk_multi_preview([str(files[0])], "StyleHeader", [
        {"section": "Header", "type": "list", "field": "dept", "values": "42"},
        {"section": "Size", "type": "list", "field": "qty", "values": "125"},
    ], registry, config)["results"][0]
    assert [f["field"] for f in res["fields"]] == ["dept", "qty"]
    assert all(f["status"] == "change" for f in res["fields"])


def test_no_ops_changes_nothing(files, registry, config):
    before = [p.read_bytes() for p in files]
    res = _apply(files, [], registry, config)
    assert all(r["status"] == "unchanged" for r in res)
    assert [p.read_bytes() for p in files] == before


# --------------------------------------------------------------------------- #
# Ranges and lists vary; a single value does not
# --------------------------------------------------------------------------- #
def test_a_range_varies_per_row(files, registry, config):
    _apply([files[0]], [{"section": "Size", "type": "random", "field": "qty",
                         "min": 100, "max": 999}], registry, config)
    got = _rows(files[0], registry, "Size", "qty")
    assert all(100 <= int(v) <= 999 for v in got)


def test_a_list_picks_only_listed_values(files, registry, config):
    _apply(files, [{"section": "Header", "type": "list", "field": "dept",
                    "values": "42,43"}], registry, config)
    assert {_hdr(p, registry, "dept") for p in files} <= {"42", "43"}


# --------------------------------------------------------------------------- #
# CHAIN ISOLATION — a hole this work uncovered, on the path the panel uses
#
# D9 built the rule, D30 wired it into `_bulk_eval` (the older header route) and
# D50 fixed the name form — but the Bulk Edit panel posts to the bulk *op*
# route, which never had the check. So `chain` could be set to `05` on a North
# America file in bulk, while the editor and the older route both refused it.
# Enforcing it in the shared `_apply_bulk_op` closes it for the single-op path
# and the multi-field path together.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("op", [
    {"type": "set", "field": "chain", "value": "05"},
    {"type": "list", "field": "chain", "values": "05"},
])
def test_europe_is_refused_on_the_single_op_bulk_route(files, registry, config, op):
    p = files[0]
    before = p.read_bytes()
    res = service.bulk_op_apply([str(p)], "StyleHeader", "Header", op,
                                registry, config, backup=False)["results"][0]
    assert res["status"] == "error"
    assert "isolated" in res["error"]
    assert p.read_bytes() == before


def test_europe_is_refused_on_the_multi_field_route(files, registry, config):
    p = files[0]
    before = p.read_bytes()
    res = _apply([p], [
        {"section": "Header", "type": "list", "field": "dept", "values": "42"},
        {"section": "Header", "type": "list", "field": "chain", "values": "05"},
    ], registry, config)
    assert res[0]["status"] == "error"
    assert p.read_bytes() == before, "the whole file is abandoned, dept included"


@pytest.mark.parametrize("chain", ["01", "02", "03", "04", "06"])
def test_a_chain_inside_the_group_is_still_allowed(files, registry, config, chain):
    """The guard must refuse the boundary, not ordinary banner changes."""
    p = files[0]
    res = service.bulk_op_apply([str(p)], "StyleHeader", "Header",
                                {"type": "set", "field": "chain", "value": chain},
                                registry, config, backup=False)["results"][0]
    assert res["status"] in ("changed", "unchanged")
    assert _hdr(p, registry, "chain") == chain


def test_the_guard_matches_the_editor_on_a_CLEARED_chain(tmp_path, registry, config):
    """Clearing a chain is a different question from moving it across the
    boundary. The editor and the older bulk route both test `old and new` — a
    guard that also refused an empty value would make bulk stricter than the
    editor, which is a fix changing more than the defect."""
    p = tmp_path / "EU.OK"
    shutil.copy(DATA_DIR / "EUStyleHeader.OK", p)
    res = service.bulk_op_apply([str(p)], "EUStyleHeader", "Header",
                                {"type": "set", "field": "chain", "value": "' '"},
                                registry, config, backup=False)["results"][0]
    assert res["status"] == "changed"


@pytest.mark.parametrize("value", ["AB", "7", ""])
def test_a_europe_file_cannot_take_a_non_europe_chain(tmp_path, registry, config, value):
    """The other half of the hole: bulk used to write 'AB', '07' and even '00'
    straight onto a Europe file's chain. Each leaves the isolated group."""
    p = tmp_path / "EU.OK"
    shutil.copy(DATA_DIR / "EUStyleHeader.OK", p)
    before = p.read_bytes()
    res = service.bulk_op_apply([str(p)], "EUStyleHeader", "Header",
                                {"type": "set", "field": "chain", "value": value},
                                registry, config, backup=False)["results"][0]
    assert res["status"] == "error"
    assert "isolated" in res["error"]
    assert p.read_bytes() == before
