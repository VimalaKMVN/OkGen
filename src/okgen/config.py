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

_DEFAULT_SEND_QUIPS = [
    "Beaming labels to NiceLabel…", "Folding the OK files neatly…",
    "Greasing the conveyor belt…", "Waking up the print triggers…",
    "Stamping fresh barcodes…", "Loading the delivery truck…",
    "Sprinkling magic toner…", "Negotiating with the printer…",
    "Aligning the perforations…", "Routing through the hot folder…",
    "Counting the cartons…", "Polishing the price tags…",
    "Teleporting to the DC…", "Warming up the label rollers…",
    "Convincing NiceLabel to cooperate…", "Untangling the ribbon…",
    "Double-checking the SKUs…", "Lining up the carton labels…",
]

_DEFAULT_SEND_DONE_QUIPS = [
    "Off to the printers! 🎉", "Labels are on their way!",
    "NiceLabel has the ball now.", "Delivered to the hot folder!",
    "Wheels up — bon voyage! ✈️", "Cartons loaded and rolling.",
]


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
        field_colors: Optional[Dict[str, str]] = None,
        section_counts: Optional[Dict[str, Dict[str, str]]] = None,
        nicelabel_path: Optional[str] = None,
        rename_tokens: Optional[Dict[str, List[str]]] = None,
        rename_presets: Optional[List[dict]] = None,
        nicelabel_warning: Optional[str] = None,
        send_quips: Optional[List[str]] = None,
        send_done_quips: Optional[List[str]] = None,
        regions: Optional[Dict[str, str]] = None,
    ):
        self._chains = chains
        self._rules = rules
        self._limits = limits or {}
        self._unique_fields = unique_fields or {}
        # {zone_value: region_label}, inverted from the region->zones config.
        self._regions = regions or {}
        self._field_colors = field_colors or {}
        self._section_counts = section_counts or {}
        self._nicelabel_path = nicelabel_path
        # {"derived": [...], "header_fields": [...]} or None (= show all)
        self._rename_tokens = rename_tokens
        self._rename_presets = rename_presets or []
        self._nicelabel_warning = nicelabel_warning
        self._send_quips = send_quips
        self._send_done_quips = send_done_quips

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

    # ----- field label colors -----
    def field_colors(self) -> Dict[str, str]:
        return dict(self._field_colors)

    # ----- NiceLabel destination -----
    def nicelabel_path(self) -> Optional[str]:
        return self._nicelabel_path

    def nicelabel_warning(self) -> str:
        return self._nicelabel_warning or (
            "Make sure the correct NiceLabel trigger(s) are running (started / "
            "turned ON) before sending — otherwise the files will sit unprocessed."
        )

    # ----- send-animation quips -----
    def send_quips(self) -> List[str]:
        """Status lines that rotate during a send (configured, or built-in)."""
        return list(self._send_quips) if self._send_quips else list(_DEFAULT_SEND_QUIPS)

    def send_done_quips(self) -> List[str]:
        """Celebratory lines shown on a successful send (configured, or built-in)."""
        return list(self._send_done_quips) if self._send_done_quips else list(_DEFAULT_SEND_DONE_QUIPS)

    # ----- bulk-rename token inclusion list -----
    def rename_token_groups(self) -> Optional[dict]:
        """{'derived': [...], 'header_fields': [...], 'custom': {name: text}} or None (all)."""
        if self._rename_tokens is None:
            return None
        return {
            "derived": list(self._rename_tokens.get("derived", [])),
            "header_fields": list(self._rename_tokens.get("header_fields", [])),
            "custom": dict(self._rename_tokens.get("custom", {})),
        }

    def rename_tokens(self) -> Optional[List[str]]:
        """Flat allowed-token names (derived + header_fields + custom), or None (all)."""
        groups = self.rename_token_groups()
        if groups is None:
            return None
        return groups["derived"] + groups["header_fields"] + list(groups["custom"].keys())

    def rename_presets(self) -> List[dict]:
        """Saved rename patterns: [{name, separator, parts:[{type,name|value}]}]."""
        return [dict(p, parts=list(p["parts"])) for p in self._rename_presets]

    # ----- zone -> region mapping -----
    def region(self, zone: Optional[str]) -> str:
        """Region label for a zone value, or '' if unmapped/blank."""
        if zone is None:
            return ""
        return self._regions.get(str(zone).strip(), "")

    def regions(self) -> Dict[str, str]:
        """The full {zone: region} map (copy)."""
        return dict(self._regions)

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

    # ----- section count fields -----
    def count_field(self, layout: Optional[str], section: Optional[str]) -> Optional[str]:
        """Header field that records a section's count, or None."""
        if layout is None or section is None:
            return None
        return self._section_counts.get(layout, {}).get(section)

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

        field_colors: Dict[str, str] = {}
        fc_path = cdir / "field_colors.yaml"
        if fc_path.is_file():
            data = yaml.safe_load(fc_path.read_text(encoding="utf-8")) or {}
            field_colors = {str(k): str(v) for k, v in (data.get("field_colors") or {}).items()}

        section_counts: Dict[str, Dict[str, str]] = {}
        sc_path = cdir / "section_counts.yaml"
        if sc_path.is_file():
            data = yaml.safe_load(sc_path.read_text(encoding="utf-8")) or {}
            raw = data.get("section_counts") or {}
            section_counts = {
                str(layout): {str(sec): str(fld) for sec, fld in (secs or {}).items()}
                for layout, secs in raw.items()
            }

        nicelabel_path = None
        nicelabel_warning = None
        send_quips = None
        send_done_quips = None
        nl_path = cdir / "nicelabel.yaml"
        if nl_path.is_file():
            data = yaml.safe_load(nl_path.read_text(encoding="utf-8")) or {}
            nicelabel_path = data.get("nicelabel_path") or None
            nicelabel_warning = data.get("warning") or None
            quips = data.get("quips")
            if isinstance(quips, list) and quips:
                send_quips = [str(q) for q in quips]
            done = data.get("done_quips")
            if isinstance(done, list) and done:
                send_done_quips = [str(q) for q in done]

        rename_tokens = None
        rt_path = cdir / "rename_tokens.yaml"
        if rt_path.is_file():
            data = yaml.safe_load(rt_path.read_text(encoding="utf-8")) or {}
            rt = data.get("rename_tokens")
            if isinstance(rt, dict):
                rename_tokens = {
                    "derived": [str(t) for t in (rt.get("derived") or [])],
                    "header_fields": [str(t) for t in (rt.get("header_fields") or [])],
                    "custom": {str(k): str(v) for k, v in (rt.get("custom") or {}).items()},
                }
            elif isinstance(rt, list):   # back-compat: a flat list = header fields
                rename_tokens = {"derived": [], "header_fields": [str(t) for t in rt], "custom": {}}

        regions: Dict[str, str] = {}
        regions_path = cdir / "regions.yaml"
        if regions_path.is_file():
            data = yaml.safe_load(regions_path.read_text(encoding="utf-8")) or {}
            for region, zones in (data.get("regions") or {}).items():
                for z in (zones or []):
                    regions[str(z).strip()] = str(region)

        rename_presets: List[dict] = []
        rp_path = cdir / "rename_presets.yaml"
        if rp_path.is_file():
            data = yaml.safe_load(rp_path.read_text(encoding="utf-8")) or {}
            for pr in (data.get("presets") or []):
                parts = []
                for part in (pr.get("parts") or []):
                    if isinstance(part, dict) and "text" in part:
                        parts.append({"type": "text", "value": str(part["text"])})
                    elif (isinstance(part, dict) and part.get("glue")) or part == "no_delim":
                        parts.append({"type": "glue"})
                    else:
                        parts.append({"type": "token", "name": str(part)})
                rename_presets.append({
                    "name": str(pr.get("name", "preset")),
                    "separator": str(pr.get("separator", "_")),
                    "parts": parts,
                })

        return cls(chains, rules, limits, unique_fields, field_colors,
                   section_counts, nicelabel_path, rename_tokens, rename_presets,
                   nicelabel_warning, send_quips, send_done_quips, regions)
