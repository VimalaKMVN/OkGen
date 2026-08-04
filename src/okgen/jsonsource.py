"""Which SOURCE a Calgary JSON file came from — SCAN or WMS.

**The file itself says which it is: a WMS file carries a ``headerASNid``, a SCAN
file does not.** The `.OK` formats that feed the SCAN side have no ASN ID field
at all (StyleHeader, Preticket, CartonLabel, DistLabels and EUPreticket — only
the two EU GTA layouts have one), so a SCAN document has nothing to put there.

That replaces an earlier belief — recorded in D27 — that SCAN populated
``headerASNid`` too and the source therefore had to be DECLARED by name. It
does not, so it is read from the payload instead, per FILE rather than per
folder. Confirmed against every sample in hand: 24 of 24 carry an ASN, they are
all WMS, and three distributionLabels that share one ``keytrol`` (140589) have
distinct ASNs — which only works if the ASN is the identity.

The source decides the KEY on StyleHeader/DistLabel (``keytrol`` for SCAN,
``headerASNid`` for WMS). CartonLabel keys on ``pickListId`` under both, but
still HAS a source and still reports it — knowing where a carton label came
from is useful even though it changes nothing about the key.

Name-based resolution is kept only as an explicit override for a caller that
passes one; nothing in the UI does. Matching is on whole TOKENS, never
substrings: ``Calgary_SCAN_2026`` matches, ``SCANNED`` does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# Names are split into tokens on anything that is not a letter or digit, so
# "Calgary_SCAN-2026", "Calgary SCAN", and "SCAN.json" all yield a "SCAN" token.
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")

# The header field whose presence means "this came from WMS".
ASN_FIELD = "headerASNid"

# Where a resolution came from, most specific first.
FROM_PAYLOAD = "the file's own headerASNid"
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

    Precedence, MOST SPECIFIC first:

    1. the file's own name — a file that names its source knows better than
       anything said about the folder around it,
    2. ``override`` — what the user answered for this folder (remembered by the
       UI), which beats a folder NAME but not a file naming itself,
    3. each folder above it, nearest first (stopping at ``root`` when given),
    4. ``default``, reported as unresolved.
    """
    p = Path(path)
    file_hit = _match(p.stem, sources) if p.suffix else None
    has_override = bool(override and override in (sources or {}))

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
        # A file that names its source wins over the folder it sits in AND over
        # a folder-level answer — both are statements about the folder, and this
        # is a statement about the file. A disagreement with either is reported
        # rather than swallowed.
        other = override if has_override else folder_hit
        return SourceResolution(
            file_hit, FROM_FILE, True,
            conflict=bool(other and other != file_hit),
            matched_on=p.name)
    if has_override:
        return SourceResolution(override, FROM_OVERRIDE, True)
    if folder_hit:
        return SourceResolution(folder_hit, FROM_FOLDER, True, matched_on=folder_name)
    return SourceResolution(default, FROM_DEFAULT, False)


def source_from_header(header: Optional[dict], sources: Dict[str, List[str]],
                       default: str) -> SourceResolution:
    """The source of a Calgary JSON file, read from its own header.

    A populated ``headerASNid`` means WMS; anything empty — null, "", spaces, or
    the key missing entirely — means SCAN. An unreadable header falls back to
    the configured default and is reported UNRESOLVED, so callers can tell
    "this file says SCAN" apart from "we could not tell".
    """
    if header is None:
        return SourceResolution(default, FROM_DEFAULT, False)
    names = set(sources or {})
    asn = header.get(ASN_FIELD)
    is_wms = asn is not None and str(asn).strip() != ""
    picked = "WMS" if is_wms else "SCAN"
    if picked not in names:                  # a site renamed its sources
        return SourceResolution(default, FROM_DEFAULT, False)
    return SourceResolution(picked, FROM_PAYLOAD, True, matched_on=ASN_FIELD)
