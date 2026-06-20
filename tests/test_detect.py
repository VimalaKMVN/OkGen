"""Detection tests: each sample OK file maps to its expected layout."""

import os
from pathlib import Path

import pytest

from okgen.detect import detect_from_header, detect_layout

DATA_DIR = Path(
    os.environ.get(
        "OKGEN_DATA_DIR",
        "/Users/praveendx/repos/OkGenData/OkFileDefinitions",
    )
)

EXPECTED = {
    "CartonLabel.OK": "CartonLabel",
    "DistLabels.OK": "DistLabels",
    "Preticket.OK": "Preticket",
    "StyleHeader.OK": "StyleHeader",
}

pytestmark = pytest.mark.skipif(
    not DATA_DIR.is_dir(), reason=f"sample data dir not present: {DATA_DIR}"
)


@pytest.mark.parametrize("filename,expected", sorted(EXPECTED.items()))
def test_detect_sample_files(filename, expected):
    result = detect_layout(DATA_DIR / filename)
    assert result.layout == expected, f"{filename}: {result.reason}"


def test_rule_from_synthetic_headers():
    # raw positions, marker included
    assert detect_from_header("|03N...").layout == "StyleHeader"
    assert detect_from_header("|02Y...").layout == "Preticket"
    assert detect_from_header("|017...").layout == "DistLabels"
    assert detect_from_header("|019...").layout == "DistLabels"
    assert detect_from_header("|011C:...").layout == "CartonLabel"
    assert detect_from_header("|01X...").layout is None
