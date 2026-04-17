/**
 * BrokerAI — Shared app layout: fixed dark sidebar + light top bar.
 * <body data-page="wizard|review|dashboard|analytics|connect">
 */
(function () {
  var ICONS = {
    wizard:    '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>',
    review:    '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
    dashboard: '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>',
    analytics: '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>',
    connect:   '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>',
    "comment-automations": '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/></svg>',
  };

  function mount() {
    var page = document.body.getAttribute("data-page") || "";
    var nav = document.getElementById("app-topnav");
    var side = document.getElementById("app-sidebar");
    var overlay = document.getElementById("app-sidebar-overlay");
    if (!nav || !side) return;

    var links = [
      { href: "/dashboard.html",              label: "Dashboard",               key: "dashboard"             },
      { href: "/wizard.html",                 label: "New Campaign",            key: "wizard"                },
      { href: "/review.html",                 label: "Review Posts",            key: "review"                },
      { href: "/analytics.html",              label: "Performance",             key: "analytics"             },
      { href: "/connect.html",                label: "Connect Social Media Accounts", key: "connect"         },
      { href: "/comment-automations.html",    label: "💬 Comment Automations", key: "comment-automations"   },
    ];

    function active(k) {
      return page === k
        ? "bg-white/10 text-white border-l-2 border-amber-500"
        : "text-slate-400 hover:bg-white/6 hover:text-white border-l-2 border-transparent";
    }

    side.setAttribute("aria-label", "Main navigation");
    side.innerHTML =
      '<div class="flex h-full flex-col">' +
        // Logo header + mobile close button
        '<div class="flex h-16 items-center justify-between gap-2 border-b border-slate-700/60 px-5">' +
          '<a href="/" class="flex items-center gap-2 text-lg font-bold text-white transition hover:opacity-80">' +
            '<span class="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-500 text-sm font-black text-white shadow-md">B</span>' +
            'Broker<span class="text-amber-400">AI</span>' +
          '</a>' +
          '<button type="button" id="sidebar-close-btn" aria-label="Close menu" class="items-center justify-center rounded-lg p-2 text-slate-400 transition hover:bg-white/10 hover:text-white">' +
            '<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>' +
          '</button>' +
        '</div>' +
        // Nav section
        '<nav class="flex flex-1 flex-col gap-0.5 overflow-y-auto p-3">' +
          '<p class="mb-2 px-3 text-[10px] font-bold uppercase tracking-widest text-slate-600">Workspace</p>' +
          links.map(function (l) {
            return (
              '<a href="' + l.href + '" class="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition duration-200 ' +
              active(l.key) + '">' +
              (ICONS[l.key] || '') +
              l.label +
              '</a>'
            );
          }).join("") +
        '</nav>' +
        // User footer
        '<div class="border-t border-slate-700/60 p-3">' +
          '<div id="nav-user-info" class="flex items-center gap-3 rounded-xl px-3 py-2.5">' +
            '<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-500/20 text-xs font-bold text-amber-400" id="nav-user-avatar">—</div>' +
            '<div class="min-w-0 flex-1">' +
              '<p id="nav-user-email" class="hidden truncate text-xs font-medium text-slate-300 sm:block"></p>' +
              '<p id="nav-user-tz" class="hidden text-[10px] text-slate-600 sm:block"></p>' +
            '</div>' +
          '</div>' +
          '<button type="button" id="nav-logout" class="mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium text-slate-500 transition hover:bg-white/5 hover:text-slate-300">' +
            '<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>' +
            'Log out' +
          '</button>' +
        '</div>' +
      '</div>';

    nav.innerHTML =
      // Social connect banner
      '<div id="brokerai-social-banner" class="hidden border-b border-amber-200/90 bg-amber-50 px-4 py-2.5 text-sm text-amber-950">' +
        '<div class="mx-auto flex max-w-6xl items-center justify-center gap-2">' +
          '<svg class="h-4 w-4 shrink-0 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>' +
          '<span>Connect your social accounts to enable publishing.</span>' +
          '<a href="/connect.html" class="ml-1 font-semibold text-amber-900 underline decoration-amber-600/60 underline-offset-2 hover:text-amber-950">Connect Social Media Accounts →</a>' +
        '</div>' +
      '</div>' +
      // Top bar
      '<div class="flex h-14 items-center justify-between gap-3 px-4 lg:px-6">' +
        '<div class="flex items-center gap-3">' +
          '<button type="button" id="brokerai-sidebar-toggle" class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:scale-105 active:scale-95 md:hidden" aria-label="Open menu">' +
            '<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg>' +
          '</button>' +
          '<span class="hidden text-sm font-semibold text-slate-800 sm:inline" id="nav-page-title">Campaign workspace</span>' +
        '</div>' +
        '<div class="flex items-center gap-2">' +
          '<a href="/wizard.html" class="hidden items-center gap-1.5 rounded-xl bg-gradient-to-br from-amber-500 to-amber-600 px-4 py-2 text-xs font-bold text-white shadow-md shadow-amber-500/20 transition duration-200 hover:scale-105 active:scale-95 sm:inline-flex">' +
            '<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4"/></svg>' +
            'New campaign' +
          '</a>' +
        '</div>' +
      '</div>';

    // Set page title in topbar
    var pageTitles = { dashboard: 'Dashboard', wizard: 'New Campaign', review: 'Review Posts', analytics: 'Performance', connect: 'Connect Social Media Accounts', 'comment-automations': 'Comment Automations' };
    var titleEl = document.getElementById('nav-page-title');
    if (titleEl && pageTitles[page]) titleEl.textContent = pageTitles[page];

    if (page === "connect") {
      var ban = document.getElementById("brokerai-social-banner");
      if (ban) ban.classList.add("hidden");
    }

    // Sidebar drawer (mobile)
    function openDrawer() {
      side.classList.add("brokerai-sidebar-open");
      side.removeAttribute("aria-hidden");
      if (overlay) overlay.classList.remove("hidden");
      var toggle = document.getElementById("brokerai-sidebar-toggle");
      if (toggle) toggle.setAttribute("aria-expanded", "true");
    }
    function closeDrawer() {
      side.classList.remove("brokerai-sidebar-open");
      if (overlay) overlay.classList.add("hidden");
      var toggle = document.getElementById("brokerai-sidebar-toggle");
      if (toggle) toggle.setAttribute("aria-expanded", "false");
      // On mobile, sidebar is off-screen when closed
      if (window.innerWidth < 768) side.setAttribute("aria-hidden", "true");
    }
    // Initialise ARIA on small screens
    if (window.innerWidth < 768) side.setAttribute("aria-hidden", "true");

    var toggle = document.getElementById("brokerai-sidebar-toggle");
    if (toggle) {
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-controls", "app-sidebar");
      toggle.addEventListener("click", function () {
        var isOpen = side.classList.contains("brokerai-sidebar-open");
        if (isOpen) { closeDrawer(); } else { openDrawer(); }
      });
    }
    var closeBtn = document.getElementById("sidebar-close-btn");
    if (closeBtn) closeBtn.addEventListener("click", closeDrawer);
    if (overlay) overlay.addEventListener("click", closeDrawer);
    // Close on Escape
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && side.classList.contains("brokerai-sidebar-open")) closeDrawer();
    });
    document.querySelectorAll("#app-sidebar a").forEach(function (a) {
      a.addEventListener("click", function () { if (window.innerWidth < 768) closeDrawer(); });
    });

    // Logout
    var lo = document.getElementById("nav-logout");
    if (lo && window.BrokerAI && BrokerAI.logout) {
      lo.addEventListener("click", function () { BrokerAI.logout(); });
    }

    // Load user info
    if (window.BrokerAI && BrokerAI.apiJson && BrokerAI.getToken && BrokerAI.getToken()) {
      BrokerAI.apiJson("/me", { method: "GET" })
        .then(function (u) {
          window.__brokerUserTimezone = (u && u.timezone) || "UTC";

          var avatarEl = document.getElementById("nav-user-avatar");
          if (avatarEl && u.email) {
            avatarEl.textContent = u.email.charAt(0).toUpperCase();
          }

          var tzEl = document.getElementById("nav-user-tz");
          if (tzEl && u.timezone) {
            tzEl.textContent = u.timezone;
            tzEl.classList.remove("hidden");
          }

          var emailEl = document.getElementById("nav-user-email");
          if (emailEl && u.email) {
            emailEl.textContent = u.email;
            emailEl.classList.remove("hidden");
          }

          var ban = document.getElementById("brokerai-social-banner");
          if (ban && page !== "connect") {
            if (u.social_connected) {
              ban.classList.add("hidden");
            } else {
              ban.classList.remove("hidden");
            }
          }
        })
        .catch(function () {});
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();

/**
 * BrokerAI — Notification Bell (S3-02)
 * Injects a bell icon with unread badge into the top-nav right section.
 * Slide-out panel lists notifications with mark-as-read support.
 */
(function () {
  "use strict";

  // ── Config ────────────────────────────────────────────────────────────────
  var POLL_INTERVAL_MS = 30000;
  var _pollTimer = null;
  var _panelOpen  = false;
  var _notifications = [];

  // ── Token helper (mirrors app.js without triggering auth redirects) ───────
  function getToken() {
    try { return localStorage.getItem("brokerai_token"); } catch (e) { return null; }
  }

  // ── Raw fetch — never throws on 401, never redirects ─────────────────────
  function notifFetch(path, method, body) {
    var token = getToken();
    if (!token) return Promise.resolve(null); // not logged in — bail silently
    var API_BASE = (function () {
      var o = window.location.origin;
      return (!o || o === "null" || !/^https?:/i.test(o)) ? "http://127.0.0.1:8000" : "";
    })();
    var opts = {
      method: method || "GET",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        Authorization: "Bearer " + token,
      },
    };
    if (body) opts.body = JSON.stringify(body);
    return fetch(API_BASE + path, opts)
      .then(function (res) {
        if (res.status === 401) return null; // silently hide bell
        if (!res.ok) return null;
        return res.json().catch(function () { return null; });
      })
      .catch(function () { return null; });
  }

  // ── Time-ago helper ───────────────────────────────────────────────────────
  function timeAgo(isoStr) {
    if (!isoStr) return "";
    var d = new Date(isoStr.indexOf("Z") === -1 ? isoStr + "Z" : isoStr);
    if (isNaN(d)) return "";
    var sec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (sec < 60) return "just now";
    var min = Math.floor(sec / 60);
    if (min < 60) return min + "m ago";
    var hr = Math.floor(min / 60);
    if (hr < 24) return hr + "h ago";
    var day = Math.floor(hr / 24);
    if (day < 30) return day + "d ago";
    return Math.floor(day / 30) + "mo ago";
  }

  // ── Badge update ──────────────────────────────────────────────────────────
  function updateBadge(count) {
    var badge = document.getElementById("notif-bell-badge");
    if (!badge) return;
    if (!count || count <= 0) {
      badge.style.display = "none";
    } else {
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.style.display = "flex";
    }
  }

  // ── Poll unread count ─────────────────────────────────────────────────────
  function pollUnreadCount() {
    notifFetch("/notifications/unread-count", "GET").then(function (data) {
      if (!data) return;
      updateBadge(data.unread_count || 0);
    });
  }

  // ── Render notification list inside the panel body ───────────────────────
  function renderPanelList() {
    var body = document.getElementById("notif-panel-body");
    if (!body) return;
    body.innerHTML = "";

    if (!_notifications.length) {
      body.innerHTML =
        '<div class="flex flex-col items-center justify-center gap-3 py-16 text-center">' +
          '<svg class="h-10 w-10 text-slate-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>' +
          '</svg>' +
          '<p class="text-sm font-semibold text-slate-500">No notifications yet</p>' +
          '<p class="text-xs text-slate-400">You\'re all caught up!</p>' +
        '</div>';
      return;
    }

    var frag = document.createDocumentFragment();
    _notifications.forEach(function (n) {
      var card = document.createElement("div");
      card.className =
        "relative flex flex-col gap-1 cursor-pointer rounded-xl p-3.5 transition duration-150 hover:bg-slate-50 " +
        (n.is_read ? "border-l-4 border-transparent" : "border-l-4 border-amber-400 bg-amber-50/40");
      card.setAttribute("role", "button");
      card.setAttribute("tabindex", "0");
      card.setAttribute("aria-label", n.title || "Notification");
      card.dataset.notifId = String(n.id);

      card.innerHTML =
        '<div class="flex items-start justify-between gap-2">' +
          '<p class="text-sm font-medium text-slate-900 leading-snug">' + escHtml(n.title || "") + '</p>' +
          '<span class="shrink-0 text-[10px] text-slate-400 whitespace-nowrap">' + timeAgo(n.created_at) + '</span>' +
        '</div>' +
        '<p class="text-xs text-slate-500 leading-snug">' + escHtml(n.message || "") + '</p>';

      function handleCardActivate() {
        var id = n.id;
        var wasRead = n.is_read;
        // Optimistically mark read in local state
        _notifications = _notifications.map(function (x) {
          return x.id === id ? Object.assign({}, x, { is_read: true }) : x;
        });
        card.classList.remove("border-amber-400", "bg-amber-50/40");
        card.classList.add("border-transparent");

        // Recount and update badge
        var unread = _notifications.filter(function (x) { return !x.is_read; }).length;
        updateBadge(unread);

        if (!wasRead) {
          notifFetch("/notifications/" + id + "/read", "PATCH");
        }

        if (n.action_url) {
          closePanel();
          window.location.href = n.action_url;
        }
      }

      card.addEventListener("click", handleCardActivate);
      card.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleCardActivate(); }
      });

      frag.appendChild(card);
    });
    body.appendChild(frag);
  }

  // ── HTML escape helper ────────────────────────────────────────────────────
  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── Open panel (fetch + render) ───────────────────────────────────────────
  function openPanel() {
    var panel   = document.getElementById("notif-panel");
    var backdrop = document.getElementById("notif-backdrop");
    if (!panel || _panelOpen) return;
    _panelOpen = true;

    // Show with transition
    panel.style.display = "flex";
    if (backdrop) backdrop.style.display = "block";
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        panel.style.transform = "translateX(0)";
        if (backdrop) backdrop.style.opacity = "1";
      });
    });

    // Fetch notifications
    var body = document.getElementById("notif-panel-body");
    if (body) {
      body.innerHTML =
        '<div class="flex items-center justify-center py-16">' +
          '<div class="h-6 w-6 animate-spin rounded-full border-2 border-amber-400 border-t-transparent"></div>' +
        '</div>';
    }

    notifFetch("/notifications", "GET").then(function (data) {
      if (!data) {
        _notifications = [];
      } else {
        _notifications = data.notifications || [];
        updateBadge(data.unread_count || 0);
      }
      renderPanelList();
    });
  }

  // ── Close panel ───────────────────────────────────────────────────────────
  function closePanel() {
    var panel    = document.getElementById("notif-panel");
    var backdrop = document.getElementById("notif-backdrop");
    if (!panel || !_panelOpen) return;
    _panelOpen = false;
    panel.style.transform = "translateX(100%)";
    if (backdrop) {
      backdrop.style.opacity = "0";
      setTimeout(function () { backdrop.style.display = "none"; }, 250);
    }
    setTimeout(function () { panel.style.display = "none"; }, 260);
  }

  // ── Mark all read ─────────────────────────────────────────────────────────
  function markAllRead() {
    _notifications = _notifications.map(function (n) {
      return Object.assign({}, n, { is_read: true });
    });
    updateBadge(0);
    renderPanelList();
    notifFetch("/notifications/read-all", "POST");
  }

  // ── Inject DOM (bell button + slide-out panel + backdrop) ────────────────
  function injectBell() {
    // Find the right-side flex container in the top nav
    var nav = document.getElementById("app-topnav");
    if (!nav) return;
    var rightGroup = nav.querySelector(".flex.items-center.gap-2");
    if (!rightGroup) return;

    // Bell button wrapper (relative, so badge can be absolute)
    var bellWrapper = document.createElement("div");
    bellWrapper.className = "relative";
    bellWrapper.id = "notif-bell-wrapper";

    bellWrapper.innerHTML =
      '<button type="button" id="notif-bell-btn" aria-label="Notifications" aria-haspopup="true" aria-expanded="false" ' +
        'class="relative inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 shadow-sm transition duration-150 hover:scale-105 hover:border-amber-300 hover:text-amber-600 active:scale-95 focus:outline-none focus:ring-2 focus:ring-amber-400/40">' +
        '<svg class="h-4.5 w-4.5" style="width:18px;height:18px" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">' +
          '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>' +
        '</svg>' +
        '<span id="notif-bell-badge" role="status" aria-live="polite" aria-atomic="true" ' +
          'style="display:none;position:absolute;top:-5px;right:-5px;min-width:18px;height:18px;padding:0 4px;font-size:10px;font-weight:700;line-height:18px;border-radius:9999px;background:#ef4444;color:#fff;border:2px solid #fff;align-items:center;justify-content:center;">' +
        '</span>' +
      '</button>';

    // Insert bell before any existing children in the right group
    rightGroup.insertBefore(bellWrapper, rightGroup.firstChild);

    // ── Backdrop ──────────────────────────────────────────────────────────
    var backdrop = document.createElement("div");
    backdrop.id = "notif-backdrop";
    backdrop.setAttribute("aria-hidden", "true");
    backdrop.style.cssText =
      "position:fixed;inset:0;z-index:49;background:rgba(15,23,42,0.25);backdrop-filter:blur(2px);" +
      "display:none;opacity:0;transition:opacity 0.25s ease;";
    document.body.appendChild(backdrop);

    // ── Slide-out panel ───────────────────────────────────────────────────
    var panel = document.createElement("div");
    panel.id = "notif-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "Notifications");
    panel.style.cssText =
      "position:fixed;top:0;right:0;bottom:0;z-index:50;width:320px;max-width:100vw;" +
      "display:none;flex-direction:column;background:#fff;border-left:1px solid #e2e8f0;" +
      "box-shadow:-8px 0 32px rgba(15,23,42,0.12);transform:translateX(100%);" +
      "transition:transform 0.25s ease;";

    panel.innerHTML =
      // Header
      '<div style="display:flex;align-items:center;justify-content:space-between;padding:16px 16px 12px;border-bottom:1px solid #f1f5f9;flex-shrink:0;">' +
        '<h2 style="font-size:15px;font-weight:700;color:#0f172a;margin:0;">Notifications</h2>' +
        '<button type="button" id="notif-mark-all-btn" ' +
          'style="font-size:11px;font-weight:600;color:#92400e;background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;padding:4px 10px;cursor:pointer;transition:background 0.15s;" ' +
          'onmouseover="this.style.background=\'#fde68a\'" onmouseout="this.style.background=\'#fef3c7\'">' +
          'Mark all read' +
        '</button>' +
      '</div>' +
      // Scrollable body
      '<div id="notif-panel-body" style="flex:1;overflow-y:auto;padding:8px;"></div>' +
      // Footer
      '<div style="padding:12px 16px;border-top:1px solid #f1f5f9;flex-shrink:0;">' +
        '<button type="button" id="notif-close-btn" ' +
          'style="width:100%;padding:10px;border-radius:10px;border:1px solid #e2e8f0;background:#fff;font-size:13px;font-weight:600;color:#475569;cursor:pointer;transition:background 0.15s;" ' +
          'onmouseover="this.style.background=\'#f8fafc\'" onmouseout="this.style.background=\'#fff\'">' +
          'Close' +
        '</button>' +
      '</div>';

    document.body.appendChild(panel);

    // ── Wire events ───────────────────────────────────────────────────────
    var bellBtn = document.getElementById("notif-bell-btn");
    if (bellBtn) {
      bellBtn.addEventListener("click", function () {
        if (_panelOpen) { closePanel(); } else { openPanel(); }
        bellBtn.setAttribute("aria-expanded", String(!_panelOpen));
      });
    }

    var closeBtn = document.getElementById("notif-close-btn");
    if (closeBtn) closeBtn.addEventListener("click", closePanel);

    var markAllBtn = document.getElementById("notif-mark-all-btn");
    if (markAllBtn) markAllBtn.addEventListener("click", markAllRead);

    backdrop.addEventListener("click", closePanel);

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && _panelOpen) closePanel();
    });
  }

  // ── Bootstrap ─────────────────────────────────────────────────────────────
  function init() {
    // Only mount when user is logged in
    if (!getToken()) return;

    injectBell();

    // Initial unread poll
    pollUnreadCount();

    // Recurring poll (store timer for potential teardown)
    _pollTimer = setInterval(pollUnreadCount, POLL_INTERVAL_MS);

    // Expose teardown on window for completeness
    window.__notifBellTeardown = function () {
      if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
      var w = document.getElementById("notif-bell-wrapper");
      var p = document.getElementById("notif-panel");
      var b = document.getElementById("notif-backdrop");
      if (w) w.remove();
      if (p) p.remove();
      if (b) b.remove();
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
