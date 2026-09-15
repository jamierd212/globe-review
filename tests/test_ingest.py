"""No network. Everything here runs against fixtures, because a test that
needs the internet is a test that fails on the day a publisher has an outage."""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingest import feeds, dedup, db

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test</title>
<item><title>Fury as Home Office plans new site</title>
<link>https://www.example.co.uk/news/123?utm_source=rss&amp;ito=1</link>
<description>&lt;p&gt;A standfirst with &lt;b&gt;markup&lt;/b&gt; in it.&lt;/p&gt;</description>
<pubDate>Mon, 01 Sep 2025 09:20:00 +0100</pubDate><guid>tag:ex:123</guid></item>
<item><title>Second story</title><link>https://example.co.uk/news/456/</link>
<pubDate>Mon, 01 Sep 2025 10:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>T</title>
<entry><title>Atom headline here</title><link href="https://a.example/one"/>
<summary>Short summary.</summary><published>2025-09-01T08:00:00Z</published>
<id>urn:1</id></entry></feed>"""

def test_parse_rss():
    it = feeds.parse(RSS)
    assert len(it) == 2, it
    a = it[0]
    assert a["title"] == "Fury as Home Office plans new site"
    assert a["url_canon"] == "https://example.co.uk/news/123", a["url_canon"]
    assert a["standfirst"] == "A standfirst with markup in it."
    assert a["published_at"] == "2025-09-01T08:20:00+00:00", a["published_at"]
    assert a["guid"] == "tag:ex:123"
    assert it[1]["url_canon"] == "https://example.co.uk/news/456"

def test_parse_atom():
    it = feeds.parse(ATOM)
    assert len(it) == 1 and it[0]["url_canon"] == "https://a.example/one"
    assert it[0]["published_at"] == "2025-09-01T08:00:00+00:00"

def test_order_preserved():
    assert [i["title"] for i in feeds.parse(RSS)][0].startswith("Fury")

def test_canon():
    c = feeds.canon_url
    assert c("http://WWW.Example.co.uk/a/b/?utm_medium=x&id=7#frag") == \
           "https://example.co.uk/a/b?id=7"
    assert c("https://example.co.uk/") == "https://example.co.uk/"

def test_dedup_catches_wire_copy_across_outlets():
    arts = [
      {"id":1,"outlet_id":"mirror","url_canon":"https://m/1","title":"Storm Bella batters northern England with 80mph gusts"},
      {"id":2,"outlet_id":"express","url_canon":"https://e/1","title":"Storm Bella batters northern England with 80mph gusts"},
      {"id":3,"outlet_id":"metro","url_canon":"https://x/1","title":"Storm Bella batters northern England, 80mph gusts recorded"},
      {"id":4,"outlet_id":"guardian","url_canon":"https://g/1","title":"Budget: chancellor freezes income tax thresholds"},
      {"id":5,"outlet_id":"mirror","url_canon":"https://m/2","title":"Storm Bella: how to keep your home safe tonight"},
    ]
    groups, method = dedup.group(arts)
    assert groups[1] == groups[2] == groups[3], (groups, method)
    assert groups[4] != groups[1]
    assert groups[5] != groups[1], "same-outlet follow-up must not be merged"
    # 5 articles, one group of 3 plus two singletons -> 2 repeats of 5 = 0.4
    assert abs(dedup.duplication_rate(groups) - 0.4) < 1e-9, dedup.duplication_rate(groups)

def test_same_outlet_never_grouped():
    arts = [{"id":1,"outlet_id":"sun","url_canon":"https://s/1","title":"Identical headline text here"},
            {"id":2,"outlet_id":"sun","url_canon":"https://s/2","title":"Identical headline text here"}]
    groups, _ = dedup.group(arts)
    assert groups[1] != groups[2]

def test_schema_and_observation_roundtrip():
    con = db.connect(":memory:")
    db.sync_outlets(con, {"outlets":[{"id":"sky","name":"Sky","leaning":0.08,
        "market":0.52,"weight":1.0,"feeds":[{"url":"https://f/1","kind":"top"}]}]})
    fid = con.execute("SELECT id FROM feed").fetchone()["id"]
    pid = con.execute("INSERT INTO poll (feed_id,polled_at,http_status,n_items) "
                      "VALUES (?,?,?,?)", (fid,"2026-01-01T06:00:00+00:00","200",2)).lastrowid
    aid = con.execute("INSERT INTO article (outlet_id,url_canon,title,first_seen,title_sig) "
                      "VALUES (?,?,?,?,?)", ("sky","https://a/1","T","2026-01-01T06:00:00+00:00","x")).lastrowid
    con.execute("INSERT INTO observation (poll_id,feed_id,article_id,polled_at,position) "
                "VALUES (?,?,?,?,?)", (pid,fid,aid,"2026-01-01T06:00:00+00:00",0))
    con.commit()
    assert con.execute("SELECT position FROM observation").fetchone()["position"] == 0
    # syncing twice must not duplicate feeds
    db.sync_outlets(con, {"outlets":[{"id":"sky","name":"Sky News","leaning":0.08,
        "market":0.52,"weight":1.0,"feeds":[{"url":"https://f/1","kind":"top"}]}]})
    assert con.execute("SELECT COUNT(*) c FROM feed").fetchone()["c"] == 1
    assert con.execute("SELECT name FROM outlet").fetchone()["name"] == "Sky News"

def test_an_outlet_we_may_not_collect_never_enters_the_database():
    """The Sun and the Times disallow us in robots.txt. The decision is
    recorded in outlets.json rather than the rows deleted, so this checks the
    exclusion is actually honoured instead of just documented."""
    import json
    spec = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "outlets.json")))
    excluded = {o["id"] for o in spec["outlets"] if o.get("excluded")}
    assert excluded, "expected at least one excluded outlet"
    con = db.connect(":memory:")
    db.sync_outlets(con, spec)
    got = {r["id"] for r in con.execute("SELECT id FROM outlet")}
    assert not (got & excluded), got & excluded
    assert got, "everything was excluded"

def test_status_spots_a_feed_that_has_gone_quiet():
    """The failure this guards against is silent: a feed keeps answering 200
    but stops producing anything new, and the globe carries on with a hole."""
    from ingest import status
    con = db.connect(":memory:")
    db.sync_outlets(con, {"outlets": [{"id": "sky", "name": "Sky", "leaning": 0.08,
        "market": 0.52, "weight": 1.0, "feeds": [
            {"url": "https://a/live", "kind": "top"},
            {"url": "https://a/frozen", "kind": "section"},
            {"url": "https://a/dead", "kind": "section"}]}]})
    ids = {r["url"]: r["id"] for r in con.execute("SELECT id, url FROM feed")}
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    for h in range(48, -1, -1):
        t = (now - timedelta(hours=h)).isoformat(timespec="seconds")
        # live: answers and brings new articles
        con.execute("INSERT INTO poll (feed_id,polled_at,http_status,n_items,n_new)"
                    " VALUES (?,?,?,?,?)", (ids["https://a/live"], t, "200", 20, 2))
        # frozen: answers with the same items forever
        con.execute("INSERT INTO poll (feed_id,polled_at,http_status,n_items,n_new)"
                    " VALUES (?,?,?,?,?)", (ids["https://a/frozen"], t, "200", 20, 0))
        # dead: stopped answering a day ago
        if h > 24:
            con.execute("INSERT INTO poll (feed_id,polled_at,http_status,n_items,n_new)"
                        " VALUES (?,?,?,?,?)", (ids["https://a/dead"], t, "200", 20, 1))
    con.commit()
    import tempfile, os as _os
    path = _os.path.join(tempfile.mkdtemp(), "t.sqlite")
    disk = db.connect(path)
    con.backup(disk); disk.commit(); disk.close()
    s = status.collect(path)
    by_url = {r["url"]: r for r in s["feeds"]}
    assert by_url["https://a/live"]["flags"] == [], "a healthy feed was flagged"
    assert by_url["https://a/live"]["flags"] == [], by_url["https://a/live"]
    assert "FROZEN" in by_url["https://a/frozen"]["flags"], by_url["https://a/frozen"]
    assert "NOT ANSWERING" in by_url["https://a/dead"]["flags"], by_url["https://a/dead"]
    txt = status.text(s)
    assert "need attention" in txt
    assert status.html(s).startswith("<!doctype html>")

def test_a_gzipped_feed_is_not_mistaken_for_a_broken_one():
    """The Independent started gzipping its feeds between two polls, without
    setting Content-Encoding. The body is valid gzip, the parser chokes, and it
    looks exactly like a malformed feed - so the magic bytes are trusted over
    the header."""
    import gzip as _gz
    body = _gz.compress(RSS)
    assert body[:2] == b"\x1f\x8b"
    # no header at all, which is the case that actually happened
    assert feeds.parse(feeds.decompress(body, {}))[0]["title"].startswith("Fury")
    # and with the header set, as most outlets do
    assert feeds.parse(feeds.decompress(body, {"Content-Encoding": "gzip"}))
    # plain XML is left alone
    assert feeds.decompress(RSS, {}) == RSS
    # something genuinely broken still reaches the parser as broken
    assert feeds.decompress(b"<not xml", {}) == b"<not xml"

def test_a_brand_new_feed_is_not_called_frozen():
    """On the first day there is no 24h of history to judge against. Flagging
    it anyway means every feed is red on day one and nobody reads the page
    again."""
    from ingest import status
    from datetime import datetime, timezone, timedelta
    import tempfile, os as _os
    con = db.connect(":memory:")
    db.sync_outlets(con, {"outlets": [{"id": "sky", "name": "Sky", "leaning": 0.0,
        "market": 0.5, "weight": 1.0,
        "feeds": [{"url": "https://a/new", "kind": "top"}]}]})
    fid = con.execute("SELECT id FROM feed").fetchone()["id"]
    now = datetime.now(timezone.utc)
    # two polls a minute apart: answering, nothing new, and only just started.
    # Also covers the case that first broke this - a long gap in polling, which
    # makes the elapsed time look like a day even though we only looked twice.
    for mins in (2, 1):
        con.execute("INSERT INTO poll (feed_id,polled_at,http_status,n_items,n_new)"
                    " VALUES (?,?,?,?,?)",
                    (fid, (now - timedelta(minutes=mins)).isoformat(timespec="seconds"),
                     "200", 20, 0))
    con.commit()
    path = _os.path.join(tempfile.mkdtemp(), "t.sqlite")
    disk = db.connect(path); con.backup(disk); disk.commit(); disk.close()
    s = status.collect(path)
    assert s["feeds"][0]["flags"] == [], s["feeds"][0]["flags"]

if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  ok   {n}")
            except AssertionError as e:
                fails += 1; print(f"  FAIL {n}: {e}")
            except Exception as e:
                fails += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")
    print(("\n%d failure(s)" % fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)
