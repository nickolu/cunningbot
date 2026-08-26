# cunningbot-pages

The public face of CunningBot: the bot POSTs rendered HTML here and gets back a
URL it can drop in a Discord message.

```
bot on the Pi ──POST /api/publish──▶ this app ──▶ Upstash Redis (TTL)
                 Bearer PUBLISH_TOKEN     │
                                          └──▶ GET /p/<id> serves the page
```

The Pi only makes outbound HTTPS calls — nothing is exposed on the home network.

## Routes

| Route | Purpose |
|---|---|
| `POST /api/publish` | Store a page. Requires `Authorization: Bearer $PUBLISH_TOKEN`. Body: `{id, guild_id, title, html, ttl_days?}`. Returns `{id, url, updated, expires_in_days}`. |
| `GET /p/<id>` | Serve the page as `text/html`. 404s identically for expired and never-existed ids. |
| `POST /api/upload` | Store an image in Blob and return a permanent URL. Same bearer token. Raw image bytes as the body; `content-type` plus `X-Guild-Id`, `X-Guild-Prefix`, and optional `X-Filename` headers. PNG/JPEG/WebP/GIF only, 4 MB max. |

## Why images are hosted here

Discord CDN links carry an expiring signature and stop working about a day after
they are issued. That is fine inside a live conversation -- the bot re-reads
history and gets freshly signed URLs -- but it breaks anything that *stores* a
URL. A published page embedding a Discord attachment looks right when created
and has broken images the next day. Uploading is therefore on demand: it happens
when a URL needs to outlive the conversation, not for every generated image.

Images go to the `cunningbot-images` public Blob store (Blob serves images with
`content-disposition: inline`; only HTML is blocked). Blob has **no TTL** -- these
URLs are permanent until something deletes them, so storage grows monotonically.

## Multi-tenancy

CunningBot runs in several Discord servers and they share this host, so page
ids are the isolation boundary:

- The **bot** derives every id as `<slug>-<hmac(guild_id, slug)>` using
  `PAGES_ID_SECRET`, which lives only on the bot host. Uploaded images use the
  same secret for their storage prefix (`img/<hmac>/...`), sent as
  `X-Guild-Prefix` -- this service validates the prefix's shape but never learns
  the secret. `addRandomSuffix` then makes each individual image URL unguessable
  on its own. Discord guild ids are
  public, so an id must not be derivable from one — that secret is what makes
  another server's URL unguessable. **Do not set `PAGES_ID_SECRET` here.**
- This **service** records the owning `guild_id` on each page and rejects a
  publish that would overwrite an id belonging to a different guild (HTTP 409).

Consequence worth knowing: rotating `PAGES_ID_SECRET` changes every future URL,
orphaning previously shared links until their TTL expires.

## Security posture

Pages contain model-authored content, so every page is served with
`content-security-policy: default-src 'none'; style-src 'unsafe-inline'; img-src https: data:`.
Script cannot run, which makes a prompt-injected page inert. Pages also carry
`x-robots-tag: noindex` and `cache-control: private` — the URLs are unlisted,
not authenticated. Treat a page link the way you'd treat a Google Drive
"anyone with the link" share.

Publishing rejects bodies over 512 KB and clamps TTL to 365 days.

## Setup

1. **Create the Vercel project** from the `cunningbot` repo, and set
   **Root Directory** to `web`. To avoid rebuilding on every bot-only commit,
   set the project's *Ignored Build Step* to:
   ```
   git diff --quiet HEAD^ HEAD -- .
   ```
2. **Add storage:** Vercel dashboard → Storage → Upstash Redis (Marketplace).
   Vercel KV and Vercel Postgres were retired in Dec 2024; Upstash is the
   supported KV path. Linking it injects `KV_REST_API_URL` / `KV_REST_API_TOKEN`,
   which `Redis.fromEnv()` picks up automatically.
3. **Set the shared secret** in Vercel → Settings → Environment Variables:
   ```
   PUBLISH_TOKEN=<openssl rand -hex 32>
   ```
4. **Configure the bot** — add to `.env` on the Pi:
   ```
   PAGES_BASE_URL=https://<your-project>.vercel.app
   PAGES_PUBLISH_TOKEN=<the same value as PUBLISH_TOKEN>
   PAGES_ID_SECRET=<a different openssl rand -hex 32>
   ```
   These must exist on the Pi *before* the code that reads them deploys, or the
   `cunningbot` container starts without the publishing tool available. (It
   degrades gracefully — the tool just reports it isn't configured.)
5. **Smoke test** once deployed:
   ```bash
   curl -sS -X POST https://<project>.vercel.app/api/publish \
     -H "Authorization: Bearer $PUBLISH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"id":"smoke-test","guild_id":"1","title":"Smoke","html":"<h1>hello</h1>"}'
   ```
   Then open the returned `url`.

## Local development

```bash
npm install
npx vercel dev
```
Needs `PUBLISH_TOKEN` plus the Upstash variables in `.env.local`.
