"""Identify which layout an OK file uses, from its header (first) record.

The rule is expressed in **raw** OK-file character positions — 1-based and
*including* the leading record marker (``|`` / ``#`` / ``&`` / occasionally
another byte). Because the xlsx ``Position`` column is measured into the
marker-stripped record, raw position == xlsx Position + 1.

    raw pos 4 == 'N'         -> StyleHeader
    raw pos 4 == 'Y'         -> Preticket
    raw pos 4 in ('7', '9')  -> DistLabels
    raw pos 5..6 == 'C:'     -> CartonLabel

Files are read as Latin-1 so every byte round-trips 1:1 (some markers and
fields are non-ASCII).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Known record markers (informational; detection does not depend on which one).
RECORD_MARKERS = {"|", "#", "&"}


@dataclass
class DetectionResult:
    layout: Optional[str]      # layout name, or None if no rule matched
    reason: str                # human-readable explanation
    marker: str                # the leading marker character
    header: str                # the raw header line (terminator stripped)

    @property
    def matched(self) -> bool:
        return self.layout is not None


def read_header_line(path: Path, encoding: str = "latin-1") -> str:
    """Return the first line of an OK file with line terminators stripped."""
    with open(path, "rb") as fh:
        raw = fh.readline()
    return raw.decode(encoding, errors="replace").rstrip("\r\n")


def detect_from_header(header: str) -> DetectionResult:
    """Apply the detection rule to an already-read header line."""
    marker = header[0] if header else ""

    def raw(pos: int) -> str:
        """1-based raw character (marker included); '' if out of range."""
        return header[pos - 1] if len(header) >= pos else ""

    p4, p5, p6 = raw(4), raw(5), raw(6)

    if p4 == "N":
        return DetectionResult("StyleHeader", "raw pos4 == 'N'", marker, header)
    if p4 == "Y":
        return DetectionResult("Preticket", "raw pos4 == 'Y'", marker, header)
    if p4 in ("7", "9"):
        return DetectionResult("DistLabels", f"raw pos4 == '{p4}'", marker, header)
    if p5 + p6 == "C:":
        return DetectionResult("CartonLabel", "raw pos5..6 == 'C:'", marker, header)

    return DetectionResult(
        None,
        f"no rule matched (pos4={p4!r}, pos5={p5!r}, pos6={p6!r})",
        marker,
        header,
    )


def detect_layout(path) -> DetectionResult:
    """Detect the layout for an OK file at ``path``."""
    header = read_header_line(Path(path))
    return detect_from_header(header)
