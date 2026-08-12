"""
categories.py
--------------
The one place that configures which categories writers can choose
from, and which Discord channel each category's published ideas get
posted into.

HOW TO SET THIS UP FOR YOUR SERVER:
  1. In Discord, create one text channel per category (e.g. #comedy,
     #tutorials, #gaming, #vlogs, #educational).
  2. Turn on Developer Mode: User Settings -> Advanced -> Developer Mode.
  3. Right-click each channel -> Copy Channel ID.
  4. Replace the example categories and IDs below with your real ones.
     The dict key is the category name shown to writers in the dropdown
     and to buyers in listings; the value is that category's channel ID
     (as a number, no quotes).

These are examples - REPLACE them with your actual categories and
channel IDs before running the bot for real.
"""

CATEGORY_CHANNELS = {
    "Anime": 1536991229533888593,
    "Gaming": 1536991270466228235,
    "Movies": 1536991292989644820,
    "Comedy": 1536992641193873458,
    "BlackPill": 1536992513238507530,
    "HopeCore": 1536992474680000572,
    "Irl-LIfeStyle": 1536992233889210428,
    "Educational": 1536992699922649098,
    "MusicAudio": 1536991405187137566,
    "SportAthletes": 1536991441153429546,
    "ContentCreators": 1536991509491097640,
    "history-philosophy": 1536991601610457108,
}