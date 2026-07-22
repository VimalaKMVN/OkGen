# OkGen — Living Build Plan & Status

**Read this first.** Single entry point for anyone (human or a fresh AI session)
picking up OkGen: what it is, how it's built, the decisions and why, where things
are, and what's next — so you can make the next increment without re-deriving
context. Keep it updated as part of each change.

> Baseline: top of `main` = tag `v0.22.1-per-layout-coverage`.
> Deeper references (don't duplicate them here):
> [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · [ARCHITECTURE.md](ARCHITECTURE.md) ·
> [DEVELOPMENT_PROCESS.md](DEVELOPMENT_PROCESS.md) · [README.md](README.md)

---

## 1. What it is
A visualizer/editor for fixed-width **"OK files"** — the label-print data files
TJX DCs feed to NiceLabel. Every field sits at an exact character position/size;
hand-editing them in a text editor is slow and breaks easily. OkGen shows
friendly per-section forms (coded values as plain-English labels), guarantees the
file round-trips **byte-for-byte**, and adds bulk operations + one-click send to
NiceLabel. Pure **Python (Flask)** + vanilla-JS SPA. Runs **local & offline** on
Windows (double-click `run.bat`).

**Salient value (use for demos/marketing):** (1) pain-free editing of one file,
then *at scale* — 100/day; (2) **bulk everything** — bulk edit, bulk rename that
keeps filenames in sync with content, Make Unique; (3) **one-click Send to
NiceLabel**.

## 2. Architecture (the seam that matters)
```
Browser (vanilla JS)  web/templates/index.html · static/app.js · static/styles.css
   │ JSON /api/*
Flask (thin wrapper)  web/app.py
   │ plain Python
Service (REUSABLE, framework-agnostic)  api/service.py   ← all real logic lives here
   │
Core engine  okfile.py (byte-exact parse/serialize) · detect.py · config.py · layout/*
```
The service layer is HTTP-free on purpose, so a React/Node front-end could swap in
later with no core rewrite. (Note: a stale docstring in `service.py` still says
"FastAPI" — the actual web layer is Flask.)

### Key files
| File | Role |
|---|---|
| `src/okgen/okfile.py` | Byte-exact parse/serialize; records hold raw bytes, edits overwrite only a field's span |
| `src/okgen/detect.py` | Layout detection from marker + header position rules |
| `src/okgen/config.py` | Loads all `config/*.yaml`; typed accessors |
| `src/okgen/layout/*` | Compile `.xlsx` layout defs → JSON; registry, validate, models |
| `src/okgen/api/service.py` | Every operation: tree, parse, edits, record add/move/delete, file ops, bulk_*, rename_*, make_unique, send_to_nicelabel, browse_folder |
| `src/okgen/web/app.py` | Flask routes (`/api/*`) |
| `src/okgen/web/static/app.js` | The whole SPA (tree, editor, bulk, rename, send animation) |
| `src/okgen/cli.py` | `okgen compile|detect|parse|serve` |
| `run.bat` / `run.cmd` / `run.sh` | One-click launch; offline install from `vendor/wheels`. `run.cmd` is an identical copy of `run.bat` for environments that strip `.bat` files in transit (AV/DLP) — see README |

### Domain facts
- **Banners (chains):** 01 TJMAXX · 02 Marshalls · 03 HomeGoods · 04 Winners · 05 Europe (EU) · 06 HomeSense (`config/chains.yaml`).
- **Layouts:** CartonLabel · DistLabels · Preticket · StyleHeader (NA, fixed-width) · **EUPreticket** (EU/EWMS, **pipe-delimited + UTF-8 BOM**) · **EUStyleHeader** + **EUCartonLabel** (EU/EWMS GTA, **pipe-delimited, Latin-1, no BOM**; every line leads with `|`). Detection keys off the char at the marker-adjusted header position (`|`/`#`/`&` first char shifts xls Position +1): `N`→StyleHeader, `Y`→Preticket, `C:`→CartonLabel, `7`/`9`→DistLabels; **UTF-8 BOM + `¦P|`→EUPreticket** (checked first); **`|`-led + format letter at raw pos 2** `D`→EUStyleHeader / `H`→EUCartonLabel.
- **Unique key per layout:** CartonLabel=`picklist_id` · DistLabels=`keytrol` · Preticket=`po` · StyleHeader=`keytrol` · EUPreticket=`po` · EUStyleHeader=`keytrol` · EUCartonLabel=`keytrol` (`config/keys.yaml`).
- **Config-driven:** `config/*.yaml` — chains, display (coded→label rules), field_colors, keys, limits, section_counts, nicelabel (hot-folder path + warning + quips/done_quips), rename_tokens, rename_presets. Tests use decoupled `tests/fixtures/config/`.

## 3. Decision log (durable — the "why")
| # | Decision | Why |
|---|---|---|
| D1 | **Pure Python (Flask) + vanilla JS**, no build step; keep `api/service.py` framework-agnostic | Simple to run/ship; preserves an easy React/Node migration path |
| D2 | **Commit directly to `main`, no branches**; per-feature checkpoint = commit+push+annotated tag; roll back via `git reset --hard <tag>` + force-push | User's chosen workflow; tags are durable recovery points |
| D3 | **Byte-exact round-trip** — records hold raw bytes; edits overwrite only a field's span | Files are positional; one stray char breaks them — the program owns positions/sizes |
| D4 | **Offline Windows distribution** via `vendor/wheels` (+ `run.bat` `pip --no-index`, run from source `PYTHONPATH=src`) | Locked-down DC boxes, no internet/PyPI |
| D5 | **Open Folder = OS-native folder dialog** (Windows `OpenFileDialog` folder mode via PowerShell · macOS `osascript` · Linux `zenity`), launched server-side (`service.browse_folder`) | Familiar Explorer UI incl. Quick Access; works because the app runs on the same machine as the browser (local use) |
| D6 | **Send to NiceLabel** = one-click copy to `config/nicelabel.yaml` hot folder; confirm modal has a **yellow warning + acknowledgement checkbox** gating Send; randomized fun animations + rotating configurable quips | Outward-facing/production action — keep an explicit, hard-to-miss confirmation |
| D7 | **Delimited layout mode** (5th layout, EU/EWMS `EUPreticket`): pipe-delimited + UTF-8 BOM. Read as Latin-1 (byte-exact preserved); `Record.field_spans` located by walking the actual `\|` delimiters instead of fixed start/size; header strips BOM+`¦` marker, detail lines have none; trailing `\` terminator + CRLF left untouched. `Layout.delimited` flag set by the `TJXEWMS_` filename prefix. Chain is read from a delimited token (chain `05` = **Europe**, badge **`EU`**, `config/chains.yaml`) — config-driven like every other banner. | New vendor format needed a parse mode the fixed-width engine couldn't handle; span-walking keeps the byte-exact round-trip guarantee and lets all existing ops (edit, bulk, make-unique, add/delete) work unchanged |
| D8 | **EU GTA layouts `EUStyleHeader` (format `D`) + `EUCartonLabel` (format `H`)** — one shared GTA spec, two layouts. Pipe-delimited **Latin-1, no BOM**; **every line leads with the `\|` delimiter**, so each section's first field is a size-0 `marker` placeholder that absorbs the empty leading token and keeps the real fields aligned. Sections are **marker-routed in delimited mode** (`_assign_delimited` now maps non-header lines to sections by their record-type char in order of appearance, like the fixed-width assigner — `&`→Lane, `#`→Detail; marker-less delimited details e.g. EUPreticket collapse to one section, so it stays backward-compatible). xlsx specs built from the GTA doc's **TARGET columns** (name = col A, size = actual sample-token width — authoritative for delimited); the fixed-width `validate` cross-check now **skips delimited layouts** (positions are delimiter-walked, not fixed). Detection: `\|`-led header with format letter `D`/`H` at raw pos 2. | Two real vendor formats sharing one spec; marker routing was the only engine gap (delimited previously supported a single detail section), and it generalizes cleanly from the fixed-width path |
| D12 | **A layout's detection-signature field is never editable** — the header byte(s) `detect.py` keys off (StyleHeader/Preticket `indicator`, DistLabels `format`, CartonLabel `picklist_pre`, EUPreticket `indicator`, EU GTA `process`) are listed as `readonly` in `field_display.yaml`; `_header_fields_for_layout`/`_detail_sections_for_layout` drop everything read-only or hidden so bulk can't reach them either; and `service._assert_layout_stable` re-detects from the serialized header before **every** write, refusing any change that would alter the detected layout. | These fields were editable in the editor *and* offered by Bulk Edit on all 7 layouts. Changing one made the file unopenable — or, for StyleHeader/Preticket, silently re-detect as **DistLabels**, so every field then parsed at the wrong offset (bad data, no error). One bulk apply would have bricked a whole selection. Config locks the 7 known cases; the save-time check makes the whole class impossible even if that list drifts or a new layout is added |
| D11 | **Row ops are staged, not written on click** — add/delete/move rows are held in a client-side **op journal** (`state.ops`, interleaved with the field edits pending at each step) and replayed server-side (`service.replay_ops`) against the on-disk file only when the user presses Save or Save As. The three `/api/record/*` routes take `preview: true` + the journal and return the resulting view **without writing**; `apply_edits` takes `ops` and writes once, to `path` (Save) or `target_path` (Save As). | Row ops used to write to the original file the instant you clicked — so Save As produced a modified original *and* a copy, and pending header edits were flushed into the original as a side effect. Staging makes Save/Save As mean the same thing for every kind of change, keeps the original byte-exact under Save As, and makes a rejected op a no-op |
| D10 | **Sections are marker-routed, not order-routed** — each layout section carries a canonical `marker` (compiler-derived) plus a `sample_raw` seed line; both the fixed-width and delimited assigners map records to sections by marker, and `okf.sections()` always returns the full canonical section list including empty ones. | Order-of-appearance routing broke whenever a section was empty (later records were absorbed into the wrong section and the empty one vanished from the UI); marker routing makes an empty section a first-class, editable state, and `sample_raw` is what lets the editor seed a valid first row into it |
| D9 | **Config-driven editor-field behavior** — four new YAML-driven layers over the section editor, all resolved server-side and emitted per field: **hide** structural fields (`field_display.yaml`), **read-only** fields shown as a static label (`field_display.yaml`), **derived** computed fields not in the raw file with an `eq`/`neq`/`in`/`nin` rule DSL that the client re-evaluates live (`derived_fields.yaml`), and **chain-edit isolation** blocking cross-region chain changes (`isolated_chain_groups` in `chains.yaml`, enforced in the dropdown **and** on save). | Keep per-layout/vendor UI rules out of code so ops can adjust labels/rules/locks without a release; the same config feeds editor, rename tokens, and save validation for one source of truth |

## 4. Current state
- **Top of `main` = tag `v0.22.1-per-layout-coverage`.** **Tests: 161 passing.**
  Coverage gap closed: the staged-row-ops work was only parametrized for **delete**; **add** and **move** were tested on StyleHeader alone. Both are now parametrized across all 7 layouts (preview writes nothing → Save As changes only the copy → source byte-identical), so every layout × every row op is covered. The add case picks the first section with headroom (StyleHeader's Lane sits at its 10-record limit) rather than skipping the layout.
- **Prior top of `main` = tag `v0.22.0-detection-signature-lock`** (was 147 passing).
  **Detection-signature fields locked** (see D12). An audit of all 7 layouts found every one has a header field that *is* its detection signature, all editable in the section editor and all offered by Bulk Edit. Fixed in three layers: (1) `config/field_display.yaml` marks each one `readonly` (shown as a static label, still visible); (2) bulk scope drops read-only/hidden fields, so Bulk Edit can no longer reach them — this was the mass-brick path; (3) `service._assert_layout_stable` re-detects from the serialized header before every write (save, Save As, row ops, bulk, make-unique) and rejects anything that would change the detected layout, leaving no `.bak` behind. Bulk reports it per file as an `error` status instead of throwing. `tests/fixtures/config/field_display.yaml` mirrors the production locks.
  - *Severity note:* StyleHeader/Preticket `indicator` failed **silently** — changing it to a digit re-detected the file as **DistLabels**, so fields parsed at the wrong offsets with no error. The other five became cleanly undetectable.
- **Prior top of `main` = tag `v0.21.1-staged-row-ops-all-layouts`** (was 125 passing).
  **Save As no longer mutates the original file** (see D11). Row add/delete/move used to write to the opened file immediately, so deleting lanes/details/stores and then choosing Save As left the *original* modified too — and any pending header edits were flushed into it as a side effect. Now:
  - **Client** keeps an ordered op journal (`state.ops` in `app.js`) alongside `state.edits`; each row op posts `preview: true` + the journal and renders the returned view **without anything being written**. The journal is only committed once the server accepts the op, so a rejected op (e.g. a section at its `max_records` limit) leaves pending state untouched.
  - **Server** grew `service.replay_ops` + pure in-memory mutators (`_op_add`/`_op_delete`/`_op_move`, `_reindex` renumbering records exactly as a reparse would) and `_build_file_view`, which renders an editor view from an unsaved in-memory file. `apply_edits` takes `ops` and writes **once** — to `path` for Save, to `target_path` for Save As, leaving the source byte-identical.
  - **Save is enabled by row ops again** (`dirtyCount()` = field edits + staged ops): previously a row op cleared `state.edits`, so Save greyed out because the change was already on disk. Row-op status messages now say "(unsaved)", and the Raw verify banner distinguishes staged rows (rendered) from just-typed field edits (not yet).
  - *Trade-off, intended:* nothing touches disk until a Save button, so abandoning a file discards staged row work — the "unsaved changes" prompt now covers row ops too.
  - **Verified on all seven layouts** (fixed-width *and* delimited) by a parametrized test: preview writes nothing, Save As leaves the source byte-identical, the copy carries the change.
  - *Found while testing, NOT fixed (pre-existing, see §6):* header fields that form a layout's **detection signature** are editable, and changing them makes the file undetectable afterwards — e.g. EUPreticket `indicator` `P`→anything, CartonLabel `picklist_pre` `C:`→anything. Reproduces with zero row ops on the old save path.
- **Prior top of `main` = tag `v0.20.0-empty-sections-single-file-bulk`** (was 112 passing).
  Empty-section routing/display/editing + padding UX + single-file bulk — fixes three related editor issues across all NA + EU layouts (see D10):
  - **Empty sections no longer shift records or vanish.** Every section now carries a canonical `marker` (derived in `layout/compiler.py`: from the sample for delimited layouts, learned from the reference `.OK` for fixed-width). The `okfile.py` assigners route by that marker instead of order-of-appearance, so an empty section (e.g. no Lane rows) keeps later sections in place. `okf.sections()` returns **every** layout section in canonical order including empties, and the UI renders an empty section as a "None" row (previously it disappeared / swallowed the next section's records). All seven `layouts/*.json` regenerated with `marker` + `sample_raw`.
  - **Empty sections are editable.** Each section carries a `sample_raw` seed line (a real reference record); single-file Add and bulk Add seed the first row from it, inserted in canonical section order. Non-add bulk ops still no-op on an empty section.
  - **Fixed-width padding UX:** editable text inputs strip pad spaces for display (cursor lands at the start, room to type); the server re-pads on save and untouched fields are never re-sent — round-trip stays byte-exact.
  - **Bulk works on a single selected file.** Bulk Edit was the only op gated to 2+ files and the toolbar button hid below 2; a plain click already selects the open file, so "Bulk Actions (1)" now acts on it — useful for updating many store/size/detail records within one file.
- **Prior top of `main` = tag `v0.19.1-plan-sync`** (doc/tooling only on top of the `v0.19.0-eu-gta-styleheader-cartonlabel` code; was 106 passing).
  EU GTA layouts `EUStyleHeader` + `EUCartonLabel` — added the 6th & 7th layouts (see D8): two `data/OkFileDefinitions/TJXEWMS_{StyleHeader,CartonLabel}Layout.xlsx` specs (Header/Lane/Detail and Header/Detail) generated from the GTA mapping doc's TARGET columns; sample `.OK` fixtures `EU{StyleHeader,CartonLabel}.OK`; a `|`-led + `D`/`H` detection rule (`detect.py`); marker-routed multi-section delimited assignment (`okfile.py` `_assign_delimited`); `validate.py` now skips delimited layouts; `config/keys.yaml` keys (both = `keytrol`). Chain 05/EU badge + all existing ops (edit/bulk/rename/make-unique) work via the delimited engine.
  - **Editor field display** (new `config/field_display.yaml` + `Config.hidden_fields`/`readonly_fields`): the synthetic leading-`|` `marker` placeholder and the `#`/`&` record-type marker fields are **hidden** from the section editor (bytes still preserved). The header's format-code field is named **`process`** (not `format`, which stays NA-only) and is **read-only**, shown as its friendly layout label via `display.yaml` (`D`→"(D) StyleHeader", `H`→"(H) Holdings/CartonLabels"). `parse_file_view` derives the display `fmt` context from `format` **or** `process`. Service emits `hidden`/`editable` per field; `app.js` skips hidden fields (form + table) and renders read-only fields as a static `.fval-ro` label.
  - **Derived (computed) fields** (new `config/derived_fields.yaml` + `Config.derived_fields`/`eval_derived`): fields shown in the editor but **not present in the raw file**, computed from other fields via a first-match rule list with `eq`/`neq`/`in`/`nin` conditions (trimmed compares). EUCartonLabel gets a read-only **`format`** (placed after `process`) = `1 - Carton Label` / `2 - AD Carton Label` / `4 - Masterpack` / `5 - AD Masterpack`, derived from `distribution_type` (≠/=`AD`) × `pack_type` (`CL`/`C` vs `MP`). Server computes the initial value and ships the rules; `app.js` mirrors the logic (`evalDerived`) and **recomputes live** (`refreshDerived` on `onEdit`) when a driving input changes; nothing is written for it on save. **Bulk-rename integration:** `_file_tokens` resolves derived fields (so the `format` token works in a rename preset for EUCartonLabel), `rename_scope` offers them in the palette, and `_build_name` space-to-underscore sanitizes derived values (`Config.all_derived_names()`); new EU GTA field tokens added to `config/rename_tokens.yaml`; per-layout rename presets in `config/rename_presets.yaml`; EU GTA `ticket_format` coded→label rules in `display.yaml`.
  - **Chain-edit isolation** (new `isolated_chain_groups` in `config/chains.yaml` + `Config.can_change_chain`): a file's chain cannot move **into or out of** an isolated group — Europe (`05`) is isolated, so Europe↔NA is blocked both ways while the NA banners (01/02/03/04/06) stay freely interchangeable. Enforced in the editor (chain dropdown offers only same-group chains; locked read-only when isolated, e.g. Europe) **and** on save (`_apply_edits_to_okf` rejects a cross-boundary chain edit; `config` now threaded through `apply_edits`/add/move/delete + the `/api/save` route). *Not yet: section-count config for the two new layouts (optional follow-up).*
- **Prior top of `main` = tag `v0.18.1-session-tooling`** (was 88 passing).
  - Ops/tooling only (no app changes): added `list-active-users.bat` (no-elevation helper to identify who's logged in / holding a deploy lock) and a project-local **`/close_shop`** skill (`.claude/skills/close_shop/`) that runs the end-of-session ritual (update PLAN + reconcile memory + checkpoint/push). `.claude/settings.local.json` is gitignored (personal). → `v0.18.1`
  - EU naming-convention work: lowercased the two capitalized field names (`Indicator`→`indicator`, `Zone`→`zone`) in `TJXEWMS_PreticketLayout.xlsx` via a targeted shared-string XML edit (preserves cached formula values) so tokens are no longer case-confusing; added the missing EU detail fields (`page`/`line`/`size`/`qty`/`ladder`) to the rename palette; added an **`EUPreticket` rename preset**; added a **configurable `region` derived token** (`config/regions.yaml` maps region→zones, e.g. Reg1=01/03/05/33, Reg2=02/04/06/07/08/10; `Config.region(zone)` resolves it, blank for unmapped/non-EU) → `v0.18.0`.
  - Prior EU increments: trimmed unused detail fields (lookup_id 18–24) via targeted XML row-delete → `v0.17.1`; fixed row-action buttons (↑↓＋✕) stacking vertically on wide tables (`.del-cell` `white-space:nowrap`) → `v0.17.1`; Raw verify tab decodes delimited (EU) files as UTF-8 so the marker shows as a clean `¦` (display-only; bytes/round-trip unchanged) → `v0.17.2`.
- **Feature set:** tree (lazy, per-banner icons, .OK only) · section editor with friendly labels + width validation + raw verify view (grid + amber line numbers) · Save/Save As · record add/move/delete + row-level controls + reorder · multi-select + bulk delete/copy (paste auto-uniquify) · **Bulk Edit** (header + detail ops, random/unique with range) · **Bulk Rename** (guided token builder + presets + glue + detail fields) · **Make Unique** (per-layout key) · unified **Bulk Actions** menu · **Send to NiceLabel** (warning + checkbox + animations + quips) · OS-native folder dialog · TJX branding (logo chip + favicon) · **5th layout EUPreticket** (EU/EWMS pipe-delimited + UTF-8 BOM, blue **EU** tree badge — all ops work via the delimited engine mode, see D7).
- See full tag history with `git tag --sort=creatordate`.

## 5. Run / test (quick reference)
```bash
# Dev server — http://127.0.0.1:8000
PYTHONPATH=src python -m okgen.cli serve          # Windows: double-click run.bat
# Tests
.venv/bin/python -m pytest tests/ -q              # currently 161
# Offline deps install (Windows box)
.venv\Scripts\python.exe -m pip install --no-index --find-links vendor\wheels flask openpyxl pyyaml
```
Commit convention: end messages with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## 6. Next increments / open threads (not yet built)
- **Calgary JSON layouts (NEW FORMAT — architectural fork, in discussion).** Source files live in `/Users/praveendx/repos/OkGenData/Calgary New Layout Definitions and JSON files/`: a definition xlsx (3 tabs `styleHeader`/`distributionLabel`/`cartonLabel` — field-mapping refs, `Field Name` + `Max Length` + `Notes`, **not** position specs) + 3 zips of real **JSON** samples (18 files). These are **JSON, not fixed-width/pipe-delimited** — a would-be **3rd engine** beyond OkGen's positional/byte-span model. Shape: `data.type` (`styleHeaders`/`cartonLabels`/`distributionLabels` = layout discriminator) · `data.timestamp` · flat `header` (~54 keys, mostly null) · nested arrays (`lanes`/`sizes`/`stores`/`details`). Gotchas: chain appears as code (`"04"`,`"06"`) **and** name (`"Winners"`); values mix `null` / `""` / `" "`; formatting is inconsistent (some carton files **minified**, style/dist **pretty-printed**) — so "byte-exact round-trip" means something new here. **Blocked on user (returning next session):** (1) the JSON **schema** (authoritative field set/order/types per type); (2) the **fork decision** — does OkGen *view/edit JSON natively* (new engine) vs *convert JSON↔existing .OK*? Design can't start until both land.
- **Production deployment** on the DC/RDP boxes beyond `run.bat` (always-available auto-start) — approach not yet decided. *Pain that reinforces this:* when the app is left running under one RDP user's session, its `python.exe` locks `.venv`, blocking a deploy that deletes it — and a non-admin can't inspect/kill another session's process. Running OkGen as a **Windows Service** (e.g. NSSM) under a dedicated service account would remove per-session locks entirely. Interim helper shipped: `list-active-users.bat` (lists logged-in users so you can reach out).
- **Productization:** generalize the layout loader to "upload your own fixed-width spec" (the key unlock for a sellable, non-TJX product). Clear IP/ownership first; clean-room any generic version.
- **DC production-tool pivot:** auth/roles/concurrency/queue dashboard — awaiting direction.
- **NiceLabel bypass:** direct Sato SBPL printing (TCP 9100) + in-app label preview — deferred; needs printer model + SBPL capture.
- **SFTP preview auto-fetch + gallery** (paramiko, SSH-key auth) — deferred; needs SFTP details.
- *Note:* earlier explorations of an IIS deploy kit, a local on-logon auto-start kit, and an in-browser folder picker with real Windows Quick Access were built but **rolled back** from `main`; they survive only as recovery tags `v0.17.0`–`v0.21.1` if ever wanted again.

## 7. How to keep this current
On each substantive change: update §4 (top-of-main tag + test count + feature note),
add a row to §3 if a durable decision was made, and tick items in §6. This file is
the contract that lets a fresh session start fast.
