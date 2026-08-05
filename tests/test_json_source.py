"""SCAN vs WMS — which field is a Calgary JSON file's unique key.

**The file says which it is.** A WMS document carries a ``headerASNid``; a SCAN
one does not, because the .OK formats feeding the SCAN side have no ASN field at
all. So the source is read from the payload, per FILE, and the folder prompt and
its remembered answers are gone.

The source decides the KEY on StyleHeader/DistLabel (``keytrol`` for SCAN,
``headerASNid`` for WMS). CartonLabel HAS a source and reports it, but keys on
``pickListId`` under both — those are two separate questions.

The tests that matter most:

* the OTHER seven layouts are completely unaffected;
* Make Unique renumbers the field the file's own source selects — and only that
  one, leaving the other source's field byte-identical;
* a SCAN file's blank ``headerASNid`` is never filled in with a fabricated
  number, which is what happened when an unlabelled folder defaulted to WMS.
"""
import json
import os
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.jsonsource import resolve_source, source_from_header
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
# Reading the source from the payload
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("asn,expected", [
    ("S403902978943435A", "WMS"),      # a real ASN -> WMS
    ("", "SCAN"),                      # every shape of "no value" -> SCAN
    ("   ", "SCAN"),
    (None, "SCAN"),
])
def test_the_header_decides_the_source(asn, expected):
    r = source_from_header({"headerASNid": asn}, SOURCES, "WMS")
    assert r.source == expected and r.resolved is True


def test_a_missing_key_is_scan_and_an_unreadable_header_is_not_resolved():
    assert source_from_header({}, SOURCES, "WMS").source == "SCAN"
    unknown = source_from_header(None, SOURCES, "WMS")
    assert unknown.resolved is False and unknown.source == "WMS"


# --------------------------------------------------------------------------- #
# End to end: the FILE decides which field Make Unique renumbers
# --------------------------------------------------------------------------- #
def _header(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]["header"]


def _folder_of_copies(tmp_path, name, fixture, count=3, source="WMS"):
    """A folder of identical files — duplicates under EITHER key.

    ``source`` shapes the payload rather than the folder name: SCAN files get a
    blank ``headerASNid``, which is what a real SCAN document looks like.
    """
    d = tmp_path / name
    d.mkdir()
    doc = json.loads((FIX / fixture).read_text(encoding="utf-8"))
    if source == "SCAN":
        doc["data"]["header"]["headerASNid"] = None
    for i in range(count):
        (d / f"f{i}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return d


@pytest.mark.parametrize("layout,fixture", sorted(SOURCE_DEPENDENT.items()))
@pytest.mark.parametrize("source,expected_field,untouched_field", [
    ("SCAN", "keytrol", "headerASNid"),
    ("WMS", "headerASNid", "keytrol"),
])
def test_make_unique_renumbers_the_field_the_payload_selects(
        tmp_path, registry, config, layout, fixture, source, expected_field,
        untouched_field):
    """The folder name says nothing — deliberately neutral. Only the payload."""
    d = _folder_of_copies(tmp_path, "Batch07", fixture, source=source)
    before = _header(d / "f0.json")

    res = service.make_unique_in_folder(d, registry, config, backup=False)

    assert res["rekeyed"], "identical files must be re-keyed"
    assert {r["field"] for r in res["rekeyed"]} == {expected_field}
    for f in sorted(d.iterdir()):
        assert _header(f)[untouched_field] == before[untouched_field]
    vals = [_header(f)[expected_field] for f in sorted(d.iterdir())]
    assert len(set(vals)) == len(vals)


@pytest.mark.parametrize("layout,fixture", sorted(SOURCE_DEPENDENT.items()))
def test_a_scan_file_never_gets_a_fabricated_asn(
        tmp_path, registry, config, layout, fixture):
    """The bug this replaced: an unlabelled folder defaulted to WMS, so Make
    Unique wrote invented ASN IDs ('1', '2') into files that have none, and
    left the real duplicate keytrol alone."""
    d = _folder_of_copies(tmp_path, "Batch07", fixture, source="SCAN")
    service.make_unique_in_folder(d, registry, config, backup=False)
    for f in sorted(d.iterdir()):
        asn = _header(f)["headerASNid"]
        assert asn is None or str(asn).strip() == "", f"ASN fabricated: {asn!r}"


@pytest.mark.parametrize("layout,fixture", sorted(SOURCE_DEPENDENT.items()))
@pytest.mark.parametrize("source,key", [("SCAN", "keytrol"), ("WMS", "headerASNid")])
def test_tree_and_view_report_the_source_from_the_file(
        tmp_path, registry, config, layout, fixture, source, key):
    d = _folder_of_copies(tmp_path, "Batch07", fixture, count=1, source=source)

    node = service.build_tree(d, config, registry)["children"][0]
    assert node["source"] == source           # the badge
    assert node["key_field"] == key

    view = service.parse_file_view(d / "f0.json", registry, config)
    assert view["key_field"] == key
    assert view["json_source"]["source"] == source


def test_one_folder_can_hold_both_sources(tmp_path, registry, config):
    """Impossible under the old per-folder model — this is the real gain."""
    d = tmp_path / "Mixed"
    d.mkdir()
    doc = json.loads((FIX / "styleheader_fmtB.json").read_text(encoding="utf-8"))
    (d / "wms.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    doc["data"]["header"]["headerASNid"] = None
    (d / "scan.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

    by_name = {c["name"]: c for c in service.build_tree(d, config, registry)["children"]}
    assert by_name["wms.json"]["source"] == "WMS"
    assert by_name["wms.json"]["key_field"] == "headerASNid"
    assert by_name["scan.json"]["source"] == "SCAN"
    assert by_name["scan.json"]["key_field"] == "keytrol"


def test_cartonlabel_reports_a_source_but_keys_the_same_either_way(
        tmp_path, registry, config):
    """Two separate questions: where it came from, and what its key is."""
    d = tmp_path / "Cartons"
    d.mkdir()
    doc = json.loads((FIX / "cartonlabel_minified.json").read_text(encoding="utf-8"))
    (d / "wms.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    doc["data"]["header"]["headerASNid"] = None
    (d / "scan.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

    by_name = {c["name"]: c for c in service.build_tree(d, config, registry)["children"]}
    assert by_name["wms.json"]["source"] == "WMS"
    assert by_name["scan.json"]["source"] == "SCAN"
    for c in by_name.values():
        assert c["key_field"] == "pickListId"      # unchanged by the source


def test_rekeyed_files_report_which_source_they_resolved_to(tmp_path, registry, config):
    d = tmp_path / "Mixed"
    d.mkdir()
    doc = json.loads((FIX / "styleheader_fmtB.json").read_text(encoding="utf-8"))
    for i in (0, 1):
        (d / f"wms{i}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    doc["data"]["header"]["headerASNid"] = None
    for i in (0, 1):
        (d / f"scan{i}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

    res = service.make_unique_in_folder(d, registry, config, backup=False)
    by_file = {r["file"]: r for r in res["rekeyed"]}
    assert by_file, "duplicates should have been re-keyed"
    for name, r in by_file.items():
        expected = "SCAN" if name.startswith("scan") else "WMS"
        assert r["source"] == expected
        assert r["field"] == ("keytrol" if expected == "SCAN" else "headerASNid")


@pytest.mark.parametrize("filename,expected", [
    # EU / EWMS formats come from WMS; the NA formats come from SCAN.
    ("EUPreticket.OK", "WMS"),
    ("EUStyleHeader.OK", "WMS"),
    ("EUCartonLabel.OK", "WMS"),
    ("StyleHeader.OK", "SCAN"),
    ("CartonLabel.OK", "SCAN"),
    ("DistLabels.OK", "SCAN"),
    ("Preticket.OK", "SCAN"),
])
def test_ok_files_badge_the_source_declared_by_their_layout(
        tmp_path, registry, config, filename, expected):
    """An .OK format is emitted by exactly one system, so its badge comes from
    the LAYOUT (config), not from reading the file."""
    d = tmp_path / "okfiles"
    d.mkdir()
    (d / filename).write_bytes((DATA_DIR / filename).read_bytes())
    node = service.build_tree(d, config, registry)["children"][0]
    assert node["source"] == expected
    assert node["source_reason"] == "the layout's own source"


def test_an_ok_files_key_never_depends_on_its_source(tmp_path, registry, config):
    """The badge is DISPLAY ONLY. `has_source` stays False for every .OK layout,
    which is what keeps the key (and Make Unique) reading from keys.yaml alone —
    only the Calgary layouts have a key that differs between sources."""
    d = tmp_path / "okfiles"
    d.mkdir()
    for fn, key in (("StyleHeader.OK", "keytrol"), ("Preticket.OK", "po"),
                    ("EUCartonLabel.OK", "keytrol")):
        (d / fn).write_bytes((DATA_DIR / fn).read_bytes())
    nodes = {n["name"]: n for n in service.build_tree(d, config, registry)["children"]}
    for fn, key in (("StyleHeader.OK", "keytrol"), ("Preticket.OK", "po"),
                    ("EUCartonLabel.OK", "keytrol")):
        layout = nodes[fn]["layout"]
        assert nodes[fn]["key_field"] == key
        assert config.has_source(layout) is False
        assert config.source_dependent(layout) is False
        assert config.unique_field_candidates(layout) == [key]


def test_duplicates_are_flagged_on_the_key_the_payload_selects(
        tmp_path, registry, config):
    d = _folder_of_copies(tmp_path, "Batch07", "styleheader_fmtB.json",
                          count=2, source="SCAN")
    kids = service.build_tree(d, config, registry)["children"]
    assert all(c["duplicate"] for c in kids), "identical keytrols must be flagged"
    assert all(c["key_field"] == "keytrol" for c in kids)


def test_has_source_and_source_dependent_are_different_questions(config):
    assert config.has_source("CalgaryCartonLabel") is True
    assert config.source_dependent("CalgaryCartonLabel") is False
    for layout in ("CalgaryStyleHeader", "CalgaryDistLabel"):
        assert config.has_source(layout) and config.source_dependent(layout)
    for layout in SOURCE_INDEPENDENT:
        if not layout.startswith("Calgary"):
            assert config.has_source(layout) is False


# --------------------------------------------------------------------------- #
# The output folder's SCAN token is a LABEL, not a mechanism
#
# Under D27 the folder name decided a file's source, and the convert modal said
# so ("folder name declares it"). D38 replaced that with reading the file's own
# headerASNid — conversion emits it as null, so the batch is SCAN by content.
# The wording was stale; these pin the behaviour it now describes.
# --------------------------------------------------------------------------- #
def test_a_converted_file_is_scan_by_its_own_content(tmp_path, registry, config):
    import shutil
    src = tmp_path / "StyleHeader.OK"
    shutil.copy2(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
                 / "StyleHeader.OK", src)
    res = service.convert_apply([str(src)], registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["data"]["header"]["headerASNid"] is None, "no ASN => SCAN"

    info = service.json_source_for(out, config)
    assert info["source"] == "SCAN"
    assert info["matched_on"] == "headerASNid", info
    assert "folder" not in info["reason"] and "name" not in info["reason"], info


def test_renaming_the_output_folder_does_not_change_the_source(
        tmp_path, registry, config):
    """The sharpest form: strip SCAN out of the folder name entirely."""
    import shutil
    src = tmp_path / "StyleHeader.OK"
    shutil.copy2(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
                 / "StyleHeader.OK", src)
    res = service.convert_apply([str(src)], registry, config)
    folder = Path(res["folder"])
    out = sorted(folder.glob("*.json"))[0]
    before = service.json_source_for(out, config)

    renamed = folder.parent / "no_source_token_at_all"
    folder.rename(renamed)
    after = service.json_source_for(renamed / out.name, config)

    assert "SCAN" in folder.name, "fixture must start with the token"
    assert before["source"] == after["source"] == "SCAN"
    assert before["reason"] == after["reason"]
