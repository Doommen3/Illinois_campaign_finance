from src.entity_resolution import (
    EntityProfile,
    EntityResolver,
    name_similarity,
    normalize_name,
    tokenize_name,
)


def test_normalize_name_removes_suffixes():
    assert normalize_name("Acme, Inc.") == "acme"
    assert normalize_name("Acme LLC") == "acme"


def test_tokenize_name_removes_stop_words():
    tokens = tokenize_name("Citizens for Better Transit Committee")
    assert "citizens" in tokens
    assert "better" in tokens
    assert "transit" in tokens
    assert "committee" not in tokens


def test_name_similarity_is_high_for_close_names():
    score = name_similarity("Citizens for Joe Moore Committee", "Citizens for Joe Moore")
    assert score >= 0.85


def test_entity_resolver_merges_high_score():
    resolver = EntityResolver()
    left = EntityProfile(record_id="lobby:1", source="lobby_client", name="Example Trade Association")
    right = EntityProfile(record_id="irs527:1", source="irs527", name="Example Trade Assn PAC")
    resolver.add_profile(left)
    resolver.add_profile(right)
    resolver.add_match(left.record_id, right.record_id, 0.95, "name_jaccard")
    resolver.resolve()
    clusters = resolver.clusters()
    assert len(clusters) == 1


def test_entity_resolver_does_not_merge_low_score():
    resolver = EntityResolver()
    left = EntityProfile(record_id="lobby:2", source="lobby_client", name="Northwest Builders Group")
    right = EntityProfile(record_id="irs527:2", source="irs527", name="Southwest Energy Fund")
    resolver.add_profile(left)
    resolver.add_profile(right)
    resolver.add_match(left.record_id, right.record_id, 0.4, "name_jaccard")
    resolver.resolve()
    clusters = resolver.clusters()
    assert len(clusters) == 2
