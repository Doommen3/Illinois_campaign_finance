"""Outside spending (D-2 Part 9): attribute independent expenditures to candidates.

Plan .claude/plans/outside-spending-drilldown.md, decisions D1-D4 and D6. A Part-9 row names
its candidate(s) in free text (``candidate_name``), so attribution is by name:

* key = (first token, last token) of the name with . , ' " stripped and case folded, so middle
  names and initials, punctuation and case do not matter ("Michelle N Pierre" meets
  "Michelle Pierre"); trailing generational suffixes (Jr, Sr, II, III, IV, V) are not the last
  token, so the candidate record "Barberis, Jr" (ISBE keeps the suffix in last_name) meets the
  filing "John Barberis";
* a row naming several candidates ("A, B; C and D") is split on , ; / & and the word "and";
  each named candidate takes amount / n, n = the names on the row (a bare suffix part, as in
  "Pat Example, Jr", and a repeated name are dropped, so n counts people); a single-comma
  "Last, First[ Middle]" ("Scherer, Sue") is the exception: one person, read as "Sue Scherer";
* a row attaches to a candidate only in an election cycle (year + year % 2) in which the
  candidate has a candidacy.

The candidate pages (webapp/routes/candidates.py) and the state race builder
(database/analytics.py) share these rules.
"""
from __future__ import annotations

import re
from datetime import date

from database.connection import _table_exists
from database.name_normalize import NICKNAMES

_SEPARATORS = re.compile(r"[,;/&]|\band\b", re.IGNORECASE)
_KEY_PUNCTUATION = re.compile(r"[.,'\"]")
# "Last, First[ Middle]": one comma, a one-token left side, a one- or two-token right side.
_LAST_FIRST = re.compile(r"\s*([^\s,;/&]+)\s*,\s*([^\s,;/&]+(?:\s+[^\s,;/&]+)?)\s*")
# A split part that is only one of these is not a name: "Carlos Rivas, Jr" names one candidate.
# A trailing one is not a key's last token either ("John Barberis Jr" is keyed on Barberis).
_GENERATIONAL_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV", "V"})

# SQL twin of the key's normalisation: the filed name with . , ' " stripped, upper-cased. A
# LIKE '%<LAST TOKEN>%' on it keeps every row whose key can match (a superset prefilter).
STRIPPED_NAME_SQL = "UPPER(regexp_replace(COALESCE(e.candidate_name, ''), '[.,''\"]', '', 'g'))"

# `like` is lower-case on purpose: database/pg_compat.py rewrites an upper-case LIKE to ILIKE, and
# the planner uses the partial index idx_isbe_condensed_expenditures_part9_date (scripts/
# isbe_sunshine_etl.py: WHERE d2_part LIKE '9%' AND archived = FALSE) only when the query repeats
# that predicate as LIKE.  The name predicate is lower-case too (an ILIKE on the UPPER()'d text
# only costs time).  The date bounds are the candidate's cycle span, a superset of the cycle gate,
# so the index scan reads only those years.
_CANDIDATE_ROWS_SQL = f"""
    SELECT e.id AS expenditure_id, e.expended_date, e.committee_id,
           COALESCE(c.name, 'Committee ' || e.committee_id) AS committee_name,
           e.amount, e.supporting, e.opposing, e.purpose,
           NULLIF(TRIM(CONCAT_WS(' ', e.first_name, e.last_name)), '') AS payee,
           e.office AS office_as_filed,
           e.candidate_name, e.candidate_name AS candidate_name_as_filed,
           e.filed_doc_id
    FROM isbe_condensed_expenditures e
    LEFT JOIN isbe_committees c ON c.id = e.committee_id
    WHERE e.d2_part like '9%' AND e.archived = FALSE
      AND e.expended_date >= ? AND e.expended_date <= ?
      AND {STRIPPED_NAME_SQL} like ?
    ORDER BY e.expended_date DESC, e.id DESC
"""


def split_candidate_names(text: str | None) -> list[str]:
    """The names in a Part-9 row's candidate text: split on , ; / & and the word "and", trimmed.

    A part that is a bare generational suffix (JR, SR, II, III, IV, V once . , ' " are stripped)
    or that repeats a part already kept (same text after that stripping, upper-cased, spaces
    collapsed) is dropped, so the list counts the people the row names.

    A text that is exactly one comma between a one-token "Last" and a one- or two-token
    "First[ Middle]" (no ; / & and no word "and") is one person filed last-name-first: it comes
    back first-name-first (["Sue Scherer"] for "Scherer, Sue"). A bare suffix on the right
    ("Example, Jr") is not a first name and takes the rule above.
    """
    pair = _LAST_FIRST.fullmatch(text or "")
    if pair and len(_SEPARATORS.findall(text)) == 1:  # the comma is the only separator
        last, first = pair.groups()
        if _KEY_PUNCTUATION.sub("", first).upper() not in _GENERATIONAL_SUFFIXES:
            return [f"{first} {last}"]
    names: list[str] = []
    seen: set[str] = set()
    for part in _SEPARATORS.split(text or ""):
        part = part.strip()
        if not part:
            continue
        normalised = " ".join(_KEY_PUNCTUATION.sub("", part).upper().split())
        if normalised in _GENERATIONAL_SUFFIXES or normalised in seen:
            continue
        seen.add(normalised)
        names.append(part)
    return names


def candidate_name_key(text: str | None) -> tuple[str, str] | None:
    """(first token, last token) of a name, . , ' " stripped, a hyphen read as a space, upper-cased;
    None below two tokens.

    "J.B." stays JB and "O'Brien" stays OBRIEN (removed, not split), while "Mary Smith-Jones" keys as
    (MARY, JONES), the same as "Mary Smith Jones" (M1). Trailing generational suffixes (JR, SR, II,
    III, IV, V) are dropped while more than two tokens remain, so "John Barberis, Jr" (the candidate
    record) and "John Barberis" (the filing) share one key, while "Pat Jr" stays (PAT, JR).
    """
    tokens = _KEY_PUNCTUATION.sub("", text or "").replace("-", " ").upper().split()
    while len(tokens) > 2 and tokens[-1] in _GENERATIONAL_SUFFIXES:
        tokens.pop()
    return (tokens[0], tokens[-1]) if len(tokens) >= 2 else None


def _first_word_aliases() -> dict[str, set[str]]:
    """NICKNAMES read on first words, both ways: "JB" <-> "JAY ROBERT" becomes JB <-> JAY."""
    aliases: dict[str, set[str]] = {}
    for name, alias_names in NICKNAMES.items():
        for alias in alias_names:
            first, other = name.split()[0], alias.split()[0]
            aliases.setdefault(first, set()).add(other)
            aliases.setdefault(other, set()).add(first)
    return aliases


_FIRST_WORD_ALIASES = _first_word_aliases()


def _first_name_aliases(first: str) -> set[str]:
    """The first token and its DIRECT aliases in the shared nickname map (database/name_normalize.NICKNAMES),
    a two-word alias by its first word as the key reads a name ("JB" -> {JB, JAY}; "JAY" -> {JAY, JB}).
    A filed name meets a record whose first token is in this set of the filed first token (decision P2-17):
    MIKE and MICHAEL, PAT and PATRICIA, TED and EDWARD meet; PATRICK and PATRICIA (both PAT), TED and ED
    (both EDWARD), THEODORE and ED do not -- no match through a shared third alias."""
    return {first, *_FIRST_WORD_ALIASES.get(first, ())}


def name_keys_match(key: tuple[str, str], other: tuple[str, str]) -> bool:
    """Whether two name keys name one person under the matcher: equal last tokens, and first tokens
    equal or direct aliases (P2-17). The twin rule of the race builder and the entity network (plan
    D4): a record whose key matches an earlier record's is the same person registered twice."""
    return key[1] == other[1] and other[0] in _first_name_aliases(key[0])


def part9_cycle(day: date) -> int:
    """Election cycle of a date: an even year is its own cycle, an odd year belongs to the next one."""
    return day.year + day.year % 2


def attribute_part9_rows(rows, candidates) -> list[dict]:
    """One dict per (row, matched candidate): the row's keys plus ``candidate``, ``share`` (amount over
    the names on the row), ``named_count`` and ``name_as_filed`` (the matching part of the text; a
    "Last, First" filing reads first-name-first).

    ``candidates`` carry ``key`` (candidate_name_key) and ``cycles``: the set of cycles a row's
    ``expended_date`` must fall in, or None for no gate; optionally ``alias_keys``, the keys of twin
    records folded into this one (P2-31), which reach it as its own key does (a candidate still takes
    a row once). Candidates sharing a key all take the row.
    A filed name meets a key when the last tokens are equal and the key's first token is the filed
    first token or a direct alias of it (M2 / P2-17: "Mike" meets "Michael"; see _first_name_aliases).
    The inputs are not modified.
    """
    by_key: dict[tuple[str, str], list[tuple[int, dict]]] = {}
    for position, candidate in enumerate(candidates):
        # Under its own key and its folded twins' keys; the filed name's aliases are looked up below.
        for key in [candidate["key"], *candidate.get("alias_keys", ())]:
            if key is not None:
                by_key.setdefault(key, []).append((position, candidate))
    attributions = []
    for row in rows:
        names = split_candidate_names(row["candidate_name"])
        day = row.get("expended_date")
        cycle = part9_cycle(day) if day is not None else None
        matched: set[int] = set()
        for name in names:
            key = candidate_name_key(name)
            matches = [] if key is None else sorted(  # the candidates' own order, whatever the alias set's
                (entry for alias in _first_name_aliases(key[0]) for entry in by_key.get((alias, key[1]), ())),
                key=lambda entry: entry[0],
            )
            for _position, candidate in matches:
                if id(candidate) in matched:
                    continue
                if candidate["cycles"] is not None and cycle not in candidate["cycles"]:
                    continue
                matched.add(id(candidate))
                attributions.append({
                    **row,
                    "candidate": candidate,
                    "share": float(row["amount"] or 0.0) / len(names),
                    "named_count": len(names),
                    "name_as_filed": name,
                })
    return attributions


def summarize(attributions) -> dict:
    """Sums of the shares (all, supporting, opposing), rows, and distinct spending committees."""
    return {
        "total": sum((item["share"] for item in attributions), 0.0),
        "support_total": sum((item["share"] for item in attributions if item["supporting"]), 0.0),
        "oppose_total": sum((item["share"] for item in attributions if item["opposing"]), 0.0),
        "row_count": len(attributions),
        "spender_count": len({item["committee_id"] for item in attributions}),
    }


def get_candidate_outside_spending(conn, candidate_id: int, *, scope) -> dict | None:
    """Part-9 rows attributed to one ISBE candidate, or None when nothing attaches all time.

    Reads ``isbe_condensed_expenditures`` (archived rows never count). ``scope`` is the page's
    TimeScope, applied to ``expended_date``: ``window``, ``by_spender`` (total desc) and ``rows``
    (line items, newest first) follow it, ``all_time`` does not.
    """
    tables = ("isbe_candidates", "isbe_candidacies", "isbe_condensed_expenditures", "isbe_committees")
    if not all(_table_exists(conn, table) for table in tables):
        return None
    person = conn.execute(
        "SELECT first_name, last_name FROM isbe_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if person is None:
        return None
    key = candidate_name_key(f"{person['first_name'] or ''} {person['last_name'] or ''}")
    if key is None:
        return None
    cycles = {
        row["election_year"] + row["election_year"] % 2
        for row in conn.execute(
            "SELECT DISTINCT election_year FROM isbe_candidacies WHERE candidate_id = ? AND election_year IS NOT NULL",
            (candidate_id,),
        ).fetchall()
    }
    if not cycles:  # no candidacy: no cycle can match
        return None

    span = (date(min(cycles) - 1, 1, 1), date(max(cycles), 12, 31))  # cycle C = (C-1)-01-01 .. C-12-31
    rows = conn.execute(_CANDIDATE_ROWS_SQL, (*span, f"%{key[1]}%")).fetchall()
    attributions = attribute_part9_rows(rows, [{"key": key, "cycles": cycles, "candidate_id": candidate_id}])
    if not attributions:
        return None

    window = [
        item for item in attributions
        if (scope.start is None or item["expended_date"] >= scope.start)
        and (scope.end is None or item["expended_date"] <= scope.end)
    ]
    by_committee: dict[int, list[dict]] = {}
    for item in window:
        by_committee.setdefault(item["committee_id"], []).append(item)
    by_spender = sorted(
        (
            {"committee_id": committee_id, "committee_name": items[0]["committee_name"], **summarize(items)}
            for committee_id, items in by_committee.items()
        ),
        key=lambda spender: (-spender["total"], spender["committee_name"]),
    )
    return {
        "all_time": summarize(attributions),
        "window": summarize(window),
        "by_spender": by_spender,
        "rows": window,
    }
