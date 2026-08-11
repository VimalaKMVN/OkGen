"""Short rename tokens: a serial, the brand BADGE, and a layout code.

User-reported: "overall the filenames are too long". Measured against the
reference files, the shipped presets produced 40–86 characters, e.g.

    Homegoods_StyleHeader_FMT_A_KEY_550000P1A_T_2_DEPT_78_CP_000000_CU_N_RP_002099.OK

Three new derived tokens shorten that without losing anything a reader needs:

    sl            a per-batch serial — 01, 02, 03 …
    brand_short   the tree BADGE text (HG, TJM, WN, EU, HS) from chains.yaml
    layout_short  a short layout code (SH / PT / CL / DL) from layout_codes.yaml

`brand` and `layout` are untouched and still give the full name, so nothing
that already uses them changes meaning.

Two decisions worth keeping straight, both the user's:

* The EU layouts share the NA codes — `EUStyleHeader` is `SH`, not `ESH` —
  because the name already carries the `EU` brand badge.
* The serial is PADDED. Unpadded it sorts 1, 10, 11, 2 in Explorer and in the
  NiceLabel hot folder, which are the two places these names are read. The
  width is the batch's own (7 files stay 01..07, 150 become 001..150), so a
  name is never longer than the batch requires, with a floor of 2.
"""
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def _copies(tmp_path, n, src="StyleHeader.OK", folder="f"):
    d = tmp_path / folder
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        p = d / f"src{i:04d}.OK"
        shutil.copy(DATA_DIR / src, p)
        out.append(str(p))
    return out


def _names(paths, parts, registry, config, separator="_"):
    res = service.bulk_rename_preview(paths, parts, separator, registry, config)
    return [r.get("new") for r in res["results"]]


# ------------------------------------------------------------ layout_short

@pytest.mark.parametrize("layout,code", [
    ("StyleHeader", "SH"), ("Preticket", "PT"), ("DistLabels", "DL"),
    ("EUStyleHeader", "SH"), ("EUPreticket", "PT"), ("EUCartonLabel", "CL"),
    ("CalgaryStyleHeader", "SH"),
])
def test_layout_code_resolves(config, layout, code):
    assert config.layout_code(layout) == code


def test_eu_layouts_share_the_na_codes(config):
    """The user's call: the brand badge already says EU, so `ESH` would only
    repeat it. If that ever changes it is a config edit, not code."""
    assert config.layout_code("EUStyleHeader") == config.layout_code("StyleHeader")
    assert config.layout_code("EUPreticket") == config.layout_code("Preticket")


def test_an_unlisted_layout_falls_back_to_its_full_name(config):
    """Never a blank. A token that resolves to nothing silently drops a segment
    out of the filename — the D43 failure — so an unmapped layout must still
    produce something, and the full name is the honest something.
    (`CartonLabel` is deliberately absent from the test fixture's map.)"""
    assert config.layout_code("CartonLabel") == "CartonLabel"
    assert config.layout_code("SomethingNew") == "SomethingNew"


def test_layout_code_of_nothing_is_empty(config):
    assert config.layout_code("") == ""
    assert config.layout_code(None) == ""


# ------------------------------------------------------------ brand_short

def test_brand_short_is_the_tree_badge_text(tmp_path, registry, config):
    """One source for both, so a filename and the badge beside it can never
    disagree about which brand a file belongs to."""
    paths = _copies(tmp_path, 1)
    got = _names(paths, [{"name": "brand_short"}], registry, config)
    assert got == [config.chain("03").short + ".OK"] == ["HG.OK"]


def test_brand_is_untouched(tmp_path, registry, config):
    got = _names(tmp_path and _copies(tmp_path, 1), [{"name": "brand"}],
                 registry, config)
    assert got == ["Homegoods.OK"]


# ------------------------------------------------------------------- sl

def test_sl_counts_from_one_and_pads_to_two_by_default(tmp_path, registry, config):
    got = _names(_copies(tmp_path, 3), [{"name": "sl"}], registry, config)
    assert got == ["01.OK", "02.OK", "03.OK"]


def test_sl_widens_to_the_batch(tmp_path, registry, config):
    """The whole reason it is padded: 1, 10, 11, 2 is how an unpadded run sorts
    in Explorer and the hot folder."""
    got = _names(_copies(tmp_path, 12), [{"name": "sl"}], registry, config)
    assert got[0] == "01.OK" and got[-1] == "12.OK"
    assert all(len(n) == len("01.OK") for n in got)


def test_sl_widens_again_past_a_hundred(tmp_path, registry, config):
    got = _names(_copies(tmp_path, 105), [{"name": "sl"}], registry, config)
    assert got[0] == "001.OK" and got[-1] == "105.OK"


def test_sl_is_never_longer_than_the_batch_requires(tmp_path, registry, config):
    """A fixed width would make every small batch carry 001; the point of
    sizing to the batch is that the name stays as short as it can."""
    assert _names(_copies(tmp_path, 9, folder="a"), [{"name": "sl"}],
                  registry, config)[0] == "01.OK"


def test_sl_restarts_per_folder(tmp_path, registry, config):
    """Two folders are numbered independently — the counter already restarted
    per folder, and the PAD is sized per folder for the same reason."""
    a = _copies(tmp_path, 2, folder="a")
    b = _copies(tmp_path, 2, folder="b")
    got = _names(a + b, [{"name": "sl"}], registry, config)
    assert sorted(got) == ["01.OK", "01.OK", "02.OK", "02.OK"]


def test_each_folder_pads_to_its_own_size(tmp_path, registry, config):
    small = _copies(tmp_path, 2, folder="small")
    big = _copies(tmp_path, 30, folder="big")
    res = service.bulk_rename_preview(small + big, [{"name": "sl"}], "_",
                                      registry, config)["results"]
    by_folder = {}
    for r in res:
        by_folder.setdefault(Path(r["path"]).parent.name, []).append(r["new"])
    assert by_folder["small"] == ["01.OK", "02.OK"]
    assert by_folder["big"][0] == "01.OK" and by_folder["big"][-1] == "30.OK"


def test_seq_is_unchanged(tmp_path, registry, config):
    """`sl` is a NEW token, not a redefinition: `seq` still stamps four digits,
    which is what Volume Generate puts on the files it creates."""
    got = _names(_copies(tmp_path, 2), [{"name": "seq"}], registry, config)
    assert got == ["0001.OK", "0002.OK"]


# ------------------------------------------------ the whole name, measured

def test_the_shipped_style_header_name_is_shorter(tmp_path, registry, config):
    """The report was about LENGTH, so assert length, not just the parts."""
    parts = [{"name": "sl"}, {"name": "brand_short"}, {"name": "layout_short"},
             {"name": "FMT"}, {"type": "glue"}, {"name": "format"},
             {"name": "keytrol"}]
    new = _names(_copies(tmp_path, 1), parts, registry, config)[0]
    assert new == "01_HG_SH_FMTA_550000.OK"   # keytrol alone; the shipped
                                              # preset glues `suffix` after it

    old_parts = [{"name": "brand"}, {"name": "layout"}, {"name": "FMT"},
                 {"name": "format"}, {"name": "keytrol"}]
    old = _names(_copies(tmp_path, 1, folder="old"), old_parts, registry, config)[0]
    assert len(new) < len(old)


def test_fmt_glues_to_its_value(tmp_path, registry, config):
    """`FMT_A` -> `FMTA`: the label and its value are one thing, and the
    separator between them was pure length."""
    glued = _names(_copies(tmp_path, 1), [{"name": "FMT"}, {"type": "glue"},
                                          {"name": "format"}], registry, config)
    assert glued == ["FMTA.OK"]
    apart = _names(_copies(tmp_path, 1, folder="b"),
                   [{"name": "FMT"}, {"name": "format"}], registry, config)
    assert apart == ["FMT_A.OK"]


def test_every_shipped_preset_starts_with_the_serial():
    """The ask was for the serial FIRST, on every preset — easy to apply to the
    one you are looking at and miss on the other six."""
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config")
    presets = shipped.rename_presets()
    assert presets, "no presets shipped"
    for p in presets:
        first = p["parts"][0]
        name = first if isinstance(first, str) else (
            first.get("name") or first.get("value"))
        assert name == "sl", f"{p['name']} starts with {name!r}, not the serial"


def test_no_shipped_preset_still_uses_the_long_forms():
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config")
    for p in shipped.rename_presets():
        names = [x if isinstance(x, str) else (x.get("name") or x.get("value"))
                 for x in p["parts"]]
        assert "brand" not in names, f"{p['name']} still uses the full brand"
        assert "layout" not in names, f"{p['name']} still uses the full layout"


def test_the_shipped_cu_label_reads_up():
    """Went `CU` -> `CUP` -> `UP` across two rounds of the user's review. It is
    pinned rather than left to the config alone because the label is GLUED to
    its value now (`UPN`), so a change here silently changes every filename."""
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config")
    assert shipped.rename_token_groups()["custom"]["CU"] == "UP"


def test_key_and_dept_labels_are_single_letters():
    """`KEY` and `DEPT` insert `K` and `D`. The token NAMES stay long because
    they are what a preset refers to; only the inserted TEXT is short, which is
    why reading the names alone suggests nothing changed here."""
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config")
    custom = shipped.rename_token_groups()["custom"]
    assert custom["KEY"] == "K"
    assert custom["DEPT"] == "D"


# ------------------------------------------------ labels glued to their value

def test_a_label_glues_to_its_own_value(tmp_path, registry, config):
    """`K_550000` -> `K550000`. The label and the value are one thing; the
    separator between them was pure length."""
    parts = [{"name": "FMT"}, {"type": "glue"}, {"name": "format"}]
    assert _names(_copies(tmp_path, 1), parts, registry, config) == ["FMTA.OK"]


def test_a_label_whose_value_is_EMPTY_is_dropped_with_it(tmp_path, registry, config):
    """A label exists only to introduce a value. Glued to nothing it said
    nothing — and worse, it stuck to whatever came next: an EUStyleHeader with
    no compare-at price produced `CPRP599`, which reads as one token made of
    two labels. Both halves of that are asserted here."""
    parts = [{"name": "FMT"}, {"type": "glue"}, {"name": "nosuchfield"},
             {"name": "CP"}, {"type": "glue"}, {"name": "keytrol"}]
    got = _names(_copies(tmp_path, 1), parts, registry, config)
    assert got == ["CP550000.OK"]        # the FMT run vanished entirely...
    assert "FMT" not in got[0]           # ...label and all


def test_an_empty_value_does_not_glue_the_NEXT_part(tmp_path, registry, config):
    """The mechanism behind `CPRP599`: skipping an empty value left the glue
    flag set, so the following part joined with no separator and two unrelated
    segments ran together."""
    parts = [{"name": "keytrol"}, {"type": "glue"}, {"name": "nosuchfield"},
             {"name": "dept"}]
    got = _names(_copies(tmp_path, 1), parts, registry, config)
    assert got == ["550000_78.OK"]       # separated, not "55000078"


def test_a_run_keeps_going_when_only_SOME_values_are_empty(tmp_path, registry, config):
    """Dropping the whole run on ANY empty part would be the opposite error:
    a StyleHeader key is keytrol + suffix, and a missing suffix must not take
    the key with it."""
    parts = [{"name": "KEY"}, {"type": "glue"}, {"name": "keytrol"},
             {"type": "glue"}, {"name": "nosuchfield"}]
    assert _names(_copies(tmp_path, 1), parts, registry, config) == ["K550000.OK"]


def test_a_pure_literal_run_survives(tmp_path, registry, config):
    """A Text part or a lone custom token is the user asking for that text —
    it has no value slot to be empty, so the drop rule must not reach it."""
    parts = [{"type": "text", "value": "PROD"}, {"name": "keytrol"}]
    assert _names(_copies(tmp_path, 1), parts, registry, config) == ["PROD_550000.OK"]


def test_every_shipped_preset_glues_each_label_to_its_value():
    """The ask was for K, T, D and UP — easy to apply to the preset you are
    looking at and miss on the other nine."""
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config")
    labels = set(shipped.rename_token_groups()["custom"])
    for p in shipped.rename_presets():
        names = [x if isinstance(x, str) else (x.get("name") or x.get("value")
                 or ("no_delim" if x.get("type") == "glue" else None))
                 for x in p["parts"]]
        for i, n in enumerate(names[:-1]):
            if n in labels:
                assert names[i + 1] in ("no_delim", None), (
                    f"{p['name']}: label {n!r} is not glued to its value")


def test_the_key_pair_comes_after_the_type_pair():
    """The user's ordering: format, then WHAT KIND of ticket, then WHICH order."""
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config")
    checked = 0
    for p in shipped.rename_presets():
        names = [x if isinstance(x, str) else (x.get("name") or x.get("value"))
                 for x in p["parts"]]
        if "T" in names and "KEY" in names:
            assert names.index("T") < names.index("KEY"), p["name"]
            checked += 1
    assert checked >= 4, "expected several presets to carry both pairs"


# ------------------------------------------- section-qualified field tokens

# On a Calgary StyleHeader NINE field names appear in two sections, and the
# token resolver fills header-first with setdefault — so the header wins and
# the detail one is unreachable by name. `type` is the one that matters: the
# header's is the DOCUMENT discriminator (`styleHeaders`) while the ticket type
# digit lives on the detail row, so a bare `type` put a word where a digit
# belonged. `.OK` layouts have NO such collision, which is why it only bit JSON.
CALGARY = Path(__file__).resolve().parent / "fixtures" / "calgary"

pytestmark_json = pytest.mark.skipif(not CALGARY.is_dir(),
                                     reason="no calgary fixtures")


def _json_copy(tmp_path, name="styleheader_fmtS.json"):
    d = tmp_path / "j"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    shutil.copy(CALGARY / name, p)
    return [str(p)]


@pytestmark_json
def test_bare_type_still_takes_the_header_value(tmp_path, registry, config):
    """Unchanged on purpose: `type` means the header field everywhere else in
    OkGen, and a preset that already says `type` must keep meaning that."""
    got = _names(_json_copy(tmp_path), [{"name": "type"}], registry, config)
    assert got == ["styleHeaders.json"]


@pytestmark_json
def test_a_qualified_token_reads_its_own_section(tmp_path, registry, config):
    got = _names(_json_copy(tmp_path), [{"name": "Details.type"}], registry, config)
    assert got == ["2.json"]


@pytestmark_json
def test_the_header_can_be_named_explicitly_too(tmp_path, registry, config):
    got = _names(_json_copy(tmp_path), [{"name": "Header.type"}], registry, config)
    assert got == ["styleHeaders.json"]


@pytestmark_json
def test_qualified_tokens_are_OFFERED_for_shadowed_fields(tmp_path, registry, config):
    """A preset could always name one (an unoffered token still resolves, D43),
    but the builder could not — so the palette has to carry them."""
    scope = service.rename_scope(_json_copy(tmp_path), registry, config)
    fields = scope["palette"]["header_fields"]
    assert "Details.type" in fields and "Header.type" in fields


@pytestmark_json
def test_only_SHADOWED_fields_are_qualified(tmp_path, registry, config):
    """Qualifying all 90-odd fields would double the palette to say nothing."""
    scope = service.rename_scope(_json_copy(tmp_path), registry, config)
    qualified = [f for f in scope["palette"]["header_fields"] if "." in f]
    assert qualified, "expected some"
    layout = registry["CalgaryStyleHeader"]
    where = {}
    for sec in layout.sections:
        for f in sec.fields:
            where.setdefault(f.name, set()).add(sec.name)
    for q in qualified:
        assert len(where[q.split(".", 1)[1]]) > 1, f"{q} is not shadowed"


def test_an_OK_layout_gets_no_qualified_tokens(tmp_path, registry, config):
    """The `.OK` layouts have zero shadowed names, so the palette must not grow
    for them — this is a JSON-shaped problem and the fix stays proportional."""
    scope = service.rename_scope(_copies(tmp_path, 1), registry, config)
    assert [f for f in scope["palette"]["header_fields"] if "." in f] == []


@pytestmark_json
def test_the_calgary_styleheader_preset_uses_the_detail_type():
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config")
    p = [x for x in shipped.rename_presets()
         if x["name"] == "Calgary StyleHeader (JSON)"][0]
    names = [x if isinstance(x, str) else (x.get("name") or x.get("value"))
             for x in p["parts"]]
    assert "Details.type" in names
    assert "type" not in names, "the bare token would write 'styleHeaders'"


def test_the_two_other_calgary_presets_carry_no_type():
    """Not an oversight: CalgaryDistLabel and CalgaryCartonLabel have only
    Header and Stores sections, so there is no ticket type to name."""
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config")
    for nm in ("Calgary DistLabel (JSON)", "Calgary CartonLabel (JSON)"):
        p = [x for x in shipped.rename_presets() if x["name"] == nm][0]
        names = [x if isinstance(x, str) else (x.get("name") or x.get("value"))
                 for x in p["parts"]]
        assert not any(n and n.endswith("type") for n in names)


# -------------------------------------------- the key is a TOKEN, not a field

# Naming the key FIELD (`keytrol`, `po`, `picklist_id`) works only on the layout
# that happens to use it. Applied to any other — a folder holding mixed layouts
# renamed with one preset — the field is empty, so the whole `K…` run is dropped
# (correctly, per D69) and the filename silently loses the value identifying the
# order. Measured across every preset x layout pair: 24 of 49 lost it. The `key`
# derived token resolves each FILE'S OWN unique field, which takes that to 0
# while leaving every correct-preset name byte-identical.
KEY_FIELDS = {"keytrol", "po", "picklist_id", "pickListId", "headerASNid"}


def _shipped():
    return Config.load(Path(__file__).resolve().parents[1] / "config")


def test_no_preset_names_a_key_FIELD_directly():
    for p in _shipped().rename_presets():
        names = [x if isinstance(x, str) else (x.get("name") or x.get("value"))
                 for x in p["parts"]]
        clash = KEY_FIELDS.intersection(names)
        assert not clash, (
            f"{p['name']} names {clash} directly; use the `key` token so the "
            f"preset still works on a layout keyed by a different field")


def test_every_preset_carries_the_key_token():
    for p in _shipped().rename_presets():
        names = [x if isinstance(x, str) else (x.get("name") or x.get("value"))
                 for x in p["parts"]]
        assert "key" in names, f"{p['name']} has no key at all"


@pytest.mark.parametrize("sample,layout", [
    ("StyleHeader.OK", "StyleHeader"), ("Preticket.OK", "Preticket"),
    ("CartonLabel.OK", "CartonLabel"), ("DistLabels.OK", "DistLabels"),
    ("EUPreticket.OK", "EUPreticket"), ("EUStyleHeader.OK", "EUStyleHeader"),
    ("EUCartonLabel.OK", "EUCartonLabel"),
])
def test_any_OK_preset_names_the_key_of_any_OK_file(tmp_path, registry, config,
                                                    sample, layout):
    """The mixed-folder case: one preset over a selection of several layouts.
    Whichever preset is used, every file must still carry its own key."""
    shipped = _shipped()
    ok_presets = [p for p in shipped.rename_presets()
                  if not p["name"].startswith("Calgary")]
    assert ok_presets
    for p in ok_presets:
        d = tmp_path / f"{layout}_{ok_presets.index(p)}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / sample
        shutil.copy(DATA_DIR / sample, f)
        new = service.bulk_rename_preview([str(f)], p["parts"], "_",
                                          registry, shipped)["results"][0]["new"]
        assert "_K" in new, (
            f"{p['name']} applied to a {layout} file produced {new!r} — no key")
