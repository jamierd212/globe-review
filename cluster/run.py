"""Turn the articles we have collected into stories.

    python3 -m cluster.run                 group the last 72 hours
    python3 -m cluster.run --hours 24
    python3 -m cluster.run --threshold 0.3 --dry

Runs after the poll. Groups the recent articles, matches each group against
the stories already in the database so a story keeps its identity overnight,
and writes the result back.

Nothing here needs a model or a key. The vectoriser counts words; swap in
sentence embeddings later and compare the two on the same day's articles.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import db                                    # noqa: E402
from cluster import embed, group as G, identity as I     # noqa: E402

WINDOW_HOURS = 72


def _day_index(stamp):
    """Days since the epoch, so story lifetimes are counted in whole days."""
    t = datetime.fromisoformat(stamp)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (t - datetime(2020, 1, 1, tzinfo=timezone.utc)).days


def load_articles(con, hours):
    cut = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds")
    return [dict(r) for r in con.execute(
        "SELECT id, outlet_id, title, standfirst, published_at, first_seen "
        "FROM article WHERE COALESCE(published_at, first_seen) >= ? "
        "ORDER BY COALESCE(published_at, first_seen)", (cut,))]


def load_stories(con):
    out = []
    for r in con.execute(
            "SELECT id, centroid, first_seen, last_seen, status, n_articles "
            "FROM story WHERE status != 'closed'"):
        if not r["centroid"]:
            continue
        vec = [float(x) for x in r["centroid"].decode().split(",")]
        out.append({"id": r["id"], "centroid": vec,
                    "first_day": _day_index(r["first_seen"]),
                    "last_day": _day_index(r["last_seen"]),
                    "status": r["status"], "n_articles": r["n_articles"]})
    return out


def name_story(members, articles):
    """A placeholder name until a model writes a proper one.

    Takes the words that most of the coverage agrees on, in the order the
    longest headline uses them. Crude, but it takes the wording from what
    everybody is saying rather than from one paper's framing, which is the
    rule that matters. See naming.md.
    """
    by_id = {a["id"]: a for a in articles}
    titles = [by_id[m]["title"] for m in members if m in by_id]
    if not titles:
        return "untitled"
    sets = [set(embed.tokens(t)) for t in titles]
    common = set.intersection(*sets) if len(sets) > 1 else sets[0]
    common = {w for w in common if "_" not in w}
    if not common:
        common = max(sets, key=len)
    longest = max(titles, key=len)
    words = [w for w in longest.split() if
             "".join(c for c in w.lower() if c.isalnum()) in common]
    name = " ".join(words[:8]) or longest[:60]
    return name[0].upper() + name[1:] if name else "untitled"


def run(hours=WINDOW_HOURS, threshold=None, dry=False, db_path=None):
    con = db.connect(db_path)
    arts = load_articles(con, hours)
    if not arts:
        print("no articles in that window - run `python3 -m ingest.run poll` first")
        return 1

    texts = [(a["title"] + " " + (a["standfirst"] or "")).strip() for a in arts]
    vecs = embed.embed_batch(texts)
    items = [{"id": a["id"], "outlet_id": a["outlet_id"], "vec": v,
              "published_at": a["published_at"] or a["first_seen"]}
             for a, v in zip(arts, vecs)]

    th = threshold if threshold is not None else G.THRESHOLD
    stories, pending = G.group(items, threshold=th)
    sp = G.spread(stories)

    print(f"{len(arts)} articles in the last {hours}h")
    print(f"  {len(stories)} stories (2+ outlets), {len(pending)} still "
          f"single-outlet")
    if sp:
        print(f"  biggest {sp['biggest']} articles ({sp['biggest_share']:.0%} of "
              f"the clustered total), median {sp['median_size']}, "
              f"median outlets {sp['median_outlets']}")

    today = _day_index(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    prior = load_stories(con)
    assigned, updated = I.assign(
        [{"key": s["key"], "centroid": s["centroid"], "n": s["n"]} for s in stories],
        prior, today)

    lt = I.lifetimes(updated)
    if lt:
        print(f"  story lifetimes: median {lt['median']}d, longest "
              f"{lt['max']}d, {lt['singletons']} one-day")

    print("\nbiggest stories:")
    for s in stories[:12]:
        print(f"  {s['n']:>3} articles, {len(s['outlets']):>2} outlets  "
              f"{name_story(s['members'], arts)[:62]}")
        print(f"       {', '.join(s['outlets'])}")

    if dry:
        print("\n(dry run - nothing written)")
        return 0

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_key = {s["key"]: s for s in stories}
    for st in updated:
        if st["status"] == "merged":
            continue
        key = next((k for k, v in assigned.items() if v == st["id"]), None)
        grp = by_key.get(key)
        name = name_story(grp["members"], arts) if grp else None
        cen = ",".join(f"{x:.5f}" for x in st["centroid"]).encode()
        row = con.execute("SELECT id FROM story WHERE id=?", (st["id"],)).fetchone()
        if row:
            con.execute(
                "UPDATE story SET last_seen=?, status=?, centroid=?, n_articles=? "
                + (", name=?" if name else "") + " WHERE id=?",
                ((now, st["status"], cen, st.get("n_articles", 0))
                 + ((name,) if name else ()) + (st["id"],)))
        else:
            con.execute(
                "INSERT INTO story (id, name, target, issue_id, first_seen, "
                "last_seen, status, centroid, n_articles, target_conf) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (st["id"], name or "untitled", "", None, now, now,
                 st["status"], cen, st.get("n_articles", 0), "generated"))
        if grp:
            for m in grp["members"]:
                con.execute(
                    "INSERT INTO story_member (article_id, story_id, assigned_at) "
                    "VALUES (?,?,?) ON CONFLICT(article_id) DO UPDATE SET "
                    "story_id=excluded.story_id, assigned_at=excluded.assigned_at",
                    (m, st["id"], now))
    con.commit()
    live = con.execute("SELECT COUNT(*) FROM story WHERE status='live'").fetchone()[0]
    print(f"\nwrote {live} live stories")
    con.close()
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    def opt(name, cast, default):
        return cast(a[a.index(name) + 1]) if name in a else default
    sys.exit(run(hours=opt("--hours", int, WINDOW_HOURS),
                 threshold=opt("--threshold", float, None),
                 dry="--dry" in a))
