"""Phase 2 tests: byte-exact round-trip, field slicing, and edit fidelity."""

import os
from pathlib import Path

import pytest

from okgen.layout.registry import LayoutRegistry
from okgen.okfile import parse_okfile

DATA_DIR = Path(
    os.environ.get(
        "OKGEN_DATA_DIR",
        str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"),
    )
)

OK_FILES = ["CartonLabel.OK", "DistLabels.OK", "Preticket.OK", "StyleHeader.OK",
            "EUPreticket.OK"]

pytestmark = pytest.mark.skipif(
    not DATA_DIR.is_dir(), reason=f"sample data dir not present: {DATA_DIR}"
)


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.mark.parametrize("filename", OK_FILES)
def test_roundtrip_byte_identical(registry, filename):
    path = DATA_DIR / filename
    okf = parse_okfile(path, registry=registry)
    assert okf.to_bytes() == path.read_bytes(), f"{filename} did not round-trip"


@pytest.mark.parametrize("filename", OK_FILES)
def test_header_is_single_record(registry, filename):
    okf = parse_okfile(DATA_DIR / filename, registry=registry)
    header = okf.records[0]
    assert header.section is not None
    assert header.index == 0


def test_carton_header_fields(registry):
    okf = parse_okfile(DATA_DIR / "CartonLabel.OK", registry=registry)
    h = okf.records[0]
    assert h.get("chain") == "01"
    assert h.get("format") == "1"
    assert h.get("picklist_pre") == "C:"


def test_styleheader_sections_and_markers(registry):
    okf = parse_okfile(DATA_DIR / "StyleHeader.OK", registry=registry)
    secs = okf.sections()
    assert {"Lane", "Size"} <= set(secs)
    # '#' lines -> Lane (8-char lane1), '&' lines -> Size (size + qty)
    assert secs["Lane"][0].get("lane1") == "RCD053  "
    size_rec = secs["Size"][1]
    assert size_rec.get("size") == "XL    "
    assert size_rec.get("qty") == "00002"


def test_edit_preserves_width_and_roundtrips(registry):
    """Editing a field changes only its span; reverting restores the bytes."""
    path = DATA_DIR / "CartonLabel.OK"
    original = path.read_bytes()
    okf = parse_okfile(path, registry=registry)
    header = okf.records[0]

    old_len = len(header.raw)
    old_chain = header.get("chain")
    header.set("chain", "07")
    assert header.get("chain") == "07"
    assert len(header.raw) == old_len, "record length must not change on edit"
    assert okf.to_bytes() != original, "edited file should differ"

    # Revert and confirm byte-exact restoration.
    header.set("chain", old_chain)
    assert okf.to_bytes() == original, "reverting the edit must restore bytes"


def test_detection_drives_parse(registry):
    """parse_okfile with no explicit layout uses detection + registry."""
    okf = parse_okfile(DATA_DIR / "Preticket.OK", registry=registry)
    assert okf.layout.name == "Preticket"


def test_eu_delimited_header_fields(registry):
    """The EU (pipe-delimited) preticket parses its header tokens by name."""
    okf = parse_okfile(DATA_DIR / "EUPreticket.OK", registry=registry)
    assert okf.layout.name == "EUPreticket"
    assert okf.layout.delimited is True
    h = okf.records[0]
    assert h.get("indicator") == "P"
    assert h.get("chain") == "05"
    assert h.get("format") == "A"
    assert h.get("po") == "10021888"
    assert h.get("zone") == "10"
    # A detail line maps its delimited tokens too.
    detail = okf.records[1]
    assert detail.get("style") == "750440"
    assert detail.get("size") == "XL    "


def test_eu_delimited_edit_preserves_delimiters_and_roundtrips(registry):
    """Editing a delimited field keeps token width, pipes, terminator and BOM."""
    path = DATA_DIR / "EUPreticket.OK"
    original = path.read_bytes()
    okf = parse_okfile(path, registry=registry)
    h = okf.records[0]

    old_len = len(h.raw)
    old_po = h.get("po")
    h.set("po", "99999999")
    assert h.get("po") == "99999999"
    assert len(h.raw) == old_len, "delimited edit must not change line length"
    assert okf.to_bytes() != original

    h.set("po", old_po)
    assert okf.to_bytes() == original, "reverting must restore exact bytes (incl. BOM)"
