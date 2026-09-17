"""The archive. An archive that has never been read back is not an archive,
it is a write-only log that feels reassuring - so every test here writes
shards and then rebuilds a database from them."""
import os, sys, json, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingest import db, archive

def _build(tmp, n_polls=3):
    """A small but complete database: outlets, feeds, articles, polls with
    positions, a story, and a score."""
    path = os.path.join(tmp, "live.sqlite")
    con = db.connect(path)
    db.sync_outlets(con, {"outlets": [
        {"id":"sky","name":"Sky","leaning":0.08,"market":0.52,"weight":1.0,
         "feeds":[{"url":"https://a/top","kind":"top"}]},
        {"id":"mail","name":"Mail","leaning":0.68,"market":-0.3,"weight":1.35,
         "feeds":[{"url":"https://b/top","kind":"top"}]}]})
    feeds = [r["id"] for r in con.execute("SELECT id FROM feed ORDER BY id")]
    aid = 0
    for p in range(n_polls):
        t = f"2026-09-0{p+1}T06:00:00+00:00"
        for fid in feeds:
            pid = con.execute("INSERT INTO poll (feed_id,polled_at,http_status,"
                "n_items,n_new,items_hash) VALUES (?,?,?,?,?,?)",
                (fid, t, "200", 3, 3, f"h{p}{fid}")).lastrowid
            for pos in range(3):
                aid += 1
                con.execute("INSERT INTO article (id,outlet_id,url_canon,title,"
                    "first_seen,title_sig,standfirst,published_at,guid) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (aid,"sky" if fid==feeds[0] else "mail",f"https://x/{aid}",
                     f"Headline {aid}",t,f"sig{aid}","A standfirst.",t,f"g{aid}"))
                con.execute("INSERT INTO observation (poll_id,feed_id,article_id,"
                    "polled_at,position) VALUES (?,?,?,?,?)",(pid,fid,aid,t,pos))
    con.execute("INSERT INTO story (id,name,target,issue_id,first_seen,last_seen,"
        "status,n_articles,target_conf) VALUES (?,?,?,?,?,?,?,?,?)",
        (1,"Test story","the thing","small-boats","2026-09-01T06:00:00+00:00",
         "2026-09-03T06:00:00+00:00","live",3,"inherited"))
    for a in (1,2,3):
        con.execute("INSERT INTO story_member (article_id,story_id,assigned_at) "
                    "VALUES (?,?,?)",(a,1,"2026-09-03T06:00:00+00:00"))
        con.execute("INSERT INTO article_score (article_id,issue_id,story_id,stance,"
            "tone,confidence,quote,desk,reason,rubric_version,model,scored_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (a,"small-boats",1,-1.0,-1.0,"high","a quote","news","because","v1",
             "m","2026-09-03T07:00:00+00:00"))
    con.commit(); con.close()
    return path

def _isolate(tmp):
    archive.SHARDS = os.path.join(tmp, "shards")

def test_everything_comes_back():
    tmp = tempfile.mkdtemp(); _isolate(tmp)
    live = _build(tmp)
    archive.export(live)
    back = os.path.join(tmp, "restored.sqlite")
    archive.restore(back, quiet=True)
    a, b = db.connect(live), db.connect(back)
    for t in ("article","poll","observation","story","article_score","story_member"):
        assert a.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == \
               b.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], t
    shutil.rmtree(tmp)

def test_feed_positions_survive_exactly():
    """The one thing that can never be collected again."""
    tmp = tempfile.mkdtemp(); _isolate(tmp)
    live = _build(tmp); archive.export(live)
    back = os.path.join(tmp,"r.sqlite"); archive.restore(back, quiet=True)
    q = "SELECT poll_id, article_id, position FROM observation ORDER BY poll_id, position"
    assert [tuple(r) for r in db.connect(live).execute(q)] == \
           [tuple(r) for r in db.connect(back).execute(q)]
    shutil.rmtree(tmp)

def test_export_only_writes_what_is_new():
    tmp = tempfile.mkdtemp(); _isolate(tmp)
    live = _build(tmp)
    first = archive.export(live)
    again = archive.export(live)
    assert first["articles"] > 0
    assert not again, f"re-exported {again} with nothing new"
    shutil.rmtree(tmp)

def test_shards_are_append_only():
    """Nothing already written may change. That is what lets git store a
    month of history for the cost of the lines added."""
    tmp = tempfile.mkdtemp(); _isolate(tmp)
    live = _build(tmp); archive.export(live)
    path = os.path.join(archive.SHARDS, "2026-09", "articles.jsonl")
    before = open(path).read()
    con = db.connect(live)
    con.execute("INSERT INTO article (id,outlet_id,url_canon,title,first_seen,"
        "title_sig) VALUES (999,'sky','https://x/999','New one',"
        "'2026-09-04T06:00:00+00:00','s999')")
    con.commit(); con.close()
    archive.export(live)
    after = open(path).read()
    assert after.startswith(before), "an existing line changed"
    assert len(after) > len(before)
    shutil.rmtree(tmp)

def test_losing_the_shards_does_not_silently_report_success():
    """The worst failure for a backup is one that looks like success. If the
    shards are gone but the database thinks they exist, export must notice."""
    tmp = tempfile.mkdtemp(); _isolate(tmp)
    live = _build(tmp); archive.export(live)
    shutil.rmtree(archive.SHARDS)
    again = archive.export(live)
    assert again.get("articles", 0) > 0, "reported nothing to do with no shards"
    shutil.rmtree(tmp)

def test_a_rebuilt_database_does_not_re_export_everything():
    """The failure a cold start on the runner produces. A database rebuilt
    from the shards has no memory of what is already in them, so the next
    export appends the whole archive again - silently doubling it."""
    tmp = tempfile.mkdtemp(); _isolate(tmp)
    live = _build(tmp)
    archive.export(live)
    lines_before = len(open(os.path.join(
        archive.SHARDS, "2026-09", "articles.jsonl")).read().splitlines())

    rebuilt = os.path.join(tmp, "rebuilt.sqlite")
    archive.restore(rebuilt, quiet=True)
    again = archive.export(rebuilt)
    assert not again, f"a rebuilt database re-exported {again}"

    lines_after = len(open(os.path.join(
        archive.SHARDS, "2026-09", "articles.jsonl")).read().splitlines())
    assert lines_after == lines_before, \
        f"the archive grew from {lines_before} to {lines_after} lines with no new data"
    shutil.rmtree(tmp)

def test_a_rebuilt_database_still_takes_new_rows():
    """...and it must not go the other way either - the cursors have to be
    right, not merely high enough to suppress everything."""
    tmp = tempfile.mkdtemp(); _isolate(tmp)
    live = _build(tmp); archive.export(live)
    rebuilt = os.path.join(tmp, "rebuilt.sqlite")
    archive.restore(rebuilt, quiet=True)
    con = db.connect(rebuilt)
    con.execute("INSERT INTO article (id,outlet_id,url_canon,title,first_seen,"
                "title_sig) VALUES (5000,'sky','https://x/5000','Later one',"
                "'2026-09-05T06:00:00+00:00','s5000')")
    con.commit(); con.close()
    again = archive.export(rebuilt)
    assert again.get("articles") == 1, again
    shutil.rmtree(tmp)

if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try: fn(); print(f"  ok   {n}")
            except AssertionError as e: fails += 1; print(f"  FAIL {n}: {e}")
            except Exception as e: fails += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")
    print(("\n%d failure(s)" % fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)
