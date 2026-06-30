"""Parse and serialize fixed-width OK files against a compiled layout.

Design goal: **byte-exact round-trip**. Each record keeps its original raw
bytes (Latin-1, including the trailing ``\\`` terminator, space padding, and
``\\r``). Fields are *views* into that raw string; editing a field overwrites
only that field's span, so an unedited file re-serializes byte-for-byte.

Record-to-section mapping (derived from the sample files):
  * Line 0 is always the Header section; its first char is the marker and is
    stripped (``|`` for most, ``\\xa6`` for Preticket).
  * Later lines starting with a marker in :data:`DETAIL_MARKERS` strip that one
    char and map to the non-header sections in order of first appearance
    (``#`` -> first detail section, ``&`` -> second, ...).
  * Later lines with no marker (alphanumeric first char, e.g. Preticket Detail)
    have offset 0 and map to the first detail section.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dfield
from pathlib import Path
from typing import Dict, List, Optional

from okgen.detect import detect_layout
from okgen.layout.models import Field, Layout, Section

ENCODING = "latin-1"

# Leading chars treated as a one-char record marker on non-header lines.
DETAIL_MARKERS = set("|#&")


@dataclass
class Record:
    """One line of an OK file, with field access overlaid on raw bytes."""

    raw: str                       # the line's content, no '\n' (may end with '\r')
    offset: int                    # leading marker length (0 or 1)
    section: Optional[Section]
    index: int                     # 0-based line index in the file
    issues: List[str] = dfield(default_factory=list)
    # For delimited records: field name -> (start, end) span into ``raw``,
    # computed by walking the actual delimiters. None for fixed-width records,
    # which derive spans from the layout's start/size instead.
    field_spans: Optional[Dict[str, tuple]] = None

    @property
    def marker(self) -> str:
        return self.raw[:self.offset]

    def _field(self, name: str) -> Field:
        if self.section is None:
            raise KeyError(f"record {self.index} has no section")
        for f in self.section.fields:
            if f.name == name:
                return f
        raise KeyError(f"no field {name!r} in section {self.section.name!r}")

    def _span(self, f: Field):
        """(start, end) raw-string indices for a field, or None if unsized."""
        if self.field_spans is not None:
            # Delimited record: span was located by walking the delimiters.
            return self.field_spans.get(f.name)
        if f.start is None or f.size is None:
            return None
        start = self.offset + f.start - 1
        return start, start + f.size

    def get(self, name: str) -> Optional[str]:
        """Raw field slice (padding included), or None if the field is unsized."""
        span = self._span(self._field(name))
        if span is None:
            return None
        return self.raw[span[0]:span[1]]

    def values(self) -> Dict[str, Optional[str]]:
        """All sliceable fields of this record's section -> raw slice."""
        if self.section is None:
            return {}
        out: Dict[str, Optional[str]] = {}
        for f in self.section.fields:
            span = self._span(f)
            out[f.name] = self.raw[span[0]:span[1]] if span else None
        return out

    def set(self, name: str, value: str) -> None:
        """Overwrite a field's span, fitting ``value`` to the field width."""
        f = self._field(name)
        span = self._span(f)
        if span is None:
            raise ValueError(f"field {name!r} has no fixed size; cannot set")
        start, end = span
        if end > len(self.raw):
            raise ValueError(
                f"field {name!r} span {start}:{end} exceeds record length {len(self.raw)}"
            )
        # Pad to the span's own width so a delimited token keeps its exact
        # width (and the surrounding delimiters/terminator stay in place).
        self.raw = self.raw[:start] + _fit(value, f, end - start) + self.raw[end:]


def _fit(value: str, f: Field, width: Optional[int] = None) -> str:
    """Fit a value to a field width using justification inferred from the sample.

    ``width`` overrides the field's declared size (used for delimited records,
    whose span width is authoritative); defaults to ``f.size``.
    """
    size = width if width is not None else f.size
    if len(value) > size:
        raise ValueError(f"value {value!r} too long for field {f.name!r} (size {size})")
    justify, pad = _infer_format(f)
    if justify == "right":
        return value.rjust(size, pad)
    return value.ljust(size, pad)


def _infer_format(f: Field):
    """Best-guess (justify, pad-char) from the field's sample Value.

    Numeric, zero-padded samples -> right-justified, '0'-padded.
    Everything else -> left-justified, space-padded. This is a heuristic;
    real justification rules can be supplied per field later.
    """
    s = f.sample_value or ""
    core = s.strip()
    if core.isdigit() and (s.startswith("0") or " " not in s):
        return "right", "0"
    return "left", " "


@dataclass
class OkFile:
    """A parsed OK file: ordered records plus reconstruction metadata."""

    path: Optional[Path]
    layout: Layout
    records: List[Record]
    trailing_newline: bool
    newline: str = "\n"           # join separator (data carries its own '\r')

    def to_bytes(self) -> bytes:
        text = self.newline.join(r.raw for r in self.records)
        if self.trailing_newline:
            text += self.newline
        return text.encode(ENCODING)

    def save(self, path=None) -> None:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("no path to save to")
        target.write_bytes(self.to_bytes())

    def sections(self) -> Dict[str, List[Record]]:
        """Group records by section name."""
        out: Dict[str, List[Record]] = {}
        for r in self.records:
            key = r.section.name if r.section else "(unassigned)"
            out.setdefault(key, []).append(r)
        return out


# UTF-8 byte sequences as seen when an EU file is read as Latin-1 (byte-exact).
_BOM_LATIN1 = "\xef\xbb\xbf"        # EF BB BF
_BROKEN_BAR_UTF8 = "\xc2\xa6"      # ¦ encoded UTF-8 (C2 A6)
_BROKEN_BAR_LATIN1 = "\xa6"        # ¦ as a single Latin-1 byte (NA layouts)


def _delim_spans(raw: str, section: Section, delimiter: str,
                 terminator: str, is_header: bool) -> Dict[str, tuple]:
    """Locate each field's (start, end) span in a delimited record line.

    Walks the actual ``delimiter`` positions so spans are exact regardless of
    token width. The header carries a leading BOM + broken-bar marker; detail
    lines have no marker. The trailing ``terminator`` and any ``\\r`` are left
    outside every span so edits never disturb them.
    """
    start = 0
    if is_header:
        if raw[start:start + 3] == _BOM_LATIN1:
            start += 3
        if raw[start:start + 2] == _BROKEN_BAR_UTF8:
            start += 2
        elif raw[start:start + 1] == _BROKEN_BAR_LATIN1:
            start += 1

    body_end = len(raw)
    while body_end > start and raw[body_end - 1] == "\r":
        body_end -= 1
    if body_end > start and raw[body_end - 1:body_end] == terminator:
        body_end -= 1

    spans: Dict[str, tuple] = {}
    pos = start
    parts = raw[start:body_end].split(delimiter)
    for f, part in zip(section.fields, parts):
        spans[f.name] = (pos, pos + len(part))
        pos += len(part) + len(delimiter)
    return spans


def _assign_delimited(raws: List[str], layout: Layout) -> List[Record]:
    """Assign records for a delimited layout (header = line 0, rest = detail)."""
    header_sec = layout.sections[0] if layout.sections else None
    detail_secs = layout.sections[1:]
    records: List[Record] = []
    for i, raw in enumerate(raws):
        is_header = i == 0
        sec = header_sec if is_header else (detail_secs[0] if detail_secs else None)
        rec = Record(raw=raw, offset=0, section=sec, index=i)
        if sec is not None:
            rec.field_spans = _delim_spans(
                raw, sec, layout.delimiter, layout.record_terminator, is_header
            )
        records.append(rec)
    return records


def _assign_records(raws: List[str], layout: Layout) -> List[Record]:
    if getattr(layout, "delimited", False):
        return _assign_delimited(raws, layout)

    header_sec = layout.sections[0] if layout.sections else None
    detail_secs = layout.sections[1:]
    marker_to_sec: Dict[str, Optional[Section]] = {}
    records: List[Record] = []

    for i, raw in enumerate(raws):
        if i == 0:
            rec = Record(raw=raw, offset=1, section=header_sec, index=i)
        else:
            first = raw[:1]
            if first in DETAIL_MARKERS:
                if first not in marker_to_sec:
                    idx = len(marker_to_sec)
                    marker_to_sec[first] = detail_secs[idx] if idx < len(detail_secs) else None
                sec = marker_to_sec[first]
                rec = Record(raw=raw, offset=1, section=sec, index=i)
                if sec is None:
                    rec.issues.append(f"marker {first!r} has no matching section")
            else:
                sec = detail_secs[0] if detail_secs else None
                rec = Record(raw=raw, offset=0, section=sec, index=i)
        records.append(rec)
    return records


def parse_okfile(path, layout: Optional[Layout] = None, registry=None) -> OkFile:
    """Parse an OK file, detecting its layout if not supplied."""
    path = Path(path)
    data = path.read_bytes()
    text = data.decode(ENCODING)

    if layout is None:
        det = detect_layout(path)
        if det.layout is None:
            raise ValueError(f"could not detect layout for {path.name}: {det.reason}")
        if registry is None:
            raise ValueError("layout not given and no registry to resolve detected layout")
        layout = registry[det.layout]

    parts = text.split("\n")
    trailing_newline = len(parts) > 0 and parts[-1] == ""
    if trailing_newline:
        parts = parts[:-1]

    records = _assign_records(parts, layout)
    return OkFile(
        path=path,
        layout=layout,
        records=records,
        trailing_newline=trailing_newline,
    )
