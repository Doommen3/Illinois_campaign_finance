"""Tests for Visualization Lab routes under /experimental/viz-lab."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from database.connection import init_db
from webapp.app import create_app
import webapp.routes.viz_lab as viz_lab_routes


@pytest.fixture(autouse=True)
def _clear_viz_lab_json_cache():
    viz_lab_routes._cached_json.cache_clear()
    yield
    viz_lab_routes._cached_json.cache_clear()


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "test_viz_lab_routes.db")
    init_db(db_path)
    return create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "ROUTE_PERF_CACHE_ENABLED": False,
            "DASHBOARD_PREWARM_ENABLED": False,
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


def test_viz_lab_index_route_redirects_to_combined_page(client):
    response = client.get("/experimental/viz-lab/?period=all")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/experimental/viz-lab?period=all")


def test_combined_viz_lab_lists_state_maps_entry(client):
    response = client.get("/experimental/viz-lab?period=all")
    assert response.status_code == 200
    assert b"Visualization Lab" in response.data
    assert b"Illinois State Maps" in response.data
    assert b"/experimental/viz-lab/state-maps" in response.data


def test_triple_pipeline_route_redirects_to_combined_lab(client):
    response = client.get("/experimental/viz-lab/triple-pipeline?period=all")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/experimental/viz-lab?period=all")


def test_triple_pipeline_entities_endpoint_uses_ranked_default(client, tmp_path: Path, monkeypatch):
    artifact_root = tmp_path / "artifacts" / "triple_pipeline"
    (artifact_root / "ego").mkdir(parents=True, exist_ok=True)

    graph_payload = {
        "metadata": {},
        "nodes": [
            {
                "id": "node-alpha",
                "entity_id": "entity:alpha",
                "canonical_name": "Alpha Entity",
                "node_size_score": 2.0,
            },
            {
                "id": "node-beta",
                "entity_id": "entity:beta",
                "canonical_name": "Beta Entity",
                "node_size_score": 9.0,
            },
        ],
        "edges": [],
    }
    (artifact_root / "graph.json").write_text(json.dumps(graph_payload), encoding="utf-8")

    ego_index_payload = {
        "entity:alpha": {
            "rank": 1,
            "filename": "01_alpha.json",
            "canonical_name": "Alpha Entity",
        }
    }
    (artifact_root / "ego" / "index.json").write_text(json.dumps(ego_index_payload), encoding="utf-8")

    monkeypatch.setattr(viz_lab_routes, "_artifact_root", lambda: artifact_root)

    response = client.get("/experimental/viz-lab/data/triple-pipeline/entities")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["default_entity_id"] == "entity:alpha"
    assert len(payload["entities"]) == 2
    alpha = next(item for item in payload["entities"] if item["entity_id"] == "entity:alpha")
    assert alpha["canonical_name"] == "Alpha Entity"
    assert alpha["rank"] == 1
    assert alpha["has_ego"] is True


def test_state_maps_route_renders(client):
    response = client.get("/experimental/viz-lab/state-maps?period=all")
    assert response.status_code == 200
    assert b"Illinois State Maps" in response.data
    assert b'id="il-map-layer"' in response.data


def test_geometry_status_reports_missing_assets(client, tmp_path: Path, monkeypatch):
    geometry_root = tmp_path / "data" / "geometry" / "il"
    geometry_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(viz_lab_routes, "_geometry_root", lambda: geometry_root)

    response = client.get("/experimental/viz-lab/data/geometry/il/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["all_present"] is False
    assert payload["build_command"] == (
        "python3 scripts/build_geometry.py --maps-root /Users/devin/Illinois_campaign_finance/Maps"
    )
    assert payload["layers"]["congressional"]["exists"] is False
    assert payload["layers"]["state-house"]["exists"] is False
    assert payload["layers"]["state-senate"]["exists"] is False


def test_geometry_layer_endpoint_serves_topojson(client, tmp_path: Path, monkeypatch):
    geometry_root = tmp_path / "data" / "geometry" / "il"
    geometry_root.mkdir(parents=True, exist_ok=True)
    layer_path = geometry_root / "il_cd119.topo.json"
    layer_path.write_text(
        json.dumps({"type": "Topology", "objects": {"il_cd119": {"type": "GeometryCollection", "geometries": []}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(viz_lab_routes, "_geometry_root", lambda: geometry_root)

    response = client.get("/experimental/viz-lab/data/geometry/il/congressional")
    assert response.status_code == 200
    assert response.is_json
    payload = response.get_json()
    assert payload["type"] == "Topology"
    assert "il_cd119" in payload["objects"]


def test_geometry_layer_endpoint_returns_actionable_missing_payload(client, tmp_path: Path, monkeypatch):
    geometry_root = tmp_path / "data" / "geometry" / "il"
    geometry_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(viz_lab_routes, "_geometry_root", lambda: geometry_root)

    response = client.get("/experimental/viz-lab/data/geometry/il/state-house")
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["error"] == "missing_geometry_asset"
    assert payload["layer_key"] == "state-house"
    assert payload["filename"] == "il_sldl.topo.json"
    assert payload["build_command"] == (
        "python3 scripts/build_geometry.py --maps-root /Users/devin/Illinois_campaign_finance/Maps"
    )
