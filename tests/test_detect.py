"""Detection tests: each sample OK file maps to its expected layout."""

import os
from pathlib import Path

import pytest

from okgen.detect import detect_from_header, detect_layout

DATA_DIR = Path(
    os.environ.get(
        "OKGEN_DATA_DIR",
        str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"),
    )
)

EXPECTED = {
    "CartonLabel.OK": "CartonLabel",
    "DistLabels.OK": "DistLabels",
    "Preticket.OK": "Preticket",
    "StyleHeader.OK": "StyleHeader",
    "EUPreticket.OK": "EUPreticket",
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


def test_eu_delimited_header_detection():
    # UTF-8 BOM + '¦P|' (read as Latin-1) -> EU delimited preticket.
    eu = "\xef\xbb\xbf\xc2\xa6P|05|A|10021888|"
    assert detect_from_header(eu).layout == "EUPreticket"
    # A BOM without the '¦P|' signature must NOT match.
    assert detect_from_header("\xef\xbb\xbf|02Y...").layout != "EUPreticket"
