"""Which SOURCE a Calgary JSON file came from — SCAN or WMS.

The two sources send **structurally identical** JSON; the only difference is
which header field is that file's unique key (``keytrol`` for SCAN,
``headerASNid`` for WMS on StyleHeader/DistLabel — CartonLabel uses
``pickListId`` either way). Nothing in the payload distinguishes them: SCAN
populates ``headerASNid`` too, and WMS carries a real-looking ``keytrol``, so
the source has to be *declared* rather than inferred from the data.

It is declared by NAME: a ``SCAN`` or ``WMS`` token in the file name or in any
folder above it. Anything unlabelled falls back to the configured default
(``WMS``), because the two mistakes are not equally bad — a WMS folder read as
SCAN would renumber ``keytrol``, which WMS ships as a constant placeholder, so
the fallback is chosen to fail toward "missed duplicate" rather than "fabricated
key". The UI asks once per folder and remembers the answer, which arrives here
as ``override``.

Matching is on whole TOKENS, not substrings: ``Calgary_SCAN_2026`` matches,
``SCANNED`` does not. A substring match would silently point Make Unique at the
wrong field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# Names are split into tokens on anything that is not a letter or digit, so
# "Calgary_SCAN-2026", "Calgary SCAN", and "SCAN.json" all yield a "SCAN" token.
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")

# Where a resolution came from, most specific first.
FROM_OVERRIDE = "override"
FROM_FILE = "file name"
FROM_FOLDER = "folder name"
FROM_DEFAULT = "default"


@dataclass(frozen=True)
class SourceResolution:
    """The source decided for a path, and how it was decided.

    ``source`` is always a concrete source name so callers never branch on
    None. ``resolved`` is False when nothing matched and the default was used —
    that is what the UI keys off to ask the user once per folder.
    """

    source: str
    reason: str
    resolved: bool
    conflict: bool = False       # file name and folder name disagreed
    matched_on: Optional[str] = None   # the name part that produced the match

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "reason": self.reason,
            "resolved": self.resolved,
            "conflict": self.conflict,
            "matched_on": self.matched_on,
        }


def _tokens(name: str) -> List[str]:
    """Upper-cased word tokens of a file or folder name."""
    return [t.upper() for t in _TOKEN_SPLIT.split(name or "") if t]


def _match(name: str, sources: Dict[str, List[str]]) -> Optional[str]:
    """The source whose configured token appears in ``name``, or None.

    A name carrying tokens for more than one source is ambiguous, so it matches
    nothing — better to fall through to a less specific name (or to be asked)
    than to pick one arbitrarily.
    """
    toks = set(_tokens(name))
    hits = [src for src, words in (sources or {}).items()
            if any(str(w).strip().upper() in toks for w in (words or []))]
    return hits[0] if len(hits) == 1 else None


def resolve_source(path, sources: Dict[str, List[str]], default: str,
                   override: Optional[str] = None,
                   root=None) -> SourceResolution:
    """Decide the source for ``path``.

    Precedence, most specific first:

    1. ``override`` — what the user answered for this folder (remembered by the
       UI), which beats any name.
    2. the file's own name,
    3. each folder above it, nearest first (stopping at ``root`` when given),
    4. ``default``, reported as unresolved.
    """
    if override and override in (sources or {}):
        return SourceResolution(override, FROM_OVERRIDE, True)

    p = Path(path)
    file_hit = _match(p.stem, sources) if p.suffix else None

    folder_hit = None
    folder_name = None
    stop = Path(root).resolve() if root else None
    for parent in p.parents:
        if stop is not None:
            try:
                if parent.resolve() == stop.parent:
                    break
            except OSError:                      # unreadable parent — stop walking
                break
        hit = _match(parent.name, sources)
        if hit:
            folder_hit, folder_name = hit, parent.name
            break

    if file_hit:
        # A file that names its source wins over the folder it happens to sit
        # in, but a disagreement is worth reporting rather than swallowing.
        return SourceResolution(
            file_hit, FROM_FILE, True,
            conflict=bool(folder_hit and folder_hit != file_hit),
            matched_on=p.name)
    if folder_hit:
        return SourceResolution(folder_hit, FROM_FOLDER, True, matched_on=folder_name)
    return SourceResolution(default, FROM_DEFAULT, False)
