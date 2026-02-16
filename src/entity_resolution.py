"""Reusable entity-resolution helpers for cross-dataset matching."""
from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple


_CORP_SUFFIXES = {
    "inc", "llc", "ltd", "co", "corp", "corporation", "company",
    "assoc", "association", "assn", "group", "pllc", "llp", "lp",
}
_NAME_STOP_WORDS = {
    "the", "of", "for", "and", "in", "a", "an", "to",
    "committee", "fund", "pac", "political", "action",
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


def normalize_name(value: Optional[str]) -> str:
    if not value:
        return ""
    text = value.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\\s]", " ", text)
    tokens = [tok for tok in text.split() if tok]
    tokens = [tok for tok in tokens if tok not in _CORP_SUFFIXES]
    return " ".join(tokens)


def tokenize_name(value: Optional[str]) -> List[str]:
    tokens = normalize_name(value).split()
    return [tok for tok in tokens if tok and tok not in _NAME_STOP_WORDS]


def normalize_address(value: Optional[str]) -> str:
    if not value:
        return ""
    text = value.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\\s]", " ", text)
    tokens = [_ADDRESS_ABBREVIATIONS.get(tok, tok) for tok in text.split() if tok]
    return " ".join(tokens)


def normalize_state(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def normalize_zip(value: Optional[str]) -> str:
    if not value:
        return ""
    digits = re.sub(r"[^0-9]", "", value)
    return digits[:5] if len(digits) >= 5 else ""


def _jaccard(left: List[str], right: List[str]) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def name_similarity(left: Optional[str], right: Optional[str]) -> float:
    if not left or not right:
        return 0.0
    left_norm = normalize_name(left)
    right_norm = normalize_name(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    tokens_left = tokenize_name(left_norm)
    tokens_right = tokenize_name(right_norm)
    jacc = _jaccard(tokens_left, tokens_right)
    ratio = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
    return 0.6 * jacc + 0.4 * ratio


def best_name_similarity(left_names: Iterable[str], right_names: Iterable[str]) -> float:
    best = 0.0
    for left in left_names:
        for right in right_names:
            score = name_similarity(left, right)
            if score > best:
                best = score
            if best >= 1.0:
                return 1.0
    return best


def address_similarity(
    left_address: Optional[str],
    right_address: Optional[str],
    left_city: Optional[str],
    right_city: Optional[str],
    left_state: Optional[str],
    right_state: Optional[str],
    left_zip: Optional[str],
    right_zip: Optional[str],
) -> float:
    if not left_address and not right_address and not left_city and not right_city:
        return 0.0
    left_addr = normalize_address(left_address)
    right_addr = normalize_address(right_address)
    if left_addr and right_addr and left_addr == right_addr:
        return 1.0
    left_zip5 = normalize_zip(left_zip)
    right_zip5 = normalize_zip(right_zip)
    if left_zip5 and right_zip5 and left_zip5 == right_zip5:
        return 0.8
    left_city_norm = (left_city or "").strip().lower()
    right_city_norm = (right_city or "").strip().lower()
    left_state_norm = normalize_state(left_state)
    right_state_norm = normalize_state(right_state)
    if left_city_norm and right_city_norm and left_state_norm and right_state_norm:
        if left_city_norm == right_city_norm and left_state_norm == right_state_norm:
            return 0.6
    return 0.0


def officer_similarity(left_officers: Iterable[str], right_officers: Iterable[str]) -> float:
    left_tokens = {normalize_name(name) for name in left_officers if name}
    right_tokens = {normalize_name(name) for name in right_officers if name}
    left_tokens = {name for name in left_tokens if name}
    right_tokens = {name for name in right_tokens if name}
    if not left_tokens or not right_tokens:
        return 0.0
    return _jaccard(list(left_tokens), list(right_tokens))


@dataclass
class EntityProfile:
    record_id: str
    source: str
    name: str
    aliases: List[str] = field(default_factory=list)
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    officers: List[str] = field(default_factory=list)
    identifiers: Dict[str, str] = field(default_factory=dict)

    def normalized_name(self) -> str:
        return normalize_name(self.name)


@dataclass
class ResolvedEntity:
    entity_id: str
    canonical_name: str
    aliases: List[str]
    sources: List[str]
    confidence_score: float


@dataclass
class ResolverConfig:
    weight_name: float = 0.6
    weight_address: float = 0.25
    weight_officer: float = 0.15
    auto_merge_threshold: float = 0.92
    review_threshold: float = 0.85


@dataclass
class MatchEvidence:
    left_id: str
    right_id: str
    score: float
    method: str
    evidence: Dict[str, str] = field(default_factory=dict)


class UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}
        self.rank: Dict[str, int] = {}

    def add(self, item: str) -> None:
        if item not in self.parent:
            self.parent[item] = item
            self.rank[item] = 0

    def find(self, item: str) -> str:
        root = self.parent.get(item, item)
        if root != item:
            root = self.find(root)
            self.parent[item] = root
        return root

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        left_rank = self.rank.get(left_root, 0)
        right_rank = self.rank.get(right_root, 0)
        if left_rank < right_rank:
            self.parent[left_root] = right_root
        elif left_rank > right_rank:
            self.parent[right_root] = left_root
        else:
            self.parent[right_root] = left_root
            self.rank[left_root] = left_rank + 1


def compute_match_score(left: EntityProfile, right: EntityProfile, config: ResolverConfig) -> Tuple[float, Dict[str, float]]:
    shared_ids = set(left.identifiers.values()) & set(right.identifiers.values())
    if shared_ids:
        return 1.0, {"id_score": 1.0, "name_score": 0.0, "address_score": 0.0, "officer_score": 0.0}

    left_names = [left.name] + list(left.aliases or [])
    right_names = [right.name] + list(right.aliases or [])
    name_score = best_name_similarity(left_names, right_names)
    address_score = address_similarity(
        left.address,
        right.address,
        left.city,
        right.city,
        left.state,
        right.state,
        left.postal_code,
        right.postal_code,
    )
    officer_score = officer_similarity(left.officers, right.officers)
    total = (
        config.weight_name * name_score
        + config.weight_address * address_score
        + config.weight_officer * officer_score
    )
    return total, {
        "id_score": 0.0,
        "name_score": name_score,
        "address_score": address_score,
        "officer_score": officer_score,
    }


def blocking_keys(profile: EntityProfile) -> List[str]:
    tokens = tokenize_name(profile.name)
    if not tokens:
        return []
    first = tokens[0]
    second = tokens[1] if len(tokens) > 1 else ""
    state = normalize_state(profile.state)
    zip5 = normalize_zip(profile.postal_code)
    keys = [f"name:{first}"]
    if second:
        keys.append(f"name:{first}:{second}")
    if state:
        keys.append(f"name_state:{first}:{state}")
    if zip5:
        keys.append(f"name_zip:{first}:{zip5}")
    return list(dict.fromkeys(keys))


def candidate_pairs(candidates: Dict[str, EntityProfile]) -> List[Tuple[str, str]]:
    blocks: Dict[str, List[str]] = defaultdict(list)
    for record_id, profile in candidates.items():
        for key in blocking_keys(profile):
            blocks[key].append(record_id)
    pairs = set()
    for ids in blocks.values():
        if len(ids) < 2:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.add((ids[i], ids[j]))
    return list(pairs)


class EntityResolver:
    def __init__(self, config: Optional[ResolverConfig] = None) -> None:
        self.config = config or ResolverConfig()
        self.profiles: Dict[str, EntityProfile] = {}
        self.matches: List[MatchEvidence] = []
        self.uf = UnionFind()

    def add_profile(self, profile: EntityProfile) -> None:
        self.profiles[profile.record_id] = profile
        self.uf.add(profile.record_id)

    def add_match(self, left_id: str, right_id: str, score: float, method: str, evidence: Optional[Dict[str, str]] = None) -> None:
        self.matches.append(
            MatchEvidence(
                left_id=left_id,
                right_id=right_id,
                score=score,
                method=method,
                evidence=evidence or {},
            )
        )

    def resolve(self) -> None:
        for match in self.matches:
            if match.score >= self.config.auto_merge_threshold:
                self.uf.union(match.left_id, match.right_id)

    def clusters(self) -> Dict[str, List[EntityProfile]]:
        grouped: Dict[str, List[EntityProfile]] = defaultdict(list)
        for record_id, profile in self.profiles.items():
            root = self.uf.find(record_id)
            grouped[root].append(profile)
        return grouped

    def cluster_confidence(self, cluster_ids: Iterable[str]) -> float:
        scores = []
        cluster_set = set(cluster_ids)
        for match in self.matches:
            if match.left_id in cluster_set and match.right_id in cluster_set:
                scores.append(match.score)
        if not scores:
            return 0.5
        return max(scores)

    def canonical_name(self, profiles: Iterable[EntityProfile]) -> str:
        names: Dict[str, List[str]] = defaultdict(list)
        for profile in profiles:
            if profile.name:
                names[normalize_name(profile.name)].append(profile.name)
        if not names:
            return ""
        best_norm = max(names.items(), key=lambda item: (len(item[1]), len(max(item[1], key=len))))[0]
        originals = names[best_norm]
        return max(originals, key=len)

    def resolved_entities(self) -> List[ResolvedEntity]:
        entities: List[ResolvedEntity] = []
        clusters = self.clusters()
        for root_id, profiles in clusters.items():
            cluster_ids = [p.record_id for p in profiles]
            entity = ResolvedEntity(
                entity_id=root_id,
                canonical_name=self.canonical_name(profiles),
                aliases=sorted({p.name for p in profiles if p.name}),
                sources=sorted({p.source for p in profiles if p.source}),
                confidence_score=round(self.cluster_confidence(cluster_ids), 4),
            )
            entities.append(entity)
        return entities
