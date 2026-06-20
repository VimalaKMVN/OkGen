"""Command-line entry points for OkGen.

Phase 1 commands:
    okgen compile   Compile xlsx layout definitions -> JSON + validation report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from okgen.detect import detect_layout
from okgen.layout.compiler import compile_dir
from okgen.layout.registry import LayoutRegistry
from okgen.layout.validate import validate_layout
from okgen.okfile import parse_okfile

# Base layout/sample data ships in the repo under data/OkFileDefinitions.
# Override with the OKGEN_DATA_DIR env var to point elsewhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = os.environ.get(
    "OKGEN_DATA_DIR",
    str(_REPO_ROOT / "data" / "OkFileDefinitions"),
)


def _cmd_compile(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not data_dir.is_dir():
        print(f"error: data dir not found: {data_dir}", file=sys.stderr)
        return 2

    layouts = compile_dir(data_dir)
    if not layouts:
        print(f"error: no .xlsx files in {data_dir}", file=sys.stderr)
        return 2

    total_mismatch = 0
    print(f"Compiling layouts from {data_dir}\n")
    for layout in layouts:
        out_path = out_dir / f"{layout.name}.json"
        out_path.write_text(json.dumps(layout.to_dict(), indent=2), encoding="utf-8")

        report = validate_layout(layout)
        total_mismatch += report.mismatch
        flag = "OK " if report.passed else "FAIL"
        print(
            f"[{flag}] {layout.name:<14} "
            f"sections={len(layout.sections)} "
            f"fields ok={report.ok} mismatch={report.mismatch} skipped={report.skipped}"
            f"  -> {out_path}"
        )
        if layout.issues:
            for iss in layout.issues:
                print(f"        layout issue: {iss}")
        for sec in layout.sections:
            for iss in sec.issues:
                print(f"        [{sec.name}] {iss}")
        if args.verbose or not report.passed:
            for c in report.checks:
                if c.status == "mismatch":
                    print(
                        f"        MISMATCH [{c.section}] {c.field} "
                        f"@{c.start} size {c.size}: "
                        f"expected={c.expected!r} actual={c.actual!r}"
                    )
                elif args.verbose and c.status == "skipped":
                    print(f"        skipped  [{c.section}] {c.field}: {c.note}")

    print(f"\nDone. {len(layouts)} layouts compiled to {out_dir}/. "
          f"Total field mismatches: {total_mismatch}.")
    return 0 if total_mismatch == 0 else 1


def _cmd_detect(args: argparse.Namespace) -> int:
    files = [Path(p) for p in args.files]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        print(f"error: file(s) not found: {', '.join(missing)}", file=sys.stderr)
        return 2

    any_unmatched = False
    for p in files:
        result = detect_layout(p)
        layout = result.layout or "UNKNOWN"
        any_unmatched = any_unmatched or not result.matched
        print(f"{p.name:<22} -> {layout:<14} ({result.reason})")
        if args.verbose:
            print(f"    marker={result.marker!r} header={result.header[:40]!r}")
    return 0 if not any_unmatched else 1


def _cmd_parse(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    registry = LayoutRegistry.from_dir(args.data_dir)
    okf = parse_okfile(path, registry=registry)

    print(f"{path.name}: layout={okf.layout.name}  records={len(okf.records)}")

    # Byte-exact round-trip check.
    roundtrip_ok = okf.to_bytes() == path.read_bytes()
    print(f"round-trip: {'IDENTICAL' if roundtrip_ok else 'DIFFERS'}")

    print("sections:")
    for name, recs in okf.sections().items():
        sec = recs[0].section if recs and recs[0].section else None
        ignored = f"  (ignored: {', '.join(sec.ignored_fields)})" if sec and sec.ignored_fields else ""
        print(f"  {name:<14} {len(recs)} record(s){ignored}")

    if args.show:
        limit = args.limit if args.limit and args.limit > 0 else None
        for name, recs in okf.sections().items():
            shown = recs if limit is None else recs[:limit]
            print(f"\n[{name}] {len(recs)} record(s):")
            if len(recs) == 1:
                # Single record (e.g. Header): one field per line.
                for fname, val in recs[0].values().items():
                    print(f"    {fname:<22} = {val!r}")
            else:
                # Repeating section: one compact line per record, all records.
                field_names = [f.name for f in recs[0].section.fields] if recs[0].section else []
                for r in shown:
                    pairs = " | ".join(f"{fn}={r.get(fn)!r}" for fn in field_names)
                    print(f"    #{r.index:<4} {pairs}")
                if limit is not None and len(recs) > limit:
                    print(f"    ... ({len(recs) - limit} more; use --limit 0 to show all)")

    return 0 if roundtrip_ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="okgen", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("compile", help="Compile xlsx layouts to JSON + validate")
    c.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                   help=f"dir with *.xlsx layouts (default: {DEFAULT_DATA_DIR})")
    c.add_argument("--out", default="layouts", help="output dir for JSON (default: layouts)")
    c.add_argument("-v", "--verbose", action="store_true", help="show skipped checks too")
    c.set_defaults(func=_cmd_compile)

    d = sub.add_parser("detect", help="Detect which layout an OK file uses")
    d.add_argument("files", nargs="+", help="path(s) to .OK file(s)")
    d.add_argument("-v", "--verbose", action="store_true", help="show marker/header")
    d.set_defaults(func=_cmd_detect)

    p = sub.add_parser("parse", help="Parse an OK file and verify byte-exact round-trip")
    p.add_argument("file", help="path to a .OK file")
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                   help=f"dir with *.xlsx layouts (default: {DEFAULT_DATA_DIR})")
    p.add_argument("--show", action="store_true", help="print every record's fields per section")
    p.add_argument("--limit", type=int, default=0,
                   help="max records shown per multi-record section (0 = all)")
    p.set_defaults(func=_cmd_parse)

    s = sub.add_parser("serve", help="Run the editor web app (Flask)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    s.add_argument("--config-dir", default=None)
    s.add_argument("--debug", action="store_true", help="Flask debug/reload mode")
    s.set_defaults(func=_cmd_serve)
    return parser


def _cmd_serve(args: argparse.Namespace) -> int:
    from okgen.web.app import create_app

    app = create_app(data_dir=args.data_dir, config_dir=args.config_dir)
    print(f"OkGen editor on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
