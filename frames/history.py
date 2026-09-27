"""Everything the interactive globe needs to play the news back, day by day.

    python3 -m frames.history          write app/history.js

frame.json is one moment. This is every day since collection began: for each
story, how much of the press it had each day, how each paper was covering it,
how they were framing it, and the headlines themselves - so the page can open
a story, open a paper inside it, and read what was actually written.

A day is a UK calendar day. A story's size on a day is not just that day's new
articles: a story that led yesterday is still on the front pages this morning,
so yesterday counts for half and the day before for a quarter. Without that
the globe flickers - every story vanishes at midnight and returns at six.

Written as a script that sets one global rather than as JSON, so the page can
be opened straight from disk as well as from a server.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import db, newsy               # noqa: E402
from score.run import desk_from_url        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "app", "history.js")
UK = ZoneInfo("Europe/London")

# what yesterday and the day before still count for today
CARRY = (1.0, 0.5, 0.25)

# How many stories a day can put on the globe. The union over all days is
# what the page carries; any one day shows only those with a real share.
TOP_PER_DAY = 40

# headlines kept per story per paper - the most prominent first
HEADS = 12

# outlet ids to the names the page's press map uses
NAMES = {"bbc": "BBC", "c4": "C4", "express": "Express", "ft": "FT",
         "guardian": "Guardian", "i": "i", "independent": "Independent",
         "itv": "ITV", "mail": "Mail", "metro": "Metro", "mirror": "Mirror",
         "sky": "Sky", "sun": "Sun", "telegraph": "Telegraph", "times": "Times"}

DESK = {"news": "News", "comment": "Comment", "leader": "Leader",
        "analysis": "Analysis", "feature": "Analysis"}
CONF_W = {"high": 1.0, "medium": 0.7, "low": 0.35}


def uk_day(stamp):
    t = datetime.fromisoformat(stamp)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(UK).date()


def smooth(counts, n_days):
    """Daily counts -> daily presence, with the carry-over above."""
    out = [0.0] * n_days
    for d, c in counts.items():
        for k, w in enumerate(CARRY):
            if d + k < n_days:
                out[d + k] += c * w
    return out


def carry_stance(pairs, n_days):
    """[(day, stance, weight)] -> one stance per day.

    Smoothed the same way as size, and carried forward through days with no
    scored article, because a paper that said nothing new today has not
    changed its line. None until the first scored article."""
    num, den = [0.0] * n_days, [0.0] * n_days
    for d, s, w in pairs:
        for k, cw in enumerate(CARRY):
            if d + k < n_days:
                num[d + k] += s * w * cw
                den[d + k] += w * cw
    out, last = [], None
    for d in range(n_days):
        if den[d] > 0:
            last = num[d] / den[d]
        out.append(None if last is None else round(last, 2))
    return out


def build(db_path=None, out=OUT, today=None):
    con = db.connect(db_path)
    rows = [dict(r) for r in con.execute(
        "SELECT s.id AS sid, s.name, s.target, s.issue_id, "
        "       a.id AS aid, a.outlet_id, a.title, a.url_canon, a.first_seen, "
        "       sc.stance, sc.confidence, sc.desk, sc.reason "
        "FROM story s "
        "JOIN story_member m ON m.story_id = s.id "
        "JOIN article a ON a.id = m.article_id "
        "LEFT JOIN article_score sc ON sc.article_id = a.id "
        "     AND sc.issue_id IS s.issue_id "
        # tagged stories only: until then the name is one paper's headline
        "WHERE s.target IS NOT NULL AND s.target != '' "
        "AND s.status != 'merged'")]
    rows = [r for r in rows if newsy.is_news_row(r) and r["outlet_id"] in NAMES]
    if not rows:
        print("no tagged stories yet")
        return None

    # how near the top of the paper's own front-page feed each article got -
    # the lowest position it was ever seen at
    best = {r["article_id"]: r["pos"] for r in con.execute(
        "SELECT o.article_id, MIN(o.position) AS pos FROM observation o "
        "JOIN feed f ON f.id = o.feed_id WHERE f.kind = 'top' "
        "GROUP BY o.article_id")}

    first = min(uk_day(r["first_seen"]) for r in rows)
    last = today or datetime.now(UK).date()
    n_days = (last - first).days + 1
    days = [(first + timedelta(d)).isoformat() for d in range(n_days)]

    stories = {}
    for r in rows:
        d = (uk_day(r["first_seen"]) - first).days
        if d < 0 or d >= n_days:
            continue
        st = stories.setdefault(r["sid"], {
            "id": r["sid"], "name": r["name"], "target": r["target"],
            "issue": r["issue_id"], "n": {}, "by": {}, "stance": [], "heads": {}})
        o = NAMES[r["outlet_id"]]
        st["n"][d] = st["n"].get(d, 0) + 1
        b = st["by"].setdefault(o, {"n": {}, "stance": []})
        b["n"][d] = b["n"].get(d, 0) + 1
        if r["stance"] is not None:
            w = CONF_W.get(r["confidence"], 0.7)
            st["stance"].append((d, r["stance"], w))
            b["stance"].append((d, r["stance"], w))
        pos = best.get(r["aid"])
        st["heads"].setdefault(o, []).append({
            "t": r["title"], "d": d,
            "s": r["stance"], "c": r["confidence"],
            "k": DESK.get(r["desk"] or desk_from_url(r["url_canon"]), "News"),
            # prominence: front of the paper's own feed, near it, or further down
            "p": 3 if pos is not None and pos < 5 else 2 if pos is not None and pos < 15 else 1,
            "u": r["url_canon"], "r": r["reason"]})

    # keep a story only if it was among the day's biggest on at least one day
    # and more than one paper ran it
    vol = {sid: smooth(st["n"], n_days) for sid, st in stories.items()}
    keep = set()
    for d in range(n_days):
        ranked = sorted((sid for sid in stories if vol[sid][d] > 0),
                        key=lambda sid: -vol[sid][d])
        keep.update(ranked[:TOP_PER_DAY])
    keep = [sid for sid in keep if len(stories[sid]["by"]) >= 2]
    keep.sort(key=lambda sid: -sum(vol[sid]))

    # The timeline ends on the last day with anything on it. Normally that is
    # today; after a gap in collection it is not, and an empty present would
    # leave the globe with nothing to draw.
    while n_days > 1 and not any(vol[sid][n_days - 1] > 0 for sid in keep):
        n_days -= 1
    days = days[:n_days]
    for sid in keep:
        vol[sid] = vol[sid][:n_days]

    # Days nothing was collected - the job was down, not the news. Drawn as
    # zero they read as the press falling silent, and the carry-over above
    # makes them worse: a half-strength echo of the day before, then nothing.
    # So each is bridged by a straight line between the collected days either
    # side, and listed, so the page can say plainly that it was not collected.
    polled = set()
    for (t,) in con.execute("SELECT polled_at FROM poll WHERE error IS NULL AND n_items > 0"):
        d = (uk_day(t) - first).days
        if 0 <= d < n_days:
            polled.add(d)
    gaps = [d for d in range(n_days) if d not in polled]

    def bridge(series):
        out = list(series)
        for d in gaps:
            a = max((k for k in range(d) if k not in gaps), default=None)
            b = min((k for k in range(d + 1, n_days) if k not in gaps), default=None)
            if a is not None and b is not None:
                out[d] = out[a] + (out[b] - out[a]) * (d - a) / (b - a)
            elif a is not None:
                out[d] = out[a]
        return out

    out_stories = []
    for sid in keep:
        st = stories[sid]
        heads = {}
        for o, hs in st["heads"].items():
            hs.sort(key=lambda h: (-h["p"], -h["d"]))
            heads[o] = hs[:HEADS]
        out_stories.append({
            "id": sid, "name": st["name"], "target": st["target"], "issue": st["issue"],
            "vol": [round(v, 2) for v in bridge(vol[sid])],
            "stance": carry_stance(st["stance"], n_days),
            "outlets": {o: {"vol": [round(v, 2) for v in bridge(smooth(b["n"], n_days))],
                            "stance": carry_stance(b["stance"], n_days)}
                        for o, b in sorted(st["by"].items())},
            "heads": heads,
        })

    # Events for the timeline: on each day, the story that rose furthest into
    # the day's coverage, captioned with what a broadcaster said about it (the
    # least partisan wording available) or, failing that, any paper.
    events = []
    for d in range(1, n_days):
        if d in gaps or d - 1 in gaps:
            continue                     # a rise across a gap is the gap, not news
        tot = sum(s["vol"][d] for s in out_stories) or 1
        prev = sum(s["vol"][d - 1] for s in out_stories) or 1
        rise = sorted(out_stories, key=lambda s: -(s["vol"][d] / tot - s["vol"][d - 1] / prev))
        s = rise[0]
        if s["vol"][d] / tot - s["vol"][d - 1] / prev < 0.02:
            continue
        pick = None
        for o in ("BBC", "Sky", "C4", "ITV"):
            pick = next((h for h in s["heads"].get(o, []) if h["d"] == d), None)
            if pick:
                break
        if not pick:
            pick = next((h for hs in s["heads"].values() for h in hs if h["d"] == d), None)
        events.append({"d": d, "id": s["id"],
                       "t": pick["t"] if pick else s["name"]})

    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": days,
        "outlets": sorted(NAMES[r["id"]] for r in con.execute(
            "SELECT DISTINCT o.id FROM outlet o JOIN feed f ON f.outlet_id = o.id "
            "WHERE f.active = 1") if r["id"] in NAMES),
        "stories": out_stories,
        "events": events,
        "gaps": gaps,
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("/* written by frames/history.py - do not edit */\n")
        f.write("window.FPM_RAW = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    size = os.path.getsize(out)
    print(f"{len(out_stories)} stories over {n_days} days ({days[0]} to {days[-1]}), "
          f"{len(events)} events, {len(gaps)} days not collected, "
          f"{size / 1e3:.0f} KB -> {os.path.relpath(out, ROOT)}")
    con.close()
    return data


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
