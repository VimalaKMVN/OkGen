# OkGen — OK File Editor

A simple desktop web app for viewing and editing fixed-width **OK files**. Open a
folder of `.OK` files, edit fields in a friendly form (with dropdowns and width
limits), and save — OkGen keeps every character position, size, and padding
correct for you, so you never have to count characters by hand.

Runs entirely on your own machine. Your files never leave your computer.

> **You received this as a ZIP file** (e.g. `OkGen.zip`) and it runs on
> **Windows**. Everything the app needs is bundled inside the ZIP, so **no
> internet is required** to install or run it. The only thing you install
> separately is **Python** itself. Just follow **Quick start** below.

---

## What you need (one time)

- **Windows** (64-bit).
- **Python 3.9–3.13.** Download from <https://www.python.org/downloads/> and
  during install **check "Add Python to PATH"**. To confirm it's installed, open
  **Command Prompt** and run:
  ```
  python --version
  ```

That's the only thing to install. All the Python packages OkGen needs are already
bundled in the ZIP (in the `vendor\wheels` folder), so setup works **completely
offline**.

---

## Quick start (recommended)

1. **Unzip** the file you were given (e.g. `OkGen.zip`) anywhere — your Desktop is
   fine. It unzips into a folder (its name may include a version, e.g.
   `OkGen-0.1`). Open that folder.
2. Double-click **`run.bat`**.
3. The first run sets everything up automatically (**offline** — nothing is
   downloaded), then your browser opens to **http://127.0.0.1:8000**. (If it
   doesn't open, paste that address into your browser.)
4. To stop the app, close the black window (or press **Ctrl + C** in it).

The launcher creates a private environment inside the folder the first time and
reuses it afterwards, so later launches are fast.

> **Windows SmartScreen note:** the first time you run `run.bat`, Windows may show
> a "Windows protected your PC" prompt because the file came from the internet.
> Click **More info → Run anyway**. (It's a plain text script — you can open it in
> Notepad to see exactly what it does.)

> **If `run.bat` is missing after you copy the folder:** corporate antivirus,
> email/file-share security (DLP), or Windows may **strip or quarantine `.bat`
> files** in transit — the GitHub zip *does* contain it, so it was removed on the
> way to your machine, not missing from the download. Three ways to fix it:
> 1. Double-click **`run.cmd`** instead — it's an identical launcher with a
>    different extension that often slips past `.bat`-specific filters.
> 2. Or recreate it: open **Notepad**, paste the contents of `run.cmd` (or
>    `run.bat` from another copy), and **Save As** `run.bat` with *Save as type:*
>    **All Files**.
> 3. Or just use **Manual start** below — no launcher needed.
>
> To avoid it next time: copy the zip via an internal share/USB (not email),
> right-click the zip → **Properties → Unblock** before extracting, or add the
> OkGen folder to your antivirus exclusions.

---

## Manual start (if you prefer the command line)

Open **Command Prompt in the OkGen folder** and run (all offline):

```bat
py -m venv .venv
.venv\Scripts\activate
pip install --no-index --find-links vendor\wheels flask openpyxl pyyaml
set PYTHONPATH=src
python -m okgen.cli serve
```

Then open <http://127.0.0.1:8000>. Next time you only need:
```bat
.venv\Scripts\activate
set PYTHONPATH=src
python -m okgen.cli serve
```

---

## Using the app

1. Click **Open Folder…**. In the Explorer window that opens, **double-click into
   the folder** that contains your `.OK` files (so that folder is the one shown),
   then click **Open** (the filename box shows a "Select this folder" placeholder
   — that's expected). Only `.OK` files are shown; other files are hidden.
2. Each file in the left tree has a **colored badge** for its chain
   (TJX / MAR / HG / WIN / HS).
3. Click a file to open it. Fields appear grouped by **section**
   (Header, Lane, Size, …).
   - **Coded fields** (like format, indicator) are **dropdowns** showing readable
     names; OkGen saves the underlying code.
   - **Other fields** are text boxes limited to the exact field width.
4. Edit values. Changed cells turn amber and the file shows a `*` (unsaved).
5. **Repeating sections** (Lane, Size, …) have:
   - **＋ Add row** — adds a copy of the last row (then tweak what you need).
     Disabled when a section hits its limit (e.g. Lane = 10).
   - **✕** on each row — delete that row.
6. **Save** writes your changes back to the file (a `.bak` backup is made).
   **Save As…** writes to a new file.
7. **Right-click a file** in the tree for **Copy / Paste / Rename / Delete**.

OkGen warns you if you try to switch files with unsaved changes.

---

## Configuration (optional)

Three editable files live in the **`config`** folder. Edit them in any text
editor, then **restart the app** to apply changes.

| File | Controls |
|---|---|
| `config/chains.yaml` | Chain code → brand name + badge color (01 = TJMAXX, 02 = Marshalls, 03 = Homegoods, 04 = Winners, 06 = HomeSense) |
| `config/display.yaml` | How coded values appear as friendly text (the dropdown labels), per chain / layout / format |
| `config/limits.yaml` | Max rows per section (e.g. StyleHeader Lane = 10) |

Tip: in `display.yaml`, put quotes around short/number codes (e.g. `"1"`, `"04"`)
and you can match several chains/layouts at once with a list, e.g.
`chain: ["03", "04"]`.

---

## Troubleshooting

- **`python` not recognized:** Python wasn't added to PATH. Re-run the Python
  installer and tick **"Add Python to PATH"**, then try `run.bat` again.
- **A previous run failed partway:** just run `run.bat` again — it detects a
  half-set-up environment and finishes installing. (No need to delete anything.)
- **`run.bat` says "Offline install failed":** your Python version isn't covered
  by the bundled packages (they cover Python 3.9–3.13, 64-bit). Run
  `python --version` and send it to whoever gave you OkGen so they can add your
  version to the bundle.
- **"Address already in use" / port 8000 busy:** another copy is running. Close
  that window, or start on another port — after the manual `activate` +
  `set PYTHONPATH=src` steps run: `python -m okgen.cli serve --port 8001` and open
  that address.
- **Open Folder dialog:** it's an Explorer-style window — go *into* the folder you
  want and click **Open** (the "Select this folder" placeholder in the filename
  box is normal). If it ever opens behind the browser, check the taskbar / Alt-Tab,
  or as a fallback paste the folder path into the box at the top and press Enter.
- **Nothing in the tree:** the folder has no `.OK` files (only `.OK` files show).
- **Made a bad edit:** every save leaves a `.bak` copy next to the file — rename
  it back to recover.

---

## Notes

- This is a **local** tool — it serves only to your own machine (`127.0.0.1`).
- The layout definitions (the `.xlsx` files in `data/OkFileDefinitions`) ship with
  the app; you normally don't touch them.
- Bundled packages live in `vendor\wheels` (Python 3.9–3.13, 64-bit Windows).
- **Branding (optional):** drop a `favicon.ico` and/or `logo.png` into
  `src\okgen\web\static\` — the browser-tab icon and the header logo pick them up
  automatically (the header falls back to the "OkGen" text if no `logo.png`).

### Project documentation
- **IMPLEMENTATION_PLAN.md** — technical design, data model, module map.
- **ARCHITECTURE.md** — architecture diagrams (rendered on GitHub).
- **DEVELOPMENT_PROCESS.md** — how this app was built with Claude Code (a
  repeatable procedure).
- **docs/SESSION_TRANSCRIPT.md** — the full development session, readable
  (raw machine log: `docs/session/session.jsonl`).

---

### For developers / other platforms

`run.sh` (macOS/Linux) and an online editable install (`pip install -e .`) are
available for development. The bundled `vendor\wheels` are Windows-only; on other
platforms install dependencies from PyPI instead. Run the tests with
`python -m pytest tests/ -q`.
