import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_MAX_BYTES, DEFAULT_MAX_FILES, DEFAULT_TTL_DAYS, MAX_BYTES_CAP, MAX_FILES_CAP,
  SESSION_OPEN_SECONDS, isAllowedType, matchesQuery, normalizeContentType,
  normalizeSessionRequest, objectPath, sanitizeFilename, servesInline, sessionIsOpen,
} from "../api/_files_lib.js";

const GUILD = "844003671334977607";

test("a minimal session request gets the defaults", () => {
  const now = Date.parse("2026-11-01T12:00:00Z");
  const r = normalizeSessionRequest({ guild_id: GUILD }, now);
  assert.equal(r.ok, true);
  assert.equal(r.session.guild_id, GUILD);
  assert.equal(r.session.kind, "any");
  assert.equal(r.session.max_files, DEFAULT_MAX_FILES);
  assert.equal(r.session.max_bytes, DEFAULT_MAX_BYTES);
  assert.equal(r.session.ttl_days, DEFAULT_TTL_DAYS);
  assert.equal(Date.parse(r.session.expires_at) - now, SESSION_OPEN_SECONDS * 1000);
});

test("limits are clamped, not trusted", () => {
  const r = normalizeSessionRequest({ guild_id: GUILD, max_files: 9999, max_bytes: 1e12 });
  assert.equal(r.session.max_files, MAX_FILES_CAP);
  assert.equal(r.session.max_bytes, MAX_BYTES_CAP);
});

test("a ttl outside the lifecycle tiers is refused", () => {
  const r = normalizeSessionRequest({ guild_id: GUILD, ttl_days: 12 });
  assert.equal(r.ok, false);
  assert.equal(r.error, "unsupported_ttl");
  assert.deepEqual(r.allowed, [1, 7, 30]);
});

test("guild id must be a snowflake", () => {
  assert.equal(normalizeSessionRequest({ guild_id: "not-a-guild" }).ok, false);
  assert.equal(normalizeSessionRequest({}).ok, false);
  assert.equal(normalizeSessionRequest(null).ok, false);
});

test("unknown kinds fall back to any; text fields are bounded", () => {
  const r = normalizeSessionRequest({
    guild_id: GUILD,
    kind: "executables",
    label: "x".repeat(500),
    requester_name: "Sam\u0000\u0007",
  });
  assert.equal(r.session.kind, "any");
  assert.equal(r.session.label.length, 120);
  assert.equal(r.session.requester_name, "Sam");
});

test("sessionIsOpen follows expires_at", () => {
  const now = Date.now();
  assert.equal(sessionIsOpen({ expires_at: new Date(now + 1000).toISOString() }, now), true);
  assert.equal(sessionIsOpen({ expires_at: new Date(now - 1000).toISOString() }, now), false);
  assert.equal(sessionIsOpen({}, now), false);
  assert.equal(sessionIsOpen(null, now), false);
});

test("images kind only signs image types", () => {
  assert.equal(isAllowedType("images", "image/png"), true);
  assert.equal(isAllowedType("images", "image/PNG; charset=binary"), true);
  assert.equal(isAllowedType("images", "application/pdf"), false);
  assert.equal(isAllowedType("images", "image/svg+xml"), false);
});

test("any kind still refuses script-bearing types", () => {
  assert.equal(isAllowedType("any", "application/pdf"), true);
  assert.equal(isAllowedType("any", "application/zip"), true);
  assert.equal(isAllowedType("any", "text/html"), false);
  assert.equal(isAllowedType("any", "image/svg+xml"), false);
  assert.equal(isAllowedType("any", "text/javascript"), false);
  assert.equal(isAllowedType("any", ""), false);
  assert.equal(isAllowedType("any", "garbage"), false);
});

test("normalizeContentType strips parameters and rejects junk", () => {
  assert.equal(normalizeContentType("Image/JPEG; q=1"), "image/jpeg");
  assert.equal(normalizeContentType("not a type"), "");
  assert.equal(normalizeContentType(undefined), "");
});

test("only displayable types are served inline", () => {
  assert.equal(servesInline("image/png"), true);
  assert.equal(servesInline("video/mp4"), true);
  assert.equal(servesInline("application/pdf"), true);
  assert.equal(servesInline("application/zip"), false);
  assert.equal(servesInline("text/html"), false);
  assert.equal(servesInline("image/svg+xml"), false);
});

test("sanitizeFilename keeps the extension and drops path tricks", () => {
  assert.equal(sanitizeFilename("../../etc/passwd"), "passwd");
  assert.equal(sanitizeFilename("C:\\Users\\me\\photo.JPG"), "photo.JPG");
  assert.equal(sanitizeFilename("  my   receipt #3?.pdf "), "my receipt 3.pdf");
  assert.equal(sanitizeFilename(".hidden"), "hidden");
  assert.equal(sanitizeFilename(""), "file");
  assert.equal(sanitizeFilename(null), "file");
  const long = sanitizeFilename("a".repeat(200) + ".tar.gz");
  assert.ok(long.length <= 80);
  assert.ok(long.endsWith(".gz"));
});

test("objectPath puts the file under its lifecycle tier", () => {
  assert.equal(objectPath(7, "abc", "x.png"), "drops/7d/abc/x.png");
  assert.equal(objectPath(1, "abc", "x.png"), "drops/1d/abc/x.png");
  assert.equal(objectPath(30, "abc", "x.png"), "drops/30d/abc/x.png");
  // An unknown tier never lands outside a rule-covered prefix.
  assert.equal(objectPath(99, "abc", "x.png"), "drops/7d/abc/x.png");
});

test("matchesQuery needs every word, anywhere, case-insensitively", () => {
  const record = {
    name: "Receipt-March.pdf",
    note: "dinner at the noodle place",
    label: "trip expenses",
    uploader_name: "Sam",
    content_type: "application/pdf",
  };
  assert.equal(matchesQuery(record, ""), true);
  assert.equal(matchesQuery(record, "receipt"), true);
  assert.equal(matchesQuery(record, "noodle sam"), true);
  assert.equal(matchesQuery(record, "PDF expenses"), true);
  assert.equal(matchesQuery(record, "noodle pizza"), false);
  assert.equal(matchesQuery({}, "anything"), false);
});
