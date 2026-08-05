"""Windows long-path (MAX_PATH) support.

Windows' Win32 API refuses any path longer than 260 characters, and reports the
refusal as ``ERROR_PATH_NOT_FOUND`` — which surfaces in Python as
``[Errno 2] No such file or directory``. That message is actively misleading:
the folder is there, the path is simply too long to name.

The escape hatch is the extended-length prefix ``\\\\?\\``, which raises the
limit to ~32,767 characters. It needs no registry change and no admin rights,
but it is strict about its input: the path must be **fully qualified and
already normalized** — no ``.``/``..`` segments and no forward slashes, because
the prefix tells Win32 to skip the parsing step that would otherwise fix them
up. :func:`long_path` does that normalization, so callers can hand it any path.

OkGen hits the limit because several operations MAKE paths longer than the ones
the user already has on disk: ``.OK`` -> JSON conversion writes into a new
``converted_…`` subfolder, Volume Generate into ``generated_…``, and both the
atomic-write ``.tmp`` and the ``.bak`` append to the name. A source file that
opens fine can therefore be impossible to write back out.

Every write path goes through the wrappers here rather than ``Path`` methods
directly, for the D30/D34 reason: a rule applied in one path and skipped by the
parallel paths stays invisible for months. The reads matter just as much — a
file OkGen has just written over 260 characters must still be openable by
OkGen.

On POSIX every function is a pass-through: there is no MAX_PATH, and ``\\\\?\\``
is a legal (if odd) filename component there, so prefixing would be wrong.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

#: The Win32 limit. A path at or under this needs no prefix; the constant is
#: also what :func:`is_long` reports against so callers can warn about output
#: that other programs (Explorer, Excel, a label printer's watcher) may not be
#: able to open even though OkGen can write it.
MAX_PATH = 260

_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"

#: Resolved once at import rather than read from ``os.name`` per call, so a
#: test can flip it without mutating the real ``os`` module — patching
#: ``os.name`` globally makes ``pathlib`` hand out ``WindowsPath`` objects and
#: takes down anything else that touches a path, pytest included.
_WINDOWS = os.name == "nt"


def extended(abs_path: str) -> str:
    """The ``\\\\?\\`` form of an ALREADY-ABSOLUTE Windows path.

    Split out from :func:`long_path` so the string rule — the part with the UNC
    special case that is easy to get wrong — can be tested directly, on any
    platform, without pretending the host is Windows.
    """
    if abs_path.startswith(_PREFIX):
        return abs_path                       # safe to apply twice
    if abs_path.startswith("\\\\"):
        # The prefix REPLACES the leading '\\', so a share needs the UNC form;
        # prefixing naively would name a nonexistent local path.
        return _UNC_PREFIX + abs_path[2:]
    return _PREFIX + abs_path


def long_path(p) -> str:
    """``p`` as a string Win32 will accept at any length.

    On Windows, returns the absolute path with the ``\\\\?\\`` extended-length
    prefix. Already-prefixed paths are returned as-is so this is safe to apply
    twice. On POSIX, returns ``os.fspath(p)`` unchanged.
    """
    s = os.fspath(p)
    if not _WINDOWS:
        return s
    if s.startswith(_PREFIX):
        return s
    # \\?\ disables Win32's own path parsing, so the path must arrive
    # fully-qualified with '/' -> '\' and any '.'/'..' already resolved.
    return extended(os.path.abspath(s))


def is_long(p) -> bool:
    """Whether ``p`` exceeds Win32's classic limit.

    True means OkGen can still read/write it (via :func:`long_path`) but other
    programs on the box may not be able to open it.
    """
    s = os.fspath(p)
    if s.startswith(_PREFIX):
        s = s[len(_PREFIX):]
    return len(s) > MAX_PATH


# --------------------------------------------------------------------------- #
# Path operations — long-path-safe equivalents of the Path methods they replace
# --------------------------------------------------------------------------- #
def read_bytes(p) -> bytes:
    with open(long_path(p), "rb") as fh:
        return fh.read()


def read_text(p, encoding: str) -> str:
    with open(long_path(p), "r", encoding=encoding) as fh:
        return fh.read()


def write_bytes(p, data: bytes) -> None:
    with open(long_path(p), "wb") as fh:
        fh.write(data)


def write_text(p, text: str, encoding: str = "utf-8") -> None:
    with open(long_path(p), "w", encoding=encoding) as fh:
        fh.write(text)


def exists(p) -> bool:
    return os.path.exists(long_path(p))


def replace(src, dst) -> None:
    """``os.replace`` — the atomic rename every write path lands on."""
    os.replace(long_path(src), long_path(dst))


def rename(src, dst) -> None:
    os.rename(long_path(src), long_path(dst))


def unlink(p, missing_ok: bool = False) -> None:
    try:
        os.remove(long_path(p))
    except FileNotFoundError:
        if not missing_ok:
            raise


def mkdir(p, parents: bool = False, exist_ok: bool = False) -> None:
    lp = long_path(p)
    if parents:
        os.makedirs(lp, exist_ok=exist_ok)
        return
    try:
        os.mkdir(lp)
    except FileExistsError:
        if not exist_ok:
            raise


def copy2(src, dst) -> None:
    shutil.copy2(long_path(src), long_path(dst))


def copytree(src, dst) -> None:
    # copytree walks and rebuilds the tree itself, so the prefix has to be on
    # both ends for every nested path it derives from them.
    shutil.copytree(long_path(src), long_path(dst))


def is_too_long_error(exc: BaseException) -> bool:
    """Whether ``exc`` is Windows refusing a path for its LENGTH.

    Win32 reports it as ``ERROR_PATH_NOT_FOUND`` (3) or, when a single
    component is oversized, ``ERROR_FILENAME_EXCED_RANGE`` (206) — the first of
    which is indistinguishable from a genuinely missing folder by errno alone.
    Used only to phrase the error, never to decide control flow, so the
    ambiguity costs nothing.
    """
    return getattr(exc, "winerror", None) in (3, 206)
