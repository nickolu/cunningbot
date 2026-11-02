import { redis, notFoundHtml } from "./_lib.js";
import { DOWNLOAD_URL_SECONDS, FILE_PREFIX, servesInline } from "./_files_lib.js";
import { contentDisposition, isStorageConfigured, signedDownloadUrl } from "./_storage.js";

/**
 * GET /f/<file id> -- the stable download URL for an uploaded file.
 *
 * The bucket is private, so this looks the file up in the catalog and
 * redirects to a signed URL good for a few minutes. One URL to share, nothing
 * public in the bucket, and a file is gone as soon as its catalog record is
 * (deleted or expired), even before the bucket's lifecycle rule removes the
 * object. Images, video, audio, and PDFs display inline so a page can embed
 * them; everything else is a download.
 */
export default async function handler(req, res) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.setHeader("allow", "GET, HEAD");
    return res.status(405).end();
  }

  res.setHeader("x-content-type-options", "nosniff");
  res.setHeader("referrer-policy", "no-referrer");
  res.setHeader("cache-control", "private, max-age=0, must-revalidate");
  res.setHeader("x-robots-tag", "noindex, nofollow");

  const fileId = String(req.query.id || "").trim().toLowerCase();
  const record = /^[0-9a-f]{24}$/.test(fileId) && isStorageConfigured()
    ? await redis.get(FILE_PREFIX + fileId)
    : null;

  // Same answer whether the file expired or never existed.
  if (!record || typeof record.object !== "string") {
    res.setHeader("content-type", "text/html; charset=utf-8");
    return res.status(404).send(notFoundHtml());
  }

  const kind = servesInline(record.content_type) ? "inline" : "attachment";
  let url;
  try {
    url = await signedDownloadUrl(record.object, {
      disposition: contentDisposition(kind, record.name || "file"),
      expiresMs: DOWNLOAD_URL_SECONDS * 1000,
    });
  } catch (e) {
    console.error("signing download for %s failed: %s", fileId, e?.message || e);
    res.setHeader("content-type", "text/plain; charset=utf-8");
    return res.status(502).send("The file store is not reachable right now.");
  }

  res.setHeader("location", url);
  return res.status(302).end();
}
