"""Experimental Visualization Lab routes."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_file, url_for

viz_lab_bp = Blueprint("viz_lab", __name__)

GEOMETRY_BUILD_COMMAND = (
    "python3 scripts/build_geometry.py --maps-root /Users/devin/Illinois_campaign_finance/Maps"
)
GEOMETRY_LAYERS = {
    "congressional": {
        "label": "Congressional (CD119)",
        "filename": "il_cd119.topo.json",
    },
    "state-house": {
        "label": "State House (SLDL)",
        "filename": "il_sldl.topo.json",
    },
    "state-senate": {
        "label": "State Senate (SLDU)",
        "filename": "il_sldu.topo.json",
    },
}


def _artifact_root() -> Path:
    return Path(current_app.root_path).parent / "artifacts" / "triple_pipeline"


def _geometry_root() -> Path:
    return Path(current_app.root_path).parent / "data" / "geometry" / "il"


@lru_cache(maxsize=4)
def _cached_json(path_str: str, mtime: float) -> dict:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def _load_cached_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return _cached_json(str(path), path.stat().st_mtime)


@viz_lab_bp.before_request
def _guard_feature_flag():
    if not bool(current_app.config.get("EXPERIMENTAL_VIZ_LAB_ENABLED", False)):
        abort(404)


def _redirect_to_combined_viz_lab():
    params = {}
    for key in ("period", "date_from", "date_to"):
        value = (request.args.get(key) or "").strip()
        if value:
            params[key] = value
    return redirect(url_for("experimental.viz_lab", **params), code=302)


@viz_lab_bp.route("/")
def viz_lab_index():
    return _redirect_to_combined_viz_lab()


@viz_lab_bp.route("/triple-pipeline")
def viz_lab_triple_pipeline():
    return _redirect_to_combined_viz_lab()


@viz_lab_bp.route("/state-maps")
def viz_lab_state_maps():
    return render_template("experimental/viz_lab_state_maps.html")


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


@viz_lab_bp.route("/data/triple-pipeline/entities")
def triple_pipeline_entities():
    graph_path = _artifact_root() / "graph.json"
    graph = _load_cached_json(graph_path)
    if graph is None:
        abort(404)

    ego_index_path = _artifact_root() / "ego" / "index.json"
    ego_index = _load_cached_json(ego_index_path) or {}

    entities = []
    for node in graph.get("nodes", []):
        entity_id = str(node.get("entity_id") or node.get("id") or "").strip()
        if not entity_id:
            continue
        canonical_name = str(node.get("canonical_name") or entity_id).strip()
        try:
            node_size_score = float(node.get("node_size_score") or 0.0)
        except (TypeError, ValueError):
            node_size_score = 0.0
        rank = None
        if entity_id in ego_index:
            try:
                rank = int(ego_index[entity_id].get("rank"))
            except (TypeError, ValueError):
                rank = None
        entities.append(
            {
                "entity_id": entity_id,
                "canonical_name": canonical_name,
                "node_size_score": node_size_score,
                "rank": rank,
                "has_ego": entity_id in ego_index,
            }
        )

    entities.sort(
        key=lambda entry: (
            0 if entry["rank"] is not None else 1,
            entry["rank"] if entry["rank"] is not None else 999999,
            -entry["node_size_score"],
            entry["canonical_name"].lower(),
            entry["entity_id"],
        )
    )

    default_entity = None
    ranked_entities = [entry for entry in entities if entry["rank"] is not None]
    if ranked_entities:
        default_entity = ranked_entities[0]
    elif entities:
        default_entity = max(entities, key=lambda entry: entry["node_size_score"])

    return jsonify(
        {
            "entities": entities,
            "default_entity_id": default_entity["entity_id"] if default_entity else "",
            "default_canonical_name": default_entity["canonical_name"] if default_entity else "",
            "default_rank": default_entity["rank"] if default_entity else None,
        }
    )


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


@viz_lab_bp.route("/data/geometry/il/status")
def geometry_status():
    root = _geometry_root()
    layer_payload = {}
    all_present = True

    for layer_key, layer in GEOMETRY_LAYERS.items():
        filename = layer["filename"]
        path = root / filename
        exists = path.exists()
        all_present = all_present and exists
        layer_payload[layer_key] = {
            "layer_key": layer_key,
            "label": layer["label"],
            "filename": filename,
            "path": str(path),
            "exists": exists,
            "url": url_for("viz_lab.geometry_layer", layer_key=layer_key),
        }

    return jsonify(
        {
            "all_present": all_present,
            "layers": layer_payload,
            "build_command": GEOMETRY_BUILD_COMMAND,
        }
    )


@viz_lab_bp.route("/data/geometry/il/<layer_key>")
def geometry_layer(layer_key: str):
    layer = GEOMETRY_LAYERS.get(layer_key)
    if layer is None:
        abort(404)

    path = _geometry_root() / layer["filename"]
    if not path.exists():
        return (
            jsonify(
                {
                    "error": "missing_geometry_asset",
                    "layer_key": layer_key,
                    "label": layer["label"],
                    "filename": layer["filename"],
                    "path": str(path),
                    "build_command": GEOMETRY_BUILD_COMMAND,
                }
            ),
            404,
        )

    return send_file(path, mimetype="application/json", download_name=layer["filename"])
