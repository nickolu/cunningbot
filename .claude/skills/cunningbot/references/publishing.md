# Publishing web pages

The bot can render content as a web page and hand back a link. Used for lists,
tables, long write-ups, and anything worth reading outside Discord.

## Pieces

| Where | What |
|---|---|
| `web/` | Vercel app: `POST /api/publish`, `GET /p/<id>`, Upstash Redis with TTL. Setup steps in `web/README.md`. |
| `bot/domain/pages/page_ids.py` | Derives guild-scoped, unguessable page ids |
| `bot/domain/pages/page_renderer.py` | Markdown → styled, self-contained HTML |
| `bot/domain/pages/page_service.py` | `publish_page(...)` — the one entry point |
| `bot/api/pages/client.py` | HTTP client for the Vercel app |
| `bot/domain/pages/image_service.py` | Hosting images at stable URLs |
| `bot/domain/agent/tools/publish_page.py`, `list_pages.py`, `read_page.py`, `host_image.py` | The agent-facing tools |

## Images expire unless you host them

**Never embed a Discord CDN URL in a published page.** Discord links carry an
expiring signature and die after about a day, so the page renders correctly when
created and has broken images the next day. The live `edit_image` flow is fine —
history is re-read each turn and returns freshly signed URLs — but anything
*stored* must be re-hosted first:

```python
from bot.domain.pages.image_service import host_image_from_url

url = await host_image_from_url(guild_id, discord_attachment_url, filename="art")
```

The agent does this via `host_image` before `publish_page`. Uploads are **on
demand** — normal `/image` use still just posts a Discord attachment, because
Blob has no TTL and every upload is permanent until deleted.

## Publishing from code

```python
from bot.domain.pages.page_service import publish_page

url = await publish_page(
    guild_id=guild_id_to_str(interaction.guild_id),
    title="Restaurants to Visit",
    markdown="- Yummy Noodles — Kearny Mesa\n- ...",
    slug="restaurants",        # omit for a one-off page
    guild_name=interaction.guild.name,
)
```

Raises `EnvironmentError` when publishing isn't configured and `RuntimeError`
when the service refuses or is unreachable. Callers must handle both — an agent
tool returns the message as a string; a cog sends it as an ephemeral reply.

## Stable vs one-off pages

Pages are stable **by default**: with no `slug`, one is derived from the title.
Pass `one_off=True` for a snapshot that should never be updated -- a reasoning
trace, or a summary of a particular day or week -- and it gets a random id
instead.

That default is deliberate. A random id is derived from `secrets.token_hex(4)`
and can never be recomputed from the title, so before the per-guild index
existed an unslugged page was unfindable once its URL scrolled out of the
channel, and asked to add to one the agent could only publish a second page
holding the new item alone.

The flip side bit dated content: "Today's Chat Summary" got the same derived
slug every day, so each summary replaced the last and yesterday's shared link
showed today's. The `publish_page` schema and system prompt now tell the agent
to publish dated summaries with `one_off=true` (#47).

**One-offs are still findable.** `web/api/publish.js` adds *every* page to the
guild's index, so `list_pages` returns one-offs too -- the agent can find last
Tuesday's summary by title. They just can't be updated in place.

Slugged pages live 365 days (`STABLE_TTL_DAYS`); one-offs get 30
(`SNAPSHOT_TTL_DAYS`). A living list that expires a month after its last edit is
the opposite of what it is for.

## Reading a page back

`list_pages(guild_id)` returns what a server has published, newest first;
`read_page(guild_id, reference)` takes a slug, a page id, or a URL and returns
the stored Markdown.

**`markdown` is `None` for a page published before source storage existed.** That
is not an empty page -- republishing over it destroys content nobody has seen.
The tool says so instead, and the system prompt tells the agent to surface it.

`web/` deploys separately from the bot, so the bot can be ahead of it. When it
is, `/api/pages` does not exist and Vercel answers with an HTML 404 rather than
the API's JSON one; the client raises `PagesNotDeployed` on that distinction and
the tools report the feature as unavailable instead of erroring.

## Multi-tenancy — read before touching page ids

Discord guild ids are **public** (they're in every channel URL), so a page id
must never be a plain function of one. Ids are
`<slug>-<hmac_sha256(PAGES_ID_SECRET, "guild_id:slug")[:16]>`:

- stable per guild+slug, so republishing updates in place;
- unguessable across guilds without the secret, which never leaves the bot host;
- readable, so a pasted link still says what it is.

The Vercel app independently records the owning guild on each page and returns
**409** if a different guild tries to overwrite that id.

`tests/test_pages.py` pins this behavior — in particular that the same slug in
two guilds yields different ids and that the guild id never appears in a URL.
If you change id derivation, those tests must stay green, and note that
rotating `PAGES_ID_SECRET` orphans every previously shared link.

## Content rules

- **Callers pass Markdown, not HTML.** `markdown-it-py` runs with `html=False`
  so embedded raw HTML is escaped. Note the `commonmark` preset turns HTML
  parsing **on** — the option is set explicitly and must stay that way.
- Pages are served with `default-src 'none'` and no script execution. Don't
  design a page that needs JavaScript without revisiting the CSP in
  `web/api/_lib.js` first.
- All styling comes from the shell in `page_renderer.py`; it is light/dark aware
  and has no external assets. Change styling there so every page stays consistent.
- 512 KB limit per page; default TTL 30 days, 365 max.

## User uploads (files *to* the bot)

The other direction: someone wants to give the bot files. Discord attachments
expire, so the bot opens an **upload session** on the pages service and hands
back a link (`/u/<sid>`); the page takes files for fifteen minutes with no
login; files land in a private Firebase Storage bucket with a stable download
URL (`/f/<id>`) and a per-guild catalog the bot can search.

| Where | What |
|---|---|
| `bot/domain/files/file_service.py` | `open_upload_link`, `search_files`, `delete_file`, `describe_file` -- the one entry point |
| `bot/api/pages/client.py` | `create_upload_session`, `get_upload_session`, `list_files`, `delete_file`; raises `UploadsNotConfigured` when the service has no bucket |
| `bot/app/upload_watch.py` | Polls a session and posts one summary in the channel when the link closes or fills |
| `bot/domain/agent/tools/request_upload.py`, `search_files.py` | The agent-facing tools |
| `bot/app/commands/files/files.py` | `/files upload`, `/files list`, `/files delete` |
| `web/api/upload-*.js`, `file.js`, `files.js`, `public/upload-page.js` | The service side; setup in `web/README.md` |

Rules of thumb:
- **An uploaded image can go on a page by its `/f/` URL.** It is already
  stable; do not `host_image` it again.
- Keep-time is a tier (1, 7, 30 days) because each is a lifecycle rule on the
  bucket. `clamp_ttl` rounds *up* to the next tier; adding a tier means adding
  a rule.
- Delete stays a slash command (opener or `manage_messages`). Don't add an
  agent tool for it without revisiting the prompt-injection argument in the
  backlog.
- Everything degrades: no `PAGES_*` env, no bucket (`UploadsNotConfigured`),
  or an old `web/` deploy (`PagesNotDeployed`) each produce a plain sentence,
  never an exception in channel.

## Configuration

`PAGES_BASE_URL`, `PAGES_PUBLISH_TOKEN`, `PAGES_ID_SECRET` on the bot (see
`.env.example`); `PUBLISH_TOKEN` plus the Upstash variables on Vercel.
`PAGES_PUBLISH_TOKEN` and `PUBLISH_TOKEN` must match; `PAGES_ID_SECRET` is
bot-only and must **not** be set on Vercel.

Missing configuration is not fatal — the tool reports that publishing isn't
available and the rest of the bot runs normally.
