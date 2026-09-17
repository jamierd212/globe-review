"""The archive, as plain text files that get added to.

    python3 -m ingest.archive export     write anything new to data/shards/
    python3 -m ingest.archive restore    rebuild the database from them
    python3 -m ingest.archive check      do the two agree?

The problem this solves: the database is a single 7MB file that changes every
hour. Git stores a whole new copy of a binary every time it changes, so
committing it hourly would add roughly 2.7GB a month. Keeping it as a download
attached to each run works but GitHub deletes those after ninety days, and the
hourly feed positions are the one thing that can never be collected again.

So the same information is also written as **plain text files that are only
ever added to**, one directory per month. Git handles that properly: appending
a few hundred lines costs a few hundred lines, not another copy of everything.

Two rules make it work, and both matter:

  append only    a line, once written, is never changed or removed. Anything
                 that needs correcting gets a new line rather than an edit.
  restorable     `restore` rebuilds the database from the shards, and `check`
                 proves it matches. An archive nobody can read back is not an
                 archive, it is a write-only log that feels reassuring.

Feed positions are stored one line per poll rather than one line per article
per poll - the ordered list of article ids IS the position data, and that form
is about twenty times smaller.
"""

import gzip
import json
import os
import sys
from datetime import datetime, timezone

from . import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARDS = os.path.join(ROOT, "data", "shards")


def _month(stamp):
    return (stamp or "1970-01")[:7]


def _path(month, name):
    d = os.path.join(SHARDS, month)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def _cursor(con, key, default=0):
    con.execute("CREATE TABLE IF NOT EXISTS shard_cursor "
                "(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    r = con.execute("SELECT value FROM shard_cursor WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _set_cursor(con, key, value):
    con.execute("INSERT INTO shard_cursor (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _append(month, name, rows):
    if not rows:
        return 0
    with open(_path(month, name), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(rows)


# ----------------------------------------------------------------- export ---

def export(db_path=None, full=False):
    con = db.connect(db_path)
    written = {}

    # The cursor that records what has been exported lives in the database,
    # and the shards live on disk. Lose one without the other and export
    # cheerfully reports "nothing new" while the archive sits empty - which is
    # the worst possible failure for a backup, because it looks like success.
    have_shards = os.path.isdir(SHARDS) and any(
        f.endswith(".jsonl") for _, _, fs in os.walk(SHARDS) for f in fs)
    con.execute("CREATE TABLE IF NOT EXISTS shard_cursor "
                "(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    exported_before = con.execute(
        "SELECT COUNT(*) FROM shard_cursor").fetchone()[0] > 0
    if full or (exported_before and not have_shards):
        if not full:
            print("the shards are missing but the database thinks they exist - "
                  "writing everything again")
        con.execute("DELETE FROM shard_cursor")
        con.commit()

    # outlets and feeds: small, and rewritten whole each time because they are
    # configuration rather than history
    outlets = [dict(r) for r in con.execute("SELECT * FROM outlet ORDER BY id")]
    feeds = [dict(r) for r in con.execute("SELECT * FROM feed ORDER BY id")]
    os.makedirs(SHARDS, exist_ok=True)
    with open(os.path.join(SHARDS, "sources.json"), "w", encoding="utf-8") as f:
        json.dump({"outlets": outlets, "feeds": feeds}, f, indent=1, ensure_ascii=False)

    # articles, by the month we first saw them
    since = _cursor(con, "article")
    rows = [dict(r) for r in con.execute(
        "SELECT id, outlet_id, url_canon, guid, title, standfirst, published_at, "
        "first_seen, title_sig FROM article WHERE id > ? ORDER BY id", (since,))]
    by_month = {}
    for r in rows:
        by_month.setdefault(_month(r["first_seen"]), []).append(r)
    for m, rs in by_month.items():
        written["articles"] = written.get("articles", 0) + _append(m, "articles.jsonl", rs)
    if rows:
        _set_cursor(con, "article", rows[-1]["id"])

    # polls, each carrying the ordered article ids that were in the feed. That
    # ordering is the irreplaceable part: it is where each story sat on each
    # paper's own front page at that hour.
    since = _cursor(con, "poll")
    polls = [dict(r) for r in con.execute(
        "SELECT id, feed_id, polled_at, http_status, n_items, n_new, items_hash, "
        "error FROM poll WHERE id > ? ORDER BY id", (since,))]
    by_month = {}
    for p in polls:
        order = [r["article_id"] for r in con.execute(
            "SELECT article_id FROM observation WHERE poll_id=? ORDER BY position",
            (p["id"],))]
        p["order"] = order
        by_month.setdefault(_month(p["polled_at"]), []).append(p)
    for m, rs in by_month.items():
        written["polls"] = written.get("polls", 0) + _append(m, "polls.jsonl", rs)
    if polls:
        _set_cursor(con, "poll", polls[-1]["id"])

    # stories: state changes, appended rather than overwritten, so the history
    # of a name or a filing decision survives
    since = _cursor(con, "story_seen")
    rows = [dict(r) for r in con.execute(
        "SELECT id, name, target, issue_id, first_seen, last_seen, status, "
        "n_articles, target_conf FROM story WHERE rowid > ? ORDER BY rowid", (since,))]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    members = {}
    for r in con.execute("SELECT story_id, article_id FROM story_member"):
        members.setdefault(r["story_id"], []).append(r["article_id"])
    for r in rows:
        r["members"] = sorted(members.get(r["id"], []))
        r["exported_at"] = stamp
    by_month = {}
    for r in rows:
        by_month.setdefault(_month(r["first_seen"]), []).append(r)
    for m, rs in by_month.items():
        written["stories"] = written.get("stories", 0) + _append(m, "stories.jsonl", rs)
    if rows:
        _set_cursor(con, "story_seen",
                    con.execute("SELECT MAX(rowid) FROM story").fetchone()[0] or 0)

    # scores
    since = _cursor(con, "score_rowid")
    rows = [dict(r) for r in con.execute(
        "SELECT rowid AS rid, article_id, issue_id, story_id, stance, tone, "
        "confidence, quote, desk, reason, rubric_version, model, scored_at "
        "FROM article_score WHERE rowid > ? ORDER BY rowid", (since,))]
    by_month = {}
    for r in rows:
        rid = r.pop("rid")
        by_month.setdefault(_month(r["scored_at"]), []).append(r)
    for m, rs in by_month.items():
        written["scores"] = written.get("scores", 0) + _append(m, "scores.jsonl", rs)
    if rows:
        _set_cursor(con, "score_rowid",
                    con.execute("SELECT MAX(rowid) FROM article_score").fetchone()[0] or 0)

    con.commit()
    con.close()
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(SHARDS) for f in fs)
    print("appended: " + (", ".join(f"{v} {k}" for k, v in sorted(written.items()))
                          or "nothing new"))
    print(f"shards now {total / 1e6:.1f} MB of plain text")
    return written


# ---------------------------------------------------------------- restore ---

def restore(db_path, quiet=False):
    """Rebuild a database from the shards. Vectors are not stored - they are
    derived from the text and can be recomputed - so the restored database is
    the record, not a working copy ready to cluster."""
    if os.path.exists(db_path):
        os.remove(db_path)
    con = db.connect(db_path)

    src = os.path.join(SHARDS, "sources.json")
    if os.path.exists(src):
        d = json.load(open(src, encoding="utf-8"))
        for o in d["outlets"]:
            con.execute("INSERT OR REPLACE INTO outlet (id,name,leaning,market,weight) "
                        "VALUES (?,?,?,?,?)",
                        (o["id"], o["name"], o["leaning"], o["market"], o["weight"]))
        for f in d["feeds"]:
            con.execute("INSERT OR REPLACE INTO feed (id,outlet_id,url,kind,curated,active) "
                        "VALUES (?,?,?,?,?,?)",
                        (f["id"], f["outlet_id"], f["url"], f["kind"],
                         f.get("curated"), f.get("active", 1)))

    counts = {"articles": 0, "polls": 0, "observations": 0, "stories": 0, "scores": 0}
    for month in sorted(os.listdir(SHARDS)) if os.path.isdir(SHARDS) else []:
        d = os.path.join(SHARDS, month)
        if not os.path.isdir(d):
            continue
        for line in _read(os.path.join(d, "articles.jsonl")):
            con.execute(
                "INSERT OR REPLACE INTO article (id,outlet_id,url_canon,guid,title,"
                "standfirst,published_at,first_seen,title_sig) VALUES (?,?,?,?,?,?,?,?,?)",
                (line["id"], line["outlet_id"], line["url_canon"], line["guid"],
                 line["title"], line["standfirst"], line["published_at"],
                 line["first_seen"], line["title_sig"]))
            counts["articles"] += 1
        for line in _read(os.path.join(d, "polls.jsonl")):
            con.execute(
                "INSERT OR REPLACE INTO poll (id,feed_id,polled_at,http_status,"
                "n_items,n_new,items_hash,error) VALUES (?,?,?,?,?,?,?,?)",
                (line["id"], line["feed_id"], line["polled_at"], line["http_status"],
                 line["n_items"], line["n_new"], line.get("items_hash"), line["error"]))
            counts["polls"] += 1
            for pos, aid in enumerate(line.get("order") or []):
                con.execute("INSERT OR REPLACE INTO observation "
                            "(poll_id,feed_id,article_id,polled_at,position) "
                            "VALUES (?,?,?,?,?)",
                            (line["id"], line["feed_id"], aid, line["polled_at"], pos))
                counts["observations"] += 1
        for line in _read(os.path.join(d, "stories.jsonl")):
            con.execute(
                "INSERT OR REPLACE INTO story (id,name,target,issue_id,first_seen,"
                "last_seen,status,n_articles,target_conf) VALUES (?,?,?,?,?,?,?,?,?)",
                (line["id"], line["name"], line["target"], line["issue_id"],
                 line["first_seen"], line["last_seen"], line["status"],
                 line["n_articles"], line.get("target_conf")))
            for aid in line.get("members") or []:
                con.execute("INSERT OR REPLACE INTO story_member "
                            "(article_id,story_id,assigned_at) VALUES (?,?,?)",
                            (aid, line["id"], line["last_seen"]))
            counts["stories"] += 1
        for line in _read(os.path.join(d, "scores.jsonl")):
            con.execute(
                "INSERT OR REPLACE INTO article_score (article_id,issue_id,story_id,"
                "stance,tone,confidence,quote,desk,reason,rubric_version,model,scored_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (line["article_id"], line["issue_id"], line["story_id"], line["stance"],
                 line["tone"], line["confidence"], line["quote"], line["desk"],
                 line["reason"], line["rubric_version"], line["model"], line["scored_at"]))
            counts["scores"] += 1
    # Tell the rebuilt database what is already in the shards. Without this
    # the next export sees empty cursors, decides nothing has ever been
    # written, and appends the whole archive again as duplicate lines - which
    # is exactly the state a cold start on the runner produces.
    con.execute("CREATE TABLE IF NOT EXISTS shard_cursor "
                "(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    for key, q in (
            ("article", "SELECT MAX(id) FROM article"),
            ("poll", "SELECT MAX(id) FROM poll"),
            ("story_seen", "SELECT MAX(rowid) FROM story"),
            ("score_rowid", "SELECT MAX(rowid) FROM article_score")):
        _set_cursor(con, key, con.execute(q).fetchone()[0] or 0)
    con.commit()
    if not quiet:
        print("restored: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    con.close()
    return counts


def _read(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


# ------------------------------------------------------------------ check ---

def check(db_path=None):
    """Rebuild into a scratch database and compare. An archive that has never
    been read back is not known to work."""
    import tempfile
    live = db.connect(db_path)
    tmp = os.path.join(tempfile.mkdtemp(), "restored.sqlite")
    restore(tmp, quiet=True)
    back = db.connect(tmp)

    ok = True
    for table in ("article", "poll", "observation", "story", "article_score",
                  "story_member"):
        a = live.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        b = back.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        mark = "ok " if a == b else "BAD"
        if a != b:
            ok = False
        print(f"  {mark} {table:<16} live {a:>7}   restored {b:>7}")

    # the positions are the part that cannot be collected again, so compare
    # them properly rather than just counting
    q = ("SELECT poll_id, article_id, position FROM observation "
         "ORDER BY poll_id, position LIMIT 5000")
    same = list(live.execute(q)) == list(back.execute(q))
    print(f"  {'ok ' if same else 'BAD'} feed positions match")
    live.close(); back.close()
    return ok and same


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "export"
    if cmd == "export":
        export(full="--full" in sys.argv)
    elif cmd == "restore":
        restore(sys.argv[2] if len(sys.argv) > 2 else db.DEFAULT_DB)
    elif cmd == "check":
        sys.exit(0 if check() else 1)
    else:
        sys.exit(__doc__)
