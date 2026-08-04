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
def test_normalized_form_roundtrips_byte_identical(registry, tmp_path, filename):
    """Parsing normalizes junk away (blank lines + padding AFTER the terminator).
    The NORMALIZED form then round-trips byte-for-byte: writing the cleaned bytes
    and re-parsing needs no further cleaning and re-serializes identically. (A
    sample with no junk — DistLabels / EUPreticket — normalizes to itself, so
    this is also the classic byte-exact round-trip for already-clean files.)"""
    okf = parse_okfile(DATA_DIR / filename, registry=registry)
    clean = okf.to_bytes()
    work = tmp_path / filename
    work.write_bytes(clean)
    again = parse_okfile(work, registry=registry)
    assert again.blank_lines_removed == 0 and again.lines_space_trimmed == 0
    assert again.to_bytes() == clean, f"{filename}: normalized form not stable"


@pytest.mark.parametrize("filename", OK_FILES)
def test_normalization_only_strips_junk_never_fields(registry, filename):
    """Cleaning removes ONLY blank junk lines and spaces AFTER the terminator —
    every field's bytes (which live before the terminator) are untouched. Proven
    by rebuilding the expected result from the raw bytes with exactly that rule."""
    original = (DATA_DIR / filename).read_bytes().decode("latin-1")
    lines = original.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    expected = []
    for i, line in enumerate(lines):
        cr = "\r" if line.endswith("\r") else ""
        c = line[:-1] if cr else line
        if i != 0 and c.strip(" ") == "":          # blank junk line (not header)
            continue
        s = c.rstrip(" ")
        if s != c and s.endswith("\\"):            # pad AFTER the terminator
            c = s
        expected.append(c + cr)
    got = parse_okfile(DATA_DIR / filename, registry=registry).to_bytes().decode("latin-1")
    got_lines = got.split("\n")
    if got_lines and got_lines[-1] == "":
        got_lines = got_lines[:-1]
    assert got_lines == expected


@pytest.mark.parametrize("filename", OK_FILES)
def test_blank_lines_dropped_trailing_and_interior(registry, tmp_path, filename):
    """Blank junk lines (empty/space-only) are dropped whether TRAILING or wedged
    BETWEEN records — no sectionless '(unassigned)' rows survive — and the content
    matches the normalized base (blanks contribute nothing that survives)."""
    base_clean = parse_okfile(DATA_DIR / filename, registry=registry).to_bytes()
    lines = base_clean.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]                          # each remaining line ends with '\r'
    want = base_clean.rstrip(b"\r\n")               # compare modulo the trailing newline

    def check(raw_bytes):
        w = tmp_path / f"j_{filename}"
        w.write_bytes(raw_bytes)
        okf = parse_okfile(w, registry=registry)
        assert okf.blank_lines_removed >= 1
        assert "(unassigned)" not in okf.sections()
        assert okf.to_bytes().rstrip(b"\r\n") == want

    check(b"\n".join(lines) + b"\n   \r\n\r\n")                # trailing blank lines
    check(b"\n".join([lines[0], b"   \r"] + lines[1:]))       # interior blank after header


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
    """Editing a field changes only its span; reverting restores the bytes.
    (Compared against the normalized baseline, since opening CartonLabel trims
    its post-terminator padding.)"""
    okf = parse_okfile(DATA_DIR / "CartonLabel.OK", registry=registry)
    clean = okf.to_bytes()                       # normalized baseline
    header = okf.records[0]

    old_len = len(header.raw)
    old_chain = header.get("chain")
    header.set("chain", "07")
    assert header.get("chain") == "07"
    assert len(header.raw) == old_len, "record length must not change on edit"
    assert okf.to_bytes() != clean, "edited file should differ"

    # Revert and confirm byte-exact restoration of the normalized form.
    header.set("chain", old_chain)
    assert okf.to_bytes() == clean, "reverting the edit must restore bytes"


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
    """Editing the keytrol (unique key) on the new EU layouts round-trips exactly
    against the normalized baseline (these samples carry post-terminator padding
    that opening trims)."""
    okf = parse_okfile(DATA_DIR / filename, registry=registry)
    clean = okf.to_bytes()                       # normalized baseline
    h = okf.records[0]

    old_len, old_key = len(h.raw), h.get("keytrol")
    h.set("keytrol", "0000000000X")   # 11 chars = field width
    assert len(h.raw) == old_len, "delimited edit must not change line length"
    assert okf.to_bytes() != clean

    h.set("keytrol", old_key)
    assert okf.to_bytes() == clean, "reverting must restore exact bytes"


# --------------------------------------------------------------------------- #
# Short records — Canada (Winners / HomeSense) .OK files commonly stop at the
# last field they actually carry, so the line ends '...FREENG\' with nothing
# after it. The terminator must stay OUT of that last field, and the fields the
# line never reaches must read blank.
# --------------------------------------------------------------------------- #
def _canada_styleheader(tmp_path, item="FREENG"):
    """A real StyleHeader.OK truncated after `item`, ending at the terminator."""
    raw = (DATA_DIR / "StyleHeader.OK").read_bytes().decode("latin-1")
    lines = raw.split("\r\n")
    item_start = 1 + 170 - 1                     # header offset + item.start - 1
    lines[0] = lines[0][:item_start] + item + "\\"
    p = tmp_path / "CanadaStyleHeader.OK"
    p.write_bytes("\r\n".join(lines).encode("latin-1"))
    return p


def test_short_record_keeps_the_terminator_out_of_the_last_field(registry, tmp_path):
    """The reported bug: `item` rendered as 'FREENG\\' (terminator, and the \\r,
    bleeding into the field) because a fixed-width span was sliced straight out
    of a line that ends before the field does."""
    okf = parse_okfile(_canada_styleheader(tmp_path), registry=registry)
    h = okf.sections()["Header"][0]
    assert h.get("item") == "FREENG"
    assert "\\" not in h.get("item") and "\r" not in h.get("item")


def test_short_record_reads_missing_trailing_fields_as_blank(registry, tmp_path):
    """fact1-3 are simply not in the file, and must stay blank — never a slice
    of the line ending."""
    okf = parse_okfile(_canada_styleheader(tmp_path), registry=registry)
    h = okf.sections()["Header"][0]
    for fld in ("fact1", "fact2", "fact3"):
        assert h.get(fld) == "", f"{fld} = {h.get(fld)!r}"
    assert h.get("keytrol") == "550000"           # fields BEFORE the cut are intact
    assert h.get("size_rec") == "04"


def test_short_record_roundtrips_byte_exact(registry, tmp_path):
    p = _canada_styleheader(tmp_path)
    okf = parse_okfile(p, registry=registry)
    assert okf.to_bytes() == p.read_bytes()


def test_short_record_edit_keeps_the_line_length_and_terminator(registry, tmp_path):
    """Editing the truncated field writes within its real room, so the record
    terminator is not pushed and the line keeps its length."""
    p = _canada_styleheader(tmp_path)
    okf = parse_okfile(p, registry=registry)
    h = okf.sections()["Header"][0]
    before = len(h.raw)
    h.set("item", "FRECAN", literal=True)
    assert h.get("item") == "FRECAN"
    assert len(h.raw) == before
    assert h.raw.rstrip("\r").endswith("FRECAN\\")


def test_short_record_refuses_a_value_the_line_has_no_room_for(registry, tmp_path):
    """`item` is declared 20 wide, but this record only reaches 6 of it —
    writing more would move the terminator, so it is refused, not truncated."""
    okf = parse_okfile(_canada_styleheader(tmp_path), registry=registry)
    h = okf.sections()["Header"][0]
    with pytest.raises(ValueError):
        h.set("item", "FRENCHENGLISH", literal=True)
    assert h.get("item") == "FREENG", "a refused write must change nothing"


def test_full_length_records_are_unaffected(registry, tmp_path):
    """The clamp is a no-op wherever the record reaches its declared length —
    the reference StyleHeader still reads its trailing fields in full."""
    okf = parse_okfile(DATA_DIR / "StyleHeader.OK", registry=registry)
    h = okf.sections()["Header"][0]
    assert h.get("item") == "ITEMITEMITEMITEMITEM"
    assert h.get("fact3") == "FACT3FACT3FACT3FACT3"
    assert len(h.get("fact3")) == 20
