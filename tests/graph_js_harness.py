"""Run the site's graph scripts under ``node`` against the DOM stand-in in ``tests/fixtures/graph_js/dom_stub.js``.

``run_graph_js(scripts, body)`` loads each script (paths under ``webapp/static/js``) into one sandbox whose ``window``
is its global object, then runs ``body`` (JavaScript with ``dom``, ``all`` and the sandbox's globals in scope) and
returns whatever ``body`` assigns to ``result`` as parsed JSON.  ``check_syntax(script)`` is ``node --check`` of one
script and ``small_ego_payload()`` a six-node ego payload for the entity views.  Tests are skipped when ``node`` is missing.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from database.entity_network import EDGE_TYPE_DEFINITIONS

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


def check_syntax(script: str) -> subprocess.CompletedProcess:
    """``node --check`` of one script under webapp/static/js (skips the test when node is missing)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the graph script harness needs it")
    return subprocess.run([node, "--check", str(JS_DIR / script)], capture_output=True, text=True, timeout=60)


def run_graph_js(scripts: list[str], body: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the graph script harness needs it")
    program = _HEAD % {"stub": json.dumps(str(STUB)), "scripts": json.dumps([str(JS_DIR / name) for name in scripts])}
    program += body + "\nconsole.log(JSON.stringify(result));\n"
    done = subprocess.run([node, "-"], input=program, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout.strip().splitlines()[-1])


def small_ego_payload():
    """A six-node ego payload in the shape of ``database.entity_network.get_entity_network``: one entity committee, its
    candidate, a donor, a vendor and two outside spenders (one for, one against the candidate)."""
    def edge(source, target, edge_type, weight, **extra):
        return {"source": source, "target": target, "edge_type": edge_type, "weight": weight, "count": 1,
                **EDGE_TYPE_DEFINITIONS[edge_type], **extra}

    def node(node_id, node_type, role, total, label):
        return {"id": node_id, "label": label, "node_type": node_type, "role": role, "total_amount": total}

    edges = [
        edge("donor:ann|lee", "committee:1", "donor_committee", 900.0),
        edge("committee:1", "vendor:acme", "committee_vendor", 400.0),
        edge("committee:9", "candidate:5", "independent_expenditure", 700.0, support_oppose="S", signed_weight=700.0),
        edge("committee:8", "candidate:5", "independent_expenditure", 300.0, support_oppose="O", signed_weight=-300.0),
        edge("committee:1", "candidate:5", "committee_candidate", 900.0),
    ]
    nodes = [
        node("committee:1", "committee", "entity", 2200.0, "Friends of Pat"), node("candidate:5", "candidate", "candidate", 1900.0, "Pat Roe"),
        node("donor:ann|lee", "donor", "donor", 900.0, "Ann Lee"), node("vendor:acme", "vendor", "vendor", 400.0, "Acme Print"),
        node("committee:9", "committee", "ie_spender", 700.0, "Pro Pat PAC"), node("committee:8", "committee", "ie_spender", 300.0, "Stop Pat PAC"),
    ]
    summary = {"truncated": {"donors": 0, "vendors": 0, "transfers": 0, "ie_spenders": 0}, "edge_type_definitions": EDGE_TYPE_DEFINITIONS}
    return {"nodes": nodes, "edges": edges, "summary": summary}
