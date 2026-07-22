"""Data model for a compiled OK-file layout.

A *Layout* corresponds to one xlsx definition file (one OK file type).
A *Section* corresponds to one tab in that xlsx (Header, Store, Detail, ...).
A *Field* is one fixed-width field within a section's record.

Positions are 1-based and measured into the record *after* the leading
record marker (``|`` / ``#``) has been stripped.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class Field:
    """One fixed-width field within a section."""

    name: str                      # output_field_name (cleaned)
    field_name: str                # original field_name from the spec
    size: Optional[int]            # field_size; None when the spec value was bad
    start: Optional[int]           # 1-based start, recomputed from cumulative sizes
    field_type: str = "char"
    declared_position: Optional[int] = None   # Position column as found in xlsx
    sample_value: Optional[str] = None         # Value column (expected slice)
    field_id: Optional[str] = None
    issues: List[str] = field(default_factory=list)

    @property
    def end(self) -> Optional[int]:
        """1-based inclusive end position, or None if size/start unknown."""
        if self.start is None or self.size is None:
            return None
        return self.start + self.size - 1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Section:
    """One tab / record section of an OK file layout."""

    name: str                      # field_name_section (cleaned) / tab title
    tab: str                       # original worksheet title
    fields: List[Field] = field(default_factory=list)
    record_length: Optional[int] = None   # computed span of all fields
    sample_record: Optional[str] = None   # tab's row-1 sample (marker stripped)
    ignored_fields: List[str] = field(default_factory=list)  # dropped unsized rows
    issues: List[str] = field(default_factory=list)
    # Canonical record-type marker for this section's detail lines (e.g. "#",
    # "&", or "" for marker-less details). Records route to their section by
    # THIS marker rather than by order of appearance, so an empty section no
    # longer captures a later section's records. None = unknown (falls back to
    # order-of-appearance). Populated by the compiler; header sections leave it
    # None (the header is always line 0, routed by position, not marker).
    marker: Optional[str] = None
    # A real, full record line for this section (EOL stripped), learned from the
    # bundled reference ``<layout>.OK`` sample. Used to seed the first row when a
    # user adds records to an otherwise-empty section — it is a genuine,
    # correctly-formatted line (marker, delimiters, padding, terminator all
    # intact), which is why it is preferred over ``sample_record`` (the xlsx
    # sample can be marker-stripped, padding-less, or non-delimited).
    sample_raw: Optional[str] = None
    # A real ALL-BLANK (zero/space-filled) record line for this section, learned
    # from the reference ``<layout>.OK`` when it contains such filler rows (some
    # formats, e.g. Preticket, pad their detail block with all-zero rows). Used
    # as the template when auto-filling a section's trailing zero rows and no
    # blank row is present in the file being edited to copy from. None when the
    # reference has no filler row.
    filler_raw: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "tab": self.tab,
            "record_length": self.record_length,
            "sample_record": self.sample_record,
            "marker": self.marker,
            "sample_raw": self.sample_raw,
            "filler_raw": self.filler_raw,
            "ignored_fields": self.ignored_fields,
            "issues": self.issues,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass
class Layout:
    """A full OK-file layout compiled from one xlsx definition file."""

    name: str                      # logical name, e.g. "CartonLabel"
    source_file: str               # xlsx filename it was compiled from
    ticket_process: Optional[str] = None   # e.g. "Carton Label"
    sections: List[Section] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    # Delimited layouts (e.g. EU/EWMS pretickets) store fields as
    # ``delimiter``-separated tokens terminated by ``record_terminator``,
    # rather than fixed-width slices. Fixed-width layouts leave this False.
    delimited: bool = False
    delimiter: str = "|"
    record_terminator: str = "\\"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_file": self.source_file,
            "ticket_process": self.ticket_process,
            "delimited": self.delimited,
            "delimiter": self.delimiter,
            "record_terminator": self.record_terminator,
            "issues": self.issues,
            "sections": [s.to_dict() for s in self.sections],
        }
