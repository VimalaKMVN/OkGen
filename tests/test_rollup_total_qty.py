"""StyleHeader's header total follows its Size rows (config/rollup_fields.yaml).

``tot_qty`` is a real header field the consuming system reads, and it must equal
the sum of every Size row's ``qty``. The field stays freely EDITABLE — the value
is enforced on the WRITE path rather than by locking the control, which is the
D51/D56 rule: a lock is a UI hint the save path never checks, and it blocks
legitimate typing.

Two states, and the second is the one that makes this more than a sum:

- **Size rows present** — the sum wins on every write path. A total the user
  typed is corrected, because with rows on disk the true total is knowable.
- **No Size rows** — the header field IS the print quantity in the new system,
  so it is authoritative and kept byte-for-byte. Deleting the last Size row must
  therefore KEEP the total it had; only a blank/zero one is seeded (5-10).

The boundaries below are the ones PLAN §6 keeps warning about (D43/D47): none /
one / all-deleted, and BOTH directions. The silent no-op is the failure mode
that a green suite does not surface, so every case asserts the bytes on disk.
"""
import random
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.api.service import EditError
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
def sh(tmp_path):
    """A copy of the reference StyleHeader (tot_qty 22, Size rows summing to 8)."""
    p = tmp_path / "SH.OK"
    shutil.copy(SAMPLE, p)
    return p


def _sized(tmp_path, name, qtys):
    """A StyleHeader whose Size section carries exactly ``qtys``."""
    raw = SAMPLE.read_bytes()
    body = b"".join(l + b"\r\n" for l in raw.split(b"\r\n")
                    if l and not l.startswith(b"&"))
    rows = b"".join(b"&XL    " + str(q).zfill(5).encode() + b"\\\r\n" for q in qtys)
    p = tmp_path / name
    p.write_bytes(body + rows)
    return p


def _total(path, registry):
    return parse_okfile(path, registry=registry).records[0].get("tot_qty")


def _size_rows(path, registry):
    okf = parse_okfile(path, registry=registry)
    return [r for r in okf.records if r.section.name == "Size"]


def _sec_index(path, registry, name):
    okf = parse_okfile(path, registry=registry)
    return next(i for i, s in enumerate(okf.layout.sections) if s.name == name)


def _save(path, registry, config, edits=None, ops=None):
    return service.apply_edits(str(path), edits or [], registry, config=config,
                               backup=False, ops=ops)


# --------------------------------------------------------------------------- #
# The sum wins whenever rows exist
# --------------------------------------------------------------------------- #
def test_reference_file_is_corrected_on_save(sh, registry, config):
    """The vendor sample declares 22 against four rows totalling 8.

    The rule post-dates these files, so this is the ordinary case, not an edge:
    a plain save brings the header into line.
    """
    assert _total(sh, registry) == "0000022"
    res = _save(sh, registry, config)
    assert _total(sh, registry) == "0000008"
    assert res["rollups"] == [{"field": "tot_qty", "section": "Size",
                               "from": "0000022", "to": "0000008",
                               "rows": 4, "reason": "sum"}]


def test_typed_total_loses_to_the_rows(sh, registry, config):
    """The field accepts the edit; the write path then corrects it.

    This is the whole design in one test — the control is never locked, so the
    user CAN type 5000, and the save is what guarantees the invariant.
    """
    _save(sh, registry, config, edits=[
        {"section_index": 0, "record_index": 0, "field": "tot_qty", "value": "5000"}])
    assert _total(sh, registry) == "0000008"


def test_editing_a_row_quantity_moves_the_total(sh, registry, config):
    rows = _size_rows(sh, registry)
    si = _sec_index(sh, registry, "Size")
    _save(sh, registry, config, edits=[
        {"section_index": si, "record_index": rows[0].index, "field": "qty",
         "value": "99"}])
    assert _total(sh, registry) == "0000105"        # 99 + 2 + 2 + 2


def test_deleting_one_row_lowers_the_total(sh, registry, config):
    rows = _size_rows(sh, registry)
    _save(sh, registry, config,
          ops=[{"type": "delete", "record_index": rows[0].index}])
    assert _total(sh, registry) == "0000006"        # 8 - 2
    assert len(_size_rows(sh, registry)) == 3


def test_adding_a_row_raises_the_total(sh, registry, config):
    """The GROW direction. D47 is the reason this is a separate test: row counts
    were only ever exercised shrinking, and 'cannot grow at all' sat unnoticed."""
    rows = _size_rows(sh, registry)
    _save(sh, registry, config,
          ops=[{"type": "add", "after_index": rows[-1].index}])
    assert len(_size_rows(sh, registry)) == 5
    assert _total(sh, registry) == "0000010"        # the clone copies qty 00002


def test_blank_row_quantity_counts_as_zero(tmp_path, registry, config):
    p = _sized(tmp_path, "BLANK.OK", [7])
    raw = p.read_bytes().replace(b"&XL    00007\\", b"&XL    00007\\\r\n&SM         \\", 1)
    p.write_bytes(raw)
    _save(p, registry, config)
    assert _total(p, registry) == "0000007"


# --------------------------------------------------------------------------- #
# No rows: the header is the authority
# --------------------------------------------------------------------------- #
def test_deleting_every_row_keeps_the_total(sh, registry, config):
    """The load-bearing case. With the size section empty the header total IS
    the print quantity, so zeroing it would destroy real data — and re-rolling a
    random value would invent some, which is the D46/D49 class."""
    _save(sh, registry, config)                     # normalise to 0000008
    rows = _size_rows(sh, registry)
    _save(sh, registry, config, ops=[{"type": "delete", "record_index": r.index}
                                     for r in sorted(rows, key=lambda r: -r.index)])
    assert _size_rows(sh, registry) == []
    assert _total(sh, registry) == "0000008"


def test_empty_section_with_zero_total_is_seeded_in_range(tmp_path, registry, config):
    p = _sized(tmp_path, "EMPTY.OK", [])
    _save(p, registry, config, edits=[
        {"section_index": 0, "record_index": 0, "field": "tot_qty", "value": "0"}])
    assert 5 <= int(_total(p, registry)) <= 10


def test_a_seeded_total_is_not_re_rolled_on_later_saves(tmp_path, registry, config):
    """Seeded once, then it is just a value in the file. Re-rolling on every
    save would make the same file show a different quantity each time and make a
    generated batch unreproducible."""
    p = _sized(tmp_path, "ONCE.OK", [])
    _save(p, registry, config, edits=[
        {"section_index": 0, "record_index": 0, "field": "tot_qty", "value": "0"}])
    seeded = _total(p, registry)
    for _ in range(3):
        res = _save(p, registry, config)
        assert res["rollups"] == []
    assert _total(p, registry) == seeded


def test_a_real_total_on_an_empty_section_is_left_alone(tmp_path, registry, config):
    p = _sized(tmp_path, "REAL.OK", [])
    _save(p, registry, config, edits=[
        {"section_index": 0, "record_index": 0, "field": "tot_qty", "value": "4321"}])
    assert _total(p, registry) == "0004321"
    _save(p, registry, config)
    assert _total(p, registry) == "0004321"


def test_the_user_may_still_edit_an_authoritative_total(tmp_path, registry, config):
    p = _sized(tmp_path, "EDIT.OK", [])
    _save(p, registry, config, edits=[
        {"section_index": 0, "record_index": 0, "field": "tot_qty", "value": "0"}])
    _save(p, registry, config, edits=[
        {"section_index": 0, "record_index": 0, "field": "tot_qty", "value": "77"}])
    assert _total(p, registry) == "0000077"


# --------------------------------------------------------------------------- #
# Refusals — never a truncated or silently wrong total
# --------------------------------------------------------------------------- #
def test_a_total_too_wide_for_the_field_is_refused_not_truncated(tmp_path, registry, config):
    """D40: writing a truncated total would misreport it rather than fail."""
    p = _sized(tmp_path, "BIG.OK", [99999] * 101)
    before = p.read_bytes()
    with pytest.raises(EditError) as exc:
        _save(p, registry, config)
    assert "7 digits" in str(exc.value) and "truncated" in str(exc.value)
    assert p.read_bytes() == before                 # nothing written


def test_a_non_numeric_quantity_is_a_clear_error(sh, registry, config):
    rows = _size_rows(sh, registry)
    si = _sec_index(sh, registry, "Size")
    before = sh.read_bytes()
    with pytest.raises(EditError) as exc:
        _save(sh, registry, config, edits=[
            {"section_index": si, "record_index": rows[0].index, "field": "qty",
             "value": "AB"}])
    assert "not a number" in str(exc.value)
    assert sh.read_bytes() == before


# --------------------------------------------------------------------------- #
# What it must NOT touch
# --------------------------------------------------------------------------- #
def test_opening_a_file_writes_nothing(sh, registry, config):
    """A mismatch is REPORTED on open and corrected on save. Recomputing at open
    would rewrite field content in a file the user only looked at — the one
    thing byte-exactness has never done (D26 normalises junk, never a value)."""
    before = sh.read_bytes()
    view = service.parse_file_view(sh, registry, config)
    assert view is not None
    okf = parse_okfile(sh, registry=registry)
    state = service.rollup_state(okf, config)
    assert state[0]["matches"] is False and state[0]["expected"] == "0000008"
    assert sh.read_bytes() == before


def test_rollup_state_reports_an_empty_section_as_authoritative(tmp_path, registry, config):
    p = _sized(tmp_path, "AUTH.OK", [])
    st = service.rollup_state(parse_okfile(p, registry=registry), config)[0]
    assert st["rows"] == 0 and st["authoritative"] is True and st["expected"] is None


def test_a_layout_with_no_rollup_is_untouched(tmp_path, registry, config):
    """Only StyleHeader declares a roll-up; every other layout must save exactly
    as it did before (D30 in reverse — a rule must not leak into paths it was
    never declared for)."""
    # Compared against a save by a config with NO roll-ups at all, rather than
    # against the file on disk: other layouts legitimately change on save (a
    # Preticket's line_count is re-synced by D16's detail fill), and the claim
    # here is only that roll-ups added nothing to that.
    plain = Config.load(FIXTURE_CONFIG)
    plain._rollups = {}                             # noqa: SLF001
    for name in ("Preticket.OK", "CartonLabel.OK", "DistLabels.OK"):
        src = DATA_DIR / name
        if not src.is_file():
            continue
        p, q = tmp_path / name, tmp_path / ("plain_" + name)
        shutil.copy(src, p)
        shutil.copy(src, q)
        assert service.rollup_state(parse_okfile(p, registry=registry), config) == []
        _save(p, registry, config)
        _save(q, registry, plain)
        assert p.read_bytes() == q.read_bytes()


def test_the_bulk_row_path_keeps_the_total_in_sync(sh, registry, config):
    """D30: a rule enforced in the editor and skipped by the parallel bulk paths
    stays invisible for months. Bulk 'keep 2 rows' must move the total too."""
    service.bulk_op_apply([str(sh)], "StyleHeader", "Size",
                          {"type": "keep", "count": 2}, registry, config,
                          backup=False)
    assert len(_size_rows(sh, registry)) == 2
    assert _total(sh, registry) == "0000004"


# --------------------------------------------------------------------------- #
# The backlog sweep
# --------------------------------------------------------------------------- #
def test_scan_previews_without_writing_anything(sh, registry, config):
    before = sh.read_bytes()
    res = service.total_qty_scan([str(sh)], registry, config, apply=False)
    one = res["results"][0]
    assert one["status"] == "would_fix"
    assert (one["from"], one["to"]) == ("0000022", "0000008")
    assert res["summary"]["would_fix"] == 1 and res["summary"]["fixed"] == 0
    assert sh.read_bytes() == before                # a preview writes NOTHING
    assert res["log"] is None


def test_fix_applies_what_the_preview_promised(sh, registry, config):
    preview = service.total_qty_scan([str(sh)], registry, config, apply=False)
    res = service.total_qty_scan([str(sh)], registry, config, apply=True,
                                 backup=False)
    assert res["results"][0]["status"] == "fixed"
    assert res["results"][0]["to"] == preview["results"][0]["to"]
    assert _total(sh, registry) == "0000008"


def test_a_file_with_no_rows_is_reported_and_left_alone(tmp_path, registry, config):
    """The whole reason the sweep needs a preview. With no size lines the total
    IS the print quantity, so a blanket recompute would write 0000000 over real
    quantities on exactly the shape the new system produces most."""
    p = _sized(tmp_path, "NOROWS.OK", [])
    before = p.read_bytes()
    res = service.total_qty_scan([str(p)], registry, config, apply=True,
                                 backup=False)
    one = res["results"][0]
    assert one["status"] == "no_rows"
    assert one["current"] == "0000022"               # reported so a human can act
    assert p.read_bytes() == before                  # and NOT touched


def test_no_row_files_are_listed_largest_first(tmp_path, registry, config):
    """Sorted so the implausible legacy totals surface without hunting."""
    made = []
    for n in ("0000005", "0009999", "0000120"):
        p = _sized(tmp_path, f"N{n}.OK", [])
        _save(p, registry, config, edits=[{"section_index": 0, "record_index": 0,
                                           "field": "tot_qty", "value": n}])
        made.append(str(p))
    res = service.total_qty_scan(made, registry, config, apply=False)
    got = [r["current"] for r in res["results"] if r["status"] == "no_rows"]
    assert got == ["0009999", "0000120", "0000005"]


def test_an_already_correct_file_is_not_rewritten(sh, registry, config):
    _save(sh, registry, config)                      # now correct
    stamp = sh.stat().st_mtime_ns
    res = service.total_qty_scan([str(sh)], registry, config, apply=True,
                                 backup=False)
    assert res["results"][0]["status"] == "ok"
    assert sh.stat().st_mtime_ns == stamp            # no write, no timestamp churn


def test_other_layouts_are_skipped_not_scanned(tmp_path, registry, config):
    p = tmp_path / "Preticket.OK"
    shutil.copy(DATA_DIR / "Preticket.OK", p)
    res = service.total_qty_scan([str(p)], registry, config, apply=True,
                                 backup=False)
    assert res["results"][0]["status"] == "skipped"
    assert res["summary"]["skipped"] == 1


def test_an_unfittable_total_is_an_error_not_a_write(tmp_path, registry, config):
    p = _sized(tmp_path, "OVER.OK", [99999] * 101)
    before = p.read_bytes()
    res = service.total_qty_scan([str(p)], registry, config, apply=True,
                                 backup=False)
    assert res["results"][0]["status"] == "error"
    assert p.read_bytes() == before


def test_the_report_names_every_bucket(tmp_path, registry, config):
    good = tmp_path / "good.OK"
    shutil.copy(SAMPLE, good)
    empty = _sized(tmp_path, "empty.OK", [])
    other = tmp_path / "Preticket.OK"
    shutil.copy(DATA_DIR / "Preticket.OK", other)
    res = service.total_qty_scan([str(good), str(empty), str(other)],
                                 registry, config, apply=False)
    text = res["report"]
    assert "TO FIX" in text and "0000022 -> 0000008" in text
    assert "NO DETAIL ROWS" in text and "empty.OK" in text
    assert "PREVIEW" in text


def test_bulk_keep_zero_leaves_the_total_standing(sh, registry, config):
    """Emptying a section via bulk is the same authority hand-over as deleting
    the last row by hand — the total that was there is the print quantity."""
    _save(sh, registry, config)                     # normalise to 0000008
    service.bulk_op_apply([str(sh)], "StyleHeader", "Size",
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)
    assert _size_rows(sh, registry) == []
    assert _total(sh, registry) == "0000008"
