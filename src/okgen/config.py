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


def _cond_ok(value, cond) -> bool:
    """Evaluate a single derived-field condition against a (trimmed) value.

    ``cond`` is a dict with one operator key (``eq``/``neq``/``in``/``nin``);
    a bare scalar is treated as ``eq``. Comparisons are trimmed on both sides.
    """
    v = (value or "").strip()
    if not isinstance(cond, dict):
        return v == str(cond).strip()
    if "eq" in cond:
        return v == str(cond["eq"]).strip()
    if "neq" in cond:
        return v != str(cond["neq"]).strip()
    if "in" in cond:
        return v in [str(x).strip() for x in (cond["in"] or [])]
    if "nin" in cond:
        return v not in [str(x).strip() for x in (cond["nin"] or [])]
    return False


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
        hidden_fields: Optional[Dict[str, set]] = None,
        readonly_fields: Optional[Dict[str, set]] = None,
        literal_fields: Optional[Dict[str, set]] = None,
        detail_fill: Optional[Dict[str, dict]] = None,
        derived_fields: Optional[Dict[str, list]] = None,
        isolated_chain_groups: Optional[List[set]] = None,
        tosca: Optional[dict] = None,
    ):
        self._tosca = tosca or {}
        self._chains = chains
        # Calgary JSON files carry the chain as a code ("04") OR a name
        # ("Winners"/"HomeSense"), so resolve a name back to its banner too.
        self._chain_by_name = {
            info.name.strip().lower(): info for info in (chains or {}).values()
        }
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
        # {layout: {field_name, ...}} for editor hide / read-only display.
        self._hidden_fields = hidden_fields or {}
        self._readonly_fields = readonly_fields or {}
        self._literal_fields = literal_fields or {}
        self._detail_fill = detail_fill or {}
        # {layout: [spec, ...]} computed fields not present in the raw file.
        self._derived_fields = derived_fields or {}
        # Groups of chains isolated for editing — you cannot change a chain
        # into or out of an isolated group (e.g. Europe <-> North America).
        self._isolated_chain_groups = [set(g) for g in (isolated_chain_groups or [])]

    # ----- chains -----
    def tosca(self) -> dict:
        """The full TOSCA config block (scripts + column/mapping settings)."""
        return self._tosca

    def tosca_scripts(self) -> List[dict]:
        """Configured TOSCA scripts: [{name, workbook, data_sheet}, …]."""
        return list(self._tosca.get("scripts") or [])

    def tosca_script(self, name: str) -> Optional[dict]:
        return next((s for s in self.tosca_scripts() if s.get("name") == name), None)

    def chain(self, code: Optional[str]) -> Optional[ChainInfo]:
        if code is None:
            return None
        info = self._chains.get(code)
        if info is None:                       # accept a chain NAME (Calgary JSON)
            info = self._chain_by_name.get(str(code).strip().lower())
        return info

    def chains(self) -> Dict[str, ChainInfo]:
        return dict(self._chains)

    def chain_name(self, code: Optional[str]) -> str:
        info = self.chain(code)
        return info.name if info else (code or "")

    def _chain_group_of(self, code: Optional[str]):
        """Index of the isolated group containing ``code``, or None if free."""
        c = (code or "").strip()
        for i, group in enumerate(self._isolated_chain_groups):
            if c in group:
                return i
        return None

    def can_change_chain(self, old: Optional[str], new: Optional[str]) -> bool:
        """True if a file's chain may change from ``old`` to ``new``.

        Blocked only across an isolation boundary: you cannot move a chain into
        or out of an isolated group (e.g. Europe). Chains sharing the same group
        — or both ungrouped (the North-America chains) — may swap freely.
        """
        return self._chain_group_of(old) == self._chain_group_of(new)

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

    # ----- editor field display (hide / read-only) -----
    def hidden_fields(self, layout: Optional[str]) -> set:
        """Field names hidden from the section editor for this layout."""
        return set(self._hidden_fields.get(layout, set())) if layout else set()

    def readonly_fields(self, layout: Optional[str]) -> set:
        """Field names shown but not editable for this layout."""
        return set(self._readonly_fields.get(layout, set())) if layout else set()

    def literal_fields(self, layout: Optional[str]) -> set:
        """Fields stored EXACTLY as typed — never zero-padded or re-justified.

        The ``"*"`` key applies to every layout; a layout-specific list adds to
        it, so a free-text field keeps the same behaviour wherever it appears.
        """
        names = set(self._literal_fields.get("*", set()))
        if layout:
            names |= set(self._literal_fields.get(layout, set()))
        return names

    def is_literal(self, layout: Optional[str], field: Optional[str]) -> bool:
        return bool(field) and field in self.literal_fields(layout)

    # ----- trailing zero-fill (Preticket-style filler rows) -----
    def zero_fill(self, layout: Optional[str], section: Optional[str]) -> Optional[int]:
        """How many trailing all-zero filler rows this section keeps, or None."""
        if not layout or not section:
            return None
        return self._detail_fill.get(layout, {}).get(section)

    def fill_sections(self, layout: Optional[str]) -> Dict[str, int]:
        """{section: zero-count} for every filled section of this layout."""
        return dict(self._detail_fill.get(layout, {})) if layout else {}

    # ----- derived (computed) fields -----
    def derived_fields(self, layout: Optional[str]) -> list:
        """Derived-field specs for this layout (list of dicts), or []."""
        return list(self._derived_fields.get(layout, [])) if layout else []

    def all_derived_names(self) -> set:
        """Every derived field name across all layouts (for token handling)."""
        return {s.get("name") for specs in self._derived_fields.values() for s in specs if s.get("name")}

    def eval_derived(self, spec: dict, values: Dict[str, Optional[str]]) -> str:
        """Value of a derived field: first rule whose ``when`` fully matches.

        ``values`` maps field name -> raw slice (padding is trimmed per rule).
        """
        for rule in spec.get("rules", []):
            when = rule.get("when") or {}
            if all(_cond_ok(values.get(k), c) for k, c in when.items()):
                return str(rule.get("value", ""))
        return str(spec.get("default", ""))

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
        isolated_chain_groups: List[set] = []
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
            isolated_chain_groups = [
                {str(x) for x in (g or [])}
                for g in (data.get("isolated_chain_groups") or [])
            ]

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

        hidden_fields: Dict[str, set] = {}
        readonly_fields: Dict[str, set] = {}
        literal_fields: Dict[str, set] = {}
        fd_path = cdir / "field_display.yaml"
        if fd_path.is_file():
            data = yaml.safe_load(fd_path.read_text(encoding="utf-8")) or {}
            hidden_fields = {
                str(l): {str(f) for f in (fs or [])}
                for l, fs in (data.get("hidden") or {}).items()
            }
            readonly_fields = {
                str(l): {str(f) for f in (fs or [])}
                for l, fs in (data.get("readonly") or {}).items()
            }
            literal_fields = {
                str(l): {str(f) for f in (fs or [])}
                for l, fs in (data.get("literal") or {}).items()
            }

        detail_fill: Dict[str, dict] = {}
        fill_path = cdir / "detail_fill.yaml"
        if fill_path.is_file():
            data = yaml.safe_load(fill_path.read_text(encoding="utf-8")) or {}
            detail_fill = {
                str(l): {str(sec): int(n) for sec, n in (secs or {}).items()}
                for l, secs in (data.get("fill") or {}).items()
            }

        derived_fields: Dict[str, list] = {}
        df2_path = cdir / "derived_fields.yaml"
        if df2_path.is_file():
            data = yaml.safe_load(df2_path.read_text(encoding="utf-8")) or {}
            raw = data.get("derived_fields") or {}
            derived_fields = {str(l): list(specs or []) for l, specs in raw.items()}

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

        tosca: dict = {}
        tosca_path = cdir / "tosca.yaml"
        if tosca_path.is_file():
            # A malformed tosca.yaml must NOT stop OkGen from starting — a common
            # cause is a double-quoted Windows path (``"C:\T…"`` -> YAML reads
            # ``\T`` as a bad escape). Disable just TOSCA and warn loudly.
            try:
                tosca = yaml.safe_load(tosca_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                import sys
                print(f"WARNING: could not parse {tosca_path} — TOSCA disabled. "
                      f"({exc}). Tip: don't wrap Windows paths in DOUBLE quotes; "
                      f"use no quotes, single quotes, or forward slashes.",
                      file=sys.stderr)
                tosca = {}
            # Normalise each script's workbook + bat paths. Use the value EXACTLY
            # as given when it already points at something real (an absolute
            # Windows/UNC path, or a path relative to the working dir) — never
            # prepend the config dir to a real path. Only when it does NOT resolve
            # as-is do we try it relative to the config dir (this is what lets the
            # test fixtures use ``../tosca/…``). If neither exists, keep the given
            # value so the run-time error reports the path the user actually set.
            for scr in (tosca.get("scripts") or []):
                for key in ("workbook", "bat"):
                    val = str(scr.get(key, "") or "").strip()
                    if not val:
                        continue
                    if Path(val).exists():
                        scr[key] = val
                    else:
                        alt = cdir / val
                        scr[key] = str(alt.resolve()) if alt.exists() else val

        return cls(chains, rules, limits, unique_fields, field_colors,
                   section_counts, nicelabel_path, rename_tokens, rename_presets,
                   nicelabel_warning, send_quips, send_done_quips, regions,
                   hidden_fields, readonly_fields, literal_fields, detail_fill,
                   derived_fields, isolated_chain_groups, tosca)
