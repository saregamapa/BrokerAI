/**
 * BrokerAI — API helpers and UI utilities (no client-side auth).
 */
(function () {
  "use strict";

  var TOKEN_KEY = "brokerai_token";

  var API_BASE = (function () {
    if (typeof window === "undefined") return "";
    var o = window.location.origin;
    if (!o || o === "null" || !/^https?:/i.test(o)) {
      return "http://127.0.0.1:8000";
    }
    return "";
  })();

  function apiUrl(path) {
    return API_BASE + path;
  }

  function getToken() {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch (e) {
      return null;
    }
  }

  function setToken(token) {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, String(token));
      else localStorage.removeItem(TOKEN_KEY);
    } catch (e) {}
  }

  function clearToken() {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch (e) {}
  }

  function jsonHeaders() {
    var h = {
      "Content-Type": "application/json",
      Accept: "application/json",
    };
    var t = getToken();
    if (t) h.Authorization = "Bearer " + t;
    return h;
  }

  /**
   * Validate stored JWT with GET /api/auth/me. Redirects to login on failure.
   * @returns {Promise<object|null>}
   */
  async function bootAuth() {
    var t = getToken();
    if (!t) {
      window.location.href = "/login.html";
      return null;
    }
    try {
      return await apiJson("/api/auth/me", { method: "GET" });
    } catch (e) {
      clearToken();
      window.location.href = "/login.html";
      return null;
    }
  }

  async function apiJson(path, options) {
    var opts = options || {};
    var headers = Object.assign({}, jsonHeaders(), opts.headers || {});
    var res = await fetch(apiUrl(path), Object.assign({}, opts, { headers: headers }));
    var text = await res.text();
    var data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (e) {
      data = { detail: text || "Invalid JSON" };
    }
    if (!res.ok) {
      var msg =
        (data && (data.detail || data.message)) ||
        res.statusText ||
        "Request failed";
      var err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
      err.status = res.status;
      throw err;
    }
    return data;
  }

  /**
   * fetch() for non-JSON responses (e.g. CSV download).
   */
  async function fetchWithAuth(path, init) {
    init = init || {};
    var headers = Object.assign({}, jsonHeaders(), init.headers || {});
    return fetch(apiUrl(path), Object.assign({}, init, { headers: headers }));
  }

  /**
   * POST multipart/form-data (e.g. file upload). Do not set Content-Type — browser sets boundary.
   * @param {string} path
   * @param {FormData} formData
   */
  async function apiForm(path, formData) {
    var headers = Object.assign({ Accept: "application/json" }, jsonHeaders());
    delete headers["Content-Type"];
    var res = await fetch(apiUrl(path), { method: "POST", body: formData, headers: headers });
    var text = await res.text();
    var data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (e) {
      data = { detail: text || "Invalid JSON" };
    }
    if (!res.ok) {
      var msg2 =
        (data && (data.detail || data.message)) ||
        res.statusText ||
        "Request failed";
      var err2 = new Error(typeof msg2 === "string" ? msg2 : JSON.stringify(msg2));
      err2.status = res.status;
      throw err2;
    }
    return data;
  }

  /**
   * @param {string} message
   * @param {"success"|"error"|"info"|"ok"} type — "ok" maps to success
   */
  function showToast(message, type) {
    var el = document.getElementById("brokerai-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "brokerai-toast";
      el.className = "brokerai-toast hidden";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    var t = type === "error" ? "error" : type === "info" ? "info" : "success";
    if (type === "ok") t = "success";
    el.textContent = message;
    el.classList.remove("hidden", "brokerai-toast--success", "brokerai-toast--error", "brokerai-toast--info");
    el.classList.add("brokerai-toast", "brokerai-toast--" + t);
    clearTimeout(el._brokeraiT);
    el._brokeraiT = setTimeout(function () {
      el.classList.add("hidden");
    }, 4200);
  }

  function setLoading(visible, textOrElId) {
    // Back-compat: 2nd arg can be either a loading element id OR a status text.
    // If it looks like a DOM id that exists, treat as elId; otherwise treat as text.
    var elId = "brokerai-loading";
    var text = null;
    if (typeof textOrElId === "string") {
      if (textOrElId && document.getElementById(textOrElId)) {
        elId = textOrElId;
      } else if (textOrElId) {
        text = textOrElId;
      }
    }
    var wrap = document.getElementById(elId);
    if (!wrap) return;
    wrap.classList.toggle("hidden", !visible);
    if (visible && text) setLoadingText(text, elId);
  }

  /**
   * Update the text inside a loading overlay (e.g. "Writing captions…" → "Generating images…").
   */
  function setLoadingText(text, elId) {
    var wrap = document.getElementById(elId || "brokerai-loading");
    if (!wrap) return;
    var p = wrap.querySelector("[data-brokerai-loading-text]") || wrap.querySelector("p");
    if (p) p.textContent = text;
  }

  /**
   * Cycle through progress messages while a long-running task runs.
   * Returns an object with `.stop()`.
   */
  function progressCycle(steps, intervalMs, elId) {
    if (!Array.isArray(steps) || !steps.length) return { stop: function () {} };
    var i = 0;
    setLoadingText(steps[0], elId);
    var t = setInterval(function () {
      i = Math.min(i + 1, steps.length - 1);
      setLoadingText(steps[i], elId);
    }, intervalMs || 3500);
    return {
      stop: function () { try { clearInterval(t); } catch (e) {} },
      advance: function (idx) { if (steps[idx]) setLoadingText(steps[idx], elId); },
    };
  }

  /**
   * Promise-based confirm dialog. Returns true if the user confirms, false otherwise.
   * Options: {title, message, confirmLabel, cancelLabel, danger}
   */
  function confirmDialog(opts) {
    opts = opts || {};
    var title = opts.title || "Are you sure?";
    var message = opts.message || "This action cannot be undone.";
    var confirmLabel = opts.confirmLabel || "Confirm";
    var cancelLabel = opts.cancelLabel || "Cancel";
    var danger = !!opts.danger;

    return new Promise(function (resolve) {
      var host = document.createElement("div");
      host.className =
        "fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/60 backdrop-blur-sm px-4";
      host.setAttribute("role", "dialog");
      host.setAttribute("aria-modal", "true");
      var btnClass = danger
        ? "bg-red-600 hover:bg-red-700 focus:ring-red-500"
        : "bg-amber-500 hover:bg-amber-600 focus:ring-amber-400";
      host.innerHTML =
        '<div class="brokerai-modal-card w-full max-w-sm rounded-2xl bg-white shadow-2xl border border-slate-200 p-6">' +
        '<h3 class="text-lg font-semibold text-slate-900"></h3>' +
        '<p class="mt-2 text-sm text-slate-600"></p>' +
        '<div class="mt-6 flex justify-end gap-2">' +
        '<button type="button" data-brokerai-cancel class="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-300"></button>' +
        '<button type="button" data-brokerai-confirm class="rounded-lg px-4 py-2 text-sm font-semibold text-white shadow focus:outline-none focus:ring-2 ' + btnClass + '"></button>' +
        "</div></div>";
      host.querySelector("h3").textContent = title;
      host.querySelector("p").textContent = message;
      var cancelBtn = host.querySelector("[data-brokerai-cancel]");
      var okBtn = host.querySelector("[data-brokerai-confirm]");
      cancelBtn.textContent = cancelLabel;
      okBtn.textContent = confirmLabel;

      function cleanup(val) {
        document.removeEventListener("keydown", onKey);
        if (host.parentNode) host.parentNode.removeChild(host);
        resolve(val);
      }
      function onKey(e) {
        if (e.key === "Escape") cleanup(false);
        if (e.key === "Enter") cleanup(true);
      }
      cancelBtn.addEventListener("click", function () { cleanup(false); });
      okBtn.addEventListener("click", function () { cleanup(true); });
      host.addEventListener("click", function (e) { if (e.target === host) cleanup(false); });
      document.addEventListener("keydown", onKey);
      document.body.appendChild(host);
      setTimeout(function () { try { okBtn.focus(); } catch (e) {} }, 10);
    });
  }

  // ---- Form validation helpers -----------------------------------------
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  function validateEmail(v) {
    if (!v || typeof v !== "string") return "Email is required.";
    if (v.length > 254) return "Email is too long.";
    if (!EMAIL_RE.test(v.trim())) return "Please enter a valid email.";
    return null;
  }

  function validatePassword(v) {
    if (!v || typeof v !== "string") return "Password is required.";
    if (v.length < 8) return "Password must be at least 8 characters.";
    if (v.length > 200) return "Password is too long.";
    return null;
  }

  function validateRequired(v, label) {
    if (v == null || String(v).trim() === "")
      return (label || "This field") + " is required.";
    return null;
  }

  /**
   * Set an inline error message under an input.
   * Expects a sibling element with [data-error-for="<inputId>"] or appends one.
   */
  function setFieldError(inputId, message) {
    var input = document.getElementById(inputId);
    if (!input) return;
    input.classList.toggle("brokerai-input-error", !!message);
    if (message) {
      input.setAttribute("aria-invalid", "true");
    } else {
      input.removeAttribute("aria-invalid");
    }
    var slot = document.querySelector('[data-error-for="' + inputId + '"]');
    if (!slot) {
      slot = document.createElement("p");
      slot.setAttribute("data-error-for", inputId);
      slot.className = "mt-1 text-xs font-medium text-red-600";
      if (input.parentNode) input.parentNode.appendChild(slot);
    }
    slot.textContent = message || "";
    slot.classList.toggle("hidden", !message);
  }

  function clearFieldErrors(inputIds) {
    (inputIds || []).forEach(function (id) { setFieldError(id, null); });
  }

  function requireAuth() {
    if (!getToken()) {
      if (typeof window !== "undefined") window.location.href = "/login.html";
      return false;
    }
    return true;
  }

  function logout() {
    try {
      var t = getToken();
      if (t) {
        fetch(apiUrl("/api/auth/logout"), {
          method: "POST",
          headers: jsonHeaders(),
        }).catch(function () {});
      }
    } catch (e) {}
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login.html";
  }

  /**
   * Parse API datetime as UTC: naive ISO strings from the backend are treated as Zulu.
   * @param {string|null|undefined} iso
   * @returns {Date|null}
   */
  function parseUtcIso(iso) {
    if (iso == null || iso === "") return null;
    var s = String(iso).trim();
    if (
      /^\d{4}-\d{2}-\d{2}T[\d:.]+/.test(s) &&
      !/[zZ]$/.test(s) &&
      !/[+-]\d{2}:?\d{2}$/.test(s)
    ) {
      s += "Z";
    }
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  /**
   * Format a UTC instant in the user's IANA timezone (or browser default if omitted).
   * @param {string|null|undefined} iso
   * @param {string|null|undefined} timeZone IANA e.g. America/New_York
   */
  function formatScheduleInUserTz(iso, timeZone) {
    var d = parseUtcIso(iso);
    if (!d) return "";
    var tz = timeZone && String(timeZone).trim() ? String(timeZone).trim() : undefined;
    var dopts = { weekday: "short", month: "short", day: "numeric", timeZone: tz };
    var topts = { hour: "numeric", minute: "2-digit", timeZone: tz };
    return (
      d.toLocaleDateString(undefined, dopts) + " · " + d.toLocaleTimeString(undefined, topts)
    );
  }

  /**
   * Calendar key YYYY-MM-DD for an instant in the given IANA zone.
   * @param {string|null|undefined} iso
   * @param {string|null|undefined} timeZone
   * @returns {string|null}
   */
  function dateKeyFromUtcInTz(iso, timeZone) {
    var d = parseUtcIso(iso);
    if (!d) return null;
    var tz = timeZone && String(timeZone).trim() ? String(timeZone).trim() : undefined;
    try {
      var fmt = new Intl.DateTimeFormat("en-CA", {
        timeZone: tz,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      });
      var parts = fmt.formatToParts(d);
      var y = "";
      var m = "";
      var day = "";
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        if (p.type === "year") y = p.value;
        if (p.type === "month") m = p.value;
        if (p.type === "day") day = p.value;
      }
      if (!y || !m || !day) return null;
      return y + "-" + m + "-" + day;
    } catch (e) {
      return null;
    }
  }

  /**
   * Inject the canonical site footer (legal links + copyright).
   * Workspace pages use <div id="app-layout" class="flex min-h-screen flex-col md:pl-64">…</div>
   * so the footer sits in the main column and aligns with the sidebar layout.
   * Opt out with <body data-skip-footer>.
   */
  function injectFooter() {
    try {
      if (document.getElementById("ba-footer")) return;
      if (document.body && document.body.hasAttribute("data-skip-footer")) return;

      var layout = document.getElementById("app-layout");
      var year = new Date().getFullYear();
      var topSpacing = layout ? "mt-auto" : "mt-12";

      var page = (document.body && document.body.getAttribute("data-page")) || "";
      var workspaceNoBrand =
        page === "dashboard" ||
        page === "wizard" ||
        page === "review" ||
        page === "analytics";
      var brandBlock = workspaceNoBrand
        ? ""
        : '<a href="/" class="font-bold text-white">Broker<span class="text-amber-400">AI</span></a>';
      var innerRowClass = workspaceNoBrand
        ? "mx-auto max-w-6xl px-6 flex w-full flex-col items-center gap-4 text-center sm:flex-row sm:items-center sm:justify-between"
        : "mx-auto max-w-6xl px-6 flex flex-col items-center gap-4 text-center sm:flex-row sm:justify-between sm:text-left";

      var html =
        '<footer id="ba-footer" class="' +
        topSpacing +
        ' shrink-0 border-t border-slate-800/80 bg-[#0f172a] py-10 text-sm text-slate-400" role="contentinfo">' +
          '<div class="' +
        innerRowClass +
        '">' +
            brandBlock +
            '<nav class="flex flex-wrap justify-center gap-x-5 gap-y-2 ' +
        (workspaceNoBrand ? "sm:justify-start" : "sm:justify-end") +
        '" aria-label="Footer">' +
              '<a href="/privacy.html" class="hover:text-white transition">Privacy Policy</a>' +
              '<a href="/terms.html" class="hover:text-white transition">Terms &amp; Conditions</a>' +
              '<a href="/cookies.html" class="hover:text-white transition">Cookies Policy</a>' +
              '<a href="/contact.html" class="hover:text-white transition">Contact Us</a>' +
            '</nav>' +
            '<p class="text-slate-500 sm:shrink-0">© ' + year + ' BrokerAI</p>' +
          '</div>' +
        '</footer>';

      var holder = document.createElement("div");
      holder.innerHTML = html;
      var footer = holder.firstChild;
      if (!footer) return;

      if (layout) {
        layout.appendChild(footer);
      } else {
        document.body.appendChild(footer);
      }
    } catch (e) { /* never break the page */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectFooter);
  } else {
    injectFooter();
  }

  window.BrokerAI = {
    apiUrl: apiUrl,
    apiJson: apiJson,
    fetchWithAuth: fetchWithAuth,
    apiForm: apiForm,
    showToast: showToast,
    setLoading: setLoading,
    setLoadingText: setLoadingText,
    progressCycle: progressCycle,
    confirmDialog: confirmDialog,
    confirm: confirmDialog, // alias
    validateEmail: validateEmail,
    validatePassword: validatePassword,
    validateRequired: validateRequired,
    setFieldError: setFieldError,
    clearFieldErrors: clearFieldErrors,
    getToken: getToken,
    setToken: setToken,
    clearToken: clearToken,
    bootAuth: bootAuth,
    requireAuth: requireAuth,
    logout: logout,
    parseUtcIso: parseUtcIso,
    formatScheduleInUserTz: formatScheduleInUserTz,
    dateKeyFromUtcInTz: dateKeyFromUtcInTz,
    injectFooter: injectFooter,
  };
})();
