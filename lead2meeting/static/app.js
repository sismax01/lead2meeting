/* Lead2Meeting Frontend Application Logic — Engineered by SISMAX */

const state = {
  leads: [],
  reps: [],
  recoveryTasks: [],
  analytics: null,
  selectedLeadId: null,
};

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatCurrency(val) {
  const num = Number(val || 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(num);
}

function formatStageLabel(stage) {
  if (!stage) return "";
  return stage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  const el = document.createElement("div");
  el.className = `toast ${type === "error" ? "toast-error" : type === "success" ? "toast-success" : ""}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 4200);
}

async function apiFetch(url, options = {}) {
  const opts = {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  };
  const resp = await fetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail =
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || "Request failed");
    throw new Error(detail);
  }
  return { status: resp.status, data };
}

async function refreshAll(preserveSelection = true) {
  try {
    const search = document.getElementById("searchLeadsInput").value.trim();
    const stage = document.getElementById("filterStageSelect").value;
    const health = document.getElementById("filterHealthSelect").value;
    const recStatus = document.getElementById("filterRecoveryStatus").value;

    const params = new URLSearchParams();
    if (stage) params.set("stage", stage);
    if (search) params.set("search", search);
    if (health === "stalled") params.set("stalled_only", "true");
    if (health === "failed") params.set("failed_only", "true");
    if (health === "recovered") params.set("recovered_only", "true");

    const recParams = new URLSearchParams();
    if (recStatus) recParams.set("status", recStatus);

    const [analyticsRes, repsRes, leadsRes, recRes] = await Promise.all([
      apiFetch("/api/analytics"),
      apiFetch("/api/reps"),
      apiFetch(`/api/leads?${params.toString()}`),
      apiFetch(`/api/recovery/tasks?${recParams.toString()}`),
    ]);

    state.analytics = analyticsRes.data;
    state.reps = repsRes.data.reps || [];
    state.leads = leadsRes.data.leads || [];
    state.recoveryTasks = recRes.data.tasks || [];

    renderKPIs();
    renderLeadsTable();
    renderRecoveryQueue();
    renderAnalyticsTab();

    if (preserveSelection && state.selectedLeadId) {
      await loadLeadInspector(state.selectedLeadId, false);
    } else if (state.leads.length > 0 && !state.selectedLeadId) {
      await loadLeadInspector(state.leads[0].id, false);
    }
  } catch (err) {
    showToast(`Failed to load platform state: ${err.message}`, "error");
  }
}

function renderKPIs() {
  const a = state.analytics;
  if (!a) return;

  const modeBanner = document.getElementById("integrationModeBanner");
  const modeText = document.getElementById("integrationModeText");
  const mode = (a.environment?.integration_mode || "simulated").toUpperCase();
  if (mode === "LIVE") {
    modeBanner.className = "mode-badge mode-live";
    modeText.textContent = "Mode: LIVE INTEGRATIONS";
  } else {
    modeBanner.className = "mode-badge mode-simulated";
    modeText.textContent = "Mode: SIMULATED SANDBOX";
  }

  document.getElementById("kpiConversionRate").textContent =
    `${a.summary.qualified_to_booked_rate_pct}%`;
  document.getElementById("kpiConversionMeta").textContent =
    `${a.summary.booked_meetings} booked of ${a.summary.qualified_leads} qualified (${a.summary.total_leads} total)`;

  document.getElementById("kpiMedianResponse").textContent =
    `${a.response_times.median_minutes}m`;
  document.getElementById("kpiSlaCompliance").textContent =
    `SLA Compliance: ${a.response_times.sla_compliance_pct}% (Target ≤ ${a.response_times.sla_target_minutes}m)`;

  document.getElementById("kpiAtRiskRevenue").textContent =
    formatCurrency(a.revenue.at_risk_pipeline_usd);
  document.getElementById("kpiAtRiskMeta").textContent =
    `${a.summary.stalled_leads} stalled, ${a.summary.failed_operations_leads} failed ops`;

  document.getElementById("kpiRecoveredRevenue").textContent =
    formatCurrency(a.revenue.recovered_pipeline_usd);
  document.getElementById("kpiRecoveredMeta").textContent =
    `${a.summary.recovered_leads} leads recovered (${a.recovery_metrics.recovery_rate_pct}% task resolution)`;

  document.getElementById("kpiBookedRevenue").textContent =
    formatCurrency(a.revenue.booked_meeting_pipeline_usd);
  document.getElementById("kpiDuplicatesMeta").textContent =
    `Active pipeline: ${formatCurrency(a.revenue.total_active_pipeline_usd)} • Duplicates merged: ${a.summary.duplicate_inquiries_merged}`;

  document.getElementById("tabLeadCount").textContent = state.leads.length;
  document.getElementById("tabRecoveryCount").textContent =
    a.recovery_metrics.open_tasks;
}

function renderLeadsTable() {
  const tbody = document.getElementById("leadsTableBody");
  if (!state.leads.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">No leads match current filter criteria.</td></tr>`;
    return;
  }

  tbody.innerHTML = state.leads
    .map((l) => {
      const isSelected = l.id === state.selectedLeadId;
      const stagePillClass =
        l.stage === "meeting_booked" || l.stage === "completed"
          ? "pill-booked"
          : l.stage === "disqualified" || l.stage === "lost"
          ? "pill-neutral"
          : "pill-stage";

      const badges = [];
      if (l.has_failed_operation) {
        badges.push(`<span class="pill pill-failed">Failed: ${escapeHtml(l.failed_operation_type)}</span>`);
      }
      if (l.is_stalled) {
        badges.push(`<span class="pill pill-stalled">Stalled SLA</span>`);
      }
      if (l.recovered) {
        badges.push(`<span class="pill pill-recovered">Recovered</span>`);
      }
      if (l.escalated) {
        badges.push(`<span class="pill pill-failed">Escalated</span>`);
      }
      if (l.duplicate_count > 0) {
        badges.push(`<span class="pill pill-neutral">Merged ×${l.duplicate_count + 1}</span>`);
      }
      if (!badges.length) {
        badges.push(`<span class="pill pill-neutral">Healthy</span>`);
      }

      return `
        <tr data-lead-id="${l.id}" class="${isSelected ? "selected-row" : ""}">
          <td>
            <strong>${escapeHtml(l.company)}</strong><br/>
            <span class="text-muted">${escapeHtml(l.full_name)} • ${escapeHtml(l.email)}</span>
          </td>
          <td>
            <strong>${formatCurrency(l.estimated_budget)}</strong><br/>
            <span class="text-muted">${escapeHtml(formatStageLabel(l.service_interest))}</span>
          </td>
          <td>
            <strong>${l.qualification_score}</strong>/100<br/>
            <span class="text-muted">${escapeHtml(l.qualification_tier.replace(/_/g, " "))}</span>
          </td>
          <td>
            <span class="pill ${stagePillClass}">${escapeHtml(formatStageLabel(l.stage))}</span><br/>
            <span class="text-muted" style="font-size:11px">${escapeHtml(l.substatus)}</span>
          </td>
          <td>${badges.join(" ")}</td>
          <td>${escapeHtml(l.rep_name || "Unassigned")}</td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll("tr[data-lead-id]").forEach((row) => {
    row.addEventListener("click", () => {
      const id = Number(row.getAttribute("data-lead-id"));
      loadLeadInspector(id, true);
    });
  });
}

async function loadLeadInspector(leadId, highlightRow = true) {
  state.selectedLeadId = leadId;
  if (highlightRow) {
    document.querySelectorAll("#leadsTableBody tr").forEach((tr) => {
      tr.classList.toggle(
        "selected-row",
        Number(tr.getAttribute("data-lead-id")) === leadId
      );
    });
  }

  try {
    const { data: lead } = await apiFetch(`/api/leads/${leadId}`);
    document.getElementById("inspectorEmptyState").classList.add("hidden");
    const container = document.getElementById("inspectorContent");
    container.classList.remove("hidden");

    const openTasks = (lead.recovery_tasks || []).filter(
      (t) => t.status === "open" || t.status === "retrying"
    );

    const alertsHtml = [];
    if (lead.has_failed_operation) {
      alertsHtml.push(`
        <div class="alert-banner alert-danger">
          <strong>Failed Operation (${escapeHtml(lead.failed_operation_type)}):</strong>
          ${escapeHtml(lead.last_error_message || "Operation failed")}
        </div>
      `);
    }
    if (lead.is_stalled) {
      alertsHtml.push(`
        <div class="alert-banner alert-warning">
          <strong>Stalled Prospect Detected:</strong>
          ${escapeHtml(lead.stall_reason || "SLA threshold exceeded")}
        </div>
      `);
    }
    if (lead.recovered) {
      alertsHtml.push(`
        <div class="alert-banner alert-success">
          <strong>Recovered Opportunity:</strong> Previously stalled or failed operation was resolved (${escapeHtml(lead.recovered_at || "")}).
        </div>
      `);
    }

    const openTasksHtml = openTasks.length
      ? `
      <div class="inspector-section">
        <h4>Active Recovery Actions Required (${openTasks.length})</h4>
        ${openTasks
          .map(
            (t) => `
          <div class="action-box">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
              <strong>${escapeHtml(t.title)}</strong>
              <span class="pill pill-failed">${escapeHtml(t.severity)} • Attempts: ${t.attempt_count}/${t.max_attempts}</span>
            </div>
            <p class="text-muted" style="margin:4px 0 8px;">${escapeHtml(t.description)}</p>
            <div class="form-row">
              <button class="btn btn-success btn-sm" onclick="handleRetryTask(${t.id}, true)">
                Safe Retry &amp; Recover Now
              </button>
              <button class="btn btn-outline btn-sm" onclick="handleRetryTask(${t.id}, false)">
                Simulate Retry Failure
              </button>
            </div>
          </div>
        `
          )
          .join("")}
      </div>
    `
      : "";

    const meetingsHtml = (lead.meetings || []).length
      ? (lead.meetings || [])
          .map(
            (m) => `
          <div class="action-box">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <strong>${escapeHtml(m.title)}</strong>
              <span class="pill ${m.status === "confirmed" || m.status === "completed" ? "pill-booked" : m.status === "sync_failed" || m.status === "no_show" ? "pill-failed" : "pill-stage"}">
                ${escapeHtml(m.status)} (${escapeHtml(m.execution_mode)})
              </span>
            </div>
            <div class="text-muted" style="margin:4px 0;">
              Slot: ${escapeHtml(m.scheduled_start)} (${m.duration_minutes}m) • Follow-ups: ${m.followup_count}
              ${m.meeting_link ? `<br/>Link: <code>${escapeHtml(m.meeting_link)}</code>` : ""}
              ${m.sync_error ? `<br/><span style="color:var(--danger)">Error: ${escapeHtml(m.sync_error)}</span>` : ""}
            </div>
            <div class="form-row" style="margin-top:8px;">
              ${
                m.status !== "confirmed" && m.status !== "sync_failed"
                  ? `<button class="btn btn-success btn-sm" onclick="handleMeetingStatus(${m.id}, 'confirmed')">Mark Confirmed (Booked)</button>`
                  : ""
              }
              ${
                m.status === "confirmed"
                  ? `<button class="btn btn-primary btn-sm" onclick="handleMeetingStatus(${m.id}, 'completed')">Mark Completed</button>`
                  : ""
              }
              ${
                m.status !== "no_show" && m.status !== "completed" && m.status !== "sync_failed"
                  ? `<button class="btn btn-outline btn-sm" onclick="handleMeetingStatus(${m.id}, 'no_show')">Flag No-Show</button>`
                  : ""
              }
            </div>
          </div>
        `
          )
          .join("")
      : `<p class="text-muted">No meetings scheduled yet for this lead.</p>`;

    const repOptions = state.reps
      .map(
        (r) =>
          `<option value="${r.id}" ${lead.assigned_rep_id === r.id ? "selected" : ""}>${escapeHtml(r.name)} (${escapeHtml(r.specialty)})</option>`
      )
      .join("");

    const defaultSlot = new Date(Date.now() + 48 * 3600 * 1000)
      .toISOString()
      .slice(0, 16) + ":00+00:00";

    const eventsHtml = (lead.events || [])
      .map(
        (ev) => `
        <li class="event-item status-${escapeHtml(ev.status)}">
          <div><strong>${escapeHtml(ev.event_type)}</strong> — ${escapeHtml(ev.summary)}</div>
          <div class="event-meta">
            <span>${escapeHtml(ev.created_at)}</span>
            <span>Mode: <strong>${escapeHtml(ev.execution_mode.toUpperCase())}</strong></span>
            <span>Actor: ${escapeHtml(ev.actor)}</span>
          </div>
        </li>
      `
      )
      .join("");

    container.innerHTML = `
      <div class="inspector-header">
        <div class="inspector-title-row">
          <div>
            <h3>${escapeHtml(lead.company)}</h3>
            <p class="text-muted">${escapeHtml(lead.full_name)} • ${escapeHtml(lead.email)} ${lead.phone ? "• " + escapeHtml(lead.phone) : ""}</p>
          </div>
          <span class="pill pill-stage">${escapeHtml(formatStageLabel(lead.stage))}</span>
        </div>
        <div class="text-muted mt-sm">
          <strong>Budget:</strong> ${formatCurrency(lead.estimated_budget)} &nbsp;|&nbsp;
          <strong>Service:</strong> ${escapeHtml(formatStageLabel(lead.service_interest))} &nbsp;|&nbsp;
          <strong>Score:</strong> ${lead.qualification_score}/100 (${escapeHtml(lead.qualification_tier)})
        </div>
        ${lead.inquiry_notes ? `<p class="mt-sm" style="background:var(--bg-subtle);padding:8px;border-radius:6px;font-size:12.5px;">${escapeHtml(lead.inquiry_notes)}</p>` : ""}
      </div>

      ${alertsHtml.join("")}
      ${openTasksHtml}

      <!-- Schedule & Manage Meetings -->
      <div class="inspector-section">
        <h4>Meetings &amp; Calendar Provisioning</h4>
        ${meetingsHtml}
        <div class="action-box mt-sm">
          <strong>Schedule Discovery Meeting</strong>
          <div class="form-row">
            <input type="text" id="schedTitle_${lead.id}" class="input-control" value="${escapeHtml(lead.company)} — Technical Discovery" placeholder="Meeting title" />
            <input type="text" id="schedStart_${lead.id}" class="input-control" value="${defaultSlot}" placeholder="ISO start time" />
          </div>
          <div class="form-row" style="align-items:center;justify-content:space-between;margin-top:8px;">
            <label class="checkbox-label">
              <input type="checkbox" id="schedSimFail_${lead.id}" />
              <span>Simulate Calendar API 504 Timeout</span>
            </label>
            <button class="btn btn-primary btn-sm" onclick="handleScheduleMeeting(${lead.id})">
              Provision Calendar Invite
            </button>
          </div>
        </div>
      </div>

      <!-- Follow-up Outreach -->
      <div class="inspector-section">
        <h4>Log Follow-Up Outreach</h4>
        <div class="action-box">
          <div class="form-row">
            <select id="followupChannel_${lead.id}" class="input-control" style="max-width:120px;">
              <option value="email">Email</option>
              <option value="phone">Phone Call</option>
              <option value="linkedin">LinkedIn</option>
            </select>
            <input type="text" id="followupMsg_${lead.id}" class="input-control" placeholder="Summary of follow-up message or call outcome..." value="Sent architecture case study and proposed 2 meeting slots." />
          </div>
          <div class="form-row" style="align-items:center;justify-content:space-between;margin-top:8px;">
            <label class="checkbox-label">
              <input type="checkbox" id="followupSimFail_${lead.id}" />
              <span>Simulate Gateway Delivery Error</span>
            </label>
            <button class="btn btn-secondary btn-sm" onclick="handleSendFollowup(${lead.id})">
              Record / Dispatch Follow-Up
            </button>
          </div>
        </div>
      </div>

      <!-- Assignment, Lifecycle Stage & Escalation -->
      <div class="inspector-section">
        <h4>Ownership, Lifecycle Stage &amp; Escalation</h4>
        <div class="form-row">
          <select id="assignRepSelect_${lead.id}" class="input-control">
            ${repOptions}
          </select>
          <button class="btn btn-outline btn-sm" onclick="handleReassignLead(${lead.id})">Reassign Rep</button>
        </div>
        <div class="form-row mt-sm">
          <select id="updateStageSelect_${lead.id}" class="input-control">
            <option value="new" ${lead.stage === "new" ? "selected" : ""}>New</option>
            <option value="qualified" ${lead.stage === "qualified" ? "selected" : ""}>Qualified</option>
            <option value="contacted" ${lead.stage === "contacted" ? "selected" : ""}>Contacted</option>
            <option value="meeting_scheduled" ${lead.stage === "meeting_scheduled" ? "selected" : ""}>Meeting Scheduled</option>
            <option value="meeting_booked" ${lead.stage === "meeting_booked" ? "selected" : ""}>Meeting Booked</option>
            <option value="completed" ${lead.stage === "completed" ? "selected" : ""}>Completed</option>
            <option value="lost" ${lead.stage === "lost" ? "selected" : ""}>Lost Opportunity</option>
            <option value="disqualified" ${lead.stage === "disqualified" ? "selected" : ""}>Disqualified</option>
          </select>
          <button class="btn btn-outline btn-sm" onclick="handleUpdateStage(${lead.id})">Update Stage</button>
          <button class="btn btn-danger btn-sm" onclick="handleEscalateLead(${lead.id})">Escalate to VP</button>
        </div>
      </div>

      <!-- Complete Event Audit Log -->
      <div class="inspector-section">
        <h4>Complete Lead Event &amp; Recovery History (${(lead.events || []).length})</h4>
        <ul class="event-timeline">
          ${eventsHtml}
        </ul>
      </div>
    `;
  } catch (err) {
    showToast(`Could not load lead #${leadId}: ${err.message}`, "error");
  }
}

function renderRecoveryQueue() {
  const container = document.getElementById("recoveryTasksList");
  if (!state.recoveryTasks.length) {
    container.innerHTML = `<div class="card"><p class="text-muted">No recovery tasks match the selected status filter.</p></div>`;
    return;
  }

  container.innerHTML = state.recoveryTasks
    .map((t) => {
      const isResolved = t.status === "resolved";
      return `
        <div class="recovery-card sev-${escapeHtml(t.severity)} status-${escapeHtml(t.status)}">
          <div>
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
              <strong>${escapeHtml(t.title)}</strong>
              <span class="pill ${isResolved ? "pill-recovered" : "pill-failed"}">${escapeHtml(t.status.toUpperCase())}</span>
            </div>
            <p class="text-muted" style="margin:6px 0;">
              <strong>Company:</strong> ${escapeHtml(t.company)} (${formatCurrency(t.estimated_budget)}) •
              <strong>Owner:</strong> ${escapeHtml(t.rep_name || "Unassigned")}
            </p>
            <p style="font-size:13px;margin-bottom:6px;">${escapeHtml(t.description)}</p>
            <p class="text-muted">
              Operation: <code>${escapeHtml(t.operation_type)}</code> •
              Attempts: ${t.attempt_count}/${t.max_attempts}
              ${t.escalated_to ? ` • Escalated to: <strong>${escapeHtml(t.escalated_to)}</strong>` : ""}
            </p>
            ${
              t.resolution_notes
                ? `<p class="mt-sm" style="color:var(--success);font-size:12px;"><strong>Resolution:</strong> ${escapeHtml(t.resolution_notes)}</p>`
                : ""
            }
          </div>
          <div class="form-row">
            ${
              !isResolved
                ? `
              <button class="btn btn-success btn-sm" onclick="handleRetryTask(${t.id}, true)">
                Execute Safe Retry &amp; Recover
              </button>
              <button class="btn btn-outline btn-sm" onclick="handleRetryTask(${t.id}, false)">
                Simulate Retry Fail
              </button>
            `
                : `<span class="pill pill-recovered">Resolved at ${escapeHtml(t.resolved_at || "")}</span>`
            }
            <button class="btn btn-outline btn-sm" onclick="inspectLeadFromQueue(${t.lead_id})">
              Inspect Journey
            </button>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderAnalyticsTab() {
  const a = state.analytics;
  if (!a) return;

  const maxCount = Math.max(1, ...(a.funnel || []).map((f) => f.count));
  document.getElementById("funnelContainer").innerHTML = (a.funnel || [])
    .map((f) => {
      const pct = Math.round((f.count / maxCount) * 100);
      return `
        <div class="funnel-row">
          <div><strong>${escapeHtml(formatStageLabel(f.stage))}</strong> (${f.count})</div>
          <div class="funnel-bar-bg">
            <div class="funnel-bar-fill" style="width:${pct}%"></div>
          </div>
          <div style="text-align:right;font-weight:600;">${formatCurrency(f.pipeline_value)}</div>
        </div>
      `;
    })
    .join("");

  document.getElementById("repsTableBody").innerHTML = (a.reps || [])
    .map(
      (r) => `
      <tr>
        <td>
          <strong>${escapeHtml(r.name)}</strong><br/>
          <span class="text-muted">${escapeHtml(r.role)}</span>
        </td>
        <td>${escapeHtml(formatStageLabel(r.specialty))}</td>
        <td>${r.active_lead_count} / ${r.capacity}</td>
        <td><span class="pill pill-booked">${r.booked_count || 0}</span></td>
        <td>${r.stalled_count ? `<span class="pill pill-stalled">${r.stalled_count}</span>` : "0"}</td>
        <td>${r.calendar_connected ? `<span class="pill pill-recovered">Connected</span>` : `<span class="pill pill-failed">Revoked</span>`}</td>
      </tr>
    `
    )
    .join("");

  document.getElementById("globalEventsBody").innerHTML = (a.recent_events || [])
    .map(
      (ev) => `
      <tr>
        <td style="white-space:nowrap;font-size:12px;">${escapeHtml(ev.created_at)}</td>
        <td><strong>${escapeHtml(ev.company)}</strong><br/><span class="text-muted">${escapeHtml(ev.full_name)}</span></td>
        <td><code>${escapeHtml(ev.event_type)}</code></td>
        <td><span class="pill pill-neutral">${escapeHtml(ev.execution_mode.toUpperCase())}</span></td>
        <td><span class="pill ${ev.status === "failed" ? "pill-failed" : ev.status === "recovered" ? "pill-recovered" : "pill-stage"}">${escapeHtml(ev.status)}</span></td>
        <td>${escapeHtml(ev.summary)}</td>
      </tr>
    `
    )
    .join("");
}

/* User Action Handlers */

window.handleRetryTask = async function (taskId, forceSucceed) {
  try {
    const { data } = await apiFetch(`/api/recovery/tasks/${taskId}/retry`, {
      method: "POST",
      body: JSON.stringify({
        actor: "ops_operator",
        force_succeed_simulation: forceSucceed,
      }),
    });
    if (data.status === "resolved") {
      showToast(`Recovered task #${taskId}: ${data.message}`, "success");
    } else {
      showToast(`Task #${taskId} status: ${data.status} — ${data.message}`, "error");
    }
    await refreshAll(true);
  } catch (err) {
    showToast(`Retry failed: ${err.message}`, "error");
  }
};

window.handleScheduleMeeting = async function (leadId) {
  try {
    const title = document.getElementById(`schedTitle_${leadId}`).value.trim();
    const scheduled_start = document.getElementById(`schedStart_${leadId}`).value.trim();
    const simulate_calendar_failure = document.getElementById(`schedSimFail_${leadId}`).checked;

    await apiFetch(`/api/leads/${leadId}/meetings`, {
      method: "POST",
      body: JSON.stringify({
        title,
        scheduled_start,
        duration_minutes: 30,
        provider: "cal_webhook",
        actor: "assigned_rep",
        simulate_calendar_failure,
      }),
    });

    if (simulate_calendar_failure) {
      showToast(
        "Simulated Calendar API 504 Failure recorded! Recovery task created.",
        "error"
      );
    } else {
      showToast("Calendar invite provisioned in sandbox.", "success");
    }
    await refreshAll(true);
  } catch (err) {
    showToast(`Schedule failed: ${err.message}`, "error");
  }
};

window.handleMeetingStatus = async function (meetingId, status) {
  try {
    await apiFetch(`/api/meetings/${meetingId}/status`, {
      method: "PATCH",
      body: JSON.stringify({
        status,
        actor: "assigned_rep",
        notes: `Updated meeting status to ${status} via operator console.`,
      }),
    });
    showToast(`Meeting #${meetingId} marked as ${status}.`, "success");
    await refreshAll(true);
  } catch (err) {
    showToast(`Could not update meeting: ${err.message}`, "error");
  }
};

window.handleSendFollowup = async function (leadId) {
  try {
    const channel = document.getElementById(`followupChannel_${leadId}`).value;
    const message_summary = document.getElementById(`followupMsg_${leadId}`).value.trim();
    const simulate_delivery_failure = document.getElementById(`followupSimFail_${leadId}`).checked;

    await apiFetch(`/api/leads/${leadId}/followups`, {
      method: "POST",
      body: JSON.stringify({
        channel,
        message_summary,
        actor: "assigned_rep",
        simulate_delivery_failure,
      }),
    });

    if (simulate_delivery_failure) {
      showToast("Simulated follow-up delivery failure logged to Recovery Queue.", "error");
    } else {
      showToast("Follow-up logged and SLA timer updated.", "success");
    }
    await refreshAll(true);
  } catch (err) {
    showToast(`Follow-up error: ${err.message}`, "error");
  }
};

window.handleReassignLead = async function (leadId) {
  try {
    const rep_id = Number(document.getElementById(`assignRepSelect_${leadId}`).value);
    await apiFetch(`/api/leads/${leadId}/assign`, {
      method: "POST",
      body: JSON.stringify({
        rep_id,
        actor: "ops_manager",
        reason: "Manual ownership reassignment via inspector",
      }),
    });
    showToast("Lead reassigned.", "success");
    await refreshAll(true);
  } catch (err) {
    showToast(`Reassign failed: ${err.message}`, "error");
  }
};

window.handleUpdateStage = async function (leadId) {
  try {
    const stage = document.getElementById(`updateStageSelect_${leadId}`).value;
    await apiFetch(`/api/leads/${leadId}/stage`, {
      method: "PATCH",
      body: JSON.stringify({
        stage,
        actor: "ops_manager",
      }),
    });
    showToast(`Lead stage updated to ${formatStageLabel(stage)}.`, "success");
    await refreshAll(true);
  } catch (err) {
    showToast(`Stage update failed: ${err.message}`, "error");
  }
};

window.handleEscalateLead = async function (leadId) {
  try {
    await apiFetch(`/api/leads/${leadId}/escalate`, {
      method: "POST",
      body: JSON.stringify({
        escalate_to: "vp_client_partnerships",
        reason: "High-value opportunity requires executive intervention to unblock scheduling.",
        actor: "ops_operator",
      }),
    });
    showToast("Lead escalated to VP of Client Partnerships.", "success");
    await refreshAll(true);
  } catch (err) {
    showToast(`Escalation failed: ${err.message}`, "error");
  }
};

window.inspectLeadFromQueue = function (leadId) {
  document.querySelector('[data-tab="pipelineTab"]').click();
  loadLeadInspector(leadId, true);
};

/* Setup Event Listeners */
document.addEventListener("DOMContentLoaded", () => {
  // Tabs navigation
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const target = btn.getAttribute("data-tab");
      document.getElementById(target).classList.add("active");
    });
  });

  // Filters
  document.getElementById("searchLeadsInput").addEventListener("input", () => refreshAll(true));
  document.getElementById("filterStageSelect").addEventListener("change", () => refreshAll(true));
  document.getElementById("filterHealthSelect").addEventListener("change", () => refreshAll(true));
  document.getElementById("filterRecoveryStatus").addEventListener("change", () => refreshAll(true));

  // SLA Scan button
  document.getElementById("btnScanStalls").addEventListener("click", async () => {
    try {
      const { data } = await apiFetch("/api/recovery/scan", { method: "POST" });
      showToast(
        `SLA Watchdog Scan complete: ${data.newly_flagged_count} newly flagged, ${data.already_stalled_count} existing stalled leads.`,
        "success"
      );
      await refreshAll(true);
    } catch (err) {
      showToast(`SLA scan failed: ${err.message}`, "error");
    }
  });

  // Reset Demo button
  document.getElementById("btnResetDemo").addEventListener("click", async () => {
    try {
      await apiFetch("/api/demo/reset", { method: "POST" });
      state.selectedLeadId = null;
      showToast("Sample dataset reset to initial state.", "success");
      await refreshAll(false);
    } catch (err) {
      showToast(`Reset failed: ${err.message}`, "error");
    }
  });

  // Capture Lead Modal
  const modal = document.getElementById("captureModal");
  document.getElementById("btnOpenCaptureModal").addEventListener("click", () => {
    modal.classList.remove("hidden");
  });
  document.getElementById("btnCloseCaptureModal").addEventListener("click", () => {
    modal.classList.add("hidden");
  });
  document.getElementById("btnCancelCapture").addEventListener("click", () => {
    modal.classList.add("hidden");
  });

  document.getElementById("captureLeadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const payload = {
        full_name: document.getElementById("leadFullName").value.trim(),
        email: document.getElementById("leadEmail").value.trim(),
        company: document.getElementById("leadCompany").value.trim(),
        phone: document.getElementById("leadPhone").value.trim() || null,
        service_interest: document.getElementById("leadService").value,
        estimated_budget: Number(document.getElementById("leadBudget").value || 0),
        project_timeline: document.getElementById("leadTimeline").value,
        company_size: document.getElementById("leadSize").value,
        inquiry_notes: document.getElementById("leadNotes").value.trim() || null,
        simulate_crm_failure: document.getElementById("leadSimCrmFail").checked,
      };
      const { data } = await apiFetch("/api/leads", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      modal.classList.add("hidden");
      document.getElementById("captureLeadForm").reset();
      state.selectedLeadId = data.id;
      if (data.deduplicated) {
        showToast(
          `Duplicate email detected! Merged into existing lead #${data.id} (${data.company}).`,
          "info"
        );
      } else if (payload.simulate_crm_failure) {
        showToast(
          `Lead #${data.id} captured, but simulated CRM sync failed — queued for recovery!`,
          "error"
        );
      } else {
        showToast(
          `Captured & qualified ${data.company} (Score: ${data.qualification_score}/100).`,
          "success"
        );
      }
      await refreshAll(true);
    } catch (err) {
      showToast(`Capture error: ${err.message}`, "error");
    }
  });

  refreshAll(false);
});
