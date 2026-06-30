"""Tests for the backend service + FastAPI endpoints."""

import os
import shutil
from pathlib import Path

import pytest

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

DATA_DIR = Path(
    os.environ.get(
        "OKGEN_DATA_DIR",
        str(Path(__file__).resolve().parents[1] / "data" / "OkFileDefinitions"),
    )
)

pytestmark = pytest.mark.skipif(
    not DATA_DIR.is_dir(), reason=f"sample data dir not present: {DATA_DIR}"
)


@pytest.fixture(scope="module")
def registry():
    return LayoutRegistry.from_dir(DATA_DIR)


FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"


@pytest.fixture(scope="module")
def config():
    return Config.load(FIXTURE_CONFIG)


def test_build_tree_only_ok_files(config):
    tree = service.build_tree(DATA_DIR, config)
    assert tree["type"] == "folder"
    files = [c for c in tree["children"] if c["type"] == "file"]
    assert files, "expected .OK files in tree"
    assert all(f["name"].lower().endswith(".ok") for f in files)
    # No .xlsx leaked into the tree.
    assert not any(f["name"].lower().endswith(".xlsx") for f in files)
    # StyleHeader file carries chain 03 -> Homegoods.
    style = next(f for f in files if f["name"] == "StyleHeader.OK")
    assert style["chain"] == "03"
    assert style["chain_info"]["name"] == "Homegoods"
    assert style["layout"] == "StyleHeader"


def test_duplicate_key_flag(tmp_path, registry, config):
    # Two StyleHeader files share keytrol -> both flagged duplicate.
    shutil.copy2(DATA_DIR / "StyleHeader.OK", tmp_path / "a.OK")
    shutil.copy2(DATA_DIR / "StyleHeader.OK", tmp_path / "b.OK")
    shutil.copy2(DATA_DIR / "CartonLabel.OK", tmp_path / "c.OK")  # different layout, no clash
    tree = service.build_tree(tmp_path, config, registry)
    by = {c["name"]: c for c in tree["children"]}
    assert by["a.OK"]["key_field"] == "keytrol"
    assert by["a.OK"]["duplicate"] and by["b.OK"]["duplicate"]
    assert by["c.OK"]["duplicate"] is False


def test_paste_auto_uniquifies_key(tmp_path, registry, config):
    src = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src)
    dst = tmp_path / "dst"; dst.mkdir()
    shutil.copy2(DATA_DIR / "StyleHeader.OK", dst / "existing.OK")  # already has keytrol 550000

    res = service.copy_files([str(src)], dst, registry, config)
    assert res["rekeyed"], "pasted file colliding on keytrol should be re-keyed"
    # The destination now has two distinct keytrol values.
    tree = service.build_tree(dst, config, registry)
    keys = sorted(c["key_value"] for c in tree["children"] if c["type"] == "file")
    assert len(set(keys)) == 2
    assert not any(c.get("duplicate") for c in tree["children"] if c["type"] == "file")


def test_make_unique_in_folder(tmp_path, registry, config):
    for n in ("a.OK", "b.OK", "c.OK"):
        shutil.copy2(DATA_DIR / "StyleHeader.OK", tmp_path / n)  # all keytrol 550000
    res = service.make_unique_in_folder(tmp_path, registry, config, backup=False)
    assert len(res["rekeyed"]) == 2          # first kept, two re-keyed
    tree = service.build_tree(tmp_path, config, registry)
    keys = [c["key_value"] for c in tree["children"] if c["type"] == "file"]
    assert len(set(keys)) == 3               # all unique now
    assert not any(c.get("duplicate") for c in tree["children"] if c["type"] == "file")


def test_bulk_excludes_key_field(registry, config):
    scope = service.bulk_scope([str(DATA_DIR / "StyleHeader.OK")], registry, config)
    names = [f["name"] for f in scope["header_fields"]["StyleHeader"]]
    assert "keytrol" not in names            # key field hidden from bulk set-value
    assert "indicator" in names


def test_build_tree_is_one_level_lazy(tmp_path, config):
    # Nested structure: root/sub/Style.OK, plus a file at root.
    (tmp_path / "sub").mkdir()
    shutil.copy2(DATA_DIR / "StyleHeader.OK", tmp_path / "sub" / "StyleHeader.OK")
    shutil.copy2(DATA_DIR / "CartonLabel.OK", tmp_path / "CartonLabel.OK")

    top = service.build_tree(tmp_path, config)
    kinds = {c["name"]: c for c in top["children"]}
    # Subfolder is present but NOT expanded (children is None).
    assert kinds["sub"]["type"] == "folder"
    assert kinds["sub"]["children"] is None
    # Root-level .OK file is listed.
    assert kinds["CartonLabel.OK"]["type"] == "file"

    # Expanding the subfolder is a separate call that lists its level.
    sub = service.build_tree(tmp_path / "sub", config)
    names = [c["name"] for c in sub["children"]]
    assert names == ["StyleHeader.OK"]


def test_parse_file_view_shape(registry, config):
    view = service.parse_file_view(DATA_DIR / "StyleHeader.OK", registry, config)
    assert view["layout"] == "StyleHeader"
    assert view["chain"] == "03"
    names = [s["name"] for s in view["sections"]]
    assert "Lane" in names and "Size" in names
    lane = next(s for s in view["sections"] if s["name"] == "Lane")
    assert len(lane["records"]) == 10            # all lanes shown
    assert "lane2" in lane["ignored_fields"]      # unsized fields ignored
    # 'indicator' field carries dropdown options from display.yaml.
    header = view["sections"][0]
    ind = next(f for f in header["fields"] if f["name"] == "indicator")
    assert ind["options"] == {"Y": "Yes", "N": "No"}


def test_save_roundtrip_and_edit(tmp_path, registry, config):
    src = DATA_DIR / "CartonLabel.OK"
    work = tmp_path / "CartonLabel.OK"
    shutil.copy2(src, work)
    original = work.read_bytes()

    # No-op save must be byte-identical.
    res = service.apply_edits(work, [], registry, backup=False)
    assert res["roundtrip_ok"]
    assert work.read_bytes() == original

    # Edit chain in the header (section 0, record 0).
    res = service.apply_edits(
        work,
        [{"section_index": 0, "record_index": 0, "field": "chain", "value": "07"}],
        registry,
        backup=False,
    )
    assert res["edits_applied"] == 1
    view = service.parse_file_view(work, registry, config)
    assert view["sections"][0]["records"][0]["values"]["chain"] == "07"


def test_save_rejects_too_wide_value(tmp_path, registry):
    src = DATA_DIR / "CartonLabel.OK"
    work = tmp_path / "CartonLabel.OK"
    shutil.copy2(src, work)
    with pytest.raises(service.EditError):
        service.apply_edits(
            work,
            [{"section_index": 0, "record_index": 0, "field": "chain", "value": "123"}],
            registry,
            backup=False,
        )


def test_copy_files_batch(tmp_path):
    # Two source files + a destination folder; one name pre-exists to test skip.
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    shutil.copy2(DATA_DIR / "StyleHeader.OK", tmp_path / "src" / "StyleHeader.OK")
    shutil.copy2(DATA_DIR / "CartonLabel.OK", tmp_path / "src" / "CartonLabel.OK")
    shutil.copy2(DATA_DIR / "CartonLabel.OK", tmp_path / "dst" / "CartonLabel.OK")  # collision

    res = service.copy_files(
        [str(tmp_path / "src" / "StyleHeader.OK"), str(tmp_path / "src" / "CartonLabel.OK")],
        tmp_path / "dst",
    )
    assert len(res["copied"]) == 2                         # both copied (none skipped)
    assert len(res["renamed"]) == 1                        # CartonLabel collided -> renamed
    assert res["renamed"][0]["to"] == "CartonLabel (1).OK"
    assert (tmp_path / "dst" / "StyleHeader.OK").exists()
    assert (tmp_path / "dst" / "CartonLabel (1).OK").exists()
    assert (tmp_path / "dst" / "CartonLabel.OK").exists()  # original untouched


def test_copy_files_multiple_collisions(tmp_path):
    (tmp_path / "dst").mkdir()
    shutil.copy2(DATA_DIR / "CartonLabel.OK", tmp_path / "CartonLabel.OK")
    shutil.copy2(DATA_DIR / "CartonLabel.OK", tmp_path / "dst" / "CartonLabel.OK")
    src = str(tmp_path / "CartonLabel.OK")
    # Paste the same file three times -> (1), (2), (3)
    service.copy_files([src], tmp_path / "dst")
    service.copy_files([src], tmp_path / "dst")
    service.copy_files([src], tmp_path / "dst")
    names = sorted(p.name for p in (tmp_path / "dst").iterdir())
    assert names == [
        "CartonLabel (1).OK", "CartonLabel (2).OK", "CartonLabel (3).OK", "CartonLabel.OK",
    ]


def test_bulk_scope_and_preview_and_apply(tmp_path, registry, config):
    # Two StyleHeader copies + one CartonLabel (different layout).
    sh1 = tmp_path / "a.OK"; sh2 = tmp_path / "b.OK"; cl = tmp_path / "c.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", sh1)
    shutil.copy2(DATA_DIR / "StyleHeader.OK", sh2)
    shutil.copy2(DATA_DIR / "CartonLabel.OK", cl)
    paths = [str(sh1), str(sh2), str(cl)]

    scope = service.bulk_scope(paths, registry, config)
    assert scope["layouts"] == {"StyleHeader": 2, "CartonLabel": 1}
    assert any(f["name"] == "indicator" for f in scope["header_fields"]["StyleHeader"])

    # Preview setting indicator -> 'Y' on StyleHeader (sample value is 'N').
    pv = service.bulk_preview(paths, "StyleHeader", "indicator", "Y", registry, config)
    by = {r["name"]: r for r in pv["results"]}
    assert by["a.OK"]["status"] == "change" and by["a.OK"]["new"] == "Y"
    assert by["c.OK"]["status"] == "skipped"          # other layout
    assert sh1.read_bytes() == DATA_DIR.joinpath("StyleHeader.OK").read_bytes()  # preview wrote nothing

    # Apply.
    ap = service.bulk_apply(paths, "StyleHeader", "indicator", "Y", registry, config, backup=False)
    changed = [r for r in ap["results"] if r["status"] == "changed"]
    assert len(changed) == 2
    assert service.parse_file_view(sh1, registry, config)["sections"][0]["records"][0]["values"]["indicator"] == "Y"
    assert cl.read_bytes() == DATA_DIR.joinpath("CartonLabel.OK").read_bytes()  # untouched


def _style_count(path, registry, config, section):
    view = service.parse_file_view(path, registry, config)
    return len(next(s for s in view["sections"] if s["name"] == section)["records"])


def test_bulk_op_add_caps_at_max_and_syncs_count(tmp_path, registry, config):
    # StyleHeader has 10 lanes already (Lane max = 10) and 4 sizes.
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    paths = [str(f)]

    # Add 20 Lanes -> capped at 10 (already at limit -> unchanged).
    pv = service.bulk_op_preview(paths, "StyleHeader", "Lane", {"type": "add", "count": 20}, registry, config)
    assert pv["results"][0]["status"] == "unchanged"

    # Add 20 Sizes -> appended (no Size limit), header size_rec synced.
    ap = service.bulk_op_apply(paths, "StyleHeader", "Size", {"type": "add", "count": 20}, registry, config, backup=False)
    assert ap["results"][0]["status"] == "changed"
    assert _style_count(f, registry, config, "Size") == 24
    hdr = service.parse_file_view(f, registry, config)["sections"][0]["records"][0]["values"]
    assert hdr["size_rec"] == "24"      # count auto-synced (size 2)


def test_bulk_op_keep_first_n_and_sync(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    ap = service.bulk_op_apply([str(f)], "StyleHeader", "Lane", {"type": "keep", "count": 5}, registry, config, backup=False)
    assert ap["results"][0]["status"] == "changed"
    assert _style_count(f, registry, config, "Lane") == 5
    hdr = service.parse_file_view(f, registry, config)["sections"][0]["records"][0]["values"]
    assert hdr["lane_rec"] == "05"


def test_bulk_op_set_all_rows(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    # Set 'qty' = 00009 on every Size row.
    ap = service.bulk_op_apply([str(f)], "StyleHeader", "Size", {"type": "set", "field": "qty", "value": "00009"}, registry, config, backup=False)
    assert ap["results"][0]["status"] == "changed"
    view = service.parse_file_view(f, registry, config)
    size = next(s for s in view["sections"] if s["name"] == "Size")
    assert all(r["values"]["qty"] == "00009" for r in size["records"])


def test_bulk_op_unique_sequential(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    service.bulk_op_apply([str(f)], "StyleHeader", "Size",
                          {"type": "unique", "field": "qty", "start": 1}, registry, config, backup=False)
    view = service.parse_file_view(f, registry, config)
    size = next(s for s in view["sections"] if s["name"] == "Size")
    qtys = [r["values"]["qty"] for r in size["records"]]
    assert qtys == ["00001", "00002", "00003", "00004"]   # qty size 5, 4 rows


def test_bulk_op_unique_overflow(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    # 'size' field is width 6; start huge so start+rows overflows.
    pv = service.bulk_op_preview([str(f)], "StyleHeader", "Size",
                                 {"type": "unique", "field": "qty", "start": 99999}, registry, config)
    assert pv["results"][0]["status"] == "too_wide"        # 99999..100002 > width 5


def test_bulk_op_random_fits_width(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    res = service.bulk_op_apply([str(f)], "StyleHeader", "Size",
                                {"type": "random", "field": "qty"}, registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    view = service.parse_file_view(f, registry, config)
    size = next(s for s in view["sections"] if s["name"] == "Size")
    for r in size["records"]:
        q = r["values"]["qty"]
        assert len(q) == 5 and q.isdigit()                 # width preserved, numeric


def test_bulk_op_random_range(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    service.bulk_op_apply([str(f)], "StyleHeader", "Size",
                          {"type": "random", "field": "qty", "min": 100, "max": 200},
                          registry, config, backup=False)
    view = service.parse_file_view(f, registry, config)
    size = next(s for s in view["sections"] if s["name"] == "Size")
    for r in size["records"]:
        q = r["values"]["qty"]
        assert len(q) == 5 and 100 <= int(q) <= 200          # within range, width preserved


def test_bulk_op_random_range_overflow(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    pv = service.bulk_op_preview([str(f)], "StyleHeader", "Size",
                                 {"type": "random", "field": "qty", "max": 999999}, registry, config)
    assert pv["results"][0]["status"] == "too_wide"          # max exceeds width 5


def test_bulk_op_scope_has_detail_sections(registry, config):
    scope = service.bulk_scope([str(DATA_DIR / "StyleHeader.OK")], registry, config)
    ds = {s["name"]: s for s in scope["detail_sections"]["StyleHeader"]}
    assert ds["Lane"]["max_records"] == 10
    assert ds["Lane"]["count_field"] == "lane_rec"
    assert ds["Size"]["count_field"] == "size_rec"


def test_bulk_preview_rejects_too_wide(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    pv = service.bulk_preview([str(f)], "StyleHeader", "indicator", "TOOLONG", registry, config)
    assert pv["results"][0]["status"] == "too_wide"


def test_folder_create_rename_delete(tmp_path):
    res = service.create_folder(tmp_path, "NewFolder")
    folder = tmp_path / "NewFolder"
    assert folder.is_dir() and res["created"] == str(folder)

    service.rename_folder(folder, tmp_path / "Renamed")
    assert (tmp_path / "Renamed").is_dir()
    assert not folder.exists()

    service.delete_folder(tmp_path / "Renamed")
    assert not (tmp_path / "Renamed").exists()


def test_create_folder_rejects_bad_name(tmp_path):
    with pytest.raises(service.EditError):
        service.create_folder(tmp_path, "bad/name")


def test_paste_whole_folder_recursive(tmp_path):
    # A source folder with a nested file, pasted into a destination.
    src = tmp_path / "Group A"
    (src / "inner").mkdir(parents=True)
    shutil.copy2(DATA_DIR / "StyleHeader.OK", src / "inner" / "StyleHeader.OK")
    dst = tmp_path / "dst"
    dst.mkdir()

    res = service.copy_files([str(src)], dst)
    assert len(res["copied"]) == 1
    assert (dst / "Group A" / "inner" / "StyleHeader.OK").exists()

    # Pasting again auto-renames the folder (Downloads-style).
    res2 = service.copy_files([str(src)], dst)
    assert res2["renamed"][0]["to"] == "Group A (1)"
    assert (dst / "Group A (1)" / "inner" / "StyleHeader.OK").exists()


def test_paste_folder_into_itself_rejected(tmp_path):
    src = tmp_path / "Group"
    (src / "sub").mkdir(parents=True)
    res = service.copy_files([str(src)], src / "sub")
    assert res["errors"] and "itself" in res["errors"][0]["error"]


def test_rename_scope_palette(tmp_path, registry, config):
    f = tmp_path / "x.OK"; shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    sc = service.rename_scope([str(f)], registry, config)
    assert sc["files"][0]["layout"] == "StyleHeader"
    assert "brand" in sc["palette"]["derived"]
    assert "keytrol" in sc["palette"]["header_fields"]
    assert sc["palette"]["custom"] == {"FMT": "FMT"}     # from fixture rename_tokens
    assert sc["sample"]["brand"] == "Homegoods"
    assert sc["sample"]["keytrol"] == "550000"


def test_rename_presets_in_scope(tmp_path, registry, config):
    f = tmp_path / "x.OK"; shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    sc = service.rename_scope([str(f)], registry, config)
    names = [p["name"] for p in sc["presets"]]
    assert "Brand Layout Key" in names
    blk = next(p for p in sc["presets"] if p["name"] == "Brand Layout Key")
    assert blk["separator"] == "_"
    assert blk["parts"] == [
        {"type": "token", "name": "brand"},
        {"type": "token", "name": "layout"},
        {"type": "token", "name": "key"},
    ]


def test_rename_preview_and_apply(tmp_path, registry, config):
    f = tmp_path / "orig.OK"; shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    parts = [
        {"type": "token", "name": "FMT"},        # custom -> "FMT"
        {"type": "token", "name": "brand"},      # Homegoods
        {"type": "token", "name": "key"},        # 550000
    ]
    pv = service.bulk_rename_preview([str(f)], parts, "_", registry, config)
    assert pv["results"][0]["new"] == "FMT_Homegoods_550000.OK"
    assert f.exists()                            # preview wrote nothing

    ap = service.bulk_rename_apply([str(f)], parts, "_", registry, config)
    assert ap["results"][0]["status"] == "renamed"
    assert (tmp_path / "FMT_Homegoods_550000.OK").exists()
    assert not f.exists()


def test_rename_no_delim_glue(tmp_path, registry, config):
    f = tmp_path / "x.OK"; shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    # FMT + (glue) + format  -> "FMTA"; then _ + keytrol
    parts = [
        {"type": "token", "name": "FMT"},
        {"type": "glue"},
        {"type": "token", "name": "format"},
        {"type": "token", "name": "keytrol"},
    ]
    pv = service.bulk_rename_preview([str(f)], parts, "_", registry, config)
    assert pv["results"][0]["new"] == "FMTA_550000.OK"


def test_rename_collision_counter(tmp_path, registry, config):
    a = tmp_path / "a.OK"; b = tmp_path / "b.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", a)   # both same brand+layout
    shutil.copy2(DATA_DIR / "StyleHeader.OK", b)
    parts = [{"type": "token", "name": "brand"}, {"type": "token", "name": "layout"}]
    ap = service.bulk_rename_apply([str(a), str(b)], parts, "_", registry, config)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["Homegoods_StyleHeader.OK", "Homegoods_StyleHeader_001.OK"]


def test_rename_text_and_swap(tmp_path, registry, config):
    # Two-phase swap: a->b, b->a must not clobber.
    a = tmp_path / "a.OK"; b = tmp_path / "b.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", a)
    shutil.copy2(DATA_DIR / "CartonLabel.OK", b)
    # rename using orig + literal text
    parts = [{"type": "token", "name": "orig"}, {"type": "text", "value": "_v2"}]
    ap = service.bulk_rename_apply([str(a), str(b)], parts, "", registry, config)
    assert (tmp_path / "a_v2.OK").exists() and (tmp_path / "b_v2.OK").exists()


def test_send_to_nicelabel(tmp_path):
    from okgen.config import Config
    dest = tmp_path / "incoming"; dest.mkdir()
    a = tmp_path / "a.OK"; b = tmp_path / "b.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", a)
    shutil.copy2(DATA_DIR / "CartonLabel.OK", b)
    cfg = Config.load(FIXTURE_CONFIG)
    cfg._nicelabel_path = str(dest)

    res = service.send_to_nicelabel([str(a), str(b)], cfg)
    assert sorted(res["sent"]) == ["a.OK", "b.OK"]
    assert (dest / "a.OK").exists() and (dest / "b.OK").exists()

    # Sending again overwrites (no error, no rename).
    res2 = service.send_to_nicelabel([str(a)], cfg)
    assert res2["sent"] == ["a.OK"]


def test_send_to_nicelabel_missing_folder(tmp_path):
    from okgen.config import Config
    a = tmp_path / "a.OK"; shutil.copy2(DATA_DIR / "StyleHeader.OK", a)
    cfg = Config.load(FIXTURE_CONFIG)
    cfg._nicelabel_path = str(tmp_path / "does-not-exist")
    with pytest.raises(service.EditError):
        service.send_to_nicelabel([str(a)], cfg)


def test_delete_files_batch(tmp_path):
    a = tmp_path / "a.OK"; b = tmp_path / "b.OK"; c = tmp_path / "c.OK"
    for f in (a, b, c):
        shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    res = service.delete_files([str(a), str(b), str(tmp_path / "missing.OK")])
    assert len(res["deleted"]) == 2
    assert len(res["errors"]) == 1          # missing file reported, not raised
    assert not a.exists() and not b.exists()
    assert c.exists()                       # untouched


def test_save_as_and_copy_delete(tmp_path, registry):
    src = DATA_DIR / "DistLabels.OK"
    work = tmp_path / "DistLabels.OK"
    shutil.copy2(src, work)

    # Save As to a new path.
    other = tmp_path / "DistLabels_copy.OK"
    service.apply_edits(work, [], registry, target_path=str(other), backup=False)
    assert other.exists()

    # Copy + delete file ops.
    dst = tmp_path / "DistLabels_copy2.OK"
    service.copy_file(work, dst)
    assert dst.exists()
    service.delete_file(dst)
    assert not dst.exists()


def test_flask_endpoints():
    from okgen.web.app import create_app

    app = create_app(data_dir=DATA_DIR)
    client = app.test_client()
    assert client.get("/api/health").get_json()["ok"] is True
    chains = client.get("/api/chains").get_json()
    assert chains["03"]["name"] == "Homegoods"
    parsed = client.get(
        "/api/parse", query_string={"path": str(DATA_DIR / "StyleHeader.OK")}
    ).get_json()
    assert parsed["layout"] == "StyleHeader"
    # The HTML UI shell renders.
    assert client.get("/").status_code == 200


def test_parse_view_includes_raw_text(registry, config):
    path = DATA_DIR / "StyleHeader.OK"
    view = service.parse_file_view(path, registry, config)
    assert view["raw_text"] == path.read_bytes().decode("latin-1")


def test_max_records_in_view(registry, config):
    view = service.parse_file_view(DATA_DIR / "StyleHeader.OK", registry, config)
    lane = next(s for s in view["sections"] if s["name"] == "Lane")
    size = next(s for s in view["sections"] if s["name"] == "Size")
    assert lane["max_records"] == 10        # configured limit
    assert size["max_records"] is None      # no limit


def test_add_record_copies_last_row(tmp_path, registry, config):
    src = DATA_DIR / "StyleHeader.OK"
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(src, work)
    before = service.parse_file_view(work, registry, config)
    size_sec = next(s for s in before["sections"] if s["name"] == "Size")
    size_idx = size_sec["index"]
    n_before = len(size_sec["records"])
    last_values = size_sec["records"][-1]["values"]

    view = service.add_record(work, size_idx, [], registry, config, backup=False)
    size = next(s for s in view["sections"] if s["name"] == "Size")
    assert len(size["records"]) == n_before + 1
    assert view["roundtrip_ok"]                       # file still well-formed
    # The appended record is a copy of the previous last row.
    assert size["records"][-1]["values"] == last_values


def test_delete_record(tmp_path, registry, config):
    src = DATA_DIR / "StyleHeader.OK"
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(src, work)
    before = service.parse_file_view(work, registry, config)
    lane = next(s for s in before["sections"] if s["name"] == "Lane")
    n_before = len(lane["records"])
    victim = lane["records"][0]["index"]

    view = service.delete_record(work, victim, [], registry, config, backup=False)
    lane_after = next(s for s in view["sections"] if s["name"] == "Lane")
    assert len(lane_after["records"]) == n_before - 1
    assert view["roundtrip_ok"]


def test_move_record_reorders(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    before = service.parse_file_view(f, registry, config)
    lane = next(s for s in before["sections"] if s["name"] == "Lane")
    first, second = lane["records"][0], lane["records"][1]
    v0, v1 = first["values"]["lane1"], second["values"]["lane1"]

    # Move the first lane down -> the first two swap.
    view = service.move_record(f, first["index"], "down", [], registry, config, backup=False)
    lane2 = next(s for s in view["sections"] if s["name"] == "Lane")
    assert lane2["records"][0]["values"]["lane1"] == v1
    assert lane2["records"][1]["values"]["lane1"] == v0
    assert view["roundtrip_ok"]


def test_move_record_edge_and_header(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    view = service.parse_file_view(f, registry, config)
    lane = next(s for s in view["sections"] if s["name"] == "Lane")
    with pytest.raises(service.EditError):                  # first row can't go up
        service.move_record(f, lane["records"][0]["index"], "up", [], registry, config, backup=False)
    with pytest.raises(service.EditError):                  # header can't move
        service.move_record(f, 0, "down", [], registry, config, backup=False)


def test_delete_header_rejected(tmp_path, registry, config):
    src = DATA_DIR / "CartonLabel.OK"
    work = tmp_path / "CartonLabel.OK"
    shutil.copy2(src, work)
    with pytest.raises(service.EditError):
        service.delete_record(work, 0, [], registry, config, backup=False)


def test_add_record_after_index_inserts_below(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    before = service.parse_file_view(f, registry, config)
    size = next(s for s in before["sections"] if s["name"] == "Size")
    first = size["records"][0]
    n = len(size["records"])

    view = service.add_record(f, edits=[], registry=registry, config=config,
                              backup=False, after_index=first["index"])
    size2 = next(s for s in view["sections"] if s["name"] == "Size")
    assert len(size2["records"]) == n + 1
    assert size2["records"][1]["values"]["size"] == first["values"]["size"]  # copy sits right below
    assert view["roundtrip_ok"]


def test_add_record_after_header_rejected(tmp_path, registry, config):
    f = tmp_path / "a.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", f)
    with pytest.raises(service.EditError):
        service.add_record(f, edits=[], registry=registry, config=config, backup=False, after_index=0)


def test_add_record_respects_lane_limit(tmp_path, registry, config):
    src = DATA_DIR / "StyleHeader.OK"        # already has 10 lanes (the limit)
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(src, work)
    view = service.parse_file_view(work, registry, config)
    lane_idx = next(s["index"] for s in view["sections"] if s["name"] == "Lane")
    with pytest.raises(service.EditError):
        service.add_record(work, lane_idx, [], registry, config, backup=False)


def test_browse_folder_parses_dialog_output(monkeypatch):
    # Mock the native dialog so the test never opens a real GUI.
    import subprocess

    class FakeProc:
        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc("/picked/folder\n"))
    assert service.browse_folder()["path"] == "/picked/folder"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(""))  # cancelled
    assert service.browse_folder()["path"] is None


def test_eu_raw_view_hides_bom_but_bytes_untouched(registry, config):
    """Raw verify tab shows EU files as clean UTF-8 (no BOM / no Latin-1 mojibake);
    the on-disk bytes and round-trip are unaffected."""
    view = service.parse_file_view(DATA_DIR / "EUPreticket.OK", registry, config)
    raw = view["raw_text"]
    assert raw.splitlines()[0].startswith("¦")   # clean broken-bar marker
    assert "﻿" not in raw                          # BOM hidden
    assert "\xef\xbb\xbf" not in raw and "\xc2\xa6" not in raw  # no Latin-1 mojibake
    assert view["roundtrip_ok"] is True                # file itself still byte-exact
    # NA files keep the byte-exact Latin-1 view unchanged.
    na = service.parse_file_view(DATA_DIR / "Preticket.OK", registry, config)
    assert na["roundtrip_ok"] is True


def test_eu_file_node_banner_is_europe(registry, config):
    """The EU (delimited) file reads chain '05' from its tokens -> Europe/EU badge."""
    tree = service.build_tree(DATA_DIR, config, registry)
    eu = next(f for f in tree["children"] if f["name"] == "EUPreticket.OK")
    assert eu["layout"] == "EUPreticket"
    assert eu["chain"] == "05"
    assert eu["chain_info"]["name"] == "Europe"
    assert eu["chain_info"]["short"] == "EU"


def test_make_unique_eu_reads_key_and_stays_nonzero(registry, config, tmp_path):
    """Make Unique on duplicate EU (delimited) files keeps the first file's real
    po, continues from it, and never assigns an all-zero key."""
    import shutil as _sh

    for n in ("a", "b", "c"):
        _sh.copy2(DATA_DIR / "EUPreticket.OK", tmp_path / f"{n}.OK")

    res = service.make_unique_in_folder(tmp_path, registry, config, backup=False)
    tree = service.build_tree(tmp_path, config, registry)
    by_name = {c["name"]: c for c in tree["children"] if c["type"] == "file"}

    # First file keeps its actual po; the others get distinct, non-zero keys.
    assert by_name["a.OK"]["key_value"] == "10021888"
    pos = {by_name[n]["key_value"] for n in ("a.OK", "b.OK", "c.OK")}
    assert len(pos) == 3, "all keys must be unique"
    assert all(int(p) != 0 for p in pos), "no key may be all-zero"
    assert all(not by_name[n]["duplicate"] for n in by_name)


def test_flask_save_endpoint(tmp_path):
    import shutil as _sh

    from okgen.web.app import create_app

    src = DATA_DIR / "CartonLabel.OK"
    work = tmp_path / "CartonLabel.OK"
    _sh.copy2(src, work)
    client = create_app(data_dir=DATA_DIR).test_client()
    res = client.post("/api/save", json={
        "path": str(work),
        "edits": [{"section_index": 0, "record_index": 0, "field": "chain", "value": "07"}],
        "backup": False,
    })
    assert res.status_code == 200
    assert res.get_json()["edits_applied"] == 1
