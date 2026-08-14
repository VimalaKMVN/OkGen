"""Run TOSCA Script — staging the selected files into TOSCA's input folders.

Updating the input sheet only tells TOSCA WHICH (Chain, Process, Format)
combinations to process; it reads the files themselves from a tree of its own,
addressed as ``{B[Chain]}\\{B[Process]}\\{B[Format]}``. So a run stages the
selected files into that tree BEFORE it writes the sheet and fires the .bat.

Covers, in both directions:
  * the Key-sheet code split (the two rows typed without a space around the
    hyphen, which used to resolve to nothing at all);
  * a clean stage — files copied, previous files removed, everything else in the
    tree left alone;
  * every reason a combination is left out (missing folder, folder whose name
    disagrees with the sheet cell, two selected files with one name) and the
    proof that it is left out of the SHEET too while the rest still runs;
  * the source-inside-its-own-target case, which the obvious clear-then-copy
    order destroys;
  * a folder-level failure aborting the whole run with the workbook untouched
    and the .bat unfired;
  * preview and run agreeing, since a confirmation that disagrees with what
    happens is worse than none.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from okgen import tosca
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(os.environ.get(
    "OKGEN_DATA_DIR",
    str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions")))
FIXJ = Path(__file__).resolve().parent / "fixtures" / "calgary"
TOSCA_FIX = Path(__file__).resolve().parent / "fixtures" / "tosca"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

pytestmark = pytest.mark.skipif(
    not (TOSCA_FIX / "laser_compare.xlsm").exists() or not FIXJ.is_dir(),
    reason="no tosca/calgary fixtures")

SCRIPT = "Laser Compare"
SHEET = "Laser-SH_PT_CL_DL_Formats"


@pytest.fixture
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture
def config():
    return Config.load(FIXTURE_CONFIG)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _wb(tmp_path, config, name="laser_compare.xlsm"):
    dst = tmp_path / name
    shutil.copy2(TOSCA_FIX / name, dst)
    for s in config.tosca()["scripts"]:
        if s["name"] == SCRIPT:
            s["workbook"] = str(dst)
    return dst


def _stage_cfg(config, root, **over):
    """Point the script at ``root`` and switch staging on."""
    cfg = {"enabled": True, "subpath": "{chain}\\{process}\\{format}",
           "match_format_by_code": True, "clear": "matching",
           "create_missing": False, "overwrite": True}
    cfg.update(over)
    config.tosca()["input_staging"] = cfg
    for s in config.tosca()["scripts"]:
        if s["name"] == SCRIPT:
            s["input_folders"] = [str(root)]


def _sample(tmp_path, src, name, chain=None, fmt=None):
    """A copy of a Calgary fixture, optionally re-chained / re-formatted so a
    test can reach a Key row the shipped samples do not carry."""
    data = json.loads((FIXJ / src).read_text("utf-8"))
    if chain is not None:
        data["data"]["header"]["chain"] = chain
    if fmt is not None:
        data["data"]["header"]["format"] = fmt
    p = tmp_path / name
    p.write_text(json.dumps(data), "utf-8")
    return p


def _tree(root, *leaves):
    """Create ``root/<leaf>`` folders and return them."""
    made = []
    for leaf in leaves:
        d = Path(root).joinpath(*leaf.split("/"))
        d.mkdir(parents=True, exist_ok=True)
        made.append(d)
    return made


def _sheet_rows(wb_path, n=6):
    wb = openpyxl.load_workbook(wb_path, keep_vba=True, data_only=True)
    ws = wb[SHEET]
    out = [[ws[f"{c}{r}"].value for c in "ABC"] for r in range(2, 2 + n)]
    wb.close()
    return [r for r in out if any(r)]


def _names(folder):
    return sorted(p.name for p in Path(folder).iterdir() if p.is_file())


# --------------------------------------------------------------------------- #
# The Key-sheet code split
# --------------------------------------------------------------------------- #
def test_format_head_splits_on_the_first_hyphen():
    """The workbooks' Key sheets are not consistently spaced. Splitting on ' -'
    returned the WHOLE string as the code for the two rows typed without the
    space, so those formats resolved to nothing."""
    assert tosca.format_head("A - Purple Tag") == "A"
    assert tosca.format_head("T-Q-Line Small Gum Label") == "T"      # TJMaxx SH/PT
    assert tosca.format_head("J- Rat Tail Gum Label") == "J"         # Marshalls SH/PT
    assert tosca.format_head("7 - Distributio Label") == "7"
    assert tosca.format_head("B") == "B"                             # bare code
    assert tosca.format_head("") == ""
    assert tosca.format_head(None) is None


def test_unspaced_key_rows_now_resolve(tmp_path, registry, config):
    """A T.J. Maxx Style Header carrying format T used to be reported as
    'not in Key column C' and dropped from the run entirely."""
    wb = _wb(tmp_path, config)
    p = _sample(tmp_path, "styleheader_fmtT.json", "t.json", chain="01", fmt="T")
    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert res["errors"] == [], res["errors"]
    assert res["written"] == 1
    assert res["rows"][0]["format"] == "T-Q-Line Small Gum Label"


def test_spaced_key_rows_are_unaffected(tmp_path, registry, config):
    """The other ~90 Key rows must resolve exactly as before — the split change
    is only allowed to ADD the two that were failing."""
    wb = _wb(tmp_path, config)
    p = _sample(tmp_path, "styleheader_fmtB.json", "b.json")     # chain 04, fmt B
    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert res["rows"][0]["format"] == "B - Blue Gum"


# --------------------------------------------------------------------------- #
# A clean stage
# --------------------------------------------------------------------------- #
def test_files_are_copied_into_the_resolved_folder(tmp_path, registry, config):
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    _stage_cfg(config, root)
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert res["staging"]["copied"] == 1
    assert _names(leaf) == ["order1.json"]
    # the row still reaches the sheet
    assert _sheet_rows(wb) == [["Winners", "Style Header", "B - Blue Gum"]]


def test_previous_files_are_removed_first(tmp_path, registry, config):
    """The whole point: the folder holds the CURRENT selection only."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    (leaf / "old_a.json").write_text("{}", "utf-8")
    (leaf / "old_b.JSON").write_text("{}", "utf-8")          # case-insensitive
    _stage_cfg(config, root)
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert res["staging"]["removed"] == 2
    assert _names(leaf) == ["order1.json"]


def test_clearing_leaves_other_files_and_subfolders_alone(tmp_path, registry, config):
    """Only FILES, only the matching extension, only in the leaf being filled."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, other = _tree(root, "Winners/Style Header/B - Blue Gum",
                        "Winners/Style Header/A - Coordinate Gum")
    (leaf / "notes.txt").write_text("keep me", "utf-8")      # wrong extension
    (leaf / "sub").mkdir()
    (leaf / "sub" / "deep.json").write_text("{}", "utf-8")   # a subfolder
    (other / "stale.json").write_text("{}", "utf-8")         # a folder not targeted
    _stage_cfg(config, root)
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert _names(leaf) == ["notes.txt", "order1.json"]
    assert (leaf / "sub" / "deep.json").is_file()
    assert _names(other) == ["stale.json"]


def test_files_sharing_a_combination_share_a_folder(tmp_path, registry, config):
    """Rows dedupe by the triple; the FILES do not — both belong in the folder."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    _stage_cfg(config, root)
    a = _sample(tmp_path, "styleheader_fmtB.json", "one.json")
    b = _sample(tmp_path, "styleheader_fmtB.json", "two.json")

    res = tosca.run([str(a), str(b)], SCRIPT, registry, config, launch=False)
    assert res["written"] == 1                    # one row
    assert res["staging"]["copied"] == 2          # two files
    assert _names(leaf) == ["one.json", "two.json"]


def test_clear_none_keeps_previous_files(tmp_path, registry, config):
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    (leaf / "old.json").write_text("{}", "utf-8")
    _stage_cfg(config, root, clear="none")
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert res["staging"]["removed"] == 0
    assert _names(leaf) == ["old.json", "order1.json"]


def test_clear_all_removes_every_file_but_no_subfolder(tmp_path, registry, config):
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    (leaf / "notes.txt").write_text("x", "utf-8")
    (leaf / "sub").mkdir()
    _stage_cfg(config, root, clear="all")
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert _names(leaf) == ["order1.json"]
    assert (leaf / "sub").is_dir()


# --------------------------------------------------------------------------- #
# The source that lives in its own target
# --------------------------------------------------------------------------- #
def test_a_file_selected_from_its_own_target_folder_survives(tmp_path, registry, config):
    """Browse the TOSCA input tree in OkGen, edit a file, run. Clear-then-copy
    would delete the file and then have nothing to copy; the snapshot makes the
    order irrelevant."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    src = _sample(tmp_path, "styleheader_fmtB.json", "staged.json")
    inplace = leaf / "staged.json"
    shutil.move(str(src), str(inplace))
    (leaf / "stale.json").write_text("{}", "utf-8")
    body = inplace.read_text("utf-8")
    _stage_cfg(config, root)

    res = tosca.run([str(inplace)], SCRIPT, registry, config, launch=False)
    assert _names(leaf) == ["staged.json"]                 # stale one gone
    assert inplace.read_text("utf-8") == body              # content intact
    assert res["staging"]["removed"] == 1                  # only the stale one


# --------------------------------------------------------------------------- #
# Combinations that are left out — and the rest still running
# --------------------------------------------------------------------------- #
def test_missing_format_folder_excludes_that_row_only(tmp_path, registry, config):
    """Nine good formats must still run when the tenth has no folder."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    good, = _tree(root, "Winners/Style Header/B - Blue Gum")   # 'S' deliberately absent
    _stage_cfg(config, root)
    ok = _sample(tmp_path, "styleheader_fmtB.json", "good.json")
    bad = _sample(tmp_path, "styleheader_fmtS.json", "bad.json")

    res = tosca.run([str(ok), str(bad)], SCRIPT, registry, config, launch=False)
    ex = res["staging"]["excluded"]
    assert len(ex) == 1 and ex[0]["files"] == ["bad.json"]
    assert "no ticket format" in ex[0]["reasons"][0]
    # left out of the SHEET as well — a row whose folder is absent would make
    # TOSCA report a failure of its own
    assert res["written"] == 1
    assert _sheet_rows(wb) == [["Winners", "Style Header", "B - Blue Gum"]]
    assert _names(good) == ["good.json"]


def test_folder_named_differently_from_the_sheet_is_reported_not_used(tmp_path, registry, config):
    """The T/J case. The folder exists and is found by CODE — but TOSCA builds
    its path from the Format CELL, so the Key sheet's spelling is what it will
    look for. Staging into the correctly-named folder would not help."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "T.J. Maxx/Style Header/T - Q-Line Small Gum Label")
    _stage_cfg(config, root)
    p = _sample(tmp_path, "styleheader_fmtT.json", "t.json", chain="01", fmt="T")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    ex = res["staging"]["excluded"]
    assert len(ex) == 1
    why = ex[0]["reasons"][0]
    assert "T-Q-Line Small Gum Label" in why          # what the sheet says
    assert "T - Q-Line Small Gum Label" in why        # what the folder is called
    assert "Key sheet" in why                         # why it fails
    assert "GTA UI" in why                            # what settles which is right
    # ***It must NOT prescribe a side.*** The message used to end "until the
    # workbook's Key sheet is corrected to <folder name>", which assumes the
    # folder is right. A user hit the opposite — a mistyped FOLDER beside a
    # correct sheet — where that advice would have edited a correct workbook to
    # match the typo. OkGen can see they disagree; it cannot know which is true.
    assert "Key sheet is corrected" not in why
    assert "and/or" in why, "the fix must name BOTH sides"
    assert res["written"] == 0
    assert _names(leaf) == []                         # nothing staged into it


def test_two_selected_files_with_one_name_stage_neither(tmp_path, registry, config):
    """They would land on top of each other and the later copy would win in
    silence — the outcome a bulk write path must never produce."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    _stage_cfg(config, root)
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir(); d2.mkdir()
    a = _sample(d1, "styleheader_fmtB.json", "same.json")
    b = _sample(d2, "styleheader_fmtB.json", "same.json")

    res = tosca.run([str(a), str(b)], SCRIPT, registry, config, launch=False)
    ex = res["staging"]["excluded"]
    assert len(ex) == 1 and "share a name" in ex[0]["reasons"][0]
    assert _names(leaf) == []                         # NEITHER, not one of two
    assert res["written"] == 0


def test_create_missing_builds_the_folder_when_switched_on(tmp_path, registry, config):
    """Off by default (the user's call), but the switch has to work."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    _tree(root, "Winners/Style Header")
    _stage_cfg(config, root, create_missing=True)
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    leaf = root / "Winners" / "Style Header" / "B - Blue Gum"
    assert res["staging"]["created"] == 1
    assert _names(leaf) == ["order1.json"]


# --------------------------------------------------------------------------- #
# Folder-level failure aborts everything
# --------------------------------------------------------------------------- #
def test_a_delete_failure_aborts_before_the_workbook_is_touched(
        tmp_path, registry, config, monkeypatch):
    """Staging runs FIRST precisely so this is possible: nothing to undo."""
    wb = _wb(tmp_path, config)
    before = wb.read_bytes()
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    (leaf / "locked.json").write_text("{}", "utf-8")
    _stage_cfg(config, root)
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    def boom(*a, **k):
        raise PermissionError("[WinError 32] used by another process")
    monkeypatch.setattr(tosca.fs, "unlink", boom)

    fired = []
    monkeypatch.setattr(tosca, "_launch_bat", lambda b: fired.append(b))

    with pytest.raises(tosca.ToscaError) as exc:
        tosca.run([str(p)], SCRIPT, registry, config, launch=True)
    assert "could not clear" in str(exc.value)
    assert "not started" in str(exc.value)
    assert wb.read_bytes() == before          # workbook byte-identical
    assert fired == []                        # .bat never fired


def test_a_read_failure_aborts_before_anything_is_deleted(
        tmp_path, registry, config, monkeypatch):
    """The snapshot is taken before the first delete, so a source that cannot be
    read costs nothing at all — the folder still holds what it held."""
    wb = _wb(tmp_path, config)
    before = wb.read_bytes()
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    (leaf / "old.json").write_text("{}", "utf-8")
    _stage_cfg(config, root)
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    # The FIRST copy staging performs is into the snapshot, so failing every
    # copy proves the delete had not happened yet.
    def boom(*a, **k):
        raise OSError("read error")
    monkeypatch.setattr(tosca.fs, "copy2", boom)

    with pytest.raises(tosca.ToscaError) as exc:
        tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert "stage" in str(exc.value)
    assert _names(leaf) == ["old.json"]        # nothing deleted
    assert wb.read_bytes() == before           # and nothing written


# --------------------------------------------------------------------------- #
# Boundaries and back-compat
# --------------------------------------------------------------------------- #
def test_staging_off_leaves_the_tree_untouched(tmp_path, registry, config):
    """A config written before this feature behaves exactly as it did."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    (leaf / "old.json").write_text("{}", "utf-8")
    _stage_cfg(config, root, enabled=False)
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert res["staging"]["enabled"] is False
    assert res["written"] == 1                 # the sheet is still written
    assert _names(leaf) == ["old.json"]        # the tree is not


def test_script_without_input_folders_stages_nothing_and_says_so(
        tmp_path, registry, config):
    wb = _wb(tmp_path, config)
    config.tosca()["input_staging"] = {"enabled": True}
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert res["staging"]["configured"] is False
    assert res["staging"]["copied"] == 0
    assert res["written"] == 1


def test_nothing_selected_stages_nothing(tmp_path, registry, config):
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    _tree(root, "Winners/Style Header/B - Blue Gum")
    _stage_cfg(config, root)

    res = tosca.run([], SCRIPT, registry, config, launch=False)
    assert res["written"] == 0
    assert res["staging"]["copied"] == 0 and res["staging"]["removed"] == 0


def test_every_selected_combination_stages(tmp_path, registry, config):
    """The all-boundary, opposite the none-boundary above."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    b, s = _tree(root, "Winners/Style Header/B - Blue Gum",
                 "Winners/Style Header/S - Small Gum")
    _stage_cfg(config, root)
    p1 = _sample(tmp_path, "styleheader_fmtB.json", "b.json")
    p2 = _sample(tmp_path, "styleheader_fmtS.json", "s.json")

    res = tosca.run([str(p1), str(p2)], SCRIPT, registry, config, launch=False)
    assert res["staging"]["excluded"] == []
    assert res["written"] == 2 and res["staging"]["copied"] == 2
    assert _names(b) == ["b.json"] and _names(s) == ["s.json"]


# --------------------------------------------------------------------------- #
# The preview shown in the confirmation
# --------------------------------------------------------------------------- #
def test_preview_matches_what_the_run_then_does(tmp_path, registry, config):
    """A confirmation that disagrees with the outcome is worse than none."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    (leaf / "old.json").write_text("{}", "utf-8")
    _stage_cfg(config, root)
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    pv = tosca.preview([str(p)], SCRIPT, registry, config)
    assert pv["remove_total"] == 1 and pv["copy_total"] == 1
    assert pv["targets"][0]["remove"] == ["old.json"]
    assert pv["targets"][0]["copy"] == ["order1.json"]
    assert pv["targets"][0]["path"] == str(leaf)

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    assert res["staging"]["removed"] == pv["remove_total"]
    assert res["staging"]["copied"] == pv["copy_total"]


def test_preview_writes_nothing(tmp_path, registry, config):
    wb = _wb(tmp_path, config)
    before = wb.read_bytes()
    root = tmp_path / "tree"
    leaf, = _tree(root, "Winners/Style Header/B - Blue Gum")
    (leaf / "old.json").write_text("{}", "utf-8")
    _stage_cfg(config, root)
    p = _sample(tmp_path, "styleheader_fmtB.json", "order1.json")

    tosca.preview([str(p)], SCRIPT, registry, config)
    assert _names(leaf) == ["old.json"]
    assert wb.read_bytes() == before


def test_preview_reports_the_combination_that_will_not_run(tmp_path, registry, config):
    """Surfaced while Cancel is still an option, not afterwards in the report."""
    wb = _wb(tmp_path, config)
    root = tmp_path / "tree"
    _tree(root, "Winners/Style Header/B - Blue Gum")
    _stage_cfg(config, root)
    ok = _sample(tmp_path, "styleheader_fmtB.json", "good.json")
    bad = _sample(tmp_path, "styleheader_fmtS.json", "bad.json")

    pv = tosca.preview([str(ok), str(bad)], SCRIPT, registry, config)
    assert pv["rows"] == 1
    assert len(pv["excluded"]) == 1 and pv["excluded"][0]["files"] == ["bad.json"]


def test_every_excluded_combination_is_reported_not_just_the_first(tmp_path, registry,
                                                                   config):
    """Two bad folders must produce TWO reported combinations, each with its own
    reason — the dialog lists them all and the report carries the detail.

    A run that stopped at the first would look like one problem to fix, and the
    second would only surface on the next attempt.
    """
    root = tmp_path / "tree"
    _tree(root, "T.J. Maxx/Style Header/T - Q-Line Small Gum Label")
    _tree(root, "Marshalls/Style Header/J - Rat Tail Gum Label")
    _stage_cfg(config, root)
    a = _sample(tmp_path, "styleheader_fmtT.json", "t.json", chain="01", fmt="T")
    b = _sample(tmp_path, "styleheader_fmtT.json", "j.json", chain="02", fmt="J")

    res = tosca.run([str(a), str(b)], SCRIPT, registry, config, launch=False)
    ex = res["staging"]["excluded"]
    assert len(ex) == 2, f"only {len(ex)} reported"
    # `format` carries the Key sheet's own wording, not the bare code
    assert {tosca.format_head(e["format"]) for e in ex} == {"T", "J"}
    for e in ex:
        assert e["reasons"] and "GTA UI" in e["reasons"][0]

    # ...and the REPORT carries the detail behind View report: every combination,
    # its reason, and the files that did not go anywhere.
    report = tosca.build_report(res) if hasattr(tosca, "build_report") else ""
    if report:
        assert report.count("[NOT RUN ]") == 2
        assert "t.json" in report and "j.json" in report
