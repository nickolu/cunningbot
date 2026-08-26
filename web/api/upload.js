import { put } from "@vercel/blob";
import { isAuthorized } from "./_lib.js";

// Raw image bytes arrive as the request body; Vercel's JSON parser must not touch them.
export const config = { api: { bodyParser: false } };

// Vercel caps function request bodies at 4.5 MB.
const MAX_BYTES = 4 * 1024 * 1024;

const ALLOWED = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
  "image/gif": "gif",
};

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("allow", "POST");
    return res.status(405).json({ error: "method_not_allowed" });
  }
  if (!isAuthorized(req)) {
    return res.status(401).json({ error: "unauthorized" });
  }

  const contentType = (req.headers["content-type"] || "").split(";")[0].trim();
  const ext = ALLOWED[contentType];
  if (!ext) {
    return res.status(415).json({ error: "unsupported_type", allowed: Object.keys(ALLOWED) });
  }

  const guildId = String(req.headers["x-guild-id"] || "").trim();
  if (!/^[0-9]{1,25}$/.test(guildId)) {
    return res.status(400).json({ error: "invalid_guild_id" });
  }

  let body;
  try {
    body = await readBody(req, MAX_BYTES);
  } catch (e) {
    if (e.code === "TOO_LARGE") {
      return res.status(413).json({ error: "image_too_large", max_bytes: MAX_BYTES });
    }
    return res.status(400).json({ error: "unreadable_body" });
  }
  if (!body.length) return res.status(400).json({ error: "empty_body" });

  // Images from different guilds live under different, unguessable prefixes, so
  // one server cannot enumerate another's uploads by listing a shared folder.
  // The bot derives this prefix from PAGES_ID_SECRET, which deliberately never
  // leaves the bot host -- this service only validates its shape.
  // addRandomSuffix then makes each individual URL unguessable on its own.
  const prefix = String(req.headers["x-guild-prefix"] || "").trim();
  if (!/^[0-9a-f]{16}$/.test(prefix)) {
    return res.status(400).json({ error: "invalid_guild_prefix" });
  }

  const name = sanitizeName(req.headers["x-filename"]) || "image";

  try {
    const blob = await put(`img/${prefix}/${name}.${ext}`, body, {
      access: "public",
      contentType,
      addRandomSuffix: true,
    });
    return res.status(200).json({ url: blob.url, pathname: blob.pathname, bytes: body.length });
  } catch (e) {
    return res.status(502).json({ error: "upload_failed", detail: String(e?.message || e) });
  }
}

function sanitizeName(raw) {
  if (typeof raw !== "string") return null;
  const n = raw.trim().toLowerCase()
    .replace(/\.[a-z0-9]+$/, "")
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return n ? n.slice(0, 60) : null;
}

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on("data", (c) => {
      total += c.length;
      if (total > limit) {
        const err = new Error("too large");
        err.code = "TOO_LARGE";
        req.destroy();
        return reject(err);
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}
