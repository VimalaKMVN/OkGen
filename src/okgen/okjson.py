"""Convert an .OK file into a Calgary JSON file — TEST DATA generation.

The two formats carry the same orders from DIFFERENT source systems, so this is
NOT a lossless re-encoding: the JSON has fields the legacy .OK never had. The
conversion therefore starts from a **real vendor sample as a template** and
overwrites only what the .OK can actually supply. Every unmapped field keeps its
template value, so an incomplete mapping degrades to "looks like a real vendor
file" rather than to a blank where data should be.

This is the first OkGen feature that CREATES data rather than preserving it, so
two rules hold throughout:

* the source .OK file is **never written** — output goes to a new folder; and
* every field is **reported with its provenance** (``ok`` / ``derived`` /
  ``template`` / ``constant``), so nothing is silently invented.

Shape notes that come from the real samples, not from guesses:

* an .OK section with no real rows leaves the template's SINGLE placeholder row
  alone — every vendor sample carries exactly one, never ``[]``; and
* a StyleHeader's ``details[0]`` is built from the .OK **header**, because the
  .OK layout has no detail rows at all.

Mapping lives in ``config/ok_to_json.yaml`` — nothing here is layout-specific.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ENCODING = "utf-8"
INDENT = 2


class ConvertError(Exception):
    """A mapping/template problem — reported per file, never half-written."""


# --------------------------------------------------------------------------- #
# Value transforms (named in config; see the list at the end of ok_to_json.yaml)
# --------------------------------------------------------------------------- #
def _trim(v) -> str:
    return "" if v is None else str(v).strip()


def _strip_zeros(v) -> str:
    s = _trim(v).lstrip("0")
    return s or "0"


def _implied_2dp(v) -> str:
    """Fixed-width minor units -> a decimal string ('002099' -> '20.99')."""
    s = _trim(v) or "0"
    if not s.isdigit():
        return s
    n = int(s)
    return f"{n // 100}.{n % 100:02d}"


def _pad9(v) -> str:
    return _trim(v).rjust(9, "0")


def _pad_left_4(v) -> str:
    """Store numbers are 4 chars — a 3-digit store gets a leading zero."""
    return _trim(v).rjust(4, "0")


def _iso_date(v) -> str:
    s = _trim(v)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def _mmyy_to_first_of_month(v) -> str:
    """'0817' (MMYY) -> '20170801'."""
    s = _trim(v)
    if len(s) != 4 or not s.isdigit():
        return s
    return f"20{s[2:4]}{s[0:2]}01"


def _yn_to_bool(v) -> str:
    return "true" if _trim(v).upper() == "Y" else "false"


def _blank_to_null(v):
    return _trim(v) or None


TRANSFORMS = {
    "trim": _trim,
    "strip_zeros": _strip_zeros,
    "implied_2dp": _implied_2dp,
    "pad9": _pad9,
    "pad_left_4": _pad_left_4,
    "iso_date": _iso_date,
    "mmyy_to_first_of_month": _mmyy_to_first_of_month,
    "yn_to_bool": _yn_to_bool,
    "blank_to_null": _blank_to_null,
}


def _apply(rule: dict, raw_value) -> Tuple[object, str]:
    """(value, provenance) for one mapped field."""
    if rule.get("raw"):
        return ("" if raw_value is None else str(raw_value)), "ok"
    name = rule.get("transform")
    if not name:
        return _trim(raw_value), "ok"
    fn = TRANSFORMS.get(name)
    if fn is None:
        raise ConvertError(f"unknown transform {name!r} in config/ok_to_json.yaml")
    return fn(raw_value), "derived"


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")




# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #
def _section_values(okf, layout, section_name: str) -> List[Dict[str, object]]:
    """Every record of a section as {field: value} dicts."""
    sec = next((s for s in layout.sections if s.name == section_name), None)
    if sec is None:
        return []
    names = [f.name for f in sec.fields]
    return [{n: r.get(n) for n in names} for r in (okf.sections().get(section_name) or [])]


def _has_real_rows(rows: List[dict]) -> bool:
    """A section of nothing but blank rows counts as EMPTY (D16's notion of a
    'real' row), so it leaves the template placeholder alone."""
    return any(any(_trim(v) for v in row.values()) for row in rows)


def convert(okf, layout, spec: dict, template: dict) -> Tuple[dict, List[dict]]:
    """Build the JSON document for one parsed .OK file.

    Returns ``(document, report)`` where report rows are
    ``{field, provenance, value, source}`` — the coverage record that keeps an
    unmapped field visible instead of silently blank.
    """
    doc = json.loads(json.dumps(template))          # deep copy, key order kept
    data = doc.get("data")
    if not isinstance(data, dict) or "header" not in data:
        raise ConvertError("template is not a Calgary JSON document (no data.header)")
    header = data["header"]
    report: List[dict] = []

    def note(field, prov, value, source):
        report.append({"field": field, "provenance": prov,
                       "value": value, "source": source})

    hdr_section = layout.sections[0].name
    hdr_rows = _section_values(okf, layout, hdr_section)
    if not hdr_rows:
        raise ConvertError("source file has no header record")
    H = hdr_rows[0]

    # --- header ------------------------------------------------------------
    for field, rule in (spec.get("header") or {}).items():
        src = rule.get("from")
        if src not in H:
            if rule.get("null_when_blank"):
                header[field] = None
                note(field, "null", None, "no .OK source")
            else:
                note(field, "template", header.get(field), f"no .OK field {src!r}")
            continue
        # Blankness is judged on the .OK VALUE, before any transform — an empty
        # price must read as null, not as the '0.00' a transform would invent.
        if rule.get("null_when_blank") and _is_blank(H[src]):
            header[field] = None
            note(field, "null", None, f"{src} is blank in the .OK")
            continue
        value, prov = _apply(rule, H[src])
        header[field] = value
        note(field, prov, value, src)

    # --- nested arrays -----------------------------------------------------
    populated_arrays = set()          # arrays whose rows really came from the .OK
    for arr_name, arr_spec in (spec.get("arrays") or {}).items():
        rows = _section_values(okf, layout, arr_spec.get("section", ""))
        # Fields that must show as JSON null rather than inherit the template's
        # value when the .OK has nothing for them. Applied to the placeholder
        # row too, so an .OK layout with no such section (a StyleHeader has no
        # stores) still reports them as present-but-empty rather than as data
        # borrowed from an unrelated order.
        nullable = set(arr_spec.get("null_when_blank") or [])

        mapped_fields = set(arr_spec.get("fields") or {})

        if not _has_real_rows(rows):
            # No .OK rows at all, so nothing here can have come from the file.
            placeholder = header.get(arr_name)
            if isinstance(placeholder, list) and nullable:
                for item in placeholder:
                    for field in nullable:
                        if field in item:
                            item[field] = None
            note(f"{arr_name}[]", "template",
                 f"{len(placeholder or [])} placeholder row(s)",
                 "no real rows in .OK")
            continue

        proto = (header.get(arr_name) or [{}])[0]
        out = []
        for row in rows:
            if not any(_trim(v) for v in row.values()):
                continue                              # skip blank filler rows
            item = json.loads(json.dumps(proto))      # keep the sample's shape
            for field, rule in (arr_spec.get("fields") or {}).items():
                src = rule.get("from")
                if src in row:
                    item[field], _ = _apply(rule, row[src])
            for field in nullable:
                if field not in item:
                    continue
                # Unmapped: there is no .OK source for it at all, so the
                # template's value is not this order's data — always null.
                # Mapped: null only when the .OK itself is blank.
                if field not in mapped_fields or _is_blank(item.get(field)):
                    item[field] = None
            out.append(item)
        header[arr_name] = out
        populated_arrays.add(arr_name)
        note(f"{arr_name}[]", "ok", f"{len(out)} row(s)", arr_spec.get("section"))

    # --- details[] ---------------------------------------------------------
    det_spec = spec.get("details") or {}
    if det_spec:
        from_section = det_spec.get("from_section", hdr_section)
        rows = hdr_rows if from_section == hdr_section else _section_values(
            okf, layout, from_section)
        proto = (data.get("details") or [{}])[0]
        out = []
        for row in rows:
            item = json.loads(json.dumps(proto))
            for field, rule in (det_spec.get("fields") or {}).items():
                src = rule.get("from")
                if src not in row:
                    if rule.get("null_when_blank"):
                        item[field] = None
                        if not out:
                            note(f"details.{field}", "null", None, "no .OK source")
                    continue
                if rule.get("null_when_blank") and _is_blank(row[src]):
                    item[field] = None
                    if not out:
                        note(f"details.{field}", "null", None, f"{src} is blank")
                    continue
                item[field], prov = _apply(rule, row[src])
                if not out:
                    note(f"details.{field}", prov, item[field], src)
            out.append(item)
        data["details"] = out

    # --- row counts --------------------------------------------------------
    # Computed from what was ACTUALLY emitted, not copied from the .OK header,
    # which can be stale (CartonLabel declares 38 stores and carries 91 rows).
    # Only counted when the rows really came from the .OK: a template
    # placeholder row is not a store, and counting it would claim data that
    # isn't there. `lanes`/`sizes` are excluded by not being listed in config.
    for field, source in (spec.get("counts") or {}).items():
        if source == "details":
            if not det_spec:
                continue                              # details not built here
            rows = data.get("details") or []
        else:
            if source not in populated_arrays:
                continue                              # placeholder, not real rows
            rows = header.get(source) or []
        header[field] = str(len(rows))
        note(field, "count", header[field], f"{len(rows)} {source} row(s)")

    # everything the mapping never touched keeps its template value
    mapped = set(spec.get("header") or {}) | set(spec.get("counts") or {})
    for field in header:
        if field not in mapped and not isinstance(header[field], list):
            note(field, "template", header[field], "unmapped — template value")

    return doc, report


def dumps(doc: dict) -> bytes:
    return (json.dumps(doc, indent=INDENT, ensure_ascii=False) + "\n").encode(ENCODING)


def load_template(spec: dict, config_dir: Path) -> dict:
    rel = spec.get("template")
    if not rel:
        raise ConvertError("no `template` configured for this conversion")
    path = Path(rel)
    if not path.is_absolute():
        path = Path(config_dir) / rel
    if not path.is_file():
        raise ConvertError(f"template not found: {path}")
    try:
        return json.loads(path.read_text(encoding=ENCODING))
    except json.JSONDecodeError as exc:
        raise ConvertError(f"template {path.name} is not valid JSON: {exc}")


def output_folder_name(layout_name: str, source: Optional[str], count: int) -> str:
    """Folder for a batch. The `source` token is what makes the existing
    SCAN/WMS resolution (D27) classify these files without a new mechanism —
    matching is on whole words, so 'SCAN' must stand alone."""
    parts = ["converted", layout_name]
    if source:
        parts.append(str(source).upper())
    parts.append(str(count))
    return "_".join(parts)
