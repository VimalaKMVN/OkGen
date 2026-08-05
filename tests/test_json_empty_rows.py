"""Keeping a JSON array section's tags when an operation removes all its rows.

D43 made emptying a Calgary JSON array actually write, which meant it wrote
`"lanes": []`. A bare `[]` tells the consuming system nothing about the shape
the section should have had, so OkGen now keeps ONE row with every field
present and empty — null by default, or whatever `json_empty_rows.yaml`
declares for that field.

Two things these lock down, because both are easy to lose:

* the rule reaches EVERY write path, not just the one it was reported on
  (`_apply_json_empty_rows` sits beside `_apply_detail_fill` and is called from
  the same five sites — the D16/D30 lesson); and
* an array that was ALREADY `[]` or `null` on disk is left exactly as it is, so
  an untouched file still round-trips byte-for-byte (D20).
"""
import json
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

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


def _copy(tmp_path, fixture, name="f.json"):
    p = tmp_path / name
    p.write_bytes((FIX / fixture).read_bytes())
    return p


def _at(path, jpath):
    cur = json.loads(Path(path).read_text(encoding="utf-8"))["data"]
    for step in jpath:
        cur = cur.get(step)
        if cur is None:
            return None
    return cur


def _empty_stores(p, layout, registry, config):
    return service.bulk_op_apply([str(p)], layout, "Stores",
                                 {"type": "keep", "count": 0},
                                 registry, config, backup=False)


# --------------------------------------------------------------------------- #
# The shape
# --------------------------------------------------------------------------- #
def test_an_emptied_array_keeps_one_row_with_its_tags(tmp_path, registry, config):
    p = _copy(tmp_path, "distlabel.json")
    assert len(_at(p, ("header", "stores"))) == 10

    _empty_stores(p, "CalgaryDistLabel", registry, config)

    rows = _at(p, ("header", "stores"))
    assert len(rows) == 1, "the section's tags must survive, not become []"
    assert set(rows[0]) == set(
        json.loads((FIX / "distlabel.json").read_text())["data"]["header"]["stores"][0]
    ), "the kept row must carry exactly the fields a real row carries"


def test_every_kept_value_is_empty(tmp_path, registry, config):
    """The point of the row is its TAGS. A value that survived would be data
    borrowed from the rows the user just deleted."""
    p = _copy(tmp_path, "distlabel.json")
    _empty_stores(p, "CalgaryDistLabel", registry, config)

    row = _at(p, ("header", "stores"))[0]
    assert all(v in (None, "") for v in row.values()), row


def test_config_decides_which_fields_empty_to_a_string(tmp_path, registry, config):
    """`lane` is declared as "" and the size fields as null, so the two must
    come out differently — that is the whole reason this is config."""
    p = _copy(tmp_path, "styleheader_fmtB.json")

    service.bulk_op_apply([str(p)], "CalgaryStyleHeader", "Lanes",
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)
    service.bulk_op_apply([str(p)], "CalgaryStyleHeader", "Sizes",
                          {"type": "keep", "count": 0}, registry, config,
                          backup=False)

    assert _at(p, ("header", "lanes")) == [{"lane": ""}]
    assert _at(p, ("header", "sizes")) == [{"size": None, "quantity": None}]


def test_an_undeclared_field_defaults_to_null(tmp_path, registry, config):
    """A section nobody has written config for still behaves — otherwise a new
    layout would silently go back to writing []."""
    p = _copy(tmp_path, "styleheader_fmtB.json")
    _empty_stores(p, "CalgaryStyleHeader", registry, config)

    row = _at(p, ("header", "stores"))
    assert len(row) == 1
    assert all(v is None for v in row[0].values()), row[0]


# --------------------------------------------------------------------------- #
# Every write path — the D16/D30 rule
# --------------------------------------------------------------------------- #
def test_the_single_file_editor_delete_also_keeps_the_tags(tmp_path, registry, config):
    """The same defect had two entrances in D43; so does this rule."""
    p = _copy(tmp_path, "distlabel.json")
    view = service.parse_file_view(p, registry, config)
    idxs = [r["index"] for s in view["sections"] if s["name"] == "Stores"
            for r in (s.get("records") or [])]

    service.apply_edits(p, [], registry, config=config, backup=False,
                        ops=[{"type": "delete", "record_index": i}
                             for i in reversed(idxs)])

    rows = _at(p, ("header", "stores"))
    assert len(rows) == 1 and all(v in (None, "") for v in rows[0].values())


def test_generate_volume_files_keep_the_tags(tmp_path, registry, config):
    """Generate writes through its own path, so it needs its own proof."""
    p = _copy(tmp_path, "distlabel.json")
    _empty_stores(p, "CalgaryDistLabel", registry, config)

    res = service.generate_apply([str(p)], {"count": 3}, registry, config)

    out = sorted(Path(res["folder"]).iterdir())
    assert len(out) == 3
    for f in out:
        rows = _at(f, ("header", "stores"))
        assert len(rows) == 1, f"{f.name} lost the section's tags"
        assert all(v in (None, "") for v in rows[0].values())


# --------------------------------------------------------------------------- #
# What it must NOT do
# --------------------------------------------------------------------------- #
def test_a_file_already_holding_an_empty_array_is_left_alone(tmp_path, registry, config):
    """Only an array THIS operation empties gets a skeleton. Filling in one the
    file already shipped would rewrite an untouched file and break D20."""
    p = _copy(tmp_path, "distlabel.json")
    _empty_stores(p, "CalgaryDistLabel", registry, config)
    emptied = p.read_bytes()

    service.apply_edits(p, [], registry, config=config, backup=False)

    assert p.read_bytes() == emptied, "a resave must not churn the file"


def test_a_null_array_is_not_turned_into_a_row(tmp_path, registry, config):
    """DistLabel and CartonLabel ship `lanes`/`sizes` as JSON null. That is not
    an emptied array and must stay null — inventing a row would claim the
    layout has a section it does not."""
    p = _copy(tmp_path, "distlabel.json")
    before = json.loads(p.read_text())["data"]["header"]
    assert before.get("lanes") is None and before.get("sizes") is None

    _empty_stores(p, "CalgaryDistLabel", registry, config)

    after = json.loads(p.read_text())["data"]["header"]
    assert after.get("lanes") is None and after.get("sizes") is None


def test_a_section_that_still_has_rows_is_untouched(tmp_path, registry, config):
    """The skeleton must fire only on a section that lost EVERY row."""
    p = _copy(tmp_path, "distlabel.json")
    service.bulk_op_apply([str(p)], "CalgaryDistLabel", "Stores",
                          {"type": "keep", "count": 3}, registry, config,
                          backup=False)

    rows = _at(p, ("header", "stores"))
    assert len(rows) == 3
    assert any(v not in (None, "") for v in rows[0].values()), "real data was wiped"


def test_the_emptied_file_still_opens_in_okgen(tmp_path, registry, config):
    """A kept row is a real row, so the section reads back as 1 blank row —
    the file must still detect, parse and be editable."""
    p = _copy(tmp_path, "distlabel.json")
    _empty_stores(p, "CalgaryDistLabel", registry, config)

    json.loads(p.read_text(encoding="utf-8"))
    view = service.parse_file_view(p, registry, config)
    assert view["layout"] == "CalgaryDistLabel"
    sec = next(s for s in view["sections"] if s["name"] == "Stores")
    assert len(sec.get("records") or []) == 1


def test_ok_files_are_unaffected(tmp_path, registry, config):
    """`.OK` layouts have no JSON arrays; the pass must be a no-op for them
    rather than an exception on the shared write path."""
    src = DATA_DIR / "StyleHeader.OK"
    p = tmp_path / "sh.OK"
    p.write_bytes(src.read_bytes())

    # D26 normalizes post-terminator padding on the FIRST save, so the
    # guarantee is idempotence: a second save changes nothing further.
    service.apply_edits(p, [], registry, config=config, backup=False)
    settled = p.read_bytes()
    service.apply_edits(p, [], registry, config=config, backup=False)

    assert p.read_bytes() == settled
