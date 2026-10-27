"""The Visualization Lab nav link follows its data artifacts (plan S-S1a, item C6).

base.html shows "Visualization Lab" whenever EXPERIMENTAL_VIZ_LAB_ENABLED is on.
On prod the triple-pipeline artifacts (artifacts/triple_pipeline/graph.json) are
not deployed, so the lab's data endpoints 404 behind a visible link. Pinned
contract: the link needs BOTH the feature flag AND graph.json under
webapp.routes.viz_lab._artifact_root(), which is the single source of truth for
the artifact location (the gate must call it, not re-derive the path).
"""
from __future__ import annotations

import pytest

from database.connection import init_db
from webapp.app import create_app
import webapp.routes.viz_lab as viz_lab_routes

NAV_LABEL = "Visualization Lab"
NAV_HREF = "/experimental/viz-lab"


def get_home_html(tmp_path, monkeypatch, *, lab_enabled, artifact_files):
    """Render GET / with the lab's artifact directory redirected into tmp_path.

    artifact_files maps a path relative to the artifact directory to its text;
    None means the artifact directory does not exist at all. The geometry
    directory is always an empty tmp dir so the repo's real data/geometry files
    (the state-maps page) cannot influence the result.
    """
    artifact_dir = tmp_path / "artifacts" / "triple_pipeline"
    geometry_dir = tmp_path / "geometry"
    geometry_dir.mkdir()
    if artifact_files is not None:
        artifact_dir.mkdir(parents=True)
        for relative_path, text in artifact_files.items():
            target = artifact_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    # Patch before create_app so a gate evaluated at app-creation time sees it too.
    monkeypatch.setattr(viz_lab_routes, "_artifact_root", lambda: artifact_dir)
    monkeypatch.setattr(viz_lab_routes, "_geometry_root", lambda: geometry_dir)
    viz_lab_routes._cached_json.cache_clear()

    db_path = str(tmp_path / "viz_lab_gating.db")
    init_db(db_path)
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "ROUTE_PERF_CACHE_ENABLED": False,
            "DASHBOARD_PREWARM_ENABLED": False,
            "EXPERIMENTAL_VIZ_LAB_ENABLED": lab_enabled,
        }
    )
    response = app.test_client().get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.mark.parametrize(
    "artifact_files",
    [
        pytest.param(None, id="artifact_directory_missing"),
        pytest.param({}, id="artifact_directory_empty"),
        # The gate is graph.json specifically, not "the directory has any file".
        pytest.param({"ego/index.json": "{}"}, id="other_artifacts_but_no_graph_json"),
    ],
)
def test_nav_link_is_hidden_when_the_graph_artifact_is_absent(tmp_path, monkeypatch, artifact_files):
    html = get_home_html(tmp_path, monkeypatch, lab_enabled=True, artifact_files=artifact_files)

    assert NAV_LABEL not in html
    assert NAV_HREF not in html


def test_nav_link_is_shown_when_the_graph_artifact_exists(tmp_path, monkeypatch):
    # "{}" is falsy once parsed on purpose: the gate must test that graph.json
    # exists, not that its parsed content is truthy.
    html = get_home_html(tmp_path, monkeypatch, lab_enabled=True, artifact_files={"graph.json": "{}"})

    assert NAV_LABEL in html
    assert NAV_HREF in html


def test_nav_link_stays_hidden_when_the_feature_flag_is_off(tmp_path, monkeypatch):
    # Artifacts alone must not enable the link: with the flag off the lab's own
    # routes 404 (viz_lab.py _guard_feature_flag), so a visible link would dead-end.
    html = get_home_html(tmp_path, monkeypatch, lab_enabled=False, artifact_files={"graph.json": "{}"})

    assert NAV_LABEL not in html
    assert NAV_HREF not in html
