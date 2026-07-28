"""Calgary JSON parity with the .OK layouts — rows, file ops, naming.

Field editing already worked (D20); what did NOT was anything that changes the
NUMBER or ORDER of rows. Those operations reported success and wrote nothing,
because the engine spliced changed scalar values into the original text and a
row that never existed has no span to splice. The editor showed the added row,
Save reported success, and the file was unchanged.

So the tests that matter most here are the ones that assert a structural change
actually reached the DISK, plus the two guarantees it must not break:

* every untouched row keeps its exact bytes (the D3/D20 promise), and
* a change the engine cannot represent FAILS LOUDLY rather than silently
  writing a subset.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.jsonengine import scan_spans
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(
    os.environ.get("OKGEN_DATA_DIR",
                   str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions")))
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="no calgary fixtures")

# (fixture, layout, an array section with rows, the JSON path to those rows)
ROW_CASES = [
    ("styleheader_fmtB.json", "CalgaryStyleHeader", "Stores", ("header", "stores")),
    ("styleheader_fmtB.json", "CalgaryStyleHeader", "Details", ("details",)),
    ("distlabel.json", "CalgaryDistLabel", "Stores", ("header", "stores")),
    ("cartonlabel_minified.json", "CalgaryCartonLabel", "Stores", ("header", "stores")),
]


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _rows(path, jpath):
    cur = json.loads(Path(path).read_text(encoding="utf-8"))["data"]
    for step in jpath:
        cur = cur.get(step) or []
    return cur


def _copy(tmp_path, fixture, name="f.json"):
    p = tmp_path / name
    p.write_bytes((FIX / fixture).read_bytes())
    return p


def _section_index(view, name):
    return next(i for i, s in enumerate(view["sections"]) if s["name"] == name)


def _ensure_two_rows(path, registry, config, section, jpath):
    """Grow a section to 2+ rows so delete/move can be tested on it.

    Several fixtures ship a single row in a section; skipping those would leave
    the riskiest operations covered on only one layout. Adding first exercises
    the add path as a side effect.
    """
    for _ in range(4):                      # bounded: if add silently does
        if len(_rows(path, jpath)) >= 2:    # nothing, fail loudly rather than
            return                          # looping forever
        si = _section_index(service.parse_file_view(path, registry, config), section)
        service.add_record(path, si, [], registry, config,
                           preview=False, backup=False)
    raise AssertionError(
        f"could not grow {section} to 2 rows — add_record is not persisting")


# --------------------------------------------------------------------------- #
# Row ops reach the disk
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fixture,layout,section,jpath", ROW_CASES)
def test_adding_a_row_is_actually_saved(tmp_path, registry, config,
                                        fixture, layout, section, jpath):
    p = _copy(tmp_path, fixture)
    before = len(_rows(p, jpath))
    si = _section_index(service.parse_file_view(p, registry, config), section)

    service.add_record(p, si, [], registry, config, preview=False, backup=False)

    assert len(_rows(p, jpath)) == before + 1
    json.loads(p.read_text(encoding="utf-8"))          # still valid JSON


@pytest.mark.parametrize("fixture,layout,section,jpath", ROW_CASES)
def test_preview_writes_nothing(tmp_path, registry, config,
                                fixture, layout, section, jpath):
    p = _copy(tmp_path, fixture)
    original = p.read_bytes()
    view = service.parse_file_view(p, registry, config)
    si = _section_index(view, section)

    pv = service.add_record(p, si, [], registry, config, preview=True)

    assert len(pv["sections"][si]["records"]) == len(view["sections"][si]["records"]) + 1
    assert p.read_bytes() == original, "a preview must not touch the file"


@pytest.mark.parametrize("fixture,layout,section,jpath", ROW_CASES)
def test_deleting_a_row_is_actually_saved(tmp_path, registry, config,
                                          fixture, layout, section, jpath):
    p = _copy(tmp_path, fixture)
    _ensure_two_rows(p, registry, config, section, jpath)
    before = _rows(p, jpath)
    view = service.parse_file_view(p, registry, config)
    si = _section_index(view, section)
    target = view["sections"][si]["records"][1]["index"]

    service.delete_record(p, target, [], registry, config, preview=False, backup=False)

    after = _rows(p, jpath)
    assert len(after) == len(before) - 1
    # the row that went is the one asked for; the others keep their order
    assert after == before[:1] + before[2:]


@pytest.mark.parametrize("fixture,layout,section,jpath", ROW_CASES)
def test_moving_a_row_is_actually_saved(tmp_path, registry, config,
                                        fixture, layout, section, jpath):
    p = _copy(tmp_path, fixture)
    _ensure_two_rows(p, registry, config, section, jpath)
    before = _rows(p, jpath)
    view = service.parse_file_view(p, registry, config)
    si = _section_index(view, section)
    second = view["sections"][si]["records"][1]["index"]

    service.move_record(p, second, "up", [], registry, config,
                        preview=False, backup=False)

    after = _rows(p, jpath)
    assert after[0] == before[1] and after[1] == before[0]
    assert after[2:] == before[2:]


def test_a_new_row_keeps_the_values_edited_into_it(tmp_path, registry, config):
    p = _copy(tmp_path, "distlabel.json")
    view = service.parse_file_view(p, registry, config)
    si = _section_index(view, "Stores")
    n = len(view["sections"][si]["records"])

    pv = service.add_record(p, si, [], registry, config, preview=True)
    new_row = pv["sections"][si]["records"][n]
    service.apply_edits(p, [{"record_index": new_row["index"],
                             "field": "store", "value": "Z999"}],
                        registry, config=config, backup=False,
                        ops=[{"type": "add", "section_index": si}])

    stores = _rows(p, ("header", "stores"))
    assert len(stores) == n + 1
    assert stores[-1]["store"] == "Z999"


# --------------------------------------------------------------------------- #
# ...without breaking what made the JSON engine worth having
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fixture,layout,section,jpath", ROW_CASES)
def test_untouched_rows_keep_their_exact_bytes(tmp_path, registry, config,
                                               fixture, layout, section, jpath):
    """A row op must not reformat the file — only the edited array may change."""
    p = _copy(tmp_path, fixture)
    _ensure_two_rows(p, registry, config, section, jpath)
    original = p.read_text(encoding="utf-8")
    spans = scan_spans(original)
    apath = ("data",) + tuple(jpath)
    kept_source = original[spans[apath + (0,)][0]:spans[apath + (0,)][1]]
    view = service.parse_file_view(p, registry, config)
    si = _section_index(view, section)

    service.delete_record(p, view["sections"][si]["records"][1]["index"], [],
                          registry, config, preview=False, backup=False)

    after = p.read_text(encoding="utf-8")
    # Everything before the edited array is byte-identical...
    assert after[:spans[apath][0]] == original[:spans[apath][0]]
    # ...everything after it is too...
    assert after[len(after) - (len(original) - spans[apath][1]):] == \
        original[spans[apath][1]:]
    # ...and the surviving row keeps its own source text exactly.
    assert kept_source in after


@pytest.mark.parametrize("fixture,layout,section,jpath", ROW_CASES)
def test_a_plain_open_and_save_is_still_byte_exact(tmp_path, registry, config,
                                                   fixture, layout, section, jpath):
    """The structural machinery must not disturb the untouched case."""
    from okgen.okfile import parse_okfile
    p = _copy(tmp_path, fixture)
    okf = parse_okfile(p, registry=registry)
    assert okf.to_bytes() == p.read_bytes()


def test_adding_rows_to_an_array_the_file_lacks_fails_loudly(tmp_path, registry, config):
    """The guarantee that replaces the old silent no-op.

    Better a clear refusal than a save that reports success and drops the work.
    """
    doc = json.loads((FIX / "styleheader_fmtB.json").read_text(encoding="utf-8"))
    del doc["data"]["header"]["sizes"]
    p = tmp_path / "no_sizes.json"
    p.write_text(json.dumps(doc, indent=2))
    original = p.read_bytes()
    si = _section_index(service.parse_file_view(p, registry, config), "Sizes")

    with pytest.raises(service.EditError, match="not an array"):
        service.add_record(p, si, [], registry, config, preview=False, backup=False)
    assert p.read_bytes() == original, "a refused save must leave the file alone"


def test_the_first_row_of_an_empty_section_is_seeded_blank(tmp_path, registry, config):
    doc = json.loads((FIX / "styleheader_fmtB.json").read_text(encoding="utf-8"))
    doc["data"]["header"]["sizes"] = []
    p = tmp_path / "empty_sizes.json"
    p.write_text(json.dumps(doc, indent=2))
    si = _section_index(service.parse_file_view(p, registry, config), "Sizes")

    service.add_record(p, si, [], registry, config, preview=False, backup=False)

    sizes = _rows(p, ("header", "sizes"))
    assert len(sizes) == 1
    assert set(sizes[0].values()) == {""}, "a seeded row starts blank"


def test_a_nested_row_array_cannot_be_overwritten_as_a_value(tmp_path, registry, config):
    """DistLabel declares `stores` as a header field AND a section — setting it
    as a string would destroy every row in it."""
    p = _copy(tmp_path, "distlabel.json")
    with pytest.raises(Exception):
        service.apply_edits(p, [{"record_index": 0, "field": "stores", "value": "x"}],
                            registry, config=config, backup=False)


# --------------------------------------------------------------------------- #
# Bulk row ops
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("op,expected", [
    ({"type": "keep", "count": 3}, 3),
    ({"type": "add", "count": 2}, 12),
])
def test_bulk_row_ops_reach_the_disk(tmp_path, registry, config, op, expected):
    paths = []
    for i in range(2):
        paths.append(str(_copy(tmp_path, "distlabel.json", f"f{i}.json")))
    assert len(_rows(paths[0], ("header", "stores"))) == 10

    res = service.bulk_op_apply(paths, "CalgaryDistLabel", "Stores", op,
                                registry, config, backup=False)

    assert all(r["status"] == "changed" for r in res["results"])
    for p in paths:
        assert len(_rows(p, ("header", "stores"))) == expected
        json.loads(Path(p).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Naming + file ops
# --------------------------------------------------------------------------- #
def test_bulk_rename_keeps_the_json_extension(tmp_path, registry, config):
    """Renaming used to produce `X.OK` holding JSON — wrong for anything
    downstream that selects files by extension."""
    p = _copy(tmp_path, "distlabel.json")
    res = service.bulk_rename_apply([str(p)], [{"type": "token", "name": "layout"}],
                                    "_", registry, config)
    assert res["results"]
    names = [f.name for f in tmp_path.iterdir()]
    assert all(n.endswith(".json") for n in names), names


def test_generated_files_keep_the_json_extension(tmp_path, registry, config):
    p = _copy(tmp_path, "distlabel.json")
    res = service.generate_apply([str(p)], {"count": 3}, registry, config)
    out = sorted(Path(res["folder"]).iterdir())
    assert len(out) == 3
    for f in out:
        assert f.suffix == ".json", f.name
        json.loads(f.read_text(encoding="utf-8"))


def test_ok_files_still_get_the_ok_extension(tmp_path, registry, config):
    """The rename fix must not change behaviour for the seven .OK layouts."""
    p = tmp_path / "StyleHeader.OK"
    p.write_bytes((DATA_DIR / "StyleHeader.OK").read_bytes())
    service.bulk_rename_apply([str(p)], [{"type": "token", "name": "layout"}],
                              "_", registry, config)
    assert all(f.suffix == ".OK" for f in tmp_path.iterdir())


def test_json_files_can_be_copied_deleted_and_renamed(tmp_path, registry, config):
    """These all gated on `is_ok_file`, which silently excluded every JSON file."""
    p = _copy(tmp_path, "distlabel.json")
    dst = tmp_path / "sub"
    dst.mkdir()

    res = service.copy_files([str(p)], str(dst), registry, config)
    assert res["copied"] and not res["errors"]

    copied = next(dst.iterdir())
    service.rename_file(str(copied), str(dst / "renamed.json"))
    assert (dst / "renamed.json").is_file()

    res = service.delete_files([str(dst / "renamed.json")])
    assert res["deleted"] and not res["errors"]


def test_send_to_nicelabel_still_refuses_json(tmp_path, registry, config):
    """Deliberately still gated — the JSON hand-off is a separate design."""
    p = _copy(tmp_path, "distlabel.json")
    try:
        res = service.send_to_nicelabel([str(p)], config)
    except service.EditError:
        return                       # no hot folder configured in tests: fine
    assert res["errors"], "JSON must not be sent until that flow is specified"


def test_clean_up_skips_json_instead_of_pretending(tmp_path, registry, config):
    p = _copy(tmp_path, "distlabel.json")
    original = p.read_bytes()
    res = service.clean_files([str(p)], registry)
    assert res["results"][0]["status"] == "skipped"
    assert p.read_bytes() == original


# --------------------------------------------------------------------------- #
# type / timestamp
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fixture,layout,label", [
    ("styleheader_fmtB.json", "CalgaryStyleHeader", "styleHeaders"),
    ("distlabel.json", "CalgaryDistLabel", "distributionLabels"),
    ("cartonlabel_minified.json", "CalgaryCartonLabel", "cartonLabels"),
])
def test_document_type_shows_the_vendor_value_verbatim(tmp_path, registry, config,
                                                       fixture, layout, label):
    """Shown exactly as the vendor sends it.

    The mapping is an identity one: it exists to claim the field from the
    generic `field: type` rule (which would otherwise offer a nonsense
    "Type 1..9" list), NOT to rename anything. The label is display-only and
    the field is read-only, so nothing here can reach the file.
    """
    p = _copy(tmp_path, fixture)
    header = service.parse_file_view(p, registry, config)["sections"][0]
    field = next(f for f in header["fields"] if f["name"] == "type")
    assert list(field["options"].values()) == [label]
    assert field["editable"] is False       # it is the detection signature


def test_the_two_different_type_fields_get_their_own_labels(tmp_path, registry, config):
    """Calgary JSON has TWO `type` fields with unrelated meanings.

    The HEADER one is the document kind ("styleHeaders"); every DETAIL row has
    a coded type (1-9) like the other layouts. A rule scoped to the layout
    alone labels both — the detail rows would offer "Style Header" for a value
    of "1".
    """
    p = _copy(tmp_path, "styleheader_fmtB.json")
    sections = {s["name"]: s for s in service.parse_file_view(p, registry, config)["sections"]}

    header = next(f for f in sections["Header"]["fields"] if f["name"] == "type")
    assert header["options"] == {"styleHeaders": "styleHeaders"}
    assert sections["Header"]["records"][0]["values"]["type"] == "styleHeaders"

    detail = next(f for f in sections["Details"]["fields"] if f["name"] == "type")
    assert "styleHeaders" not in detail["options"], \
        "the document-kind label must not leak onto the coded detail type"
    assert detail["options"] == {"1": "Detail Type 1", "2": "Detail Type 2"}
    assert sections["Details"]["records"][0]["values"]["type"] in detail["options"]


def test_a_section_scoped_rule_beats_a_layout_scoped_one(config):
    """The specificity ordering the fix relies on."""
    assert config.options("type", layout="CalgaryStyleHeader", section="Header") == \
        {"styleHeaders": "styleHeaders"}
    detail = config.options("type", layout="CalgaryStyleHeader", section="Details")
    assert detail and "styleHeaders" not in detail


def test_a_header_scoped_rule_does_not_reach_other_sections():
    """The exact production shape: a generic rule for the coded detail `type`,
    plus a Header-scoped rule for the document kind.

    Built inline rather than from the shared fixture on purpose — the fixture
    also carries a Details-scoped rule, which outranks the header rule whether
    or not it is section-scoped, so it cannot detect the scope going missing.
    """
    rules = [
        {"match": {"field": "type"},
         "values": {"1": "Type 1", "2": "Type 2"}},
        {"match": {"layout": "CalgaryStyleHeader", "section": "Header",
                   "field": "type"},
         "values": {"styleHeaders": "styleHeaders"}},
    ]
    cfg = Config(chains={}, rules=rules)

    header = cfg.options("type", layout="CalgaryStyleHeader", section="Header")
    detail = cfg.options("type", layout="CalgaryStyleHeader", section="Details")
    assert header == {"styleHeaders": "styleHeaders"}
    assert detail == {"1": "Type 1", "2": "Type 2"}, \
        "a Header-scoped rule must not label the coded detail type"

    # ...and without the scope it WOULD, which is the bug this guards against.
    unscoped = [rules[0],
                {"match": {"layout": "CalgaryStyleHeader", "field": "type"},
                 "values": {"styleHeaders": "styleHeaders"}}]
    leaked = Config(chains={}, rules=unscoped).options(
        "type", layout="CalgaryStyleHeader", section="Details")
    assert leaked == {"styleHeaders": "styleHeaders"}


def test_section_criterion_does_not_disturb_the_other_layouts(config):
    """Rules with no `section:` must keep matching exactly as before."""
    for layout in ("StyleHeader", "Preticket", "CartonLabel", "DistLabels"):
        for section in (None, "Header", "Lane", "Detail"):
            assert config.options("indicator", layout=layout, section=section) == \
                config.options("indicator", layout=layout)


def test_timestamp_is_editable(tmp_path, registry, config):
    """It was locked by the layout spec; users need past/current/future stamps."""
    p = _copy(tmp_path, "styleheader_fmtB.json")
    header = service.parse_file_view(p, registry, config)["sections"][0]
    field = next(f for f in header["fields"] if f["name"] == "timestamp")
    assert field["editable"] is True and not field.get("hidden")
    assert header["records"][0]["values"]["timestamp"]


def test_the_document_type_stays_locked_everywhere(tmp_path, registry, config):
    """Unlocking `timestamp` must not unlock the detection signature beside it.

    `type` is read-only in the layout SPEC rather than in config, and bulk /
    generate previously only honoured the config list — so it was offered for
    mass editing despite being the field that decides the layout (D12).
    """
    p = _copy(tmp_path, "styleheader_fmtB.json")
    paths = [str(p)]
    header = service.parse_file_view(p, registry, config)["sections"][0]
    assert next(f for f in header["fields"]
                if f["name"] == "type")["editable"] is False

    scope = service.bulk_scope(paths, registry, config)
    assert "type" not in [f["name"] for f in
                          scope["header_fields"]["CalgaryStyleHeader"]]
    gen = service.generate_scope(paths, registry, config)
    assert "type" not in [f["name"] for f in gen["header_fields"]]
