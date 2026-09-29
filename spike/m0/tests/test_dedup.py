from m0.dedup import Doc, cluster_kind, normalize, shingles

W = " ".join(f"w{i}" for i in range(200))
W_CHANGED = W.replace(" w100 ", " changed ")
Z = " ".join(f"z{i}" for i in range(200))


def test_normalize_templates_repo_and_owner_and_collapses_space():
    assert normalize("Acme-Tools  uses\nACME-TOOLS by bigco", "bigco/acme-tools") == "{repo} uses {repo} by {owner}"


def test_normalize_leaves_short_names_alone():
    assert normalize("an ai tool", "ai/an") == "an ai tool"


def test_shingles_short_text_is_one_shingle():
    assert shingles("a b c") == {"a b c"}
    assert len(shingles("a b c d e f")) == 2


def test_cluster_tiers_are_cumulative():
    docs = [
        Doc("skill", "o1/alpha", "SKILL.md", "a", "project alpha " + W),
        Doc("skill", "o3/gamma", "SKILL.md", "a", "project alpha " + W),  # same blob, other repo
        Doc("skill", "o2/beta", "SKILL.md", "c", "PROJECT   BETA " + W.upper()),  # same after normalize
        Doc("skill", "o4/delta", "SKILL.md", "d", "project delta " + W_CHANGED),  # near-duplicate
        Doc("skill", "o5/eps", "SKILL.md", "e", "project eps " + Z),  # unrelated
    ]
    counts, reps = cluster_kind(docs)
    assert counts["n"] == 5
    assert counts["exact"] == 4
    assert counts["normalized"] == 3
    assert counts["minhash"]["0.8"] == 2
    assert len(reps["0.8"]) == 2
