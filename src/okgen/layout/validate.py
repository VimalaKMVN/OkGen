"""Self-validate a compiled layout against its embedded sample records.

Each tab's row-1 sample plus the per-field ``Value`` column is a built-in
test: slice the sample at the field's computed ``start``/``size`` and compare
to the expected ``Value``. Mismatches mean the layout's positions or sizes are
wrong (usually traceable to bad ``Position``/``field_size`` cells in the xlsx).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from okgen.layout.models import Layout, Section


@dataclass
class FieldCheck:
    section: str
    field: str
    start: Optional[int]
    size: Optional[int]
    expected: Optional[str]
    actual: Optional[str]
    status: str   # "ok" | "mismatch" | "skipped"
    note: str = ""


@dataclass
class LayoutReport:
    layout: str
    checks: List[FieldCheck]

    @property
    def ok(self) -> int:
        return sum(1 for c in self.checks if c.status == "ok")

    @property
    def mismatch(self) -> int:
        return sum(1 for c in self.checks if c.status == "mismatch")

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.checks if c.status == "skipped")

    @property
    def passed(self) -> bool:
        return self.mismatch == 0


def _slice(record: str, start: int, size: int) -> str:
    """1-based slice of ``size`` chars starting at ``start``."""
    return record[start - 1: start - 1 + size]


def _check_section(section: Section) -> List[FieldCheck]:
    checks: List[FieldCheck] = []
    record = section.sample_record
    for f in section.fields:
        if record is None or f.start is None or f.size is None or f.sample_value is None:
            checks.append(
                FieldCheck(
                    section.name, f.name, f.start, f.size,
                    f.sample_value, None, "skipped",
                    note="no sample/position/size to compare",
                )
            )
            continue
        actual = _slice(record, f.start, f.size)
        expected = f.sample_value
        # Compare exact first; fall back to a stripped comparison so that
        # Excel-trimmed Value cells don't show as spurious mismatches.
        if actual == expected:
            status, note = "ok", ""
        elif actual.strip() == expected.strip():
            status, note = "ok", "matched after trim"
        else:
            status, note = "mismatch", ""
        checks.append(
            FieldCheck(section.name, f.name, f.start, f.size, expected, actual, status, note)
        )
    return checks


def validate_layout(layout: Layout) -> LayoutReport:
    checks: List[FieldCheck] = []
    for section in layout.sections:
        checks.extend(_check_section(section))
    return LayoutReport(layout=layout.name, checks=checks)
