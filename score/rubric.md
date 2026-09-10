# Scoring rubric v1

The instruction given to every scorer, human or model. Humans label the gold
set against this first; the model prompt is generated from it. If the two ever
diverge, this file is right and the prompt is wrong.

## The unit

One `(issue, actor)` pair per item — never "the article". An article about a
returns agreement is favourable toward the Home Office and hostile toward the
people being returned, and a single number for it means nothing.

The **input** is the headline plus the standfirst. Not the body, even where we
have it: every outlet must be scored on the same instrument, and three of the
nationals give us nothing else.

## The scale

Stance is **favourability toward the thing the issue's `target` field names**,
and nothing else.

| | |
|---|---|
| **+2** | Actively championing. Reads as advocacy for the target. |
| **+1** | Sympathetic. Frames the target favourably or its opponents unfavourably. |
| **0** | Straight reporting, or genuinely balanced. |
| **−1** | Critical. Frames the target unfavourably. |
| **−2** | Hostile. Reads as a campaign against the target. |

Before scoring, **read the target field**. "Net zero rollback" does not mean
net zero — the target is *weakening the commitments*, so a paper cheering the
delay is **+2**, not −2. Half the plausible errors in this whole system are
this one error.

## What stance is not

- **Not tone.** "Fury as ministers delay boiler ban" is angry in tone and
  favourable to the delay. Tone is a separate field.
- **Not agreement with facts.** A factually wrong piece that is warm toward the
  target is still positive.
- **Not quality.** A badly written piece and a well written one score the same.
- **Not the outlet's reputation.** Score what is in front of you. If the
  *Guardian* runs a piece hostile to a target it usually supports, that is the
  finding, not an error to be smoothed away.

## Desk

Record the desk — `news`, `comment`, `leader`, `analysis`, `feature` — from the
URL path or section, not by inference from the writing.

Do **not** adjust the score for the desk. A leader is allowed to be −2; a news
piece is allowed to be −2. Whether a paper's position lives in its news pages
or its comment pages is one of the things this instrument exists to measure,
and adjusting for it would erase the answer. The real Express sample scored
news at −1.6 against comment at −1.3, the opposite of what the prototype
assumed.

## Confidence

`high` / `medium` / `low`.

Use **low** when the headline is ambiguous without the body, when the target is
only glancingly present, or when it is a straight wire report with no framing.
Low-confidence scores are kept but down-weighted, and the share of them per
outlet is published — an outlet where everything scores low-confidence is an
outlet we cannot honestly say much about.

## The quote

Return a **verbatim span from the input** that carries the stance. Not a
paraphrase.

The quote is not decoration. It is what makes a wrong score debuggable, and it
is what appears on the drill-down page when a reader disputes a cell. A score
with no quotable span should be scored **0, confidence low** — if you cannot
point at the words, you are inferring from the outlet, which is the one thing
forbidden here.

## Abstain

Return `null` rather than guessing when:

- the item is not actually about the issue (classifier error — say so);
- the target is absent (a story about the Channel that never touches asylum);
- it is a liveblog, a picture round-up, or a puzzle page.

Abstentions are cheap. A guessed score is not: it is indistinguishable from a
real one once it reaches the archive.

## Worked examples

| Headline | Issue → target | Score | Why |
|---|---|---|---|
| "Fury as Home Office plans to outnumber villagers 10 to one" | small-boats → asylum seekers | **−2** | Campaigning framing, "fury", "outnumber". Filed as news; do not adjust for that. |
| "Net zero target date pushed back to 2040" | net-zero-rollback → *weakening the commitments* | **0** | Straight report of the change. No framing either way. |
| "Common sense at last as absurd 2035 boiler ban is axed" | net-zero-rollback → *weakening the commitments* | **+2** | Championing the rollback. Note the sign — hostile language about the *policy* is favourable to the *target*. |
| "Britain's green promises in tatters as ministers retreat" | net-zero-rollback → *weakening the commitments* | **−2** | Same event, opposite framing. |
| "Storm Bella: how to keep your home safe tonight" | climate-impacts | **null** | Service journalism. No stance toward the target. |
| "Doctors reject 4% offer and set new strike dates" | nhs-pay → the pay claims | **0** | Reports the position without endorsing or attacking it. |
| "Militant medics hold patients to ransom again" | nhs-pay → the pay claims | **−2** | "Militant", "ransom". |

## Scoring blind

The scorer never sees the outlet name, the taxonomy's `expect` field, or any
previous score for the same story. `expect` is a prior held for testing only —
letting it reach the scorer would make the direction regression a test of
itself.

## Version

Bump the version at the top when any rule changes, and stamp scores with it.
A rubric change means the archive was produced by two different instruments,
and playback has to be able to mark the boundary rather than smooth over it.
