/**
 * BrokerAI — API helpers, JWT auth, UI utilities
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
      localStorage.setItem(TOKEN_KEY, token);
    } catch (e) {}
  }

  function clearToken() {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch (e) {}
  }

  function authHeaders() {
    var h = {
      "Content-Type": "application/json",
      Accept: "application/json",
    };
    var t = getToken();
    if (t) h.Authorization = "Bearer " + t;
    return h;
  }

  async function apiJson(path, options) {
    var opts = options || {};
    var headers = Object.assign({}, authHeaders(), opts.headers || {});
    var res = await fetch(apiUrl(path), Object.assign({}, opts, { headers: headers }));
    var text = await res.text();
    var data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (e) {
      data = { detail: text || "Invalid JSON" };
    }
    if (res.status === 401) {
      clearToken();
      var onLoginPage =
        typeof window !== "undefined" &&
        (window.location.pathname.indexOf("login") !== -1 ||
          window.location.pathname.indexOf("signup") !== -1);
      if (!onLoginPage && typeof window !== "undefined") {
        window.location.href = "/login.html";
      }
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

  function setLoading(visible, elId) {
    var wrap = document.getElementById(elId || "brokerai-loading");
    if (!wrap) return;
    wrap.classList.toggle("hidden", !visible);
  }

  function requireAuth() {
    if (!getToken()) {
      if (typeof window !== "undefined") window.location.href = "/login.html";
      return false;
    }
    return true;
  }

  function logout() {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/";
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

  window.BrokerAI = {
    apiUrl: apiUrl,
    apiJson: apiJson,
    showToast: showToast,
    setLoading: setLoading,
    getToken: getToken,
    setToken: setToken,
    clearToken: clearToken,
    requireAuth: requireAuth,
    logout: logout,
    parseUtcIso: parseUtcIso,
    formatScheduleInUserTz: formatScheduleInUserTz,
    dateKeyFromUtcInTz: dateKeyFromUtcInTz,
  };
})();
