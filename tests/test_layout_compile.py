"""Phase 1 regression tests: layouts compile and self-validate against samples."""

import os
from pathlib import Path

import pytest

from okgen.layout.compiler import compile_dir
from okgen.layout.validate import validate_layout

DATA_DIR = Path(
    os.environ.get(
        "OKGEN_DATA_DIR",
        "/Users/praveendx/repos/OkGenData/OkFileDefinitions",
    )
)

pytestmark = pytest.mark.skipif(
    not DATA_DIR.is_dir(), reason=f"sample data dir not present: {DATA_DIR}"
)


@pytest.fixture(scope="module")
def layouts():
    return compile_dir(DATA_DIR)


def test_all_four_layouts_compile(layouts):
    names = {l.name for l in layouts}
    assert names == {"CartonLabel", "DistLabels", "Preticket", "StyleHeader"}


def test_no_sample_mismatches(layouts):
    """Every field with a known size/position must match its sample slice."""
    for layout in layouts:
        report = validate_layout(layout)
        bad = [
            f"{c.section}.{c.field}: expected {c.expected!r} got {c.actual!r}"
            for c in report.checks
            if c.status == "mismatch"
        ]
        assert not bad, f"{layout.name} mismatches: " + "; ".join(bad)


def test_positions_are_contiguous_where_known(layouts):
    """Fields with known start/size must not overlap within a section."""
    for layout in layouts:
        for section in layout.sections:
            prev_end = 0
            for f in section.fields:
                if f.start is None or f.size is None:
                    continue
                assert f.start > prev_end, (
                    f"{layout.name}/{section.name}/{f.name} overlaps "
                    f"(start {f.start} <= prev end {prev_end})"
                )
                prev_end = f.end
