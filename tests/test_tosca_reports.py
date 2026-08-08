"""📄 TOSCA Reports — open a script's test-results folder in the OS file manager.

A TOSCA run writes its reports (Word and Excel documents, .txt logs, images,
subfolders) into a folder beside the script's own .bat, four levels into
``D:\\ToscaAutomation\\...``. Finding it by hand was the pain; OkGen makes it one
click.

**It hands off to Explorer rather than listing the files itself.** Word and
Excel cannot be rendered in a browser without a heavy vendored converter (the
D31 trade, for far less benefit), and Explorer already does everything wanted
here — thumbnails, sorting, and a double-click that opens each document in its
own application. A table imitating Explorer would be more work and permanently
worse.

The folder is DECLARED per script (``results:`` in tosca.yaml), so the other
eight scripts are a config edit away. A script without the key is simply not
offered — never a button that opens nothing.
"""
from pathlib import Path

import pytest

from okgen import tosca
from okgen.api import service
from okgen.api.service import EditError
from okgen.config import Config


def _cfg(tmp_path, scripts):
    """A Config whose tosca.yaml declares exactly ``scripts``."""
    default_wb = "X:\\wb.xlsm"
    default_bat = "X:\\run.bat"
    lines = ["scripts:"]
    for s in scripts:
        wb = s.get("workbook", default_wb)
        bat = s.get("bat", default_bat)
        lines.append(f'  - name: "{s["name"]}"')
        lines.append(f'    workbook: {wb}')
        lines.append('    data_sheet: "Sheet1"')
        lines.append(f'    bat: {bat}')
        if s.get("results") is not None:
            lines.append(f'    results: {s["results"]}')
    (tmp_path / "tosca.yaml").write_text("\n".join(lines) + "\n")
    return Config.load(tmp_path)


# --------------------------------------------------------------------------- #
# Which scripts are offered
# --------------------------------------------------------------------------- #
def test_only_scripts_with_a_results_folder_are_offered(tmp_path):
    """A script with no `results:` must not appear — a picker entry that opens
    nothing is worse than no entry."""
    real = tmp_path / "Thermal_TestResults"
    real.mkdir()
    config = _cfg(tmp_path, [
        {"name": "OK Regression Thermal", "results": str(real)},
        {"name": "OK Functional Laser"},                       # no results key
    ])
    got = tosca.report_folders(config)
    assert [f["name"] for f in got] == ["OK Regression Thermal"]


def test_no_configured_folders_returns_empty_not_an_error(tmp_path):
    """Before any folder is set up the button must degrade quietly."""
    config = _cfg(tmp_path, [{"name": "OK Functional Laser"}])
    assert tosca.report_folders(config) == []


def test_a_folder_is_reported_missing_rather_than_hidden(tmp_path):
    """Only some of these folders exist on any given machine. A declared-but-
    absent one is still LISTED, flagged — hiding it would look like a config
    that never took effect."""
    config = _cfg(tmp_path, [
        {"name": "OK Regression Laser", "results": str(tmp_path / "nope")}])
    entry = tosca.report_folders(config)[0]
    assert entry["exists"] is False
    assert entry["folder"].endswith("nope")


def test_an_existing_folder_is_reported_as_present(tmp_path):
    real = tmp_path / "Laser_TestResults"
    real.mkdir()
    config = _cfg(tmp_path, [{"name": "OK Regression Laser", "results": str(real)}])
    assert tosca.report_folders(config)[0]["exists"] is True


# --------------------------------------------------------------------------- #
# Opening one
# --------------------------------------------------------------------------- #
def test_opening_reveals_the_folder(tmp_path, monkeypatch):
    real = tmp_path / "Thermal_TestResults"
    real.mkdir()
    config = _cfg(tmp_path, [{"name": "OK Regression Thermal", "results": str(real)}])
    seen = []
    monkeypatch.setattr(tosca, "_reveal", lambda f: seen.append(f))
    res = tosca.open_report_folder(config, "OK Regression Thermal")
    assert seen == [str(real)]
    assert res["opened"] == str(real)


def test_the_path_is_passed_raw_not_long_path_prefixed(tmp_path, monkeypatch):
    """`okgen.paths` prefixes \\\\?\\ for reads and writes past MAX_PATH (D44),
    but Explorer does NOT accept a prefixed path — the same reason run() hands
    os.startfile the plain .bat path. A prefixed path here would fail on exactly
    the deep folders this feature exists for."""
    real = tmp_path / "Thermal_TestResults"
    real.mkdir()
    config = _cfg(tmp_path, [{"name": "OK Regression Thermal", "results": str(real)}])
    seen = []
    monkeypatch.setattr(tosca, "_reveal", lambda f: seen.append(f))
    tosca.open_report_folder(config, "OK Regression Thermal")
    assert not seen[0].startswith("\\\\?\\")


def test_a_script_without_a_results_folder_says_what_to_do(tmp_path, monkeypatch):
    config = _cfg(tmp_path, [{"name": "OK Functional Laser"}])
    monkeypatch.setattr(tosca, "_reveal", lambda f: pytest.fail("must not open"))
    with pytest.raises(tosca.ToscaError) as exc:
        tosca.open_report_folder(config, "OK Functional Laser")
    assert "config/tosca.yaml" in str(exc.value)


def test_a_missing_folder_is_named_rather_than_opened(tmp_path, monkeypatch):
    """An empty Explorer window would not explain itself; the message must."""
    missing = tmp_path / "not_set_up"
    config = _cfg(tmp_path, [{"name": "OK Regression Laser", "results": str(missing)}])
    monkeypatch.setattr(tosca, "_reveal", lambda f: pytest.fail("must not open"))
    with pytest.raises(tosca.ToscaError) as exc:
        tosca.open_report_folder(config, "OK Regression Laser")
    assert "does not exist on this machine" in str(exc.value)
    assert str(missing) in str(exc.value)


def test_an_unknown_script_name_is_refused(tmp_path, monkeypatch):
    config = _cfg(tmp_path, [{"name": "OK Regression Laser", "results": str(tmp_path)}])
    monkeypatch.setattr(tosca, "_reveal", lambda f: pytest.fail("must not open"))
    with pytest.raises(tosca.ToscaError):
        tosca.open_report_folder(config, "Something Else")


# --------------------------------------------------------------------------- #
# Through the service layer (what the routes call)
# --------------------------------------------------------------------------- #
def test_service_lists_and_opens(tmp_path, monkeypatch):
    real = tmp_path / "Laser_TestResults"
    real.mkdir()
    config = _cfg(tmp_path, [{"name": "OK Regression Laser", "results": str(real)}])
    assert service.tosca_report_folders(config)[0]["name"] == "OK Regression Laser"
    monkeypatch.setattr(tosca, "_reveal", lambda f: None)
    assert service.open_tosca_reports(config, "OK Regression Laser")["opened"] == str(real)


def test_service_raises_edit_error_so_the_route_returns_422(tmp_path):
    """The route maps EditError to 422; a raw ToscaError would 500."""
    config = _cfg(tmp_path, [{"name": "OK Regression Laser"}])
    with pytest.raises(EditError):
        service.open_tosca_reports(config, "OK Regression Laser")


# --------------------------------------------------------------------------- #
# The SHIPPED config — the two folders the user has set up
# --------------------------------------------------------------------------- #
def test_the_shipped_config_declares_the_four_regression_folders():
    """The .OK and JSON regression scripts, each with its own results folder.
    Unquoted in YAML, so every backslash survives — double quotes would break
    the whole file (and with it every TOSCA script), the trap tosca.yaml warns
    about at the top."""
    config = Config.load(Path(__file__).resolve().parents[1] / "config")
    by_name = {f["name"]: f["folder"] for f in tosca.report_folders(config)}
    assert set(by_name) == {"OK Regression Thermal", "OK Regression Laser",
                            "JSON Regression Thermal", "JSON Regression Laser"}
    assert by_name["OK Regression Thermal"].endswith(
        r"REG_THERMAL_SH_PT_CL_DL_Comparison\Thermal_TestResults")
    assert by_name["OK Regression Laser"].endswith(
        r"REG_LASER_SH_PT_CL_DL_Comparison\Laser_TestResults")
    assert by_name["JSON Regression Thermal"].endswith(
        r"REG_JSON_THERMAL_SH_PT_CL_DL_Comparison\Thermal_TestResults")
    assert by_name["JSON Regression Laser"].endswith(
        r"REG_JSON_LASER_SH_PT_CL_DL_Comparison\Laser_TestResults")
    assert all("\\" in f for f in by_name.values()), "backslashes must survive YAML"


def test_the_ok_and_json_folders_are_distinct_despite_identical_leaf_names():
    """`Laser_TestResults` names TWO different folders — one under
    REG_LASER_…, one under REG_JSON_LASER_…. That is exactly why the picker
    shows the full path beside the script name: the leaf alone is ambiguous, and
    opening the .OK results when you wanted the JSON ones would look right."""
    config = Config.load(Path(__file__).resolve().parents[1] / "config")
    folders = [f["folder"] for f in tosca.report_folders(config)]
    leaves = [f.rsplit("\\", 1)[-1] for f in folders]
    assert len(set(leaves)) == 2, "leaf names collide, by design"
    assert len(set(folders)) == 4, "but the full paths must all differ"


def test_every_results_folder_sits_beside_its_own_script():
    """A results folder belongs to ONE script. Pairing it with the wrong .bat's
    directory would silently open another suite's reports."""
    import ntpath
    config = Config.load(Path(__file__).resolve().parents[1] / "config")
    for s in config.tosca_scripts():
        folder = (s.get("results") or "").strip()
        if not folder:
            continue
        assert ntpath.dirname(folder) == ntpath.dirname(s["bat"]), s["name"]


def test_declaring_a_results_folder_does_not_disturb_running_that_script():
    """The new key is additive: the script's workbook, sheet and .bat are
    untouched, so Run TOSCA behaves exactly as before."""
    config = Config.load(Path(__file__).resolve().parents[1] / "config")
    script = next(s for s in config.tosca_scripts()
                  if s.get("name") == "OK Regression Thermal")
    assert script["workbook"].endswith(".xlsm")
    assert script["bat"].endswith("REG_THERMAL_ExecutionScript.bat")
    assert script["data_sheet"]


# --------------------------------------------------------------------------- #
# Bringing the window to the FRONT
#
# `os.startfile` opens Explorer but does not raise it: OkGen is a background
# process as far as Win32 is concerned, so the window can land behind the
# browser — the same foreground lock that made the folder chooser look like it
# had never opened (D24 / v0.80.0), reported here as "the reports folder is not
# visible sometimes".
# --------------------------------------------------------------------------- #
def test_the_windows_script_raises_the_window_rather_than_only_opening_it():
    ps = tosca._REVEAL_PS
    for needed in ("SetForegroundWindow", "BringWindowToTop", "AttachThreadInput",
                   "keybd_event"):
        assert needed in ps, needed


def test_a_minimised_window_is_restored_not_just_raised():
    """Raising an iconic window shows a taskbar flash and nothing else."""
    assert "IsIconic" in tosca._REVEAL_PS
    assert "ShowWindow" in tosca._REVEAL_PS


def test_an_already_open_folder_is_reused_not_duplicated():
    """Clicking Reports twice must raise the one window, not stack copies."""
    assert "Find-Window" in tosca._REVEAL_PS
    assert "$shell.Windows()" in tosca._REVEAL_PS


def test_the_path_is_passed_by_environment_not_interpolated():
    """A Windows path is full of backslashes and may contain quotes. Building
    PowerShell by concatenation is how a path becomes code."""
    assert "OKGEN_REVEAL_PATH" in tosca._REVEAL_PS
    assert "{folder}" not in tosca._REVEAL_PS


def test_the_script_still_opens_the_folder_if_the_window_is_never_found():
    """Never leave the user with nothing because the raise failed."""
    assert "Start-Process explorer.exe" in tosca._REVEAL_PS


def test_a_failure_falls_back_to_the_plain_open(monkeypatch):
    """Worst case must be exactly the previous behaviour, not an error."""
    seen = []
    monkeypatch.setattr(tosca.os, "name", "nt", raising=False)
    monkeypatch.setattr(tosca.os, "startfile", lambda f: seen.append(f), raising=False)

    def boom(_):
        raise RuntimeError("no powershell")

    monkeypatch.setattr(tosca, "_reveal_windows_front", boom)
    tosca._reveal(r"D:\ToscaAutomation\x")
    assert seen == [r"D:\ToscaAutomation\x"]


def test_non_windows_opens_without_the_foreground_dance(monkeypatch):
    """`open` and `xdg-open` activate the window themselves."""
    seen = []
    monkeypatch.setattr(tosca.os, "name", "posix", raising=False)
    monkeypatch.setattr(tosca.subprocess, "Popen",
                        lambda cmd, **k: seen.append(cmd))
    tosca._reveal("/tmp/reports")
    assert seen and seen[0][0] in ("open", "xdg-open")
    assert seen[0][1] == "/tmp/reports"
