import { redis, isAuthorized, normalizeGuildId } from "./_lib.js";
import {
  FILES_INDEX_MAX_ENTRIES, FILES_INDEX_PREFIX, FILE_PREFIX, matchesQuery, presentFile,
} from "./_files_lib.js";
import { deleteObject } from "./_storage.js";

/**
 * The file catalog, for the bot (bearer token):
 *
 *   GET    /api/files?guild_id=<id>&q=<words>&limit=<n>  -> newest first, filtered
 *   DELETE /api/files?guild_id=<id>&id=<file>             -> remove one file
 *
 * `guild_id` is mandatory on both: the bot holds one token for every server it
 * serves, so without it a bug in one guild's code path could read or delete
 * another's files. A file belonging to a different guild answers exactly like
 * a missing one.
 */
export default async function handler(req, res) {
  if (req.method !== "GET" && req.method !== "DELETE") {
    res.setHeader("allow", "GET, DELETE");
    return res.status(405).json({ error: "method_not_allowed" });
  }
  if (!isAuthorized(req)) {
    return res.status(401).json({ error: "unauthorized" });
  }
  res.setHeader("cache-control", "no-store");

  const guildId = normalizeGuildId(req.query.guild_id);
  if (!guildId) return res.status(400).json({ error: "invalid_guild_id" });

  if (req.method === "DELETE") return remove(req, res, guildId);
  return list(req, res, guildId);
}

async function list(req, res, guildId) {
  const index = FILES_INDEX_PREFIX + guildId;
  const query = typeof req.query.q === "string" ? req.query.q.slice(0, 200) : "";
  let limit = Number(req.query.limit);
  if (!Number.isFinite(limit) || limit <= 0) limit = 25;
  limit = Math.min(limit, FILES_INDEX_MAX_ENTRIES);

  let ids = [];
  try {
    ids = (await redis.zrange(index, 0, FILES_INDEX_MAX_ENTRIES - 1, { rev: true })) || [];
  } catch (e) {
    console.error("files index read failed for guild %s: %s", guildId, e?.message || e);
    return res.status(200).json({ files: [], total: 0 });
  }
  if (!ids.length) return res.status(200).json({ files: [], total: 0 });

  const records = await redis.mget(...ids.map((id) => FILE_PREFIX + id));

  const live = [];
  const expired = [];
  ids.forEach((id, i) => {
    const record = records[i];
    // An index member outlives its record -- it has no TTL of its own -- so
    // reads prune what has gone instead of a scheduled job doing it.
    if (!record || record.guild_id !== guildId) {
      expired.push(id);
      return;
    }
    live.push({ id, record });
  });

  if (expired.length) {
    try {
      await redis.zrem(index, ...expired);
    } catch (e) {
      console.error("files index prune failed for guild %s: %s", guildId, e?.message || e);
    }
  }

  const matched = live.filter(({ record }) => matchesQuery(record, query));
  const files = matched.slice(0, limit).map(({ id, record }) => presentFile(req, id, record));
  return res.status(200).json({ files, total: matched.length });
}

async function remove(req, res, guildId) {
  const fileId = String(req.query.id || "").trim().toLowerCase();
  if (!/^[0-9a-f]{24}$/.test(fileId)) return res.status(400).json({ error: "invalid_id" });

  const key = FILE_PREFIX + fileId;
  const record = await redis.get(key);
  if (!record || record.guild_id !== guildId) {
    return res.status(404).json({ error: "not_found" });
  }

  // Catalog first: once the record is gone /f/<id> stops serving the file even
  // if the object delete fails and has to wait for the lifecycle rule.
  await redis.del(key);
  try {
    await redis.zrem(FILES_INDEX_PREFIX + guildId, fileId);
  } catch (e) {
    console.error("files index zrem failed for %s: %s", fileId, e?.message || e);
  }
  try {
    await deleteObject(record.object);
  } catch (e) {
    console.error("object delete for %s failed: %s", fileId, e?.message || e);
    return res.status(200).json({ id: fileId, deleted: true, object_deleted: false });
  }

  return res.status(200).json({ id: fileId, deleted: true, object_deleted: true });
}
