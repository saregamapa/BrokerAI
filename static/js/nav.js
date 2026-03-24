/**
 * Shared SaaS layout: fixed dark sidebar + light top bar + main.
 * <body data-page="wizard|review|dashboard|home|other">
 */
(function () {
  function mount() {
    var page = document.body.getAttribute("data-page") || "";
    var nav = document.getElementById("app-topnav");
    var side = document.getElementById("app-sidebar");
    var overlay = document.getElementById("app-sidebar-overlay");
    if (!nav || !side) return;

    var links = [
      { href: "/", label: "Home", key: "home" },
      { href: "/wizard.html", label: "Wizard", key: "wizard" },
      { href: "/review.html", label: "Review", key: "review" },
      { href: "/dashboard.html", label: "Calendar", key: "dashboard" },
    ];

    function active(k) {
      return page === k
        ? "bg-white/10 text-white border-l-2 border-amber-500"
        : "text-slate-400 hover:bg-white/5 hover:text-white border-l-2 border-transparent";
    }

    side.innerHTML =
      '<div class="flex h-full flex-col">' +
      '<div class="flex h-16 items-center gap-2 border-b border-slate-700/60 px-5">' +
      '<span class="text-lg font-semibold tracking-tight text-white">Broker<span class="text-amber-500">AI</span></span>' +
      "</div>" +
      '<nav class="flex flex-1 flex-col gap-0.5 p-3">' +
      '<p class="mb-2 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Workspace</p>' +
      links
        .map(function (l) {
          return (
            '<a href="' +
            l.href +
            '" class="flex items-center rounded-lg px-3 py-2.5 text-sm font-medium transition duration-200 ' +
            active(l.key) +
            '">' +
            l.label +
            "</a>"
          );
        })
        .join("") +
      '<div class="mt-auto border-t border-slate-700/60 pt-3">' +
      '<a href="/login.html" class="block rounded-lg px-3 py-2 text-sm text-slate-500 transition hover:bg-white/5 hover:text-slate-300">Switch account</a>' +
      "</div>" +
      "</nav>" +
      "</div>";

    nav.innerHTML =
      '<div class="flex h-14 items-center justify-between gap-3 px-4 lg:px-6">' +
      '<div class="flex min-w-0 items-center gap-3">' +
      '<button type="button" id="brokerai-sidebar-toggle" class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:scale-105 active:scale-95 md:hidden" aria-label="Open menu">' +
      '<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg>' +
      "</button>" +
      '<span class="hidden text-sm font-medium text-slate-500 sm:inline">Campaign workspace</span>' +
      "</div>" +
      '<div class="flex items-center gap-2 sm:gap-3">' +
      '<span id="nav-user-email" class="hidden max-w-[12rem] truncate text-sm text-slate-600 sm:inline"></span>' +
      '<button type="button" id="nav-logout" class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition duration-200 hover:border-slate-300 hover:shadow-md hover:scale-105 active:scale-95">Log out</button>' +
      "</div>" +
      "</div>";

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
    if (overlay) {
      overlay.addEventListener("click", closeDrawer);
    }
    document.querySelectorAll("#app-sidebar a").forEach(function (a) {
      a.addEventListener("click", function () {
        if (window.innerWidth < 768) closeDrawer();
      });
    });

    var lo = document.getElementById("nav-logout");
    if (lo && window.BrokerAI && BrokerAI.logout) {
      lo.addEventListener("click", function () {
        BrokerAI.logout();
      });
    }

    if (window.BrokerAI && BrokerAI.apiJson && BrokerAI.getToken && BrokerAI.getToken()) {
      BrokerAI.apiJson("/me", { method: "GET" })
        .then(function (u) {
          var el = document.getElementById("nav-user-email");
          if (el && u.email) el.textContent = u.email;
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
