"""SCAN 6-digit prices, blank Pre-Ticket version/description, and the
`totalQuantity` roll-up on both engines.

Four user asks landed together here, and the thread that ties them is that a
**converted file is always a SCAN file**:

* a SCAN price is **6 digits**, zero-filled at the front. The 9-digit form the
  shipped conversions used to write is the **WMS** one — it was inferred from
  the vendor samples in the repo, every one of which carries a ``headerASNid``
  and is therefore WMS. Conversion never produces a WMS file, because no
  convertible `.OK` layout has an ASN field.
* ``totalQuantity`` is the **sum of the detail lines**, on the `.OK` side and
  the JSON side alike — written in each engine's own form (fixed-width zero-fill
  vs the vendor's unpadded value).
* a converted Pre-Ticket's ``version`` and ``description`` are **blank**; the
  sample's ``INITIAL`` / ``PUMPKIN SPICE amp EVERYTH`` are one order's data, and
  ``description`` was the last genuine template leak.
* ``lineCount`` is **deliberately unchanged** — the user's call, made with the
  vendor data in front of them (all 13 vendor Style Headers carry a space).

The WMS half is pinned as hard as the SCAN half: the user's instruction was
"don't touch WMS", so a test asserting a 6-digit price is only half the claim.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import parse_okfile
from okgen.okjson import TRANSFORMS

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("OKGEN_DATA_DIR",
                               str(ROOT / "data" / "OkFileDefinitions")))
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    """The SHIPPED config — conversion for all four layouts is defined there."""
    return Config.load(ROOT / "config")


@pytest.fixture(scope="module")
def fixcfg():
    return Config.load(FIXTURE_CONFIG)


def _convert(tmp_path, registry, config, name):
    src = tmp_path / "in"
    src.mkdir(exist_ok=True)
    p = src / name
    shutil.copy2(DATA_DIR / name, p)
    res = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]
    return out, json.loads(out.read_text(encoding="utf-8"))["data"]


# ------------------------------------------------------- the pad6 transform ---

@pytest.mark.parametrize("raw,want", [
    ("002099", "002099"),      # already 6 and zero-filled — passes through
    ("599999", "599999"),
    ("4599", "004599"),        # short -> zeros at the FRONT (45.99)
    ("  199", "000199"),       # space-padded short value (1.99)
    ("  6899", "006899"),      # space-padded (68.99)
    ("000000", "000000"),      # a REAL zero price is preserved as one
    ("      ", ""),            # BLANK stays blank — not 000000
    ("", ""),
])
def test_pad6_is_six_digits_and_keeps_blank_blank(raw, want):
    """The front zeros go on the FRONT — trimming from the other end would keep
    the padding and throw the number away.

    The blank case is the one that is easy to get wrong and is a real value:
    `CartonLabel.OK` carries `ret_price` as six spaces, meaning *no price*
    rather than a price of zero, and zero-filling it would invent 0.00. The
    9-digit `pad9` this replaces got that wrong ('      ' -> '000000000').
    """
    assert TRANSFORMS["pad6"](raw) == want


def test_pad9_still_exists_and_still_pads_to_nine():
    """`pad6` is an addition, not a rename — the WMS form must remain available
    for whatever declares it."""
    assert TRANSFORMS["pad9"]("002099") == "000002099"


# ------------------------------------------------- converted prices are SCAN ---

def test_a_converted_style_header_price_is_six_digits(tmp_path, registry, config):
    _, d = _convert(tmp_path, registry, config, "StyleHeader.OK")
    row = d["details"][0]
    assert row["retailPrice"] == "002099"
    assert row["compareAtPrice"] == "000000"       # the .OK carries a real zero
    assert len(row["retailPrice"]) == 6


def test_a_converted_dist_label_price_is_six_digits_not_dotted(tmp_path, registry,
                                                               config):
    """REVERSES the earlier dotted '16.99' (PLAN D85).

    That value was taken on the user's word over the sample files, every one of
    which carries the un-dotted form; re-confirmed as 6 digits with the data in
    front of them. Do not restore the dot without new information.
    """
    _, d = _convert(tmp_path, registry, config, "DistLabels.OK")
    assert d["header"]["retailPrice"] == "001699"
    assert "." not in d["header"]["retailPrice"]


def test_a_converted_preticket_price_is_six_digits(tmp_path, registry, config):
    _, d = _convert(tmp_path, registry, config, "Preticket.OK")
    row = d["details"][0]
    assert row["retailPrice"] == "599999"
    assert row["compareAtPrice"] == "999999"


def test_a_SHORT_price_is_padded_end_to_end_not_just_by_the_transform(tmp_path,
                                                                      registry,
                                                                      config):
    """The padding half of the rule, proven through a real conversion.

    Every price in every shipped sample is ALREADY six characters, so the
    cross-layout audit cannot see a padding-width bug at all — swapping `pad6`
    for `pad5` moves zero of its 7,799 lines. This test supplies what the
    samples do not: a `.OK` whose price field is space-padded (`'  1699'`,
    which is how a short value sits in a 6-wide fixed-width field) and requires
    the converted JSON to carry `001699`.
    """
    src = tmp_path / "in"
    src.mkdir()
    p = src / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)

    # SPACE-pad the price in place. It has to be done on the bytes: writing
    # '2099' through OkGen's own edit path stores '002099', because a 6-wide
    # field whose sample value is zero-filled is written zero-filled — so that
    # route cannot produce the case this test exists for.
    raw = p.read_bytes()
    assert raw.count(b"002099") == 1, "price is not uniquely locatable"
    p.write_bytes(raw.replace(b"002099", b"  2099"))

    view = service.parse_file_view(p, registry, config)
    stored = view["sections"][0]["records"][0]["values"]["ret_price"]
    assert stored.startswith(" "), f"setup failed: price is {stored!r}"

    res = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]
    d = json.loads(out.read_text(encoding="utf-8"))["data"]
    got = d["details"][0]["retailPrice"]
    assert got == "002099", f"a space-padded price must convert to 6 digits, got {got!r}"
    assert len(got) == 6


def test_every_converted_file_is_SCAN_which_is_why_prices_are_six(tmp_path,
                                                                  registry,
                                                                  config):
    """The load-bearing fact behind the whole 6-digit rule.

    `headerASNid` is read from the `.OK` field `header_asn_id`, and no
    convertible layout has one — so a converted file can never be WMS, and a
    6-digit price can never reach a WMS file by this route.
    """
    for name in ("StyleHeader.OK", "DistLabels.OK", "Preticket.OK",
                 "CartonLabel.OK"):
        if not (DATA_DIR / name).is_file():
            continue
        _, d = _convert(tmp_path, registry, config, name)
        assert d["header"]["headerASNid"] is None, f"{name} converted to a WMS file"


# ------------------------------------------ WMS files keep the 9-digit form ---

def test_a_wms_vendor_file_keeps_its_nine_digit_prices(tmp_path, registry, fixcfg):
    """"Don't touch WMS for now" — the other half of the claim.

    A vendor Style Header carries `000799999`. Opening and saving it must not
    re-pad that to 6: the price transforms live in the CONVERSION mapping only,
    and no write path applies them.
    """
    src = FIX / "styleheader_fmtB.json"
    p = tmp_path / src.name
    shutil.copy2(src, p)
    before = json.loads(p.read_text(encoding="utf-8"))["data"]
    assert before["header"]["headerASNid"], "fixture is not a WMS file"
    assert before["details"][0]["retailPrice"] == "000799999"

    # every write path, not just save — chain isolation was bypassed three times
    # through the bulk paths precisely because only the single path was checked
    service.apply_edits(p, [], registry, config=fixcfg, backup=False)
    view = service.parse_file_view(p, registry, fixcfg)
    service.apply_edits(p, [{"section_index": 0,
                             "record_index": view["sections"][0]["records"][0]["index"],
                             "field": "department", "value": "77"}],
                        registry, config=fixcfg, backup=False)
    service.bulk_multi_apply([str(p)], "CalgaryStyleHeader",
                             [{"section": "Header", "field": "department",
                               "type": "set", "value": "88"}],
                             registry, fixcfg, backup=False)

    after = json.loads(p.read_text(encoding="utf-8"))["data"]
    assert after["details"][0]["retailPrice"] == "000799999"
    assert after["details"][0]["compareAtPrice"] == "000999999"
    # The edits really landed. Without this the test would pass just as happily
    # on a run where the bulk op silently did nothing — which is exactly what
    # happened when `layout` was passed as None while writing this.
    assert after["header"]["department"] == "88"


# ---------------------------------------- Pre-Ticket version / description ---

def test_a_converted_preticket_has_blank_version_and_description(tmp_path,
                                                                 registry,
                                                                 config):
    """The user's call: these are "usually blank in PTs".

    `description` is the important one — the `.OK` Pre-Ticket has no such field,
    so every converted file used to inherit the sample order's product text.
    """
    _, d = _convert(tmp_path, registry, config, "Preticket.OK")
    assert d["header"]["version"] == ""
    assert d["header"]["description"] == ""
    # named explicitly so the assertion cannot pass on a file that simply
    # dropped the keys
    assert "version" in d["header"] and "description" in d["header"]


def test_the_other_conversions_keep_their_description(tmp_path, registry, config):
    """Blanking is scoped to the Pre-Ticket, not applied across the board: a
    Style Header and a Dist Label both have a real `.OK` `desc` field to carry."""
    _, sh = _convert(tmp_path, registry, config, "StyleHeader.OK")
    assert sh["header"]["description"].strip() != ""


# -------------------------------------------------- totalQuantity roll-ups ---

def test_a_converted_preticket_total_is_the_stripped_ok_total(tmp_path, registry,
                                                              config):
    """Copied from the `.OK` and zero-stripped, in the vendor's unpadded form.

    The user chose copy-and-strip over summing at conversion time, on the basis
    that `tot_qty` is itself a roll-up on the `.OK` side now — so an OkGen-saved
    file already holds the right sum. The accepted consequence is visible here:
    the shipped sample has never been saved, so it still carries 0000038 beside
    four rows that sum to 9.
    """
    _, d = _convert(tmp_path, registry, config, "Preticket.OK")
    assert d["header"]["totalQuantity"] == "38"          # stale, and accepted
    assert "0000038" != d["header"]["totalQuantity"]     # ...but stripped

    # save the .OK once and the roll-up makes it agree
    src = tmp_path / "saved"
    src.mkdir()
    p = src / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", p)
    service.apply_edits(p, [], registry, target_path=str(p), config=config,
                        backup=False)
    res = service.convert_apply([str(p)], registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]
    d2 = json.loads(out.read_text(encoding="utf-8"))["data"]
    assert d2["header"]["totalQuantity"] == "9"
    assert sum(int(r["quantity"]) for r in d2["details"]) == 9


def test_a_json_total_is_written_UNPADDED_unlike_the_ok_one(tmp_path, registry,
                                                            fixcfg):
    """Each engine writes the sum in its OWN form.

    A fixed-width `.OK` field must fill its 7 characters or every field after it
    shifts. A JSON value must NOT be padded: all 13 vendor Style Headers carry
    `totalQuantity` as '10'/'12'/'20'/'22' beside 7-padded detail quantities, so
    zero-filling the JSON one would disagree with every real file.
    """
    src = FIX / "preticket.json"
    p = tmp_path / src.name
    shutil.copy2(src, p)
    service.apply_edits(p, [], registry, config=fixcfg, backup=False)
    d = json.loads(p.read_text(encoding="utf-8"))["data"]
    total = d["header"]["totalQuantity"]
    assert total == "2", "a JSON roll-up must not be zero-filled"
    assert total == str(sum(int(r["quantity"]) for r in d["details"]))


def test_the_json_style_header_total_follows_its_detail_rows(tmp_path, registry,
                                                             fixcfg):
    """Editing a detail quantity re-sums the header total — the gap the user
    asked to close for Style Headers as well as Pre-Tickets.

    The vendor files already satisfy this (13 of 13), so a test that merely
    opened one would pass without the rule existing. This EDITS a quantity and
    requires the header to follow.
    """
    src = FIX / "styleheader_fmtB.json"
    p = tmp_path / src.name
    shutil.copy2(src, p)
    view = service.parse_file_view(p, registry, fixcfg)
    di = next(i for i, s in enumerate(view["sections"]) if s["name"] == "Details")
    rec = view["sections"][di]["records"][0]["index"]
    assert json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["totalQuantity"] == "20"

    service.apply_edits(p, [{"section_index": di, "record_index": rec,
                             "field": "quantity", "value": "0000031"}],
                        registry, config=fixcfg, backup=False)
    d = json.loads(p.read_text(encoding="utf-8"))["data"]
    assert d["details"][0]["quantity"] == "0000031"
    assert d["header"]["totalQuantity"] == "31", "the header total did not follow"


def test_dist_label_and_carton_label_declare_no_json_rollup(config):
    """Scoped deliberately: their vendor samples carry `totalQuantity: null` and
    no detail rows at all, so a roll-up would invent a total rather than derive
    one."""
    assert config.rollups("CalgaryDistLabel") in (None, [], {})
    assert config.rollups("CalgaryCartonLabel") in (None, [], {})


# ------------------------------------------------------- lineCount untouched ---

def test_json_line_count_declares_a_width_of_two(registry):
    """`lineCount` rendered as `lineCount (?)` on all four Calgary layouts.

    Two is not a guess: the `.OK` `line_count` is a 2-character fixed-width
    field on both layouts that carry one, and nothing OkGen can produce is
    longer — the vendor samples hold `' '` or `null`, and a converted
    Pre-Ticket holds `'05'`. Declared on all FOUR JSON layouts rather than only
    the Pre-Ticket, because it is the same field wearing the same name, and a
    width declared on one is the kind of thing that silently diverges.

    The `.OK` side is asserted UNCHANGED in the same test: same name, different
    engine (D29), and there a size IS the format, so widening one would shift
    every field after it.
    """
    for name in ("CalgaryPreticket", "CalgaryStyleHeader", "CalgaryDistLabel",
                 "CalgaryCartonLabel"):
        f = next(x for s in registry.get(name).sections for x in s.fields
                 if x.name == "lineCount")
        assert f.size == 2, f"{name}.lineCount is {f.size}, not 2"
    for name in ("Preticket", "EUPreticket"):
        f = next(x for s in registry.get(name).sections for x in s.fields
                 if x.name == "line_count")
        assert f.size == 2, f"{name}.line_count moved to {f.size}"


def test_declaring_line_count_moved_no_VALUE(tmp_path, registry, fixcfg):
    """The D34/D39 condition every width declaration has to meet: the values
    stay exactly as they were.

    `lineCount` carries `' '` on the Style Headers and `null` on the Carton and
    Dist Labels — the absent-vs-blank distinction — so the risk of declaring a
    width is that a save starts coercing one into the other, or zero-fills them.
    """
    checked = 0
    for src in sorted(FIX.glob("*.json")):
        p = tmp_path / src.name
        shutil.copy2(src, p)
        before = json.loads(p.read_text(encoding="utf-8"))["data"]["header"]
        if "lineCount" not in before:
            continue
        service.apply_edits(p, [], registry, config=fixcfg, backup=False)
        after = json.loads(p.read_text(encoding="utf-8"))["data"]["header"]
        assert after["lineCount"] == before["lineCount"], src.name
        checked += 1
    assert checked >= 5, "not enough samples exercised"
    # named explicitly, so the check cannot pass vacuously on files that
    # happen to carry neither form
    vals = {json.loads((FIX / n).read_text(encoding="utf-8"))["data"]["header"]["lineCount"]
            for n in ("styleheader_fmtB.json", "cartonlabel_minified.json")}
    assert vals == {" ", None}


def test_line_count_is_now_writable_and_refuses_an_over_long_value(tmp_path,
                                                                   registry,
                                                                   fixcfg):
    """A field with no declared width is refused by Bulk Edit, omitted from the
    Volume Generate panel and silently skipped by Generate through the API
    (v0.97.0 / v0.108.0). Declaring 2 is what makes it editable on every path —
    and the refusal must NAME the width, not merely fail."""
    p = tmp_path / "sh.json"
    shutil.copy2(FIX / "styleheader_fmtB.json", p)
    service.apply_edits(p, [{"section_index": 0, "record_index": 0,
                             "field": "lineCount", "value": "12"}],
                        registry, config=fixcfg, backup=False)
    assert json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["lineCount"] == "12"

    with pytest.raises(Exception) as exc:
        service.apply_edits(p, [{"section_index": 0, "record_index": 0,
                                 "field": "lineCount", "value": "123"}],
                            registry, config=fixcfg, backup=False)
    assert "lineCount" in str(exc.value) and "2" in str(exc.value)
    # the refusal left the previous value alone
    assert json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["lineCount"] == "12"


def test_line_count_is_still_copied_and_is_NOT_a_count(tmp_path, registry, config):
    """The user withdrew this one after seeing the vendor data: all 13 vendor
    Style Headers carry `lineCount` as a single space, and there is no vendor
    Pre-Ticket to check against.

    Pinned as a GUARD, not as an aspiration — if someone later wires `lineCount`
    into `counts:`, this fails and points at the decision.
    """
    _, d = _convert(tmp_path, registry, config, "Preticket.OK")
    assert d["header"]["lineCount"] == "05"       # the .OK header's own value
    assert len(d["details"]) == 4                 # ...which disagrees, by design


# ------------------------------------------------- the Total Qty sweep ---

def test_the_sweep_now_covers_pretickets(tmp_path, registry, config):
    p = tmp_path / "Preticket.OK"
    shutil.copy(DATA_DIR / "Preticket.OK", p)
    res = service.total_qty_scan([str(p)], registry, config, apply=False)
    assert res["results"][0]["status"] == "would_fix"
    assert res["results"][0]["to"] == "0000009"


def test_the_sweep_still_skips_json_but_no_longer_lies_about_why(tmp_path,
                                                                 registry,
                                                                 config):
    """JSON layouts DO declare roll-ups now, so the old reason ("JSON layouts
    declare no roll-up") became untrue. The sweep is still `.OK`-only — it
    exists for the fixed-width backlog — but it has to say so honestly."""
    p = tmp_path / "preticket.json"
    shutil.copy(FIX / "preticket.json", p)
    res = service.total_qty_scan([str(p)], registry, config, apply=False)
    assert res["results"][0]["status"] == "skipped"
    assert "declare no roll-up" not in res["results"][0]["detail"]
    assert "on save" in res["results"][0]["detail"]


def test_the_sweep_on_a_style_header_is_unchanged(tmp_path, registry, config):
    """The user's explicit condition: the Total Qty check must work as before.
    A Style Header still reports the same field, section and correction."""
    p = tmp_path / "StyleHeader.OK"
    shutil.copy(DATA_DIR / "StyleHeader.OK", p)
    res = service.total_qty_scan([str(p)], registry, config, apply=False)
    r = res["results"][0]
    assert r["status"] == "would_fix"
    assert r["field"] == "tot_qty" and r["section"] == "Size"
    assert r["from"] == "0000022" and r["to"] == "0000008"
