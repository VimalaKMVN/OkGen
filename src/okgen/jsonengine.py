"""JSON engine — the 3rd OkGen parse/serialize mode (Calgary layouts).

Where the fixed-width engine addresses a field by byte span and the delimited
engine by walking delimiters, the JSON engine addresses a field by its **key
path** into a parsed JSON document. Byte-exact round-trip is preserved the same
way fixed-width is (D3): the raw text is scanned ONCE into a ``path -> (start,
end)`` span map over every scalar, and a save splices ONLY the changed values
back into the original text (right-to-left), leaving all other bytes — key
order, indentation (pretty or minified), whitespace — untouched.

Records are laid over the document by section:
  * an ``object`` section (the flat ``data.header``) is ONE record;
  * an ``array`` section (``data.header.stores``, ``data.details`` …) is one
    record per element.
A field's path is its ``json_path`` (absolute from ``data``, e.g. ["header",
"chain"] / ["type"]) for object sections, or ``<section path> + [i] + [name]``
for array elements.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

ENCODING = "utf-8"
_WS = " \t\n\r"


def scan_spans(text: str) -> Dict[Tuple, Tuple[int, int]]:
    """Map every path -> (start, end); ``text[start:end]`` is its exact source.

    Scalars map to their token (quotes included for strings, the literal for
    number/bool/null). **Containers map too** — an object or array spans from
    its opening brace/bracket to its closing one. That is what makes structural
    editing possible: an array element's span is the exact source text of that
    row, so adding, deleting and reordering rows are text splices like any
    other edit, and every untouched row keeps its bytes verbatim.
    """
    spans: Dict[Tuple, Tuple[int, int]] = {}
    n = len(text)

    def skip_ws(i):
        while i < n and text[i] in _WS:
            i += 1
        return i

    def str_end(i):                       # i at opening quote -> index past close
        j = i + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                return j + 1
            j += 1
        raise ValueError("unterminated string in JSON")

    def value(i, path):
        i = skip_ws(i)
        c = text[i]
        if c == "{":
            return obj(i, path)
        if c == "[":
            return arr(i, path)
        if c == '"':
            j = str_end(i)
            spans[path] = (i, j)
            return j
        j = i                              # number / true / false / null
        while j < n and text[j] not in ",}]" and text[j] not in _WS:
            j += 1
        spans[path] = (i, j)
        return j

    def obj(i, path):
        start = i
        i = skip_ws(i + 1)
        if text[i] == "}":
            spans[path] = (start, i + 1)
            return i + 1
        while True:
            i = skip_ws(i)
            kend = str_end(i)
            key = json.loads(text[i:kend])
            i = skip_ws(kend)
            if text[i] != ":":
                raise ValueError(f"expected ':' at {i}")
            i = value(i + 1, path + (key,))
            i = skip_ws(i)
            if text[i] == ",":
                i += 1
                continue
            if text[i] == "}":
                spans[path] = (start, i + 1)
                return i + 1
            raise ValueError(f"bad object char {text[i]!r} at {i}")

    def arr(i, path):
        start = i
        i = skip_ws(i + 1)
        if text[i] == "]":
            spans[path] = (start, i + 1)
            return i + 1
        idx = 0
        while True:
            i = value(i, path + (idx,))
            idx += 1
            i = skip_ws(i)
            if text[i] == ",":
                i += 1
                continue
            if text[i] == "]":
                spans[path] = (start, i + 1)
                return i + 1
            raise ValueError(f"bad array char {text[i]!r} at {i}")

    value(0, ())
    return spans


def _at(data, path):
    """Value at a path tuple, or None if any step is missing."""
    cur = data
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list):
            cur = cur[p] if isinstance(p, int) and 0 <= p < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def _set_at(data, path, value) -> None:
    """Set the parsed value at a path (mirrors the staged text edit)."""
    cur = data
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value


class JsonState:
    """Shared parse state for one JSON file: the source text, its parsed data,
    the scalar span map, and the set of staged value edits (path -> new token)."""

    def __init__(self, text: str, data, spans):
        self.text = text
        self.data = data
        self.spans = spans
        self.edits: Dict[Tuple, str] = {}
        # Every array section that is a real list in THIS file, recorded at
        # parse time (see :func:`parse`). Structural changes are otherwise
        # inferred from the surviving records, which cannot see an array whose
        # rows were ALL removed — there is no record left to name it.
        self.array_paths: List[Tuple] = []

    def serialize(self, records=None) -> bytes:
        """The document with staged edits applied.

        Two passes, in this order:

        1. **values** — splice each changed scalar back over its own span, which
           is what keeps an ordinary edit byte-exact everywhere else;
        2. **structure** — for any array whose rows were added, removed or
           reordered, rebuild just that array's source region from the row
           texts, reusing the file's own separator and indentation.

        Values go first so that step 2 works on text whose rows already carry
        their new values; spans are re-scanned in between because a changed
        token shifts every offset after it. An array whose rows were untouched
        is never rebuilt, so its bytes — and the whole rest of the file — are
        preserved exactly.
        """
        text = self.text
        if self.edits:
            repls = []
            for path, token in self.edits.items():
                span = self.spans.get(path)
                if span is not None:
                    repls.append((span[0], span[1], token))
            for start, end, token in sorted(repls, key=lambda r: r[0], reverse=True):
                text = text[:start] + token + text[end:]

        if records is not None:
            text = self._apply_structure(text, records)
        return text.encode(ENCODING)

    # ----- structural (row) changes -----
    def _apply_structure(self, text: str, records) -> str:
        """Rebuild every array whose row membership or order changed."""
        # Seed with every array the FILE has, not just the ones records still
        # mention: deleting the last row of an array leaves no record carrying
        # its `array_path`, so an emptied array would otherwise look untouched
        # and be silently left alone (bulk "keep 0 rows" reported success and
        # wrote nothing). An array nothing touched still compares equal below
        # and is never rebuilt, so this costs no bytes.
        wanted: Dict[Tuple, list] = {ap: [] for ap in self.array_paths}
        for rec in records:
            ap = getattr(rec, "array_path", None)
            if ap is not None:
                wanted.setdefault(ap, []).append(rec)
        if not wanted:
            return text

        changed = {ap: rows for ap, rows in wanted.items()
                   if _row_layout(rows) != tuple(range(len(_at(self.data, ap) or [])))}
        if not changed:
            return text                                   # nothing structural

        spans = scan_spans(text)
        indent = _detect_indent(text)
        # Right-to-left so an earlier array's rebuild can't shift a later span.
        for ap in sorted(changed, key=lambda a: spans.get(a, (0, 0))[0], reverse=True):
            span = spans.get(ap)
            if span is None:
                raise ValueError(
                    f"cannot change rows: {'.'.join(str(p) for p in ap)} is not an "
                    f"array in this file")
            text = (text[:span[0]]
                    + _rebuild_array(text, spans, ap, changed[ap], indent)
                    + text[span[1]:])
        return text


def _row_layout(rows) -> Tuple:
    """The rows' original indices, with None for each newly added row.

    Equal to ``(0, 1, 2, …)`` exactly when nothing was added, removed or moved
    — which is how :meth:`JsonState._apply_structure` decides an array can be
    left byte-for-byte alone.
    """
    return tuple(getattr(r, "orig_index", None) for r in rows)


def _detect_indent(text: str) -> str:
    """The file's own one-level indent, or "" for a minified document."""
    i = text.find("\n")
    if i < 0:
        return ""
    j = i + 1
    while j < len(text) and text[j] in " \t":
        j += 1
    return text[i + 1:j]


def _rebuild_array(text: str, spans, array_path: Tuple, rows, indent: str) -> str:
    """The source text for one array, given the rows it should now hold.

    Reuses the file's own layout rather than reformatting: the gap after ``[``,
    the exact separator between two existing elements, and the gap before ``]``
    are all lifted from the original. An untouched row contributes its own
    source verbatim.
    """
    a_start, a_end = spans[array_path]
    orig_n = 0
    while (array_path + (orig_n,)) in spans:
        orig_n += 1

    if orig_n:
        first = spans[array_path + (0,)]
        open_gap = text[a_start + 1:first[0]]
        close_gap = text[spans[array_path + (orig_n - 1,)][1]:a_end - 1]
        sep = (text[first[1]:spans[array_path + (1,)][0]] if orig_n > 1
               else "," + open_gap)
    else:
        # The array is empty in the file, so it has no layout of its own to
        # copy — fall back to the document's indent.
        base = _base_indent(text, a_start)
        open_gap = ("\n" + base + indent) if indent else ""
        close_gap = ("\n" + base) if indent else ""
        sep = "," + open_gap

    if not rows:
        return "[]"

    parts = []
    for r in rows:
        oi = getattr(r, "orig_index", None)
        if oi is not None:
            parts.append(text[spans[array_path + (oi,)][0]:spans[array_path + (oi,)][1]])
        else:
            parts.append(_new_row_text(text, spans, array_path, r, indent))
    return "[" + open_gap + sep.join(parts) + close_gap + "]"


def _base_indent(text: str, pos: int) -> str:
    """The leading whitespace of the line ``pos`` sits on."""
    line_start = text.rfind("\n", 0, pos) + 1
    j = line_start
    while j < len(text) and text[j] in " \t":
        j += 1
    return text[line_start:j]


def _new_row_text(text: str, spans, array_path: Tuple, rec, indent: str) -> str:
    """Source text for a row that wasn't in the file.

    A cloned row copies its template's exact source and splices its own edited
    values into that copy, so it matches the file's formatting perfectly. A
    seeded row (the section was empty, so there is nothing to clone) is rendered
    from the layout's fields using the document's indent.
    """
    tpl = getattr(rec, "template_index", None)
    if tpl is not None and (array_path + (tpl,)) in spans:
        s, e = spans[array_path + (tpl,)]
        snippet = text[s:e]
        edits = getattr(rec, "pending", None) or {}
        if edits:
            snip_spans = scan_spans(snippet)
            repls = [(snip_spans[(k,)][0], snip_spans[(k,)][1],
                      json.dumps(v, ensure_ascii=False))
                     for k, v in edits.items() if (k,) in snip_spans]
            for a, b, tok in sorted(repls, key=lambda r: r[0], reverse=True):
                snippet = snippet[:a] + tok + snippet[b:]
        return snippet

    obj = getattr(rec, "pending", None) or {}
    if not indent:
        return json.dumps(obj, ensure_ascii=False)
    base = _base_indent(text, spans[array_path][0]) + indent
    rendered = json.dumps(obj, ensure_ascii=False, indent=len(indent) or 2)
    return rendered.replace("\n", "\n" + base)


def _display(v) -> str:
    """A scalar JSON value as an editor string. Strings pass through (spaces
    preserved); null -> "" (untouched nulls stay null on save); other scalars
    render as their JSON token."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


class JsonRecord:
    """One editable object in the document (the header, or one array element),
    presenting the same get/values/set surface as a fixed-width ``Record``."""

    def __init__(self, state: JsonState, section, index: int, base: Tuple,
                 array_path: Tuple = None, orig_index: int = None):
        self._state = state
        self.section = section
        self.index = index
        self._base = base                 # path tuple to this record's object
        self.issues: List[str] = []
        self.marker = ""                  # JSON has no record-type marker
        self.offset = 0
        self.field_spans = None
        self.raw = ""                     # not meaningful for JSON records
        # Structural identity. `array_path` is the array this row belongs to
        # (None for the header, which is an object and can't be added to or
        # removed). `orig_index` is where the row sat in the file as read, and
        # is None for a row added in this session — together they tell
        # serialize which rows moved, went, or arrived.
        self.array_path = array_path
        self.orig_index = orig_index
        self.template_index = None        # a new row's source row, if cloned
        self.pending: Dict[str, str] = {}  # a new row's values (no spans yet)

    @property
    def is_new(self) -> bool:
        return self.array_path is not None and self.orig_index is None

    def _field(self, name):
        if self.section is None:
            raise KeyError(f"record {self.index} has no section")
        for f in self.section.fields:
            if f.name == name:
                return f
        raise KeyError(f"no field {name!r} in section {self.section.name!r}")

    def _path(self, f) -> Tuple:
        if getattr(f, "json_path", None) is not None:
            return ("data",) + tuple(f.json_path)      # absolute from data
        return self._base + (f.name,)

    def get(self, name: str) -> Optional[str]:
        if self.is_new:                   # not in the document yet
            return _display(self.pending.get(name))
        return _display(_at(self._state.data, self._path(self._field(name))))

    def values(self) -> Dict[str, Optional[str]]:
        if self.section is None:
            return {}
        return {f.name: self.get(f.name) for f in self.section.fields}

    def set(self, name: str, value: str, literal: bool = False) -> None:
        """Stage a value edit. ``literal`` is irrelevant for JSON (no padding);
        the value is stored exactly as given as a JSON string."""
        f = self._field(name)
        if self.is_new:                   # lives only in `pending` until saved
            self.pending[name] = value
            return
        path = self._path(f)
        if isinstance(_at(self._state.data, path), (dict, list)):
            # Some layouts declare a nested array as a header field (DistLabel's
            # `lanes`/`sizes`). Those are SECTIONS, edited as rows — writing a
            # string over one would destroy every row in it.
            raise ValueError(
                f"field {name!r} holds nested rows and cannot be set as a value")
        if path not in self._state.spans:
            # Field key is absent from THIS document — inserting a new key is a
            # structural change not supported yet; refuse rather than silently
            # dropping the edit.
            raise ValueError(
                f"field {name!r} is not present in this JSON file (cannot add keys yet)")
        self._state.edits[path] = json.dumps(value, ensure_ascii=False)
        _set_at(self._state.data, path, value)


def clone_record(template: "JsonRecord") -> "JsonRecord":
    """A new row copied from ``template`` — the JSON equivalent of duplicating
    a fixed-width line. The copy carries the template's CURRENT values and, on
    save, its exact source text, so it matches the file's formatting."""
    if template.array_path is None:
        raise ValueError("only rows of an array section can be duplicated")
    clone = JsonRecord(template._state, template.section, template.index,
                       template._base, array_path=template.array_path,
                       orig_index=None)
    clone.template_index = (template.orig_index if template.orig_index is not None
                            else template.template_index)
    clone.pending = dict(template.values())
    if clone.template_index is None:
        # Duplicating a row that is itself new: there is no source text to copy,
        # so it renders from its values like a seeded row.
        clone.pending = {k: (v or "") for k, v in clone.pending.items()}
    return clone


def seed_record(state: "JsonState", section, array_path: Tuple) -> "JsonRecord":
    """The first row for an EMPTY array section — every field blank.

    There is no existing row to copy, so the row is rendered from the layout's
    field list. Blank rather than invented values: the user fills it in.
    """
    rec = JsonRecord(state, section, 0, array_path + (0,),
                     array_path=array_path, orig_index=None)
    rec.pending = {f.name: "" for f in section.fields}
    return rec


def parse(text: str, layout):
    """Parse JSON ``text`` against a json_mode ``layout`` -> (JsonState, records)."""
    data = json.loads(text)
    spans = scan_spans(text)
    state = JsonState(text, data, spans)
    records: List[JsonRecord] = []
    idx = 0
    for sec in layout.sections:
        jp = tuple(sec.json_path or [])
        base = ("data",) + jp
        if sec.json_kind == "array":
            arr = _at(data, base)
            if isinstance(arr, list):
                state.array_paths.append(base)
            else:
                arr = []                 # absent or null: no rows, nothing to rebuild
            for i in range(len(arr)):
                records.append(JsonRecord(state, sec, idx, base + (i,),
                                          array_path=base, orig_index=i))
                idx += 1
        else:                              # object (single record, e.g. Header)
            records.append(JsonRecord(state, sec, idx, base))
            idx += 1
    return state, records
