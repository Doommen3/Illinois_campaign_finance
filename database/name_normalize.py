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


def name_tokens(text: str | None) -> list[str]:
    """The normalized tokens of a QUERY. Two kinds of token are noise in a query and are dropped so they cannot hide the
    records filed without them: a generational suffix (unless that would leave a single token: "Pat Jr" keeps JR) and a
    single-letter initial (while a multi-letter token remains). Only query tokens are ever required, so the stored name's
    own extra tokens never matter."""
    tokens = normalize_name(text).split()
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
    lexeme and its aliases ORed -- "Frerichs, Mike W." gives "frerichs:* & (mike:* | michael:*)". None when no token remains.
    Tokens are [A-Z0-9] only, so nothing the user typed can act as a tsquery operator."""
    groups = []
    for token in name_tokens(text):
        options = [_lexeme(token)] + [_lexeme(alias) for alias in sorted(expand_token(token) - {token})]
        groups.append(options[0] if len(options) == 1 else "(" + " | ".join(options) + ")")
    return " & ".join(groups) or None


def NAME_TSVECTOR_SQL(expr: str) -> str:  # noqa: N802 - the contract's name
    """The ONE server-side normalization, spelled identically in the predicates and in the GIN expression indexes so the planner
    can use them. It is the SQL twin of normalize_name (upper-case, everything outside A-Z, 0-9 and space becomes a space)."""
    return f"to_tsvector('simple', regexp_replace(upper(coalesce({expr}, '')), '[^A-Z0-9 ]', ' ', 'g'))"
