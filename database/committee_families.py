"""Committee families: reviewable groupings of serial committee re-registrations.

Group, never merge: committee ids, pages and per-committee totals never change; a
family only says "these ISBE committees look like one political actor". Public pages
show accepted families only. Design: docs/committee_families_design.md (plan S-C8,
decision J-S9); tests: tests/test_committee_families.py.

Evidence rule (a pair of committees is linked when it shares a linked candidate AND):
  identical normalized name, OR trigram similarity >= 0.8, OR a shared officer name
  (normalized first + last), OR the same non-blank street address in the same zip.
Linked pairs are unioned into families (connected components). The seeder reads only
isbe_committees, isbe_candidate_committees and isbe_officers. Like
database/privacy_redactions.py nothing here commits; the caller does.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict

STATUSES = ("pending", "accepted", "rejected")
SIMILARITY_THRESHOLD = 0.8

_NORM = (
    r"btrim(regexp_replace(lower(regexp_replace(coalesce({col}, ''), "
    r"'[^[:alnum:]]+', ' ', 'g')), '\s+', ' ', 'g'))"
)


def has_trgm(conn) -> bool:
    """pg_trgm is installed (CI has no extension; the similarity clauses are skipped without it)."""
    return conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'").fetchone() is not None


def _strong_pairs_sql(trgm: bool) -> str:
    sim = "similarity(x.norm_name, y.norm_name)" if trgm else "NULL::real"
    return f"""
WITH cm AS NOT MATERIALIZED (
    SELECT id,
           {_NORM.format(col='name')} AS norm_name,
           {_NORM.format(col='address1')} AS norm_street,
           {_NORM.format(col='zipcode')} AS norm_zip
    FROM isbe_committees
),
cand_pairs AS (
    SELECT a.committee_id AS c1, b.committee_id AS c2,
           array_agg(DISTINCT a.candidate_id ORDER BY a.candidate_id) AS shared_candidates
    FROM isbe_candidate_committees a
    JOIN isbe_candidate_committees b ON b.candidate_id = a.candidate_id AND a.committee_id < b.committee_id
    GROUP BY a.committee_id, b.committee_id
),
officer_names AS (
    -- one pass over isbe_officers (a per-pair EXISTS rescans it: minutes instead of a second)
    SELECT committee_id,
           array_agg(DISTINCT lower(btrim(coalesce(first_name, '') || ' ' || coalesce(last_name, '')))) AS names
    FROM isbe_officers
    WHERE coalesce(last_name, '') <> ''
      AND committee_id IN (SELECT c1 FROM cand_pairs UNION SELECT c2 FROM cand_pairs)
    GROUP BY committee_id
)
SELECT p.c1, p.c2, p.shared_candidates,
       (x.norm_name <> '' AND x.norm_name = y.norm_name) AS same_name,
       COALESCE({sim} >= {SIMILARITY_THRESHOLD}, FALSE) AS similar,
       round(({sim})::numeric, 3) AS sim,
       (x.norm_street <> '' AND x.norm_street = y.norm_street AND x.norm_zip = y.norm_zip) AS same_address,
       COALESCE(o1.names && o2.names, FALSE) AS shared_officer
FROM cand_pairs p
JOIN cm x ON x.id = p.c1
JOIN cm y ON y.id = p.c2
LEFT JOIN officer_names o1 ON o1.committee_id = p.c1
LEFT JOIN officer_names o2 ON o2.committee_id = p.c2
WHERE (x.norm_name <> '' AND x.norm_name = y.norm_name)
   OR COALESCE({sim} >= {SIMILARITY_THRESHOLD}, FALSE)
   OR (x.norm_street <> '' AND x.norm_street = y.norm_street AND x.norm_zip = y.norm_zip)
   OR COALESCE(o1.names && o2.names, FALSE)
ORDER BY p.c1, p.c2
"""


_WEAK_PAIRS_SQL = f"""
WITH cm AS (
    SELECT id, zipcode, {_NORM.format(col='name')} AS norm_name
    FROM isbe_committees
    WHERE coalesce(zipcode, '') <> ''
)
SELECT x.id AS c1, y.id AS c2, round(similarity(x.norm_name, y.norm_name)::numeric, 3) AS sim
FROM cm x JOIN cm y ON y.zipcode = x.zipcode AND x.id < y.id
WHERE similarity(x.norm_name, y.norm_name) >= {SIMILARITY_THRESHOLD}
  AND NOT EXISTS (
      SELECT 1 FROM isbe_candidate_committees a JOIN isbe_candidate_committees b ON b.candidate_id = a.candidate_id
      WHERE a.committee_id = x.id AND b.committee_id = y.id)
ORDER BY sim DESC, c1, c2
"""


def strong_pairs(conn) -> list[dict]:
    """Linked pairs: c1 < c2, shared_candidates, same_name, similar, sim, same_address, shared_officer."""
    rows = conn.execute(_strong_pairs_sql(has_trgm(conn))).fetchall()
    return [
        {
            "c1": int(r["c1"]),
            "c2": int(r["c2"]),
            "shared_candidates": [int(c) for c in r["shared_candidates"]],
            "same_name": bool(r["same_name"]),
            "similar": bool(r["similar"]),
            "sim": float(r["sim"]) if r["sim"] is not None else None,
            "same_address": bool(r["same_address"]),
            "shared_officer": bool(r["shared_officer"]),
        }
        for r in rows
    ]


def weak_pairs(conn) -> list[dict]:
    """Same zip and name similarity >= 0.8 but no shared candidate: a review queue, never seeded.

    Read-only. Returns [] where pg_trgm is missing.
    """
    if not has_trgm(conn):
        return []
    return [
        {"c1": int(r["c1"]), "c2": int(r["c2"]), "sim": float(r["sim"])}
        for r in conn.execute(_WEAK_PAIRS_SQL).fetchall()
    ]


def member_hash(committee_ids) -> str:
    """sha1 of the sorted member ids: the identity of a family's member set."""
    return hashlib.sha1(",".join(str(i) for i in sorted(committee_ids)).encode()).hexdigest()


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _evidence(pairs: list[dict]) -> dict:
    """Only what holds: flags that are true, the best near-miss similarity, every shared candidate."""
    evidence: dict = {}
    similar = [p["sim"] for p in pairs if p["similar"] and not p["same_name"] and p["sim"] is not None]
    if any(p["same_name"] for p in pairs):
        evidence["same_name"] = True
    if similar:
        evidence["similar_name"] = max(similar)
    if any(p["shared_officer"] for p in pairs):
        evidence["shared_officer"] = True
    if any(p["same_address"] for p in pairs):
        evidence["same_address"] = True
    evidence["shared_candidates"] = sorted({c for p in pairs for c in p["shared_candidates"]})
    return evidence


def build_families(pairs: list[dict]) -> list[dict]:
    """Union linked pairs into families: members (sorted ids), member_hash, evidence, member_evidence."""
    uf = UnionFind()
    for p in pairs:
        uf.union(p["c1"], p["c2"])
    members: dict[int, list[int]] = defaultdict(list)
    for node in uf.parent:
        members[uf.find(node)].append(node)
    family_pairs: dict[int, list[dict]] = defaultdict(list)
    member_pairs: dict[int, list[dict]] = defaultdict(list)
    for p in pairs:
        family_pairs[uf.find(p["c1"])].append(p)
        member_pairs[p["c1"]].append(p)
        member_pairs[p["c2"]].append(p)
    families = []
    for root, ids in members.items():
        ids.sort()
        families.append({
            "members": ids,
            "member_hash": member_hash(ids),
            "evidence": _evidence(family_pairs[root]),
            "member_evidence": {cid: _evidence(member_pairs[cid]) for cid in ids},
        })
    families.sort(key=lambda f: f["members"])
    return families


def _label_families(conn, families: list[dict]) -> None:
    """label = as-filed name of the most recently active member (active first, then latest creation date)."""
    owner = {cid: index for index, family in enumerate(families) for cid in family["members"]}
    if not owner:
        return
    rows = conn.execute(
        "SELECT id, name FROM isbe_committees WHERE id = ANY(?) "
        "ORDER BY COALESCE(active, FALSE) DESC, creation_date DESC NULLS LAST, id DESC",
        (sorted(owner),),
    ).fetchall()
    labels: dict[int, str] = {}
    for row in rows:
        if row["name"] and row["name"].strip():
            labels.setdefault(owner[row["id"]], row["name"])
    for index, family in enumerate(families):
        family["label"] = labels.get(index) or f"Committee {family['members'][0]}"


def propose_families(conn) -> tuple[list[dict], list[dict]]:
    """(strong pairs, labelled families): the one place the evidence rule is applied."""
    pairs = strong_pairs(conn)
    families = build_families(pairs)
    _label_families(conn, families)
    return pairs, families


def seed_families(conn) -> dict:
    """Insert a pending family for every member set not seen before; never touches existing ones.

    A family whose member_hash already exists keeps its status, notes and label. Returns
    pairs (strong pairs found), families_created, families_existing and families_orphaned
    (stored families with a member no longer in isbe_committees: reported, never deleted).
    """
    pairs, families = propose_families(conn)
    known = {row["member_hash"] for row in conn.execute("SELECT member_hash FROM committee_family").fetchall()}
    created = existing = 0
    for family in families:
        if family["member_hash"] in known:
            existing += 1
            continue
        family_id = conn.execute(
            "INSERT INTO committee_family (label, evidence, member_hash) "
            "VALUES (?, CAST(? AS JSONB), ?) RETURNING id",
            (family["label"], json.dumps(family["evidence"]), family["member_hash"]),
        ).fetchone()["id"]
        conn.executemany(
            "INSERT INTO committee_family_member (family_id, committee_id, evidence) "
            "VALUES (?, ?, CAST(? AS JSONB))",
            [(family_id, cid, json.dumps(family["member_evidence"][cid])) for cid in family["members"]],
        )
        created += 1
    orphaned = conn.execute(
        "SELECT COUNT(DISTINCT m.family_id) AS n FROM committee_family_member m "
        "LEFT JOIN isbe_committees c ON c.id = m.committee_id WHERE c.id IS NULL"
    ).fetchone()["n"]
    return {
        "pairs": len(pairs),
        "families_created": created,
        "families_existing": existing,
        "families_orphaned": int(orphaned),
    }


# ---------------------------------------------------------------------------
# Review and display (admin pages, candidate profile)
# ---------------------------------------------------------------------------

def status_counts(conn) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM committee_family GROUP BY status").fetchall():
        counts[row["status"]] = int(row["n"])
    return counts


def list_families(conn, *, status: str, limit: int, offset: int) -> list[dict]:
    """One review page: exact-name groups first, then the largest families."""
    rows = conn.execute(
        """
        SELECT f.id, f.label, f.status, f.evidence, f.reviewed_at,
               COUNT(m.committee_id) AS member_count,
               COUNT(c.id) FILTER (WHERE c.active) AS active_count
        FROM committee_family f
        LEFT JOIN committee_family_member m ON m.family_id = f.id
        LEFT JOIN isbe_committees c ON c.id = m.committee_id
        WHERE f.status = ?
        GROUP BY f.id
        ORDER BY COALESCE((f.evidence->>'same_name')::boolean, FALSE) DESC, COUNT(m.committee_id) DESC, f.id
        LIMIT ? OFFSET ?
        """,
        (status, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def get_family(conn, family_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, label, status, evidence, notes, created_at, reviewed_at FROM committee_family WHERE id = ?",
        (family_id,),
    ).fetchone()
    return dict(row) if row else None


def family_members(conn, family_id: int) -> list[dict]:
    """Members newest registration first, each with its linked candidates (id and name)."""
    members = [
        dict(row)
        for row in conn.execute(
            """
            SELECT m.committee_id AS id, m.evidence, c.name, c.type, c.active, c.creation_date
            FROM committee_family_member m
            LEFT JOIN isbe_committees c ON c.id = m.committee_id
            WHERE m.family_id = ?
            ORDER BY c.creation_date DESC NULLS LAST, m.committee_id DESC
            """,
            (family_id,),
        ).fetchall()
    ]
    linked: dict[int, list[dict]] = defaultdict(list)
    if members:
        for row in conn.execute(
            """
            SELECT cc.committee_id, d.id AS candidate_id, d.first_name, d.last_name
            FROM isbe_candidate_committees cc
            JOIN isbe_candidates d ON d.id = cc.candidate_id
            WHERE cc.committee_id = ANY(?)
            ORDER BY d.last_name, d.first_name, d.id
            """,
            ([m["id"] for m in members],),
        ).fetchall():
            name = " ".join(p.strip() for p in (row["first_name"], row["last_name"]) if p and p.strip())
            linked[row["committee_id"]].append({"id": row["candidate_id"], "name": name or f"Candidate {row['candidate_id']}"})
    for member in members:
        member["candidates"] = linked.get(member["id"], [])
    return members


def accept_conflicts(conn, family_id: int) -> list[dict]:
    """Committees of this family that already sit in a different ACCEPTED family."""
    rows = conn.execute(
        """
        SELECT DISTINCT m.committee_id, f.id AS family_id, f.label
        FROM committee_family_member mine
        JOIN committee_family_member m ON m.committee_id = mine.committee_id AND m.family_id <> mine.family_id
        JOIN committee_family f ON f.id = m.family_id AND f.status = 'accepted'
        WHERE mine.family_id = ?
        ORDER BY m.committee_id, f.id
        """,
        (family_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def set_status(conn, family_id: int, status: str) -> None:
    conn.execute(
        "UPDATE committee_family SET status = ?, reviewed_at = now() WHERE id = ?",
        (status, family_id),
    )


def remove_member(conn, family_id: int, committee_id: int) -> str | None:
    """Drop one committee from a family and rewrite member_hash; returns a refusal message or None."""
    ids = [r["committee_id"] for r in conn.execute(
        "SELECT committee_id FROM committee_family_member WHERE family_id = ?", (family_id,)
    ).fetchall()]
    if committee_id not in ids:
        return f"Committee {committee_id} is not a member of this family."
    if len(ids) <= 2:
        return "A family needs at least two committees; reject the family instead."
    remaining = [i for i in ids if i != committee_id]
    new_hash = member_hash(remaining)
    clash = conn.execute(
        "SELECT id FROM committee_family WHERE member_hash = ? AND id <> ?", (new_hash, family_id)
    ).fetchone()
    if clash:
        return f"Family {clash['id']} already has exactly these remaining members."
    conn.execute(
        "DELETE FROM committee_family_member WHERE family_id = ? AND committee_id = ?",
        (family_id, committee_id),
    )
    conn.execute("UPDATE committee_family SET member_hash = ? WHERE id = ?", (new_hash, family_id))
    return None


def accepted_family_map(conn, committee_ids) -> dict[int, tuple[int, str]]:
    """{committee_id: (family_id, label)} for committees in an ACCEPTED family (lowest family id wins)."""
    ids = list(committee_ids)
    if not ids:
        return {}
    result: dict[int, tuple[int, str]] = {}
    for row in conn.execute(
        """
        SELECT m.committee_id, f.id AS family_id, f.label
        FROM committee_family_member m
        JOIN committee_family f ON f.id = m.family_id
        WHERE f.status = 'accepted' AND m.committee_id = ANY(?)
        ORDER BY f.id
        """,
        (ids,),
    ).fetchall():
        result.setdefault(row["committee_id"], (row["family_id"], row["label"]))
    return result
