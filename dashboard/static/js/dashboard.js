/**
 * GridMind Command Center: Operational Telemetry & Agent Observability Client.
 * ES Module implementation with authenticated state-changing actions,
 * scenario isolation, bounded audit pagination, and history-mode preservation.
 */

class GridMindDashboard {
  constructor() {
    this.pollInterval = 2000;
    this.timer = null;
    this.activeScenario = "SC01";
    this.activeIncidentId = null;
    this.selectedRecordId = null;
    this.mode = "live"; // "live" | "history"
    this.authToken = localStorage.getItem("gridmind_auth_token") || "gm-lead-token-secret";
    this.isPlanning = false;
    this.isSubmittingApproval = false;
    this.auditPageSize = 20;
    this.auditOffset = 0;

    this.dom = {
      // Header metrics & Auth
      freqVal: document.getElementById("metric-freq"),
      tempVal: document.getElementById("metric-temp"),
      demandVal: document.getElementById("metric-demand"),
      revisionVal: document.getElementById("metric-revision"),
      gridStatusBadge: document.getElementById("grid-status-badge"),
      scenarioButtons: document.querySelectorAll(".scenario-btn"),
      btnAnalyze: document.getElementById("btn-analyze-incident"),
      
      // Stage tracker
      stageSteps: document.querySelectorAll(".stage-step"),

      // History mode banner
      historyBanner: document.getElementById("history-mode-banner"),

      // Panels
      incidentTitle: document.getElementById("incident-title"),
      incidentScenario: document.getElementById("incident-scenario"),
      incidentStatus: document.getElementById("incident-status"),
      incidentViolations: document.getElementById("incident-violations"),

      // Topology & Thermals
      transformerGauges: document.getElementById("transformer-gauges"),
      topoLines: document.querySelectorAll(".topo-line"),
      topoNodes: document.querySelectorAll(".topo-node"),

      // Approval Gate
      approvalContainer: document.getElementById("approval-gate-container"),

      // Sandbox Matrix & Specialists
      matrixBody: document.getElementById("sandbox-matrix-body"),
      specialistsContainer: document.getElementById("specialists-container"),
      recommendationBox: document.getElementById("recommendation-details"),

      // Activity Feed & Audit Trail
      activityFeed: document.getElementById("activity-feed-list"),
      auditHistoryList: document.getElementById("audit-history-list"),
      postVerificationBox: document.getElementById("post-verification-box"),
      btnRefreshHistory: document.getElementById("btn-refresh-history"),
    };

    this.init();
  }

  async init() {
    this.bindEvents();
    await this.refreshState();
    await this.fetchAuditHistory();
    this.startPolling();
  }

  bindEvents() {
    // Scenario buttons
    this.dom.scenarioButtons.forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const targetSc = e.target.getAttribute("data-scenario");
        if (targetSc) {
          await this.loadScenario(targetSc);
        }
      });
    });

    // Analyze Incident trigger
    if (this.dom.btnAnalyze) {
      this.dom.btnAnalyze.addEventListener("click", async () => {
        await this.triggerCommanderPlan();
      });
    }

    // Refresh history button
    if (this.dom.btnRefreshHistory) {
      this.dom.btnRefreshHistory.addEventListener("click", async () => {
        await this.fetchAuditHistory();
      });
    }
  }

  getAuthHeaders() {
    return {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${this.authToken}`,
    };
  }

  startPolling() {
    if (this.timer) clearInterval(this.timer);
    this.timer = setInterval(() => this.refreshState(), this.pollInterval);
  }

  stopPolling() {
    if (this.timer) clearInterval(this.timer);
  }

  async loadScenario(scenarioId) {
    try {
      this.dom.btnAnalyze.disabled = true;
      const res = await fetch("/api/scenario/load", {
        method: "POST",
        headers: this.getAuthHeaders(),
        body: JSON.stringify({ scenario_id: scenarioId }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to load scenario");
      }
      this.activeScenario = scenarioId;
      this.mode = "live";
      this.selectedRecordId = null;
      this.hideHistoryBanner();
      await this.refreshState();
      await this.fetchAuditHistory();
    } catch (err) {
      console.error("Scenario load error:", err);
      alert("Error loading scenario: " + err.message);
    } finally {
      this.dom.btnAnalyze.disabled = false;
    }
  }

  async triggerCommanderPlan() {
    if (this.isPlanning) return;
    try {
      this.isPlanning = true;
      this.dom.btnAnalyze.disabled = true;
      this.dom.btnAnalyze.innerHTML = '<span class="spinner"></span> Investigating...';

      const res = await fetch("/api/commander/plan", {
        method: "POST",
        headers: this.getAuthHeaders(),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to plan incident response");
      }
      const data = await res.json();
      this.activeIncidentId = data.incident_id;
      this.mode = "live";
      this.selectedRecordId = null;
      this.hideHistoryBanner();
      await this.refreshState();
      await this.fetchAuditHistory();
    } catch (err) {
      console.error("Commander plan error:", err);
      alert("Commander Planning Error: " + err.message);
    } finally {
      this.isPlanning = false;
      this.dom.btnAnalyze.disabled = false;
      this.dom.btnAnalyze.innerHTML = "⚡ Analyze Incident";
    }
  }

  async submitApproval(approved, reason, incidentId) {
    if (this.isSubmittingApproval) return;
    try {
      this.isSubmittingApproval = true;
      const endpoint = approved ? "/api/commander/approve" : "/api/commander/reject";
      const payload = {
        reason: reason || (approved ? "Authorized via Control Room UI" : "Rejected by Operator"),
        incident_id: incidentId || this.activeIncidentId,
      };

      const res = await fetch(endpoint, {
        method: "POST",
        headers: this.getAuthHeaders(),
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Approval request failed");
      }

      await this.refreshState();
      await this.fetchAuditHistory();
    } catch (err) {
      console.error("Approval error:", err);
      alert("Approval Error: " + err.message);
    } finally {
      this.isSubmittingApproval = false;
    }
  }

  async refreshState() {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) return;
      const data = await res.json();

      // Always update live top telemetry gauges
      this.renderHeader(data);
      this.renderTopology(data);
      this.renderTransformers(data);

      // In history mode, do NOT overwrite historical audit record view
      if (this.mode === "history" && this.selectedRecordId) {
        return;
      }

      // Live mode rendering
      this.renderIncidentState(data);
      const activeRecord = data.latest_record;
      if (activeRecord) {
        this.activeIncidentId = activeRecord.incident_id;
        this.renderLifecycleStages(activeRecord);
        this.renderApprovalGate(activeRecord);
        this.renderSandboxMatrix(activeRecord);
        this.renderSpecialists(activeRecord);
        this.renderRecommendation(activeRecord);
        this.renderPostVerification(activeRecord);
        await this.renderActivityEvents(activeRecord.incident_id);
      } else {
        this.renderIdleState();
      }
    } catch (err) {
      console.debug("Telemetry polling error:", err);
    }
  }

  renderHeader(data) {
    const grid = data.grid_state || {};
    const inc = data.incident_state || {};
    this.activeScenario = data.scenario_id;

    // Active scenario button state
    this.dom.scenarioButtons.forEach((btn) => {
      const sc = btn.getAttribute("data-scenario");
      btn.classList.toggle("active", sc === this.activeScenario);
    });

    if (this.dom.freqVal) this.dom.freqVal.textContent = (grid.frequency_hz || 60.0).toFixed(2) + " Hz";
    if (this.dom.tempVal) this.dom.tempVal.textContent = (grid.ambient_temp_c || 25.0).toFixed(1) + "°C";
    if (this.dom.demandVal) this.dom.demandVal.textContent = (grid.demand_multiplier || 1.0).toFixed(2) + "x";
    if (this.dom.revisionVal) this.dom.revisionVal.textContent = data.state_revision || "00000000";

    const isStable = inc.is_stable ?? true;
    const violsCount = (inc.active_violations || []).length;

    if (this.dom.gridStatusBadge) {
      if (!isStable || violsCount > 0) {
        this.dom.gridStatusBadge.textContent = "INCIDENT ACTIVE";
        this.dom.gridStatusBadge.className = "metric-val critical";
      } else {
        this.dom.gridStatusBadge.textContent = "GRID NOMINAL";
        this.dom.gridStatusBadge.className = "metric-val nominal";
      }
    }
  }

  renderIncidentState(data) {
    const inc = data.incident_state || {};
    const viols = inc.active_violations || [];
    const latest = data.latest_record;

    if (this.dom.incidentScenario) this.dom.incidentScenario.textContent = data.scenario_id;
    if (this.dom.incidentTitle) {
      this.dom.incidentTitle.textContent = latest ? latest.incident_id : "NO ACTIVE INCIDENT";
    }
    if (this.dom.incidentStatus) {
      this.dom.incidentStatus.textContent = latest ? latest.status : "STANDBY";
    }

    if (this.dom.incidentViolations) {
      if (viols.length === 0) {
        this.dom.incidentViolations.innerHTML = '<span class="text-muted">Zero active physical violations.</span>';
      } else {
        this.dom.incidentViolations.innerHTML = viols
          .map((v) => `<div class="viol-item">⚠️ ${this.escapeHtml(v.description)}</div>`)
          .join("");
      }
    }
  }

  renderTopology(data) {
    const inc = data.incident_state || {};
    const grid = data.grid_state || {};
    const trippedLines = new Set(inc.tripped_lines || []);
    const overheatedXfmrs = new Set(inc.overheated_transformers || []);

    const lineL08 = document.getElementById("svg-line-L08");
    if (lineL08) {
      if (trippedLines.has("L08")) {
        lineL08.setAttribute("class", "topo-line tripped");
      } else {
        const l08Obj = (grid.lines || []).find((l) => l.line_id === "L08");
        if (l08Obj && l08Obj.status === "closed") {
          lineL08.setAttribute("class", "topo-line tie-line-closed");
        } else {
          lineL08.setAttribute("class", "topo-line tie-line-open");
        }
      }
    }

    const nodeN08 = document.getElementById("svg-node-N08");
    if (nodeN08) {
      if (overheatedXfmrs.has("T04")) {
        nodeN08.setAttribute("class", "topo-node commercial tripped");
      } else {
        nodeN08.setAttribute("class", "topo-node commercial");
      }
    }
  }

  renderTransformers(data) {
    const grid = data.grid_state || {};
    const xfmrs = grid.transformers || [];
    if (!this.dom.transformerGauges) return;

    this.dom.transformerGauges.innerHTML = xfmrs
      .map((t) => {
        const temp = t.temperature_c || 0;
        const load = t.load_pct || 0;
        let tempClass = "temp-normal";
        if (temp >= 110.0) tempClass = "temp-crit";
        else if (temp >= 95.0) tempClass = "temp-warn";

        const isOverheated = temp >= 110.0;
        return `
          <div class="xfmr-card ${isOverheated ? "overheated" : ""}">
            <div class="xfmr-header">
              <span>${t.transformer_id} (${t.node_id})</span>
              <span class="badge-tag ${isOverheated ? "badge-reject" : "badge-accept"}">${t.status}</span>
            </div>
            <div class="xfmr-temp ${tempClass}">${temp.toFixed(1)}°C</div>
            <div class="xfmr-meta">
              <span>Load: ${load.toFixed(1)}%</span>
              <span>Rating: ${t.rating_kva}kVA</span>
            </div>
          </div>
        `;
      })
      .join("");
  }

  renderLifecycleStages(record) {
    const status = record.status;
    const hasOps = !!(record.specialist_results && record.specialist_results.operations);
    const hasSafety = !!(record.specialist_results && record.specialist_results.safety);
    const hasPlanning = !!(record.specialist_results && record.specialist_results.planning);
    const hasRec = !!record.recommended_action || status === "NO_SAFE_ACTION" || status === "NOMINAL";
    const isApproved = record.approval && record.approval.approved === true;
    const isRejected = status === "REJECTED_BY_HUMAN" || (record.approval && record.approval.approved === false);
    const isExecuted = record.execution && record.execution.executed === true;
    const isVerified = status === "VERIFIED";

    const stageStatusMap = {
      1: "completed", // Incident Detected
      2: hasOps ? "completed" : "not-reached", // Ops Reasoning
      3: hasOps && record.specialist_results.operations.candidates && record.specialist_results.operations.candidates.length > 0 ? "completed" : "not-reached",
      4: hasSafety ? "completed" : "not-reached", // Sandbox Eval
      5: hasSafety ? "completed" : "not-reached", // Safety Checks
      6: hasPlanning ? "completed" : "not-reached", // Planning Advice
      7: hasRec ? "completed" : "not-reached", // Recommendation
      8: status === "PENDING_APPROVAL" ? "paused" : isApproved ? "completed" : isRejected ? "rejected" : "not-reached",
      9: isVerified ? "completed" : isExecuted ? "active" : "not-reached",
    };

    this.dom.stageSteps.forEach((step) => {
      const stageNum = parseInt(step.getAttribute("data-stage"), 10);
      const stateClass = stageStatusMap[stageNum] || "not-reached";
      step.className = `stage-step ${stateClass}`;
    });
  }

  renderApprovalGate(record) {
    if (!this.dom.approvalContainer) return;
    const status = record.status;

    if (status !== "PENDING_APPROVAL" || this.mode === "history") {
      this.dom.approvalContainer.innerHTML = "";
      this.dom.approvalContainer.style.display = "none";
      return;
    }

    this.dom.approvalContainer.style.display = "block";
    const rec = record.recommended_action || {};
    const actType = rec.action_type || "N/A";
    const cid = rec.candidate_id || "C00";
    const paramsJson = JSON.stringify(rec.parameters || {}, null, 2);

    this.dom.approvalContainer.innerHTML = `
      <div class="approval-banner">
        <div class="approval-header-row">
          <div class="approval-badge">⚠️ HUMAN APPROVAL REQUIRED (PENDING_APPROVAL)</div>
          <span style="font-size: 11px; color: var(--text-muted);">Execution paused. Simulator state is untouched.</span>
        </div>
        <div class="approval-details-box">
          <div class="action-line">Recommended Action: ${this.escapeHtml(actType)} [${this.escapeHtml(cid)}]</div>
          <div class="params-text">Parameters: <code>${this.escapeHtml(paramsJson)}</code></div>
        </div>
        <div class="approval-actions-row">
          <input type="text" id="operator-reason-input" placeholder="Approval Reason / Authorization Note" value="Standard incident protocol verified" style="background: rgba(0,0,0,0.4); border: 1px solid var(--border-subtle); color: #fff; padding: 6px 10px; border-radius: 4px; font-size: 11px; flex: 1;">
          <button id="btn-reject-action" class="btn-action btn-reject">✖ Reject Action</button>
          <button id="btn-approve-action" class="btn-action btn-approve">✔ Authorize & Execute</button>
        </div>
      </div>
    `;

    document.getElementById("btn-approve-action").addEventListener("click", () => {
      const opReason = document.getElementById("operator-reason-input").value;
      this.submitApproval(true, opReason, record.incident_id);
    });

    document.getElementById("btn-reject-action").addEventListener("click", () => {
      const opReason = document.getElementById("operator-reason-input").value;
      this.submitApproval(false, opReason, record.incident_id);
    });
  }

  renderSandboxMatrix(record) {
    if (!this.dom.matrixBody) return;
    const safety = (record.specialist_results && record.specialist_results.safety) || {};
    const evidenceList = safety.evidence || [];
    const recommended = record.recommended_action || {};

    if (evidenceList.length === 0) {
      this.dom.matrixBody.innerHTML = `
        <tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 16px;">
          No sandbox candidate actions evaluated yet. Click "Analyze Incident" to start.
        </td></tr>
      `;
      return;
    }

    this.dom.matrixBody.innerHTML = evidenceList
      .map((ev) => {
        const action = ev.action || {};
        const cid = action.candidate_id || "C";
        const atype = action.action_type || "N/A";
        const isRec = recommended.candidate_id === cid;
        const valid = ev.action_valid;
        const stable = ev.is_stable;
        const violsCount = (ev.violations || []).length;
        const t04Temp = ev.predicted_temp_t04 ? `${ev.predicted_temp_t04.toFixed(1)}°C` : "N/A";
        const isSafe = valid && stable && violsCount === 0;

        return `
          <tr class="${isRec ? "selected-row" : ""}">
            <td>
              <strong>${this.escapeHtml(cid)}</strong>
              ${isRec ? '<span class="badge-tag badge-rec">RECOMMENDED</span>' : ""}
            </td>
            <td><code>${this.escapeHtml(atype)}</code></td>
            <td><pre style="margin: 0; font-size: 10px;">${this.escapeHtml(JSON.stringify(action.parameters || {}))}</pre></td>
            <td style="font-family: var(--font-mono);">${t04Temp}</td>
            <td>${stable ? '<span style="color: var(--color-green);">STABLE</span>' : '<span style="color: var(--color-rose);">UNSTABLE</span>'}</td>
            <td>${violsCount === 0 ? '<span style="color: var(--color-green);">0</span>' : `<span style="color: var(--color-rose);">${violsCount}</span>`}</td>
            <td>
              <span class="badge-tag ${isSafe ? "badge-accept" : "badge-reject"}">
                ${isSafe ? "ACCEPT" : "REJECT"}
              </span>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  renderSpecialists(record) {
    if (!this.dom.specialistsContainer) return;
    const s = record.specialist_results || {};
    const roles = ["operations", "safety", "planning"];

    this.dom.specialistsContainer.innerHTML = roles
      .map((role) => {
        const data = s[role] || {};
        const status = data.status || "STANDBY";
        const finding = data.finding || "No findings recorded.";
        const rec = data.recommendation || "Pending investigation.";
        const isAccept = status === "ACCEPT";

        return `
          <div class="specialist-card">
            <div class="spec-header">
              <span class="spec-name">${role} Specialist</span>
              <span class="badge-tag ${isAccept ? "badge-accept" : "badge-reject"}">${status}</span>
            </div>
            <div class="spec-finding">${this.escapeHtml(finding)}</div>
            <div class="spec-rec"><strong>Recommendation:</strong> ${this.escapeHtml(rec)}</div>
          </div>
        `;
      })
      .join("");
  }

  renderRecommendation(record) {
    if (!this.dom.recommendationBox) return;
    const rec = record.recommended_action;
    const status = record.status;

    if (!rec) {
      this.dom.recommendationBox.innerHTML = `
        <div style="color: var(--text-muted); font-size: 11px;">
          No action recommended. Incident Status: <strong>${this.escapeHtml(status)}</strong>
        </div>
      `;
      return;
    }

    this.dom.recommendationBox.innerHTML = `
      <div style="display: flex; flex-direction: column; gap: 4px;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="badge-tag badge-rec">${this.escapeHtml(rec.candidate_id || "C00")}</span>
          <strong style="font-size: 13px; color: var(--text-highlight);">${this.escapeHtml(rec.action_type)}</strong>
        </div>
        <pre style="background: rgba(0,0,0,0.3); padding: 6px 10px; border-radius: 4px; font-size: 11px; margin: 4px 0;">${this.escapeHtml(JSON.stringify(rec.parameters || {}, null, 2))}</pre>
        <div style="font-size: 10px; color: var(--text-muted);">
          Selected via deterministic tie-breaking: disruption priority rank 1 & maximum thermal relief.
        </div>
      </div>
    `;
  }

  renderPostVerification(record) {
    if (!this.dom.postVerificationBox) return;
    const exec = record.execution || {};
    const verif = record.verification || {};
    const isExecuted = exec.executed === true;

    if (!isExecuted) {
      this.dom.postVerificationBox.innerHTML = `
        <span style="color: var(--text-muted); font-size: 11px;">
          Awaiting human approval. Live action not executed yet.
        </span>
      `;
      return;
    }

    const isVerified = verif.verified === true;
    this.dom.postVerificationBox.innerHTML = `
      <div style="display: flex; flex-direction: column; gap: 6px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 700; font-size: 12px; color: #fff;">Execution Result</span>
          <span class="badge-tag ${isVerified ? "badge-accept" : "badge-reject"}">
            ${isVerified ? "VERIFIED (Stable)" : "EXECUTED UNVERIFIED"}
          </span>
        </div>
        <div style="font-size: 11px; color: var(--text-secondary);">
          Live grid physically checked: stable = <strong>${verif.post_state_stable}</strong>, active violations = <strong>${(verif.active_violations || []).length}</strong>.
        </div>
      </div>
    `;
  }

  async renderActivityEvents(incidentId) {
    if (!this.dom.activityFeed || !incidentId) return;
    try {
      const res = await fetch(`/api/events/${encodeURIComponent(incidentId)}`);
      if (!res.ok) return;
      const data = await res.json();
      const events = data.events || [];

      if (events.length === 0) {
        this.dom.activityFeed.innerHTML = '<div style="color: var(--text-muted); padding: 12px;">No events recorded for this incident.</div>';
        return;
      }

      this.dom.activityFeed.innerHTML = events
        .map((ev) => {
          let cardTypeClass = "event-inspection";
          let tagClass = "inspect";
          let label = ev.event_type;

          if (ev.event_type === "state_inspection") {
            cardTypeClass = "event-inspection";
            tagClass = "inspect";
            label = "STATE INSPECTION";
          } else if (ev.event_type === "sandbox_evaluation") {
            cardTypeClass = "event-sandbox";
            tagClass = "sandbox";
            label = "SANDBOX EVAL";
          } else if (ev.event_type === "reasoning_summary") {
            cardTypeClass = "event-reasoning";
            tagClass = "reason";
            label = "SPECIALIST REASONING";
          } else if (ev.event_type === "recommendation") {
            cardTypeClass = "event-recommendation";
            tagClass = "rec";
            label = "RECOMMENDATION";
          } else if (ev.event_type === "approval_checkpoint") {
            cardTypeClass = "event-approval";
            tagClass = "gate";
            label = "APPROVAL CHECKPOINT";
          } else if (ev.event_type === "execution_dispatch") {
            cardTypeClass = "event-execution";
            tagClass = "exec";
            label = "EXECUTION DISPATCH";
          } else if (ev.event_type === "verification_result") {
            cardTypeClass = "event-verification";
            tagClass = "verdict";
            label = "VERIFICATION RESULT";
          }

          const stageDisplay = ev.stage ? `<span style="color: var(--text-muted); font-size: 9px; margin-left: 6px;">[${ev.stage.toUpperCase()}]</span>` : "";

          return `
            <div class="activity-event-card ${cardTypeClass}">
              <div class="activity-meta-row">
                <div>
                  <span class="activity-tag ${tagClass}">${label}</span>
                  ${stageDisplay}
                </div>
                <span>${this.formatTime(ev.timestamp)}</span>
              </div>
              <div class="activity-summary">${this.escapeHtml(ev.summary)}</div>
            </div>
          `;
        })
        .join("");
    } catch (err) {
      console.debug("Activity fetch error:", err);
    }
  }

  async fetchAuditHistory() {
    if (!this.dom.auditHistoryList) return;
    try {
      const res = await fetch(`/api/audit/records?limit=${this.auditPageSize}&offset=${this.auditOffset}`);
      if (!res.ok) return;
      const data = await res.json();
      const records = data.records || [];

      if (records.length === 0) {
        this.dom.auditHistoryList.innerHTML = '<div style="color: var(--text-muted); padding: 8px;">No audit records in SQLite store.</div>';
        return;
      }

      this.dom.auditHistoryList.innerHTML = records
        .map((r) => {
          const isSelected = r.incident_id === (this.selectedRecordId || this.activeIncidentId);
          let statusBadgeClass = "badge-rec";
          if (r.status === "VERIFIED") statusBadgeClass = "badge-accept";
          else if (r.status === "REJECTED_BY_HUMAN" || r.status === "NO_SAFE_ACTION") statusBadgeClass = "badge-reject";

          return `
            <div class="audit-history-item ${isSelected ? "active-item" : ""}" data-id="${r.incident_id}">
              <div>
                <strong>${this.escapeHtml(r.incident_id)}</strong>
                <span style="color: var(--text-muted); font-size: 10px; margin-left: 6px;">${r.scenario_id}</span>
              </div>
              <div style="display: flex; align-items: center; gap: 6px;">
                <span class="badge-tag ${statusBadgeClass}">${r.status}</span>
                <span style="font-size: 9px; color: var(--text-muted);">${this.formatTime(r.updated_at)}</span>
              </div>
            </div>
          `;
        })
        .join("");

      // Bind click on historical records to enter history mode
      this.dom.auditHistoryList.querySelectorAll(".audit-history-item").forEach((item) => {
        item.addEventListener("click", async () => {
          const targetId = item.getAttribute("data-id");
          if (targetId) {
            await this.selectHistoricalRecord(targetId);
          }
        });
      });
    } catch (err) {
      console.debug("Audit history fetch error:", err);
    }
  }

  async selectHistoricalRecord(incidentId) {
    try {
      const res = await fetch(`/api/audit/records/${encodeURIComponent(incidentId)}`);
      if (!res.ok) return;
      const rec = await res.json();

      this.mode = "history";
      this.selectedRecordId = incidentId;
      this.showHistoryBanner(rec);

      // Highlight in history drawer
      this.dom.auditHistoryList.querySelectorAll(".audit-history-item").forEach((item) => {
        item.classList.toggle("active-item", item.getAttribute("data-id") === incidentId);
      });

      // Render historical record details
      if (this.dom.incidentScenario) this.dom.incidentScenario.textContent = rec.scenario_id;
      if (this.dom.incidentTitle) this.dom.incidentTitle.textContent = rec.incident_id;
      if (this.dom.incidentStatus) this.dom.incidentStatus.textContent = rec.status;

      this.renderLifecycleStages(rec);
      this.renderApprovalGate(rec);
      this.renderSandboxMatrix(rec);
      this.renderSpecialists(rec);
      this.renderRecommendation(rec);
      this.renderPostVerification(rec);
      await this.renderActivityEvents(rec.incident_id);
    } catch (err) {
      console.error("Failed to load historical record:", err);
    }
  }

  showHistoryBanner(record) {
    if (!this.dom.historyBanner) return;
    this.dom.historyBanner.style.display = "flex";
    this.dom.historyBanner.innerHTML = `
      <div style="display: flex; align-items: center; gap: 10px;">
        <span class="badge-tag badge-rec" style="background: rgba(56, 189, 248, 0.25);">HISTORICAL INSPECTION MODE</span>
        <span style="font-size: 11px; color: #f8fafc;">
          Viewing persistent record <strong>${this.escapeHtml(record.incident_id)}</strong> (Scenario: <strong>${record.scenario_id}</strong> | Status: <strong>${record.status}</strong>). Live polling will not overwrite this view.
        </span>
      </div>
      <button id="btn-return-live" class="btn-action" style="padding: 4px 12px; font-size: 10px; background: #0284c7;">
        ↩ Return to Live Control
      </button>
    `;

    document.getElementById("btn-return-live").addEventListener("click", async () => {
      this.mode = "live";
      this.selectedRecordId = null;
      this.hideHistoryBanner();
      await this.refreshState();
    });
  }

  hideHistoryBanner() {
    if (!this.dom.historyBanner) return;
    this.dom.historyBanner.style.display = "none";
    this.dom.historyBanner.innerHTML = "";
  }

  renderIdleState() {
    if (this.dom.matrixBody) {
      this.dom.matrixBody.innerHTML = `
        <tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 16px;">
          Grid in nominal standby. Select a scenario and click "Analyze Incident".
        </td></tr>
      `;
    }
  }

  formatTime(isoString) {
    if (!isoString) return "";
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return isoString;
    }
  }

  escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
}

// Initialize on DOM ready
document.addEventListener("DOMContentLoaded", () => {
  window.gridMindDashboard = new GridMindDashboard();
});
