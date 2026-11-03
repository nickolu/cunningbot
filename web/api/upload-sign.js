import { redis } from "./_lib.js";
import {
  PENDING_PREFIX, PENDING_SECONDS, SESSION_COUNT_PREFIX, SESSION_KEEP_SECONDS,
  SESSION_PREFIX, cleanNote, isAllowedType, newId, normalizeContentType,
  objectPath, sanitizeFilename, sessionIsOpen,
} from "./_files_lib.js";
import { isStorageConfigured, signedUploadUrl } from "./_storage.js";

/**
 * POST /api/upload-sign -- called by the upload page for each file.
 *
 * Body: { session_id, name, content_type, bytes, note? }
 * Answer: { file_id, upload_url, headers }
 *
 * No bearer token: the session id is the credential, and it only ever came
 * from a link the bot posted. The server decides everything that matters --
 * type, size, remaining count -- and the limits are baked into the signed URL,
 * so a tampered request can at most be refused here or by the bucket.
 */
export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("allow", "POST");
    return res.status(405).json({ error: "method_not_allowed" });
  }
  res.setHeader("cache-control", "no-store");

  if (!isStorageConfigured()) {
    return res.status(503).json({ error: "storage_not_configured" });
  }

  const body = typeof req.body === "string" ? safeParse(req.body) : req.body;
  if (!body || typeof body !== "object") return res.status(400).json({ error: "invalid_json" });

  const sessionId = String(body.session_id || "").trim().toLowerCase();
  if (!/^[0-9a-f]{24}$/.test(sessionId)) return res.status(400).json({ error: "invalid_session" });

  const session = await redis.get(SESSION_PREFIX + sessionId);
  if (!session) return res.status(404).json({ error: "session_not_found" });
  if (!sessionIsOpen(session)) return res.status(410).json({ error: "session_closed" });

  const contentType = normalizeContentType(body.content_type) || "application/octet-stream";
  if (!isAllowedType(session.kind, contentType)) {
    return res.status(415).json({ error: "unsupported_type", kind: session.kind });
  }

  const bytes = Number(body.bytes);
  if (!Number.isFinite(bytes) || bytes < 0) return res.status(400).json({ error: "invalid_size" });
  if (bytes > session.max_bytes) {
    return res.status(413).json({ error: "file_too_large", max_bytes: session.max_bytes });
  }

  // Count the slot before signing. A signed URL that is never used still
  // spends a slot -- the cap is on what a link can be made to accept, not on
  // what arrives, and this keeps the check a single atomic step.
  const countKey = SESSION_COUNT_PREFIX + sessionId;
  const used = await redis.incr(countKey);
  if (used === 1) await redis.expire(countKey, SESSION_KEEP_SECONDS);
  if (used > session.max_files) {
    return res.status(409).json({ error: "session_full", max_files: session.max_files });
  }

  const fileId = newId();
  const name = sanitizeFilename(body.name);
  const object = objectPath(session.ttl_days, fileId, name);

  const pending = {
    session_id: sessionId,
    guild_id: session.guild_id,
    channel_id: session.channel_id,
    uploader_id: session.requester_id,
    uploader_name: session.requester_name,
    label: session.label,
    object,
    name,
    content_type: contentType,
    max_bytes: session.max_bytes,
    ttl_days: session.ttl_days,
    note: cleanNote(body.note),
    created_at: new Date().toISOString(),
  };
  await redis.set(PENDING_PREFIX + fileId, pending, { ex: PENDING_SECONDS });

  let uploadUrl;
  try {
    uploadUrl = await signedUploadUrl(object, contentType, session.max_bytes, PENDING_SECONDS * 1000);
  } catch (e) {
    console.error("signing upload for %s failed: %s", fileId, e?.message || e);
    return res.status(502).json({ error: "signing_failed" });
  }

  return res.status(200).json({
    file_id: fileId,
    upload_url: uploadUrl,
    // The browser must send exactly these; both are part of the signature.
    headers: {
      "Content-Type": contentType,
      "x-goog-content-length-range": `0,${session.max_bytes}`,
    },
  });
}

function safeParse(s) {
  try { return JSON.parse(s); } catch { return null; }
}
