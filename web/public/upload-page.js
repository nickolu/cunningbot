/* The upload page's only script. Loaded from this origin under a CSP that
 * allows nothing else. It never sees a secret: the session id in the URL is
 * the only credential, and the server re-checks it on every call.
 *
 * Per file: ask /api/upload-sign for a signed PUT, PUT the bytes straight to
 * the bucket (XMLHttpRequest, for progress), then tell /api/upload-complete so
 * the file enters the catalog. Each step can fail on its own and the row says
 * which one did.
 */
(function () {
  "use strict";

  var main = document.querySelector("main");
  if (!main) return;

  var cfg = {
    session: main.dataset.session,
    maxFiles: Number(main.dataset.maxFiles) || 1,
    maxBytes: Number(main.dataset.maxBytes) || 0,
    remaining: Number(main.dataset.remaining) || 0,
    kind: main.dataset.kind || "any",
    accept: (main.dataset.accept || "").split(",").filter(Boolean),
  };

  var picker = document.getElementById("picker");
  var drop = document.getElementById("drop");
  var rows = document.getElementById("rows");
  var uploadBtn = document.getElementById("upload");
  var status = document.getElementById("status");
  var remainingEl = document.getElementById("remaining");

  var queue = []; // { file, li, noteInput, state }
  var busy = false;

  function fmtBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
    return (n / 1048576).toFixed(n < 10485760 ? 1 : 0) + " MB";
  }

  function setStatus(text) { status.textContent = text || ""; }

  function pendingCount() {
    return queue.filter(function (q) { return q.state === "ready"; }).length;
  }

  function refresh() {
    remainingEl.textContent = String(cfg.remaining);
    uploadBtn.disabled = busy || pendingCount() === 0;
    picker.disabled = busy || cfg.remaining <= 0;
    var text = drop.querySelector(".drop-text");
    if (cfg.remaining <= 0) text.textContent = "This link has taken all the files it can";
  }

  function rejectReason(file) {
    if (cfg.maxBytes && file.size > cfg.maxBytes) return "Too large (limit " + fmtBytes(cfg.maxBytes) + ")";
    if (cfg.kind === "images" && cfg.accept.indexOf((file.type || "").toLowerCase()) === -1) {
      return "Not an accepted image type";
    }
    return null;
  }

  function addFiles(files) {
    var slots = cfg.remaining - pendingCount();
    Array.prototype.forEach.call(files, function (file) {
      var li = document.createElement("li");
      var name = document.createElement("span");
      name.className = "name";
      name.textContent = file.name;
      var size = document.createElement("span");
      size.className = "size";
      size.textContent = fmtBytes(file.size);
      var note = document.createElement("input");
      note.className = "note";
      note.type = "text";
      note.maxLength = 200;
      note.placeholder = "What is this? (optional, helps find it later)";
      var state = document.createElement("span");
      state.className = "state";
      var bar = document.createElement("div");
      bar.className = "bar";
      bar.appendChild(document.createElement("i"));
      bar.hidden = true;

      li.appendChild(name); li.appendChild(size); li.appendChild(note);
      li.appendChild(bar); li.appendChild(state);
      rows.appendChild(li);

      var entry = { file: file, li: li, noteInput: note, state: "ready", stateEl: state, bar: bar };
      var reason = rejectReason(file);
      if (!reason && slots <= 0) reason = "No slots left on this link";
      if (reason) {
        entry.state = "rejected";
        note.disabled = true;
        state.textContent = reason;
        state.className = "state bad";
      } else {
        slots -= 1;
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "remove";
        remove.textContent = "remove";
        remove.addEventListener("click", function () {
          if (busy || entry.state !== "ready") return;
          entry.state = "removed";
          rows.removeChild(li);
          refresh();
        });
        size.appendChild(document.createTextNode(" "));
        size.appendChild(remove);
      }
      queue.push(entry);
    });
    refresh();
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error(data.error || ("HTTP " + res.status));
          err.code = data.error;
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function putFile(signed, file, onProgress) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("PUT", signed.upload_url, true);
      Object.keys(signed.headers || {}).forEach(function (h) {
        xhr.setRequestHeader(h, signed.headers[h]);
      });
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else reject(new Error("The storage bucket refused the upload (HTTP " + xhr.status + ")"));
      };
      xhr.onerror = function () { reject(new Error("Network error while uploading")); };
      xhr.send(file);
    });
  }

  var MESSAGES = {
    session_closed: "This link has closed. Ask the bot for a new one.",
    session_full: "This link has taken all the files it can.",
    session_not_found: "This link has expired. Ask the bot for a new one.",
    unsupported_type: "That file type is not accepted here.",
    file_too_large: "Too large for this link.",
    not_uploaded: "The upload did not reach the bucket.",
    storage_not_configured: "Uploads are not set up on this server yet.",
  };

  function describe(err) {
    return MESSAGES[err && err.code] || (err && err.message) || "Something went wrong";
  }

  function uploadOne(entry) {
    var state = entry.stateEl;
    var fill = entry.bar.querySelector("i");
    entry.state = "uploading";
    entry.noteInput.disabled = true;
    entry.bar.hidden = false;
    state.className = "state";
    state.textContent = "Preparing…";

    return postJson("/api/upload-sign", {
      session_id: cfg.session,
      name: entry.file.name,
      content_type: entry.file.type || "application/octet-stream",
      bytes: entry.file.size,
      note: entry.noteInput.value,
    }).then(function (signed) {
      cfg.remaining = Math.max(0, cfg.remaining - 1);
      refresh();
      state.textContent = "Uploading…";
      return putFile(signed, entry.file, function (frac) {
        fill.style.width = Math.round(frac * 100) + "%";
      }).then(function () {
        state.textContent = "Saving…";
        return postJson("/api/upload-complete", { session_id: cfg.session, file_id: signed.file_id });
      });
    }).then(function (done) {
      entry.state = "done";
      fill.style.width = "100%";
      state.className = "state ok";
      state.textContent = "";
      state.appendChild(document.createTextNode("Uploaded — "));
      var a = document.createElement("a");
      a.href = done.url;
      a.textContent = done.url;
      a.target = "_blank";
      a.rel = "noopener";
      state.appendChild(a);
    }).catch(function (err) {
      entry.state = "failed";
      entry.bar.hidden = true;
      state.className = "state bad";
      state.textContent = describe(err);
      if (err && (err.code === "session_closed" || err.code === "session_not_found")) {
        cfg.remaining = 0;
      }
    });
  }

  function uploadAll() {
    if (busy) return;
    var ready = queue.filter(function (q) { return q.state === "ready"; });
    if (!ready.length) return;
    busy = true;
    refresh();
    setStatus("Uploading " + ready.length + (ready.length === 1 ? " file…" : " files…"));

    var chain = Promise.resolve();
    ready.forEach(function (entry) {
      chain = chain.then(function () { return uploadOne(entry); });
    });
    chain.then(function () {
      busy = false;
      var failed = ready.filter(function (q) { return q.state === "failed"; }).length;
      var done = ready.length - failed;
      setStatus(done + " uploaded" + (failed ? ", " + failed + " failed" : "") +
        ". The bot can find " + (done === 1 ? "it" : "them") + " by name or note.");
      refresh();
    });
  }

  picker.addEventListener("change", function () {
    if (picker.files && picker.files.length) addFiles(picker.files);
    picker.value = "";
  });

  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) {
      e.preventDefault();
      if (!picker.disabled) drop.classList.add("over");
    });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) {
      e.preventDefault();
      drop.classList.remove("over");
    });
  });
  drop.addEventListener("drop", function (e) {
    if (picker.disabled) return;
    if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });

  uploadBtn.addEventListener("click", uploadAll);
  refresh();
})();
