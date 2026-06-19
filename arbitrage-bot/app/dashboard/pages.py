"""Dashboard HTML — single-page app with tabs for each lifecycle stage."""

DASHBOARD_HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>Arbitrage Bot Dashboard</title>
<meta name="referrer" content="no-referrer"/>
<style>
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 20px;
    background: #0f0f14; color: #e4e4e8;
  }
  h1 { margin: 0 0 20px; font-size: 24px; }
  h2 { margin: 30px 0 15px; font-size: 18px; color: #9aa0a6; }
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px; margin-bottom: 20px;
  }
  .stat {
    background: #1a1a22; padding: 12px 14px;
    border-radius: 8px; border: 1px solid #2a2a35;
  }
  .stat .num { font-size: 22px; font-weight: 600; color: #fff; }
  .stat .label { font-size: 11px; color: #7a7a85; text-transform: uppercase; }
  .stat.green .num { color: #8fe0a3; }
  .stat.red .num { color: #f0a0a0; }
  .stat.yellow .num { color: #f0d080; }
  .stat.blue .num { color: #5b9eff; }
  .controls { margin-bottom: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
  button {
    background: #2d6cdf; color: #fff; border: none;
    padding: 7px 14px; border-radius: 6px; cursor: pointer;
    font-size: 13px; font-weight: 500;
  }
  button:hover { background: #3a7aed; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.secondary { background: #2a2a35; }
  button.approve { background: #1f7a3a; }
  button.reject { background: #8a2a2a; }
  button.warn { background: #8a6a2a; }
  button.info { background: #2a6a8a; }
  button.small { padding: 4px 10px; font-size: 12px; }
  .tabs {
    display: flex; gap: 4px; margin-bottom: 20px;
    border-bottom: 1px solid #2a2a35; flex-wrap: wrap;
  }
  .tab {
    padding: 10px 16px; cursor: pointer;
    color: #7a7a85; font-size: 14px;
    border-bottom: 2px solid transparent;
  }
  .tab:hover { color: #e4e4e8; }
  .tab.active { color: #fff; border-bottom-color: #2d6cdf; }
  .tab .badge-count {
    background: #2a2a35; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; margin-left: 6px;
  }
  .card {
    background: #1a1a22; border: 1px solid #2a2a35;
    border-radius: 8px; padding: 14px; margin-bottom: 10px;
  }
  .card-header {
    display: flex; justify-content: space-between;
    align-items: flex-start; margin-bottom: 10px; gap: 10px;
  }
  .title { font-weight: 600; font-size: 14px; flex: 1; }
  .title a { color: #5b9eff; text-decoration: none; }
  .title a:hover { text-decoration: underline; }
  .badges { display: flex; gap: 5px; flex-wrap: wrap; }
  .badge {
    font-size: 10px; padding: 3px 7px; border-radius: 4px;
    background: #2a2a35; color: #b4b4c0;
  }
  .badge.green { background: #1f4d2a; color: #8fe0a3; }
  .badge.red { background: #4d1f1f; color: #f0a0a0; }
  .badge.yellow { background: #4d3f1f; color: #f0d080; }
  .badge.blue { background: #1f3a4d; color: #8fc0f0; }
  .badge.purple { background: #3a1f4d; color: #c08fe0; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 8px; margin: 8px 0;
  }
  .field { background: #13131a; padding: 6px 9px; border-radius: 4px; }
  .field .k { font-size: 9px; color: #7a7a85; text-transform: uppercase; }
  .field .v { font-size: 13px; color: #fff; margin-top: 2px; }
  .field .v.profit-pos { color: #8fe0a3; }
  .field .v.profit-neg { color: #f0a0a0; }
  details { margin: 6px 0; }
  summary {
    cursor: pointer; color: #9aa0a6; font-size: 12px;
    padding: 4px 0;
  }
  summary:hover { color: #e4e4e8; }
  .subtle { font-size: 11px; color: #7a7a85; }
  .evidence {
    background: #13131a; padding: 8px; border-radius: 4px;
    margin-top: 4px; font-family: Menlo, monospace; font-size: 11px;
  }
  .evidence-row {
    display: flex; justify-content: space-between;
    padding: 2px 0; border-bottom: 1px solid #2a2a35;
  }
  .evidence-row:last-child { border-bottom: none; }
  .empty {
    padding: 30px; text-align: center; color: #7a7a85;
    background: #1a1a22; border-radius: 8px;
  }
  .actions {
    display: flex; gap: 5px; margin-top: 10px; flex-wrap: wrap;
    align-items: center;
  }
  .scan-row {
    display: grid;
    grid-template-columns: 50px 1fr 1fr 80px 90px 70px;
    gap: 8px; padding: 6px 10px;
    border-bottom: 1px solid #2a2a35; font-size: 12px;
  }
  .scan-row.header {
    color: #7a7a85; font-size: 10px;
    text-transform: uppercase; font-weight: 600;
  }
  .status-running { color: #f0d080; }
  .status-completed { color: #8fe0a3; }

  .modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.7);
    display: none; align-items: center; justify-content: center; z-index: 100;
  }
  .modal-bg.show { display: flex; }
  .modal {
    background: #1a1a22; border: 1px solid #2a2a35;
    border-radius: 10px; padding: 20px; min-width: 400px;
    max-width: 90vw; max-height: 90vh; overflow: auto;
  }
  .modal h3 { margin: 0 0 12px; }
  .modal .form-row { margin-bottom: 10px; }
  .modal label { display: block; font-size: 12px; color: #9aa0a6; margin-bottom: 3px; }
  .modal input, .modal select, .modal textarea {
    width: 100%; background: #13131a; color: #fff;
    border: 1px solid #2a2a35; border-radius: 5px;
    padding: 7px 9px; font-size: 13px; font-family: inherit;
  }
  .modal textarea { min-height: 60px; resize: vertical; }
  .modal-actions {
    display: flex; gap: 8px; justify-content: flex-end; margin-top: 15px;
  }

  .pnl-row {
    display: grid;
    grid-template-columns: 1fr 100px 100px 100px 100px;
    gap: 10px; padding: 8px 12px; border-bottom: 1px solid #2a2a35;
    font-size: 13px; align-items: center;
  }
  .pnl-row.header {
    color: #7a7a85; font-size: 10px;
    text-transform: uppercase; font-weight: 600;
  }
  .pnl-row .number { text-align: right; font-family: Menlo, monospace; }

  .conditional { display: none; }
  .conditional.active { display: block; }
</style>
</head>
<body>

<h1>Arbitrage Bot Dashboard <span id="engine-version-tag" style="font-size:13px;color:#7a7a85;font-weight:normal;margin-left:10px;"></span></h1>

<div class="stats" id="stats"></div>

<div class="controls">
  <button onclick="triggerScan()" id="scanBtn">Run Scan Now</button>
  <button class="secondary" onclick="refresh()">Refresh</button>
</div>

<div class="tabs">
  <div class="tab active" data-tab="review" onclick="switchTab('review')">
    Review Queue <span class="badge-count" id="count-review">0</span>
  </div>
  <div class="tab" data-tab="watchlist" onclick="switchTab('watchlist')">
    Watchlist <span class="badge-count" id="count-watchlist">0</span>
  </div>
  <div class="tab" data-tab="approved" onclick="switchTab('approved')">
    Approved (not bought) <span class="badge-count" id="count-approved">0</span>
  </div>
  <div class="tab" data-tab="purchased" onclick="switchTab('purchased')">
    Bought (not sold) <span class="badge-count" id="count-purchased">0</span>
  </div>
  <div class="tab" data-tab="sold" onclick="switchTab('sold')">
    Sold / Closed <span class="badge-count" id="count-sold">0</span>
  </div>
  <div class="tab" data-tab="rejected" onclick="switchTab('rejected')">
    Rejected <span class="badge-count" id="count-rejected">0</span>
  </div>
  <div class="tab" data-tab="pnl" onclick="switchTab('pnl')">P&L</div>
  <div class="tab" data-tab="queries" onclick="switchTab('queries')">Queries</div>
  <div class="tab" data-tab="nearmiss" onclick="switchTab('nearmiss')">Top Failed</div>
  <div class="tab" data-tab="scans" onclick="switchTab('scans')">Scans</div>
</div>

<div id="content"></div>

<!-- Modal: Decide -->
<div class="modal-bg" id="modal-decide">
  <div class="modal">
    <h3>Review Decision</h3>
    <input type="hidden" id="decide-id"/>
    <div class="form-row">
      <label>Decision</label>
      <select id="decide-decision">
        <option value="approved">Approve</option>
        <option value="watchlist">Watchlist (revisit later)</option>
        <option value="needs_more_info">Needs more info</option>
        <option value="passed_no_action">Pass (no action)</option>
        <option value="rejected_bad_match">Reject — bad spec match</option>
        <option value="rejected_bad_condition">Reject — bad condition</option>
        <option value="rejected_too_risky">Reject — too risky</option>
        <option value="rejected_margin_not_real">Reject — margin isn't real</option>
        <option value="rejected_not_my_category">Reject — not my category</option>
        <option value="rejected_insufficient_confidence">Reject — insufficient confidence</option>
        <option value="rejected_mock">Reject — mock data</option>
        <option value="rejected_other">Reject — other</option>
      </select>
    </div>
    <div class="form-row">
      <label>Notes (optional)</label>
      <textarea id="decide-notes" placeholder="Why this decision?"></textarea>
    </div>
    <div class="modal-actions">
      <button class="secondary" onclick="closeModal('modal-decide')">Cancel</button>
      <button onclick="submitDecision()">Save</button>
    </div>
  </div>
</div>

<!-- Modal: Record purchase -->
<div class="modal-bg" id="modal-purchase">
  <div class="modal">
    <h3>Record Purchase</h3>
    <input type="hidden" id="purchase-cand-id"/>
    <div class="form-row">
      <label>Purchase date (optional — leave blank for now)</label>
      <input type="datetime-local" id="purchase-date"/>
    </div>
    <div class="form-row">
      <label>Actual purchase price (__CUR__)</label>
      <input type="number" step="0.01" id="purchase-price"/>
    </div>
    <div class="form-row">
      <label>Tax paid (__CUR__)</label>
      <input type="number" step="0.01" id="purchase-tax" value="0"/>
    </div>
    <div class="form-row">
      <label>Inbound shipping (__CUR__)</label>
      <input type="number" step="0.01" id="purchase-inbound" value="0"/>
    </div>
    <div class="form-row">
      <label>Repair / refurb cost (__CUR__)</label>
      <input type="number" step="0.01" id="purchase-repair" value="0"/>
    </div>
    <div class="form-row">
      <label>Misc costs (__CUR__)</label>
      <input type="number" step="0.01" id="purchase-misc" value="0"/>
    </div>
    <div class="form-row">
      <label>Marketplace bought from</label>
      <input type="text" id="purchase-marketplace" placeholder="ebay, vinted, fb marketplace…"/>
    </div>
    <div class="form-row">
      <label>Notes</label>
      <textarea id="purchase-notes"></textarea>
    </div>
    <div class="modal-actions">
      <button class="secondary" onclick="closeModal('modal-purchase')">Cancel</button>
      <button onclick="submitPurchase()">Record purchase</button>
    </div>
  </div>
</div>

<!-- Modal: Record sale -->
<div class="modal-bg" id="modal-sale">
  <div class="modal">
    <h3>Record Sale Outcome</h3>
    <input type="hidden" id="sale-purchase-id"/>
    <div class="form-row">
      <label>Status</label>
      <select id="sale-status" onchange="updateSaleFields()">
        <option value="sold">Sold (at expected price)</option>
        <option value="liquidated">Liquidated (sold below estimate)</option>
        <option value="listed">Listed (still selling)</option>
        <option value="relisted">Relisted (after no sale)</option>
        <option value="unsold_still_holding">Unsold but still holding</option>
        <option value="returned">Returned by buyer</option>
        <option value="written_off">Written off (counted as loss)</option>
        <option value="abandoned">Abandoned</option>
      </select>
    </div>

    <div class="conditional" id="fields-sold">
      <div class="form-row">
        <label>Listed date (optional)</label>
        <input type="datetime-local" id="sale-listed-at"/>
      </div>
      <div class="form-row">
        <label>Sale date (optional, defaults to now)</label>
        <input type="datetime-local" id="sale-date"/>
      </div>
      <div class="form-row">
        <label>Actual sale price (__CUR__)</label>
        <input type="number" step="0.01" id="sale-price"/>
      </div>
      <div class="form-row">
        <label>Outbound shipping cost (__CUR__)</label>
        <input type="number" step="0.01" id="sale-outbound" value="0"/>
      </div>
      <div class="form-row">
        <label>Selling fees (__CUR__)</label>
        <input type="number" step="0.01" id="sale-fees" value="0"/>
      </div>
      <div class="form-row">
        <label>Payment processing fees (__CUR__)</label>
        <input type="number" step="0.01" id="sale-payment-fees" value="0"/>
      </div>
      <div class="form-row">
        <label>Sale platform</label>
        <input type="text" id="sale-platform" placeholder="ebay, stockx, vinted…"/>
      </div>
    </div>

    <div class="conditional" id="fields-listed">
      <div class="form-row">
        <label>Listed date (optional)</label>
        <input type="datetime-local" id="listed-at-only"/>
      </div>
      <div class="form-row">
        <label>Sale platform</label>
        <input type="text" id="sale-platform-listed" placeholder="ebay, stockx, vinted…"/>
      </div>
    </div>

    <div class="conditional" id="fields-closed">
      <div class="form-row">
        <label>Return costs (__CUR__) — applicable for returned/written-off</label>
        <input type="number" step="0.01" id="sale-return-costs" value="0"/>
      </div>
    </div>

    <div class="form-row">
      <label>Notes</label>
      <textarea id="sale-notes"></textarea>
    </div>
    <div class="modal-actions">
      <button class="secondary" onclick="closeModal('modal-sale')">Cancel</button>
      <button onclick="submitSale()">Save</button>
    </div>
  </div>
</div>

<script>
// Crucial: HTML escape any text from external sources (eBay titles, notes, etc.)
function esc(text) {
  if (text === null || text === undefined) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// URL escape for href attribute (keep query strings working but escape quotes)
function escUrl(url) {
  if (!url) return '#';
  return String(url).replace(/"/g, '%22').replace(/'/g, '%27');
}

let currentTab = "review";

async function refresh() {
  await loadStats();
  await loadCounts();
  await renderCurrentTab();
}

async function loadStats() {
  const summary = await fetch("/analytics/candidates").then(r => r.json());
  const pnl = await fetch("/analytics/pnl").then(r => r.json());

  // Display engine version filter context
  try {
    const ev = await fetch("/analytics/engine-versions").then(r => r.json());
    const tag = document.getElementById("engine-version-tag");
    if (tag) {
      const others = (ev.all_seen || []).filter(v => v !== ev.current);
      const note = others.length
        ? ` · older data: ${others.join(", ")}`
        : '';
      tag.textContent = `valuation: ${ev.current} (filter: ${summary.engine_version_filter})${note}`;
    }
  } catch (e) {}
  const cur = "__CUR__";
  document.getElementById("stats").innerHTML = `
    <div class="stat"><div class="num">${summary.total}</div><div class="label">Candidates</div></div>
    <div class="stat yellow"><div class="num">${summary.pending}</div><div class="label">Pending</div></div>
    <div class="stat green"><div class="num">${summary.approved}</div><div class="label">Approved</div></div>
    <div class="stat blue"><div class="num">${summary.lifecycle.purchased + summary.lifecycle.listed + summary.lifecycle.sold + summary.lifecycle.closed}</div><div class="label">Bought</div></div>
    <div class="stat green"><div class="num">${summary.lifecycle.sold}</div><div class="label">Sold</div></div>
    <div class="stat ${pnl.total_actual_profit > 0 ? 'green' : 'red'}"><div class="num">${cur}${pnl.total_actual_profit || 0}</div><div class="label">Total P&L</div></div>
    <div class="stat blue"><div class="num">${pnl.win_rate ? (pnl.win_rate*100).toFixed(0)+'%' : '—'}</div><div class="label">Win Rate</div></div>
    <div class="stat yellow"><div class="num">${cur}${pnl.inventory_cost_at_risk || 0}</div><div class="label">Inventory at risk</div></div>
  `;
}

async function loadCounts() {
  const summary = await fetch("/analytics/candidates").then(r => r.json());
  document.getElementById("count-review").textContent = summary.pending;
  document.getElementById("count-watchlist").textContent = summary.watchlist;
  document.getElementById("count-rejected").textContent = summary.rejected;
  const lc = summary.lifecycle;
  document.getElementById("count-approved").textContent = summary.approved - (lc.purchased + lc.listed + lc.sold + lc.closed);
  document.getElementById("count-purchased").textContent = lc.purchased + lc.listed;
  document.getElementById("count-sold").textContent = lc.sold + lc.closed;
}

async function renderCurrentTab() {
  const wrap = document.getElementById("content");
  wrap.innerHTML = '<div class="empty">Loading…</div>';

  if (currentTab === "scans") return renderScans(wrap);
  if (currentTab === "pnl") return renderPnl(wrap);
  if (currentTab === "queries") return renderQueries(wrap);
  if (currentTab === "nearmiss") return renderNearMisses(wrap);

  let url = "/review?";
  switch (currentTab) {
    case "review":     url += "decision=pending"; break;
    case "watchlist":  url += "watchlist=true"; break;
    case "approved":   url += "decision=approved&lifecycle_stage=none"; break;
    case "purchased":  url += "lifecycle_stage=purchased,listed"; break;
    case "sold":       url += "lifecycle_stage=sold,closed"; break;
    case "rejected":   url += "decision_group=rejected"; break;
  }

  const cands = await fetch(url).then(r => r.json());
  if (cands.length === 0) {
    wrap.innerHTML = `<div class="empty">No items in this tab.</div>`;
    return;
  }
  wrap.innerHTML = cands.map(c => renderCandidate(c, currentTab)).join("");
}

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll(".tab").forEach(t => {
    t.classList.toggle("active", t.dataset.tab === tab);
  });
  renderCurrentTab();
}

function renderCandidate(c, tab) {
  const cur = "__CUR__";
  const profitCls = c.net_profit > 0 ? "profit-pos" : "profit-neg";
  const compBadge = c.comp_source === "sold"
    ? '<span class="badge green">sold comps</span>'
    : '<span class="badge yellow">active comps</span>';
  const matchBadge = c.match_quality >= 0.8
    ? `<span class="badge green">match ${esc(c.match_quality)}</span>`
    : c.match_quality >= 0.5
    ? `<span class="badge yellow">match ${esc(c.match_quality)}</span>`
    : `<span class="badge red">match ${esc(c.match_quality)}</span>`;
  const flagBadges = (c.risk_flags || []).map(f =>
    `<span class="badge red">${esc(f)}</span>`
  ).join("");
  const decisionBadge = c.decision !== "pending"
    ? `<span class="badge purple">${esc(c.decision)}</span>` : "";
  const stageBadge = c.lifecycle_stage !== "none"
    ? `<span class="badge blue">${esc(c.lifecycle_stage)}</span>` : "";
  const mockBadge = c.is_mock ? '<span class="badge yellow">MOCK</span>' : "";

  const evidenceRows = (c.comp_evidence || []).map(e => `
    <div class="evidence-row">
      <span>${esc(e.title)}</span><strong>${cur}${esc(e.price)}</strong>
    </div>
  `).join("");

  const penalties = (c.penalties_applied || []).length
    ? `<ul style="margin:4px 0;padding-left:18px;font-size:11px;color:#b4b4c0">
         ${c.penalties_applied.map(p => `<li>${esc(p)}</li>`).join("")}
       </ul>`
    : '<span class="subtle">none</span>';

  const decisionNotes = c.decision_notes
    ? `<div class="subtle" style="margin-top:6px;"><strong>Notes:</strong> ${esc(c.decision_notes)}</div>`
    : "";

  let actions = "";
  if (tab === "review" || tab === "watchlist" || tab === "rejected") {
    actions = `<button class="info small" onclick="openDecideModal(${c.id})">Decide</button>`;
  } else if (tab === "approved") {
    actions = `
      <button class="approve small" onclick="openPurchaseModal(${c.id})">Record purchase</button>
      <button class="info small" onclick="openDecideModal(${c.id})">Change decision</button>
    `;
  } else if (tab === "purchased") {
    actions = `<button class="info small" onclick="openSaleModalForCandidate(${c.id})">Record sale outcome</button>`;
  }

  return `
    <div class="card">
      <div class="card-header">
        <div class="title">
          <a href="${escUrl(c.source_url)}" target="_blank" rel="noopener noreferrer">${esc(c.title)}</a>
        </div>
        <div class="badges">
          ${mockBadge} ${decisionBadge} ${stageBadge}
          ${compBadge} ${matchBadge}
          <span class="badge blue">${esc(c.source)}</span>
          ${flagBadges}
        </div>
      </div>
      <div class="grid">
        <div class="field"><div class="k">Price + Shipping</div><div class="v">${cur}${c.price.toFixed(2)}${c.shipping > 0 ? ' + ' + cur + c.shipping.toFixed(2) : ''}</div></div>
        <div class="field"><div class="k">Landed Cost</div><div class="v">${cur}${(c.price + c.shipping).toFixed(2)}</div></div>
        <div class="field"><div class="k">v1 Comp Est.</div><div class="v">${c.v1_expected_resale != null ? cur + c.v1_expected_resale.toFixed(2) : '<span class="subtle">n/a</span>'}</div></div>
        <div class="field"><div class="k">v2 Est. Resale</div><div class="v">${c.v2_expected_resale != null ? cur + c.v2_expected_resale.toFixed(2) : cur + c.expected_resale.toFixed(2)}</div></div>
        <div class="field"><div class="k">Net Profit</div><div class="v ${profitCls}">${cur}${c.net_profit.toFixed(2)}</div></div>
        <div class="field"><div class="k">ROI</div><div class="v ${profitCls}">${esc(c.roi)}</div></div>
        <div class="field"><div class="k">Score</div><div class="v">${c.score.toFixed(2)}</div></div>
        <div class="field"><div class="k">Confidence</div><div class="v">${c.confidence.toFixed(2)}</div></div>
      </div>
      <div class="subtle"><strong>Why it passed:</strong> ${esc(c.why_passed)}</div>
      ${decisionNotes}
      ${renderValuationBreakdown(c)}
      ${renderNegotiation(c)}
      <details>
        <summary>Comp evidence (${(c.comp_evidence || []).length} samples)</summary>
        <div class="subtle">${esc(c.match_details || '')}</div>
        <div class="evidence">${evidenceRows || '<em>no evidence</em>'}</div>
      </details>
      <details>
        <summary>Penalties applied</summary>${penalties}
      </details>
      <div class="actions">${actions}</div>
    </div>
  `;
}

function renderValuationBreakdown(c) {
  const cur = "__CUR__";
  const v = c.valuation_breakdown;
  if (!v) return "";

  const conservative = c.conservative_resale;
  // v15.5.1: range middle is v2, never v1
  const v2Expected = c.v2_expected_resale ?? v.expected_resale ?? c.expected_resale;
  const v1Estimate = c.v1_expected_resale ?? v.v1_expected_resale;
  const optimistic = c.optimistic_resale;
  const valConf = c.valuation_confidence;
  const method = esc(c.valuation_method || "unknown");
  const warnings = c.valuation_warnings || [];

  const warningTags = warnings.length
    ? warnings.map(w => `<span class="badge red">${esc(w)}</span>`).join(" ")
    : "";

  // Range chip — middle is v2 expected_resale, NOT v1
  const rangeRow = (conservative && v2Expected && optimistic)
    ? `<div style="display:flex;gap:10px;align-items:center;margin:6px 0;flex-wrap:wrap;">
         <span class="subtle">Range:</span>
         <span class="badge yellow">conservative ${cur}${conservative.toFixed(2)}</span>
         <span class="badge green">v2 expected ${cur}${v2Expected.toFixed(2)}</span>
         <span class="badge blue">optimistic ${cur}${optimistic.toFixed(2)}</span>
       </div>`
    : "";

  // Sources
  const weights = v.source_weights || {};
  const weightLine = Object.keys(weights).length
    ? Object.entries(weights).map(([k, val]) => `${esc(k)}: ${(val*100).toFixed(0)}%`).join(", ")
    : "n/a";

  // Anchor
  let anchorLine = "n/a";
  if (v.reference_anchor_low != null) {
    anchorLine = `${cur}${v.reference_anchor_low.toFixed(0)} / ${cur}${v.reference_anchor_mid.toFixed(0)} / ${cur}${v.reference_anchor_high.toFixed(0)}`;
    if (v.reference_anchor_source) {
      anchorLine += ` <span class="subtle">(${esc(v.reference_anchor_source)})</span>`;
    }
  }

  const condReasons = (v.condition_reasons || []).length
    ? v.condition_reasons.map(r => `<li>${esc(r)}</li>`).join("")
    : "<li><span class='subtle'>no adjustments</span></li>";

  const liqLine = `${esc(v.liquidity_band || "unknown")} (${(v.liquidity_score || 0).toFixed(2)})`;

  // Active discount as percentage
  const activeDiscPct = v.active_listing_discount != null
    ? `×${v.active_listing_discount.toFixed(2)}` : 'n/a';

  return `
    <details>
      <summary><strong>Valuation breakdown</strong> — method: <span class="badge purple">${method}</span> ${warningTags}</summary>
      ${rangeRow}
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:6px;margin:6px 0;">
        <div class="field"><div class="k">v2 confidence</div><div class="v">${valConf != null ? valConf.toFixed(2) : 'n/a'}</div></div>
        <div class="field"><div class="k">raw active median</div><div class="v">${v.raw_active_comp_median != null ? cur + v.raw_active_comp_median.toFixed(2) : 'n/a'}</div></div>
        <div class="field"><div class="k">active discount</div><div class="v">${activeDiscPct}</div></div>
        <div class="field"><div class="k">v1 adjusted estimate</div><div class="v">${v1Estimate != null ? cur + v1Estimate.toFixed(2) : 'n/a'}</div></div>
        <div class="field"><div class="k">v2 expected resale</div><div class="v">${v2Expected != null ? cur + v2Expected.toFixed(2) : 'n/a'}</div></div>
        <div class="field"><div class="k">active count</div><div class="v">${v.active_comp_count || 0}</div></div>
        <div class="field"><div class="k">spread CV</div><div class="v">${v.active_comp_spread != null ? v.active_comp_spread.toFixed(3) : 'n/a'}</div></div>
        <div class="field"><div class="k">sold median</div><div class="v">${v.sold_comp_median != null ? cur + v.sold_comp_median.toFixed(2) : '<span class="subtle">none</span>'}</div></div>
        <div class="field"><div class="k">own avg (v2 input)</div><div class="v">${v.own_outcome_average != null ? cur + v.own_outcome_average.toFixed(2) : '<span class="subtle">none</span>'}</div></div>
        <div class="field"><div class="k">condition adj</div><div class="v">×${(v.condition_adjustment || 1).toFixed(2)}</div></div>
        <div class="field"><div class="k">liquidity</div><div class="v">${liqLine}</div></div>
      </div>
      <div class="subtle"><strong>Anchor (low/mid/high):</strong> ${anchorLine}</div>
      <div class="subtle"><strong>Source weights:</strong> ${esc(weightLine)}</div>
      <div class="subtle"><strong>Condition reasons:</strong>
        <ul style="margin:4px 0 4px 18px">${condReasons}</ul>
      </div>
      ${v.explanation ? `<div class="subtle" style="margin-top:6px;"><strong>Explanation:</strong> ${esc(v.explanation)}</div>` : ""}
    </details>
  `;
}

// ── v15.5.9: Target buy price / negotiation panel ─────────────────
//
// Renders the "Target buy price / negotiation" section on a card.
// `c` is the card data (review candidate or near-miss). Both expose
// the same `negotiation` shape from the API.
function renderNegotiation(c) {
  const cur = "__CUR__";
  const n = c.negotiation;
  if (!n || !n.target_review || !n.target_alert) return "";
  const tr = n.target_review;
  const ta = n.target_alert;

  const labelBadge = (label) => {
    if (label === "already_passes")
      return '<span class="badge green">already passes</span>';
    if (label === "negotiable")
      return '<span class="badge yellow">negotiable</span>';
    if (label === "too_expensive")
      return '<span class="badge red">too expensive</span>';
    if (label === "infeasible")
      return '<span class="badge red">infeasible</span>';
    return `<span class="badge">${esc(label)}</span>`;
  };

  const fmtPrice = (v) =>
    (v == null) ? '<span class="subtle">n/a</span>' : cur + v.toFixed(2);
  const fmtDisc  = (abs, pct) => {
    if (abs == null) return '<span class="subtle">n/a</span>';
    if (abs <= 0)    return '<span class="subtle">none needed</span>';
    return `${cur}${abs.toFixed(2)} (${(pct * 100).toFixed(1)}%)`;
  };

  // Bucket chips — only show the ones that are TRUE
  const bucketLabels = {
    profitable_before_fees:        "profitable before fees",
    failed_only_by_profit:         "fails only on profit",
    failed_only_by_roi:            "fails only on ROI",
    failed_condition_risk:         "condition / risk",
    failed_valuation_uncertainty:  "valuation uncertain",
    negotiable_review:             "negotiable → review",
    negotiable_alert:              "negotiable → alert",
  };
  const buckets = n.buckets || {};
  const bucketChips = Object.entries(buckets)
    .filter(([, on]) => on)
    .map(([k]) => `<span class="badge blue">${bucketLabels[k] || k}</span>`)
    .join(" ");

  // Summary line shown in the <summary> element so it's visible
  // before expansion. Pick the more interesting label of the two.
  let summaryLabel;
  if (tr.label === "already_passes" && ta.label !== "already_passes") {
    summaryLabel = `→ alert ${labelBadge(ta.label)}`;
  } else {
    summaryLabel = labelBadge(tr.label);
  }
  const summaryDisc = (tr.label !== "already_passes" && tr.discount_needed_abs != null && tr.discount_needed_abs > 0)
    ? ` — needs ${cur}${tr.discount_needed_abs.toFixed(2)} off (${(tr.discount_needed_pct*100).toFixed(1)}%)`
    : "";

  return `
    <details>
      <summary><strong>Target buy price / negotiation</strong> ${summaryLabel}<span class="subtle">${summaryDisc}</span></summary>
      <div class="subtle" style="margin:6px 0 4px;">
        Holding the estimated resale fixed, this shows the highest price at which the listing
        would still satisfy each threshold pair. The binding constraint is whichever max is lower.
      </div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(220px,1fr));gap:10px;margin:8px 0;">
        <div style="background:#13131a;border-radius:6px;padding:10px;">
          <div class="subtle" style="margin-bottom:4px;">
            <strong>Review thresholds</strong>
            (${cur}${tr.min_profit_threshold} profit, ${(tr.min_roi_threshold*100).toFixed(0)}% ROI)
            ${labelBadge(tr.label)}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;">
            <div class="field"><div class="k">Max buy (profit)</div><div class="v">${fmtPrice(tr.max_buy_for_profit)}</div></div>
            <div class="field"><div class="k">Max buy (ROI)</div><div class="v">${fmtPrice(tr.max_buy_for_roi)}</div></div>
            <div class="field"><div class="k">Max buy (binding: ${esc(tr.binding_constraint)})</div><div class="v">${fmtPrice(tr.max_buy_overall)}</div></div>
            <div class="field"><div class="k">Discount needed</div><div class="v">${fmtDisc(tr.discount_needed_abs, tr.discount_needed_pct)}</div></div>
          </div>
        </div>
        <div style="background:#13131a;border-radius:6px;padding:10px;">
          <div class="subtle" style="margin-bottom:4px;">
            <strong>Alert thresholds</strong>
            (${cur}${ta.min_profit_threshold} profit, ${(ta.min_roi_threshold*100).toFixed(0)}% ROI)
            ${labelBadge(ta.label)}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;">
            <div class="field"><div class="k">Max buy (profit)</div><div class="v">${fmtPrice(ta.max_buy_for_profit)}</div></div>
            <div class="field"><div class="k">Max buy (ROI)</div><div class="v">${fmtPrice(ta.max_buy_for_roi)}</div></div>
            <div class="field"><div class="k">Max buy (binding: ${esc(ta.binding_constraint)})</div><div class="v">${fmtPrice(ta.max_buy_overall)}</div></div>
            <div class="field"><div class="k">Discount needed</div><div class="v">${fmtDisc(ta.discount_needed_abs, ta.discount_needed_pct)}</div></div>
          </div>
        </div>
      </div>
      ${bucketChips ? `<div class="subtle" style="margin-top:6px;"><strong>Failure tags:</strong> ${bucketChips}</div>` : ''}
    </details>
  `;
}

async function renderNearMisses(wrap) {
  const cur = "__CUR__";
  const [rows, summary] = await Promise.all([
    fetch("/near-misses").then(r => r.json()),
    fetch("/analytics/negotiation").then(r => r.json()).catch(() => null),
  ]);
  if (rows.length === 0) {
    wrap.innerHTML = `<div class="empty">No failed scored listings recorded yet.<br>(This list resets every scan and shows the top scored listings that didn't pass review.)</div>`;
    return;
  }
  const genuineCount = rows.filter(m => m.is_genuine_near_miss).length;
  let html = `<h2>Top Failed Opportunities (latest scan, top ${rows.length} by score)</h2>`;
  html += `<div class="subtle" style="margin-bottom:12px;">
    These are scored listings that did <strong>not</strong> pass review thresholds.
    Most are diagnostic only — they tell you which thresholds bite hardest.
    A small subset will be flagged as <span class="badge yellow">Genuine near-miss</span> —
    those are listings close enough to passing that the bot will rescore them
    on the next scan (price changed, watchlisted, or thresholds barely missed).
    ${genuineCount} of ${rows.length} listed here qualify as genuine near-misses.
  </div>`;

  // v15.5.9: failure-bucket summary across the WHOLE failed pool
  // (not just the top-by-score window shown below). Each failed
  // listing can sit in multiple buckets — counts are not exclusive.
  if (summary && summary.total_failed_scored > 0) {
    const t = summary.thresholds || {};
    const c = summary.bucket_counts || {};
    const lbl = summary.by_negotiation_label || {};
    const chip = (count, label, cls) =>
      `<span class="badge ${cls || 'blue'}" title="${label}">${count} ${label}</span>`;

    html += `<h2 style="margin-top:6px;">Failure-bucket summary (across all ${summary.total_failed_scored} failed scored listings)</h2>`;
    html += `<div class="subtle" style="margin-bottom:8px;">
      A listing can sit in multiple buckets — these are not mutually exclusive counts.
      Negotiation limits in use: ≤ ${cur}${(t.max_discount_abs ?? 0).toFixed(2)} or
      ≤ ${((t.max_discount_pct ?? 0) * 100).toFixed(0)}% off the asking price (laxer wins).
    </div>`;
    html += `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px;">
      ${chip(c.profitable_before_fees || 0,        "profitable before fees",  "yellow")}
      ${chip(c.failed_only_by_profit || 0,         "failed only by profit",   "yellow")}
      ${chip(c.failed_only_by_roi || 0,            "failed only by ROI",      "yellow")}
      ${chip(c.failed_condition_risk || 0,         "condition / risk",        "red")}
      ${chip(c.failed_valuation_uncertainty || 0,  "valuation uncertain",     "purple")}
      ${chip(c.negotiable_review || 0,             `negotiable → review`,     "green")}
      ${chip(c.negotiable_alert || 0,              `negotiable → alert`,      "green")}
    </div>`;
    html += `<div class="subtle" style="margin-bottom:14px;">
      <strong>Negotiation labels (review thresholds):</strong>
      ${chip(lbl.already_passes || 0, "already passes", "green")}
      ${chip(lbl.negotiable || 0,     "negotiable",     "yellow")}
      ${chip(lbl.too_expensive || 0,  "too expensive",  "red")}
      ${chip(lbl.infeasible || 0,     "infeasible",     "red")}
    </div>`;
  }

  for (const m of rows) {
    const profitCls = m.net_profit > 0 ? "profit-pos" : "profit-neg";
    const genuineBadge = m.is_genuine_near_miss
      ? '<span class="badge yellow" title="Will be rescored on next scan">Genuine near-miss</span>'
      : '';
    html += `
      <div class="card">
        <div class="card-header">
          <div class="title">
            <a href="${escUrl(m.url)}" target="_blank" rel="noopener noreferrer">${esc(m.title)}</a>
          </div>
          <div class="badges">
            ${genuineBadge}
            <span class="badge blue">${esc(m.category)}</span>
            <span class="badge ${m.comp_source === 'sold' ? 'green' : 'yellow'}">${esc(m.comp_source)} comps (${m.comp_count})</span>
          </div>
        </div>
        <div class="grid">
          <div class="field"><div class="k">Price + Shipping</div><div class="v">${cur}${m.price.toFixed(2)}${(m.shipping || 0) > 0 ? ' + ' + cur + (m.shipping || 0).toFixed(2) : ''}</div></div>
          <div class="field"><div class="k">Landed Cost</div><div class="v">${cur}${(m.price + (m.shipping || 0)).toFixed(2)}</div></div>
          <div class="field"><div class="k">v1 Comp Est.</div><div class="v">${m.v1_expected_resale != null ? cur + m.v1_expected_resale.toFixed(2) : '<span class="subtle">n/a</span>'}</div></div>
          <div class="field"><div class="k">v2 Est. Resale</div><div class="v">${m.v2_expected_resale != null ? cur + m.v2_expected_resale.toFixed(2) : cur + m.expected_resale.toFixed(2)}</div></div>
          <div class="field"><div class="k">Net Profit</div><div class="v ${profitCls}">${cur}${m.net_profit.toFixed(2)}</div></div>
          <div class="field"><div class="k">ROI</div><div class="v ${profitCls}">${(m.roi*100).toFixed(1)}%</div></div>
          <div class="field"><div class="k">Score</div><div class="v">${m.score.toFixed(2)}</div></div>
          <div class="field"><div class="k">Confidence</div><div class="v">${m.confidence.toFixed(2)}</div></div>
          <div class="field"><div class="k">Match</div><div class="v">${m.match_quality.toFixed(2)}</div></div>
        </div>
        <div class="subtle"><strong>Why it failed review:</strong> ${esc(m.fail_reason)}</div>
        ${m.valuation_method ? '<div class="subtle"><strong>Valuation method:</strong> <span class="badge purple">' + esc(m.valuation_method) + '</span></div>' : ''}
        ${renderValuationBreakdown(m)}
        ${renderNegotiation(m)}
      </div>
    `;
  }
  wrap.innerHTML = html;
}

async function renderQueries(wrap) {
  const rows = await fetch("/queries/performance").then(r => r.json());
  if (rows.length === 0) {
    wrap.innerHTML = `<div class="empty">No query data yet — run a scan first.</div>`;
    return;
  }

  let html = `
    <h2>Query Performance (all scans aggregated)</h2>
    <div style="overflow-x:auto;">
    <table style="width:100%; border-collapse: collapse; font-size: 12px;">
      <thead>
        <tr style="color:#7a7a85; text-transform:uppercase; font-size:10px;">
          <th style="text-align:left; padding:8px; border-bottom:1px solid #2a2a35;">Query</th>
          <th style="text-align:left; padding:8px; border-bottom:1px solid #2a2a35;">Cat</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;">Scans</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;" title="Raw items returned by eBay">Raw</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;" title="Filtered out by negative keywords">Neg</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;" title="Already in DB (deduped)">Dupes</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;">New</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;">Scored</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;">Exact</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;">Partial</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;">Broad</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;">Cands</th>
          <th style="text-align:right; padding:8px; border-bottom:1px solid #2a2a35;">Alerts</th>
        </tr>
      </thead>
      <tbody>
  `;

  for (const r of rows) {
    const candCls = r.candidates > 0 ? "color:#8fe0a3;font-weight:600;" : "";
    html += `
      <tr style="border-bottom:1px solid #2a2a35;">
        <td style="padding:7px;">${esc(r.query_terms)}</td>
        <td style="padding:7px;"><span class="badge blue">${esc(r.category)}</span></td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace;">${r.scan_count}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace;">${r.raw_returned}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace; color:#f0a0a0;">${r.negative_filtered}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace; color:#7a7a85;">${r.duplicates}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace;">${r.new_listings}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace;">${r.scored}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace; color:#8fe0a3;">${r.exact_matches}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace; color:#f0d080;">${r.partial_matches}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace; color:#f0a0a0;">${r.broad_rejected}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace; ${candCls}">${r.candidates}</td>
        <td style="padding:7px; text-align:right; font-family:Menlo,monospace;">${r.alerts}</td>
      </tr>
    `;
  }

  html += `</tbody></table></div>`;

  // Per-query failure-reason tags (multi-label — a single listing can
  // hit multiple failure reasons; counters are not mutually exclusive)
  html += `<h2>Failure tags per query</h2>`;
  html += `<div class="subtle" style="margin-bottom:10px;">
    Each failed listing can carry multiple tags (e.g. a listing failing both
    profit and ROI checks counts in both columns). These are <strong>not
    mutually exclusive counts</strong> — they are multi-label failure tags
    summed across listings. Use them to spot which thresholds bite hardest
    per query.
  </div>`;

  const FAIL_FIELDS = [
    ["failed_profit",         "profit"],
    ["failed_roi",            "roi"],
    ["failed_score",          "score"],
    ["failed_confidence",     "confidence"],
    ["failed_match_quality",  "match"],
    ["failed_active_only",    "active comps"],
    ["failed_battery_health", "battery health"],
    ["failed_risk_flags",     "risk flag"],
    ["failed_comp_pool",      "comp pool"],
    ["failed_no_comps",       "no comps"],
    ["failed_other",          "other"],
  ];

  for (const r of rows) {
    const totalFails = FAIL_FIELDS.reduce(
      (sum, [k]) => sum + (r[k] || 0), 0,
    );
    if (totalFails === 0 && r.scored === 0) continue;

    let tags = FAIL_FIELDS
      .filter(([k]) => (r[k] || 0) > 0)
      .map(([k, label]) => {
        const count = r[k];
        // Color intensity: red for the worst bite, neutral for small ones
        const cls = count >= r.scored / 2 ? "red" : "yellow";
        return `<span class="badge ${cls}">${esc(label)}: ${count}</span>`;
      });

    if (tags.length === 0) tags = ['<span class="subtle">no listings failed checks</span>'];

    html += `
      <div class="card" style="padding:10px 14px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <div><strong>${esc(r.query_terms)}</strong> <span class="badge blue">${esc(r.category)}</span></div>
          <div class="subtle">scored: ${r.scored} · candidates: ${r.candidates}</div>
        </div>
        <div class="badges" style="gap:6px;">
          ${tags.join("")}
        </div>
      </div>
    `;
  }
  wrap.innerHTML = html;
}

async function renderScans(wrap) {
  const scans = await fetch("/scans").then(r => r.json());
  wrap.innerHTML = `
    <div class="scan-row header">
      <div>ID</div><div>Started</div><div>Sources</div>
      <div>Listings</div><div>Candidates</div><div>Status</div>
    </div>
    ${scans.map(s => `
      <div class="scan-row">
        <div>#${s.id}</div>
        <div class="subtle">${s.started_at ? esc(new Date(s.started_at).toLocaleString()) : ''}</div>
        <div class="subtle">${esc((s.sources || []).join(", "))}</div>
        <div>${s.listings}</div>
        <div>${s.candidates}</div>
        <div class="status-${esc(s.status)}">${esc(s.status)}</div>
      </div>
    `).join("")}
  `;
}

async function renderPnl(wrap) {
  const cur = "__CUR__";
  const summary = await fetch("/analytics/pnl").then(r => r.json());
  const pva = await fetch("/analytics/predicted-vs-actual").then(r => r.json());
  const cat = await fetch("/analytics/categories").then(r => r.json());
  const rej = await fetch("/analytics/rejections").then(r => r.json());
  const wins = await fetch("/analytics/top-wins").then(r => r.json());
  const misses = await fetch("/analytics/biggest-misses").then(r => r.json());

  let html = `
    <h2>Overall P&L</h2>
    <div class="stats">
      <div class="stat"><div class="num">${summary.total_purchases}</div><div class="label">Purchases</div></div>
      <div class="stat green"><div class="num">${summary.sold_count}</div><div class="label">Sold</div></div>
      <div class="stat blue"><div class="num">${summary.win_count}</div><div class="label">Wins</div></div>
      <div class="stat red"><div class="num">${summary.loss_count}</div><div class="label">Losses</div></div>
      <div class="stat ${summary.total_actual_profit > 0 ? 'green' : 'red'}"><div class="num">${cur}${summary.total_actual_profit || 0}</div><div class="label">Net P&L (realized)</div></div>
      <div class="stat yellow"><div class="num">${summary.inventory_count}</div><div class="label">Still in inventory</div></div>
      <div class="stat yellow"><div class="num">${cur}${summary.inventory_cost_at_risk || 0}</div><div class="label">Cost at risk</div></div>
      <div class="stat blue"><div class="num">${summary.avg_days_to_sell || '—'}</div><div class="label">Avg days to sell</div></div>
    </div>

    <h2>Predicted vs Actual</h2>
  `;

  if (pva.sample_size === 0) {
    html += `<div class="empty">No finalized sales yet. P&L analytics excludes mock data by default.</div>`;
  } else {
    html += `
      <div class="card">
        <div class="grid">
          <div class="field"><div class="k">Sample size</div><div class="v">${pva.sample_size}</div></div>
          <div class="field"><div class="k">Avg resale error</div><div class="v ${pva.avg_resale_error > 0 ? 'profit-pos' : 'profit-neg'}">${cur}${pva.avg_resale_error}</div></div>
          <div class="field"><div class="k">Avg profit error</div><div class="v ${pva.avg_profit_error > 0 ? 'profit-pos' : 'profit-neg'}">${cur}${pva.avg_profit_error}</div></div>
          <div class="field"><div class="k">Avg ROI error</div><div class="v">${(pva.avg_roi_error*100).toFixed(1)}%</div></div>
          <div class="field"><div class="k">Overestimated</div><div class="v">${pva.overestimate_count}</div></div>
          <div class="field"><div class="k">Underestimated</div><div class="v">${pva.underestimate_count}</div></div>
        </div>
      </div>
    `;
  }

  html += `<h2>By Category</h2>`;
  if (cat.length === 0) {
    html += `<div class="empty">No data yet.</div>`;
  } else {
    html += `
      <div class="pnl-row header">
        <div>Category</div>
        <div class="number">Cands</div>
        <div class="number">Approved</div>
        <div class="number">Sold</div>
        <div class="number">Avg ROI</div>
      </div>
      ${cat.map(c => `
        <div class="pnl-row">
          <div>${esc(c.category)}</div>
          <div class="number">${c.candidates}</div>
          <div class="number">${c.approved}</div>
          <div class="number">${c.sold}</div>
          <div class="number">${c.avg_actual_roi !== null ? (c.avg_actual_roi*100).toFixed(1)+'%' : '—'}</div>
        </div>
      `).join("")}
    `;
  }

  html += `<h2>Top Wins</h2>`;
  html += wins.length === 0
    ? `<div class="empty">No sold items yet.</div>`
    : wins.map(w => `
      <div class="card">
        <div class="title">${esc(w.title)}</div>
        <div class="subtle">Predicted ${cur}${w.predicted_profit} → Actual ${cur}${w.actual_profit} (ROI ${(w.actual_roi*100).toFixed(1)}%)</div>
      </div>
    `).join("");

  html += `<h2>Biggest Misses</h2>`;
  html += misses.length === 0
    ? `<div class="empty">No sold items yet.</div>`
    : misses.map(m => `
      <div class="card">
        <div class="title">${esc(m.title)}</div>
        <div class="subtle">Predicted ${cur}${m.predicted_profit} (conf ${m.predicted_confidence.toFixed(2)}) → Actual ${cur}${m.actual_profit} (error ${cur}${m.profit_error})</div>
      </div>
    `).join("");

  html += `<h2>Rejection Patterns</h2>`;
  html += rej.total_rejections === 0
    ? `<div class="empty">No rejections yet.</div>`
    : `<div class="card">${rej.by_reason.map(r =>
        `<div style="display:flex;justify-content:space-between;padding:4px 0;">
          <span>${esc(r.label)}</span>
          <span class="subtle">${r.count} (${r.pct}%)</span>
        </div>`
      ).join("")}</div>`;

  wrap.innerHTML = html;
}

function openDecideModal(id) {
  document.getElementById("decide-id").value = id;
  document.getElementById("decide-decision").value = "approved";
  document.getElementById("decide-notes").value = "";
  document.getElementById("modal-decide").classList.add("show");
}

function openPurchaseModal(id) {
  document.getElementById("purchase-cand-id").value = id;
  document.getElementById("purchase-date").value = "";
  document.getElementById("purchase-price").value = "";
  ["tax", "inbound", "repair", "misc"].forEach(k => {
    document.getElementById("purchase-" + k).value = "0";
  });
  document.getElementById("purchase-marketplace").value = "ebay";
  document.getElementById("purchase-notes").value = "";
  document.getElementById("modal-purchase").classList.add("show");
}

async function openSaleModalForCandidate(candId) {
  const data = await fetch(`/review/${candId}/purchase`).then(r => r.json());
  if (!data.purchase_id) {
    alert("No purchase record found for this candidate.");
    return;
  }
  document.getElementById("sale-purchase-id").value = data.purchase_id;
  document.getElementById("sale-status").value = "sold";
  document.getElementById("sale-listed-at").value = "";
  document.getElementById("sale-date").value = "";
  document.getElementById("sale-price").value = "";
  document.getElementById("sale-outbound").value = "0";
  document.getElementById("sale-fees").value = "0";
  document.getElementById("sale-payment-fees").value = "0";
  document.getElementById("sale-platform").value = "ebay";
  document.getElementById("listed-at-only").value = "";
  document.getElementById("sale-platform-listed").value = "ebay";
  document.getElementById("sale-return-costs").value = "0";
  document.getElementById("sale-notes").value = "";
  updateSaleFields();
  document.getElementById("modal-sale").classList.add("show");
}

function updateSaleFields() {
  const status = document.getElementById("sale-status").value;
  const showSold = ["sold", "liquidated"].includes(status);
  const showListed = ["listed", "relisted"].includes(status);
  const showClosed = ["returned", "written_off", "abandoned"].includes(status);
  document.getElementById("fields-sold").classList.toggle("active", showSold);
  document.getElementById("fields-listed").classList.toggle("active", showListed);
  document.getElementById("fields-closed").classList.toggle("active", showClosed);
}

function closeModal(id) {
  document.getElementById(id).classList.remove("show");
}

async function submitDecision() {
  const id = document.getElementById("decide-id").value;
  const decision = document.getElementById("decide-decision").value;
  const notes = document.getElementById("decide-notes").value;
  const resp = await fetch(`/review/${id}/decide`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({decision, notes}),
  });
  if (!resp.ok) { alert("Failed to save decision"); return; }
  closeModal("modal-decide");
  refresh();
}

async function submitPurchase() {
  const id = document.getElementById("purchase-cand-id").value;
  const dateRaw = document.getElementById("purchase-date").value;
  const body = {
    actual_purchase_price: parseFloat(document.getElementById("purchase-price").value),
    purchased_at: dateRaw ? new Date(dateRaw).toISOString() : null,
    tax_paid: parseFloat(document.getElementById("purchase-tax").value || 0),
    inbound_shipping_cost: parseFloat(document.getElementById("purchase-inbound").value || 0),
    repair_cost: parseFloat(document.getElementById("purchase-repair").value || 0),
    misc_buy_costs: parseFloat(document.getElementById("purchase-misc").value || 0),
    marketplace_purchased_from: document.getElementById("purchase-marketplace").value,
    purchase_notes: document.getElementById("purchase-notes").value,
  };
  if (!body.actual_purchase_price) { alert("Enter purchase price"); return; }
  const resp = await fetch(`/review/${id}/purchase`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  if (!resp.ok) { alert("Failed: " + (await resp.text())); return; }
  closeModal("modal-purchase");
  refresh();
}

async function submitSale() {
  const purchaseId = document.getElementById("sale-purchase-id").value;
  const status = document.getElementById("sale-status").value;
  const listedRaw = document.getElementById("sale-listed-at").value
                 || document.getElementById("listed-at-only").value;
  const saleRaw = document.getElementById("sale-date").value;

  const body = {
    sale_status: status,
    listed_at: listedRaw ? new Date(listedRaw).toISOString() : null,
    sale_date: saleRaw ? new Date(saleRaw).toISOString() : null,
    actual_sale_price: parseFloat(document.getElementById("sale-price").value || 0),
    outbound_shipping_cost: parseFloat(document.getElementById("sale-outbound").value || 0),
    selling_fees: parseFloat(document.getElementById("sale-fees").value || 0),
    payment_processing_fees: parseFloat(document.getElementById("sale-payment-fees").value || 0),
    return_costs: parseFloat(document.getElementById("sale-return-costs").value || 0),
    sale_platform: document.getElementById("sale-platform").value
                || document.getElementById("sale-platform-listed").value,
    final_notes: document.getElementById("sale-notes").value,
  };
  const resp = await fetch(`/purchase/${purchaseId}/sale`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  if (!resp.ok) { alert("Failed: " + (await resp.text())); return; }
  closeModal("modal-sale");
  refresh();
}

async function triggerScan() {
  const btn = document.getElementById("scanBtn");
  btn.disabled = true; btn.textContent = "Scanning...";
  await fetch("/scan", { method: 'POST' });
  btn.disabled = false; btn.textContent = "Run Scan Now";
  refresh();
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""
