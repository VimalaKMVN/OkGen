# OkGen — OK File Editor

A simple desktop web app for viewing and editing fixed-width **OK files**. Open a
folder of `.OK` files, edit fields in a friendly form (with dropdowns and width
limits), and save — OkGen keeps every character position, size, and padding
correct for you, so you never have to count characters by hand.

Runs entirely on your own machine. Your files never leave your computer.

---

## What you need (one time)

- **Python 3.9 or newer.**
  - Windows: download from <https://www.python.org/downloads/> and during
    install **check "Add Python to PATH"**.
  - Check it's installed — open a terminal / Command Prompt and run:
    ```
    python --version
    ```

That's it. No internet is needed after the first setup (which downloads a couple
of small Python packages).

---

## Quick start (recommended)

1. **Unzip** the OkGen folder anywhere (e.g. your Desktop).
2. Double-click the launcher:
   - **Windows:** `run.bat`
   - **macOS / Linux:** `run.sh` (or run `./run.sh` in a terminal)
3. The first run sets everything up automatically, then your browser opens to
   **http://127.0.0.1:8000**. (If it doesn't open, paste that address into your
   browser.)
4. To stop the app, close the terminal window (or press **Ctrl + C** in it).

The launcher creates a private environment inside the folder the first time and
reuses it afterwards, so later launches are fast.

---

## Manual start (if you prefer the command line)

Open a terminal **in the OkGen folder** and run:

**Windows**
```bat
py -m venv .venv
.venv\Scripts\activate
pip install -e .
okgen serve
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
okgen serve
```

Then open <http://127.0.0.1:8000>. Next time you only need the activate + serve
steps:
```
.venv\Scripts\activate   &  okgen serve      (Windows)
source .venv/bin/activate && okgen serve     (macOS/Linux)
```

---

## Using the app

1. Click **Open Folder…** and pick the folder that contains your `.OK` files.
   (Only `.OK` files are shown; other files are hidden.)
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

- **`python` / `okgen` not recognized (Windows):** Python wasn't added to PATH.
  Re-run the Python installer and tick "Add Python to PATH", or use the `run.bat`
  launcher which handles the environment for you.
- **"Address already in use" / port 8000 busy:** another copy is running. Close
  it, or start on another port: `okgen serve --port 8001` and open that address.
- **Open Folder dialog doesn't appear:** it opens on the machine running the app;
  bring it to the front (it may be behind the browser). As a fallback, paste the
  folder path into the box and press Enter.
- **Nothing in the tree:** the folder has no `.OK` files (only `.OK` files show).
- **Made a bad edit:** every save leaves a `.bak` copy next to the file — rename
  it back to recover.

---

## Notes

- This is a **local** tool — it serves only to your own machine (`127.0.0.1`).
- The layout definitions (the `.xlsx` files in `data/OkFileDefinitions`) ship with
  the app; you normally don't touch them.
- For the technical design, see **IMPLEMENTATION_PLAN.md**.
