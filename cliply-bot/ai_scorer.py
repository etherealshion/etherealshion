"""
ai_scorer.py
------------
Calls the real Claude API to rate a submitted idea 1-5 stars with
feedback, replacing the random placeholder used in Phases 1 and 2.

Design choice worth knowing: if the API call fails for ANY reason
(missing/invalid key, network hiccup, rate limit, malformed response),
we fall back to a random placeholder rating instead of crashing.
A broken AI call is not a good enough reason to stop someone from
submitting their $10 idea - the rating is advisory anyway.

IMPORTANT: score_idea() now returns a THIRD value, `used_fallback`
(True/False), specifically so callers (cogs/write.py) can show a
visible warning in Discord itself when a fallback happens - not just
a line in your terminal that's easy to miss. If /write ever looks like
"just random 1-5s again," check the embed footer first: it will now
say so explicitly instead of silently pretending to be real AI output.
"""

import json
import os
import random
import traceback

from anthropic import AsyncAnthropic

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = (
    "You are an editorial scout rating short-form content ideas for a "
    "creator marketplace. Rate the idea from 1 to 5 stars based on how "
    "clear, well-scoped, and commercially appealing it is to a buyer. "
    "Respond with ONLY a JSON object in exactly this shape and nothing else "
    '(no markdown, no code fences, no extra text): '
    '{"stars": <integer 1-5>, "feedback": "<one or two sentences of actionable feedback>"}'
)

# Used only if the real API call fails - keeps /write working regardless.
_FALLBACK_FEEDBACK = {
    1: "This needs more development before it's ready for editors.",
    2: "There's a spark here, but the execution needs work.",
    3: "Solid, workable idea with real commercial potential.",
    4: "Strong concept with clear editor appeal.",
    5: "Outstanding idea - highly marketable and well put together.",
}

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    """
    Builds the Anthropic client the first time it's needed, rather than
    at import time. Re-checking the key fresh on every call (instead of
    baking in whatever os.getenv() happened to return once, at import
    time) is more defensive and avoids a common source of "why isn't my
    key working" confusion.
    """
    global _client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Check that it's in your .env file "
            "(no quotes, no extra spaces) and that .env sits in the same "
            "folder you run `python bot.py` from."
        )
    if _client is None:
        _client = AsyncAnthropic(api_key=api_key)
    return _client


def _fallback_score() -> tuple[int, str]:
    score = random.randint(1, 5)
    feedback = _FALLBACK_FEEDBACK[score]
    return score, feedback


async def score_idea(title: str, category: str, description: str) -> tuple[int, str, bool]:
    """
    Sends the idea to Claude and returns (stars, feedback, used_fallback).
    Never raises - always returns something usable. used_fallback is
    True whenever the real API call didn't work and we had to use the
    random placeholder instead - callers should surface this to the user.
    """
    user_prompt = f"Title: {title}\nCategory: {category}\nDescription: {description}"

    try:
        client = _get_client()
        response = await client.messages.create(
            model=MODEL,
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = response.content[0].text.strip()

        # Claude usually follows "no code fences" instructions, but models
        # occasionally wrap JSON in ```json ... ``` anyway - strip that off
        # defensively so json.loads doesn't choke on it.
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            if raw_text.lower().startswith("json"):
                raw_text = raw_text[4:].strip()

        data = json.loads(raw_text)
        stars = int(data["stars"])
        feedback = str(data["feedback"]).strip()

        if not (1 <= stars <= 5) or not feedback:
            raise ValueError(f"Model returned an unusable rating: {data!r}")

        return stars, feedback, False

    except Exception:
        # Full traceback, not just str(error) - so you can see exactly
        # WHERE it failed (auth, network, JSON parsing, etc.) instead of
        # a one-line summary that can hide the real cause.
        print("[ai_scorer] Falling back to placeholder score - the real API call failed:")
        traceback.print_exc()
        score, feedback = _fallback_score()
        return score, feedback, True