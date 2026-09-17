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
`list_pages` and `read_page` shipped in #42 but the backfill never ran. A dry
run on 2026-09-16 found **17 registered channels across 7 servers**, none with
either tool, so the agent in any registered channel **cannot find or read
pages** -- unregistered channels can, since they get the defaults. Run the
backfill in `add-agent-tool.md` (`DEFAULT_TOOLS_TO_ADD` already names both).

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

## Proposed — requested 2026-09-16, not yet planned or ordered

Raw asks with the context found when they were logged. None has a phase number
yet; pick them up in planning, not by starting on one.

### Move the bot off the Pi onto a droplet
**Why:** the Pi's deploys are manual, its checkout drifts from `origin/main`, and
the auto-deploy timer was never installed. A droplet is a chance to fix deploys
properly rather than install the timer on hardware we're leaving.
**Where:** `docker-compose.yml` is already portable — nine services plus
`redis:7-alpine` with a named `redis_data` volume, no ARM-specific images.
**Decide in planning:** migrate Redis data (RDB dump from the Pi's volume) or
start fresh; how `.env` gets there (every key also needs its compose
`environment:` entry since #43); deploy mechanism (GitHub Action over SSH vs. the
#31 timer); whether the "Install the Pi auto-deploy timer" ops item above is
then moot — probably yes, so don't do both. Update `deploy.md` and memory.

### Agent can search news from the RSS feeds
**The ask:** "is there any news about XYZ?" → the agent searches what the feeds
have already pulled in.
**What exists** (`bot/app/redis/rss_store.py`): there is **no searchable article
archive**. `seen` sets hold only IDs; `pending` lists are cleared after each
summary; `story_history:{channel}` is a sorted set kept for dedup and pruned at
**7 days** (`cleanup_old_story_history`); roundup `articles` lists are capped at
100. So a search tool today could only see about a week of summarized stories.
**Decided 2026-09-16: 7 days to start, so no new storage.** `story_history`
already is a 7-day archive: `rss_summary_poster.py` writes every posted story
with `title`, `summary`, `article_urls`, `posted_at`, `edition`, scored by time.

**Rejected: reading the channel text.** Every RSS post is an embed, and neither
`flatten_discord_message` nor `read_channel` reads embed text; `read_channel`
renders them as `[+1 embed(s)]`. Scanning channels would also mean paging
through history (Phase 3's problem) and parsing formatted summaries back into
stories that Redis already holds in structured form.

**Shape:** a `search_news` agent tool (one module in `bot/domain/agent/tools/`)
that loads `get_stories_within_window(..., 168)` for each of the guild's summary
channels, keyword-matches title + summary, and returns matches with their links
and dates. Add a store method to list a guild's `story_history` channels.
Fall back to `web_search` when nothing matches.

**Gaps to check before building:**
- **Feeds posted directly aren't archived.** `post_direct_items` in
  `rss_feed_poster.py` sends an embed per item and saves nothing searchable.
  Either write those items to history too (small change) or accept that only
  summary channels are searchable.
- Check whether any channel's dedup window is set shorter than 7 days (and
  whether that affects what's kept), and that the 7-day cleanup actually runs.
**Built in two steps (decided 2026-09-16):**

1. **Search the 7 days we already keep.** `search_news` over `story_history`,
   as above. Keyword match only.
2. **Start archiving news so history goes back further.** Save every feed item
   when `rss_feed_poster.py` first sees it: title, link, feed name, published
   date, feed description. Save it whether the feed posts items directly or
   feeds a summary, which also closes the gap above. `search_news` then searches
   the archive, and `story_history` goes back to only doing dedup.
   **Decide when planning step 2:**
   - **Storage.** Redis keeps everything in memory, which suits a week of
     stories, but months of articles is a question for the droplet move. The
     other option is SQLite/Postgres with full-text search. Decide alongside
     the droplet item.
   - **Retention.** How long to keep items (90 days? a year?).
   - **Search.** Keyword vs. embeddings, and whether to dedup the same story
     across feeds.

   Step 2 can start archiving before step 1 ships, since history only builds
   from the day archiving starts.

### More models, Grok, and managing models from Discord
**Why:** `bot/domain/llm/models.py` is a hardcoded table plus a
`PermittedModelType` literal, so every new model is a PR and a deploy. Pickers
use static `app_commands.Choice` lists, capped at 25.
**Shape:**
- Add a provider abstraction in `bot/api/` so xAI (Grok) sits next to OpenAI.
  xAI's API is OpenAI-compatible, so it may be a base-URL + key swap on the
  existing client — verify tool calling works before assuming so.
- Move the enabled-model list into Redis; `models.py` becomes seed data.
- `/models add|test|remove` (admin-only), limited to models listed by a
  connected provider. `test` runs what `scripts/check_models.py` does — a real
  completion *and* a tool call — before a model is enabled. Listing ≠ working.
- Switch model pickers to autocomplete to get past the 25-choice cap.
**Related:** the `-pro`/`-codex` Responses API follow-up below.

### `/bot` — one-shot agent call in any channel
**The ask:** run the agent from a slash command without registering the channel.
**Where:** `bot/app/commands/agent/agent.py` (the `/agent` group). Reuse
`UNREGISTERED_AGENT_CONFIG` and `run_agent` from `agent_listener.py`; history
fetch in `_handle_agent_response` should move somewhere both can call.
Must `defer()` — the agent easily exceeds Discord's 3 s interaction deadline.
Decide whether it includes channel history or only the prompt. Update `/help`.

### Multi-modal output: voice/sound, video, slideshows
**Where:** image generation already has OpenAI and Google clients in
`bot/api/openai/` and `bot/api/google/`, and `host_image` hosts results. New
media follows that pattern: a client per vendor, an agent tool per capability.
**Candidates:** TTS (OpenAI `audio/speech`) posted as a Discord attachment;
sound effects / music (vendor TBD); video (Sora / Veo — slow, async, expensive,
needs a job-and-poll pattern and a cost cap); slideshows as a published page
(`publish_page`) rather than a `.pptx`. Split into separate items at planning;
TTS is the cheap first win.

### Publish the agent's reasoning as a page
**The ask:** each agent run automatically produces a web page showing its
context, tool calls, and results, so users can see *why* it answered as it did.
**Where:** `run_agent` in `bot/domain/agent/agent_service.py` (up to
`MAX_TOOL_ROUNDS = 5`) is where spans would be collected; publish via the
`bot/domain/pages` service and append a small link to the reply.
**Care:** the page would expose channel history and tool output, so it must
respect the page visibility model and redact secrets. Don't publish on every
message — decide between always, on-request ("show your work"), or a per-channel
toggle. Use `one_off=true` so traces don't overwrite each other (see the daily
summaries follow-up).

### Interrupt the bot
**Why:** once a run starts it holds the per-channel `asyncio.Lock` in
`agent_listener.py` and messages arriving meanwhile are **silently dropped**
(`if lock.locked(): return`).
**Shape:** keep the running `asyncio.Task` per channel so it can be cancelled;
trigger on a stop word ("stop", "nvm") or a 🛑 reaction from the requester. Also
decide whether a new message mid-run should cancel-and-restart with the new
context, or queue instead of being dropped. Cancellation must not leave a
half-published page or a half-sent split message. Media generation (see above)
makes this more valuable.

### Server-wide memory
**The ask:** the bot remembers facts about a server across channels and
conversations.
**Where:** a new `memory_store.py` in `bot/app/redis/`, keyed
`memory:{guild_id}:...`; `remember` / `recall` / `forget` agent tools; relevant
memories injected into the agent's system prompt in `agent_service.py`.
**Decide in planning:** explicit ("remember that…") only, or automatic
extraction (riskier — it will store wrong or private things); retrieval by
keyword vs. embeddings once there are too many to inject whole; a way for users
to list and delete memories (probably `/memory`); size caps. Treat memory
content as untrusted input — it's a persistent prompt-injection vector.

---

## Follow-ups

### Mentioning the bot in an unregistered channel still doesn't work
Long-standing. #36 was meant to fix it (`UNREGISTERED_AGENT_CONFIG` in
`agent_listener.py`), yet it's still reported broken. Unconfirmed suspects, in
order:
1. **Role mention, not user mention.** Discord gives the bot a managed role with
   the same name; picking that from the `@` menu produces `<@&role>`, which
   isn't in `message.mentions`, so `_is_summoned` misses it. Check
   `message.role_mentions` against `guild.me.roles`.
2. **Threads and forum posts are ignored** — `on_message` returns unless the
   channel is exactly `discord.TextChannel`.
3. A channel that *was* registered and paused has `enabled: false` and stays
   silent by design — it looks unregistered to users.

Confirm on the host by grepping `logs/` for `agent_summoned_unregistered` right
after a failing mention: no event means the gate rejected it (1 or 2).

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
| — | Dated summaries publish as one-off pages instead of overwriting each other | #PR |
