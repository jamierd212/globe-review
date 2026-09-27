"""Which model does the naming and scoring, behind one call.

    complete(system, user, max_tokens) -> (parsed JSON or None, tokens in, tokens out)

Two providers:

  anthropic   Claude Haiku, through the official SDK. Where this started.
  gemini      Google's cheapest Flash-Lite, through plain HTTPS - nothing to
              install. Has a free daily allowance that this job fits inside
              when articles are sent in groups.

Chosen by FPM_PROVIDER, falling back to PROVIDER below.

Gemini 3.8 Flash, thinking "low", since 27 Sept 2026. Compared on 311 of
Claude Haiku's scored articles and then refereed item by item where the two
pointed opposite ways: Flash was right on 24 of 25 clear cases. Haiku's
errors were systematic - scoring against the far side of the target, so
every "slow AI down" piece came out favourable to the speed of AI. Flash-Lite
was tried first and flipped signs the other way. `python3 -m score.compare`
reruns the test for any model, and is the thing to run before switching again.

The free tier has two limits, and they need handling differently. Too many
requests in a minute is a wait. Too many in a day is not - waiting would hold
the hourly job for hours - so DailyLimit is raised and the caller stops,
leaving the rest for the next run.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

PROVIDER = "gemini"

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
# The model the comparison was run on. GEMINI_MODEL overrides it. If Google
# retires the name, the newest stable Flash (not Lite) is used instead and a
# warning is printed - the job keeps running, but rerun the comparison.
GEMINI_DEFAULT = "gemini-3.8-flash"
# Kept under the free tier's per-minute allowance, which Google sets per
# account and changes without notice. GEMINI_RPM overrides it.
GEMINI_RPM = 8
# How hard the model thinks before answering. Left at Google's default, Flash
# thought for ~7,700 tokens per request: two minutes each, and the thinking
# used up the reply's length so twenty answers came back cut off. "low" took
# five seconds and answered all twenty. GEMINI_THINKING overrides it.
GEMINI_THINKING = "low"
_NO_THINKING_CONFIG = set()      # models that refused the setting


class DailyLimit(Exception):
    """The day's free allowance is used up. Stop, and try again next run."""


def provider():
    return (os.environ.get("FPM_PROVIDER") or PROVIDER).strip().lower()


def parse_json(txt):
    """The model's reply as JSON: an object or an array. Tolerates a code
    fence or a sentence around it, which both providers occasionally add."""
    if not txt:
        return None
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
    try:
        return json.loads(txt)
    except ValueError:
        pass
    for open_, close in (("[", "]"), ("{", "}")):
        i, j = txt.find(open_), txt.rfind(close)
        if 0 <= i < j:
            try:
                return json.loads(txt[i:j + 1])
            except ValueError:
                continue
    return None


# ------------------------------------------------------------- anthropic ---

_ANTHROPIC = None


def _anthropic(system, user, max_tokens, retries=4):
    global _ANTHROPIC
    if _ANTHROPIC is None:
        import anthropic                      # noqa: local import
        _ANTHROPIC = anthropic.Anthropic()
    for attempt in range(retries):
        try:
            r = _ANTHROPIC.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=max_tokens,
                system=[{"type": "text", "text": system}],
                messages=[{"role": "user", "content": user}])
            txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            return parse_json(txt), r.usage.input_tokens, r.usage.output_tokens
        except Exception as e:
            if attempt == retries - 1:
                sys.stderr.write(f"  gave up: {type(e).__name__}: {e}\n")
                return None, 0, 0
            time.sleep(2 ** attempt)
    return None, 0, 0


# ---------------------------------------------------------------- gemini ---

_GEMINI_MODEL = None
_LAST_CALL = 0.0


def _gemini_key():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set - see .env")
    return key


def _request(method, path, body=None, timeout=120):
    # The key goes in a header, never in the URL, where it would end up in
    # logs and error messages.
    req = urllib.request.Request(
        f"{GEMINI_API}/{path}", method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"x-goog-api-key": _gemini_key(),
                 "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _version(name):
    """'models/gemini-3.1-flash-lite' -> (3, 1), for picking the newest."""
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def _newest(models, kind):
    """The newest stable model of a kind ('flash' or 'flash-lite')."""
    names = [m["name"].replace("models/", "") for m in models
             if "generateContent" in m.get("supportedGenerationMethods", [])
             and not re.search(r"preview|exp|tts|image|audio|live|latest|omni", m["name"])]
    if kind == "flash-lite":
        names = [n for n in names if n.endswith("flash-lite")]
    else:
        names = [n for n in names if n.endswith("flash")]
    return max(names, key=_version) if names else None


def gemini_model():
    """GEMINI_MODEL if set, else GEMINI_DEFAULT - checked against what this
    key can actually use, because Google retires model names and a missing
    one would fail every request until someone noticed."""
    global _GEMINI_MODEL
    if _GEMINI_MODEL:
        return _GEMINI_MODEL
    wanted = (os.environ.get("GEMINI_MODEL") or GEMINI_DEFAULT).replace("models/", "").strip()
    models = _request("GET", "models?pageSize=1000").get("models", [])
    have = {m["name"].replace("models/", "") for m in models}
    if wanted in have:
        _GEMINI_MODEL = wanted
    else:
        kind = "flash-lite" if wanted.endswith("flash-lite") else "flash"
        _GEMINI_MODEL = _newest(models, kind)
        if not _GEMINI_MODEL:
            raise RuntimeError(f"{wanted} is not available and there is no {kind} to fall back on")
        sys.stderr.write(f"  {wanted} is not available to this key - using "
                         f"{_GEMINI_MODEL}. Rerun score.compare before trusting it.\n")
    return _GEMINI_MODEL


def _pace():
    global _LAST_CALL
    rpm = float(os.environ.get("GEMINI_RPM") or GEMINI_RPM)
    gap = 60.0 / rpm - (time.monotonic() - _LAST_CALL)
    if gap > 0:
        time.sleep(gap)
    _LAST_CALL = time.monotonic()


def _is_daily(err):
    """Google reports which limit was hit in the error's details."""
    for d in err.get("details", []):
        for v in d.get("violations", []) or []:
            if "PerDay" in (v.get("quotaId") or ""):
                return True
    return "per day" in (err.get("message") or "").lower()


def _retry_after(err, default):
    for d in err.get("details", []):
        m = re.match(r"(\d+(?:\.\d+)?)s", str(d.get("retryDelay") or ""))
        if m:
            return min(90.0, float(m.group(1)) + 1)
    return default


def _gemini(system, user, max_tokens, retries=5):
    model = gemini_model()
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": 0, "maxOutputTokens": max_tokens},
        # The news is full of killings, abuse and extremism, and scoring how a
        # paper frames them is the whole job. With the default filters a
        # single court-report headline blanks the reply for all twenty items
        # sent with it: found when two requests in the first comparison came
        # back empty. Nothing is being generated here but a number per item.
        "safetySettings": [{"category": c, "threshold": "BLOCK_NONE"} for c in (
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")],
    }
    level = os.environ.get("GEMINI_THINKING") or GEMINI_THINKING
    if level and model not in _NO_THINKING_CONFIG:
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": level}
    for attempt in range(retries):
        _pace()
        try:
            r = _request("POST", f"models/{model}:generateContent", body)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode()).get("error", {})
            except Exception:
                err = {}
            if (e.code == 400 and "thinking" in (err.get("message") or "").lower()
                    and "thinkingConfig" in body["generationConfig"]):
                # this model does not take the setting; ask without it
                _NO_THINKING_CONFIG.add(model)
                del body["generationConfig"]["thinkingConfig"]
                continue
            if e.code == 429:
                if _is_daily(err):
                    raise DailyLimit(err.get("message", "daily limit reached"))
                time.sleep(_retry_after(err, 20 * (attempt + 1)))
                continue
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            sys.stderr.write(f"  gemini {e.code}: {err.get('message', '')[:200]}\n")
            return None, 0, 0
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries - 1:
                sys.stderr.write(f"  gave up: {type(e).__name__}: {e}\n")
                return None, 0, 0
            time.sleep(2 ** attempt)
            continue
        usage = r.get("usageMetadata", {})
        cand = (r.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        txt = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not txt:
            why = ((r.get("promptFeedback") or {}).get("blockReason")
                   or cand.get("finishReason") or "no text")
            sys.stderr.write(f"  gemini returned nothing: {why}\n")
        return (parse_json(txt), usage.get("promptTokenCount", 0),
                usage.get("candidatesTokenCount", 0))
    return None, 0, 0


# ------------------------------------------------------------------- api ---

def model_name():
    """What gets written against every score, so a mixed archive can always
    be told apart."""
    return gemini_model() if provider() == "gemini" else ANTHROPIC_MODEL


def label():
    """The model's name for a log line, without asking the network."""
    if provider() == "gemini":
        return _GEMINI_MODEL or os.environ.get("GEMINI_MODEL") or GEMINI_DEFAULT
    return ANTHROPIC_MODEL


def complete(system, user, max_tokens=300):
    if provider() == "gemini":
        return _gemini(system, user, max_tokens)
    return _anthropic(system, user, max_tokens)


def grouped():
    """Whether to send several items per request. Gemini's free allowance is
    counted in requests, so it groups; the Claude path keeps its per-item
    batch at half price."""
    return provider() == "gemini"
