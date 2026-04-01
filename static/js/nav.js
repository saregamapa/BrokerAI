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
  };

  function mount() {
    var page = document.body.getAttribute("data-page") || "";
    var nav = document.getElementById("app-topnav");
    var side = document.getElementById("app-sidebar");
    var overlay = document.getElementById("app-sidebar-overlay");
    if (!nav || !side) return;

    var links = [
      { href: "/dashboard.html", label: "Dashboard",       key: "dashboard" },
      { href: "/wizard.html",    label: "New Campaign",    key: "wizard"    },
      { href: "/review.html",    label: "Review Posts",    key: "review"    },
      { href: "/analytics.html", label: "Performance",     key: "analytics" },
      { href: "/connect.html",   label: "Connect Accounts",key: "connect"   },
    ];

    function active(k) {
      return page === k
        ? "bg-white/10 text-white border-l-2 border-amber-500"
        : "text-slate-400 hover:bg-white/6 hover:text-white border-l-2 border-transparent";
    }

    side.innerHTML =
      '<div class="flex h-full flex-col">' +
        // Logo header
        '<div class="flex h-16 items-center gap-2 border-b border-slate-700/60 px-5">' +
          '<a href="/" class="flex items-center gap-2 text-lg font-bold text-white transition hover:opacity-80">' +
            '<span class="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-500 text-sm font-black text-white shadow-md">B</span>' +
            'Broker<span class="text-amber-400">AI</span>' +
          '</a>' +
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
          '<a href="/connect.html" class="ml-1 font-semibold text-amber-900 underline decoration-amber-600/60 underline-offset-2 hover:text-amber-950">Connect now →</a>' +
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
    var pageTitles = { dashboard: 'Dashboard', wizard: 'New Campaign', review: 'Review Posts', analytics: 'Performance', connect: 'Connect Accounts' };
    var titleEl = document.getElementById('nav-page-title');
    if (titleEl && pageTitles[page]) titleEl.textContent = pageTitles[page];

    if (page === "connect") {
      var ban = document.getElementById("brokerai-social-banner");
      if (ban) ban.classList.add("hidden");
    }

    // Sidebar drawer (mobile)
    function closeDrawer() {
      side.classList.remove("brokerai-sidebar-open");
      if (overlay) overlay.classList.add("hidden");
    }

    var toggle = document.getElementById("brokerai-sidebar-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        side.classList.toggle("brokerai-sidebar-open");
        if (overlay) overlay.classList.toggle("hidden");
      });
    }
    if (overlay) overlay.addEventListener("click", closeDrawer);
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
