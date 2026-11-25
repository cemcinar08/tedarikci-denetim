"use strict";
/* Tedarikçi Denetim — tek sayfalık arayüz (bağımlılıksız) */

const $app = document.getElementById("app");
let META = null;
let SETTINGS = null;
let ME = null;
const can = (perm) => !!ME && (ME.permissions.includes("*") || ME.permissions.includes(perm));
const isSupplier = () => ME?.role === "supplier";

// ---------------------------------------------------------------- yardımcılar
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (d) => (d ? new Date(d.length <= 10 ? d + "T00:00:00" : d).toLocaleDateString("tr-TR") : "—");
const fmtDT = (d) => (d ? new Date(d).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" }) : "—");
const num = (v) => (v == null ? "—" : Number(v).toLocaleString("tr-TR", { maximumFractionDigits: 1 }));
const todayISO = () => new Date().toISOString().slice(0, 10);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return null;
  if (res.status === 401 && !path.startsWith("/api/auth/")) { ME = null; showLogin(); throw new Error("Oturum sona erdi, lütfen tekrar giriş yapın."); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg = data.detail;
    if (Array.isArray(msg)) msg = msg.map((e) => e.msg).join("; ");
    throw new Error(msg || `Hata ${res.status}`);
  }
  return data;
}

function toast(msg, ms = 3500) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

function modal(html, onMount) {
  const m = document.getElementById("modal");
  const box = document.getElementById("modal-box");
  box.innerHTML = html;
  box.style.maxWidth = "";
  m.classList.remove("hidden");
  m.onclick = (e) => { if (e.target === m) closeModal(); };
  onMount && onMount(document.getElementById("modal-box"));
}
const closeModal = () => {
  document.getElementById("modal").classList.add("hidden");
  document.getElementById("modal-box").innerHTML = ""; // gizli formda yinelenen id'ler kalmasın
};
document.addEventListener("keydown", (e) => e.key === "Escape" && closeModal());

const formData = (form) => Object.fromEntries([...new FormData(form)].map(([k, v]) => [k, v === "" ? null : v]));

// ---------------------------------------------------------------- rozetler
const riskBadge = (r) => riskPill(r);
const sevBadge = (s) => `<span class="badge ${{ kritik: "b-crit", yüksek: "b-bad", orta: "b-warn", düşük: "" }[s] || ""}">${esc(s)}</span>`;
const statusBadge = (s) => `<span class="badge ${{ kapandı: "b-ok", "devam ediyor": "b-warn", açık: "b-bad", tamamlandı: "b-ok", planlandı: "", aktif: "b-ok", askıda: "b-warn", pasif: "", aday: "b-warn", "çıkış sürecinde": "b-bad" }[s] ?? ""}">${esc(s)}</span>`;
const checkBadge = (s) => {
  const m = { pass: ["b-ok", "Geçti"], warn: ["b-warn", "Uyarı"], fail: ["b-bad", "Başarısız"], info: ["", "Bilgi"], error: ["", "Ölçülemedi"] }[s] || ["", s];
  return `<span class="badge ${m[0]}">${m[1]}</span>`;
};
const gradeColor = (g) => ({ A: "b-ok", B: "b-ok", C: "b-warn", D: "b-bad", F: "b-bad" }[g] || "");
const gradeBox = (g, big) => (g ? `<span class="grade badge ${gradeColor(g)} ${big ? "big" : ""}">${esc(g)}</span>` : `<span class="muted">—</span>`);
const scoreColor = (v) => (v == null ? "var(--info)" : v >= 85 ? "var(--ok)" : v >= 70 ? "var(--warn)" : "var(--bad)");
const meter = (v) => (v == null ? `<span class="muted" style="display:block">—</span>` :
  `<div style="display:flex;align-items:center;gap:8px"><div class="meter" style="flex:1"><div style="width:${v}%;background:${scoreColor(v)}"></div></div><b style="min-width:34px;text-align:right">${num(v)}</b></div>`);
const ratingBadge = (r) => (r ? `<span class="badge ${{ Onaylı: "b-ok", "Şartlı Onaylı": "b-warn", "Gelişim Gerekli": "b-bad", Onaysız: "b-crit" }[r]}">${esc(r)}</span>` : "");
const overdue = (f) => f.status !== "kapandı" && f.due_date && f.due_date < todayISO();

function stackedBar(parts) {
  const total = parts.reduce((a, p) => a + p.v, 0) || 1;
  return `<div class="bar">${parts.map((p) => `<div title="${esc(p.l)}: ${p.v}" style="width:${(100 * p.v) / total}%;background:${p.c}"></div>`).join("")}</div>
  <div class="legend">${parts.map((p) => `<span><i style="background:${p.c}"></i>${esc(p.l)}: <b>${p.v}</b></span>`).join("")}</div>`;
}

// ---------------------------------------------------------------- yönlendirme
const routes = [
  [/^#?\/?$/, viewDashboard],
  [/^#\/tedarikciler$/, viewSuppliers],
  [/^#\/tedarikci\/(\d+)(?:\/(\w+))?$/, viewSupplier],
  [/^#\/denetimler$/, viewAudits],
  [/^#\/denetim\/(\d+)$/, viewAudit],
  [/^#\/bulgular$/, viewFindings],
  [/^#\/istihbarat$/, viewIntel],
  [/^#\/tarama\/(\d+)$/, viewScan],
  [/^#\/rapor\/(\d+)$/, viewReport],
  [/^#\/soru-setleri$/, viewQuestionSets],
  [/^#\/soru-seti\/(\d+|yeni)$/, viewQuestionSet],
  [/^#\/ayarlar$/, viewSettings],
  [/^#\/kullanicilar$/, viewUsers],
  [/^#\/kayitlar$/, viewAuditLogs],
  [/^#\/capa$/, viewCapaList],
  [/^#\/capa\/(\d+)$/, viewCapa],
];

async function router() {
  if (!document.getElementById("modal").classList.contains("hidden")) closeModal();
  const h = location.hash || "#/";
  const top = h.split("/")[1] || "";
  const map = { capa: "capa", tedarikci: isSupplier() ? "" : "tedarikciler", denetim: "denetimler", tarama: "istihbarat", rapor: "tedarikciler", "soru-seti": "soru-setleri" };
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (map[top] ?? top)));
  for (const [re, fn] of routes) {
    const m = h.match(re);
    if (m) {
      try {
        if (!META) META = await api("/api/meta");
        await fn(...m.slice(1));
        applyPerms($app);
      } catch (e) {
        $app.innerHTML = `<div class="card"><b>Hata:</b> ${esc(e.message)}</div>`;
      }
      window.scrollTo(0, 0);
      return;
    }
  }
  $app.innerHTML = `<div class="empty">Sayfa bulunamadı.</div>`;
}
window.addEventListener("hashchange", () => ME && router());

// Yetkisi olmayan öğeleri gizle: data-perm="izin" / data-hide-role="rol"
function applyPerms(root = document) {
  root.querySelectorAll("[data-perm]").forEach((el) => el.classList.toggle("hidden", !can(el.dataset.perm)));
  root.querySelectorAll("[data-hide-role]").forEach((el) => { if (ME && el.dataset.hideRole === ME.role) el.classList.add("hidden"); });
}

async function boot() {
  const st = await api("/api/auth/status");
  if (st.setup_required) return showSetup();
  if (!st.user) return showLogin();
  ME = await api("/api/me");
  document.getElementById("nav").classList.remove("hidden");
  const um = document.getElementById("usermenu");
  um.classList.remove("hidden");
  um.innerHTML = `<div class="who"><b>${esc(ME.name)}</b><div class="small muted">${esc(ME.role_title)}${ME.supplier_name ? " · " + esc(ME.supplier_name) : ""}</div></div>
    ${themeButton()}<button class="sm" id="pw">Parola</button><button class="sm" id="logout">Çıkış</button>`;
  bindTheme(um);
  document.getElementById("logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.hash = "#/"; location.reload(); };
  document.getElementById("pw").onclick = passwordForm;
  applyPerms();
  if (isSupplier() && (!location.hash || location.hash === "#/")) location.hash = `#/tedarikci/${ME.supplier_id}`;
  router();
}

function authShell(html) {
  document.getElementById("nav").classList.add("hidden");
  document.getElementById("usermenu").classList.add("hidden");
  $app.innerHTML = `<div class="theme-float">${themeButton()}</div><div class="card login-box">${html}</div>`;
  bindTheme($app);
}

// ---------------------------------------------------------------- tema (açık / koyu)
const currentTheme = () => document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
const themeButton = () => `<button class="sm theme-toggle" data-theme-toggle title="${currentTheme() === "dark" ? "Açık temaya geç" : "Koyu temaya geç"}" aria-label="Temayı değiştir">${currentTheme() === "dark" ? "☀" : "☾"}</button>`;
function bindTheme(root) {
  root.querySelectorAll("[data-theme-toggle]").forEach((b) => (b.onclick = () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch { /* özel pencere */ }
    document.querySelectorAll("[data-theme-toggle]").forEach((x) => { x.textContent = next === "dark" ? "☀" : "☾"; x.title = next === "dark" ? "Açık temaya geç" : "Koyu temaya geç"; });
  }));
}

function showLogin() {
  authShell(`<h1>Giriş</h1><div class="muted" style="margin-bottom:16px">Tedarikçi Denetim</div>
    <form id="lf"><label>E-posta</label><input name="email" type="email" required autocomplete="username">
    <label>Parola</label><input name="password" type="password" required autocomplete="current-password">
    <button class="primary" style="width:100%">Giriş yap</button></form>`);
  document.getElementById("lf").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/auth/login", { method: "POST", body: formData(e.target) }); boot(); } catch (err) { toast(err.message); }
  };
}

function showSetup() {
  authShell(`<h1>İlk kurulum</h1><div class="muted" style="margin-bottom:16px">Yönetici hesabını oluşturun. Diğer kullanıcıları (denetçi, izleyici, tedarikçi) sonra "Kullanıcılar" sayfasından eklersiniz.</div>
    <form id="sf"><label>Ad soyad</label><input name="name" required><label>E-posta</label><input name="email" type="email" required>
    <label>Parola (en az 8 karakter)</label><input name="password" type="password" minlength="8" required autocomplete="new-password">
    <button class="primary" style="width:100%">Yöneticiyi oluştur</button></form>`);
  document.getElementById("sf").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/auth/setup", { method: "POST", body: formData(e.target) }); boot(); } catch (err) { toast(err.message); }
  };
}

function passwordForm() {
  modal(`<h2>Parola değiştir</h2><form id="f"><label>Mevcut parola</label><input type="password" name="current" required>
    <label style="margin-top:8px">Yeni parola (en az 8 karakter)</label><input type="password" name="new" minlength="8" required>
    <div class="actions" style="justify-content:flex-end;margin-top:12px"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Kaydet</button></div></form>`, (box) => {
    box.querySelector("#f").onsubmit = async (e) => {
      e.preventDefault();
      try { await api("/api/me/password", { method: "PUT", body: formData(e.target) }); closeModal(); toast("Parola değiştirildi"); } catch (err) { toast(err.message); }
    };
  });
}

boot();

// ================================================================ PANO
const RISK_BADGE = { yüksek: ["b-bad", "Yüksek"], orta: ["b-warn", "Orta"], düşük: ["b-ok", "Düşük"], bilinmiyor: ["", "Değerlendirilmedi"] };
const riskPill = (r) => { const [c, l] = RISK_BADGE[r] || ["", r || "—"]; return `<span class="badge ${c}"><span class="dot"></span>${l}</span>`; };
const daysTo = (d) => (d ? Math.round((new Date(d + "T00:00:00") - new Date(todayISO() + "T00:00:00")) / 864e5) : null);
const store = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }, set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* yok */ } } };

async function viewDashboard() {
  const [d, sm, pend] = await Promise.all([api("/api/dashboard"), api("/api/summary?days=60"),
    can("capa:read") ? api("/api/capa?status=onay bekliyor") : Promise.resolve([])]);
  const t = d.totals, c = sm.capa, ce = sm.certificates, rk = d.risk;
  const metric = (label, value, sub = "", cls = "", href = null) =>
    `<${href ? `a href="${href}"` : "div"} class="metric"><div class="l">${label}</div><div class="v ${cls}">${value}</div><div class="s">${sub || "&nbsp;"}</div></${href ? "a" : "div"}>`;
  $app.innerHTML = `
  <div class="page-head"><div><h1>Genel bakış</h1><div class="muted">${isSupplier() ? esc(ME.supplier_name || "") + " · " : ""}${new Date().toLocaleDateString("tr-TR", { dateStyle: "long" })}</div></div>
    <div class="actions">${can("report:read") ? `<button id="exp">CSV dışa aktar</button>` : ""}<a class="btn primary" href="#/tedarikciler" data-perm="supplier:write">Tedarikçi ekle</a></div></div>
  <div class="metric-strip">
    ${metric("Aktif tedarikçi", t.suppliers, `${rk["bilinmiyor"]} değerlendirilmedi`)}
    ${metric("Yüksek risk", rk["yüksek"], `orta ${rk["orta"]} · düşük ${rk["düşük"]}`, rk["yüksek"] ? "bad" : "")}
    ${metric("Reddedilen denetim", sm.rejected_suppliers, "son denetimi reddedilen", sm.rejected_suppliers ? "bad" : "")}
    ${metric("Açık CAPA", c.open, `${c.by_severity["kritik"]} kritik · ${c.by_severity["yüksek"]} yüksek`, "", can("capa:read") ? "#/capa" : null)}
    ${metric("Onay bekleyen", c.by_status["onay bekliyor"], "denetçi değerlendirmesi", c.by_status["onay bekliyor"] ? "warn" : "", can("capa:read") ? "#/capa?status=onay bekliyor" : null)}
    ${metric("Termini geçen CAPA", c.overdue, `${t.overdue_findings} gecikmiş bulgu`, c.overdue ? "bad" : "")}
    ${metric("Açık bulgu", t.open_findings, `son 30 günde ${t.closed_last_30} kapandı`, "", can("finding:read") ? "#/bulgular" : null)}
    ${metric("Sertifika · 60 gün", ce.expiring + ce.expired, ce.expired ? `${ce.expired} süresi dolmuş` : "yaklaşan bitiş", ce.expired ? "bad" : ce.expiring ? "warn" : "")}
  </div>

  <div class="card flush">
    <div class="card-head"><h2>Tedarikçi risk tablosu</h2><span class="small muted" id="cnt"></span></div>
    <div class="toolbar">
      <input id="fq" class="search" placeholder="Tedarikçi veya kategori ara…" style="width:auto">
      <select id="fr" style="width:auto"><option value="">Tüm riskler</option><option value="yüksek">Yüksek</option><option value="orta">Orta</option><option value="düşük">Düşük</option><option value="bilinmiyor">Değerlendirilmedi</option></select>
      <select id="ft" style="width:auto"><option value="">Tüm tier'lar</option>${[...new Map(d.ranking.filter((x) => x.tier_id).map((x) => [x.tier_id, x.tier_name]))].sort().map(([id, n]) => `<option value="${id}">${esc(n)}</option>`).join("")}<option value="-">Tier atanmamış</option></select>
      <select id="fk" style="width:auto"><option value="">Tüm kritiklikler</option><option>yüksek</option><option>orta</option><option>düşük</option></select>
      <label class="te-small" style="margin-left:4px"><input type="checkbox" id="fa"> Yalnızca aksiyon gerekenler</label>
      <span class="grow"></span><button class="sm ghost" id="fclr">Filtreleri temizle</button>
    </div>
    <div class="table-wrap" id="rt"></div>
  </div>

  <div class="grid g3" style="margin-top:12px">
    <div class="card flush"><div class="card-head"><h2>Onay bekleyen CAPA</h2><a class="small muted" href="#/capa?status=onay bekliyor">Tümü →</a></div>
      ${pend.length ? `<table class="dense"><tbody>${pend.slice(0, 6).map((x) => `<tr><td><a href="#/capa/${x.id}">${esc(x.title.length > 70 ? x.title.slice(0, 70) + "…" : x.title)}</a><div class="small muted">${esc(x.supplier_name)} · ${x.round}. gönderim</div></td><td class="num">${sevBadge(x.severity)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty small">Onay bekleyen CAPA yok.</div>`}</div>
    <div class="card flush"><div class="card-head"><h2>Sertifika bitişleri</h2><span class="small muted">60 gün</span></div>
      ${ce.items.length ? `<table class="dense"><tbody>${ce.items.slice(0, 6).map((x) => `<tr><td>${esc(x.title || x.original_name)}<div class="small muted">${esc(x.supplier_name)}</div></td>
        <td class="num small">${fmtDate(x.expires_at)}<div>${x.days_left < 0 ? `<span class="badge b-bad">${-x.days_left} gün geçti</span>` : `<span class="badge b-warn">${x.days_left} gün</span>`}</div></td></tr>`).join("")}</tbody></table>` : `<div class="empty small">Yaklaşan sertifika bitişi yok.</div>`}</div>
    ${sm.contracts ? `<div class="card flush"><div class="card-head"><h2>Sözleşme bitişleri</h2><span class="small muted">ihbar süresi dahil</span></div>
      ${sm.contracts.length ? `<table class="dense"><tbody>${sm.contracts.slice(0, 6).map((x) => `<tr><td><a href="#/tedarikci/${x.supplier_id}/sozlesmeler">${esc(x.title)}</a><div class="small muted">${esc(x.supplier_name)}${x.notice_deadline ? " · ihbar " + fmtDate(x.notice_deadline) : ""}</div></td>
        <td class="num small">${fmtDate(x.end_date)}<div>${contractBadge(x)}</div></td></tr>`).join("")}</tbody></table>` : `<div class="empty small">Yaklaşan sözleşme bitişi yok.</div>`}</div>` : ""}
    <div class="card flush"><div class="card-head"><h2>Planlanan denetimler</h2><a class="small muted" href="#/denetimler">Tümü →</a></div>
      ${d.upcoming.length ? `<table class="dense"><tbody>${d.upcoming.slice(0, 6).map((a) => `<tr><td><a href="#/denetim/${a.id}">${esc(a.supplier_name)}</a><div class="small muted">${esc(a.audit_type)}${a.auditor ? " · " + esc(a.auditor) : ""}</div></td><td class="num small">${fmtDate(a.audit_date)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty small">Planlı denetim yok.</div>`}</div>
  </div>`;

  // ---- risk tablosu: filtre + sıralama (tercihler tarayıcıda hatırlanır)
  const RANK = { yüksek: 0, orta: 1, düşük: 2, bilinmiyor: 3 }, CRIT = { yüksek: 0, orta: 1, düşük: 2 };
  const DEC = { rejected: 0, improvement_required: 1, conditional: 2, approved: 3 };
  const cols = [
    ["name", "Tedarikçi", (x) => x.name.toLocaleLowerCase("tr")],
    ["tier", "Tier", (x) => x.tier_level ?? 99],
    ["crit", "Kritiklik", (x) => CRIT[x.criticality] ?? 9],
    ["risk", "Risk", (x) => RANK[x.risk] ?? 9],
    ["dec", "Son karar", (x) => DEC[x.audit_decision] ?? 9],
    ["score", "Puan", (x) => x.overall_score ?? -1, "num"],
    ["intel", "Siber", (x) => x.intel_score ?? -1, "num"],
    ["capa", "Açık CAPA", (x) => x.open_capa, "num"],
    ["late", "Geciken", (x) => x.overdue_capa + x.overdue_findings, "num"],
    ["cert", "Sertifika", (x) => x.next_cert_expiry || "9999"],
    ["last", "Son denetim", (x) => x.last_audit_date || "0000"],
    ["next", "Sonraki", (x) => x.next_audit_date || "9999"],
  ];
  let sort = store.get("dash.sort", { k: "risk", dir: 1 });
  const f = store.get("dash.filter", {});
  const el = (id) => document.getElementById(id);
  el("fq").value = f.q || ""; el("fr").value = f.r || ""; el("ft").value = f.t || ""; el("fk").value = f.k || ""; el("fa").checked = !!f.a;
  const needsAction = (x) => x.risk === "yüksek" || x.overdue_capa > 0 || x.overdue_findings > 0 || x.pending_capa > 0 || (x.next_cert_expiry && daysTo(x.next_cert_expiry) < 0);
  let shown = [];
  const draw = () => {
    const q = el("fq").value.trim().toLocaleLowerCase("tr"), r = el("fr").value, tr = el("ft").value, k = el("fk").value, act = el("fa").checked;
    store.set("dash.filter", { q, r, t: tr, k, a: act });
    shown = d.ranking.filter((x) => (!q || `${x.name} ${x.category || ""}`.toLocaleLowerCase("tr").includes(q)) && (!r || x.risk === r)
      && (!tr || (tr === "-" ? !x.tier_id : String(x.tier_id) === tr)) && (!k || x.criticality === k) && (!act || needsAction(x)));
    const key = cols.find((c) => c[0] === sort.k) || cols[3];
    shown.sort((a, b) => { const va = key[2](a), vb = key[2](b); return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir || a.name.localeCompare(b.name, "tr"); });
    el("cnt").textContent = `${shown.length} / ${d.ranking.length} tedarikçi`;
    el("rt").innerHTML = `<table><thead><tr>${cols.map(([id, l, , cls]) => `<th class="sortable ${cls || ""} ${sort.k === id ? "sorted" : ""}" data-k="${id}">${l}<span class="arr">${sort.k === id ? (sort.dir > 0 ? "↑" : "↓") : "↕"}</span></th>`).join("")}</tr></thead><tbody>
      ${shown.length ? shown.map((x) => { const cd = daysTo(x.next_cert_expiry); return `<tr>
        <td><a href="#/tedarikci/${x.id}"><b>${esc(x.name)}</b></a>${x.sanctions === "doğrulandı" ? ` <span class="badge b-crit">yaptırım</span>` : x.sanctions === "olası eşleşme" ? ` <span class="badge b-warn">yaptırım?</span>` : ""}${x.category ? `<div class="small muted">${esc(x.category)}</div>` : ""}</td>
        <td class="small">${x.tier_level ? `<span class="badge">T${x.tier_level}</span>` : `<span class="muted">—</span>`}</td>
        <td class="small">${esc(x.criticality)}</td>
        <td>${riskPill(x.risk)}</td>
        <td>${x.audit_decision ? decisionBadge(x.audit_decision) : `<span class="muted small">—</span>`}</td>
        <td class="num"><div style="display:flex;align-items:center;gap:8px;justify-content:flex-end"><div class="meter" style="width:56px;min-width:56px">${x.overall_score == null ? "" : `<div style="width:${x.overall_score}%;background:${x.audit_decision === "rejected" ? "var(--crit)" : scoreColor(x.overall_score)}"></div>`}</div><span style="min-width:30px">${num(x.overall_score)}</span></div></td>
        <td class="num">${gradeBox(x.intel_grade)}</td>
        <td class="num">${x.open_capa || `<span class="muted">0</span>`}${x.pending_capa ? ` <span class="badge b-warn" title="Onay bekleyen">${x.pending_capa}</span>` : ""}</td>
        <td class="num">${x.overdue_capa + x.overdue_findings ? `<span style="color:var(--bad);font-weight:600">${x.overdue_capa + x.overdue_findings}</span>` : `<span class="muted">0</span>`}</td>
        <td class="small">${x.next_cert_expiry ? `${fmtDate(x.next_cert_expiry)} ${cd < 0 ? `<span class="badge b-bad">doldu</span>` : cd <= 60 ? `<span class="badge b-warn">${cd} g</span>` : ""}` : `<span class="muted">—</span>`}</td>
        <td class="small">${fmtDate(x.last_audit_date)}</td>
        <td class="small">${fmtDate(x.next_audit_date)}</td></tr>`; }).join("")
      : `<tr><td colspan="${cols.length}" class="empty">Filtrelere uyan tedarikçi yok.</td></tr>`}</tbody></table>`;
    el("rt").querySelectorAll("th.sortable").forEach((th) => (th.onclick = () => {
      sort = sort.k === th.dataset.k ? { k: sort.k, dir: -sort.dir } : { k: th.dataset.k, dir: 1 };
      store.set("dash.sort", sort); draw();
    }));
  };
  ["fq"].forEach((i) => (el(i).oninput = draw));
  ["fr", "ft", "fk", "fa"].forEach((i) => (el(i).onchange = draw));
  el("fclr").onclick = () => { el("fq").value = ""; el("fr").value = ""; el("ft").value = ""; el("fk").value = ""; el("fa").checked = false; draw(); };
  const exp = el("exp");
  if (exp) exp.onclick = () => {
    const head = ["Tedarikçi", "Kategori", "Tier", "Kritiklik", "Risk", "Son karar", "Genel puan", "Denetim puanı", "Siber puan", "Açık CAPA", "Onay bekleyen", "Geciken CAPA", "Gecikmiş bulgu", "En yakın sertifika bitişi", "Son denetim", "Sonraki denetim"];
    const rowsCsv = shown.map((x) => [x.name, x.category, x.tier_name, x.criticality, x.risk, x.audit_decision ? DECISION[x.audit_decision][1] : "", x.overall_score, x.audit_score, x.intel_score,
      x.open_capa, x.pending_capa, x.overdue_capa, x.overdue_findings, x.next_cert_expiry, x.last_audit_date, x.next_audit_date]);
    const cell = (v) => { let s2 = String(v ?? ""); if (/^[=+\-@]/.test(s2)) s2 = "'" + s2; return `"${s2.replace(/"/g, '""')}"`; };
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + [head, ...rowsCsv].map((r) => r.map(cell).join(";")).join("\r\n")], { type: "text/csv;charset=utf-8" }));
    a.download = `tedarikci-risk-${todayISO()}.csv`; a.click();
  };
  draw();
}

// ================================================================ TEDARİKÇİLER
async function viewSuppliers() {
  const list = await api("/api/suppliers");
  $app.innerHTML = `
  <div class="page-head"><div><h1>Tedarikçiler</h1><div class="muted">${list.length} kayıt</div></div>
    <div class="actions"><button id="imp" data-perm="supplier:write">İçe aktar</button><button class="primary" id="add" data-perm="supplier:write">+ Yeni tedarikçi</button></div></div>
  <div class="card">
    <div class="filters"><input id="q" placeholder="Ara (ad, kategori, domain)…" style="min-width:260px">
      <select id="fr"><option value="">Tüm riskler</option><option>yüksek</option><option>orta</option><option>düşük</option><option>bilinmiyor</option></select></div>
    <div class="table-wrap"><table><thead><tr><th>Tedarikçi</th><th>Kategori</th><th>Kritiklik</th><th>Domain'ler</th><th>Denetim</th><th>Siber</th><th>Risk</th><th class="num">Açık bulgu</th><th>Durum</th></tr></thead>
    <tbody id="tb"></tbody></table></div></div>`;
  const draw = () => {
    const q = document.getElementById("q").value.toLowerCase();
    const fr = document.getElementById("fr").value;
    const rows = list.filter((s) => (!fr || s.summary.risk === fr) &&
      (!q || [s.name, s.category, ...s.domains].join(" ").toLowerCase().includes(q)));
    document.getElementById("tb").innerHTML = rows.length ? rows.map((s) => `<tr>
      <td><a href="#/tedarikci/${s.id}"><b>${esc(s.name)}</b></a><div class="small muted">${esc(s.country || "")}</div></td>
      <td>${esc(s.category || "—")}</td><td>${esc(s.criticality)}</td>
      <td><div class="chips">${s.domains.map((d) => `<span class="chip">${esc(d)}</span>`).join("") || `<span class="muted">—</span>`}</div></td>
      <td>${num(s.summary.audit_score)} ${s.summary.audit_decision ? decisionBadge(s.summary.audit_decision) : ratingBadge(s.summary.audit_rating)}</td><td>${gradeBox(s.summary.intel_grade)}</td>
      <td>${riskBadge(s.summary.risk)}</td><td class="num">${s.summary.open_findings}</td><td>${statusBadge(s.status)}</td></tr>`).join("")
      : `<tr><td colspan="9" class="empty">Kayıt yok.</td></tr>`;
  };
  document.getElementById("q").oninput = draw;
  document.getElementById("fr").onchange = draw;
  document.getElementById("add").onclick = onboardModal;
  document.getElementById("imp").onclick = importModal;
  draw();
}

async function supplierForm(s = {}, focus = null) {
  const tiers = await api("/api/supplier-tiers").catch(() => []);
  const sel = (name, opts, val) => `<select name="${name}">${opts.map((o) => `<option ${o === val ? "selected" : ""}>${o}</option>`).join("")}</select>`;
  modal(`<h2>${s.id ? "Tedarikçiyi düzenle" : "Yeni tedarikçi"}</h2>
  <form id="f">
    <div class="row"><div><label>Firma adı *</label><input name="name" required value="${esc(s.name || "")}"></div>
      <div><label>Kategori</label><input name="category" placeholder="ör. Hammadde, Lojistik, Yazılım" value="${esc(s.category || "")}"></div></div>
    <div class="row"><div><label>Kritiklik</label>${sel("criticality", ["düşük", "orta", "yüksek"], s.criticality || "orta")}</div>
      <div><label>Durum</label>${sel("status", ["aday", "aktif", "askıda", "çıkış sürecinde", "pasif"], s.status || "aktif")}</div>
      <div><label>Ülke</label><input name="country" value="${esc(s.country || "")}"></div>
      <div><label>Seviye (tier)</label><select name="tier_id"><option value="">—</option>${tiers.map((t) => `<option value="${t.id}" ${t.id === s.tier_id ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select></div></div>
    <div class="row"><div><label>Yetkili</label><input name="contact_name" value="${esc(s.contact_name || "")}"></div>
      <div><label>E-posta</label><input name="email" type="email" value="${esc(s.email || "")}"></div>
      <div><label>Telefon</label><input name="phone" value="${esc(s.phone || "")}"></div></div>
    <div style="margin-bottom:12px"><label>Domain'ler (dış istihbarat için; virgül veya satır ile ayırın)</label>
      <textarea name="domains" placeholder="ornekfirma.com.tr&#10;ornekfirma.com">${esc((s.domains || []).join("\n"))}</textarea></div>
    <div style="margin-bottom:12px"><label>Notlar</label><textarea name="notes">${esc(s.notes || "")}</textarea></div>
    <h3 id="bildirimBas">Bildirim ayarları</h3>
    <div class="small muted" style="margin-bottom:8px">Bulgular ve dış istihbarat sonuçları bu kanallardan tedarikçiye iletilir.</div>
    <div style="margin-bottom:12px"><label>Bildirim e-postaları (virgülle ayırın)</label><input name="notify_emails" placeholder="kalite@tedarikci.com, bilgiguvenligi@tedarikci.com" value="${esc(s.notify_emails || "")}"></div>
    <div class="row"><div><label>Webhook adresi (tedarikçinin sistemi)</label><input name="webhook_url" placeholder="https://tedarikci.com/api/musteri-bildirim" value="${esc(s.webhook_url || "")}"></div>
      <div><label>Webhook imza anahtarı</label><div style="display:flex;gap:6px"><input name="webhook_secret" value="${esc(s.webhook_secret || "")}" autocomplete="off"><button type="button" id="gen" title="Rastgele anahtar üret">Üret</button></div></div></div>
    <label style="display:flex;gap:8px;align-items:center;margin-bottom:12px;color:var(--text);font-weight:400"><input type="checkbox" name="auto_notify" style="width:auto" ${s.auto_notify ? "checked" : ""}>
      Yeni bulgular oluştuğunda (denetim veya istihbarat taraması) tedarikçiye otomatik bildir</label>
    <div class="actions" style="justify-content:flex-end"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Kaydet</button></div>
  </form>`, (box) => {
    box.querySelector("#gen").onclick = () => {
      const a = new Uint8Array(24); crypto.getRandomValues(a);
      box.querySelector("[name=webhook_secret]").value = [...a].map((x) => x.toString(16).padStart(2, "0")).join("");
    };
    if (focus === "bildirim") setTimeout(() => { box.querySelector("#bildirimBas").scrollIntoView(); box.querySelector("[name=notify_emails]").focus(); }, 50);
    box.querySelector("#f").onsubmit = async (e) => {
      e.preventDefault();
      const d = formData(e.target);
      d.auto_notify = e.target.auto_notify.checked;
      d.domains = (d.domains || "").split(/[\s,;]+/).filter(Boolean);
      try {
        const r = await api(s.id ? `/api/suppliers/${s.id}` : "/api/suppliers", { method: s.id ? "PUT" : "POST", body: d });
        closeModal(); toast("Kaydedildi");
        if (s.id) router(); else location.hash = `#/tedarikci/${r.id}`;
      } catch (err) { toast(err.message); }
    };
  });
}

async function viewSupplier(id, tab = "ozet") {
  const s = await api(`/api/suppliers/${id}`);
  const sm = s.summary;
  const tabs = [["ozet", "Özet"], ["risk", "Risk & Uyum"], ["denetimler", `Denetimler (${s.audits.length})`], ["bulgular", `Bulgular (${sm.open_findings} açık)`], ["capa", "CAPA"], ["sozlesmeler", "Sözleşmeler"], ["belgeler", "Sertifikalar"], ["istihbarat", "Dış İstihbarat"], ["bildirimler", "Bildirimler"]].filter(([k]) =>
    !(k === "bildirimler" && !can("notify:send")) && !(k === "istihbarat" && isSupplier()) && !(k === "sozlesmeler" && !can("contract:read")));
  $app.innerHTML = `
  <div class="page-head"><div><div class="small"><a href="#/tedarikciler">← Tedarikçiler</a></div>
    <h1>${esc(s.name)}</h1><div class="muted">${esc([s.category, s.country, s.tier_name].filter(Boolean).join(" · ") || "—")} · Kritiklik: <b>${esc(s.criticality)}</b> · ${statusBadge(s.status)}</div></div>
    <div class="actions">${["aktif", "askıda"].includes(s.status) ? `<button id="offb" data-perm="supplier:write">Çıkarma sürecini başlat</button>` : ""}<button id="edit" data-perm="supplier:write">Düzenle</button><a class="btn" href="#/rapor/${s.id}" data-perm="report:read">Rapor</a><button class="primary" id="newAudit" data-perm="audit:write">+ Denetim planla</button></div></div>
  <div class="kpis">
    <div class="kpi"><div class="l">Risk seviyesi</div><div class="v">${riskBadge(sm.risk)}</div></div>
    <div class="kpi"><div class="l">Genel puan</div><div class="v" style="color:${sm.audit_decision === "rejected" ? "var(--crit)" : scoreColor(sm.overall_score)}">${num(sm.overall_score)}</div></div>
    <div class="kpi"><div class="l">Son denetim puanı</div><div class="v">${num(sm.audit_score)}</div><div class="l">${sm.audit_decision ? decisionBadge(sm.audit_decision) : ratingBadge(sm.audit_rating)} ${fmtDate(sm.last_audit_date)}</div></div>
    <div class="kpi"><div class="l">Siber güvenlik notu</div><div class="v">${gradeBox(sm.intel_grade)} <span style="font-size:16px">${num(sm.intel_score)}</span></div><div class="l">${sm.last_scan ? "Tarama: " + fmtDT(sm.last_scan) : "Henüz taranmadı"}</div></div>
    <div class="kpi"><div class="l">Açık / gecikmiş bulgu</div><div class="v">${sm.open_findings} <span style="font-size:16px;color:var(--bad)">/ ${sm.overdue_findings}</span></div></div>
  </div>
  <div id="lifecycle"></div>
  <div class="tabs">${tabs.map(([k, l]) => `<button class="${k === tab ? "active" : ""}" onclick="location.hash='#/tedarikci/${s.id}/${k}'">${l}</button>`).join("")}</div>
  <div id="tab"></div>`;
  document.getElementById("edit").onclick = () => supplierForm(s);
  lifecycleBanner(s);
  document.getElementById("newAudit").onclick = () => auditForm(s.id);
  const T = document.getElementById("tab");

  if (tab === "ozet") {
    T.innerHTML = `<div class="grid g2"><div class="card"><h2>Firma bilgileri</h2><table><tbody>
      <tr><th>Yetkili</th><td>${esc(s.contact_name || "—")}</td></tr>
      <tr><th>E-posta</th><td>${s.email ? `<a href="mailto:${esc(s.email)}">${esc(s.email)}</a>` : "—"}</td></tr>
      <tr><th>Telefon</th><td>${esc(s.phone || "—")}</td></tr>
      <tr><th>Domain'ler</th><td><div class="chips">${s.domains.map((d) => `<span class="chip">${esc(d)}</span>`).join("") || "—"}</div></td></tr>
      <tr><th>Sonraki denetim</th><td>${fmtDate(sm.next_audit_date)}</td></tr>
      <tr><th>Kayıt tarihi</th><td>${fmtDate(s.created_at)}</td></tr></tbody></table>
      ${s.notes ? `<h3>Notlar</h3><div style="white-space:pre-wrap">${esc(s.notes)}</div>` : ""}
      <div style="margin-top:16px"><button class="danger sm" id="del" data-perm="supplier:write">Tedarikçiyi sil</button></div></div>
      <div class="card"><h2>Öncelikli açık bulgular</h2>${findingsTable(s.findings.filter((f) => f.status !== "kapandı").slice(0, 8), { compact: true })}</div></div>`;
    document.getElementById("del").onclick = async () => {
      if (!confirm(`"${s.name}" ve tüm denetim/bulgu/tarama kayıtları silinsin mi?`)) return;
      await api(`/api/suppliers/${s.id}`, { method: "DELETE" });
      location.hash = "#/tedarikciler";
    };
  } else if (tab === "denetimler") {
    T.innerHTML = `<div class="card">${auditsTable(s.audits)}</div>`;
  } else if (tab === "bulgular") {
    T.innerHTML = `<div class="card"><div class="actions" style="margin-bottom:12px"><button id="nf" data-perm="finding:write">+ Manuel bulgu ekle</button>
      <button id="nsel" data-perm="notify:send">Seçilenleri bildir</button><button id="nall" data-perm="notify:send" ${sm.open_findings ? "" : "disabled"}>Tüm açık bulguları bildir (${sm.open_findings})</button></div>
      <div id="ft"></div></div>`;
    document.getElementById("ft").innerHTML = findingsTable(s.findings, { selectable: true });
    bindFindingActions(T, s.findings);
    document.getElementById("nsel").onclick = () => {
      const sel = selectedFindings(T);
      if (!sel.length) return toast("Önce tablodan bulgu seçin.");
      notifyModal({ supplier_id: s.id, finding_ids: sel.map((x) => x.id) });
    };
    document.getElementById("nall").onclick = () =>
      notifyModal({ supplier_id: s.id, finding_ids: s.findings.filter((f) => f.status !== "kapandı").map((f) => f.id) });
    document.getElementById("nf").onclick = () => findingForm({ supplier_id: s.id });
  } else if (tab === "istihbarat") {
    T.innerHTML = `<div class="card"><div class="page-head" style="margin-bottom:8px"><div><h2>Domain güvenlik değerlendirmesi</h2>
      <div class="muted small">Pasif ve herkese açık kaynaklar: DNS, e-posta güvenliği (SPF/DMARC), TLS, HTTP güvenlik başlıkları, açık servisler ve bilinen CVE'ler (Shodan InternetDB), sertifika şeffaflığı (crt.sh), domain kaydı (RDAP).</div></div>
      <div class="actions"><label style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" id="cf" checked style="width:auto"> Başarısız kontrollerden bulgu oluştur</label>
      <button class="primary" id="scan" data-perm="intel:scan" ${s.domains.length ? "" : "disabled"}>Tüm domain'leri tara</button></div></div>
      ${s.domains.length ? "" : `<div class="empty">Bu tedarikçi için domain tanımlı değil. <a href="#" id="ed2">Domain ekleyin</a>.</div>`}
      ${s.scans.length ? `<table><thead><tr><th>Domain</th><th>Not</th><th>Puan</th><th>Özet</th><th>Tarih</th><th></th></tr></thead><tbody>
        ${s.scans.map((x) => { const su = x.result.summary; return `<tr><td><b>${esc(x.domain)}</b></td><td>${gradeBox(x.grade)}</td><td style="min-width:140px">${meter(x.score)}</td>
        <td class="small"><span style="color:var(--bad)">${su.fail} başarısız</span> · <span style="color:var(--warn)">${su.warn} uyarı</span> · <span style="color:var(--ok)">${su.pass} geçti</span>${su.error ? ` · ${su.error} ölçülemedi` : ""}</td>
        <td class="small">${fmtDT(x.scanned_at)}</td><td style="white-space:nowrap"><button class="sm" data-nscan="${x.id}" data-perm="notify:send">Bildir</button> <a href="#/tarama/${x.id}">Detay →</a></td></tr>`; }).join("")}</tbody></table>` : ""}
      </div>
      ${s.scan_history.length > s.scans.length ? `<div class="card"><h2>Tarama geçmişi</h2><table><tbody>${s.scan_history.map((x) => `<tr><td>${fmtDT(x.scanned_at)}</td><td>${esc(x.domain)}</td><td>${gradeBox(x.grade)}</td><td>${num(x.score)}</td><td><a href="#/tarama/${x.id}">Detay</a></td></tr>`).join("")}</tbody></table></div>` : ""}`;
    const ed2 = document.getElementById("ed2"); if (ed2) ed2.onclick = (e) => { e.preventDefault(); supplierForm(s); };
    T.querySelectorAll("[data-nscan]").forEach((b) => (b.onclick = () => notifyModal({ supplier_id: s.id, scan_id: +b.dataset.nscan,
      finding_ids: s.findings.filter((f) => f.source === "istihbarat" && f.status !== "kapandı" && f.title.startsWith(`[${s.scans.find((x) => x.id == b.dataset.nscan).domain}]`)).map((f) => f.id) })));
    document.getElementById("scan").onclick = async (e) => {
      const b = e.target; b.disabled = true; b.innerHTML = `<span class="spinner"></span> Taranıyor…`;
      try {
        const r = await api(`/api/suppliers/${s.id}/scan?create_findings=${document.getElementById("cf").checked}`, { method: "POST" });
        const c = r.reduce((a, x) => a + x.findings_created, 0), k = r.reduce((a, x) => a + x.findings_closed, 0);
        const nt = r.flatMap((x) => x.notified || []);
        toast(`Tarama tamamlandı. ${c} yeni bulgu, ${k} bulgu otomatik kapatıldı.${nt.length ? " Tedarikçiye otomatik bildirildi (" + [...new Set(nt)].join(", ") + ")." : ""}`);
        router();
      } catch (err) { toast(err.message); b.disabled = false; b.textContent = "Tüm domain'leri tara"; }
    };
  } else if (tab === "risk") {
    await riskTab(T, s);
  } else if (tab === "capa") {
    const list = await api(`/api/capa?supplier_id=${s.id}`);
    T.innerHTML = `<div class="card">${can("capa:write") ? `<div class="actions" style="margin-bottom:12px"><button id="nc">+ Manuel CAPA</button></div>` : ""}${capaTable(list)}</div>`;
    const nc = document.getElementById("nc");
    if (nc) nc.onclick = () => capaForm(s.id);
  } else if (tab === "sozlesmeler") {
    await contractsTab(T, s);
  } else if (tab === "belgeler") {
    T.innerHTML = `<div class="card"><div class="page-head" style="margin-bottom:8px"><div><h2>Sertifikalar ve belgeler</h2>
      <div class="small muted">ISO, TSE, CE vb. sertifikalar geçerlilik tarihleriyle izlenir; bitişe 60 gün kala özet ekranında uyarı verilir.</div></div>
      <button class="primary" id="ncert" data-perm="evidence:write">+ Sertifika yükle</button></div><div id="certs"></div></div>`;
    const draw = async () => {
      const certs = await api(`/api/evidence?supplier_id=${s.id}&kind=sertifika`);
      document.getElementById("certs").innerHTML = evidenceTable(certs, { cert: true });
      bindEvidence(document.getElementById("certs"), draw);
    };
    document.getElementById("ncert").onclick = () => uploadModal({ supplier_id: s.id, kind: "sertifika" }, draw);
    await draw();
  } else if (tab === "bildirimler") {
    const list = await api(`/api/notifications?supplier_id=${s.id}`);
    SETTINGS = SETTINGS || (await api("/api/settings").catch(() => null));
    T.innerHTML = `<div class="card"><div class="page-head" style="margin-bottom:8px"><div><h2>Bildirim ayarları</h2>
        <table class="small"><tbody>
        <tr><th>E-posta alıcıları</th><td>${esc(s.notify_emails || "—")}</td></tr>
        <tr><th>Webhook</th><td>${esc(s.webhook_url || "—")}${s.webhook_url ? (s.webhook_secret ? ` <span class="badge b-ok">imzalı</span>` : ` <span class="badge b-warn">imzasız</span>`) : ""}</td></tr>
        <tr><th>Otomatik bildirim</th><td>${s.auto_notify ? `<span class="badge b-ok">açık</span> — yeni denetim/istihbarat bulguları otomatik gönderilir` : "kapalı"}</td></tr></tbody></table></div>
        <div class="actions"><button id="ens">Bildirim ayarlarını düzenle</button>${s.webhook_url ? `<button id="wht">Webhook'u test et</button>` : ""}</div></div></div>
      <div class="card"><h2>Gönderilen bildirimler</h2>${notificationsTable(list)}</div>`;
    document.getElementById("ens").onclick = () => supplierForm(s, "bildirim");
    const wht = document.getElementById("wht");
    if (wht) wht.onclick = async () => {
      wht.disabled = true;
      try { const r = await api(`/api/suppliers/${s.id}/webhook-test`, { method: "POST" }); toast(`Webhook çalışıyor (HTTP ${r.status_code}).`); }
      catch (e) { toast(e.message, 6000); }
      wht.disabled = false;
    };
    bindNotificationActions(T);
  }
}

// ================================================================ BİLDİRİMLER
const notifStatus = (n) => `<span class="badge ${{ gönderildi: "b-ok", başarısız: "b-bad", manuel: "", beklemede: "b-warn" }[n.status] ?? ""}">${esc(n.status)}</span>`;
const chLabel = { email: "E-posta", webhook: "Webhook", manuel: "Manuel e-posta" };

function notificationsTable(list) {
  if (!list.length) return `<div class="empty">Henüz bildirim gönderilmedi.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Tarih</th><th>Kanal</th><th>Konu / alıcı</th><th>Durum</th><th>Tedarikçi yanıtları</th><th></th></tr></thead><tbody>
  ${list.map((n) => `<tr><td class="small">${fmtDT(n.sent_at || n.created_at)}${n.auto ? `<div><span class="badge">otomatik</span></div>` : ""}</td>
    <td>${esc(chLabel[n.channel] || n.channel)}</td>
    <td><b>${esc(n.subject || "")}</b><div class="small muted" style="word-break:break-all">${esc(n.target)}</div>
      <div class="small muted">${n.finding_ids.length} bulgu${n.scan_id ? " + tarama" : ""}${n.viewed_at ? ` · <span style="color:var(--ok)">yanıt sayfası açıldı ${fmtDT(n.viewed_at)}</span>` : ""}</div></td>
    <td>${notifStatus(n)}${n.response_code ? `<div class="small muted">HTTP ${n.response_code}</div>` : ""}${n.error ? `<div class="small" style="color:var(--bad);max-width:260px">${esc(n.error)}</div>` : ""}</td>
    <td class="small">${n.responses.length ? n.responses.slice(0, 4).map((r) => `<div style="margin-bottom:4px"><span class="badge">${esc(r.status)}</span> ${r.finding_title ? esc(r.finding_title.slice(0, 60)) : ""}
      ${r.comment ? `<div class="muted">${esc(r.comment.slice(0, 140))}</div>` : ""}<div class="muted">${fmtDT(r.created_at)} · ${esc(r.via)}${r.responder ? " · " + esc(r.responder) : ""}</div></div>`).join("") + (n.responses.length > 4 ? `<div class="muted">+${n.responses.length - 4} yanıt</div>` : "") : `<span class="muted">—</span>`}</td>
    <td style="white-space:nowrap">${n.status === "başarısız" ? `<button class="sm" data-retry="${n.id}">Tekrar dene</button> ` : ""}<button class="sm" data-link="${esc(n.token)}">Yanıt linki</button></td></tr>`).join("")}</tbody></table></div>`;
}

function bindNotificationActions(root) {
  root.querySelectorAll("[data-retry]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try { const n = await api(`/api/notifications/${b.dataset.retry}/retry`, { method: "POST" }); toast(n.status === "gönderildi" ? "Gönderildi" : "Yine başarısız: " + (n.error || ""), 6000); router(); }
    catch (e) { toast(e.message, 6000); b.disabled = false; }
  }));
  root.querySelectorAll("[data-link]").forEach((b) => (b.onclick = async () => {
    const url = `${(SETTINGS?.public_base_url || location.origin).replace(/\/$/, "")}/yanit/${b.dataset.link}`;
    try { await navigator.clipboard.writeText(url); toast("Yanıt linki kopyalandı"); } catch { prompt("Yanıt linki:", url); }
  }));
}


async function notifyModal({ supplier_id, finding_ids = [], scan_id = null }) {
  const pv = async (note) => api("/api/notifications/preview", { method: "POST", body: { supplier_id, finding_ids, scan_id, note } });
  let p;
  try { p = await pv(null); } catch (e) { return toast(e.message); }
  SETTINGS = SETTINGS || (await api("/api/settings").catch(() => null));
  const emailOk = p.recipients.email && p.smtp_ready;
  const emailHint = !p.recipients.email ? "Tedarikçide bildirim e-postası tanımlı değil" : !p.smtp_ready ? "SMTP ayarları yapılmamış (Ayarlar)" : esc(p.recipients.email);
  modal(`<h2>Tedarikçiye bildir</h2>
    <div class="muted" style="margin-bottom:12px"><b>${esc(p.payload.supplier.name)}</b> · ${finding_ids.length} bulgu${scan_id ? ` · ${esc(p.payload.scan.domain)} tarama sonucu (not ${esc(p.payload.scan.grade || "—")})` : ""}</div>
    ${p.base_is_local ? `<div class="card" style="background:var(--warn-bg);color:var(--warn);border-color:transparent">
      <b>Uyarı:</b> Yanıt linkleri <code>${esc(p.base_url)}</code> adresini gösteriyor; bu adres yalnızca sizin bilgisayarınızda çalışır, tedarikçi açamaz.
      Tedarikçinin linke veya API'ye ulaşabilmesi için uygulamayı erişilebilir bir sunucuda çalıştırıp <a href="#/ayarlar" onclick="closeModal()">Ayarlar</a>'dan genel adresi girin. E-posta ve webhook yine de gönderilir.</div>` : ""}
    <label>Kanallar</label>
    <div class="set-grid" style="margin-bottom:12px">
      <label class="set-card"><input type="checkbox" name="ch" value="email" ${emailOk ? "checked" : "disabled"}><div><b>E-posta</b><div class="small muted" style="word-break:break-all">${emailHint}</div></div></label>
      <label class="set-card"><input type="checkbox" name="ch" value="webhook" ${p.recipients.webhook ? "checked" : "disabled"}><div><b>Webhook (tedarikçi sistemi)</b><div class="small muted" style="word-break:break-all">${p.recipients.webhook ? esc(p.recipients.webhook) + (p.webhook_signed ? " · imzalı" : " · imzasız") : "Tedarikçide webhook adresi tanımlı değil"}</div></div></label>
    </div>
    ${!emailOk && !p.recipients.webhook ? `<div class="small muted" style="margin:-4px 0 12px">Tedarikçinin bildirim e-postasını veya webhook adresini <a href="#" id="editSup">tedarikçi ayarlarından</a> ekleyin, ya da aşağıdan kendi e-posta programınızla gönderin.</div>` : ""}
    <label>Ek mesaj (isteğe bağlı)</label><textarea id="note" placeholder="ör. Lütfen 15 gün içinde aksiyon planınızı iletin."></textarea>
    <div class="tabs" style="margin-top:12px"><button type="button" class="active" data-pv="mail">E-posta önizleme</button><button type="button" data-pv="json">Webhook JSON</button></div>
    <div id="pvbox"></div>
    <div class="actions" style="justify-content:space-between;margin-top:12px">
      <button type="button" id="manual" title="Kendi e-posta programınız açılır; bildirim kaydedilir ve yanıt linki oluşturulur">Kendi e-posta programımla gönder</button>
      <div class="actions"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary" id="send">Gönder</button></div></div>`, (box) => {
    box.style.maxWidth = "820px";
    let mode = "mail";
    const draw = () => {
      box.querySelector("#pvbox").innerHTML = mode === "mail"
        ? `<div class="small" style="margin-bottom:6px"><b>Konu:</b> ${esc(p.subject)}</div><iframe sandbox="" style="width:100%;height:340px;border:1px solid var(--border);border-radius:6px;background:#fff" srcdoc="${esc(p.html)}"></iframe>`
        : `<pre style="max-height:340px;overflow:auto;background:var(--bg);padding:10px;border-radius:6px;font-size:12px">${esc(JSON.stringify(p.payload, null, 2))}</pre>
           <div class="small muted">Başlıklar: <code>X-TD-Event</code>, <code>X-TD-Delivery</code>, <code>X-TD-Timestamp</code>${p.webhook_signed ? `, <code>X-TD-Signature: sha256=HMAC(secret, timestamp + "." + gövde)</code>` : ""}</div>`;
    };
    draw();
    box.querySelectorAll("[data-pv]").forEach((b) => (b.onclick = () => {
      mode = b.dataset.pv; box.querySelectorAll("[data-pv]").forEach((x) => x.classList.toggle("active", x === b)); draw();
    }));
    let t;
    box.querySelector("#note").oninput = (e) => { clearTimeout(t); t = setTimeout(async () => { p = { ...p, ...(await pv(e.target.value || null)) }; draw(); }, 400); };
    const es = box.querySelector("#editSup");
    if (es) es.onclick = async (e) => { e.preventDefault(); const sup = await api(`/api/suppliers/${supplier_id}`); supplierForm(sup, "bildirim"); };
    const note = () => box.querySelector("#note").value.trim() || null;
    box.querySelector("#send").onclick = async (e) => {
      const channels = [...box.querySelectorAll("input[name=ch]:checked")].map((c) => c.value);
      if (!channels.length) return toast("Gönderilecek kanal yok. Tedarikçi ayarlarını tamamlayın veya kendi e-posta programınızla gönderin.", 6000);
      e.target.disabled = true; e.target.innerHTML = `<span class="spinner"></span> Gönderiliyor…`;
      try {
        const r = await api("/api/notifications", { method: "POST", body: { supplier_id, finding_ids, scan_id, channels, note: note() } });
        const ok = r.filter((n) => n.status === "gönderildi").map((n) => chLabel[n.channel]);
        const bad = r.filter((n) => n.status === "başarısız");
        closeModal();
        toast(ok.length ? `Gönderildi: ${ok.join(", ")}${bad.length ? ` · Başarısız: ${bad.map((n) => chLabel[n.channel] + " (" + n.error + ")").join(", ")}` : ""}`
          : `Gönderilemedi: ${bad.map((n) => chLabel[n.channel] + " — " + n.error).join("; ")}`, 7000);
        router();
      } catch (err) { toast(err.message); e.target.disabled = false; e.target.textContent = "Gönder"; }
    };
    box.querySelector("#manual").onclick = async () => {
      try {
        const [n] = await api("/api/notifications", { method: "POST", body: { supplier_id, finding_ids, scan_id, channels: ["manuel"], note: note() } });
        const to = p.recipients.email || "";
        location.href = `mailto:${encodeURIComponent(to).replace(/%2C/g, ",").replace(/%40/g, "@")}?subject=${encodeURIComponent(n.subject)}&body=${encodeURIComponent(n.mailto_body.slice(0, 1800))}`;
        closeModal(); toast("E-posta programınız açılıyor. Bildirim kaydedildi."); setTimeout(router, 800);
      } catch (err) { toast(err.message); }
    };
  });
}

// ================================================================ AYARLAR
async function viewSettings() {
  const st = (SETTINGS = await api("/api/settings"));
  const f = (k, label, attrs = "") => `<div><label>${label}</label><input name="${k}" value="${esc(st[k] || "")}" ${attrs}></div>`;
  $app.innerHTML = `<div class="page-head"><div><h1>Ayarlar</h1><div class="muted">Tedarikçi bildirimleri için genel ayarlar</div></div></div>
  <form id="sf">
  <div class="card"><h2>Kurum</h2><div class="row">
    ${f("org_name", "Kurum adı (e-postalarda gönderen olarak görünür)", 'placeholder="ör. ABC Sanayi A.Ş."')}
    ${f("public_base_url", "Uygulamanın genel adresi (tedarikçinin yanıt linki için)", 'placeholder="ör. https://denetim.sirketiniz.com"')}</div>
    <div class="small muted">Tedarikçinin yanıt sayfasını ve yanıt API'sini kullanabilmesi için uygulama internetten (veya tedarikçinin ağından) erişilebilir bir sunucuda çalışmalıdır.
      Uygulama yalnızca kendi bilgisayarınızda çalışıyorsa e-posta ve webhook gönderilir ama tedarikçi yanıt linkini açamaz; yanıtları siz bulgu üzerine işleyebilirsiniz.</div></div>
  <div class="card"><h2>E-posta (SMTP)</h2>
    <div class="row">${f("smtp_host", "SMTP sunucusu", 'placeholder="ör. smtp.office365.com, smtp.gmail.com"')}${f("smtp_port", "Port", 'placeholder="587"')}
      <div><label>Güvenlik</label><select name="smtp_security">${[["starttls", "STARTTLS (587)"], ["ssl", "SSL/TLS (465)"], ["none", "Yok (önerilmez)"]].map(([v, l]) => `<option value="${v}" ${st.smtp_security === v ? "selected" : ""}>${l}</option>`).join("")}</select></div></div>
    <div class="row">${f("smtp_user", "Kullanıcı adı", 'autocomplete="off"')}
      <div><label>Parola ${st.smtp_password_set ? `<span class="badge b-ok">kayıtlı</span>` : ""}</label><input name="smtp_password" type="password" autocomplete="new-password" placeholder="${st.smtp_password_set ? "Değiştirmek için yazın" : ""}"></div></div>
    <div class="row">${f("smtp_from", "Gönderen adresi", 'placeholder="denetim@sirketiniz.com"')}${f("reply_to", "Yanıt adresi (Reply-To, isteğe bağlı)")}</div>
    <div class="small muted">Gmail / Microsoft 365 kullanıyorsanız normal parolanız yerine "uygulama parolası" oluşturmanız gerekebilir. Parola bu bilgisayardaki veritabanında saklanır.</div>
    <div class="actions" style="margin-top:12px;align-items:center"><span>Durum: ${st.smtp_ready ? `<span class="badge b-ok">hazır</span>` : `<span class="badge b-warn">yapılandırılmadı</span>`}</span>
      <input id="testTo" placeholder="Test alıcısı (e-posta)" style="width:260px"><button type="button" id="test">Test e-postası gönder</button></div></div>
  <div class="actions" style="justify-content:flex-end"><button class="primary">Kaydet</button></div></form>
  <div class="card" style="margin-top:16px" id="wcard"></div>
  <div class="card" style="margin-top:16px" id="sancard"></div>
  <div class="card" style="margin-top:16px" id="whcard"></div>
  <div class="card" style="margin-top:16px"><h2>Tedarikçi sistemleri için entegrasyon bilgisi</h2><div class="small">
    <p><b>Webhook (bizden tedarikçiye):</b> Her tedarikçi için tanımlanan adrese <code>POST</code> ile JSON gönderilir. Olaylar: <code>finding.notification</code>, <code>intel.notification</code>, <code>test</code>.
      Başlıklar: <code>X-TD-Event</code>, <code>X-TD-Delivery</code> (tekil id), <code>X-TD-Timestamp</code>, <code>X-TD-Signature</code>.
      İmza: <code>sha256=</code> + HMAC-SHA256(paylaşılan anahtar, <code>timestamp + "." + ham gövde</code>) (hex). 2xx dışındaki yanıtlar başarısız sayılır ve "Tekrar dene" ile yeniden gönderilebilir.</p>
    <p><b>Yanıt API'si (tedarikçiden bize):</b> Her bildirimde yer alan <code>response.api</code> adresine:<br>
      <code>POST /api/public/respond/{token}</code> · JSON: <code>{"finding_id": 12, "status": "okundu | aksiyon planlandı | giderildi | itiraz", "comment": "...", "planned_date": "YYYY-AA-GG", "responder": "..."}</code><br>
      <code>finding_id</code> verilmezse yanıt bildirimdeki tüm bulgulara uygulanır. <code>GET</code> aynı adreste bildirimin güncel durumunu döner. Token bildirime özeldir; yalnızca o bildirimin bulgularına erişim verir.</p>
    <p>Tedarikçinin "aksiyon planlandı" veya "giderildi" yanıtı, açık bulguyu "devam ediyor" durumuna alır. Bulguyu kapatmak (doğrulamak) her zaman sizin kararınızdır.</p></div></div>`;
  webhookAdmin(document.getElementById("whcard"));
  weightsAdmin(document.getElementById("wcard"));
  sanctionsAdmin(document.getElementById("sancard"));
  document.getElementById("sf").onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    if (!d.smtp_password) delete d.smtp_password;
    try { SETTINGS = await api("/api/settings", { method: "PUT", body: d }); toast("Ayarlar kaydedildi"); viewSettings(); }
    catch (err) { toast(err.message); }
  };
  document.getElementById("test").onclick = async (e) => {
    const to = document.getElementById("testTo").value.trim();
    if (!to) return toast("Test alıcısını yazın.");
    e.target.disabled = true; e.target.innerHTML = `<span class="spinner"></span> Gönderiliyor…`;
    try { await api("/api/settings/test-email", { method: "POST", body: { to } }); toast("Test e-postası gönderildi."); }
    catch (err) { toast(err.message, 8000); }
    e.target.disabled = false; e.target.textContent = "Test e-postası gönder";
  };
}

// ================================================================ DENETİMLER
function auditsTable(list, withSupplier = false) {
  if (!list.length) return `<div class="empty">Denetim kaydı yok.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Tarih</th>${withSupplier ? "<th>Tedarikçi</th>" : ""}<th>Soru setleri</th><th>Tür</th><th>Denetçi</th><th>Durum</th><th>Puan</th><th></th></tr></thead><tbody>
  ${list.map((a) => `<tr><td>${fmtDate(a.audit_date)}</td>${withSupplier ? `<td><a href="#/tedarikci/${a.supplier_id}">${esc(a.supplier_name)}</a></td>` : ""}
    <td class="small">${esc((a.set_names || ["Genel Tedarikçi Denetimi"]).join(", "))}</td>
    <td>${esc(a.audit_type)}</td><td>${esc(a.auditor || "—")}</td><td>${statusBadge(a.status)} ${decisionBadge(a.decision)}</td><td style="min-width:140px">${meter(a.score)}</td>
    <td style="white-space:nowrap"><a href="#/denetim/${a.id}">Aç →</a>${can("report:read") ? ` · <a href="/api/reports/audits/${a.id}.pdf" target="_blank">PDF</a> · <a href="/api/reports/audits/${a.id}.csv">CSV</a>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
}

async function viewAudits() {
  const list = await api("/api/audits");
  $app.innerHTML = `<div class="page-head"><div><h1>Denetimler</h1><div class="muted">${list.length} denetim</div></div>
    <div class="actions"><button class="primary" id="na" data-perm="audit:write">+ Denetim planla</button></div></div><div class="card">${auditsTable(list, true)}</div>`;
  document.getElementById("na").onclick = async () => {
    const sups = await api("/api/suppliers");
    if (!sups.length) return toast("Önce bir tedarikçi ekleyin.");
    auditForm(null, sups);
  };
}

// Bölüm/soru düzenleyici (özel sorular ve soru seti editörü ortak kullanır)
function sectionBuilder(root, sections = []) {
  const blank = () => ({ title: "", weight: 10, questions: [] });
  let data = sections.length ? sections.map((x) => ({ title: x.title, weight: x.weight, questions: x.questions.map((q) => q.text ?? q) })) : [];
  const draw = () => {
    root.innerHTML = `${data.map((sec, i) => `<div class="sb-sec" data-i="${i}">
        <div class="sb-head"><input class="sb-title" placeholder="Bölüm adı (ör. Fiziksel Güvenlik)" value="${esc(sec.title)}">
          <label class="sb-w" title="Bölümün toplam puandaki ağırlığı">Ağırlık <input class="sb-weight" type="number" min="1" value="${esc(sec.weight)}"></label>
          <button type="button" class="sm danger sb-del" title="Bölümü sil">✕</button></div>
        <textarea class="sb-qs" placeholder="Her satıra bir soru yazın&#10;ör. Sunucu odasına girişler kayıt altında mı?&#10;ör. Yedekler şifreli mi?">${esc(sec.questions.join("\n"))}</textarea>
        <div class="small muted sb-count">${sec.questions.length} soru</div></div>`).join("")}
      <button type="button" class="sm sb-add">+ Bölüm ekle</button>`;
    root.querySelectorAll(".sb-sec").forEach((el) => {
      const i = +el.dataset.i;
      el.querySelector(".sb-title").oninput = (e) => (data[i].title = e.target.value);
      el.querySelector(".sb-weight").oninput = (e) => (data[i].weight = +e.target.value || 1);
      el.querySelector(".sb-qs").oninput = (e) => {
        data[i].questions = e.target.value.split("\n").map((x) => x.trim()).filter(Boolean);
        el.querySelector(".sb-count").textContent = `${data[i].questions.length} soru`;
        root.dispatchEvent(new Event("change"));
      };
      el.querySelector(".sb-del").onclick = () => { data.splice(i, 1); draw(); root.dispatchEvent(new Event("change")); };
    });
    root.querySelector(".sb-add").onclick = () => { data.push(blank()); draw(); root.querySelector(".sb-sec:last-of-type .sb-title")?.focus(); };
  };
  draw();
  return {
    get: () => data.filter((x) => x.title.trim() || x.questions.length),
    count: () => data.reduce((a, x) => a + x.questions.length, 0),
    validate() {
      for (const [i, x] of this.get().entries()) {
        if (!x.title.trim()) return `${i + 1}. bölümün adı boş.`;
        if (!x.questions.length) return `"${x.title}" bölümünde soru yok.`;
      }
      return null;
    },
  };
}

async function auditForm(supplierId, suppliers) {
  const sets = await api("/api/question-sets");
  const std = sets.filter((x) => x.builtin), mine = sets.filter((x) => !x.builtin);
  const setCard = (x) => `<label class="set-card"><input type="checkbox" name="qs" value="${x.id}">
      <div><div><b>${esc(x.name)}</b> <span class="badge">${esc(x.standard || "Özel")}</span></div>
      <div class="small muted">${x.question_count} soru · ${x.sections.length} bölüm${x.description ? " · " + esc(x.description) : ""}</div></div></label>`;
  modal(`<h2>Denetim planla</h2><form id="f">
    ${suppliers ? `<div style="margin-bottom:12px"><label>Tedarikçi</label><select name="supplier_id">${suppliers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select></div>` : ""}
    <div class="row"><div><label>Tarih</label><input type="date" name="audit_date" required value="${todayISO()}"></div>
      <div><label>Tür</label><select name="audit_type"><option>yerinde</option><option>uzaktan</option><option>doküman</option></select></div>
      <div><label>Denetçi</label><input name="auditor"></div>
      <div><label>Durum</label><select name="status"><option>planlandı</option><option>devam ediyor</option></select></div></div>

    <h3>Özel soru seti</h3>
    <div class="small muted" style="margin-bottom:8px">Bu denetime özel sorular ekleyin (satır başına "!" yazarsanız soru <b>kritik</b> olur). İsterseniz kütüphaneye kaydedip sonraki denetimlerde tekrar kullanabilirsiniz; ağırlık ve koşulları Soru Setleri sayfasından ayarlayabilirsiniz.</div>
    <div id="sb"></div>
    <div id="saveRow" class="hidden" style="display:flex;gap:8px;align-items:center;margin-top:8px">
      <label style="display:flex;gap:6px;align-items:center;margin:0;white-space:nowrap"><input type="checkbox" id="saveChk" style="width:auto"> Kütüphaneye kaydet:</label>
      <input id="saveName" placeholder="Set adı (ör. Lojistik Depo Denetimi)"></div>

    <h3>Standart soru setleri</h3>
    <div class="set-grid">${std.map(setCard).join("")}</div>
    ${mine.length ? `<h3>Kayıtlı özel setlerim</h3><div class="set-grid">${mine.map(setCard).join("")}</div>` : ""}
    <div class="small muted" style="margin:6px 0 12px">Birden fazla set seçebilirsiniz; hepsi tek denetimde birleşir ve her bölüm kendi ağırlığıyla puanlanır. <a href="#/soru-setleri" onclick="closeModal()">Soru setlerini yönet →</a></div>

    <div style="margin-bottom:12px"><label>Not / kapsam</label><textarea name="notes"></textarea></div>
    <div class="actions" style="justify-content:space-between;align-items:center"><span class="small muted" id="total"></span>
      <div class="actions"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Oluştur</button></div></div></form>`, (box) => {
    box.style.maxWidth = "860px";
    const sbRoot = box.querySelector("#sb");
    const sb = sectionBuilder(sbRoot);
    const update = () => {
      const chosen = [...box.querySelectorAll("input[name=qs]:checked")].map((c) => sets.find((x) => x.id == c.value));
      const n = chosen.reduce((a, x) => a + x.question_count, 0) + sb.count();
      box.querySelector("#total").textContent = `Toplam ${n} soru · ${chosen.length} set${sb.count() ? " + özel sorular" : ""}`;
      box.querySelector("#saveRow").classList.toggle("hidden", !sb.count());
    };
    sbRoot.addEventListener("change", update);
    sbRoot.addEventListener("click", () => setTimeout(update));
    box.querySelectorAll("input[name=qs]").forEach((c) => (c.onchange = update));
    update();
    box.querySelector("#f").onsubmit = async (e) => {
      e.preventDefault();
      const d = formData(e.target);
      delete d.qs;
      const sid = supplierId || d.supplier_id; delete d.supplier_id;
      const err = sb.validate();
      if (err) return toast(err);
      d.question_set_ids = [...box.querySelectorAll("input[name=qs]:checked")].map((c) => +c.value);
      d.custom_sections = sb.get();
      if (!d.question_set_ids.length && !d.custom_sections.length) return toast("En az bir soru seti seçin veya özel soru ekleyin.");
      if (box.querySelector("#saveChk").checked) {
        d.save_custom_as = box.querySelector("#saveName").value.trim();
        if (!d.save_custom_as) return toast("Kütüphaneye kaydetmek için set adı girin.");
      }
      try {
        const a = await api(`/api/suppliers/${sid}/audits`, { method: "POST", body: d });
        closeModal(); location.hash = `#/denetim/${a.id}`;
      } catch (err) { toast(err.message); }
    };
  });
}

// ================================================================ SORU SETLERİ
async function viewQuestionSets() {
  const sets = await api("/api/question-sets");
  const card = (x) => `<div class="card qs-card">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">
        <div><a href="#/soru-seti/${x.id}"><b>${esc(x.name)}</b></a> <span class="badge ${x.builtin ? "" : "b-ok"}">${esc(x.standard || "Özel")}</span></div>
        ${x.builtin ? `<span class="small muted">Standart</span>` : ""}</div>
      <div class="small muted" style="margin:6px 0">${esc(x.description || "")}</div>
      <div class="small">${x.sections.length} bölüm · <b>${x.question_count}</b> soru${x.critical_count ? ` · <span style="color:var(--crit)">${x.critical_count} kritik</span>` : ""}${x.conditional_count ? ` · ${x.conditional_count} koşullu` : ""}${x.used_in ? ` · ${x.used_in} denetimde kullanıldı` : ""}</div>
      <div class="actions" style="margin-top:10px"><a class="btn sm" href="#/soru-seti/${x.id}">${x.builtin ? "Görüntüle" : "Düzenle"}</a>
        <button class="sm" data-clone="${x.id}" data-perm="template:write">Kopyala</button></div></div>`;
  $app.innerHTML = `<div class="page-head"><div><h1>Soru Setleri</h1><div class="muted">Denetimlerde kullanılacak standart ve özel soru setleri</div></div>
    <div class="actions"><a class="btn primary" href="#/soru-seti/yeni" data-perm="template:write">+ Yeni özel set</a></div></div>
    <h2>Standart setler</h2><div class="qs-grid">${sets.filter((x) => x.builtin).map(card).join("")}</div>
    <h2 style="margin-top:8px">Özel setlerim</h2>
    ${sets.some((x) => !x.builtin) ? `<div class="qs-grid">${sets.filter((x) => !x.builtin).map(card).join("")}</div>`
      : `<div class="card empty">Henüz özel set yok. Sıfırdan oluşturabilir veya bir standart seti kopyalayıp kendinize göre düzenleyebilirsiniz.</div>`}
    <div class="card small muted">Standart setlerdeki sorular ilgili standardın maddelerine göre hazırlanmış özet denetim sorularıdır (madde numaraları parantez içinde); standardın resmi metni yerine geçmez. Standart setler değiştirilemez, kopyalayarak uyarlayabilirsiniz. Bir set sonradan değişse de, daha önce planlanmış denetimler kendi soru kopyalarını korur.</div>`;
  $app.querySelectorAll("[data-clone]").forEach((b) => (b.onclick = async () => {
    const c = await api(`/api/question-sets/${b.dataset.clone}/clone`, { method: "POST" });
    toast("Kopya oluşturuldu, düzenleyebilirsiniz"); location.hash = `#/soru-seti/${c.id}`;
  }));
}

function templateEditor(root, sections) {
  let seq = 0;
  const newCode = () => `n${Date.now().toString(36)}${seq++}`;
  const data = (sections.length ? sections : [{ title: "", weight: 10, questions: [{ text: "" }] }]).map((sec) => ({
    title: sec.title, weight: sec.weight,
    questions: sec.questions.map((q) => ({ code: q.id || q.code || newCode(), text: q.text, weight: q.weight || 1, critical: !!q.critical, condition: q.condition || null })),
  }));
  const ANS = ["uygun", "kısmen", "uygun değil", "na"];
  const allQs = () => data.flatMap((s) => s.questions);
  const draw = () => {
    root.innerHTML = data.map((sec, i) => `<div class="sb-sec" data-s="${i}">
      <div class="sb-head"><input class="sb-title" placeholder="Bölüm adı" value="${esc(sec.title)}">
        <label class="sb-w">Bölüm ağırlığı <input class="sb-weight" type="number" min="1" value="${esc(sec.weight)}"></label>
        <button type="button" class="sm danger" data-delsec="${i}" title="Bölümü sil">✕</button></div>
      ${sec.questions.map((q, j) => {
        const simple = !q.condition || (q.condition.q && ["in", "eq"].includes(q.condition.op || "eq"));
        const sel = !q.condition ? [] : q.condition.op === "in" ? q.condition.value : [q.condition.value];
        return `<div class="te-q" data-s="${i}" data-j="${j}">
          <div class="te-row"><input class="te-text" placeholder="Soru metni" value="${esc(q.text)}">
            <label class="te-small" title="Soru ağırlığı">× <input class="te-weight" type="number" min="0.1" step="0.5" value="${esc(q.weight)}"></label>
            <label class="te-small te-crit" title="Uygun değil = denetim reddedilir"><input type="checkbox" class="te-critical" ${q.critical ? "checked" : ""}> kritik</label>
            <button type="button" class="sm danger" data-delq="${i}.${j}">✕</button></div>
          <div class="te-row small">${simple ? `<span class="muted">Görünürlük:</span>
            <select class="te-parent"><option value="">Her zaman göster</option>${allQs().filter((p) => p.code !== q.code).map((p) => `<option value="${esc(p.code)}" ${q.condition?.q === p.code ? "selected" : ""}>${esc((p.text || "(adsız soru)").slice(0, 70))}</option>`).join("")}</select>
            <span class="te-ans ${q.condition ? "" : "hidden"}">cevabı ${ANS.map((v) => `<label><input type="checkbox" value="${v}" ${sel.includes(v) ? "checked" : ""}> ${v}</label>`).join(" ")} ise</span>`
            : `<span class="muted">Gelişmiş koşul (API ile tanımlanmış, korunur)</span> <button type="button" class="sm" data-clrcond="${i}.${j}">Koşulu kaldır</button>`}</div></div>`;
      }).join("")}
      <button type="button" class="sm" data-addq="${i}">+ Soru</button></div>`).join("") + `<button type="button" class="sm" id="te-addsec">+ Bölüm ekle</button>`;
    root.querySelectorAll(".sb-sec").forEach((el) => {
      const sec = data[+el.dataset.s];
      el.querySelector(".sb-title").oninput = (e) => (sec.title = e.target.value);
      el.querySelector(".sb-weight").oninput = (e) => (sec.weight = +e.target.value || 1);
    });
    root.querySelectorAll(".te-q").forEach((el) => {
      const q = data[+el.dataset.s].questions[+el.dataset.j];
      el.querySelector(".te-text").oninput = (e) => {
        q.text = e.target.value; // koşul listelerindeki metni yerinde güncelle (yeniden çizmeden; odak kaybolmasın)
        root.querySelectorAll(`.te-parent option[value="${CSS.escape(q.code)}"]`).forEach((o) => (o.textContent = (q.text || "(adsız soru)").slice(0, 70)));
      };
      el.querySelector(".te-weight").oninput = (e) => (q.weight = +e.target.value || 1);
      el.querySelector(".te-critical").onchange = (e) => (q.critical = e.target.checked);
      const par = el.querySelector(".te-parent");
      if (par) {
        const sync = () => {
          const vals = [...el.querySelectorAll(".te-ans input:checked")].map((c) => c.value);
          q.condition = par.value ? { q: par.value, op: "in", value: vals.length ? vals : ["uygun değil"] } : null;
          if (par.value && !vals.length) el.querySelector('.te-ans input[value="uygun değil"]').checked = true;
          el.querySelector(".te-ans").classList.toggle("hidden", !par.value);
        };
        par.onchange = sync;
        el.querySelectorAll(".te-ans input").forEach((c) => (c.onchange = sync));
      }
    });
    root.querySelectorAll("[data-addq]").forEach((b) => (b.onclick = () => { data[+b.dataset.addq].questions.push({ code: newCode(), text: "", weight: 1, critical: false, condition: null }); draw(); }));
    root.querySelectorAll("[data-delq]").forEach((b) => (b.onclick = () => {
      const [i, j] = b.dataset.delq.split(".").map(Number);
      const code = data[i].questions[j].code;
      data[i].questions.splice(j, 1);
      allQs().forEach((q) => { if (q.condition && JSON.stringify(q.condition).includes(`"${code}"`)) q.condition = null; });
      draw();
    }));
    root.querySelectorAll("[data-clrcond]").forEach((b) => (b.onclick = () => { const [i, j] = b.dataset.clrcond.split(".").map(Number); data[i].questions[j].condition = null; draw(); }));
    root.querySelectorAll("[data-delsec]").forEach((b) => (b.onclick = () => { if (confirm("Bölüm ve soruları silinsin mi?")) { data.splice(+b.dataset.delsec, 1); draw(); } }));
    root.querySelector("#te-addsec").onclick = () => { data.push({ title: "", weight: 10, questions: [{ code: newCode(), text: "", weight: 1, critical: false, condition: null }] }); draw(); };
  };
  draw();
  return {
    get: () => data.map((s) => ({ ...s, questions: s.questions.filter((q) => q.text.trim()) })).filter((s) => s.title.trim() || s.questions.length),
    validate() {
      for (const [i, x] of this.get().entries()) {
        if (!x.title.trim()) return `${i + 1}. bölümün adı boş.`;
        if (!x.questions.length) return `"${x.title}" bölümünde soru yok.`;
      }
      const codes = new Set(this.get().flatMap((s) => s.questions.map((q) => q.code)));
      for (const q of this.get().flatMap((s) => s.questions)) if (q.condition?.q && !codes.has(q.condition.q)) return `"${q.text.slice(0, 40)}" sorusunun bağlı olduğu soru silinmiş.`;
      return null;
    },
  };
}

async function viewQuestionSet(id) {
  const isNew = id === "yeni";
  const x = isNew ? { name: "", standard: "Özel", description: "", sections: [], builtin: false } : await api(`/api/question-sets/${id}`);
  const head = `<div class="small"><a href="#/soru-setleri">← Soru setleri</a></div>`;
  if (x.builtin) {
    $app.innerHTML = `<div class="page-head"><div>${head}<h1>${esc(x.name)}</h1><div class="muted">${esc(x.description || "")}</div></div>
      <div class="actions"><button class="primary" id="clone">Kopyala ve düzenle</button></div></div>
      ${x.sections.map((sec) => `<div class="card"><h2>${esc(sec.title)} <span class="muted small">· ağırlık ${num(sec.weight)}</span></h2>
        <ol style="margin:0;padding-left:20px">${sec.questions.map((q) => `<li style="margin-bottom:4px">${esc(q.text)}
          ${q.critical ? `<span class="badge b-crit">kritik</span>` : ""}${(q.weight || 1) != 1 ? ` <span class="badge">×${num(q.weight)}</span>` : ""}
          ${q.condition ? `<div class="small muted">↳ ${esc(condText(q.condition, Object.fromEntries(x.sections.flatMap((s2) => s2.questions.map((y) => [y.id, y.text])))))}</div>` : ""}</li>`).join("")}</ol></div>`).join("")}`;
    document.getElementById("clone").onclick = async () => {
      const c = await api(`/api/question-sets/${x.id}/clone`, { method: "POST" }); location.hash = `#/soru-seti/${c.id}`;
    };
    return;
  }
  $app.innerHTML = `<div class="page-head"><div>${head}<h1>${isNew ? "Yeni özel soru seti" : "Soru setini düzenle"}</h1></div>
    <div class="actions" data-perm="template:write">${isNew ? "" : `<button class="danger" id="del">Sil</button>`}<button class="primary" id="save">Kaydet</button></div></div>
    <div class="card"><div class="row" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">
      <div><label>Set adı *</label><input id="n" value="${esc(x.name)}" placeholder="ör. Depo ve Lojistik Denetimi"></div>
      <div><label>Standart / etiket</label><input id="st" value="${esc(x.standard || "Özel")}" placeholder="ör. Özel, TISAX, SOC 2"></div></div>
      <div style="margin-top:12px"><label>Açıklama</label><textarea id="ds" style="min-height:50px">${esc(x.description || "")}</textarea></div></div>
    <div class="card"><h2>Bölümler ve sorular</h2><div class="small muted" style="margin-bottom:10px">
      <b>Bölüm ağırlığı</b> bölümün toplam puana etkisini, <b>soru ağırlığı</b> sorunun bölüm içindeki etkisini belirler.
      <b>Kritik</b> işaretli soruda "Uygun değil" cevabı denetimi puandan bağımsız reddeder (kill-switch).
      <b>Koşul</b> ile bir soruyu yalnızca başka bir sorunun belirli cevaplarında gösterebilirsiniz (ör. sertifika yoksa alternatif kanıt sorusu).</div><div id="sb"></div></div>`;
  const sb = templateEditor(document.getElementById("sb"), x.sections);
  document.getElementById("save").onclick = async () => {
    const body = { name: document.getElementById("n").value.trim(), standard: document.getElementById("st").value.trim() || "Özel",
      description: document.getElementById("ds").value.trim() || null, sections: sb.get() };
    if (!body.name) return toast("Set adı gerekli.");
    const err = sb.validate(); if (err) return toast(err);
    if (!body.sections.length) return toast("En az bir bölüm ve soru ekleyin.");
    try {
      const r = await api(isNew ? "/api/question-sets" : `/api/question-sets/${x.id}`, { method: isNew ? "POST" : "PUT", body });
      toast("Kaydedildi"); if (isNew) location.hash = `#/soru-seti/${r.id}`;
    } catch (e) { toast(e.message); }
  };
  const del = document.getElementById("del");
  if (del) del.onclick = async () => {
    if (!confirm("Bu soru seti silinsin mi? (Bu setle yapılmış denetimler etkilenmez.)")) return;
    await api(`/api/question-sets/${x.id}`, { method: "DELETE" }); location.hash = "#/soru-setleri";
  };
}

// ---------------------------------------------------------------- denetim kuralları (app/templates_engine.py ile aynı)
const ENG = {
  pts: { uygun: 1, kısmen: 0.5, "uygun değil": 0 },
  evalCond(c, ans) {
    if (!c) return true;
    if (c.all) return c.all.every((x) => ENG.evalCond(x, ans));
    if (c.any) return c.any.some((x) => ENG.evalCond(x, ans));
    if (c.not) return !ENG.evalCond(c.not, ans);
    const a = ans[c.q], op = c.op || "eq";
    if (op === "answered") return a != null;
    if (a == null) return false;
    if (op === "eq") return a === c.value;
    if (op === "neq") return a !== c.value;
    if (op === "in") return (c.value || []).includes(a);
    return false;
  },
  visible(sections, answers) {
    const qs = sections.flatMap((s) => s.questions);
    let vis = new Set(qs.map((q) => q.id));
    for (let i = 0; i <= qs.length; i++) {
      const eff = {}; for (const [k, v] of Object.entries(answers)) if (vis.has(k) && v) eff[k] = v;
      const nxt = new Set(qs.filter((q) => ENG.evalCond(q.condition, eff)).map((q) => q.id));
      if (nxt.size === vis.size && [...nxt].every((x) => vis.has(x))) break;
      vis = nxt;
    }
    return vis;
  },
  score(sections, answers) {
    const vis = ENG.visible(sections, answers);
    let tot = 0, tw = 0, answered = 0, total = 0; const kills = [];
    const secs = sections.map((sec) => {
      let n = 0, d = 0;
      for (const q of sec.questions) {
        if (!vis.has(q.id)) continue;
        total++; const a = answers[q.id];
        if (a) answered++;
        if (q.critical && a === "uygun değil") kills.push(q);
        if (a in ENG.pts) { const w = +(q.weight || 1); n += ENG.pts[a] * w; d += w; }
      }
      const pct = d ? (100 * n) / d : null;
      if (pct != null) { tot += pct * (+sec.weight || 1); tw += +sec.weight || 1; }
      return { title: sec.title, pct };
    });
    const score = tw ? Math.round((tot / tw) * 10) / 10 : null;
    let decision = score == null ? null : score >= 85 ? "approved" : score >= 70 ? "conditional" : score >= 50 ? "improvement_required" : "rejected";
    if (kills.length) decision = "rejected";
    return { score, decision, kills, secs, answered, total, vis };
  },
};
const DECISION = { approved: ["b-ok", "Onaylı"], conditional: ["b-warn", "Şartlı Onaylı"], improvement_required: ["b-bad", "Gelişim Gerekli"], rejected: ["b-crit", "Reddedildi"] };
const decisionBadge = (d) => (d ? `<span class="badge ${DECISION[d][0]}">${DECISION[d][1]}</span>` : "");

// Alt soruları üst sorularının hemen altına diz (görünüm sırası)
function displayOrder(questions) {
  const ids = new Set(questions.map((q) => q.id)), kids = {}, roots = [];
  for (const q of questions) {
    const p = q.condition?.q;
    if (p && ids.has(p) && p !== q.id) (kids[p] ||= []).push(q); else roots.push(q);
  }
  const out = [], seen = new Set();
  const walk = (q, depth) => { if (seen.has(q.id)) return; seen.add(q.id); out.push([q, depth]); (kids[q.id] || []).forEach((k) => walk(k, depth + 1)); };
  roots.forEach((q) => walk(q, 0));
  questions.forEach((q) => walk(q, 1)); // döngü/eksik ebeveyn güvenliği
  return out;
}
const condText = (c, qtext) => {
  if (!c) return "";
  if (c.all || c.any) return "birden çok koşul";
  if (c.not) return "koşul";
  const t = (qtext[c.q] || "önceki soru").slice(0, 50);
  const v = c.op === "answered" ? "cevaplanırsa" : c.op === "in" ? `"${c.value.join(" / ")}" ise` : c.op === "neq" ? `"${c.value}" değilse` : `"${c.value}" ise`;
  return `"${t}…" ${v} görünür`;
};

async function viewAudit(id) {
  const a = await api(`/api/audits/${id}`);
  const answers = Object.fromEntries(Object.entries(a.answers).map(([k, v]) => [k, v.answer]));
  const notes = Object.fromEntries(Object.entries(a.answers).map(([k, v]) => [k, v.note || ""]));
  const ro = !can("audit:write");
  const qtext = Object.fromEntries(a.checklist.flatMap((s) => s.questions.map((q) => [q.id, q.text])));
  const opts = [["uygun", "Uygun"], ["kısmen", "Kısmen"], ["uygun değil", "Uygun değil"], ["na", "N/A"]];
  $app.innerHTML = `
  <div class="page-head"><div><div class="small"><a href="#/tedarikci/${a.supplier_id}/denetimler">← ${esc(a.supplier_name)}</a></div>
    <h1>Denetim — ${fmtDate(a.audit_date)}</h1><div class="muted">${esc(a.audit_type)} · Denetçi: ${esc(a.auditor || "—")} · ${statusBadge(a.status)}</div>
    <div class="chips" style="margin-top:6px">${(a.set_names || []).map((n) => `<span class="badge">${esc(n)}</span>`).join("")}</div></div>
    <div class="actions"><a class="btn" data-perm="report:read" href="/api/reports/audits/${a.id}.pdf" target="_blank">PDF</a><a class="btn" data-perm="report:read" href="/api/reports/audits/${a.id}.csv">CSV</a>
      <span class="actions" data-perm="audit:write"><button class="danger" id="del">Sil</button><button id="gen">Bulguları oluştur</button><button class="primary" id="save">Kaydet</button></span></div></div>
  <div class="small muted" style="margin:-8px 0 12px"><span class="badge b-crit">kritik</span> işaretli maddede "Uygun değil" cevabı, puandan bağımsız olarak denetimi <b>reddeder</b> (kill-switch). ↳ işaretli sorular yalnızca bağlı oldukları sorunun cevabına göre açılır.</div>
  <div class="grid" style="grid-template-columns:minmax(0,1fr) 300px;align-items:start" id="lay">
    <div>${a.checklist.map((sec) => `<div class="card"><h2>${esc(sec.title)} <span class="muted small">· ağırlık ${num(sec.weight)}${(a.set_names || []).length > 1 && sec.source ? " · " + esc(sec.source) : ""}</span></h2>
      ${displayOrder(sec.questions).map(([q, depth]) => `<div class="q ${depth ? "q-child" : ""}" data-q="${esc(q.id)}" style="${depth ? `margin-left:${Math.min(depth, 3) * 18}px` : ""}">
        <div>${depth ? `<span class="muted">↳ </span>` : ""}${esc(q.text)} ${q.critical ? `<span class="badge b-crit" title="Uygun değil = denetim reddedilir">kritik</span>` : ""}${(q.weight || 1) != 1 ? ` <span class="badge" title="Soru ağırlığı">×${num(q.weight)}</span>` : ""}
          ${q.condition ? `<div class="small muted">${esc(condText(q.condition, qtext))}</div>` : ""}</div>
        <div class="seg">${opts.map(([v, l]) => `<button type="button" data-v="${v}" class="${answers[q.id] === v ? "on" : ""}" ${ro ? "disabled" : ""}>${l}</button>`).join("")}</div>
        <input class="note" placeholder="Not / kanıt" value="${esc(notes[q.id] || "")}" ${ro ? "readonly" : ""}>
        <div class="ev-slot" data-ev="${esc(q.id)}"></div></div>`).join("")}</div>`).join("")}</div>
    <div style="position:sticky;top:72px;max-height:calc(100vh - 88px);overflow-y:auto">
      <div class="card"><h2>Sonuç</h2><div id="res"></div></div>
      <div class="card"><label>Durum</label><select id="st" ${ro ? "disabled" : ""}>${["planlandı", "devam ediyor", "tamamlandı"].map((s) => `<option ${s === a.status ? "selected" : ""}>${s}</option>`).join("")}</select>
        <label style="margin-top:10px">Genel notlar</label><textarea id="nt" ${ro ? "readonly" : ""}>${esc(a.notes || "")}</textarea>
        <div class="small muted" style="margin-top:8px">Denetim <b>tamamlandı</b> olarak kaydedildiğinde uygunsuzluklar için CAPA kayıtları otomatik açılır.</div></div>
      <div id="sidecards"></div>
      ${a.findings.length ? `<div class="card"><h2>Bu denetimden bulgular</h2>${a.findings.map((f) => `<div class="small" style="margin-bottom:6px">${sevBadge(f.severity)} ${statusBadge(f.status)} ${esc(f.title)}</div>`).join("")}</div>` : ""}
    </div></div>`;
  if (window.innerWidth < 900) document.getElementById("lay").style.gridTemplateColumns = "1fr";

  const calc = () => {
    const r = ENG.score(a.checklist, answers);
    $app.querySelectorAll(".q[data-q]").forEach((el) => el.classList.toggle("hidden", !r.vis.has(el.dataset.q)));
    document.getElementById("res").innerHTML = `<div style="font-size:36px;font-weight:800;color:${r.kills.length ? "var(--crit)" : scoreColor(r.score)}">${num(r.score)}</div>
      <div>${decisionBadge(r.decision)}</div>
      ${r.kills.length ? `<div class="small" style="margin-top:8px;color:var(--crit)"><b>Kill-switch:</b> ${r.kills.length} kritik madde uygun değil — puandan bağımsız olarak reddedildi.<ul style="margin:4px 0 0;padding-left:18px">${r.kills.map((q) => `<li>${esc(q.text)}</li>`).join("")}</ul></div>` : ""}
      <div class="small muted" style="margin:6px 0 10px">${r.answered}/${r.total} görünür soru cevaplandı</div>
      ${r.secs.map((x) => `<div class="small" style="margin-bottom:6px">${esc(x.title)}${meter(x.pct == null ? null : Math.round(x.pct))}</div>`).join("")}`;
  };
  calc();

  $app.querySelectorAll(".q[data-q]").forEach((el) => {
    const q = el.dataset.q;
    el.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => {
      const v = answers[q] === b.dataset.v ? null : b.dataset.v;
      answers[q] = v;
      el.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x.dataset.v === v));
      calc();
    }));
    el.querySelector(".note").oninput = (e) => (notes[q] = e.target.value);
  });
  if (typeof auditExtras === "function") auditExtras(a);
  if (ro) return;

  const save = async () => {
    const body = Object.fromEntries([...new Set([...Object.keys(answers), ...Object.keys(notes)])].map((k) => [k, { answer: answers[k] || null, note: notes[k] || null }]));
    return api(`/api/audits/${id}`, { method: "PUT", body: { status: document.getElementById("st").value, notes: document.getElementById("nt").value, answers: body } });
  };
  document.getElementById("save").onclick = async () => {
    try { const r = await save(); toast(r.capa_created ? `Denetim kaydedildi. ${r.capa_created} CAPA kaydı açıldı.` : "Denetim kaydedildi"); if (r.capa_created) router(); } catch (e) { toast(e.message); }
  };
  document.getElementById("gen").onclick = async () => {
    try { await save(); const r = await api(`/api/audits/${id}/generate-findings`, { method: "POST" }); toast(`${r.created} bulgu oluşturuldu`); router(); }
    catch (e) { toast(e.message); }
  };
  document.getElementById("del").onclick = async () => {
    if (!confirm("Bu denetim silinsin mi?")) return;
    await api(`/api/audits/${id}`, { method: "DELETE" }); location.hash = `#/tedarikci/${a.supplier_id}/denetimler`;
  };
}

// ================================================================ BULGULAR
const supStatusBadge = (f) => (f.supplier_status ? `<span class="badge ${{ okundu: "", bildirildi: "", "aksiyon planlandı": "b-warn", giderildi: "b-ok", itiraz: "b-bad" }[f.supplier_status] ?? ""}" title="${esc([f.supplier_comment, f.supplier_due_date ? "Plan: " + fmtDate(f.supplier_due_date) : ""].filter(Boolean).join(" — "))}">${esc(f.supplier_status)}</span>${f.supplier_due_date ? `<div class="small muted">plan: ${fmtDate(f.supplier_due_date)}</div>` : ""}` : `<span class="muted small">—</span>`);

function findingsTable(list, { compact = false, withSupplier = false, selectable = false } = {}) {
  if (!list.length) return `<div class="empty">Bulgu yok.</div>`;
  return `<div class="table-wrap"><table><thead><tr>${selectable ? `<th><input type="checkbox" class="fsel-all" title="Tümünü seç" style="width:auto"></th>` : ""}<th>Önem</th>${withSupplier ? "<th>Tedarikçi</th>" : ""}<th>Bulgu</th>${compact ? "" : "<th>Kaynak</th><th>Sorumlu</th>"}<th>Termin</th><th>Durum</th>${compact ? "" : "<th>Tedarikçi yanıtı</th><th></th>"}</tr></thead><tbody>
  ${list.map((f) => `<tr>${selectable ? `<td><input type="checkbox" class="fsel" value="${f.id}" data-sid="${f.supplier_id}" style="width:auto"></td>` : ""}<td>${sevBadge(f.severity)}</td>${withSupplier ? `<td><a href="#/tedarikci/${f.supplier_id}/bulgular">${esc(f.supplier_name)}</a></td>` : ""}
    <td><b>${esc(f.title)}</b>${!compact && f.action ? `<div class="small" style="color:var(--primary)">Aksiyon: ${esc(f.action)}</div>` : ""}${!compact && f.description ? `<div class="small muted" style="white-space:pre-wrap">${esc(f.description)}</div>` : ""}${!compact && f.supplier_comment ? `<div class="small" style="margin-top:4px"><b>Tedarikçi:</b> ${esc(f.supplier_comment)}</div>` : ""}</td>
    ${compact ? "" : `<td><span class="badge">${esc(f.source)}</span></td><td>${esc(f.owner || "—")}</td>`}
    <td class="small" style="${overdue(f) ? "color:var(--bad);font-weight:700" : ""}">${fmtDate(f.due_date)}${overdue(f) ? "<br>gecikti" : ""}</td>
    <td>${compact ? statusBadge(f.status) : `<select data-fid="${f.id}" class="fstatus" style="width:auto" ${can("finding:write") ? "" : "disabled"}>${["açık", "devam ediyor", "kapandı"].map((s) => `<option ${s === f.status ? "selected" : ""}>${s}</option>`).join("")}</select>`}</td>
    ${compact ? "" : `<td>${supStatusBadge(f)}</td><td style="white-space:nowrap"><button class="sm" data-perm="notify:send" data-notify="${f.id}" data-sid="${f.supplier_id}" title="Tedarikçiye bildir">Bildir</button> <button class="sm" data-perm="capa:write" data-capa="${f.id}" data-sid="${f.supplier_id}" title="Bu bulgu için CAPA aç">CAPA</button> <button class="sm" data-perm="finding:write" data-edit="${f.id}">Düzenle</button></td>`}</tr>`).join("")}</tbody></table></div>`;
}

function bindFindingActions(root, list) {
  root.querySelectorAll(".fstatus").forEach((sel) => (sel.onchange = async () => {
    try { await api(`/api/findings/${sel.dataset.fid}`, { method: "PUT", body: { status: sel.value } }); toast("Durum güncellendi"); router(); }
    catch (e) { toast(e.message); }
  }));
  root.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = async () => {
    const all = list || (await api("/api/findings"));
    findingForm(all.find((f) => f.id == b.dataset.edit));
  }));
  root.querySelectorAll("[data-notify]").forEach((b) => (b.onclick = () =>
    notifyModal({ supplier_id: +b.dataset.sid, finding_ids: [+b.dataset.notify] })));
  root.querySelectorAll("[data-capa]").forEach((b) => (b.onclick = () => capaForm(+b.dataset.sid, +b.dataset.capa)));
  const all = root.querySelector(".fsel-all");
  if (all) all.onchange = () => root.querySelectorAll(".fsel").forEach((c) => (c.checked = all.checked));
}
const selectedFindings = (root) => [...root.querySelectorAll(".fsel:checked")].map((c) => ({ id: +c.value, sid: +c.dataset.sid }));

function findingForm(f) {
  const sel = (name, opts, val) => `<select name="${name}">${opts.map((o) => `<option ${o === val ? "selected" : ""}>${o}</option>`).join("")}</select>`;
  modal(`<h2>${f.id ? "Bulguyu düzenle" : "Yeni bulgu"}</h2><form id="f">
    <div style="margin-bottom:12px"><label>Başlık *</label><input name="title" required value="${esc(f.title || "")}"></div>
    <div class="row"><div><label>Önem</label>${sel("severity", ["kritik", "yüksek", "orta", "düşük"], f.severity || "orta")}</div>
      <div><label>Durum</label>${sel("status", ["açık", "devam ediyor", "kapandı"], f.status || "açık")}</div>
      <div><label>Termin</label><input type="date" name="due_date" value="${esc(f.due_date || "")}"></div></div>
    <div style="margin-bottom:12px"><label>Açıklama</label><textarea name="description">${esc(f.description || "")}</textarea></div>
    <div style="margin-bottom:12px"><label>Düzeltici / önleyici faaliyet</label><textarea name="action">${esc(f.action || "")}</textarea></div>
    <div style="margin-bottom:12px"><label>Sorumlu</label><input name="owner" value="${esc(f.owner || "")}"></div>
    ${f.supplier_status ? `<div class="card" style="background:var(--bg)"><label>Tedarikçi yanıtı</label>${supStatusBadge(f)}
      ${f.supplier_comment ? `<div style="white-space:pre-wrap;margin-top:6px">${esc(f.supplier_comment)}</div>` : ""}
      <div class="small muted" style="margin-top:6px">${f.notified_at ? "Bildirildi: " + fmtDT(f.notified_at) : ""}${f.supplier_updated_at ? " · Son yanıt: " + fmtDT(f.supplier_updated_at) : ""}</div></div>` : ""}
    <div class="actions" style="justify-content:space-between">${f.id ? `<button type="button" class="danger" id="fdel">Sil</button>` : "<span></span>"}
      <div class="actions"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Kaydet</button></div></div></form>`, (box) => {
    box.querySelector("#f").onsubmit = async (e) => {
      e.preventDefault();
      const d = formData(e.target);
      try {
        if (f.id) await api(`/api/findings/${f.id}`, { method: "PUT", body: d });
        else await api("/api/findings", { method: "POST", body: { ...d, supplier_id: f.supplier_id } });
        closeModal(); toast("Kaydedildi"); router();
      } catch (err) { toast(err.message); }
    };
    const del = box.querySelector("#fdel");
    if (del) del.onclick = async () => { if (confirm("Bulgu silinsin mi?")) { await api(`/api/findings/${f.id}`, { method: "DELETE" }); closeModal(); router(); } };
  });
}

async function viewFindings() {
  const p = new URLSearchParams(location.hash.split("?")[1] || "");
  const state = { status: p.get("status") ?? "açık-tümü", source: "", sev: "", q: "" };
  $app.innerHTML = `<div class="page-head"><div><h1>Bulgular &amp; Aksiyonlar</h1><div class="muted">Uygunsuzluklar ve düzeltici faaliyet (DÖF) takibi</div></div>
    <div class="actions"><button id="nsel" data-perm="notify:send">Seçilenleri tedarikçiye bildir</button><button id="csv">CSV indir</button></div></div>
    <div class="card"><div class="filters">
      <select id="fs"><option value="açık-tümü">Açık + devam eden</option><option value="">Tümü</option><option>açık</option><option>devam ediyor</option><option>kapandı</option></select>
      <select id="fsrc"><option value="">Tüm kaynaklar</option><option>denetim</option><option>istihbarat</option><option>manuel</option></select>
      <select id="fsev"><option value="">Tüm önemler</option><option>kritik</option><option>yüksek</option><option>orta</option><option>düşük</option></select>
      <label style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" id="fod" style="width:auto"> Yalnızca gecikenler</label>
      <input id="fq" placeholder="Ara…"></div><div id="ft"></div></div>`;
  let list = [];
  const draw = () => {
    const q = document.getElementById("fq").value.toLowerCase();
    const od = document.getElementById("fod").checked;
    const sev = document.getElementById("fsev").value;
    const rows = list.filter((f) => (!sev || f.severity === sev) && (!od || overdue(f)) &&
      (!q || [f.title, f.supplier_name, f.owner, f.description].join(" ").toLowerCase().includes(q)));
    document.getElementById("ft").innerHTML = findingsTable(rows, { withSupplier: true, selectable: true });
    bindFindingActions(document.getElementById("ft"), list);
    draw.rows = rows;
  };
  const load = async () => {
    const qs = new URLSearchParams();
    const st = document.getElementById("fs").value, src = document.getElementById("fsrc").value;
    if (st) qs.set("status", st);
    if (src) qs.set("source", src);
    list = await api(`/api/findings?${qs}`);
    draw();
  };
  document.getElementById("fs").value = state.status;
  ["fs", "fsrc"].forEach((i) => (document.getElementById(i).onchange = load));
  ["fsev", "fod"].forEach((i) => (document.getElementById(i).onchange = draw));
  document.getElementById("fq").oninput = draw;
  document.getElementById("nsel").onclick = () => {
    const sel = selectedFindings(document.getElementById("ft"));
    if (!sel.length) return toast("Önce tablodan bulgu seçin.");
    const sids = [...new Set(sel.map((x) => x.sid))];
    if (sids.length > 1) return toast("Bildirim tedarikçi bazında gönderilir; tek bir tedarikçinin bulgularını seçin.");
    notifyModal({ supplier_id: sids[0], finding_ids: sel.map((x) => x.id) });
  };
  document.getElementById("csv").onclick = () => {
    const cols = ["id", "supplier_name", "severity", "title", "source", "status", "owner", "due_date", "action", "description", "supplier_status", "supplier_comment", "supplier_due_date", "notified_at", "created_at", "closed_at"];
    const csv = [cols.join(";"), ...(draw.rows || []).map((f) => cols.map((c) => `"${String(f[c] ?? "").replace(/"/g, '""')}"`).join(";"))].join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
    a.download = `bulgular-${todayISO()}.csv`; a.click();
  };
  await load();
}

// ================================================================ DIŞ İSTİHBARAT
async function viewIntel() {
  const sups = await api("/api/suppliers");
  const withD = sups.filter((s) => s.domains.length && s.status !== "pasif");
  $app.innerHTML = `<div class="page-head"><div><h1>Dış İstihbarat</h1><div class="muted">Tedarikçi domain'lerinin dışarıdan görünen siber güvenlik duruşu</div></div>
    <div class="actions"><button class="primary" id="all" ${withD.length ? "" : "disabled"}>Tüm tedarikçileri tara (${withD.length})</button></div></div>
  <div class="card"><h2>Hızlı tarama (kaydetmeden)</h2><div class="muted small" style="margin-bottom:8px">Aday tedarikçi ön değerlendirmesi için herhangi bir domain'i tarayın.</div>
    <form id="qf" class="filters"><input name="domain" placeholder="ornekfirma.com" required style="min-width:280px"><button class="primary">Tara</button></form><div id="qr"></div></div>
  <div class="card"><h2>Tedarikçi siber güvenlik durumu</h2><div class="table-wrap"><table><thead><tr><th>Tedarikçi</th><th>Domain'ler</th><th>Not</th><th>En düşük puan</th><th>Son tarama</th><th></th></tr></thead><tbody>
    ${withD.length ? withD.map((s) => `<tr id="row-${s.id}"><td><a href="#/tedarikci/${s.id}/istihbarat">${esc(s.name)}</a></td>
      <td><div class="chips">${s.domains.map((d) => `<span class="chip">${esc(d)}</span>`).join("")}</div></td><td>${gradeBox(s.summary.intel_grade)}</td>
      <td style="min-width:140px">${meter(s.summary.intel_score)}</td><td class="small">${fmtDT(s.summary.last_scan)}</td><td class="st small"></td></tr>`).join("")
      : `<tr><td colspan="6" class="empty">Domain tanımlı tedarikçi yok.</td></tr>`}</tbody></table></div></div>
  <div class="card small muted"><b>Yöntem:</b> Tüm kontroller pasif ve herkese açık kaynaklara dayanır; port taraması, zafiyet istismarı veya kimlik doğrulama denemesi yapılmaz.
    Not hesaplaması: her kontrol ağırlıklıdır (geçti = tam puan, uyarı = yarım, başarısız = 0; bilgi/ölçülemedi puanlanmaz). A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, F &lt; 60.
    Tedarikçinin notu, domain'leri arasındaki en düşük puana göre belirlenir.</div>`;

  document.getElementById("qf").onsubmit = async (e) => {
    e.preventDefault();
    const d = e.target.domain.value, out = document.getElementById("qr");
    out.innerHTML = `<div class="muted"><span class="spinner"></span> ${esc(d)} taranıyor (10–30 sn)…</div>`;
    try { out.innerHTML = scanDetail(await api(`/api/quick-scan?domain=${encodeURIComponent(d)}`)); }
    catch (err) { out.innerHTML = `<div style="color:var(--bad)">${esc(err.message)}</div>`; }
  };
  document.getElementById("all").onclick = async (e) => {
    e.target.disabled = true;
    let created = 0;
    for (const s of withD) {
      const cell = document.querySelector(`#row-${s.id} .st`);
      cell.innerHTML = `<span class="spinner"></span>`;
      try { const r = await api(`/api/suppliers/${s.id}/scan`, { method: "POST" }); created += r.reduce((a, x) => a + x.findings_created, 0); cell.textContent = "✓ " + r.map((x) => x.grade || "?").join(", "); }
      catch (err) { cell.textContent = "Hata"; cell.title = err.message; }
    }
    toast(`Tarama bitti. ${created} yeni bulgu oluşturuldu.`);
    setTimeout(router, 1200);
  };
}

function scanDetail(r) {
  const cats = {};
  r.checks.forEach((c) => (cats[c.category] ||= []).push(c));
  const order = { fail: 0, warn: 1, error: 2, pass: 3, info: 4 };
  const subs = r.checks.find((c) => c.id === "osint.subdomains")?.data?.subdomains || [];
  return `<div class="card" style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">${gradeBox(r.grade, true)}
    <div><div style="font-size:18px;font-weight:700">${esc(r.domain)}${r.web_host !== r.domain ? ` <span class="muted small">(web: ${esc(r.web_host)})</span>` : ""}</div>
    <div class="muted">Puan: <b>${num(r.score)}</b> / 100 · ${fmtDT(r.scanned_at)} · ${r.duration_s} sn</div>
    <div class="small"><span style="color:var(--bad)">${r.summary.fail} başarısız</span> · <span style="color:var(--warn)">${r.summary.warn} uyarı</span> · <span style="color:var(--ok)">${r.summary.pass} geçti</span> · ${r.summary.info} bilgi · ${r.summary.error} ölçülemedi</div></div></div>
  ${Object.entries(cats).map(([cat, cs]) => `<div class="card"><h2>${esc(cat)}</h2>
    ${cs.sort((a, b) => order[a.status] - order[b.status]).map((c) => `<div class="check"><div>${checkBadge(c.status)}</div>
      <div><b>${esc(c.title)}</b>${c.weight ? ` <span class="muted small">· ağırlık ${c.weight}</span>` : ""}<div class="detail small">${esc(c.detail)}</div>
      ${c.recommendation ? `<div class="rec">→ ${esc(c.recommendation)}</div>` : ""}</div></div>`).join("")}</div>`).join("")}
  ${subs.length ? `<div class="card"><h2>Sertifika şeffaflığında görülen alt alan adları (${subs.length})</h2><div class="chips">${subs.slice(0, 300).map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div></div>` : ""}`;
}

async function viewScan(id) {
  const s = await api(`/api/scans/${id}`);
  $app.innerHTML = `<div class="page-head"><div><div class="small"><a href="#/tedarikci/${s.supplier_id}/istihbarat">← Tedarikçiye dön</a></div><h1>Tarama sonucu</h1></div>
    <div class="actions"><button class="primary" id="nscan" data-perm="notify:send">Tedarikçiye bildir</button></div></div>${scanDetail(s.result)}`;
  document.getElementById("nscan").onclick = async () => {
    const f = await api(`/api/findings?supplier_id=${s.supplier_id}&source=istihbarat&status=açık-tümü`);
    notifyModal({ supplier_id: s.supplier_id, scan_id: s.id, finding_ids: f.filter((x) => x.title.startsWith(`[${s.domain}]`)).map((x) => x.id) });
  };
}

// ================================================================ RAPOR
async function viewReport(id) {
  const s = await api(`/api/suppliers/${id}`);
  const sm = s.summary;
  const lastAudit = s.audits.find((a) => a.status === "tamamlandı");
  const ad = lastAudit ? await api(`/api/audits/${lastAudit.id}`) : null;
  const open = s.findings.filter((f) => f.status !== "kapandı");
  $app.innerHTML = `<div class="page-head no-print"><div class="small"><a href="#/tedarikci/${s.id}">← ${esc(s.name)}</a></div>
    <div class="actions"><button class="primary" onclick="window.print()">Yazdır / PDF</button></div></div>
  <div class="card"><div class="muted small">TEDARİKÇİ DEĞERLENDİRME RAPORU · ${new Date().toLocaleDateString("tr-TR")}</div>
    <h1 style="margin-top:4px">${esc(s.name)}</h1><div class="muted">${esc([s.category, s.country].filter(Boolean).join(" · "))} · Kritiklik: ${esc(s.criticality)}</div>
    <div class="kpis" style="margin-top:16px">
      <div class="kpi"><div class="l">Risk seviyesi</div><div class="v">${riskBadge(sm.risk)}</div></div>
      <div class="kpi"><div class="l">Genel puan</div><div class="v">${num(sm.overall_score)}</div></div>
      <div class="kpi"><div class="l">Denetim puanı</div><div class="v">${num(sm.audit_score)}</div><div class="l">${ratingBadge(sm.audit_rating)}</div></div>
      <div class="kpi"><div class="l">Siber güvenlik</div><div class="v">${gradeBox(sm.intel_grade)} ${num(sm.intel_score)}</div></div>
      <div class="kpi"><div class="l">Açık bulgu</div><div class="v">${sm.open_findings}</div><div class="l">${sm.critical_findings} kritik · ${sm.overdue_findings} gecikmiş</div></div></div>
    <div class="small muted">Genel puan = denetim puanı × %60 + siber güvenlik puanı × %40 (mevcut olanlar üzerinden).</div></div>
  ${ad ? `<div class="card"><h2>Son denetim — ${fmtDate(ad.audit_date)} (${esc(ad.audit_type)}, ${esc(ad.auditor || "—")})</h2>
    <div class="small muted" style="margin:-6px 0 10px">Soru setleri: ${esc((ad.set_names || []).join(", "))}</div>
    <table><thead><tr><th>Bölüm</th><th>Puan</th></tr></thead><tbody>${ad.result.sections.map((x) => `<tr><td>${esc(x.title)}${(ad.set_names || []).length > 1 && x.source ? ` <span class="muted small">· ${esc(x.source)}</span>` : ""}</td><td style="width:50%">${meter(x.score == null ? null : Math.round(x.score))}</td></tr>`).join("")}</tbody></table>
    ${ad.notes ? `<h3>Denetçi notları</h3><div style="white-space:pre-wrap">${esc(ad.notes)}</div>` : ""}</div>` : `<div class="card muted">Tamamlanmış denetim yok.</div>`}
  <div class="card"><h2>Siber güvenlik özeti</h2>${s.scans.length ? `<table><thead><tr><th>Domain</th><th>Not</th><th>Puan</th><th>Başarısız kontroller</th></tr></thead><tbody>
    ${s.scans.map((x) => `<tr><td>${esc(x.domain)}</td><td>${gradeBox(x.grade)}</td><td>${num(x.score)}</td><td class="small">${x.result.checks.filter((c) => c.status === "fail").map((c) => esc(c.title)).join(", ") || "—"}</td></tr>`).join("")}</tbody></table>`
    : `<div class="muted">Tarama yapılmadı.</div>`}</div>
  <div class="card"><h2>Açık bulgular ve aksiyon planı</h2>${findingsTable(open, { compact: false }).replace(/<select[^>]*>[\s\S]*?<\/select>/g, (m) => statusBadge((m.match(/<option selected>([^<]+)/) || [])[1] || ""))}</div>`;
  $app.querySelectorAll("[data-edit]").forEach((b) => b.remove());
}


// ================================================================ KULLANICILAR & SEVİYELER
const ROLE_TR = { admin: "Yönetici", auditor: "Denetçi", supplier: "Tedarikçi", viewer: "İzleyici" };

async function viewUsers() {
  const [users, sups, tiers] = await Promise.all([api("/api/users"), api("/api/suppliers"), api("/api/supplier-tiers")]);
  $app.innerHTML = `<div class="page-head"><div><h1>Kullanıcılar</h1><div class="muted">Roller: <b>Yönetici</b> her şey · <b>Denetçi</b> denetim, bulgu, CAPA onayı · <b>İzleyici</b> salt okunur · <b>Tedarikçi</b> yalnızca kendi firmasının kayıtları ve CAPA girişi</div></div>
    <div class="actions"><button class="primary" id="nu">+ Kullanıcı</button></div></div>
  <div class="card"><div class="table-wrap"><table><thead><tr><th>Ad</th><th>E-posta</th><th>Rol</th><th>Tedarikçi</th><th>Son giriş</th><th>Durum</th><th></th></tr></thead><tbody>
  ${users.map((u) => `<tr><td><b>${esc(u.name)}</b></td><td>${esc(u.email)}</td><td>${esc(ROLE_TR[u.role] || u.role)}</td><td>${esc(u.supplier_name || "—")}</td>
    <td class="small">${fmtDT(u.last_login_at)}</td><td>${u.active ? `<span class="badge b-ok">aktif</span>` : `<span class="badge">pasif</span>`}</td>
    <td><button class="sm" data-u="${u.id}">Düzenle</button></td></tr>`).join("")}</tbody></table></div></div>
  <div class="card"><div class="page-head" style="margin-bottom:8px"><div><h2>Tedarikçi seviyeleri (tier)</h2><div class="small muted">Seviye, denetim sıklığını belirler; tedarikçi formundan atanır.</div></div>
    <button id="nt">+ Seviye</button></div>
    <table><thead><tr><th>Seviye</th><th>Ad</th><th>Denetim sıklığı</th><th class="num">Tedarikçi</th><th></th></tr></thead><tbody>
    ${tiers.map((t) => `<tr><td>${t.level}</td><td><b>${esc(t.name)}</b><div class="small muted">${esc(t.description || "")}</div></td><td>${t.audit_interval_days} gün</td><td class="num">${t.supplier_count}</td>
      <td><button class="sm" data-t="${t.id}">Düzenle</button></td></tr>`).join("")}</tbody></table></div>`;
  const userForm = (u = { role: "auditor", active: 1 }) => modal(`<h2>${u.id ? "Kullanıcıyı düzenle" : "Yeni kullanıcı"}</h2><form id="f">
    <div class="row"><div><label>Ad soyad</label><input name="name" required value="${esc(u.name || "")}"></div><div><label>E-posta</label><input name="email" type="email" required value="${esc(u.email || "")}"></div></div>
    <div class="row"><div><label>Rol</label><select name="role">${Object.entries(ROLE_TR).map(([k, v]) => `<option value="${k}" ${k === u.role ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div id="supRow"><label>Tedarikçi</label><select name="supplier_id"><option value="">—</option>${sups.map((x) => `<option value="${x.id}" ${x.id === u.supplier_id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></div></div>
    <div class="row"><div><label>${u.id ? "Yeni parola (değiştirmek için)" : "Parola (en az 8 karakter)"}</label><input name="password" type="password" minlength="8" ${u.id ? "" : "required"} autocomplete="new-password"></div>
      <div><label>Durum</label><select name="active"><option value="1" ${u.active ? "selected" : ""}>aktif</option><option value="0" ${u.active ? "" : "selected"}>pasif</option></select></div></div>
    <div class="actions" style="justify-content:flex-end"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Kaydet</button></div></form>`, (box) => {
    const sync = () => box.querySelector("#supRow").classList.toggle("hidden", box.querySelector("[name=role]").value !== "supplier");
    box.querySelector("[name=role]").onchange = sync; sync();
    box.querySelector("#f").onsubmit = async (e) => {
      e.preventDefault();
      const d = formData(e.target);
      d.active = d.active === "1";
      if (d.role !== "supplier") d.supplier_id = null;
      try { await api(u.id ? `/api/users/${u.id}` : "/api/users", { method: u.id ? "PUT" : "POST", body: d }); closeModal(); toast("Kaydedildi"); router(); }
      catch (err) { toast(err.message); }
    };
  });
  const tierForm = (t = { level: 4, audit_interval_days: 365 }) => modal(`<h2>${t.id ? "Seviyeyi düzenle" : "Yeni seviye"}</h2><form id="f">
    <div class="row"><div><label>Ad</label><input name="name" required value="${esc(t.name || "")}"></div><div><label>Seviye no (1 = en kritik)</label><input name="level" type="number" min="1" max="9" value="${t.level}"></div>
      <div><label>Denetim sıklığı (gün)</label><input name="audit_interval_days" type="number" min="30" value="${t.audit_interval_days}"></div></div>
    <label>Açıklama</label><textarea name="description">${esc(t.description || "")}</textarea>
    <div class="actions" style="justify-content:space-between;margin-top:12px">${t.id ? `<button type="button" class="danger" id="td">Sil</button>` : "<span></span>"}<div class="actions"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Kaydet</button></div></div></form>`, (box) => {
    box.querySelector("#f").onsubmit = async (e) => {
      e.preventDefault();
      try { await api(t.id ? `/api/supplier-tiers/${t.id}` : "/api/supplier-tiers", { method: t.id ? "PUT" : "POST", body: formData(e.target) }); closeModal(); router(); }
      catch (err) { toast(err.message); }
    };
    const td = box.querySelector("#td");
    if (td) td.onclick = async () => { try { await api(`/api/supplier-tiers/${t.id}`, { method: "DELETE" }); closeModal(); router(); } catch (err) { toast(err.message); } };
  });
  document.getElementById("nu").onclick = () => userForm();
  document.getElementById("nt").onclick = () => tierForm();
  $app.querySelectorAll("[data-u]").forEach((b) => (b.onclick = () => userForm(users.find((u) => u.id == b.dataset.u))));
  $app.querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => tierForm(tiers.find((t) => t.id == b.dataset.t))));
}

async function viewAuditLogs() {
  const logs = await api("/api/audit-logs?limit=500");
  $app.innerHTML = `<div class="page-head"><div><h1>İşlem Kayıtları</h1><div class="muted">Kim, ne zaman, neyi değiştirdi (son 500 kayıt)</div></div></div>
  <div class="card"><div class="filters"><input id="lq" placeholder="Ara (kullanıcı, işlem, kayıt)…" style="min-width:300px"></div><div id="lt"></div></div>`;
  const draw = () => {
    const q = document.getElementById("lq").value.toLowerCase();
    const r = logs.filter((l) => !q || [l.actor, l.action, l.entity, l.entity_id, JSON.stringify(l.detail)].join(" ").toLowerCase().includes(q));
    document.getElementById("lt").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Zaman</th><th>Kullanıcı</th><th>İşlem</th><th>Kayıt</th><th>Ayrıntı</th><th>IP</th></tr></thead><tbody>
      ${r.map((l) => `<tr><td class="small">${fmtDT(l.created_at + "Z")}</td><td>${esc(l.actor || "—")}</td><td><code>${esc(l.action)}</code></td>
        <td class="small">${esc([l.entity, l.entity_id].filter(Boolean).join(" #"))}</td><td class="small muted" style="max-width:360px;word-break:break-word">${esc(l.detail ? JSON.stringify(l.detail) : "")}</td><td class="small">${esc(l.ip || "")}</td></tr>`).join("")}</tbody></table></div>`;
  };
  document.getElementById("lq").oninput = draw; draw();
}


// ================================================================ KANIT / SERTİFİKA
async function uploadFile(fields, file) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) if (v != null && v !== "") fd.append(k, v);
  fd.append("file", file);
  const res = await fetch("/api/evidence", { method: "POST", body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : (data.detail || []).map((e) => e.msg).join("; ") || `Hata ${res.status}`);
  return data;
}
const fmtSize = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");
const certBadge = (st) => (st ? `<span class="badge ${{ "süresi doldu": "b-crit", yaklaşıyor: "b-warn", geçerli: "b-ok" }[st]}">${esc(st)}</span>` : "");

function evidenceTable(list, { cert = false, compact = false } = {}) {
  if (!list.length) return `<div class="empty small">${cert ? "Sertifika yok." : "Dosya yok."}</div>`;
  return `<table class="${compact ? "small" : ""}"><tbody>${list.map((e) => `<tr>
    <td><a href="/api/evidence/${e.id}/download">${esc(e.title || e.original_name)}</a><div class="small muted">${esc(e.original_name)} · ${fmtSize(e.size)} · ${esc(e.uploaded_by_name || "")} · ${fmtDate(e.created_at)}</div></td>
    ${cert || e.expires_at ? `<td class="small">Bitiş: <b>${fmtDate(e.expires_at)}</b> ${certBadge(e.cert_status)}</td>` : ""}
    <td style="white-space:nowrap;text-align:right">${can("evidence:write") && (!isSupplier() || e.uploaded_by === ME.id) ? `${cert ? `<button class="sm" data-evedit="${e.id}" data-exp="${esc(e.expires_at || "")}">Tarih</button> ` : ""}<button class="sm danger" data-evdel="${e.id}">Sil</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
}

function bindEvidence(root, after) {
  root.querySelectorAll("[data-evdel]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Dosya silinsin mi?")) return;
    try { await api(`/api/evidence/${b.dataset.evdel}`, { method: "DELETE" }); toast("Silindi"); after && after(); } catch (e) { toast(e.message); }
  }));
  root.querySelectorAll("[data-evedit]").forEach((b) => (b.onclick = async () => {
    const v = prompt("Yeni geçerlilik bitiş tarihi (YYYY-AA-GG):", b.dataset.exp);
    if (!v) return;
    try { await api(`/api/evidence/${b.dataset.evedit}`, { method: "PUT", body: { expires_at: v } }); after && after(); } catch (e) { toast(e.message); }
  }));
}

function uploadModal(fields, after) {
  const cert = fields.kind === "sertifika";
  modal(`<h2>${cert ? "Sertifika yükle" : "Kanıt dosyası yükle"}</h2><form id="uf">
    <label>Dosya (PDF, görsel, Office, ZIP · en fazla 20 MB)</label><input type="file" name="file" required>
    <div class="row" style="margin-top:12px"><div><label>Başlık</label><input name="title" placeholder="${cert ? "ör. ISO 27001 sertifikası" : "ör. Yedekleme test raporu"}"></div>
      <div><label>Geçerlilik bitişi${cert ? " *" : " (isteğe bağlı)"}</label><input type="date" name="expires_at" ${cert ? "required" : ""}></div></div>
    <div class="actions" style="justify-content:flex-end"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Yükle</button></div></form>`, (box) => {
    box.querySelector("#uf").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target.file.files[0];
      const btn = e.target.querySelector("button.primary"); btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> Yükleniyor…`;
      try { await uploadFile({ ...fields, title: e.target.title.value, expires_at: e.target.expires_at.value }, f); closeModal(); toast("Yüklendi"); after && after(); }
      catch (err) { toast(err.message, 6000); btn.disabled = false; btn.textContent = "Yükle"; }
    };
  });
}

// Denetim ekranına kanıt ve CAPA ekleri
async function auditExtras(a) {
  const [files, caps] = await Promise.all([api(`/api/evidence?audit_id=${a.id}`), can("capa:read") ? api(`/api/capa?audit_id=${a.id}`) : []]);
  const canUp = can("evidence:write") && !(isSupplier() && a.status === "tamamlandı");
  const drawSlots = (list) => $app.querySelectorAll(".ev-slot").forEach((slot) => {
    const mine = list.filter((e) => e.question_id === slot.dataset.ev);
    slot.innerHTML = `${mine.length ? `<div class="ev-list">${mine.map((e) => `<a class="chip" href="/api/evidence/${e.id}/download" title="${esc(e.original_name)}">📎 ${esc((e.title || e.original_name).slice(0, 40))}</a>`).join(" ")}</div>` : ""}
      ${canUp ? `<button type="button" class="sm ev-up">+ Kanıt</button>` : ""}`;
    const up = slot.querySelector(".ev-up");
    if (up) up.onclick = () => uploadModal({ audit_id: a.id, question_id: slot.dataset.ev }, async () => drawSlots(await api(`/api/evidence?audit_id=${a.id}`)));
  });
  drawSlots(files);
  if (caps.length) document.getElementById("sidecards").innerHTML = `<div class="card"><h2>CAPA kayıtları (${caps.length})</h2>
    ${caps.map((c) => `<div class="small" style="margin-bottom:6px"><a href="#/capa/${c.id}">${esc(c.title.slice(0, 80))}</a><div>${sevBadge(c.severity)} ${capaBadge(c.status)}</div></div>`).join("")}</div>`;
}

// ================================================================ CAPA
const capaBadge = (st) => `<span class="badge ${{ açık: "b-bad", "onay bekliyor": "b-warn", reddedildi: "b-crit", onaylandı: "b-ok" }[st] || ""}">${esc(st)}</span>`;

function capaTable(list, withSupplier = false) {
  if (!list.length) return `<div class="empty">CAPA kaydı yok.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Önem</th>${withSupplier ? "<th>Tedarikçi</th>" : ""}<th>CAPA</th><th>Kaynak</th><th>Termin</th><th>Durum</th><th class="num">Kanıt</th></tr></thead><tbody>
  ${list.map((c) => `<tr><td>${sevBadge(c.severity)}</td>${withSupplier ? `<td>${esc(c.supplier_name)}</td>` : ""}
    <td><a href="#/capa/${c.id}"><b>${esc(c.title)}</b></a>${c.status === "reddedildi" && c.review_comment ? `<div class="small" style="color:var(--crit)">Red gerekçesi: ${esc(c.review_comment)}</div>` : ""}</td>
    <td class="small">${esc(c.trigger)}</td>
    <td class="small" style="${c.status !== "onaylandı" && c.due_date < todayISO() ? "color:var(--bad);font-weight:700" : ""}">${fmtDate(c.due_date)}</td>
    <td>${capaBadge(c.status)}${c.round > 1 ? ` <span class="small muted">${c.round}. gönderim</span>` : ""}</td><td class="num">${c.evidence_count ?? ""}</td></tr>`).join("")}</tbody></table></div>`;
}

async function viewCapaList() {
  const st = new URLSearchParams(location.hash.split("?")[1] || "").get("status") ?? "açık-tümü";
  $app.innerHTML = `<div class="page-head"><div><h1>CAPA</h1><div class="muted">${isSupplier() ? "Size açılmış düzeltici faaliyetler: kök neden, aksiyon ve kanıt girip onaya gönderin." : "Düzeltici / önleyici faaliyetler ve onay bekleyen tedarikçi yanıtları"}</div></div></div>
    <div class="card"><div class="filters"><select id="cs">${[["açık-tümü", "Açık (tümü)"], ["onay bekliyor", "Onay bekleyen"], ["açık", "Tedarikçi yanıtı bekleyen"], ["reddedildi", "Reddedilen"], ["onaylandı", "Onaylanan"], ["", "Tümü"]].map(([v, l]) => `<option value="${v}" ${v === st ? "selected" : ""}>${l}</option>`).join("")}</select></div><div id="ct"></div></div>`;
  const load = async () => { document.getElementById("ct").innerHTML = capaTable(await api(`/api/capa?status=${encodeURIComponent(document.getElementById("cs").value)}`), !isSupplier()); };
  document.getElementById("cs").onchange = load;
  await load();
}

async function viewCapa(id) {
  const c = await api(`/api/capa/${id}`);
  const editable = can("capa:submit") && ["açık", "reddedildi"].includes(c.status);
  const reviewable = can("capa:review") && c.status === "onay bekliyor";
  const canUp = can("evidence:write") && c.status !== "onaylandı" && (!isSupplier() || c.status !== "onay bekliyor");
  $app.innerHTML = `<div class="page-head"><div><div class="small"><a href="#/capa">← CAPA listesi</a></div>
    <h1>${esc(c.title)}</h1><div class="muted">${esc(c.supplier_name)} · ${sevBadge(c.severity)} · ${capaBadge(c.status)} · Kaynak: ${esc(c.trigger)} · Termin: <b>${fmtDate(c.due_date)}</b>
    ${c.audit_id ? ` · <a href="#/denetim/${c.audit_id}">Denetim ${fmtDate(c.audit_date)}</a>` : ""}</div></div></div>
  ${c.status === "reddedildi" ? `<div class="card" style="background:var(--bad-bg);color:var(--bad);border-color:transparent"><b>Denetçi reddetti:</b> ${esc(c.review_comment || "")}<div class="small">Düzeltip yeniden gönderin.</div></div>` : ""}
  <div class="grid g2" style="align-items:start">
    <div>
      ${c.description ? `<div class="card"><h2>Uygunsuzluk</h2><div style="white-space:pre-wrap">${esc(c.description)}</div></div>` : ""}
      <div class="card"><h2>Tedarikçi aksiyon planı ${c.round ? `<span class="small muted">· ${c.round}. gönderim, ${fmtDT(c.submitted_at)}${c.submitted_by_name ? " · " + esc(c.submitted_by_name) : ""}</span>` : ""}</h2>
        ${editable ? `<form id="sf">
          <label>Kök neden *</label><textarea name="root_cause" required minlength="3">${esc(c.root_cause || "")}</textarea>
          <label style="margin-top:8px">Düzeltici faaliyet (ne yapıldı / yapılacak) *</label><textarea name="corrective_action" required minlength="3">${esc(c.corrective_action || "")}</textarea>
          <label style="margin-top:8px">Önleyici faaliyet (tekrarı nasıl önlenecek)</label><textarea name="preventive_action">${esc(c.preventive_action || "")}</textarea>
          <div class="row" style="margin-top:8px"><div><label>Planlanan tamamlanma</label><input type="date" name="planned_date" value="${esc(c.planned_date || "")}"></div>
            <div><label>Denetçiye not</label><input name="comment"></div></div>
          ${c.severity === "kritik" ? `<div class="small" style="color:var(--crit);margin-bottom:8px">Kritik CAPA: göndermeden önce en az bir kanıt dosyası yükleyin.</div>` : ""}
          <div class="actions" style="justify-content:flex-end"><button class="primary">Onaya gönder</button></div></form>`
        : c.round ? `<table><tbody><tr><th>Kök neden</th><td style="white-space:pre-wrap">${esc(c.root_cause)}</td></tr><tr><th>Düzeltici</th><td style="white-space:pre-wrap">${esc(c.corrective_action)}</td></tr>
            <tr><th>Önleyici</th><td style="white-space:pre-wrap">${esc(c.preventive_action || "—")}</td></tr><tr><th>Planlanan</th><td>${fmtDate(c.planned_date)}</td></tr></tbody></table>`
        : `<div class="muted">Tedarikçi henüz aksiyon planı göndermedi.</div>`}
      </div>
      ${reviewable ? `<div class="card"><h2>Denetçi değerlendirmesi</h2><textarea id="rc" placeholder="Yorum (red için zorunlu)"></textarea>
        <div class="actions" style="justify-content:flex-end;margin-top:8px"><button class="danger" id="capa-reject">Reddet</button><button class="primary" id="capa-approve">Onayla ve kapat</button></div></div>` : ""}
    </div>
    <div>
      <div class="card"><div class="page-head" style="margin-bottom:8px"><h2 style="margin:0">Kanıtlar (${c.evidence.length})</h2>${canUp ? `<button class="sm" id="ev">+ Kanıt yükle</button>` : ""}</div><div id="evl">${evidenceTable(c.evidence)}</div></div>
      <div class="card"><h2>Geçmiş</h2>${c.events.map((e) => `<div class="small" style="margin-bottom:8px"><b>${esc(e.action)}</b>${e.to_status ? ` → ${capaBadge(e.to_status)}` : ""} <span class="muted">${fmtDT(e.created_at + "Z")} · ${esc(e.actor || "sistem")}</span>${e.comment ? `<div class="muted">${esc(e.comment)}</div>` : ""}</div>`).join("")}</div>
    </div></div>`;
  bindEvidence(document.getElementById("evl"), router);
  const ev = document.getElementById("ev");
  if (ev) ev.onclick = () => uploadModal({ capa_id: c.id }, router);
  const sf = document.getElementById("sf");
  if (sf) sf.onsubmit = async (e) => {
    e.preventDefault();
    try { await api(`/api/capa/${c.id}/submit`, { method: "POST", body: formData(e.target) }); toast("Onaya gönderildi"); router(); } catch (err) { toast(err.message, 6000); }
  };
  const rv = (decision) => async () => {
    try { await api(`/api/capa/${c.id}/review`, { method: "POST", body: { decision, comment: document.getElementById("rc").value || null } }); toast(decision === "approve" ? "Onaylandı, bulgu kapatıldı" : "Reddedildi"); router(); }
    catch (err) { toast(err.message); }
  };
  if (reviewable) { document.getElementById("capa-approve").onclick = rv("approve"); document.getElementById("capa-reject").onclick = rv("reject"); }
}

function capaForm(supplierId, findingId = null) {
  modal(`<h2>Manuel CAPA</h2><form id="f">
    <div style="margin-bottom:12px"><label>Başlık *</label><input name="title" ${findingId ? "" : "required"} placeholder="${findingId ? "Boş bırakılırsa bulgu başlığı kullanılır" : ""}"></div>
    <div class="row"><div><label>Önem</label><select name="severity"><option>kritik</option><option>yüksek</option><option selected>orta</option><option>düşük</option></select></div>
      <div><label>Termin</label><input type="date" name="due_date"></div></div>
    <label>Açıklama</label><textarea name="description"></textarea>
    <div class="actions" style="justify-content:flex-end;margin-top:12px"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Oluştur</button></div></form>`, (box) => {
    box.querySelector("#f").onsubmit = async (e) => {
      e.preventDefault();
      try { const c = await api("/api/capa", { method: "POST", body: { ...formData(e.target), supplier_id: supplierId, finding_id: findingId } }); closeModal(); location.hash = `#/capa/${c.id}`; }
      catch (err) { toast(err.message); }
    };
  });
}


// ================================================================ WEBHOOK ABONELİKLERİ
const EVENT_TR = { "*": "Tüm olaylar", "finding.created": "Yeni bulgu", "finding.status_changed": "Bulgu durumu değişti", "capa.created": "CAPA açıldı",
  "capa.submitted": "CAPA onaya gönderildi", "capa.approved": "CAPA onaylandı", "capa.rejected": "CAPA reddedildi", "audit.completed": "Denetim tamamlandı" };

async function webhookAdmin(root) {
  const [subs, dels, sups] = await Promise.all([api("/api/webhooks"), api("/api/webhooks/deliveries?limit=30"), api("/api/suppliers")]);
  root.innerHTML = `<div class="page-head" style="margin-bottom:8px"><div><h2 style="margin:0">Olay webhook'ları</h2>
    <div class="small muted">Yeni bulgu, bulgu durum değişimi, CAPA adımları ve denetim tamamlanması gibi olaylar seçtiğiniz sistemlere (SIEM, ticket, Slack/Teams köprüsü, GRC) imzalı JSON olarak iletilir.</div></div>
    <button id="nwh">+ Abonelik</button></div>
  ${subs.items.length ? `<table><thead><tr><th>Ad</th><th>Adres</th><th>Olaylar</th><th>Kapsam</th><th></th></tr></thead><tbody>${subs.items.map((w) => `<tr>
    <td><b>${esc(w.name)}</b> ${w.active ? "" : `<span class="badge">pasif</span>`}</td><td class="small" style="word-break:break-all">${esc(w.url)}${w.secret ? ` <span class="badge b-ok">imzalı</span>` : ""}</td>
    <td class="small">${w.events.map((e) => esc(EVENT_TR[e] || e)).join(", ")}</td><td class="small">${esc(w.supplier_name || "Tüm tedarikçiler")}</td>
    <td style="white-space:nowrap"><button class="sm" data-wt="${w.id}">Test</button> <button class="sm" data-we="${w.id}">Düzenle</button></td></tr>`).join("")}</tbody></table>`
    : `<div class="empty small">Abonelik yok.</div>`}
  ${dels.length ? `<h3>Son teslimatlar</h3><table class="small"><tbody>${dels.map((d) => `<tr><td>${fmtDT(d.created_at + "Z")}</td><td>${esc(d.subscription_name)}</td><td><code>${esc(d.event_type)}</code> ${esc(d.entity || "")} #${esc(d.entity_id || "")}</td>
    <td><span class="badge ${{ gönderildi: "b-ok", başarısız: "b-bad" }[d.status] || "b-warn"}">${esc(d.status)}</span>${d.response_code ? ` HTTP ${d.response_code}` : ""}${d.error ? `<div style="color:var(--bad)">${esc(d.error)}</div>` : ""}</td>
    <td>${d.attempts} deneme</td><td>${d.status === "başarısız" ? `<button class="sm" data-wr="${d.id}">Tekrar</button>` : ""}</td></tr>`).join("")}</tbody></table>` : ""}`;
  const form = (w = { events: ["*"], active: true }) => modal(`<h2>${w.id ? "Aboneliği düzenle" : "Yeni webhook aboneliği"}</h2><form id="wf">
    <div class="row"><div><label>Ad</label><input name="name" required value="${esc(w.name || "")}" placeholder="ör. SIEM, Jira"></div>
      <div><label>Kapsam</label><select name="supplier_id"><option value="">Tüm tedarikçiler</option>${sups.map((x) => `<option value="${x.id}" ${x.id === w.supplier_id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></div></div>
    <label>Adres (URL)</label><input name="url" required value="${esc(w.url || "")}" placeholder="https://...">
    <div class="row" style="margin-top:8px"><div><label>İmza anahtarı</label><div style="display:flex;gap:6px"><input name="secret" value="${esc(w.secret || "")}" autocomplete="off"><button type="button" id="wg">Üret</button></div></div>
      <div><label>Durum</label><select name="active"><option value="1" ${w.active ? "selected" : ""}>aktif</option><option value="0" ${w.active ? "" : "selected"}>pasif</option></select></div></div>
    <label>Olaylar</label><div class="set-grid" style="margin-bottom:12px">${Object.entries(EVENT_TR).map(([k, v]) => `<label class="te-small"><input type="checkbox" name="ev" value="${k}" ${w.events.includes(k) ? "checked" : ""}> ${v}</label>`).join("")}</div>
    <div class="actions" style="justify-content:space-between">${w.id ? `<button type="button" class="danger" id="wd">Sil</button>` : "<span></span>"}<div class="actions"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Kaydet</button></div></div></form>`, (box) => {
    box.querySelector("#wg").onclick = () => { const a = new Uint8Array(24); crypto.getRandomValues(a); box.querySelector("[name=secret]").value = [...a].map((x) => x.toString(16).padStart(2, "0")).join(""); };
    box.querySelector("#wf").onsubmit = async (e) => {
      e.preventDefault();
      const d = formData(e.target);
      d.events = [...e.target.querySelectorAll("[name=ev]:checked")].map((c) => c.value);
      if (d.events.includes("*")) d.events = ["*"];
      d.active = d.active === "1"; delete d.ev;
      try { await api(w.id ? `/api/webhooks/${w.id}` : "/api/webhooks", { method: w.id ? "PUT" : "POST", body: d }); closeModal(); webhookAdmin(root); } catch (err) { toast(err.message); }
    };
    const wd = box.querySelector("#wd");
    if (wd) wd.onclick = async () => { if (confirm("Abonelik silinsin mi?")) { await api(`/api/webhooks/${w.id}`, { method: "DELETE" }); closeModal(); webhookAdmin(root); } };
  });
  root.querySelector("#nwh").onclick = () => form();
  root.querySelectorAll("[data-we]").forEach((b) => (b.onclick = () => form(subs.items.find((x) => x.id == b.dataset.we))));
  root.querySelectorAll("[data-wt]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; const r = await api(`/api/webhooks/${b.dataset.wt}/test`, { method: "POST" }).catch((e) => ({ status: "başarısız", error: e.message }));
    toast(r.status === "gönderildi" ? `Test gönderildi (HTTP ${r.response_code})` : `Başarısız: ${r.error}`, 6000); webhookAdmin(root);
  }));
  root.querySelectorAll("[data-wr]").forEach((b) => (b.onclick = async () => { await api(`/api/webhooks/deliveries/${b.dataset.wr}/retry`, { method: "POST" }); webhookAdmin(root); }));
}


// ================================================================ RİSK & UYUM
const DIM_COLOR = { audit: "var(--foreground)", intel: "var(--accent)", financial: "#7c3aed", esg: "var(--ok)", reputation: "var(--warn)" };
const RATING_TR = { financial: "Finansal", esg: "ESG", reputation: "İtibar" };

async function riskTab(T, s) {
  const [ratings, checks] = await Promise.all([api(`/api/suppliers/${s.id}/risk-ratings`), api(`/api/suppliers/${s.id}/sanctions-checks`)]);
  const m = s.summary.maturity;
  const last = checks[0];
  T.innerHTML = `<div class="grid g2" style="align-items:start">
    <div class="card"><h2>Olgunluk puanı ${num(m.score)} <span class="small muted">· veri kapsamı %${m.coverage}</span></h2>
      <div class="small muted" style="margin-bottom:10px">Ağırlıklı ortalama. Verisi olmayan boyutun ağırlığı diğerlerine oransal dağıtılır. Ağırlıkları Ayarlar'dan değiştirebilirsiniz.</div>
      <div class="bar" style="height:10px;margin-bottom:12px">${m.breakdown.filter((b) => b.contribution != null).map((b) => `<div title="${esc(b.label)}: ${num(b.contribution)}" style="width:${b.contribution}%;background:${DIM_COLOR[b.key]}"></div>`).join("")}</div>
      <table class="dense"><thead><tr><th>Boyut</th><th class="num">Puan</th><th class="num">Ayarlı ağırlık</th><th class="num">Etkin ağırlık</th><th class="num">Katkı</th></tr></thead><tbody>
      ${m.breakdown.map((b) => `<tr><td><span class="dot" style="color:${DIM_COLOR[b.key]}"></span> ${esc(b.label)}</td><td class="num">${b.value == null ? `<span class="muted">veri yok</span>` : num(b.value)}</td>
        <td class="num muted">${num(b.weight)}</td><td class="num">${b.effective_weight ? "%" + num(b.effective_weight) : "—"}</td><td class="num"><b>${num(b.contribution)}</b></td></tr>`).join("")}</tbody></table></div>
    <div class="card"><div class="page-head" style="margin-bottom:8px"><h2 style="margin:0">Yaptırım taraması</h2><button class="sm" id="scr" data-perm="supplier:write">Şimdi tara</button></div>
      <div class="small muted" style="margin-bottom:8px">Firma adı OFAC SDN, BM Konsolide ve AB Konsolide yaptırım listelerinde aranır (şirket ekleri ve Türkçe karakterler normalize edilir).</div>
      ${last ? `<div style="margin-bottom:8px">${{ temiz: `<span class="badge b-ok">Temiz</span>`, "olası eşleşme": `<span class="badge b-warn">Olası eşleşme (${last.match_count})</span>`, "yanlış pozitif": `<span class="badge">Yanlış pozitif</span>`, "doğrulandı": `<span class="badge b-crit">Doğrulanmış yaptırım</span>` }[last.status]}
          <span class="small muted">${fmtDT(last.checked_at + "Z")} · ${esc(last.checked_by || "")}</span>${last.resolution_comment ? `<div class="small">${esc(last.resolution_comment)} — ${esc(last.resolved_by || "")}</div>` : ""}</div>
        ${last.matches.length ? `<table class="dense small"><thead><tr><th>Liste</th><th>Kayıt</th><th>Program</th><th class="num">Benzerlik</th></tr></thead><tbody>${last.matches.map((x) => `<tr><td>${esc(x.list)}</td><td>${esc(x.name)}<div class="muted">${esc(x.ref || "")}</div></td><td>${esc(x.program || "")}</td><td class="num">%${Math.round(x.score * 100)}</td></tr>`).join("")}</tbody></table>` : ""}
        ${last.status === "olası eşleşme" && can("supplier:write") ? `<div class="actions" style="margin-top:8px"><input id="rcm" placeholder="Değerlendirme notu" style="flex:1"><button class="sm" id="fp">Yanlış pozitif</button><button class="sm danger" id="cf">Doğrula</button></div>` : ""}`
      : `<div class="empty small">Henüz tarama yapılmadı.</div>`}</div>
  </div>
  <div class="card"><div class="page-head" style="margin-bottom:8px"><div><h2 style="margin:0">Finansal / ESG / itibar puanları</h2><div class="small muted">0–100 arası. Kaynak: kredi derecelendirme, Findeks raporu, EcoVadis, CDP, basın taraması veya iç değerlendirme.</div></div>
    <button class="sm" id="nr" data-perm="supplier:write">+ Puan ekle</button></div>
    ${ratings.length ? `<table class="dense"><thead><tr><th>Alan</th><th class="num">Puan</th><th>Kaynak</th><th>Not</th><th>Tarih</th><th>Giren</th></tr></thead><tbody>${ratings.map((r) => `<tr><td>${esc(RATING_TR[r.domain])}</td><td class="num"><b>${num(r.score)}</b></td><td>${esc(r.source || "—")}</td><td class="small">${esc(r.notes || "")}</td><td class="small">${fmtDate(r.assessed_at)}</td><td class="small muted">${esc(r.assessed_by || "")}</td></tr>`).join("")}</tbody></table>` : `<div class="empty small">Puan girilmedi. Olgunluk puanı yalnızca denetim ve siber puanla hesaplanıyor.</div>`}</div>`;
  const scr = document.getElementById("scr");
  scr.onclick = async () => { scr.disabled = true; try { const r = await api(`/api/suppliers/${s.id}/sanctions-screen`, { method: "POST" }); toast(r.status === "temiz" ? "Eşleşme yok" : `${r.match_count} olası eşleşme`); router(); } catch (e) { toast(e.message, 6000); scr.disabled = false; } };
  const res = (status) => async () => { try { await api(`/api/sanctions-checks/${last.id}/resolve`, { method: "POST", body: { status, comment: document.getElementById("rcm").value || null } }); router(); } catch (e) { toast(e.message); } };
  if (document.getElementById("fp")) { document.getElementById("fp").onclick = res("yanlış pozitif"); document.getElementById("cf").onclick = res("doğrulandı"); }
  document.getElementById("nr").onclick = () => modal(`<h2>Risk puanı ekle</h2><form id="f"><div class="row">
      <div><label>Alan</label><select name="domain"><option value="financial">Finansal</option><option value="esg">ESG</option><option value="reputation">İtibar</option></select></div>
      <div><label>Puan (0–100)</label><input name="score" type="number" min="0" max="100" step="0.1" required></div>
      <div><label>Değerlendirme tarihi</label><input name="assessed_at" type="date" value="${todayISO()}"></div></div>
      <label>Kaynak</label><input name="source" placeholder="ör. Findeks, EcoVadis, iç değerlendirme">
      <label style="margin-top:8px">Not</label><textarea name="notes"></textarea>
      <div class="actions" style="justify-content:flex-end;margin-top:12px"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Kaydet</button></div></form>`, (box) => {
    box.querySelector("#f").onsubmit = async (e) => { e.preventDefault(); try { await api(`/api/suppliers/${s.id}/risk-ratings`, { method: "POST", body: formData(e.target) }); closeModal(); router(); } catch (err) { toast(err.message); } };
  });
}

async function weightsAdmin(root) {
  const w = await api("/api/score-weights");
  root.innerHTML = `<h2>Olgunluk puanı ağırlıkları</h2><div class="small muted" style="margin-bottom:10px">Tedarikçinin genel (olgunluk) puanı bu ağırlıklarla hesaplanır. Dış istihbarat (siber) puanının etkisini buradan artırıp azaltabilirsiniz. Verisi olmayan boyutun ağırlığı diğerlerine oransal dağıtılır.</div>
    <form id="wf"><div class="row">${Object.entries(w.labels).map(([k, l]) => `<div><label>${esc(l)}</label><input type="number" name="${k}" min="0" max="100" step="1" value="${w.weights[k]}"></div>`).join("")}</div>
    <div class="actions" style="justify-content:space-between"><span class="small muted" id="wsum"></span><div class="actions"><button type="button" id="wdef">Varsayılana dön</button><button class="primary">Kaydet</button></div></div></form>`;
  const f = root.querySelector("#wf");
  const sum = () => { const t = [...f.querySelectorAll("input")].reduce((a, i) => a + (+i.value || 0), 0); root.querySelector("#wsum").textContent = `Toplam ${t} — oranlar: ` + [...f.querySelectorAll("input")].filter((i) => +i.value).map((i) => `${w.labels[i.name]} %${Math.round((+i.value / t) * 100)}`).join(" · "); };
  f.oninput = sum; sum();
  root.querySelector("#wdef").onclick = () => { for (const [k, v] of Object.entries(w.defaults)) f[k].value = v; sum(); };
  f.onsubmit = async (e) => { e.preventDefault(); try { await api("/api/score-weights", { method: "PUT", body: Object.fromEntries([...f.querySelectorAll("input")].map((i) => [i.name, +i.value || 0])) }); toast("Ağırlıklar kaydedildi; tüm puanlar yeniden hesaplandı."); } catch (err) { toast(err.message); } };
}

async function sanctionsAdmin(root) {
  const st = await api("/api/sanctions/status");
  root.innerHTML = `<div class="page-head" style="margin-bottom:8px"><div><h2 style="margin:0">Yaptırım listeleri</h2><div class="small muted">Resmi listeler indirilip bu bilgisayarda saklanır; tarama çevrimdışı yapılır. Ayda bir güncellemeniz önerilir.</div></div><button id="supd">Listeleri güncelle</button></div>
    <table class="dense"><tbody>${st.map((x) => `<tr><td><b>${esc(x.list)}</b><div class="small muted" style="word-break:break-all">${esc(x.url)}</div></td><td class="num">${x.entries.toLocaleString("tr-TR")} kayıt</td><td class="small">${x.updated_at ? fmtDT(x.updated_at + "Z") : `<span class="muted">indirilmedi</span>`}</td></tr>`).join("")}</tbody></table>`;
  root.querySelector("#supd").onclick = async (e) => {
    e.target.disabled = true; e.target.innerHTML = `<span class="spinner"></span> İndiriliyor (1–2 dk)…`;
    const r = await api("/api/sanctions/update", { method: "POST" }).catch((err) => [{ ok: false, list: "—", error: err.message }]);
    toast(r.map((x) => `${x.list}: ${x.ok ? x.entries.toLocaleString("tr-TR") + " kayıt" : "hata (" + x.error + ")"}`).join(" · "), 9000);
    sanctionsAdmin(root);
  };
}

function importModal() {
  modal(`<h2>Tedarikçileri içe aktar</h2>
    <div class="small muted" style="margin-bottom:10px">Excel (.xlsx) veya CSV dosyası yükleyin. İlk satır başlık olmalı; tanınan sütunlar: Firma adı (zorunlu), Kategori, Ülke, Kritiklik, Tier, Yetkili, E-posta, Telefon, Domain, Bildirim e-postası, Notlar.
      <a href="/api/import/suppliers/template.csv">Örnek şablonu indirin</a>.</div>
    <input type="file" id="if" accept=".csv,.xlsx,.txt"><div id="ip" style="margin-top:12px"></div>
    <div class="actions" style="justify-content:flex-end;margin-top:12px"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary" id="ic" disabled>İçe aktar</button></div>`, (box) => {
    box.style.maxWidth = "900px";
    const send = async (commit) => {
      const fd = new FormData(); fd.append("file", box.querySelector("#if").files[0]); fd.append("commit", commit ? "true" : "false");
      const r = await fetch("/api/import/suppliers", { method: "POST", body: fd }); const d = await r.json();
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Dosya okunamadı");
      return d;
    };
    box.querySelector("#if").onchange = async () => {
      const ip = box.querySelector("#ip"); ip.innerHTML = `<span class="spinner"></span> Okunuyor…`;
      try {
        const d = await send(false);
        ip.innerHTML = `<div style="margin-bottom:8px"><span class="badge b-ok">${d.valid} aktarılacak</span> ${d.invalid ? `<span class="badge b-bad">${d.invalid} hatalı (atlanacak)</span>` : ""}</div>
          <div class="table-wrap" style="max-height:340px;overflow:auto;border:1px solid var(--border);border-radius:6px"><table class="dense small"><thead><tr><th>Satır</th><th>Firma</th><th>Kritiklik</th><th>Domain</th><th>Durum</th></tr></thead><tbody>
          ${d.rows.map((r) => `<tr><td>${r.row}</td><td>${esc(r.name || "—")}</td><td>${esc(r.criticality)}</td><td>${esc(r.domains.join(", "))}</td>
            <td>${r.errors.length ? `<span style="color:var(--bad)">${esc(r.errors.join("; "))}</span>` : `<span class="badge b-ok">tamam</span>`}${r.warnings.length ? `<div class="muted">${esc(r.warnings.join("; "))}</div>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
        box.querySelector("#ic").disabled = !d.valid;
      } catch (e) { ip.innerHTML = `<div style="color:var(--bad)">${esc(e.message)}</div>`; }
    };
    box.querySelector("#ic").onclick = async (e) => {
      e.target.disabled = true;
      try { const d = await send(true); closeModal(); toast(`${d.created} tedarikçi eklendi.`); router(); } catch (err) { toast(err.message); e.target.disabled = false; }
    };
  });
}


// ================================================================ KABUL / ÇIKARMA / SÖZLEŞME
async function onboardModal() {
  const q = await api("/api/onboarding/questions");
  modal(`<h2>Yeni tedarikçi — kabul</h2>
    <div class="small muted" style="margin-bottom:12px">Doğal risk soruları tedarikçinin tier'ını, kritikliğini ve hangi soru setleriyle denetlenmesi gerektiğini önerir. Tedarikçi <b>aday</b> olarak oluşturulur; değerlendirme sonrası "Onayla" ile aktif edilir.</div>
    <form id="of"><div class="row"><div><label>Firma adı *</label><input name="name" required></div><div><label>Kategori</label><input name="category"></div><div><label>Ülke</label><input name="country" value="Türkiye"></div></div>
    <div class="row"><div><label>Yetkili</label><input name="contact_name"></div><div><label>E-posta</label><input name="email" type="email"></div><div><label>Domain</label><input name="domains" placeholder="ornek.com.tr"></div></div>
    <h3>Doğal risk</h3>
    ${q.questions.map((x) => `<div style="margin-bottom:8px"><label>${esc(x.text)}</label><select name="q_${x.key}" required><option value="">Seçin…</option>${x.options.map((o) => `<option value="${o.value}">${esc(o.label)}</option>`).join("")}</select></div>`).join("")}
    <label>İlgili düzenlemeler</label><div class="chips" style="margin-bottom:12px">${Object.entries(q.regulations).map(([k, v]) => `<label class="te-small"><input type="checkbox" name="reg" value="${k}"> ${esc(v)}</label>`).join("")}</div>
    <div class="card" id="sug" style="background:var(--muted-bg)"><span class="muted small">Tüm soruları cevaplayınca öneri burada görünür.</span></div>
    <div class="actions" style="justify-content:space-between"><a href="#" id="quick" class="small">Formsuz hızlı ekle</a><div class="actions"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Aday olarak oluştur</button></div></div></form>`, (box) => {
    box.style.maxWidth = "760px";
    const f = box.querySelector("#of");
    const body = () => ({ answers: Object.fromEntries(q.questions.map((x) => [x.key, f[`q_${x.key}`].value])), regulations: [...f.querySelectorAll("[name=reg]:checked")].map((c) => c.value) });
    const preview = async () => {
      const b = body();
      if (Object.values(b.answers).some((v) => !v)) return;
      const r = await api("/api/onboarding/preview", { method: "POST", body: b }).catch(() => null);
      if (!r) return;
      box.querySelector("#sug").innerHTML = `<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap"><div><div class="small muted">Doğal risk</div><div style="font-size:22px;font-weight:600">${r.score}</div></div>
        <div><div class="small muted">Önerilen</div><b>${esc(r.tier ? r.tier.name : "Tier " + r.tier_level)}</b> · kritiklik ${esc(r.criticality)}</div>
        <div style="flex:1"><div class="small muted">Önerilen soru setleri</div>${r.templates.map((t) => `<span class="badge">${esc(t)}</span>`).join(" ")}</div></div>
        ${r.reasons.length ? `<div class="small" style="margin-top:6px">Gerekçe: ${esc(r.reasons.join(" · "))}</div>` : ""}`;
    };
    f.onchange = preview;
    box.querySelector("#quick").onclick = (e) => { e.preventDefault(); closeModal(); supplierForm(); };
    f.onsubmit = async (e) => {
      e.preventDefault();
      const d = formData(f);
      try {
        const r = await api("/api/onboarding", { method: "POST", body: { ...body(), name: d.name, category: d.category, country: d.country, contact_name: d.contact_name, email: d.email, domains: (d.domains || "").split(/[\s,;]+/).filter(Boolean) } });
        closeModal(); toast("Aday tedarikçi oluşturuldu"); location.hash = `#/tedarikci/${r.supplier_id}`;
      } catch (err) { toast(err.message); }
    };
  });
}

async function lifecycleBanner(s) {
  const el = document.getElementById("lifecycle");
  const ob = document.getElementById("offb");
  if (ob) ob.onclick = () => {
    const reason = prompt("Çıkarma nedeni (ör. sözleşme sona erdi, performans, alternatif tedarikçi):");
    if (reason) api(`/api/suppliers/${s.id}/offboarding`, { method: "POST", body: { reason } }).then(router).catch((e) => toast(e.message));
  };
  if (s.status === "aday") {
    const it = await api(`/api/suppliers/${s.id}/intake`);
    el.innerHTML = `<div class="card" style="border-color:var(--warn-bd);background:var(--warn-bg)"><div class="page-head" style="margin:0"><div>
      <b>Aday tedarikçi.</b> ${it ? `Doğal risk ${it.score} · önerilen Tier ${it.tier_level} · önerilen setler: ${it.templates.map((t) => `<span class="badge">${esc(t)}</span>`).join(" ")}` : "Kabul formu doldurulmadı."}
      <div class="small muted">Değerlendirmeyi (denetim/öz-değerlendirme, belgeler) tamamladıktan sonra tedarikçiyi onaylayın.</div></div>
      <button class="primary" id="act" data-perm="supplier:write">Onayla ve aktif et</button></div></div>`;
    document.getElementById("act").onclick = () => api(`/api/suppliers/${s.id}/activate`, { method: "POST" }).then(() => { toast("Tedarikçi aktif"); router(); }).catch((e) => toast(e.message));
    applyPerms(el);
  } else if (s.status === "çıkış sürecinde" || s.status === "pasif") {
    const o = await api(`/api/suppliers/${s.id}/offboarding`);
    if (!o) return;
    const done = !!o.completed_at, edit = can("supplier:write") && !done;
    el.innerHTML = `<div class="card"><div class="page-head" style="margin-bottom:8px"><div><h2 style="margin:0">Çıkarma (offboarding) ${done ? `<span class="badge b-ok">tamamlandı ${fmtDate(o.completed_at)}</span>` : `<span class="badge b-bad">${o.progress}/${o.items.length}</span>`}</h2>
      <div class="small muted">Neden: ${esc(o.reason || "—")} · başlatan ${esc(o.started_by || "")} · ${fmtDate(o.started_at)}</div></div>
      ${edit ? `<button class="primary" id="ocomp" ${o.progress < o.items.length ? "disabled" : ""}>Çıkarmayı tamamla</button>` : ""}</div>
      ${o.items.map((i) => `<label class="te-small" style="padding:4px 0"><input type="checkbox" data-ok="${i.key}" ${i.done ? "checked" : ""} ${edit ? "" : "disabled"}> ${esc(i.label)}
        ${i.done ? `<span class="small muted">— ${esc(i.done_by || "")} ${fmtDate(i.done_at)}</span>` : ""}</label>`).join("")}
      ${edit ? `<div class="small muted" style="margin-top:6px">Tamamlanınca tedarikçi pasife alınır, tedarikçi kullanıcı hesapları ve tedarikçiye özel webhook'lar kapatılır.</div>` : ""}</div>`;
    el.querySelectorAll("[data-ok]").forEach((c) => (c.onchange = () => api(`/api/suppliers/${s.id}/offboarding/items/${c.dataset.ok}`, { method: "PUT", body: { done: c.checked } }).then(() => lifecycleBanner(s)).catch((e) => toast(e.message))));
    const oc = document.getElementById("ocomp");
    if (oc) oc.onclick = () => confirm("Tedarikçi pasife alınacak ve hesapları kapatılacak. Devam?") && api(`/api/suppliers/${s.id}/offboarding/complete`, { method: "POST" }).then(() => { toast("Çıkarma tamamlandı"); router(); }).catch((e) => toast(e.message));
  }
}

const contractBadge = (c) => `<span class="badge ${{ aktif: "b-ok", bitiyor: "b-warn", "otomatik yenilenecek": "", "süresi doldu": "b-bad", "başlamadı": "", süresiz: "" }[c.status] ?? ""}">${esc(c.status)}${c.days_left != null && c.days_left >= 0 && c.status !== "aktif" ? ` · ${c.days_left} gün` : ""}</span>`;

async function contractsTab(T, s) {
  const list = await api(`/api/suppliers/${s.id}/contracts`);
  const files = await api(`/api/evidence?supplier_id=${s.id}&kind=sözleşme`);
  T.innerHTML = `<div class="card flush"><div class="card-head"><h2>Sözleşmeler</h2><button class="sm" id="nc2" data-perm="contract:write">+ Sözleşme</button></div>
    ${list.length ? `<div class="table-wrap"><table><thead><tr><th>Sözleşme</th><th>Dönem</th><th>Durum</th><th>İhbar</th><th class="num">Bedel</th><th>Maddeler</th><th>Dosyalar</th><th></th></tr></thead><tbody>
    ${list.map((c) => `<tr><td><b>${esc(c.title)}</b><div class="small muted">${esc(c.contract_no || "")}${c.owner ? " · " + esc(c.owner) : ""}</div></td>
      <td class="small">${fmtDate(c.start_date)} – ${fmtDate(c.end_date)}${c.auto_renew ? `<div class="muted">otomatik yenilenir</div>` : ""}</td>
      <td>${contractBadge(c)}</td><td class="small">${c.notice_deadline ? fmtDate(c.notice_deadline) + `<div class="muted">${c.notice_days} gün önce</div>` : "—"}</td>
      <td class="num">${c.value != null ? Number(c.value).toLocaleString("tr-TR") + " " + esc(c.currency || "") : "—"}</td>
      <td class="small">${[c.has_dpa && "Veri işleme (DPA)", c.has_nda && "Gizlilik (NDA)", c.has_security_clause && "Güvenlik maddesi"].filter(Boolean).map((x) => `<span class="badge b-ok">${x}</span>`).join(" ") || `<span class="badge b-warn">eksik</span>`}</td>
      <td class="small">${files.filter((f) => f.contract_id === c.id).map((f) => `<a href="/api/evidence/${f.id}/download">📎 ${esc(f.title || f.original_name)}</a>`).join("<br>") || "—"}</td>
      <td style="white-space:nowrap" data-perm="contract:write"><button class="sm" data-cu="${c.id}">Dosya</button> <button class="sm" data-ce="${c.id}">Düzenle</button></td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty small">Sözleşme kaydı yok.</div>`}</div>`;
  const form = (c = { currency: "TRY", notice_days: 0 }) => modal(`<h2>${c.id ? "Sözleşmeyi düzenle" : "Yeni sözleşme"}</h2><form id="cf">
    <div class="row"><div><label>Başlık *</label><input name="title" required value="${esc(c.title || "")}"></div><div><label>Sözleşme no</label><input name="contract_no" value="${esc(c.contract_no || "")}"></div><div><label>Sorumlu</label><input name="owner" value="${esc(c.owner || "")}"></div></div>
    <div class="row"><div><label>Başlangıç</label><input type="date" name="start_date" value="${esc(c.start_date || "")}"></div><div><label>Bitiş</label><input type="date" name="end_date" value="${esc(c.end_date || "")}"></div>
      <div><label>Fesih ihbar süresi (gün)</label><input type="number" min="0" name="notice_days" value="${c.notice_days || 0}"></div></div>
    <div class="row"><div><label>Bedel</label><input type="number" min="0" step="0.01" name="value" value="${c.value ?? ""}"></div><div><label>Para birimi</label><input name="currency" maxlength="3" value="${esc(c.currency || "TRY")}"></div>
      <div><label>&nbsp;</label><label class="te-small"><input type="checkbox" name="auto_renew" ${c.auto_renew ? "checked" : ""}> Otomatik yenilenir</label></div></div>
    <div class="chips" style="margin-bottom:10px"><label class="te-small"><input type="checkbox" name="has_dpa" ${c.has_dpa ? "checked" : ""}> Veri işleme sözleşmesi (KVKK/GDPR)</label>
      <label class="te-small"><input type="checkbox" name="has_nda" ${c.has_nda ? "checked" : ""}> Gizlilik sözleşmesi</label><label class="te-small"><input type="checkbox" name="has_security_clause" ${c.has_security_clause ? "checked" : ""}> Bilgi güvenliği maddesi / denetim hakkı</label></div>
    <label>SLA / hizmet seviyeleri</label><textarea name="sla">${esc(c.sla || "")}</textarea><label style="margin-top:8px">Notlar</label><textarea name="notes">${esc(c.notes || "")}</textarea>
    <div class="actions" style="justify-content:space-between;margin-top:12px">${c.id ? `<button type="button" class="danger" id="cdel">Sil</button>` : "<span></span>"}<div class="actions"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Kaydet</button></div></div></form>`, (box) => {
    const f = box.querySelector("#cf");
    f.onsubmit = async (e) => {
      e.preventDefault();
      const d = formData(f);
      for (const k of ["auto_renew", "has_dpa", "has_nda", "has_security_clause"]) d[k] = f[k].checked;
      d.notice_days = +d.notice_days || 0; d.value = d.value === null ? null : +d.value;
      try { await api(c.id ? `/api/contracts/${c.id}` : `/api/suppliers/${s.id}/contracts`, { method: c.id ? "PUT" : "POST", body: d }); closeModal(); router(); } catch (err) { toast(err.message); }
    };
    const del = box.querySelector("#cdel");
    if (del) del.onclick = () => confirm("Sözleşme silinsin mi?") && api(`/api/contracts/${c.id}`, { method: "DELETE" }).then(() => { closeModal(); router(); });
  });
  document.getElementById("nc2").onclick = () => form();
  T.querySelectorAll("[data-ce]").forEach((b) => (b.onclick = () => form(list.find((c) => c.id == b.dataset.ce))));
  T.querySelectorAll("[data-cu]").forEach((b) => (b.onclick = () => uploadModal({ contract_id: b.dataset.cu, kind: "sözleşme" }, router)));
}
