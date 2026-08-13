"""Run TOSCA Script — the run report and its log file.

The run window used to print everything it knew: every staged file name, every
deleted file name, every excluded combination, in one scrolling column. It is a
SUMMARY now — counts and one line per Chain/Process/Format — and the detail
moved into a plain-text report behind "View report", which is also written to
``logs/okgen_tosca_<stamp>.log``.

Deliberately the same shape as ``nicelabel_post.build_report`` / ``_write_log``,
for the reason that file already states: ONE function returns the text, the log
gets it and the window shows it, so what a user pastes into a message cannot
differ from what the log says. These tests hold that property rather than
trusting it — a second formatter is exactly how the two would drift.

The load-bearing tests here are the ones about what must NOT be lost. Moving
detail out of a window is only safe if it is reachable somewhere, and the
failure mode is silent: a combination that did not run looks exactly like a
successful one once its file names are hidden.
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


@pytest.fixture
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture
def config():
    return Config.load(FIXTURE_CONFIG)


def _wb(tmp_path, config, name="laser_compare.xlsm"):
    dst = tmp_path / name
    shutil.copy2(TOSCA_FIX / name, dst)
    for s in config.tosca()["scripts"]:
        if s["name"] == SCRIPT:
            s["workbook"] = str(dst)
    return dst


def _stage_cfg(config, root, **over):
    cfg = {"enabled": True, "subpath": "{chain}\\{process}\\{format}",
           "match_format_by_code": True, "clear": "matching",
           "create_missing": False, "overwrite": True}
    cfg.update(over)
    config.tosca()["input_staging"] = cfg
    for s in config.tosca()["scripts"]:
        if s["name"] == SCRIPT:
            s["input_folders"] = [str(root)]


def _sample(tmp_path, src, name, chain=None, fmt=None):
    data = json.loads((FIXJ / src).read_text("utf-8"))
    if chain is not None:
        data["data"]["header"]["chain"] = chain
    if fmt is not None:
        data["data"]["header"]["format"] = fmt
    p = tmp_path / name
    p.write_text(json.dumps(data), "utf-8")
    return p


def _logs_to(config, folder):
    config.tosca()["log_folder"] = str(folder)


# --------------------------------------------------------------------------- #
# the log
# --------------------------------------------------------------------------- #

def test_every_run_writes_a_log(tmp_path, registry, config):
    """The user's explicit call: every run, not only a failing one. "What did
    last Tuesday's run actually stage?" is the question a log exists to answer,
    and by then the result window is long gone."""
    _wb(tmp_path, config)
    logs = tmp_path / "logs"
    _logs_to(config, logs)
    p = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")
    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False, stage=False)

    assert res["log"], "no log path returned"
    written = Path(res["log"])
    assert written.exists()
    assert written.parent == logs
    assert written.name.startswith("okgen_tosca_") and written.suffix == ".log"


def test_the_log_and_the_window_show_the_same_text(tmp_path, registry, config):
    """The whole reason there is one build_report(): a report pasted from the
    window into a message must be the report in the file, byte for byte."""
    _wb(tmp_path, config)
    _logs_to(config, tmp_path / "logs")
    p = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")
    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False, stage=False)

    assert res["report"]
    assert Path(res["log"]).read_text(encoding="utf-8") == res["report"]


def test_the_log_folder_falls_back_to_the_one_beside_okgen(tmp_path, registry, config):
    """Blank or absent `log_folder:` means the `logs` folder beside OkGen — next
    to the okgen_send_*, okgen_total_qty_* and okgen_folder_dialog_* files that
    are already there."""
    config.tosca().pop("log_folder", None)
    assert tosca.log_folder(config) == tosca.default_log_folder()
    assert tosca.default_log_folder().name == "logs"

    config.tosca()["log_folder"] = "   "        # blank counts as absent
    assert tosca.log_folder(config) == tosca.default_log_folder()

    config.tosca()["log_folder"] = str(tmp_path / "elsewhere")
    assert tosca.log_folder(config) == tmp_path / "elsewhere"


def test_a_log_that_cannot_be_written_never_fails_the_run(tmp_path, registry, config):
    """A logging problem must not cost the user a run that otherwise worked —
    the report still reaches the window, and the UI simply omits the "Also
    written to" line rather than naming a file that is not there."""
    _wb(tmp_path, config)
    # a FILE where the folder should be, so mkdir cannot succeed
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("x", encoding="utf-8")
    _logs_to(config, blocked)
    p = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False, stage=False)
    assert res["log"] is None
    assert res["report"], "the report must survive a failed log write"
    assert res["written"] == 1


# --------------------------------------------------------------------------- #
# the summary roll-up the window renders
# --------------------------------------------------------------------------- #

def test_combinations_carry_each_rows_own_staging_counts(tmp_path, registry, config):
    """One entry per Chain/Process/Format with its own copied/removed figures.

    Computed on the SERVER so the report and the window cannot disagree about a
    count — the row IS the folder, which is the addressing rule staging is built
    on, so the join is available here and nowhere else without repeating it.
    """
    _wb(tmp_path, config)
    root = tmp_path / "tree"
    (root / "T.J. Maxx" / "Style Header" / "A - Purple Tag").mkdir(parents=True)
    _stage_cfg(config, root)
    _logs_to(config, tmp_path / "logs")
    p = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    combos = res["combinations"]
    assert len(combos) == 1
    c = combos[0]
    assert c["status"] == "written"
    assert c["chain"] == "T.J. Maxx" and c["process"] == "Style Header"
    assert c["copied"] == 1
    assert c["paths"], "a written combination must name the folder it staged into"


def test_an_excluded_combination_is_in_the_roll_up_as_not_run(tmp_path, registry, config):
    """The one outcome that looks exactly like success once file names are
    hidden, so it is carried as its own entry rather than only as a count."""
    _wb(tmp_path, config)
    root = tmp_path / "tree"
    root.mkdir()                       # no chain/process/format folders at all
    _stage_cfg(config, root)
    _logs_to(config, tmp_path / "logs")
    p = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    not_run = [c for c in res["combinations"] if c["status"] == "not_run"]
    assert len(not_run) == 1
    assert not_run[0]["files"] == ["sh.json"]
    assert not_run[0]["reasons"], "an excluded combination must say why"
    # and it is NOT counted as written
    assert res["written"] == 0


# --------------------------------------------------------------------------- #
# what the report must contain — the detail that left the window
# --------------------------------------------------------------------------- #

def test_the_report_carries_what_the_window_no_longer_shows(tmp_path, registry, config):
    """The load-bearing test of this change. Moving detail out of the window is
    only correct if it is reachable, and each of these was previously printed in
    the window itself."""
    _wb(tmp_path, config)
    root = tmp_path / "tree"
    leaf = root / "T.J. Maxx" / "Style Header" / "A - Purple Tag"
    leaf.mkdir(parents=True)
    (leaf / "previous_one.json").write_text("{}", encoding="utf-8")
    _stage_cfg(config, root)
    _logs_to(config, tmp_path / "logs")
    p = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")

    res = tosca.run([str(p)], SCRIPT, registry, config, launch=False)
    rep = res["report"]

    assert "OkGen — Run TOSCA Script" in rep
    assert res["workbook"] in rep                      # the workbook path
    assert res["data_sheet"] in rep                    # the sheet
    assert "sh.json" in rep                            # the copied file NAME
    assert "previous_one.json" in rep                  # the removed file NAME
    assert str(leaf) in rep                            # the staged folder PATH
    assert "T.J. Maxx" in rep and "Style Header" in rep
    assert "1 rows written" in rep
    assert "Started" in rep and "Elapsed" in rep


def test_the_report_names_an_excluded_combination_and_its_reason(tmp_path, registry,
                                                                 config):
    _wb(tmp_path, config)
    root = tmp_path / "tree"
    root.mkdir()
    _stage_cfg(config, root)
    _logs_to(config, tmp_path / "logs")
    p = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")

    rep = tosca.run([str(p)], SCRIPT, registry, config, launch=False)["report"]
    assert "NOT RUN" in rep
    assert "sh.json" in rep
    assert "1 combination(s) NOT run" in rep
    # the reason, not just the fact
    assert "no " in rep.lower()


def test_the_report_names_files_the_script_does_not_apply_to(tmp_path, registry,
                                                             config):
    """.OK selected for a JSON script — reported, never silently dropped."""
    _wb(tmp_path, config)
    _logs_to(config, tmp_path / "logs")
    # The fixture script accepts both engines (no `applies_to`, the back-compat
    # default). Scope it to JSON so there is a skip to report at all — without
    # this the test would assert on an empty list and pass vacuously.
    for s in config.tosca()["scripts"]:
        if s["name"] == SCRIPT:
            s["applies_to"] = ["json"]
    ok_file = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", ok_file)
    good = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")

    res = tosca.run([str(good), str(ok_file)], SCRIPT, registry, config,
                    launch=False, stage=False)
    assert res["skipped"], "the .OK should have been skipped by this JSON script"
    assert "NOT APPLICABLE" in res["report"]
    assert "StyleHeader.OK" in res["report"]


def test_staging_switched_off_is_stated_in_the_report(tmp_path, registry, config):
    """"No files were copied" is exactly the outcome a run that looked
    successful could otherwise hide, so it is a sentence rather than a zero."""
    _wb(tmp_path, config)
    _logs_to(config, tmp_path / "logs")
    config.tosca()["input_staging"] = {"enabled": False}
    p = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")

    rep = tosca.run([str(p)], SCRIPT, registry, config, launch=False)["report"]
    assert "STAGING OFF" in rep


def test_the_report_says_the_bat_was_not_started(tmp_path, registry, config):
    _wb(tmp_path, config)
    _logs_to(config, tmp_path / "logs")
    p = _sample(tmp_path, "styleheader_fmtB.json", "sh.json", chain="01", fmt="A")
    rep = tosca.run([str(p)], SCRIPT, registry, config, launch=False,
                    stage=False)["report"]
    assert "LAUNCH" in rep


def test_long_file_lists_are_wrapped_not_one_endless_line(tmp_path, registry, config):
    """A log line carrying forty comma-separated names is unreadable in a
    terminal and unquotable in a message."""
    names = [f"file_{i:03d}_with_a_realistically_long_name.json" for i in range(40)]
    lines = tosca._wrap_names(names, 6)
    assert len(lines) > 1
    assert all(len(ln) <= 110 for ln in lines), max(len(ln) for ln in lines)
    assert all(ln.startswith("      ") for ln in lines)
    # nothing lost in the wrapping
    joined = " ".join(ln.strip() for ln in lines)
    for n in names:
        assert n in joined


def test_a_run_with_nothing_to_report_still_produces_one(tmp_path, registry, config):
    """A run that staged nothing is itself an answer — the report says so
    rather than being empty or absent."""
    _wb(tmp_path, config)
    _logs_to(config, tmp_path / "logs")
    bad = tmp_path / "Broken.json"
    bad.write_text('{"data": {"type": "nope"}}', encoding="utf-8")

    res = tosca.run([str(bad)], SCRIPT, registry, config, launch=False, stage=False)
    assert res["written"] == 0
    assert res["report"]
    assert "0 rows written" in res["report"]
    assert Path(res["log"]).exists(), "a no-op run is logged too"
