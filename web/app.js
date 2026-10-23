// BatchWatch front end: hash-routed views, English and Arabic, no build step.
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const iso = (s) => `⁨${s ?? ""}⁩`;  // keeps a product name or code from reordering the sentence around it
  const store = {
    get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch { /* private mode */ } },
  };

  // ---------- language
  let lang = store.get("bw.lang") || ((navigator.language || "").startsWith("ar") ? "ar" : "en");
  const t = (key, ...args) => {
    const v = (window.I18N[lang] || {})[key] ?? window.I18N.en[key];
    if (v === undefined) return key;
    return typeof v === "function" ? v(...args) : v;
  };
  const has = (key) => key in (window.I18N[lang] || {}) || key in window.I18N.en;
  const locale = () => (lang === "ar" ? "ar-u-ca-gregory-nu-latn" : "en-GB");

  function fmtDate(d) {
    let s = String(d || "");
    if (/^\d{8}$/.test(s)) s = `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}`;
    const m = /^(\d{4}-\d{2}-\d{2})/.exec(s);
    if (!m) return s;
    return new Date(`${m[1]}T00:00:00Z`).toLocaleDateString(locale(), { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  }
  function fmtDayMonth(d) {
    const m = /^(\d{4}-\d{2}-\d{2})/.exec(String(d || ""));
    return m ? new Date(`${m[1]}T00:00:00Z`).toLocaleDateString(locale(), { day: "numeric", month: "short", timeZone: "UTC" }) : "";
  }
  function fmtTime(iso8601) {
    const d = new Date(iso8601);
    return isNaN(d) ? "" : d.toLocaleString(locale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }
  function ago(iso8601) {
    const d = new Date(iso8601);
    if (isNaN(d)) return "";
    const s = (d - Date.now()) / 1000, rtf = new Intl.RelativeTimeFormat(locale(), { numeric: "auto" });
    for (const [unit, size] of [["day", 86400], ["hour", 3600], ["minute", 60]]) if (Math.abs(s) >= size) return rtf.format(Math.round(s / size), unit);
    return rtf.format(Math.round(s), "second");
  }
  function agoShort(iso8601) {
    const m = Math.max(0, Math.round((Date.now() - new Date(iso8601)) / 60000));
    return m < 60 ? t("short_min", m) : m < 1440 ? t("short_hour", Math.round(m / 60)) : t("short_day", Math.round(m / 1440));
  }
  const hoursSince = (iso8601) => (Date.now() - new Date(iso8601)) / 3600000;
  const money = (v) => (v == null ? "–" : `$${Number(v).toFixed(v < 0.1 ? 3 : 2)}`);

  // ---------- vocabulary
  const STOP_TYPES = new Set(["recall", "falsified", "substandard", "watchlist"]);
  const SOURCES = {
    "SFDA (Saudi Arabia)": ["SFDA", "sa"], "FDA (US)": ["FDA", "us"], "MHRA (UK)": ["MHRA", "uk"], "Health Canada": ["Health Canada", "ca"],
    "NAFDAC (Nigeria)": ["NAFDAC", "ng"], "DRAP (Pakistan)": ["DRAP", "pk"], "SAHPRA (South Africa)": ["SAHPRA", "za"],
    "Rwanda FDA": ["Rwanda FDA", "rw"], "FDA Ghana": ["FDA Ghana", "gh"], WHO: ["WHO", "global"],
  };
  function place(source) {
    const known = SOURCES[source];
    if (known) return { reg: known[0], code: known[1], country: t(`c_${known[1]}`) };
    const m = /^(.*?)\s*\((.*)\)$/.exec(source || "");
    return m ? { reg: m[1], code: "", country: m[2] } : { reg: source || "", code: "", country: "" };
  }
  const where = (source) => { const p = place(source); return p.country && p.country !== p.reg ? `${p.reg} · ${p.country}` : p.reg; };
  // a country named by the trace agent (in English): WHO's global reports are named as such, Arabic gets the Arabic name
  function countryName(c, authority) {
    if (!c || /^international/i.test(c)) return /\bWHO\b|World Health/i.test(authority || "") ? t("who_global") : t("international");
    return lang === "ar" ? (window.I18N.countriesAr[c] || c) : c;
  }

  // a pharmacist's decision on a "Confirm product" result: {kind: "same"|"different", at, why: "lookalike"|"product"|"all"}
  const decisions = new Map();
  const dkey = (m, scope = "pack") => `${scope}|${scope === "stock" ? lineKey(m) : ""}|${m.alert.alert_id}`;
  const decided = (m, scope = "pack") => decisions.get(dkey(m, scope));
  const isLookalike = (m) => m.reasons.some((r) => /look-alike/.test(r));
  const tierOf = (m, scope) => { const d = decided(m, scope); return d && d.kind === "same" ? "CONFIRMED" : m.tier; };
  function sevOf(m, scope) {
    const d = decided(m, scope);
    if (d && d.kind === "different") return "off";
    const tier = tierOf(m, scope);
    if (tier === "CONFIRMED") return STOP_TYPES.has(m.alert.type) ? "stop" : "info";
    if (tier === "VERIFY") return "check";
    if (tier === "WATCH") return "info";
    return "off";
  }
  function statusKey(m, scope) {
    const d = decided(m, scope);
    if (d && d.kind === "different") return "ruledout";
    const tier = tierOf(m, scope);
    if (tier === "CONFIRMED") return STOP_TYPES.has(m.alert.type) ? "stop" : "follow";
    if (tier === "VERIFY") return isLookalike(m) ? "checkbatch" : "confirm";
    return { WATCH: "review", WITHDRAWN: "none" }[tier];
  }
  const RANK = { stop: 0, checkbatch: 1, confirm: 1, follow: 2, review: 3, none: 4, ruledout: 5 };
  const rank = (m, scope) => RANK[statusKey(m, scope)];
  const ICON = {
    stop: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.5 3h7L21 8.5v7L15.5 21h-7L3 15.5v-7z M12 8v5 M12 16.5v.01"/></svg>`,
    check: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4L2.5 20h19z M12 10v4 M12 17v.01"/></svg>`,
    info: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z M12 11v5 M12 8v.01"/></svg>`,
    off: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z M8 12h8"/></svg>`,
    none: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4a7 7 0 1 0 0 14a7 7 0 0 0 0-14z M16 16l4.5 4.5 M8.5 11h5"/></svg>`,
    ext: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6 M20 4l-9 9 M18 14v6H4V6h6"/></svg>`,
    chev: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`,
    globe: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z M3 12h18 M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/></svg>`,
    ok: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>`,
    warn: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4L2.5 20h19z M12 10v4 M12 17v.01"/></svg>`,
    chip: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h10v10H7z M9 3v4 M15 3v4 M9 17v4 M15 17v4 M3 9h4 M3 15h4 M17 9h4 M17 15h4"/></svg>`,
    flag: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 21V4 M5 4h11l-2 4 2 4H5"/></svg>`,
    lock: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 11h12v9H6z M8.5 11V8a3.5 3.5 0 0 1 7 0v3"/></svg>`,
  };
  const sevIcon = (sev) => ICON[sev === "stop" ? "stop" : sev === "check" ? "check" : sev === "info" ? "info" : "off"];
  const badge = (m, scope) => `<span class="badge s-${sevOf(m, scope)}">${sevIcon(sevOf(m, scope))}${esc(t(`st_${statusKey(m, scope)}`))}</span>`;
  const typeName = (type) => t(`type_${(type || "other").replace(/ /g, "_")}`);
  function typeTag(type, status) {
    if (status === "withdrawn") return `<span class="tag">${esc(t("type_withdrawn"))}</span>`;
    return `<span class="tag${type === "falsified" ? " t-falsified" : ""}">${esc(typeName(type))}</span>`;
  }
  const lot = (b, hit = false) => `<span class="lot${hit ? " hit" : ""}">${esc(b)}</span>`;
  const mono = (s) => `<bdi class="mono" dir="ltr">${esc(s)}</bdi>`;
  const extLink = (url, text, cls = "") => `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener" dir="auto">${esc(text)}${ICON.ext}</a>`;
  const sfdaArabic = (url) => (/sfda\.gov\.sa\/en\//.test(url || "") ? url.replace("/en/", "/ar/") : null);
  const strengthShown = (name, strength) => strength && !String(name || "").toLowerCase().replace(/\s+/g, "").includes(String(strength).toLowerCase().replace(/\s+/g, ""));

  // ---------- state
  let registry = null, statusData = null, insightsData = null, limits = null;
  let lastLive = null;  // the live SFDA search for the batch on screen: {loading}, {error}, or the server's answer
  let lastPack = null, packParams = null, stock = null, stockFilter = "", groupByLocation = false;
  const lastVisit = store.get("bw.lastVisit");

  async function api(path, opts) {
    const res = await fetch(path, opts);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || t("err_request", res.status));
    return body;
  }
  function announce(msg) { $("#sr-status").textContent = ""; setTimeout(() => ($("#sr-status").textContent = msg), 50); }
  let toastTimer;
  function toast(msg, action) {
    const box = $("#toast");
    box.innerHTML = `<span>${esc(msg)}</span>${action ? `<button type="button">${esc(action.label)}</button>` : ""}<button type="button" aria-label="${esc(t("dismiss"))}">✕</button>`;
    box.hidden = false;
    const btns = $$("button", box);
    if (action) btns[0].addEventListener("click", () => { box.hidden = true; action.run(); });
    btns[btns.length - 1].addEventListener("click", () => (box.hidden = true));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (box.hidden = true), 8000);
  }

  // ---------- static text and direction
  function applyStatic() {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    $$("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
    $$("[data-i18n-ph]").forEach((el) => (el.placeholder = t(el.dataset.i18nPh)));
    $$("[data-i18n-aria]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nAria)));
    const btn = $("#lang-btn");
    btn.textContent = lang === "ar" ? "EN" : "ع";
    btn.lang = lang === "ar" ? "en" : "ar";
    btn.setAttribute("aria-label", lang === "ar" ? "English" : "العربية");
    if (lang === "ar" && !$("#font-ar")) {
      document.head.appendChild(Object.assign(document.createElement("link"), { id: "font-ar", rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap" }));
    }
  }
  $("#lang-btn").addEventListener("click", () => {
    lang = lang === "ar" ? "en" : "ar";
    store.set("bw.lang", lang);
    applyStatic();
    renderLive();
    renderIntro();
    renderSaved();
    if (lastPack) renderPack();
    if (stock) renderStock();
    alertsReady = false; howReady = false;
    show(location.hash.slice(1), false);
  });

  // ---------- routing
  const VIEWS = ["check", "alerts", "how"];
  function show(hash, focus = true) {
    const name = VIEWS.includes(hash) ? hash : hash === "registry" ? "alerts" : "check";
    $$(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
    $$(".nav a").forEach((a) => (a.dataset.view === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    document.title = name === "check" ? `BatchWatch · ${t("check_title")}` : `${t(`nav_${name}`)} · BatchWatch`;
    if (name !== "check") { stopCamera(); setSticky(false); } else if (lastPack && mode === "pack") setSticky(true);
    if (name === "alerts") renderAlerts();
    if (name === "how") renderHow();
    if (focus) $(`#${name}-title`)?.focus({ preventScroll: true });
  }
  window.addEventListener("hashchange", () => { show(location.hash.slice(1)); window.scrollTo(0, 0); });

  // ---------- live status (the smart update), honest about its age
  async function loadStatus() {
    try { statusData = await api("/api/status"); } catch { statusData = null; }
    renderLive();
  }
  function liveState() {
    const run = statusData && statusData.last_run;
    if (!statusData) return { state: "idle", text: t("live_offline"), short: "" };
    if (!run || !run.finished) {  // no scheduled check yet: the phone still shows how fresh the alerts are
      const asof = statusData.alerts_as_of;
      return { state: "idle", text: asof ? t("live_asof", fmtDate(asof)) : t("live_offline"), short: asof ? fmtDayMonth(asof) : "" };
    }
    const h = hoursSince(run.finished);
    if (run.out_of_credit) return { state: "stale", text: t("live_credit", ago(run.finished)), short: agoShort(run.finished) };
    if (h <= 2) return { state: "live", text: t("live_checked", ago(run.finished)), short: agoShort(run.finished) };
    if (h <= 24) return { state: "stale", text: t("live_stale", ago(run.finished)), short: agoShort(run.finished) };
    return { state: "idle", text: t("live_old", fmtDate(run.finished)), short: agoShort(run.finished) };
  }
  function renderLive() {
    const btn = $("#live-btn"), ls = liveState();
    btn.dataset.state = ls.state;
    $("#live-text").textContent = ls.text;
    $("#live-short").textContent = ls.short;
    btn.title = ls.text;
    btn.setAttribute("aria-label", ls.text);
    const s = statusData;
    if (!s) { $("#live-pop").innerHTML = `<p>${esc(t("live_offline"))}</p>`; return; }
    const run = s.last_run;
    // the per-regulator list only once a scheduled check has reached a regulator; before that it is all dashes
    const checked = s.sources.some((x) => x.last_checked || x.last_error);
    const rows = checked ? s.sources.map((x) => `<tr><td>${esc(where(x.name))}</td><td>${x.last_error ? `<span class="s-check">${esc(t("live_error"))}</span>` : x.last_checked ? esc(ago(x.last_checked)) : "–"}</td></tr>`).join("") : "";
    $("#live-pop").innerHTML = `<h3>${esc(t("live_title"))}</h3>
      <p><strong>${esc(ls.text)}</strong></p>
      <p>${run ? esc(t("live_last", fmtTime(run.finished || run.started), run.seconds, run.new, money(run.cost_usd))) : esc(t("live_never"))}</p>
      ${run && run.out_of_credit ? `<p class="s-check">${esc(t("live_credit_note"))}</p>` : ""}
      ${s.runs_24h ? `<p>${esc(t("live_24h", s.runs_24h, s.new_24h, money(s.cost_24h)))}</p>` : ""}
      ${s.detection && s.detection.median_hours != null ? `<p>${esc(t("live_detect", s.detection.median_hours, s.detection.alerts))}</p>` : ""}
      <p class="note">${esc(t("live_explain"))}</p>
      ${rows ? `<table class="src-table"><tbody>${rows}</tbody></table>` : ""}
      <p><a href="/feed.xml">${esc(t("feed"))}</a> · <a href="#how" data-close>${esc(t("live_more"))}</a></p>`;
  }
  $("#live-btn").addEventListener("click", () => {
    const pop = $("#live-pop"), open = pop.hidden;
    pop.hidden = !open;
    $("#live-btn").setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (e) => {
    const pop = $("#live-pop");
    if (!pop.hidden && (e.target.closest("[data-close]") || !e.target.closest("#live-pop, #live-btn"))) {
      pop.hidden = true; $("#live-btn").setAttribute("aria-expanded", "false");
    }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#live-pop").hidden) { $("#live-pop").hidden = true; $("#live-btn").focus(); } });

  // ---------- mode switch
  let mode = "pack";
  const tabs = $$(".segmented [role=tab]");
  function setMode(next) {
    mode = next;
    tabs.forEach((b) => { const on = b.dataset.mode === mode; b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    $("#panel-pack").hidden = mode !== "pack";
    $("#panel-stock").hidden = mode !== "stock";
    $("#pack-result").hidden = mode !== "pack";
    $("#stock-result").hidden = mode !== "stock";
    $("#check-grid").dataset.mode = mode;
    if (mode !== "pack") { stopCamera(); setSticky(false); } else if (lastPack) setSticky(true);
    renderIntro();
  }
  tabs.forEach((b, i) => {
    b.addEventListener("click", () => setMode(b.dataset.mode));
    b.addEventListener("keydown", (e) => {
      const keys = { ArrowRight: 1, ArrowLeft: -1, Home: "first", End: "last" };
      if (!(e.key in keys)) return;
      e.preventDefault();
      const j = keys[e.key] === "first" ? 0 : keys[e.key] === "last" ? tabs.length - 1 : (i + (lang === "ar" ? -keys[e.key] : keys[e.key]) + tabs.length) % tabs.length;
      setMode(tabs[j].dataset.mode); tabs[j].focus();
    });
  });

  // ---------- intro: one quiet line of proof under the box, before any answer
  function renderIntro() {
    const box = $("#intro");
    const empty = mode === "pack" ? !lastPack : !stock;
    box.hidden = !empty;
    $("#check-grid").classList.toggle("has-result", !empty);
    if (!empty) return;
    const ev = registry && registry.stats.evaluation;
    const facts = [
      registry ? [ICON.flag, `<b>${registry.stats.sources.length}</b> ${esc(t("p_regulators"))}`] : null,
      ev ? [ICON.ok, `<b>${ev.batches_correct}/${ev.batches_gold}</b> ${esc(t("p_batches"))}`] : null,
      [ICON.lock, esc(t(mode === "pack" ? "privacy_pack" : "privacy_stock"))],
    ].filter(Boolean);
    box.innerHTML = `<ul class="facts-line">${facts.map(([icon, html]) => `<li>${icon}<span>${html}</span></li>`).join("")}</ul>
      <p class="powered">${t("powered_html")}${mode === "pack" ? ` · <a href="demo-pack.svg" target="_blank" rel="noopener">${esc(t("demo_pack_link"))}</a>` : ""}</p>`;
  }

  // ---------- an alert as evidence (one pack and stock rows)
  function headline(m, scope) {
    const noun = t(`noun_${(m.alert.type || "other").replace(/ /g, "_")}`);
    const tier = tierOf(m, scope);
    const key = `h_conf_${String(m.alert.type).replace(/ /g, "_")}`;
    if (tier === "CONFIRMED") return has(key) ? t(key) : t("h_conf_other", noun);
    if (tier === "VERIFY") return isLookalike(m) ? t("h_lookalike", noun) : m.alert.batch === "ALL" ? t("h_allbatches", noun) : t("h_batch_in", noun);
    if (tier === "WATCH") return t("h_watch", noun);
    return t("h_withdrawn");
  }
  function subline(m, regs, scope) {
    const a = m.alert, tier = tierOf(m, scope), d = decided(m, scope);
    let s;
    if (tier === "CONFIRMED") s = d ? t("sub_confirmed_by_you", iso(a.product)) : t("sub_confirmed", iso(a.product));
    else if (tier === "WITHDRAWN") s = t("sub_withdrawn");
    else if (tier === "WATCH") s = t("sub_watch", iso(a.product));
    else if (isLookalike(m)) s = t("sub_lookalike", iso(a.batch));
    else if (a.batch === "ALL") s = t("sub_all", iso(a.product), a.condition ? iso(a.condition) : "");
    else s = t("sub_batch", iso(a.product));
    return regs.length > 1 ? `${s} ${t("sub_reported_by", regs)}` : s;
  }
  const todoText = (m, scope) => {
    const k = statusKey(m, scope);
    if (k === "confirm") return STOP_TYPES.has(m.alert.type) ? t("do_confirm_stop") : t("do_confirm_notice");
    return t(`do_${k}`);
  };
  // a check that failed on the very batch being acted on is said out loud
  function flagNote(m) {
    const f = (m.alert.flags || []).find((x) => (x.batches || []).some((b) => String(b).toUpperCase() === String(m.alert.batch).toUpperCase()));
    return f ? `<p class="flag-note">${ICON.flag}<span>${esc(t(`flag_${f.check}`, m.alert.batch, m.alert.product))}</span></p>` : "";
  }
  function checksPill(a) {
    const flagged = a.checks_passed < a.checks_total;
    return `<span class="pill${flagged ? " pill-warn" : ""}">${flagged ? ICON.flag : ICON.ok}${esc(flagged ? t("checks_flag", a.checks_passed, a.checks_total) : t("checks_n", a.checks_passed, a.checks_total))}</span>`;
  }
  function crossLine(tb) {
    if (!(tb && tb.verified)) return "";
    const e = tb.earliest;
    const places = tb.places.map((c) => countryName(c, e && e.authority));
    return `<p class="provenance">${ICON.globe}<span>${esc(t("also_flagged", places, e ? { place: countryName(e.country, e.authority), days: e.days } : null))}</span></p>`;
  }

  function alertRef(m, opts = {}) {
    const a = m.alert, scope = opts.scope || "pack", d = decided(m, scope);
    const ar = sfdaArabic(a.url);
    return `<section class="ref${d && d.kind === "different" ? " dismissed" : ""}" data-alert="${esc(a.alert_id)}">
      <div class="ref-head">${typeTag(a.type)}<span>${esc(where(a.source))} · ${esc(fmtDate(a.date))}</span>${d && d.kind === "different" ? `<span class="badge s-off">${esc(t("not_this"))}</span>` : ""}</div>
      ${extLink(a.url, a.title, "ref-title")}
      ${ar && lang === "ar" ? `<p class="provenance">${extLink(ar, t("notice_ar"), "ext")}</p>` : ""}
      <dl class="facts">
        <dt>${esc(t("f_alert_product"))}</dt><dd><bdi>${esc(a.product)}</bdi></dd>
        <dt>${esc(t("f_alert_batch"))}</dt><dd>${a.batch === "ALL" ? esc(t("all_batches")) + (a.condition ? ` (<bdi>${esc(a.condition)}</bdi>)` : "") : lot(a.batch, true)}</dd>
        ${a.reason ? `<dt>${esc(t("f_reason"))}</dt><dd dir="auto">${esc(a.reason)}</dd>` : ""}
        ${a.required_action ? `<dt>${esc(t("f_regulator_says"))}</dt><dd dir="auto">${esc(a.required_action)}</dd>` : ""}
      </dl>
      ${flagNote(m)}
      ${crossLine(a.trace)}
      <details class="more evidence"><summary>${esc(t("evidence"))}</summary><div class="evidence-body">
        <div class="provenance"><span class="pill">${ICON.chip}${esc(t("read_by", a.model))}</span>${checksPill(a)}</div>
        <p class="note">${esc(t("why_matched"))}</p><ul class="note">${m.reasons.map((r) => `<li dir="auto">${esc(r)}</li>`).join("")}</ul>
        <details class="more" data-reading="${esc(a.alert_id)}"><summary>${esc(t("how_read"))}</summary><div class="reading-out"><p class="loading">${esc(t("loading"))}</p></div></details>
        ${opts.noTrace ? "" : `<details class="more" data-trace-open><summary>${esc(t("where_else"))}</summary>${traceBlock(a.alert_id)}</details>`}
      </div></details>
    </section>`;
  }

  function compareStrip(yours, theirs) {
    const a = String(yours).toUpperCase(), b = String(theirs).toUpperCase();
    const name = (ch) => (/[0-9]/.test(ch) ? t("char_digit", ch) : /[A-Z]/.test(ch) ? t("char_letter", ch) : ch);
    const mark = (s, o) => [...s].map((ch, i) => (ch !== o[i] ? `<span class="diff" data-name="${esc(name(ch))}">${esc(ch)}</span>` : esc(ch))).join("");
    return `<div class="compare" dir="ltr"><div><span class="label">${esc(t("cmp_yours"))}</span><code>${mark(a, b)}</code></div>
      <div><span class="label">${esc(t("cmp_alert"))}</span><code>${mark(b, a)}</code></div></div>`;
  }

  // the question that turns "Confirm product" into an answer; scope "pack" or "stock"
  function questionHtml(m, scope, yourBatch) {
    const a = m.alert, key = dkey(m, scope);
    if (tierOf(m, scope) !== "VERIFY" || decided(m, scope)) return "";
    let q, yes, no;
    if (isLookalike(m)) { q = t("q_lookalike_html", mono(a.batch)); yes = t("yes_says", a.batch); no = t("no_says", yourBatch); }
    else if (a.batch === "ALL") { q = esc(t("q_all", iso(a.product), a.condition ? iso(a.condition) : "")); yes = t("yes_same"); no = t("no_different"); }
    else { q = esc(t("q_product", iso(a.product))); yes = t("yes_same"); no = t("no_different"); }
    return `<div class="confirm" role="group" aria-label="${esc(t("confirm_label"))}">
      <p>${q}</p>
      <div class="row-btns"><button class="btn btn-danger" type="button" data-decide="same" data-key="${esc(key)}">${esc(yes)}</button>
      <button class="btn btn-soft" type="button" data-decide="different" data-key="${esc(key)}">${esc(no)}</button></div></div>`;
  }
  function decisionNote(m, scope) {
    const d = decided(m, scope);
    if (!d) return "";
    const key = dkey(m, scope);
    const text = d.kind === "same" ? t("confirmed_at", fmtTime(d.at)) : t("ruled_out_at", fmtTime(d.at));
    return `<p class="confirmed-note">${esc(text)} <button class="link-btn" type="button" data-undo="${esc(key)}">${esc(t("undo"))}</button></p>`;
  }
  function decide(key, kind) {
    const scope = key.split("|")[0], alertId = key.split("|").pop();
    const m = (scope === "pack" ? lastPack.matches : stock.matches).find((x) => dkey(x, scope) === key);
    decisions.set(key, { kind, at: new Date().toISOString(), why: m && isLookalike(m) ? "lookalike" : m && m.alert.batch === "ALL" ? "all" : "product", alertId });
  }

  // ---------- one pack
  function timelineFor(ms) {
    const byReg = new Map();
    ms.forEach((m) => { const p = place(m.alert.source); if (!byReg.has(p.reg)) byReg.set(p.reg, { p, date: m.alert.date }); });
    if (byReg.size < 2) return "";
    const nodes = [...byReg.values()].sort((x, y) => String(x.date).localeCompare(String(y.date)));
    const days = (x, y) => Math.round((new Date(y) - new Date(x)) / 86400000);
    const arrow = lang === "ar" ? "←" : "→";
    return `<div class="timeline" aria-label="${esc(t("timeline_label"))}">${nodes.map((n, i) =>
      `${i ? `<span class="gap">${arrow} ${esc(t("days_later", days(nodes[i - 1].date, n.date)))} ${arrow}</span>` : ""}<span class="node"><b>${esc(n.p.reg)}</b><span>${esc(n.p.country)} · ${esc(fmtDate(n.date))}</span></span>`).join("")}</div>`;
  }
  // a pack named only by foreign regulators: say plainly that the SFDA has not (yet) published it
  function saudiLine(ms) {
    const live = ms.filter((m) => statusKey(m) !== "ruledout" && statusKey(m) !== "none");
    if (!live.length || live.some((m) => /SFDA/.test(m.alert.source))) return "";
    const regs = [...new Set(live.map((m) => place(m.alert.source).reg))];
    return `<p class="saudi-line">${ICON.info}<span>${esc(t("not_in_sfda", regs))}</span></p>`;
  }

  // the answer of the live search of the SFDA's own notices, shown when no stored alert names the batch
  function liveHtml() {
    const r = lastLive;
    if (!r || r.status === "skipped") return "";
    if (r.loading) return `<p class="note" aria-busy="true">${esc(t("live_searching"))}</p>`;
    if (r.error) return `<p class="note">${esc(t("live_error"))}</p>`;
    if (r.status === "error") return `<p class="note">${esc(t("live_error"))}</p>`;
    const hits = (r.hits || []).map((h) => `<div class="alert-ref"><p><b><bdi>${esc(h.title)}</bdi></b> · ${esc(fmtDate(h.date))}</p>
        <p class="muted"><bdi>${esc(h.quote)}</bdi></p>
        <p>${esc(t(h.in_registry ? "live_stored" : "live_new"))} <a href="${esc(h.url)}" target="_blank" rel="noopener noreferrer">${esc(t("live_open"))}</a></p></div>`).join("");
    const done = r.complete ? t("live_not_found", r.listed, r.searched) : t("live_incomplete", r.listed, r.searched, r.failed);
    return `${hits}${r.status === "found" && r.complete ? "" : `<p class="note">${esc(done)}</p>`}<p class="note muted">${esc(t("live_scope"))}</p>`;
  }

  function verdictHtml(data) {
    const p = data.pack;
    const ms = [...data.matches].sort((x, y) => rank(x) - rank(y));
    const live = ms.filter((m) => statusKey(m) !== "ruledout");
    const asOf = data.alerts_as_of ? t("as_of", fmtTime(data.alerts_as_of)) : "";
    if (!live.length && !ms.length && lastLive && lastLive.status === "found") {
      return `<article class="verdict v-check">
        <header class="verdict-head">${ICON.info}<div>
          <p class="verdict-kicker">${esc(t("live_kicker"))} · ${mono(p.batch)}</p>
          <h2 tabindex="-1">${esc(t("live_h_found"))}</h2>
          <p class="verdict-sub">${esc(t("live_sub_found", lastLive.hits.length))}</p>
        </div></header>
        <div class="verdict-body">${liveHtml()}</div>
      </article>`;
    }
    if (!live.length) {
      const ruled = ms.length ? [...decisions.values()].find((d) => d.kind === "different") : null;
      const key = !ms.length ? "none" : ruled && ruled.why === "lookalike" ? "none_lookalike" : "none_dismissed";
      return `<article class="verdict v-none">
        <header class="verdict-head">${ICON.none}<div>
          <p class="verdict-kicker">${esc(t("kicker_nomatch"))} · ${mono(p.batch)}</p>
          <h2 tabindex="-1">${esc(t(`h_${key}`, p.batch))}</h2>
          <p class="verdict-sub">${esc(t(`sub_${key}`, data.alerts_checked, registry ? registry.stats.sources.length : 10, iso(p.batch), !!p.gtin))} ${esc(asOf)}</p>
        </div></header>
        <div class="verdict-body">
          ${!ms.length ? liveHtml() : ""}
          ${!p.gtin && !ms.length ? `<p class="note">${esc(t("tip_gtin"))}</p>` : ""}
          ${ms.map((m) => `${alertRef(m)}${decisionNote(m, "pack")}`).join("")}
        </div>
      </article>`;
    }
    const top = live[0], sev = sevOf(top);
    const regs = [...new Set(live.map((m) => place(m.alert.source).reg))];
    const stop = statusKey(top) === "stop";
    return `<article class="verdict v-${sev}">
      <header class="verdict-head">${sevIcon(sev)}<div>
        <p class="verdict-kicker">${esc(t(`st_${statusKey(top)}`))} · ${mono(p.batch)}</p>
        <h2 tabindex="-1">${esc(headline(top))}</h2>
        <p class="verdict-sub">${esc(subline(top, regs))}</p>
      </div></header>
      <div class="verdict-body">
        ${isLookalike(top) ? compareStrip(p.batch, top.alert.batch) : ""}
        ${questionHtml(top, "pack", p.batch)}
        <div class="todo"><h3 class="label">${esc(t("what_to_do"))}</h3><p class="todo-main">${esc(todoText(top))}</p>${decisionNote(top, "pack")}</div>
        ${flagNote(top)}
        ${saudiLine(live)}
        <div class="actions">
          <button class="btn btn-soft btn-sm" type="button" data-act="copy">${esc(t(stop ? "act_copy_q" : "act_copy"))}</button>
          <button class="btn btn-soft btn-sm" type="button" data-act="share">${esc(t("act_share"))}</button>
          <button class="btn btn-soft btn-sm" type="button" data-act="print">${esc(t("act_print"))}</button>
        </div>
        <div class="note-box" hidden><label class="label" for="note-text">${esc(t("note_label"))}</label><textarea id="note-text" readonly rows="8"></textarea></div>
        ${timelineFor(live)}
        <h3 class="label found-title">${esc(t("found_in", ms.length))} <span class="muted">· ${esc(asOf)}</span></h3>
        ${ms.map((m) => alertRef(m)).join("")}
      </div>
    </article>`;
  }

  function packCard(p, params) {
    const part = (k, v) => (v ? `<span><span class="label">${esc(t(k))}</span> ${mono(v)}</span>` : "");
    if (params && params.code) return `<div class="pack-card"><span class="label">${esc(t("read_from_code"))}</span>${part("k_gtin", p.gtin)}${part("k_batch", p.batch)}${part("k_expiry", p.expiry)}${part("k_serial", p.serial)}</div>`;
    const extra = [part("k_gtin", params && params.gtin), part("k_expiry", params && params.expiry), params && params.product ? `<span><span class="label">${esc(t("f_product"))}</span> <bdi>${esc(params.product)}</bdi></span>` : ""].join("");
    return extra ? `<div class="pack-card"><span class="label">${esc(t("checked_with"))}</span>${part("k_batch", p.batch)}${extra}</div>` : "";
  }

  function renderPack(focusHeading = false) {
    const out = $("#pack-result");
    out.innerHTML = packCard(lastPack.pack, packParams) + verdictHtml(lastPack);
    $$(".trace", out).forEach((box) => loadTrace(box, false));
    renderIntro();
    if (focusHeading) $(".verdict h2", out)?.focus({ preventScroll: false });
  }

  $("#pack-result").addEventListener("click", (e) => {
    const d = e.target.closest("[data-decide]"), u = e.target.closest("[data-undo]"), act = e.target.closest("[data-act]");
    if (d) { decide(d.dataset.key, d.dataset.decide); renderPack(true); announce(t(d.dataset.decide === "same" ? "announce_confirmed" : "announce_dismissed")); }
    if (u) { decisions.delete(u.dataset.undo); renderPack(true); announce(t("announce_undone")); }
    if (act) packAction(act.dataset.act);
  });

  function quarantineNote() {
    const p = lastPack.pack;
    const top = [...lastPack.matches].filter((m) => statusKey(m) !== "ruledout").sort((x, y) => rank(x) - rank(y))[0];
    if (!top) return "";
    const a = top.alert, d = decided(top);
    return t("note_text", { status: t(`st_${statusKey(top)}`), product: a.product, batch: p.batch, expiry: p.expiry, gtin: p.gtin,
      source: where(a.source), type: typeName(a.type), title: a.title, date: fmtDate(a.date), url: a.url,
      says: a.required_action, todo: todoText(top), when: fmtTime(new Date().toISOString()),
      confirmed: d && d.kind === "same" ? fmtTime(d.at) : "" });
  }
  async function packAction(kind) {
    const text = quarantineNote();
    if (kind === "print") { window.print(); return; }
    if (kind === "share" && navigator.share) { try { await navigator.share({ title: "BatchWatch", text }); return; } catch { return; } }
    try { await navigator.clipboard.writeText(text); toast(t("copied")); }
    catch {  // no clipboard permission: show the note ready to copy by hand
      const box = $("#pack-result .note-box"), area = $("#note-text");
      if (box && area) { area.value = text; box.hidden = false; area.focus(); area.select(); }
      toast(t("copy_failed"));
    }
  }

  // No stored alert names the batch: open every notice on the SFDA site and search its text (about 15 s the first time)
  async function liveSearch(pack) {
    lastLive = { loading: true };
    try {
      const r = await api(`/api/lookup/live?${new URLSearchParams({ batch: pack.pack.batch })}`);
      if (lastPack !== pack) return;  // the visitor started another check meanwhile
      lastLive = r;
    } catch (e) {
      if (lastPack !== pack) return;
      lastLive = { error: e.message };
    }
    renderPack();
    const found = lastLive.status === "found";
    announce(found ? t("live_h_found") : lastLive.error ? t("live_error") : t("h_none"));
  }

  async function lookupPack(params) {
    packParams = params;
    [...decisions.keys()].filter((k) => k.startsWith("pack|")).forEach((k) => decisions.delete(k));
    const out = $("#pack-result");
    $("#intro").hidden = true;
    out.innerHTML = `<div class="skeleton" aria-busy="true"><i></i><i></i><i></i></div>`;
    try {
      lastLive = null;
      lastPack = await api(`/api/lookup?${new URLSearchParams(params)}`);
      $("#pack-batch").value = lastPack.pack.batch || "";
      if (!lastPack.matches.length) liveSearch(lastPack);
      // the extra fields apply to this check only: the next pack starts clean, and the verdict shows what was used
      ["#pack-gtin", "#pack-expiry", "#pack-product"].forEach((s) => ($(s).value = ""));
      renderPack();
      const top = [...lastPack.matches].sort((x, y) => rank(x) - rank(y))[0];
      announce(top ? `${t(`st_${statusKey(top)}`)}: ${headline(top)}` : t("h_none"));
      if (top && sevOf(top) === "stop" && navigator.vibrate) navigator.vibrate([120, 60, 120]);
      setSticky(true);
      out.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    } catch (e) { lastPack = null; showPackError(e.message); }
  }
  function showPackError(msg) { $("#pack-result").innerHTML = `<p class="notice notice-error" role="alert">${esc(msg)}</p>`; }

  $("#pack-form").addEventListener("submit", (e) => {
    e.preventDefault();
    stopCamera();
    const params = Object.fromEntries([...new FormData(e.target).entries()].map(([k, v]) => [k, String(v).trim()]).filter(([, v]) => v));
    if (!params.batch) { showPackError(t("err_no_batch")); $("#pack-batch").focus(); return; }
    lookupPack(params);
  });
  $("#more-btn").addEventListener("click", (e) => {
    const box = $("#more-fields"), open = box.hidden;
    box.hidden = !open;
    e.currentTarget.setAttribute("aria-expanded", String(open));
    if (open) $("#pack-product").focus();
  });
  function runDemo(params) {
    stopCamera();
    ["#pack-gtin", "#pack-expiry", "#pack-product"].forEach((s) => ($(s).value = ""));
    if (params.batch) $("#pack-batch").value = params.batch;
    lookupPack(params);
  }
  $("#pack-demo").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    runDemo(chip.dataset.code === "sample" ? { code: SAMPLE_PACK } : { batch: chip.dataset.batch });
  });

  function setSticky(on) {
    const showBar = on && mode === "pack" && !!lastPack && matchMedia("(max-width: 979px)").matches;
    $("#sticky-bar").hidden = !showBar;
    document.body.classList.toggle("has-sticky", showBar);
  }
  $("#next-scan").addEventListener("click", () => { window.scrollTo(0, 0); startCamera(); });
  $("#next-type").addEventListener("click", () => { window.scrollTo(0, 0); $("#pack-batch").select(); $("#pack-batch").focus(); });

  // ---------- barcode: GS1 DataMatrix decoded on the device
  const SCANNER_URL = "https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js";
  const SAMPLE_PACK = "]d2" + "0105012748615328" + "17290228" + "10487069" + "\u001d" + "21DEMO0001";
  let scanner = null;
  function loadScanner() {
    if (window.Html5Qrcode) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const s = Object.assign(document.createElement("script"), { src: SCANNER_URL, onload: resolve, onerror: () => reject(new Error(t("err_scanner"))) });
      document.head.appendChild(s);
    });
  }
  const newScanner = () => { const F = window.Html5QrcodeSupportedFormats; return new window.Html5Qrcode("scan-reader", { formatsToSupport: [F.DATA_MATRIX, F.QR_CODE], verbose: false }); };
  async function stopCamera() {
    if (scanner && scanner.isScanning) { try { await scanner.stop(); } catch { /* already stopped */ } }
    $("#scan-reader").hidden = true;
    $("#scan-btn .long").textContent = t("scan_btn");
    $("#scan-btn .short").textContent = t("scan_short");
  }
  async function startCamera() {
    if (mode !== "pack") setMode("pack");
    if (location.hash && location.hash !== "#check") location.hash = "#check";
    if (scanner && scanner.isScanning) { await stopCamera(); return; }
    try {
      await loadScanner();
      $("#scan-reader").hidden = false;
      scanner = scanner || newScanner();
      $("#scan-btn .long").textContent = t("stop_camera");
      $("#scan-btn .short").textContent = t("stop_camera");
      await scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: 220 }, async (text) => { await stopCamera(); lookupPack({ code: text }); }, () => {});
    } catch (e) { await stopCamera(); showPackError(e && e.message ? e.message : t("err_camera")); }
  }
  async function scanPhoto(file) {
    if (!file) return;
    try { await loadScanner(); scanner = scanner || newScanner(); lookupPack({ code: await scanner.scanFile(file, false) }); }
    catch { showPackError(t("err_photo")); }
    finally { $("#scan-file").value = ""; }
  }
  $("#scan-btn").addEventListener("click", startCamera);
  $("#scan-file").addEventListener("change", (e) => scanPhoto(e.target.files[0]));

  // ---------- stock list: one row per stock line, every alert that names it inside
  const lineKey = (m) => m.item.item_id || `${m.item.product_name}|${m.item.batch_number}`;
  function stockLines() {
    const lines = new Map();
    stock.matches.forEach((m) => { const k = lineKey(m); (lines.get(k) || lines.set(k, []).get(k)).push(m); });
    return [...lines.values()].map((ms) => ms.sort((x, y) => rank(x, "stock") - rank(y, "stock")));
  }
  const lineStatus = (ms) => statusKey(ms[0], "stock");
  const SEV_FOR = { stop: "s-stop", checkbatch: "s-check", confirm: "s-check", follow: "s-info", review: "s-info", none: "s-off", ruledout: "s-off" };
  function renderStock() {
    const data = stock, out = $("#stock-result");
    $("#stock-error").hidden = true;
    const lines = stockLines();
    const groups = {};
    lines.forEach((ms) => { const k = lineStatus(ms); groups[k] = (groups[k] || 0) + 1; });
    const act = lines.filter((ms) => ["stop", "confirm", "checkbatch", "follow"].includes(lineStatus(ms))).length;
    const clear = data.lines_checked - lines.length;
    const file = data.sample ? t(`sample_${data.sample}_name`) : data.file;
    const seg = (cls, n) => (n ? `<span class="${cls}" style="flex:${n}" title="${n}"></span>` : "");
    const keys = ["stop", "checkbatch", "confirm", "follow", "review", "none", "ruledout"].filter((k) => groups[k]);
    const cols = Object.entries(data.columns || {}).filter(([k]) => ["product_name", "batch_number", "expiry", "quantity", "location", "gtin", "manufacturer"].includes(k));
    out.innerHTML = `
      <div class="stock-head">
        <div><p class="label">${esc(file)} · ${esc(t("checked_at", fmtTime(data.checked_at)))}</p>
          <h2 tabindex="-1">${esc(act ? t("lines_need", act, data.lines_checked) : t("lines_clear", data.lines_checked))}</h2>
          <p>${esc(t("lines_sub", data.alerts_checked, clear, data.alerts_as_of ? fmtTime(data.alerts_as_of) : ""))}</p></div>
        <div class="actions"><button class="btn btn-soft btn-sm" type="button" data-act="csv">${esc(t("act_csv"))}</button>
          <button class="btn btn-soft btn-sm" type="button" data-act="print">${esc(t("act_print_list"))}</button></div>
      </div>
      ${cols.length ? `<details class="more cols-read"><summary>${esc(t(data.columns_by === "nemotron" ? "cols_by_ai" : "cols_by_names", cols.length))}</summary>
        <p>${cols.map(([k, h]) => `${esc(t(`col_${k}`))}: <bdi>${esc(h)}</bdi>`).join(" · ")}</p></details>` : ""}
      <div class="triage" aria-hidden="true">${keys.map((k) => seg(SEV_FOR[k], groups[k])).join("")}${seg("clear", clear)}</div>
      <div class="chip-row" role="group" aria-label="${esc(t("filter_label"))}">
        <button class="chip" type="button" data-filter="" aria-pressed="${!stockFilter}">${esc(t("all"))} <span class="n">${lines.length}</span></button>
        ${keys.map((k) => `<button class="chip" type="button" data-filter="${k}" aria-pressed="${stockFilter === k}"><span class="dot ${SEV_FOR[k]}"></span>${esc(t(`st_${k}`))} <span class="n">${groups[k]}</span></button>`).join("")}
        <span class="chip chip-legend"><span class="dot dot-clear"></span>${esc(t("no_alert"))} <span class="n">${clear}</span></span>
        <button class="chip" type="button" data-group aria-pressed="${groupByLocation}">${esc(t("group_location"))}</button>
      </div>
      <div class="table">
        <div class="thead label" aria-hidden="true"><span>${esc(t("th_status"))}</span><span>${esc(t("th_product"))}</span><span>${esc(t("th_batch"))}</span><span class="c-qty">${esc(t("th_qty"))}</span><span>${esc(t("th_location"))}</span><span>${esc(t("th_alert"))}</span><span></span></div>
        <div id="stock-rows"></div>
      </div>`;
    drawStockRows();
    renderIntro();
  }
  function drawStockRows() {
    let lines = stockLines().filter((ms) => !stockFilter || lineStatus(ms) === stockFilter)
      .sort((x, y) => rank(x[0], "stock") - rank(y[0], "stock") || String(x[0].item.product_name).localeCompare(y[0].item.product_name));
    let html;
    if (groupByLocation) {
      const byLoc = new Map();
      lines.forEach((ms) => { const l = ms[0].item.location || t("no_location"); (byLoc.get(l) || byLoc.set(l, []).get(l)).push(ms); });
      html = [...byLoc.entries()].sort((a, b) => a[0].localeCompare(b[0], locale())).map(([loc, ls]) =>
        `<div class="loc-head"><bdi>${esc(loc)}</bdi> <span class="n">${ls.length}</span></div>${ls.map(stockRow).join("")}`).join("");
    } else html = lines.map(stockRow).join("");
    $("#stock-rows").innerHTML = html || `<div class="table-foot">${esc(t("no_lines"))}</div>`;
  }
  function stockRow(ms) {
    const m = ms[0], i = m.item, a = m.alert, p = place(a.source);
    const strength = strengthShown(i.product_name, i.strength) ? ` <span class="muted">${esc(i.strength)}</span>` : "";
    const meta = [`${t("th_batch")} ${i.batch_number}`, i.quantity && `${t("th_qty")} ${i.quantity}`, i.location, ms.map((x) => place(x.alert.source).reg).join(" + ")].filter(Boolean).join(" · ");
    const regs = [...new Set(ms.map((x) => place(x.alert.source).reg))].join(" + ");
    return `<details class="row" data-sev="${sevOf(m, "stock")}" data-line="${esc(lineKey(m))}">
      <summary class="row-main">
        <span class="c-status">${badge(m, "stock")}</span>
        <span class="c-product"><strong><bdi>${esc(i.product_name).replace(/\//g, "/<wbr>")}</bdi></strong>${strength}</span>
        <span class="c-batch"><span class="sr-only">${esc(t("th_batch"))} </span>${mono(i.batch_number)}</span>
        <span class="c-qty"><span class="sr-only">${esc(t("th_qty"))} </span>${esc(i.quantity || "–")}</span>
        <span class="c-loc"><span class="sr-only">${esc(t("th_location"))} </span><bdi>${esc(i.location || "–")}</bdi></span>
        <span class="c-alert">${esc(regs)}<span class="muted">${ms.length > 1 ? esc(t("n_alerts", ms.length)) : esc(fmtDate(a.date))}</span></span>
        <span class="c-meta"><bdi>${esc(meta)}</bdi></span>
        <span class="c-chev">${ICON.chev}</span>
        <span class="c-print">${esc(todoText(m, "stock"))}${a.required_action ? ` · ${esc(t("f_regulator_says"))}: ${esc(a.required_action)}` : ""} · ${esc(t("done_by"))} ________</span>
      </summary>
      <div class="row-detail">
        ${isLookalike(m) ? compareStrip(i.batch_number, a.batch) : ""}
        ${questionHtml(m, "stock", i.batch_number)}
        <div class="todo"><h3 class="label">${esc(t("what_to_do"))}</h3><p class="todo-main">${esc(todoText(m, "stock"))}</p>${decisionNote(m, "stock")}</div>
        ${ms.map((x) => alertRef(x, { scope: "stock" })).join("")}
      </div>
    </details>`;
  }
  $("#stock-result").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip[data-filter]");
    if (chip) { stockFilter = chip.dataset.filter; $$(".chip[data-filter]", $("#stock-result")).forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.filter === stockFilter))); drawStockRows(); return; }
    if (e.target.closest("[data-group]")) { groupByLocation = !groupByLocation; e.target.closest("[data-group]").setAttribute("aria-pressed", String(groupByLocation)); drawStockRows(); return; }
    const d = e.target.closest("[data-decide]"), u = e.target.closest("[data-undo]");
    if (d || u) {  // the row stays open, focus lands on its new answer
      const key = (d || u).dataset[d ? "key" : "undo"], line = key.split("|")[1];
      if (d) decide(key, d.dataset.decide); else decisions.delete(key);
      renderStock();
      const again = $$("#stock-rows details.row").find((r) => r.dataset.line === line);
      if (again) { again.open = true; const todo = $(".todo-main", again); todo.tabIndex = -1; todo.focus(); }
      announce(t(d ? (d.dataset.decide === "same" ? "announce_confirmed" : "announce_dismissed") : "announce_undone"));
      return;
    }
    const act = e.target.closest("[data-act]");
    if (act && act.dataset.act === "csv") exportCsv();
    if (act && act.dataset.act === "print") { $$("#stock-rows details.row").forEach((r) => (r.open = false)); window.print(); }
  });
  function exportCsv() {
    const head = ["status", "what_to_do", "item_id", "product_name", "batch_number", "expiry", "quantity", "location", "regulator", "alert_date",
      "alert_type", "alert_title", "alert_batch", "regulator_says", "why", "alert_url", "read_by", "checks", "checked_at", "alerts_as_of", "action_taken", "signed_by"];
    const rows = stock.matches.map((m) => {
      const d = decided(m, "stock");
      return [t(`st_${statusKey(m, "stock")}`), todoText(m, "stock"), m.item.item_id, m.item.product_name, m.item.batch_number, m.item.expiry,
        m.item.quantity, m.item.location, m.alert.source, m.alert.date, m.alert.type, m.alert.title, m.alert.batch, m.alert.required_action,
        m.reasons.join("; "), m.alert.url, m.alert.model, `${m.alert.checks_passed}/${m.alert.checks_total}`, stock.checked_at, stock.alerts_as_of,
        d ? t(d.kind === "same" ? "csv_same" : "csv_different", fmtTime(d.at)) : "", ""];
    });
    const csv = [head, ...rows].map((r) => r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv" }));
    const link = Object.assign(document.createElement("a"), { href: url, download: `batchwatch_report_${new Date().toISOString().slice(0, 10)}.csv` });
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function runStock(request, saved) {
    $$("#panel-stock .chip, #file-input").forEach((el) => (el.disabled = true));
    $("#stock-result").innerHTML = `<div class="skeleton" aria-busy="true"><i></i><i></i><i></i></div>`;
    $("#intro").hidden = true;
    try {
      const data = await request();
      const before = saved && saved.keys ? new Set(saved.keys) : null;
      [...decisions.keys()].filter((k) => k.startsWith("stock|")).forEach((k) => decisions.delete(k));
      stock = Object.assign(data, saved && saved.sample ? { sample: saved.sample } : {});
      stockFilter = "";
      renderStock();
      if ($("#keep-stock").checked && saved && !saved.transient) {
        const keys = data.matches.filter((m) => ["stop", "confirm", "checkbatch", "follow"].includes(statusKey(m, "stock"))).map((m) => `${lineKey(m)}|${m.alert.alert_id}`);
        const fresh = before ? keys.filter((k) => !before.has(k)) : [];
        store.set("bw.stock", { ...saved, keys, checkedAt: data.checked_at, asOf: data.alerts_as_of, lines: data.lines_checked });
        if (before && fresh.length) toast(t("saved_new", fresh.length), { label: t("show"), run: () => { location.hash = "#check"; setMode("stock"); } });
      }
      renderSaved();
      announce(t("announce_stock", stockLines().length, data.lines_checked));
      $("#stock-result").scrollIntoView({ block: "start" });
      $("#stock-result h2")?.focus({ preventScroll: true });
    } catch (e) {
      $("#stock-result").innerHTML = "";
      const box = $("#stock-error"); box.textContent = e.message; box.hidden = false;
      renderIntro();
    } finally { $$("#panel-stock .chip, #file-input").forEach((el) => (el.disabled = false)); $("#file-input").value = ""; }
  }
  const fileToB64 = (file) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1]); r.onerror = rej; r.readAsDataURL(file); });
  const b64ToFile = (b64, name) => { const bin = atob(b64); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return new File([u], name); };
  async function runFile(file) {
    if (!file) return;
    let saved = null;
    if ($("#keep-stock").checked) {
      const b64 = file.size < 1_500_000 ? await fileToB64(file) : null;
      saved = b64 ? { name: file.name, b64 } : null;
      if (!saved) toast(t("too_big_to_keep"));
    }
    const form = new FormData(); form.append("file", file);
    runStock(() => api("/api/check", { method: "POST", body: form }), saved);
  }
  function runSample(name) {
    const saved = $("#keep-stock").checked ? { sample: name, name } : { sample: name, transient: true };
    runStock(() => api(`/api/check/sample?name=${name}`), saved);
  }
  function recheckSaved() {
    const s = store.get("bw.stock");
    if (!s) return;
    if (s.sample) return runStock(() => api(`/api/check/sample?name=${s.sample}`), s);
    const form = new FormData(); form.append("file", b64ToFile(s.b64, s.name));
    runStock(() => api("/api/check", { method: "POST", body: form }), s);
  }
  function renderSaved() {
    const s = store.get("bw.stock"), box = $("#saved-banner");
    $("#keep-stock").checked = !!s || $("#keep-stock").checked;
    if (!s) { box.hidden = true; return; }
    const newer = statusData && statusData.alerts_as_of && s.asOf && statusData.alerts_as_of > s.asOf;
    box.hidden = false;
    box.classList.toggle("has-new", !!newer);
    box.innerHTML = `<span>${esc(t(newer ? "saved_newer" : "saved_line", s.sample ? t(`sample_${s.sample}_name`) : s.name, s.lines || "", s.checkedAt ? fmtTime(s.checkedAt) : ""))}</span>
      <button class="btn btn-soft btn-sm" type="button" data-saved="check">${esc(t("check_again"))}</button>
      <button class="link-btn" type="button" data-saved="forget">${esc(t("forget"))}</button>`;
  }
  $("#saved-banner").addEventListener("click", (e) => {
    const b = e.target.closest("[data-saved]");
    if (!b) return;
    if (b.dataset.saved === "check") recheckSaved();
    else { store.del("bw.stock"); $("#keep-stock").checked = false; renderSaved(); }
  });
  $("#keep-stock").addEventListener("change", (e) => { if (!e.target.checked) { store.del("bw.stock"); renderSaved(); } });
  const dz = $("#dropzone");
  $("#file-input").addEventListener("change", (e) => runFile(e.target.files[0]));
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("is-over"); }));
  dz.addEventListener("drop", (e) => runFile(e.dataTransfer.files[0]));
  $("#panel-stock").addEventListener("click", (e) => { const c = e.target.closest("[data-sample]"); if (c) runSample(c.dataset.sample); });

  // ---------- alerts
  let alertsReady = false, fCountry = "", fType = "", hits = null;
  const TYPE_FILTERS = ["", "recall", "falsified", "notice", "withdrawn"];
  const typeGroup = (a) => (a.status === "withdrawn" ? "withdrawn" : a.type === "recall" ? "recall" : ["falsified", "substandard", "watchlist"].includes(a.type) ? "falsified" : "notice");
  const isNew = (a) => !a.backfill && lastVisit && a.detected_at > lastVisit;
  function alertHeadline(a) {
    const names = [...new Set(a.products.map((p) => p.name).filter(Boolean))];
    return names.length ? names.slice(0, 2).join(" · ") + (names.length > 2 ? ` ${t("more_n", names.length - 2)}` : "") : a.title;
  }
  function renderAlerts() {
    if (!registry || alertsReady) return;
    alertsReady = true;
    const counts = {};
    registry.alerts.forEach((a) => { const c = place(a.source).code; counts[c] = (counts[c] || 0) + 1; });
    const order = ["sa", ...Object.keys(counts).filter((c) => c !== "sa" && c !== "global").sort((x, y) => t(`c_${x}`).localeCompare(t(`c_${y}`), locale())), "global"].filter((c) => counts[c]);
    const groups = {};
    registry.alerts.forEach((a) => { const g = typeGroup(a); groups[g] = (groups[g] || 0) + 1; });
    const opt = (key, label, n, on) => `<option value="${esc(key)}"${on ? " selected" : ""}>${esc(label)} (${n})</option>`;
    $("#filters").innerHTML = `
      <label class="select"><span class="sr-only">${esc(t("country"))}</span><select data-filter="country">
        ${opt("", t("all_countries"), registry.alerts.length, !fCountry)}${order.map((c) => opt(c, t(`c_${c}`), counts[c], fCountry === c)).join("")}</select></label>
      <label class="select"><span class="sr-only">${esc(t("type"))}</span><select data-filter="type">
        ${TYPE_FILTERS.filter((k) => !k || groups[k]).map((k) => opt(k, t(`tf_${k || "all"}`), k ? groups[k] : registry.alerts.length, fType === k)).join("")}</select></label>`;
    const fresh = registry.alerts.filter(isNew);
    $("#new-since").innerHTML = fresh.length ? `<div class="new-since"><h2>${esc(t("new_since", fresh.length))}</h2><ul>${fresh.slice(0, 8).map((a) =>
      `<li><a href="#alerts" data-open="${esc(a.alert_id)}"><bdi>${esc(alertHeadline(a))}</bdi></a> · ${esc(where(a.source))} · ${esc(ago(a.detected_at))}</li>`).join("")}</ul></div>` : "";
    drawAlerts();
  }
  $("#new-since").addEventListener("click", (e) => {
    const a = e.target.closest("[data-open]");
    if (!a) return;
    e.preventDefault();
    const row = $(`#alert-list details[data-alert="${CSS.escape(a.dataset.open)}"]`);
    if (row) { row.open = true; row.scrollIntoView({ block: "start" }); }
  });
  $("#filters").addEventListener("change", (e) => {
    const sel = e.target.closest("select[data-filter]");
    if (!sel) return;
    if (sel.dataset.filter === "country") fCountry = sel.value; else fType = sel.value;
    drawAlerts();
  });
  function drawAlerts() {
    const list = registry.alerts.filter((a) => (!fCountry || place(a.source).code === fCountry) && (!fType || typeGroup(a) === fType) && (!hits || hits.has(a.alert_id)));
    const q = $("#alert-search").value.trim();
    let count = t("alerts_count", list.length);
    if (q.length >= 2 && hits) count = list.length ? t("alerts_mention", list.length, q) : hits.size ? t("alerts_filtered", q, hits.size) : t("alerts_none", q, registry.alerts.length);
    $("#alert-count").textContent = count;
    $("#alert-list").innerHTML = list.map(alertRow).join("");
  }
  function alertRow(a) {
    const hit = hits && hits.get(a.alert_id);
    const tb = a.trace;
    const extra = [
      isNew(a) ? `<span class="new-dot">${esc(t("new"))}</span>` : "",
      hit && hit.batch ? `<span class="badge ${hit.kind === "exact batch" ? "s-stop" : "s-check"}">${esc(t(hit.kind === "exact batch" ? "exact_batch" : "lookalike_batch"))}</span>${lot(hit.batch, true)}` : "",
      tb && tb.verified ? `<span class="pill">${ICON.globe}${esc(t("also_in", tb.places.map((c) => countryName(c, tb.earliest && tb.earliest.authority))))}</span>` : "",
      a.checks_passed < a.checks.length ? `<span class="pill pill-warn">${ICON.flag}${esc(t("needs_review"))}</span>` : "",
      !a.backfill ? `<span class="pill">${esc(t("picked_up", ago(a.detected_at)))}</span>` : "",
    ].join("");
    return `<details class="row alert-row" data-alert="${esc(a.alert_id)}">
      <summary class="alert-main">
        <span class="a-type">${typeTag(a.type, a.status)}</span>
        <span class="a-body"><span class="a-title"><bdi>${esc(alertHeadline(a))}</bdi></span>
          <span class="a-meta">${esc(where(a.source))} · ${esc(fmtDate(a.date))} · ${esc(a.batch_count ? t("n_batches", a.batch_count) : t("no_batches"))}</span>
          ${extra ? `<span class="a-extra">${extra}</span>` : ""}</span>
        <span class="c-chev">${ICON.chev}</span>
      </summary>
      <div class="row-detail alert-detail">${alertDetail(a)}</div>
    </details>`;
  }
  function codesText(a, p) {
    const sfda = /SFDA/.test(a.source);
    return p.codes.map((c) => (/غير مسجل|unregistered/i.test(c) ? t("not_registered") : `${sfda ? t("reg_no") : t("code")} ${c}`)).join(" · ");
  }
  function alertDetail(a) {
    const products = a.products.map((p) => `<div class="product">
        <div class="product-name"><bdi>${esc(p.name)}</bdi></div>
        ${p.manufacturer || p.codes.length ? `<div class="product-meta"><bdi>${esc([p.manufacturer, codesText(a, p)].filter(Boolean).join(" · "))}</bdi></div>` : ""}
        <div>${p.batches.length ? p.batches.map((b) => (b.number === "ALL"
          ? `<span class="lot">${esc(t("all_batches"))}${b.expiry ? ` · ${esc(b.expiry)}` : ""}</span>`
          : `<span class="lot${b.added_by ? " added" : ""}${(a.flags || []).some((f) => f.check === "ungrounded" && f.batches.includes(b.number)) ? " ungrounded" : ""}" title="${esc(b.added_by ? t("added_by_reader") : b.expiry ? `${t("k_expiry")} ${b.expiry}` : "")}">${esc(b.number)}</span>`)).join("")
          : `<span class="muted">${esc(t("no_batches"))}</span>`}</div>
      </div>`).join("");
    const how = a.escalated_because ? t("read_escalated", a.model, a.escalated_because) : t("read_ok", a.model);
    const ar = sfdaArabic(a.url);
    return `<p dir="auto">${esc(a.reason)}</p>
      ${a.action ? `<dl class="facts"><dt>${esc(t("f_regulator_says"))}</dt><dd dir="auto">${esc(a.action)}</dd></dl>` : ""}
      ${a.checks_passed < a.checks.length ? `<p class="flag-note">${ICON.flag}<span>${esc(t("review_note", a.checks.filter((c) => !c.passed).map((c) => t(`chk_${a.checks.indexOf(c)}`))))}</span></p>` : ""}
      <div class="products">${products || `<p class="muted">${esc(t("no_products"))}</p>`}</div>
      <div class="provenance"><span class="pill">${ICON.chip}${esc(how)}</span>
        <span class="pill${a.checks_passed < a.checks.length ? " pill-warn" : ""}">${a.checks_passed === a.checks.length ? ICON.ok : ICON.flag}${esc(t("checks_n", a.checks_passed, a.checks.length))}</span>
        ${extLink(a.url, t("original_notice"), "ext")}${ar ? ` · ${extLink(ar, t("notice_ar"), "ext")}` : ""}</div>
      <details class="more" data-reading="${esc(a.alert_id)}"><summary>${esc(t("how_read"))}</summary><div class="reading-out"><p class="loading">${esc(t("loading"))}</p></div></details>
      ${traceBlock(a.alert_id)}`;
  }
  let searchTimer;
  $("#alert-search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    searchTimer = setTimeout(async () => {
      if (q.length < 2) { hits = null; drawAlerts(); return; }
      try {
        const { results } = await api(`/api/search?q=${encodeURIComponent(q)}`);
        hits = new Map();
        results.forEach((r) => { if (!hits.has(r.alert_id)) hits.set(r.alert_id, r); });
        const lower = q.toLowerCase();
        registry.alerts.forEach((a) => { if (!hits.has(a.alert_id) && String(a.title).toLowerCase().includes(lower)) hits.set(a.alert_id, { title: true }); });
        drawAlerts();
      } catch (err) { $("#alert-count").textContent = err.message; }
    }, 200);
  });

  // reading: the notice text with every extracted batch number highlighted, plus the six checks
  const readings = {};
  async function loadReading(box) {
    const id = box.dataset.reading, out = $(".reading-out", box);
    if (box.dataset.loaded) return;
    box.dataset.loaded = "1";
    try {
      const d = readings[id] || (readings[id] = await api(`/api/alert/${encodeURIComponent(id)}`));
      const a = d.alert;
      const nums = [...new Set(a.products.flatMap((p) => p.batches.map((b) => b.number)).filter((b) => b && b !== "ALL"))].sort((x, y) => y.length - x.length);
      let html = esc(d.text || "");
      if (nums.length) {  // the same tolerance as the grounding check: up to two separators inside a number
        const body = (n) => [...String(n).replace(/[\s\-/.]/g, "")].map((c) => c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s\\-/.]{0,2}");
        const pattern = new RegExp(`(?<![A-Za-z0-9])(${nums.map(body).join("|")})(?![A-Za-z0-9])`, "gi");
        html = html.replace(pattern, "<mark>$1</mark>");
      }
      const checks = a.checks.map((c, n) => `<li class="${c.passed ? "ok" : "warn"}">${c.passed ? ICON.ok : ICON.flag}<span>${esc(t(`chk_${n}`))}<small dir="auto">${esc(c.detail)}</small></span></li>`).join("");
      out.innerHTML = `<p class="note">${esc(a.escalated_because ? t("read_escalated", a.model, a.escalated_because) : t("read_ok", a.model))}</p>
        <ul class="checks">${checks}</ul>
        ${d.text ? `<p class="note">${esc(t("reading_note", nums.length))}</p><div class="reading" dir="auto">${html}</div>` : ""}`;
    } catch (e) { out.innerHTML = `<p class="notice notice-error">${esc(e.message)}</p>`; }
  }

  document.addEventListener("toggle", (e) => {
    const el = e.target;
    if (!(el instanceof HTMLDetailsElement) || !el.open) return;
    if (el.dataset.reading) loadReading(el);
    $$(":scope > .row-detail .trace, :scope > .trace", el).forEach((box) => { if (!box.dataset.loaded) { box.dataset.loaded = "1"; loadTrace(box, false); } });
  }, true);

  // ---------- cross-border trace (Nemotron 3 Super + Tavily), streamed live
  function traceBlock(alertId) {
    const left = limits ? Math.max(0, limits.daily_limit - limits.used_today) : null;
    return `<div class="trace" data-alert="${esc(alertId)}">
      <div class="trace-bar">
        <button class="btn btn-quiet btn-sm trace-btn" type="button">${ICON.globe}${esc(t("trace_btn"))}</button>
        <span class="trace-hint">${esc(t("trace_hint"))}${left != null ? ` · ${esc(t("trace_left", left))}` : ""}</span>
      </div>
      <div class="trace-out"></div>
    </div>`;
  }
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
  function stepLine(ev) {
    if (ev.event === "search") return t("ev_search", ev.query);
    if (ev.event === "results") return t("ev_results", ev.results, (ev.urls || []).map(host).slice(0, 3));
    if (ev.event === "read") return t("ev_read", (ev.urls || []).map(host));
    if (ev.event === "read_done") return t("ev_read_done", ev.ok);
    if (ev.event === "writing") return t("ev_writing");
    return "";
  }
  function renderTrace(out, tr) {
    const finds = tr.findings || [];
    const good = finds.filter((f) => f.verified && f.elsewhere);  // a regulator in another country, quote and batches on the page
    const dated = good.filter((f) => f.days_from_original != null).sort((x, y) => x.days_from_original - y.days_from_original);
    const who = (f) => [f.authority, f.place && !/^international/i.test(f.place) ? countryName(f.place) : ""].filter(Boolean);
    const head = dated.length ? t("tr_head", who(dated[0]), dated[0].days_from_original) : good.length ? t("tr_head_nodate", who(good[0])) : t("tr_none_reg");
    const steps = (tr.steps || []).map((s) => s.tool === "search" ? `<li>${esc(t("ev_search", s.query))} · ${esc(t("ev_results_n", s.results))}</li>`
      : `<li>${esc(t("ev_read", (s.urls || []).map(host)))}</li>`).join("");
    const cards = finds.sort((x, y) => (y.verified && y.elsewhere) - (x.verified && x.elsewhere)).map((f) => `<div class="finding${f.verified ? " is-verified" : ""}">
        <div class="finding-head">
          <span class="badge ${f.verified ? "s-ok" : "s-check"}">${f.verified ? ICON.ok : ICON.warn}${esc(t(f.verified ? "verified" : "not_verified"))}</span>
          <span class="tag">${esc(t(f.kind === "regulator" ? (f.elsewhere ? "kind_reg" : "kind_same") : "kind_other"))}</span>
          <strong>${esc(countryName(f.place || f.country, f.authority))}</strong><bdi>${esc(f.authority || "")}</bdi>
          ${f.date_iso || f.date ? `<span class="muted">${esc(fmtDate(f.date_iso || f.date))}${f.days_from_original != null ? ` · ${esc(t("rel_days", f.days_from_original))}` : ""}</span>` : ""}
        </div>
        ${(f.batches_verified || []).length ? `<div>${f.batches_verified.map((b) => lot(b, true)).join("")}</div>` : ""}
        ${f.evidence_quote ? `<blockquote dir="auto">${esc(f.evidence_quote)}</blockquote>` : ""}
        <a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(host(f.url))}${ICON.ext}</a>
      </div>`).join("");
    const calls = tr.tavily_calls || { search: 0, extract: 0 };
    out.innerHTML = `<div class="trace-result">
      <h4 class="label">${esc(t("trace_title"))}</h4>
      <p class="trace-summary"><strong>${esc(head)}</strong></p>
      ${tr.summary ? `<details class="more"><summary>${esc(t("agent_summary"))}</summary><p class="trace-summary" dir="auto">${esc(tr.summary)}</p></details>` : ""}
      ${cards}
      <details class="more"><summary>${esc(t("agent_log", modelName(tr.model), calls.search, calls.extract, money(tr.llm_cost_usd)))}</summary><ol>${steps}</ol></details>
      <div class="trace-bar trace-again"><button class="btn btn-quiet btn-sm trace-btn" type="button" data-fresh>${ICON.globe}${esc(t("trace_again"))}</button>
        <span class="trace-hint">${[tr.traced_at ? t("traced_when", ago(tr.traced_at)) : "", t("trace_hint"), limits ? t("trace_left", Math.max(0, limits.daily_limit - limits.used_today)) : ""].filter(Boolean).map(esc).join(" · ")}</span></div>
    </div>`;
  }
  const modelName = (id) => (/super/i.test(id) ? "Nemotron 3 Super" : /ultra/i.test(id) ? "Nemotron 3 Ultra" : /nano/i.test(id) ? "Nemotron 3 Nano" : id);
  // fresh: run the agent again over a saved trace (live, capped, not saved); on failure the saved trace stays on screen
  async function loadTrace(box, run, fresh = false) {
    const out = $(".trace-out", box), bar = $(".trace-bar", box), btn = $(".trace-btn", box);
    if (!run) {
      try { const { trace } = await api(`/api/trace/${encodeURIComponent(box.dataset.alert)}`); if (trace) { renderTrace(out, trace); bar.hidden = true; } } catch { /* button stays */ }
      return;
    }
    btn.disabled = true;
    const before = fresh ? out.innerHTML : "";
    out.innerHTML = `<div class="trace-live"><p class="loading">${esc(t("trace_running"))}</p><ol class="steps-live"></ol></div>`;
    const list = $(".steps-live", out);
    try {
      const res = await fetch(`/api/trace/${encodeURIComponent(box.dataset.alert)}${fresh ? "?fresh=1" : ""}`, { method: "POST" });
      if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.detail || t("err_request", res.status)); }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, i); buf = buf.slice(i + 2);
          const ev = /^event: (.*)$/m.exec(frame), data = /^data: (.*)$/m.exec(frame);
          if (!ev || !data) continue;
          const payload = JSON.parse(data[1]);
          if (ev[1] === "step") {
            $$("li.now", list).forEach((li) => li.classList.remove("now"));
            list.insertAdjacentHTML("beforeend", `<li class="now">${esc(stepLine(payload))}</li>`);
          } else if (ev[1] === "done") { renderTrace(out, payload.trace); bar.hidden = true; refreshLimits(); }
          else if (ev[1] === "error") throw new Error(payload.detail);
        }
      }
    } catch (e) { out.innerHTML = `<p class="notice notice-error">${esc(e.message)}</p>${before}`; }
    finally { btn.disabled = false; }
  }
  document.addEventListener("click", (e) => { const btn = e.target.closest(".trace-btn"); if (btn) loadTrace(btn.closest(".trace"), true, "fresh" in btn.dataset); });
  async function refreshLimits() { try { limits = await api("/api/limits"); } catch { limits = null; } }

  // ---------- how it works
  let howReady = false;
  function renderHow() {
    if (!registry || howReady) return;
    howReady = true;
    const s = registry.stats, ev = s.evaluation, ins = insightsData, st = statusData;
    const models = s.models || [];
    const ours = models.find((m) => m.run === "nano+super"), ultra = models.find((m) => m.run === "ultra"), nano = models.find((m) => m.run === "nano");
    const base = models.find((m) => m.run === "baseline_unguarded");
    const escalated = ev ? ev.escalated_to_fallback : null;
    const tiles = ev ? [[`${ev.alerts_fully_correct}<small>/${ev.alerts}</small>`, t("s_alerts")], [`${ev.batches_correct}<small>/${ev.batches_gold}</small>`, t("s_batches")],
      [`${ev.batches_extra}`, t("s_invented")], [money(ev.cost_usd), t("s_cost", ev.alerts)]] : [];
    const run = st && st.last_run;
    const flow = [
      ["fl_watch", t("fl_watch_d", s.sources.length), run ? t("fl_watch_n", ago(run.finished || run.started)) : t("fl_watch_n0")],
      ["fl_read", t("fl_read_d"), t("fl_read_n", s.alerts)],
      ["fl_check", t("fl_check_d"), ev ? t("fl_check_n", ev.alerts - (escalated || 0), ev.alerts) : ""],
      ["fl_reread", t("fl_reread_d"), escalated != null ? t("fl_reread_n", escalated, ev.alerts) : ""],
      ["fl_match", t("fl_match_d"), t("fl_match_n")],
      ["fl_trace", t("fl_trace_d"), ins ? t("fl_trace_n", ins.traced, ins.with_verified) : ""],
    ];
    const modelRows = [ours, ultra, nano].filter(Boolean).map((m) => `<tr class="${m.run === "nano+super" ? "ours" : ""}"><td>${esc(t(`run_${m.run.replace("+", "_")}`))}</td>
      <td class="num">${m.alerts_fully_correct}/${m.alerts}</td><td class="num">${m.batches_correct}/${m.batches_gold}</td><td class="num">${m.batches_extra}</td>
      <td class="num">${money(m.cost_usd)}</td><td class="num">${m.median_latency_s != null ? `${m.median_latency_s} s` : esc(t("not_measured"))}</td></tr>`).join("");
    const gaps = ins ? ins.longest.slice(0, 5).map((g) => `<li><b>${esc(t("days_short", Math.abs(g.days)))}</b><span>${esc(t(g.days < 0 ? "gap_before" : "gap_after", where(g.from), g.to, countryName(g.country, g.to)))} ${g.batches.map((b) => lot(b)).join("")}</span></li>`).join("") : "";
    const srcRows = st ? st.sources.map((x) => `<tr><td>${esc(where(x.name))}</td><td>${x.last_checked ? esc(ago(x.last_checked)) : "–"}</td><td>${x.last_new ? esc(ago(x.last_new)) : "–"}</td><td>${x.last_error ? esc(t("live_error")) : esc(t("ok"))}</td></tr>`).join("") : "";
    $("#how-body").innerHTML = `
      <div class="stats">${tiles.map(([n, l]) => `<div class="stat"><div class="stat-n">${n}</div><div class="stat-l">${esc(l)}</div></div>`).join("")}</div>
      ${ev ? `<p class="note">${esc(t("s_note", s.evaluation_run, ev.alerts, s.sources.length))}</p>` : ""}

      <section class="how-section"><h2>${esc(t("flow_title"))}</h2><p>${esc(t("flow_lede"))}</p>
        <ol class="flow">${flow.map(([k, d, n]) => `<li><b>${esc(t(k))}</b><span>${esc(d)}</span>${n ? `<em>${esc(n)}</em>` : ""}</li>`).join("")}</ol>
        <details class="more"><summary>${esc(t("method_notes"))}</summary><p class="note">${esc(t("flow_counts_note", s.alerts, ev ? ev.alerts : 90))}</p></details></section>

      ${modelRows ? `<section class="how-section"><h2>${esc(t("models_title"))}</h2>
        <p>${esc(ultra && ours ? t("models_lede", Math.round(ultra.cost_usd / ours.cost_usd), nano ? nano.batches_missed : null, nano ? nano.alerts_fully_correct : null) : "")}</p>
        <div class="tbl-wrap"><table class="data"><thead><tr><th>${esc(t("m_setup"))}</th><th class="num">${esc(t("m_alerts"))}</th><th class="num">${esc(t("m_batches"))}</th>
          <th class="num">${esc(t("m_invented"))}</th><th class="num">${esc(t("m_cost"))}</th><th class="num">${esc(t("m_latency"))}</th></tr></thead><tbody>${modelRows}</tbody></table></div>
        <details class="more"><summary>${esc(t("method_notes"))}</summary><p class="note">${esc(t("models_note"))}</p>
        ${base ? `<p class="note">${esc(t("baseline", base.alerts_fully_correct, base.alerts, base.batches_correct, base.batches_gold))}</p>` : ""}</details></section>` : ""}

      ${ins ? `<section class="how-section"><h2>${esc(t("xb_title"))}</h2>
        <p>${esc(t("xb_lede", ins.with_verified, ins.traced, ins.gaps, ins.within_day, ins.month_or_more))}</p>
        <ul class="gaps">${gaps}</ul>
        <div class="source-list">${ins.places.map(([c, n]) => `<span class="source-item">${esc(countryName(c, "WHO"))} <span>${n}</span></span>`).join("")}</div></section>` : ""}

      <section class="how-section"><h2>${esc(t("watch_title"))}</h2><p>${esc(t("watch_lede"))}</p>
        ${st && st.detection && st.detection.median_hours != null ? `<p class="note">${esc(t("live_detect", st.detection.median_hours, st.detection.alerts))}</p>` : ""}
        ${srcRows ? `<div class="tbl-wrap"><table class="data"><thead><tr><th>${esc(t("w_source"))}</th><th>${esc(t("w_checked"))}</th><th>${esc(t("w_new"))}</th><th>${esc(t("w_state"))}</th></tr></thead><tbody>${srcRows}</tbody></table></div>` : ""}
        <p class="note">${t("watch_channels_html")}</p></section>

      <section class="how-section"><div class="cards3">
        <div class="card"><h3>${esc(t("never_title"))}</h3><ul><li>${esc(t("never_1"))}</li><li>${esc(t("never_2"))}</li><li>${esc(t("never_3"))}</li><li>${esc(t("never_4"))}</li></ul></div>
        <div class="card"><h3>${esc(t("ai_title"))}</h3><ul><li>${esc(t("ai_1"))}</li><li>${esc(t("ai_2"))}</li><li>${esc(t("ai_3"))}</li><li>${esc(t("ai_4"))}</li></ul></div>
        <div class="card"><h3>${esc(t("sources_title"))}</h3><div class="source-list">${[...s.sources].sort((x, y) => place(x.name).country.localeCompare(place(y.name).country, locale()))
          .map((x) => `<span class="source-item">${esc(place(x.name).country || x.name)} <span>${esc(place(x.name).reg)} · ${x.alerts}</span></span>`).join("")}</div></div>
      </div></section>`;
  }

  // ---------- start
  (async () => {
    applyStatic();
    try {
      [registry, statusData, insightsData, limits] = await Promise.all([api("/api/registry"), api("/api/status").catch(() => null),
        api("/api/insights").catch(() => null), api("/api/limits").catch(() => null)]);
    } catch (e) { showPackError(t("err_registry", e.message)); }
    renderLive();
    renderIntro();
    renderSaved();
    const fresh = registry ? registry.alerts.filter(isNew).length : 0;
    if (fresh) { $("#new-badge").textContent = fresh; $("#new-badge").hidden = false; }
    show(location.hash.slice(1), false);
    setTimeout(() => store.set("bw.lastVisit", new Date().toISOString()), 4000);
    // a kept stock list is re-checked when the registry has moved on since its last check
    const s = store.get("bw.stock");
    if (s && statusData && statusData.alerts_as_of && s.asOf && statusData.alerts_as_of > s.asOf) recheckSaved();
    setInterval(loadStatus, 120000);
  })();
})();
