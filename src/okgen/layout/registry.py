"""Load compiled layouts and index them by name for runtime use."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from okgen.layout.compiler import compile_dir
from okgen.layout.models import Layout


class LayoutRegistry:
    """In-memory set of layouts keyed by name (e.g. 'CartonLabel')."""

    def __init__(self, layouts: Dict[str, Layout]):
        self._layouts = layouts

    @classmethod
    def from_dir(cls, data_dir) -> "LayoutRegistry":
        return cls({l.name: l for l in compile_dir(Path(data_dir))})

    def get(self, name: str) -> Optional[Layout]:
        return self._layouts.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._layouts

    def __getitem__(self, name: str) -> Layout:
        return self._layouts[name]

    def names(self):
        return sorted(self._layouts)
