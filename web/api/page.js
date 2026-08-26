import { redis, KEY_PREFIX, PAGE_CSP, normalizeId, notFoundHtml } from "./_lib.js";

export default async function handler(req, res) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.setHeader("allow", "GET, HEAD");
    return res.status(405).end();
  }

  const id = normalizeId(req.query.id);
  const record = id ? await redis.get(KEY_PREFIX + id) : null;

  res.setHeader("content-type", "text/html; charset=utf-8");
  res.setHeader("content-security-policy", PAGE_CSP);
  res.setHeader("x-content-type-options", "nosniff");
  res.setHeader("referrer-policy", "no-referrer");
  // Unlisted URLs: keep them out of shared caches and search engines.
  res.setHeader("cache-control", "private, max-age=0, must-revalidate");
  res.setHeader("x-robots-tag", "noindex, nofollow");

  // Same response whether the page expired or never existed — no enumeration signal.
  if (!record || typeof record.html !== "string") {
    return res.status(404).send(notFoundHtml());
  }

  return res.status(200).send(record.html);
}
