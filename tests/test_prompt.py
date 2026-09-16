"""The prompt. Two rules here are enforced rather than trusted, and both would
fail silently: the scorer must never see which paper wrote the item, and the
target must be stated every time."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from score import prompt as P
from score.run import parse_json, desk_from_url, money

def test_the_scorer_is_never_told_which_paper():
    """Covers the rubric too, not just the item. The rubric is fed to the model
    verbatim, so a worked example naming a paper would hand it a prior about
    how that paper usually scores."""
    p = P.item_prompt("Fury as Home Office plans new site",
                      "Locals say they were not consulted.",
                      "small-boats", "people crossing the Channel in small boats")
    low = (p + P.system_prompt()).lower()
    for paper in ("express", "guardian", "daily mail", "telegraph", "mirror",
                  "the sun", "metro", "sky news"):
        assert paper not in low, f"the prompt names {paper}"

def test_the_target_is_always_stated():
    p = P.item_prompt("Net zero target date pushed back", None,
                      "net-zero-rollback", "weakening the climate commitments")
    assert "weakening the climate commitments" in p
    assert "TARGET" in p

def test_the_taxonomy_prior_never_reaches_the_model():
    """`expect` says which way the left and right should come out. If the model
    saw it, the direction check would be testing its own assumption."""
    issues = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "taxonomy.json")))["issues"]
    safe = [{"id": i["id"], "name": i["name"], "target": i["target"]} for i in issues]
    p = P.tag_prompt("A story", ["a headline"], safe)
    for word in ("expect", "favourable\", \"right", "regression"):
        assert word not in p, f"the prior leaked: {word}"
    assert "hostile" not in p.lower() or "target" in p.lower()

def test_the_prompt_carries_the_whole_rubric():
    s = P.system_prompt()
    for rule in ("Not tone", "Abstain", "verbatim", "target"):
        assert rule.lower() in s.lower(), rule
    assert P.rubric_version() == "v1", P.rubric_version()

def test_desk_comes_from_the_url_not_the_words():
    assert desk_from_url("https://x.co.uk/comment/2026/09/piece") == "comment"
    assert desk_from_url("https://x.co.uk/news/uk/story") == "news"
    assert desk_from_url("https://x.co.uk/opinion/columnists/a") == "comment"
    assert desk_from_url(None) == "news"
    # an angry news headline is still news
    assert desk_from_url("https://x.co.uk/news/fury-as-ministers-cave") == "news"

def test_json_survives_fences_and_chatter():
    assert parse_json('{"stance": -2}')["stance"] == -2
    assert parse_json('```json\n{"stance": 1}\n```')["stance"] == 1
    assert parse_json('Sure! {"stance": 0, "quote": null} hope that helps')["stance"] == 0
    assert parse_json("not json at all") is None
    assert parse_json("") is None

def test_cost_arithmetic():
    # 1M in + 1M out at Haiku 4.5 list
    assert abs(money(1_000_000, 1_000_000) - 6.00) < 1e-9

def test_the_naming_rules_reach_the_model():
    t = P.TAG_SYSTEM
    for rule in ("four to seven words", "belong to none of them",
                 "takes a side", "Name the event, not the field",
                 "A name is always required"):
        assert rule in t, rule

def test_a_loaded_name_is_rejected():
    """A name sits on every paper's coverage of that story. One that carries a
    word being measured is measuring its own label, so it fails safe."""
    from score.run import clean_name
    keep = "Old headline"
    for bad in ("Fury as ministers cave on boiler ban",
                "Reform donations: a scandal in the making",
                "Is AI dangerous?",
                "Chaos as asylum hotel plan collapses",
                "Humiliating climbdown over welfare cuts"):
        assert clean_name(bad, keep) == keep, bad

def test_a_good_name_is_taken():
    from score.run import clean_name
    for good, want in (
        ("reform donations reach £72m", "Reform donations reach £72m"),
        ("Sanctions on Israeli settlements announced", "Sanctions on Israeli settlements announced"),
        ('  "Johnson train hit by drone in Ukraine"  ', "Johnson train hit by drone in Ukraine")):
        assert clean_name(good, "fallback") == want, good

def test_nonsense_falls_back():
    from score.run import clean_name
    for bad in ("", None, "Reform", "x " * 40,
                "The Guardian view on Lucy Letby: Thirlwall should have waited for "
                "the pending case to conclude first"):
        assert clean_name(bad, "fallback") == "fallback", repr(bad)

if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try: fn(); print(f"  ok   {n}")
            except AssertionError as e: fails += 1; print(f"  FAIL {n}: {e}")
            except Exception as e: fails += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")
    print(("\n%d failure(s)" % fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)
