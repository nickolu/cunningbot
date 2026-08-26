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
| `agent_tools.py` → `publish_page`, `host_image` | The agent-facing tools |

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

The `slug` decides:

- **With a slug** — `restaurants` always resolves to the same URL for that
  guild. Republish whenever the underlying data changes and the previously
  shared link shows the new content. This is what a "living" list wants.
- **Without a slug** — a random id, so every publish is a new page. Right for
  snapshots: a reasoning trace, a weekly summary, a one-time report.

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

## Configuration

`PAGES_BASE_URL`, `PAGES_PUBLISH_TOKEN`, `PAGES_ID_SECRET` on the bot (see
`.env.example`); `PUBLISH_TOKEN` plus the Upstash variables on Vercel.
`PAGES_PUBLISH_TOKEN` and `PUBLISH_TOKEN` must match; `PAGES_ID_SECRET` is
bot-only and must **not** be set on Vercel.

Missing configuration is not fatal — the tool reports that publishing isn't
available and the rest of the bot runs normally.
