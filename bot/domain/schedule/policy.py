"""Limits on scheduled prompts, agreed with the user on 2026-09-20.

The numbers are arbitrary starting points; change them here. A stored prompt
runs unattended with the channel's tools until someone cancels it, so the caps
are what keep a server from piling up cost or a standing prompt-injection
target.
"""

# Jobs a server may hold, and jobs one person may hold. Paused jobs count;
# cancelled ones are deleted, so they don't.
MAX_JOBS_PER_GUILD = 10
MAX_JOBS_PER_USER = 3

# Failed runs in a row before a job pauses itself and its creator is told.
MAX_CONSECUTIVE_FAILURES = 3

# A prompt is replayed on every run; keep it to a paragraph.
MAX_PROMPT_CHARS = 1000

# The zone assumed when someone doesn't name one ("every day at 9am"). The
# read-back always names the zone, so a wrong assumption shows up before
# Confirm. Decided with the user 2026-09-21.
DEFAULT_SCHEDULE_TZ = "America/Los_Angeles"

# How long a drafted schedule waits for Confirm before it's forgotten.
DRAFT_TTL_SECONDS = 15 * 60
