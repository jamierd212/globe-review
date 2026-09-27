"""The switchable model layer and grouped scoring, against a stand-in for
Gemini so none of this needs a key or spends anything."""
import os, sys, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingest import db
from score import llm as L, run as R, prompt as P


def test_parse_json_takes_arrays_and_objects():
    assert L.parse_json('[{"id":"a1","stance":-1}]') == [{"id": "a1", "stance": -1}]
    assert L.parse_json('```json\n{"stance": 1}\n```') == {"stance": 1}
    assert L.parse_json('Here you go: [{"id": "a2"}] hope that helps') == [{"id": "a2"}]
    assert L.parse_json("nothing useful") is None


def test_daily_limit_is_told_apart_from_per_minute():
    daily = {"details": [{"violations": [
        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}
    minute = {"details": [{"violations": [
        {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"retryDelay": "17s"}]}
    assert L._is_daily(daily) and not L._is_daily(minute)
    assert L._retry_after(minute, 5) == 18.0


def test_key_never_goes_in_the_url():
    seen = {}
    class Fake:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"models": []}'
    import urllib.request
    real = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=0: (seen.update(
        url=req.full_url, headers=dict(req.headers)), Fake())[1]
    os.environ["GEMINI_API_KEY"] = "test-key-123"
    try:
        L._request("GET", "models?pageSize=1000")
    finally:
        urllib.request.urlopen = real
    assert "test-key-123" not in seen["url"]
    assert seen["headers"].get("X-goog-api-key") == "test-key-123"


def test_newest_stable_flash_lite_is_picked():
    L._GEMINI_MODEL = None
    os.environ.pop("GEMINI_MODEL", None)
    real = L._request
    L._request = lambda m, p, b=None: {"models": [
        {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-flash-lite-preview", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]}]}
    try:
        assert L.gemini_model() == "gemini-3.1-flash-lite"
    finally:
        L._request = real
        L._GEMINI_MODEL = None


def _todo(n=25, stories=1):
    return [{"id": i, "title": f"Headline {i}", "standfirst": "", "outlet_id": "mail",
             "url_canon": f"https://www.dailymail.co.uk/news/{i}",
             "story_id": 1 + i % stories, "issue_id": None,
             "target": f"target of story {1 + i % stories}",
             "story": f"Story {1 + i % stories}"}
            for i in range(1, n + 1)]


def test_grouped_scoring_sends_twenty_at_a_time_and_never_the_paper():
    sent = []
    def fake(system, user, max_tokens=300):
        sent.append(user)
        ids = [l[1:-1] for l in user.splitlines() if l.startswith("[a")]
        # answers all but one, to prove a missing answer is not invented
        return [{"id": i, "stance": -1, "confidence": "high"} for i in ids[:-1]], 100, 50
    real = L.complete
    L.complete = fake
    got = {}
    try:
        R.score_groups(_todo(25), lambda t, r, now: got.__setitem__(t["id"], r))
    finally:
        L.complete = real
    assert len(sent) == 2, len(sent)                 # 20 + 5
    assert len(got) == 23                            # one unanswered per request
    assert all("mail" not in u.lower() for u in sent), "the paper reached the prompt"


def test_many_small_stories_share_requests_each_under_its_own_target():
    """An hourly run has a few new articles on each of many stories. One
    request per story would use the whole free day; packing must not mix up
    which target each article is scored against."""
    sent = []
    def fake(system, user, max_tokens=300):
        sent.append(user)
        ids = [l[1:-1] for l in user.splitlines() if l.startswith("[a")]
        return [{"id": i, "stance": 0} for i in ids], 1, 1
    real = L.complete
    L.complete = fake
    try:
        R.score_groups(_todo(40, stories=10), lambda t, r, now: None)
    finally:
        L.complete = real
    assert len(sent) == 2, len(sent)                 # 40 articles, 10 stories
    # every article sits under its own story's target
    for u in sent:
        target = None
        for line in u.splitlines():
            if line.startswith("TARGET"):
                target = line.rsplit(": ", 1)[1]
            m = re.match(r"HEADLINE: Headline (\d+)", line)
            if m:
                i = int(m.group(1))
                assert target == f"target of story {1 + i % 10}", (i, target)


def test_daily_limit_stops_cleanly():
    calls = []
    def fake(system, user, max_tokens=300):
        calls.append(1)
        if len(calls) > 1:
            raise L.DailyLimit("done for today")
        ids = [l[1:-1] for l in user.splitlines() if l.startswith("[a")]
        return [{"id": i, "stance": 0} for i in ids], 1, 1
    real = L.complete
    L.complete = fake
    got = {}
    try:
        R.score_groups(_todo(45), lambda t, r, now: got.__setitem__(t["id"], r))
    finally:
        L.complete = real
    assert len(got) == 20


def test_rescoring_a_storyless_subject_replaces_rather_than_duplicates():
    con = db.connect(":memory:")
    t = _todo(1)[0]
    R._store(con, t, {"stance": -1}, "v1", "claude", "2026-09-27")
    R._store(con, t, {"stance": 1}, "v1", "gemini", "2026-09-28")
    rows = con.execute("SELECT stance, model FROM article_score").fetchall()
    assert [tuple(r) for r in rows] == [(1.0, "gemini")], [tuple(r) for r in rows]


def test_default_stays_on_claude_until_switched():
    os.environ.pop("FPM_PROVIDER", None)
    assert L.provider() == "anthropic"
    assert not L.grouped()


if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try:
                fn(); print("ok  ", n)
            except AssertionError as e:
                fails += 1; print("FAIL", n, e)
    sys.exit(1 if fails else 0)
