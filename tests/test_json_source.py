"""SCAN vs WMS — which field is a Calgary JSON file's unique key.

The two sources send structurally identical JSON and differ only in their
identity field (``keytrol`` for SCAN, ``headerASNid`` for WMS on StyleHeader
and DistLabel; ``pickListId`` for CartonLabel either way). Nothing in the
payload says which is which, so the source is declared by a SCAN/WMS token in
the file or folder name, or answered once per folder and remembered by the UI
(arriving here as an explicit ``source``).

The tests that matter most:

* the OTHER seven layouts are completely unaffected, whatever source is passed;
* Make Unique renumbers the field the resolved source selects — and only that
  one, leaving the other source's field byte-identical;
* ``SCANNED`` does not match ``SCAN`` (token matching, not substring), because
  a false match would silently point Make Unique at the wrong field.
"""
import json
import os
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.jsonsource import resolve_source
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(
    os.environ.get("OKGEN_DATA_DIR",
                   str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions")))
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="no calgary fixtures")

SOURCE_DEPENDENT = {"CalgaryStyleHeader": "styleheader_fmtB.json",
                    "CalgaryDistLabel": "distlabel.json"}
# Every layout whose key is a single field, whatever the source.
SOURCE_INDEPENDENT = ["StyleHeader", "CartonLabel", "DistLabels", "Preticket",
                      "EUPreticket", "EUStyleHeader", "EUCartonLabel",
                      "CalgaryCartonLabel"]


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #
SOURCES = {"SCAN": ["SCAN"], "WMS": ["WMS"]}


@pytest.mark.parametrize("path,expected,reason,resolved", [
    ("/d/Calgary_SCAN_2026/a.json", "SCAN", "folder name", True),
    ("/d/Calgary SCAN/a.json", "SCAN", "folder name", True),
    ("/d/calgary-scan/a.json", "SCAN", "folder name", True),      # case-insensitive
    ("/d/WMS/a.json", "WMS", "folder name", True),
    ("/d/out/wms_2026/a.json", "WMS", "folder name", True),
    ("/d/out/file_SCAN.json", "SCAN", "file name", True),          # file beats folder
    ("/d/plain/a.json", "WMS", "default", False),                  # nothing said -> default
    ("/d/SCANNED/a.json", "WMS", "default", False),                # NOT a SCAN match
    ("/d/WMSX/a.json", "WMS", "default", False),                   # NOT a WMS match
    ("/d/SCAN_WMS/a.json", "WMS", "default", False),               # ambiguous -> unresolved
])
def test_resolver_reads_the_name(path, expected, reason, resolved):
    r = resolve_source(path, SOURCES, "WMS")
    assert (r.source, r.reason, r.resolved) == (expected, reason, resolved)


def test_substring_never_matches_a_token():
    """The guard that keeps a wrong folder name from redirecting Make Unique."""
    for name in ("SCANNED", "SCANNER", "PRESCAN", "WMSX", "XWMS", "WMSDATA"):
        r = resolve_source(f"/d/{name}/a.json", SOURCES, "WMS")
        assert r.resolved is False, f"{name} must not match a source token"


def test_explicit_override_beats_a_folder_name():
    r = resolve_source("/d/Calgary_WMS/a.json", SOURCES, "WMS", override="SCAN")
    assert (r.source, r.reason, r.resolved) == ("SCAN", "override", True)


def test_a_file_naming_itself_beats_a_stored_folder_answer():
    """Most specific wins: the answer is about the FOLDER, the name is about
    the FILE — so a SCAN file dropped into a folder answered WMS is still SCAN.
    """
    r = resolve_source("/d/plain/rerun_SCAN.json", SOURCES, "WMS", override="WMS")
    assert (r.source, r.reason) == ("SCAN", "file name")
    assert r.conflict is True          # it disagrees with the stored answer


def test_a_stored_answer_still_applies_to_unnamed_files_beside_it():
    r = resolve_source("/d/plain/5h4ayclu.f4b.json", SOURCES, "WMS", override="SCAN")
    assert (r.source, r.reason) == ("SCAN", "override")


def test_file_and_folder_disagreement_is_reported():
    r = resolve_source("/d/WMS_out/thing_SCAN.json", SOURCES, "WMS")
    assert r.source == "SCAN" and r.conflict is True


def test_nearest_labelled_ancestor_wins():
    r = resolve_source("/d/SCAN_root/sub/deeper/a.json", SOURCES, "WMS")
    assert r.source == "SCAN" and r.reason == "folder name"


def test_unknown_override_falls_back_to_the_name():
    """A stale/garbage stored answer must not silently become the source."""
    r = resolve_source("/d/SCAN_x/a.json", SOURCES, "WMS", override="NOPE")
    assert r.source == "SCAN" and r.reason == "folder name"


# --------------------------------------------------------------------------- #
# Key resolution
# --------------------------------------------------------------------------- #
def test_calgary_keys_differ_by_source(config):
    for layout in ("CalgaryStyleHeader", "CalgaryDistLabel"):
        assert config.unique_field(layout, "SCAN") == "keytrol"
        assert config.unique_field(layout, "WMS") == "headerASNid"
        assert config.source_dependent(layout) is True


def test_cartonlabel_key_is_the_same_under_both_sources(config):
    assert config.unique_field("CalgaryCartonLabel", "SCAN") == "pickListId"
    assert config.unique_field("CalgaryCartonLabel", "WMS") == "pickListId"
    # ...so the UI must never ask about a folder holding only carton labels.
    assert config.source_dependent("CalgaryCartonLabel") is False


def test_missing_or_unknown_source_uses_the_default(config):
    for src in (None, "", "NOPE"):
        assert config.unique_field("CalgaryStyleHeader", src) == "headerASNid"


@pytest.mark.parametrize("layout", SOURCE_INDEPENDENT)
def test_other_layouts_ignore_the_source_entirely(config, layout):
    """The seven .OK layouts must be untouched by any of this."""
    base = config.unique_field(layout)
    assert base
    for src in ("SCAN", "WMS", None, "NOPE"):
        assert config.unique_field(layout, src) == base
    assert config.source_dependent(layout) is False


# --------------------------------------------------------------------------- #
# End to end: the folder name decides which field Make Unique renumbers
# --------------------------------------------------------------------------- #
def _header(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]["header"]


def _folder_of_copies(tmp_path, name, fixture, count=3):
    """A folder of identical files — i.e. duplicates under EITHER source."""
    d = tmp_path / name
    d.mkdir()
    for i in range(count):
        (d / f"f{i}.json").write_bytes((FIX / fixture).read_bytes())
    return d


@pytest.mark.parametrize("layout,fixture", sorted(SOURCE_DEPENDENT.items()))
@pytest.mark.parametrize("folder,expected_field,untouched_field", [
    ("Calgary_SCAN_2026", "keytrol", "headerASNid"),
    ("Calgary_WMS_out", "headerASNid", "keytrol"),
])
def test_make_unique_renumbers_the_field_the_source_selects(
        tmp_path, registry, config, layout, fixture, folder, expected_field,
        untouched_field):
    d = _folder_of_copies(tmp_path, folder, fixture)
    before = _header(d / "f0.json")

    res = service.make_unique_in_folder(d, registry, config, backup=False)

    assert res["rekeyed"], "identical files must be re-keyed"
    assert {r["field"] for r in res["rekeyed"]} == {expected_field}
    # The OTHER source's field is left exactly as it was in every file.
    for f in sorted(d.iterdir()):
        assert _header(f)[untouched_field] == before[untouched_field]
    # ...and the selected field is now unique across the folder.
    vals = [_header(f)[expected_field] for f in sorted(d.iterdir())]
    assert len(set(vals)) == len(vals)


@pytest.mark.parametrize("layout,fixture", sorted(SOURCE_DEPENDENT.items()))
def test_answering_the_source_beats_an_unlabelled_folder(
        tmp_path, registry, config, layout, fixture):
    """What the UI stores per folder overrides the WMS default."""
    d = _folder_of_copies(tmp_path, "no_token_here", fixture)
    before = _header(d / "f0.json")

    res = service.make_unique_in_folder(d, registry, config, backup=False,
                                        source="SCAN")

    assert {r["field"] for r in res["rekeyed"]} == {"keytrol"}
    for f in sorted(d.iterdir()):
        assert _header(f)["headerASNid"] == before["headerASNid"]


@pytest.mark.parametrize("layout,fixture", sorted(SOURCE_DEPENDENT.items()))
def test_tree_and_view_report_the_resolved_source(
        tmp_path, registry, config, layout, fixture):
    d = _folder_of_copies(tmp_path, "Calgary_SCAN_x", fixture, count=1)

    tree = service.build_tree(d, config, registry)
    assert tree["json_source"]["source"] == "SCAN"
    assert tree["json_source"]["resolved"] is True
    assert tree["children"][0]["key_field"] == "keytrol"

    view = service.parse_file_view(d / "f0.json", registry, config)
    assert view["key_field"] == "keytrol"
    assert view["json_source"]["source"] == "SCAN"


def test_unlabelled_folder_is_reported_unresolved_so_the_ui_can_ask(
        tmp_path, registry, config):
    d = _folder_of_copies(tmp_path, "nothing_in_the_name",
                          SOURCE_DEPENDENT["CalgaryStyleHeader"], count=1)
    info = service.build_tree(d, config, registry)["json_source"]
    assert info["resolved"] is False and info["source"] == "WMS"
    assert info["layouts"] == ["CalgaryStyleHeader"]


def test_folder_of_self_naming_files_is_not_asked_about(tmp_path, registry, config):
    """Every file names its own source, so the folder's name decides nothing."""
    d = tmp_path / "no_token_here"
    d.mkdir()
    for i in range(3):
        (d / f"batch{i}_SCAN.json").write_bytes(
            (FIX / SOURCE_DEPENDENT["CalgaryStyleHeader"]).read_bytes())

    tree = service.build_tree(d, config, registry)
    assert tree["json_source"]["resolved"] is True
    assert tree["json_source"]["source"] == "SCAN"
    assert all(c["key_field"] == "keytrol" for c in tree["children"])


def test_one_named_file_overrides_the_folder_it_sits_in(tmp_path, registry, config):
    d = tmp_path / "Calgary_WMS_out"
    d.mkdir()
    fx = (FIX / SOURCE_DEPENDENT["CalgaryStyleHeader"]).read_bytes()
    (d / "ordinary.json").write_bytes(fx)
    (d / "rerun_SCAN.json").write_bytes(fx)

    by_name = {c["name"]: c for c in service.build_tree(d, config, registry)["children"]}
    assert by_name["ordinary.json"]["key_field"] == "headerASNid"   # folder: WMS
    assert by_name["rerun_SCAN.json"]["key_field"] == "keytrol"     # its own name


def test_rekeyed_files_report_which_source_they_resolved_to(tmp_path, registry, config):
    """A mixed folder is the case where a bulk run is otherwise silent about
    having renumbered two DIFFERENT fields."""
    d = tmp_path / "Calgary_WMS_out"
    d.mkdir()
    fx = (FIX / SOURCE_DEPENDENT["CalgaryStyleHeader"]).read_bytes()
    for n in ("a.json", "b.json", "one_SCAN.json", "two_SCAN.json"):
        (d / n).write_bytes(fx)

    rekeyed = service.make_unique_in_folder(d, registry, config,
                                            backup=False)["rekeyed"]
    got = {r["file"]: (r["field"], r["source"]) for r in rekeyed if r.get("to")}
    # One file per source keeps its key (first occurrence wins), the rest move.
    assert got, "identical files must be re-keyed"
    for name, (field, src) in got.items():
        if "SCAN" in name:
            assert (field, src) == ("keytrol", "SCAN")
        else:
            assert (field, src) == ("headerASNid", "WMS")
    assert {v[1] for v in got.values()} == {"SCAN", "WMS"}


def test_source_is_absent_for_layouts_that_do_not_care(tmp_path, registry, config):
    d = tmp_path / "plain"
    d.mkdir()
    fx = (FIX / "cartonlabel_minified.json").read_bytes()
    for n in ("a.json", "b.json"):
        (d / n).write_bytes(fx)
    rekeyed = service.make_unique_in_folder(d, registry, config,
                                            backup=False)["rekeyed"]
    assert rekeyed and all(r["source"] is None for r in rekeyed)


def test_carton_label_folder_is_never_asked_about(tmp_path, registry, config):
    """Its key is pickListId either way, so there is nothing to ask."""
    d = _folder_of_copies(tmp_path, "plain", "cartonlabel_minified.json", count=1)
    assert service.build_tree(d, config, registry)["json_source"] is None


def test_ok_file_folder_has_no_source_block(tmp_path, registry, config):
    """A folder of .OK files must be completely unaffected."""
    d = tmp_path / "okfiles"
    d.mkdir()
    (d / "StyleHeader.OK").write_bytes((DATA_DIR / "StyleHeader.OK").read_bytes())
    tree = service.build_tree(d, config, registry)
    assert tree["json_source"] is None
    assert tree["children"][0]["key_field"] == "keytrol"      # its own, unchanged


# --------------------------------------------------------------------------- #
# The safety net for a folder whose source was only ASSUMED
# --------------------------------------------------------------------------- #
def _write_variant(path, keytrol, asn):
    doc = json.loads((FIX / SOURCE_DEPENDENT["CalgaryStyleHeader"]).read_text())
    doc["data"]["header"]["keytrol"] = keytrol
    doc["data"]["header"]["headerASNid"] = asn
    Path(path).write_text(json.dumps(doc))


def test_unlabelled_folder_hints_when_the_other_key_collides(tmp_path, registry, config):
    """Distinct headerASNid but a shared keytrol => probably SCAN, not WMS."""
    d = tmp_path / "unlabelled"
    d.mkdir()
    for i in range(3):
        _write_variant(d / f"f{i}.json", keytrol="140038", asn=f"V40380420894058{i}A")

    info = service.build_tree(d, config, registry)["json_source"]
    assert info["resolved"] is False
    assert info["hint"] and info["hint"]["field"] == "keytrol"


def test_a_named_folder_is_never_second_guessed(tmp_path, registry, config):
    """Real WMS files share a placeholder keytrol — that must NOT nag or warn.

    This is the case that rules out comparing every candidate key for
    duplicates: a correct WMS folder would otherwise carry a permanent warning
    on every file that Make Unique could never clear.
    """
    d = tmp_path / "Calgary_WMS_real"
    d.mkdir()
    for i in range(3):
        _write_variant(d / f"f{i}.json", keytrol="0", asn=f"V40380420894058{i}A")

    tree = service.build_tree(d, config, registry)
    assert tree["json_source"]["hint"] is None
    assert not any(c["duplicate"] for c in tree["children"]), \
        "a constant WMS keytrol must not read as a duplicate"


def test_duplicates_are_flagged_on_the_resolved_key(tmp_path, registry, config):
    d = tmp_path / "Calgary_SCAN_dupes"
    d.mkdir()
    for i in range(3):
        _write_variant(d / f"f{i}.json", keytrol="140038", asn=f"V40380420894058{i}A")

    tree = service.build_tree(d, config, registry)
    assert all(c["duplicate"] for c in tree["children"]), \
        "a shared keytrol IS a duplicate once the folder is known to be SCAN"
