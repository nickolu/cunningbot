import { redis, isAuthorized, normalizeGuildId } from "./_lib.js";
import {
  FILE_PREFIX, SESSION_FILES_PREFIX, SESSION_KEEP_SECONDS, SESSION_PREFIX,
  newId, normalizeSessionRequest, presentFile, publicUploadUrl, sessionIsOpen,
} from "./_files_lib.js";
import { isStorageConfigured } from "./_storage.js";

/**
 * Upload sessions, for the bot (bearer token on both):
 *
 *   POST /api/upload-sessions                     -> open a session, get its link
 *   GET  /api/upload-sessions?id=<sid>&guild_id=  -> the session and what it has received
 *
 * A session is the capability behind an upload link: it says which guild and
 * channel asked, who asked, how many files of what size and type the link
 * accepts, and for how long. The link stops taking files after
 * SESSION_OPEN_SECONDS but the record stays SESSION_KEEP_SECONDS so the bot
 * can read back what arrived after the link closed.
 */
export default async function handler(req, res) {
  if (req.method !== "POST" && req.method !== "GET") {
    res.setHeader("allow", "GET, POST");
    return res.status(405).json({ error: "method_not_allowed" });
  }
  if (!isAuthorized(req)) {
    return res.status(401).json({ error: "unauthorized" });
  }
  res.setHeader("cache-control", "no-store");

  if (req.method === "POST") return create(req, res);
  return read(req, res);
}

async function create(req, res) {
  // Refuse to hand out a link that can never work. The bot reports this as
  // "uploads aren't configured" instead of giving someone a dead page.
  if (!isStorageConfigured()) {
    return res.status(503).json({ error: "storage_not_configured" });
  }

  const body = typeof req.body === "string" ? safeParse(req.body) : req.body;
  const result = normalizeSessionRequest(body);
  if (!result.ok) {
    return res.status(400).json({ error: result.error, allowed: result.allowed });
  }

  const sessionId = newId();
  const session = { ...result.session, id: sessionId, created_by: "bot" };
  await redis.set(SESSION_PREFIX + sessionId, session, { ex: SESSION_KEEP_SECONDS });

  return res.status(200).json({
    session_id: sessionId,
    url: publicUploadUrl(req, sessionId),
    expires_at: session.expires_at,
    max_files: session.max_files,
    max_bytes: session.max_bytes,
    ttl_days: session.ttl_days,
    kind: session.kind,
  });
}

async function read(req, res) {
  const guildId = normalizeGuildId(req.query.guild_id);
  if (!guildId) return res.status(400).json({ error: "invalid_guild_id" });

  const sessionId = String(req.query.id || "").trim().toLowerCase();
  if (!/^[0-9a-f]{24}$/.test(sessionId)) {
    return res.status(400).json({ error: "invalid_id" });
  }

  const session = await redis.get(SESSION_PREFIX + sessionId);
  // Expired, never existed, or another guild's: one answer, no probing.
  if (!session || session.guild_id !== guildId) {
    return res.status(404).json({ error: "not_found" });
  }

  const fileIds = (await redis.lrange(SESSION_FILES_PREFIX + sessionId, 0, -1)) || [];
  let files = [];
  if (fileIds.length) {
    const records = await redis.mget(...fileIds.map((id) => FILE_PREFIX + id));
    files = fileIds
      .map((id, i) => (records[i] ? presentFile(req, id, records[i]) : null))
      .filter(Boolean);
  }

  return res.status(200).json({
    session: {
      id: sessionId,
      guild_id: session.guild_id,
      channel_id: session.channel_id,
      requester_id: session.requester_id,
      requester_name: session.requester_name,
      label: session.label,
      kind: session.kind,
      max_files: session.max_files,
      max_bytes: session.max_bytes,
      ttl_days: session.ttl_days,
      created_at: session.created_at,
      expires_at: session.expires_at,
      open: sessionIsOpen(session),
      url: publicUploadUrl(req, sessionId),
    },
    files,
  });
}

function safeParse(s) {
  try { return JSON.parse(s); } catch { return null; }
}
