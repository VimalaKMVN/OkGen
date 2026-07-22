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
            "EUPreticket.OK", "EUStyleHeader.OK", "EUCartonLabel.OK"]

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


def _drop_section(okf, section_name):
    """Rebuild an OkFile with every record of one section removed."""
    okf.records = [
        r for r in okf.records
        if not (r.section and r.section.name == section_name)
    ]
    return okf


@pytest.mark.parametrize(
    "filename,layout_name,empty_sec,kept_sec",
    [
        # Fixed-width StyleHeader: drop the '#'-marked Lane records; the
        # '&'-marked Size records must STAY in Size (not slide up into Lane).
        ("StyleHeader.OK", "StyleHeader", "Lane", "Size"),
        # Delimited EU StyleHeader: drop the '&'-marked Lane records; the
        # '#'-marked Detail records must STAY in Detail.
        ("EUStyleHeader.OK", "EUStyleHeader", "Lane", "Detail"),
    ],
)
def test_empty_section_does_not_shift_later_records(
    registry, tmp_path, filename, layout_name, empty_sec, kept_sec
):
    layout = registry.get(layout_name)
    okf = parse_okfile(DATA_DIR / filename, layout=layout, registry=registry)
    kept_before = [r.raw for r in okf.sections()[kept_sec]]
    assert kept_before, f"fixture must have {kept_sec} records to be meaningful"

    _drop_section(okf, empty_sec)
    out = tmp_path / filename
    okf.save(out)

    reparsed = parse_okfile(out, layout=layout, registry=registry)
    secs = reparsed.sections()
    # The emptied section is still present (shown as "None"), not vanished.
    assert empty_sec in secs and secs[empty_sec] == []
    # The later section keeps ITS records — no misassignment into the empty one.
    assert [r.raw for r in secs[kept_sec]] == kept_before


def test_empty_sections_appear_in_canonical_order(registry):
    """Every layout section is a key in sections() even with zero records."""
    layout = registry.get("StyleHeader")
    okf = parse_okfile(DATA_DIR / "StyleHeader.OK", layout=layout, registry=registry)
    _drop_section(okf, "Lane")
    _drop_section(okf, "Size")
    keys = list(okf.sections())
    assert keys == [s.name for s in layout.sections]  # header, Lane, Size — all present
    assert okf.sections()["Lane"] == []
    assert okf.sections()["Size"] == []


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


def test_eu_styleheader_sections_and_markers(registry):
    """EUStyleHeader (format D) splits its '|'-led records into Header/Lane/Detail."""
    okf = parse_okfile(DATA_DIR / "EUStyleHeader.OK", registry=registry)
    assert okf.layout.name == "EUStyleHeader"
    assert okf.layout.delimited is True
    secs = okf.sections()
    assert {"Header", "Lane", "Detail"} == set(secs)
    h = okf.records[0]
    assert h.get("process") == "D"
    assert h.get("chain") == "05"
    assert h.get("keytrol") == "126539Q    "
    # '&' record -> Lane, '#' records -> Detail (marker-routed).
    assert secs["Lane"][0].get("lane_location") == "STM 201    "
    assert len(secs["Detail"]) == 10
    assert secs["Detail"][0].get("store") == "3110601"


def test_eu_cartonlabel_header_and_detail(registry):
    """EUCartonLabel (format H) parses header tokens and '#' detail records."""
    okf = parse_okfile(DATA_DIR / "EUCartonLabel.OK", registry=registry)
    assert okf.layout.name == "EUCartonLabel"
    secs = okf.sections()
    assert {"Header", "Detail"} == set(secs)
    h = okf.records[0]
    assert h.get("process") == "H"
    assert h.get("chain") == "05"
    assert h.get("c_distro") == "C:1234567  "
    assert len(secs["Detail"]) == 4


@pytest.mark.parametrize("filename", ["EUStyleHeader.OK", "EUCartonLabel.OK"])
def test_eu_delimited_edit_roundtrips(registry, filename):
    """Editing the keytrol (unique key) on the new EU layouts round-trips exactly."""
    path = DATA_DIR / filename
    original = path.read_bytes()
    okf = parse_okfile(path, registry=registry)
    h = okf.records[0]

    old_len, old_key = len(h.raw), h.get("keytrol")
    h.set("keytrol", "0000000000X")   # 11 chars = field width
    assert len(h.raw) == old_len, "delimited edit must not change line length"
    assert okf.to_bytes() != original

    h.set("keytrol", old_key)
    assert okf.to_bytes() == original, "reverting must restore exact bytes"
