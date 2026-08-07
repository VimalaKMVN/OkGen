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


def _yaml_read_error(path: Path, exc: Exception) -> str:
    """Explain a YAML failure in terms of what the user actually typed.

    The overwhelmingly common one is a Windows path in DOUBLE quotes: YAML
    processes backslash escapes inside double quotes, so ``"D:\\NiceLabel\\TJX"``
    dies on ``\\T``. Left as a raw parser message ("found unknown escape
    character 'T'") that reads like a bug in OkGen rather than a fixable typo.
    """
    msg = f"{path.name} could not be read, so that feature is switched off: {exc}"
    if "unknown escape character" in str(exc):
        msg += ("\n  >> A Windows path inside DOUBLE quotes is the usual cause — "
                "YAML reads \\T, \\A, \\J… as escape codes. Fix it any of these ways:"
                "\n       single quotes:      'D:\\NiceLabel\\TJX GTA\\Incoming'"
                "\n       forward slashes:    \"D:/NiceLabel/TJX GTA/Incoming\""
                "\n       doubled backslashes:\"D:\\\\NiceLabel\\\\TJX GTA\\\\Incoming\"")
    return msg + f"\n  >> Fix {path}, then RESTART OkGen (config is read once at startup)."


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
        # {layout: field} for most layouts; {layout: {source: field}} for the
        # Calgary JSON layouts (see unique_field / json_sources.yaml).
        unique_fields: Optional[Dict[str, object]] = None,
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
        json_empty_rows: Optional[Dict[str, dict]] = None,
        json_seed_rows: Optional[Dict[str, dict]] = None,
        trim_trailing: Optional[List[str]] = None,
        derived_fields: Optional[Dict[str, list]] = None,
        isolated_chain_groups: Optional[List[set]] = None,
        tosca: Optional[dict] = None,
        json_sources: Optional[Dict[str, List[str]]] = None,
        json_source_default: Optional[str] = None,
        date_fields: Optional[Dict[str, Dict[str, str]]] = None,
        nicelabel_post: Optional[dict] = None,
        nicelabel_post_error: Optional[str] = None,
        ok_to_json: Optional[dict] = None,
        config_dir: Optional[str] = None,
        pad_zero_fields: Optional[Dict[str, set]] = None,
        freeform_fields: Optional[Dict[str, set]] = None,
        # Declared source per .OK layout (display only) — see layout_source().
        layout_sources: Optional[Dict[str, str]] = None,
        # {layout: [spec, ...]} header fields summed from a detail section.
        rollups: Optional[Dict[str, list]] = None,
    ):
        self._rollups = rollups or {}
        # .OK -> Calgary JSON conversion mapping (ok_to_json.yaml). `config_dir`
        # is kept because the mapping names a TEMPLATE file relative to it.
        self._pad_zero_fields = pad_zero_fields or {}
        self._freeform_fields = freeform_fields or {}
        self._ok_to_json = ok_to_json or {}
        self._config_dir = config_dir or ""
        # The JSON hand-off (HTTP POST) block — see nicelabel_post.yaml. The
        # .OK hand-off (hot-folder copy) stays in nicelabel_path above.
        self._nicelabel_post = nicelabel_post or {}
        # Why that file could not be read, if it could not be. An unparsable
        # file and an unfilled one look identical from the block alone (both
        # empty), and telling the user to "fill in the config" they just filled
        # in sends them hunting in the wrong place.
        self._nicelabel_post_error = nicelabel_post_error or ""
        # {layout|"*": {field: format}} — fields holding a moment in time.
        self._date_fields = date_fields or {}
        # {source: [name tokens]} and the fallback source, for Calgary JSON.
        self._json_sources = json_sources or {}
        self._json_source_default = json_source_default or ""
        self._layout_sources = layout_sources or {}
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
        # {layout: {section: {field: empty value}}} — what one kept row holds
        # when an operation empties a JSON array section.
        self._json_empty_rows = json_empty_rows or {}
        # {layout: {section: {field: seed value}}} — the first row added to
        # an empty JSON section (the JSON answer to `sample_raw`).
        self._json_seed_rows = json_seed_rows or {}
        # Layouts whose detail lines get trailing pad (after the record
        # terminator) trimmed on write.
        self._trim_trailing = {str(l) for l in (trim_trailing or [])}
        # {layout: [spec, ...]} computed fields not present in the raw file.
        self._derived_fields = derived_fields or {}
        # Groups of chains isolated for editing — you cannot change a chain
        # into or out of an isolated group (e.g. Europe <-> North America).
        self._isolated_chain_groups = [set(g) for g in (isolated_chain_groups or [])]

    # ----- chains -----
    # ----- .OK -> Calgary JSON conversion -----
    def conversions(self) -> dict:
        """{source .OK layout: conversion spec} from ok_to_json.yaml."""
        return dict(self._ok_to_json.get("conversions") or {})

    def conversion_for(self, layout: Optional[str]) -> Optional[dict]:
        """The conversion spec for an .OK layout, or None if it has no target."""
        return self.conversions().get(layout) if layout else None

    @property
    def config_dir(self) -> str:
        """Where the YAML was loaded from — conversion templates resolve here."""
        return self._config_dir

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
        """Index of the isolated group containing ``code``, or None if free.

        The groups are written as 2-char CODES, but a Calgary JSON file may
        carry its chain by NAME ("Winners", "Europe") — D41. Resolve first, or
        a name matches no group, reads as ungrouped, and the isolation rule
        silently passes: writing ``Europe`` onto an NA carton label was accepted
        while ``05`` was correctly refused.
        """
        c = (code or "").strip()
        info = self.chain(c)
        if info is not None:
            c = info.code
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

    def chains_like(self, code: Optional[str]) -> List[str]:
        """Every chain a file on ``code`` may legally become, including itself.

        The banners an isolated chain can move to is just itself (Europe); an
        ungrouped chain gets all the other ungrouped ones. Used wherever a chain
        is CHOSEN rather than typed — the editor dropdown and, so it cannot
        wander across the boundary, volume generation.
        """
        return sorted(c for c in self._chains if self.can_change_chain(code, c))

    # ----- display labels -----
    def options(
        self,
        field: str,
        chain: Optional[str] = None,
        layout: Optional[str] = None,
        fmt: Optional[str] = None,
        section: Optional[str] = None,
    ) -> Dict[str, str]:
        """Most-specific {code: label} map for a field in the given context.

        ``section`` disambiguates a field NAME that appears in more than one
        section of the same layout with different meanings — Calgary JSON has a
        document-level ``type`` (``styleHeaders``) in the header AND a coded
        ``type`` (1-9) on every detail row. Without it, one rule would label
        both."

        Falls back to the chain registry for the ``chain`` field itself.
        Returns {} when no rule applies (field is free-form / not coded).
        """
        # Rules are written against the 2-char chain CODE, but a Calgary JSON
        # file may carry the chain by NAME ("Winners") — resolve to the code so
        # one rule set serves both engines. A code resolves to itself.
        info = self.chain(chain)
        chain_code = info.code if info is not None else chain

        best: Optional[dict] = None
        best_score = -1
        for rule in self._rules:
            match = rule.get("match", {})
            if match.get("field") != field:
                continue
            if not _crit_matches(match.get("chain"), chain_code):
                continue
            if not _crit_matches(match.get("layout"), layout):
                continue
            if not _crit_matches(match.get("format"), fmt):
                continue
            if not _crit_matches(match.get("section"), section):
                continue
            score = sum(
                1 for k in ("chain", "layout", "format", "section")
                if _is_specific(match.get(k))
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
        section: Optional[str] = None,
    ) -> str:
        """Friendly label for a code, or the code itself if unmapped."""
        opts = self.options(field, chain=chain, layout=layout, fmt=fmt,
                            section=section)
        return opts.get(code, code)

    # ----- field label colors -----
    def field_colors(self) -> Dict[str, str]:
        return dict(self._field_colors)

    # ----- NiceLabel destination -----
    def nicelabel_path(self) -> Optional[str]:
        return self._nicelabel_path

    def nicelabel_post(self) -> dict:
        """The JSON POST hand-off config block (endpoint, credentials, folder).

        Raw on purpose — ``okgen.nicelabel_post.settings_from`` owns validating
        it, so the error messages live next to the code that needs the values.
        """
        return self._nicelabel_post

    def nicelabel_post_error(self) -> str:
        """Why nicelabel_post.yaml could not be read (empty if it was fine)."""
        return self._nicelabel_post_error

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

    # ----- typeable fields that still OFFER their known values -----
    def freeform_fields(self, layout: Optional[str]) -> set:
        """Fields the editor must let the user TYPE, even though display.yaml
        gives them a value list.

        A field with options normally renders as a dropdown, which is right when
        the list is the whole truth. It is wrong for a field whose list is one
        entry the user must be able to re-type in another capitalisation: the
        dropdown offers a single choice and there is no way to enter anything.
        Listed fields render as a text box with their known values SUGGESTED, so
        discoverability survives. What is actually allowed is still decided on
        save, never by this list. Same ``"*"``-plus-layout merge as
        :meth:`literal_fields`.
        """
        names = set(self._freeform_fields.get("*", set()))
        if layout:
            names |= set(self._freeform_fields.get(layout, set()))
        return names

    def is_freeform(self, layout: Optional[str], field: Optional[str]) -> bool:
        return bool(field) and field in self.freeform_fields(layout)

    # ----- zero-padded fields (JSON layouts have no fixed width) -----
    def pad_zero_fields(self, layout: Optional[str]) -> set:
        """Fields whose value is LEFT-padded with zeros to the field's declared
        size on every write.

        Fixed-width layouts pad by construction — a 4-char field is always 4
        chars. JSON values are trimmed strings, so a store typed as ``202``
        would be stored as ``202`` and rejected downstream, which expects
        ``0202``. Same ``"*"``-plus-layout merge as :meth:`literal_fields`.
        """
        names = set(self._pad_zero_fields.get("*", set()))
        if layout:
            names |= set(self._pad_zero_fields.get(layout, set()))
        return names

    def is_pad_zero(self, layout: Optional[str], field: Optional[str]) -> bool:
        return bool(field) and field in self.pad_zero_fields(layout)

    # ----- JSON empty-row skeletons -----
    def json_empty_row(self, layout: Optional[str], section: Optional[str],
                       fields: Optional[List[str]] = None) -> Dict[str, object]:
        """What the ONE kept row holds when a JSON array section is emptied.

        Every field of the section is present. The value is ``None`` (JSON
        null) unless ``json_empty_rows.yaml`` declares something else for it —
        so a new layout or a newly added field needs no config to behave
        sensibly, and config exists only for the exceptions.
        """
        declared = self._json_empty_rows.get(layout or "", {}).get(section or "", {})
        if fields is None:
            return dict(declared)
        return {f: declared.get(f) for f in fields}

    def has_json_empty_rows(self) -> bool:
        """Whether any layout declares one. Only used to keep the feature out
        of a config set that predates it."""
        return bool(self._json_empty_rows)

    def json_seed_row(self, layout: Optional[str],
                      section: Optional[str]) -> Dict[str, object]:
        """Declared seed values for the first row of an empty JSON section.

        Only what config states; the caller fills the rest (a temporal field
        with `now`, a pad_zeros field zero-padded, everything else blank), so a
        section with no entry here still yields a usable row.
        """
        return dict(self._json_seed_rows.get(layout or "", {}).get(section or "", {}))

    # ----- trailing zero-fill (Preticket-style filler rows) -----
    def zero_fill(self, layout: Optional[str], section: Optional[str]) -> Optional[int]:
        """How many trailing all-zero filler rows this section keeps, or None."""
        if not layout or not section:
            return None
        return self._detail_fill.get(layout, {}).get(section)

    def fill_sections(self, layout: Optional[str]) -> Dict[str, int]:
        """{section: zero-count} for every filled section of this layout."""
        return dict(self._detail_fill.get(layout, {})) if layout else {}

    def trims_trailing(self, layout: Optional[str]) -> bool:
        """True if this layout trims padding after the record terminator on
        write (Preticket's '...VENDORST\\   ' -> '...VENDORST\\')."""
        return bool(layout) and layout in self._trim_trailing

    # ----- roll-up (summed) fields -----
    def rollups(self, layout: Optional[str]) -> list:
        """Roll-up specs for this layout (list of dicts), or [].

        Each spec sums ``source`` across every real row of ``section`` into the
        header's ``field``. See config/rollup_fields.yaml for the contract.
        """
        return list(self._rollups.get(layout, [])) if layout else []

    def rollup_for_field(self, layout: Optional[str], field: str) -> Optional[dict]:
        """The spec whose header field is ``field``, or None."""
        return next((s for s in self.rollups(layout) if s.get("field") == field), None)

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
    def unique_field(self, layout: Optional[str],
                     source: Optional[str] = None) -> Optional[str]:
        """Field that must be unique within a folder for this layout, or None.

        Most layouts map to a single field name and ``source`` is ignored. The
        Calgary JSON layouts map to a ``{source: field}`` dict instead, because
        SCAN and WMS send the same structure with a different identity field;
        an unknown/missing ``source`` falls back to the configured default one.
        """
        if layout is None:
            return None
        val = self._unique_fields.get(layout)
        if isinstance(val, dict):
            return val.get(source) or val.get(self._json_source_default)
        return val

    def unique_field_candidates(self, layout: Optional[str]) -> List[str]:
        """Every field that is a key for this layout under ANY source.

        More than one only for the source-dependent JSON layouts. Duplicate
        detection compares all of them so a folder answered with the wrong
        source still surfaces a collision instead of silently passing.
        """
        if layout is None:
            return []
        val = self._unique_fields.get(layout)
        if isinstance(val, dict):
            out = []
            for f in val.values():        # dedupe, keep config order
                if f and f not in out:
                    out.append(f)
            return out
        return [val] if val else []

    # ----- date/time fields -----
    def date_format(self, layout: Optional[str], field: Optional[str]) -> Optional[str]:
        """The write format for a temporal field, or None if it isn't one.

        A layout-specific entry wins over the ``"*"`` (all layouts) one, so a
        layout whose date field differs can override the shared default.
        """
        if not field:
            return None
        for key in (layout, "*"):
            if key and key in self._date_fields:
                fmt = self._date_fields[key].get(field)
                if fmt:
                    return fmt
        return None

    def date_fields(self, layout: Optional[str]) -> Dict[str, str]:
        """Every temporal field that applies to a layout -> its format."""
        out = dict(self._date_fields.get("*", {}))
        out.update(self._date_fields.get(layout, {}) if layout else {})
        return out

    # ----- JSON source (SCAN / WMS) -----
    @property
    def json_sources(self) -> Dict[str, List[str]]:
        """{source name: [name tokens]} used to resolve a JSON file's source."""
        return dict(self._json_sources)

    @property
    def json_source_default(self) -> str:
        """Source assumed when a name carries no token and none was chosen."""
        return self._json_source_default

    def source_dependent(self, layout: Optional[str]) -> bool:
        """True when this layout's KEY actually differs between sources.

        CalgaryCartonLabel maps to ``pickListId`` under both, so its source
        changes nothing about the key. It still HAS a source — see
        :meth:`has_source`; the two questions are separate.
        """
        return len(self.unique_field_candidates(layout)) > 1

    def has_source(self, layout: Optional[str]) -> bool:
        """True when this layout carries a SCAN/WMS source at all.

        Every Calgary JSON layout does, CartonLabel included: knowing where a
        file came from is worth reporting even where it does not change the key.
        No ``.OK`` layout has one — they map to a single field, not a per-source
        dict.
        """
        return layout is not None and isinstance(self._unique_fields.get(layout), dict)

    def layout_source(self, layout: Optional[str]) -> Optional[str]:
        """The source a layout is ALWAYS from, or None if it isn't declared.

        An ``.OK`` format is emitted by exactly one system, so its source is a
        property of the layout and needs no reading: the EU/EWMS layouts are
        WMS, the NA ones SCAN (``layout_sources`` in json_sources.yaml). A
        Calgary JSON layout is deliberately absent here — both sources send the
        same structure, so only its own payload can answer (see
        :func:`okgen.jsonsource.source_from_header`).

        Display only. It never selects a key: an ``.OK`` layout maps to a single
        field in keys.yaml, which is why :meth:`source_dependent` stays False.
        """
        if layout is None:
            return None
        return self._layout_sources.get(layout)

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
            # A layout's key is either a plain field name (the 7 fixed-width /
            # delimited layouts) OR a {source: field} map for the Calgary JSON
            # layouts, whose identity field depends on where the file came
            # from. Keep the mapping shape as authored — flattening it with
            # str() would turn the dict into the string "{'SCAN': ...}".
            unique_fields = {}
            for k, v in (data.get("unique_fields") or {}).items():
                unique_fields[str(k)] = ({str(s): str(f) for s, f in v.items()}
                                         if isinstance(v, dict) else str(v))

        date_fields: Dict[str, Dict[str, str]] = {}
        df_path = cdir / "date_fields.yaml"
        if df_path.is_file():
            data = yaml.safe_load(df_path.read_text(encoding="utf-8")) or {}
            date_fields = {str(lay): {str(f): str(fmt) for f, fmt in (m or {}).items()}
                           for lay, m in (data.get("date_fields") or {}).items()}

        json_sources: Dict[str, List[str]] = {}
        json_source_default = ""
        layout_sources: Dict[str, str] = {}
        js_path = cdir / "json_sources.yaml"
        if js_path.is_file():
            data = yaml.safe_load(js_path.read_text(encoding="utf-8")) or {}
            json_sources = {str(k): [str(w) for w in (v or [])]
                            for k, v in (data.get("sources") or {}).items()}
            json_source_default = str(data.get("default", "") or "")
            layout_sources = {str(k): str(v).strip().upper()
                              for k, v in (data.get("layout_sources") or {}).items()
                              if str(v or "").strip()}

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

        # The JSON hand-off (HTTP POST). Like tosca.yaml, a malformed file must
        # NOT stop OkGen from starting — it disables just this one action. The
        # reason is KEPT, not just printed, so the UI can say what is actually
        # wrong instead of reporting an unparsable file as an unconfigured one.
        nicelabel_post: dict = {}
        nicelabel_post_error = ""
        nlp_path = cdir / "nicelabel_post.yaml"
        if nlp_path.is_file():
            try:
                nicelabel_post = yaml.safe_load(
                    nlp_path.read_text(encoding="utf-8")) or {}
                if not isinstance(nicelabel_post, dict):
                    raise ValueError("the top level must be a list of settings")
            except Exception as exc:
                nicelabel_post = {}
                nicelabel_post_error = _yaml_read_error(nlp_path, exc)
                print(f"WARNING: {nicelabel_post_error}")

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
        pad_zero_fields: Dict[str, set] = {}
        freeform_fields: Dict[str, set] = {}
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
            pad_zero_fields = {
                str(l): {str(f) for f in (fs or [])}
                for l, fs in (data.get("pad_zeros") or {}).items()
            }
            freeform_fields = {
                str(l): {str(f) for f in (fs or [])}
                for l, fs in (data.get("freeform") or {}).items()
            }

        detail_fill: Dict[str, dict] = {}
        trim_trailing: List[str] = []
        fill_path = cdir / "detail_fill.yaml"
        if fill_path.is_file():
            data = yaml.safe_load(fill_path.read_text(encoding="utf-8")) or {}
            detail_fill = {
                str(l): {str(sec): int(n) for sec, n in (secs or {}).items()}
                for l, secs in (data.get("fill") or {}).items()
            }
            trim_trailing = [str(l) for l in (data.get("trim_trailing") or [])]

        json_empty_rows: Dict[str, dict] = {}
        jer_path = cdir / "json_empty_rows.yaml"
        if jer_path.is_file():
            data = yaml.safe_load(jer_path.read_text(encoding="utf-8")) or {}
            json_empty_rows = {
                str(l): {str(sec): dict(fields or {})
                         for sec, fields in (secs or {}).items()}
                for l, secs in (data.get("empty_rows") or {}).items()
            }

        json_seed_rows: Dict[str, dict] = {}
        jsr_path = cdir / "json_seed_rows.yaml"
        if jsr_path.is_file():
            data = yaml.safe_load(jsr_path.read_text(encoding="utf-8")) or {}
            json_seed_rows = {
                str(l): {str(sec): dict(fields or {})
                         for sec, fields in (secs or {}).items()}
                for l, secs in (data.get("seed_rows") or {}).items()
            }

        derived_fields: Dict[str, list] = {}
        df2_path = cdir / "derived_fields.yaml"
        if df2_path.is_file():
            data = yaml.safe_load(df2_path.read_text(encoding="utf-8")) or {}
            raw = data.get("derived_fields") or {}
            derived_fields = {str(l): list(specs or []) for l, specs in raw.items()}

        rollups: Dict[str, list] = {}
        rf_path = cdir / "rollup_fields.yaml"
        if rf_path.is_file():
            data = yaml.safe_load(rf_path.read_text(encoding="utf-8")) or {}
            raw = data.get("rollups") or {}
            rollups = {str(l): list(specs or []) for l, specs in raw.items()}

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

        # .OK -> Calgary JSON conversion mapping. Like tosca.yaml, a malformed
        # file disables ONLY conversion and says so — it must not stop startup.
        ok_to_json: dict = {}
        o2j_path = cdir / "ok_to_json.yaml"
        if o2j_path.is_file():
            try:
                ok_to_json = yaml.safe_load(o2j_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                import sys
                print(f"WARNING: could not parse {o2j_path} — .OK->JSON conversion "
                      f"disabled. ({exc})", file=sys.stderr)
                ok_to_json = {}

        return cls(chains, rules, limits, unique_fields, field_colors,
                   section_counts, nicelabel_path, rename_tokens, rename_presets,
                   nicelabel_warning, send_quips, send_done_quips, regions,
                   hidden_fields, readonly_fields, literal_fields, detail_fill,
                   json_empty_rows, json_seed_rows, trim_trailing, derived_fields, isolated_chain_groups, tosca,
                   json_sources, json_source_default, date_fields, nicelabel_post,
                   nicelabel_post_error, ok_to_json, str(cdir),
                   pad_zero_fields=pad_zero_fields,
                   freeform_fields=freeform_fields,
                   layout_sources=layout_sources,
                   rollups=rollups)
