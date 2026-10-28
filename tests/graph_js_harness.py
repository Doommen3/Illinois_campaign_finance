"""Run the site's graph scripts under ``node`` against the DOM stand-in in ``tests/fixtures/graph_js/dom_stub.js``.

``run_graph_js(scripts, body)`` loads each script (paths under ``webapp/static/js``) into one sandbox whose ``window``
is its global object, then runs ``body`` (JavaScript with ``dom``, ``all`` and the sandbox's globals in scope) and
returns whatever ``body`` assigns to ``result`` as parsed JSON.  The test is skipped when ``node`` is missing.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "webapp" / "static" / "js"
STUB = ROOT / "tests" / "fixtures" / "graph_js" / "dom_stub.js"

_HEAD = """
const vm = require("vm"), fs = require("fs");
const { makeDom, all } = require(%(stub)s);
const dom = makeDom();
const sandbox = { document: dom.document, console }; sandbox.window = sandbox;
vm.createContext(sandbox);
for (const file of %(scripts)s) { vm.runInContext(fs.readFileSync(file, "utf8"), sandbox, { filename: file }); }
let result;
"""


def run_graph_js(scripts: list[str], body: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the graph script harness needs it")
    program = _HEAD % {"stub": json.dumps(str(STUB)), "scripts": json.dumps([str(JS_DIR / name) for name in scripts])}
    program += body + "\nconsole.log(JSON.stringify(result));\n"
    done = subprocess.run([node, "-"], input=program, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout.strip().splitlines()[-1])
