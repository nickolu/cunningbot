import { redis } from "./_lib.js";
import {
  IMAGE_TYPES, KINDS, SESSION_COUNT_PREFIX, SESSION_PREFIX,
  escapeHtml, formatBytes, sessionIsOpen,
} from "./_files_lib.js";

/**
 * GET /u/<session id> -- the page people upload from.
 *
 * It is the one page on this host that runs script, so it gets its own CSP:
 * one first-party script (`/upload-page.js`), fetches to this origin and PUTs
 * to the bucket, nothing else. The shared page CSP in `_lib.js` stays as it is.
 * The page carries no secret: everything the script needs is a session id that
 * was already in the URL, and the server re-checks the session on every call.
 */
const UPLOAD_PAGE_CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'unsafe-inline'",
  "connect-src 'self' https://storage.googleapis.com",
  "img-src 'self' data: blob:",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

export default async function handler(req, res) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.setHeader("allow", "GET, HEAD");
    return res.status(405).end();
  }

  res.setHeader("content-type", "text/html; charset=utf-8");
  res.setHeader("content-security-policy", UPLOAD_PAGE_CSP);
  res.setHeader("x-content-type-options", "nosniff");
  res.setHeader("referrer-policy", "no-referrer");
  res.setHeader("cache-control", "private, no-store");
  res.setHeader("x-robots-tag", "noindex, nofollow");

  const sessionId = String(req.query.id || "").trim().toLowerCase();
  const session = /^[0-9a-f]{24}$/.test(sessionId)
    ? await redis.get(SESSION_PREFIX + sessionId)
    : null;

  if (!session) {
    return res.status(404).send(shell("Nothing here", `<h1>Nothing here</h1>
<p>This upload link has expired or never existed. Ask the bot for a new one.</p>`));
  }
  if (!sessionIsOpen(session)) {
    return res.status(410).send(shell("Link closed", `<h1>This link has closed</h1>
<p>Upload links stay open for fifteen minutes. Ask the bot for a new one.</p>`));
  }

  const used = Number(await redis.get(SESSION_COUNT_PREFIX + sessionId)) || 0;
  const remaining = Math.max(0, session.max_files - used);

  const who = session.requester_name ? escapeHtml(session.requester_name) : "someone";
  const where = session.channel_name ? ` in <b>#${escapeHtml(session.channel_name)}</b>` : "";
  const label = session.label ? `<h1>${escapeHtml(session.label)}</h1>` : "<h1>Upload files</h1>";
  const accept = session.kind === "images" ? IMAGE_TYPES.join(",") : "";
  const ttl = session.ttl_days === 1 ? "1 day" : `${session.ttl_days} days`;

  const body = `
<main
  data-session="${sessionId}"
  data-max-files="${session.max_files}"
  data-max-bytes="${session.max_bytes}"
  data-remaining="${remaining}"
  data-kind="${escapeHtml(session.kind)}"
  data-accept="${accept}"
>
  ${label}
  <p class="meta">Requested by ${who}${where}. Files are kept for ${ttl}.</p>
  <p class="meta limits">${escapeHtml(KINDS[session.kind] || KINDS.any)} &middot; up to ${formatBytes(session.max_bytes)} each &middot; <span id="remaining">${remaining}</span> of ${session.max_files} left</p>

  <label class="drop" id="drop">
    <input type="file" id="picker" ${accept ? `accept="${accept}"` : ""} multiple ${remaining ? "" : "disabled"}>
    <span class="drop-text">${remaining ? "Choose files or drop them here" : "This link has taken all the files it can"}</span>
  </label>

  <ul id="rows" class="rows"></ul>

  <div class="actions">
    <button id="upload" type="button" disabled>Upload</button>
    <span id="status" class="meta"></span>
  </div>

  <p class="meta fine">Anyone with this link can upload until it closes. Links expire fifteen minutes after they are created.</p>
</main>
<script src="/upload-page.js" defer></script>`;

  return res.status(200).send(shell(session.label || "Upload files", body));
}

function shell(title, body) {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>
  :root { color-scheme: light dark;
    --bg: #fbfbfa; --fg: #1c1f23; --muted: #6b7280; --line: #d9d9d6; --accent: #2563eb;
    --ok: #15803d; --bad: #b91c1c; --card: #ffffff; }
  @media (prefers-color-scheme: dark) { :root {
    --bg: #16181a; --fg: #e8e6e3; --muted: #9aa0a6; --line: #33363a; --accent: #60a5fa;
    --ok: #4ade80; --bad: #f87171; --card: #1e2124; } }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg);
    font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 640px; margin: 0 auto; padding: 32px 16px 48px; }
  h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
  .meta { color: var(--muted); margin: 0 0 .5rem; font-size: .95rem; }
  .fine { font-size: .85rem; margin-top: 1.5rem; }
  .drop { display: block; margin: 1.25rem 0 1rem; padding: 2rem 1rem; text-align: center;
    border: 2px dashed var(--line); border-radius: 12px; cursor: pointer; background: var(--card); }
  .drop.over { border-color: var(--accent); }
  .drop input { position: absolute; width: 1px; height: 1px; opacity: 0; }
  .drop-text { color: var(--muted); }
  .rows { list-style: none; margin: 0; padding: 0; }
  .rows li { display: grid; grid-template-columns: 1fr auto; gap: .25rem .75rem; align-items: center;
    padding: .75rem 0; border-top: 1px solid var(--line); }
  .rows li:last-child { border-bottom: 1px solid var(--line); }
  .name { font-weight: 600; overflow-wrap: anywhere; }
  .size { color: var(--muted); font-size: .9rem; white-space: nowrap; }
  .note { grid-column: 1 / -1; width: 100%; padding: .4rem .6rem; border: 1px solid var(--line);
    border-radius: 8px; background: var(--bg); color: var(--fg); font: inherit; font-size: .95rem; }
  .state { grid-column: 1 / -1; font-size: .9rem; color: var(--muted); overflow-wrap: anywhere; }
  .state.ok { color: var(--ok); } .state.bad { color: var(--bad); }
  .bar { grid-column: 1 / -1; height: 4px; background: var(--line); border-radius: 2px; overflow: hidden; }
  .bar > i { display: block; height: 100%; width: 0; background: var(--accent); transition: width .15s; }
  .actions { display: flex; gap: 1rem; align-items: center; margin-top: 1rem; flex-wrap: wrap; }
  button { font: inherit; font-weight: 600; padding: .55rem 1.2rem; border-radius: 8px; border: 0;
    background: var(--accent); color: #fff; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  .remove { background: none; color: var(--muted); padding: 0 .25rem; font-weight: 400; }
  a { color: var(--accent); }
</style></head>
<body>${body}</body></html>`;
}
