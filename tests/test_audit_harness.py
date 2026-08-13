"""The cross-layout audit harness must not silently go vacuous.

``tests/audit_layouts.py`` answers *"did this change disturb anything else?"* by
being run against two tags and diffed. Its whole value rests on it actually
exercising the paths it claims to, and the way it fails is silent: a payload
shape changes, an axis stops producing rows, and the diff comes back CLEAN
because nothing ran. That happened on the day it was written — four axes
(`bulk_field`, `bulk_prev`, `rowop`, `rollup`) reported zero because the probe
guessed the wrong keys, and the run still looked like a pass.

So this pins the INVENTORY: every axis non-zero, all 10 layouts reached, and the
write axes present — those are the ones that catch a padding/alignment
regression, which a preview-only audit misses almost entirely (a deliberately
wrong-side pad moved 2 lines without them and 294 with).

It does not assert any VALUE. Values are the thing that legitimately changes
when behaviour changes; the diff between two tags is where they are judged.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "tests" / "audit_layouts.py"

# The axes that must produce rows, and the floor each must clear. The floors are
# deliberately well under the real counts — this guards against an axis going to
# zero or collapsing, not against the samples changing.
FLOORS = {
    "sample": 10, "detect": 10, "view_head": 10, "view_field": 500,
    "view_value": 100, "view_meta": 10, "resave": 10, "rollup": 1,
    "bulk_scope": 10, "bulk_field": 500, "bulk_prev": 2000, "rowop": 100,
    "rename_scope": 10, "rename": 100, "gen_scope": 10,
    "conv_scope": 10, "conv_prev": 10, "source": 10, "tosca": 10,
    "keycell": 100,
    # The write axes — the reason the audit can see a write-path regression.
    "write_field": 1000, "write_rowop": 100, "write_convert": 5,
    "write_generate": 10,
}


@pytest.fixture(scope="module")
def audit_output():
    if not AUDIT.is_file():
        pytest.skip("audit_layouts.py not present")
    proc = subprocess.run([sys.executable, str(AUDIT)],
                          capture_output=True, text=True, timeout=600,
                          env=dict(os.environ, AUDIT_ROOT=str(ROOT),
                                   PYTHONPATH=str(ROOT / "src")))
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return proc.stdout


def _payloads(out, axis):
    """The parsed payload of every row on one axis (`axis|key|{json}`)."""
    rows = []
    for line in out.splitlines():
        if not line.startswith(axis + "|"):
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        try:
            rows.append(json.loads(parts[2]))
        except ValueError:
            rows.append({"error": "unparseable"})
    return rows


def _is_error(payload):
    if not isinstance(payload, dict):
        return False
    return payload.get("status") == "error" or payload.get("error") not in (None, "")


def _counts(out):
    return {line.split("|")[1]: int(line.split("|")[2])
            for line in out.splitlines() if line.startswith("count|")}


def test_every_axis_produces_rows(audit_output):
    """A zero count is the same tell as a suspiciously clean diff."""
    counts = _counts(audit_output)
    missing = [a for a in FLOORS if a not in counts]
    assert not missing, f"axes produced NOTHING at all: {missing}"
    short = {a: (counts[a], f) for a, f in FLOORS.items() if counts[a] < f}
    assert not short, f"axes collapsed below their floor (count, floor): {short}"


def test_all_ten_layouts_are_reached(audit_output):
    """Both engines, every layout — the question being answered is about all 10."""
    seen = {line.split('"layout": "')[1].split('"')[0]
            for line in audit_output.splitlines()
            if line.startswith("sample|") and '"layout": "' in line}
    expected = {"StyleHeader", "Preticket", "CartonLabel", "DistLabels",
                "EUPreticket", "EUStyleHeader", "EUCartonLabel",
                "CalgaryStyleHeader", "CalgaryDistLabel", "CalgaryCartonLabel"}
    assert expected <= seen, f"layouts never audited: {sorted(expected - seen)}"


def test_the_probes_get_real_answers_not_errors(audit_output):
    """A count is not enough, and this test exists because the count fooled it.

    Feeding the row-op probe a key the server never reads (``kind`` where it
    reads ``type``) left all 144 probes reporting ``"status": "error"`` — the
    inventory looked perfectly healthy and the axis was worth nothing. So the
    OUTCOMES have to be checked too: most probes must come back with a real
    verdict rather than an error.
    """
    for axis, floor in (("bulk_prev", 0.5), ("rowop", 0.5), ("rename", 0.5)):
        rows = _payloads(audit_output, axis)
        assert rows, f"{axis}: no probes at all"
        # Parse, don't substring-match: every payload carries an "error" KEY
        # (usually null), so `'"error"' in line` matched all 3,028 healthy
        # bulk previews and reported the axis as totally broken.
        good = sum(1 for r in rows if not _is_error(r))
        assert good > len(rows) * floor, (
            f"{axis}: only {good} of {len(rows)} probes got a real answer — "
            f"the probe is most likely calling with the wrong payload shape")


def test_the_write_axes_actually_write(audit_output):
    """Every applied-write probe erroring would leave the audit blind while its
    counts still looked healthy — so require most of them to have produced a
    hash of real bytes."""
    for axis in ("write_field", "write_rowop", "write_generate"):
        rows = [l for l in audit_output.splitlines() if l.startswith(axis + "|")]
        hashed = [l for l in rows if '"sha"' in l]
        assert len(hashed) > len(rows) * 0.5, (
            f"{axis}: only {len(hashed)} of {len(rows)} probes wrote anything")


def test_the_audit_is_deterministic(audit_output):
    """Two runs of the SAME build must be identical, or a live timestamp shows
    up as a behavioural difference. Conversion writes a `now` stamp, which is
    exactly what caught this out."""
    proc = subprocess.run([sys.executable, str(AUDIT)],
                          capture_output=True, text=True, timeout=600,
                          env=dict(os.environ, AUDIT_ROOT=str(ROOT),
                                   PYTHONPATH=str(ROOT / "src")))
    assert proc.returncode == 0
    a = audit_output.splitlines()
    b = proc.stdout.splitlines()
    diff = [x for x, y in zip(a, b) if x != y]
    assert not diff, f"audit is not deterministic; first drift: {diff[:3]}"
