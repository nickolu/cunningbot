import { redis } from "./_lib.js";
import {
  FILES_INDEX_PREFIX, FILE_PREFIX, PENDING_PREFIX, SESSION_FILES_PREFIX,
  SESSION_KEEP_SECONDS, normalizeContentType, publicFileUrl,
} from "./_files_lib.js";
import { deleteObject, objectMetadata } from "./_storage.js";

// A listing's index must outlive its longest-lived member. Files top out at
// 30 days; a year matches the pages index and costs nothing.
const INDEX_TTL_SECONDS = 365 * 86400;

/**
 * POST /api/upload-complete -- the upload page says a PUT finished.
 *
 * Body: { session_id, file_id }
 * Answer: { file_id, url, name, bytes }
 *
 * The page's word is not taken for it: the object is looked up in the bucket
 * and has to exist, fit the session's size limit, and carry the content type
 * that was signed. Only then does the file enter the catalog -- so the catalog
 * never points at something that is not there, and a client cannot register a
 * file it did not upload.
 */
export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("allow", "POST");
    return res.status(405).json({ error: "method_not_allowed" });
  }
  res.setHeader("cache-control", "no-store");

  const body = typeof req.body === "string" ? safeParse(req.body) : req.body;
  if (!body || typeof body !== "object") return res.status(400).json({ error: "invalid_json" });

  const fileId = String(body.file_id || "").trim().toLowerCase();
  const sessionId = String(body.session_id || "").trim().toLowerCase();
  if (!/^[0-9a-f]{24}$/.test(fileId) || !/^[0-9a-f]{24}$/.test(sessionId)) {
    return res.status(400).json({ error: "invalid_id" });
  }

  const pendingKey = PENDING_PREFIX + fileId;
  const pending = await redis.get(pendingKey);
  if (!pending || pending.session_id !== sessionId) {
    return res.status(404).json({ error: "not_found" });
  }

  let meta;
  try {
    meta = await objectMetadata(pending.object);
  } catch (e) {
    console.error("metadata read for %s failed: %s", fileId, e?.message || e);
    return res.status(502).json({ error: "storage_unavailable" });
  }
  if (!meta) return res.status(409).json({ error: "not_uploaded" });

  // The signature should make these impossible. If they happen anyway the
  // object is not kept.
  const wrongType = normalizeContentType(meta.contentType) !== pending.content_type;
  if (meta.size > pending.max_bytes || wrongType) {
    await deleteObject(pending.object).catch(() => {});
    await redis.del(pendingKey);
    return res.status(400).json({ error: wrongType ? "type_mismatch" : "file_too_large" });
  }

  const now = new Date();
  const ttlSeconds = pending.ttl_days * 86400;
  const record = {
    guild_id: pending.guild_id,
    channel_id: pending.channel_id,
    session_id: sessionId,
    uploader_id: pending.uploader_id,
    uploader_name: pending.uploader_name,
    label: pending.label,
    object: pending.object,
    name: pending.name,
    content_type: pending.content_type,
    bytes: meta.size,
    note: pending.note || "",
    ttl_days: pending.ttl_days,
    created_at: now.toISOString(),
    expires_at: new Date(now.getTime() + ttlSeconds * 1000).toISOString(),
  };

  await redis.set(FILE_PREFIX + fileId, record, { ex: ttlSeconds });
  await redis.del(pendingKey);

  // Discovery. Failing here must not fail the upload: the file is stored and
  // downloadable, and the worst case is it is missing from a listing.
  try {
    const index = FILES_INDEX_PREFIX + pending.guild_id;
    await redis.zadd(index, { score: now.getTime(), member: fileId });
    await redis.expire(index, INDEX_TTL_SECONDS);
    const sessionFiles = SESSION_FILES_PREFIX + sessionId;
    await redis.rpush(sessionFiles, fileId);
    await redis.expire(sessionFiles, SESSION_KEEP_SECONDS);
  } catch (e) {
    console.error("indexing %s failed: %s", fileId, e?.message || e);
  }

  return res.status(200).json({
    file_id: fileId,
    url: publicFileUrl(req, fileId),
    name: record.name,
    bytes: record.bytes,
    expires_at: record.expires_at,
  });
}

function safeParse(s) {
  try { return JSON.parse(s); } catch { return null; }
}
