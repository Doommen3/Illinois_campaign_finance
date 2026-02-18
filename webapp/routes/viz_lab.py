"""Experimental Visualization Lab routes."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

viz_lab_bp = Blueprint("viz_lab", __name__)


def _artifact_root() -> Path:
    return Path(current_app.root_path).parent / "artifacts" / "triple_pipeline"


def _json_from_path(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def _cached_json(path_str: str, mtime: float) -> dict:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def _load_cached_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return _cached_json(str(path), path.stat().st_mtime)


@viz_lab_bp.route("/")
def viz_lab_index():
    return render_template("experimental/viz_lab_index.html")


@viz_lab_bp.route("/triple-pipeline")
def viz_lab_triple_pipeline():
    return render_template("experimental/viz_lab_triple_pipeline.html")


@viz_lab_bp.route("/data/triple-pipeline/graph")
def triple_pipeline_graph():
    path = _artifact_root() / "graph.json"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="application/json", download_name="graph.json")


@viz_lab_bp.route("/data/triple-pipeline/ego")
def triple_pipeline_ego():
    entity_id = (request.args.get("entity_id") or "").strip()
    rank = (request.args.get("rank") or "").strip()
    index_path = _artifact_root() / "ego" / "index.json"
    index = _load_cached_json(index_path)
    if index is None:
        abort(404)

    target = None
    if entity_id and entity_id in index:
        target = index[entity_id]
    elif rank:
        for entry in index.values():
            if str(entry.get("rank")) == rank:
                target = entry
                break

    if target is None:
        abort(404)

    filename = target.get("filename")
    if not filename:
        abort(404)
    path = _artifact_root() / "ego" / filename
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="application/json", download_name=filename)


@viz_lab_bp.route("/data/triple-pipeline/timeline")
def triple_pipeline_timeline():
    entity_id = (request.args.get("entity_id") or "").strip()
    if not entity_id:
        return jsonify({"entity_id": entity_id, "series": []})

    index_path = _artifact_root() / "timeline_index.json"
    index = _load_cached_json(index_path)
    if index is None:
        abort(404)

    series = index.get(entity_id, [])
    return jsonify({"entity_id": entity_id, "series": series})
