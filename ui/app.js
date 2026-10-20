/* app.js — KB Lifecycle Agent UI logic */

const API = '';  // same origin
let currentRunId = null;
let pollInterval = null;
let allFindings = [];

// ── Tab switching ─────────────────────────────────────────────────────────

function showTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('content-' + name).classList.add('active');

  // Lazy-load data when switching tabs
  if (name === 'review') loadReviewQueue();
  if (name === 'log') loadAuditLog();
}

// ── Status helpers ────────────────────────────────────────────────────────

function setStatus(label, state) {
  document.getElementById('statusLabel').textContent = label;
  const dot = document.getElementById('statusDot');
  dot.className = 'status-dot ' + state;
}

function setAgentState(agentKey, state, badge) {
  const card = document.getElementById('agent-' + agentKey);
  const badgeEl = document.getElementById('badge-' + agentKey);
  if (!card) return;
  card.className = 'agent-card ' + state;
  if (badgeEl && badge !== undefined) {
    badgeEl.textContent = badge;
    badgeEl.className = 'agent-badge ' + (state === 'done' ? 'ok' : state === 'failed' ? 'err' : '');
  }
}

// ── Progress log ──────────────────────────────────────────────────────────

function addProgress(text, state = 'done', meta = '') {
  const log = document.getElementById('progressLog');
  if (log.querySelector('.empty-state')) log.innerHTML = '';
  const el = document.createElement('div');
  el.className = 'progress-entry';
  el.innerHTML = `
    <div class="step-dot ${state}"></div>
    <div class="step-text">${text}</div>
    <div class="step-meta">${meta}</div>
  `;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

// ── Run lifecycle sweep ───────────────────────────────────────────────────

async function startSweep() {
  const btn = document.getElementById('btnSweep');
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-icon">⏳</span> Running…';
  setStatus('Running sweep…', 'running');
  document.getElementById('progressLog').innerHTML = '';

  // Reset agent badges
  ['ingestion','audit','remediation','verifier','gap'].forEach(k => {
    setAgentState(k, '', '–');
  });

  try {
    const res = await fetch(API + '/api/orchestrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skip_llm_audit: false, max_proposals: 6, gap_top_n: 5 }),
    });
    const data = await res.json();
    currentRunId = data.run_id;
    addProgress('Orchestrator started lifecycle sweep', 'running', data.run_id);
    pollStatus();
  } catch (e) {
    addProgress('Failed to start sweep: ' + e.message, 'failed');
    setStatus('Error', 'error');
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">▶</span> Run Full Sweep';
  }
}

function pollStatus() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(async () => {
    try {
      const res = await fetch(API + '/api/status/' + currentRunId);
      const data = await res.json();

      if (data.status === 'running') {
        setStatus('Running…', 'running');
        return;
      }

      clearInterval(pollInterval);

      if (data.status === 'done') {
        handleSweepDone(data.result);
      } else {
        setStatus('Error', 'error');
        addProgress('Sweep error: ' + JSON.stringify(data.result?.error || data), 'failed');
        resetBtn();
      }
    } catch (e) {
      // network blip — keep polling
    }
  }, 2000);
}

function handleSweepDone(result) {
  setStatus('Sweep complete', 'done');
  addProgress(`Sweep complete — ${result.completed}/${result.total_steps} steps succeeded`, 'done');

  const res = result.results || {};

  // Ingestion
  if (res.ingestion_agent) {
    const r = res.ingestion_agent;
    setAgentState('ingestion', 'done', r.articles_parsed);
    addProgress(`Ingestion: ${r.articles_parsed} articles loaded, ${r.errors} errors`, 'done');
    document.getElementById('stat-articles').textContent = r.total_in_store || r.articles_parsed;
  }

  // Audit
  if (res.audit_agent) {
    const r = res.audit_agent;
    setAgentState('audit', 'done', r.total_findings);
    addProgress(`Audit: ${r.total_findings} issues (${r.duplicates} dup, ${r.stale} stale, ${r.contradictions} contradiction, ${r.low_quality} low-quality)`, 'done');
    document.getElementById('stat-findings').textContent = r.total_findings;
    allFindings = r.findings || [];
    renderFindings(allFindings);
  }

  // Remediation
  if (res.remediation_agent) {
    const r = res.remediation_agent;
    setAgentState('remediation', 'done', r.proposals_created);
    addProgress(`Remediation: ${r.proposals_created} proposals drafted`, 'done');
    document.getElementById('stat-proposals').textContent = r.proposals_created;
  }

  // Verifier
  if (res.verifier_agent) {
    const r = res.verifier_agent;
    setAgentState('verifier', 'done', `✓${r.accepted} ✗${r.auto_rejected}`);
    addProgress(`Verifier: ${r.accepted} accepted, ${r.auto_rejected} auto-rejected (self-correction)`, 'done');
  }

  // Gap
  if (res.gap_agent) {
    const r = res.gap_agent;
    setAgentState('gap', 'done', r.gaps_found);
    addProgress(`Gap Agent: ${r.gaps_found} missing articles identified`, 'done');
    // Add gap findings
    const gapFindings = r.findings || [];
    allFindings = [...allFindings, ...gapFindings];
  }

  // Failed steps
  result.plan.forEach(step => {
    if (step.status === 'failed') {
      const key = step.agent.replace('_agent', '');
      setAgentState(key, 'failed', 'ERR');
    }
  });

  updateLogCount();
  resetBtn();
}

function resetBtn() {
  const btn = document.getElementById('btnSweep');
  btn.disabled = false;
  btn.innerHTML = '<span class="btn-icon">▶</span> Run Full Sweep';
}

// ── Findings ──────────────────────────────────────────────────────────────

let currentFilter = 'all';

function filterFindings(type) {
  currentFilter = type;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  const filtered = type === 'all' ? allFindings : allFindings.filter(f => f.finding_type === type);
  renderFindings(filtered);
}

function renderFindings(findings) {
  const el = document.getElementById('findingsList');
  if (!findings || findings.length === 0) {
    el.innerHTML = '<div class="empty-state">No findings' + (currentFilter !== 'all' ? ' for this filter' : '') + '</div>';
    return;
  }
  el.innerHTML = findings.map(f => `
    <div class="finding-card" data-type="${f.finding_type}">
      <div class="finding-header">
        <span class="finding-type-badge type-${f.finding_type}">${f.finding_type.replace('_', ' ')}</span>
        <span class="finding-sev-badge sev-${f.severity}">${f.severity}</span>
        <span class="finding-id">#${f.finding_id}</span>
      </div>
      <div class="finding-desc">${escHtml(f.description)}</div>
      <div class="finding-evidence">🔍 ${escHtml(f.evidence)}</div>
      <div class="finding-action">→ ${escHtml(f.suggested_action)}</div>
      <div class="finding-conf">Confidence: ${(f.confidence * 100).toFixed(0)}%</div>
    </div>
  `).join('');
}

// ── Review queue ──────────────────────────────────────────────────────────

async function loadReviewQueue() {
  try {
    const res = await fetch(API + '/api/review-queue');
    const data = await res.json();
    renderReviewQueue(data.proposals || []);
    document.getElementById('stat-proposals').textContent = data.total;
  } catch (e) { /* silently fail */ }
}

function renderReviewQueue(proposals) {
  const el = document.getElementById('reviewList');
  if (!proposals || proposals.length === 0) {
    el.innerHTML = '<div class="empty-state">No proposals yet — run a sweep first</div>';
    return;
  }
  el.innerHTML = proposals.map(p => {
    const score = p.verifier_score != null ? `Score: ${(p.verifier_score * 100).toFixed(0)}%` : 'Unverified';
    const isPending = p.status === 'pending';
    return `
    <div class="review-card">
      <div class="review-card-header" onclick="toggleReview('${p.proposal_id}')">
        <span class="review-action-badge action-${p.action}">${p.action}</span>
        <span class="review-status-badge status-${p.status}">${p.status}</span>
        <span style="font-size:0.78rem;color:var(--muted);flex:1;padding:0 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
          ${escHtml(p.target_articles?.join(', ') || '')}
        </span>
        <span class="review-verifier">${score}</span>
        <span style="margin-left:8px;color:var(--muted);font-size:0.8rem;">▾</span>
      </div>
      <div class="review-card-body" id="body-${p.proposal_id}">
        <div class="review-rationale">${escHtml(p.rationale || '')}</div>
        <div class="review-content">${escHtml(p.proposed_content || '')}</div>
        ${p.verifier_notes ? `<div style="font-size:0.75rem;color:var(--muted);">Verifier: ${escHtml(p.verifier_notes)}</div>` : ''}
        ${isPending ? `
        <div class="review-actions">
          <input class="review-notes-input" id="notes-${p.proposal_id}" placeholder="Review notes (optional)" />
          <button class="btn-approve" onclick="reviewAction('${p.proposal_id}', 'approve')">✓ Approve</button>
          <button class="btn-reject" onclick="reviewAction('${p.proposal_id}', 'reject')">✗ Reject</button>
        </div>` : ''}
      </div>
    </div>`;
  }).join('');
}

function toggleReview(id) {
  const body = document.getElementById('body-' + id);
  body.classList.toggle('open');
}

async function reviewAction(proposalId, action) {
  const notes = document.getElementById('notes-' + proposalId)?.value || '';
  try {
    await fetch(API + `/api/review/${proposalId}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes }),
    });
    loadReviewQueue();
    updateLogCount();
  } catch (e) {
    alert('Failed to ' + action + ': ' + e.message);
  }
}

// ── Q&A ───────────────────────────────────────────────────────────────────

async function askQuestion() {
  const input = document.getElementById('qaInput');
  const q = input.value.trim();
  if (!q) return;

  const resEl = document.getElementById('qaResult');
  resEl.innerHTML = '<div class="empty-state">Thinking…</div>';

  try {
    const res = await fetch(API + '/api/qa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, n_results: 5 }),
    });
    const data = await res.json();
    const conf = (data.confidence * 100).toFixed(0);
    const citations = (data.citations || []).map(c => `<span class="qa-chip">📄 ${c}</span>`).join('');
    resEl.innerHTML = `
      <div class="qa-answer">${escHtml(data.answer)}</div>
      <div class="qa-meta">
        ${citations}
        <div class="qa-confidence">
          <span>Confidence:</span>
          <div class="confidence-bar">
            <div class="confidence-fill" style="width:${conf}%"></div>
          </div>
          <span>${conf}%</span>
        </div>
      </div>
      ${data.caveat ? `<div class="qa-caveat">⚠ ${escHtml(data.caveat)}</div>` : ''}
    `;
    updateLogCount();
  } catch (e) {
    resEl.innerHTML = `<div class="empty-state" style="color:var(--red)">Error: ${e.message}</div>`;
  }
}

function setQ(q) {
  document.getElementById('qaInput').value = q;
  askQuestion();
}

// ── Audit log ─────────────────────────────────────────────────────────────

async function loadAuditLog() {
  try {
    const res = await fetch(API + '/api/audit-log');
    const data = await res.json();
    renderAuditLog(data.entries || []);
    document.getElementById('stat-log').textContent = data.total;
  } catch (e) { /* silently fail */ }
}

async function updateLogCount() {
  try {
    const res = await fetch(API + '/api/audit-log');
    const data = await res.json();
    document.getElementById('stat-log').textContent = data.total;
  } catch (e) {}
}

function renderAuditLog(entries) {
  const el = document.getElementById('auditLogList');
  if (!entries || entries.length === 0) {
    el.innerHTML = '<div class="empty-state">No audit events yet</div>';
    return;
  }
  // Show newest first
  el.innerHTML = [...entries].reverse().map(e => `
    <div class="log-entry">
      <span class="log-agent">${e.agent}</span>
      <div style="flex:1;min-width:0;">
        <div class="log-action">${e.action}</div>
        <div class="log-notes">${escHtml(e.notes || '')}</div>
      </div>
      <span class="log-outcome outcome-${e.outcome}">${e.outcome}</span>
      <span class="log-time">${fmtTime(e.timestamp)}</span>
    </div>
  `).join('');
}

// ── Reset ─────────────────────────────────────────────────────────────────

async function resetSystem() {
  if (!confirm('Reset all data and start fresh?')) return;
  await fetch(API + '/api/reset', { method: 'POST' });
  allFindings = [];
  document.getElementById('progressLog').innerHTML = '<div class="empty-state">Run a sweep to see live progress →</div>';
  document.getElementById('findingsList').innerHTML = '<div class="empty-state">Run a sweep to see audit findings</div>';
  document.getElementById('reviewList').innerHTML = '<div class="empty-state">No proposals yet</div>';
  document.getElementById('auditLogList').innerHTML = '<div class="empty-state">Audit log will appear here</div>';
  document.getElementById('qaResult').innerHTML = '<div class="empty-state">Ask a question to get a cited answer</div>';
  ['stat-articles','stat-findings','stat-proposals','stat-log'].forEach(id => {
    document.getElementById(id).textContent = '–';
  });
  ['ingestion','audit','remediation','verifier','gap'].forEach(k => setAgentState(k, '', '–'));
  setStatus('Idle', '');
}

// ── Helpers ───────────────────────────────────────────────────────────────

function escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtTime(ts) {
  if (!ts) return '';
  try {
    return new Date(ts).toLocaleTimeString();
  } catch { return ts; }
}

// ── Init ──────────────────────────────────────────────────────────────────

(async function init() {
  // Load initial stats
  try {
    const [art, log] = await Promise.all([
      fetch(API + '/api/articles?limit=1').then(r => r.json()),
      fetch(API + '/api/audit-log').then(r => r.json()),
    ]);
    if (art.total > 0) document.getElementById('stat-articles').textContent = art.total;
    if (log.total > 0) {
      document.getElementById('stat-log').textContent = log.total;
      setStatus('Ready', 'done');
    }
  } catch (e) { /* server might not be up yet */ }
})();
