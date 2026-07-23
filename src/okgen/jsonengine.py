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
    """Map every SCALAR leaf's path -> (start, end); text[start:end] is the exact
    source token (quotes included for strings, literal for number/bool/null)."""
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
        i = skip_ws(i + 1)
        if text[i] == "}":
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
                return i + 1
            raise ValueError(f"bad object char {text[i]!r} at {i}")

    def arr(i, path):
        i = skip_ws(i + 1)
        if text[i] == "]":
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

    def serialize(self) -> bytes:
        if not self.edits:
            return self.text.encode(ENCODING)          # untouched -> byte-exact
        repls = []
        for path, token in self.edits.items():
            span = self.spans.get(path)
            if span is not None:
                repls.append((span[0], span[1], token))
        text = self.text
        for start, end, token in sorted(repls, key=lambda r: r[0], reverse=True):
            text = text[:start] + token + text[end:]
        return text.encode(ENCODING)


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

    def __init__(self, state: JsonState, section, index: int, base: Tuple):
        self._state = state
        self.section = section
        self.index = index
        self._base = base                 # path tuple to this record's object
        self.issues: List[str] = []
        self.marker = ""                  # JSON has no record-type marker
        self.offset = 0
        self.field_spans = None
        self.raw = ""                     # not meaningful for JSON records

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
        return _display(_at(self._state.data, self._path(self._field(name))))

    def values(self) -> Dict[str, Optional[str]]:
        if self.section is None:
            return {}
        return {f.name: self.get(f.name) for f in self.section.fields}

    def set(self, name: str, value: str, literal: bool = False) -> None:
        """Stage a value edit. ``literal`` is irrelevant for JSON (no padding);
        the value is stored exactly as given as a JSON string."""
        f = self._field(name)
        path = self._path(f)
        if path not in self._state.spans:
            # Field key is absent from THIS document — inserting a new key is a
            # structural change not supported yet; refuse rather than silently
            # dropping the edit.
            raise ValueError(
                f"field {name!r} is not present in this JSON file (cannot add keys yet)")
        self._state.edits[path] = json.dumps(value, ensure_ascii=False)
        _set_at(self._state.data, path, value)


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
            arr = arr if isinstance(arr, list) else []
            for i in range(len(arr)):
                records.append(JsonRecord(state, sec, idx, base + (i,)))
                idx += 1
        else:                              # object (single record, e.g. Header)
            records.append(JsonRecord(state, sec, idx, base))
            idx += 1
    return state, records
