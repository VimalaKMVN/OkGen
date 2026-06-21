# OkGen — Implementation Plan

A visualizer/editor for fixed-width **OK files**. A user opens a folder of `.OK`
files; the app detects which layout (`.xlsx` definition) each file uses, parses
the fixed-width records into labelled fields grouped by section, and lets the
user edit **values only** — the program owns every character position, size, and
padding. Saving writes the values back byte-for-byte.

The whole thing is **pure Python (Flask)**. No Node, no build step.

---

## 1. Goal & core problem

OK files are fixed-width: every field lives at an exact character position with a
fixed size. Editing them by hand means counting characters meticulously across
hundreds of files — error-prone and slow. OkGen removes that burden: the user
sees friendly field forms and dropdowns; the program guarantees positions/sizes
stay correct and the file round-trips exactly.

---

## 2. Architecture (layers)

```
            ┌─────────────────────────────────────────────┐
   Browser  │  web/templates/index.html + static/app.js    │  ← UI (vanilla JS)
            └───────────────▲──────────────────────────────┘
                            │ JSON /api/*
            ┌───────────────┴──────────────────────────────┐
   Flask    │  web/app.py        (thin HTTP wrapper)         │
            └───────────────▲──────────────────────────────┘
                            │ plain Python calls
            ┌───────────────┴──────────────────────────────┐
   Service  │  api/service.py    (framework-agnostic logic)  │  ← REUSABLE SEAM
            └───────────────▲──────────────────────────────┘
                            │
   Core     │  okfile.py · detect.py · config.py · layout/*  │  ← parsing engine
```

**Why this shape:** all real logic lives in `service.py` and the core modules,
which know nothing about Flask. The Flask layer is ~80 lines of wiring. If the
team ever wants a React front-end, it consumes the **same `/api/*` JSON
endpoints** — the Python barely changes.

### Module map
| Module | Responsibility |
|---|---|
| `layout/models.py` | `Field` / `Section` / `Layout` data model |
| `layout/compiler.py` | Compile `*.xlsx` definitions → validated layouts (recompute field positions from cumulative sizes; drop unsized spec rows) |
| `layout/validate.py` | Slice each tab's sample record, assert field == `Value` column |
| `layout/registry.py` | Load + index layouts by name |
| `detect.py` | Identify a file's layout from its header; `read_chain()` for icons |
| `okfile.py` | Parse/serialize OK files; **byte-exact round-trip**; field get/set |
| `config.py` | Chain registry + display-label rules + record limits (YAML) |
| `api/service.py` | Tree build, parse-to-view, save, add/delete record, file ops, folder dialog |
| `web/app.py` | Flask app: HTML UI + JSON API |
| `web/static/app.js` | The single-page UI |
| `cli.py` | `okgen compile | detect | parse | serve` |

---

## 3. Data model & key design decisions

### OK file structure (discovered from the samples)
- Fixed-width records, one per line; lines end with a `\` terminator + space
  padding + `\r\n` (CRLF).
- **First char = record marker**: `|`/`¦` = Header (line 0), `#` / `&` =
  detail sections, or **no marker** (Preticket Detail starts with a digit).
- **Marker → section** mapping: line 0 = Header; `#`/`&` map to non-header
  sections in order of first appearance; unmarked lines → first detail section.

### Position model
- The xlsx `Position` is 1-based **into the marker-stripped record**. So raw OK
  position = xlsx Position + 1 for marked records.

### Layout detection (from the header line, raw positions)
| Test | → Layout |
|---|---|
| raw pos 4 = `N` | StyleHeader |
| raw pos 4 = `Y` | Preticket |
| raw pos 4 = `7` or `9` | DistLabels |
| raw pos 5–6 = `C:` | CartonLabel |

### Byte-exact round-trip (the correctness backbone)
Each record keeps its **original raw bytes**; fields are *views*. Editing a field
overwrites only that field's span, so an unedited file re-serializes
byte-for-byte. Files are read/written as Latin-1 so every byte (incl. non-ASCII
markers and CRLF) round-trips. Every save is verified (`roundtrip_ok`).

### Repeating records & limits
- Sections like Lane/Size/Store/Detail repeat (one record per line). Unsized spec
  rows (e.g. `lane2..lane10`, `*_totqty`) are ignored; `lane1`'s position/size
  applies to every lane record.
- Per-section `max_records` (e.g. StyleHeader Lane = 10) from `config/limits.yaml`.
  Add-row is disabled at the limit (UI) and rejected by the backend (422).

### Config-driven display (user-owned)
- `config/chains.yaml` — chain code → brand name + icon color
  (01 TJMAXX, 02 Marshalls, 03 Homegoods, 04 Winners, 06 HomeSense).
- `config/display.yaml` — coded field values → friendly labels, matched by
  `chain` × `layout` × `format` × `field`. Any of chain/layout/format may be a
  **list** (`["03","04"]`) to match several at once; the **most-specific** rule
  wins. Coded fields render as dropdowns; the underlying code is saved.

### Field width validation
- Inputs are limited to the field's `field_size`; the backend re-validates on
  save and rejects over-width values.

### Add / Delete row
- **Add** copies the section's **last record** (values included) — users
  typically duplicate a row and tweak a few fields.
- **Delete** removes a row (header protected). Both apply pending edits first,
  stay byte-exact, and write a `.bak`.

### Native folder picker
- "Open Folder" opens the **OS-native dialog** (macOS `osascript`, Windows
  PowerShell `FolderBrowserDialog`, Linux `zenity`) so users browse like any
  upload button. A manual path box remains as a fallback.

---

## 4. Build phases (history)

| Phase | What | Status |
|---|---|---|
| 1 | Layout compiler + self-validator + detection | ✅ `phase1-*` |
| 2 | Byte-exact parser/serializer | ✅ `phase2-parser-serializer` |
| 2.1 | Ignore unsized fields; show all repeating records | ✅ `phase2.1-repeating-records` |
| 3a | Config system (chains/display) + chain reader | ✅ |
| 3b | Service layer + JSON API | ✅ `phase3b-backend` |
| 3c | Flask UI (tree, editor, save, file actions) | ✅ `phase3c-flask-ui` |
| 3d | Add-row, limits, unsaved guards, list config | ✅ `phase3d-*` |
| 3e | Native folder dialog, add-copies-row, delete-row | ✅ `phase3e-*` |

Each phase is a git tag (checkpoint) you can roll back to.

---

## 5. Testing

`pytest` covers: layout compile + sample self-validation, detection, byte-exact
round-trip on all sample files, field slicing, edit/save fidelity, add/delete
record, row limits, config resolution (incl. list matching), and the Flask
endpoints. Tests use a fixed fixture config (`tests/fixtures/config/`) so they
don't depend on the editable production config.

```
python -m pytest tests/ -q
```

---

## 6. Open items / roadmap

- **Header count sync** — adding/deleting Lane/Size rows does not yet update the
  header count fields (`lane_rec` / `size_rec`). Optional follow-up.
- **Real format codes** — `config/display.yaml` is populated; extend as needed.
- **Offline Windows distribution (done)** — dependency wheels for Python 3.9–3.13
  (win_amd64) are vendored in `vendor/wheels`; `run.bat` installs from them with
  `pip --no-index` and runs from source (`PYTHONPATH=src`), so no internet is
  needed. Future: optionally serve with `waitress` and/or wrap as a one-click
  executable.
- **React migration** — if/when desired, reuse the `/api/*` JSON endpoints and
  replace `web/static` + `templates` with a React app; `service.py` is unchanged.
- **Path safety** — the local backend reads/writes by absolute path (intended for
  local single-user use); add a root-folder sandbox if ever exposed beyond local.
