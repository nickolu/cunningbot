import {
  redis, KEY_PREFIX, MAX_HTML_BYTES, DEFAULT_TTL_DAYS, MAX_TTL_DAYS,
  normalizeId, isAuthorized,
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
  const guildId = typeof body.guild_id === "string" ? body.guild_id.trim() : "";
  if (!/^[0-9]{1,25}$/.test(guildId)) {
    return res.status(400).json({ error: "invalid_guild_id" });
  }

  const html = body.html;
  if (typeof html !== "string" || !html.trim()) {
    return res.status(400).json({ error: "missing_html" });
  }
  if (Buffer.byteLength(html, "utf8") > MAX_HTML_BYTES) {
    return res.status(413).json({ error: "html_too_large", max_bytes: MAX_HTML_BYTES });
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
    created_at: existing?.created_at || now,
    updated_at: now,
  };

  await redis.set(key, record, { ex: Math.round(ttlDays * 86400) });

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
