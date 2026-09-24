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


STORY_FIELDS = ("name", "target", "issue_id", "first_seen", "last_seen",
                "status", "target_conf")
SCORE_FIELDS = ("story_id", "stance", "tone", "confidence", "quote", "desk",
                "reason", "rubric_version", "model", "scored_at")


def _story_sig(r):
    """What counts as a change to a story. last_seen is compared by the DAY:
    it moves every hour a story is running, and a line per story per hour
    would make this the biggest file in the archive for no gain - story
    lifetimes are counted in whole days. n_articles is left out because it is
    derived, and rebuilt from the memberships on restore."""
    if r is None:
        return None
    return tuple((r.get(f) or "")[:10] if f == "last_seen" else r.get(f)
                 for f in STORY_FIELDS)


def _score_sig(r):
    return None if r is None else tuple(r.get(f) for f in SCORE_FIELDS)


def _archived_state():
    """Stories, memberships and scores as the shards currently have them,
    replayed in the order they were written."""
    stories, members, scores = {}, {}, {}
    for month in sorted(os.listdir(SHARDS)) if os.path.isdir(SHARDS) else []:
        d = os.path.join(SHARDS, month)
        if not os.path.isdir(d):
            continue
        for line in _read(os.path.join(d, "stories.jsonl")):
            stories[line["id"]] = line
            for aid in line.get("members") or []:      # the first format
                members[aid] = line["id"]
        for line in _read(os.path.join(d, "members.jsonl")):
            members[line["article_id"]] = line["story_id"]
        for line in _read(os.path.join(d, "scores.jsonl")):
            k = (line["article_id"], line["issue_id"])
            if line.get("deleted"):
                scores.pop(k, None)
            else:
                scores[k] = line
    return stories, members, scores


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

    # Stories, memberships and scores are different: they CHANGE. A story
    # gains articles, goes quiet, gets a corrected target; a score is redone.
    # The first version exported each row once, when it was new, so every
    # later change was silently missing - found when an article joining an
    # existing story never reached the archive and the rebuilt database came
    # back with 1,346 fewer memberships than the live one.
    #
    # So these are exported by comparison: replay what the archive already
    # says, compare it with the database, and append a line for anything that
    # differs - including a deletion line for a row that has gone. Replaying
    # is cheap (these files are small next to articles and polls) and it
    # cannot drift, because there is no cursor to lose.
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    month = _month(stamp)
    had_stories, had_members, had_scores = _archived_state()

    rows = []
    for r in con.execute(f"SELECT id, n_articles, {', '.join(STORY_FIELDS)} "
                         "FROM story ORDER BY id"):
        r = dict(r)
        if _story_sig(had_stories.get(r["id"])) != _story_sig(r):
            r["exported_at"] = stamp
            rows.append(r)
    written["stories"] = _append(month, "stories.jsonl", rows)

    rows = []
    for r in con.execute("SELECT article_id, story_id FROM story_member "
                         "ORDER BY article_id"):
        if had_members.get(r["article_id"]) != r["story_id"]:
            rows.append({"article_id": r["article_id"], "story_id": r["story_id"],
                         "at": stamp})
    written["members"] = _append(month, "members.jsonl", rows)

    rows, live_keys = [], set()
    for r in con.execute(f"SELECT article_id, issue_id, {', '.join(SCORE_FIELDS)} "
                         "FROM article_score ORDER BY rowid"):
        r = dict(r)
        k = (r["article_id"], r["issue_id"])
        live_keys.add(k)
        if _score_sig(had_scores.get(k)) != _score_sig(r):
            rows.append(r)
    for k in sorted(set(had_scores) - live_keys, key=lambda k: (k[0], k[1] or "")):
        rows.append({"article_id": k[0], "issue_id": k[1], "deleted": True,
                     "at": stamp})
    written["scores"] = _append(month, "scores.jsonl", rows)
    written = {k: v for k, v in written.items() if v}

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
                 line.get("n_articles") or 0, line.get("target_conf")))
            for aid in line.get("members") or []:       # the first format
                con.execute("INSERT OR REPLACE INTO story_member "
                            "(article_id,story_id,assigned_at) VALUES (?,?,?)",
                            (aid, line["id"], line["last_seen"]))
            counts["stories"] += 1
        for line in _read(os.path.join(d, "members.jsonl")):
            con.execute("INSERT OR REPLACE INTO story_member "
                        "(article_id,story_id,assigned_at) VALUES (?,?,?)",
                        (line["article_id"], line["story_id"], line["at"]))
        for line in _read(os.path.join(d, "scores.jsonl")):
            if line.get("deleted"):
                con.execute("DELETE FROM article_score WHERE article_id=? "
                            "AND issue_id IS ?", (line["article_id"], line["issue_id"]))
                continue
            con.execute(
                "DELETE FROM article_score WHERE article_id=? AND issue_id IS ?",
                (line["article_id"], line["issue_id"]))
            con.execute(
                "INSERT INTO article_score (article_id,issue_id,story_id,"
                "stance,tone,confidence,quote,desk,reason,rubric_version,model,scored_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (line["article_id"], line["issue_id"], line["story_id"], line["stance"],
                 line["tone"], line["confidence"], line["quote"], line["desk"],
                 line["reason"], line["rubric_version"], line["model"], line["scored_at"]))
            counts["scores"] += 1
    # derived, so rebuilt rather than trusted
    con.execute("UPDATE story SET n_articles = (SELECT COUNT(*) FROM story_member m "
                "WHERE m.story_id = story.id)")
    # Tell the rebuilt database what is already in the shards. Without this
    # the next export sees empty cursors, decides nothing has ever been
    # written, and appends the whole archive again as duplicate lines - which
    # is exactly the state a cold start on the runner produces.
    con.execute("CREATE TABLE IF NOT EXISTS shard_cursor "
                "(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    for key, q in (
            ("article", "SELECT MAX(id) FROM article"),
            ("poll", "SELECT MAX(id) FROM poll")):
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

def _mark(name, good, detail):
    print(f"  {'ok ' if good else 'BAD'} {name:<16} {detail}")
    return good


def _diff(a, b):
    if a == b:
        return ""
    k = sorted(set(a) ^ set(b) | {x for x in a if x in b and a[x] != b[x]}, key=str)
    return f"   ({len(k)} differ, e.g. {k[:3]})"


def check(db_path=None):
    """Rebuild into a scratch database and compare. An archive that has never
    been read back is not known to work."""
    import tempfile
    live = db.connect(db_path)
    tmp = os.path.join(tempfile.mkdtemp(), "restored.sqlite")
    restore(tmp, quiet=True)
    back = db.connect(tmp)

    ok = True
    for table in ("article", "poll", "observation"):
        x = live.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        y = back.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        ok &= _mark(table, x == y, f"live {x:>7}   restored {y:>7}")

    # the tables that change are compared row by row: counting them is how
    # the missing memberships got through
    def rows(con, q, sig):
        return {tuple(r[:2]) if sig is _score_sig else r[0]: sig(dict(r))
                for r in con.execute(q)}
    qs = f"SELECT id, {', '.join(STORY_FIELDS)} FROM story"
    a_, b_ = rows(live, qs, _story_sig), rows(back, qs, _story_sig)
    ok &= _mark("story", a_ == b_, f"live {len(a_):>7}   restored {len(b_):>7}"
                + _diff(a_, b_))
    qm = "SELECT article_id, story_id FROM story_member"
    a_ = {r[0]: r[1] for r in live.execute(qm)}
    b_ = {r[0]: r[1] for r in back.execute(qm)}
    ok &= _mark("story_member", a_ == b_, f"live {len(a_):>7}   restored {len(b_):>7}"
                + _diff(a_, b_))
    qc = f"SELECT article_id, issue_id, {', '.join(SCORE_FIELDS)} FROM article_score"
    a_, b_ = rows(live, qc, _score_sig), rows(back, qc, _score_sig)
    ok &= _mark("article_score", a_ == b_, f"live {len(a_):>7}   restored {len(b_):>7}"
                + _diff(a_, b_))

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
