"""Search program, Phase 1 -- name matching system-wide: the tests-first suite (plan step S5).

Plan: .claude/plans/outside-spending-drilldown.md, "## Search program -- name matching system-wide" (decisions S1-S5) and
"### Contract -- database/name_normalize.py and the /search matching". The file starts red; every red test names behaviour the
implementation has to add. A test whose docstring says "(guard)" already passes on HEAD: it pins behaviour the change must
NOT break (an id path, a failure mode a careless fallback or cache key would introduce).

Contract pinned, literally:

  (1) database/name_normalize.py -- normalize_name, name_tokens, NICKNAMES, expand_token, search_tsquery, NAME_TSVECTOR_SQL.
  (2) Five GIN expression indexes on the contract string, each created by the right file: pattern tests (as
      tests/test_search_perf.py does for the trigram index), plus a plan test that goes one step further -- the SQL /search
      really runs must be servable by the index the file creates (a DDL that no predicate matches ships a 700 ms timeout).
  (3) /search: token matching (any order, prefix, nickname) on committees, donors, local and federal candidates; ONE substring
      fallback per section when the token path finds nothing and the query has >= 4 characters; the local candidate section also
      lists candidates WITHOUT a committee link (totals 0); committees reached through a matched candidate
      (isbe_candidate_committees + accepted committee_family members) are listed AFTER the name matches, once per committee id,
      marked "via candidate <name>"; numeric / key-like queries keep their id paths; the search_results cache key starts with "sn2"
      (search Phase 2, P2-23: the apostrophe rule R7 changed the matching, so "sn1" entries must not be served).

The seed world (all names fictional; every expected value below is derived from it in a comment next to the fixture):

  candidates   101 Michael W Exampleton (Treasurer)      linked to committees 201 and 203
               102 Michael Exampleton (State Senator)    linked to committee 203 (a joint committee with 101)
               103 Mike Exampleton (State Rep)           NO committee link at all
               104 Brad Exampleton (County Clerk)        the unrelated namesake, linked to committee 204
               301 Sean O'Neil-Smythe | 302 John Quillberis, Jr | 303 John Quillberis   (apostrophe / hyphen / suffix shapes, unlinked)
               304 Neil Decoyworth (unlinked; a plain Neil: "O'Neil" must not reach it, "Neil" must)
  committees   201 Friends of Exampleton | 202 Mike Exampleton for County Board (unlinked) | 203 Exampleton for Auditor
               204 Prairie State Leadership Fund (Brad's) | 205 Exampleton Victory Fund (unlinked, ACCEPTED family with 201)
               206 Friends of Someone Else | 207 Harbor Pending Fund (PENDING family with 201) | 208 Ridge Rejected Fund
               (REJECTED family with 201) | 209 Nonexampleton Holdings PAC (substring decoy)
  donors       Michael Exampleton 300 | Mike Exampleton 200 | M. Exampleton-Ruiz 400 | Thomas Exampleton 100 |
               Nonexampleton Group 50 (substring decoy)
  federal      H4IL99001 EXAMPLETON, MICHAEL W | H4IL99002 EXAMPLETON, BRAD

Readings of the contract chosen here (each is a decision the orchestrator can overturn by editing the named test):

  R1  A middle initial in the query is OPTIONAL (orchestrator decision 2026-10-29, amending the tester's first reading): the
      contract's own example is search_tsquery("Frerichs, Mike W.") == "frerichs:* & (mike:* | michael:*)". "Mike W. Exampleton"
      therefore finds the same records as "Mike Exampleton" (101, 102 and 103), not only the one whose name has a W-token
      (test_a_middle_initial_is_optional_*).
  R2  The substring fallback runs ONLY when the token path returned nothing (Contract), not as "token OR substring" (the wording
      of S2): the decoys 209 / Nonexampleton Group must NOT appear for "Exampleton" but must for the fragment "xamplet".
  R3  A committee found by NAME is listed unmarked even when it is also linked to a matched candidate; only committees reached
      through the candidate alone carry "via candidate".
  R4  The committees section does not depend on the type filter (type=committees lists the same rows as type=all).
  R5  Two spellings that normalise to one form share one cache entry ONLY while the raw text cannot change any section; a
      key-like query (contains | or :, or has >= 3 digits) feeds the donor-key and filed-doc sections with its raw text, so it
      keeps that text in the key.
  R6  Existing result order is preserved: local candidates and donors by total descending; name matches precede via-candidate rows.
  R7  (search Phase 2, P2-23) A word-internal apostrophe (a letter on both sides, ' or ’) is the one mark the QUERY side reads:
      "O'Neil" is the token O'NEIL and asks for the joined word or both halves, (oneil:* | (o:* & neil:*)), so it no longer
      finds every Neil; "O' Neil", "'Neil" and "Neil" are unchanged ("Neil" still finds the O'Neils). The stored side and
      the GIN index expression are untouched.

Accepted holes (named, not pinned): order inside the name-match block and inside the via-candidate block (collation dependent);
which of two matched candidates labels a joint committee; whether fec_name keeps being searched; accent folding (the contract
turns every non-ASCII character into a separator; 254 of ~1.19 M local names are non-ASCII); a NULL first or last name (the
contract's index expression first_name || ' ' || last_name is NULL-propagating; local isbe_candidates has none); the position of a
suffix token inside a query; the limit (30 / 50) with more matches than fit; Phase 2 surfaces.
"""
from __future__ import annotations

import ast
import re
from collections import namedtuple
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import webapp.cache_backend as cache_backend
import webapp.routes.main as main_routes
from database import name_normalize as nn
from database.connection import get_db, init_db
from database.pg_compat import PostgresCompatCursor
from webapp.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
ETL_SCRIPT = REPO_ROOT / "scripts" / "isbe_sunshine_etl.py"
SWAP_SCRIPT = REPO_ROOT / "scripts" / "swap_bulk_to_isbe.py"
SCHEMA_SQL = REPO_ROOT / "database" / "schema.sql"

# The ONE server-side normalization of the contract, spelled out here on purpose (not taken from the product) so the tests that
# compare Python with SQL measure the product against the contract.
CONTRACT_TSVECTOR = "to_tsvector('simple', regexp_replace(upper(coalesce({expr}, '')), '[^A-Z0-9 ]', ' ', 'g'))"


def _each(cases, check) -> None:
    """Run check(case) for every case and fail ONCE at the end, listing every failing case with its assertion message (a table
    test that stops at its first bad row hides the others from the implementer)."""
    cases = list(cases)
    failures = []
    for case in cases:
        try:
            check(case)
        except AssertionError as exc:
            failures.append(f"{case!r}: {exc}")
    assert not failures, f"{len(failures)} of {len(cases)} cases fail:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# (1) database/name_normalize.py -- pure functions
# ---------------------------------------------------------------------------

NORMALIZE_CASES = [
    # (input, normalize_name(input)); each expectation is the contract sentence applied by hand
    ("Mike Frerichs", "MIKE FRERICHS"),                       # upper-case
    ("  mike \t frerichs \n ", "MIKE FRERICHS"),              # whitespace of every kind collapses, ends trimmed
    ("Frerichs, Mike W.", "FRERICHS MIKE W"),                 # comma and period split; NO reordering
    ("O'Neil-Smith", "O NEIL SMITH"),                         # apostrophe and hyphen split tokens (M1: hyphen = space)
    ("Smith-Jones", "SMITH JONES"),
    ("St. Clair & Sons/Co.", "ST CLAIR SONS CO"),             # '&' and '/' split as well
    ('Robert "Bud" Jones', "ROBERT BUD JONES"),               # quotes
    ("snake_case", "SNAKE CASE"),                             # '_' is not A-Z, 0-9 or space
    ("Local 150 PAC", "LOCAL 150 PAC"),                       # digits survive
    ("Quillberis, Jr", "QUILLBERIS JR"),                          # the stored-side form keeps its suffix (query-side drop only)
    ("Exampleton, Mike", "EXAMPLETON MIKE"),                  # no "Last, First" reversal in the normalizer
    ("", ""),
    ("   ", ""),
    ("---", ""),                                              # nothing alphanumeric -> empty
    ("%%", ""),
    (None, ""),
]


def test_normalize_name_cases():
    """Kills: keeping the apostrophe or the hyphen (tokens "O'NEIL-SMITH"); `\\w`-style cleaning that keeps '_'; reordering
    "Last, First"; dropping the suffix on the stored side; returning None for None / blank instead of ''."""

    def check(case):
        text, expected = case
        assert nn.normalize_name(text) == expected

    _each(NORMALIZE_CASES, check)


def test_normalize_name_is_idempotent():
    """A second pass changes nothing, so the query side and the stored side can both be normalized without drift."""

    def check(case):
        once = nn.normalize_name(case[0])
        assert nn.normalize_name(once) == once

    _each(NORMALIZE_CASES, check)


TOKEN_CASES = [
    ("Mike Exampleton", ["MIKE", "EXAMPLETON"]),
    ("Exampleton, Mike W.", ["EXAMPLETON", "MIKE"]),          # order kept, the initial is dropped (R1)
    ("O'Neil-Smith", ["O'NEIL", "SMITH"]),                   # R7: a word-internal apostrophe keeps O'NEIL whole (no lone O)
    ("Mike Exampleton Jr", ["MIKE", "EXAMPLETON"]),           # generational suffixes drop on the query side ...
    ("Mike Exampleton Jr.", ["MIKE", "EXAMPLETON"]),
    ("John Smith Sr", ["JOHN", "SMITH"]),
    ("John Smith II", ["JOHN", "SMITH"]),
    ("John Smith III", ["JOHN", "SMITH"]),
    ("John Smith IV", ["JOHN", "SMITH"]),
    ("John Smith V", ["JOHN", "SMITH"]),
    ("Pat Jr", ["PAT", "JR"]),                                # ... only while more than one token remains: "Pat Jr" keeps JR
    ("Jr", ["JR"]),
    ("", []),
    ("   ", []),
    ("---", []),
    (None, []),
]


def test_name_tokens_cases():
    """Kills: no suffix drop; dropping only JR; dropping the suffix when it would leave a single token ("Pat Jr" -> ["PAT"]);
    dropping non-suffix tokens (the initial W); a string instead of a list; None for blank input."""

    def check(case):
        text, expected = case
        assert nn.name_tokens(text) == expected

    _each(TOKEN_CASES, check)


NORMALIZE_QUERY_CASES = [
    # (query, normalize_query(query)): normalize_name, except a word-internal apostrophe survives as "'" (R7)
    ("O'Neil-Smythe", "O'NEIL SMYTHE"),
    ("Smith O’Neil", "SMITH O'NEIL"),                         # the curly apostrophe becomes the straight one
    (None, ""),
    ("   ", ""),
    ("Mike Exampleton-Ruiz, Jr.", "MIKE EXAMPLETON RUIZ JR"),  # no apostrophe: exactly normalize_name
]


def test_normalize_query_keeps_a_word_internal_apostrophe():
    """R7. Kills: keeping every apostrophe (a leading or spaced one is a separator), dropping it (O NEIL), keeping the curly
    form, and drifting from normalize_name anywhere else."""

    def check(case):
        text, expected = case
        assert nn.normalize_query(text) == expected
        if text and "'" not in text and "’" not in text:
            assert nn.normalize_query(text) == nn.normalize_name(text)

    _each(NORMALIZE_QUERY_CASES, check)


def test_apostrophe_tsquery_semantics_in_postgresql():
    """R7 read by PostgreSQL itself (the stored side cuts O'Neil into O + NEIL; a filer may also write ONEIL): the compound
    group finds both spellings and not the plain Neil. Kills a Python-only reading of the group (e.g. 'o neil:*', a phrase
    query, or the halves ORed)."""
    tsquery = nn.search_tsquery("O'Neil Smythe")
    conn = get_db()

    def check(case):
        stored, expected = case
        row = conn.execute(
            f"SELECT ({CONTRACT_TSVECTOR.format(expr='CAST(? AS TEXT)')}) @@ to_tsquery('simple', ?) AS hit", (stored, tsquery)
        ).fetchone()
        assert row["hit"] is expected, f"{stored!r} vs {tsquery!r}"

    _each([("SEAN O NEIL SMYTHE", True), ("SEAN ONEIL SMYTHE", True), ("NEIL SMYTHE", False)], check)


# The contract's nickname list, nickname -> the full names it stands for ("X/Y <-> Z" = X and Y both alias Z).
# "JB <-> JAY ROBERT" is left out on purpose: its alias is two tokens (see test_every_nickname_..._postgresql_accepts).
CONTRACT_NICKNAMES = {
    "MIKE": ("MICHAEL",), "TOM": ("THOMAS",), "BOB": ("ROBERT",), "ROB": ("ROBERT",), "BILL": ("WILLIAM",),
    "WILL": ("WILLIAM",), "JIM": ("JAMES",), "DAN": ("DANIEL",), "SAM": ("SAMUEL", "SAMANTHA"), "CHUY": ("JESUS",),
    "PAT": ("PATRICK", "PATRICIA"), "TONY": ("ANTHONY",), "CHRIS": ("CHRISTOPHER", "CHRISTINE"), "LIZ": ("ELIZABETH",),
    "BETH": ("ELIZABETH",), "KATE": ("KATHERINE", "KATHLEEN"), "KATIE": ("KATHERINE", "KATHLEEN"), "JOE": ("JOSEPH",),
    "DAVE": ("DAVID",), "STEVE": ("STEVEN", "STEPHEN"), "RICK": ("RICHARD",), "DICK": ("RICHARD",), "ED": ("EDWARD",),
    "TED": ("THEODORE", "EDWARD"), "JEFF": ("JEFFREY",), "MATT": ("MATTHEW",), "NICK": ("NICHOLAS",),
    "ALEX": ("ALEXANDER", "ALEXANDRA"), "JEN": ("JENNIFER",), "JENNY": ("JENNIFER",), "SUE": ("SUSAN",),
    "PEG": ("MARGARET",), "PEGGY": ("MARGARET",),
}


def test_nicknames_is_a_symmetric_map_of_uppercase_tokens():
    """The contract's NICKNAMES: dict[str, tuple[str, ...]], "aliases both ways". Kills: an empty map; lower-case keys (the
    query tokens are upper-case); a one-way map (ROBERT -> BOB missing, so "Robert Smith" would not find "Bob Smith"); a name
    listed as its own alias; aliases that are not tuples."""
    assert isinstance(nn.NICKNAMES, dict)
    assert nn.NICKNAMES, "NICKNAMES is empty"
    for key, aliases in nn.NICKNAMES.items():
        assert key == key.upper(), key
        assert isinstance(aliases, tuple), (key, aliases)
        assert aliases, key
        assert key not in aliases, key
        for alias in aliases:
            assert alias == alias.upper(), (key, alias)
            assert key in nn.NICKNAMES.get(alias, ()), f"{key} -> {alias} but {alias} does not map back to {key}"


def test_every_contract_nickname_expands_both_ways():
    """One entry per contract pair (33 nicknames). The full name is in the nickname's expansion AND the nickname is in the full
    name's, so a query typed either way finds a record stored the other way. Kills: a map holding only the MIKE / MICHAEL pair
    of the examples; a one-way expansion."""

    def check(nickname):
        assert nickname in nn.expand_token(nickname)
        for full in CONTRACT_NICKNAMES[nickname]:
            assert full in nn.expand_token(nickname), f"{nickname} does not expand to {full}"
            assert nickname in nn.expand_token(full), f"{full} does not expand back to {nickname}"

    _each(sorted(CONTRACT_NICKNAMES), check)


def test_expand_token_is_the_token_plus_its_direct_aliases_only():
    """Kills: a transitive closure (ED -> EDWARD -> TED would make "Ed Smith" find "Ted Smith"; SAMUEL -> SAM -> SAMANTHA);
    dropping the token itself from its own expansion; expanding an ordinary surname; a list instead of a set."""
    assert isinstance(nn.expand_token("MIKE"), set)
    assert nn.expand_token("EXAMPLETON") == {"EXAMPLETON"}
    assert nn.expand_token("FRERICHS") == {"FRERICHS"}
    assert "TED" not in nn.expand_token("ED")
    assert "ED" not in nn.expand_token("TED")
    assert "SAMANTHA" not in nn.expand_token("SAMUEL")
    assert "SAMUEL" not in nn.expand_token("SAMANTHA")
    assert "ROB" not in nn.expand_token("BOB")
    assert "WILL" not in nn.expand_token("BILL")


_GROUP = r"(?:[a-z0-9]+:\*|\([a-z0-9]+:\*(?: \| [a-z0-9]+:\*)+\))"
_TSQUERY_FORMAT = re.compile(rf"^{_GROUP}(?: & {_GROUP})*$")


def _groups(tsquery: str) -> list[frozenset[str]]:
    """The AND-ed groups of a contract tsquery, each group the SET of its prefix lexemes (alternation order is free)."""
    assert _TSQUERY_FORMAT.match(tsquery), f"not in the contract's format: {tsquery!r}"
    return [frozenset(part.strip("()").split(" | ")) for part in tsquery.split(" & ")]


TSQUERY_CASES = [
    # (query, the lexemes each query token's group must at least contain, in query-token order)
    ("Mike Frerichs", [{"mike:*", "michael:*"}, {"frerichs:*"}]),
    ("Frerichs, Mike W.", [{"frerichs:*"}, {"mike:*", "michael:*"}]),               # the contract's own example: the initial is optional
    ("Frerichs Mike", [{"frerichs:*"}, {"mike:*", "michael:*"}]),                  # an alias is expanded in ANY position
    ("MICHAEL EXAMPLETON", [{"michael:*", "mike:*"}, {"exampleton:*"}]),           # upper-case query -> lower-case lexemes
    ("Sam Ames", [{"sam:*", "samuel:*", "samantha:*"}, {"ames:*"}]),
    ("Ted Ames", [{"ted:*", "theodore:*", "edward:*"}, {"ames:*"}]),
    ("Edward Ames", [{"edward:*", "ed:*", "ted:*"}, {"ames:*"}]),
    ("Mike Exampleton Jr", [{"mike:*", "michael:*"}, {"exampleton:*"}]),           # query-side suffix drop reaches the tsquery
    ("Pat Jr", [{"pat:*", "patrick:*", "patricia:*"}, {"jr:*"}]),                  # ... but not when it would leave one token
]


def test_search_tsquery_groups_one_alternation_per_query_token():
    """The contract format "(mike:* | michael:*) & frerichs:*", alternation order free. Each group must contain the
    contract's lexemes and be exactly what expand_token gives for that query token. Kills: aliases only on the first token; a
    flat OR of every lexeme; no prefix marker; upper-case lexemes (the 'simple' dictionary does not fold the tsquery side);
    reordering the tokens; a dropped middle initial."""

    def check(case):
        query, cores = case
        tsquery = nn.search_tsquery(query)
        assert tsquery is not None
        groups = _groups(tsquery)
        tokens = nn.name_tokens(query)
        assert len(groups) == len(cores) == len(tokens), f"{tsquery!r}"
        for position, (group, core, token) in enumerate(zip(groups, cores, tokens)):
            assert core <= group, f"group {position} of {tsquery!r} lacks {sorted(core - group)}"
            expected = {alias.lower() + ":*" for alias in nn.expand_token(token)}
            assert group == expected, f"group {position} of {tsquery!r} is not expand_token({token!r})"

    _each(TSQUERY_CASES, check)


TSQUERY_EXACT = [
    ("O'Neil-Smith", "(oneil:* | (o:* & neil:*)) & smith:*"),   # R7: the joined word or both halves
    ("D’Angelo", "(dangelo:* | (d:* & angelo:*))"),             # the curly apostrophe reads the same
    ("O' Neil", "neil:*"),                                       # not word-internal: a separator, the lone O drops (R1)
    ("'Neil", "neil:*"),
    ("Jr", "jr:*"),
    ("Local 150", "local:* & 150:*"),
    ("Exampleton", "exampleton:*"),
    ("  EXAMPLETON,   Ruiz ", "exampleton:* & ruiz:*"),
]


def test_search_tsquery_exact_strings_for_tokens_without_aliases():
    """The contract's literal strings. Kills: a different joiner ("&&", no spaces), a missing prefix marker, a hyphen kept
    inside a lexeme, upper-case lexemes; for R7, an apostrophe kept inside a lexeme ("o'neil:*", which to_tsquery would split),
    the joined word or the halves alone, and a leading or spaced apostrophe read as word-internal."""

    def check(case):
        query, expected = case
        assert nn.search_tsquery(query) == expected

    _each(TSQUERY_EXACT, check)


def test_search_tsquery_is_none_when_no_token_remains():
    """None, not '' and not a tsquery that matches everything: the route must be able to skip the token predicate."""

    def check(query):
        assert nn.search_tsquery(query) is None

    _each(["", "   ", "%%", "---", "!!!!", "(( ))", None], check)


def test_name_tsvector_sql_is_the_contract_string():
    """The ONE server-side normalization, used verbatim in predicates and GIN expression indexes (so the planner can use them)."""
    assert nn.NAME_TSVECTOR_SQL("x") == CONTRACT_TSVECTOR.format(expr="x")
    expression = "first_name || ' ' || last_name"
    assert nn.NAME_TSVECTOR_SQL(expression) == CONTRACT_TSVECTOR.format(expr=expression)


# ---------------------------------------------------------------------------
# (1b) the pure functions against PostgreSQL itself
# ---------------------------------------------------------------------------

def test_every_nickname_key_and_alias_gives_a_tsquery_postgresql_accepts():
    """The contract lists "JB <-> JAY ROBERT": an alias of TWO tokens. Written into an alternation as one lexeme it is
    `(jb:* | jay robert:*)`, which to_tsquery rejects ("syntax error in tsquery"); the search section would then fail for every
    query that starts with JB and the page would show "Some sections failed". Whatever the map holds, every key and every
    alias used as a query token must give a tsquery PostgreSQL parses."""
    tokens = set(nn.NICKNAMES)
    for aliases in nn.NICKNAMES.values():
        tokens.update(aliases)
    assert tokens, "NICKNAMES is empty"
    conn = get_db()
    rejected = {}
    for token in sorted(tokens):
        tsquery = nn.search_tsquery(f"{token} Exampleton")
        try:
            conn.execute("SELECT to_tsquery('simple', ?)", (tsquery,)).fetchone()
        except Exception as exc:  # noqa: BLE001 - the message is the finding
            rejected[token] = (tsquery, str(exc).splitlines()[0])
            conn.rollback()
    assert rejected == {}


AGREEMENT_STRINGS = [
    "M. Exampleton-Ruiz",
    "O'Neil-Smith",
    "Mc'Donald-Smith, Jr. & Sons/Co. (Local #150) _x_ \t\n y",
    "AT&T / Verizon",
    "snake_case_name",
    'Robert "Bud" Jones',
    "a.b.c d,e;f:g",
    "  lead and trail  ",
    "H4IL99001 2nd Ward",
    "50% Off, 100%",
    "back\\slash",
]


def test_python_normalization_and_the_sql_expression_cut_names_into_the_same_tokens():
    """The query is cut in Python, the stored name in SQL; a character one side treats as a separator and the other keeps
    (underscore, apostrophe, tab, '%') is a silent non-match. Compared token set by token set on every ASCII punctuation shape.
    Kills: `\\w` / `isalnum()` based Python cleaning (keeps '_' and Unicode letters); keeping apostrophes; not splitting on tabs."""
    conn = get_db()

    def check(text):
        rows = conn.execute(
            f"SELECT unnest(tsvector_to_array({CONTRACT_TSVECTOR.format(expr='CAST(? AS TEXT)')})) AS lexeme", (text,)
        ).fetchall()
        sql_tokens = {row["lexeme"] for row in rows}
        python_tokens = {token.lower() for token in nn.normalize_name(text).split()}
        assert python_tokens == sql_tokens

    _each(AGREEMENT_STRINGS, check)


MATCH_CASES = [
    # (stored name, query, does the tsvector of the stored name match the query's tsquery)
    ("Michael W Exampleton", "Mike Exampleton", True),            # nickname, and an extra middle token on the stored side
    ("Michael W Exampleton", "Exampleton Mike", True),            # any order
    ("Michael W Exampleton", "Exampleton, Michael W.", True),     # "Last, First M."
    ("Michael W Exampleton", "mike w. exampleton", True),
    ("Michael Exampleton", "Mike W. Exampleton", True),           # R1: the initial is optional
    ("Mike Exampleton", "Michael Exampleton", True),              # aliases both ways
    ("Brad Exampleton", "Mike Exampleton", False),                # the surname alone is not enough
    ("Thomas Exampleton", "Mike Exampleton", False),
    ("Thomas Exampleton", "Tom Exampleton", True),                # a second nickname pair: the map is not a MIKE special case
    ("M. Exampleton-Ruiz", "Michael Exampleton", False),          # the initial M does not extend "michael"
    ("M. Exampleton-Ruiz", "Exampleton-Ruiz", True),              # hyphen = space on both sides
    ("M. Exampleton-Ruiz", "Exampleton Ruiz", True),
    ("Exampleton Ruiz", "Exampleton-Ruiz", True),
    ("Friends of Exampleton", "Friends of", True),                # organisation words are tokens; "of" is not a stop word
    ("Friends of Exampleton", "of friends", True),
    ("Friends of Exampleton", "Friend Exam", True),               # prefix on every token
    ("Mike Exampleton", "Mike Exam", True),                       # search-as-you-type: the last token is a prefix
    ("Friends of Exampleton", "xampleton", False),                # a prefix starts at a token start (the fallback covers this)
    ("Friends of Exampleton", "Exampleton Friends Fund", False),  # every query token must match some token
    ("John Quillberis, Jr", "John Quillberis", True),
    ("John Quillberis, Jr", "John Quillberis Jr", True),
    ("John Quillberis", "John Quillberis Jr", True),                  # the suffix is dropped on the QUERY side only
    ("Sean O'Neil-Smythe", "O'Neil Smythe", True),
    ("Sean O'Neil-Smythe", "Neil Smythe", True),
    ("Sean O'Neil-Smythe", "O'Neil", True),                       # R7: both halves
    ("Neil Decoyworth", "O'Neil", False),                         # R7: a plain Neil is not an O'Neil
    ("Local 150 PAC", "Local 15", True),                          # numeric prefix
    ("Edward Ames", "Ted Ames", True),
    ("Ted Ames", "Ed Ames", False),                               # one hop: ED is not TED
]


def test_the_contract_predicate_matches_names_the_way_the_plan_says():
    """normalize + expand + the tsvector expression, end to end on PostgreSQL. Hand-derived expectations above; the False rows
    are as important as the True ones (they kill an OR instead of AND, surname-only matching, a transitive alias, a dropped
    initial, a prefix that works inside a token). name_matches, the Python twin the Viz Lab uses, must read every case the
    same way (search Phase 2: it is kept in step with the tsquery, R7 included)."""
    conn = get_db()

    def check(case):
        stored, query, expected = case
        tsquery = nn.search_tsquery(query)
        assert tsquery is not None
        row = conn.execute(
            f"SELECT ({CONTRACT_TSVECTOR.format(expr='CAST(? AS TEXT)')}) @@ to_tsquery('simple', ?) AS hit", (stored, tsquery)
        ).fetchone()
        assert row["hit"] is expected, f"{stored!r} vs {query!r} ({tsquery})"
        assert nn.name_matches(query, stored) is expected, f"name_matches({query!r}, {stored!r})"

    _each(MATCH_CASES, check)


# ---------------------------------------------------------------------------
# (2) the five GIN expression indexes: pattern tests (+ the schema.sql ones built for real)
# ---------------------------------------------------------------------------

def _collapse(text: str) -> str:
    return " ".join(text.split())


def _python_sql_text(path: Path) -> str:
    """Every string literal of a Python file with SQL comments removed, whitespace collapsed: SQL that only appears in a
    comment (or in code that is not a string) is not there."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pieces = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    return _collapse(re.sub(r"--[^\n]*", " ", "\n".join(pieces)))


def _sql_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return _collapse(re.sub(r"--[^\n]*", " ", text))


def _source_sql(path: Path) -> str:
    return _python_sql_text(path) if path.suffix == ".py" else _sql_text(path)


def _index_statement(source_sql: str, name: str):
    found = re.search(rf"CREATE INDEX(?: IF NOT EXISTS)? {re.escape(name)}\b[^;]*;", source_sql)
    return found.group(0) if found else None


# (index name, table, the file that must create it, the column(s) its expression must be built on, the table's CREATE text)
INDEXES = [
    ("idx_isbe_committees_name_tsv", "isbe_committees", ETL_SCRIPT, ("name",), "CREATE TABLE isbe_committees ("),
    ("idx_isbe_candidates_name_tsv", "isbe_candidates", ETL_SCRIPT, ("first_name", "last_name"), "CREATE TABLE isbe_candidates ("),
    (
        "idx_analytics_donor_summary_name_tsv", "analytics_donor_summary", SCHEMA_SQL, ("donor_name",),
        "CREATE TABLE IF NOT EXISTS analytics_donor_summary (",
    ),
    (
        "idx_fec_candidate_match_name_tsv", "fec_candidate_match", SCHEMA_SQL, ("candidate_name",),
        "CREATE TABLE IF NOT EXISTS fec_candidate_match (",
    ),
    (
        "idx_bulk_cand_cmte_finance_agg_name_tsv", "bulk_candidate_committee_finance_agg", SWAP_SCRIPT,
        ("candidate_full_name",), None,
    ),
]
INDEX_IDS = [spec[0] for spec in INDEXES]


@pytest.mark.parametrize("name, table, source, columns, _table_sql", INDEXES, ids=INDEX_IDS)
def test_the_contract_index_is_created_by_the_right_file_on_the_contract_expression(name, table, source, columns, _table_sql):
    """Pattern test, house style (tests/test_search_perf.py): the statement is in the file's SQL (not a comment) and reads
    `CREATE INDEX [IF NOT EXISTS] <name> ON <table> USING gin (to_tsvector('simple', regexp_replace(upper(coalesce(<column
    expression>, '')), '[^A-Z0-9 ]', ' ', 'g')));`. Kills: a btree index (cannot serve @@); a missing index; the right index in
    the wrong file (ETL vs schema.sql vs swap script rebuild different objects); another table or column; an expression that
    differs from NAME_TSVECTOR_SQL (the planner would ignore the index); a different dictionary than 'simple'."""
    statement = _index_statement(_source_sql(source), name)
    assert statement is not None, f"no `CREATE INDEX {name} ...;` in {source.relative_to(REPO_ROOT)}"
    body = statement.split(name, 1)[1].strip()
    head = f"ON {table} USING gin (to_tsvector('simple', regexp_replace(upper(coalesce("
    tail = ", '')), '[^A-Z0-9 ]', ' ', 'g')));"
    assert body.startswith(head), f"{name} starts `{body[:len(head)]}`, expected `{head}`"
    assert body.endswith(tail), f"{name} ends `{body[-len(tail):]}`, expected `{tail}`"
    expression = body[len(head):len(body) - len(tail)]
    for column in columns:
        assert re.search(rf"\b{column}\b", expression), f"{name}: expression `{expression}` is not built on {column}"


def test_the_contract_indexes_come_after_the_tables_they_are_built_on():
    """schema.sql and the ETL DDL run top to bottom: an index above its table fails the whole script (and with it every test,
    every import). Kills the right statement in the wrong place."""

    def check(spec):
        name, table, source, _columns, table_sql = spec
        code = _source_sql(source)
        table_at = code.find(table_sql)
        index_at = code.find(f"{name} ON {table} ")
        assert table_at != -1, f"{table_sql!r} is no longer in {source.name}"
        assert index_at != -1, f"no `{name} ON {table}` in {source.name}"
        assert table_at < index_at, f"{name} is created above the CREATE TABLE of {table}"

    _each([spec for spec in INDEXES if spec[4]], check)


def test_schema_sql_builds_the_donor_and_fec_indexes_as_gin_on_the_expression():
    """Behaviour, not text: init_db() applies schema.sql to the per-test schema and both indexes exist as GIN indexes over a
    to_tsvector expression of the right column. Kills a statement that is textually right but never runs (guarded by an
    always-false condition, placed after an early return) or that PostgreSQL rewrote into something else."""
    conn = get_db()
    init_db()
    names = [spec[0] for spec in INDEXES if spec[2] == SCHEMA_SQL]
    rows = conn.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() AND indexname IN (?, ?)", tuple(names)
    ).fetchall()
    definitions = {row["indexname"]: row["indexdef"] for row in rows}
    assert sorted(definitions) == sorted(names)
    assert "USING gin" in definitions["idx_analytics_donor_summary_name_tsv"]
    assert "to_tsvector" in definitions["idx_analytics_donor_summary_name_tsv"]
    assert "donor_name" in definitions["idx_analytics_donor_summary_name_tsv"]
    assert "USING gin" in definitions["idx_fec_candidate_match_name_tsv"]
    assert "to_tsvector" in definitions["idx_fec_candidate_match_name_tsv"]
    assert "candidate_name" in definitions["idx_fec_candidate_match_name_tsv"]


# ---------------------------------------------------------------------------
# The seed world
# ---------------------------------------------------------------------------

def _seed_world(conn) -> None:
    # --- ISBE tables as the ETL creates them (columns /search can read; the two ENUM columns of isbe_committees left out)
    conn.execute(
        """
        CREATE TABLE isbe_committees (
            id INTEGER PRIMARY KEY, type TEXT, state_committee BOOLEAN, local_committee BOOLEAN, refer_name TEXT,
            name TEXT, address1 TEXT, address2 TEXT, address3 TEXT, city TEXT, state TEXT, zipcode TEXT,
            active BOOLEAN DEFAULT TRUE, status_date DATE, creation_date DATE, creation_amount DOUBLE PRECISION,
            party TEXT, purpose TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_committees (id, type, name, party, city, state) VALUES
            (201, 'Candidate', 'Friends of Exampleton', 'Democratic', 'Chicago', 'IL'),
            (202, 'Candidate', 'Mike Exampleton for County Board', 'Republican', 'Peoria', 'IL'),
            (203, 'Candidate', 'Exampleton for Auditor', 'Democratic', 'Springfield', 'IL'),
            (204, 'Political Action', 'Prairie State Leadership Fund', NULL, 'Peoria', 'IL'),
            (205, 'Candidate', 'Exampleton Victory Fund', 'Democratic', 'Chicago', 'IL'),
            (206, 'Candidate', 'Friends of Someone Else', NULL, 'Joliet', 'IL'),
            (207, 'Candidate', 'Harbor Pending Fund', NULL, 'Waukegan', 'IL'),
            (208, 'Candidate', 'Ridge Rejected Fund', NULL, 'Rockford', 'IL'),
            (209, 'Political Action', 'Nonexampleton Holdings PAC', NULL, 'Elgin', 'IL')
        """
    )
    conn.execute(
        """
        CREATE TABLE isbe_candidates (
            id INTEGER PRIMARY KEY, last_name TEXT, first_name TEXT, address1 TEXT, address2 TEXT, city TEXT, state TEXT,
            zipcode TEXT, office TEXT, district_type TEXT, district TEXT, residence_county TEXT, party TEXT,
            redaction_requested BOOLEAN DEFAULT FALSE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_candidates (id, first_name, last_name, office, district_type, district, party) VALUES
            (101, 'Michael W', 'Exampleton', 'Treasurer', 'Statewide', NULL, 'Democratic'),
            (102, 'Michael', 'Exampleton', 'State Senator', 'Senate District', '12', 'Republican'),
            (103, 'Mike', 'Exampleton', 'State Representative', 'Representative District', '45', 'Independent'),
            (104, 'Brad', 'Exampleton', 'County Clerk', 'County', 'Cook', 'Democratic'),
            (301, 'Sean', 'O''Neil-Smythe', 'Alderman', 'Municipal', '7', NULL),
            (302, 'John', 'Quillberis, Jr', 'Trustee', 'Community College', '3', NULL),
            (303, 'John', 'Quillberis', 'Trustee', 'Community College', '3', NULL),
            (304, 'Neil', 'Decoyworth', 'Trustee', 'Park District', '2', NULL)
        """
    )
    conn.execute("CREATE TABLE isbe_candidate_committees (id INTEGER PRIMARY KEY, committee_id INTEGER, candidate_id INTEGER)")
    conn.execute(
        # 101 -> 201, 203 | 102 -> 203 (joint with 101) | 104 -> 204 | 103 has NO link; 202 and 205 are linked to nobody
        "INSERT INTO isbe_candidate_committees (id, committee_id, candidate_id) VALUES (1, 201, 101), (2, 203, 101), "
        "(3, 203, 102), (4, 204, 104)"
    )

    # --- the finance aggregate as a TABLE with the view's 19 columns (a materialized view in production); only linked candidates
    conn.execute(
        """
        CREATE TABLE bulk_candidate_committee_finance_agg (
            candidate_id INTEGER, candidate_full_name TEXT, office_sought TEXT, district_type TEXT, district TEXT,
            candidate_party_affiliation TEXT, committee_id_sbe INTEGER, committee_name TEXT, committee_type TEXT,
            committee_party_affiliation TEXT, period_year INTEGER, election_cycle INTEGER, filing_count INTEGER,
            sum_total_receipts DOUBLE PRECISION, sum_total_expenditures DOUBLE PRECISION,
            max_ending_funds_available DOUBLE PRECISION, archived_filing_count INTEGER, period_start_date TEXT,
            period_end_date TEXT
        )
        """
    )
    conn.execute(
        # Totals the page must show (SUM of sum_total_receipts, COUNT DISTINCT committee_id_sbe), as the old query computed them:
        #   101: 1000.0 (cmte 201, 2024) + 250.0 (201, 2026) + 500.0 (203, 2026) = 1750.0 over {201, 203} = 2 committees
        #   102: 500.0 (203, 2026)                                                =  500.0 over {203}      = 1 committee
        #   104:  90.0 (204, 2026)                                                =   90.0 over {204}      = 1 committee
        #   103: no row (no committee link)                                       =    0.0                 = 0 committees
        """
        INSERT INTO bulk_candidate_committee_finance_agg VALUES
            (101, 'Michael W Exampleton', 'Treasurer', 'Statewide', NULL, 'Democratic',
             201, 'Friends of Exampleton', 'Candidate', 'Democratic', 2024, 2024, 1, 1000.0, 400.0, 600.0, 0,
             '2024-01-05', '2024-03-15'),
            (101, 'Michael W Exampleton', 'Treasurer', 'Statewide', NULL, 'Democratic',
             201, 'Friends of Exampleton', 'Candidate', 'Democratic', 2026, 2026, 1, 250.0, 100.0, 750.0, 0,
             '2026-01-05', '2026-03-15'),
            (101, 'Michael W Exampleton', 'Treasurer', 'Statewide', NULL, 'Democratic',
             203, 'Exampleton for Auditor', 'Candidate', 'Democratic', 2026, 2026, 1, 500.0, 120.0, 380.0, 0,
             '2026-02-01', '2026-04-01'),
            (102, 'Michael Exampleton', 'State Senator', 'Senate District', '12', 'Republican',
             203, 'Exampleton for Auditor', 'Candidate', 'Democratic', 2026, 2026, 1, 500.0, 120.0, 380.0, 0,
             '2026-02-01', '2026-04-01'),
            (104, 'Brad Exampleton', 'County Clerk', 'County', 'Cook', 'Democratic',
             204, 'Prairie State Leadership Fund', 'Political Action', NULL, 2026, 2026, 1, 90.0, 10.0, 80.0, 0,
             '2026-03-01', '2026-03-31')
        """
    )

    # --- committee families: only the ACCEPTED one (201 + 205) may add a committee through a candidate
    conn.execute(
        "INSERT INTO committee_family (id, label, status, member_hash) VALUES "
        "(1, 'Friends of Exampleton', 'accepted', 'hash-accepted'), (2, 'Harbor Pending Fund', 'pending', 'hash-pending'), "
        "(3, 'Ridge Rejected Fund', 'rejected', 'hash-rejected')"
    )
    conn.execute(
        "INSERT INTO committee_family_member (family_id, committee_id) VALUES (1, 201), (1, 205), (2, 201), (2, 207), (3, 201), (3, 208)"
    )

    # --- donors (the key of 'Mike Exampleton' starts with the donor-key form the key-like cache test pastes)
    conn.execute(
        """
        INSERT INTO analytics_donor_summary (source, donor_key, donor_name, donor_address, donor_city, donor_state,
                                             total_amount, contribution_count, committee_count) VALUES
            ('bulk_receipts', 'michael|exampleton|1 oak st|chicago|il|60601', 'Michael Exampleton',
             '1 Oak St, Chicago, IL 60601', 'Chicago', 'IL', 300.0, 3, 1),
            ('bulk_receipts', 'mike|exampleton|9 elm st|peoria|il|61602', 'Mike Exampleton',
             '9 Elm St, Peoria, IL 61602', 'Peoria', 'IL', 200.0, 2, 1),
            ('bulk_receipts', 'm|exampleton-ruiz|3 pine st|joliet|il|60432', 'M. Exampleton-Ruiz',
             '3 Pine St, Joliet, IL 60432', 'Joliet', 'IL', 400.0, 4, 2),
            ('bulk_receipts', 'thomas|exampleton|5 birch st|aurora|il|60505', 'Thomas Exampleton',
             '5 Birch St, Aurora, IL 60505', 'Aurora', 'IL', 100.0, 1, 1),
            ('bulk_receipts', 'nonexampleton group||7 cedar st|elgin|il|60120', 'Nonexampleton Group',
             '7 Cedar St, Elgin, IL 60120', 'Elgin', 'IL', 50.0, 1, 1)
        """
    )

    # --- two federal candidates (FEC style "LAST, FIRST M")
    conn.execute(
        "INSERT INTO fec_il_candidate_seed (candidate_key, candidate_name, normalized_candidate_name) VALUES "
        "('ck-exampleton-michael', 'EXAMPLETON, MICHAEL W', 'EXAMPLETON MICHAEL W'), "
        "('ck-exampleton-brad', 'EXAMPLETON, BRAD', 'EXAMPLETON BRAD')"
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_match (seed_candidate_key, candidate_name, fec_name, fec_candidate_id, office, district,
                                         cycle, match_status, match_score) VALUES
            ('ck-exampleton-michael', 'EXAMPLETON, MICHAEL W', 'EXAMPLETON, MICHAEL W', 'H4IL99001', 'U.S. House', 'IL-01',
             2026, 'matched', 0.95),
            ('ck-exampleton-brad', 'EXAMPLETON, BRAD', 'EXAMPLETON, BRAD', 'H4IL99002', 'U.S. House', 'IL-02',
             2026, 'matched', 0.95)
        """
    )
    conn.commit()


def _app_config(db_path: str, **overrides) -> dict:
    config = {
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "DASHBOARD_PREWARM_ENABLED": False,
        "ROUTE_PERF_CACHE_ENABLED": False,
        "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS": 0,
        # The 700 ms section budget is real; a loaded machine must not turn a tiny query into a timed-out section.
        "SEARCH_QUERY_TIMEOUT_MS": 30000,
    }
    config.update(overrides)
    return config


def _build_world(tmp_path: Path) -> str:
    db_path = str(tmp_path / "search_names.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_world(conn)
    conn.close()
    return db_path


@pytest.fixture
def world_client(tmp_path):
    return create_app(_app_config(_build_world(tmp_path))).test_client()


# ---------------------------------------------------------------------------
# Reading the rendered page (stdlib only: bs4 is not in requirements.txt). Columns are found by their <th> text, so a column the
# implementation adds does not move the ones read here.
# ---------------------------------------------------------------------------

def _squash(parts) -> str:
    return " ".join(" ".join(parts).split())


class _RowParser(HTMLParser):
    """Cell texts of one <tr>: `cells` with everything, `visible` without the collapsed <details> panels, and the anchors."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self.visible: list[str] = []
        self.anchors: list[tuple[str, str, bool]] = []
        self._cell = None
        self._cell_visible = None
        self._href = None
        self._anchor_text: list[str] = []
        self._details = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._cell, self._cell_visible = [], []
        elif tag == "details":
            self._details += 1
        elif tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._anchor_text = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self.cells.append(_squash(self._cell))
            self.visible.append(_squash(self._cell_visible))
            self._cell = self._cell_visible = None
        elif tag == "details":
            self._details -= 1
        elif tag == "a" and self._href is not None:
            self.anchors.append((self._href, _squash(self._anchor_text), self._details > 0))
            self._href = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
            if self._details == 0:
                self._cell_visible.append(data)
        if self._href is not None:
            self._anchor_text.append(data)


class _Row:
    def __init__(self, parser: _RowParser, headers: list[str]):
        self.visible = parser.visible
        self.anchors = parser.anchors
        self.headers = headers

    @property
    def visible_text(self) -> str:
        return " ".join(self.visible)

    def cell(self, header: str) -> str:
        return self.visible[self.headers.index(header.lower())]

    def link(self, path_pattern: str) -> tuple[str, str]:
        """(href, text) of the first visible anchor whose path matches the regex; ("", "") when there is none."""
        for href, text, inside_details in self.anchors:
            if not inside_details and re.fullmatch(path_pattern, urlsplit(href).path):
                return href, text
        return "", ""


def _sections(html: str) -> dict[str, list[_Row]]:
    """{heading name: body rows} for every result <section>; the heading's (N) must equal the number of rows ("sections render
    their counts as today"). A section with no rows is not rendered, so it is simply absent."""
    found: dict[str, list[_Row]] = {}
    for block in re.findall(r"<section>(.*?)</section>", html, re.S):
        heading = re.search(r"<h2>\s*([A-Za-z ]+?)\s*\((\d+)\)", block)
        if not heading:
            continue
        headers = [_squash([re.sub(r"<[^>]+>", " ", text)]).lower() for text in re.findall(r"<th[^>]*>(.*?)</th>", block, re.S)]
        rows = []
        for fragment in re.findall(r"<tr\b.*?</tr>", block, re.S | re.I):
            if "<td" not in fragment.lower():
                continue
            parser = _RowParser()
            parser.feed(fragment)
            parser.close()
            rows.append(_Row(parser, headers))
        name, count = heading.group(1), int(heading.group(2))
        assert count == len(rows), f"heading says {name} ({count}) but the table has {len(rows)} rows"
        found[name] = rows
    return found


Candidate = namedtuple("Candidate", "id name office total committees")
CommitteeRow = namedtuple("CommitteeRow", "id name via text")
DonorRow = namedtuple("DonorRow", "name total")


def _money(text: str):
    found = re.search(r"\$([\d,]+\.\d{2})", text)
    return float(found.group(1).replace(",", "")) if found else None


def _local_candidates(sections) -> list[Candidate]:
    found = []
    for row in sections.get("Candidates", []):
        if row.cell("Scope") != "local":
            continue
        href, text = row.link(r"/candidates/\d+")
        committees = re.search(r"(\d+) committee", row.cell("Totals"))
        found.append(
            Candidate(
                int(urlsplit(href).path.rsplit("/", 1)[1]) if href else None,
                text,
                row.cell("Office"),
                _money(row.cell("Totals")),
                int(committees.group(1)) if committees else None,
            )
        )
    return found


def _federal_ids(sections) -> list[str]:
    found = []
    for row in sections.get("Candidates", []):
        if row.cell("Scope") != "federal":
            continue
        href, _text = row.link(r"/federal-finance/[^/]+")
        found.append(urlsplit(href).path.rsplit("/", 1)[1] if href else None)
    return found


def _committees(sections) -> list[CommitteeRow]:
    found = []
    for row in sections.get("Committees", []):
        href, text = row.link(r"/committees/\d+")
        found.append(
            CommitteeRow(
                int(urlsplit(href).path.rsplit("/", 1)[1]) if href else None,
                text,
                "via candidate" in row.visible_text.lower(),
                row.visible_text,
            )
        )
    return found


def _donors(sections) -> list[DonorRow]:
    found = []
    for row in sections.get("Donors", []):
        _href, text = row.link(r"/donors/.+")
        found.append(DonorRow(text, _money(row.cell("Total"))))
    return found


def _page(client, query: str, **params) -> str:
    response = client.get("/search", query_string={"q": query, **params})
    assert response.status_code == 200, (query, response.status_code)
    html = response.get_data(as_text=True)
    banner = re.search(r"Some sections [^<]*", html)
    assert banner is None, f"{query!r}: {banner.group(0)}"
    return html


def _view(client, query: str, **params) -> dict[str, list[_Row]]:
    return _sections(_page(client, query, **params))


# Local candidates the page must show for the people named Michael / Mike Exampleton (see the seed comments):
#   id: (name, office, total receipts, committee count)
PEOPLE = {
    101: ("Michael W Exampleton", "Treasurer", 1750.0, 2),
    102: ("Michael Exampleton", "State Senator", 500.0, 1),
    103: ("Mike Exampleton", "State Representative", 0.0, 0),
    104: ("Brad Exampleton", "County Clerk", 90.0, 1),
}


def _assert_people(found: list[Candidate], ids: list[int]) -> None:
    assert [candidate.id for candidate in found] == ids
    for candidate in found:
        name, office, total, committees = PEOPLE[candidate.id]
        assert candidate.name == name, candidate
        assert candidate.office.startswith(office), candidate
        assert candidate.total == total, candidate
        assert candidate.committees == committees, candidate


# ---------------------------------------------------------------------------
# (3) /search: one person, many spellings
# ---------------------------------------------------------------------------

PLAIN_SPELLINGS = [
    "Mike Exampleton",
    "Michael Exampleton",
    "Exampleton Mike",
    "Exampleton, Michael",
    "  mike   EXAMPLETON  ",
    "Mike Exampleton Jr",
    "Mike Exam",
]


def test_every_spelling_finds_the_same_three_people_including_the_one_without_a_committee(world_client):
    """102 Michael Exampleton, 101 Michael W Exampleton and 103 Mike Exampleton (no committee link: totals 0) for every
    nickname / order / case / suffix / typed-prefix variant; never 104 Brad. Totals, office and committee count are the old
    query's. Kills: the verbatim-substring predicate (finds one of the three); nickname-less matching; matview-only search (103
    missing); a union without de-duplication (101 has three matview rows plus its isbe_candidates row); a union that keeps
    isbe_candidates' zero totals for linked people; COUNT(*) instead of COUNT(DISTINCT committee); surname-only matching (Brad);
    a suffix kept as a required token ("Jr"); a prefix that only works on the first token."""

    def check(query):
        sections = _view(world_client, query)
        _assert_people(_local_candidates(sections), [101, 102, 103])
        assert _federal_ids(sections) == ["H4IL99001"]

    _each(PLAIN_SPELLINGS, check)


def test_every_spelling_lists_name_matches_first_then_the_committees_reached_through_the_candidates(world_client):
    """Name match: 202 'Mike Exampleton for County Board' (unlinked), first, unmarked (R3). Through the matched candidates and
    NOT by name: 201 (101), 203 (joint 101 / 102) and 205 (accepted family of 201), marked "via candidate" with a candidate's
    name. Not listed: 204 (Brad's), 206, 207 (pending family), 208 (rejected family), 209. Kills: name-only committees; no
    family expansion or one that accepts pending / rejected families; marking every row; marking none; naming the wrong
    candidate; surname-wide expansion (204); duplicate rows (203 is reachable twice)."""

    def check(query):
        committees = _committees(_view(world_client, query))
        ids = [committee.id for committee in committees]
        assert len(ids) == len(set(ids)), f"a committee is listed twice: {ids}"
        assert set(ids) == {201, 202, 203, 205}
        assert ids[0] == 202, "name matches are listed before the committees found through a candidate"
        by_id = {committee.id: committee for committee in committees}
        assert by_id[202].via is False, by_id[202].text
        for committee_id in (201, 203, 205):
            assert by_id[committee_id].via is True, f"found only through a candidate, must say so: {by_id[committee_id].text}"
        assert "Michael W Exampleton" in by_id[201].text
        assert "Michael W Exampleton" in by_id[205].text
        assert "Michael W Exampleton" in by_id[203].text or "Michael Exampleton" in by_id[203].text

    _each(PLAIN_SPELLINGS, check)


def test_every_spelling_finds_the_two_donors_named_michael_and_mike_largest_first(world_client):
    """Michael Exampleton (300) and Mike Exampleton (200), in the old total-descending order (R6). Not M. Exampleton-Ruiz (the
    initial M is not Michael), not Thomas, not the substring decoy. Kills: verbatim substring; AND relaxed to OR; a prefix test
    that lets 'M' match 'michael'; a lost ORDER BY."""

    def check(query):
        donors = _donors(_view(world_client, query))
        assert [donor.name for donor in donors] == ["Michael Exampleton", "Mike Exampleton"]
        assert [donor.total for donor in donors] == [300.0, 200.0]

    _each(PLAIN_SPELLINGS, check)


def test_a_second_nickname_pair_works_on_the_route(world_client):
    """'Tom Exampleton' and 'Thomas Exampleton' both find the donor 'Thomas Exampleton' and nothing else (no candidate or
    committee is named Thomas / Tom). Kills a map special-cased to the MIKE / MICHAEL pair of the examples."""

    def check(query):
        sections = _view(world_client, query)
        assert [donor.name for donor in _donors(sections)] == ["Thomas Exampleton"]
        assert set(sections) == {"Donors"}

    _each(["Tom Exampleton", "Thomas Exampleton"], check)


INITIAL_SPELLINGS = ["Exampleton, Michael W.", "michael w exampleton", "Mike W. Exampleton"]


def test_a_middle_initial_is_optional_in_candidates_and_donors(world_client):
    """R1 -- the orchestrator's decision: a single-letter initial in the query is dropped while a multi-letter token remains, so the
    spellings WITH the W find exactly what the plain spellings find: 101, 102 and 103 (the stored name's own W is extra and never
    required), the federal 'EXAMPLETON, MICHAEL W', and the donors Michael (300) and Mike (200). Kills: the initial kept as a
    required token (only 101 and the federal record would match, the donors none); an OR; surname-only matching (Brad)."""

    def check(query):
        sections = _view(world_client, query)
        _assert_people(_local_candidates(sections), [101, 102, 103])
        assert _federal_ids(sections) == ["H4IL99001"]
        assert [donor.name for donor in _donors(sections)] == ["Michael Exampleton", "Mike Exampleton"]

    _each(INITIAL_SPELLINGS, check)


def test_a_middle_initial_is_optional_in_committees(world_client):
    """R1 continued: the matched candidates are 101, 102 and 103, so the committees are the plain spellings' -- 202 by name, first and
    unmarked, then 201, 203 and the family member 205 marked "via candidate" with a candidate's name; not 204 (Brad's). Kills: the
    initial narrowing the candidates to 101 (202 would vanish, the joint committee 203 would lose 102); surname-wide expansion;
    unmarked via rows."""

    def check(query):
        committees = _committees(_view(world_client, query))
        ids = [committee.id for committee in committees]
        assert len(ids) == len(set(ids)), f"a committee is listed twice: {ids}"
        assert set(ids) == {201, 202, 203, 205}
        assert ids[0] == 202, "name matches are listed before the committees found through a candidate"
        by_id = {committee.id: committee for committee in committees}
        assert by_id[202].via is False, by_id[202].text
        for committee_id in (201, 203, 205):
            assert by_id[committee_id].via is True, by_id[committee_id].text
        assert "Michael W Exampleton" in by_id[201].text
        assert "Michael W Exampleton" in by_id[205].text

    _each(INITIAL_SPELLINGS, check)


def test_the_bare_surname_finds_everyone_and_lists_only_candidate_only_committees_as_via(world_client):
    """'Exampleton' alone: all four people (largest total first, the unlinked 103 last) and both federal namesakes; the four
    committees whose NAME has the token, unmarked and first (201 and 203 are also linked to matched candidates: R3), then 204
    (Prairie State Leadership Fund, Brad's, no token in its name) marked via Brad; the four donors largest first. Not listed: the
    decoys 'Nonexampleton Holdings PAC' / 'Nonexampleton Group' (the token path is a token-start prefix, the fallback does not
    run because it found rows: R2), 206, 207, 208. Kills: always-OR fallback; family expansion of pending / rejected families;
    marking duplicates; an unmarked via row."""
    sections = _view(world_client, "Exampleton")
    _assert_people(_local_candidates(sections), [101, 102, 104, 103])
    assert sorted(_federal_ids(sections)) == ["H4IL99001", "H4IL99002"]

    committees = _committees(sections)
    ids = [committee.id for committee in committees]
    assert len(ids) == len(set(ids)), f"a committee is listed twice: {ids}"
    assert set(ids) == {201, 202, 203, 204, 205}
    assert set(ids[:4]) == {201, 202, 203, 205}, "the four name matches come first"
    assert ids[4] == 204
    by_id = {committee.id: committee for committee in committees}
    for committee_id in (201, 202, 203, 205):
        assert by_id[committee_id].via is False, by_id[committee_id].text
    assert by_id[204].via is True
    assert "Brad Exampleton" in by_id[204].text

    donors = _donors(sections)
    assert [donor.name for donor in donors] == ["M. Exampleton-Ruiz", "Michael Exampleton", "Mike Exampleton", "Thomas Exampleton"]
    assert [donor.total for donor in donors] == [400.0, 300.0, 200.0, 100.0]


def test_a_hyphenated_surname_is_two_tokens_in_either_spelling_and_order(world_client):
    """'M. Exampleton-Ruiz' is found by the hyphenated, the spaced and the reversed spelling, and nothing else is listed (the
    fallback must not add rows when the token path found one). The hyphenated spelling already works on HEAD (substring); the
    other two do not. Kills: hyphen kept inside a token; an exact-substring predicate; a fallback that always runs."""

    def check(query):
        sections = _view(world_client, query)
        assert [donor.name for donor in _donors(sections)] == ["M. Exampleton-Ruiz"]
        assert set(sections) == {"Donors"}

    _each(["Exampleton-Ruiz", "Exampleton Ruiz", "Ruiz Exampleton"], check)


def test_organisation_queries_match_on_tokens_in_any_order(world_client):
    """'Friends of' finds the two 'Friends of ...' committees (an organisation query keeps working: 'of' is a token, not a stop
    word), 'Exampleton Friends' finds only the one that also has Exampleton, in reversed order. No candidate matches either, so
    no committee is added through a candidate. Kills: stop-word removal; an exact-substring predicate (the reversed query)."""
    friends = _view(world_client, "Friends of")
    assert sorted(committee.id for committee in _committees(friends)) == [201, 206]
    assert all(committee.via is False for committee in _committees(friends))
    assert set(friends) == {"Committees"}

    reversed_query = _view(world_client, "Exampleton Friends")
    assert [committee.id for committee in _committees(reversed_query)] == [201]
    assert set(reversed_query) == {"Committees"}


def test_a_mid_word_fragment_of_four_or_more_characters_falls_back_to_substring(world_client):
    """No token starts with 'xamplet' / 'mple', so the token path is empty in every section and ONE substring fallback runs:
    all four people (totals intact), both federal namesakes, the four Exampleton committees plus the decoy 209 -- unmarked, they
    are name matches -- and the five donors including the decoy, largest first. 204 (Brad's, no match in its name) may or may
    not be added through the fallback-found candidates: not pinned. 206, 207, 208 never appear. 'mple' is exactly four
    characters: the boundary. Kills: no fallback; a fallback that drops totals; a fallback only on one of the sections."""

    def check(query):
        sections = _view(world_client, query)
        _assert_people(_local_candidates(sections), [101, 102, 104, 103])
        assert sorted(_federal_ids(sections)) == ["H4IL99001", "H4IL99002"]

        committees = _committees(sections)
        ids = [committee.id for committee in committees]
        assert len(ids) == len(set(ids)), f"a committee is listed twice: {ids}"
        assert {201, 202, 203, 205, 209} <= set(ids)
        assert set(ids) <= {201, 202, 203, 204, 205, 209}
        for committee in committees:
            if committee.id != 204:
                assert committee.via is False, committee

        donors = _donors(sections)
        assert [donor.name for donor in donors] == [
            "M. Exampleton-Ruiz", "Michael Exampleton", "Mike Exampleton", "Thomas Exampleton", "Nonexampleton Group",
        ]

    _each(["xamplet", "mple"], check)


def test_a_three_character_fragment_does_not_fall_back(world_client):
    """'mpl' is inside 'Exampleton' but starts no token and is below the 4-character floor: no section lists anything and the
    page says so. Kills: a fallback without the length floor (every 3-letter fragment would scan every table)."""
    html = _page(world_client, "mpl")
    assert _sections(html) == {}
    assert "No results found" in html


def test_a_query_without_a_single_letter_or_digit_lists_nothing(world_client):
    """(guard) Passes on HEAD. Normalizes to no token: tsquery None. Kills a fallback built on the EMPTY normalized string
    (`LIKE '%%'` lists the first rows of every table) and a None tsquery handed to to_tsquery (an errored section)."""

    def check(query):
        html = _page(world_client, query)
        assert _sections(html) == {}
        assert "No results found" in html

    _each(["!!!!", "----", "(( ))", "??"], check)


SYNTAX_SPELLINGS = [
    "Mike | Exampleton",
    "Mike & Exampleton",
    "(Mike) Exampleton",
    "!Mike Exampleton",
    "Mike:* Exampleton",
    "Mike? Exampleton",
    "mike' exampleton",
    "Mike_Exampleton",
]


def test_tsquery_syntax_in_the_query_text_is_only_punctuation(world_client):
    """`|`, `&`, `(`, `!`, `:*`, `?`, `'`, `_` are separators, never operators: each spelling answers exactly like 'Mike
    Exampleton' and no section fails. Kills: user text handed to to_tsquery unescaped (a syntax error -> 'Some sections failed',
    or worse an operator the user did not type) and a tsquery built from the raw lower-cased string."""

    def check(query):
        _assert_people(_local_candidates(_view(world_client, query)), [101, 102, 103])

    _each(SYNTAX_SPELLINGS, check)


SHAPE_CASES = [
    ("O'Neil Smythe", {301}),
    ("Neil Smythe", {301}),
    ("Smythe", {301}),
    ("O'Neil-Smythe", {301}),
    ("John Quillberis", {302, 303}),
    ("John Quillberis Jr", {302, 303}),
    ("Quillberis, John", {302, 303}),
    ("O'Neil", {301}),                     # R7: the O'Neil, not the plain Neil (304)
    ("Neil", {301, 304}),                  # R7: "Neil" still finds the O'Neils too
]


def test_apostrophe_hyphen_and_suffix_shapes_find_candidates_without_a_committee(world_client):
    """301 'Sean O'Neil-Smythe' is found through either half of the hyphenated name and through the apostrophe; 302 'John
    Quillberis, Jr' (the suffix lives in last_name) and 303 'John Quillberis' (no suffix) are BOTH found by 'John Quillberis Jr' because
    the query-side suffix is dropped. None of them has a committee: totals 0. Kills: a suffix kept as a required token (303
    missing); apostrophe / hyphen kept in tokens; 'Last, First' not matching."""

    def check(case):
        query, ids = case
        candidates = _local_candidates(_view(world_client, query))
        assert sorted(candidate.id for candidate in candidates) == sorted(ids)
        for candidate in candidates:
            assert candidate.total == 0.0, candidate
            assert candidate.committees == 0, candidate

    _each(SHAPE_CASES, check)


def test_a_numeric_query_still_reaches_a_candidate_without_a_committee_by_id(world_client):
    """The id path of today (`CAST(candidate_id AS TEXT) LIKE`) must also cover candidates that exist only in isbe_candidates:
    '103' finds 103 Mike Exampleton (unlinked). Kills: the id path kept on the finance aggregate only."""
    candidates = _local_candidates(_view(world_client, "103", type="candidates"))
    assert [candidate.id for candidate in candidates] == [103]


def test_a_federal_id_query_still_finds_the_federal_candidate(world_client):
    """(guard) Passes on HEAD. `fec_candidate_id LIKE` is one of today's id paths and must survive the token predicate."""
    sections = _view(world_client, "H4IL99001", type="candidates")
    assert _federal_ids(sections) == ["H4IL99001"]
    assert _local_candidates(sections) == []


def test_the_committees_section_does_not_depend_on_the_type_filter(world_client):
    """R4: the dropdown narrows WHAT is listed, not what a section contains. type=committees and type=candidates answer with the
    same rows (and the same via marks) as the matching sections of type=all. Kills: committees-through-a-candidate computed only
    from the candidates section of an all-sections request."""
    everything = _view(world_client, "Mike Exampleton")
    only_committees = _view(world_client, "Mike Exampleton", type="committees")
    only_candidates = _view(world_client, "Mike Exampleton", type="candidates")
    reached = {committee.id for committee in _committees(only_committees)}
    assert {201, 203, 205} <= reached, "type=committees must list the committees reached through the candidates too"
    assert sorted((c.id, c.via) for c in _committees(only_committees)) == sorted((c.id, c.via) for c in _committees(everything))
    assert [c.id for c in _local_candidates(only_candidates)] == [101, 102, 103]


# ---------------------------------------------------------------------------
# The search_results cache
# ---------------------------------------------------------------------------

class FakeRedis:
    """The slice of redis-py that RouteCache uses (same shape as tests/test_search_perf.py)."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def ping(self):
        return True

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    def delete(self, *keys):
        removed = 0
        for key in keys:
            self.ttls.pop(key, None)
            if self.values.pop(key, None) is not None:
                removed += 1
        return removed

    def scan(self, cursor=0, match=None, count=None):
        prefix = (match or "*").rstrip("*")
        return 0, [key for key in self.values if key.startswith(prefix)]


SEARCH_KEY_PREFIX = "ilcf:cache:search_results:"


@pytest.fixture(autouse=True)
def _isolated_search_cache(monkeypatch):
    """Never touch a developer's Redis, and start / end every test with an empty module-level search cache."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    main_routes._search_results_cache.invalidate()
    yield
    main_routes._search_results_cache.invalidate()


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: fake)
    return fake


@pytest.fixture
def cached_client(tmp_path, fake_redis):
    return create_app(_app_config(_build_world(tmp_path), ROUTE_PERF_CACHE_ENABLED=True)).test_client()


def _stored_entries(fake: FakeRedis) -> list[str]:
    return [key for key in fake.values if key.startswith(SEARCH_KEY_PREFIX)]


def _key(query: str, scope: str = "cycle:2026", search_type: str = "all") -> str:
    return main_routes._search_results_cache_key(scope, query, search_type)


def test_the_search_cache_key_starts_with_the_revision_sn2():
    """S4: the revision prefix makes every entry stored before the fix unreachable (the old key had no prefix). The revision is
    pinned by value; it must also survive a query with no token ('!!!!') without raising. Kills: no prefix; a different
    revision string; the prefix put in the middle."""

    def check(case):
        query, search_type = case
        key = _key(query, search_type=search_type)
        assert isinstance(key, str)
        assert key.startswith("sn2"), key

    _each(
        [(query, search_type) for query in ("Mike Exampleton", "Exampleton, Michael W.", "!!!!", "103") for search_type in ("all", "donors")],
        check,
    )


def test_apostrophe_spellings_get_their_own_cache_keys():
    """R7 makes "O'Neil", "O Neil" and "ONeil" three different searches, so three cache entries; the curly apostrophe is the
    same search as the straight one. Kills a key still built from normalize_name (O'Neil and O Neil would share an entry and
    serve each other's results)."""
    keys = [_key("O'Neil"), _key("O Neil"), _key("ONeil")]
    assert len(set(keys)) == 3, keys
    assert _key("O'Neil") == _key("O’Neil")


def test_spellings_with_one_normalised_form_share_a_cache_key():
    """Two spellings that normalise to the same tsquery are one search: they share one cache entry (the old key lower-cased the
    raw text only, so 'mike  exampleton' / 'mike-exampleton' were three entries). Kills: a prefix glued onto the old raw key."""

    def check(case):
        first, second = case
        assert _key(first) == _key(second)

    _each(
        [
            ("Mike Exampleton", "mike  exampleton"),
            ("Mike Exampleton", "Mike-Exampleton"),
            ("Mike Exampleton", "MIKE, EXAMPLETON."),
            ("Exampleton Mike", "exampleton, mike"),
        ],
        check,
    )


def test_different_queries_scopes_and_types_do_not_share_a_cache_key():
    """(guard) Passes on HEAD. Kills: a constant key; a key built from the revision alone; a key that forgets the resolved scope
    (metric cross-talk between windows) or the type (a donors-only payload served to type=all)."""
    base = _key("Mike Exampleton")
    assert _key("Brad Exampleton") != base
    assert _key("Mike Exampleto") != base
    assert _key("Mike Exampleton Ruiz") != base
    assert _key("Mike Exampleton", scope="all") != base
    assert _key("Mike Exampleton", search_type="donors") != base


def test_a_key_like_query_keeps_its_raw_text_in_the_cache_key():
    """(guard) Passes on HEAD. R5. `mike|exampleton`, `Local-150` and `Local 150` normalise to the same tokens as their plain
    spellings, but a query with `|` / `:` or >= 3 digits also feeds the donor-key section (and a digits-only one the filed-doc
    section) with its RAW text, where the spellings answer differently. A key built from the normalised form alone would serve
    one spelling the other's page."""
    assert _key("mike|exampleton") != _key("Mike Exampleton")
    assert _key("mike:exampleton") != _key("Mike Exampleton")
    assert _key("Local-150") != _key("Local 150")


def test_a_donor_key_query_is_not_served_its_plain_spellings_cached_page(cached_client):
    """(guard) Passes on HEAD (the old key keeps the raw text). Route level, cache on: 'mike|exampleton' is key-like, so type=all
    also runs the donor-key section and lists the donor whose key starts with it; 'Mike Exampleton' is not and lists none. Both
    orders, each pair on an emptied cache: the second request of a pair is the one that could be mis-served. Kills: a cache key
    derived from the normalised form only (the second request would get the first one's page)."""

    def check(pair):
        main_routes._search_results_cache.invalidate()
        for query in pair:
            sections = _view(cached_client, query)
            has_donor_keys = "Donor Keys" in sections
            assert has_donor_keys is ("|" in query), f"{query!r}: donor-key section present={has_donor_keys}"

    _each([("Mike Exampleton", "mike|exampleton"), ("mike|exampleton", "Mike Exampleton")], check)


def test_one_stored_entry_per_normalised_search_and_a_new_entry_for_every_other(cached_client, fake_redis):
    """Route level: the key function is really what the route stores under. Three spellings of one search leave ONE entry; a
    different person and a different type each add one. Kills: the new key function defined but not used by the route."""
    _page(cached_client, "Mike Exampleton")
    assert len(_stored_entries(fake_redis)) == 1
    _page(cached_client, "mike-exampleton")
    assert len(_stored_entries(fake_redis)) == 1
    _page(cached_client, "MIKE, EXAMPLETON.")
    assert len(_stored_entries(fake_redis)) == 1
    _page(cached_client, "Brad Exampleton")
    assert len(_stored_entries(fake_redis)) == 2
    _page(cached_client, "Mike Exampleton", type="donors")
    assert len(_stored_entries(fake_redis)) == 3


def test_a_cached_page_renders_like_the_computed_one_via_marks_included(cached_client, fake_redis):
    """The payload that goes through the shared cache (JSON) must carry the new fields: the second page equals the first row for
    row -- candidates, committees with their 'via candidate' marks, donors -- and a donor added after the first request is NOT on
    it (so it really came from the cache). Kills: a payload that drops the via mark, holds a set / tuple that does not survive
    JSON, or is stored but never read."""
    first = _view(cached_client, "Mike Exampleton")
    assert any(committee.via for committee in _committees(first)), "control: the computed page marks 'via candidate'"
    conn = get_db()
    conn.execute(
        "INSERT INTO analytics_donor_summary (source, donor_key, donor_name, total_amount, contribution_count, committee_count) "
        "VALUES ('bulk_receipts', 'mike|exampleton|latecomer', 'Mike Exampleton Latecomer', 10.0, 1, 1)"
    )
    conn.commit()
    conn.close()

    second = _view(cached_client, "Mike Exampleton")

    assert "Mike Exampleton Latecomer" not in [donor.name for donor in _donors(second)], "recomputed: the cache was not used"
    assert _local_candidates(second) == _local_candidates(first)
    assert _committees(second) == _committees(first)
    assert _donors(second) == _donors(first)


# ---------------------------------------------------------------------------
# The indexes must be usable by the SQL /search really runs
# ---------------------------------------------------------------------------

@pytest.fixture
def sql_log(monkeypatch):
    """(sql, params) of every statement the compat layer runs while the test runs."""
    log: list[tuple] = []
    original = PostgresCompatCursor.execute

    def recording(self, sql, params=None):
        log.append((sql, params))
        return original(self, sql, params)

    monkeypatch.setattr(PostgresCompatCursor, "execute", recording)
    return log


USABLE = [spec for spec in INDEXES if spec[1] != "fec_candidate_match"]


@pytest.mark.parametrize("name, table, source, _columns, _table_sql", USABLE, ids=[spec[0] for spec in USABLE])
def test_the_search_sql_on_the_table_is_served_by_the_contract_index(tmp_path, sql_log, name, table, source, _columns, _table_sql):
    """The pattern tests prove the DDL text; this proves it is the index the route's predicate can use. The index is created from
    the repo's own statement (schema.sql runs in init_db; the ETL / swap statement is cut out of the file and executed on the
    seed table), /search is requested, and every recorded statement that applies `@@` to the table is EXPLAINed with sequential
    scans disabled: at least one must name the index. Kills: a predicate spelled differently from the indexed expression
    (`to_tsvector('simple', name)`, `lower(...)`, a missing coalesce); the token predicate OR-ed with a non-indexable one (the
    fallback or the id path in the same WHERE), which forces a full scan of 112 K matview rows / 1.1 M donors per search.
    fec_candidate_match is left out: its predicate may legitimately OR in fec_name / fec_candidate_id, and it holds ~130 rows."""
    db_path = str(tmp_path / "search_plan.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_world(conn)
    if source == SCHEMA_SQL:
        built = conn.execute(
            "SELECT 1 AS present FROM pg_indexes WHERE schemaname = current_schema() AND indexname = ?", (name,)
        ).fetchone()
        assert built is not None, f"{name} is not created by schema.sql"
    else:
        statement = _index_statement(_source_sql(source), name)
        assert statement is not None, f"no `CREATE INDEX {name} ...;` in {source.relative_to(REPO_ROOT)}"
        conn.execute(statement)
    conn.commit()
    conn.close()

    client = create_app(_app_config(db_path)).test_client()
    _page(client, "Mike Exampleton")
    statements = [
        (sql, params)
        for sql, params in list(sql_log)
        if "@@" in sql and re.search(rf"\b{table}\b", sql, re.I)
    ]
    assert statements, f"/search ran no statement that applies @@ to {table}"

    conn = get_db()
    conn.execute("SET enable_seqscan = off")
    plans = []
    for sql, params in statements:
        rows = conn.execute("EXPLAIN " + sql, params if params is not None else ()).fetchall()
        plans.append(" ".join(str(value) for row in rows for value in row.values()))
    conn.rollback()
    conn.close()
    assert any(name in plan for plan in plans), f"no plan uses {name}:\n" + "\n---\n".join(plans)
