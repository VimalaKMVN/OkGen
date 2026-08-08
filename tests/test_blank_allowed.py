"""Clearing an ad-date field blanks it, instead of writing a number.

A fixed-width field is written using the justification inferred from its SAMPLE
value, and a sample like ``0000`` makes the field right-justified and '0'-filled.
That is correct for a value and wrong for an absence — ``"".rjust(4, "0")`` is
``"0000"`` — so clearing the box produced a date of zeros, and the ONLY way to
blank the field was to type exactly its width in spaces:

    ""     -> "0000"
    "  "   -> "00  "
    "    " -> "    "     (the one that worked, and nobody guesses it)

User-reported on ``CartonLabel.store.dad_date`` and ``DistLabels.Store.ad``.
Those two are declared in ``field_display.yaml``'s ``blank_allowed`` list; the
behaviour is deliberately NOT global (see the config comment) so every other
zero-padded field keeps padding exactly as it did, which the control tests below
pin. A real value is unaffected: ``531`` still becomes ``0531``.

Bulk Edit and Volume Generate are untouched — they already have an explicit
quoted-blank token (``' '``) that writes literal spaces on any field.
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


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _edit(tmp_path, registry, config, sample, section, field, value):
    """Write ``value`` into the first row of ``section`` and read it back."""
    p = tmp_path / sample
    shutil.copy(DATA_DIR / sample, p)
    okf = parse_okfile(p, registry=registry)
    si = next(i for i, s in enumerate(okf.layout.sections) if s.name == section)
    rec = next(r for r in okf.records if r.section and r.section.name == section)
    service.apply_edits(str(p), [{"section_index": si, "record_index": rec.index,
                                  "field": field, "value": value}],
                        registry, config=config, backup=False)
    back = next(r for r in parse_okfile(p, registry=registry).records
                if r.section and r.section.name == section)
    return back.get(field)


TARGETS = [
    ("CartonLabel.OK", "store", "dad_date"),
    ("DistLabels.OK", "Store", "ad"),
]


# --------------------------------------------------------------------------- #
# The reported bug: an emptied box must blank the field
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sample,section,field", TARGETS)
@pytest.mark.parametrize("typed", ["", " ", "  ", "    "])
def test_an_emptied_field_is_blanked(tmp_path, registry, config,
                                     sample, section, field, typed):
    """Every way of saying "nothing" gives the same blank — not just the one
    that happens to match the field width."""
    assert _edit(tmp_path, registry, config, sample, section, field, typed) == "    "


@pytest.mark.parametrize("sample,section,field", TARGETS)
def test_the_record_keeps_its_exact_length(tmp_path, registry, config,
                                           sample, section, field):
    """A blank is spaces of the FIELD'S width — a shorter write would shift
    every field after it (D3).

    Asserted on the RECORD, not the file: saving a CartonLabel also strips its
    post-terminator padding (D26 junk normalisation), so the file legitimately
    shrinks and its size proves nothing here.
    """
    p = tmp_path / sample
    shutil.copy(DATA_DIR / sample, p)
    okf = parse_okfile(p, registry=registry)
    si = next(i for i, s in enumerate(okf.layout.sections) if s.name == section)
    rec = next(r for r in okf.records if r.section and r.section.name == section)
    before_len = len(rec.raw)
    siblings = {f.name: rec.get(f.name) for f in rec.section.fields if f.name != field}

    service.apply_edits(str(p), [{"section_index": si, "record_index": rec.index,
                                  "field": field, "value": ""}],
                        registry, config=config, backup=False)

    after = next(r for r in parse_okfile(p, registry=registry).records
                 if r.section and r.section.name == section)
    assert len(after.raw) == before_len
    # Every other field of the row reads exactly as it did — the blank did not
    # move the span boundaries.
    assert {f.name: after.get(f.name)
            for f in after.section.fields if f.name != field} == siblings


# --------------------------------------------------------------------------- #
# A REAL value is untouched — this changes the empty case only
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sample,section,field", TARGETS)
@pytest.mark.parametrize("typed,expected", [("531", "0531"), ("0531", "0531"),
                                            ("1", "0001")])
def test_a_real_value_still_zero_pads(tmp_path, registry, config,
                                      sample, section, field, typed, expected):
    assert _edit(tmp_path, registry, config, sample, section, field, typed) == expected


# --------------------------------------------------------------------------- #
# Scope: NOTHING else changes. These are the control cases — an undeclared
# zero-padded field in the SAME section must still pad an empty value.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sample,section,field,expected", [
    ("CartonLabel.OK", "store", "store", "0000"),
    ("CartonLabel.OK", "store", "cart_seq", "00000"),
    ("CartonLabel.OK", "store", "sequence", "000000"),
    ("DistLabels.OK", "Store", "store", "0000"),
    ("DistLabels.OK", "Store", "units", "00000"),
    ("DistLabels.OK", "Store", "seq", "000000"),
])
def test_an_undeclared_field_still_pads(tmp_path, registry, config,
                                        sample, section, field, expected):
    """Deliberately NOT a global rule — declaring one field must not quietly
    change every zero-padded field on the layout."""
    assert _edit(tmp_path, registry, config, sample, section, field, "") == expected


def test_the_carton_header_ad_date_is_not_declared(tmp_path, registry, config):
    """CartonLabel's HEADER ad_date samples spaces, so it always blanked
    correctly and needs no declaration — a reminder that the two fields differ
    only by what their reference file happens to carry."""
    assert not config.allows_blank("CartonLabel", "ad_date")
    assert _edit(tmp_path, registry, config,
                 "CartonLabel.OK", "Header", "ad_date", "") == "    "


# --------------------------------------------------------------------------- #
# The declaration itself
# --------------------------------------------------------------------------- #
def test_only_the_two_reported_fields_are_declared(config):
    assert config.allows_blank("CartonLabel", "dad_date")
    assert config.allows_blank("DistLabels", "ad")
    # Not on other layouts, and not on fields that were never reported.
    assert not config.allows_blank("StyleHeader", "dad_date")
    assert not config.allows_blank("DistLabels", "store")
    assert not config.allows_blank("EUStyleHeader", "ad_date")


def test_an_absent_config_block_declares_nothing(tmp_path):
    """A config with no `blank_allowed:` block must declare nothing — an empty
    field_display.yaml cannot silently turn the rule on."""
    (tmp_path / "field_display.yaml").write_text("hidden: {}\n")
    cfg = Config.load(tmp_path)
    assert cfg.allows_blank("CartonLabel", "dad_date") is False
    assert cfg.blank_allowed_fields("CartonLabel") == set()


# --------------------------------------------------------------------------- #
# Bulk is untouched: it keeps its own quoted-blank token
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sample,layout,section,field", [
    ("CartonLabel.OK", "CartonLabel", "store", "dad_date"),
    ("DistLabels.OK", "DistLabels", "Store", "ad"),
])
def test_bulk_still_blanks_through_its_token(tmp_path, registry, config,
                                             sample, layout, section, field):
    p = tmp_path / sample
    shutil.copy(DATA_DIR / sample, p)
    service.bulk_op_apply([str(p)], layout, section,
                          {"type": "set", "field": field, "value": "' '"},
                          registry, config, backup=False)
    rows = [r.get(field) for r in parse_okfile(p, registry=registry).records
            if r.section and r.section.name == section]
    assert rows and all(v == "    " for v in rows)
