"""Turning the rubric into instructions for the model.

The rubric is one file and it is the authority. This reads it rather than
restating it, so the humans labelling the gold set and the model scoring the
archive cannot drift apart — which they would, silently, within a month of
somebody editing one of them.

Two rules are enforced here rather than trusted:

  blind     the scorer never sees which paper wrote it, and never sees the
            taxonomy's `expect` field. If either reached the prompt, every
            score would be partly a guess from the masthead, and the direction
            check would become a test of its own assumptions.
  target    the target is stated in the prompt every single time. The one
            error that inverts a score rather than blurring it is scoring
            "net zero rollback" as though it were about net zero.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUBRIC = os.path.join(ROOT, "score", "rubric.md")


def rubric_text():
    with open(RUBRIC, encoding="utf-8") as f:
        return f.read()


def rubric_version():
    m = re.search(r"^#\s*Scoring rubric\s+(\S+)", rubric_text(), re.M)
    return m.group(1) if m else "unversioned"


SYSTEM = """You score British newspaper coverage against a written rubric.

The rubric follows in full. Follow it exactly; where your judgement and the \
rubric disagree, the rubric wins.

You will not be told which newspaper wrote the item, and you must not guess. \
Score only the words in front of you.

Reply with JSON and nothing else:
{"stance": -2..2 or null, "confidence": "high"|"medium"|"low",
 "quote": "verbatim span from the input, or null", "tone": -2..2,
 "reason": "one short clause"}

--- RUBRIC ---
%s"""


def system_prompt():
    return SYSTEM % rubric_text()


def item_prompt(title, standfirst, issue_name, target, desk=None):
    """The per-item message. Deliberately small: headline, standfirst, and what
    a favourable score would be favourable towards."""
    body = [f"ISSUE: {issue_name}",
            f"TARGET (score favourability toward this, and nothing else): {target}"]
    if desk:
        body.append(f"DESK: {desk}   (record it; do not adjust the score for it)")
    body += ["", "HEADLINE: " + title]
    if standfirst:
        body.append("STANDFIRST: " + standfirst)
    return "\n".join(body)


TAG_SYSTEM = """You name British news stories and file them under a fixed list \
of standing subjects.

Reply with JSON and nothing else:
{"name": "<short neutral name for the story>",
 "issue_id": "<id from the list, or null>", "confidence": "high"|"medium"|"low",
 "target": "<what a favourable score would be favourable TOWARD>"}

Naming rules. You are given several papers' headlines for the same story; the
name goes on all of them, so it must belong to none of them.

- **Say what happened, in four to seven words.** "Reform donations reach £72m",
  not "How could Reform UK spend its £72m of new billionaire money?".
- **Take the wording every paper shares.** Never lift one paper's headline. If
  one calls it a crackdown and another a climbdown, the name uses neither.
- **No word that takes a side.** Not crackdown, climbdown, chaos, fury,
  blow, blasted, slammed, humiliation, triumph. Those are the words being
  measured; putting them in the label means measuring your own label.
- **Name the event, not the field.** "Sanctions on Israeli settlements" is a
  story; "Middle East" is a subject. If the name would still be true next month
  whatever happens, it is too broad.
- **No question marks, no colons introducing a paper's angle, no quotes from
  one paper.** Plain statement of the event.
- It has to fit inside a small shape, so keep it short.
- **A name is always required.** Never return null for the name, whatever you
  decide about filing. A story with no standing subject is still a story, still
  goes on the globe, and still needs something written on it. Foreign news and
  one-off events are the common case here.

Filing rules:

- Pick the single best fit. If nothing fits, return null. Sport, showbiz, \
lifestyle, puzzles and service journalism should return null.
- When you pick a subject, copy its target verbatim.
- When you return null, write a target for this story yourself: name the \
specific thing being judged, not the field. For "UK sanctions on Israeli \
settlements" the target is "the sanctions", not "the Middle East". Getting \
this wrong inverts every score on the story rather than blurring it.
- The target must be a thing that can be favoured or opposed, never a topic."""


def tag_prompt(name, headlines, issues):
    """One story, the standing list, and a few of its headlines.

    The `expect` field is stripped: it is a prior held for testing, and letting
    it near the model would make the direction check meaningless.
    """
    lines = ["SUBJECTS:"]
    for i in issues:
        lines.append(f"  {i['id']}: {i['name']} - target: {i['target']}")
    lines += ["", f"STORY: {name}", "HEADLINES:"]
    for h in headlines[:8]:
        lines.append("  - " + h)
    return "\n".join(lines)
