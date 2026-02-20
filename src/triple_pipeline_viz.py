from __future__ import annotations

import argparse
import json
import logging
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import networkx as nx
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {
    "edges": [
        "source_entity_id",
        "source_name",
        "target_entity_id",
        "target_name",
        "edge_type",
        "weight",
        "weight_unit",
        "evidence_table",
        "evidence_ref",
    ],
    "entities": [
        "entity_id",
        "canonical_name",
        "entity_type",
        "confidence_score",
        "lobby_names[]",
        "pac_names[]",
        "committee_names[]",
        "donor_names[]",
        "527_names[]",
        "addresses[]",
        "officers[]",
        "ein",
        "first_seen_date",
        "last_seen_date",
        "channel_lobby",
        "channel_527",
        "channel_campaign",
        "evidence_count_lobby",
        "evidence_count_527",
        "evidence_count_campaign",
    ],
    "timeline": [
        "entity_id",
        "canonical_name",
        "month_key",
        "lobbying_reports",
        "campaign_receipts",
        "campaign_expenditures",
        "transfers_in",
        "transfers_out",
        "irs527_expenditures",
        "irs527_contributions",
    ],
    "top": [
        "rank",
        "canonical_name",
        "channels_count",
        "confidence_score",
        "total_lobby_reports",
        "total_527_amount",
        "total_campaign_amount",
        "total_transfers_in",
        "total_transfers_out",
        "persistence_months",
        "tri_channel_premium_metrics",
    ],
}

DEFAULT_OUTDIR = Path("artifacts/triple_pipeline")
DEFAULT_SEARCH_DIRS = [Path("data/exports"), Path("artifacts/triple_pipeline"), Path("artifacts")]

EDGE_DOWNSAMPLE_THRESHOLD = 50000
DEFAULT_MAX_NODES = 450
DEFAULT_MAX_EDGES = 3500
DEFAULT_MAX_EDGES_PER_TYPE = 1500
DEFAULT_MIN_WEIGHT = 0.0
DEFAULT_EGO_COUNT = 25
DEFAULT_EGO_MAX_NODES = 120
DEFAULT_EGO_MAX_EDGES = 240
DEFAULT_SEED = 1337

CHANNEL_LABELS = ["lobby", "527", "campaign"]


@dataclass
class BuildTimings:
    parse_seconds: float
    build_seconds: float
    pdf_seconds: float


def _coerce_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _slugify(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in (text or "").strip())
    cleaned = "-".join(filter(None, cleaned.split("-")))
    return cleaned[:64] or "entity"


def _resolve_default_path(filename: str) -> Path | None:
    for base in DEFAULT_SEARCH_DIRS:
        candidate = base / filename
        if candidate.exists():
            return candidate
    return None


def _resolve_input_path(arg_value: str | None, filename: str, arg_name: str) -> Path:
    if arg_value:
        path = Path(arg_value)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        return path
    default_path = _resolve_default_path(filename)
    if default_path is None:
        raise FileNotFoundError(
            f"Missing required input {filename}. Provide --{arg_name} or place it in data/exports or artifacts."
        )
    return default_path


def _profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "row_count": int(len(df)),
        "columns": {},
    }
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        examples = []
        if not non_null.empty:
            examples = list(dict.fromkeys(non_null.astype(str).head(8).tolist()))[:3]
        null_pct = float(series.isna().mean() * 100.0) if len(series) else 0.0
        entry: dict[str, Any] = {
            "dtype": str(series.dtype),
            "null_pct": round(null_pct, 2),
            "examples": examples,
        }
        if pd.api.types.is_numeric_dtype(series):
            numeric = series.dropna().astype(float)
            if not numeric.empty:
                entry["stats"] = {
                    "min": float(numeric.min()),
                    "max": float(numeric.max()),
                    "mean": float(numeric.mean()),
                    "median": float(numeric.median()),
                }
        profile["columns"][col] = entry
    return profile


def _validate_columns(df: pd.DataFrame, expected: list[str]) -> dict[str, Any]:
    cols = list(df.columns)
    expected_set = set(expected)
    actual_set = set(cols)
    return {
        "expected": expected,
        "actual": cols,
        "set_match": actual_set == expected_set,
        "order_match": cols == expected,
        "missing": [c for c in expected if c not in actual_set],
        "extra": [c for c in cols if c not in expected_set],
    }


def _channel_mask(row: pd.Series) -> int:
    mask = 0
    if int(row.get("channel_lobby") or 0) == 1:
        mask |= 1
    if int(row.get("channel_527") or 0) == 1:
        mask |= 2
    if int(row.get("channel_campaign") or 0) == 1:
        mask |= 4
    return mask


def _primary_channel(mask: int) -> str:
    if mask & 4:
        return "campaign"
    if mask & 2:
        return "527"
    if mask & 1:
        return "lobby"
    return "unknown"


def _build_nodes(entities: pd.DataFrame, top_entities: pd.DataFrame) -> list[dict[str, Any]]:
    evidence_sum = (
        _coerce_numeric(entities["evidence_count_lobby"]).fillna(0)
        + _coerce_numeric(entities["evidence_count_527"]).fillna(0)
        + _coerce_numeric(entities["evidence_count_campaign"]).fillna(0)
    )
    entities = entities.copy()
    entities["evidence_sum"] = evidence_sum

    top_entities = top_entities.copy()
    top_entities["total_amount_score"] = (
        _coerce_numeric(top_entities.get("total_campaign_amount", 0)).fillna(0)
        + _coerce_numeric(top_entities.get("total_527_amount", 0)).fillna(0)
        + _coerce_numeric(top_entities.get("total_transfers_in", 0)).fillna(0)
        + _coerce_numeric(top_entities.get("total_transfers_out", 0)).fillna(0)
    )

    top_by_name = (
        top_entities.sort_values(
            ["channels_count", "total_amount_score", "total_lobby_reports"],
            ascending=False,
        )
        .drop_duplicates(subset=["canonical_name"], keep="first")
        .set_index("canonical_name")
    )

    nodes: list[dict[str, Any]] = []
    def _safe_int(value: Any) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    for _, row in entities.iterrows():
        evidence_counts = {
            "lobby": _safe_int(row.get("evidence_count_lobby")),
            "527": _safe_int(row.get("evidence_count_527")),
            "campaign": _safe_int(row.get("evidence_count_campaign")),
        }
        base_score = float(row.get("evidence_sum") or 0.0)
        top_row = None
        if row.get("canonical_name") in top_by_name.index:
            top_row = top_by_name.loc[row.get("canonical_name")]
        bonus = 0.0
        if top_row is not None:
            bonus = float(top_row.get("total_amount_score") or 0.0) / 1_000_000.0
            bonus += float(top_row.get("total_lobby_reports") or 0.0)
        node_size_score = math.log1p(base_score) + math.log1p(max(0.0, bonus))
        mask = _channel_mask(row)
        nodes.append(
            {
                "id": row.get("entity_id"),
                "entity_id": row.get("entity_id"),
                "canonical_name": row.get("canonical_name"),
                "entity_type": row.get("entity_type"),
                "confidence_score": float(row.get("confidence_score") or 0.0),
                "channel_lobby": int(row.get("channel_lobby") or 0),
                "channel_527": int(row.get("channel_527") or 0),
                "channel_campaign": int(row.get("channel_campaign") or 0),
                "evidence_count_lobby": evidence_counts["lobby"],
                "evidence_count_527": evidence_counts["527"],
                "evidence_count_campaign": evidence_counts["campaign"],
                "evidence_counts": evidence_counts,
                "node_channel_mask": mask,
                "node_size_score": float(node_size_score),
            }
        )
    return nodes


def _build_edges(edges: pd.DataFrame) -> list[dict[str, Any]]:
    edges = edges.copy()
    edges["weight"] = _coerce_numeric(edges["weight"]).fillna(0)
    max_by_type = edges.groupby("edge_type")["weight"].max().to_dict()

    results: list[dict[str, Any]] = []
    for _, row in edges.iterrows():
        max_weight = float(max_by_type.get(row.get("edge_type"), 0.0) or 0.0)
        strength = float(row.get("weight") or 0.0) / max_weight if max_weight > 0 else 0.0
        results.append(
            {
                "source": row.get("source_entity_id"),
                "target": row.get("target_entity_id"),
                "edge_type": row.get("edge_type"),
                "weight": float(row.get("weight") or 0.0),
                "weight_unit": row.get("weight_unit"),
                "evidence_table": row.get("evidence_table"),
                "evidence_ref": row.get("evidence_ref"),
                "edge_strength_score": strength,
            }
        )
    return results


def _filter_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    max_nodes: int,
    max_edges: int,
    max_edges_per_type: int,
    min_weight: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    nodes_sorted = sorted(nodes, key=lambda n: n.get("node_size_score", 0.0), reverse=True)
    top_nodes = {n["id"] for n in nodes_sorted[:max_nodes]}
    nodes_filtered = [n for n in nodes if n["id"] in top_nodes]

    edges_filtered = [
        e
        for e in edges
        if e.get("weight", 0.0) >= min_weight
        and e.get("source") in top_nodes
        and e.get("target") in top_nodes
    ]

    by_type: dict[str, list[dict[str, Any]]] = {}
    for edge in edges_filtered:
        by_type.setdefault(edge.get("edge_type") or "unknown", []).append(edge)

    capped_edges: list[dict[str, Any]] = []
    for edge_type, group in by_type.items():
        group_sorted = sorted(group, key=lambda e: e.get("weight", 0.0), reverse=True)
        capped_edges.extend(group_sorted[:max_edges_per_type])

    capped_edges = sorted(capped_edges, key=lambda e: e.get("weight", 0.0), reverse=True)
    if len(capped_edges) > max_edges:
        capped_edges = capped_edges[:max_edges]

    edge_node_ids = {e["source"] for e in capped_edges} | {e["target"] for e in capped_edges}
    nodes_filtered = [n for n in nodes_filtered if n["id"] in edge_node_ids]

    filtered_meta = {
        "raw_node_count": len(nodes),
        "raw_edge_count": len(edges),
        "filtered_node_count": len(nodes_filtered),
        "filtered_edge_count": len(capped_edges),
        "max_nodes": max_nodes,
        "max_edges": max_edges,
        "max_edges_per_type": max_edges_per_type,
        "min_weight": min_weight,
        "downsampled": len(capped_edges) < len(edges),
    }
    return nodes_filtered, capped_edges, filtered_meta


def _build_ego_graphs(
    center_ids: list[str],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    max_nodes: int,
    max_edges: int,
) -> dict[str, dict[str, Any]]:
    node_map = {n["id"]: n for n in nodes}
    edges_by_node: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        edges_by_node.setdefault(edge["source"], []).append(edge)
        edges_by_node.setdefault(edge["target"], []).append(edge)

    ego_graphs: dict[str, dict[str, Any]] = {}
    for center_id in center_ids:
        related_edges = edges_by_node.get(center_id, [])
        related_edges = sorted(related_edges, key=lambda e: e.get("weight", 0.0), reverse=True)
        if len(related_edges) > max_edges:
            related_edges = related_edges[:max_edges]
        node_ids = {center_id}
        for edge in related_edges:
            node_ids.add(edge["source"])
            node_ids.add(edge["target"])
        if len(node_ids) > max_nodes:
            node_ids = set(list(node_ids)[:max_nodes])
            related_edges = [
                e for e in related_edges if e["source"] in node_ids and e["target"] in node_ids
            ]
        ego_graphs[center_id] = {
            "center_id": center_id,
            "nodes": [node_map[nid] for nid in node_ids if nid in node_map],
            "edges": related_edges,
            "downsampled": len(related_edges) < len(edges_by_node.get(center_id, [])),
        }
    return ego_graphs


def _build_timeline_index(timeline: pd.DataFrame, entity_ids: set[str]) -> dict[str, Any]:
    timeline = timeline.copy()
    timeline = timeline[timeline["entity_id"].isin(entity_ids)]
    timeline["month_key"] = timeline["month_key"].astype(str)
    timeline = timeline.sort_values(["entity_id", "month_key"])\
        .fillna(0)

    index: dict[str, list[dict[str, Any]]] = {}
    for entity_id, group in timeline.groupby("entity_id"):
        records = []
        for _, row in group.iterrows():
            records.append(
                {
                    "month_key": row.get("month_key"),
                    "lobbying_reports": float(row.get("lobbying_reports") or 0.0),
                    "campaign_receipts": float(row.get("campaign_receipts") or 0.0),
                    "campaign_expenditures": float(row.get("campaign_expenditures") or 0.0),
                    "transfers_in": float(row.get("transfers_in") or 0.0),
                    "transfers_out": float(row.get("transfers_out") or 0.0),
                    "irs527_expenditures": float(row.get("irs527_expenditures") or 0.0),
                    "irs527_contributions": float(row.get("irs527_contributions") or 0.0),
                }
            )
        index[str(entity_id)] = records
    return index


def _render_network_page(ax, nodes, edges, seed: int, title: str, subtitle: str | None = None) -> dict:
    graph = nx.Graph()
    for node in nodes:
        graph.add_node(node["id"], **node)
    for edge in edges:
        graph.add_edge(edge["source"], edge["target"], **edge)

    if graph.number_of_nodes() == 0:
        ax.text(0.5, 0.5, "No graph data available", ha="center", va="center")
        ax.set_axis_off()
        return {}

    pos = nx.spring_layout(graph, seed=seed, k=1 / math.sqrt(max(graph.number_of_nodes(), 1)))
    colors = []
    for node_id in graph.nodes():
        mask = graph.nodes[node_id].get("node_channel_mask", 0)
        if mask == 7:
            colors.append("#0f766e")
        elif mask & 4:
            colors.append("#2563eb")
        elif mask & 2:
            colors.append("#7c3aed")
        elif mask & 1:
            colors.append("#b45309")
        else:
            colors.append("#64748b")

    nx.draw_networkx_edges(
        graph,
        pos,
        ax=ax,
        alpha=0.25,
        width=0.6,
        edge_color="#94a3b8",
    )
    sizes = [200 + 600 * graph.nodes[n].get("node_size_score", 0.0) for n in graph.nodes()]
    nx.draw_networkx_nodes(
        graph,
        pos,
        ax=ax,
        node_size=sizes,
        node_color=colors,
        linewidths=0.5,
        edgecolors="#0f172a",
        alpha=0.9,
    )
    ax.set_title(title, fontsize=13, fontweight="bold")
    if subtitle:
        ax.text(0.5, -0.05, subtitle, transform=ax.transAxes, ha="center", va="top", fontsize=9)
    ax.set_axis_off()
    return {"pos": pos, "graph": graph}


def _render_component_page(ax, graph: nx.Graph, pos: dict, title: str) -> None:
    if graph.number_of_nodes() == 0:
        ax.text(0.5, 0.5, "No components available", ha="center", va="center")
        ax.set_axis_off()
        return

    components = list(nx.connected_components(graph))
    components = sorted(components, key=len, reverse=True)
    comp_map = {}
    for idx, comp in enumerate(components):
        for node_id in comp:
            comp_map[node_id] = idx

    palette = [
        "#2563eb",
        "#7c3aed",
        "#0f766e",
        "#b45309",
        "#db2777",
        "#64748b",
    ]
    colors = [palette[comp_map.get(node_id, 0) % len(palette)] for node_id in graph.nodes()]
    nx.draw_networkx_edges(
        graph,
        pos,
        ax=ax,
        alpha=0.2,
        width=0.5,
        edge_color="#cbd5f5",
    )
    sizes = [200 + 600 * graph.nodes[n].get("node_size_score", 0.0) for n in graph.nodes()]
    nx.draw_networkx_nodes(
        graph,
        pos,
        ax=ax,
        node_size=sizes,
        node_color=colors,
        linewidths=0.4,
        edgecolors="#0f172a",
        alpha=0.9,
    )
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_axis_off()


def _render_flow_page(ax, nodes, edges, title: str) -> None:
    node_map = {n["id"]: n for n in nodes}
    flows: dict[tuple[str, str], float] = {}
    for edge in edges:
        source = node_map.get(edge["source"])
        target = node_map.get(edge["target"])
        if not source or not target:
            continue
        source_channel = _primary_channel(int(source.get("node_channel_mask", 0)))
        target_channel = _primary_channel(int(target.get("node_channel_mask", 0)))
        if source_channel == "unknown" or target_channel == "unknown":
            continue
        key = (source_channel, target_channel)
        flows[key] = flows.get(key, 0.0) + float(edge.get("weight", 0.0))

    channels = ["lobby", "527", "campaign"]
    x_positions = {channel: idx for idx, channel in enumerate(channels)}
    totals = {channel: 0.0 for channel in channels}
    for (src, tgt), value in flows.items():
        totals[src] += value
        totals[tgt] += value

    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_xlim(-0.5, len(channels) - 0.5)
    ax.set_ylim(0, max(totals.values()) * 1.1 + 1)

    for channel, x in x_positions.items():
        ax.bar(x, totals[channel], color="#94a3b8", width=0.3, alpha=0.4)
        ax.text(x, totals[channel] + max(totals.values()) * 0.02 + 1, channel.title(), ha="center")

    for (src, tgt), value in flows.items():
        if value <= 0:
            continue
        x0 = x_positions[src]
        x1 = x_positions[tgt]
        y0 = totals[src] * 0.6
        y1 = totals[tgt] * 0.6
        ax.plot([x0, x1], [y0, y1], linewidth=max(1.0, value / max(totals.values()) * 6), alpha=0.6)
        ax.text((x0 + x1) / 2, (y0 + y1) / 2, f"{value:,.0f}", fontsize=8, ha="center")

    ax.set_axis_off()


def _render_timeline_page(axs, timeline, top_entities) -> None:
    if timeline.empty:
        for ax in axs.flatten():
            ax.set_axis_off()
        return

    top = top_entities.head(len(axs.flatten()))
    for ax, (_, row) in zip(axs.flatten(), top.iterrows()):
        entity_id = row.get("entity_id")
        label = row.get("canonical_name")
        series = timeline[timeline["entity_id"] == entity_id]
        if series.empty:
            ax.set_axis_off()
            continue
        months = series["month_key"].astype(str)
        lobby = _coerce_numeric(series["lobbying_reports"]).fillna(0)
        campaign = _coerce_numeric(series["campaign_receipts"]).fillna(0)
        irs = _coerce_numeric(series["irs527_contributions"]).fillna(0)
        ax.plot(months, np.log1p(lobby), label="Lobby reports", color="#b45309")
        ax.plot(months, np.log1p(campaign), label="Campaign receipts", color="#2563eb")
        ax.plot(months, np.log1p(irs), label="527 contributions", color="#7c3aed")
        ax.set_title(str(label)[:36], fontsize=9)
        ax.tick_params(axis="x", labelrotation=45, labelsize=6)
        ax.tick_params(axis="y", labelsize=6)
    axs[0, 0].legend(loc="upper left", fontsize=7)


def _render_evidence_page(ax, edges, title: str) -> None:
    ax.set_title(title, fontsize=13, fontweight="bold")
    if not edges:
        ax.text(0.5, 0.5, "No edges to show", ha="center", va="center")
        ax.set_axis_off()
        return
    sample = sorted(edges, key=lambda e: e.get("weight", 0.0), reverse=True)[:12]
    rows = [
        [
            e.get("edge_type"),
            f"{e.get('weight', 0.0):,.0f}",
            e.get("evidence_table"),
            e.get("evidence_ref"),
        ]
        for e in sample
    ]
    table = ax.table(
        cellText=rows,
        colLabels=["Edge type", "Weight", "Evidence table", "Evidence ref"],
        loc="center",
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.4)
    ax.set_axis_off()


def _log_profile(profile: dict[str, Any]) -> None:
    for key in ["edges", "entities", "timeline", "top"]:
        info = profile.get(key, {})
        logger.info("Profile %s: %s rows", key, info.get("row_count"))
        for col, meta in (info.get("columns") or {}).items():
            stats = meta.get("stats")
            stats_text = ""
            if stats:
                stats_text = (
                    f" min={stats.get('min')} max={stats.get('max')} "
                    f"mean={stats.get('mean')} median={stats.get('median')}"
                )
            logger.info(
                "  - %s | dtype=%s | null%%=%s | examples=%s%s",
                col,
                meta.get("dtype"),
                meta.get("null_pct"),
                ", ".join(meta.get("examples") or []),
                stats_text,
            )


def build_triple_pipeline(
    edges_path: Path,
    entities_path: Path,
    timeline_path: Path,
    top_path: Path,
    outdir: Path,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
    max_edges_per_type: int = DEFAULT_MAX_EDGES_PER_TYPE,
    min_weight: float = DEFAULT_MIN_WEIGHT,
    ego_count: int = DEFAULT_EGO_COUNT,
    ego_max_nodes: int = DEFAULT_EGO_MAX_NODES,
    ego_max_edges: int = DEFAULT_EGO_MAX_EDGES,
    seed: int = DEFAULT_SEED,
    build_pdf: bool = True,
) -> BuildTimings:
    start_parse = time.perf_counter()
    edges = pd.read_csv(edges_path, low_memory=False)
    entities = pd.read_csv(entities_path, low_memory=False)
    timeline = pd.read_csv(timeline_path, low_memory=False)
    top_entities = pd.read_csv(top_path, low_memory=False)
    parse_seconds = time.perf_counter() - start_parse

    start_build = time.perf_counter()
    profile = {
        "edges": _profile_dataframe(edges),
        "entities": _profile_dataframe(entities),
        "timeline": _profile_dataframe(timeline),
        "top": _profile_dataframe(top_entities),
        "validation": {
            "edges": _validate_columns(edges, REQUIRED_COLUMNS["edges"]),
            "entities": _validate_columns(entities, REQUIRED_COLUMNS["entities"]),
            "timeline": _validate_columns(timeline, REQUIRED_COLUMNS["timeline"]),
            "top": _validate_columns(top_entities, REQUIRED_COLUMNS["top"]),
        },
    }
    _log_profile(profile)

    for key, validation in profile["validation"].items():
        if not validation["set_match"]:
            raise ValueError(f"{key} columns mismatch: missing={validation['missing']} extra={validation['extra']}")

    entity_ids = set(entities["entity_id"].dropna().astype(str))
    edge_sources = set(edges["source_entity_id"].dropna().astype(str))
    edge_targets = set(edges["target_entity_id"].dropna().astype(str))
    missing_sources = sorted(edge_sources - entity_ids)
    missing_targets = sorted(edge_targets - entity_ids)
    profile["validation"]["edge_entity_id_missing"] = {
        "missing_source_count": len(missing_sources),
        "missing_target_count": len(missing_targets),
        "missing_source_sample": missing_sources[:10],
        "missing_target_sample": missing_targets[:10],
    }
    if missing_sources or missing_targets:
        raise ValueError(
            f"Edge endpoints missing from entities. Missing sources={len(missing_sources)} targets={len(missing_targets)}"
        )

    timeline_missing = sorted(set(timeline["entity_id"].dropna().astype(str)) - entity_ids)
    profile["validation"]["timeline_entity_id_missing"] = {
        "missing_count": len(timeline_missing),
        "missing_sample": timeline_missing[:10],
    }
    if timeline_missing:
        raise ValueError(f"Timeline entity_id values missing from entities: {len(timeline_missing)}")

    top_entities = top_entities.copy()
    entity_lookup = (
        entities.assign(evidence_sum=_coerce_numeric(entities["evidence_count_lobby"]).fillna(0)
                        + _coerce_numeric(entities["evidence_count_527"]).fillna(0)
                        + _coerce_numeric(entities["evidence_count_campaign"]).fillna(0))
        .sort_values(["confidence_score", "evidence_sum", "entity_id"], ascending=False)
        .drop_duplicates(subset=["canonical_name"], keep="first")
        .set_index("canonical_name")
    )
    top_entities["entity_id"] = top_entities["canonical_name"].map(
        lambda name: entity_lookup.loc[name, "entity_id"] if name in entity_lookup.index else None
    )
    missing_top = top_entities["entity_id"].isna().sum()
    profile["validation"]["top_entities_missing_entity_id"] = int(missing_top)

    nodes = _build_nodes(entities, top_entities)
    edges_records = _build_edges(edges)

    filtered_nodes, filtered_edges, filter_meta = _filter_graph(
        nodes,
        edges_records,
        max_nodes=max_nodes,
        max_edges=max_edges,
        max_edges_per_type=max_edges_per_type,
        min_weight=min_weight,
    )

    if len(edges_records) > EDGE_DOWNSAMPLE_THRESHOLD:
        filter_meta["downsampled"] = True
        filter_meta["downsample_reason"] = "edge_threshold"
        filter_meta["edge_threshold"] = EDGE_DOWNSAMPLE_THRESHOLD

    entity_ids_filtered = {n["id"] for n in filtered_nodes}
    timeline_index = _build_timeline_index(timeline, entity_ids_filtered)

    top_entities_ranked = top_entities.dropna(subset=["entity_id"]).copy()
    for col in [
        "channels_count",
        "total_campaign_amount",
        "total_527_amount",
        "total_transfers_in",
        "total_transfers_out",
        "total_lobby_reports",
    ]:
        top_entities_ranked[col] = _coerce_numeric(top_entities_ranked[col]).fillna(0)
    top_entities_ranked = top_entities_ranked.sort_values(
        [
            "channels_count",
            "total_campaign_amount",
            "total_527_amount",
            "total_transfers_in",
            "total_transfers_out",
            "total_lobby_reports",
        ],
        ascending=False,
    )
    top_entities_ranked = top_entities_ranked.head(ego_count)

    ego_graphs = _build_ego_graphs(
        top_entities_ranked["entity_id"].astype(str).tolist(),
        nodes,
        edges_records,
        max_nodes=ego_max_nodes,
        max_edges=ego_max_edges,
    )

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "ego").mkdir(parents=True, exist_ok=True)

    with (outdir / "profile.json").open("w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, default=str)

    profile_md_lines = ["# Triple Pipeline Profile", ""]
    for key in ["edges", "entities", "timeline", "top"]:
        info = profile[key]
        profile_md_lines.append(f"## {key.title()} ({info['row_count']} rows)")
        profile_md_lines.append("")
        for col, meta in info["columns"].items():
            stats = meta.get("stats")
            stats_text = ""
            if stats:
                stats_text = (
                    f" | stats: min={stats.get('min')}, max={stats.get('max')}, "
                    f"mean={stats.get('mean')}, median={stats.get('median')}"
                )
            profile_md_lines.append(
                f"- `{col}` | dtype: {meta['dtype']} | null%: {meta['null_pct']} | "
                f"examples: {', '.join(meta['examples'])}{stats_text}"
            )
        profile_md_lines.append("")
    profile_md_lines.append("## Validation")
    profile_md_lines.append("" )
    for key, validation in profile["validation"].items():
        profile_md_lines.append(f"- `{key}`: {validation}")
    (outdir / "profile.md").write_text("\n".join(profile_md_lines), encoding="utf-8")

    graph_payload = {
        "metadata": {
            "seed": seed,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "filters": filter_meta,
            "source_files": {
                "edges": str(edges_path),
                "entities": str(entities_path),
                "timeline": str(timeline_path),
                "top": str(top_path),
            },
        },
        "nodes": filtered_nodes,
        "edges": filtered_edges,
    }

    with (outdir / "graph.json").open("w", encoding="utf-8") as f:
        json.dump(graph_payload, f, indent=2)

    ego_index: dict[str, Any] = {}
    for rank, row in enumerate(top_entities_ranked.itertuples(index=False), start=1):
        entity_id = str(row.entity_id)
        slug = _slugify(str(row.canonical_name))
        filename = f"{rank:02d}_{slug}.json"
        ego_index[entity_id] = {
            "rank": rank,
            "filename": filename,
            "canonical_name": row.canonical_name,
        }
        ego_payload = ego_graphs.get(entity_id, {"center_id": entity_id, "nodes": [], "edges": []})
        ego_payload["metadata"] = {
            "rank": rank,
            "canonical_name": row.canonical_name,
        }
        with (outdir / "ego" / filename).open("w", encoding="utf-8") as f:
            json.dump(ego_payload, f, indent=2)

    with (outdir / "ego" / "index.json").open("w", encoding="utf-8") as f:
        json.dump(ego_index, f, indent=2)

    with (outdir / "timeline_index.json").open("w", encoding="utf-8") as f:
        json.dump(timeline_index, f, indent=2)

    build_seconds = time.perf_counter() - start_build

    start_pdf = time.perf_counter()
    if build_pdf:
        random.seed(seed)
        np.random.seed(seed)
        plt.rcParams.update({"font.size": 9})
        report_path = outdir / "triple_pipeline_viz_report.pdf"
        with PdfPages(report_path) as pdf:
            # Page 1: Executive summary
            fig, ax = plt.subplots(figsize=(11, 8.5))
            ax.set_axis_off()
            first_seen = pd.to_datetime(entities["first_seen_date"], errors="coerce")
            last_seen = pd.to_datetime(entities["last_seen_date"], errors="coerce")
            coverage_start = first_seen.min()
            coverage_end = last_seen.max()
            if pd.isna(coverage_start):
                coverage_start = "Unknown"
            else:
                coverage_start = coverage_start.date().isoformat()
            if pd.isna(coverage_end):
                coverage_end = "Unknown"
            else:
                coverage_end = coverage_end.date().isoformat()
            tri_channel = [n for n in nodes if n.get("node_channel_mask") == 7]
            ax.text(0.02, 0.95, "Triple Pipeline Executive Summary", fontsize=16, fontweight="bold")
            ax.text(0.02, 0.9, f"Entities: {len(nodes):,}")
            ax.text(0.02, 0.86, f"Edges: {len(edges_records):,}")
            ax.text(0.02, 0.82, f"Coverage: {coverage_start} to {coverage_end}")
            ax.text(0.02, 0.78, f"Tri-channel entities: {len(tri_channel):,}")
            ax.text(0.02, 0.74, f"Seed: {seed}")

            tri_table = top_entities_ranked[top_entities_ranked["channels_count"] == 3].head(10)
            if not tri_table.empty:
                rows = [
                    [
                        row.canonical_name,
                        int(row.total_lobby_reports or 0),
                        f"{float(row.total_campaign_amount or 0):,.0f}",
                        f"{float(row.total_527_amount or 0):,.0f}",
                    ]
                    for row in tri_table.itertuples(index=False)
                ]
                table = ax.table(
                    cellText=rows,
                    colLabels=["Entity", "Lobby reports", "Campaign $", "527 $"],
                    loc="lower left",
                    cellLoc="left",
                    bbox=[0.02, 0.1, 0.96, 0.55],
                )
                table.auto_set_font_size(False)
                table.set_fontsize(8)
            pdf.savefig(fig)
            plt.close(fig)

            # Page 2: Global network
            fig, ax = plt.subplots(figsize=(11, 8.5))
            subtitle = (
                f"Filtered to {filter_meta['filtered_node_count']} nodes / {filter_meta['filtered_edge_count']} edges "
                f"(max_nodes={filter_meta['max_nodes']}, max_edges={filter_meta['max_edges']})."
            )
            if filter_meta.get("downsampled"):
                subtitle += " Downsampled for scale."
            network_state = _render_network_page(
                ax,
                filtered_nodes,
                filtered_edges,
                seed=seed,
                title="Global Network (Filtered)",
                subtitle=subtitle,
            )
            pdf.savefig(fig)
            plt.close(fig)

            # Page 3: Community/cluster
            fig, ax = plt.subplots(figsize=(11, 8.5))
            graph = network_state.get("graph")
            pos = network_state.get("pos") or {}
            if graph is None:
                ax.set_axis_off()
                ax.text(0.5, 0.5, "No graph data available", ha="center", va="center")
            else:
                _render_component_page(ax, graph, pos, "Connected Components (Community Proxy)")
            pdf.savefig(fig)
            plt.close(fig)

            # Page 4: Sankey-like pipeline
            fig, ax = plt.subplots(figsize=(11, 8.5))
            _render_flow_page(ax, filtered_nodes, filtered_edges, "Channel Flow (Approximate)")
            pdf.savefig(fig)
            plt.close(fig)

            # Page 5: Timeline panel
            fig, axs = plt.subplots(2, 2, figsize=(11, 8.5))
            tri_entities = top_entities_ranked[top_entities_ranked["channels_count"] == 3]
            if tri_entities.empty:
                tri_entities = top_entities_ranked
            _render_timeline_page(axs, timeline, tri_entities)
            fig.suptitle("Timeline Signals (log1p)", fontsize=13, fontweight="bold")
            fig.tight_layout(rect=[0, 0.03, 1, 0.95])
            pdf.savefig(fig)
            plt.close(fig)

            # Page 6: Evidence appendix
            fig, ax = plt.subplots(figsize=(11, 8.5))
            _render_evidence_page(ax, filtered_edges, "Evidence Appendix (Sample Edges)")
            pdf.savefig(fig)
            plt.close(fig)

    pdf_seconds = time.perf_counter() - start_pdf

    logger.info("Parse time: %.2fs", parse_seconds)
    logger.info("Build time: %.2fs", build_seconds)
    logger.info("PDF time: %.2fs", pdf_seconds)

    return BuildTimings(parse_seconds=parse_seconds, build_seconds=build_seconds, pdf_seconds=pdf_seconds)


def run_cli() -> None:
    parser = argparse.ArgumentParser(description="Build Triple Pipeline visualizations + report")
    parser.add_argument("--edges", help="Path to triple_pipeline_edges.csv")
    parser.add_argument("--entities", help="Path to triple_pipeline_entities.csv")
    parser.add_argument("--timeline", help="Path to triple_pipeline_timeline.csv")
    parser.add_argument("--top", help="Path to triple_pipeline_top_entities.csv")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR), help="Output directory")
    parser.add_argument("--max-nodes", type=int, default=DEFAULT_MAX_NODES)
    parser.add_argument("--max-edges", type=int, default=DEFAULT_MAX_EDGES)
    parser.add_argument("--max-edges-per-type", type=int, default=DEFAULT_MAX_EDGES_PER_TYPE)
    parser.add_argument("--min-weight", type=float, default=DEFAULT_MIN_WEIGHT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--skip-pdf", action="store_true", help="Skip PDF generation")
    args = parser.parse_args()

    edges_path = _resolve_input_path(args.edges, "triple_pipeline_edges.csv", "edges")
    entities_path = _resolve_input_path(args.entities, "triple_pipeline_entities.csv", "entities")
    timeline_path = _resolve_input_path(args.timeline, "triple_pipeline_timeline.csv", "timeline")
    top_path = _resolve_input_path(args.top, "triple_pipeline_top_entities.csv", "top")

    outdir = Path(args.outdir)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    build_triple_pipeline(
        edges_path=edges_path,
        entities_path=entities_path,
        timeline_path=timeline_path,
        top_path=top_path,
        outdir=outdir,
        max_nodes=args.max_nodes,
        max_edges=args.max_edges,
        max_edges_per_type=args.max_edges_per_type,
        min_weight=args.min_weight,
        seed=args.seed,
        build_pdf=not args.skip_pdf,
    )


if __name__ == "__main__":
    run_cli()
