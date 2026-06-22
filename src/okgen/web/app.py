"""Flask app: serves the editor UI and the JSON API.

The JSON API (``/api/*``) is the stable seam — it is exactly what a future
React front-end would consume. The HTML page + static JS is the current UI; it
calls the same JSON endpoints. All real work is delegated to
:mod:`okgen.api.service`, which is framework-agnostic.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "OkFileDefinitions"


def create_app(data_dir=None, config_dir=None) -> Flask:
    app = Flask(__name__)
    registry = LayoutRegistry.from_dir(data_dir or _DEFAULT_DATA_DIR)
    config = Config.load(config_dir)

    def _err(message, status):
        return jsonify({"error": message}), status

    # ----- UI -----
    @app.get("/")
    def index():
        chains = {code: info.to_dict() for code, info in config.chains().items()}
        return render_template("index.html", chains=chains,
                               field_colors=config.field_colors(),
                               nicelabel_path=config.nicelabel_path() or "")

    # ----- JSON API -----
    @app.get("/favicon.ico")
    def favicon():
        path = service.Path(app.static_folder) / "favicon.ico"
        if not path.is_file():
            abort(404)
        return send_from_directory(app.static_folder, "favicon.ico")

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "layouts": registry.names()})

    @app.get("/api/chains")
    def chains():
        return jsonify({code: info.to_dict() for code, info in config.chains().items()})

    @app.get("/api/tree")
    def tree():
        directory = request.args.get("dir", "")
        try:
            return jsonify(service.build_tree(directory, config, registry))
        except NotADirectoryError as exc:
            return _err(str(exc), 400)

    @app.get("/api/parse")
    def parse():
        path = request.args.get("path", "")
        try:
            return jsonify(service.parse_file_view(path, registry, config))
        except FileNotFoundError:
            return _err(f"not found: {path}", 404)
        except ValueError as exc:
            return _err(str(exc), 400)

    @app.post("/api/save")
    def save():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.apply_edits(
                body.get("path"),
                body.get("edits", []),
                registry,
                target_path=body.get("target_path"),
                backup=body.get("backup", True),
            ))
        except service.EditError as exc:
            return _err(str(exc), 422)
        except FileNotFoundError:
            return _err(f"not found: {body.get('path')}", 404)

    @app.post("/api/record/add")
    def record_add():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.add_record(
                body.get("path"),
                int(body.get("section_index")),
                body.get("edits", []),
                registry,
                config,
                backup=body.get("backup", True),
            ))
        except service.EditError as exc:
            return _err(str(exc), 422)
        except FileNotFoundError:
            return _err(f"not found: {body.get('path')}", 404)

    @app.post("/api/record/delete")
    def record_delete():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.delete_record(
                body.get("path"),
                int(body.get("record_index")),
                body.get("edits", []),
                registry,
                config,
                backup=body.get("backup", True),
            ))
        except service.EditError as exc:
            return _err(str(exc), 422)
        except FileNotFoundError:
            return _err(f"not found: {body.get('path')}", 404)

    @app.post("/api/browse-folder")
    def browse_folder():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.browse_folder(body.get("initial")))

    @app.post("/api/file/delete")
    def file_delete():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.delete_file(body.get("path")))
        except service.EditError as exc:
            return _err(str(exc), 422)

    @app.post("/api/file/delete-batch")
    def file_delete_batch():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.delete_files(body.get("paths", [])))

    @app.post("/api/rename/scope")
    def rename_scope():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.rename_scope(body.get("paths", []), registry, config))

    @app.post("/api/rename/preview")
    def rename_preview():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.bulk_rename_preview(
            body.get("paths", []), body.get("parts", []), body.get("separator", "_"), registry, config))

    @app.post("/api/rename/apply")
    def rename_apply():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.bulk_rename_apply(
            body.get("paths", []), body.get("parts", []), body.get("separator", "_"), registry, config))

    @app.post("/api/send")
    def send():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.send_to_nicelabel(body.get("paths", []), config))
        except service.EditError as exc:
            return _err(str(exc), 422)

    @app.post("/api/file/copy")
    def file_copy():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.copy_file(body.get("src"), body.get("dst")))
        except service.EditError as exc:
            return _err(str(exc), 422)

    @app.post("/api/file/copy-batch")
    def file_copy_batch():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.copy_files(
                body.get("srcs", []), body.get("dst_dir"), registry, config))
        except service.EditError as exc:
            return _err(str(exc), 422)

    @app.post("/api/unique/folder")
    def unique_folder():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.make_unique_in_folder(body.get("path"), registry, config))
        except service.EditError as exc:
            return _err(str(exc), 422)

    @app.post("/api/unique/bulk")
    def unique_bulk():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.make_unique_files(body.get("paths", []), registry, config))

    @app.post("/api/file/rename")
    def file_rename():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.rename_file(body.get("src"), body.get("dst")))
        except service.EditError as exc:
            return _err(str(exc), 422)

    @app.post("/api/bulk/scope")
    def bulk_scope():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.bulk_scope(body.get("paths", []), registry, config))

    @app.post("/api/bulk/preview")
    def bulk_preview():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.bulk_preview(
            body.get("paths", []), body.get("layout"),
            body.get("field"), body.get("value", ""), registry, config))

    @app.post("/api/bulk/apply")
    def bulk_apply():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.bulk_apply(
            body.get("paths", []), body.get("layout"),
            body.get("field"), body.get("value", ""), registry, config,
            backup=body.get("backup", True)))

    @app.post("/api/bulk/op/preview")
    def bulk_op_preview():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.bulk_op_preview(
            body.get("paths", []), body.get("layout"), body.get("section"),
            body.get("op", {}), registry, config))

    @app.post("/api/bulk/op/apply")
    def bulk_op_apply():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.bulk_op_apply(
            body.get("paths", []), body.get("layout"), body.get("section"),
            body.get("op", {}), registry, config, backup=body.get("backup", True)))

    @app.post("/api/folder/create")
    def folder_create():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.create_folder(body.get("parent"), body.get("name")))
        except service.EditError as exc:
            return _err(str(exc), 422)

    @app.post("/api/folder/rename")
    def folder_rename():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.rename_folder(body.get("src"), body.get("dst")))
        except service.EditError as exc:
            return _err(str(exc), 422)

    @app.post("/api/folder/delete")
    def folder_delete():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.delete_folder(body.get("path")))
        except service.EditError as exc:
            return _err(str(exc), 422)

    return app
