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
from okgen.layout.validate import validate_layout

DEFAULT_DATA_DIR = os.environ.get(
    "OKGEN_DATA_DIR",
    "/Users/praveendx/repos/OkGenData/OkFileDefinitions",
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
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
