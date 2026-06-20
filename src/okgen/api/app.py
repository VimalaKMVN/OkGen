"""FastAPI app exposing the OkGen editor backend.

Thin HTTP layer over :mod:`okgen.api.service`. The backend runs locally and
performs file I/O by path (so the browser never has to touch the filesystem).
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from okgen.api import service
from okgen.config import Config
from okgen.layout.registry import LayoutRegistry


class Edit(BaseModel):
    section_index: int
    record_index: int
    field: str
    value: str


class SaveRequest(BaseModel):
    path: str
    edits: List[Edit] = []
    target_path: Optional[str] = None
    backup: bool = True


class PathRequest(BaseModel):
    path: str


class CopyRequest(BaseModel):
    src: str
    dst: str


def create_app(data_dir=None, config_dir=None) -> FastAPI:
    app = FastAPI(title="OkGen Editor API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    registry = LayoutRegistry.from_dir(data_dir) if data_dir else LayoutRegistry.from_dir(
        service.Path(__file__).resolve().parents[3] / "data" / "OkFileDefinitions"
    )
    config = Config.load(config_dir)

    @app.get("/api/health")
    def health():
        return {"ok": True, "layouts": registry.names()}

    @app.get("/api/chains")
    def chains():
        return {code: info.to_dict() for code, info in config.chains().items()}

    @app.get("/api/tree")
    def tree(dir: str):
        try:
            return service.build_tree(dir, config)
        except NotADirectoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/parse")
    def parse(path: str):
        try:
            return service.parse_file_view(path, registry, config)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"not found: {path}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/save")
    def save(req: SaveRequest):
        try:
            return service.apply_edits(
                req.path,
                [e.model_dump() for e in req.edits],
                registry,
                target_path=req.target_path,
                backup=req.backup,
            )
        except service.EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"not found: {req.path}")

    @app.post("/api/file/delete")
    def file_delete(req: PathRequest):
        try:
            return service.delete_file(req.path)
        except service.EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/api/file/copy")
    def file_copy(req: CopyRequest):
        try:
            return service.copy_file(req.src, req.dst)
        except service.EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/api/file/rename")
    def file_rename(req: CopyRequest):
        try:
            return service.rename_file(req.src, req.dst)
        except service.EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    return app


# Module-level app for `uvicorn okgen.api.app:app`
app = create_app()
