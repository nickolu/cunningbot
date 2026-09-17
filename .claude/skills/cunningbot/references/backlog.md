# Backlog

What's left to build on CunningBot, in rough priority order, with the context
and decisions already made so nobody re-plans them.

**Last verified against `main` and the Pi: 2026-09-16.** Anything below may have
changed since — check before acting on a claim, and update the date when you do.

## Keeping this file honest

- **Ship an item → delete it here in the same PR**, and add one line to *Done*
  with the PR number. A backlog listing finished work is worse than none.
- **Found something worth doing but not now?** Add it under the section it
  belongs to, with *why* and *where*. A bare title is useless three weeks later.
- Record decisions as they're made, including rejected alternatives. The point is
  that the next person doesn't re-litigate them.
- Work in phases, planned with the user one at a time: scope the phase, agree on
  PR boundaries, then build. Don't start a phase that hasn't been planned.

---

## Ops — small, unblocked, mostly on the Pi or in Discord

### Backfill the page tools into registered channels
`list_pages` and `read_page` shipped in #42 but the backfill never ran. The one
registered agent channel's stored tool list has neither, so the agent there
**cannot find or read pages** — unregistered channels can, since they get the
defaults. Run the backfill in `add-agent-tool.md` (`DEFAULT_TOOLS_TO_ADD`
already names both tools), or `/agent tool list_pages enable` and
`/agent tool read_page enable` in that channel.

### Turn on GitHub issue filing — needs a code change first
Shipped in #40, inert, for two reasons:

1. **`docker-compose.yml` doesn't pass the variables.** #43 stopped baking `.env`
   into the image, so a key only reaches a container if its service's
   `environment:` block names it. The `cunningbot` service lists
   `PAGES_PUBLISH_TOKEN` but not `GITHUB_TOKEN` or `GITHUB_ISSUE_REPO` — #40
   predates #43 and was never updated. Adding the token to `.env` alone does
   nothing. Needs a PR adding both lines.
2. `GITHUB_TOKEN` is not in the Pi's `.env`. Needs a fine-grained PAT (repo
   `nickolu/cunningbot`, Issues: read and write) and
   `GITHUB_ISSUE_REPO=nickolu/cunningbot`.

Then rebuild, and `/agent tool create_github_issue enable` in one channel.
**Do not backfill it**; it's opt-in because it writes to a public repo.

### Consolidate the wishlist pages
Guild `844003671334977607` has four near-duplicate "Bot Fails + Wishlist" pages
— the exact fragmentation Phase 2 fixed. All predate source storage, so the
agent can't append to any of them: it will correctly refuse and say republishing
replaces the page. Merge their rendered content into one page under slug
`bot-fails-wishlist`; after that it appends normally. Deleting the stray URLs is
the user's call — someone may have them.

### Install the Pi auto-deploy timer
PR #31 added it; the one-time `sudo` install was never run (still no timer on
2026-09-16). The Pi's checkout also keeps accumulating merge commits from manual
`git pull`, which makes `--ff-only` — what the timer uses — fail. Reset the
checkout to `origin/main` first, then install.

---

## Phase 3 — Channel history and media backfill

**The ask:** "parse ~500 messages in a channel and collect the images moop
posted, to make daily-update pages." Failed because the bot can't reach them.

**Why it fails today** (`bot/domain/agent/tools/read_channel.py`):
- `limit` clamps to 50 and there's no `before`/`after` cursor, so nothing older
  than the last 50 messages is reachable.
- Attachments render as `[Attachments: filename]` — **the URLs are discarded**.
  Even inside the 50-message window the images were unreachable.

**Shape:**
- `before`/`after` (message id or ISO date) and internal pagination past 50.
- Filters: `author`, `has_attachments`.
- Emit attachment URLs and `msg.jump_url`.
- **Bulk hosting is part of this, not a follow-up.** Discord CDN URLs expire in
  ~a day (`publishing.md`), so collected images die before the page is read.
  Chain to `host_image_from_url`.

**Open questions for planning:** a hard ceiling on messages per call (the agent
gets 5 tool rounds per message); whether this is one tool with filters or a
separate `collect_channel_media` tool; what a 500-message result does to the
model's context — probably summarize or return only matches, never raw history.

## Phase 4 — Temporary upload page and file catalog

**The ask:** the bot shares a page with an upload form that posts to blob
storage, with a short TTL and limits on what can be uploaded, and uploaded files
can be recalled later by searching their metadata.

**Constraints already found:**
- **Can't ride on `/p/`.** Page CSP is `default-src 'none'; ... form-action 'none'`
  (`web/api/_lib.js`), deliberately, so a prompt-injected page can do nothing.
  An upload form needs its own route with its own CSP. Don't loosen the shared one.
- **Needs a different auth model.** `web/api/upload.js` takes the bot's master
  `PUBLISH_TOKEN` — unusable from a public form. Use a short-lived, per-page
  upload token instead.
- `upload.js` is also image-only and 4 MB (Vercel's 4.5 MB body cap). Larger
  files need client-side upload direct to Blob.
- The file catalog is a sibling of the Phase 2 page index — reuse that pattern
  (per-guild sorted set on Upstash, prune on read).

Largest security surface in the backlog. Plan it carefully.

---

## Follow-ups

### Daily summaries probably overwrite each other
Since #42, a page with no slug gets one derived from its title. The agent
publishes "Today's Chat Summary" — the same title every day, so the same slug,
so **each day's summary likely replaces the last** and yesterday's shared link
shows today's content. Seen 2026-09-16: one such page, updated 09-15, with
stored source. It should have been `one_off=true`. Fix options: sharpen the
`one_off` guidance in the `publish_page` schema and system prompt around
recurring/dated content, or have the agent date-stamp snapshot titles. Confirm
by checking whether earlier summaries still exist before fixing.

### `-pro` and `-codex` models need the Responses API
`gpt-5.5-pro` and `gpt-5.3-codex` are in the account's `/v1/models` listing but
only serve `/v1/responses`; the bot only calls chat completions. Two such models
shipped in `/chat` as guaranteed errors before #39 removed them. Supporting them
means a second client path in `bot/api/openai/`. Run `scripts/check_models.py`
after any catalog change.

### Seven pre-existing test failures
Red on `main` since before this backlog started: two in
`test_af_command_integration.py`, two in `test_google_image_clients.py`, three
in `test_image_command_integration.py`. They make it harder to tell a regression
from noise — every PR has to say "same 7".

### Domain imports from app
`bot/domain/agent/tools/dice.py` imports `bot.app.commands.dice.roll.DiceRoller`,
breaking the rule that `bot/domain/` doesn't depend on `bot/app/`. Move
`DiceRoller` into `bot/domain/dice/` and have the cog import it from there.

### `/pages` slash command (optional)
Deliberately skipped in Phase 2 — the agent tools cover the ask. Worth adding if
people want to browse a server's pages without asking the bot.

---

## Done

| Phase | What | PR |
|---|---|---|
| 1 | Agent tools are one module each, collected by a registry | #38 |
| 1 | One model catalog; removed four models that could only ever error | #39 |
| 1 | `create_github_issue` tool + `/agent tool` toggle | #40 |
| 2 | Pages store source; per-guild index; reindex endpoint | #41 |
| 2 | `list_pages`, `read_page`, slug-by-default, 365-day stable pages | #42 |
