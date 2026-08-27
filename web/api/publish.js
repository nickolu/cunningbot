import {
  redis, KEY_PREFIX, MAX_HTML_BYTES, MAX_SOURCE_BYTES, DEFAULT_TTL_DAYS,
  MAX_TTL_DAYS, normalizeId, normalizeGuildId, isAuthorized, indexKey,
} from "./_lib.js";

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("allow", "POST");
    return res.status(405).json({ error: "method_not_allowed" });
  }
  if (!isAuthorized(req)) {
    return res.status(401).json({ error: "unauthorized" });
  }

  const body = typeof req.body === "string" ? safeParse(req.body) : req.body;
  if (!body) return res.status(400).json({ error: "invalid_json" });

  const id = normalizeId(body.id);
  if (!id) return res.status(400).json({ error: "invalid_id" });

  // The guild that owns this page. Asserted by the bot, which is the only
  // holder of PUBLISH_TOKEN; used to stop one guild overwriting another's page.
  const guildId = normalizeGuildId(body.guild_id);
  if (!guildId) {
    return res.status(400).json({ error: "invalid_guild_id" });
  }

  const html = body.html;
  if (typeof html !== "string" || !html.trim()) {
    return res.status(400).json({ error: "missing_html" });
  }
  if (Buffer.byteLength(html, "utf8") > MAX_HTML_BYTES) {
    return res.status(413).json({ error: "html_too_large", max_bytes: MAX_HTML_BYTES });
  }

  // Optional: the Markdown this page was rendered from. Kept alongside the html
  // under the same key so it expires with the page rather than outliving it.
  // Pages published before this existed simply have none.
  const markdown = typeof body.markdown === "string" ? body.markdown : "";
  if (Buffer.byteLength(markdown, "utf8") > MAX_SOURCE_BYTES) {
    return res.status(413).json({ error: "source_too_large", max_bytes: MAX_SOURCE_BYTES });
  }

  let ttlDays = Number(body.ttl_days ?? DEFAULT_TTL_DAYS);
  if (!Number.isFinite(ttlDays) || ttlDays <= 0) ttlDays = DEFAULT_TTL_DAYS;
  ttlDays = Math.min(ttlDays, MAX_TTL_DAYS);

  const key = KEY_PREFIX + id;

  // Ownership check: an id may only ever be rewritten by the guild that created it.
  const existing = await redis.get(key);
  if (existing && existing.guild_id && existing.guild_id !== guildId) {
    return res.status(409).json({ error: "id_owned_by_another_guild" });
  }

  const now = new Date().toISOString();
  const record = {
    guild_id: guildId,
    title: typeof body.title === "string" ? body.title.slice(0, 200) : "",
    html,
    // Republishing without source must not resurrect stale source from the
    // previous version -- it would no longer describe the html beside it.
    markdown,
    created_at: existing?.created_at || now,
    updated_at: now,
  };

  const ttlSeconds = Math.round(ttlDays * 86400);
  await redis.set(key, record, { ex: ttlSeconds });

  // Index the page for discovery. Failing here must not fail the publish: the
  // page itself is stored and served, and the worst case is that it is missing
  // from a listing until it is republished.
  try {
    const index = indexKey(guildId);
    await redis.zadd(index, { score: Date.parse(now), member: id });
    await redis.expire(index, MAX_TTL_DAYS * 86400);
  } catch (e) {
    console.error("index update failed for %s: %s", id, e?.message || e);
  }

  const host = req.headers["x-forwarded-host"] || req.headers.host;
  const proto = req.headers["x-forwarded-proto"] || "https";

  return res.status(200).json({
    id,
    url: `${proto}://${host}/p/${id}`,
    updated: Boolean(existing),
    expires_in_days: ttlDays,
  });
}

function safeParse(s) {
  try { return JSON.parse(s); } catch { return null; }
}
