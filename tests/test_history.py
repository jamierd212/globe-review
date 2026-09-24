"""The playback history: days, carry-over, and the end of the timeline."""
import os, sys, tempfile, json
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingest import db
from frames import history as H


def _db():
    path = os.path.join(tempfile.mkdtemp(), "h.sqlite")
    con = db.connect(path)
    for oid, lean in (("bbc", -0.05), ("mail", 0.68)):
        con.execute("INSERT INTO outlet (id,name,leaning,market,weight) VALUES (?,?,?,0,1)",
                    (oid, oid, lean))
    con.execute("INSERT INTO story (id,name,target,issue_id,first_seen,last_seen,status) "
                "VALUES (1,'Village referendum on asylum camp','the camp',NULL,"
                "'2026-09-13T08:00:00+00:00','2026-09-14T08:00:00+00:00','live')")
    arts = [(1, "bbc", "2026-09-13T08:00:00+00:00", -1.0),
            (2, "mail", "2026-09-13T09:00:00+00:00", -2.0),
            (3, "mail", "2026-09-14T09:00:00+00:00", None)]
    for aid, oid, t, stance in arts:
        con.execute("INSERT INTO article (id,outlet_id,url_canon,title,first_seen,title_sig) "
                    "VALUES (?,?,?,?,?,?)", (aid, oid, f"https://x/news/{aid}",
                                             f"Village votes on camp {aid}", t, f"s{aid}"))
        con.execute("INSERT INTO story_member (article_id,story_id,assigned_at) VALUES (?,1,?)",
                    (aid, t))
        if stance is not None:
            con.execute("INSERT INTO article_score (article_id,issue_id,story_id,stance,"
                        "confidence,rubric_version,model,scored_at) VALUES (?,NULL,1,?,"
                        "'high','v1','m',?)", (aid, stance, t))
    con.commit(); con.close()
    return path


def _load(out):
    txt = open(out).read()
    return json.loads(txt[txt.index("= ") + 2:].rstrip().rstrip(";"))


def test_days_carry_and_stance():
    out = os.path.join(tempfile.mkdtemp(), "h.js")
    H.build(_db(), out=out, today=date(2026, 9, 14))
    d = _load(out)
    assert d["days"] == ["2026-09-13", "2026-09-14"]
    s = d["stories"][0]
    # day two: one new article plus half of day one's two
    assert s["vol"] == [2.0, 2.0], s["vol"]
    # the Mail said nothing scored on day two; its line carries forward
    assert s["outlets"]["Mail"]["stance"][1] == -2.0
    assert s["outlets"]["BBC"]["vol"] == [1.0, 0.5]


def test_timeline_ends_on_the_last_day_with_news():
    """An empty present would leave the globe nothing to draw."""
    out = os.path.join(tempfile.mkdtemp(), "h.js")
    H.build(_db(), out=out, today=date(2026, 9, 25))
    d = _load(out)
    assert d["days"][-1] == "2026-09-16", d["days"][-1]   # carry lasts two days


if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try:
                fn(); print("ok  ", n)
            except AssertionError as e:
                fails += 1; print("FAIL", n, e)
    sys.exit(1 if fails else 0)
