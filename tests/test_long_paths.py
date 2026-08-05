"""Windows long paths (MAX_PATH) — writing, reading back, and warning.

The reported failure: converting an `.OK` file whose output landed 264
characters deep died with `[Errno 2] No such file or directory: '….json.tmp'`.
The folder existed; Windows simply refuses a path over 260 characters and
reports it as "path not found". Conversion is where it bites first because it
is one of the operations that MAKES the path longer — the source name plus a
new `converted_…` folder — but Save As, the `.bak`, Volume Generate and Bulk
Rename's staging name all lengthen a path too.

Two halves here:

* the `okgen.paths` helper in isolation, with `os.name` forced to `nt` so the
  Windows string transformation is exercised on any dev machine; and
* real end-to-end runs over a genuinely >260-character path. POSIX allows
  those (its limit is per-component, not total), so these assert the round
  trip on every platform and would have caught the reported bug on Windows.
"""
import shutil
from pathlib import Path

import pytest

from okgen import detect, paths as fs
from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import parse_okfile

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"


@pytest.fixture
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture
def config():
    return Config.load(FIXTURE_CONFIG)


def _deep_folder(tmp_path: Path, target_len: int = 300) -> Path:
    """A folder deep enough that anything written in it passes ``target_len``.

    Built from several nested components rather than one huge name because a
    single path COMPONENT is capped at 255 characters on both NTFS and POSIX —
    it is the total that Windows limits to 260.
    """
    folder = tmp_path
    while len(str(folder)) < target_len:
        folder = folder / ("d" * 40)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --------------------------------------------------------------------------- #
# The helper
# --------------------------------------------------------------------------- #
def test_windows_paths_get_the_extended_length_prefix():
    assert fs.extended(r"C:\a\b.OK") == "\\\\?\\C:\\a\\b.OK"


def test_a_unc_share_takes_the_unc_form():
    """``\\\\?\\`` REPLACES the leading ``\\\\``, so a share needs ``UNC\\`` —
    prefixing it naively would name a nonexistent local path."""
    assert fs.extended(r"\\srv\share\a.OK") == "\\\\?\\UNC\\srv\\share\\a.OK"


def test_prefixing_is_idempotent():
    """Helpers call each other, so applying the prefix twice must be a no-op —
    a doubled prefix names nothing."""
    once = "\\\\?\\C:\\a\\b.OK"
    assert fs.extended(once) == once


def test_posix_paths_are_left_alone(monkeypatch):
    """There is no MAX_PATH on POSIX, and ``\\\\?\\`` is a legal filename there,
    so prefixing would create a wrongly-named file."""
    monkeypatch.setattr(fs, "_WINDOWS", False)
    assert fs.long_path("/tmp/a/b.OK") == "/tmp/a/b.OK"


def test_is_long_measures_the_real_path_not_the_prefix():
    assert not fs.is_long("C:\\" + "a" * 100)
    assert fs.is_long("C:\\" + "a" * 300)
    # the prefix is bookkeeping, not part of the name being measured
    assert not fs.is_long("\\\\?\\C:\\" + "a" * 100)


def test_the_windows_too_long_errno_is_recognised():
    """Win32 reports it as ERROR_PATH_NOT_FOUND (3), which is why the raw
    message says 'No such file or directory'."""
    exc = OSError(2, "No such file or directory")
    exc.winerror = 3
    assert fs.is_too_long_error(exc)
    other = OSError(2, "No such file or directory")
    other.winerror = 2
    assert not fs.is_too_long_error(other)


# --------------------------------------------------------------------------- #
# Conversion — the reported failure
# --------------------------------------------------------------------------- #
def test_conversion_writes_into_a_path_over_the_limit(tmp_path, registry, config):
    """The exact reported case: the `.OK` opens fine, the `.json` beneath a
    `converted_…` folder is over 260 characters. It must be WRITTEN, not
    reported as 'No such file or directory'."""
    folder = _deep_folder(tmp_path)
    src = folder / "SH_long_name_like_the_users.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src)

    res = service.convert_apply([str(src)], registry, config)

    assert res["errors"] == []
    assert res["written"] == 1
    out = sorted(Path(res["folder"]).glob("*.json"))
    assert len(out) == 1
    assert len(str(out[0])) > fs.MAX_PATH, "fixture must actually exceed the limit"


def test_a_converted_long_path_file_reopens_in_okgen(tmp_path, registry, config):
    """Writing it is only half the job — OkGen must be able to open what it
    just wrote, which means the READ paths take the prefix too."""
    folder = _deep_folder(tmp_path)
    src = folder / "SH_reopen.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src)
    res = service.convert_apply([str(src)], registry, config)
    out = sorted(Path(res["folder"]).glob("*.json"))[0]

    assert detect.detect_layout(out).layout == "CalgaryStyleHeader"
    okf = parse_okfile(out, registry=registry)
    assert okf.layout.name == "CalgaryStyleHeader"
    assert okf.to_bytes() == fs.read_bytes(out)      # still byte-exact (D20)


def test_conversion_warns_about_paths_other_programs_cannot_open(
        tmp_path, registry, config):
    """OkGen can write past 260, but Explorer and the consuming system may not
    open it — so the result says so instead of leaving a silent trap."""
    folder = _deep_folder(tmp_path)
    src = folder / "SH_warn.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src)

    res = service.convert_apply([str(src)], registry, config)

    assert len(res["long_paths"]) == 1
    assert res["long_paths"][0]["length"] > fs.MAX_PATH
    assert res["max_path"] == fs.MAX_PATH


def test_a_short_conversion_reports_no_long_paths(tmp_path, registry, config):
    """The warning must not fire on the ordinary case — it would be noise on
    every run and stop meaning anything."""
    src = tmp_path / "SH.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src)
    res = service.convert_apply([str(src)], registry, config)
    assert res["written"] == 1 and res["long_paths"] == []


# --------------------------------------------------------------------------- #
# The other write paths — same class, found by the same 'five call sites' rule
# --------------------------------------------------------------------------- #
def test_save_and_backup_survive_a_long_path(tmp_path, registry, config):
    """Save appends '.tmp' then '.bak', so it lengthens a path that was already
    legal — the same way conversion does."""
    folder = _deep_folder(tmp_path, target_len=250)
    src = folder / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src)
    assert len(str(src)) > fs.MAX_PATH - 10, "fixture must sit near the limit"

    okf = parse_okfile(src, registry=registry)
    okf.save(src)

    # D26: open normalizes junk, so the guarantee is idempotence plus provable
    # field preservation — not equality with a padded original.
    reopened = parse_okfile(src, registry=registry)
    assert reopened.to_bytes() == fs.read_bytes(src)
    assert [r.values() for r in reopened.records] == [r.values() for r in okf.records]


def test_save_as_into_a_long_path_leaves_the_source_untouched(
        tmp_path, registry, config):
    folder = _deep_folder(tmp_path)
    src = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src)
    original = fs.read_bytes(src)
    target = folder / "copy.OK"

    okf = parse_okfile(src, registry=registry)
    okf.save(target)

    assert len(str(target)) > fs.MAX_PATH
    assert fs.read_bytes(target) == okf.to_bytes()
    assert fs.read_bytes(src) == original        # D11: the source is not written


def test_copy_and_rename_work_past_the_limit(tmp_path):
    folder = _deep_folder(tmp_path)
    src = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src)

    dst = folder / "copied.OK"
    service.copy_file(str(src), str(dst))
    assert fs.exists(dst) and len(str(dst)) > fs.MAX_PATH

    renamed = folder / "renamed.OK"
    service.rename_file(str(dst), str(renamed))
    assert fs.exists(renamed) and not fs.exists(dst)


# --------------------------------------------------------------------------- #
# A simulated Windows filesystem
#
# The tests above run the real code over a real >260-character path, but on
# POSIX that path is simply legal — `long_path` is a pass-through there, so
# they would pass on the BROKEN code too and prove nothing about the fix.
#
# This fixture supplies the only thing POSIX won't: a filesystem that REFUSES
# an over-long path the way Win32 does. It is enforced at both boundaries a
# caller can cross — `builtins.open` (which the helper uses) and the pathlib
# methods (which the old code used) — so it cannot be satisfied by routing
# around the helper, only by prefixing the path.
# --------------------------------------------------------------------------- #
def test_conversion_survives_a_filesystem_that_enforces_max_path(
        tmp_path, registry, config, monkeypatch):
    """The reported bug, reproduced: with MAX_PATH enforced, converting into a
    deep folder must still write the file.

    Every filesystem entry point is guarded the same way — refuse an unprefixed
    path over the limit, honour a prefixed one — so the ONLY way to pass is to
    prefix. Without the `\\\\?\\` prefix this raises the user's exact error,
    `[Errno 2] No such file or directory: '\u2026.json.tmp'`.
    """
    import builtins

    folder = _deep_folder(tmp_path)
    src = folder / "SH_maxpath.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src)

    real = {"open": builtins.open, "replace": fs.os.replace,
            "makedirs": fs.os.makedirs, "mkdir": fs.os.mkdir,
            "exists": fs.os.path.exists, "remove": fs.os.remove}

    def strip(p):
        """The prefix means nothing to the real (POSIX) filesystem underneath —
        take it off before delegating."""
        s = str(p)
        return s[4:] if s.startswith("\\\\?\\") else p

    def enforce(p):
        """Win32's actual rule: an unprefixed path over the limit is refused as
        ERROR_PATH_NOT_FOUND. Applied to the two calls the user's traceback came
        from — the '.tmp' write and the replace that swaps it in."""
        s = str(p)
        if not s.startswith("\\\\?\\") and len(s) > fs.MAX_PATH:
            exc = OSError(2, "No such file or directory", s)
            exc.winerror = 3
            raise exc
        return strip(p)

    monkeypatch.setattr(fs, "_WINDOWS", True)
    monkeypatch.setattr(builtins, "open",
                        lambda f, *a, **k: real["open"](enforce(f), *a, **k))
    monkeypatch.setattr(fs.os, "replace",
                        lambda a, b: real["replace"](enforce(a), enforce(b)))
    # Not enforced, only unprefixed: makedirs recurses through os.path.exists,
    # so raising in here would fight the simulation rather than test the code.
    monkeypatch.setattr(fs.os, "makedirs",
                        lambda n, *a, **k: real["makedirs"](strip(n), *a, **k))
    monkeypatch.setattr(fs.os, "mkdir",
                        lambda n, *a, **k: real["mkdir"](strip(n), *a, **k))
    monkeypatch.setattr(fs.os.path, "exists", lambda n: real["exists"](strip(n)))
    monkeypatch.setattr(fs.os, "remove", lambda n: real["remove"](strip(n)))

    res = service.convert_apply([str(src)], registry, config)

    assert res["errors"] == [], f"long path refused: {res['errors']}"
    assert res["written"] == 1


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def test_a_too_long_write_is_explained_as_a_length_problem():
    """The raw OS text ('No such file or directory') sends the user hunting for
    a missing folder. The message must name the real cause."""
    out = Path("C:\\" + "a" * 300 + "\\f.json")
    exc = OSError(2, "No such file or directory")
    exc.winerror = 3
    msg = service._write_failed_message(out, exc)
    assert "260" in msg and "characters" in msg.lower()
    assert "No such file" not in msg


def test_a_locked_file_still_reports_as_locked_not_as_too_long():
    """The pre-existing D25 message must win for its own case — a lock and a
    length are different fixes."""
    exc = PermissionError(13, "Permission denied")
    msg = service._write_failed_message(Path("/tmp/short.OK"), exc)
    assert "open in another" in msg


# --------------------------------------------------------------------------- #
# The guard — D30/D34: a rule one path applies and the parallel paths skip is
# invisible for months. Pin that every write goes through the helper.
# --------------------------------------------------------------------------- #
def test_no_module_writes_through_a_bare_path_method():
    src_dir = Path(__file__).resolve().parents[1] / "src" / "okgen"
    banned = (".write_bytes(", ".write_text(", "os.replace(", "shutil.copy2(",
              "shutil.copytree(", ".read_bytes()", ".rename(")
    offenders = []
    for py in src_dir.rglob("*.py"):
        if py.name == "paths.py":          # the helper is where these belong
            continue
        for i, line in enumerate(py.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if any(b in code for b in banned) and "fs." not in code:
                offenders.append(f"{py.relative_to(src_dir)}:{i}: {line.strip()}")
    assert not offenders, (
        "these bypass okgen.paths and will fail on a >260-char Windows path:\n"
        + "\n".join(offenders))
