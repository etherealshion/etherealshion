"""
utils.py
--------
Small helpers shared across cogs. Pulling these out to their own file
(instead of defining them inside write.py) means other cogs can use
them without having to import anything from write.py directly - which
matters because write.py needs to import BuyButtonView FROM
marketplace.py. If cogs tried to import from each other in a loop,
Python would raise a circular import error.
"""

import os

PRICE = 00.01  # both a write slot and a purchase cost $10

# Centralized here (rather than redefined in admin.py, support.py,
# etc.) so there's exactly one place reading this from the environment -
# avoids the risk of one file getting updated and another being missed.
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Members with a role of this EXACT name (case-insensitive) publish
# write slots for free, same as the owner. Change this string if your
# server's moderator role is named something else.
FREE_PUBLISH_ROLE_NAME = "Moderator"


def make_preview(full_text: str, limit: int = 3) -> str:
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


def is_free_publisher(member) -> bool:
    """
    True if this member should skip the $10 write-slot payment - the
    bot owner, or anyone holding the FREE_PUBLISH_ROLE_NAME role.
    `member` is a discord.Member (has a .roles list) when this is
    called inside a server, which /write always is.
    """
    if member.id == OWNER_ID:
        return True
    member_roles = getattr(member, "roles", [])
    return any(role.name.lower() == FREE_PUBLISH_ROLE_NAME.lower() for role in member_roles)