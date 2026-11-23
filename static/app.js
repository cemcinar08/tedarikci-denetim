"use strict";
/* Tedarikçi Denetim — tek sayfalık arayüz (bağımlılıksız) */

const $app = document.getElementById("app");
let META = null;

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
const riskBadge = (r) => `<span class="badge ${{ yüksek: "b-bad", orta: "b-warn", düşük: "b-ok" }[r] || ""}">${esc(r ?? "—")}</span>`;
const sevBadge = (s) => `<span class="badge ${{ kritik: "b-crit", yüksek: "b-bad", orta: "b-warn", düşük: "" }[s] || ""}">${esc(s)}</span>`;
const statusBadge = (s) => `<span class="badge ${{ kapandı: "b-ok", "devam ediyor": "b-warn", açık: "b-bad", tamamlandı: "b-ok", planlandı: "", aktif: "b-ok", askıda: "b-warn", pasif: "" }[s] ?? ""}">${esc(s)}</span>`;
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
];

async function router() {
  if (!document.getElementById("modal").classList.contains("hidden")) closeModal();
  const h = location.hash || "#/";
  const top = h.split("/")[1] || "";
  const map = { tedarikci: "tedarikciler", denetim: "denetimler", tarama: "istihbarat", rapor: "tedarikciler", "soru-seti": "soru-setleri" };
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (map[top] ?? top)));
  for (const [re, fn] of routes) {
    const m = h.match(re);
    if (m) {
      try {
        if (!META) META = await api("/api/meta");
        await fn(...m.slice(1));
      } catch (e) {
        $app.innerHTML = `<div class="card"><b>Hata:</b> ${esc(e.message)}</div>`;
      }
      window.scrollTo(0, 0);
      return;
    }
  }
  $app.innerHTML = `<div class="empty">Sayfa bulunamadı.</div>`;
}
window.addEventListener("hashchange", router);
router();

// ================================================================ PANO
async function viewDashboard() {
  const d = await api("/api/dashboard");
  const t = d.totals;
  $app.innerHTML = `
  <div class="page-head"><div><h1>Pano</h1><div class="muted">Tedarikçi risk durumu — ${new Date().toLocaleDateString("tr-TR")}</div></div>
    <div class="actions"><a class="btn primary" href="#/tedarikciler">+ Tedarikçi</a></div></div>
  <div class="kpis">
    <div class="kpi"><div class="v">${t.suppliers}</div><div class="l">Aktif tedarikçi</div></div>
    <div class="kpi"><div class="v" style="color:var(--bad)">${d.risk["yüksek"]}</div><div class="l">Yüksek riskli tedarikçi</div></div>
    <div class="kpi"><div class="v">${t.open_findings}</div><div class="l">Açık bulgu</div></div>
    <div class="kpi"><div class="v" style="color:${t.overdue_findings ? "var(--bad)" : "inherit"}">${t.overdue_findings}</div><div class="l">Süresi geçmiş aksiyon</div></div>
    <div class="kpi"><div class="v" style="color:var(--ok)">${t.closed_last_30}</div><div class="l">Son 30 günde kapanan</div></div>
  </div>
  <div class="grid g3">
    <div class="card"><h2>Risk dağılımı</h2>${stackedBar([
      { l: "Yüksek", v: d.risk["yüksek"], c: "var(--bad)" }, { l: "Orta", v: d.risk["orta"], c: "var(--warn)" },
      { l: "Düşük", v: d.risk["düşük"], c: "var(--ok)" }, { l: "Değerlendirilmedi", v: d.risk["bilinmiyor"], c: "var(--border)" }])}</div>
    <div class="card"><h2>Açık bulgular (önem)</h2>${stackedBar([
      { l: "Kritik", v: d.severity["kritik"], c: "var(--crit)" }, { l: "Yüksek", v: d.severity["yüksek"], c: "var(--bad)" },
      { l: "Orta", v: d.severity["orta"], c: "var(--warn)" }, { l: "Düşük", v: d.severity["düşük"], c: "var(--info)" }])}</div>
    <div class="card"><h2>Siber güvenlik notları</h2>${stackedBar("ABCDF".split("").map((g, i) => ({
      l: g, v: d.grades[g], c: ["var(--ok)", "var(--ok)", "var(--warn)", "var(--bad)", "var(--crit)"][i] })))}</div>
  </div>
  <div class="card"><h2>Tedarikçi risk sıralaması</h2>
    ${d.ranking.length ? `<div class="table-wrap"><table><thead><tr><th>Tedarikçi</th><th>Kritiklik</th><th>Risk</th><th>Genel puan</th><th>Denetim</th><th>Siber</th><th class="num">Açık bulgu</th><th>Son denetim</th></tr></thead><tbody>
    ${d.ranking.map((s) => `<tr><td><a href="#/tedarikci/${s.id}">${esc(s.name)}</a></td><td>${esc(s.criticality)}</td><td>${riskBadge(s.risk)}</td>
      <td style="min-width:140px">${meter(s.overall_score)}</td><td>${num(s.audit_score)}</td><td>${gradeBox(s.intel_grade)}</td>
      <td class="num">${s.open_findings}${s.critical_findings ? ` <span class="badge b-crit">${s.critical_findings} kritik</span>` : ""}</td><td>${fmtDate(s.last_audit_date)}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">Henüz tedarikçi yok. <a href="#/tedarikciler">İlk tedarikçiyi ekleyin</a>.</div>`}
  </div>
  <div class="grid g2">
    <div class="card"><h2>Süresi geçmiş aksiyonlar</h2>
      ${d.overdue.length ? `<table><tbody>${d.overdue.map((f) => `<tr><td>${sevBadge(f.severity)}</td><td>${esc(f.title)}<div class="small muted"><a href="#/tedarikci/${f.supplier_id}">${esc(f.supplier_name)}</a> · Sorumlu: ${esc(f.owner || "—")}</div></td><td class="small" style="color:var(--bad)">${fmtDate(f.due_date)}</td></tr>`).join("")}</tbody></table>`
        : `<div class="empty">Süresi geçmiş aksiyon yok 👍</div>`}</div>
    <div class="card"><h2>Planlanan denetimler</h2>
      ${d.upcoming.length ? `<table><tbody>${d.upcoming.map((a) => `<tr><td>${fmtDate(a.audit_date)}</td><td><a href="#/denetim/${a.id}">${esc(a.supplier_name)}</a><div class="small muted">${esc(a.audit_type)} · ${esc(a.auditor || "—")}</div></td><td>${statusBadge(a.status)}</td></tr>`).join("")}</tbody></table>`
        : `<div class="empty">Planlı denetim yok.</div>`}
      ${d.stale.length ? `<h3>Son 12 ayda denetlenmemiş (orta/yüksek kritik)</h3><div class="chips">${d.stale.map((s) => `<a class="chip" href="#/tedarikci/${s.id}">${esc(s.name)}</a>`).join("")}</div>` : ""}
    </div>
  </div>`;
}

// ================================================================ TEDARİKÇİLER
async function viewSuppliers() {
  const list = await api("/api/suppliers");
  $app.innerHTML = `
  <div class="page-head"><div><h1>Tedarikçiler</h1><div class="muted">${list.length} kayıt</div></div>
    <div class="actions"><button class="primary" id="add">+ Yeni tedarikçi</button></div></div>
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
      <td>${num(s.summary.audit_score)} ${ratingBadge(s.summary.audit_rating)}</td><td>${gradeBox(s.summary.intel_grade)}</td>
      <td>${riskBadge(s.summary.risk)}</td><td class="num">${s.summary.open_findings}</td><td>${statusBadge(s.status)}</td></tr>`).join("")
      : `<tr><td colspan="9" class="empty">Kayıt yok.</td></tr>`;
  };
  document.getElementById("q").oninput = draw;
  document.getElementById("fr").onchange = draw;
  document.getElementById("add").onclick = () => supplierForm();
  draw();
}

function supplierForm(s = {}) {
  const sel = (name, opts, val) => `<select name="${name}">${opts.map((o) => `<option ${o === val ? "selected" : ""}>${o}</option>`).join("")}</select>`;
  modal(`<h2>${s.id ? "Tedarikçiyi düzenle" : "Yeni tedarikçi"}</h2>
  <form id="f">
    <div class="row"><div><label>Firma adı *</label><input name="name" required value="${esc(s.name || "")}"></div>
      <div><label>Kategori</label><input name="category" placeholder="ör. Hammadde, Lojistik, Yazılım" value="${esc(s.category || "")}"></div></div>
    <div class="row"><div><label>Kritiklik</label>${sel("criticality", ["düşük", "orta", "yüksek"], s.criticality || "orta")}</div>
      <div><label>Durum</label>${sel("status", ["aktif", "askıda", "pasif"], s.status || "aktif")}</div>
      <div><label>Ülke</label><input name="country" value="${esc(s.country || "")}"></div></div>
    <div class="row"><div><label>Yetkili</label><input name="contact_name" value="${esc(s.contact_name || "")}"></div>
      <div><label>E-posta</label><input name="email" type="email" value="${esc(s.email || "")}"></div>
      <div><label>Telefon</label><input name="phone" value="${esc(s.phone || "")}"></div></div>
    <div style="margin-bottom:12px"><label>Domain'ler (dış istihbarat için; virgül veya satır ile ayırın)</label>
      <textarea name="domains" placeholder="ornekfirma.com.tr&#10;ornekfirma.com">${esc((s.domains || []).join("\n"))}</textarea></div>
    <div style="margin-bottom:12px"><label>Notlar</label><textarea name="notes">${esc(s.notes || "")}</textarea></div>
    <div class="actions" style="justify-content:flex-end"><button type="button" onclick="closeModal()">Vazgeç</button><button class="primary">Kaydet</button></div>
  </form>`, (box) => {
    box.querySelector("#f").onsubmit = async (e) => {
      e.preventDefault();
      const d = formData(e.target);
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
  const tabs = [["ozet", "Özet"], ["denetimler", `Denetimler (${s.audits.length})`], ["bulgular", `Bulgular (${sm.open_findings} açık)`], ["istihbarat", "Dış İstihbarat"]];
  $app.innerHTML = `
  <div class="page-head"><div><div class="small"><a href="#/tedarikciler">← Tedarikçiler</a></div>
    <h1>${esc(s.name)}</h1><div class="muted">${esc([s.category, s.country].filter(Boolean).join(" · ") || "—")} · Kritiklik: <b>${esc(s.criticality)}</b> · ${statusBadge(s.status)}</div></div>
    <div class="actions"><button id="edit">Düzenle</button><a class="btn" href="#/rapor/${s.id}">Rapor</a><button class="primary" id="newAudit">+ Denetim planla</button></div></div>
  <div class="kpis">
    <div class="kpi"><div class="l">Risk seviyesi</div><div class="v">${riskBadge(sm.risk)}</div></div>
    <div class="kpi"><div class="l">Genel puan</div><div class="v" style="color:${scoreColor(sm.overall_score)}">${num(sm.overall_score)}</div></div>
    <div class="kpi"><div class="l">Son denetim puanı</div><div class="v">${num(sm.audit_score)}</div><div class="l">${ratingBadge(sm.audit_rating)} ${fmtDate(sm.last_audit_date)}</div></div>
    <div class="kpi"><div class="l">Siber güvenlik notu</div><div class="v">${gradeBox(sm.intel_grade)} <span style="font-size:16px">${num(sm.intel_score)}</span></div><div class="l">${sm.last_scan ? "Tarama: " + fmtDT(sm.last_scan) : "Henüz taranmadı"}</div></div>
    <div class="kpi"><div class="l">Açık / gecikmiş bulgu</div><div class="v">${sm.open_findings} <span style="font-size:16px;color:var(--bad)">/ ${sm.overdue_findings}</span></div></div>
  </div>
  <div class="tabs">${tabs.map(([k, l]) => `<button class="${k === tab ? "active" : ""}" onclick="location.hash='#/tedarikci/${s.id}/${k}'">${l}</button>`).join("")}</div>
  <div id="tab"></div>`;
  document.getElementById("edit").onclick = () => supplierForm(s);
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
      <div style="margin-top:16px"><button class="danger sm" id="del">Tedarikçiyi sil</button></div></div>
      <div class="card"><h2>Öncelikli açık bulgular</h2>${findingsTable(s.findings.filter((f) => f.status !== "kapandı").slice(0, 8), { compact: true })}</div></div>`;
    document.getElementById("del").onclick = async () => {
      if (!confirm(`"${s.name}" ve tüm denetim/bulgu/tarama kayıtları silinsin mi?`)) return;
      await api(`/api/suppliers/${s.id}`, { method: "DELETE" });
      location.hash = "#/tedarikciler";
    };
  } else if (tab === "denetimler") {
    T.innerHTML = `<div class="card">${auditsTable(s.audits)}</div>`;
  } else if (tab === "bulgular") {
    T.innerHTML = `<div class="card"><div class="actions" style="margin-bottom:12px"><button id="nf">+ Manuel bulgu ekle</button></div>
      <div id="ft"></div></div>`;
    document.getElementById("ft").innerHTML = findingsTable(s.findings);
    bindFindingActions(T);
    document.getElementById("nf").onclick = () => findingForm({ supplier_id: s.id });
  } else if (tab === "istihbarat") {
    T.innerHTML = `<div class="card"><div class="page-head" style="margin-bottom:8px"><div><h2>Domain güvenlik değerlendirmesi</h2>
      <div class="muted small">Pasif ve herkese açık kaynaklar: DNS, e-posta güvenliği (SPF/DMARC), TLS, HTTP güvenlik başlıkları, açık servisler ve bilinen CVE'ler (Shodan InternetDB), sertifika şeffaflığı (crt.sh), domain kaydı (RDAP).</div></div>
      <div class="actions"><label style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" id="cf" checked style="width:auto"> Başarısız kontrollerden bulgu oluştur</label>
      <button class="primary" id="scan" ${s.domains.length ? "" : "disabled"}>Tüm domain'leri tara</button></div></div>
      ${s.domains.length ? "" : `<div class="empty">Bu tedarikçi için domain tanımlı değil. <a href="#" id="ed2">Domain ekleyin</a>.</div>`}
      ${s.scans.length ? `<table><thead><tr><th>Domain</th><th>Not</th><th>Puan</th><th>Özet</th><th>Tarih</th><th></th></tr></thead><tbody>
        ${s.scans.map((x) => { const su = x.result.summary; return `<tr><td><b>${esc(x.domain)}</b></td><td>${gradeBox(x.grade)}</td><td style="min-width:140px">${meter(x.score)}</td>
        <td class="small"><span style="color:var(--bad)">${su.fail} başarısız</span> · <span style="color:var(--warn)">${su.warn} uyarı</span> · <span style="color:var(--ok)">${su.pass} geçti</span>${su.error ? ` · ${su.error} ölçülemedi` : ""}</td>
        <td class="small">${fmtDT(x.scanned_at)}</td><td><a href="#/tarama/${x.id}">Detay →</a></td></tr>`; }).join("")}</tbody></table>` : ""}
      </div>
      ${s.scan_history.length > s.scans.length ? `<div class="card"><h2>Tarama geçmişi</h2><table><tbody>${s.scan_history.map((x) => `<tr><td>${fmtDT(x.scanned_at)}</td><td>${esc(x.domain)}</td><td>${gradeBox(x.grade)}</td><td>${num(x.score)}</td><td><a href="#/tarama/${x.id}">Detay</a></td></tr>`).join("")}</tbody></table></div>` : ""}`;
    const ed2 = document.getElementById("ed2"); if (ed2) ed2.onclick = (e) => { e.preventDefault(); supplierForm(s); };
    document.getElementById("scan").onclick = async (e) => {
      const b = e.target; b.disabled = true; b.innerHTML = `<span class="spinner"></span> Taranıyor…`;
      try {
        const r = await api(`/api/suppliers/${s.id}/scan?create_findings=${document.getElementById("cf").checked}`, { method: "POST" });
        const c = r.reduce((a, x) => a + x.findings_created, 0), k = r.reduce((a, x) => a + x.findings_closed, 0);
        toast(`Tarama tamamlandı. ${c} yeni bulgu, ${k} bulgu otomatik kapatıldı.`);
        router();
      } catch (err) { toast(err.message); b.disabled = false; b.textContent = "Tüm domain'leri tara"; }
    };
  }
}

// ================================================================ DENETİMLER
function auditsTable(list, withSupplier = false) {
  if (!list.length) return `<div class="empty">Denetim kaydı yok.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Tarih</th>${withSupplier ? "<th>Tedarikçi</th>" : ""}<th>Soru setleri</th><th>Tür</th><th>Denetçi</th><th>Durum</th><th>Puan</th><th></th></tr></thead><tbody>
  ${list.map((a) => `<tr><td>${fmtDate(a.audit_date)}</td>${withSupplier ? `<td><a href="#/tedarikci/${a.supplier_id}">${esc(a.supplier_name)}</a></td>` : ""}
    <td class="small">${esc((a.set_names || ["Genel Tedarikçi Denetimi"]).join(", "))}</td>
    <td>${esc(a.audit_type)}</td><td>${esc(a.auditor || "—")}</td><td>${statusBadge(a.status)}</td><td style="min-width:140px">${meter(a.score)}</td>
    <td><a href="#/denetim/${a.id}">Aç →</a></td></tr>`).join("")}</tbody></table></div>`;
}

async function viewAudits() {
  const list = await api("/api/audits");
  $app.innerHTML = `<div class="page-head"><div><h1>Denetimler</h1><div class="muted">${list.length} denetim</div></div>
    <div class="actions"><button class="primary" id="na">+ Denetim planla</button></div></div><div class="card">${auditsTable(list, true)}</div>`;
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
    <div class="small muted" style="margin-bottom:8px">Bu denetime özel sorular ekleyin. İsterseniz kütüphaneye kaydedip sonraki denetimlerde tekrar kullanabilirsiniz.</div>
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
      <div class="small">${x.sections.length} bölüm · <b>${x.question_count}</b> soru${x.used_in ? ` · ${x.used_in} denetimde kullanıldı` : ""}</div>
      <div class="actions" style="margin-top:10px"><a class="btn sm" href="#/soru-seti/${x.id}">${x.builtin ? "Görüntüle" : "Düzenle"}</a>
        <button class="sm" data-clone="${x.id}">Kopyala</button></div></div>`;
  $app.innerHTML = `<div class="page-head"><div><h1>Soru Setleri</h1><div class="muted">Denetimlerde kullanılacak standart ve özel soru setleri</div></div>
    <div class="actions"><a class="btn primary" href="#/soru-seti/yeni">+ Yeni özel set</a></div></div>
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

async function viewQuestionSet(id) {
  const isNew = id === "yeni";
  const x = isNew ? { name: "", standard: "Özel", description: "", sections: [], builtin: false } : await api(`/api/question-sets/${id}`);
  const head = `<div class="small"><a href="#/soru-setleri">← Soru setleri</a></div>`;
  if (x.builtin) {
    $app.innerHTML = `<div class="page-head"><div>${head}<h1>${esc(x.name)}</h1><div class="muted">${esc(x.description || "")}</div></div>
      <div class="actions"><button class="primary" id="clone">Kopyala ve düzenle</button></div></div>
      ${x.sections.map((sec) => `<div class="card"><h2>${esc(sec.title)} <span class="muted small">· ağırlık ${num(sec.weight)}</span></h2>
        <ol style="margin:0;padding-left:20px">${sec.questions.map((q) => `<li style="margin-bottom:4px">${esc(q.text)}</li>`).join("")}</ol></div>`).join("")}`;
    document.getElementById("clone").onclick = async () => {
      const c = await api(`/api/question-sets/${x.id}/clone`, { method: "POST" }); location.hash = `#/soru-seti/${c.id}`;
    };
    return;
  }
  $app.innerHTML = `<div class="page-head"><div>${head}<h1>${isNew ? "Yeni özel soru seti" : "Soru setini düzenle"}</h1></div>
    <div class="actions">${isNew ? "" : `<button class="danger" id="del">Sil</button>`}<button class="primary" id="save">Kaydet</button></div></div>
    <div class="card"><div class="row" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">
      <div><label>Set adı *</label><input id="n" value="${esc(x.name)}" placeholder="ör. Depo ve Lojistik Denetimi"></div>
      <div><label>Standart / etiket</label><input id="st" value="${esc(x.standard || "Özel")}" placeholder="ör. Özel, TISAX, SOC 2"></div></div>
      <div style="margin-top:12px"><label>Açıklama</label><textarea id="ds" style="min-height:50px">${esc(x.description || "")}</textarea></div></div>
    <div class="card"><h2>Bölümler ve sorular</h2><div class="small muted" style="margin-bottom:10px">Her satır bir sorudur. Ağırlık, bölümün toplam puana etkisini belirler (ör. 30 ve 10 ağırlıklı iki bölümde ilki 3 kat etkilidir).</div><div id="sb"></div></div>`;
  const sb = sectionBuilder(document.getElementById("sb"), x.sections.length ? x.sections : [{ title: "", weight: 10, questions: [] }]);
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

async function viewAudit(id) {
  const a = await api(`/api/audits/${id}`);
  const answers = { ...a.answers };
  const opts = [["uygun", "Uygun"], ["kısmen", "Kısmen"], ["uygun değil", "Uygun değil"], ["na", "N/A"]];
  $app.innerHTML = `
  <div class="page-head"><div><div class="small"><a href="#/tedarikci/${a.supplier_id}/denetimler">← ${esc(a.supplier_name)}</a></div>
    <h1>Denetim — ${fmtDate(a.audit_date)}</h1><div class="muted">${esc(a.audit_type)} · Denetçi: ${esc(a.auditor || "—")}</div>
    <div class="chips" style="margin-top:6px">${(a.set_names || []).map((n) => `<span class="badge">${esc(n)}</span>`).join("")}</div></div>
    <div class="actions"><button class="danger" id="del">Sil</button><button id="gen">Bulguları oluştur</button><button class="primary" id="save">Kaydet</button></div></div>
  <div class="grid" style="grid-template-columns:minmax(0,1fr) 300px;align-items:start" id="lay">
    <div>${a.checklist.map((sec) => `<div class="card"><h2>${esc(sec.title)} <span class="muted small">· ağırlık ${num(sec.weight)}${(a.set_names || []).length > 1 && sec.source ? " · " + esc(sec.source) : ""}</span></h2>
      ${sec.questions.map((q) => `<div class="q" data-q="${q.id}"><div>${esc(q.text)}</div>
        <div class="seg">${opts.map(([v, l]) => `<button type="button" data-v="${v}" class="${answers[q.id]?.answer === v ? "on" : ""}">${l}</button>`).join("")}</div>
        <input class="note" placeholder="Not / kanıt" value="${esc(answers[q.id]?.note || "")}"></div>`).join("")}</div>`).join("")}</div>
    <div style="position:sticky;top:72px;max-height:calc(100vh - 88px);overflow-y:auto">
      <div class="card"><h2>Sonuç</h2><div id="res"></div></div>
      <div class="card"><label>Durum</label><select id="st">${["planlandı", "devam ediyor", "tamamlandı"].map((s) => `<option ${s === a.status ? "selected" : ""}>${s}</option>`).join("")}</select>
        <label style="margin-top:10px">Genel notlar</label><textarea id="nt">${esc(a.notes || "")}</textarea>
        <div class="small muted" style="margin-top:8px">Pano ve risk hesaplarında yalnızca <b>tamamlandı</b> durumundaki denetimler kullanılır.</div></div>
      ${a.findings.length ? `<div class="card"><h2>Bu denetimden bulgular</h2>${a.findings.map((f) => `<div class="small" style="margin-bottom:6px">${sevBadge(f.severity)} ${statusBadge(f.status)} ${esc(f.title)}</div>`).join("")}</div>` : ""}
    </div></div>`;
  if (window.innerWidth < 900) document.getElementById("lay").style.gridTemplateColumns = "1fr";

  const calc = () => {
    // istemci tarafı canlı önizleme (sunucu ile aynı formül)
    const pts = { uygun: 1, kısmen: 0.5, "uygun değil": 0 };
    let tot = 0, tw = 0, answered = 0, total = 0;
    const secs = a.checklist.map((sec) => {
      const p = sec.questions.map((q) => answers[q.id]?.answer).filter((x) => x in pts).map((x) => pts[x]);
      total += sec.questions.length; answered += sec.questions.filter((q) => answers[q.id]?.answer).length;
      const pct = p.length ? (100 * p.reduce((x, y) => x + y, 0)) / p.length : null;
      if (pct != null) { tot += pct * sec.weight; tw += sec.weight; }
      return { title: sec.title, pct };
    });
    const score = tw ? Math.round((tot / tw) * 10) / 10 : null;
    const rating = score == null ? null : score >= 85 ? "Onaylı" : score >= 70 ? "Şartlı Onaylı" : score >= 50 ? "Gelişim Gerekli" : "Onaysız";
    document.getElementById("res").innerHTML = `<div style="font-size:36px;font-weight:800;color:${scoreColor(score)}">${num(score)}</div>
      <div>${ratingBadge(rating)}</div><div class="small muted" style="margin:6px 0 10px">${answered}/${total} soru cevaplandı</div>
      ${secs.map((x) => `<div class="small" style="margin-bottom:6px">${esc(x.title)}${meter(x.pct == null ? null : Math.round(x.pct))}</div>`).join("")}`;
  };
  calc();

  $app.querySelectorAll(".q").forEach((el) => {
    const q = el.dataset.q;
    el.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => {
      const v = answers[q]?.answer === b.dataset.v ? null : b.dataset.v;
      answers[q] = { ...(answers[q] || {}), answer: v };
      el.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x.dataset.v === v));
      calc();
    }));
    el.querySelector(".note").oninput = (e) => (answers[q] = { ...(answers[q] || {}), note: e.target.value });
  });

  const save = async () => {
    await api(`/api/audits/${id}`, { method: "PUT", body: { status: document.getElementById("st").value, notes: document.getElementById("nt").value, answers } });
  };
  document.getElementById("save").onclick = async () => { try { await save(); toast("Denetim kaydedildi"); } catch (e) { toast(e.message); } };
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
function findingsTable(list, { compact = false, withSupplier = false } = {}) {
  if (!list.length) return `<div class="empty">Bulgu yok.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Önem</th>${withSupplier ? "<th>Tedarikçi</th>" : ""}<th>Bulgu</th>${compact ? "" : "<th>Kaynak</th><th>Sorumlu</th>"}<th>Termin</th><th>Durum</th>${compact ? "" : "<th></th>"}</tr></thead><tbody>
  ${list.map((f) => `<tr><td>${sevBadge(f.severity)}</td>${withSupplier ? `<td><a href="#/tedarikci/${f.supplier_id}/bulgular">${esc(f.supplier_name)}</a></td>` : ""}
    <td><b>${esc(f.title)}</b>${!compact && f.action ? `<div class="small" style="color:var(--primary)">Aksiyon: ${esc(f.action)}</div>` : ""}${!compact && f.description ? `<div class="small muted" style="white-space:pre-wrap">${esc(f.description)}</div>` : ""}</td>
    ${compact ? "" : `<td><span class="badge">${esc(f.source)}</span></td><td>${esc(f.owner || "—")}</td>`}
    <td class="small" style="${overdue(f) ? "color:var(--bad);font-weight:700" : ""}">${fmtDate(f.due_date)}${overdue(f) ? "<br>gecikti" : ""}</td>
    <td>${compact ? statusBadge(f.status) : `<select data-fid="${f.id}" class="fstatus" style="width:auto">${["açık", "devam ediyor", "kapandı"].map((s) => `<option ${s === f.status ? "selected" : ""}>${s}</option>`).join("")}</select>`}</td>
    ${compact ? "" : `<td><button class="sm" data-edit="${f.id}">Düzenle</button></td>`}</tr>`).join("")}</tbody></table></div>`;
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
}

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
    <div class="actions"><button id="csv">CSV indir</button></div></div>
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
    document.getElementById("ft").innerHTML = findingsTable(rows, { withSupplier: true });
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
  document.getElementById("csv").onclick = () => {
    const cols = ["id", "supplier_name", "severity", "title", "source", "status", "owner", "due_date", "action", "description", "created_at", "closed_at"];
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
  $app.innerHTML = `<div class="page-head"><div><div class="small"><a href="#/tedarikci/${s.supplier_id}/istihbarat">← Tedarikçiye dön</a></div><h1>Tarama sonucu</h1></div></div>${scanDetail(s.result)}`;
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
