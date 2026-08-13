"""Cross-layout regression audit — run identically on two tags and diff.

Exercises every read AND write path OkGen offers, over all 10 layouts (7 line-
based .OK + 3 Calgary JSON), and prints a normalised, line-oriented fingerprint.

Deliberately prints an INVENTORY of what was compared as well as the values: a
clean diff means nothing if the probe silently exercised nothing, which is the
failure mode this repo keeps hitting. Every count below should be non-zero and
identical on both sides.

Normalisation: absolute paths, temp dirs and today's date are replaced, so the
only differences left are behavioural.
"""
import datetime
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("AUDIT_ROOT")
            or Path(__file__).resolve().parents[1]).resolve()   # checkout under test
sys.path.insert(0, str(ROOT / "src"))

from okgen.api import service                                    # noqa: E402
from okgen.config import Config                                  # noqa: E402
from okgen.layout.registry import LayoutRegistry                 # noqa: E402
from okgen.detect import detect_layout                           # noqa: E402
from okgen import tosca                                          # noqa: E402

DATA = ROOT / "data" / "OkFileDefinitions"
CAL = ROOT / "tests" / "fixtures" / "calgary"
TOSCA_FIX = ROOT / "tests" / "fixtures" / "tosca"
TOSCA_CFG = ROOT / "tests" / "fixtures" / "config"

registry = LayoutRegistry.from_dir(DATA)
config = Config.load(ROOT / "config")

TODAY = datetime.date.today()
counts = {}


def bump(k, n=1):
    counts[k] = counts.get(k, 0) + n


def norm(s):
    """Strip everything that legitimately differs between two checkouts."""
    s = str(s)
    s = s.replace(str(ROOT), "<ROOT>").replace(str(ROOT).replace("\\", "/"), "<ROOT>")
    s = re.sub(r"/private/var/folders/[^\"',\s)]+", "<TMP>", s)
    s = re.sub(r"/var/folders/[^\"',\s)]+", "<TMP>", s)
    s = re.sub(r"/tmp/[A-Za-z0-9_\-./]+", "<TMP>", s)
    s = s.replace(TODAY.strftime("%m/%d/%Y"), "<TODAY>")
    s = s.replace(TODAY.strftime("%d/%m/%Y"), "<TODAY>")
    s = s.replace(TODAY.strftime("%Y%m%d"), "<TODAY8>")
    s = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?", "<STAMP>", s)
    s = re.sub(r"okgen-tosca-[A-Za-z0-9_]+", "<STAGEDIR>", s)
    return s


def emit(axis, key, value):
    bump(axis)
    print(f"{axis}|{key}|{norm(json.dumps(value, sort_keys=True, default=str))}")


def samples():
    """(layout, path) for every shipped sample, both engines."""
    out = []
    for p in sorted(DATA.glob("*.OK")):
        out.append((p.stem, p))
    for p in sorted(CAL.glob("*.json")):
        try:
            out.append((detect_layout(p).layout, p))
        except Exception as exc:                                   # noqa: BLE001
            out.append((f"UNDETECTED({exc})", p))
    return out


SAMPLES = samples()
work = Path(tempfile.mkdtemp(prefix="audit-"))


def copy(p, name=None):
    d = work / (name or p.name)
    shutil.copy2(p, d)
    return d


print("### AXIS 0: the inventory itself")
for layout, p in SAMPLES:
    emit("sample", p.name, {"layout": layout, "bytes": p.stat().st_size})

print("### AXIS 1: layout detection")
for layout, p in SAMPLES:
    try:
        d = detect_layout(p)
        emit("detect", p.name, {"layout": d.layout, "conf": getattr(d, "confidence", None)})
    except Exception as exc:                                       # noqa: BLE001
        emit("detect", p.name, {"error": str(exc)})

print("### AXIS 2: the editor view — every section, every field, every value")
for layout, p in SAMPLES:
    try:
        v = service.parse_file_view(p, registry, config)
    except Exception as exc:                                       # noqa: BLE001
        emit("view", p.name, {"error": str(exc)})
        continue
    emit("view_head", p.name, {k: v.get(k) for k in
                               ("layout", "key_field", "source", "engine", "json_mode")})
    for sec in v.get("sections", []):
        for f in sec.get("fields", []):
            emit("view_field", f"{p.name}:{sec.get('name')}.{f.get('name')}",
                 {k: f.get(k) for k in ("size", "start", "type", "hidden", "editable",
                                        "freeform", "literal", "derived", "date",
                                        "options", "value_forms", "date_example")})
        for i, rec in enumerate(sec.get("records", [])):
            emit("view_value", f"{p.name}:{sec.get('name')}[{i}]", rec.get("values"))
    emit("view_meta", p.name, {k: v.get(k) for k in
                               ("roundtrip_ok", "blank_lines_removed",
                                "lines_space_trimmed", "chain", "format",
                                "chain_info", "json_source")})
    for b in v.get("rollups", []) or []:
        emit("rollup", f"{p.name}:{b.get('field')}", b)

print("### AXIS 3: open then save with NO edits is byte-identical")
for layout, p in SAMPLES:
    c = copy(p)
    before = c.read_bytes()
    try:
        service.apply_edits(c, [], registry, backup=False, config=config)
        emit("resave", p.name, {"identical": c.read_bytes() == before,
                                "delta": len(c.read_bytes()) - len(before)})
    except Exception as exc:                                       # noqa: BLE001
        emit("resave", p.name, {"error": str(exc)})

print("### AXIS 4: bulk scope + a field write PREVIEW for every field, 4 lengths")
for layout, p in SAMPLES:
    try:
        sc = service.bulk_scope([str(p)], registry, config)
    except Exception as exc:                                       # noqa: BLE001
        emit("bulk_scope", p.name, {"error": str(exc)})
        continue
    emit("bulk_scope", p.name,
         {"layouts": sc.get("layouts"), "sources": sc.get("sources"),
          "key_fields": sc.get("key_fields"), "rollups": sc.get("rollups"),
          "files": [{k: fl.get(k) for k in ("name", "layout", "chain", "source")}
                    for fl in sc.get("files", [])]})
    # header fields are a flat list per layout; detail sections are named groups
    groups = []
    for lay, flds in (sc.get("header_fields") or {}).items():
        groups.append({"name": "Header", "fields": flds})
    for lay, secs in (sc.get("detail_sections") or {}).items():
        for d in secs:
            groups.append(d)
    for sec in groups:
        for f in sec.get("fields", []):
            emit("bulk_field", f"{p.name}:{sec.get('name')}.{f.get('name')}",
                 {k: f.get(k) for k in ("size", "date", "locked", "reason",
                                        "type", "options", "date_example")})
            for val in ("A", "1234", "ABCDEFGHIJ", ""):
                try:
                    r = service.bulk_preview([str(p)], layout, f.get("name"), val,
                                             registry, config)["results"][0]
                    emit("bulk_prev",
                         f"{p.name}:{sec.get('name')}.{f.get('name')}:{val!r}",
                         {k: r.get(k) for k in ("status", "detail", "error",
                                                "before", "after", "rollup")})
                except Exception as exc:                           # noqa: BLE001
                    emit("bulk_prev",
                         f"{p.name}:{sec.get('name')}.{f.get('name')}:{val!r}",
                         {"error": type(exc).__name__ + ": " + str(exc)})

print("### AXIS 5: row ops preview (add / delete / renumber), every section")
for layout, p in SAMPLES:
    try:
        sc = service.bulk_scope([str(p)], registry, config)
    except Exception:                                              # noqa: BLE001
        continue
    groups = [{"name": "Header"}]
    for lay, secs in (sc.get("detail_sections") or {}).items():
        groups.extend(secs)
    for sec in groups:
        # `type`, not `kind` — and the row ops are add/keep (the first cut used
        # a key the server never reads, so all 108 probes came back "error":
        # a fully vacuous axis that would still have diffed clean.
        for op in ({"type": "add", "count": 2},
                   {"type": "add", "count": 1},
                   {"type": "keep", "count": 0},
                   {"type": "keep", "count": 1}):
            try:
                r = service.bulk_op_preview([str(p)], layout, sec.get("name"), op,
                                            registry, config)
                rows = r.get("results", [{}])[0]
                emit("rowop", f"{p.name}:{sec.get('name')}:{op['type']}{op['count']}",
                     {k: rows.get(k) for k in ("status", "detail", "error",
                                               "before", "after", "rollup")})
            except Exception as exc:                               # noqa: BLE001
                emit("rowop", f"{p.name}:{sec.get('name')}:{op['type']}{op['count']}",
                     {"error": type(exc).__name__ + ": " + str(exc)})

print("### AXIS 6: rename — EVERY preset applied to EVERY layout (the cross-product)")
presets = config.rename_presets() if hasattr(config, "rename_presets") else []
if not presets:
    presets = (config.rename_config() or {}).get("presets", []) \
        if hasattr(config, "rename_config") else []
for layout, p in SAMPLES:
    try:
        emit("rename_scope", p.name,
             {"tokens": sorted((service.rename_scope([str(p)], registry, config)
                                or {}).get("tokens", []), key=str)})
    except Exception as exc:                                       # noqa: BLE001
        emit("rename_scope", p.name, {"error": str(exc)})
    for pre in presets:
        name = pre.get("name")
        parts = pre.get("parts") or pre.get("tokens") or []
        sep = pre.get("separator", "_")
        try:
            r = service.bulk_rename_preview([str(p)], parts, sep, registry, config)
            newn = [x.get("new") or x.get("to") or x.get("name")
                    for x in (r.get("results") or r.get("files") or [])]
            emit("rename", f"{p.name}:{name}", newn)
        except Exception as exc:                                   # noqa: BLE001
            emit("rename", f"{p.name}:{name}", {"error": type(exc).__name__ + ": " + str(exc)})

print("### AXIS 7: Volume Generate scope (fields offered + no-data warnings)")
for layout, p in SAMPLES:
    try:
        g = service.generate_scope([str(p)], registry, config)
        emit("gen_scope", p.name,
             {"sections": [{"name": s.get("name"),
                            "fields": [f.get("name") for f in s.get("fields", [])],
                            "rows": s.get("rows"), "has_data": s.get("has_data")}
                           for s in g.get("sections", [])],
              "no_data": g.get("no_data_templates")})
    except Exception as exc:                                       # noqa: BLE001
        emit("gen_scope", p.name, {"error": type(exc).__name__ + ": " + str(exc)})

print("### AXIS 8: .OK -> JSON conversion")
for layout, p in SAMPLES:
    try:
        sc = service.convert_scope([str(p)], registry, config)
        emit("conv_scope", p.name, {k: sc.get(k) for k in
                                    ("convertible", "layout", "reason", "count")})
        pv = service.convert_preview([str(p)], registry, config)
        emit("conv_prev", p.name, pv)
    except Exception as exc:                                       # noqa: BLE001
        emit("conv", p.name, {"error": type(exc).__name__ + ": " + str(exc)})

print("### AXIS 9: JSON source (SCAN / WMS) + unique key")
for layout, p in SAMPLES:
    try:
        emit("source", p.name, service.json_source_for(p, config))
    except Exception as exc:                                       # noqa: BLE001
        emit("source", p.name, {"error": type(exc).__name__ + ": " + str(exc)})

print("### AXIS 10: TOSCA row resolution (the only axis expected to move)")
import openpyxl                                                    # noqa: E402
tcfg = Config.load(TOSCA_CFG)
kwb = openpyxl.load_workbook(TOSCA_FIX / "laser_compare.xlsm", data_only=True, read_only=True)
kws = kwb["Key"]
for layout, p in SAMPLES:
    rows, errs = tosca.build_rows([str(p)], registry, tcfg, kws)
    emit("tosca", p.name,
         {"rows": [{k: v for k, v in r.items() if k not in ("date", "files")} for r in rows],
          "errors": [e["error"] for e in errs]})
# every Key cell, resolved by its own code — where T and J live
for col in "CDEFGHIJKLMN":
    for r in range(2, (kws.max_row or 2) + 1):
        v = kws[f"{col}{r}"].value
        if not v:
            continue
        emit("keycell", f"{col}{r}", {"raw": str(v),
                                      "resolved": tosca._format_string(kws, col, str(v)[0])})
kwb.close()

import hashlib


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def digest_norm(path):
    """Hash a produced file with live stamps normalised out.

    Converting a DistLabel writes a `now` timestamp, so the raw bytes differ
    between two runs of the SAME build — a false difference that would have
    masqueraded as a regression. Verified by running the audit twice on one
    build and requiring a zero diff before comparing tags at all.
    """
    return hashlib.sha256(
        norm(Path(path).read_text("utf-8", errors="replace")).encode()).hexdigest()[:16]


print("### AXIS 11: field writes APPLIED — hash the bytes that land on disk")
# Preview alone is not enough. A change to the write path (padding, alignment,
# terminators) is invisible to a preview probe: an earlier cut of this audit
# padded fixed-width fields on the WRONG SIDE and moved only 2 of 5,296 lines,
# because nothing here actually wrote. These axes hash the resulting file.
for layout, p in SAMPLES:
    try:
        sc = service.bulk_scope([str(p)], registry, config)
    except Exception:                                              # noqa: BLE001
        continue
    groups = [{"name": "Header", "fields": f}
              for f in (sc.get("header_fields") or {}).values()]
    for secs in (sc.get("detail_sections") or {}).values():
        groups.extend(secs)
    for sec in groups:
        for f in sec.get("fields", []):
            fname = f.get("name")
            for val in ("7", "42"):
                c = copy(p, f"w_{p.stem}_{sec.get('name')}_{fname}_{val}{p.suffix}")
                try:
                    r = service.bulk_apply([str(c)], layout, fname, val,
                                           registry, config, backup=False)
                    emit("write_field", f"{p.name}:{sec.get('name')}.{fname}:{val}",
                         {"status": (r.get("results") or [{}])[0].get("status"),
                          "sha": digest(c),
                          "size": Path(c).stat().st_size})
                except Exception as exc:                           # noqa: BLE001
                    emit("write_field", f"{p.name}:{sec.get('name')}.{fname}:{val}",
                         {"error": type(exc).__name__ + ": " + str(exc)})

print("### AXIS 12: row ops APPLIED — hash the bytes")
for layout, p in SAMPLES:
    try:
        sc = service.bulk_scope([str(p)], registry, config)
    except Exception:                                              # noqa: BLE001
        continue
    groups = [{"name": "Header"}]
    for secs in (sc.get("detail_sections") or {}).values():
        groups.extend(secs)
    for sec in groups:
        for op in ({"type": "add", "count": 2}, {"type": "keep", "count": 1},
                   {"type": "keep", "count": 0},
                   {"type": "list", "field": None, "values": "5"}):
            o = dict(op)
            if o.get("type") == "list":
                flds = sec.get("fields") or []
                if not flds:
                    continue
                o["field"] = flds[0].get("name")
            c = copy(p, f"r_{p.stem}_{sec.get('name')}_{o['type']}{o.get('count','')}"
                        f"{o.get('field','')}{p.suffix}")
            try:
                r = service.bulk_op_apply([str(c)], layout, sec.get("name"), o,
                                          registry, config, backup=False)
                emit("write_rowop",
                     f"{p.name}:{sec.get('name')}:{o['type']}{o.get('count','')}{o.get('field','')}",
                     {"status": (r.get("results") or [{}])[0].get("status"),
                      "sha": digest(c), "size": Path(c).stat().st_size})
            except Exception as exc:                               # noqa: BLE001
                emit("write_rowop",
                     f"{p.name}:{sec.get('name')}:{o['type']}{o.get('count','')}{o.get('field','')}",
                     {"error": type(exc).__name__ + ": " + str(exc)})

print("### AXIS 13: .OK -> JSON conversion APPLIED — hash the produced JSON")
for layout, p in SAMPLES:
    if p.suffix.lower() != ".ok":
        continue
    dest = Path(tempfile.mkdtemp(prefix="conv-", dir=str(work)))
    try:
        r = service.convert_apply([str(p)], registry, config, dest=str(dest))
        made = sorted(dest.rglob("*.json"))
        emit("write_convert", p.name,
             {"count": len(made),
              "files": [{"sha": digest_norm(m), "size": m.stat().st_size}
                        for m in made]})
    except Exception as exc:                                       # noqa: BLE001
        emit("write_convert", p.name, {"error": type(exc).__name__ + ": " + str(exc)})

print("### AXIS 14: Volume Generate APPLIED — hash every produced file")
for layout, p in SAMPLES:
    dest = Path(tempfile.mkdtemp(prefix="gen-", dir=str(work)))
    spec = {"count": 3, "dest": str(dest), "fields": [], "sections": []}
    try:
        r = service.generate_apply([str(p)], spec, registry, config)
        made = sorted(dest.rglob("*"))
        emit("write_generate", p.name,
             {"written": r.get("written"),
              "files": [{"sha": digest(m), "size": m.stat().st_size}
                        for m in made if m.is_file()]})
    except Exception as exc:                                       # noqa: BLE001
        emit("write_generate", p.name, {"error": type(exc).__name__ + ": " + str(exc)})

print("### INVENTORY (every count must be non-zero and equal on both sides)")
for k in sorted(counts):
    print(f"count|{k}|{counts[k]}")
print(f"count|TOTAL|{sum(counts.values())}")
shutil.rmtree(work, ignore_errors=True)
