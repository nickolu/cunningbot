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
