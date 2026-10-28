"""Per-entity analytics for candidate and race detail pages (candidate / race "entity").

Plan: .claude/plans/race-sort-and-entity-networks.md (contract, decisions N11-N13, step-3 readings A1-A9).
Pure data module: no Flask, no caching (the routes cache), ``conn`` is the app's psycopg compat connection.

``resolve_entity`` turns a candidate id or a race slug into ``{kind, key, label, cycle, committee_ids,
candidate_ids, groups}``; every builder below takes that dict and restricts itself to the entity's committees.
Receipt base = ``isbe_condensed_receipts`` with ``archived = FALSE AND d2_part like '1%' AND amount > 0`` (the
partial index idx_isbe_condensed_receipts_active_part1; `like` is lower-case on purpose, database/pg_compat.py
rewrites an upper-case LIKE to ILIKE and defeats the index), windowed on ``received_date`` (inclusive, either
bound optional). ``entity_donor_rows`` is the one grouped statement (donor_key x committee); the Donors,
Geography and Relationships builders and the concentration flags of Risk are projections of it. The extra
statements are the ones a projection cannot supply: in-kind (5A) totals, monthly series (distinct donors and
per-committee sums per month), the site-wide threshold, the large contributions themselves, the co-giving scan
and the D-2 reconciliation table.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date

from database.analytics import (
    _city_display,
    _city_key,
    _gini,
    _state_race_slug,
    _table_exists,
    current_election_cycle,
)
from database.committee_families import accepted_family_map

ENTITY_KINDS = ("candidate", "race")
ENTITY_CACHE_REV = "ea1"

_DEFAULT_TOP_N = 25
_LARGE_FLOOR = 5000.0
_SPIKE_RATIO = 3.0
_SPIKE_MIN_DELTA = 2000.0
_CONCENTRATION_HHI = 4500.0
_CONCENTRATION_MIN_DONORS = 3
_CONCENTRATION_HIGH_HHI = 7500.0
_RECON_MIN_ABS_DIFF = 1000.0

# The /donors/key/<key> identity, the display name and the redaction rule over alias ``r``.
_DONOR_KEY_SQL = (
    "LOWER(TRIM(COALESCE(r.first_name, '') || '|' || COALESCE(r.last_name, '') || '|' || COALESCE(r.address1, '')"
    " || '|' || COALESCE(r.address2, '') || '|' || COALESCE(r.city, '') || '|' || COALESCE(r.state, '')"
    " || '|' || COALESCE(r.zipcode, '')))"
)
_DONOR_NAME_SQL = "TRIM(COALESCE(r.first_name, '') || ' ' || COALESCE(r.last_name, ''))"
_RECEIPT_BASE_SQL = "r.archived = FALSE AND r.d2_part like '1%' AND r.amount > 0"
_ZIP5_RE = re.compile(r"\d{5}")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _money(value) -> float:
    return round(float(value or 0.0), 2)


def _window(date_from, date_to):
    """`` AND r.received_date >= ? AND r.received_date <= ?`` for the bounds that are set, and their parameters."""
    sql, params = "", []
    for operator, bound in ((">=", date_from), ("<=", date_to)):
        if bound is not None:
            sql += f" AND r.received_date {operator} ?"
            params.append(bound)
    return sql, params


def _entity_committee_ids(entity) -> list[int]:
    return sorted({int(committee_id) for committee_id in entity["committee_ids"]})


def _committee_names(conn, committee_ids) -> dict[int, str]:
    ids = sorted({int(committee_id) for committee_id in committee_ids})
    if not ids:
        return {}
    rows = conn.execute("SELECT id, name FROM isbe_committees WHERE id = ANY(?)", (ids,)).fetchall()
    return {row["id"]: row["name"] for row in rows}


def _name_order(name: str, tie: str = ""):
    return (name.casefold(), name, tie)


def _month_index(month: str) -> int:
    year, number = month.split("-")
    return int(year) * 12 + int(number) - 1


def _month_label(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


# ---------------------------------------------------------------------------
# resolve_entity
# ---------------------------------------------------------------------------

def resolve_entity(conn, kind, key, *, election_cycle=None, today=None):
    """Resolve ``(kind, key)`` to an entity dict, or None for an unknown kind / key (the routes 404)."""
    if kind == "candidate":
        return _resolve_candidate(conn, key)
    if kind == "race":
        return _resolve_race(conn, key, election_cycle, today)
    return None


def _resolve_candidate(conn, key):
    text = "" if key is None else str(key).strip()
    if not re.fullmatch(r"[0-9]+", text):
        return None
    candidate_id = int(text)
    candidate = conn.execute(
        "SELECT id, first_name, last_name FROM isbe_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if candidate is None:
        return None
    label = " ".join(
        part.strip() for part in (candidate["first_name"], candidate["last_name"]) if part and part.strip()
    ) or f"Candidate {candidate_id}"

    linked = sorted({
        row["committee_id"]
        for row in conn.execute(
            "SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = ? AND committee_id IS NOT NULL",
            (candidate_id,),
        ).fetchall()
    })
    families = accepted_family_map(conn, linked)  # {committee_id: (family_id, label)}; accepted families only
    family_labels = {family_id: family_label for family_id, family_label in families.values()}
    family_members: dict[int, set] = defaultdict(set)
    if family_labels:
        for row in conn.execute(
            "SELECT family_id, committee_id FROM committee_family_member WHERE family_id = ANY(?)",
            (sorted(family_labels),),
        ).fetchall():
            family_members[row["family_id"]].add(row["committee_id"])

    in_family = {committee_id for committee_id in linked if committee_id in families}
    singles = [committee_id for committee_id in linked if committee_id not in in_family]
    names = _committee_names(conn, singles)
    committee_ids = sorted(set(linked).union(*family_members.values()) if family_members else set(linked))

    groups = [
        {"label": family_labels[family_id], "committee_ids": sorted(family_members[family_id]),
         "candidate_id": candidate_id, "family_id": family_id}
        for family_id in sorted(family_labels, key=lambda fid: (family_labels[fid].casefold(), fid))
    ]
    groups += [
        {"label": names.get(committee_id) or f"Committee {committee_id}", "committee_ids": [committee_id],
         "candidate_id": candidate_id, "family_id": None}
        for committee_id in sorted(singles, key=lambda cid: ((names.get(cid) or "").casefold(), cid))
    ]
    return {
        "kind": "candidate", "key": str(candidate_id), "label": label, "cycle": None,
        "committee_ids": committee_ids, "candidate_ids": [candidate_id], "groups": groups,
    }


def _resolve_race(conn, key, election_cycle, today):
    race_key_value = "" if key is None else str(key)
    cycle = int(election_cycle) if election_cycle is not None else current_election_cycle(today)
    # The race builder's own query (database/analytics.py _build_state_race_analytics_rows), so the first
    # spelling seen names the race exactly as the race page does.
    rows = conn.execute(
        """
        SELECT DISTINCT
            candidate_id,
            TRIM(COALESCE(candidate_full_name, '')) AS candidate_full_name,
            TRIM(COALESCE(office_sought, '')) AS office_sought,
            TRIM(COALESCE(district_type, '')) AS district_type,
            TRIM(COALESCE(district, '')) AS district,
            committee_id_sbe
        FROM isbe_race_candidacies
        WHERE election_cycle = ?
        ORDER BY office_sought, district_type, district, candidate_id, committee_id_sbe
        """,
        (cycle,),
    ).fetchall()

    label = None
    candidates: dict = {}
    for row in rows:
        office = row["office_sought"] or "Unknown Office"
        district_type = row["district_type"] or "Unknown District Type"
        district = row["district"] or ""
        if _state_race_slug(office, district_type, district) != race_key_value:
            continue
        if label is None:
            label = f"{office} - {district_type}{(' ' + district) if district else ''}"
        token = row["candidate_id"] if row["candidate_id"] is not None else ("name", row["candidate_full_name"])
        candidate = candidates.setdefault(token, {
            "label": row["candidate_full_name"] or f"Candidate {row['candidate_id']}",
            "committee_ids": set(),
            "candidate_id": row["candidate_id"],
        })
        if row["committee_id_sbe"] is not None:
            candidate["committee_ids"].add(int(row["committee_id_sbe"]))
    if label is None:
        return None

    groups = [
        {"label": candidate["label"], "committee_ids": sorted(candidate["committee_ids"]),
         "candidate_id": candidate["candidate_id"], "family_id": None}
        for candidate in sorted(
            candidates.values(), key=lambda c: (c["label"].casefold(), c["candidate_id"] or 0)
        )
    ]
    return {
        "kind": "race", "key": race_key_value, "label": label, "cycle": cycle,
        "committee_ids": sorted({cid for group in groups for cid in group["committee_ids"]}),
        "candidate_ids": sorted({group["candidate_id"] for group in groups if group["candidate_id"] is not None}),
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# entity_donor_rows -- the one grouped statement
# ---------------------------------------------------------------------------

def entity_donor_rows(conn, committee_ids, *, date_from: date | None = None, date_to: date | None = None):
    """One row per (donor_key, committee_id) over the receipt base, amount desc / name / committee id."""
    ids = sorted({int(committee_id) for committee_id in committee_ids})
    if not ids:
        return []
    window_sql, window_params = _window(date_from, date_to)
    fetched = conn.execute(
        f"""
        SELECT {_DONOR_KEY_SQL} AS donor_key,
               r.committee_id,
               MIN(c.name) AS committee_name,
               MIN({_DONOR_NAME_SQL}) AS donor_name,
               MAX(CASE WHEN TRIM(COALESCE(r.first_name, '')) <> '' THEN 1 ELSE 0 END) AS is_person,
               SUM(r.amount) AS total_amount,
               COUNT(*) AS contribution_count,
               MIN(NULLIF(TRIM(r.city), '')) AS city,
               MIN(NULLIF(TRIM(r.state), '')) AS state,
               MIN(NULLIF(TRIM(r.zipcode), '')) AS zipcode,
               MIN(NULLIF(TRIM(r.occupation), '')) AS occupation,
               MIN(NULLIF(TRIM(r.employer), '')) AS employer,
               BOOL_OR(COALESCE(r.redaction_requested, FALSE)
                       OR COALESCE(r.address1 = 'Redaction Requested', FALSE)) AS redacted,
               MIN(r.received_date) AS first_date,
               MAX(r.received_date) AS last_date
        FROM isbe_condensed_receipts r
        LEFT JOIN isbe_committees c ON c.id = r.committee_id
        WHERE r.committee_id = ANY(?) AND {_RECEIPT_BASE_SQL}{window_sql}
        GROUP BY 1, 2
        """,
        [ids, *window_params],
    ).fetchall()

    rows = []
    for row in fetched:
        redacted = bool(row["redacted"])
        state = None if redacted or not row["state"] else row["state"].strip().upper()
        if state is not None and not (len(state) == 2 and state.isalpha()):
            state = None
        zip_match = None if redacted or not row["zipcode"] else _ZIP5_RE.match(row["zipcode"].strip())
        rows.append({
            "donor_key": row["donor_key"],
            "donor_name": row["donor_name"] or "",
            "donor_class": "individual" if row["is_person"] else "organization",
            "committee_id": int(row["committee_id"]),
            "committee_name": row["committee_name"] or f"Committee {row['committee_id']}",
            "total_amount": _money(row["total_amount"]),
            "contribution_count": int(row["contribution_count"]),
            "city": None if redacted else row["city"],
            "state": state,
            "zip5": zip_match.group(0) if zip_match else None,
            "occupation": row["occupation"],
            "employer": row["employer"],
            "redacted": redacted,
            "first_date": row["first_date"],
            "last_date": row["last_date"],
        })
    rows.sort(key=lambda r: (-r["total_amount"], r["donor_name"].casefold(), r["donor_name"], r["committee_id"]))
    return rows


# ---------------------------------------------------------------------------
# projections of the donor rows
# ---------------------------------------------------------------------------

def _in_kind_by_committee(conn, committee_ids, date_from, date_to) -> dict[int, float]:
    """Σ amount of the entity's in-kind (5A) rows per committee, archived excluded, inside the window."""
    if not committee_ids:
        return {}
    window_sql, window_params = _window(date_from, date_to)
    rows = conn.execute(
        f"""
        SELECT r.committee_id, SUM(r.amount) AS amount
        FROM isbe_condensed_receipts r
        WHERE r.committee_id = ANY(?) AND r.archived = FALSE AND r.d2_part = '5A'{window_sql}
        GROUP BY 1
        """,
        [list(committee_ids), *window_params],
    ).fetchall()
    return {row["committee_id"]: float(row["amount"] or 0.0) for row in rows}


def _metrics(rows, in_kind_total) -> dict:
    """The /analytics/donors concentration metrics (get_donor_concentration formulas) over donor rows."""
    per_donor: dict[str, float] = defaultdict(float)
    contributions = 0
    individual = organization = 0.0
    for row in rows:
        per_donor[row["donor_key"]] += row["total_amount"]
        contributions += row["contribution_count"]
        if row["donor_class"] == "individual":
            individual += row["total_amount"]
        else:
            organization += row["total_amount"]
    amounts = sorted((amount for amount in per_donor.values() if amount > 0), reverse=True)
    total = sum(amounts)
    shares = [amount / total for amount in amounts] if total > 0 else []
    return {
        "total_amount": _money(total),
        "donor_count": len(amounts),
        "contribution_count": contributions,
        "top1_share": round(sum(shares[:1]), 4),
        "top5_share": round(sum(shares[:5]), 4),
        "top10_share": round(sum(shares[:10]), 4),
        "hhi": round(sum(share ** 2 for share in shares) * 10000, 2),
        "gini": round(_gini(amounts), 4),
        "individual_amount": _money(individual),
        "organization_amount": _money(organization),
        "in_kind_total": _money(in_kind_total),
    }


def _group_rows(entity, rows):
    """[(group, its donor rows)] in entity order; a committee in two groups feeds both."""
    by_committee: dict[int, list] = defaultdict(list)
    for row in rows:
        by_committee[row["committee_id"]].append(row)
    return [
        (group, [row for committee_id in group["committee_ids"] for row in by_committee.get(int(committee_id), ())])
        for group in entity["groups"]
    ]


def _donor_entries(entity, rows) -> dict[str, dict]:
    """Per donor key, across the entity's committees: totals, committees reached and the amount per group index."""
    committee_groups: dict[int, list[int]] = defaultdict(list)
    for index, group in enumerate(entity["groups"]):
        for committee_id in group["committee_ids"]:
            committee_groups[int(committee_id)].append(index)
    entries: dict[str, dict] = {}
    for row in rows:
        entry = entries.get(row["donor_key"])
        if entry is None:
            entry = entries[row["donor_key"]] = {
                "donor_key": row["donor_key"], "donor_name": row["donor_name"], "donor_class": row["donor_class"],
                "total_amount": 0.0, "contribution_count": 0, "committees": set(), "city": row["city"],
                "state": row["state"], "redacted": False, "group_amounts": {},
            }
        entry["total_amount"] += row["total_amount"]
        entry["contribution_count"] += row["contribution_count"]
        entry["committees"].add(row["committee_id"])
        entry["redacted"] = entry["redacted"] or row["redacted"]
        if row["donor_class"] == "individual":
            entry["donor_class"] = "individual"
        for index in committee_groups.get(row["committee_id"], ()):
            entry["group_amounts"][index] = entry["group_amounts"].get(index, 0.0) + row["total_amount"]
    for entry in entries.values():
        entry["total_amount"] = _money(entry["total_amount"])
    return entries


def _ranked(entries):
    return sorted(
        entries.values(), key=lambda e: (-e["total_amount"], *_name_order(e["donor_name"], e["donor_key"]))
    )


def _shared_donors(entity, entries, top_n) -> list[dict]:
    groups = entity["groups"]
    shared = [entry for entry in _ranked(entries) if len(entry["group_amounts"]) >= 2]
    return [
        {
            "donor_key": entry["donor_key"],
            "donor_name": entry["donor_name"],
            "total_amount": entry["total_amount"],
            "group_count": len(entry["group_amounts"]),
            "groups": [
                {"label": groups[index]["label"], "amount": _money(entry["group_amounts"][index])}
                for index in sorted(entry["group_amounts"])
            ],
        }
        for entry in shared[: max(int(top_n), 0)]
    ]


def get_entity_donors(conn, entity, *, date_from: date | None = None, date_to: date | None = None, top_n=25):
    """Per-group and whole-entity donor metrics, class breakdown, top donors and donors shared across groups."""
    committee_ids = _entity_committee_ids(entity)
    rows = entity_donor_rows(conn, committee_ids, date_from=date_from, date_to=date_to)
    in_kind = _in_kind_by_committee(conn, committee_ids, date_from, date_to)

    groups = []
    for group, group_rows in _group_rows(entity, rows):
        group_in_kind = sum(in_kind.get(int(committee_id), 0.0) for committee_id in group["committee_ids"])
        groups.append({
            "label": group["label"],
            "committee_ids": list(group["committee_ids"]),
            "candidate_id": group.get("candidate_id"),
            "family_id": group.get("family_id"),
            **_metrics(group_rows, group_in_kind),
        })

    entries = _donor_entries(entity, rows)
    labels = [group["label"] for group in entity["groups"]]
    ranked = _ranked(entries)
    top_donors = [
        {
            "donor_key": entry["donor_key"],
            "donor_name": entry["donor_name"],
            "donor_class": entry["donor_class"],
            "total_amount": entry["total_amount"],
            "contribution_count": entry["contribution_count"],
            "committee_count": len(entry["committees"]),
            "city": None if entry["redacted"] else entry["city"],
            "state": None if entry["redacted"] else entry["state"],
            "redacted": entry["redacted"],
            "groups": [labels[index] for index in sorted(entry["group_amounts"])],
        }
        for entry in ranked[: max(int(top_n), 0)]
    ]
    class_breakdown = []
    for donor_class in ("individual", "organization"):
        members = [entry for entry in entries.values() if entry["donor_class"] == donor_class]
        if members:
            class_breakdown.append({
                "donor_class": donor_class,
                "total_amount": _money(sum(entry["total_amount"] for entry in members)),
                "donor_count": len(members),
            })
    class_breakdown.sort(key=lambda item: -item["total_amount"])

    return {
        "groups": groups,
        "totals": _metrics(rows, sum(in_kind.values())),
        "class_breakdown": class_breakdown,
        "top_donors": top_donors,
        "shared_donors": _shared_donors(entity, entries, top_n),
    }


# ---------------------------------------------------------------------------
# geography
# ---------------------------------------------------------------------------

def _monthly_series(conn, committee_ids, date_from, date_to) -> list[dict]:
    """Receipt-base months from the first to the last with data, zero-filled; donors are distinct per month."""
    if not committee_ids:
        return []
    window_sql, window_params = _window(date_from, date_to)
    fetched = conn.execute(
        f"""
        SELECT TO_CHAR(r.received_date, 'YYYY-MM') AS month,
               SUM(r.amount) AS total_amount,
               COUNT(*) AS contribution_count,
               COUNT(DISTINCT {_DONOR_KEY_SQL}) AS donor_count
        FROM isbe_condensed_receipts r
        WHERE r.committee_id = ANY(?) AND {_RECEIPT_BASE_SQL}{window_sql}
        GROUP BY 1
        """,
        [committee_ids, *window_params],
    ).fetchall()
    if not fetched:
        return []
    by_month = {row["month"]: row for row in fetched}
    first, last = (_month_index(month) for month in (min(by_month), max(by_month)))
    series = []
    for index in range(first, last + 1):
        row = by_month.get(_month_label(index))
        series.append({
            "month": _month_label(index),
            "total_amount": _money(row["total_amount"]) if row else 0.0,
            "contribution_count": int(row["contribution_count"]) if row else 0,
            "donor_count": int(row["donor_count"]) if row else 0,
        })
    return series


def get_entity_geography(
    conn, entity, *, date_from: date | None = None, date_to: date | None = None, limit_states=20, limit_cities=30
):
    """States, cities and a monthly series for the entity's receipts; redacted and state-less rows count in none."""
    committee_ids = _entity_committee_ids(entity)
    rows = entity_donor_rows(conn, committee_ids, date_from=date_from, date_to=date_to)
    totals = _metrics(rows, 0.0)
    total_amount = totals["total_amount"]

    states: dict[str, list] = {}
    cities: dict[tuple, list] = {}
    unknown = 0.0
    for row in rows:
        state = row["state"]
        if state is None:
            unknown += row["total_amount"]
            continue
        bucket = states.setdefault(state, [0.0, 0, set()])
        bucket[0] += row["total_amount"]
        bucket[1] += row["contribution_count"]
        bucket[2].add(row["donor_key"])
        city_key = _city_key(row["city"])
        if city_key:
            bucket = cities.setdefault((city_key, state), [0.0, 0, set(), _city_display(row["city"])])
            bucket[0] += row["total_amount"]
            bucket[1] += row["contribution_count"]
            bucket[2].add(row["donor_key"])

    def share(amount):
        return round(amount / total_amount, 4) if total_amount > 0 else 0.0

    state_rows = [
        {"state": state, "total_amount": _money(bucket[0]), "donor_count": len(bucket[2]),
         "contribution_count": bucket[1], "share": share(bucket[0])}
        for state, bucket in states.items()
    ]
    state_rows.sort(key=lambda item: (-item["total_amount"], item["state"]))
    city_rows = [
        {"city": bucket[3], "state": state, "total_amount": _money(bucket[0]), "donor_count": len(bucket[2]),
         "contribution_count": bucket[1], "share": share(bucket[0])}
        for (_, state), bucket in cities.items()
    ]
    city_rows.sort(key=lambda item: (-item["total_amount"], item["city"], item["state"]))

    illinois = states.get("IL", [0.0])[0]
    return {
        "states": state_rows[: max(int(limit_states), 0)],
        "cities": city_rows[: max(int(limit_cities), 0)],
        "time_series": _monthly_series(conn, committee_ids, date_from, date_to),
        "in_state_share": share(illinois),
        "unknown_amount": _money(unknown),
        "totals": {
            "total_amount": total_amount,
            "donor_count": totals["donor_count"],
            "contribution_count": totals["contribution_count"],
        },
    }


# ---------------------------------------------------------------------------
# risk
# ---------------------------------------------------------------------------

def _site_large_threshold(conn) -> float:
    """max(5,000, 2 x p95) over every receipt-base row of every committee, all time (percentile_cont, plan A1)."""
    row = conn.execute(
        f"SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY r.amount) AS p95 "
        f"FROM isbe_condensed_receipts r WHERE {_RECEIPT_BASE_SQL}"
    ).fetchone()
    p95 = float(row["p95"]) if row and row["p95"] is not None else 0.0
    return round(max(_LARGE_FLOOR, 2.0 * p95), 2)


def _large_flags(conn, committee_ids, threshold, date_from, date_to, limit, labels, names):
    window_sql, window_params = _window(date_from, date_to)
    fetched = conn.execute(
        f"""
        SELECT r.committee_id, r.received_date, r.amount, {_DONOR_KEY_SQL} AS donor_key,
               {_DONOR_NAME_SQL} AS donor_name, COUNT(*) OVER () AS total
        FROM isbe_condensed_receipts r
        WHERE r.committee_id = ANY(?) AND {_RECEIPT_BASE_SQL} AND r.amount >= ?{window_sql}
        ORDER BY r.amount DESC, donor_name, r.committee_id, r.received_date, r.id
        LIMIT ?
        """,
        [committee_ids, threshold, *window_params, max(int(limit), 0)],
    ).fetchall()
    flags = []
    for row in fetched:
        amount = _money(row["amount"])
        flags.append({
            "flag_type": "large_single_contribution",
            "severity": "high" if amount >= 2.0 * threshold else "medium",
            "committee_id": row["committee_id"],
            "committee_name": names.get(row["committee_id"]) or f"Committee {row['committee_id']}",
            "group_label": labels.get(row["committee_id"]),
            "donor_key": row["donor_key"],
            "donor_name": row["donor_name"],
            "event_date": row["received_date"],
            "month": row["received_date"].strftime("%Y-%m"),
            "value": amount,
            "baseline": threshold,
            "threshold": threshold,
            "details": (
                f"${amount:,.2f} from {row['donor_name'] or 'an unnamed donor'}; "
                f"the flag starts at ${threshold:,.2f}."
            ),
        })
    return flags, (int(fetched[0]["total"]) if fetched else 0)


def _spike_flags(conn, committee_ids, date_from, date_to, labels, names):
    window_sql, window_params = _window(date_from, date_to)
    fetched = conn.execute(
        f"""
        SELECT r.committee_id, TO_CHAR(r.received_date, 'YYYY-MM') AS month, SUM(r.amount) AS amount
        FROM isbe_condensed_receipts r
        WHERE r.committee_id = ANY(?) AND {_RECEIPT_BASE_SQL}{window_sql}
        GROUP BY 1, 2
        """,
        [committee_ids, *window_params],
    ).fetchall()
    months: dict[int, dict[int, float]] = defaultdict(dict)
    for row in fetched:
        months[row["committee_id"]][_month_index(row["month"])] = float(row["amount"] or 0.0)

    flags = []
    for committee_id, month_map in months.items():
        first, last = min(month_map), max(month_map)
        series = [month_map.get(index, 0.0) for index in range(first, last + 1)]
        for position in range(1, len(series)):
            prior = series[max(0, position - 3):position]
            baseline = sum(prior) / len(prior)
            value = series[position]
            if baseline <= 0:
                continue
            if round(value, 2) >= round(_SPIKE_RATIO * baseline, 2) and round(value - baseline, 2) >= _SPIKE_MIN_DELTA:
                threshold = round(max(_SPIKE_RATIO * baseline, baseline + _SPIKE_MIN_DELTA), 2)
                month = _month_label(first + position)
                flags.append({
                    "flag_type": "monthly_spike",
                    "severity": "high" if value >= 2.0 * threshold else "medium",
                    "committee_id": committee_id,
                    "committee_name": names.get(committee_id) or f"Committee {committee_id}",
                    "group_label": labels.get(committee_id),
                    "donor_key": None,
                    "donor_name": None,
                    "event_date": date(int(month[:4]), int(month[5:]), 1),
                    "month": month,
                    "value": _money(value),
                    "baseline": _money(baseline),
                    "threshold": threshold,
                    "details": (
                        f"{month} receipts of ${value:,.2f} are at least {_SPIKE_RATIO:g}x the trailing "
                        f"{len(prior)}-month average of ${baseline:,.2f} and ${value - baseline:,.2f} above it."
                    ),
                })
    flags.sort(key=lambda flag: (-flag["value"], flag["committee_id"], flag["month"]))
    return flags


def _concentration_flags(group_metrics):
    flags = []
    for label, metrics in group_metrics:
        if metrics["hhi"] >= _CONCENTRATION_HHI and metrics["donor_count"] >= _CONCENTRATION_MIN_DONORS:
            flags.append({
                "flag_type": "high_donor_concentration",
                "severity": "high" if metrics["hhi"] >= _CONCENTRATION_HIGH_HHI else "medium",
                "committee_id": None,
                "committee_name": None,
                "group_label": label,
                "donor_key": None,
                "donor_name": None,
                "event_date": None,
                "month": None,
                "value": metrics["hhi"],
                "baseline": float(metrics["donor_count"]),
                "threshold": _CONCENTRATION_HHI,
                "details": (
                    f"HHI {metrics['hhi']:,.0f} over {metrics['donor_count']} donors; the top donor gave "
                    f"{metrics['top1_share']:.0%} of ${metrics['total_amount']:,.2f}."
                ),
            })
    flags.sort(key=lambda flag: (-flag["value"], flag["group_label"] or ""))
    return flags


def _reconciliation_rows(conn, committee_ids, limit, labels):
    if not committee_ids or not _table_exists(conn, "bulk_d2_receipts_recon"):
        return [], 0
    fetched = conn.execute(
        """
        SELECT r.d2_totals_record_id, r.committee_id_sbe AS committee_id,
               COALESCE(c.name, r.committee_name) AS committee_name, r.filed_doc_id,
               r.d2_total_receipts, r.d2_total_expenditures, r.ending_funds_available, r.is_archived,
               r.receipt_row_count, r.receipts_amount_sum, r.first_receipt_date, r.last_receipt_date,
               r.receipts_minus_d2_total, COUNT(*) OVER () AS total
        FROM bulk_d2_receipts_recon r
        LEFT JOIN isbe_committees c ON c.id = r.committee_id_sbe
        WHERE r.committee_id_sbe = ANY(?)
          AND ABS(COALESCE(CAST(r.receipts_minus_d2_total AS DOUBLE PRECISION), 0.0)) >= ?
        ORDER BY ABS(COALESCE(CAST(r.receipts_minus_d2_total AS DOUBLE PRECISION), 0.0)) DESC,
                 r.committee_id_sbe, r.filed_doc_id
        LIMIT ?
        """,
        (committee_ids, _RECON_MIN_ABS_DIFF, max(int(limit), 0)),
    ).fetchall()
    rows = []
    for row in fetched:
        diff = _money(row["receipts_minus_d2_total"])
        rows.append({
            "committee_id": row["committee_id"],
            "committee_name": row["committee_name"] or f"Committee {row['committee_id']}",
            "group_label": labels.get(row["committee_id"]),
            "d2_totals_record_id": row["d2_totals_record_id"],
            "filed_doc_id": row["filed_doc_id"],
            "d2_total_receipts": _money(row["d2_total_receipts"]),
            "d2_total_expenditures": _money(row["d2_total_expenditures"]),
            "ending_funds_available": _money(row["ending_funds_available"]),
            "is_archived": row["is_archived"],
            "receipt_row_count": int(row["receipt_row_count"] or 0),
            "receipts_amount_sum": _money(row["receipts_amount_sum"]),
            "first_receipt_date": row["first_receipt_date"],
            "last_receipt_date": row["last_receipt_date"],
            "receipts_minus_d2_total": diff,
            "diff": diff,
        })
    return rows, (int(fetched[0]["total"]) if fetched else 0)


def get_entity_risk(
    conn, entity, *, date_from: date | None = None, date_to: date | None = None, limit=50, large_threshold=None
):
    """Large contributions, monthly spikes, concentrated groups and D-2 mismatches, from the entity's rows only.
    ``limit`` caps each list; ``counts`` are the uncapped totals per type."""
    committee_ids = _entity_committee_ids(entity)
    labels: dict[int, str] = {}
    for group in entity["groups"]:
        for committee_id in group["committee_ids"]:
            labels.setdefault(int(committee_id), group["label"])
    names = _committee_names(conn, committee_ids)

    if large_threshold is None:
        threshold, source = _site_large_threshold(conn), "site_wide"
    else:
        threshold, source = float(large_threshold), "override"

    large, large_total = ([], 0)
    spikes = []
    group_metrics = []
    if committee_ids:
        large, large_total = _large_flags(conn, committee_ids, threshold, date_from, date_to, limit, labels, names)
        spikes = _spike_flags(conn, committee_ids, date_from, date_to, labels, names)
        rows = entity_donor_rows(conn, committee_ids, date_from=date_from, date_to=date_to)
        group_metrics = [(group["label"], _metrics(group_rows, 0.0)) for group, group_rows in _group_rows(entity, rows)]
    concentration = _concentration_flags(group_metrics)
    reconciliation, reconciliation_total = _reconciliation_rows(conn, committee_ids, limit, labels)

    cap = max(int(limit), 0)
    return {
        "flags": [*large, *spikes[:cap], *concentration[:cap]],
        "threshold": {"large_single": threshold, "source": source},
        "reconciliation": reconciliation,
        "reconciliation_available": _table_exists(conn, "bulk_d2_receipts_recon"),
        "counts": {
            "large_single_contribution": large_total,
            "monthly_spike": len(spikes),
            "high_donor_concentration": len(concentration),
            "reconciliation": reconciliation_total,
        },
    }


# ---------------------------------------------------------------------------
# relationships
# ---------------------------------------------------------------------------

def get_entity_relationships(
    conn,
    entity,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit=25,
    min_shared_donors=2,
    donor_pool=2000,
):
    """Committees outside the entity that received receipt-base money in the window from at least
    ``min_shared_donors`` of the entity's ``donor_pool`` biggest donors, plus the donors shared across groups."""
    committee_ids = _entity_committee_ids(entity)
    rows = entity_donor_rows(conn, committee_ids, date_from=date_from, date_to=date_to)
    entries = _donor_entries(entity, rows)
    pool = _ranked(entries)[: max(int(donor_pool), 0)]
    pool_by_key = {entry["donor_key"]: entry for entry in pool}

    gifts: dict[int, dict[str, float]] = defaultdict(dict)
    if pool_by_key:
        window_sql, window_params = _window(date_from, date_to)
        hashes = [hashlib.md5(donor_key.encode("utf-8")).hexdigest() for donor_key in pool_by_key]
        for row in conn.execute(
            f"""
            SELECT {_DONOR_KEY_SQL} AS donor_key, r.committee_id, SUM(r.amount) AS amount
            FROM isbe_condensed_receipts r
            WHERE md5({_DONOR_KEY_SQL}) = ANY(?) AND NOT (r.committee_id = ANY(?))
              AND {_RECEIPT_BASE_SQL}{window_sql}
            GROUP BY 1, 2
            """,
            [hashes, committee_ids, *window_params],
        ).fetchall():
            if row["donor_key"] in pool_by_key:
                gifts[row["committee_id"]][row["donor_key"]] = float(row["amount"] or 0.0)

    ranked = []
    for committee_id, by_donor in gifts.items():
        if len(by_donor) < max(int(min_shared_donors), 1):
            continue
        to_committee = sum(by_donor.values())
        ranked.append((-len(by_donor), -round(to_committee, 2), committee_id))
    ranked.sort()

    names = _committee_names(conn, [committee_id for _, _, committee_id in ranked[: max(int(limit), 0)]])
    cogiving = []
    for _, _, committee_id in ranked[: max(int(limit), 0)]:
        by_donor = gifts[committee_id]
        shared = sorted(
            (
                {
                    "donor_key": donor_key,
                    "donor_name": pool_by_key[donor_key]["donor_name"],
                    "amount_to_entity": pool_by_key[donor_key]["total_amount"],
                    "amount_to_committee": _money(amount),
                }
                for donor_key, amount in by_donor.items()
            ),
            key=lambda item: (-item["amount_to_entity"], -item["amount_to_committee"],
                              *_name_order(item["donor_name"], item["donor_key"])),
        )
        cogiving.append({
            "committee_id": committee_id,
            "committee_name": names.get(committee_id) or f"Committee {committee_id}",
            "shared_donor_count": len(by_donor),
            "shared_amount_to_entity": _money(sum(pool_by_key[donor_key]["total_amount"] for donor_key in by_donor)),
            "shared_amount_to_committee": _money(sum(by_donor.values())),
            "top_shared_donors": shared[:5],
        })

    return {
        "cogiving": cogiving,
        "shared_donors": _shared_donors(entity, entries, _DEFAULT_TOP_N),
        "summary": {
            "entity_donor_count": len(entries),
            "donor_pool_size": len(pool),
            "cogiving_committee_count": len(ranked),
        },
    }
