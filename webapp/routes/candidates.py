"""Candidate profile: ``/candidates/<isbe candidate id>``.

Plan profiles-races-dated-widgets, step C1 (section 2.4, journal rows J-C1..J-C3). Lists every
committee linked to one ISBE candidate, most recently active first, so a reader can tell which
are still live. Built on the ISBE tables and matviews from ``scripts/isbe_sunshine_etl.py``.
The landing page ``/candidates`` stays in ``main.py``.
"""
from collections import Counter
from dataclasses import replace
from datetime import date

from flask import Blueprint, abort, current_app, render_template

from database.committee_families import accepted_family_map
from webapp.utils.data_through import latest_data_date
from webapp.utils.time_scope import (
    TimeScope, current_cycle, cycle_window, resolve_time_scope, scope_params, scope_where,
)

candidates_bp = Blueprint("candidates", __name__)

RECENT_YEARS = 1  # "Recently active" badge: last activity within 12 months of data_through
MUTED_YEARS = 4   # muted row: last activity 4+ years before data_through

# Committees linked to the candidate (one qmark parameter: the candidate id).
_LINKED_COMMITTEES = "(SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = ?)"


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # Feb 29 in a non-leap year
        return day.replace(year=day.year - years, day=28)


def _candidate_header(conn, candidate_id: int) -> dict | None:
    """Name and the profile facts; never selects an address column."""
    row = conn.execute(
        """
        SELECT first_name, last_name, office, district_type, district, party, residence_county
        FROM isbe_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if row is None:
        return None
    name = " ".join(part.strip() for part in (row["first_name"], row["last_name"]) if part and part.strip())
    district = " ".join(part for part in (row["district_type"], row["district"]) if part)
    facts = [
        (label, value)
        for label, value in (
            ("Office", row["office"]),
            ("District", district),
            ("Party", row["party"]),
            ("Residence county", row["residence_county"]),
        )
        if value
    ]
    return {"name": name or f"Candidate {candidate_id}", "facts": facts}


def _money_by_committee(conn, table: str, date_column: str, candidate_id: int,
                        scope: TimeScope, cycle_scope: TimeScope) -> dict:
    """{committee_id: row} over non-archived rows of the condensed matview ``table``.

    ``last_date`` is the committee's latest row, all time (any amount). ``window_total`` and
    ``cycle_total`` sum amounts > 0 inside the page window / the current cycle.
    """
    window_sql, window_params = scope_where(scope, f"m.{date_column}")
    cycle_sql, cycle_params = scope_where(cycle_scope, f"m.{date_column}")
    rows = conn.execute(
        f"""
        SELECT m.committee_id,
               MAX(m.{date_column}) AS last_date,
               SUM(m.amount) FILTER (WHERE m.amount > 0{window_sql}) AS window_total,
               SUM(m.amount) FILTER (WHERE m.amount > 0{cycle_sql}) AS cycle_total
        FROM {table} m
        WHERE m.archived = FALSE
          AND m.committee_id IN {_LINKED_COMMITTEES}
        GROUP BY m.committee_id
        """,
        (*window_params, *cycle_params, candidate_id),
    ).fetchall()
    return {row["committee_id"]: row for row in rows}


def _committee_rows(conn, candidate_id: int, scope: TimeScope, cycle_scope: TimeScope,
                    data_through: date | None) -> list[dict]:
    """One row per linked committee, ordered by last activity (newest first, never-active last)."""
    receipts = _money_by_committee(conn, "isbe_condensed_receipts", "received_date", candidate_id, scope, cycle_scope)
    expenditures = _money_by_committee(
        conn, "isbe_condensed_expenditures", "expended_date", candidate_id, scope, cycle_scope,
    )
    committees = conn.execute(
        f"""
        SELECT c.id, c.name, c.active,
               f.doc_name AS last_filing_name,
               f.received_datetime::date AS last_filing_date
        FROM isbe_committees c
        LEFT JOIN isbe_most_recent_filings f ON f.committee_id = c.id
        WHERE c.id IN {_LINKED_COMMITTEES}
        ORDER BY c.name, c.id
        """,
        (candidate_id,),
    ).fetchall()

    recent_cutoff = _years_before(data_through, RECENT_YEARS) if data_through else None
    muted_cutoff = _years_before(data_through, MUTED_YEARS) if data_through else None
    rows = []
    for committee in committees:
        received = receipts.get(committee["id"])
        spent = expenditures.get(committee["id"])
        dated = [
            (day, kind)
            for day, kind in (
                (received["last_date"] if received else None, "receipt"),
                (spent["last_date"] if spent else None, "expenditure"),
                (committee["last_filing_date"], "filing"),
            )
            if day
        ]
        # On a date tie the first kind listed wins: money moving outranks the paperwork.
        last_date, last_kind = max(dated, key=lambda pair: pair[0]) if dated else (None, None)
        rows.append({
            "id": committee["id"],
            "name": committee["name"] or f"Committee {committee['id']}",
            "active": committee["active"],
            "last_date": last_date,
            "last_kind": last_kind,
            "recent": bool(last_date and recent_cutoff and last_date >= recent_cutoff),
            "idle": bool(last_date and muted_cutoff and last_date <= muted_cutoff),
            "last_filing_name": committee["last_filing_name"] or "",
            "raised": float(received["window_total"] or 0) if received else 0.0,
            "spent": float(spent["window_total"] or 0) if spent else 0.0,
            "cycle_raised": float(received["cycle_total"] or 0) if received else 0.0,
            "cycle_spent": float(spent["cycle_total"] or 0) if spent else 0.0,
        })
    # Stable sort: ties keep the name, id order from the query.
    rows.sort(key=lambda row: row["last_date"] or date.min, reverse=True)
    return rows


def _family_blocks(conn, rows: list[dict]) -> list[dict]:
    """Group ``rows`` (already newest activity first) into blocks for the committees table.

    Committees that share one ACCEPTED committee family (two or more of this candidate's) form a
    block labelled with the family; every other committee is its own unlabelled block. A block
    sits where its newest member sits, so ordering stays by last activity within and across blocks.
    """
    families = accepted_family_map(conn, [row["id"] for row in rows]) if _table_exists(conn, "committee_family") else {}
    sizes = Counter(family_id for family_id, _ in families.values())
    blocks: list[dict] = []
    placed: dict[int, dict] = {}
    for row in rows:
        family_id, label = families.get(row["id"], (None, None))
        if family_id is None or sizes[family_id] < 2:
            blocks.append({"label": None, "rows": [row]})
        elif family_id in placed:
            placed[family_id]["rows"].append(row)
        else:
            placed[family_id] = {"label": label, "rows": [row]}
            blocks.append(placed[family_id])
    return blocks


def _candidacies(conn, candidate_id: int) -> list:
    # Enum columns are cast to text so a NULL (outcome still pending) renders blank, never "None".
    return conn.execute(
        """
        SELECT election_year,
               COALESCE(election_type, '') AS election_type,
               COALESCE(race_type::text, '') AS race_type,
               COALESCE(outcome::text, '') AS outcome
        FROM isbe_candidacies
        WHERE candidate_id = ?
        ORDER BY election_year DESC NULLS LAST, election_type, id
        """,
        (candidate_id,),
    ).fetchall()


def _receipts_by_cycle(conn, candidate_id: int) -> list[dict]:
    """Non-archived receipts (amount > 0, every d2 part) per election cycle, newest cycle first."""
    rows = conn.execute(
        f"""
        SELECT EXTRACT(YEAR FROM r.received_date)::int AS year, SUM(r.amount) AS raised
        FROM isbe_condensed_receipts r
        WHERE r.archived = FALSE AND r.amount > 0 AND r.received_date IS NOT NULL
          AND r.committee_id IN {_LINKED_COMMITTEES}
        GROUP BY 1
        """,
        (candidate_id,),
    ).fetchall()
    by_cycle: dict[int, float] = {}
    for row in rows:
        cycle = row["year"] + row["year"] % 2  # an odd year belongs to the next even-year cycle
        by_cycle[cycle] = by_cycle.get(cycle, 0.0) + float(row["raised"])
    return [{"cycle": cycle, "raised": by_cycle[cycle]} for cycle in sorted(by_cycle, reverse=True)]


@candidates_bp.route("/<int:candidate_id>")
def candidate_detail(candidate_id):
    """Candidate profile: committees by last activity, candidacies, receipts by cycle."""
    conn = current_app.get_database()
    if not _table_exists(conn, "isbe_candidates"):
        abort(404)
    candidate = _candidate_header(conn, candidate_id)
    if candidate is None:
        abort(404)

    data_through = latest_data_date("isbe")
    scope = resolve_time_scope(default="all", axis="received", data_through=data_through)
    cycle_start, cycle_end = cycle_window(current_cycle())
    cycle_scope = replace(scope, start=cycle_start, end=cycle_end)

    committees = _committee_rows(conn, candidate_id, scope, cycle_scope, data_through)
    totals = {
        key: sum(row[key] for row in committees)
        for key in ("raised", "spent", "cycle_raised", "cycle_spent")
    }
    return render_template(
        "candidates/detail.html",
        candidate=candidate,
        committees=committees,
        committee_blocks=_family_blocks(conn, committees),
        totals=totals,
        candidacies=_candidacies(conn, candidate_id),
        cycles=_receipts_by_cycle(conn, candidate_id),
        time_scope=scope,
        scope_default_key="all",
        time_scope_source_label="ISBE",
        scope_query=scope_params(scope),
    )
