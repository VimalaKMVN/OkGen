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


def _pad6(v) -> str:
    """A SCAN price: six digits, zero-filled at the front ('4599' -> '004599').

    Every `.OK` price field is six characters wide by construction, so this
    normally passes the value through untouched — it exists for the case where
    the `.OK` pads with SPACES rather than zeros, which `_trim` would otherwise
    collapse to a short string ('  1699' -> '1699').

    A BLANK price stays blank. `CartonLabel.OK` carries `ret_price` as six
    spaces, meaning *no price* rather than a price of zero, and zero-filling
    that would invent 0.00 — the absent-vs-blank distinction D34/D39 exist to
    keep. A real '000000' is a zero and is preserved as one.
    """
    s = _trim(v)
    return s.rjust(6, "0") if s else ""


def _pad_left_4(v) -> str:
    """Store numbers are 4 chars — a 3-digit store gets a leading zero."""
    return _trim(v).rjust(4, "0")


def _iso_date(v) -> str:
    s = _trim(v)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def _current_year() -> str:
    """The full year to stamp a ladder plan with, e.g. '2026'.

    The `.OK` ladder field carries **MMDD and no year at all** (user-confirmed),
    so the year can only come from the clock. Deliberately a function rather
    than a literal, for the same reason as :func:`_current_century`: a
    hard-coded year is wrong the moment the calendar turns.

    Note the consequence, which is inherent to the rule rather than a defect:
    converting the SAME `.OK` file next year produces a different `ladderPlan`.
    """
    from datetime import datetime, timezone
    return str(datetime.now(timezone.utc).year)


def _ladder_mmdd_parts(v):
    """Split an `.OK` ladder MMDD into (MM, DD), or None if it is not one.

    ``'0000'`` is returned as a REAL answer (the "no ladder plan" case, which
    both shipped Pre-Ticket samples carry on every row) and is distinguished
    from junk by the caller. Anything that is not four digits — including the
    Pre-Ticket spec's own sample value ``'4542'``, whose month is 45 — is None.
    """
    s = _trim(v)
    if len(s) != 4 or not s.isdigit():
        return None
    mm, dd = s[0:2], s[2:4]
    if mm == "00" and dd == "00":
        return ("00", "00")                 # the explicit no-plan form
    if not (1 <= int(mm) <= 12) or not (1 <= int(dd) <= 31):
        return None
    return (mm, dd)


def _mmdd_to_mmyy(v) -> str:
    """`.OK` ladder MMDD -> the JSON's `ladderPlanMMYY`: MM + the CURRENT YY.

    ``'0829'`` -> ``'0826'`` in 2026. The name is literal — the JSON field holds
    MM and YY, not MM and DD — and the vendor files confirm it: they carry
    ``'0426'`` beside a ``ladderPlan`` of ``'20260401'``, so the second half is
    the year and not the day.

    ``'0000'`` -> ``'0000'`` (the user's call): both ladder fields go all-zero
    together, so "no ladder plan" reads the same way in each. A blank or
    unparseable value stays BLANK rather than being zero-filled, which would
    invent a plan the file does not have.
    """
    parts = _ladder_mmdd_parts(v)
    if parts is None:
        return ""
    mm, dd = parts
    if (mm, dd) == ("00", "00"):
        return "0000"
    return f"{mm}{_current_year()[2:4]}"


def _mmdd_to_plan(v) -> str:
    """`.OK` ladder MMDD -> the JSON's full `ladderPlan`: CURRENT YEAR + MMDD.

    ``'0829'`` -> ``'20260829'`` in 2026, and ``'0000'`` -> ``'00000000'`` (the
    user's call). Reproduces the vendor files exactly: their `.OK` MMDD of
    ``'0401'`` gives ``'20260401'``, which is what they carry.

    ***The day is NOT forced to the 1st.*** Every vendor row happens to be a
    first-of-month, which is what made the old rule look right; it is the .OK's
    own DD, and the vendor rows simply carry ``01`` there.
    """
    parts = _ladder_mmdd_parts(v)
    if parts is None:
        return ""
    mm, dd = parts
    if (mm, dd) == ("00", "00"):
        return "00000000"
    return f"{_current_year()}{mm}{dd}"


def _yn_to_bool(v) -> str:
    return "true" if _trim(v).upper() == "Y" else "false"


def _blank_to_null(v):
    return _trim(v) or None


# --------------------------------------------------------------------------- #
# MULTI-source transforms — one JSON field built from SEVERAL `.OK` fields.
#
# Declared with a LIST `from:`, so config still says where every character came
# from. They exist because the Pre-Ticket mapping needs two values the .OK
# splits across fields and the JSON joins back together, and inventing a
# separate config key per pair would not generalise.
#
# Each receives the raw values in the order `from:` lists them.
# --------------------------------------------------------------------------- #

def _current_century() -> str:
    """The century a 2-digit `.OK` year belongs to, e.g. '20'.

    The `.OK` carries YY and the JSON wants CCYY, and nothing in the file says
    which century — so it is TAKEN FROM TODAY (the user's call: "whatever is the
    current century"). Deliberately a function rather than a literal: hard-coding
    '20' would quietly produce wrong dates from 2100, and this is the only place
    that assumption lives.
    """
    from datetime import datetime, timezone
    return str(datetime.now(timezone.utc).year // 100).zfill(2)


def _ok_datetime_to_stamp(date8, time4) -> str:
    """`.OK` date + HHMM -> the JSON's 30-character RFC 3339 nanosecond stamp.

    ``20260804`` + ``0718`` -> ``2026-08-04T07:18:00.000000000Z``.

    The seconds and nanoseconds are ZERO-FILLED rather than stamped from the
    clock: the value is meant to say when this order was TRANSMITTED, which the
    .OK records only to the minute. Inventing a plausible fractional second
    would make a converted file look more precise than its source. (The
    alternative — a conversion-time `now` — was considered and rejected by the
    user, because it discards the HHMM the .OK does carry.)

    Anything the .OK cannot supply comes back blank rather than half-formed: a
    partial stamp would be refused by the field's own date validator anyway,
    and a clear blank is easier to spot than a wrong instant.
    """
    d, t = _trim(date8), _trim(time4)
    if len(d) != 8 or not d.isdigit():
        return ""
    if len(t) != 4 or not t.isdigit():
        t = "0000"
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}T{t[0:2]}:{t[2:4]}:00.000000000Z"


MULTI_TRANSFORMS = {
    "ok_datetime_to_stamp": _ok_datetime_to_stamp,
}


TRANSFORMS = {
    "trim": _trim,
    "strip_zeros": _strip_zeros,
    "implied_2dp": _implied_2dp,
    "pad9": _pad9,
    "pad6": _pad6,
    "pad_left_4": _pad_left_4,
    "iso_date": _iso_date,
    "mmdd_to_mmyy": _mmdd_to_mmyy,
    "mmdd_to_plan": _mmdd_to_plan,
    "yn_to_bool": _yn_to_bool,
    "blank_to_null": _blank_to_null,
}


def _is_multi(rule: dict) -> bool:
    """Whether ``from:`` names SEVERAL .OK fields rather than one."""
    return isinstance(rule.get("from"), (list, tuple))


def _apply(rule: dict, raw_value) -> Tuple[object, str]:
    """(value, provenance) for one mapped field.

    ``raw_value`` is a single value, or — when ``from:`` is a list — the list of
    values in the order config names them, which only a MULTI transform accepts.
    """
    if _is_multi(rule):
        name = rule.get("transform")
        fn = MULTI_TRANSFORMS.get(name)
        if fn is None:
            raise ConvertError(
                f"{name!r} is not a multi-source transform, but its `from:` names "
                f"{len(rule['from'])} fields — check config/ok_to_json.yaml "
                f"(available: {', '.join(sorted(MULTI_TRANSFORMS))})")
        return fn(*raw_value), "derived"
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


def _is_filler(row: dict) -> bool:
    """A structural padding row: every field blank or all zeros.

    Deliberately NOT "every field blank": a fixed-width filler row is written as
    zeros (`000`/`000000`), which is not blank at all, and treating it as data
    is what put ten empty detail lines into a converted Pre-Ticket.
    """
    return all(_trim(v).strip("0") == "" for v in row.values())




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


def _empty_row(placeholder: list, mapped_fields, nullable, empty_rows, arr_name,
               declared_row=None, sections=None, date_formats=None, note=None):
    """One row carrying a section's TAGS and no values.

    Keys come from the template's own row when it has one, so the shape matches
    what a real row of this section looks like; otherwise from the fields the
    mapping knows about. Values default to JSON null, overridden per field by
    ``json_empty_rows.yaml`` (so a field declared as "" writes "") — the same
    source the JSON engine uses when a bulk op empties a section, which is what
    keeps the two paths from drifting.

    ``declared_row`` is the array's own ``empty_row:`` block in
    ``ok_to_json.yaml``, applied LAST so it wins over the shared default. It
    exists because one field of an emptied section is not absent at all: a
    StyleHeader with no size lines still has a printed quantity, and it is
    `tot_qty` — the same rule D58 settled for the .OK side, where an empty Size
    section makes the header total authoritative rather than derived. Leaving it
    null loses the file's only quantity.

    The rules are the ordinary ones (``from``/``transform``/``raw``, or an
    outright ``value``) plus ``from_section`` (default ``Header``), since by
    definition the value cannot come from this section's own rows — there are
    none. That is also why it is declared per array in the conversion config and
    NOT in json_empty_rows.yaml: the JSON engine's own emptying has no `.OK`
    header to read, so this is one place the two paths legitimately differ.
    """
    keys = list(placeholder[0]) if placeholder and isinstance(placeholder[0], dict) \
        else list(dict.fromkeys(list(mapped_fields) + list(nullable)))
    declared = (empty_rows or {}).get(arr_name) or {}
    row = {k: declared.get(k) for k in keys}
    for field, rule in (declared_row or {}).items():
        if field not in row:
            raise ConvertError(
                f"empty_row declares {arr_name}[].{field}, which is not a field "
                f"of that section — check config/ok_to_json.yaml")
        if "value" in rule:
            row[field], prov = _generated(rule, date_formats or {}, field)
            src = "declared in config"
        else:
            sec = rule.get("from_section") or "Header"
            name = rule.get("from")
            rows = (sections or {}).get(sec) or []
            if not rows or name not in rows[0]:
                raise ConvertError(
                    f"empty_row {arr_name}[].{field} reads {name!r} from section "
                    f"{sec!r}, which the .OK does not have — check "
                    f"config/ok_to_json.yaml")
            # Deliberately NO blank check. The user's call: tot_qty is copied
            # through whatever it says, so a zero total converts to "0" rather
            # than reverting to null. The populated-row path judges blankness
            # before its transform; here the value IS the answer, and a file
            # with no size lines and no total has nothing better to offer.
            row[field], prov = _apply(rule, rows[0][name])
            src = f"{sec}.{name}"
        if note:
            note(f"{arr_name}[].{field}", prov, row[field],
                 f"no rows in .OK — taken from {src}")
    return row


def _generated(rule: dict, date_formats: Dict[str, str], field: str):
    """A field the .OK cannot supply but config declares outright.

    ``value:`` is written as given, EXCEPT ``now`` on a field the layout
    declares temporal (``date_fields.yaml``), which is stamped at conversion
    time in that field's own format — the same word the editor accepts when a
    date is typed by hand (D29), so config has one spelling to learn.

    Exists because "no .OK source" had exactly two possible answers before:
    keep the vendor template's value (another order's data) or write null.
    A DistLabels store `date` wants neither.
    """
    value = rule.get("value")
    fmt = date_formats.get(field)
    if fmt and isinstance(value, str) and value.strip():
        from okgen import datetimes
        try:
            return datetimes.normalize(value, fmt), "generated"
        except datetimes.DateError as exc:
            raise ConvertError(
                f"{field}: {exc} — check `value:` in config/ok_to_json.yaml") from exc
    return value, "generated"


def convert(okf, layout, spec: dict, template: dict,
            empty_rows: Optional[Dict[str, dict]] = None,
            date_formats: Optional[Dict[str, str]] = None) -> Tuple[dict, List[dict]]:
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

    date_formats = dict(date_formats or {})
    hdr_section = layout.sections[0].name
    hdr_rows = _section_values(okf, layout, hdr_section)
    if not hdr_rows:
        raise ConvertError("source file has no header record")
    H = hdr_rows[0]

    # --- document-level fields (data.timestamp, …) -------------------------
    #
    # `data.timestamp` and `data.type` sit BESIDE `data.header`, not inside it,
    # so the header block below cannot reach them — it writes into `header[…]`.
    # Before this, a conversion could only inherit the template's stamp, and all
    # three shipped conversions still do (none declares a `document:` block), so
    # every converted Style Header, Dist Label and Carton Label carried the
    # SAMPLE's timestamp. That is left exactly as it was; this only gives a
    # layout the option, which the Pre-Ticket uses.
    #
    # `type` is deliberately reachable here but never declared: it is the
    # detection discriminator, and the target layout's own spec already decides
    # it. Writing it from the .OK could only ever make the file undetectable.
    for field, rule in (spec.get("document") or {}).items():
        if field == "type":
            raise ConvertError(
                "`document:` must not set `type` — it is the layout "
                "discriminator and the template already carries the right one")
        if field not in data:
            raise ConvertError(
                f"`document: {field}` is not a field of the template document "
                f"(have: {', '.join(k for k in data if k != 'header')}) — "
                f"check config/ok_to_json.yaml")
        if "value" in rule:
            data[field], prov = _generated(rule, date_formats, field)
            note(field, prov, data[field], "declared in config")
            continue
        src = rule.get("from")
        names = list(src) if _is_multi(rule) else [src]
        missing = [n for n in names if n not in H]
        if missing:
            note(field, "template", data.get(field), f"no .OK field {missing[0]!r}")
            continue
        value, prov = _apply(rule, [H[n] for n in names] if _is_multi(rule) else H[src])
        # A transform that cannot build the value returns blank rather than a
        # half-formed one; the template's stamp is a better answer than "".
        if value == "":
            note(field, "template", data.get(field),
                 f"{'+'.join(names)} could not make a value")
            continue
        data[field] = value
        note(field, prov, value, "+".join(names))

    # --- header ------------------------------------------------------------
    for field, rule in (spec.get("header") or {}).items():
        if "value" in rule:                       # declared outright, no .OK source
            header[field], prov = _generated(rule, date_formats, field)
            note(field, prov, header[field], "declared in config")
            continue
        # A header field may read from the FIRST ROW of a repeating section.
        # A Pre-Ticket's `vendorStyle` and `category` live on the detail line in
        # the .OK and on the header in the JSON, so without this they could only
        # inherit the template — another order's values, the D46 leak. Default
        # stays the header, so every existing conversion is untouched.
        from_section = rule.get("from_section")
        row = H
        row_label = ""
        if from_section and from_section != hdr_section:
            srows = _section_values(okf, layout, from_section)
            if not _has_real_rows(srows):
                note(field, "template", header.get(field),
                     f"no rows in .OK section {from_section!r}")
                continue
            row = srows[0]
            row_label = f"{from_section}[0]."
        src = rule.get("from")
        names = list(src) if _is_multi(rule) else [src]
        missing = [n for n in names if n not in row]
        if missing:
            if rule.get("null_when_blank"):
                header[field] = None
                note(field, "null", None, "no .OK source")
            else:
                note(field, "template", header.get(field),
                     f"no .OK field {missing[0]!r}")
            continue
        # Blankness is judged on the .OK VALUE, before any transform — an empty
        # price must read as null, not as the '0.00' a transform would invent.
        if rule.get("null_when_blank") and all(_is_blank(row[n]) for n in names):
            header[field] = None
            note(field, "null", None, f"{names[0]} is blank in the .OK")
            continue
        value, prov = _apply(rule, [row[n] for n in names] if _is_multi(rule)
                             else row[src])
        header[field] = value
        note(field, prov, value, row_label + "+".join(names))

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
                # `empty_row:` may read another section (in practice the header,
                # for a total that survives its own rows) — resolved here rather
                # than up front, since only an EMPTY array ever consults it.
                declared_row = arr_spec.get("empty_row") or {}
                sections = ({s.name: _section_values(okf, layout, s.name)
                             for s in layout.sections} if declared_row else {})
                header[arr_name] = [_empty_row(placeholder, mapped_fields,
                                               nullable, empty_rows, arr_name,
                                               declared_row, sections,
                                               date_formats, note)]
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
                if "value" in rule:               # declared outright, per ROW
                    item[field], prov = _generated(rule, date_formats, field)
                    if not out:
                        note(f"{arr_name}[].{field}", prov, item[field],
                             "declared in config")
                    continue
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
        # A fixed-width detail section can be padded to a block size with
        # STRUCTURAL all-zero rows — `detail_fill.yaml` keeps Preticket.Lane at
        # a minimum of 10, so a file with one real line carries ten `000…` rows
        # after it. Those are padding, not order lines: converting them produces
        # ten junk JSON details that look exactly like real ones. The same rows
        # are already skipped as "not data" by `_section_has_data` on the .OK
        # side, so this makes conversion agree with the rest of OkGen rather
        # than inventing a rule.
        #
        # Only TRAILING runs are dropped, and only when the whole row is zeros
        # or blanks — a zero in the middle of real lines is somebody's data.
        if det_spec.get("skip_filler_rows"):
            while rows and _is_filler(rows[-1]):
                rows = rows[:-1]
        proto = (data.get("details") or [{}])[0]
        out = []
        for row in rows:
            item = json.loads(json.dumps(proto))
            for field, rule in (det_spec.get("fields") or {}).items():
                if "value" in rule:               # declared outright, per ROW
                    item[field], prov = _generated(rule, date_formats, field)
                    if not out:
                        note(f"details.{field}", prov, item[field],
                             "declared in config")
                    continue
                src = rule.get("from")
                names = list(src) if _is_multi(rule) else [src]
                missing = [n for n in names if n not in row]
                if missing:
                    if rule.get("null_when_blank"):
                        item[field] = None
                        if not out:
                            note(f"details.{field}", "null", None, "no .OK source")
                    continue
                if rule.get("null_when_blank") and all(_is_blank(row[n]) for n in names):
                    item[field] = None
                    if not out:
                        note(f"details.{field}", "null", None,
                             f"{names[0]} is blank")
                    continue
                item[field], prov = _apply(rule, [row[n] for n in names]
                                           if _is_multi(rule) else row[src])
                if not out:
                    note(f"details.{field}", prov, item[field], "+".join(names))
            out.append(item)
        if not out:
            # D43's boundary, arriving through conversion: an .OK whose detail
            # section was emptied would otherwise produce a bare `"details": []`,
            # which tells the consuming system nothing about the shape it should
            # have had. Every nested array already keeps ONE row with every
            # field present and empty; `details` is an array like the others and
            # now behaves like them. Reachable only on a layout whose details
            # come from a repeating section — the other three build their row
            # from the header, which always exists.
            out = [_empty_row([proto] if proto else [],
                              set(det_spec.get("fields") or {}), set(),
                              empty_rows, "details")]
            note("details[]", "empty", "1 blank row",
                 f"no rows in .OK section {from_section!r}")
        data["details"] = out

    # --- row counts --------------------------------------------------------
    empty_counts = dict(spec.get("empty_counts") or {})
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
            if source in emptied_arrays and field not in empty_counts:
                # The kept row carries tags, not data — the count is 0, not 1,
                # or the header would claim a store that does not exist. What
                # an empty section's count SAYS is config's to decide
                # (`empty_counts`); "0" is only the default for a field that
                # declares nothing.
                header[field] = "0"
                note(field, "count", "0", f"no {source} rows in .OK")
                continue
            if source not in populated_arrays:
                continue                              # placeholder, not real rows
            rows = header.get(source) or []
        header[field] = str(len(rows))
        note(field, "count", header[field], f"{len(rows)} {source} row(s)")

    # --- counts for an array with no rows ------------------------------------
    # Declared per layout, per field: what the header says when that array
    # carries no real .OK rows — whether it was emptied, or the template holds
    # it as null and the .OK has no such section at all. A field is left alone
    # while its array HAS rows, so the computed count above still governs the
    # normal case. The value is written verbatim, so `null` is a JSON null and
    # `" "` is the single space a StyleHeader already carries.
    applied_empty = set()
    for field, rule in empty_counts.items():
        arr = (rule or {}).get("array")
        if not arr or field not in header or arr in populated_arrays:
            continue
        header[field] = rule.get("value")
        applied_empty.add(field)
        note(field, "count", header[field], f"no {arr} rows — declared empty count")

    # everything the mapping never touched keeps its template value — including
    # a count whose empty rule did NOT fire, which is the point of not computing
    # `storeLines`/`lineCount`: with data present they are the template's.
    mapped = (set(spec.get("header") or {}) | set(spec.get("counts") or {})
              | applied_empty)
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
