"""Turning a headline into numbers.

Two options, same interface.

`hashed` needs nothing installed. It counts words and word pairs, weights them
by how rare they are across the day, and squeezes the result into a fixed
number of buckets. It is not clever — it cannot tell that "asylum seekers" and
"migrants" are related — but it groups near-identical wire copy and follow-ups
perfectly well, and it means the whole pipeline runs on day one without
installing anything.

`sentence` is the real one, and needs `sentence-transformers` locally. Use
`all-MiniLM-L6-v2`: small, fast on a laptop, free, and runs offline so no
article text leaves the machine.

Start on `hashed`. Switch when you have enough stories to compare the two
properly — the number to look at is how many stories each produces per day and
how long they live, not how good the clusters look by eye.
"""

import math
import re
import sys
from collections import Counter

DIM = 512
_word = re.compile(r"[a-z0-9']+")

STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "at", "by", "from", "as", "is", "are", "was", "were", "be", "it", "its",
        "that", "this", "his", "her", "their", "they", "we", "you", "will",
        "has", "have", "had", "says", "said", "after", "over", "into", "out"}


def tokens(text):
    """Words and word pairs. Pairs matter: 'small boats' means something that
    'small' and 'boats' separately do not."""
    ws = [w for w in _word.findall((text or "").lower()) if w not in STOP and len(w) > 2]
    return ws + [f"{a}_{b}" for a, b in zip(ws, ws[1:])]


def idf(docs):
    """How rare each word is across today's articles. A word in every headline
    tells you nothing; a word in three tells you those three belong together."""
    n = len(docs) or 1
    df = Counter()
    for d in docs:
        df.update(set(tokens(d)))
    return {w: math.log(n / (1 + c)) + 1.0 for w, c in df.items()}


def hashed(text, weights=None, dim=DIM):
    """One vector per article, length 1."""
    v = [0.0] * dim
    for w, c in Counter(tokens(text)).items():
        weight = (weights or {}).get(w, 1.0)
        h = hash_str(w)
        # a second hash decides the sign, so unrelated words that land in the
        # same bucket tend to cancel rather than pile up
        v[h % dim] += (1 + math.log(c)) * weight * (1 if (h >> 16) & 1 else -1)
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def hash_str(s):
    """Stable across runs, unlike Python's built-in hash for strings."""
    h = 2166136261
    for ch in s:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


def embed_batch(texts, dim=DIM):
    """Vectors for one window of articles, sharing one rarity table."""
    w = idf(texts)
    return [hashed(t, w, dim) for t in texts]


MODEL_NAME = "all-MiniLM-L6-v2"
_MODEL = None


def sentence_batch(texts, model_name=MODEL_NAME, batch_size=64):
    """The real one. Runs locally on the machine; no article text is sent
    anywhere, and no key is needed."""
    from sentence_transformers import SentenceTransformer   # noqa: local import
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer(model_name)
    return [list(map(float, v)) for v in
            _MODEL.encode(texts, normalize_embeddings=True, batch_size=batch_size)]


def available():
    """Is the sentence model installed and loadable?"""
    try:
        import sentence_transformers          # noqa: F401
        return True
    except Exception:
        return False


def which(prefer_sentence=True):
    """Which vectoriser will be used, without loading it.

    find_spec rather than import: importing sentence_transformers pulls in
    torch, which takes about thirty seconds. Asking whether it is installed
    should not cost that, because on most runs every vector is already stored
    and the model is never needed at all.
    """
    if prefer_sentence:
        import importlib.util
        if importlib.util.find_spec("sentence_transformers") is not None:
            return "sentence"
    return "hashed"


def best_batch(texts, prefer_sentence=True):
    """Vectors, and which method produced them.

    Prefers the sentence model and quietly falls back to word counting, so the
    pipeline runs on a machine with nothing installed. The caller needs to know
    WHICH it got, because the similarity threshold is different for each and
    using the wrong one gives an empty globe with no error.
    """
    if prefer_sentence and available():
        try:
            return sentence_batch(texts), "sentence"
        except Exception as e:            # model missing from disk, no network
            sys.stderr.write(f"sentence model unavailable ({type(e).__name__}), "
                             f"falling back to word counting\n")
    return embed_batch(texts), "hashed"


# --- storing them ----------------------------------------------------------
#
# Packed as float32. 384 dimensions is 1.5KB an article, which is nothing
# next to re-reading every article through the model once an hour.

import array as _array


def pack(vec):
    return _array.array("f", vec).tobytes()


def unpack(blob):
    a = _array.array("f")
    a.frombytes(blob)
    return list(a)
