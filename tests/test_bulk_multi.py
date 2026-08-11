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
    # `tot_qty` joins them un-asked: editing the Size rows moves the total, and
    # a correction the user did not request is exactly what must be visible.
    assert [f["field"] for f in res["fields"]] == ["dept", "qty", "tot_qty"]
    assert all(f["status"] == "change" for f in res["fields"])
    assert res["fields"][2]["rollup"]["reason"] == "sum"


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


# --------------------------------------------------------------------------- #
# What the report SAYS — `format: A -> B`, not just "changed"
#
# The single-field panel used to show the transition, and that is what a preview
# is read FOR: checking the change before it lands on a whole selection. Only
# the `set` op produced that line, and only for a one-record section, so the
# before/after is captured here instead — every op type, detail sections too.
# --------------------------------------------------------------------------- #
def test_a_header_field_reports_its_transition(files, registry, config):
    res = service.bulk_multi_preview([str(files[0])], "StyleHeader", [
        {"section": "Header", "type": "list", "field": "chain", "values": "04"},
    ], registry, config)["results"][0]
    f = res["fields"][0]
    assert (f["before"], f["after"]) == ("03", "04")
    assert f["rows"] == 1 and f["moved"] == 1 and f["varies"] is False


def test_a_detail_field_reports_the_transition_and_the_row_count(files, registry, config):
    res = service.bulk_multi_preview([str(files[0])], "StyleHeader", [
        {"section": "Size", "type": "list", "field": "qty", "values": "125"},
    ], registry, config)["results"][0]
    f = res["fields"][0]
    assert (f["before"], f["after"]) == ("00002", "00125")
    assert f["rows"] == 4 and f["moved"] == 4
    assert f["varies"] is False, "one value on every row does not vary"


def test_a_range_is_flagged_as_varying(files, registry, config):
    """One before/after pair would misrepresent the other rows."""
    res = service.bulk_multi_preview([str(files[0])], "StyleHeader", [
        {"section": "Size", "type": "random", "field": "qty", "min": 100, "max": 999},
    ], registry, config)["results"][0]
    assert res["fields"][0]["varies"] is True


def test_a_field_that_did_not_move_carries_no_transition(files, registry, config):
    """Setting a field to what it already holds must not claim a change."""
    res = service.bulk_multi_preview([str(files[0])], "StyleHeader", [
        {"section": "Header", "type": "list", "field": "chain", "values": "03"},
    ], registry, config)["results"][0]
    assert "before" not in res["fields"][0]


def test_the_transition_survives_apply(files, registry, config):
    """The preview's promise and the applied result must say the same thing."""
    pv = service.bulk_multi_preview([str(files[0])], "StyleHeader", [
        {"section": "Header", "type": "list", "field": "dept", "values": "42"},
    ], registry, config)["results"][0]["fields"][0]
    ap = _apply([files[0]], [{"section": "Header", "type": "list",
                              "field": "dept", "values": "42"}], registry, config)[0]
    assert (ap["fields"][0]["before"], ap["fields"][0]["after"]) == (pv["before"], pv["after"])
    assert _hdr(files[0], registry, "dept") == ap["fields"][0]["after"]


# --------------------------------------------------------------------------- #
# ROLL-UPS in the multi-field report — v0.78.0's rule, on the new path
#
# `bulk_multi_*` calls `_apply_bulk_op` directly and never goes through
# `_bulk_op_eval`'s roll-up wrapper, so the preview promised the TYPED total
# while the save wrote the sum: the same "reports one thing, writes another"
# defect v0.78.0 fixed on the single-op route, reappearing through a parallel
# path (D30) that happened to be new rather than old.
# --------------------------------------------------------------------------- #
def _preview(paths, ops, registry, config):
    return service.bulk_multi_preview([str(p) for p in paths], "StyleHeader",
                                      ops, registry, config)["results"][0]


def test_a_typed_total_previews_as_the_sum_not_as_typed(files, registry, config):
    ops = [{"section": "Header", "type": "list", "field": "tot_qty", "values": "0000500"}]
    pv = _preview([files[0]], ops, registry, config)
    f = next(x for x in pv["fields"] if x["field"] == "tot_qty")
    assert f["after"] == "0000008", "the preview must show what will really land"
    assert f["rollup"]["typed"] == "0000500", "and what was discarded"
    assert f["rollup"]["rows"] == 4


def test_the_previewed_total_is_what_lands_on_disk(files, registry, config):
    ops = [{"section": "Header", "type": "list", "field": "tot_qty", "values": "0000500"}]
    promised = next(x for x in _preview([files[0]], ops, registry, config)["fields"]
                    if x["field"] == "tot_qty")["after"]
    _apply([files[0]], ops, registry, config)
    assert _hdr(files[0], registry, "tot_qty") == promised


def test_editing_the_rows_previews_the_total_it_moves(files, registry, config):
    """The total follows the rows on its own — a change the user did not ask
    for, so it must appear in the preview rather than only after the fact."""
    pv = _preview([files[0]], [{"section": "Size", "type": "list",
                                "field": "qty", "values": "125"}], registry, config)
    tot = next((x for x in pv["fields"] if x["field"] == "tot_qty"), None)
    assert tot is not None and tot["after"] == "0000500"
    assert tot["rollup"]["reason"] == "sum"


def test_an_unfittable_total_is_an_error_in_the_multi_preview(tmp_path, registry, config):
    """Refused at save (D40), so it must not preview as an applicable change."""
    raw = SAMPLE.read_bytes()
    body = b"".join(l + b"\r\n" for l in raw.split(b"\r\n")
                    if l and not l.startswith(b"&"))
    rows = b"".join(b"&XL    " + b"99999" + b"\\\r\n" for _ in range(101))
    big = tmp_path / "BIG.OK"
    big.write_bytes(body + rows)
    pv = _preview([big], [{"section": "Header", "type": "list",
                           "field": "dept", "values": "42"}], registry, config)
    assert pv["status"] == "error"
    assert "digits" in pv["error"]


def test_a_file_with_no_size_lines_keeps_the_typed_total(tmp_path, registry, config):
    """The other half of D58: with no rows the header field IS the quantity."""
    raw = SAMPLE.read_bytes()
    body = b"".join(l + b"\r\n" for l in raw.split(b"\r\n")
                    if l and not l.startswith(b"&"))
    ns = tmp_path / "NS.OK"
    ns.write_bytes(body)
    ops = [{"section": "Header", "type": "list", "field": "tot_qty", "values": "0000500"}]
    pv = _preview([ns], ops, registry, config)
    f = next(x for x in pv["fields"] if x["field"] == "tot_qty")
    assert f["after"] == "0000500" and "rollup" not in f
    service.bulk_multi_apply([str(ns)], "StyleHeader", ops, registry, config,
                             backup=False)
    assert _hdr(ns, registry, "tot_qty") == "0000500"


# --------------------------------------------------------------------------- #
# What the user READS when something is wrong
#
# A status token is a value the code branches on, not a sentence. Two of them
# were reaching the screen verbatim (`missing_field`, `no_section`), which tells
# a user nothing about what to do.
# --------------------------------------------------------------------------- #
STATUS_TOKENS = {"missing_field", "no_section", "too_wide", "error", "skipped",
                 "change", "unchanged"}


def test_no_raw_status_token_is_shown_as_an_error(files, registry, config):
    """Whatever goes wrong, the message must be a sentence."""
    for ops in ([{"section": "Header", "type": "list", "field": "nope", "values": "1"}],
                [{"section": "Nope", "type": "list", "field": "dept", "values": "1"}],
                [{"section": "Header", "type": "list", "field": "dept", "values": ""}]):
        res = _preview([files[0]], ops, registry, config)
        for f in res["fields"]:
            if f.get("error"):
                assert f["error"] not in STATUS_TOKENS, f["error"]
                assert len(f["error"].split()) > 2, f["error"]


def test_a_field_not_in_the_section_names_the_field_and_section(files, registry, config):
    res = _preview([files[0]], [{"section": "Header", "type": "list",
                                 "field": "nope", "values": "1"}], registry, config)
    assert "no field 'nope' in Header" in res["fields"][0]["error"]


def test_a_section_with_no_rows_skips_that_FIELD_not_the_file(files, registry, config):
    """One store-less file in a selection must not block its own header edits.
    A section this file does not have is a field that does not apply, not a
    failure — abandoning the file for it is too harsh."""
    ops = [{"section": "Header", "type": "list", "field": "dept", "values": "42"},
           {"section": "Nope", "type": "list", "field": "dept", "values": "9"}]
    res = _preview([files[0]], ops, registry, config)
    assert res["status"] == "change"
    by = {(f["section"], f["field"]): f for f in res["fields"]}
    assert by[("Header", "dept")]["status"] == "change"
    assert by[("Nope", "dept")]["status"] == "skipped"
    assert "skipped" in by[("Nope", "dept")]["error"]
    _apply([files[0]], ops, registry, config)
    assert _hdr(files[0], registry, "dept") == "42", "the header edit still lands"


def test_setting_a_field_to_its_current_value_is_unchanged(files, registry, config):
    """The `list` op reports `change` without comparing, so this used to preview
    as a change and rewrite the file — and its .bak — for nothing. The single-op
    `set` path returns `unchanged`; the two must agree."""
    p = files[0]
    current = _hdr(p, registry, "chain")
    before = p.read_bytes()
    ops = [{"section": "Header", "type": "list", "field": "chain", "values": current}]
    pv = _preview([p], ops, registry, config)
    assert pv["status"] == "unchanged"
    assert pv["fields"][0]["status"] == "unchanged", "the field must agree with the file"
    service.bulk_multi_apply([str(p)], "StyleHeader", ops, registry, config,
                             backup=True)
    assert p.read_bytes() == before
    assert not list(p.parent.glob("*.bak")), "an unchanged file must not be rewritten"


# --------------------------------------------------------------------------- #
# FORMAT validation — `enforce_options` in field_display.yaml
#
# A ticket format of `8` — which no chain declares — saved cleanly on all nine
# layouts that expose the field, and only failed later at NiceLabel. The list
# lives in display.yaml already; enforcement makes it the whole truth.
#
# The hazard is the opposite one: enforcing an INCOMPLETE list refuses a
# legitimate value, which is worse than accepting a wrong one. That is why it
# is opt-in per field, and why the tests below check both directions.
# --------------------------------------------------------------------------- #
SHIPPED = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture(scope="module")
def shipped_config():
    return Config.load(SHIPPED)


def _fmt_field(layout):
    return "ticket_format" if layout == "EUCartonLabel" else "format"


OK_LAYOUTS = [("StyleHeader.OK", "StyleHeader"), ("Preticket.OK", "Preticket"),
              ("CartonLabel.OK", "CartonLabel"), ("EUStyleHeader.OK", "EUStyleHeader"),
              ("EUCartonLabel.OK", "EUCartonLabel"), ("EUPreticket.OK", "EUPreticket")]


@pytest.mark.parametrize("sample,layout", OK_LAYOUTS)
def test_an_undeclared_format_is_refused(tmp_path, registry, shipped_config,
                                         sample, layout):
    src = DATA_DIR / sample
    if not src.exists():
        pytest.skip(f"no sample for {sample}")
    p = tmp_path / sample
    shutil.copy(src, p)
    before = p.read_bytes()
    field = _fmt_field(layout)
    res = service.bulk_multi_apply(
        [str(p)], layout,
        [{"section": "Header", "type": "list", "field": field, "values": "8"}],
        registry, shipped_config, backup=False)["results"][0]
    assert res["status"] == "error"
    assert "not valid for this file" in res["error"]
    assert p.read_bytes() == before


@pytest.mark.parametrize("sample,layout", OK_LAYOUTS)
def test_every_declared_format_still_saves(tmp_path, registry, shipped_config,
                                           sample, layout):
    """The hazard direction: a list narrower than reality would block real work.
    Every format the shipped config declares for this file must save."""
    src = DATA_DIR / sample
    if not src.exists():
        pytest.skip(f"no sample for {sample}")
    view = service.parse_file_view(src, registry, shipped_config)
    field = _fmt_field(layout)
    entry = next((f for f in view["sections"][0]["fields"] if f["name"] == field), None)
    assert entry and entry.get("options"), f"{layout}.{field} declares no options"
    for code in entry["options"]:
        p = tmp_path / sample
        shutil.copy(src, p)
        res = service.bulk_multi_apply(
            [str(p)], layout,
            [{"section": "Header", "type": "list", "field": field, "values": code}],
            registry, shipped_config, backup=False)["results"][0]
        assert res["status"] in ("changed", "unchanged"), (code, res.get("error"))


def test_the_error_names_the_allowed_values(tmp_path, registry, shipped_config):
    """A refusal must be diagnosable: if a real format is missing from config,
    the message is what tells you so."""
    p = tmp_path / "SH.OK"
    shutil.copy(DATA_DIR / "StyleHeader.OK", p)
    res = service.bulk_multi_apply(
        [str(p)], "StyleHeader",
        [{"section": "Header", "type": "list", "field": "format", "values": "8"}],
        registry, shipped_config, backup=False)["results"][0]
    assert "allowed:" in res["error"]
    assert "A" in res["error"] and "B" in res["error"]


def test_a_list_is_checked_before_it_is_picked_from(tmp_path, registry, shipped_config):
    """A `list` op picks at random, so validating the RESULT would pass or fail
    by luck. One bad entry must refuse the whole op, every time."""
    p = tmp_path / "SH.OK"
    shutil.copy(DATA_DIR / "StyleHeader.OK", p)
    before = p.read_bytes()
    for _ in range(12):
        res = service.bulk_multi_apply(
            [str(p)], "StyleHeader",
            [{"section": "Header", "type": "list", "field": "format",
              "values": "A,B,8"}], registry, shipped_config, backup=False)["results"][0]
        assert res["status"] == "error"
    assert p.read_bytes() == before


def test_generate_cannot_create_a_file_with_an_invalid_format(tmp_path, registry,
                                                              shipped_config):
    """Generation is the path most able to produce a wrong value with nobody
    typing it: a numeric RANGE on `format` emitted 3, 3, 7 on a layout whose
    formats are letters."""
    p = tmp_path / "SH.OK"
    shutil.copy(DATA_DIR / "StyleHeader.OK", p)
    with pytest.raises(Exception) as exc:
        service.generate_preview([str(p)], {
            "count": 3,
            "header_fields": [{"name": "format", "mode": "random", "min": 1, "max": 9}],
        }, registry, shipped_config, sample=3)
    assert "not valid" in str(exc.value)


def test_the_single_file_editor_refuses_it_too(tmp_path, registry, shipped_config):
    p = tmp_path / "SH.OK"
    shutil.copy(DATA_DIR / "StyleHeader.OK", p)
    before = p.read_bytes()
    with pytest.raises(EditError):
        service.apply_edits(str(p), [{"section_index": 0, "record_index": 0,
                                      "field": "format", "value": "8"}],
                            registry, config=shipped_config, backup=False)
    assert p.read_bytes() == before


def test_fields_that_are_NOT_enforced_stay_permissive(shipped_config):
    """`chain` and `type` are governed by isolation and the detection guard, and
    their lists are suggestions (D56) — enforcing them by list would refuse a
    legitimate re-casing."""
    assert shipped_config.enforces_options("StyleHeader", "format") is True
    assert shipped_config.enforces_options("CalgaryStyleHeader", "chain") is False
    assert shipped_config.enforces_options("CalgaryStyleHeader", "type") is False
    assert shipped_config.enforces_options("CalgaryStyleHeader", "compareAtUp") is False


# --------------------------------------------------------------------------- #
# The ROWS & SEQUENCES panel (single-op) reads as words too
#
# The guard above covers the multi-field panel. The single-op panel prints
# `status` straight into a column, so it still showed raw tokens — `no_section`
# with an EMPTY Change column told a user nothing at all. Same class, other
# panel, found while checking what an emptied section does on the .OK side.
# --------------------------------------------------------------------------- #
def _op(paths, layout, section, op, registry, config):
    return service.bulk_op_preview(paths, layout, section, op, registry, config)


def test_single_op_no_rows_explains_itself(files, registry, config):
    """`no_section` on a file whose section has no rows. The status is what the
    code branches on; `detail` is what the user reads."""
    service.bulk_op_apply([str(files[0])], "StyleHeader", "Lane",
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)
    res = _op([str(files[0])], "StyleHeader", "Lane",
              {"type": "set", "field": "lane1", "value": "0007"},
              registry, config)
    r = res["results"][0]
    assert r["status"] == "no_section"
    assert r.get("detail"), "no explanation at all — the original defect"
    assert r["detail"] not in STATUS_TOKENS
    assert len(r["detail"].split()) > 2
    assert "Lane" in r["detail"]


def test_single_op_missing_section_explains_itself(files, registry, config):
    res = _op([str(files[0])], "StyleHeader", "NoSuchSection",
              {"type": "set", "field": "x", "value": "1"}, registry, config)
    r = res["results"][0]
    assert r["status"] == "no_section"
    assert "NoSuchSection" in r.get("detail", "")


def test_every_non_changing_single_op_status_carries_a_sentence(files, registry, config):
    """Whatever goes wrong, something readable comes back — the rule the
    multi-field panel has had, applied to this one."""
    service.bulk_op_apply([str(files[0])], "StyleHeader", "Lane",
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)
    cases = [
        ("Lane", {"type": "set", "field": "lane1", "value": "0007"}),
        ("NoSuchSection", {"type": "set", "field": "x", "value": "1"}),
        ("Size", {"type": "set", "field": "nosuchfield", "value": "1"}),
    ]
    for section, op in cases:
        r = _op([str(files[0])], "StyleHeader", section, op,
                registry, config)["results"][0]
        if r["status"] in ("change", "changed", "unchanged"):
            continue
        text = r.get("detail") or r.get("error") or ""
        assert text, f"{section}/{r['status']} says nothing"
        assert text not in STATUS_TOKENS, text
        assert len(text.split()) > 2, text
