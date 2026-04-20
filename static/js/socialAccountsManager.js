/**
 * Social accounts: /api/social-accounts (DB-backed list; sync + Ayrshare JWT connect server-side only).
 */
(function () {
  "use strict";

  var POLL_MS = 2000;
  var POLL_MAX = 10;
  var _lastHasProfile = false;

  var PLATFORM_LABELS = {
    facebook: "Facebook",
    instagram: "Instagram",
    linkedin: "LinkedIn",
    twitter: "X (Twitter)",
    tiktok: "TikTok",
    youtube: "YouTube",
    pinterest: "Pinterest",
    threads: "Threads",
  };

  function setActionsDisabled(disabled) {
    var bc = document.getElementById("btn-connect");
    var br = document.getElementById("btn-refresh");
    if (bc) bc.disabled = !!disabled;
    if (br) br.disabled = !!disabled;
  }

  function statusLabel(st) {
    var s = (st || "").toLowerCase();
    if (s === "connected") return "Connected";
    if (s === "expired") return "Needs reconnect";
    if (s === "disconnected") return "Disconnected";
    if (s === "error") return "Error";
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "Unknown";
  }

  function updateSummary(accounts, hasProfile) {
    var el = document.getElementById("social-accounts-summary");
    if (!el) return;
    var list = accounts || [];
    var active = list.filter(function (a) {
      return (a.status || "").toLowerCase() === "connected";
    }).length;
    var total = list.length;
    if (total === 0) {
      el.textContent = hasProfile
        ? "No networks linked yet. Connect an account to publish and sync status here."
        : "When you connect the first time, we create a secure Ayrshare profile for your workspace.";
      return;
    }
    if (active === total) {
      el.textContent =
        active +
        (active === 1 ? " account is " : " accounts are ") +
        "connected and syncing with Ayrshare.";
    } else {
      el.textContent =
        active +
        " active · " +
        (total - active) +
        (total - active === 1 ? " account needs attention" : " accounts need attention") +
        " (expired or disconnected).";
    }
  }

  function applyMetaHint(text) {
    var hint = document.getElementById("social-accounts-meta");
    if (!hint) return;
    var t = (text || "").trim();
    if (!t) {
      hint.textContent = "";
      hint.classList.add("hidden");
      return;
    }
    hint.textContent = t;
    hint.classList.remove("hidden");
  }

  /**
   * Inline SVG glyphs (viewBox 0 0 24 24) for Ayrshare-supported networks.
   * JWT connect allows: facebook, instagram, linkedin, twitter, tiktok, youtube.
   * Sync also surfaces pinterest + threads.
   * Brand paths: Facebook / LinkedIn / YouTube / Pinterest / TikTok / Threads (Simple Icons, MIT).
   */
  function _svgWrap(inner) {
    return (
      '<svg class="h-6 w-6" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
      inner +
      "</svg>"
    );
  }

  var BRAND_SVG_INNER = {
    facebook:
      '<path fill="#1877F2" d="M24 12.073C24 5.446 18.627 0 12 0S0 5.446 0 12.073c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>',
    instagram:
      '<path fill="#E4405F" d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 5.838c-3.403 0-6.162 2.759-6.162 6.162S8.597 18.163 12 18.163s6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 11-2.881 0 1.44 1.44 0 012.881 0z"/>',
    linkedin:
      '<path fill="#0A66C2" d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>',
    twitter:
      '<path fill="currentColor" d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>',
    tiktok:
      '<path fill="#000000" d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/>',
    youtube:
      '<path fill="#FF0000" d="M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>',
    pinterest:
      '<path fill="#BD081C" d="M12.017 0C5.396 0 .029 5.367.029 11.987c0 5.079 3.158 9.417 7.618 11.162-.105-.949-.199-2.403.041-3.439.219-.937 1.406-5.957 1.406-5.957s-.359-.719-.359-1.781c0-1.663.967-2.911 2.168-2.911 1.024 0 1.518.769 1.518 1.688 0 1.029-.655 2.568-.994 3.994-.285 1.193.6 2.165 1.775 2.165 2.128 0 3.768-2.245 3.768-5.487 0-2.861-2.063-4.869-5.008-4.869-3.41 0-5.409 2.562-5.409 5.199 0 1.033.394 2.127.889 2.722a.36.36 0 01.083.345l-.333 1.36c-.053.22-.174.267-.402.161-1.499-.698-2.436-2.889-2.436-4.649 0-3.785 2.75-7.262 7.929-7.262 4.163 0 7.398 2.967 7.398 6.931 0 4.136-2.607 7.464-6.227 7.464-1.216 0-2.359-.631-2.75-1.378l-.748 2.853c-.271 1.043-1.002 2.35-1.492 3.146C9.57 23.812 10.763 24.009 12.017 24c6.624 0 11.99-5.367 11.99-11.988C24.007 5.367 18.641.001.012.001z"/>',
    threads:
      '<path fill="currentColor" d="M12.186 24h-.007c-3.581-.024-6.334-1.205-8.184-3.509C2.35 18.44 1.5 15.586 1.472 12.01v-.017c.03-3.579.879-6.43 2.525-8.482C5.845 1.205 8.6.024 12.18 0h.014c2.746.02 5.043.725 6.826 2.098 1.677 1.29 2.858 3.13 3.509 5.467l-2.04.569c-1.104-3.96-3.898-5.984-8.304-6.015-2.91.022-5.11.936-6.54 2.717C4.307 6.504 3.616 8.914 3.589 12c.027 3.086.718 5.496 2.057 7.164 1.43 1.783 3.631 2.698 6.54 2.717 2.623-.02 4.358-.631 5.8-2.045 1.647-1.613 1.618-3.593 1.09-4.798-.31-.71-.873-1.3-1.634-1.75-.192 1.352-.622 2.446-1.284 3.272-.886 1.102-2.14 1.704-3.73 1.79-1.202.065-2.361-.218-3.259-.801-1.063-.689-1.685-1.74-1.752-2.964-.065-1.19.408-2.285 1.33-3.082.88-.76 2.119-1.207 3.583-1.291a13.853 13.853 0 0 1 3.02.142c-.126-.742-.375-1.332-.75-1.757-.513-.586-1.308-.883-2.359-.89h-.029c-.844 0-1.992.232-2.721 1.32L7.734 7.847c.98-1.454 2.568-2.256 4.478-2.256h.044c3.194.02 5.097 1.975 5.287 5.388.108.046.216.094.321.142 1.49.7 2.58 1.761 3.154 3.07.797 1.82.871 4.79-1.548 7.158-1.85 1.81-4.094 2.628-7.277 2.65Zm1.003-11.69c-.242 0-.487.007-.739.021-1.836.103-2.98.946-2.916 2.143.067 1.256 1.452 1.839 2.784 1.767 1.224-.065 2.818-.543 3.086-3.71a10.5 10.5 0 00-2.215-.221z"/>',
    _default:
      '<path stroke="#64748b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/>',
  };

  var INSTAGRAM_GRADIENT_INNER =
    '<defs><linearGradient id="IGGRAD" x1="0%" y1="100%" x2="100%" y2="0%"><stop stop-color="#FFDC80"/><stop offset="0.5" stop-color="#F77737"/><stop offset="1" stop-color="#C32AA8"/></linearGradient></defs>' +
    '<path fill="url(#IGGRAD)" d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 5.838c-3.403 0-6.162 2.759-6.162 6.162S8.597 18.163 12 18.163s6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 11-2.881 0 1.44 1.44 0 012.881 0z"/>';

  function _slugForIcon(plat) {
    var p = (plat || "").toLowerCase().replace(/[^a-z]/g, "");
    if (p === "x") return "twitter";
    return p;
  }

  function platformDisplayName(slug) {
    var s = _slugForIcon(slug);
    return PLATFORM_LABELS[s] || (slug ? String(slug).charAt(0).toUpperCase() + String(slug).slice(1) : "Network");
  }

  function platformIconSvg(plat) {
    var slug = _slugForIcon(plat);
    var inner = BRAND_SVG_INNER[slug] || BRAND_SVG_INNER._default;
    return _svgWrap(inner);
  }

  /** Account row avatar: brand glyph + subtle status ring */
  function platformIconTile(plat, status) {
    var st = (status || "").toLowerCase();
    var ring =
      st === "connected"
        ? "ring-2 ring-emerald-100/90"
        : st === "expired"
          ? "ring-2 ring-amber-100/90"
          : st === "disconnected"
            ? "ring-1 ring-slate-200"
            : "ring-1 ring-red-100";
    var check =
      st === "connected"
        ? '<span class="absolute -bottom-0.5 -right-0.5 h-3.5 w-3.5 rounded-full border-2 border-white bg-emerald-500 shadow-sm" title="Active"></span>'
        : "";
    return (
      '<span class="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-slate-200/90 bg-white text-slate-900 shadow-sm ' +
      ring +
      '">' +
      platformIconSvg(plat) +
      check +
      "</span>"
    );
  }

  function mountSupportedPlatformsStrip() {
    var host = document.getElementById("social-supported-strip");
    if (!host) return;
    var igId = "ig" + String(Math.random()).slice(2, 10);
    var items = [
      { slug: "facebook", label: "Facebook" },
      { slug: "instagram", label: "Instagram", gradient: true },
      { slug: "linkedin", label: "LinkedIn" },
      { slug: "twitter", label: "X (Twitter)" },
      { slug: "tiktok", label: "TikTok" },
      { slug: "youtube", label: "YouTube" },
      { slug: "pinterest", label: "Pinterest" },
      { slug: "threads", label: "Threads" },
    ];
    var lis = items
      .map(function (it) {
        var inner;
        if (it.gradient) {
          inner = INSTAGRAM_GRADIENT_INNER.split("IGGRAD").join(igId);
        } else {
          inner = BRAND_SVG_INNER[it.slug] || BRAND_SVG_INNER._default;
        }
        var svg =
          '<svg class="h-7 w-7" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
          inner +
          "</svg>";
        var shortLabel = it.label.indexOf(" ") >= 0 ? it.label.split(" ")[0] : it.label;
        return (
          '<li class="flex flex-col items-center gap-2" title="' +
          it.label +
          '">' +
          '<span class="flex h-[3.75rem] w-[3.75rem] items-center justify-center rounded-2xl border border-slate-200/90 bg-white text-slate-900 shadow-sm ring-1 ring-slate-100/80 transition duration-200 hover:-translate-y-0.5 hover:border-amber-300/70 hover:shadow-md">' +
          svg +
          "</span>" +
          '<span class="max-w-[5rem] truncate text-center text-[10px] font-semibold uppercase tracking-wide text-slate-500">' +
          shortLabel +
          "</span>" +
          "</li>"
        );
      })
      .join("");
    host.innerHTML =
      '<div class="overflow-hidden rounded-2xl border border-slate-200/80 bg-gradient-to-b from-white to-slate-50/80 shadow-md shadow-slate-200/40 ring-1 ring-slate-100/90" role="region" aria-label="Networks supported via Ayrshare">' +
      '<div class="border-b border-slate-100/90 bg-white/60 px-4 py-3 sm:px-5">' +
      '<p class="text-center text-[10px] font-bold uppercase tracking-[0.2em] text-slate-400">Supported networks</p>' +
      '<p class="mt-1 text-center text-xs text-slate-500">OAuth connections are managed by <span class="font-semibold text-slate-700">Ayrshare</span> — BrokerAI stores status for your workspace.</p>' +
      "</div>" +
      '<ul class="flex flex-wrap items-start justify-center gap-3 px-3 py-5 sm:gap-4 sm:px-5 sm:py-6">' +
      lis +
      "</ul>" +
      '<div class="border-t border-amber-100/80 bg-amber-50/40 px-4 py-3 sm:px-5">' +
      '<p class="text-center text-[11px] leading-relaxed text-slate-600"><span class="font-semibold text-slate-800">Connect flow:</span> Facebook, Instagram, LinkedIn, X, TikTok &amp; YouTube. <span class="font-semibold text-slate-800">Also supported</span> in sync: Pinterest &amp; Threads.</p>' +
      "</div>" +
      "</div>";
  }

  function statusBadgeHtml(st) {
    var lab = statusLabel(st);
    var s = (st || "").toLowerCase();
    var shell =
      s === "connected"
        ? "border-emerald-200/90 bg-emerald-50 text-emerald-900"
        : s === "expired"
          ? "border-amber-200/90 bg-amber-50 text-amber-900"
          : s === "disconnected"
            ? "border-slate-200 bg-slate-50 text-slate-700"
            : "border-red-200 bg-red-50 text-red-900";
    var dot =
      s === "connected"
        ? '<span class="h-2 w-2 shrink-0 rounded-full bg-emerald-500 shadow-sm shadow-emerald-600/30" aria-hidden="true"></span>'
        : s === "expired"
          ? '<span class="h-2 w-2 shrink-0 rounded-full bg-amber-500" aria-hidden="true"></span>'
          : '<span class="h-2 w-2 shrink-0 rounded-full bg-slate-400" aria-hidden="true"></span>';
    return (
      '<span class="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[11px] font-bold uppercase tracking-wide ' +
      shell +
      '">' +
      dot +
      "<span>" +
      escapeHtml(lab) +
      "</span></span>"
    );
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function updateLastSync(accounts) {
    var el = document.getElementById("social-last-sync");
    if (!el) return;
    var latest = 0;
    (accounts || []).forEach(function (a) {
      if (!a.last_synced_at) return;
      var iso = a.last_synced_at.indexOf("Z") === -1 ? a.last_synced_at + "Z" : a.last_synced_at;
      var t = new Date(iso).getTime();
      if (!isNaN(t) && t > latest) latest = t;
    });
    if (!latest) {
      el.textContent = "Not synced yet";
      return;
    }
    var sec = Math.max(0, Math.floor((Date.now() - latest) / 1000));
    if (sec < 60) el.textContent = "Updated " + sec + "s ago";
    else if (sec < 3600) el.textContent = "Updated " + Math.floor(sec / 60) + "m ago";
    else el.textContent = "Updated " + Math.floor(sec / 3600) + "h ago";
  }

  function statusHintHtml(st) {
    var s = (st || "").toLowerCase();
    if (s === "expired") {
      return (
        '<p class="mt-1.5 text-xs leading-snug text-amber-800/90">Token or permissions expired. Reconnect in Ayrshare, then run <strong class="font-semibold">Sync now</strong>.</p>'
      );
    }
    if (s === "disconnected") {
      return '<p class="mt-1.5 text-xs leading-snug text-slate-500">Not used for publishing. Revoke access in Ayrshare if you no longer need this profile.</p>';
    }
    return '<p class="mt-1.5 text-xs leading-snug text-slate-500">In sync with Ayrshare · eligible for publishing when your plan allows.</p>';
  }

  function renderEmptyState() {
    return (
      '<div class="rounded-2xl border border-dashed border-slate-200/90 bg-gradient-to-b from-slate-50/80 to-white px-5 py-12 text-center sm:px-8 sm:py-14">' +
      '<div class="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-white shadow-inner ring-1 ring-slate-200/80">' +
      '<svg class="h-7 w-7 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>' +
      "</div>" +
      '<p class="text-base font-semibold tracking-tight text-slate-900">No linked accounts yet</p>' +
      '<p class="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-600">Link the networks you publish to. You’ll step through Ayrshare’s secure OAuth flow, then return here—we’ll pull the latest status automatically.</p>' +
      '<ol class="mx-auto mt-8 max-w-sm space-y-3 text-left text-sm text-slate-600">' +
      '<li class="flex gap-3"><span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-amber-100 text-xs font-bold text-amber-900">1</span><span><span class="font-semibold text-slate-800">Connect account</span> — opens Ayrshare</span></li>' +
      '<li class="flex gap-3"><span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-amber-100 text-xs font-bold text-amber-900">2</span><span>Sign in and approve the social network</span></li>' +
      '<li class="flex gap-3"><span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-amber-100 text-xs font-bold text-amber-900">3</span><span>Back on BrokerAI, tap <span class="font-semibold text-slate-800">Sync now</span> if accounts don’t appear within a few seconds</span></li>' +
      "</ol>" +
      "</div>"
    );
  }

  function render(accounts) {
    var root = document.getElementById("social-accounts-list");
    if (!root) return;
    if (!accounts || !accounts.length) {
      root.innerHTML = renderEmptyState();
      return;
    }
    root.innerHTML = accounts
      .map(function (a) {
        var st = (a.status || "").toLowerCase();
        var showDisconnect = st === "connected" || st === "expired";
        var platName = platformDisplayName(a.platform);
        var sub =
          '<p class="truncate font-mono text-[11px] tracking-tight text-slate-400">' +
          escapeHtml(String(a.account_id || "").slice(0, 52) || "—") +
          "</p>";
        var titleBlock =
          '<p class="truncate text-[15px] font-semibold tracking-tight text-slate-900">' +
          escapeHtml(a.account_name || platName) +
          "</p>" +
          '<p class="text-xs font-medium text-slate-500">' +
          escapeHtml(platName) +
          "</p>";
        return (
          '<article class="group relative flex flex-col gap-4 rounded-2xl border border-slate-200/90 bg-white p-4 shadow-sm ring-1 ring-transparent transition duration-200 hover:-translate-y-px hover:border-slate-300/90 hover:shadow-md hover:ring-slate-100 sm:flex-row sm:items-stretch sm:justify-between sm:p-5" role="listitem">' +
          '<div class="flex min-w-0 flex-1 items-start gap-4">' +
          platformIconTile(a.platform, a.status) +
          '<div class="min-w-0 flex-1">' +
          titleBlock +
          sub +
          statusHintHtml(a.status) +
          "</div>" +
          "</div>" +
          '<div class="flex shrink-0 flex-col items-stretch justify-center gap-3 border-t border-slate-100 pt-3 sm:w-52 sm:border-0 sm:border-l sm:border-slate-100 sm:pl-5 sm:pt-0">' +
          '<div class="flex justify-end sm:justify-end">' +
          statusBadgeHtml(a.status) +
          "</div>" +
          (showDisconnect
            ? '<button type="button" data-disconnect="' +
              a.id +
              '" class="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-center text-xs font-semibold text-slate-700 shadow-sm transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-300 focus-visible:ring-offset-2 sm:w-auto">Remove from BrokerAI</button>'
            : "") +
          "</div>" +
          "</article>"
        );
      })
      .join("");

    root.querySelectorAll("[data-disconnect]").forEach(function (btn) {
      btn.addEventListener("click", onDisconnectClick);
    });
  }

  async function loadList() {
    var data = await window.BrokerAI.apiJson("/api/social-accounts", { method: "GET" });
    _lastHasProfile = !!data.has_profile;
    updateSummary(data.accounts || [], _lastHasProfile);
    if (_lastHasProfile) applyMetaHint("");
    else applyMetaHint("Your Ayrshare profile is created automatically the first time you connect.");
    render(data.accounts || []);
    updateLastSync(data.accounts || []);
  }

  async function onSync(silent) {
    if (!silent) {
      setActionsDisabled(true);
      if (window.BrokerAI.setLoading) window.BrokerAI.setLoading(true, "Syncing with Ayrshare…");
    }
    try {
      var res = await window.BrokerAI.apiJson("/api/social-accounts/sync", { method: "POST" });
      if (!res.ok) {
        var msg =
          res.error === "ayrshare_unreachable"
            ? "Couldn’t reach Ayrshare. Check your connection and try again."
            : res.error === "ayrshare_error"
              ? "Ayrshare reported an auth issue. Reconnect the network, then sync again."
              : "Sync didn’t complete. Please try again.";
        if (!silent) window.BrokerAI.showToast(msg, "error");
      } else if (!silent) {
        window.BrokerAI.showToast("Accounts are up to date.", "success");
      }
      updateSummary(res.accounts || [], _lastHasProfile);
      render(res.accounts || []);
      updateLastSync(res.accounts || []);
    } catch (e) {
      if (!silent) window.BrokerAI.showToast((e && e.message) || "Sync failed. Retry.", "error");
    } finally {
      if (!silent) {
        if (window.BrokerAI.setLoading) window.BrokerAI.setLoading(false);
        setActionsDisabled(false);
      }
    }
  }

  async function onConnect() {
    var bc = document.getElementById("btn-connect");
    if (bc) bc.disabled = true;
    try {
      var res = await window.BrokerAI.apiJson("/api/social-accounts/connect", { method: "POST" });
      if (res.url) window.location.href = res.url;
    } catch (e) {
      window.BrokerAI.showToast((e && e.message) || "Could not start connect flow.", "error");
    } finally {
      if (bc) bc.disabled = false;
    }
  }

  async function onDisconnectClick(ev) {
    var id = parseInt(ev.currentTarget.getAttribute("data-disconnect"), 10);
    if (!id) return;
    if (
      !window.confirm(
        "Remove this account from BrokerAI? It will stay connected in Ayrshare until you revoke it there."
      )
    )
      return;
    try {
      await window.BrokerAI.apiJson("/api/social-accounts/disconnect", {
        method: "POST",
        body: JSON.stringify({ id: id }),
      });
      window.BrokerAI.showToast("Account removed from your workspace.", "success");
      await loadList();
    } catch (e) {
      window.BrokerAI.showToast((e && e.message) || "Disconnect failed.", "error");
    }
  }

  function pollAfterReturn() {
    var qs = new URLSearchParams(window.location.search);
    if (qs.get("synced") !== "1") return;
    var n = 0;
    function scheduleNext() {
      if (n >= POLL_MAX) return;
      setTimeout(tick, n === 0 ? 400 : POLL_MS);
    }
    function tick() {
      n++;
      window.BrokerAI.apiJson("/api/social-accounts/sync", { method: "POST" }).then(
        function (res) {
          updateSummary(res.accounts || [], _lastHasProfile);
          render(res.accounts || []);
          updateLastSync(res.accounts || []);
          if (n === 1 && res.ok) window.BrokerAI.showToast("Finishing setup after connect…", "info");
        },
        function () {}
      ).finally(scheduleNext);
    }
    scheduleNext();
    if (window.history.replaceState) {
      window.history.replaceState({}, "", "/connect.html");
    }
  }

  async function init() {
    await window.BrokerAI.bootAuth();
    mountSupportedPlatformsStrip();
    var bc = document.getElementById("btn-connect");
    var br = document.getElementById("btn-refresh");
    if (bc) bc.addEventListener("click", onConnect);
    if (br)
      br.addEventListener("click", function () {
        onSync(false);
      });
    if (window.BrokerAI.setLoading) window.BrokerAI.setLoading(true);
    try {
      await loadList();
    } finally {
      if (window.BrokerAI.setLoading) window.BrokerAI.setLoading(false);
    }
    pollAfterReturn();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
