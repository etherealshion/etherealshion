"""
utils.py
--------
Small helpers shared between cogs/write.py and cogs/marketplace.py.
Pulling these out to their own file (instead of defining them inside
write.py) means marketplace.py can use them without having to import
anything from write.py - which matters because write.py needs to
import BuyButtonView FROM marketplace.py. If both files tried to
import from each other, Python would raise a circular import error.
"""

PRICE = 10  # both a write slot and a purchase cost $10


def make_preview(full_text: str, limit: int = 6) -> str:
    """
    Truncates the full idea text down to a short teaser for browsing -
    only the first `limit` characters, by design. This is intentionally
    strict: it exists purely to confirm an idea exists and hint at its
    start, not to give editors enough to judge (or steal) the idea
    without paying.
    """
    if not full_text:
        return full_text
    return full_text[:limit] + "..."