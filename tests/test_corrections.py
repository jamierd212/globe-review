"""Hand corrections: applied every run, so they must be safe to repeat, must
never touch the wrong story, and must keep scores made after the fix."""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingest import db
from score import run as R

FIX = {"stories": [{"id": 1, "name": "Letby inquiry findings released",
                    "issue_id": None, "target": "the Thirlwall inquiry and its findings",
                    "made": "2026-09-24T08:45:00+00:00"}]}


def setup(name="Letby inquiry findings released"):
    con = db.connect(":memory:")
    con.execute("INSERT INTO story (id, name, issue_id, target, first_seen, last_seen, "
                "status) VALUES (1, ?, 'nhs-pressure', 'the current state of the NHS', "
                "'2026-09-15', '2026-09-17', 'live')", (name,))
    for aid, when in ((1, "2026-09-16T00:00:00+00:00"), (2, "2026-09-25T00:00:00+00:00")):
        con.execute("INSERT INTO article_score (article_id, issue_id, story_id, stance, "
                    "rubric_version, model, scored_at) VALUES (?,?,1,-1,'v1','m',?)",
                    (aid, "nhs-pressure" if aid == 1 else None, when))
    con.commit()
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(FIX, f); f.close()
    return con, f.name


def test_retargets_and_drops_only_old_scores():
    con, path = setup()
    assert R.apply_corrections(con, path) == 1
    s = con.execute("SELECT issue_id, target FROM story WHERE id=1").fetchone()
    assert (s["issue_id"], s["target"]) == (None, "the Thirlwall inquiry and its findings")
    left = [r[0] for r in con.execute("SELECT article_id FROM article_score")]
    assert left == [2], "a score made after the correction was thrown away"


def test_second_run_changes_nothing():
    con, path = setup()
    R.apply_corrections(con, path)
    assert R.apply_corrections(con, path) == 0


def test_wrong_story_is_left_alone():
    con, path = setup(name="Something else entirely")
    assert R.apply_corrections(con, path) == 0
    assert con.execute("SELECT COUNT(*) FROM article_score").fetchone()[0] == 2


def test_the_real_file_is_well_formed():
    fixes = json.load(open(R.CORRECTIONS, encoding="utf-8"))["stories"]
    for c in fixes:
        assert {"id", "name", "issue_id", "target", "made", "why"} <= set(c), c
        assert c["target"].strip(), "a correction must name a target"


if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", n)
            except AssertionError as e:
                fails += 1
                print("FAIL", n, e)
    sys.exit(1 if fails else 0)
