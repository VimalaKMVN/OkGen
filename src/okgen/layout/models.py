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
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "tab": self.tab,
            "record_length": self.record_length,
            "sample_record": self.sample_record,
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

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_file": self.source_file,
            "ticket_process": self.ticket_process,
            "issues": self.issues,
            "sections": [s.to_dict() for s in self.sections],
        }
