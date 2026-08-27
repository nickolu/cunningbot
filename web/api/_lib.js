import { Redis } from "@upstash/redis";
import { timingSafeEqual } from "node:crypto";

// Reads UPSTASH_REDIS_REST_URL/TOKEN, falling back to KV_REST_API_URL/TOKEN,
// both of which the Vercel <-> Upstash marketplace integration may inject.
export const redis = Redis.fromEnv();

export const KEY_PREFIX = "page:";
export const MAX_HTML_BYTES = 512 * 1024;
// The Markdown a page was rendered from, stored so the bot can read a page back
// and append to it instead of replacing it. Capped separately rather than
// sharing the html budget: source is always smaller than the html it produces,
// and a combined cap would shrink the largest page that can be published.
export const MAX_SOURCE_BYTES = 512 * 1024;
export const DEFAULT_TTL_DAYS = 30;
export const MAX_TTL_DAYS = 365;
// Newest-first index of a guild's page ids, so the bot can answer "what pages
// do we have?". Members are not removed when their page expires -- reads prune
// what has gone, which costs one round trip and needs no scheduled job.
export const INDEX_PREFIX = "guild-pages:";
// A listing reads this many ids before pruning. Comfortably more than any
// server will accumulate inside one TTL window.
export const INDEX_MAX_ENTRIES = 200;

export function indexKey(guildId) {
  return INDEX_PREFIX + guildId;
}

/** Guild ids are Discord snowflakes; anything else is a client bug. */
export function normalizeGuildId(raw) {
  const id = typeof raw === "string" ? raw.trim() : "";
  return /^[0-9]{1,25}$/.test(id) ? id : null;
}

/**
 * Page ids arrive already scoped to a guild by the bot: "<slug>-<hmac>", where
 * the hmac is derived from the guild id and a secret only the bot holds. This
 * service never derives ids itself — it only validates their shape and enforces
 * that a given id keeps belonging to the guild that first claimed it.
 */
export function normalizeId(raw) {
  if (typeof raw !== "string") return null;
  const id = raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  if (!id || id.length > 128) return null;
  return id;
}

/** Constant-time bearer check against PUBLISH_TOKEN. */
export function isAuthorized(req) {
  const expected = process.env.PUBLISH_TOKEN;
  if (!expected) return false;

  const header = req.headers.authorization || "";
  const presented = header.startsWith("Bearer ") ? header.slice(7) : "";

  const a = Buffer.from(presented);
  const b = Buffer.from(expected);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

/**
 * Pages hold model-authored HTML. Nothing else lives on this origin and there
 * are no cookies to steal, but blocking script outright keeps a prompt-injected
 * page from doing anything at all. Styles and images are all a page needs.
 */
export const PAGE_CSP =
  "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; font-src https:; base-uri 'none'; form-action 'none'";

export function notFoundHtml() {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Not found</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
         display: grid; place-content: center; min-height: 100vh; margin: 0; text-align: center;
         color: #1c1f23; background: #fbfbfa; }
  @media (prefers-color-scheme: dark) { body { color: #e8e6e3; background: #16181a; } }
  p { color: #6b7280; }
</style></head>
<body><div><h1>Nothing here</h1><p>This page has expired or never existed.</p></div></body></html>`;
}
