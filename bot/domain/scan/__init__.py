"""Channel history scanning: the page -> extract -> merge loop.

No discord.py in here. The caller (bot/app/scan_runtime.py) passes in a
callable that yields pages of plain messages and a callable that talks to the
model, so the whole loop is testable without a network or a gateway.
"""
