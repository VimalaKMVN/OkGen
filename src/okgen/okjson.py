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

* an .OK section with **no rows** emits ONE row carrying the section's tags and
  no values. The template's rows are NOT placeholders on two of the three
  layouts (CalgaryDistLabel ships 10 real stores, CalgaryCartonLabel 5), so
  keeping them handed another order's store numbers and quantities to a file
  that has none — with the row count agreeing, which made it look legitimate.
  Only CalgaryStyleHeader carries a genuinely blank single row; and
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


def _empty_row(placeholder: list, mapped_fields, nullable, empty_rows, arr_name):
    """One row carrying a section's TAGS and no values.

    Keys come from the template's own row when it has one, so the shape matches
    what a real row of this section looks like; otherwise from the fields the
    mapping knows about. Values default to JSON null, overridden per field by
    ``json_empty_rows.yaml`` (so a field declared as "" writes "") — the same
    source the JSON engine uses when a bulk op empties a section, which is what
    keeps the two paths from drifting.
    """
    keys = list(placeholder[0]) if placeholder and isinstance(placeholder[0], dict) \
        else list(dict.fromkeys(list(mapped_fields) + list(nullable)))
    declared = (empty_rows or {}).get(arr_name) or {}
    return {k: declared.get(k) for k in keys}


def convert(okf, layout, spec: dict, template: dict,
            empty_rows: Optional[Dict[str, dict]] = None) -> Tuple[dict, List[dict]]:
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
    emptied_arrays = set()            # arrays the .OK has, but with no rows
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
            # No .OK rows at all, so NOTHING here can have come from the file.
            #
            # The template's rows are not placeholders on two of the three
            # layouts — CalgaryDistLabel ships 10 real stores and
            # CalgaryCartonLabel 5, straight from the vendor sample order.
            # Keeping them emitted another order's store numbers, units and
            # quantities as if they were this file's, with `numberOfStores`
            # agreeing, so the document looked entirely legitimate. That is
            # D34's borrowed-data failure reaching through the array path.
            # (D33 assumed "every vendor sample carries exactly one placeholder
            # row"; that is true only of CalgaryStyleHeader, which is where the
            # rule was worked through.)
            #
            # So emit ONE row carrying the section's tags and no values — the
            # same shape the JSON engine writes when a bulk op empties a
            # section, so the two paths agree.
            placeholder = header.get(arr_name)
            if isinstance(placeholder, list):
                header[arr_name] = [_empty_row(placeholder, mapped_fields,
                                               nullable, empty_rows, arr_name)]
                emptied_arrays.add(arr_name)
                note(f"{arr_name}[]", "empty", "1 empty row",
                     "no rows in .OK — template rows are another order's data")
            else:
                # null / absent in the template: not an array at all, leave it.
                note(f"{arr_name}[]", "template", placeholder, "not an array")
            continue

        proto = (header.get(arr_name) or [{}])[0]
        out = []
        for row in rows:
            if not any(_trim(v) for v in row.values()):
                continue                              # skip blank filler rows
            item = json.loads(json.dumps(proto))      # keep the sample's shape
            blank_in_ok = set()
            for field, rule in (arr_spec.get("fields") or {}).items():
                src = rule.get("from")
                if src not in row or _is_blank(row[src]):
                    # Blankness is judged on the .OK VALUE, before any transform
                    # — strip_zeros would otherwise turn an absent quantity into
                    # a '0' that reads as real data (the header path's rule).
                    blank_in_ok.add(field)
                if src in row:
                    item[field], _ = _apply(rule, row[src])
            for field in nullable:
                if field not in item:
                    continue
                # Unmapped: there is no .OK source for it at all, so the
                # template's value is not this order's data — always null.
                # Mapped: null only when the .OK itself is blank.
                if field not in mapped_fields or field in blank_in_ok:
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
            if source in emptied_arrays:
                # The kept row carries tags, not data — the count is 0, not 1,
                # or the header would claim a store that does not exist.
                header[field] = "0"
                note(field, "count", "0", f"no {source} rows in .OK")
                continue
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
    """Folder for a batch, e.g. ``converted_CalgaryStyleHeader_SCAN_3``.

    The source token is a LABEL for the human reading the folder list. It used
    to be load-bearing — D27 resolved a file's source by matching SCAN/WMS in
    the folder or file name — but D38 replaced that with reading the file's own
    ``headerASNid``, and conversion emits it as null. Renaming this folder does
    not change what the files are."""
    parts = ["converted", layout_name]
    if source:
        parts.append(str(source).upper())
    parts.append(str(count))
    return "_".join(parts)
