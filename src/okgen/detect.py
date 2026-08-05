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

from okgen import paths as fs

# Known record markers (informational; detection does not depend on which one).
RECORD_MARKERS = {"|", "#", "&"}

# EU/EWMS pipe-delimited pretickets are UTF-8 with a BOM and a broken-bar
# marker; read as Latin-1 the header begins with these byte sequences.
_UTF8_BOM = "\xef\xbb\xbf"          # EF BB BF
_EU_HEADER_SIG = "\xc2\xa6P|"      # ¦ (UTF-8 C2 A6) + 'P|' (Indicator='P', delimited)


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
    with open(fs.long_path(path), "rb") as fh:
        raw = fh.readline()
    return raw.decode(encoding, errors="replace").rstrip("\r\n")


def detect_from_header(header: str) -> DetectionResult:
    """Apply the detection rule to an already-read header line."""
    # EU/EWMS delimited preticket: UTF-8 BOM + broken-bar '¦P|' header.
    # Checked first because its bytes don't fit the positional rules below.
    if header.startswith(_UTF8_BOM) and header[3:3 + len(_EU_HEADER_SIG)] == _EU_HEADER_SIG:
        return DetectionResult(
            "EUPreticket", "UTF-8 BOM + '¦P|' delimited header", "¦", header
        )

    # EU/EWMS pipe-delimited StyleHeader / CartonLabel: Latin-1 (no BOM), the
    # line leads with the '|' delimiter and carries the file format letter at
    # raw pos 2 -> 'D' = StyleHeader (layup), 'H' = CartonLabel (holdings).
    # NA chain codes at that position are always digits, so there's no clash.
    if header[:1] in RECORD_MARKERS and header[1:2] in ("D", "H"):
        name = "EUStyleHeader" if header[1] == "D" else "EUCartonLabel"
        return DetectionResult(
            name, f"raw pos2 == {header[1]!r} (EU delimited)", header[0], header
        )

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


# Calgary JSON layouts are identified by the ``data.type`` discriminator, not by
# a positional header rule (JSON has no fixed byte layout).
_JSON_TYPE_TO_LAYOUT = {
    "styleHeaders": "CalgaryStyleHeader",
    "cartonLabels": "CalgaryCartonLabel",
    "distributionLabels": "CalgaryDistLabel",
}

# Matched CASE-INSENSITIVELY. The discriminator is a word, not a positional
# byte, and the field is editable — so `StyleHeaders` or `STYLEHEADERS` is the
# same document type as `styleHeaders`. Matching exactly made any re-cased value
# detect as NOTHING, which left the file unopenable in OkGen.
_JSON_TYPE_LOWER = {k.lower(): v for k, v in _JSON_TYPE_TO_LAYOUT.items()}


def json_type_is_known(value) -> bool:
    """Whether ``value`` names a Calgary document type, in any casing."""
    return isinstance(value, str) and value.strip().lower() in _JSON_TYPE_LOWER


def canonical_json_types() -> list:
    """The three type words in their canonical casing (for the editor list)."""
    return list(_JSON_TYPE_TO_LAYOUT)


def _detect_json(path: Path) -> Optional[DetectionResult]:
    """Detect a Calgary JSON layout by its ``data.type``, or None if not JSON.

    JSON is multi-line (a pretty-printed doc's first line is just ``{``), so this
    reads the whole (small) file rather than the header line."""
    try:
        with open(fs.long_path(path), "rb") as fh:
            head = fh.read(64)
    except OSError:
        return None
    if head.lstrip(_UTF8_BOM.encode("latin-1")).lstrip()[:1] != b"{":
        return None                        # not a JSON document
    try:
        import json
        text = fs.read_text(path, "utf-8")
        jtype = json.loads(text).get("data", {}).get("type")
    except (ValueError, OSError):
        return None
    name = (_JSON_TYPE_LOWER.get(jtype.strip().lower())
            if isinstance(jtype, str) else None)
    if name is None:
        return None
    return DetectionResult(name, f"JSON data.type == {jtype!r}", "{", text[:80])


def detect_layout(path) -> DetectionResult:
    """Detect the layout for an OK file at ``path``."""
    path = Path(path)
    js = _detect_json(path)
    if js is not None:
        return js
    header = read_header_line(path)
    return detect_from_header(header)


def read_chain(path) -> str:
    """Return the 2-char chain code from an OK file header (cheap, no full parse).

    The chain is at xlsx Position 1, size 2 — i.e. raw header chars 2..3,
    right after the 1-char record marker.
    """
    header = read_header_line(Path(path))
    return header[1:3]
