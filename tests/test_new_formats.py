"""The four ticket formats added at the user's request, end to end.

    Homegoods StyleHeaders        Q  Two Part Tag
                                  X  Dumbbell Gum Label
    Winners/HomeSense StyleHeaders W  Purple Tuff Tag
                                  X  Purple Rat Tail

Valid on BOTH engines' Style Header layouts. `test_config.py` covers the labels
and the option lists; this file covers what happens when one is actually
WRITTEN — through the single editor, Bulk Edit and Volume Generate — because a
format letter is not inert. On some layouts `format` IS the detection signature
(DistLabels keys off '7'/'9' at raw pos 4), so the thing worth proving is that a
file carrying a new format still opens as the layout it was.

Config-only change, so these are guards against the code paths that could still
get it wrong, not tests of the YAML.
"""
import json
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry
from okgen.okfile import parse_okfile

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIX = Path(__file__).resolve().parent / "fixtures" / "calgary"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"

# (source file, layout, the formats that layout's chain accepts)
# StyleHeader.OK is chain 03 (Homegoods); the Calgary style-header fixtures
# are 04/06 (Winners/HomeSense).
CASES = [
    ("StyleHeader.OK", DATA_DIR / "StyleHeader.OK", "StyleHeader", ["Q", "X"]),
    ("styleheader_fmtB.json", FIX / "styleheader_fmtB.json",
     "CalgaryStyleHeader", ["W", "X"]),
]

pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="no calgary fixtures")


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _format_of(path, registry):
    p = Path(path)
    if p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))["data"]["header"]["format"]
    return parse_okfile(p, registry=registry).records[0].get("format")


def _copy(tmp_path, src, name):
    p = tmp_path / name
    shutil.copy2(src, p)
    return p


@pytest.mark.parametrize("name,src,layout,formats", CASES)
def test_a_new_format_saves_and_the_file_still_opens(tmp_path, registry, config,
                                                     name, src, layout, formats):
    """The load-bearing one: `format` is a detection signature on other layouts,
    so a new letter must not make the file unopenable or flip it to another
    layout — which is the D12 failure this is checked against."""
    p = _copy(tmp_path, src, name)
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]

    for code in formats:
        service.apply_edits(p, [{"section_index": 0, "record_index": ri,
                                 "field": "format", "value": code}],
                            registry, config=config, backup=False)

        assert _format_of(p, registry) == code
        assert service.parse_file_view(p, registry, config)["layout"] == layout


@pytest.mark.parametrize("name,src,layout,formats", CASES)
def test_bulk_edit_writes_a_new_format(tmp_path, registry, config,
                                       name, src, layout, formats):
    p = _copy(tmp_path, src, name)

    res = service.bulk_apply([str(p)], layout, "format", formats[0],
                             registry, config, backup=False)["results"][0]

    assert res["status"] == "changed", res
    assert _format_of(p, registry) == formats[0]
    assert service.parse_file_view(p, registry, config)["layout"] == layout


@pytest.mark.parametrize("name,src,layout,formats", CASES)
def test_volume_generate_writes_new_formats(tmp_path, registry, config,
                                            name, src, layout, formats):
    p = _copy(tmp_path, src, name)

    res = service.generate_apply(
        [str(p)],
        {"count": 6, "dest": str(tmp_path / "out"),
         "header_fields": [{"name": "format", "values": ", ".join(formats)}]},
        registry, config)

    written = sorted(Path(res["folder"]).iterdir())
    assert written, "generate produced no files"
    seen = {_format_of(f, registry) for f in written}
    assert seen <= set(formats), seen
    for f in written:                       # every generated file still opens
        assert service.parse_file_view(f, registry, config)["layout"] == layout


@pytest.mark.parametrize("name,src,layout,formats", CASES)
def test_a_renamed_file_carries_the_format_LETTER(tmp_path, registry, config,
                                                  name, src, layout, formats):
    """Filenames carry the letter, not the wording (the user's call) — so a new
    format has to reach the name as `X`, never as `Purple_Rat_Tail`."""
    code = formats[-1]
    p = _copy(tmp_path, src, name)
    view = service.parse_file_view(p, registry, config)
    ri = view["sections"][0]["records"][0]["index"]
    service.apply_edits(p, [{"section_index": 0, "record_index": ri,
                             "field": "format", "value": code}],
                        registry, config=config, backup=False)

    parts = [{"type": "token", "name": "format"}, {"type": "token", "name": "key"}]
    pv = service.bulk_rename_preview([str(p)], parts, "_", registry, config)

    new_name = pv["results"][0]["new"]
    assert new_name.startswith(code + "_"), new_name
    assert "_" not in new_name[:len(code)], "the letter must not be a wording"


def test_an_ok_and_a_json_file_agree_on_the_new_formats(registry, config):
    """One list serves both engines (D41) — a test that asserts they are the
    SAME map, not merely that each has the new letters, so the two cannot drift
    apart later."""
    prod = Config.load(Path(__file__).resolve().parents[1] / "config")
    if not (Path(__file__).resolve().parents[1] / "config" / "display.yaml").is_file():
        pytest.skip("shipped config not present")

    for chain in ("03", "04", "06"):
        ok = prod.options("format", chain=chain, layout="StyleHeader")
        js = prod.options("format", chain=chain, layout="CalgaryStyleHeader")
        assert ok == js != {}, chain


# --------------------------------------------------------------------------- #
# Rename uses the format LETTER, not the label wording
# --------------------------------------------------------------------------- #
SHIPPED_CONFIG = Path(__file__).resolve().parents[1] / "config"
shipped = pytest.mark.skipif(not (SHIPPED_CONFIG / "rename_tokens.yaml").is_file(),
                             reason="shipped config not present")


@pytest.fixture(scope="module")
def prod():
    return Config.load(SHIPPED_CONFIG)


@shipped
def test_the_palette_offers_the_letter_and_not_the_wording(prod):
    """`format_label` resolves to "Purple Rat Tail", which lands in a filename
    as `Purple_Rat_Tail` — long, space-sanitised, and not what the naming
    convention expects. It is withdrawn from the palette so a rename cannot be
    built from it by accident; the raw `format` token stays."""
    groups = prod.rename_token_groups()

    assert "format_label" not in groups["derived"]
    assert "format" in groups["header_fields"]


@shipped
def test_no_shipped_preset_uses_the_wording(prod):
    """A preset is the path most users take, so the palette alone is not
    enough — none may name the label token."""
    def names(parts):
        return [p if isinstance(p, str) else (p or {}).get("name")
                for p in (parts or [])]

    for preset in prod.rename_presets():
        used = names(preset.get("parts"))
        assert "format_label" not in used, preset.get("name")
        assert "format" in used or not any(n and "format" in str(n) for n in used), \
            preset.get("name")


@shipped
def test_an_existing_preset_naming_the_wording_still_resolves(tmp_path, registry,
                                                              prod):
    """Withdrawing a token from the PALETTE must not make it resolve to nothing.
    That is exactly D43's failure — a preset referenced `ticket_format`, which
    the palette never offered, and its segment came out silently blank on every
    file. Anyone with a saved preset naming `format_label` keeps working."""
    p = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
    parts = [{"type": "token", "name": "format_label"},
             {"type": "token", "name": "key"}]

    pv = service.bulk_rename_preview([str(p)], parts, "_", registry, prod)

    new_name = pv["results"][0]["new"]
    assert new_name == "Regular_Tag_550000.OK", new_name
