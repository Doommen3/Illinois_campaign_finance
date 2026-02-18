from pathlib import Path
import json

from src.triple_pipeline_viz import build_triple_pipeline


def test_triple_pipeline_build(tmp_path):
    fixture_dir = Path("tests/fixtures/triple_pipeline")
    timings = build_triple_pipeline(
        edges_path=fixture_dir / "triple_pipeline_edges.csv",
        entities_path=fixture_dir / "triple_pipeline_entities.csv",
        timeline_path=fixture_dir / "triple_pipeline_timeline.csv",
        top_path=fixture_dir / "triple_pipeline_top_entities.csv",
        outdir=tmp_path,
        build_pdf=False,
    )

    assert (tmp_path / "profile.json").exists()
    assert (tmp_path / "profile.md").exists()
    assert (tmp_path / "graph.json").exists()
    assert (tmp_path / "ego" / "index.json").exists()
    assert (tmp_path / "timeline_index.json").exists()

    graph = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert graph["nodes"]
    assert graph["edges"]

    edge = graph["edges"][0]
    assert "evidence_table" in edge
    assert "evidence_ref" in edge

    assert timings.parse_seconds >= 0
