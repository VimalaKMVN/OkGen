"""The native folder chooser: a launch that FAILS must not look like a cancel.

Users reported repeatedly that "Open Folder" sometimes shows nothing at all —
not behind the browser, not on another monitor. Two things made that impossible
to diagnose, and both are covered here:

- ``browse_folder`` returned ``{"path": None}`` for BOTH an empty stdout after a
  crashed launch and an ordinary Cancel, so the UI said "No folder selected"
  either way and nothing was written anywhere.
- Each platform signals a cancel differently (Windows exits 0 with no output;
  ``osascript`` raises "User canceled. (-128)" and exits non-zero; ``zenity``
  exits 1), so one rule cannot serve all three. Getting it wrong in either
  direction is bad: a failure read as a cancel is the invisible bug, and a
  cancel read as a failure cries wolf on the commonest action there is.

The *placement* fix that accompanies this (the owner window no longer sits at
-32000,-32000, where a common dialog could be drawn off-screen) is PowerShell
and cannot be exercised from here — see PLAN §6.
"""
import subprocess

import pytest

from okgen.api import service


def _proc(rc=0, out="", err=""):
    return subprocess.CompletedProcess(["dialog"], rc, out, err)


@pytest.fixture(autouse=True)
def _clean_browse_state():
    service._BROWSE_STATE.update({"proc": None, "started": None, "killed": False})
    yield
    service._BROWSE_STATE.update({"proc": None, "started": None, "killed": False})


def _run(monkeypatch, system, proc):
    monkeypatch.setattr(service.platform, "system", lambda: system, raising=False)
    monkeypatch.setattr(service, "_run_dialog", lambda *a, **k: proc)
    monkeypatch.setattr(service, "_write_browse_log", lambda *a, **k: "/logs/x.log")
    return service.browse_folder()


# --------------------------------------------------------------------------- #
# A chosen folder still comes back, on every platform
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("system", ["Windows", "Darwin", "Linux"])
def test_a_chosen_path_is_returned(monkeypatch, system):
    r = _run(monkeypatch, system, _proc(0, "C:\\OkFiles\n"))
    assert r["path"] == "C:\\OkFiles"
    assert not r.get("failed")


# --------------------------------------------------------------------------- #
# Cancel is an ordinary action: no error, no log
# --------------------------------------------------------------------------- #
def test_windows_cancel_is_a_cancel(monkeypatch):
    """A dismissed OpenFileDialog exits 0 with nothing on stdout."""
    r = _run(monkeypatch, "Windows", _proc(0, ""))
    assert r["cancelled"] is True
    assert "error" not in r and "log" not in r


def test_macos_cancel_is_a_cancel(monkeypatch):
    """osascript exits non-zero on cancel — non-zero alone cannot mean failure."""
    r = _run(monkeypatch, "Darwin", _proc(1, "", "execution error: User canceled. (-128)"))
    assert r["cancelled"] is True
    assert "error" not in r


def test_linux_cancel_is_a_cancel(monkeypatch):
    r = _run(monkeypatch, "Linux", _proc(1, ""))
    assert r["cancelled"] is True


# --------------------------------------------------------------------------- #
# A failed launch is reported, and logged
# --------------------------------------------------------------------------- #
def test_windows_failure_is_not_reported_as_a_cancel(monkeypatch):
    """The reported bug: PowerShell dies, stdout is empty, and the old code
    called that "no folder selected"."""
    r = _run(monkeypatch, "Windows", _proc(1, "", "cannot be loaded because running "
                                                  "scripts is disabled on this system"))
    assert r["failed"] is True
    assert r.get("cancelled") is not True
    assert "scripts is disabled" in r["error"]
    assert r["log"]


def test_macos_failure_is_distinguished_from_cancel(monkeypatch):
    """Non-zero WITHOUT the cancel signature is a real failure."""
    r = _run(monkeypatch, "Darwin", _proc(1, "", "execution error: no user interaction allowed"))
    assert r["failed"] is True
    assert "no user interaction" in r["error"]


def test_linux_failure_is_distinguished_from_cancel(monkeypatch):
    r = _run(monkeypatch, "Linux", _proc(2, "", "Gtk cannot open display"))
    assert r["failed"] is True


def test_a_failure_with_no_output_still_reports(monkeypatch):
    """Silence is the worst case — it must still be an error, not a shrug."""
    r = _run(monkeypatch, "Windows", _proc(9, "", ""))
    assert r["failed"] is True
    assert "exit 9" in r["error"]


def test_a_timeout_is_reported_as_a_failure(monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "Windows", raising=False)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(["dialog"], 120)

    monkeypatch.setattr(service, "_run_dialog", boom)
    monkeypatch.setattr(service, "_write_browse_log", lambda *a, **k: "/logs/x.log")
    r = service.browse_folder()
    assert r["failed"] is True
    assert "2 minutes" in r["error"]


def test_a_missing_dialog_program_is_reported(monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "Linux", raising=False)

    def boom(*a, **k):
        raise FileNotFoundError("zenity")

    monkeypatch.setattr(service, "_run_dialog", boom)
    monkeypatch.setattr(service, "_write_browse_log", lambda *a, **k: "/logs/x.log")
    r = service.browse_folder()
    assert r["failed"] is True
    assert "paste a path" in r["error"]


# --------------------------------------------------------------------------- #
# The lock is released whatever happened — a failure must not lock out retries
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("proc", [_proc(0, "/tmp/x"), _proc(0, ""), _proc(3, "", "boom")])
def test_the_lock_is_released_every_time(monkeypatch, proc):
    _run(monkeypatch, "Windows", proc)
    assert service._BROWSE_LOCK.acquire(blocking=False)
    service._BROWSE_LOCK.release()


def test_a_second_request_while_one_is_open_is_refused(monkeypatch):
    """And it says HOW LONG, so the UI can offer to abandon it."""
    service._BROWSE_LOCK.acquire()
    service._BROWSE_STATE["proc"] = object()
    service._BROWSE_STATE["started"] = service.time.time() - 5
    try:
        r = service.browse_folder()
        assert r["already_open"] is True
        assert r["running_seconds"] >= 5
    finally:
        service._BROWSE_STATE["proc"] = None
        service._BROWSE_LOCK.release()


# --------------------------------------------------------------------------- #
# Abandoning a stuck chooser
# --------------------------------------------------------------------------- #
def test_cancel_with_nothing_running_says_so(monkeypatch):
    assert service.cancel_browse()["cancelled"] is False


def test_cancel_kills_the_running_dialog(monkeypatch):
    class FakeProc:
        def __init__(self):
            self.killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    p = FakeProc()
    service._BROWSE_STATE["proc"] = p
    assert service.cancel_browse()["cancelled"] is True
    assert p.killed is True
    assert service._BROWSE_STATE["killed"] is True


def test_an_abandoned_launch_is_a_cancel_not_a_failure(monkeypatch):
    """The user asked for it — logging it as a fault would bury the real ones."""
    logged = []

    def killed_mid_run(*a, **k):
        # The abandon arrives WHILE the dialog is running, which is the only
        # time it can: browse_folder clears the flag as it starts.
        service._BROWSE_STATE["killed"] = True
        return _proc(-9, "", "")

    monkeypatch.setattr(service.platform, "system", lambda: "Windows", raising=False)
    monkeypatch.setattr(service, "_run_dialog", killed_mid_run)
    monkeypatch.setattr(service, "_write_browse_log",
                        lambda *a, **k: logged.append(a) or "/logs/x.log")
    r = service.browse_folder()
    assert r["cancelled"] is True and r["abandoned"] is True
    assert logged == []


def test_browse_running_seconds_is_none_when_idle():
    assert service.browse_running_seconds() is None
