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
    assert "indicator" not in names          # detection signature — locked in bulk too
    assert "dept" in names                   # ordinary fields still offered


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
    assert any(f["name"] == "dept" for f in scope["header_fields"]["StyleHeader"])

    # Preview setting dept -> '77' on StyleHeader.
    pv = service.bulk_preview(paths, "StyleHeader", "dept", "77", registry, config)
    by = {r["name"]: r for r in pv["results"]}
    assert by["a.OK"]["status"] == "change" and by["a.OK"]["new"] == "77"
    assert by["c.OK"]["status"] == "skipped"          # other layout
    assert sh1.read_bytes() == DATA_DIR.joinpath("StyleHeader.OK").read_bytes()  # preview wrote nothing

    # Apply.
    ap = service.bulk_apply(paths, "StyleHeader", "dept", "77", registry, config, backup=False)
    changed = [r for r in ap["results"] if r["status"] == "changed"]
    assert len(changed) == 2
    assert service.parse_file_view(sh1, registry, config)["sections"][0]["records"][0]["values"]["dept"] == "77"
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


def _styleheader_with_empty_lane(tmp_path, registry):
    """A StyleHeader file whose Lane ('#') section has been emptied out."""
    from okgen.okfile import parse_okfile
    lay = registry.get("StyleHeader")
    okf = parse_okfile(DATA_DIR / "StyleHeader.OK", layout=lay, registry=registry)
    okf.records = [r for r in okf.records if not (r.section and r.section.name == "Lane")]
    work = tmp_path / "StyleHeader.OK"
    okf.save(work)
    return work


def test_add_record_seeds_empty_section(tmp_path, registry, config):
    """Adding to an empty section seeds a valid first row without disturbing
    the sibling section."""
    work = _styleheader_with_empty_lane(tmp_path, registry)
    before = service.parse_file_view(work, registry, config)
    lane = next(s for s in before["sections"] if s["name"] == "Lane")
    n_size = len(next(s for s in before["sections"] if s["name"] == "Size")["records"])
    assert lane["records"] == []                    # empty, but present as "None"

    view = service.add_record(work, lane["index"], [], registry, config, backup=False)
    lane2 = next(s for s in view["sections"] if s["name"] == "Lane")
    size2 = next(s for s in view["sections"] if s["name"] == "Size")
    assert len(lane2["records"]) == 1               # seeded the first Lane row
    assert len(size2["records"]) == n_size          # Size untouched (no shift)
    assert view["roundtrip_ok"]


def test_bulk_add_seeds_empty_section(tmp_path, registry, config):
    """Bulk Add can populate an empty section from its seed line."""
    work = _styleheader_with_empty_lane(tmp_path, registry)
    res = service._bulk_op_eval(
        work, "StyleHeader", "Lane", {"type": "add", "count": 3}, registry, config)
    assert res["status"] == "change", res
    res["okf"].save(work)
    view = service.parse_file_view(work, registry, config)
    lane = next(s for s in view["sections"] if s["name"] == "Lane")
    assert len(lane["records"]) == 3
    assert view["roundtrip_ok"]


def test_bulk_set_on_empty_section_is_noop(tmp_path, registry, config):
    """Non-add bulk ops still report no_section on an empty section."""
    work = _styleheader_with_empty_lane(tmp_path, registry)
    res = service._bulk_op_eval(
        work, "StyleHeader", "Lane",
        {"type": "set", "field": "lane1", "value": "X"}, registry, config)
    assert res["status"] == "no_section"


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


def test_eu_gta_hidden_and_readonly_process(registry, config):
    """EU GTA layouts hide marker fields and lock `process` to a friendly label."""
    for fn, letter, want_label in [
        ("EUStyleHeader.OK", "D", "(D) StyleHeader"),
        ("EUCartonLabel.OK", "H", "(H) Holdings/CartonLabels"),
    ]:
        view = service.parse_file_view(DATA_DIR / fn, registry, config)
        by_sec = {s["name"]: s for s in view["sections"]}

        # The leading-'|' placeholder is hidden in every section; the record-type
        # markers ('#'/'&') are hidden in their detail/lane sections.
        for sec in view["sections"]:
            hidden = {f["name"] for f in sec["fields"] if f["hidden"]}
            assert "marker" in hidden
        assert "detail_marker" in {f["name"] for f in by_sec["Detail"]["fields"] if f["hidden"]}
        if "Lane" in by_sec:
            assert "lane_marker" in {f["name"] for f in by_sec["Lane"]["fields"] if f["hidden"]}

        # `process` is shown but not editable, and carries its layout-name label.
        proc = next(f for f in by_sec["Header"]["fields"] if f["name"] == "process")
        assert proc["editable"] is False
        assert proc["options"][letter] == want_label


def test_eu_cartonlabel_derived_format(registry, config):
    """EUCartonLabel gets a read-only, computed `format` field (not in the raw)."""
    view = service.parse_file_view(DATA_DIR / "EUCartonLabel.OK", registry, config)
    header = next(s for s in view["sections"] if s["name"] == "Header")
    names = [f["name"] for f in header["fields"]]

    # Present, derived, read-only, and placed right after `process`.
    fmt = next(f for f in header["fields"] if f["name"] == "format")
    assert fmt["derived"] is True and fmt["editable"] is False
    assert set(fmt["inputs"]) == {"distribution_type", "pack_type"}
    assert names[names.index("process") + 1] == "format"

    # Sample file is distribution_type=RG, pack_type=CL -> "1 - Carton Label".
    assert header["records"][0]["values"]["format"] == "1 - Carton Label"

    # It is NOT a real field of the layout (absent from the raw record).
    real_names = {f.name for sec in registry["EUCartonLabel"].sections for f in sec.fields}
    assert "format" not in real_names
    # ...and StyleHeader has no derived format field at all.
    sh = service.parse_file_view(DATA_DIR / "EUStyleHeader.OK", registry, config)
    assert not any(f.get("derived") for s in sh["sections"] for f in s["fields"])


def test_derived_format_rules_cover_all_cases(config):
    """The four documented distribution_type/pack_type combinations resolve."""
    spec = config.derived_fields("EUCartonLabel")[0]
    cases = {
        ("RG", "CL"): "1 - Carton Label",
        ("AD", "CL"): "2 - AD Carton Label",
        ("AD", "C "): "2 - AD Carton Label",   # padded, trimmed
        ("RG", "MP"): "4 - Masterpack",
        ("AD", "MP"): "5 - AD Masterpack",
        ("XX", "ZZ"): "",                      # no rule -> default
    }
    for (dt, pt), want in cases.items():
        got = config.eval_derived(spec, {"distribution_type": dt, "pack_type": pt})
        assert got == want, f"{dt}/{pt} -> {got!r} (want {want!r})"


def test_rename_resolves_derived_format_token(registry, config):
    """The rename `format` token resolves via the derived rule for EUCartonLabel."""
    toks = service._file_tokens(DATA_DIR / "EUCartonLabel.OK", registry, config, {})
    assert toks["format"] == "1 - Carton Label"   # derived from RG + CL

    # It's offered in the rename palette, alongside the new EU GTA fields.
    scope = service.rename_scope([str(DATA_DIR / "EUCartonLabel.OK")], registry, config)
    hf = scope["palette"]["header_fields"]
    assert "format" in hf
    assert {"distribution_type", "pack_type", "zone_retail"} <= set(hf)

    # A rename using `format` sanitizes the spaces (like format_label).
    parts = [{"type": "token", "name": "format"}]
    name = service._build_name(parts, toks, "_", 1,
                               label_names={"brand", "format_label"} | config.all_derived_names())
    assert name == "1_-_Carton_Label"


def test_chain_isolation_rules(config):
    """Europe is isolated; NA chains interchange freely."""
    assert config.can_change_chain("05", "01") is False   # Europe -> NA blocked
    assert config.can_change_chain("01", "05") is False   # NA -> Europe blocked
    assert config.can_change_chain("03", "02") is True     # NA <-> NA allowed
    assert config.can_change_chain("05", "05") is True     # unchanged is fine


def test_chain_field_options_restricted(registry, config):
    """The editor offers only same-group chains; Europe is locked read-only."""
    eu = service.parse_file_view(DATA_DIR / "EUCartonLabel.OK", registry, config)
    ch = next(f for f in eu["sections"][0]["fields"] if f["name"] == "chain")
    assert ch["editable"] is False and list(ch["options"]) == ["05"]

    na = service.parse_file_view(DATA_DIR / "CartonLabel.OK", registry, config)
    ch = next(f for f in na["sections"][0]["fields"] if f["name"] == "chain")
    assert ch["editable"] is True
    assert "05" not in ch["options"] and "02" in ch["options"]


def test_save_blocks_cross_region_chain_change(tmp_path, registry, config):
    """Saving a chain edit across the Europe boundary is rejected."""
    import shutil as _sh
    work = tmp_path / "EUCartonLabel.OK"
    _sh.copy2(DATA_DIR / "EUCartonLabel.OK", work)
    with pytest.raises(service.EditError):
        service.apply_edits(
            str(work),
            [{"section_index": 0, "record_index": 0, "field": "chain", "value": "01"}],
            registry, config=config,
        )


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


# --------------------------------------------------------------------------- #
# Staged row ops — nothing is written until Save/Save As
# --------------------------------------------------------------------------- #
def test_row_op_preview_writes_nothing(tmp_path, registry, config):
    """A previewed row op shows the result but leaves the file on disk alone."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    original = work.read_bytes()

    before = service.parse_file_view(work, registry, config)
    lane = next(s for s in before["sections"] if s["name"] == "Lane")
    n_before = len(lane["records"])

    view = service.delete_record(
        work, lane["records"][0]["index"], [], registry, config, preview=True)

    lane_after = next(s for s in view["sections"] if s["name"] == "Lane")
    assert len(lane_after["records"]) == n_before - 1   # preview reflects the delete
    assert work.read_bytes() == original                # ...but disk is untouched
    assert not (tmp_path / "StyleHeader.OK.bak").exists()


def test_save_as_with_staged_ops_leaves_original_untouched(tmp_path, registry, config):
    """The reported bug: row deletions + edits must land ONLY in the Save As file."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    original = work.read_bytes()
    other = tmp_path / "copy.OK"

    before = service.parse_file_view(work, registry, config)
    lane = next(s for s in before["sections"] if s["name"] == "Lane")
    n_lanes = len(lane["records"])

    ops = [
        {"type": "edit", "edits": [
            {"section_index": 0, "record_index": 0, "field": "keytrol", "value": "551234"}]},
        {"type": "delete", "record_index": lane["records"][0]["index"]},
    ]
    res = service.apply_edits(work, [], registry, target_path=str(other),
                              config=config, ops=ops)

    assert res["ops_applied"] == 2
    assert work.read_bytes() == original          # source file untouched
    saved = service.parse_file_view(other, registry, config)
    assert len(next(s for s in saved["sections"] if s["name"] == "Lane")["records"]) == n_lanes - 1
    assert saved["sections"][0]["records"][0]["values"]["keytrol"].strip() == "551234"


def test_save_applies_staged_ops_in_place(tmp_path, registry, config):
    """Plain Save replays the same journal into the file that was opened."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    before = service.parse_file_view(work, registry, config)
    lane = next(s for s in before["sections"] if s["name"] == "Lane")
    n_lanes = len(lane["records"])

    service.apply_edits(work, [], registry, backup=False, config=config,
                        ops=[{"type": "delete", "record_index": lane["records"][0]["index"]}])

    after = service.parse_file_view(work, registry, config)
    assert len(next(s for s in after["sections"] if s["name"] == "Lane")["records"]) == n_lanes - 1
    assert after["roundtrip_ok"]


def test_staged_ops_replay_in_order_with_shifting_indices(tmp_path, registry, config):
    """Each op's indices are relative to the view the previous ops produced."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    before = service.parse_file_view(work, registry, config)
    size = next(s for s in before["sections"] if s["name"] == "Size")
    n_size = len(size["records"])

    # Delete the first Size row, then add one back after what is NOW the first
    # row (an index that only exists post-delete).
    first = size["records"][0]["index"]
    view = service.add_record(
        work, None, [], registry, config, after_index=first, preview=True,
        ops=[{"type": "delete", "record_index": first}])

    size_after = next(s for s in view["sections"] if s["name"] == "Size")
    assert len(size_after["records"]) == n_size      # -1 then +1
    assert work.read_bytes() == (DATA_DIR / "StyleHeader.OK").read_bytes()


def test_rejected_staged_op_writes_nothing(tmp_path, registry, config):
    """A bad op aborts the whole save — neither file is written."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    original = work.read_bytes()
    other = tmp_path / "copy.OK"

    with pytest.raises(service.EditError):
        service.apply_edits(work, [], registry, target_path=str(other), config=config,
                            ops=[{"type": "delete", "record_index": 0}])  # header row

    assert work.read_bytes() == original
    assert not other.exists()


def test_flask_record_delete_preview_does_not_write(tmp_path):
    from okgen.web.app import create_app

    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    original = work.read_bytes()

    client = create_app(data_dir=DATA_DIR).test_client()
    res = client.post("/api/record/delete", json={
        "path": str(work), "record_index": 1, "preview": True,
    })
    assert res.status_code == 200
    assert work.read_bytes() == original


def _non_fill_detail(view, config):
    """First populated non-header section whose row count is NOT auto-managed by
    detail-fill (Preticket's Lane is — its counts are covered by dedicated
    fill tests, so the generic count assertions skip it)."""
    return next((s for s in view["sections"]
                 if not s["is_header"] and s["records"]
                 and not config.zero_fill(view["layout"], s["name"])), None)


@pytest.mark.parametrize("sample", [
    "StyleHeader.OK", "Preticket.OK", "DistLabels.OK", "CartonLabel.OK",
    "EUPreticket.OK", "EUStyleHeader.OK", "EUCartonLabel.OK",
])
def test_save_as_leaves_original_untouched_every_layout(tmp_path, registry, config, sample):
    """Every layout, fixed-width and delimited: Save As must not touch the source."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    original = work.read_bytes()

    before = service.parse_file_view(work, registry, config)
    detail = _non_fill_detail(before, config)
    if detail is None:
        pytest.skip(f"{sample}: only detail-fill-managed sections (covered elsewhere)")
    victim = detail["records"][0]["index"]

    # Preview the delete: the view reflects it, the file does not.
    view = service.delete_record(work, victim, [], registry, config, preview=True)
    n_preview = len(next(s for s in view["sections"] if s["name"] == detail["name"])["records"])
    assert n_preview == len(detail["records"]) - 1
    assert work.read_bytes() == original

    # Save As: the copy carries the change, the source is byte-identical.
    other = tmp_path / f"copy_{sample}"
    service.apply_edits(work, [], registry, target_path=str(other), config=config,
                        ops=[{"type": "delete", "record_index": victim}])
    assert work.read_bytes() == original
    saved = service.parse_file_view(other, registry, config)
    assert len(next(s for s in saved["sections"] if s["name"] == detail["name"])["records"]) == n_preview


# --------------------------------------------------------------------------- #
# Layout detection signatures — locked in the editor, in bulk, and on save
# --------------------------------------------------------------------------- #
# Each layout's header carries the byte(s) detect.py keys off. Editing one makes
# the file unopenable, or (StyleHeader/Preticket) silently detect as DistLabels.
SIGNATURE_FIELDS = [
    ("StyleHeader.OK", "StyleHeader", "indicator"),
    ("Preticket.OK", "Preticket", "indicator"),
    ("DistLabels.OK", "DistLabels", "format"),
    ("CartonLabel.OK", "CartonLabel", "picklist_pre"),
    ("EUPreticket.OK", "EUPreticket", "indicator"),
    ("EUStyleHeader.OK", "EUStyleHeader", "process"),
    ("EUCartonLabel.OK", "EUCartonLabel", "process"),
]


@pytest.mark.parametrize("sample,layout,field", SIGNATURE_FIELDS)
def test_signature_field_is_readonly_in_editor(registry, config, sample, layout, field):
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    view = service.parse_file_view(DATA_DIR / sample, registry, config)
    assert view["layout"] == layout
    meta = next(f for f in view["sections"][0]["fields"] if f["name"] == field)
    assert not meta["editable"], f"{layout}.{field} is the detection signature — must be read-only"


@pytest.mark.parametrize("sample,layout,field", SIGNATURE_FIELDS)
def test_signature_field_not_offered_in_bulk(registry, config, sample, layout, field):
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    scope = service.bulk_scope([str(DATA_DIR / sample)], registry, config)
    names = [f["name"] for f in scope["header_fields"][layout]]
    assert field not in names, f"bulk edit must not offer {layout}.{field}"


@pytest.mark.parametrize("sample,layout,field", SIGNATURE_FIELDS)
def test_save_refuses_to_break_detection(tmp_path, registry, config, sample, layout, field):
    """The backstop: even bypassing the config lock, the write is rejected."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    original = work.read_bytes()

    with pytest.raises(service.EditError, match="detection signature"):
        service.apply_edits(
            work, [{"section_index": 0, "record_index": 0, "field": field, "value": "8"}],
            registry, config=config, backup=False)

    assert work.read_bytes() == original                 # nothing written
    assert not work.with_suffix(".OK.bak").exists()       # and no stray backup


def test_bulk_apply_reports_signature_break_per_file(tmp_path, registry, config):
    """A signature-breaking bulk apply errors per file instead of bricking them."""
    a, b = tmp_path / "a.OK", tmp_path / "b.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", a)
    shutil.copy2(DATA_DIR / "StyleHeader.OK", b)
    original = a.read_bytes()

    res = service.bulk_apply([str(a), str(b)], "StyleHeader", "indicator", "Y",
                             registry, config, backup=False)
    assert [r["status"] for r in res["results"]] == ["error", "error"]
    assert all("detection signature" in r["error"] for r in res["results"])
    assert a.read_bytes() == original and b.read_bytes() == original


ALL_SAMPLES = ["StyleHeader.OK", "Preticket.OK", "DistLabels.OK", "CartonLabel.OK",
               "EUPreticket.OK", "EUStyleHeader.OK", "EUCartonLabel.OK"]


def _detail_count(view, section):
    return len(next(s for s in view["sections"] if s["name"] == section)["records"])


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_staged_add_every_layout(tmp_path, registry, config, sample):
    """Add-row staged: preview writes nothing, Save As adds only to the copy."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    original = work.read_bytes()

    before = service.parse_file_view(work, registry, config)
    # First populated section with room to grow (StyleHeader's Lane sits at its
    # 10-record limit, so fall through to Size rather than skipping the layout).
    detail = next((s for s in before["sections"]
                   if not s["is_header"] and s["records"]
                   and not config.zero_fill(before["layout"], s["name"])
                   and (s["max_records"] is None or len(s["records"]) < s["max_records"])), None)
    if detail is None:
        pytest.skip(f"{sample}: no non-fill section with headroom")
    anchor = detail["records"][0]["index"]

    view = service.add_record(work, None, [], registry, config,
                              after_index=anchor, preview=True)
    assert _detail_count(view, detail["name"]) == len(detail["records"]) + 1
    assert work.read_bytes() == original            # preview wrote nothing

    other = tmp_path / f"copy_{sample}"
    service.apply_edits(work, [], registry, target_path=str(other), config=config,
                        ops=[{"type": "add", "after_index": anchor}])
    assert work.read_bytes() == original            # source untouched
    saved = service.parse_file_view(other, registry, config)
    assert _detail_count(saved, detail["name"]) == len(detail["records"]) + 1


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_staged_move_every_layout(tmp_path, registry, config, sample):
    """Move-row staged: preview writes nothing, Save As reorders only the copy."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    original = work.read_bytes()

    before = service.parse_file_view(work, registry, config)
    detail = next((s for s in before["sections"]
                   if not s["is_header"] and len(s["records"]) >= 2), None)
    if detail is None:
        pytest.skip(f"{sample} has no section with 2+ rows to reorder")
    first, second = detail["records"][0], detail["records"][1]
    field = next(f["name"] for f in detail["fields"]
                 if not f.get("hidden") and first["values"].get(f["name"]) is not None)
    v0, v1 = first["values"][field], second["values"][field]

    view = service.move_record(work, first["index"], "down", [], registry, config, preview=True)
    moved = next(s for s in view["sections"] if s["name"] == detail["name"])["records"]
    assert (moved[0]["values"][field], moved[1]["values"][field]) == (v1, v0)
    assert work.read_bytes() == original            # preview wrote nothing

    other = tmp_path / f"copy_{sample}"
    service.apply_edits(work, [], registry, target_path=str(other), config=config,
                        ops=[{"type": "move", "record_index": first["index"], "direction": "down"}])
    assert work.read_bytes() == original            # source untouched
    saved = service.parse_file_view(other, registry, config)
    srows = next(s for s in saved["sections"] if s["name"] == detail["name"])["records"]
    assert (srows[0]["values"][field], srows[1]["values"][field]) == (v1, v0)


# --------------------------------------------------------------------------- #
# v0.20.0 features, per layout: empty sections, padding, single-file bulk
# --------------------------------------------------------------------------- #
def _empty_out_section(work, section_name, registry, config):
    """Delete every row of a section via staged ops; returns the saved view."""
    ops = []
    while True:
        view = service.parse_file_view(work, registry, config)
        rows = next(s for s in view["sections"] if s["name"] == section_name)["records"]
        if not rows:
            break
        service.delete_record(work, rows[0]["index"], [], registry, config, backup=False)
    return service.parse_file_view(work, registry, config)


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_empty_section_survives_every_layout(tmp_path, registry, config, sample):
    """Emptying a section keeps it visible and does not shift the other sections."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    before = service.parse_file_view(work, registry, config)
    target = next(s for s in before["sections"] if not s["is_header"] and s["records"])
    others = {s["name"]: len(s["records"]) for s in before["sections"]
              if s["name"] != target["name"]}

    after = _empty_out_section(work, target["name"], registry, config)

    names = [s["name"] for s in after["sections"]]
    assert names == [s["name"] for s in before["sections"]]      # canonical order kept
    emptied = next(s for s in after["sections"] if s["name"] == target["name"])
    assert emptied["records"] == []                              # present, shown as "None"
    for s in after["sections"]:                                  # siblings unshifted
        if s["name"] != target["name"]:
            assert len(s["records"]) == others[s["name"]], f"{s['name']} shifted"
    assert after["roundtrip_ok"]


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_empty_section_can_be_reseeded_every_layout(tmp_path, registry, config, sample):
    """After emptying a section you can add its first row back from the seed."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    before = service.parse_file_view(work, registry, config)
    target = _non_fill_detail(before, config)     # fill layouts add 10 filler rows
    if target is None:
        pytest.skip(f"{sample}: only detail-fill-managed sections")

    after = _empty_out_section(work, target["name"], registry, config)
    sec_index = next(s["index"] for s in after["sections"] if s["name"] == target["name"])
    view = service.add_record(work, sec_index, [], registry, config, backup=False)

    assert _detail_count(view, target["name"]) == 1
    assert view["roundtrip_ok"]


def test_section_without_sample_rows_cannot_be_seeded(tmp_path, registry, config):
    """Known gap: a section absent from the reference .OK has no seed to copy.

    DistLabels' TSticker has zero rows in data/OkFileDefinitions/DistLabels.OK,
    so the compiler could not learn its marker or a sample line — adding its
    first row fails with a clear error rather than writing a guessed record.
    Fixing this needs a real DistLabels sample containing TSticker rows.
    """
    work = tmp_path / "DistLabels.OK"
    shutil.copy2(DATA_DIR / "DistLabels.OK", work)
    view = service.parse_file_view(work, registry, config)
    tsticker = next((s for s in view["sections"] if s["name"] == "TSticker"), None)
    if tsticker is None:
        pytest.skip("TSticker section not in this layout build")
    assert tsticker["records"] == []
    with pytest.raises(service.EditError, match="no template to seed"):
        service.add_record(work, tsticker["index"], [], registry, config, backup=False)


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_short_value_is_repadded_on_save_every_layout(tmp_path, registry, config, sample):
    """Padding UX: a short typed value is re-padded, so record width is preserved."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    width_before = len(work.read_bytes().split(b"\n")[0])

    view = service.parse_file_view(work, registry, config)
    field = next(f for f in view["sections"][0]["fields"]
                 if f.get("size") and f["size"] >= 4 and f["editable"]
                 and not f["hidden"] and not f.get("derived") and not f.get("options"))
    res = service.apply_edits(
        work, [{"section_index": 0, "record_index": 0, "field": field["name"], "value": "7"}],
        registry, config=config, backup=False)

    assert res["roundtrip_ok"]
    assert len(work.read_bytes().split(b"\n")[0]) == width_before   # re-padded to width
    stored = service.parse_file_view(work, registry, config)["sections"][0]["records"][0]
    assert len(stored["values"][field["name"]]) == field["size"]
    assert stored["values"][field["name"]].strip().lstrip("0") == "7"


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_bulk_works_on_a_single_file_every_layout(tmp_path, registry, config, sample):
    """Bulk Edit applies to a selection of ONE file, on every layout."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    scope = service.bulk_scope([str(work)], registry, config)
    layout = next(iter(scope["layouts"]))
    field = next(f for f in scope["header_fields"][layout]
                 if f.get("size") and f["size"] >= 4 and not f.get("options"))

    pv = service.bulk_preview([str(work)], layout, field["name"], "7", registry, config)
    assert pv["results"][0]["status"] == "change"
    ap = service.bulk_apply([str(work)], layout, field["name"], "7", registry, config,
                            backup=False)
    assert ap["results"][0]["status"] == "changed"


def test_bulk_preview_flags_partial_signature_value(tmp_path, registry, config):
    """CartonLabel 'format' is legal to edit, but N/Y/7/9 collide with another
    layout's detection rule — the PREVIEW must say so, not just the apply."""
    work = tmp_path / "CartonLabel.OK"
    shutil.copy2(DATA_DIR / "CartonLabel.OK", work)
    original = work.read_bytes()

    pv = service.bulk_preview([str(work)], "CartonLabel", "format", "7", registry, config)
    assert pv["results"][0]["status"] == "error"
    assert "detection signature" in pv["results"][0]["error"]

    ap = service.bulk_apply([str(work)], "CartonLabel", "format", "7", registry, config,
                            backup=False)
    assert ap["results"][0]["status"] == "error"
    assert work.read_bytes() == original


def test_derived_field_survives_staged_preview_and_is_never_written(tmp_path, registry, config):
    """Derived values must be computed on the in-memory preview path too.

    ``_build_file_view`` renders staged (unsaved) state without touching disk;
    this checks the derived `format` is still computed there, recomputes when a
    driving input changes, and is never persisted into the file's bytes.
    """
    work = tmp_path / "EUCartonLabel.OK"
    shutil.copy2(DATA_DIR / "EUCartonLabel.OK", work)
    original = work.read_bytes()

    def fmt_of(view):
        return view["sections"][0]["records"][0]["values"]["format"]

    assert fmt_of(service.parse_file_view(work, registry, config)) == "1 - Carton Label"

    # Preview a staged row delete: derived still resolves, nothing written.
    detail = next(s for s in service.parse_file_view(work, registry, config)["sections"]
                  if s["name"] == "Detail")
    preview = service.delete_record(work, detail["records"][0]["index"], [],
                                    registry, config, preview=True)
    assert fmt_of(preview) == "1 - Carton Label"
    assert work.read_bytes() == original

    # Preview with a pending edit to a driving input -> recomputed live.
    preview2 = service.delete_record(
        work, detail["records"][0]["index"],
        [{"section_index": 0, "record_index": 0, "field": "distribution_type", "value": "AD"}],
        registry, config, preview=True)
    assert fmt_of(preview2) == "2 - AD Carton Label"
    assert work.read_bytes() == original

    # Save As with that edit staged: the copy stores the INPUT, never the
    # derived string, and the source is untouched.
    other = tmp_path / "copy.OK"
    service.apply_edits(
        work, [], registry, target_path=str(other), config=config,
        ops=[{"type": "edit", "edits": [
            {"section_index": 0, "record_index": 0, "field": "distribution_type", "value": "AD"}]}])
    assert work.read_bytes() == original
    assert b"AD Carton Label" not in other.read_bytes()      # derived not persisted
    saved = service.parse_file_view(other, registry, config)
    assert saved["sections"][0]["records"][0]["values"]["distribution_type"].strip() == "AD"
    assert fmt_of(saved) == "2 - AD Carton Label"            # recomputed on reload


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_staged_add_then_edit_new_row_every_layout(tmp_path, registry, config, sample):
    """Regression: editing a row added in the SAME staged batch must not corrupt it.

    Delimited records locate fields by walking delimiters (``field_spans``),
    which is built during parsing. A cloned row built without it fell back to
    the fixed-width formula and wrote over the record-type marker, orphaning the
    row into "(unassigned)". The pre-staging code hid this because every row op
    saved to disk first, so the following edit re-parsed. Staged ops replay
    add-then-edit entirely in memory, so clones must carry real spans
    (``okfile.new_record``).
    """
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)

    before = service.parse_file_view(work, registry, config)
    sec = next((s for s in before["sections"]
                if not s["is_header"] and s["records"]
                and not config.zero_fill(before["layout"], s["name"])
                and (s["max_records"] is None or len(s["records"]) < s["max_records"])), None)
    if sec is None:
        pytest.skip(f"{sample}: no non-fill section with headroom")
    anchor = sec["records"][0]["index"]
    field = next(f["name"] for f in sec["fields"] if f.get("size") and not f.get("hidden"))

    service.apply_edits(work, [], registry, config=config, backup=False, ops=[
        {"type": "add", "after_index": anchor},
        {"type": "edit", "edits": [{"section_index": sec["index"],
                                    "record_index": anchor + 1,
                                    "field": field, "value": "7"}]},
    ])

    after = service.parse_file_view(work, registry, config)
    names = {s["name"] for s in after["sections"]}
    assert "(unassigned)" not in names, "the added row lost its marker and was orphaned"
    assert [s["name"] for s in after["sections"]] == [s["name"] for s in before["sections"]]
    grown = next(s for s in after["sections"] if s["name"] == sec["name"])
    assert len(grown["records"]) == len(sec["records"]) + 1
    assert after["roundtrip_ok"]
    # the edit landed in the right field of the right row
    assert grown["records"][1]["values"][field].strip().lstrip("0") == "7"


# --------------------------------------------------------------------------- #
# Volume generation
# --------------------------------------------------------------------------- #
def _gen_spec(scope, count, with_rows=True):
    sec = next((s for s in scope["sections"] if s["fields"]), None)
    spec = {
        "count": count,
        "header_fields": ([{"name": scope["header_fields"][0]["name"], "min": 1, "max": 99}]
                          if scope["header_fields"] else []),
        "name_parts": [{"type": "token", "name": "layout"},
                       {"type": "token", "name": "key"},
                       {"type": "token", "name": "seq"}],
        "separator": "_",
    }
    if sec:
        spec["detail_fields"] = [{"section": sec["name"], "name": sec["fields"][0]["name"],
                                  "min": 1, "max": 50}]
        if with_rows:
            spec["row_counts"] = [{"section": sec["name"], "min": 1, "max": 4}]
    return spec


def test_generate_scope_excludes_key_and_locked_fields(registry, config):
    scope = service.generate_scope(DATA_DIR / "StyleHeader.OK", registry, config)
    names = [f["name"] for f in scope["header_fields"]]
    assert scope["key_field"] == "keytrol"
    assert "keytrol" not in names          # the key is assigned, never randomized
    assert "indicator" not in names        # detection signature stays locked
    assert scope["max_count"] == service.GENERATE_MAX


def test_generate_preview_writes_nothing(tmp_path, registry, config):
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    before = set(tmp_path.iterdir())
    scope = service.generate_scope(work, registry, config)

    pv = service.generate_preview(work, _gen_spec(scope, 50), registry, config)
    assert pv["count"] == 50 and pv["truncated"]
    assert len(pv["sample"]) == 5
    assert set(tmp_path.iterdir()) == before          # nothing created
    assert not Path(pv["folder"]).exists()
    assert len({r["key"] for r in pv["sample"]}) == 5  # keys already unique in preview


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_generate_volume_every_layout(tmp_path, registry, config, sample):
    """Generate a batch from each layout: unique keys, right layout, varied rows."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    template_bytes = work.read_bytes()
    scope = service.generate_scope(work, registry, config)

    res = service.generate_apply(work, _gen_spec(scope, 12), registry, config)
    folder = Path(res["folder"])
    files = sorted(folder.glob("*.OK"))

    assert res["written"] == 12 and len(files) == 12
    assert work.read_bytes() == template_bytes          # template untouched
    keys = set()
    for f in files:
        view = service.parse_file_view(f, registry, config)
        assert view["layout"] == scope["layout"]        # still detects correctly
        assert view["roundtrip_ok"]
        keys.add(view["sections"][0]["records"][0]["values"][scope["key_field"]])
    assert len(keys) == 12, "generated files must all have distinct keys"


def test_generate_second_batch_does_not_reuse_keys(tmp_path, registry, config):
    """A later batch must start above the keys of an earlier one."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    scope = service.generate_scope(work, registry, config)

    def keys_of(res):
        return {service.parse_file_view(f, registry, config)
                ["sections"][0]["records"][0]["values"]["keytrol"]
                for f in Path(res["folder"]).glob("*.OK")}

    first = keys_of(service.generate_apply(work, _gen_spec(scope, 8), registry, config))
    second = keys_of(service.generate_apply(work, _gen_spec(scope, 8), registry, config))
    assert len(first) == 8 and len(second) == 8
    assert not (first & second), "second batch reused keys from the first"


def test_generate_rejects_bad_counts(tmp_path, registry, config):
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    scope = service.generate_scope(work, registry, config)
    with pytest.raises(service.EditError):
        service.generate_apply(work, _gen_spec(scope, 0), registry, config)
    with pytest.raises(service.EditError):
        service.generate_apply(work, _gen_spec(scope, service.GENERATE_MAX + 1), registry, config)
    assert not list(tmp_path.glob("generated_*"))


def test_flask_generate_endpoints(tmp_path):
    """The three /api/generate/* routes: scope, preview (no write), apply."""
    from okgen.web.app import create_app

    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    client = create_app(data_dir=DATA_DIR).test_client()

    sc = client.post("/api/generate/scope", json={"path": str(work)})
    assert sc.status_code == 200
    scope = sc.get_json()
    assert scope["layout"] == "StyleHeader" and scope["palette"]

    spec = {"count": 6,
            "header_fields": [{"name": "dept", "min": 1, "max": 99}],
            "name_parts": [{"type": "token", "name": "layout"},
                           {"type": "token", "name": "key"}],
            "separator": "_"}

    pv = client.post("/api/generate/preview", json={"path": str(work), "spec": spec})
    assert pv.status_code == 200
    assert len(pv.get_json()["sample"]) == 5          # capped sample
    assert not list(tmp_path.glob("generated_*"))     # preview wrote nothing

    ap = client.post("/api/generate/apply", json={"path": str(work), "spec": spec})
    assert ap.status_code == 200
    body = ap.get_json()
    assert body["written"] == 6
    assert len(list(Path(body["folder"]).glob("*.OK"))) == 6

    over = client.post("/api/generate/apply",
                       json={"path": str(work), "spec": dict(spec, count=99999)})
    assert over.status_code == 422
    assert "limit" in over.get_json()["error"]


# --------------------------------------------------------------------------- #
# Marker / field-span integrity for rows created at runtime
# --------------------------------------------------------------------------- #
def _row_creation_cases(view):
    """(section, editable fields) for each populated non-header section."""
    for sec in view["sections"]:
        if sec["is_header"] or not sec["records"]:
            continue
        fields = [f for f in sec["fields"]
                  if f.get("size") and not f.get("hidden")
                  and not f.get("derived") and f.get("editable")]
        if fields:
            yield sec, fields


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_edit_touches_only_its_own_field_on_created_rows(tmp_path, registry, config, sample):
    """Every section, every field: editing a row created at runtime must change
    ONLY that field — never a neighbour, never the record-type marker.

    This is the general form of the delimited-span corruption: a row built
    outside parsing had no delimiter-walked spans, so writes landed at
    fixed-width offsets and clobbered whatever was actually there.
    """
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    base = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, base)
    view = service.parse_file_view(base, registry, config)
    section_names = [s["name"] for s in view["sections"]]

    for sec, fields in _row_creation_cases(view):
        original_row = dict(sec["records"][0]["values"])
        for f in fields:
            work = tmp_path / f"w_{sec['name']}_{f['name']}.OK"
            shutil.copy2(base, work)
            anchor = sec["records"][0]["index"]
            value = ("9" * f["size"])[:f["size"]]
            try:
                service.apply_edits(work, [], registry, config=config, backup=False, ops=[
                    {"type": "add", "after_index": anchor},
                    {"type": "edit", "edits": [{"section_index": sec["index"],
                                                "record_index": anchor + 1,
                                                "field": f["name"], "value": value}]}])
            except service.EditError as exc:
                if "limit" in str(exc):        # section full — not a span problem
                    continue
                raise

            after = service.parse_file_view(work, registry, config)
            assert [s["name"] for s in after["sections"]] == section_names, \
                f"{sec['name']}/{f['name']}: section routing changed (marker clobbered?)"
            assert after["roundtrip_ok"]
            new_row = next(s for s in after["sections"]
                           if s["name"] == sec["name"])["records"][1]["values"]
            assert new_row[f["name"]].strip().lstrip("0") in (value.strip().lstrip("0"), "")
            for other in fields:
                if other["name"] == f["name"]:
                    continue
                assert new_row[other["name"]] == original_row[other["name"]], (
                    f"{sec['name']}: editing {f['name']} also changed {other['name']}")


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_seeded_and_bulk_added_rows_take_edits_correctly(tmp_path, registry, config, sample):
    """The other two row-creation paths — seeding an empty section and bulk Add
    — must produce rows whose fields are addressable, like a parsed row."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    base = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, base)
    view = service.parse_file_view(base, registry, config)

    for sec, fields in _row_creation_cases(view):
        field = fields[0]
        value = ("9" * field["size"])[:field["size"]]

        # --- seeded row: empty the section, add the first row back, edit it ---
        seeded = tmp_path / f"seed_{sec['name']}.OK"
        shutil.copy2(base, seeded)
        emptied = _empty_out_section(seeded, sec["name"], registry, config)
        si = next(s["index"] for s in emptied["sections"] if s["name"] == sec["name"])
        try:
            service.add_record(seeded, si, [], registry, config, backup=False)
        except service.EditError as exc:
            if "no template to seed" not in str(exc):
                raise
        else:
            v1 = service.parse_file_view(seeded, registry, config)
            idx = next(s for s in v1["sections"]
                       if s["name"] == sec["name"])["records"][0]["index"]
            service.apply_edits(seeded, [{"section_index": si, "record_index": idx,
                                          "field": field["name"], "value": value}],
                                registry, config=config, backup=False)
            v2 = service.parse_file_view(seeded, registry, config)
            assert "(unassigned)" not in [s["name"] for s in v2["sections"]]
            assert v2["roundtrip_ok"]
            got = next(s for s in v2["sections"]
                       if s["name"] == sec["name"])["records"][0]["values"][field["name"]]
            assert got.strip().lstrip("0") in (value.strip().lstrip("0"), "")

        # --- bulk-added row: add 2 rows via the bulk op, then edit the last ---
        bulk = tmp_path / f"bulk_{sec['name']}.OK"
        shutil.copy2(base, bulk)
        res = service._bulk_op_eval(bulk, view["layout"], sec["name"],
                                    {"type": "add", "count": 2}, registry, config)
        if res["status"] != "change":
            continue                            # at its limit
        res["okf"].save(bulk)
        v3 = service.parse_file_view(bulk, registry, config)
        last = next(s for s in v3["sections"] if s["name"] == sec["name"])["records"][-1]
        service.apply_edits(bulk, [{"section_index": sec["index"],
                                    "record_index": last["index"],
                                    "field": field["name"], "value": value}],
                            registry, config=config, backup=False)
        v4 = service.parse_file_view(bulk, registry, config)
        assert "(unassigned)" not in [s["name"] for s in v4["sections"]]
        assert v4["roundtrip_ok"]
        got = next(s for s in v4["sections"]
                   if s["name"] == sec["name"])["records"][-1]["values"][field["name"]]
        assert got.strip().lstrip("0") in (value.strip().lstrip("0"), "")


def test_no_direct_record_construction_outside_okfile():
    """Rows must be built via okfile.new_record so delimited spans are computed.

    A bare ``Record(...)`` elsewhere silently reintroduces the marker-clobbering
    bug, and only on delimited layouts — cheap to guard statically.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "okgen"
    offenders = []
    for py in src.rglob("*.py"):
        if py.name == "okfile.py":
            continue                            # parsing + new_record live here
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if "Record(" in stripped and "new_record(" not in stripped \
                    and not stripped.startswith("#"):
                offenders.append(f"{py.name}:{i}: {stripped}")
    assert not offenders, "build rows with okfile.new_record():\n" + "\n".join(offenders)


# --------------------------------------------------------------------------- #
# Keys with a literal prefix (e.g. EUCartonLabel keytrol "C:88813")
# --------------------------------------------------------------------------- #
def _set_keytrol(path, value, registry, config):
    service.apply_edits(path, [{"section_index": 0, "record_index": 0,
                                "field": "keytrol", "value": value}],
                        registry, config=config, backup=False)
    return path


def _keytrol(path, registry, config):
    return service.parse_file_view(path, registry, config)["sections"][0]["records"][0]["values"]["keytrol"]


def test_split_key_prefix_number_suffix():
    """A key is prefix + number + suffix; only the number is ours to renumber."""
    def parts(v):
        p = service._split_key(v)
        return (p.prefix, p.value, p.suffix, p.width)

    assert parts("C:88813   ") == ("C:", 88813, "", 5)      # EUCartonLabel prefix
    assert parts("126539Q") == ("", 126539, "Q", 6)         # EUStyleHeader suffix
    assert parts("33001P3A") == ("", 33001, "P3A", 5)       # both kinds of tail
    assert parts("0008881334") == ("", 8881334, "", 10)     # plain, zero-padded
    assert parts("C:") == ("C:", None, "", 0)               # nothing to renumber
    assert parts("   ") == ("", None, "", 0)
    assert parts(None) == ("", None, "", 0)


def test_format_key_keeps_suffix_in_place():
    """The digit run keeps its width so a suffix does not drift."""
    assert service._format_key("", 126540, 11, "Q", 6) == "126540Q"
    assert service._format_key("C:", 88814, 11, "", 5) == "C:88814"
    assert service._format_key("C:", 88814, 11) == "C:000088814"   # no width -> fill
    # the digit run grows only when the number needs it, and only into free space
    assert service._format_key("", 1234567, 11, "Q", 6) == "1234567Q"
    with pytest.raises(service.EditError):
        service._format_key("", 12345678901, 11, "Q", 6)           # no room left


def test_make_unique_keeps_key_prefix(tmp_path, registry, config):
    """Duplicate C: keys: the prefix stays, only the number is bumped."""
    def make(name, key):
        shutil.copy2(DATA_DIR / "EUCartonLabel.OK", tmp_path / name)
        return _set_keytrol(tmp_path / name, key, registry, config)

    a = make("a.OK", "C:88813")
    b = make("b.OK", "C:88813")        # duplicate of a
    c = make("c.OK", "0000123")        # different prefix space

    service.make_unique_in_folder(tmp_path, registry, config, backup=False)

    ka, kb, kc = (_keytrol(p, registry, config) for p in (a, b, c))
    assert ka.startswith("C:") and kb.startswith("C:"), "the C: prefix must survive"
    assert ka.strip() != kb.strip(), "the numbers must differ"
    assert kc.strip() == "0000123", "a plain key in another prefix space is untouched"


def test_prefixed_and_plain_keys_are_separate_numbering_spaces(tmp_path, registry, config):
    """'C:00007' and '00007' are different keys — neither forces the other to move."""
    shutil.copy2(DATA_DIR / "EUCartonLabel.OK", tmp_path / "p.OK")
    plain = _set_keytrol(tmp_path / "p.OK", "0000007", registry, config)
    shutil.copy2(DATA_DIR / "EUCartonLabel.OK", tmp_path / "q.OK")
    pref = _set_keytrol(tmp_path / "q.OK", "C:7", registry, config)

    service.make_unique_in_folder(tmp_path, registry, config, backup=False)

    assert _keytrol(plain, registry, config).strip() == "0000007"
    assert _keytrol(pref, registry, config).strip().startswith("C:")
    assert _keytrol(pref, registry, config).strip().endswith("7")


def test_save_as_does_not_touch_a_prefixed_key(tmp_path, registry, config):
    """Editing another field and saving a copy leaves the C: key exactly as-is."""
    shutil.copy2(DATA_DIR / "EUCartonLabel.OK", tmp_path / "src.OK")
    src = _set_keytrol(tmp_path / "src.OK", "C:88813", registry, config)
    before = _keytrol(src, registry, config)

    other = tmp_path / "copy.OK"
    service.apply_edits(src, [{"section_index": 0, "record_index": 0,
                               "field": "dept", "value": "77"}],
                        registry, target_path=str(other), config=config)

    assert _keytrol(src, registry, config) == before
    assert _keytrol(other, registry, config) == before


def test_paste_rekey_keeps_prefix(tmp_path, registry, config):
    """Auto-uniquify on paste renumbers within the prefix, keeping it."""
    src_dir = tmp_path / "src"; src_dir.mkdir()
    dst = tmp_path / "dst"; dst.mkdir()
    shutil.copy2(DATA_DIR / "EUCartonLabel.OK", src_dir / "s.OK")
    _set_keytrol(src_dir / "s.OK", "C:88813", registry, config)
    shutil.copy2(DATA_DIR / "EUCartonLabel.OK", dst / "existing.OK")
    _set_keytrol(dst / "existing.OK", "C:88813", registry, config)

    service.copy_files([str(src_dir / "s.OK")], dst, registry, config)

    pasted = _keytrol(dst / "s.OK", registry, config)
    assert pasted.startswith("C:")
    assert pasted.strip() != _keytrol(dst / "existing.OK", registry, config).strip()


def test_generate_inherits_template_key_prefix(tmp_path, registry, config):
    """Volume files generated from a C: template all keep C: and stay unique."""
    shutil.copy2(DATA_DIR / "EUCartonLabel.OK", tmp_path / "tmpl.OK")
    template = _set_keytrol(tmp_path / "tmpl.OK", "C:88813", registry, config)
    before = _keytrol(template, registry, config)

    res = service.generate_apply(template, {
        "count": 5,
        "name_parts": [{"type": "token", "name": "orig"}, {"type": "token", "name": "seq"}],
        "separator": "_"}, registry, config)

    keys = [_keytrol(f, registry, config) for f in Path(res["folder"]).glob("*.OK")]
    assert len(keys) == 5
    assert all(k.startswith("C:") for k in keys), keys
    assert len({k.strip() for k in keys}) == 5
    assert _keytrol(template, registry, config) == before      # template untouched


def test_make_unique_keeps_key_suffix_eustyleheader(tmp_path, registry, config):
    """EUStyleHeader keytrol = leading digits + optional letter suffix.

    Only the digits are renumbered; the suffix stays put and the digit run keeps
    its width, so 126539Q becomes 126540Q (not 0000126540Q).
    """
    for name in ("a.OK", "b.OK", "c.OK"):
        shutil.copy2(DATA_DIR / "EUStyleHeader.OK", tmp_path / name)

    before = service.parse_file_view(tmp_path / "a.OK", registry, config)
    original = before["sections"][0]["records"][0]["values"]["keytrol"].strip()
    assert original.endswith("Q"), f"fixture expected to carry a suffix, got {original!r}"

    service.make_unique_in_folder(tmp_path, registry, config, backup=False)

    keys = []
    for name in ("a.OK", "b.OK", "c.OK"):
        view = service.parse_file_view(tmp_path / name, registry, config)
        assert view["layout"] == "EUStyleHeader" and view["roundtrip_ok"]
        keys.append(view["sections"][0]["records"][0]["values"]["keytrol"].strip())

    assert original in keys, "the first file must keep its key"
    assert len(set(keys)) == 3, keys
    for k in keys:
        assert k.endswith("Q"), f"suffix lost: {k}"
        assert k[:-1].isdigit() and len(k[:-1]) == 6, f"digit run changed shape: {k}"


def test_make_unique_keeps_alphanumeric_tail_preticket(tmp_path, registry, config):
    """Preticket po '33001P3A' renumbers the leading digits, keeping 'P3A'."""
    for name in ("a.OK", "b.OK"):
        shutil.copy2(DATA_DIR / "Preticket.OK", tmp_path / name)
    before = service.parse_file_view(tmp_path / "a.OK", registry, config)
    original = before["sections"][0]["records"][0]["values"]["po"].strip()

    service.make_unique_in_folder(tmp_path, registry, config, backup=False)

    keys = [service.parse_file_view(tmp_path / n, registry, config)
            ["sections"][0]["records"][0]["values"]["po"].strip() for n in ("a.OK", "b.OK")]
    assert original in keys and len(set(keys)) == 2, keys
    tail = original.lstrip("0123456789")
    assert all(k.endswith(tail) for k in keys), keys


def test_key_spaces_are_per_prefix_and_suffix(tmp_path, registry, config):
    """'C:7', '7' and '7Q' are three distinct keys — none displaces another."""
    def make(name, key):
        shutil.copy2(DATA_DIR / "EUCartonLabel.OK", tmp_path / name)
        return _set_keytrol(tmp_path / name, key, registry, config)

    plain, pref, suff = make("a.OK", "0000007"), make("b.OK", "C:7"), make("c.OK", "7Q")
    service.make_unique_in_folder(tmp_path, registry, config, backup=False)

    assert _keytrol(plain, registry, config).strip() == "0000007"
    assert _keytrol(pref, registry, config).strip() == "C:7"
    assert _keytrol(suff, registry, config).strip() == "7Q"


# --------------------------------------------------------------------------- #
# "Random value from my list" — bulk ops and volume generation
# --------------------------------------------------------------------------- #
def test_clean_values_accepts_string_or_list():
    """Users type 'a, b, c'; the API may also be handed a real list."""
    assert service._clean_values("10, 20 ,30") == ["10", "20", "30"]
    assert service._clean_values(["A", " B ", ""]) == ["A", "B"]
    assert service._clean_values("  5  ") == ["5"]
    assert service._clean_values("10,,20") == ["10", "20"]      # blanks dropped
    assert service._clean_values("") == []
    assert service._clean_values(None) == []


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_bulk_list_op_every_layout_and_section(tmp_path, registry, config, sample):
    """The list op works on every section of every layout — header included.

    Header sections hold one record, so each FILE gets one pick; detail sections
    get a pick per ROW. Every value written must come from the user's list.
    """
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    view = service.parse_file_view(DATA_DIR / sample, registry, config)
    layout = view["layout"]

    for sec in view["sections"]:
        if not sec["records"]:
            continue
        field = next((f for f in sec["fields"]
                      if f.get("size") and f["size"] >= 2 and not f.get("hidden")
                      and not f.get("derived") and f.get("editable")), None)
        if field is None:
            continue
        allowed = ["11", "22", "33"]

        work = tmp_path / f"{sec['name']}_{sample}"
        shutil.copy2(DATA_DIR / sample, work)
        res = service._bulk_op_eval(work, layout, sec["name"],
                                    {"type": "list", "field": field["name"], "values": allowed},
                                    registry, config)
        assert res["status"] == "change", (sec["name"], field["name"], res)
        res["okf"].save(work)

        after = service.parse_file_view(work, registry, config)
        assert after["layout"] == layout and after["roundtrip_ok"]
        rows = next(s for s in after["sections"] if s["name"] == sec["name"])["records"]
        # A zero-filled section (Preticket Lane) keeps its all-zero filler rows
        # untouched — bulk field ops only write to real rows. Check just those.
        fill_managed = bool(config.zero_fill(layout, sec["name"]))
        for r in rows:
            got = r["values"][field["name"]].strip().lstrip("0") or "0"
            is_blank = all((v or "").strip("0 ") == "" for v in r["values"].values())
            if fill_managed and is_blank:
                continue                          # untouched filler row
            assert got in [a.lstrip("0") for a in allowed], \
                f"{layout}/{sec['name']}/{field['name']} wrote {got!r}, not from {allowed}"


def test_bulk_list_op_spreads_across_files(tmp_path, registry, config):
    """Across many files the picks vary, and never leave the allowed set."""
    paths = []
    for i in range(30):
        p = tmp_path / f"f{i}.OK"
        shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
        paths.append(str(p))

    seen = set()
    for p in paths:
        res = service._bulk_op_eval(Path(p), "StyleHeader", "Header",
                                    {"type": "list", "field": "dept", "values": ["11", "22", "33"]},
                                    registry, config)
        assert res["status"] == "change"
        res["okf"].save(Path(p))
        seen.add(service.parse_file_view(p, registry, config)
                 ["sections"][0]["records"][0]["values"]["dept"].strip())

    assert seen <= {"11", "22", "33"}
    assert len(seen) > 1, "30 files should not all draw the same value"


def test_bulk_list_op_rejects_empty_and_too_wide(tmp_path, registry, config):
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    original = work.read_bytes()

    empty = service._bulk_op_eval(work, "StyleHeader", "Header",
                                  {"type": "list", "field": "dept", "values": []},
                                  registry, config)
    assert empty["status"] == "error"

    wide = service._bulk_op_eval(work, "StyleHeader", "Header",
                                 {"type": "list", "field": "dept", "values": ["1", "123456"]},
                                 registry, config)
    assert wide["status"] == "too_wide"
    assert work.read_bytes() == original


def test_generate_uses_only_listed_values(tmp_path, registry, config):
    """Volume generation: listed values only, for header AND detail fields."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)

    res = service.generate_apply(work, {
        "count": 30,
        "header_fields": [{"name": "dept", "values": "11,22,33"}],
        "detail_fields": [{"section": "Size", "name": "qty", "values": ["5", "10", "15"]}],
        "name_parts": [{"type": "token", "name": "orig"}, {"type": "token", "name": "seq"}],
        "separator": "_"}, registry, config)

    depts, qtys = set(), set()
    for f in Path(res["folder"]).glob("*.OK"):
        view = service.parse_file_view(f, registry, config)
        depts.add(view["sections"][0]["records"][0]["values"]["dept"].strip())
        for r in next(s for s in view["sections"] if s["name"] == "Size")["records"]:
            qtys.add(r["values"]["qty"].strip().lstrip("0") or "0")

    assert depts <= {"11", "22", "33"} and len(depts) > 1, depts
    assert qtys <= {"5", "10", "15"} and len(qtys) > 1, qtys


def test_generate_list_takes_precedence_over_range(tmp_path, registry, config):
    """When both are given the list wins — the range can't leak other values."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    res = service.generate_apply(work, {
        "count": 20,
        "header_fields": [{"name": "dept", "min": "70", "max": "99", "values": "11,22"}],
        "name_parts": [{"type": "token", "name": "orig"}, {"type": "token", "name": "seq"}],
        "separator": "_"}, registry, config)
    seen = {service.parse_file_view(f, registry, config)
            ["sections"][0]["records"][0]["values"]["dept"].strip()
            for f in Path(res["folder"]).glob("*.OK")}
    assert seen <= {"11", "22"}, seen


def test_generate_rejects_too_wide_listed_value(tmp_path, registry, config):
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    with pytest.raises(service.EditError, match="too long"):
        service.generate_apply(work, {
            "count": 3,
            "header_fields": [{"name": "dept", "values": "11,999999"}],
            "name_parts": [{"type": "token", "name": "orig"}],
            "separator": "_"}, registry, config)
    assert not list(tmp_path.glob("generated_*"))


# --------------------------------------------------------------------------- #
# Literal fields — stored exactly as typed
# --------------------------------------------------------------------------- #
LITERAL_CASES = [
    # sample, section, field, typed value
    ("StyleHeader.OK", "Header", "message1", " HI "),
    ("StyleHeader.OK", "Header", "message2", "A  B"),
    ("StyleHeader.OK", "Header", "fact1", "  indented"),
    ("StyleHeader.OK", "Header", "item", "RED SHIRT"),
    ("StyleHeader.OK", "Header", "area", "AB"),
    ("Preticket.OK", "Lane", "message1", " X "),
    ("Preticket.OK", "Lane", "item", "TRAILING  "),
    ("EUPreticket.OK", "Lane", "message1", "AB"),
    ("EUPreticket.OK", "Lane", "message2", " C "),
    ("EUStyleHeader.OK", "Header", "description", " Gucci pumps "),
    ("EUStyleHeader.OK", "Header", "upp_weight", "12.5"),
    ("EUCartonLabel.OK", "Header", "description", "  spaced  "),
]


@pytest.mark.parametrize("sample,section,field,typed", LITERAL_CASES)
def test_literal_field_stored_exactly_as_typed(tmp_path, registry, config,
                                               sample, section, field, typed):
    """No zero-padding, no re-justifying: the value keeps its own spaces and
    only the leftover at the END is filled."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)

    view = service.parse_file_view(work, registry, config)
    sec = next(s for s in view["sections"] if s["name"] == section)
    meta = next(f for f in sec["fields"] if f["name"] == field)
    assert meta["literal"] is True, f"{field} should be configured literal"

    service.apply_edits(work, [{"section_index": sec["index"],
                                "record_index": sec["records"][0]["index"],
                                "field": field, "value": typed}],
                        registry, config=config, backup=False)

    after = service.parse_file_view(work, registry, config)
    stored = next(s for s in after["sections"]
                  if s["name"] == section)["records"][0]["values"][field]
    assert stored == typed.ljust(meta["size"]), f"{stored!r} != {typed.ljust(meta['size'])!r}"
    assert stored.startswith(typed)          # their characters, untouched, in place
    assert "0" not in stored[len(typed):]    # the fill is spaces, never zeros
    assert after["roundtrip_ok"]
    assert len(stored) == meta["size"]       # record length unchanged


def test_literal_fix_for_eupreticket_message1(tmp_path, registry, config):
    """Regression: this field's sample is '01        ', which made OkGen guess
    "zero-padded number" and store 'AB' as '00000000AB'."""
    work = tmp_path / "EUPreticket.OK"
    shutil.copy2(DATA_DIR / "EUPreticket.OK", work)
    view = service.parse_file_view(work, registry, config)
    lane = next(s for s in view["sections"] if s["name"] == "Lane")

    service.apply_edits(work, [{"section_index": lane["index"],
                                "record_index": lane["records"][0]["index"],
                                "field": "message1", "value": "AB"}],
                        registry, config=config, backup=False)

    stored = service.parse_file_view(work, registry, config)
    got = next(s for s in stored["sections"]
               if s["name"] == "Lane")["records"][0]["values"]["message1"]
    assert got == "AB        ", got
    assert not got.startswith("0")


def test_code_fields_still_zero_pad(tmp_path, registry, config):
    """dept is a code, deliberately NOT literal: 7 must still store as 07."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    assert config.is_literal("StyleHeader", "dept") is False

    service.apply_edits(work, [{"section_index": 0, "record_index": 0,
                                "field": "dept", "value": "7"}],
                        registry, config=config, backup=False)
    view = service.parse_file_view(work, registry, config)
    assert view["sections"][0]["records"][0]["values"]["dept"] == "07"


@pytest.mark.parametrize("sample,section,field", [(a, b, c) for a, b, c, _ in LITERAL_CASES])
def test_literal_field_still_enforces_width(tmp_path, registry, config, sample, section, field):
    """Too-long values are rejected, never truncated — a short record corrupts
    every field after it."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    original = work.read_bytes()
    view = service.parse_file_view(work, registry, config)
    sec = next(s for s in view["sections"] if s["name"] == section)
    size = next(f["size"] for f in sec["fields"] if f["name"] == field)

    with pytest.raises(service.EditError):
        service.apply_edits(work, [{"section_index": sec["index"],
                                    "record_index": sec["records"][0]["index"],
                                    "field": field, "value": "X" * (size + 1)}],
                            registry, config=config, backup=False)
    assert work.read_bytes() == original


def test_literal_applies_to_bulk_and_generation(tmp_path, registry, config):
    """The rule holds wherever a value is written, not just single-file edits."""
    # bulk set
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    res = service._bulk_op_eval(work, "StyleHeader", "Header",
                                {"type": "set", "field": "message1", "value": " HI "},
                                registry, config)
    assert res["status"] == "change"
    res["okf"].save(work)
    got = service.parse_file_view(work, registry, config)
    assert got["sections"][0]["records"][0]["values"]["message1"] == " HI      "[:10].ljust(10)

    # bulk "random from my list" — note the list itself is comma-separated, so
    # entries are trimmed ('10, 20' must parse as two values). The chosen value
    # is then written literally (left-aligned, space-filled, never zero-padded).
    res2 = service._bulk_op_eval(work, "StyleHeader", "Header",
                                 {"type": "list", "field": "message2", "values": [" A ", "B"]},
                                 registry, config)
    assert res2["status"] == "change"
    res2["okf"].save(work)
    v2 = service.parse_file_view(work, registry, config)
    assert v2["sections"][0]["records"][0]["values"]["message2"] in ("A         ", "B         ")

    # volume generation
    gen = service.generate_apply(work, {
        "count": 5,
        "header_fields": [{"name": "message1", "values": "X,Y"}],
        "name_parts": [{"type": "token", "name": "orig"}, {"type": "token", "name": "seq"}],
        "separator": "_"}, registry, config)
    for f in Path(gen["folder"]).glob("*.OK"):
        val = service.parse_file_view(f, registry, config)["sections"][0]["records"][0]["values"]["message1"]
        # literal: left-aligned and space-filled, never zero-padded
        assert val in ("X         ", "Y         "), val


# --------------------------------------------------------------------------- #
# Seeding the first row into an EMPTY section — must be a clean blank template
# --------------------------------------------------------------------------- #
def _empty_then_seed(work, section, registry, config):
    """Delete every row of a section, then add its first row back (seeded)."""
    while True:
        rows = next(s for s in service.parse_file_view(work, registry, config)["sections"]
                    if s["name"] == section)["records"]
        if not rows:
            break
        service.delete_record(work, rows[0]["index"], [], registry, config, backup=False)
    si = next(s["index"] for s in service.parse_file_view(work, registry, config)["sections"]
              if s["name"] == section)
    return service.add_record(work, si, [], registry, config, backup=False)


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_seeded_first_row_is_aligned_to_the_field_grid(tmp_path, registry, config, sample):
    """Adding the first row to an emptied section reproduces a real row's field
    values EXACTLY — same alignment — both in the staged preview (no save) and
    after saving. A one-char offset error (the reported 'right-moved' symptom)
    shifts every field and shows only while the added row is still unsaved.
    """
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    view = service.parse_file_view(DATA_DIR / sample, registry, config)

    for sec in view["sections"]:
        if sec["is_header"] or not sec["records"]:
            continue
        want = dict(sec["records"][0]["values"])   # a genuine first row

        work = tmp_path / f"{sec['name']}_{sample}"
        shutil.copy2(DATA_DIR / sample, work)
        while True:
            rows = next(s for s in service.parse_file_view(work, registry, config)["sections"]
                        if s["name"] == sec["name"])["records"]
            if not rows:
                break
            service.delete_record(work, rows[0]["index"], [], registry, config, backup=False)
        si = next(s["index"] for s in service.parse_file_view(work, registry, config)["sections"]
                  if s["name"] == sec["name"])

        preview = service.add_record(work, si, [], registry, config, preview=True)
        assert "(unassigned)" not in [s["name"] for s in preview["sections"]]
        pv_row = next(s for s in preview["sections"]
                      if s["name"] == sec["name"])["records"][0]["values"]
        assert pv_row == want, f"{sec['name']} preview misaligned"

        service.add_record(work, si, [], registry, config, backup=False)
        saved = service.parse_file_view(work, registry, config)
        assert saved["roundtrip_ok"]
        sv_row = next(s for s in saved["sections"]
                      if s["name"] == sec["name"])["records"][0]["values"]
        assert sv_row == want, f"{sec['name']} saved misaligned"


def test_seeded_preticket_row_not_shifted(tmp_path, registry, config):
    """Regression for the exact report: Preticket's Lane is marker-less, so its
    seed offset must be 0. A hardcoded 1 shifted the staged view — page read
    '010' instead of '001', message1 'ESSAGES01M' instead of 'MESSAGES01'."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    while True:
        rows = next(s for s in service.parse_file_view(work, registry, config)["sections"]
                    if s["name"] == "Lane")["records"]
        if not rows:
            break
        service.delete_record(work, rows[0]["index"], [], registry, config, backup=False)
    si = next(s["index"] for s in service.parse_file_view(work, registry, config)["sections"]
              if s["name"] == "Lane")

    preview = service.add_record(work, si, [], registry, config, preview=True)
    vals = next(s for s in preview["sections"] if s["name"] == "Lane")["records"][0]["values"]
    assert vals["page"] == "001", f"shifted: page={vals['page']!r}"
    assert vals["message1"] == "MESSAGES01", f"shifted: message1={vals['message1']!r}"


# --------------------------------------------------------------------------- #
# Preticket-style trailing zero-fill: >=1 real Lane row -> real + 10 filler
# --------------------------------------------------------------------------- #
def _lane_split(view):
    """(real rows, filler rows) for a Preticket Lane view."""
    lane = next(s for s in view["sections"] if s["name"] == "Lane")
    real = [r for r in lane["records"]
            if any((r["values"].get(k) or "").strip("0 ") for k in r["values"])]
    filler = [r for r in lane["records"] if r not in real]
    return real, filler


def _line_count(view):
    return view["sections"][0]["records"][0]["values"].get("line_count")


def test_fill_config(config):
    assert config.zero_fill("Preticket", "Lane") == 10     # 10 filler rows
    assert config.zero_fill("EUPreticket", "Lane") == 0    # count-only, no filler
    assert config.zero_fill("StyleHeader", "Lane") is None # not managed at all


def test_eupreticket_line_count_follows_real_rows_no_filler(tmp_path, registry, config):
    """EUPreticket has no filler rows: line_count is synced to the real detail
    count and NO all-zero rows are added or removed."""
    work = tmp_path / "EUPreticket.OK"
    shutil.copy2(DATA_DIR / "EUPreticket.OK", work)

    before = service.parse_file_view(work, registry, config)
    lane = next(s for s in before["sections"] if s["name"] == "Lane")
    n = len(lane["records"])                              # 5 real rows in the sample

    other = tmp_path / "copy.OK"
    service.apply_edits(work, [], registry, target_path=str(other), config=config)
    after = service.parse_file_view(other, registry, config)

    lane_after = next(s for s in after["sections"] if s["name"] == "Lane")
    assert len(lane_after["records"]) == n               # no rows added or removed
    assert after["sections"][0]["records"][0]["values"]["line_count"] == str(n).zfill(2)
    assert after["roundtrip_ok"]
    # not a single all-zero filler row was introduced
    for r in lane_after["records"]:
        assert any((v or "").strip("0 ") for v in r["values"].values())


def test_eupreticket_line_count_after_delete_and_keep(tmp_path, registry, config):
    """Deleting rows and bulk 'keep N' both re-sync EUPreticket line_count."""
    work = tmp_path / "EUPreticket.OK"
    shutil.copy2(DATA_DIR / "EUPreticket.OK", work)

    lane = next(s for s in service.parse_file_view(work, registry, config)["sections"]
                if s["name"] == "Lane")
    service.delete_record(work, lane["records"][0]["index"], [], registry, config, backup=False)
    v = service.parse_file_view(work, registry, config)
    n = len(next(s for s in v["sections"] if s["name"] == "Lane")["records"])
    assert v["sections"][0]["records"][0]["values"]["line_count"] == str(n).zfill(2)

    work2 = tmp_path / "EUPreticket2.OK"
    shutil.copy2(DATA_DIR / "EUPreticket.OK", work2)
    service.bulk_op_apply([str(work2)], "EUPreticket", "Lane",
                          {"type": "keep", "count": 2}, registry, config, backup=False)
    v2 = service.parse_file_view(work2, registry, config)
    lane2 = next(s for s in v2["sections"] if s["name"] == "Lane")
    assert len(lane2["records"]) == 2
    assert v2["sections"][0]["records"][0]["values"]["line_count"] == "02"


def test_fill_on_save_normalizes_to_ten(tmp_path, registry, config):
    """The sample has 4 real + 19 filler; a save standardises to 4 real + 10."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    other = tmp_path / "copy.OK"
    service.apply_edits(work, [], registry, target_path=str(other), config=config)

    real, filler = _lane_split(service.parse_file_view(other, registry, config))
    assert len(real) == 4 and len(filler) == 10
    assert _line_count(service.parse_file_view(other, registry, config)) == "04"
    assert service.parse_file_view(other, registry, config)["roundtrip_ok"]


def test_fill_is_idempotent(tmp_path, registry, config):
    """Re-saving an already-normalised file is byte-identical."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    a = tmp_path / "a.OK"
    service.apply_edits(work, [], registry, target_path=str(a), config=config)
    b = tmp_path / "b.OK"
    service.apply_edits(a, [], registry, target_path=str(b), config=config)
    assert a.read_bytes() == b.read_bytes()


def test_fill_bulk_keep_one_row(tmp_path, registry, config):
    """Bulk 'keep first 1 row' -> 1 real + 10 filler."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    res = service.bulk_op_apply([str(work)], "Preticket", "Lane",
                                {"type": "keep", "count": 1}, registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    real, filler = _lane_split(service.parse_file_view(work, registry, config))
    assert len(real) == 1 and len(filler) == 10
    assert _line_count(service.parse_file_view(work, registry, config)) == "01"


def test_fill_add_first_row_to_empty_lane(tmp_path, registry, config):
    """Adding the first row to an emptied Lane seeds 1 real + 10 filler, using
    the compile-time filler_raw (the file has no blank row left to copy)."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    while True:
        rows = next(s for s in service.parse_file_view(work, registry, config)["sections"]
                    if s["name"] == "Lane")["records"]
        if not rows:
            break
        service.delete_record(work, rows[0]["index"], [], registry, config, backup=False)
    # emptied: no fill (no real rows)
    real, filler = _lane_split(service.parse_file_view(work, registry, config))
    assert len(real) == 0 and len(filler) == 0

    si = next(s["index"] for s in service.parse_file_view(work, registry, config)["sections"]
              if s["name"] == "Lane")
    view = service.add_record(work, si, [], registry, config, backup=False)
    real, filler = _lane_split(view)
    assert len(real) == 1 and len(filler) == 10
    assert view["roundtrip_ok"]


def test_fill_generation_produces_filler(tmp_path, registry, config):
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    res = service.generate_apply(work, {
        "count": 3,
        "name_parts": [{"type": "token", "name": "orig"}, {"type": "token", "name": "seq"}],
        "separator": "_"}, registry, config)
    for f in Path(res["folder"]).glob("*.OK"):
        real, filler = _lane_split(service.parse_file_view(f, registry, config))
        assert len(filler) == 10, f"{f.name}: {len(filler)} filler rows"
        assert service.parse_file_view(f, registry, config)["roundtrip_ok"]


def test_fill_filler_rows_are_all_zero(tmp_path, registry, config):
    """The appended filler rows are genuine all-zero rows (copied, not junk)."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    other = tmp_path / "copy.OK"
    service.apply_edits(work, [], registry, target_path=str(other), config=config)
    _, filler = _lane_split(service.parse_file_view(other, registry, config))
    for row in filler:
        for name, value in row["values"].items():
            assert (value or "").strip("0 ") == "", f"filler field {name}={value!r} not blank"


def test_fill_does_not_touch_non_preticket(tmp_path, registry, config):
    """A layout with no fill config is byte-identical through a no-op save."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    before = work.read_bytes()
    other = tmp_path / "copy.OK"
    service.apply_edits(work, [], registry, target_path=str(other), config=config)
    assert other.read_bytes() == before


def test_fill_line_count_zero_when_no_real_rows(tmp_path, registry, config):
    """line_count is 0 when the Lane has no real data — whether it holds only
    all-zero filler rows or is completely empty."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)

    # normalise to 4 real + 10 filler, then delete every REAL row
    saved = tmp_path / "n.OK"
    service.apply_edits(work, [], registry, target_path=str(saved), config=config)
    while True:
        real, _ = _lane_split(service.parse_file_view(saved, registry, config))
        if not real:
            break
        service.delete_record(saved, real[0]["index"], [], registry, config, backup=False)

    view = service.parse_file_view(saved, registry, config)
    real, filler = _lane_split(view)
    assert real == []                                    # only zero rows remain
    assert _line_count(view) == "00", _line_count(view)  # count reflects zero real

    # a completely empty Lane also reports 0
    work2 = tmp_path / "Preticket2.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work2)
    while True:
        rows = next(s for s in service.parse_file_view(work2, registry, config)["sections"]
                    if s["name"] == "Lane")["records"]
        if not rows:
            break
        service.delete_record(work2, rows[0]["index"], [], registry, config, backup=False)
    empty_saved = tmp_path / "e.OK"
    service.apply_edits(work2, [], registry, target_path=str(empty_saved), config=config)
    assert _line_count(service.parse_file_view(empty_saved, registry, config)) == "00"


def test_fill_bulk_field_op_leaves_filler_untouched(tmp_path, registry, config):
    """A bulk field-set on Preticket Lane writes only to REAL rows; the all-zero
    filler stays all-zero (and is not miscounted as real)."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    res = service.bulk_op_apply([str(work)], "Preticket", "Lane",
                                {"type": "set", "field": "message1", "value": "HELLO"},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"

    real, filler = _lane_split(service.parse_file_view(work, registry, config))
    assert len(real) == 4 and len(filler) == 10
    assert _line_count(service.parse_file_view(work, registry, config)) == "04"
    # every real row got HELLO; every filler row stayed blank
    for r in real:
        assert r["values"]["message1"].strip() == "HELLO"
    for r in filler:
        assert r["values"]["message1"].strip("0 ") == ""


def test_fill_bulk_header_edit_normalizes(tmp_path, registry, config):
    """A bulk HEADER set-value on a Preticket also enforces real + 10 filler."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    res = service.bulk_apply([str(work)], "Preticket", "dept", "77",
                             registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    real, filler = _lane_split(service.parse_file_view(work, registry, config))
    assert len(real) == 4 and len(filler) == 10
    assert _line_count(service.parse_file_view(work, registry, config)) == "04"


@pytest.mark.parametrize("rows_spec", [None, {"min": 2, "max": 5}])
def test_fill_generation_line_count_matches_real_rows(tmp_path, registry, config, rows_spec):
    """Generation (with and without row-count variation) always yields
    real + 10 filler, and line_count equals the real-row count."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    spec = {"count": 4,
            "detail_fields": [{"section": "Lane", "name": "qty", "min": 1, "max": 9}],
            "name_parts": [{"type": "token", "name": "seq"}], "separator": "_"}
    if rows_spec:
        spec["row_counts"] = [{"section": "Lane", **rows_spec}]

    res = service.generate_apply(work, spec, registry, config)
    for f in Path(res["folder"]).glob("*.OK"):
        view = service.parse_file_view(f, registry, config)
        real, filler = _lane_split(view)
        assert len(filler) == 10
        assert _line_count(view) == str(len(real)).zfill(2)
        assert view["roundtrip_ok"]


def test_line_count_left_unchanged_when_over_two_digits(tmp_path, registry, config):
    """Preticket/EUPreticket line_count is 2 digits: once real rows reach 100+
    it no longer fits, so the count is LEFT AS-IS (not truncated/overflowed)."""
    # direct check of the width guard at the 99/100 boundary
    from okgen.okfile import parse_okfile
    okf = parse_okfile(DATA_DIR / "EUPreticket.OK", registry=registry)
    original = okf.records[0].get("line_count")

    service._set_count_field(okf, "EUPreticket", "Lane", 99, config)
    assert okf.records[0].get("line_count") == "99"      # 2 digits -> fits

    service._set_count_field(okf, "EUPreticket", "Lane", 100, config)
    assert okf.records[0].get("line_count") == "99"      # 3 digits -> unchanged
    service._set_count_field(okf, "EUPreticket", "Lane", 12345, config)
    assert okf.records[0].get("line_count") == "99"      # still unchanged


def test_preticket_over_99_rows_keeps_count_and_filler(tmp_path, registry, config):
    """Preticket with 100+ real rows: line_count is frozen (won't fit 2 digits),
    but the 10-row filler block and round-trip are unaffected."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    before_lc = _line_count(service.parse_file_view(work, registry, config))

    lane = next(s for s in service.parse_file_view(work, registry, config)["sections"]
                if s["name"] == "Lane")
    anchor = next(r["index"] for r in lane["records"]
                  if any((r["values"].get(k) or "").strip("0 ") for k in r["values"]))
    service.apply_edits(work, [], registry, config=config, backup=False,
                        ops=[{"type": "add", "after_index": anchor} for _ in range(100)])

    view = service.parse_file_view(work, registry, config)
    real, filler = _lane_split(view)
    assert len(real) >= 100
    assert len(filler) == 10                              # filler still maintained
    assert _line_count(view) == before_lc                # count left as-is (frozen)
    assert view["roundtrip_ok"]


# --------------------------------------------------------------------------- #
# Volume generation from MULTIPLE templates
# --------------------------------------------------------------------------- #
def _three_styleheader_templates(tmp_path, registry, config):
    paths = []
    for i, kt in enumerate(["550001", "550002", "550003"]):
        p = tmp_path / f"t{i}.OK"
        shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
        service.apply_edits(p, [{"section_index": 0, "record_index": 0,
                                 "field": "keytrol", "value": kt}],
                            registry, config=config, backup=False)
        paths.append(str(p))
    return paths


def test_generate_scope_accepts_multiple_templates(tmp_path, registry, config):
    paths = _three_styleheader_templates(tmp_path, registry, config)
    scope = service.generate_scope(paths, registry, config)
    assert scope["template_count"] == 3
    assert scope["layout"] == "StyleHeader"
    assert scope["paths"] == paths


def test_generate_from_pool_uses_all_templates_unique_keys(tmp_path, registry, config):
    paths = _three_styleheader_templates(tmp_path, registry, config)
    res = service.generate_apply(paths, {
        "count": 30,
        "name_parts": [{"type": "token", "name": "orig"}, {"type": "token", "name": "seq"}],
        "separator": "_"}, registry, config)

    files = sorted(Path(res["folder"]).glob("*.OK"))
    assert len(files) == 30
    keys, origins = set(), set()
    for f in files:
        view = service.parse_file_view(f, registry, config)
        assert view["layout"] == "StyleHeader" and view["roundtrip_ok"]
        keys.add(view["sections"][0]["records"][0]["values"]["keytrol"].strip())
        origins.add(f.name.split("_")[0])            # the 'orig' token = template stem
    assert len(keys) == 30, "keys must be unique across the whole batch"
    assert len(origins) > 1, "a pool of 3 should draw from more than one template"


def test_generate_rejects_mixed_layout_templates(tmp_path, registry, config):
    a = tmp_path / "a.OK"; shutil.copy2(DATA_DIR / "StyleHeader.OK", a)
    b = tmp_path / "b.OK"; shutil.copy2(DATA_DIR / "CartonLabel.OK", b)
    with pytest.raises(service.EditError, match="same layout"):
        service.generate_scope([str(a), str(b)], registry, config)
    with pytest.raises(service.EditError, match="same layout"):
        service.generate_apply([str(a), str(b)], {"count": 3}, registry, config)


def test_generate_single_template_backward_compatible(tmp_path, registry, config):
    """A bare path string still works (single-template path)."""
    p = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", p)
    scope = service.generate_scope(str(p), registry, config)
    assert scope["template_count"] == 1
    res = service.generate_apply(str(p), {
        "count": 4, "name_parts": [{"type": "token", "name": "seq"}], "separator": "_"},
        registry, config)
    assert res["written"] == 4


def test_generate_pool_preview_names_template(tmp_path, registry, config):
    paths = _three_styleheader_templates(tmp_path, registry, config)
    pv = service.generate_preview(paths, {
        "count": 8, "name_parts": [{"type": "token", "name": "seq"}], "separator": "_"},
        registry, config)
    assert pv["templates"] == 3
    assert all(r["template"] for r in pv["sample"])   # each row records its source


# --------------------------------------------------------------------------- #
# Explicit-blank token: '' (or "") means "set this field blank"
# --------------------------------------------------------------------------- #
def test_unquote_blank_helper():
    # quotes wrapping only spaces -> blank, in any of these forms
    for tok in ["''", "' '", "'  '", '""', '" "', "  ' '  "]:
        assert service._unquote_blank(tok) == "", tok
        assert service._is_blank_token(tok) is True, tok
    # real content (even quoted) is left alone
    for tok in ["XS", "' XS '", "' 'x", ""]:
        assert service._is_blank_token(tok) is False, tok
    assert service._unquote_blank("XS") == "XS"
    assert service._unquote_blank(None) is None


def test_clean_values_keeps_blank_token_drops_bare_empty():
    assert service._clean_values("XS, ' ', S") == ["XS", "", "S"]   # ' ' kept as blank
    assert service._clean_values("XS, '', S") == ["XS", "", "S"]    # '' also works
    assert service._clean_values("XS,,S") == ["XS", "S"]            # bare empty dropped
    assert service._clean_values("' '") == [""]
    assert service._clean_values('X,"",Y') == ["X", "", "Y"]


def test_single_edit_blank_size_via_quotes(tmp_path, registry, config):
    """A field can be set blank in a single-file edit with the '' token
    (clearing the box also works — this is the explicit form)."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    view = service.parse_file_view(work, registry, config)
    size = next(s for s in view["sections"] if s["name"] == "Size")

    service.apply_edits(work, [{"section_index": size["index"],
                                "record_index": size["records"][0]["index"],
                                "field": "size", "value": "' '"}],
                        registry, config=config, backup=False)
    after = service.parse_file_view(work, registry, config)
    val = next(s for s in after["sections"] if s["name"] == "Size")["records"][0]["values"]["size"]
    assert val.strip() == "" and len(val) == 6        # blank, still field-width
    assert after["roundtrip_ok"]


def test_bulk_set_blank_size_via_quotes(tmp_path, registry, config):
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    res = service.bulk_op_apply([str(work)], "StyleHeader", "Size",
                                {"type": "set", "field": "size", "value": "' '"},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    rows = next(s for s in service.parse_file_view(work, registry, config)["sections"]
                if s["name"] == "Size")["records"]
    assert all(r["values"]["size"].strip() == "" for r in rows)


def test_bulk_list_blank_is_a_random_choice(tmp_path, registry, config):
    """'' in a value list makes blank one of the random picks."""
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    service.bulk_op_apply([str(work)], "StyleHeader", "Size",
                          {"type": "list", "field": "size", "values": ["XS", "' '", "S"]},
                          registry, config, backup=False)
    vals = {r["values"]["size"].strip()
            for r in next(s for s in service.parse_file_view(work, registry, config)["sections"]
                          if s["name"] == "Size")["records"]}
    assert vals <= {"XS", "S", ""}                    # only allowed values
    # (can't assert blank always appears — it's random — but it's a valid choice)


def test_generation_blank_in_value_list(tmp_path, registry, config):
    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    res = service.generate_apply(work, {
        "count": 25,
        "detail_fields": [{"section": "Size", "name": "size", "values": "XS,' ',S"}],
        "name_parts": [{"type": "token", "name": "seq"}], "separator": "_"},
        registry, config)
    seen = set()
    for f in Path(res["folder"]).glob("*.OK"):
        for r in next(s for s in service.parse_file_view(f, registry, config)["sections"]
                      if s["name"] == "Size")["records"]:
            seen.add(r["values"]["size"].strip())
    assert seen <= {"XS", "S", ""}
    assert "" in seen, "over 25 files, the blank choice should appear at least once"


def test_blank_token_writes_spaces_on_numeric_field(tmp_path, registry, config):
    """The explicit blank token writes SPACES on any field — including a numeric
    one that would otherwise zero-fill — because the user typed spaces and asked
    for a visually blank field."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    view = service.parse_file_view(work, registry, config)
    lane = next(s for s in view["sections"] if s["name"] == "Lane")
    idx = lane["records"][0]["index"]

    # 'qty' is a numeric field: a plain "" would zero-fill, but '   ' -> spaces
    service.apply_edits(work, [{"section_index": lane["index"], "record_index": idx,
                                "field": "qty", "value": "'   '"}],
                        registry, config=config, backup=False)
    val = next(s for s in service.parse_file_view(work, registry, config)["sections"]
               if s["name"] == "Lane")["records"][0]["values"]["qty"]
    assert val.strip() == "" and set(val) == {" "}, repr(val)   # spaces, not zeros


@pytest.mark.parametrize("sample,section,field", [
    ("StyleHeader.OK", "Size", "size"),
    ("Preticket.OK", "Lane", "size"),
    ("EUPreticket.OK", "Lane", "size"),
    ("EUStyleHeader.OK", "Detail", "size_code"),
    ("EUCartonLabel.OK", "Detail", "size_code"),
])
def test_blank_size_every_layout(tmp_path, registry, config, sample, section, field):
    """The real merchandise size field blanks to spaces via ' ' on every layout
    that has one — single-file edit and bulk set."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    view = service.parse_file_view(DATA_DIR / sample, registry, config)
    sec = next((s for s in view["sections"] if s["name"] == section and s["records"]), None)
    if sec is None:
        pytest.skip(f"{sample} has no {section} rows")

    # single-file
    a = tmp_path / f"a_{sample}"; shutil.copy2(DATA_DIR / sample, a)
    service.apply_edits(a, [{"section_index": sec["index"],
                             "record_index": sec["records"][0]["index"],
                             "field": field, "value": "' '"}],
                        registry, config=config, backup=False)
    va = next(s for s in service.parse_file_view(a, registry, config)["sections"]
              if s["name"] == section)["records"][0]["values"][field]
    assert va.strip() == "" and set(va) == {" "}
    assert service.parse_file_view(a, registry, config)["roundtrip_ok"]

    # bulk set — in a zero-filled section (Preticket Lane) bulk ops touch only
    # the REAL rows; the all-zero filler rows keep their reference content.
    b = tmp_path / f"b_{sample}"; shutil.copy2(DATA_DIR / sample, b)
    res = service.bulk_op_apply([str(b)], view["layout"], section,
                                {"type": "set", "field": field, "value": "'   '"},
                                registry, config, backup=False)
    assert res["results"][0]["status"] == "changed"
    fill_managed = bool(config.zero_fill(view["layout"], section))
    for r in next(s for s in service.parse_file_view(b, registry, config)["sections"]
                  if s["name"] == section)["records"]:
        is_blank_row = all((v or "").strip("0 ") == "" for v in r["values"].values())
        if fill_managed and is_blank_row:
            continue                              # untouched filler row
        assert r["values"][field].strip() == "" and set(r["values"][field]) == {" "}


@pytest.mark.parametrize("sample", ["EUStyleHeader.OK", "EUCartonLabel.OK"])
def test_blank_eu_header_size_families(tmp_path, registry, config, sample):
    """The EU GTA header carries three size families — size_1..10, pack_size_*,
    phys_size_1..10 — all of which blank to spaces via the ' ' token, each field
    independently and only when named in the edit."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    families = (["size_%d" % i for i in range(1, 11)]
                + ["pack_size_%d" % i for i in range(1, 6)] + ["pack_size"]
                + ["phys_size_%d" % i for i in range(1, 11)])

    work = tmp_path / sample
    shutil.copy2(DATA_DIR / sample, work)
    header = service.parse_file_view(work, registry, config)["sections"][0]
    present = {f["name"] for f in header["fields"]}
    targets = [f for f in families if f in present]
    assert "size_1" in targets and "size_10" in targets     # the header size fields exist

    service.apply_edits(work, [{"section_index": 0, "record_index": 0,
                                "field": f, "value": "' '"} for f in targets],
                        registry, config=config, backup=False)
    after = service.parse_file_view(work, registry, config)
    vals = after["sections"][0]["records"][0]["values"]
    for f in targets:
        assert vals[f].strip() == "" and set(vals[f]) == {" "}, f"{f}={vals[f]!r}"
    assert after["roundtrip_ok"]


def test_blank_only_touches_named_fields(tmp_path, registry, config):
    """Blanking is opt-in: only the field(s) you name change; the rest of the
    record is byte-identical."""
    work = tmp_path / "EUStyleHeader.OK"
    shutil.copy2(DATA_DIR / "EUStyleHeader.OK", work)
    before = service.parse_file_view(work, registry, config)["sections"][0]["records"][0]["values"]

    service.apply_edits(work, [{"section_index": 0, "record_index": 0,
                                "field": "size_1", "value": "' '"}],
                        registry, config=config, backup=False)
    after = service.parse_file_view(work, registry, config)["sections"][0]["records"][0]["values"]

    assert after["size_1"].strip() == "" and set(after["size_1"]) == {" "}
    for name, val in before.items():
        if name == "size_1":
            continue
        assert after[name] == val, f"{name} changed unexpectedly: {val!r} -> {after[name]!r}"


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_blank_every_size_field_all_sections(tmp_path, registry, config, sample):
    """Exhaustive: for EVERY layout, discover EVERY size-like editable field in
    EVERY section (Header / Lane / Size / Detail / store …) and assert it blanks
    to spaces via the ' ' token — both single-file edit and bulk set. A field
    that ships already-blank is a correct no-op (still ends blank)."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    view = service.parse_file_view(DATA_DIR / sample, registry, config)
    layout = view["layout"]

    def spaces(v):
        return v.strip() == "" and set(v) == {" "}

    tested = 0
    for sec in view["sections"]:
        size_fields = [f for f in sec["fields"]
                       if "size" in f["name"].lower() and f.get("size")
                       and not f.get("hidden") and not f.get("derived") and f.get("editable")]
        if not sec["records"] or not size_fields:
            continue
        for f in size_fields:
            tested += 1
            fname = f["name"]

            # single-file edit blanks one row's field; others stay put
            a = tmp_path / f"a_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, a)
            s = next(x for x in service.parse_file_view(a, registry, config)["sections"]
                     if x["name"] == sec["name"])
            service.apply_edits(a, [{"section_index": sec["index"],
                                     "record_index": s["records"][0]["index"],
                                     "field": fname, "value": "' '"}],
                                registry, config=config, backup=False)
            av = service.parse_file_view(a, registry, config)
            got = next(x for x in av["sections"] if x["name"] == sec["name"])["records"][0]["values"][fname]
            assert spaces(got), f"{layout}/{sec['name']}/{fname} single -> {got!r}"
            assert av["roundtrip_ok"]

            # bulk set blanks the field on every (real) row
            b = tmp_path / f"b_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, b)
            res = service.bulk_op_apply([str(b)], layout, sec["name"],
                                        {"type": "set", "field": fname, "value": "'   '"},
                                        registry, config, backup=False)
            assert res["results"][0]["status"] in ("changed", "unchanged")
            fm = bool(config.zero_fill(layout, sec["name"]))
            for r in next(x for x in service.parse_file_view(b, registry, config)["sections"]
                          if x["name"] == sec["name"])["records"]:
                is_filler = all((v or "").strip("0 ") == "" for v in r["values"].values())
                if fm and is_filler:
                    continue                       # filler rows aren't touched by bulk
                assert spaces(r["values"][fname]), f"{layout}/{sec['name']}/{fname} bulk -> {r['values'][fname]!r}"

    # every layout except DistLabels has at least one size field
    if layout != "DistLabels":
        assert tested > 0, f"{layout}: no size field exercised"


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_blank_every_message_field_all_sections(tmp_path, registry, config, sample):
    """Exhaustive, mirroring the size sweep: for EVERY layout, discover EVERY
    message-like editable field in EVERY section (message1/message2 on
    StyleHeader/Preticket/EUPreticket; message_code_1/2 on the EU GTA Detail)
    and assert it blanks to spaces via the ' ' token in single-file edit, bulk
    set, bulk 'random from list' (blank as a choice), and volume generation."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    view = service.parse_file_view(DATA_DIR / sample, registry, config)
    layout = view["layout"]

    def spaces(v):
        return v.strip() == "" and set(v) == {" "}

    def is_filler(r):
        return all((v or "").strip("0 ") == "" for v in r["values"].values())

    for sec in view["sections"]:
        fields = [f for f in sec["fields"]
                  if "message" in f["name"].lower() and f.get("size")
                  and not f.get("hidden") and not f.get("derived") and f.get("editable")]
        if not sec["records"] or not fields:
            continue
        fm = bool(config.zero_fill(layout, sec["name"]))
        for f in fields:
            fname = f["name"]

            # single-file edit
            a = tmp_path / f"a_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, a)
            s = next(x for x in service.parse_file_view(a, registry, config)["sections"]
                     if x["name"] == sec["name"])
            service.apply_edits(a, [{"section_index": sec["index"],
                                     "record_index": s["records"][0]["index"],
                                     "field": fname, "value": "' '"}],
                                registry, config=config, backup=False)
            av = service.parse_file_view(a, registry, config)
            got = next(x for x in av["sections"] if x["name"] == sec["name"])["records"][0]["values"][fname]
            assert spaces(got), f"{layout}/{sec['name']}/{fname} single -> {got!r}"
            assert av["roundtrip_ok"]

            # bulk set
            b = tmp_path / f"b_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, b)
            res = service.bulk_op_apply([str(b)], layout, sec["name"],
                                        {"type": "set", "field": fname, "value": "'   '"},
                                        registry, config, backup=False)
            assert res["results"][0]["status"] in ("changed", "unchanged")
            for r in next(x for x in service.parse_file_view(b, registry, config)["sections"]
                          if x["name"] == sec["name"])["records"]:
                if fm and is_filler(r):
                    continue
                assert spaces(r["values"][fname]), f"{layout}/{sec['name']}/{fname} bulk -> {r['values'][fname]!r}"

            # bulk 'random from list' with a blank choice
            c = tmp_path / f"c_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, c)
            service.bulk_op_apply([str(c)], layout, sec["name"],
                                  {"type": "list", "field": fname, "values": ["AA", "' '"]},
                                  registry, config, backup=False)
            vals = {r["values"][fname].strip()
                    for r in next(x for x in service.parse_file_view(c, registry, config)["sections"]
                                  if x["name"] == sec["name"])["records"]
                    if not (fm and is_filler(r))}
            assert vals <= {"AA", ""}, f"{layout}/{sec['name']}/{fname} list -> {vals}"

            # volume generation with a blank choice
            g = tmp_path / f"g_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, g)
            spec = {"count": 12, "name_parts": [{"type": "token", "name": "seq"}], "separator": "_"}
            if sec["is_header"]:
                spec["header_fields"] = [{"name": fname, "values": "AA,' '"}]
            else:
                spec["detail_fields"] = [{"section": sec["name"], "name": fname, "values": "AA,' '"}]
            res = service.generate_apply(g, spec, registry, config)
            seen = set()
            for gf in Path(res["folder"]).glob("*.OK"):
                gs = next(x for x in service.parse_file_view(gf, registry, config)["sections"]
                          if x["name"] == sec["name"])
                for r in gs["records"]:
                    if fm and is_filler(r):
                        continue
                    seen.add(r["values"][fname].strip())
            assert seen <= {"AA", ""}, f"{layout}/{sec['name']}/{fname} gen -> {seen}"


# Fields the user asked to be blank-able (mapped to real layout names). Vendor
# Style / Approx Size / Country are not present in any current layout (listed in
# field_display.yaml so they behave the day they appear).
_BLANKABLE_MISC = {"description", "universal_item", "item", "fact1", "fact2", "fact3",
                   "engnoun", "frnoun", "instructions", "frdesc", "suppress", "dsuppress"}


@pytest.mark.parametrize("sample", ALL_SAMPLES)
def test_blank_misc_text_fields_all_sections(tmp_path, registry, config, sample):
    """Description, Item, Fact1-3, Eng/Fre Noun, Instructions, French description,
    header- and line-level Suppress all blank to spaces via ' ' — single-file
    edit, bulk set, bulk 'random from list', and volume generation — on every
    layout/section where they occur."""
    if not (DATA_DIR / sample).exists():
        pytest.skip(f"no sample for {sample}")
    view = service.parse_file_view(DATA_DIR / sample, registry, config)
    layout = view["layout"]

    def spaces(v):
        return v.strip() == "" and set(v) == {" "}

    def is_filler(r):
        return all((v or "").strip("0 ") == "" for v in r["values"].values())

    for sec in view["sections"]:
        fields = [f for f in sec["fields"]
                  if f["name"] in _BLANKABLE_MISC and f.get("size")
                  and not f.get("hidden") and not f.get("derived") and f.get("editable")]
        if not sec["records"] or not fields:
            continue
        fm = bool(config.zero_fill(layout, sec["name"]))
        for f in fields:
            fname = f["name"]

            a = tmp_path / f"a_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, a)
            s = next(x for x in service.parse_file_view(a, registry, config)["sections"]
                     if x["name"] == sec["name"])
            service.apply_edits(a, [{"section_index": sec["index"],
                                     "record_index": s["records"][0]["index"],
                                     "field": fname, "value": "' '"}],
                                registry, config=config, backup=False)
            av = service.parse_file_view(a, registry, config)
            got = next(x for x in av["sections"] if x["name"] == sec["name"])["records"][0]["values"][fname]
            assert spaces(got), f"{layout}/{sec['name']}/{fname} single -> {got!r}"
            assert av["roundtrip_ok"]

            b = tmp_path / f"b_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, b)
            res = service.bulk_op_apply([str(b)], layout, sec["name"],
                                        {"type": "set", "field": fname, "value": "'   '"},
                                        registry, config, backup=False)
            assert res["results"][0]["status"] in ("changed", "unchanged")
            for r in next(x for x in service.parse_file_view(b, registry, config)["sections"]
                          if x["name"] == sec["name"])["records"]:
                if fm and is_filler(r):
                    continue
                assert spaces(r["values"][fname]), f"{layout}/{sec['name']}/{fname} bulk -> {r['values'][fname]!r}"

            c = tmp_path / f"c_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, c)
            service.bulk_op_apply([str(c)], layout, sec["name"],
                                  {"type": "list", "field": fname, "values": ["A", "' '"]},
                                  registry, config, backup=False)
            vals = {r["values"][fname].strip()
                    for r in next(x for x in service.parse_file_view(c, registry, config)["sections"]
                                  if x["name"] == sec["name"])["records"]
                    if not (fm and is_filler(r))}
            assert vals <= {"A", ""}, f"{layout}/{sec['name']}/{fname} list -> {vals}"

            g = tmp_path / f"g_{sec['name']}_{fname}_{sample}"
            shutil.copy2(DATA_DIR / sample, g)
            spec = {"count": 12, "name_parts": [{"type": "token", "name": "seq"}], "separator": "_"}
            if sec["is_header"]:
                spec["header_fields"] = [{"name": fname, "values": "A,' '"}]
            else:
                spec["detail_fields"] = [{"section": sec["name"], "name": fname, "values": "A,' '"}]
            res = service.generate_apply(g, spec, registry, config)
            seen = set()
            for gf in Path(res["folder"]).glob("*.OK"):
                gs = next(x for x in service.parse_file_view(gf, registry, config)["sections"]
                          if x["name"] == sec["name"])
                for r in gs["records"]:
                    if fm and is_filler(r):
                        continue
                    seen.add(r["values"][fname].strip())
            assert seen <= {"A", ""}, f"{layout}/{sec['name']}/{fname} gen -> {seen}"


def test_instructions_is_literal_exact(tmp_path, registry, config):
    """StyleHeader 'instructions' stores EXACTLY what the user types — leading,
    middle and trailing spaces preserved, space-padded to width, NEVER
    zero-padded (it is on the literal list, so this can't depend on the sample
    value heuristic). It exists only on StyleHeader (Header)."""
    # not present in the other layouts -> nothing to guard there
    for name in ("Preticket", "DistLabels", "CartonLabel",
                 "EUPreticket", "EUStyleHeader", "EUCartonLabel"):
        L = registry.get(name)
        assert not any(f.name == "instructions" for sec in L.sections for f in sec.fields)

    assert config.is_literal("StyleHeader", "instructions") is True

    work = tmp_path / "StyleHeader.OK"
    shutil.copy2(DATA_DIR / "StyleHeader.OK", work)
    width = next(f.size for f in registry.get("StyleHeader").sections[0].fields
                 if f.name == "instructions")

    for typed in [" HI ", "A  B", "  indented", "5", "12345", " 007 "]:
        w = tmp_path / "w.OK"
        shutil.copy2(DATA_DIR / "StyleHeader.OK", w)
        service.apply_edits(w, [{"section_index": 0, "record_index": 0,
                                 "field": "instructions", "value": typed}],
                            registry, config=config, backup=False)
        got = service.parse_file_view(w, registry, config)["sections"][0]["records"][0]["values"]["instructions"]
        assert got == typed.ljust(width), f"{typed!r} -> {got!r}"
        assert "0" not in got[len(typed):], f"tail zero-padded: {got!r}"   # spaces only
        assert service.parse_file_view(w, registry, config)["roundtrip_ok"]


def test_generation_does_not_touch_filler_rows(tmp_path, registry, config):
    """Regression: randomizing a detail field during volume generation must NOT
    write into the Preticket zero-filler rows (that would 'activate' them into
    real rows and inflate line_count). Only the real rows get values."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    res = service.generate_apply(work, {
        "count": 4,
        "detail_fields": [{"section": "Lane", "name": "size", "values": "XS,S,M"},
                          {"section": "Lane", "name": "message1", "values": "AA,BB"}],
        "name_parts": [{"type": "token", "name": "seq"}], "separator": "_"},
        registry, config)

    for f in Path(res["folder"]).glob("*.OK"):
        view = service.parse_file_view(f, registry, config)
        real, filler = _lane_split(view)
        assert len(real) == 4 and len(filler) == 10
        assert _line_count(view) == "04", _line_count(view)
        # real rows got the randomized values, from the allowed sets
        for r in real:
            assert r["values"]["size"].strip() in ("XS", "S", "M")
            assert r["values"]["message1"].strip() in ("AA", "BB")
        # filler rows are still genuine all-zero rows (untouched)
        for r in filler:
            for name, val in r["values"].items():
                assert (val or "").strip("0 ") == "", f"filler {name}={val!r} not blank"
        assert view["roundtrip_ok"]


def test_generation_row_count_variation_keeps_filler_clean(tmp_path, registry, config):
    """Row-count variation + field randomization: real rows vary and carry the
    random values, filler stays all-zero and line_count = real count."""
    work = tmp_path / "Preticket.OK"
    shutil.copy2(DATA_DIR / "Preticket.OK", work)
    res = service.generate_apply(work, {
        "count": 6,
        "detail_fields": [{"section": "Lane", "name": "size", "values": "XS,S"}],
        "row_counts": [{"section": "Lane", "min": 2, "max": 6}],
        "name_parts": [{"type": "token", "name": "seq"}], "separator": "_"},
        registry, config)

    for f in Path(res["folder"]).glob("*.OK"):
        view = service.parse_file_view(f, registry, config)
        real, filler = _lane_split(view)
        assert 2 <= len(real) <= 6
        assert len(filler) == 10
        assert _line_count(view) == str(len(real)).zfill(2)
        assert all(r["values"]["size"].strip() in ("XS", "S") for r in real)
        for r in filler:
            assert all((v or "").strip("0 ") == "" for v in r["values"].values())
