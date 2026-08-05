"""Adding to a JSON section with no data REPLACES its blank rows.

D45 keeps one tag-carrying row when a section is emptied, so the consuming
system still sees the field names instead of a bare `[]`. That row is a real
row on disk, so adding afterwards left it sitting above the new row — and every
later add stacked on top of it:

    emptied          -> 1 blank
    + add            -> 1 blank + 1 real
    + bulk add 3     -> 1 blank + 3 real
    + generate n=3   -> 1 blank + 2 real   <- and only TWO real stores

Generate was the worst of the three: it counted the blank toward the target, so
asking for 3 stores produced 2.

The rule now: when a row is added to a JSON section whose rows are ALL blank,
those rows are dropped first — the section held no data, so the row being added
is its first.

This CANNOT be done by recognising D45's marker. An emptied `lanes` marker is
`{"lane": ""}`, byte-identical to the row the vendor ships. So the rule is about
blankness, and the accepted consequence is that a genuinely blank vendor row is
replaced by the first row added to that section
(`styleheader_fmtB` ships exactly one). A blank row sitting among REAL rows is
never touched — that is the user's own row, not a placeholder.

JSON only: `.OK` sections are fill-managed (D16) and untouched.
"""
import json
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


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _rows(path, key):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]["header"][key]


def _blank(row):
    return all(v in (None, "") or (isinstance(v, str) and not v.strip())
               for v in row.values())


def _counts(path, key):
    rows = _rows(path, key)
    return len(rows), sum(1 for r in rows if _blank(r))


def _si(path, name, registry, config):
    view = service.parse_file_view(path, registry, config)
    return next(i for i, s in enumerate(view["sections"]) if s["name"] == name)


def _emptied(tmp_path, registry, config, fixture="distlabel.json",
             layout="CalgaryDistLabel", section="Stores"):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / fixture, p)
    service.bulk_op_apply([str(p)], layout, section, {"type": "keep", "count": 0},
                          registry, config, backup=False)
    return p


# --------------------------------------------------------------------------- #
# The three add paths
# --------------------------------------------------------------------------- #
def test_single_add_replaces_the_marker(tmp_path, registry, config):
    p = _emptied(tmp_path, registry, config)
    assert _counts(p, "stores") == (1, 1), "fixture must start as one blank row"

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert _counts(p, "stores") == (1, 0)


def test_a_second_add_stacks_normally(tmp_path, registry, config):
    """Only the no-data state is replaced; after that, adds accumulate."""
    p = _emptied(tmp_path, registry, config)
    for _ in range(2):
        service.add_record(p, _si(p, "Stores", registry, config), [],
                           registry, config, preview=False, backup=False)

    assert _counts(p, "stores") == (2, 0)


@pytest.mark.parametrize("n", [1, 3, 5])
def test_bulk_add_yields_exactly_n_real_rows(tmp_path, registry, config, n):
    p = _emptied(tmp_path, registry, config)

    service.bulk_op_apply([str(p)], "CalgaryDistLabel", "Stores",
                          {"type": "add", "count": n}, registry, config, backup=False)

    assert _counts(p, "stores") == (n, 0)


@pytest.mark.parametrize("n", [1, 3, 8])
def test_generate_yields_exactly_n_real_rows(tmp_path, registry, config, n):
    """The sharpest case: the blank used to count toward the target, so asking
    for 3 stores produced 2 real ones."""
    p = _emptied(tmp_path, registry, config)

    res = service.generate_apply(
        [str(p)], {"count": 1,
                   "row_counts": [{"section": "Stores", "min": n, "max": n}]},
        registry, config)

    out = sorted(Path(res["folder"]).iterdir())[0]
    assert _counts(out, "stores") == (n, 0)


# --------------------------------------------------------------------------- #
# Sections where the marker is indistinguishable from a vendor row
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("section,key", [("Lanes", "lanes"), ("Sizes", "sizes")])
def test_it_works_where_the_marker_looks_like_a_real_row(tmp_path, registry, config,
                                                         section, key):
    """An emptied `lanes` marker is `{"lane": ""}` — exactly what the vendor
    ships. A rule that tried to RECOGNISE the marker would fail here."""
    p = _emptied(tmp_path, registry, config, "styleheader_fmtB.json",
                 "CalgaryStyleHeader", section)

    service.add_record(p, _si(p, section, registry, config), [], registry, config,
                       preview=False, backup=False)

    assert _counts(p, key) == (1, 0)


def test_a_vendor_shipped_blank_row_is_replaced(tmp_path, registry, config):
    """The accepted trade. `styleheader_fmtB` ships one blank store row; adding
    a store replaces it, because an all-blank section holds no data and nothing
    distinguishes that row from D45's marker."""
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "styleheader_fmtB.json", p)
    assert _counts(p, "stores") == (1, 1), "fixture must ship one blank store"

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert _counts(p, "stores") == (1, 0)


# --------------------------------------------------------------------------- #
# What must NOT be replaced
# --------------------------------------------------------------------------- #
def test_a_populated_section_is_untouched(tmp_path, registry, config):
    p = tmp_path / "f.json"
    shutil.copy2(FIX / "distlabel.json", p)
    before = _rows(p, "stores")
    assert len(before) == 10

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    after = _rows(p, "stores")
    assert len(after) == 11
    assert after[:10] == before, "existing rows must keep their exact values"


def test_a_blank_row_among_real_rows_is_kept(tmp_path, registry, config):
    """All-or-nothing: a blank row the user put between real ones is theirs."""
    doc = json.loads((FIX / "distlabel.json").read_text(encoding="utf-8"))
    stores = doc["data"]["header"]["stores"]
    blank = {k: "" for k in stores[0]}
    doc["data"]["header"]["stores"] = [stores[0], blank, stores[1]]
    p = tmp_path / "f.json"
    p.write_text(json.dumps(doc, indent=4), encoding="utf-8")

    service.add_record(p, _si(p, "Stores", registry, config), [], registry, config,
                       preview=False, backup=False)

    total, blanks = _counts(p, "stores")
    assert total == 4 and blanks == 1, "the interior blank row must survive"


def test_an_emptied_section_still_reports_its_tags_until_a_row_is_added(
        tmp_path, registry, config):
    """The D45 guarantee is unchanged — emptying still leaves the tag row. It is
    only replaced when a row is actually added."""
    p = _emptied(tmp_path, registry, config)
    rows = _rows(p, "stores")
    assert len(rows) == 1 and rows[0], "tags must still be there"
    assert all(v is None for v in rows[0].values())


# --------------------------------------------------------------------------- #
# .OK must not move
# --------------------------------------------------------------------------- #
def test_the_rule_never_fires_on_an_ok_layout(registry, config):
    """The helper is the single gate, so pin it directly: no `.OK` section can
    ever be selected for replacement, whatever its rows look like. Preticket's
    Lane is the sharpest case — its trailing filler rows ARE all-zero blanks,
    and they are D16's structural filler, not placeholders to be thrown away."""
    p = Path(DATA_DIR) / "Preticket.OK"
    okf = parse_okfile(p, registry=registry)
    for sec in okf.layout.sections:
        assert service._json_blank_rows_to_replace(okf, sec, config) == [], sec.name


def test_an_ok_add_behaves_exactly_as_before(tmp_path, registry, config):
    """Preticket Lane goes 23 -> 15 on an add: the fill pass keeps the real rows
    plus exactly 10 filler (D16). That is pre-existing behaviour, verified
    unchanged against v0.65.1 — recorded here so a future edit to the blank-row
    rule cannot quietly alter it."""
    p = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", p)
    assert len(parse_okfile(p, registry=registry).sections()["Lane"]) == 23

    service.add_record(p, _si(p, "Lane", registry, config), [], registry, config,
                       preview=False, backup=False)

    assert len(parse_okfile(p, registry=registry).sections()["Lane"]) == 15
