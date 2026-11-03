import { randomBytes } from "node:crypto";

/**
 * Pure helpers for user uploads: limits, validation, naming, and search. No
 * Redis or Storage imports so the unit tests can load this without
 * credentials.
 *
 * The model, in one paragraph: the bot mints an *upload session* (who asked,
 * where, how much they may upload, for how long) and posts a link to it in
 * Discord. The page behind the link asks this service to sign a direct-to-bucket
 * PUT for each file, and reports back when the PUT has landed. Files are
 * recorded in a per-guild catalog the bot can list and search. Nobody logs in:
 * the link is the capability, and the Discord conversation it was posted in is
 * the audience.
 */

// How long an upload link accepts new files after the bot creates it.
export const SESSION_OPEN_SECONDS = 15 * 60;
// How long the session record itself is kept, so the bot can read back what was
// uploaded after the link has stopped accepting files.
export const SESSION_KEEP_SECONDS = 60 * 60;
// A signed PUT that was never used, or never confirmed, is forgotten after this.
export const PENDING_SECONDS = 60 * 60;
// A download redirect's signed URL is good for this long.
export const DOWNLOAD_URL_SECONDS = 10 * 60;

export const DEFAULT_MAX_FILES = 10;
export const MAX_FILES_CAP = 25;
export const DEFAULT_MAX_BYTES = 500 * 1024 * 1024;
export const MAX_BYTES_CAP = 500 * 1024 * 1024;
export const MAX_NOTE_CHARS = 200;
export const MAX_LABEL_CHARS = 120;

/**
 * File lifetime is enforced by the bucket, not by this service: each tier is
 * an object prefix with a lifecycle rule that deletes objects of that age (see
 * README). The catalog record gets the same TTL so the two expire together.
 * Adding a tier here means adding a lifecycle rule to the bucket.
 */
export const TTL_TIERS = { 1: "1d", 7: "7d", 30: "30d" };
export const DEFAULT_TTL_DAYS = 7;

export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

// Types the bucket would happily store but that this service never signs. The
// bucket is private and every download is forced to `attachment` unless the
// type is one browsers can only *display*, so these are belt and braces rather
// than the whole defence -- but a stored-XSS vector is not worth a feature.
export const BLOCKED_TYPES = new Set([
  "text/html",
  "application/xhtml+xml",
  "image/svg+xml",
  "application/javascript",
  "text/javascript",
  "application/x-javascript",
]);

// Served inline when downloaded; everything else is an attachment.
export const INLINE_TYPE_PREFIXES = ["image/", "video/", "audio/"];
export const INLINE_TYPES = new Set(["application/pdf", "text/plain"]);

export const KINDS = {
  images: "Images only (PNG, JPEG, WebP, GIF)",
  any: "Any file",
};

export const UPLOAD_PATH_PREFIX = "drops/";

export const SESSION_PREFIX = "upload-session:";
export const SESSION_COUNT_PREFIX = "upload-session-count:";
export const SESSION_FILES_PREFIX = "upload-session-files:";
export const PENDING_PREFIX = "upload-pending:";
export const FILE_PREFIX = "file:";
export const FILES_INDEX_PREFIX = "guild-files:";
export const FILES_INDEX_MAX_ENTRIES = 500;

export function newId() {
  return randomBytes(12).toString("hex");
}

export function isSnowflake(raw) {
  return typeof raw === "string" && /^[0-9]{1,25}$/.test(raw.trim());
}

function clampInt(raw, fallback, min, max) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.round(n)));
}

function cleanText(raw, max) {
  if (typeof raw !== "string") return "";
  return raw.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, max);
}

/**
 * Validate the bot's request to open an upload session. Returns
 * `{ ok: true, session }` or `{ ok: false, error }`. Everything the page shows
 * or the catalog records comes from here, so unknown fields are dropped and
 * known ones are bounded.
 */
export function normalizeSessionRequest(body, now = Date.now()) {
  if (!body || typeof body !== "object") return { ok: false, error: "invalid_json" };

  const guildId = typeof body.guild_id === "string" ? body.guild_id.trim() : "";
  if (!isSnowflake(guildId)) return { ok: false, error: "invalid_guild_id" };

  const channelId = typeof body.channel_id === "string" ? body.channel_id.trim() : "";
  if (channelId && !isSnowflake(channelId)) return { ok: false, error: "invalid_channel_id" };

  const requesterId = typeof body.requester_id === "string" ? body.requester_id.trim() : "";
  if (requesterId && !isSnowflake(requesterId)) return { ok: false, error: "invalid_requester_id" };

  const kind = body.kind in KINDS ? body.kind : "any";

  const ttlDays = clampInt(body.ttl_days, DEFAULT_TTL_DAYS, 1, 365);
  if (!(ttlDays in TTL_TIERS)) {
    return { ok: false, error: "unsupported_ttl", allowed: Object.keys(TTL_TIERS).map(Number) };
  }

  const createdAt = new Date(now).toISOString();
  const session = {
    guild_id: guildId,
    channel_id: channelId || null,
    channel_name: cleanText(body.channel_name, 100) || null,
    requester_id: requesterId || null,
    requester_name: cleanText(body.requester_name, 100) || null,
    label: cleanText(body.label, MAX_LABEL_CHARS) || null,
    kind,
    max_files: clampInt(body.max_files, DEFAULT_MAX_FILES, 1, MAX_FILES_CAP),
    max_bytes: clampInt(body.max_bytes, DEFAULT_MAX_BYTES, 1, MAX_BYTES_CAP),
    ttl_days: ttlDays,
    created_at: createdAt,
    expires_at: new Date(now + SESSION_OPEN_SECONDS * 1000).toISOString(),
  };
  return { ok: true, session };
}

export function sessionIsOpen(session, now = Date.now()) {
  if (!session || typeof session.expires_at !== "string") return false;
  const exp = Date.parse(session.expires_at);
  return Number.isFinite(exp) && exp > now;
}

/** Lower-cased media type with parameters stripped, or "" if unusable. */
export function normalizeContentType(raw) {
  if (typeof raw !== "string") return "";
  const type = raw.split(";")[0].trim().toLowerCase();
  return /^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(type) ? type : "";
}

export function isAllowedType(kind, contentType) {
  const type = normalizeContentType(contentType);
  if (!type || BLOCKED_TYPES.has(type)) return false;
  if (kind === "images") return IMAGE_TYPES.includes(type);
  return true;
}

export function servesInline(contentType) {
  const type = normalizeContentType(contentType);
  if (!type || BLOCKED_TYPES.has(type)) return false;
  if (INLINE_TYPES.has(type)) return true;
  return INLINE_TYPE_PREFIXES.some((p) => type.startsWith(p));
}

/**
 * A filename safe to use as an object name component and a download name.
 * Keeps the extension, drops path separators and control characters, and
 * never returns an empty string.
 */
export function sanitizeFilename(raw, fallback = "file") {
  if (typeof raw !== "string") return fallback;
  let name = raw.replace(/\\/g, "/").split("/").pop() || "";
  name = name.replace(/[\u0000-\u001f\u007f"<>:*?|%#&;]/g, "").trim();
  name = name.replace(/\s+/g, " ");
  name = name.replace(/^\.+/, "");
  if (!name) return fallback;
  if (name.length > 80) {
    const dot = name.lastIndexOf(".");
    const ext = dot > 0 && name.length - dot <= 12 ? name.slice(dot) : "";
    name = name.slice(0, 80 - ext.length) + ext;
  }
  return name;
}

export function objectPath(ttlDays, fileId, safeName) {
  const tier = TTL_TIERS[ttlDays] || TTL_TIERS[DEFAULT_TTL_DAYS];
  return `${UPLOAD_PATH_PREFIX}${tier}/${fileId}/${safeName}`;
}

export function cleanNote(raw) {
  return cleanText(raw, MAX_NOTE_CHARS);
}

/**
 * Case-insensitive search over the words of `query`: every word must appear
 * somewhere in the file's name, note, session label, uploader name, or type.
 * Good enough for "the receipt from Sam" over a few hundred files; a store
 * with real full-text search is a later decision (see backlog).
 */
export function matchesQuery(record, query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const haystack = [
    record.name,
    record.note,
    record.label,
    record.uploader_name,
    record.content_type,
  ]
    .filter((v) => typeof v === "string")
    .join("\n")
    .toLowerCase();
  return words.every((w) => haystack.includes(w));
}

export function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function formatBytes(n) {
  if (!Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function publicFileUrl(req, fileId) {
  const host = req.headers["x-forwarded-host"] || req.headers.host;
  const proto = req.headers["x-forwarded-proto"] || "https";
  return `${proto}://${host}/f/${fileId}`;
}

export function publicUploadUrl(req, sessionId) {
  const host = req.headers["x-forwarded-host"] || req.headers.host;
  const proto = req.headers["x-forwarded-proto"] || "https";
  return `${proto}://${host}/u/${sessionId}`;
}

/** The shape the bot sees for one catalog entry. */
export function presentFile(req, id, record) {
  return {
    id,
    name: record.name || "",
    content_type: record.content_type || "",
    bytes: Number(record.bytes) || 0,
    note: record.note || "",
    label: record.label || "",
    uploader_id: record.uploader_id || null,
    uploader_name: record.uploader_name || null,
    channel_id: record.channel_id || null,
    session_id: record.session_id || null,
    created_at: record.created_at || null,
    expires_at: record.expires_at || null,
    url: publicFileUrl(req, id),
  };
}
