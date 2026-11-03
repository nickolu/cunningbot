import { Storage } from "@google-cloud/storage";

/**
 * The Firebase Storage bucket user uploads live in. Firebase Storage is a
 * Google Cloud Storage bucket, and this service talks to it as GCS with a
 * service account: signed URLs for the browser's PUT and for downloads,
 * metadata reads to confirm an upload landed, and lifecycle rules on the
 * bucket (set up once, see README) to delete files when their tier expires.
 *
 * The bucket is private. Nothing in it has a public URL; every download goes
 * through `/f/<id>`, which checks the catalog and redirects to a short-lived
 * signed URL. That keeps the download URL stable, lets a record be revoked by
 * deleting it, and means expiry is whichever of the catalog TTL and the
 * lifecycle rule fires first.
 *
 * Env: GCS_BUCKET (bucket name, no gs://) and GCS_SERVICE_ACCOUNT (the service
 * account key JSON, or that JSON base64-encoded). Both only on Vercel.
 */

let bucket = null;

export function isStorageConfigured() {
  return Boolean(process.env.GCS_BUCKET && process.env.GCS_SERVICE_ACCOUNT);
}

function loadCredentials() {
  let raw = process.env.GCS_SERVICE_ACCOUNT || "";
  raw = raw.trim();
  if (!raw.startsWith("{")) {
    raw = Buffer.from(raw, "base64").toString("utf8");
  }
  const creds = JSON.parse(raw);
  if (!creds.client_email || !creds.private_key) {
    throw new Error("GCS_SERVICE_ACCOUNT is not a service account key");
  }
  return creds;
}

export function getBucket() {
  if (bucket) return bucket;
  if (!isStorageConfigured()) {
    throw new Error("GCS_BUCKET / GCS_SERVICE_ACCOUNT are not configured");
  }
  const credentials = loadCredentials();
  const storage = new Storage({ credentials, projectId: credentials.project_id });
  bucket = storage.bucket(process.env.GCS_BUCKET);
  return bucket;
}

/**
 * A V4 signed URL the browser PUTs the file to. The signature binds the
 * content type and a content-length range, so the bucket itself rejects a
 * different type or a larger body -- the page cannot talk its way past the
 * session's limits by editing a request.
 */
export async function signedUploadUrl(object, contentType, maxBytes, expiresMs) {
  const [url] = await getBucket().file(object).getSignedUrl({
    version: "v4",
    action: "write",
    expires: Date.now() + expiresMs,
    contentType,
    extensionHeaders: { "x-goog-content-length-range": `0,${maxBytes}` },
  });
  return url;
}

export async function signedDownloadUrl(object, { disposition, expiresMs }) {
  const [url] = await getBucket().file(object).getSignedUrl({
    version: "v4",
    action: "read",
    expires: Date.now() + expiresMs,
    responseDisposition: disposition,
  });
  return url;
}

/** `{ size, contentType }` for an object, or null if it is not there. */
export async function objectMetadata(object) {
  try {
    const [meta] = await getBucket().file(object).getMetadata();
    return { size: Number(meta.size) || 0, contentType: String(meta.contentType || "") };
  } catch (e) {
    if (e?.code === 404) return null;
    throw e;
  }
}

export async function deleteObject(object) {
  try {
    await getBucket().file(object).delete();
  } catch (e) {
    if (e?.code !== 404) throw e;
  }
}

/**
 * RFC 6266 disposition with a plain ASCII fallback and a UTF-8 `filename*`,
 * so a name with spaces or non-ASCII survives the redirect.
 */
export function contentDisposition(kind, filename) {
  const ascii = String(filename).replace(/[^\x20-\x7e]/g, "_").replace(/["\\]/g, "_");
  const utf8 = encodeURIComponent(String(filename));
  return `${kind}; filename="${ascii}"; filename*=UTF-8''${utf8}`;
}
