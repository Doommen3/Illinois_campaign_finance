"""Shared name normalizer for search (Search program, Phase 1, decision S1).

One rule for how a typed name and a stored name are cut into tokens, so /search matches them in any order, with any
punctuation, across nicknames ("Mike" finds "Michael") and as search-as-you-type prefixes. The query is cut here, in Python;
the stored name is cut in SQL by NAME_TSVECTOR_SQL, which is also the text of the GIN expression indexes -- the two sides must
agree on what a separator is (tests/test_search_names.py compares them on every ASCII punctuation shape).

Contract: .claude/plans/outside-spending-drilldown.md, "Search program" -> "Contract -- database/name_normalize.py and the
/search matching".
"""
from __future__ import annotations

import re

_NOT_ALNUM = re.compile(r"[^A-Z0-9 ]")
# A word-internal apostrophe, straight or curly, with TWO or more letters after it: the one punctuation mark the QUERY side
# reads (search Phase 2, P2-23 / P2-M7). One letter after it is a possessive or contraction ("Women's", "I'm"): a separator.
_INNER_APOSTROPHE = re.compile(r"(?<=[A-Za-z])['’](?=[A-Za-z]{2,})")

# Generational suffixes. A query keeps them only when dropping them would leave a single token ("Pat Jr").
_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV", "V"})


def _upper(text: str) -> str:
    """Upper-case one character at a time, as PostgreSQL's upper() does. str.upper() also expands 'ß' to 'SS' and ligatures to
    letter pairs, which would leave letters on the Python side that the SQL side turns into separators."""
    chars = []
    for char in text:
        upper = char.upper()
        chars.append(upper if len(upper) == 1 else char)
    return "".join(chars)


def normalize_name(text: str | None) -> str:
    """Upper-case; every character outside A-Z, 0-9 and space becomes a space (so '-', '.', ',', "'", '&', '/' and every
    non-ASCII letter split tokens); whitespace collapsed and trimmed. None or blank gives ''. No reordering."""
    if not text:
        return ""
    return " ".join(_NOT_ALNUM.sub(" ", _upper(text)).split())


def normalize_query(text: str | None) -> str:
    """normalize_name, except a word-internal apostrophe (' or ’, a letter before it and two or more after it) survives as
    "'" -- the one mark the query side reads: "O'Neil-Smythe" -> "O'NEIL SMYTHE", "Smith O’Neil" -> "SMITH O'NEIL". A
    possessive or contraction (one letter after: "Women's", "McDonald's", "I'm") is a separator, as on the stored side, so
    it keeps the plain reading ("WOMEN S"; name_tokens then drops the lone S). The stored side stays on normalize_name /
    NAME_TSVECTOR_SQL (apostrophe = separator); search_tsquery turns a kept apostrophe into a compound group."""
    return "'".join(normalize_name(piece) for piece in _INNER_APOSTROPHE.split(text or ""))


def name_tokens(text: str | None) -> list[str]:
    """The normalized tokens of a QUERY. Two kinds of token are noise in a query and are dropped so they cannot hide the
    records filed without them: a generational suffix (unless that would leave a single token: "Pat Jr" keeps JR) and a
    single-letter initial (while a multi-letter token remains). Only query tokens are ever required, so the stored name's
    own extra tokens never matter."""
    tokens = normalize_query(text).split()
    without_suffix = [token for token in tokens if token not in _SUFFIXES]
    if len(without_suffix) > 1:
        tokens = without_suffix
    if any(len(token) > 1 for token in tokens):
        tokens = [token for token in tokens if len(token) > 1 or not token.isalpha()]
    return tokens


# nickname -> the full names it stands for; every pair is made symmetric below.
_NICKNAME_TO_FULL: dict[str, tuple[str, ...]] = {
    "MIKE": ("MICHAEL",),
    "TOM": ("THOMAS",),
    "BOB": ("ROBERT",),
    "ROB": ("ROBERT",),
    "BILL": ("WILLIAM",),
    "WILL": ("WILLIAM",),
    "JIM": ("JAMES",),
    "DAN": ("DANIEL",),
    "SAM": ("SAMUEL", "SAMANTHA"),
    "CHUY": ("JESUS",),
    "JB": ("JAY ROBERT",),
    "PAT": ("PATRICK", "PATRICIA"),
    "TONY": ("ANTHONY",),
    "CHRIS": ("CHRISTOPHER", "CHRISTINE"),
    "LIZ": ("ELIZABETH",),
    "BETH": ("ELIZABETH",),
    "KATE": ("KATHERINE", "KATHLEEN"),
    "KATIE": ("KATHERINE", "KATHLEEN"),
    "JOE": ("JOSEPH",),
    "DAVE": ("DAVID",),
    "STEVE": ("STEVEN", "STEPHEN"),
    "RICK": ("RICHARD",),
    "DICK": ("RICHARD",),
    "ED": ("EDWARD",),
    "TED": ("THEODORE", "EDWARD"),
    "JEFF": ("JEFFREY",),
    "MATT": ("MATTHEW",),
    "NICK": ("NICHOLAS",),
    "ALEX": ("ALEXANDER", "ALEXANDRA"),
    "JEN": ("JENNIFER",),
    "JENNY": ("JENNIFER",),
    "SUE": ("SUSAN",),
    "PEG": ("MARGARET",),
    "PEGGY": ("MARGARET",),
}


def _symmetric(pairs: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    both: dict[str, list[str]] = {}
    for nickname, full_names in pairs.items():
        for full_name in full_names:
            both.setdefault(nickname, []).append(full_name)
            both.setdefault(full_name, []).append(nickname)
    return {name: tuple(aliases) for name, aliases in both.items()}


# First-token aliases, both ways. Direct aliases only (ED -> EDWARD, but not ED -> TED): expand_token does not chain. An alias
# of two words ("JB" <-> "JAY ROBERT") becomes a conjunction in the tsquery; its reverse entry exists for symmetry only, because
# expand_token works one query token at a time.
NICKNAMES: dict[str, tuple[str, ...]] = _symmetric(_NICKNAME_TO_FULL)


def expand_token(token: str) -> set[str]:
    """The token plus its direct aliases."""
    return {token, *NICKNAMES.get(token, ())}


def _lexeme(alias: str) -> str:
    parts = [f"{part.lower()}:*" for part in alias.split()]
    return parts[0] if len(parts) == 1 else "(" + " & ".join(parts) + ")"


def search_tsquery(text: str | None) -> str | None:
    """A to_tsquery('simple', ...) string for a typed name: one group per query token, ANDed, each group the token's prefix
    lexeme and its aliases ORed -- "Frerichs, Mike W." gives "frerichs:* & (mike:* | michael:*)". A compound token (a
    word-internal apostrophe, normalize_query) asks for the joined word or its halves ADJACENT and in order, a one-letter half
    as an exact lexeme, with no aliases: "O'Neil" gives "(oneil:* | (o <-> neil:*))", matching a stored "O'Neil" (cut into O +
    NEIL) or "ONeil", not every Neil and not "Law Office of Neil". A possessive ("Women's") is no compound: "women:*". None when no
    token remains. Lexemes are [a-z0-9] only (the apostrophe never reaches the tsquery), so nothing the user typed can act
    as a tsquery operator."""
    groups = []
    for token in name_tokens(text):
        if "'" in token:
            joined = _lexeme(token.replace("'", ""))
            halves = " <-> ".join(part.lower() if len(part) == 1 else f"{part.lower()}:*" for part in token.split("'"))
            groups.append(f"({joined} | ({halves}))")
            continue
        options = [_lexeme(token)] + [_lexeme(alias) for alias in sorted(expand_token(token) - {token})]
        groups.append(options[0] if len(options) == 1 else "(" + " | ".join(options) + ")")
    return " & ".join(groups) or None


def NAME_TSVECTOR_SQL(expr: str) -> str:  # noqa: N802 - the contract's name
    """The ONE server-side normalization, spelled identically in the predicates and in the GIN expression indexes so the planner
    can use them. It is the SQL twin of normalize_name (upper-case, everything outside A-Z, 0-9 and space becomes a space)."""
    return f"to_tsvector('simple', regexp_replace(upper(coalesce({expr}, '')), '[^A-Z0-9 ]', ' ', 'g'))"


# ---------------------------------------------------------------------------
# Search Phase 2 (decisions P2-1, P2-2, P2-8, P2-10): the predicates every other name search on the site uses. Each returns
# (sql, params) with the user's text only in params, or None when the rule does not apply to the query.
# ---------------------------------------------------------------------------

_FALLBACK_MIN_LENGTH = 4  # a shorter normalized fragment never runs the substring fallback (it would scan a table)


def name_match_sql(column_expr: str, query: str | None) -> tuple[str, list[str]] | None:
    """The token predicate on a stored name: NAME_TSVECTOR_SQL(column_expr) @@ to_tsquery('simple', ?), spelled as the GIN
    expression indexes are, with search_tsquery(query) bound. None when the query has no token."""
    tsquery = search_tsquery(query)
    if tsquery is None:
        return None
    return f"{NAME_TSVECTOR_SQL(column_expr)} @@ to_tsquery('simple', ?)", [tsquery]


def substring_pattern(query: str | None) -> str | None:
    """The ONE LIKE pattern of every substring branch (follow-up FB-M1): the query's name_tokens joined by '%' and wrapped
    '%...%', a compound's apostrophe as '_' (LIKE's one-character wildcard: O'NEIL, O NEIL and O-NEIL, not KHOT, NEIL) --
    "O'Neil Smythe" -> "%O_NEIL%SMYTHE%". The stored side is not normalized, so the tokens are joined by '%', never by a
    space. Only A-Z, 0-9 and the compound's '_' reach the pattern, so nothing typed acts as a wildcard. None when no token."""
    tokens = [token.replace("'", "_") for token in name_tokens(query)]
    return "%" + "%".join(tokens) + "%" if tokens else None


def substring_fallback_sql(column_expr: str, query: str | None) -> tuple[str, list[str]] | None:
    """The ONE fallback a caller runs when the token predicate found nothing (as /search): the bare column LIKE
    substring_pattern(query), for a normalized query of 4+ characters, else None. No UPPER(): the compat layer runs LIKE as
    ILIKE, and the trigram index on analytics_donor_summary.donor_name serves only the bare column."""
    if len(normalize_name(query)) < _FALLBACK_MIN_LENGTH:
        return None
    return f"{column_expr} LIKE ?", [substring_pattern(query)]


def normalized_substring_sql(column_expr: str, query: str | None) -> tuple[str, list[str]] | None:
    """The predicate on a transactional column (millions of rows, no name index; decision P2-2): UPPER(col) LIKE
    substring_pattern(query) -- the stored side is not normalized, so "Farrow, Michael" must still reach a stored
    "FARROW, MICHAEL". None when nothing alphanumeric was typed."""
    pattern = substring_pattern(query)
    if pattern is None:
        return None
    return f"UPPER({column_expr}) LIKE ?", [pattern]


def name_predicates(columns: str | tuple[str, ...], query: str | None) -> list[tuple[str, list[str]]]:
    """The WHERE variants a caller tries in order until one finds rows: the token predicate, then the substring fallback,
    each ORed over ``columns``; a variant is left out when its rule does not apply. Empty when the query has no token."""
    columns = (columns,) if isinstance(columns, str) else tuple(columns)
    variants = []
    for rule in (name_match_sql, substring_fallback_sql):
        parts = [rule(column, query) for column in columns]
        if parts[0] is None:  # whether a rule applies depends on the query only
            continue
        sql = " OR ".join(part[0] for part in parts)
        variants.append((sql if len(parts) == 1 else f"({sql})", [param for part in parts for param in part[1]]))
    return variants


def name_matches(query: str | None, label: str | None, *, substring: bool = False) -> bool:
    """Python twin of the two SQL rules, for names already in memory (the Viz Lab label search, P2-4). By default the token
    rule: every query token -- or one of its aliases, a two-word alias word by word -- prefixes a token of the normalized
    label. With ``substring``, the fallback rule: the normalized query (4+ characters) occurs in the normalized label."""
    if substring:
        needle = normalize_name(query)
        return len(needle) >= _FALLBACK_MIN_LENGTH and needle in normalize_name(label)
    label_tokens = normalize_name(label).split()
    query_tokens = name_tokens(query)
    if not query_tokens:
        return False

    def prefixes(word: str) -> bool:
        return any(token.startswith(word) for token in label_tokens)

    def half_matches(label_token: str, part: str) -> bool:
        return label_token == part if len(part) == 1 else label_token.startswith(part)

    def token_matches(token: str) -> bool:
        if "'" in token:  # a compound, as search_tsquery reads it: the joined word, or the halves on consecutive tokens
            parts = token.split("'")
            return prefixes(token.replace("'", "")) or any(
                all(half_matches(label_tokens[start + offset], part) for offset, part in enumerate(parts))
                for start in range(len(label_tokens) - len(parts) + 1)
            )
        return any(all(prefixes(word) for word in alias.split()) for alias in expand_token(token))

    return all(token_matches(token) for token in query_tokens)
