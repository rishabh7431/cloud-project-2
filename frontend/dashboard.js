/* ==========================================================================
   Nutritional Insights — dashboard data + visualizations
   Renders the four charts and wires filters / pagination / recipes / clusters
   to the Azure Function. Auth is handled separately in dashboard.html
   (Supabase); this script only runs for a signed-in user.
   ========================================================================== */
const API_BASE = "https://project2-func-gsfhenfdbae0bsh9.eastus-01.azurewebsites.net/api";
const ENDPOINTS = {
  INSIGHTS: API_BASE + "/analyze_diets",
  RECIPES: API_BASE + "/recipes",
  CLUSTERS: API_BASE + "/clusters",
};

(() => {
  "use strict";

  const COLORS = { protein: "#2563eb", carbs: "#f59e0b", fat: "#ef4444" };
  const PIE_PALETTE = ["#2563eb", "#7c3aed", "#f59e0b", "#16a34a", "#ef4444", "#0891b2", "#db2777"];
  const NUTRIENTS = ["Protein(g)", "Carbs(g)", "Fat(g)"];
  const NUTRIENT_SHORT = { "Protein(g)": "Prot", "Carbs(g)": "Carb", "Fat(g)": "Fat" };

  const charts = { bar: null, scatter: null, pie: null };
  let insights = null;
  let recipesPage = 1;
  let recipesTotalPages = 1;
  let activePanel = null; // "recipes" | "clusters" | null

  const $ = (id) => document.getElementById(id);
  const P = (r) => Number(r["Protein(g)"]) || 0;
  const C = (r) => Number(r["Carbs(g)"]) || 0;
  const F = (r) => Number(r["Fat(g)"]) || 0;

  /* --------------------------- Fetch helper --------------------------- */
  async function fetchJson(url) {
    const started = performance.now();
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    const ms = performance.now() - started;
    if (!res.ok) throw new Error("HTTP " + res.status + " " + res.statusText);
    return { payload: await res.json(), ms: ms };
  }

  function setStatus(msg, kind) {
    const s = $("apiStatus");
    if (!s) return;
    s.textContent = msg;
    s.className = "text-sm mt-3 " + (kind === "error" ? "text-red-600" : kind === "ok" ? "text-green-700" : "text-gray-600");
  }

  const fmtExec = (secs, fallbackMs) =>
    (secs != null ? (secs * 1000).toFixed(0) + " ms" : Math.round(fallbackMs) + " ms");

  /* --------------------------- Filters --------------------------- */
  function currentFilter() {
    return {
      sel: $("dietFilter").value,
      q: $("searchInput").value.trim().toLowerCase(),
    };
  }

  function filterByDiet(rows) {
    const f = currentFilter();
    return rows.filter((r) => {
      const d = String(r.Diet_type || "");
      const matchSel = f.sel === "all" || d.toLowerCase() === f.sel.toLowerCase();
      const matchQ = !f.q || d.toLowerCase().includes(f.q);
      return matchSel && matchQ;
    });
  }

  function populateDietFilter(list) {
    const sel = $("dietFilter");
    const current = sel.value;
    sel.innerHTML = '<option value="all">All Diet Types</option>';
    (list || []).forEach((d) => {
      const o = document.createElement("option");
      o.value = d; o.textContent = d;
      sel.appendChild(o);
    });
    if ([].slice.call(sel.options).some((o) => o.value === current)) sel.value = current;
  }

  /* --------------------------- Charts --------------------------- */
  function renderCharts() {
    if (!insights) return;
    const bars = filterByDiet((insights.bar_chart && insights.bar_chart.data) || insights.average_macros || []);
    const scatterRows = filterByDiet((insights.scatter_plot && insights.scatter_plot.data) || []);
    const pieRows = filterByDiet((insights.pie_chart && insights.pie_chart.data) || []);

    // Bar
    const labels = bars.map((r) => r.Diet_type);
    if (charts.bar) charts.bar.destroy();
    charts.bar = new Chart($("barChart"), {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          { label: "Protein (g)", data: bars.map(P), backgroundColor: COLORS.protein, borderRadius: 4 },
          { label: "Carbs (g)", data: bars.map(C), backgroundColor: COLORS.carbs, borderRadius: 4 },
          { label: "Fat (g)", data: bars.map(F), backgroundColor: COLORS.fat, borderRadius: 4 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12, padding: 6, font: { size: 9 } } } },
        scales: { y: { beginAtZero: true, grid: { color: "#eef1f5" } }, x: { grid: { display: false } } },
      },
    });

    // Scatter (protein vs carbs, colored by diet)
    const byDiet = {};
    scatterRows.forEach((r) => {
      const d = r.Diet_type || "Other";
      (byDiet[d] = byDiet[d] || []).push({ x: P(r), y: C(r), name: r.Recipe_name });
    });
    const scatterSets = Object.keys(byDiet).map((d, i) => ({
      label: d,
      data: byDiet[d],
      backgroundColor: PIE_PALETTE[i % PIE_PALETTE.length],
      pointRadius: 4, pointHoverRadius: 6,
    }));
    if (charts.scatter) charts.scatter.destroy();
    charts.scatter = new Chart($("scatterPlot"), {
      type: "scatter",
      data: { datasets: scatterSets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, padding: 6, font: { size: 9 } } },
          tooltip: { callbacks: { label: (c) => (c.raw.name ? c.raw.name + ": " : "") + "P " + c.parsed.x + "g, C " + c.parsed.y + "g" } },
        },
        scales: {
          x: { title: { display: true, text: "Protein (g)" }, grid: { color: "#eef1f5" } },
          y: { title: { display: true, text: "Carbs (g)" }, grid: { color: "#eef1f5" } },
        },
      },
    });

    // Heatmap (nutrient correlation matrix)
    renderHeatmap((insights.heatmap && insights.heatmap.data) || []);

    // Pie (recipe counts)
    const pieLabels = pieRows.map((r) => r.Diet_type);
    const pieData = pieRows.map((r) => Number(r.Recipe_count) || 0);
    if (charts.pie) charts.pie.destroy();
    charts.pie = new Chart($("pieChart"), {
      type: "pie",
      data: { labels: pieLabels, datasets: [{ data: pieData, backgroundColor: pieLabels.map((_, i) => PIE_PALETTE[i % PIE_PALETTE.length]), borderColor: "#fff", borderWidth: 2 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, padding: 6, font: { size: 9 } } },
          tooltip: {
            callbacks: {
              label: (c) => {
                const total = c.dataset.data.reduce((a, b) => a + b, 0);
                const pct = total ? ((c.parsed / total) * 100).toFixed(1) : 0;
                return " " + c.label + ": " + c.parsed + " recipes (" + pct + "%)";
              },
            },
          },
        },
      },
    });
  }

  /* --------------------------- Heatmap (HTML grid) --------------------------- */
  function renderHeatmap(data) {
    const box = $("heatmap");
    box.innerHTML = "";
    if (!data.length) return;
    box.style.display = "grid";
    box.style.gap = "3px";
    box.style.alignContent = "center";
    box.style.gridTemplateColumns = "minmax(42px, 0.8fr) repeat(" + NUTRIENTS.length + ", 1fr)";

    const lookup = {};
    data.forEach((d) => { (lookup[d.y] = lookup[d.y] || {})[d.x] = d.value; });

    box.appendChild(labelCell(""));
    NUTRIENTS.forEach((n) => box.appendChild(labelCell(NUTRIENT_SHORT[n])));

    NUTRIENTS.forEach((yN) => {
      box.appendChild(labelCell(NUTRIENT_SHORT[yN]));
      NUTRIENTS.forEach((xN) => {
        const v = (lookup[yN] && lookup[yN][xN] != null) ? lookup[yN][xN] : 0;
        const c = document.createElement("div");
        c.textContent = (Math.round(v * 100) / 100).toFixed(2);
        c.style.cssText = "display:flex;align-items:center;justify-content:center;font-size:0.72rem;font-weight:600;border-radius:3px;min-height:26px;";
        c.style.background = lerpColor([255, 255, 204], [37, 52, 148], v);
        c.style.color = v > 0.55 ? "#fff" : "#1f2328";
        box.appendChild(c);
      });
    });
  }

  function labelCell(text) {
    const d = document.createElement("div");
    d.textContent = text;
    d.style.cssText = "display:flex;align-items:center;font-size:0.72rem;font-weight:600;color:#6b7280;padding:0 3px;";
    return d;
  }
  function lerpColor(a, b, t) {
    const tt = Math.max(0, Math.min(1, t));
    const c = a.map((v, i) => Math.round(v + (b[i] - v) * tt));
    return "rgb(" + c[0] + ", " + c[1] + ", " + c[2] + ")";
  }

  /* --------------------------- Insights --------------------------- */
  async function loadInsights() {
    disableApi(true);
    setStatus("Loading nutritional insights...");
    try {
      const out = await fetchJson(ENDPOINTS.INSIGHTS);
      insights = out.payload;
      const meta = insights.metadata || {};
      populateDietFilter((insights.filters && insights.filters.diet_types) || []);
      renderCharts();
      setStatus(
        "Loaded " + (meta.total_recipes != null ? meta.total_recipes.toLocaleString() : "?") +
        " recipes across " + (meta.total_diet_types != null ? meta.total_diet_types : "?") +
        " diet types · execution time " + fmtExec(meta.execution_time_seconds, out.ms), "ok");
    } catch (err) {
      console.error(err);
      setStatus("Could not load insights: " + err.message + " (check the function URL / CORS).", "error");
    } finally {
      disableApi(false);
    }
  }

  /* --------------------------- Recipes --------------------------- */
  function recipesUrl(page) {
    const f = currentFilter();
    const params = new URLSearchParams({ page: String(page), page_size: "10" });
    if (f.sel !== "all") params.set("diet_type", f.sel);
    if (f.q) params.set("search", $("searchInput").value.trim());
    return ENDPOINTS.RECIPES + "?" + params.toString();
  }

  async function loadRecipes(page) {
    activePanel = "recipes";
    disableApi(true);
    setStatus("Loading recipes...");
    try {
      const out = await fetchJson(recipesUrl(page || 1));
      const p = out.payload;
      const pag = p.pagination || {};
      recipesPage = pag.page || page || 1;
      recipesTotalPages = pag.total_pages || 1;
      renderRecipesTable(p.recipes || []);
      renderPagination();
      setStatus(
        "Page " + recipesPage + " of " + recipesTotalPages + " · " +
        (pag.total_items != null ? pag.total_items.toLocaleString() : "?") +
        " matching recipes · execution time " + fmtExec(p.execution_time_seconds, out.ms), "ok");
    } catch (err) {
      console.error(err);
      setStatus("Could not load recipes: " + err.message, "error");
    } finally {
      disableApi(false);
    }
  }

  function renderRecipesTable(rows) {
    const box = $("results");
    box.hidden = false;
    if (!rows.length) {
      box.innerHTML = '<h3 class="text-lg font-semibold mb-2">Recipes</h3><p class="text-sm text-gray-600">No recipes match the current filters.</p>';
      return;
    }
    let html = '<h3 class="text-lg font-semibold mb-2">Recipes</h3>' +
      '<div class="overflow-x-auto bg-white shadow-lg rounded-lg"><table class="min-w-full text-sm">' +
      '<thead class="bg-gray-100"><tr>' +
      '<th class="text-left p-3 font-semibold">Diet</th>' +
      '<th class="text-left p-3 font-semibold">Recipe</th>' +
      '<th class="text-left p-3 font-semibold">Cuisine</th>' +
      '<th class="text-right p-3 font-semibold">Protein</th>' +
      '<th class="text-right p-3 font-semibold">Carbs</th>' +
      '<th class="text-right p-3 font-semibold">Fat</th>' +
      '</tr></thead><tbody>';
    rows.forEach((r) => {
      html += '<tr class="border-t">' +
        '<td class="p-3">' + esc(r.Diet_type) + '</td>' +
        '<td class="p-3">' + esc(r.Recipe_name) + '</td>' +
        '<td class="p-3">' + esc(r.Cuisine_type) + '</td>' +
        '<td class="p-3 text-right">' + P(r) + '</td>' +
        '<td class="p-3 text-right">' + C(r) + '</td>' +
        '<td class="p-3 text-right">' + F(r) + '</td>' +
        '</tr>';
    });
    html += "</tbody></table></div>";
    box.innerHTML = html;
  }

  /* --------------------------- Clusters --------------------------- */
  async function loadClusters() {
    activePanel = "clusters";
    disableApi(true);
    setStatus("Loading clusters...");
    try {
      const out = await fetchJson(ENDPOINTS.CLUSTERS);
      const p = out.payload;
      renderClusters(p.clusters || []);
      recipesTotalPages = 1; recipesPage = 1; renderPagination();
      setStatus("Loaded " + (p.clusters || []).length + " clusters · execution time " + fmtExec(p.execution_time_seconds, out.ms), "ok");
    } catch (err) {
      console.error(err);
      setStatus("Could not load clusters: " + err.message, "error");
    } finally {
      disableApi(false);
    }
  }

  function renderClusters(clusters) {
    const box = $("results");
    box.hidden = false;
    let html = '<h3 class="text-lg font-semibold mb-2">Diet Clusters</h3>' +
      '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">';
    clusters.forEach((c) => {
      html += '<div class="bg-white p-4 shadow-lg rounded-lg">' +
        '<h4 class="font-semibold">' + esc(c.cluster_name) + '</h4>' +
        '<p class="text-sm text-gray-600 mb-3">' + esc((c.diet_types || []).join(", ")) + '</p>' +
        '<div class="flex gap-4 text-sm">' +
        '<span>Protein<br><span class="font-semibold text-base">' + round1(c.average_protein_g) + 'g</span></span>' +
        '<span>Carbs<br><span class="font-semibold text-base">' + round1(c.average_carbs_g) + 'g</span></span>' +
        '<span>Fat<br><span class="font-semibold text-base">' + round1(c.average_fat_g) + 'g</span></span>' +
        '</div></div>';
    });
    html += "</div>";
    box.innerHTML = html;
  }

  const round1 = (n) => (Math.round(Number(n) * 10) / 10);
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m])); }

  /* --------------------------- Pagination (Tailwind buttons) --------------------------- */
  function renderPagination() {
    const nav = $("pagination");
    const base = "px-3 py-1 rounded";
    const gray = base + " bg-gray-300 hover:bg-gray-400";
    const grayOff = base + " bg-gray-300 opacity-50 cursor-not-allowed";
    const active = base + " bg-blue-600 text-white";

    function btn(label, page, opts) {
      opts = opts || {};
      const b = document.createElement("button");
      b.textContent = label;
      b.className = opts.active ? active : (opts.disabled ? grayOff : gray);
      if (opts.disabled) b.disabled = true;
      else b.addEventListener("click", () => { if (activePanel === "recipes" && page !== recipesPage) loadRecipes(page); });
      return b;
    }

    nav.innerHTML = "";
    nav.appendChild(btn("Previous", recipesPage - 1, { disabled: recipesPage <= 1 }));

    const start = Math.max(1, recipesPage - 1);
    const end = Math.min(recipesTotalPages, start + 2);
    if (start > 1) { nav.appendChild(btn("1", 1, {})); if (start > 2) nav.appendChild(ellipsis()); }
    for (let p = start; p <= end; p++) nav.appendChild(btn(String(p), p, { active: p === recipesPage }));
    if (end < recipesTotalPages) { if (end < recipesTotalPages - 1) nav.appendChild(ellipsis()); nav.appendChild(btn(String(recipesTotalPages), recipesTotalPages, {})); }

    nav.appendChild(btn("Next", recipesPage + 1, { disabled: recipesPage >= recipesTotalPages }));
  }
  function ellipsis() { const s = document.createElement("span"); s.textContent = "…"; s.className = "px-2 py-1 text-gray-500"; return s; }

  function disableApi(v) { ["getInsights", "getRecipes", "getClusters"].forEach((id) => { const b = $(id); if (b) b.disabled = v; }); }

  /* --------------------------- Account panel + logout --------------------------- */
  function sb() { return window.supabaseClient || null; }

  function initAccountPanel(user) {
    const acct = $("accountPanel"), out = $("signedOutPanel");
    if (user) {
      acct.hidden = false; out.hidden = true;
      $("acctEmail").textContent = user.email || (user.user_metadata && user.user_metadata.email) || "your account";
      const p = user.app_metadata && user.app_metadata.provider;
      $("acctProvider").textContent = p ? "· via " + p : "";
      $("panelLogout").addEventListener("click", async () => {
        const c = sb(); if (c) await c.auth.signOut();
        window.location.href = "index.html";
      });
    } else {
      acct.hidden = true; out.hidden = false;
    }
  }

  /* --------------------------- 2FA (Supabase MFA / TOTP) --------------------------- */
  let mfaFactorId = null, mfaVerifiedId = null;

  function mfaShow(state) { // "enroll" | "setup" | "enabled"
    $("mfaEnroll").hidden = state !== "enroll";
    $("mfaSetup").hidden = state !== "setup";
    $("mfaEnabled").hidden = state !== "enabled";
  }
  function mfaMsg(m, ok) {
    const e = $("mfaMsg");
    e.textContent = m || "";
    e.className = "text-sm mt-3 " + (ok ? "text-green-700" : (m ? "text-red-600" : ""));
  }
  function showQr(qr) {
    const box = $("mfaQr");
    if (typeof qr === "string" && qr.trim().slice(0, 4).toLowerCase() === "<svg") box.innerHTML = qr;
    else box.innerHTML = '<img alt="2FA QR code" width="180" height="180" src="' + qr + '">';
  }

  async function refreshMfa() {
    const c = sb();
    if (!c || !c.auth.mfa) { $("mfaStatus").textContent = "2FA is unavailable in this environment."; return; }
    const { data, error } = await c.auth.mfa.listFactors();
    if (error) { $("mfaStatus").textContent = "Could not check 2FA status: " + error.message; return; }
    const totp = (data && data.totp) || [];
    const verified = totp.find((f) => f.status === "verified");
    if (verified) {
      mfaVerifiedId = verified.id;
      $("mfaStatus").textContent = "Your account is protected by an authenticator app.";
      mfaShow("enabled");
    } else {
      $("mfaStatus").textContent = "Not enabled. Add a second layer of security with an authenticator app.";
      mfaShow("enroll");
    }
  }

  async function enable2fa() {
    const c = sb(); if (!c) return;
    mfaMsg("");
    try {
      // Remove any leftover unverified factor from a previous attempt.
      const list = await c.auth.mfa.listFactors();
      const stale = (((list.data && list.data.totp) || []).filter((f) => f.status !== "verified"));
      for (const f of stale) { try { await c.auth.mfa.unenroll({ factorId: f.id }); } catch (e) {} }

      const { data, error } = await c.auth.mfa.enroll({ factorType: "totp", friendlyName: "Authenticator " + Date.now() });
      if (error) throw error;
      mfaFactorId = data.id;
      showQr(data.totp.qr_code);
      mfaShow("setup");
      $("twofa").value = "";
      $("twofa").focus();
    } catch (err) { mfaMsg("Could not start 2FA setup: " + err.message, false); }
  }

  async function verify2fa() {
    const c = sb(); if (!c || !mfaFactorId) return;
    const code = $("twofa").value.trim();
    if (!/^\d{6}$/.test(code)) { mfaMsg("Enter the 6-digit code from your authenticator app.", false); return; }
    mfaMsg("Verifying…");
    try {
      const ch = await c.auth.mfa.challenge({ factorId: mfaFactorId });
      if (ch.error) throw ch.error;
      const v = await c.auth.mfa.verify({ factorId: mfaFactorId, challengeId: ch.data.id, code: code });
      if (v.error) throw v.error;
      mfaMsg("2FA activated successfully.", true);
      await refreshMfa();
    } catch (err) { mfaMsg("Verification failed: " + err.message, false); }
  }

  async function disable2fa() {
    const c = sb(); if (!c || !mfaVerifiedId) return;
    mfaMsg("Removing 2FA…");
    try {
      const { error } = await c.auth.mfa.unenroll({ factorId: mfaVerifiedId });
      if (error) throw error;
      mfaVerifiedId = null;
      mfaMsg("2FA disabled.", true);
      await refreshMfa();
    } catch (err) { mfaMsg("Could not disable 2FA: " + err.message, false); }
  }

  function initSecurity() {
    $("enable2fa").addEventListener("click", enable2fa);
    $("verify2fa").addEventListener("click", verify2fa);
    $("disable2fa").addEventListener("click", disable2fa);
    $("cancel2fa").addEventListener("click", () => { mfaShow("enroll"); mfaMsg(""); });
    refreshMfa();
  }

  /* --------------------------- Cloud cleanup (informational only) --------------------------- */
  function initCleanup() {
    const btn = $("cleanupBtn"); if (!btn) return;
    btn.addEventListener("click", () => { const i = $("cleanupInfo"); if (i) i.hidden = !i.hidden; });
  }

  /* --------------------------- Init --------------------------- */
  function onFilterChange() {
    if (insights) renderCharts();
    if (activePanel === "recipes") loadRecipes(1);
  }

  async function start() {
    initCleanup();
    let user = null;
    try {
      const c = sb();
      if (c) { const { data } = await c.auth.getSession(); user = data && data.session ? data.session.user : null; }
    } catch (e) {}
    initAccountPanel(user);
    if (!user) return; // anonymous: dashboard.html's auth script handles the redirect
    initSecurity();

    $("getInsights").addEventListener("click", loadInsights);
    $("getRecipes").addEventListener("click", () => loadRecipes(1));
    $("getClusters").addEventListener("click", loadClusters);
    $("dietFilter").addEventListener("change", onFilterChange);
    let t;
    $("searchInput").addEventListener("input", () => { clearTimeout(t); t = setTimeout(onFilterChange, 300); });

    loadInsights();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
