"""Turn the articles we have collected into stories.

    python3 -m cluster.run                 group the last 72 hours
    python3 -m cluster.run --hours 24
    python3 -m cluster.run --threshold 0.3 --dry

Runs after the poll. Groups the recent articles, matches each group against
the stories already in the database so a story keeps its identity overnight,
and writes the result back.

Uses the sentence model if it is installed and falls back to word counting if
not, so it runs either way. Nothing leaves the machine and no key is needed.
The similarity threshold differs between the two and is chosen to match.
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


def name_story(members, articles, vecs_by_id=None):
    """A readable name, until a model writes a better one.

    Takes the headline nearest the middle of the cluster - the one that is most
    typical of what everybody is running. That is a real sentence rather than
    the bag of shared words the first version produced ("Trump", "£36m donor
    Ben Delo: crypto king who thrown"), and being the most central headline it
    is the least likely to carry any single paper's angle.

    It is still one paper's words, which naming.md says to avoid, so these are
    recorded as `generated` and meant to be replaced. See naming.md.
    """
    by_id = {a["id"]: a for a in articles}
    rows = [(m, by_id[m]["title"]) for m in members if m in by_id]
    if not rows:
        return "untitled"
    if vecs_by_id and len(rows) > 1:
        dim = len(next(iter(vecs_by_id.values())))
        cen = [0.0] * dim
        for m, _ in rows:
            v = vecs_by_id.get(m)
            if v:
                for i, x in enumerate(v):
                    cen[i] += x
        norm = sum(x * x for x in cen) ** 0.5 or 1.0
        cen = [x / norm for x in cen]
        best, best_sim = rows[0][1], -2.0
        for m, title in rows:
            v = vecs_by_id.get(m)
            if not v:
                continue
            sim = sum(a * b for a, b in zip(v, cen))
            if sim > best_sim:
                best, best_sim = title, sim
        title = best
    else:
        title = min(rows, key=lambda r: len(r[1]))[1]
    # drop the trailing colon-clause papers use for their own billing
    for sep in (" - ", " | "):
        if sep in title:
            title = title.split(sep)[0]
    return title[:90].strip()


def run(hours=WINDOW_HOURS, threshold=None, dry=False, db_path=None):
    con = db.connect(db_path)
    arts = load_articles(con, hours)
    if not arts:
        print("no articles in that window - run `python3 -m ingest.run poll` first")
        return 1

    texts = [(a["title"] + " " + (a["standfirst"] or "")).strip() for a in arts]
    vecs, how = embed.best_batch(texts)
    vecs_by_id = {a["id"]: v for a, v in zip(arts, vecs)}
    items = [{"id": a["id"], "outlet_id": a["outlet_id"], "vec": v,
              "published_at": a["published_at"] or a["first_seen"]}
             for a, v in zip(arts, vecs)]

    # the threshold belongs to the vectoriser, not to the project
    th = threshold if threshold is not None else G.THRESHOLDS[how]
    stories, pending = G.group(items, threshold=th)
    sp = G.spread(stories)

    print(f"{len(arts)} articles in the last {hours}h, vectorised by "
          f"{'sentence model' if how == 'sentence' else 'word counting'} "
          f"(threshold {th})")
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
              f"{name_story(s["members"], arts, vecs_by_id)[:62]}")
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
        name = name_story(grp["members"], arts, vecs_by_id) if grp else None
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
