"""The ``committee-meetings:<begin>:<end>`` archive — the WSL committee-meeting docket.

Fetched per date window (``windows``) so each closed window is a stable, once-only cache
key; ``harvest`` also freezes the durable Joint/``Other`` committee seed.
"""
