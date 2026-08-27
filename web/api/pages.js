import {
  redis, KEY_PREFIX, INDEX_MAX_ENTRIES, MAX_TTL_DAYS,
  normalizeId, normalizeGuildId, isAuthorized, indexKey,
} from "./_lib.js";

/**
 * Read side of the page store, for the bot rather than for browsers.
 *
 *   GET  /api/pages?guild_id=<id>            -> that guild's pages, newest first
 *   GET  /api/pages?guild_id=<id>&id=<page>  -> one page's Markdown source
 *   POST /api/pages                          -> rebuild every guild's index
 *
 * Both require the publish token. `guild_id` is mandatory on the single-page
 * read too: the bot holds one token for every server it serves, so without
 * that check a bug in one guild's code path could read another's content.
 * Pages are unlisted, not secret, but their contents should not be reachable
 * across guilds through this API.
 */
export default async function handler(req, res) {
  if (req.method !== "GET" && req.method !== "POST") {
    res.setHeader("allow", "GET, POST");
    return res.status(405).json({ error: "method_not_allowed" });
  }
  if (!isAuthorized(req)) {
    return res.status(401).json({ error: "unauthorized" });
  }

  if (req.method === "POST") return reindex(res);

  const guildId = normalizeGuildId(req.query.guild_id);
  if (!guildId) {
    return res.status(400).json({ error: "invalid_guild_id" });
  }

  res.setHeader("cache-control", "no-store");

  return req.query.id
    ? readSource(res, guildId, req.query.id)
    : listPages(res, guildId);
}

async function readSource(res, guildId, rawId) {
  const id = normalizeId(rawId);
  if (!id) return res.status(400).json({ error: "invalid_id" });

  const record = await redis.get(KEY_PREFIX + id);

  // Expired, never existed, or belongs to another guild all answer the same
  // way, so this endpoint cannot be used to probe for either.
  if (!record || record.guild_id !== guildId) {
    return res.status(404).json({ error: "not_found" });
  }

  return res.status(200).json({
    id,
    title: record.title || "",
    // Pages published before source was stored have none. The caller has to be
    // able to tell that apart from a page whose source is genuinely empty.
    markdown: typeof record.markdown === "string" ? record.markdown : null,
    created_at: record.created_at || null,
    updated_at: record.updated_at || null,
  });
}

async function listPages(res, guildId) {
  const index = indexKey(guildId);

  let ids = [];
  try {
    ids = (await redis.zrange(index, 0, INDEX_MAX_ENTRIES - 1, { rev: true })) || [];
  } catch (e) {
    console.error("index read failed for guild %s: %s", guildId, e?.message || e);
    return res.status(200).json({ pages: [] });
  }
  if (!ids.length) return res.status(200).json({ pages: [] });

  const records = await redis.mget(...ids.map((id) => KEY_PREFIX + id));

  const pages = [];
  const expired = [];
  ids.forEach((id, i) => {
    const record = records[i];
    // An index entry outlives the page it names -- a set member has no TTL of
    // its own. Reads drop what has gone rather than a scheduled job doing it.
    if (!record || record.guild_id !== guildId) {
      expired.push(id);
      return;
    }
    pages.push({
      id,
      title: record.title || "",
      has_source: typeof record.markdown === "string" && record.markdown.length > 0,
      created_at: record.created_at || null,
      updated_at: record.updated_at || null,
    });
  });

  if (expired.length) {
    try {
      await redis.zrem(index, ...expired);
    } catch (e) {
      console.error("index prune failed for guild %s: %s", guildId, e?.message || e);
    }
  }

  return res.status(200).json({ pages });
}

/**
 * Rebuild the per-guild indexes by scanning the stored pages.
 *
 * Indexing happens on publish, so every page that predates it is invisible to a
 * listing until someone happens to republish it -- which for a page nobody
 * knows the URL of is exactly never. Run this once after deploying. It is
 * idempotent: zadd overwrites the score for an id already present.
 */
async function reindex(res) {
  const perGuild = new Map();
  let cursor = "0";
  let scanned = 0;

  do {
    const [next, keys] = await redis.scan(cursor, {
      match: KEY_PREFIX + "*",
      count: 100,
    });
    cursor = String(next);
    if (!keys?.length) continue;

    scanned += keys.length;
    const records = await redis.mget(...keys);
    keys.forEach((key, i) => {
      const record = records[i];
      if (!record?.guild_id) return;
      const id = key.slice(KEY_PREFIX.length);
      const score = Date.parse(record.updated_at || record.created_at || "") || 0;
      if (!perGuild.has(record.guild_id)) perGuild.set(record.guild_id, []);
      perGuild.get(record.guild_id).push({ score, member: id });
    });
  } while (cursor !== "0");

  const guilds = [];
  for (const [guildId, entries] of perGuild) {
    const index = indexKey(guildId);
    await redis.zadd(index, ...entries);
    await redis.expire(index, MAX_TTL_DAYS * 86400);
    guilds.push({ guild_id: guildId, pages: entries.length });
  }

  return res.status(200).json({ scanned, guilds });
}
