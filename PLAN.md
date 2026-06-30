# OkGen — Living Build Plan & Status

**Read this first.** Single entry point for anyone (human or a fresh AI session)
picking up OkGen: what it is, how it's built, the decisions and why, where things
are, and what's next — so you can make the next increment without re-deriving
context. Keep it updated as part of each change.

> Baseline: this reflects the **"Golden" release** = tag
> `v0.17.0-eu-preticket-layout`, which is the top of `main`.
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
- **Layouts:** CartonLabel · DistLabels · Preticket · StyleHeader (NA, fixed-width) · **EUPreticket** (EU/EWMS, **pipe-delimited + UTF-8 BOM**). Detection keys off the char at the marker-adjusted header position (`|`/`#`/`&` first char shifts xls Position +1): `N`→StyleHeader, `Y`→Preticket, `C:`→CartonLabel, `7`/`9`→DistLabels; **UTF-8 BOM + `¦P|`→EUPreticket** (checked first).
- **Unique key per layout:** CartonLabel=`picklist_id` · DistLabels=`keytrol` · Preticket=`po` · StyleHeader=`keytrol` · EUPreticket=`po` (`config/keys.yaml`).
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

## 4. Current state
- **Top of `main` = tag `v0.17.0-eu-preticket-layout`** (the "Golden" baseline). **Tests: 86 passing.**
- **Feature set:** tree (lazy, per-banner icons, .OK only) · section editor with friendly labels + width validation + raw verify view (grid + amber line numbers) · Save/Save As · record add/move/delete + row-level controls + reorder · multi-select + bulk delete/copy (paste auto-uniquify) · **Bulk Edit** (header + detail ops, random/unique with range) · **Bulk Rename** (guided token builder + presets + glue + detail fields) · **Make Unique** (per-layout key) · unified **Bulk Actions** menu · **Send to NiceLabel** (warning + checkbox + animations + quips) · OS-native folder dialog · TJX branding (logo chip + favicon) · **5th layout EUPreticket** (EU/EWMS pipe-delimited + UTF-8 BOM, blue **EU** tree badge — all ops work via the delimited engine mode, see D7).
- See full tag history with `git tag --sort=creatordate`.

## 5. Run / test (quick reference)
```bash
# Dev server — http://127.0.0.1:8000
PYTHONPATH=src python -m okgen.cli serve          # Windows: double-click run.bat
# Tests
.venv/bin/python -m pytest tests/ -q              # currently 77
# Offline deps install (Windows box)
.venv\Scripts\python.exe -m pip install --no-index --find-links vendor\wheels flask openpyxl pyyaml
```
Commit convention: end messages with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## 6. Next increments / open threads (not yet built)
- **Production deployment** on the DC/RDP boxes beyond `run.bat` (always-available auto-start) — approach not yet decided.
- **Productization:** generalize the layout loader to "upload your own fixed-width spec" (the key unlock for a sellable, non-TJX product). Clear IP/ownership first; clean-room any generic version.
- **DC production-tool pivot:** auth/roles/concurrency/queue dashboard — awaiting direction.
- **NiceLabel bypass:** direct Sato SBPL printing (TCP 9100) + in-app label preview — deferred; needs printer model + SBPL capture.
- **SFTP preview auto-fetch + gallery** (paramiko, SSH-key auth) — deferred; needs SFTP details.
- *Note:* earlier explorations of an IIS deploy kit, a local on-logon auto-start kit, and an in-browser folder picker with real Windows Quick Access were built but **rolled back** from `main`; they survive only as recovery tags `v0.17.0`–`v0.21.1` if ever wanted again.

## 7. How to keep this current
On each substantive change: update §4 (top-of-main tag + test count + feature note),
add a row to §3 if a durable decision was made, and tick items in §6. This file is
the contract that lets a fresh session start fast.
