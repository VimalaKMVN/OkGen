"""User-editable configuration: chain registry + display-label mappings.

Loaded from a config directory (default: ``<repo>/config``) containing
``chains.yaml`` and ``display.yaml``. See those files for the schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@dataclass
class ChainInfo:
    code: str
    name: str
    short: str = ""
    color: str = "#666666"
    icon: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "short": self.short,
            "color": self.color,
            "icon": self.icon,
        }


def _is_specific(criterion) -> bool:
    """A match criterion is 'specific' unless it is missing or the '*' wildcard."""
    return not (criterion is None or criterion == "*")


def _crit_matches(criterion, value: Optional[str]) -> bool:
    """True if a match criterion accepts ``value``.

    A criterion may be:
      * missing / None / "*"  -> matches anything
      * a single value        -> exact match
      * a list/tuple          -> matches if value is in the list (OR)
    """
    if not _is_specific(criterion):
        return True
    if isinstance(criterion, (list, tuple, set)):
        return value in {str(c) for c in criterion}
    return criterion == value


class Config:
    """Chain registry plus display-label rules, with specificity resolution."""

    def __init__(
        self,
        chains: Dict[str, ChainInfo],
        rules: List[dict],
        limits: Optional[Dict[str, Dict[str, int]]] = None,
        unique_fields: Optional[Dict[str, str]] = None,
    ):
        self._chains = chains
        self._rules = rules
        self._limits = limits or {}
        self._unique_fields = unique_fields or {}

    # ----- chains -----
    def chain(self, code: Optional[str]) -> Optional[ChainInfo]:
        if code is None:
            return None
        return self._chains.get(code)

    def chains(self) -> Dict[str, ChainInfo]:
        return dict(self._chains)

    def chain_name(self, code: Optional[str]) -> str:
        info = self.chain(code)
        return info.name if info else (code or "")

    # ----- display labels -----
    def options(
        self,
        field: str,
        chain: Optional[str] = None,
        layout: Optional[str] = None,
        fmt: Optional[str] = None,
    ) -> Dict[str, str]:
        """Most-specific {code: label} map for a field in the given context.

        Falls back to the chain registry for the ``chain`` field itself.
        Returns {} when no rule applies (field is free-form / not coded).
        """
        best: Optional[dict] = None
        best_score = -1
        for rule in self._rules:
            match = rule.get("match", {})
            if match.get("field") != field:
                continue
            if not _crit_matches(match.get("chain"), chain):
                continue
            if not _crit_matches(match.get("layout"), layout):
                continue
            if not _crit_matches(match.get("format"), fmt):
                continue
            score = sum(
                1 for k in ("chain", "layout", "format") if _is_specific(match.get(k))
            )
            if score > best_score:
                best_score = score
                best = rule

        if best is not None:
            return {str(k): str(v) for k, v in best.get("values", {}).items()}

        if field == "chain":
            return {code: info.name for code, info in self._chains.items()}
        return {}

    def label(
        self,
        field: str,
        code: str,
        chain: Optional[str] = None,
        layout: Optional[str] = None,
        fmt: Optional[str] = None,
    ) -> str:
        """Friendly label for a code, or the code itself if unmapped."""
        opts = self.options(field, chain=chain, layout=layout, fmt=fmt)
        return opts.get(code, code)

    # ----- unique key field -----
    def unique_field(self, layout: Optional[str]) -> Optional[str]:
        """Field that must be unique within a folder for this layout, or None."""
        if layout is None:
            return None
        return self._unique_fields.get(layout)

    # ----- record limits -----
    def max_records(self, layout: Optional[str], section: Optional[str]) -> Optional[int]:
        """Max records allowed for a section, or None for unlimited."""
        if layout is None or section is None:
            return None
        return self._limits.get(layout, {}).get(section)

    # ----- loading -----
    @classmethod
    def load(cls, config_dir=None) -> "Config":
        cdir = Path(config_dir) if config_dir is not None else _DEFAULT_CONFIG_DIR
        chains: Dict[str, ChainInfo] = {}
        chains_path = cdir / "chains.yaml"
        if chains_path.is_file():
            data = yaml.safe_load(chains_path.read_text(encoding="utf-8")) or {}
            for code, c in (data.get("chains") or {}).items():
                code = str(code)
                c = c or {}
                chains[code] = ChainInfo(
                    code=code,
                    name=c.get("name", code),
                    short=c.get("short", ""),
                    color=c.get("color", "#666666"),
                    icon=c.get("icon"),
                )

        rules: List[dict] = []
        display_path = cdir / "display.yaml"
        if display_path.is_file():
            data = yaml.safe_load(display_path.read_text(encoding="utf-8")) or {}
            rules = data.get("rules") or []

        limits: Dict[str, Dict[str, int]] = {}
        limits_path = cdir / "limits.yaml"
        if limits_path.is_file():
            data = yaml.safe_load(limits_path.read_text(encoding="utf-8")) or {}
            raw = data.get("max_records") or {}
            limits = {
                str(layout): {str(sec): int(n) for sec, n in (secs or {}).items()}
                for layout, secs in raw.items()
            }

        unique_fields: Dict[str, str] = {}
        keys_path = cdir / "keys.yaml"
        if keys_path.is_file():
            data = yaml.safe_load(keys_path.read_text(encoding="utf-8")) or {}
            unique_fields = {str(k): str(v) for k, v in (data.get("unique_fields") or {}).items()}

        return cls(chains, rules, limits, unique_fields)
