# OkGen — Architecture

Diagrams render automatically on GitHub (Mermaid). For prose, see
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## 1. Components & layers

```mermaid
flowchart TB
    subgraph Browser["🌐 Browser (local)"]
        UI["index.html + app.js + styles.css<br/>VSCode-style tree · section editor"]
    end

    subgraph Flask["🐍 Flask app — okgen/web/app.py"]
        HTML["GET / → HTML shell"]
        API["JSON API /api/*<br/>tree · parse · save · record/add · record/delete<br/>file/copy·rename·delete · browse-folder · chains"]
    end

    subgraph Service["⚙️ Service layer — okgen/api/service.py (framework-agnostic)"]
        SVC["build_tree · parse_file_view · apply_edits<br/>add_record · delete_record · file ops · browse_folder"]
    end

    subgraph Core["🧩 Core engine"]
        OKF["okfile.py<br/>parse / serialize (byte-exact)"]
        DET["detect.py<br/>layout detection + read_chain"]
        CFG["config.py<br/>chains · display labels · limits"]
        LAY["layout/*<br/>compiler · validate · registry · models"]
    end

    subgraph Disk["💾 Disk"]
        OKFILES[".OK files"]
        XLSX["data/OkFileDefinitions/*.xlsx"]
        YAML["config/*.yaml"]
    end

    UI -- "fetch JSON" --> HTML
    UI -- "fetch JSON" --> API
    API --> SVC
    SVC --> OKF
    SVC --> DET
    SVC --> CFG
    SVC --> LAY
    OKF --- OKFILES
    LAY --- XLSX
    CFG --- YAML
    DET --- OKFILES
```

**Key idea:** all real logic lives in the **service layer + core engine**, which
know nothing about Flask. The Flask layer is a thin HTTP wrapper. Swapping the
browser UI for React later means reusing the same `/api/*` endpoints — the Python
does not change.

## 2. Data: how a file becomes an editable form

```mermaid
flowchart LR
    XLSX["TJXNA_*Layout.xlsx<br/>(field defs)"] -->|compile + self-validate| LAYOUT["Layout JSON<br/>sections → fields(start,size,type)"]
    OK[".OK file"] -->|read header| DETECT{"detect layout<br/>(raw pos 4 / 5-6)"}
    DETECT --> LAYOUT
    OK -->|"split records<br/>marker → section"| RECORDS["records (raw bytes kept)"]
    LAYOUT --> SLICE["slice fields by position+size"]
    RECORDS --> SLICE
    CFG["config/display.yaml"] -->|"code → label"| SLICE
    SLICE --> VIEW["editor view JSON<br/>sections · fields · dropdowns · all records"]
    VIEW --> EDIT["user edits values"]
    EDIT -->|"overwrite only edited spans"| SAVE["save (byte-exact + .bak)"]
    SAVE --> OK
```

The position model: xlsx `Position` is 1-based **into the marker-stripped
record**; the leading marker (`|` / `#` / `&` / `¦`) shifts raw positions by 1.
Editing overwrites only a field's span, so untouched bytes round-trip exactly.

## 3. Request flow: opening and saving a file

```mermaid
sequenceDiagram
    participant U as User
    participant JS as Browser (app.js)
    participant F as Flask
    participant S as service.py
    participant D as Disk

    U->>JS: Click "Open Folder…"
    JS->>F: POST /api/browse-folder
    F->>S: browse_folder()
    S-->>F: chosen path (native OS dialog)
    JS->>F: GET /api/tree?dir=…
    F->>S: build_tree()
    S->>D: scan .OK files + read chain
    S-->>JS: tree (files + chain badges)

    U->>JS: Click a file
    JS->>F: GET /api/parse?path=…
    F->>S: parse_file_view()
    S->>D: read file + detect layout
    S-->>JS: sections, fields, dropdowns, records

    U->>JS: Edit fields, click Save
    JS->>F: POST /api/save {path, edits}
    F->>S: apply_edits() (width-validated)
    S->>D: write byte-exact + .bak
    S-->>JS: roundtrip_ok ✓
```

## 4. Detection rule (which layout?)

```mermaid
flowchart TD
    H["OK header line (raw, marker included)"] --> P4{"raw pos 4"}
    P4 -->|N| SH["StyleHeader"]
    P4 -->|Y| PT["Preticket"]
    P4 -->|7 or 9| DL["DistLabels"]
    P4 -->|else| P56{"raw pos 5–6 == 'C:'"}
    P56 -->|yes| CL["CartonLabel"]
    P56 -->|no| UNK["unknown"]
```

## 5. Build phases (each a git tag / checkpoint)

```mermaid
flowchart LR
    P1["Phase 1<br/>compiler+detect"] --> P2["Phase 2<br/>parser/serializer"]
    P2 --> P21["Phase 2.1<br/>repeating records"]
    P21 --> P3A["Phase 3a/3b<br/>config + backend"]
    P3A --> P3C["Phase 3c<br/>Flask UI"]
    P3C --> P3D["Phase 3d<br/>add-rows · limits · list config"]
    P3D --> P3E["Phase 3e<br/>folder dialog · copy/delete row"]
    P3E --> DIST["v0.2<br/>offline Windows bundle"]
```
