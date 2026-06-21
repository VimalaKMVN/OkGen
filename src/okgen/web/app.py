"""Flask app: serves the editor UI and the JSON API.

The JSON API (``/api/*``) is the stable seam — it is exactly what a future
React front-end would consume. The HTML page + static JS is the current UI; it
calls the same JSON endpoints. All real work is delegated to
:mod:`okgen.api.service`, which is framework-agnostic.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request

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
        return render_template("index.html", chains=chains)

    # ----- JSON API -----
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
            return jsonify(service.build_tree(directory, config))
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

    @app.post("/api/file/copy")
    def file_copy():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.copy_file(body.get("src"), body.get("dst")))
        except service.EditError as exc:
            return _err(str(exc), 422)

    @app.post("/api/file/rename")
    def file_rename():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.rename_file(body.get("src"), body.get("dst")))
        except service.EditError as exc:
            return _err(str(exc), 422)

    return app
