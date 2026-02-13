"""Local donor-entity resolution for bulk analytics donor rows.

This module builds a confidence-scored entity layer on top of
analytics_donor_summary rows (typically source='bulk_receipts').
It is intentionally conservative:
- Strongly-supported clusters can be auto-merged.
- Medium-confidence clusters are queued for review.
- Low-confidence rows remain singleton entities.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
from itertools import combinations
import json
import re
import sqlite3
from typing import Optional


LOCAL_DONOR_ENTITY_METHOD_VERSION = "local_entity_v1"

_NAME_STOP_WORDS = {"mr", "mrs", "ms", "dr", "jr", "sr", "ii", "iii", "iv", "v"}
_EMPLOYER_STOP_WORDS = {
    "llc",
    "inc",
    "corp",
    "corporation",
    "co",
    "company",
    "the",
    "and",
    "ltd",
    "lp",
    "llp",
    "pllc",
    "group",
    "holdings",
}
_OCCUPATION_STOP_WORDS = {"self", "employed", "retired"}
_WEAK_EMPLOYER_ANCHORS = {
    "self",
    "owner",
    "business",
    "retired",
    "none",
    "unknown",
    "individual",
    "state",
    "city",
    "county",
    "committee",
    "fund",
    "campaign",
    "democratic",
    "republican",
}
_BRIDGE_MIN_TOTAL_AMOUNT = 1_000_000.0
_ORG_KEYWORDS = {
    "llc",
    "inc",
    "corp",
    "corporation",
    "co",
    "company",
    "committee",
    "pac",
    "fund",
    "union",
    "association",
    "party",
    "friends",
    "bank",
    "group",
    "trust",
    "foundation",
    "club",
    "campaign",
    "candidate",
    "democratic",
    "republican",
    "dues",
    "local",
    "council",
    "city",
    "county",
    "state",
    "village",
    "township",
    "school",
    "college",
    "hospital",
    "church",
    "services",
    "service",
    "construction",
    "electric",
    "plumbing",
    "realty",
    "properties",
    "management",
    "investments",
    "seiu",
    "afscme",
    "ibew",
    "iuoe",
    "teamsters",
    "fraternal",
    "brotherhood",
    "federation",
    "labor",
}
_ADDRESS_ABBREVIATIONS = {
    "street": "st",
    "st.": "st",
    "avenue": "ave",
    "ave.": "ave",
    "boulevard": "blvd",
    "blvd.": "blvd",
    "road": "rd",
    "rd.": "rd",
    "drive": "dr",
    "dr.": "dr",
    "lane": "ln",
    "ln.": "ln",
    "court": "ct",
    "ct.": "ct",
    "place": "pl",
    "pl.": "pl",
    "terrace": "ter",
    "ter.": "ter",
    "suite": "ste",
    "ste.": "ste",
    "apartment": "apt",
    "apt.": "apt",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _clean_text(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def _normalize_name_tokens(value: str | None) -> list[str]:
    return [token for token in _clean_text(value).split() if token and token not in _NAME_STOP_WORDS]


def _is_person_like(first_tokens: list[str], last_tokens: list[str], full_tokens: list[str]) -> bool:
    if not first_tokens or not last_tokens:
        return False
    if len(first_tokens) > 2 or len(last_tokens) > 2:
        return False

    first = first_tokens[0]
    last = last_tokens[-1]
    if len(first) < 2 or len(last) < 2:
        return False
    if first.isdigit() or last.isdigit():
        return False
    if "and" in first_tokens or "and" in full_tokens:
        return False
    if first in _ORG_KEYWORDS or last in _ORG_KEYWORDS:
        return False
    if any(token in _ORG_KEYWORDS for token in full_tokens):
        return False
    return True


def _canonical_person_name(donor_key: str | None, donor_name: str | None) -> Optional[str]:
    parts = (donor_key or "").split("|")
    first = parts[0] if len(parts) >= 1 else ""
    last = parts[1] if len(parts) >= 2 else ""

    first_tokens = _normalize_name_tokens(first)
    last_tokens = _normalize_name_tokens(last)
    full_tokens = _normalize_name_tokens(donor_name)

    if first_tokens and last_tokens and _is_person_like(first_tokens, last_tokens, full_tokens):
        return f"{first_tokens[0]} {last_tokens[-1]}"

    if len(full_tokens) >= 2 and _is_person_like([full_tokens[0]], [full_tokens[-1]], full_tokens):
        return f"{full_tokens[0]} {full_tokens[-1]}"

    return None


def _parse_donor_key(donor_key: str | None) -> tuple[str, str, str, str, str, str, str]:
    parts = (donor_key or "").split("|")
    while len(parts) < 7:
        parts.append("")
    return (
        parts[0],
        parts[1],
        parts[2],
        parts[3],
        parts[4],
        parts[5],
        parts[6],
    )


def _extract_zip5(raw_zip: str | None, donor_address: str | None) -> str:
    text = (raw_zip or "").strip()
    m = re.match(r"^(\d{5})", text)
    if m:
        return m.group(1)
    addr = donor_address or ""
    m = re.search(r"(\d{5})", addr)
    if m:
        return m.group(1)
    return ""


def _normalize_addr_tokens(addr_line_1: str | None, addr_line_2: str | None, donor_address: str | None) -> tuple[str, ...]:
    text = " ".join(value for value in [addr_line_1, addr_line_2, donor_address] if value)
    tokens = _clean_text(text).split()
    out: list[str] = []
    skip_next = False
    for token in tokens:
        token = _ADDRESS_ABBREVIATIONS.get(token, token)
        if skip_next:
            skip_next = False
            continue
        if token in {"apt", "ste", "unit", "fl", "floor", "room", "rm", "#"}:
            skip_next = True
            continue
        if re.fullmatch(r"\d{5}(?:\d{4})?", token):
            continue
        out.append(token)
    return tuple(out)


def _normalize_tokens(value: str | None, stop_words: set[str]) -> tuple[str, ...]:
    return tuple(token for token in _clean_text(value).split() if token and token not in stop_words)


def _jaccard(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def _tier_from_score(score: float, high_threshold: float, medium_threshold: float) -> str:
    if score >= high_threshold:
        return "high"
    if score >= medium_threshold:
        return "medium"
    return "low"


@dataclass(slots=True)
class _DonorRecord:
    donor_key: str
    donor_name: str
    donor_city: str
    donor_state: str
    donor_zip5: str
    canonical_name: str
    full_name_normalized: str
    address_tokens: tuple[str, ...]
    employer_tokens: tuple[str, ...]
    occupation_tokens: tuple[str, ...]
    employer_anchor: str
    address_anchor: str
    total_amount: float
    contribution_count: int
    committee_count: int

    @property
    def city_state_key(self) -> str:
        if not self.donor_city and not self.donor_state:
            return ""
        return f"{self.donor_city}|{self.donor_state}"


@dataclass(slots=True)
class _PairScore:
    left_idx: int
    right_idx: int
    score: float
    city_state_match: bool
    zip_match: bool
    address_similarity: float
    employer_similarity: float
    occupation_similarity: float
    multi_home_bonus: bool
    bridge_rule: bool


class _UnionFind:
    def __init__(self, size: int):
        self.parents = list(range(size))
        self.ranks = [0] * size

    def find(self, value: int) -> int:
        while self.parents[value] != value:
            self.parents[value] = self.parents[self.parents[value]]
            value = self.parents[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.ranks[left_root] < self.ranks[right_root]:
            left_root, right_root = right_root, left_root
        self.parents[right_root] = left_root
        if self.ranks[left_root] == self.ranks[right_root]:
            self.ranks[left_root] += 1


def _candidate_pairs(records: list[_DonorRecord]) -> list[tuple[int, int]]:
    size = len(records)
    if size <= 80:
        return list(combinations(range(size), 2))

    buckets: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for idx, record in enumerate(records):
        city_state = record.city_state_key
        if city_state:
            buckets[("city_state", city_state)].append(idx)
        if record.donor_zip5:
            buckets[("zip5", record.donor_zip5)].append(idx)
        if city_state and record.donor_zip5:
            buckets[("city_state_zip5", city_state, record.donor_zip5)].append(idx)
        if city_state and record.employer_anchor:
            buckets[("city_state_employer", city_state, record.employer_anchor)].append(idx)
        if city_state and record.address_anchor:
            buckets[("city_state_address", city_state, record.address_anchor)].append(idx)

    pair_set: set[tuple[int, int]] = set()
    for idxs in buckets.values():
        if len(idxs) < 2 or len(idxs) > 200:
            continue
        for left_idx, right_idx in combinations(idxs, 2):
            if left_idx > right_idx:
                left_idx, right_idx = right_idx, left_idx
            pair_set.add((left_idx, right_idx))
    return list(pair_set)


def _score_pair(
    left: _DonorRecord,
    right: _DonorRecord,
    *,
    left_idx: int,
    right_idx: int,
    group_size: int,
    medium_threshold: float,
    high_threshold: float,
) -> Optional[_PairScore]:
    city_state_match = bool(left.city_state_key and left.city_state_key == right.city_state_key)
    zip_match = bool(left.donor_zip5 and left.donor_zip5 == right.donor_zip5)

    address_similarity = _jaccard(left.address_tokens, right.address_tokens)
    employer_similarity = _jaccard(left.employer_tokens, right.employer_tokens)
    occupation_similarity = _jaccard(left.occupation_tokens, right.occupation_tokens)

    multi_home_bonus = city_state_match and employer_similarity >= 0.85 and address_similarity < 0.30
    bridge_rule = (
        bool(left.full_name_normalized)
        and left.full_name_normalized == right.full_name_normalized
        and bool(left.donor_state)
        and left.donor_state == right.donor_state
        and bool(left.employer_anchor)
        and left.employer_anchor == right.employer_anchor
        and left.employer_anchor not in _WEAK_EMPLOYER_ANCHORS
        and len(left.employer_anchor) >= 4
        and employer_similarity >= 0.40
        and max(left.total_amount, right.total_amount) >= _BRIDGE_MIN_TOTAL_AMOUNT
    )
    signal_present = (
        zip_match
        or address_similarity >= 0.40
        or employer_similarity >= 0.60
        or occupation_similarity >= 0.60
        or multi_home_bonus
        or bridge_rule
    )

    score = 0.32

    if left.full_name_normalized and left.full_name_normalized == right.full_name_normalized:
        score += 0.10

    if city_state_match:
        score += 0.16
    elif left.donor_state and right.donor_state and left.donor_state != right.donor_state:
        score -= 0.25

    if zip_match:
        score += 0.08

    if address_similarity >= 0.85:
        score += 0.22
    elif address_similarity >= 0.60:
        score += 0.15
    elif address_similarity >= 0.40:
        score += 0.08

    if left.employer_tokens and right.employer_tokens:
        if employer_similarity >= 0.85:
            score += 0.18
        elif employer_similarity >= 0.60:
            score += 0.12
        elif employer_similarity >= 0.40:
            score += 0.06
        elif address_similarity < 0.30 and not zip_match:
            score -= 0.08

    if left.occupation_tokens and right.occupation_tokens:
        if occupation_similarity >= 0.85:
            score += 0.07
        elif occupation_similarity >= 0.60:
            score += 0.04
        elif occupation_similarity >= 0.40:
            score += 0.02

    if multi_home_bonus:
        # Allows same-name/same-city/same-employer records with different homes
        # to cross medium-confidence thresholds without auto-merging.
        score += 0.10

    if bridge_rule:
        # Allow high-dollar same-name/same-state/same-employer-anchor variants
        # (often alternate home/PO box records) to be reviewed together.
        score = max(score, min(high_threshold - 0.01, medium_threshold + 0.02))

    if group_size >= 10:
        score -= 0.04
    if group_size >= 20:
        score -= 0.06
    if group_size >= 40:
        score -= 0.08

    if not signal_present and score < medium_threshold:
        return None

    score = max(0.0, min(1.0, score))
    return _PairScore(
        left_idx=left_idx,
        right_idx=right_idx,
        score=score,
        city_state_match=city_state_match,
        zip_match=zip_match,
        address_similarity=address_similarity,
        employer_similarity=employer_similarity,
        occupation_similarity=occupation_similarity,
        multi_home_bonus=multi_home_bonus,
        bridge_rule=bridge_rule,
    )


def _entity_id(source: str, canonical_name: str, donor_keys: list[str], method_version: str) -> str:
    digest = hashlib.sha1(
        "|".join([source, canonical_name, method_version] + sorted(donor_keys)).encode("utf-8")
    ).hexdigest()[:24]
    return f"local_{digest}"


def rebuild_local_donor_entities(
    conn: sqlite3.Connection,
    *,
    source: str = "bulk_receipts",
    medium_threshold: float = 0.70,
    high_threshold: float = 0.82,
    auto_threshold: float = 0.90,
    max_group_size: int = 400,
    dry_run: bool = False,
    preview_limit: int = 25,
) -> dict:
    """Rebuild local donor entities from analytics_donor_summary.

    Returns summary metrics and top review candidates by total dollars.
    """
    if high_threshold < medium_threshold:
        raise ValueError("high_threshold must be >= medium_threshold")
    if auto_threshold < high_threshold:
        raise ValueError("auto_threshold must be >= high_threshold")

    if not _table_exists(conn, "analytics_donor_summary"):
        return {
            "source": source,
            "status": "skipped",
            "reason": "analytics_donor_summary table not found",
            "dry_run": bool(dry_run),
            "top_review_entities": [],
        }

    method_version = (
        f"{LOCAL_DONOR_ENTITY_METHOD_VERSION}:"
        f"m{medium_threshold:.2f}:h{high_threshold:.2f}:a{auto_threshold:.2f}"
    )

    total_source_rows = 0
    person_like_rows = 0
    name_counts: dict[str, int] = defaultdict(int)
    for row in conn.execute(
        """
        SELECT donor_key, donor_name
        FROM analytics_donor_summary
        WHERE source = ?
        """,
        (source,),
    ):
        total_source_rows += 1
        canonical_name = _canonical_person_name(row["donor_key"], row["donor_name"])
        if not canonical_name:
            continue
        person_like_rows += 1
        name_counts[canonical_name] += 1

    multi_name_groups = {name for name, count in name_counts.items() if count > 1}

    conn.execute("DROP TABLE IF EXISTS temp.tmp_local_donor_entity_candidates")
    conn.execute(
        """
        CREATE TEMP TABLE tmp_local_donor_entity_candidates (
            canonical_name TEXT NOT NULL,
            donor_key TEXT NOT NULL,
            donor_name TEXT,
            donor_address TEXT,
            donor_city TEXT,
            donor_state TEXT,
            occupation TEXT,
            employer TEXT,
            total_amount REAL NOT NULL,
            contribution_count INTEGER NOT NULL,
            committee_count INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX temp.idx_tmp_local_donor_entity_candidates_name
        ON tmp_local_donor_entity_candidates(canonical_name, donor_key)
        """
    )

    insert_payload: list[tuple] = []
    for row in conn.execute(
        """
        SELECT
            donor_key,
            donor_name,
            donor_address,
            donor_city,
            donor_state,
            occupation,
            employer,
            total_amount,
            contribution_count,
            committee_count
        FROM analytics_donor_summary
        WHERE source = ?
        """,
        (source,),
    ):
        canonical_name = _canonical_person_name(row["donor_key"], row["donor_name"])
        if not canonical_name or canonical_name not in multi_name_groups:
            continue

        insert_payload.append(
            (
                canonical_name,
                row["donor_key"],
                row["donor_name"],
                row["donor_address"],
                row["donor_city"],
                row["donor_state"],
                row["occupation"],
                row["employer"],
                float(row["total_amount"] or 0.0),
                int(row["contribution_count"] or 0),
                int(row["committee_count"] or 0),
            )
        )
        if len(insert_payload) >= 5000:
            conn.executemany(
                """
                INSERT INTO tmp_local_donor_entity_candidates (
                    canonical_name,
                    donor_key,
                    donor_name,
                    donor_address,
                    donor_city,
                    donor_state,
                    occupation,
                    employer,
                    total_amount,
                    contribution_count,
                    committee_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                insert_payload,
            )
            insert_payload.clear()

    if insert_payload:
        conn.executemany(
            """
            INSERT INTO tmp_local_donor_entity_candidates (
                canonical_name,
                donor_key,
                donor_name,
                donor_address,
                donor_city,
                donor_state,
                occupation,
                employer,
                total_amount,
                contribution_count,
                committee_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_payload,
        )

    existing_review_status: dict[str, str] = {}
    if not dry_run and _table_exists(conn, "donor_entity_local_member"):
        for row in conn.execute(
            """
            SELECT donor_key, review_status
            FROM donor_entity_local_member
            WHERE source = ?
            """,
            (source,),
        ):
            existing_review_status[row["donor_key"]] = (row["review_status"] or "pending").strip().lower() or "pending"

    stats = {
        "source": source,
        "method_version": method_version,
        "dry_run": bool(dry_run),
        "total_source_rows": total_source_rows,
        "person_like_rows": person_like_rows,
        "multi_name_groups": len(multi_name_groups),
        "candidate_rows": int(
            conn.execute("SELECT COUNT(*) AS count FROM tmp_local_donor_entity_candidates").fetchone()["count"]
        ),
        "processed_groups": 0,
        "skipped_large_groups": 0,
        "entity_rows_written": 0,
        "member_rows_written": 0,
        "review_entities": 0,
        "auto_merge_entities": 0,
        "singleton_entities": 0,
        "duplicate_rows_medium": 0,
        "duplicate_rows_high": 0,
    }

    top_review_entities: list[dict] = []

    entity_buffer: list[tuple] = []
    member_buffer: list[tuple] = []

    def flush_buffers() -> None:
        if dry_run:
            entity_buffer.clear()
            member_buffer.clear()
            return
        if entity_buffer:
            conn.executemany(
                """
                INSERT INTO donor_entity_local (
                    entity_id,
                    source,
                    canonical_name,
                    display_name,
                    member_count,
                    total_amount,
                    confidence_score,
                    peak_confidence_score,
                    confidence_tier,
                    merge_action,
                    method_version,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                entity_buffer,
            )
            entity_buffer.clear()
        if member_buffer:
            conn.executemany(
                """
                INSERT INTO donor_entity_local_member (
                    source,
                    donor_key,
                    entity_id,
                    canonical_name,
                    donor_name,
                    donor_city,
                    donor_state,
                    donor_zip5,
                    confidence_score,
                    confidence_tier,
                    merge_action,
                    total_amount,
                    contribution_count,
                    committee_count,
                    review_status,
                    reasons_json,
                    method_version,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                member_buffer,
            )
            member_buffer.clear()

    def process_group(canonical_name: str, records: list[_DonorRecord]) -> None:
        if not records:
            return
        stats["processed_groups"] += 1
        group_size = len(records)

        if group_size > max_group_size:
            stats["skipped_large_groups"] += 1
            for record in records:
                entity_key = _entity_id(source, canonical_name, [record.donor_key], method_version)
                reasons_json = json.dumps(
                    {
                        "canonical_name": canonical_name,
                        "note": "group skipped due max_group_size guardrail",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                entity_buffer.append(
                    (
                        entity_key,
                        source,
                        canonical_name,
                        record.donor_name,
                        1,
                        float(record.total_amount),
                        0.0,
                        0.0,
                        "low",
                        "singleton",
                        method_version,
                    )
                )
                member_buffer.append(
                    (
                        source,
                        record.donor_key,
                        entity_key,
                        canonical_name,
                        record.donor_name,
                        record.donor_city.upper() or None,
                        record.donor_state.upper() or None,
                        record.donor_zip5 or None,
                        0.0,
                        "low",
                        "singleton",
                        float(record.total_amount),
                        int(record.contribution_count),
                        int(record.committee_count),
                        "not_needed",
                        reasons_json,
                        method_version,
                    )
                )
                stats["entity_rows_written"] += 1
                stats["member_rows_written"] += 1
                stats["singleton_entities"] += 1
            if len(entity_buffer) >= 1000 or len(member_buffer) >= 5000:
                flush_buffers()
            return

        candidate_pairs = _candidate_pairs(records)
        medium_edges: list[_PairScore] = []
        high_edges: list[_PairScore] = []
        for left_idx, right_idx in candidate_pairs:
            scored = _score_pair(
                records[left_idx],
                records[right_idx],
                left_idx=left_idx,
                right_idx=right_idx,
                group_size=group_size,
                medium_threshold=medium_threshold,
                high_threshold=high_threshold,
            )
            if not scored:
                continue
            if scored.score >= medium_threshold:
                medium_edges.append(scored)
            if scored.score >= high_threshold:
                high_edges.append(scored)

        high_union = _UnionFind(group_size)
        for edge in high_edges:
            high_union.union(edge.left_idx, edge.right_idx)
        high_components: dict[int, list[int]] = defaultdict(list)
        for idx in range(group_size):
            high_components[high_union.find(idx)].append(idx)
        for component in high_components.values():
            if len(component) > 1:
                stats["duplicate_rows_high"] += len(component) - 1

        medium_union = _UnionFind(group_size)
        for edge in medium_edges:
            medium_union.union(edge.left_idx, edge.right_idx)
        medium_components: dict[int, list[int]] = defaultdict(list)
        for idx in range(group_size):
            medium_components[medium_union.find(idx)].append(idx)

        adjacency: dict[int, list[_PairScore]] = defaultdict(list)
        for edge in medium_edges:
            adjacency[edge.left_idx].append(edge)
            adjacency[edge.right_idx].append(edge)

        for component in medium_components.values():
            component_set = set(component)
            member_best_score: dict[int, float] = {idx: 0.0 for idx in component}
            member_best_edge: dict[int, tuple[_PairScore, int]] = {}
            component_edges: list[_PairScore] = []

            for idx in component:
                for edge in adjacency.get(idx, []):
                    if edge.left_idx not in component_set or edge.right_idx not in component_set:
                        continue
                    component_edges.append(edge)
                    neighbor_idx = edge.right_idx if edge.left_idx == idx else edge.left_idx
                    if edge.score > member_best_score[idx]:
                        member_best_score[idx] = edge.score
                        member_best_edge[idx] = (edge, neighbor_idx)

            component_size = len(component)
            donor_keys = [records[idx].donor_key for idx in component]
            display_member_idx = max(component, key=lambda idx: records[idx].total_amount)
            display_name = records[display_member_idx].donor_name
            total_amount = sum(records[idx].total_amount for idx in component)

            if component_size > 1:
                cluster_confidence = min(member_best_score[idx] for idx in component)
                peak_confidence = max(member_best_score[idx] for idx in component)
                stats["duplicate_rows_medium"] += component_size - 1
            else:
                cluster_confidence = 0.0
                peak_confidence = 0.0

            confidence_tier = _tier_from_score(cluster_confidence, high_threshold, medium_threshold)
            state_set = {records[idx].donor_state for idx in component if records[idx].donor_state}

            if component_size <= 1:
                merge_action = "singleton"
            elif cluster_confidence >= auto_threshold and len(state_set) <= 1:
                merge_action = "auto_merge"
            else:
                merge_action = "review"

            if merge_action == "auto_merge":
                stats["auto_merge_entities"] += 1
            elif merge_action == "review":
                stats["review_entities"] += 1
            else:
                stats["singleton_entities"] += 1

            entity_key = _entity_id(source, canonical_name, donor_keys, method_version)
            entity_buffer.append(
                (
                    entity_key,
                    source,
                    canonical_name,
                    display_name,
                    component_size,
                    float(total_amount),
                    round(float(cluster_confidence), 4),
                    round(float(peak_confidence), 4),
                    confidence_tier,
                    merge_action,
                    method_version,
                )
            )
            stats["entity_rows_written"] += 1

            if merge_action == "review" and component_size > 1:
                top_review_entities.append(
                    {
                        "entity_id": entity_key,
                        "canonical_name": canonical_name,
                        "display_name": display_name,
                        "member_count": component_size,
                        "total_amount": round(float(total_amount), 2),
                        "confidence_score": round(float(cluster_confidence), 4),
                        "peak_confidence_score": round(float(peak_confidence), 4),
                    }
                )

            for idx in component:
                record = records[idx]
                member_score = member_best_score[idx]
                member_tier = _tier_from_score(member_score, high_threshold, medium_threshold)
                if merge_action == "review":
                    review_status = existing_review_status.get(record.donor_key, "pending")
                else:
                    review_status = "not_needed"

                reason_payload = {
                    "canonical_name": canonical_name,
                    "cluster_size": component_size,
                    "cluster_confidence_score": round(float(cluster_confidence), 4),
                    "cluster_peak_confidence_score": round(float(peak_confidence), 4),
                }
                best_edge_tuple = member_best_edge.get(idx)
                if best_edge_tuple:
                    edge, neighbor_idx = best_edge_tuple
                    reason_payload["best_match_donor_key"] = records[neighbor_idx].donor_key
                    reason_payload["best_match_score"] = round(float(edge.score), 4)
                    reason_payload["signals"] = {
                        "city_state_match": bool(edge.city_state_match),
                        "zip_match": bool(edge.zip_match),
                        "address_similarity": round(float(edge.address_similarity), 4),
                        "employer_similarity": round(float(edge.employer_similarity), 4),
                        "occupation_similarity": round(float(edge.occupation_similarity), 4),
                        "multi_home_bonus": bool(edge.multi_home_bonus),
                        "bridge_rule": bool(edge.bridge_rule),
                    }
                elif component_size <= 1:
                    reason_payload["note"] = "no medium-confidence links in canonical-name group"

                reasons_json = json.dumps(
                    reason_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                )

                member_buffer.append(
                    (
                        source,
                        record.donor_key,
                        entity_key,
                        canonical_name,
                        record.donor_name,
                        record.donor_city.upper() or None,
                        record.donor_state.upper() or None,
                        record.donor_zip5 or None,
                        round(float(member_score), 4),
                        member_tier,
                        merge_action,
                        float(record.total_amount),
                        int(record.contribution_count),
                        int(record.committee_count),
                        review_status,
                        reasons_json,
                        method_version,
                    )
                )
                stats["member_rows_written"] += 1

        if len(entity_buffer) >= 1000 or len(member_buffer) >= 5000:
            flush_buffers()

    started_transaction = False
    try:
        if not dry_run and not conn.in_transaction:
            conn.execute("BEGIN")
            started_transaction = True
        elif not dry_run:
            started_transaction = True

        if not dry_run:
            conn.execute("DELETE FROM donor_entity_local_member WHERE source = ?", (source,))
            conn.execute("DELETE FROM donor_entity_local WHERE source = ?", (source,))

        current_name = None
        current_records: list[_DonorRecord] = []
        row_cursor = conn.execute(
            """
            SELECT
                canonical_name,
                donor_key,
                donor_name,
                donor_address,
                donor_city,
                donor_state,
                occupation,
                employer,
                total_amount,
                contribution_count,
                committee_count
            FROM tmp_local_donor_entity_candidates
            ORDER BY canonical_name, donor_key
            """
        )
        for row in row_cursor:
            canonical_name = row["canonical_name"]
            if current_name is not None and canonical_name != current_name:
                process_group(current_name, current_records)
                current_records = []

            _, _, addr_line_1, addr_line_2, city_from_key, state_from_key, raw_zip = _parse_donor_key(row["donor_key"])
            donor_city = _clean_text(city_from_key or row["donor_city"])
            donor_state = _clean_text(state_from_key or row["donor_state"])
            donor_zip5 = _extract_zip5(raw_zip, row["donor_address"])
            address_tokens = _normalize_addr_tokens(addr_line_1, addr_line_2, row["donor_address"])
            employer_tokens = _normalize_tokens(row["employer"], _EMPLOYER_STOP_WORDS)
            occupation_tokens = _normalize_tokens(row["occupation"], _OCCUPATION_STOP_WORDS)

            current_records.append(
                _DonorRecord(
                    donor_key=row["donor_key"],
                    donor_name=(row["donor_name"] or "").strip() or row["donor_key"],
                    donor_city=donor_city,
                    donor_state=donor_state,
                    donor_zip5=donor_zip5,
                    canonical_name=canonical_name,
                    full_name_normalized=_clean_text(row["donor_name"]),
                    address_tokens=address_tokens,
                    employer_tokens=employer_tokens,
                    occupation_tokens=occupation_tokens,
                    employer_anchor=employer_tokens[0] if employer_tokens else "",
                    address_anchor=address_tokens[0] if address_tokens else "",
                    total_amount=float(row["total_amount"] or 0.0),
                    contribution_count=int(row["contribution_count"] or 0),
                    committee_count=int(row["committee_count"] or 0),
                )
            )
            current_name = canonical_name

        if current_name is not None and current_records:
            process_group(current_name, current_records)

        flush_buffers()

        if not dry_run and started_transaction:
            conn.commit()
    except Exception:
        if not dry_run and conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.execute("DROP TABLE IF EXISTS temp.tmp_local_donor_entity_candidates")

    top_review_entities = sorted(
        top_review_entities,
        key=lambda row: (row["total_amount"], row["member_count"]),
        reverse=True,
    )[: max(0, int(preview_limit))]

    stats["top_review_entities"] = top_review_entities
    return stats
