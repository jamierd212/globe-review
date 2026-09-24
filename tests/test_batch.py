"""A slow batch must not hold the hourly job hostage.

Anthropic promises batch results within a day, and the job is killed at 45
minutes. Waiting without limit would lose that hour's articles, so the job
gives up waiting, writes the batch down, and a later run collects it.
"""
import os, sys, json
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingest import db
from score import run as R


class FakeBatches:
    def __init__(self):
        self.done, self.sent = False, []

    def create(self, requests):
        self.sent.append(requests)
        return NS(id=f"b{len(self.sent)}")

    def retrieve(self, bid):
        return NS(processing_status="ended" if self.done else "in_progress",
                  request_counts=NS(succeeded=0, processing=1, errored=0))

    def results(self, bid):
        for r in self.sent[int(bid[1:]) - 1]:
            text = json.dumps({"stance": 0.5, "tone": 0, "confidence": 0.8,
                               "quote": "q", "reason": "r"})
            msg = NS(content=[NS(type="text", text=text)],
                     usage=NS(input_tokens=10, output_tokens=5))
            yield NS(custom_id=r["custom_id"],
                     result=NS(type="succeeded", message=msg))


def setup():
    con = db.connect(":memory:")
    con.execute("INSERT INTO outlet (id, name, leaning, market, weight) VALUES ('sky', 'Sky', 0, 0.5, 1)")
    con.execute("INSERT INTO article (id, outlet_id, url_canon, title, first_seen, title_sig) "
                "VALUES (1, 'sky', 'https://x/news/1', 'A thing happened', 'now', 'sig')")
    con.commit()
    todo = [{"id": 1, "title": "A thing happened", "standfirst": "",
             "url_canon": "https://x/news/1", "story_id": None,
             "issue_id": "nhs", "target": "the NHS", "story": "A thing"}]
    fake = FakeBatches()
    return con, todo, fake, NS(messages=NS(batches=fake))


def scored(con):
    return con.execute("SELECT COUNT(*) FROM article_score").fetchone()[0]


def test_slow_batch_is_left_pending_not_waited_for():
    con, todo, fake, cli = setup()
    R.score_batched(cli, todo, "sys", "v1", con, poll_every=0, wait=0)
    assert scored(con) == 0
    pending = con.execute("SELECT COUNT(*) FROM score_batch "
                          "WHERE collected_at IS NULL").fetchone()[0]
    assert pending == 1, "the batch id was not written down"


def test_next_run_collects_it_and_does_not_send_twice():
    con, todo, fake, cli = setup()
    R.score_batched(cli, todo, "sys", "v1", con, poll_every=0, wait=0)
    # still running an hour later: nothing new is sent
    R.score_batched(cli, todo, "sys", "v1", con, poll_every=0, wait=0)
    assert len(fake.sent) == 1, "sent the same articles again"
    fake.done = True
    assert R.collect_pending(cli, con) == "clear"
    assert scored(con) == 1


def test_fast_batch_is_collected_in_the_same_run():
    con, todo, fake, cli = setup()
    fake.done = True
    R.score_batched(cli, todo, "sys", "v1", con, poll_every=0, wait=5)
    assert scored(con) == 1


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
