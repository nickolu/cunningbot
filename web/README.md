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
| `GET /api/pages?guild_id=<id>` | List that guild's pages, newest first. Same bearer token. Returns `{pages: [{id, title, has_source, created_at, updated_at}]}`. |
| `GET /api/pages?guild_id=<id>&id=<page_id>` | Return one page's Markdown source, so the bot can append to a page instead of replacing it. `{id, title, markdown, created_at, updated_at}`; `markdown` is `null` for pages published before source was stored. |
| `POST /api/pages` | Rebuild every guild's page index by scanning stored pages. Run once after deploying; idempotent. |
| `POST /api/upload` | Store an image in Blob and return a permanent URL. Same bearer token. Raw image bytes as the body; `content-type` plus `X-Guild-Id`, `X-Guild-Prefix`, and optional `X-Filename` headers. PNG/JPEG/WebP/GIF only, 4 MB max. |
| `POST /api/upload-sessions` | Open an upload link for someone in Discord. Same bearer token. Body: `{guild_id, channel_id?, channel_name?, requester_id?, requester_name?, label?, kind?, max_files?, max_bytes?, ttl_days?}`. Returns `{session_id, url, expires_at, ...}`. 503 until the bucket is configured. |
| `GET /api/upload-sessions?id=<sid>&guild_id=<id>` | The session and every file it has received so far, for the bot to report back. Same bearer token. |
| `GET /u/<sid>` | The upload page behind a link. Shows the limits, takes files, closes 15 minutes after the session was opened. |
| `POST /api/upload-sign` | Called by the upload page per file: `{session_id, name, content_type, bytes, note?}`. Returns a signed PUT URL for the bucket plus the headers that must accompany it. No token: the session id is the credential. |
| `POST /api/upload-complete` | Called by the upload page after the PUT: `{session_id, file_id}`. Confirms the object exists in the bucket with the signed type and size, then records it in the catalog. |
| `GET /f/<id>` | Stable download URL for an uploaded file. Redirects to a 10-minute signed URL; 404s identically for expired and never-existed ids. |
| `GET /api/files?guild_id=<id>&q=<words>&limit=<n>` | That guild's uploaded files, newest first, filtered by words in the name, note, label, uploader, or type. Same bearer token. |
| `DELETE /api/files?guild_id=<id>&id=<file>` | Remove a file: catalog record first, then the object. Same bearer token. |

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

## User uploads live in Firebase Storage

People can send files *to* the bot too: it opens an **upload session** and posts
a link (`/u/<sid>`), the page behind it takes files for fifteen minutes, and the
bot can list and search what arrived. Nobody logs in -- the Discord conversation
the link was posted in is the audience, and the link is the capability. The
session bounds what the link can be made to accept: how many files, how large,
images only or anything, and for how long they are kept (1, 7, or 30 days).

Files go to a **private** Firebase Storage bucket, which is a Google Cloud
Storage bucket this service drives with a service account:

- The browser PUTs straight to a **V4 signed URL**. The signature binds the
  content type and a `x-goog-content-length-range`, so the bucket itself
  refuses a different type or a larger body. No 4 MB body cap, no file bytes
  through this service.
- `/api/upload-complete` reads the object's metadata before the file enters the
  catalog, so a client cannot register something it never uploaded.
- Every download goes through `/f/<id>`, which redirects to a short-lived
  signed URL. Nothing in the bucket is public; the URL to share is stable; and
  deleting the catalog record revokes the file at once.
- **Expiry is the bucket's job.** Objects live under `drops/1d/`, `drops/7d/`,
  and `drops/30d/`, and a lifecycle rule per prefix deletes them at that age.
  The catalog record gets the same TTL. No cron.
- Script-bearing types (`text/html`, `image/svg+xml`, JavaScript) are never
  signed, and anything that is not an image, video, audio, PDF, or plain text
  downloads as an attachment.

The catalog is a per-guild sorted set on Upstash (`guild-files:<guild_id>`),
the same pattern as the page index: members are pruned when a listing finds
their record gone. Search is word matching over name, note, label, uploader,
and type -- fine for a few hundred files; a store with real full-text search is
a later decision.

## Pages remember their source

A page record holds the Markdown it was rendered from as well as the html. The
bot could otherwise only ever replace a page: asked to "add this to the list",
it had no way to read back what the list already said, so it published a new
page containing only the new item.

Source lives under the same key as the html rather than somewhere on the bot,
so it expires exactly when the page does and there is no second store to keep
in sync. Pages published before this have `markdown: null`, which the bot
reports rather than treating as an empty page.

Discovery works off a per-guild sorted set (`guild-pages:<guild_id>`), scored by
update time. A set member has no TTL of its own, so listings prune ids whose
page has expired instead of a scheduled job doing it. Indexing happens on
publish, which means pages that predate it are invisible until `POST /api/pages`
rebuilds the indexes.

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

The upload page (`/u/<sid>`) is the one page here that runs script, and it has
its own CSP: `script-src 'self'` for `/upload-page.js`, `connect-src` to this
origin and `storage.googleapis.com`, nothing else. The shared page CSP is
unchanged.

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
5. **Set up the upload bucket** (optional -- without it the upload routes answer
   503 and the bot reports uploads as unavailable; everything else works).
   Use a **separate Firebase project** from anything else you run, so a rules
   or IAM mistake stays contained.
   1. Firebase console → create a project → Build → Storage → Get started.
      Keep the default security rules (deny all): this service never uses the
      client SDK, and the Admin SDK bypasses rules. Note the bucket name
      (`<project>.firebasestorage.app` or `<project>.appspot.com`).
   2. Google Cloud console for that project → IAM → Service Accounts → create
      one (e.g. `cunningbot-uploads`) with the **Storage Object Admin** role on
      the project, then Keys → Add key → JSON. Signing happens locally with the
      key, so no extra IAM permission is needed.
   3. CORS, so browsers may PUT to the bucket from this app's origin. Save as
      `cors.json` and apply with
      `gcloud storage buckets update gs://<bucket> --cors-file=cors.json`:
      ```json
      [{
        "origin": ["https://<your-project>.vercel.app"],
        "method": ["PUT"],
        "responseHeader": ["Content-Type", "x-goog-content-length-range"],
        "maxAgeSeconds": 3600
      }]
      ```
   4. Lifecycle rules, one per TTL tier. Save as `lifecycle.json` and apply with
      `gcloud storage buckets update gs://<bucket> --lifecycle-file=lifecycle.json`:
      ```json
      {"rule": [
        {"action": {"type": "Delete"}, "condition": {"age": 1,  "matchesPrefix": ["drops/1d/"]}},
        {"action": {"type": "Delete"}, "condition": {"age": 7,  "matchesPrefix": ["drops/7d/"]}},
        {"action": {"type": "Delete"}, "condition": {"age": 30, "matchesPrefix": ["drops/30d/"]}}
      ]}
      ```
   5. Vercel → Settings → Environment Variables:
      ```
      GCS_BUCKET=<bucket name, no gs://>
      GCS_SERVICE_ACCOUNT=<the key JSON, pasted whole, or base64 of it>
      ```
      Redeploy. The bot needs nothing new: it only ever talks to this service.
6. **Smoke test** once deployed:
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
Needs `PUBLISH_TOKEN` plus the Upstash variables in `.env.local`; add
`GCS_BUCKET` and `GCS_SERVICE_ACCOUNT` to exercise uploads. `npm test` runs the
unit tests for the upload helpers.
