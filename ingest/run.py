"""The hourly job, and the one-off checks that come before it.

    python3 -m ingest.run check     # are these feed urls real? run this FIRST
    python3 -m ingest.run poll      # one pass over every feed
    python3 -m ingest.run report    # the daily go/no-go CSV

`check` exists because outlets.json is a list of educated guesses. Verify it
before you trust a single number that comes out of the other two.
"""

import json
import os
import sys
from datetime import datetime, timezone

from . import db, feeds, dedup, newsy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "outlets.json")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_spec():
    with open(SPEC, encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------- check ----

def cmd_check():
    spec = load_spec()
    bad = 0
    skipped = [o["id"] for o in spec["outlets"] if o.get("excluded")]
    print(f"{'outlet':<14} {'kind':<8} {'status':<9} {'items':<6} {'dated':<6} url")
    for o in spec["outlets"]:
        if o.get("excluded"):
            continue
        for f in o["feeds"]:
            status, body, _ = feeds.fetch(f["url"])
            n = dated = 0
            note = ""
            if body:
                try:
                    items = feeds.parse(body)
                    n = len(items)
                    dated = sum(1 for i in items if i["published_at"])
                except Exception as e:
                    note = f"  PARSE FAIL: {type(e).__name__}"
            if status != 200 or n == 0:
                bad += 1
                note = note or "  <-- fix outlets.json"
            print(f"{o['id']:<14} {f['kind']:<8} {str(status):<9} {n:<6} {dated:<6} "
                  f"{f['url']}{note}")
    print(f"\n{bad} feed(s) need attention.")
    if skipped:
        print(f"not collected (see the `excluded` note in outlets.json): "
              f"{', '.join(skipped)}")
    print("Also worth knowing before you rely on position: a 'top' feed whose")
    print("items are strictly newest-first carries no editorial signal. Poll one")
    print("for a day and see whether the order ever changes without a new item.")
    return 1 if bad else 0


# ------------------------------------------------------------------ poll ----

def cmd_poll(db_path=None):
    spec = load_spec()
    con = db.connect(db_path)
    db.sync_outlets(con, spec)
    stamp = now()
    totals = {"feeds": 0, "items": 0, "new": 0, "failed": 0}

    for row in con.execute("SELECT id, outlet_id, url FROM feed ORDER BY id"):
        feed_id, outlet_id, url = row["id"], row["outlet_id"], row["url"]
        status, body, _ = feeds.fetch(url)
        items, err = [], None
        if body:
            try:
                items = feeds.parse(body)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
        elif status != 304:
            err = f"http {status}"

        cur = con.execute(
            "INSERT INTO poll (feed_id, polled_at, http_status, n_items, error) "
            "VALUES (?,?,?,?,?)", (feed_id, stamp, str(status), len(items), err))
        poll_id = cur.lastrowid
        totals["feeds"] += 1
        if err:
            totals["failed"] += 1

        n_new = 0
        for pos, it in enumerate(items):
            if not it["url_canon"]:
                continue
            r = con.execute(
                "SELECT id FROM article WHERE outlet_id=? AND url_canon=?",
                (outlet_id, it["url_canon"])).fetchone()
            if r:
                article_id = r["id"]
            else:
                article_id = con.execute(
                    "INSERT INTO article (outlet_id, url_canon, guid, title, "
                    "standfirst, published_at, first_seen, title_sig) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (outlet_id, it["url_canon"], it["guid"], it["title"],
                     it["standfirst"], it["published_at"], stamp,
                     dedup.title_sig(it["title"]))).lastrowid
                n_new += 1
            # the row that cannot be reconstructed later
            con.execute(
                "INSERT OR IGNORE INTO observation "
                "(poll_id, feed_id, article_id, polled_at, position) VALUES (?,?,?,?,?)",
                (poll_id, feed_id, article_id, stamp, pos))

        con.execute("UPDATE poll SET n_new=? WHERE id=?", (n_new, poll_id))
        totals["items"] += len(items)
        totals["new"] += n_new
        con.commit()

    print(f"{stamp}  feeds={totals['feeds']} items={totals['items']} "
          f"new={totals['new']} failed={totals['failed']}")
    con.close()
    return 0


# ---------------------------------------------------------------- report ----

def cmd_report(db_path=None, days=1, out=None):
    """The week-one deliverable: does the premise hold?"""
    con = db.connect(db_path)
    cutoff = con.execute(
        "SELECT datetime('now', ?)", (f"-{days} day",)).fetchone()[0]

    allrows = [dict(r) for r in con.execute(
        "SELECT a.id, a.outlet_id, a.url_canon, a.title FROM article a "
        "WHERE a.first_seen >= ?", (cutoff,))]
    # Sport and showbiz are not news and are counted nowhere. They are still
    # collected, because the filter could be wrong and an article we never
    # stored is gone, but nothing downstream sees them.
    rows, notnews = newsy.split(allrows)
    groups, method = dedup.group(rows)

    con.executemany(
        "INSERT INTO dup_member (article_id, group_id, method) VALUES (?,?,?) "
        "ON CONFLICT(article_id) DO UPDATE SET group_id=excluded.group_id, "
        "method=excluded.method",
        [(aid, gid, method.get(aid, "unique")) for aid, gid in groups.items()])
    con.commit()

    per = {}
    for r in rows:
        p = per.setdefault(r["outlet_id"], {"n": 0, "dup": 0})
        p["n"] += 1
        if method.get(r["id"], "unique") != "unique":
            p["dup"] += 1

    # silent source loss: a feed that has stopped answering
    stale = [dict(r) for r in con.execute(
        "SELECT f.url, o.name, MAX(p.polled_at) AS last_ok "
        "FROM feed f JOIN outlet o ON o.id=f.outlet_id "
        "LEFT JOIN poll p ON p.feed_id=f.id AND p.error IS NULL AND p.n_items>0 "
        "GROUP BY f.id HAVING last_ok IS NULL OR last_ok < ?", (cutoff,))]

    lines = ["outlet,articles,duplicated,dup_share"]
    print(f"\n{'outlet':<14} {'articles':>9} {'dup':>6} {'dup share':>10}")
    for oid in sorted(per, key=lambda k: -per[k]["n"]):
        p = per[oid]
        share = p["dup"] / p["n"] if p["n"] else 0
        print(f"{oid:<14} {p['n']:>9} {p['dup']:>6} {share:>9.0%}")
        lines.append(f"{oid},{p['n']},{p['dup']},{share:.4f}")

    overall = dedup.duplication_rate(groups)
    print(f"\n{len(rows)} news articles in the last {days}d across {len(per)} outlets")
    print(f"({len(notnews)} sport/showbiz/lifestyle items collected and excluded)")
    print(f"agency-copy duplication: {overall:.0%} of items are a repeat of "
          f"something already seen elsewhere")
    if stale:
        print(f"\n!! {len(stale)} feed(s) with no successful poll in {days}d:")
        for s in stale:
            print(f"   {s['name']}  {s['url']}  last_ok={s['last_ok']}")
    lines.append(f"TOTAL,{len(rows)},,{overall:.4f}")

    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\nwrote {out}")
    con.close()
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        sys.exit(cmd_check())
    if cmd == "poll":
        sys.exit(cmd_poll())
    if cmd == "report":
        d = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        sys.exit(cmd_report(days=d, out=os.path.join(ROOT, "data", "daily.csv")))
    sys.exit(__doc__)
