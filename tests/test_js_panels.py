"""Run the browser-side smoke tests (Node + a dependency-free DOM stub).

The Python suite covers the server only, so client-side logic used to rest on
code review alone — which is how a temporal-dead-zone error shipped that
silently aborted the whole Generate panel render. These tests execute the real
``app.js`` in Node against a stub DOM and assert the panel builds and its
buttons actually issue requests.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent / "js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.mark.parametrize("script", sorted(p.name for p in JS_DIR.glob("test_*.js")))
def test_js_panel_smoke(script):
    result = subprocess.run(
        [NODE, str(JS_DIR / script)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(f"{script} failed:\n{result.stdout}\n{result.stderr}")
