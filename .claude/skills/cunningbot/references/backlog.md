# Backlog

What's left to build on CunningBot, in rough priority order, with the context
and decisions already made so nobody re-plans them.

**Last verified against `main` and the Pi: 2026-09-17.** Anything below may have
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

### After the next deploy: backfill `search_news`
#54 adds `search_news` to `DEFAULT_TOOLS_TO_ADD`. Once it's deployed, run the
backfill in `add-agent-tool.md` (dry run first) or registered channels won't get
it. `list_pages` and `read_page` were backfilled into all 17 registered channels
on 2026-09-17, so the run should only add `search_news`.

### Turn on GitHub issue filing — needs a token
Shipped in #40, still inert. #49 passes `GITHUB_TOKEN` and `GITHUB_ISSUE_REPO`
to the `cunningbot` container (the only service that loads agent tools); before
it, #43's switch away from a baked-in `.env` meant the keys never arrived.
What's left is on the Pi: add a fine-grained PAT (repo `nickolu/cunningbot`,
Issues: read and write) and `GITHUB_ISSUE_REPO=nickolu/cunningbot` to `.env`,
rebuild, then `/agent tool create_github_issue enable` in one channel.
**Do not backfill it**; it's opt-in because it writes to a public repo.

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
summary; `story_history:{channel}` is a sorted set kept for dedup and meant to be
pruned at **7 days** (see the gap below); roundup `articles` lists are capped at
100. So a search tool today could only see about a week of summarized stories.
**Decided 2026-09-16: 7 days to start, so no new storage.** `story_history`
already is a 7-day archive: `rss_summary_poster.py` writes every posted story
with `title`, `summary`, `article_urls`, `posted_at`, `edition`, scored by time.

**Rejected: reading the channel text.** Every RSS post is an embed, and neither
`flatten_discord_message` nor `read_channel` reads embed text; `read_channel`
renders them as `[+1 embed(s)]`. Scanning channels would also mean paging
through history (Phase 3's problem) and parsing formatted summaries back into
stories that Redis already holds in structured form.

**Step 1 is built: #54.** `search_news` searches `story_history` for the
guild (keys are `rss:{guild_id}:story_history:{channel_id}`, so no config lookup
is needed), 168 hours back. Query words are prefix-matched against title and
summary with filler words dropped; stories with every word come first, else
ones with at least half, newest first, capped at 10. No match points the model
at `web_search`. Breaking-news entries live in the same set and are searchable.
Directly posted feeds are not -- step 2 fixes that.

**Found while building step 1 -- decide before step 2:**
- **The 7-day cleanup never runs.** `RSSRedisStore.cleanup_old_story_history`
  has no callers; `rss_summary_poster` calls `cleanup_old_history()` in
  `bot/app/story_history.py`, which prunes the old JSON file, not Redis. So
  `story_history` grows forever and only `allkeys-lru` eviction bounds it --
  which under memory pressure can evict *any* key, trivia and agent config
  included. Wiring the cleanup in deletes the only history older than a week,
  which step 2 might want, so it was left alone. Decide with step 2's storage.
- A channel's dedup window (6-168 h) only controls how much history dedup
  reads; every post is written regardless, so it doesn't shorten what's kept.

**Step 2 -- start archiving news so history goes back further.** Save every
feed item when `rss_feed_poster.py` first sees it: title, link, feed name,
published date, feed description. Save it whether the feed posts items directly
or feeds a summary. `search_news` then searches the archive, and
`story_history` goes back to only doing dedup.
**Decide when planning step 2:**
- **Storage.** Redis keeps everything in memory, which suits a week of stories,
  but months of articles is a question for the droplet move. The other option
  is SQLite/Postgres with full-text search. Decide alongside the droplet item.
- **Retention.** How long to keep items (90 days? a year?).
- **Search.** Keyword vs. embeddings, and whether to dedup the same story
  across feeds.

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

### Rich page components
**The ask** (from the wishlist page): image galleries, sortable tables, and
similar widgets on published pages.
**Constraint:** pages are served with `default-src 'none'` and no script
execution (`web/api/_lib.js`), deliberately, so a prompt-injected page can do
nothing. Galleries can probably be done in CSS alone inside
`page_renderer.py`'s shell; anything interactive (sorting) needs JavaScript and
therefore a CSP decision -- e.g. a fixed, first-party script served from `web/`
with a nonce, never script from page content. Don't loosen the shared CSP for
it without planning.

### Delete pages
**The ask:** be able to delete a published page.
**What exists:** nothing. A page only goes away when its TTL runs out -- 30
days for one-offs, 365 for slugged pages, extended on every republish. There is
no delete route in `web/api/` (`pages.js` answers GET and POST only), no method
in `bot/api/pages/client.py`, and no agent tool. The closest thing today is
republishing over a slug, which replaces the content but keeps the URL alive.
**Shape:**
- `DELETE /api/pages?guild_id=<id>&id=<page>` in `web/api/pages.js`, behind the
  publish token. Check the stored `guild_id` the way `publish.js` does, and
  answer a page owned by another guild exactly like a missing one. `ZREM` the
  id from the guild's index in the same call instead of waiting for a read to
  prune it.
- `delete_page(guild_id, reference)` in `page_service.py`, taking a slug, id,
  or URL like `read_page`.
- Exposed as an agent tool, a `/pages delete` subcommand (see the optional
  `/pages` follow-up below), or both.
- `web/` deploys separately (`vercel --prod` from `web/`), and the client must
  report `PagesNotDeployed` when the route isn't there yet.
**Decide in planning:**
- **Who may delete.** The agent acts for anyone in the channel, and page
  content or channel history can prompt-inject it. Options: slash command only
  with `manage_messages`, or an agent tool that asks for confirmation. Probably
  keep it off the default tool list either way.
- **Hosted images.** Images uploaded with `host_image` live in Vercel Blob with
  no TTL and aren't tied to a page, so deleting a page leaves them behind.
  Decide whether deleting a page also removes the images only it uses.
- Whether deletion is immediate or soft (hidden, then purged after a few days)
  so a mistaken delete can be undone.
**Unblocks:** removing the six stray wishlist copies. On 2026-09-17 seven
overlapping "Bot fails" pages in guild `844003671334977607` were merged into
`bot-fails-wishlist`; the others (slugs `bot-fails`, `bot-fails-4065083ecb10e71f`,
`bot-fails-4065083ecb10e71f-copy`, `bot-fails-master`,
`bot-fails-wishlist-a126d3329c649ae0`, `didnt-work-list`) are still live until
they expire.

---

## Follow-ups

### Confirm the unregistered-mention fix
Mentioning the bot in an unregistered channel was long reported broken despite
#36. #51 fixes the two likeliest causes without having confirmed either: a
mention of the bot's managed role (`<@&role>`) now summons it, and threads and
forum posts are handled (a thread uses its own registration, else its parent's,
else the unregistered defaults). After deploy, mention the bot where it failed
and grep `logs/` for `agent_summoned_unregistered`:
- **No event:** the gate still rejects it -- the cause is something else.
- **Event but no reply:** most likely the bot lacks Read Message History there.
  `channel.history()` raises Forbidden inside `_handle_agent_response`, which
  only logs `Agent error in channel ...` and posts nothing.
A registered-but-paused channel stays silent by design and looks unregistered.

### `-pro` and `-codex` models need the Responses API
`gpt-5.5-pro` and `gpt-5.3-codex` are in the account's `/v1/models` listing but
only serve `/v1/responses`; the bot only calls chat completions. Two such models
shipped in `/chat` as guaranteed errors before #39 removed them. Supporting them
means a second client path in `bot/api/openai/`. Run `scripts/check_models.py`
after any catalog change.

### `/image` shows a rate-limit message for any Gemini error
The catch-all `except` in `ImageCog._image_handler` shows the Gemini rate-limit
message whenever the model is Gemini (`or is_gemini`), whatever the error
actually was, so real bugs look like quota problems. Only show it for an actual
`RATE_LIMIT:` result. Found while fixing the tests in #52.

### "generateding" in the `/image` error message
The generic error builds `f"{action_type}ing"` from `action_type="generated"`,
so users see "while generateding the image".

### The test suite needs `OPENAI_API_KEY`, and one test calls the API
Without the variable set, 5 test modules fail to import. With it set,
`tests/test_llm_client.py::test_live_chat_openai` makes a real chat call (and
fails with a dummy key). Until #52, three `/image` tests also made real, billed
image calls. Make the modules importable without a key and skip live tests
unless explicitly enabled (e.g. a marker plus an env flag).

### `int | None` in the Gemini clients
Both Gemini clients annotate a local as `retry_after_seconds: int | None`.
Harmless (local annotations aren't evaluated) but breaks the `Optional[...]`
rule; fix when next touching those files.

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
| — | Dated summaries publish as one-off pages instead of overwriting each other | #47 |
| — | Page tools backfilled into all 17 registered channels (ops, 2026-09-17) | — |
| — | Seven "Bot fails" pages merged into `bot-fails-wishlist` (ops, 2026-09-17) | — |
| — | `GITHUB_TOKEN` / `GITHUB_ISSUE_REPO` passed to the container | #49 |
| — | `DiceRoller` moved to `bot/domain/dice/` | #50 |
| — | Bot answers mentions of its managed role and in threads (unconfirmed fix) | #51 |
| — | Seven stale tests fixed; suite green, no more live image calls | #52 |
| — | `/bot` one-shot agent command with channel history | #53 |
| — | `search_news` over the 7-day story history (news search step 1) | #54 |
