# Naming a story, and saying what it is about

A shape on the globe is **a story that is running right now** — "UK sanctions
on Israeli settlements", not "Middle East". Most last a few days. A handful
last weeks. The globe should look different on Tuesday from how it looked on
Monday, and if it doesn't, something is wrong.

The standing list of about forty subjects in `taxonomy.json` is a **label
attached to each story**, not a thing that gets drawn. It exists for one
purpose: so you can still compare March with September. A story can't carry a
six-month line because it doesn't live that long. Its subject can.

So:

| | On the globe | In the archive |
|---|---|---|
| **Story** | yes — one shape each | born and dies within days or weeks |
| **Subject** | never drawn | how a six-month line gets drawn |

## Naming rules

**Take the name from what the papers are actually covering, not from one
paper's headline.** If you name a story from the Express's headline you have
imported the Express's framing into a label that then sits on everybody's
coverage. Use the wording shared across the cluster.

**Name the event, not the field.** "Sanctions on Israeli settlements" is a
story. "Middle East" is a subject. If the name would still be true next month
whatever happens, it's too broad.

**Four to seven words.** It has to fit inside a shape, and the shapes get
small.

**No adjectives that take a side.** Not "crackdown", not "climbdown", not
"chaos". Those are the words being measured; putting them in the label means
measuring your own label.

**Don't rename a story once it has started**, unless it has genuinely become a
different story — and if it has, that's a new story, not a rename. Someone
watching a line move needs the line to keep meaning the same thing.

## Saying what the story is about

Every story has to record **what a favourable score would be favourable
towards**. Not the subject — the specific thing.

This is the same trap as the standing subject list, and it bites harder here,
because these are written fresh every day. "Net zero rollback" was the case
that caught us out: written down as being about *net zero*, the Guardian and
the Express both come out backwards, because the Guardian opposes the rollback
and the Express welcomes it. The story is about the rollback.

For "UK sanctions on Israeli settlements", the thing being judged is **the
sanctions**. A paper cheering them is positive. A paper calling them a betrayal
is negative. Write that down when the story is born and don't change it.

### What it does now

Until a model writes the names, a story is named after **the headline nearest
the middle of the cluster** — the one most typical of what all the papers are
running. It reads as a real sentence, and being the most central it is the
least likely to carry any one paper's angle.

It is still one paper's words, which is what the rule above says to avoid, so
these are recorded as `generated` and are meant to be replaced. The first
attempt took the words all the headlines had in common and produced "Trump"
and "£36m donor Ben Delo: crypto king who thrown" — worse on every count.

### Where the wording comes from

1. **Borrow it** from the standing subject the story is tagged with, where the
   story is clearly a case of it. Safest, because those were written by hand
   and checked.
2. **Write it fresh** with a model where the story doesn't fit an existing
   subject. Record it as written-fresh so it can be found again.
3. **A person reads it** — every fresh one, once, in the daily review. This
   should be a minute or two a day.

That third step matters more than it looks. Around forty subjects were written
by hand and can be checked by hand. Thirty new stories a day cannot. The
review is the only thing standing between one badly worded line and a shape
that is the wrong colour for a week — and unlike most mistakes here, nobody
looking at the globe would be able to tell.

The safety net underneath is the direction check in `score/direction.py`. It
works at subject level, so it can't see one bad story, but a run of them
tilting the same way will show up as the whole subject flipping.
