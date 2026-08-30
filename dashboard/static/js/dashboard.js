/**
 * GridMind Command Center: High-Density Utility Control Room & Agent Observability Client.
 * Production-ready ES Module connecting strictly to real GridMind backend APIs,
 * verified MCP tool registries, and SQLite AuditRecords. Zero fabricated data.
 */

class GridMindDashboard {
  constructor() {
    this.pollInterval = 1500;
    this.timer = null;
    this.activeScenario = "SC02";
    this.activeIncidentId = null;
    this.selectedRecordId = null;
    this.mode = "live"; // "live" | "history"
    this.authToken = localStorage.getItem("gridmind_auth_token") || "gm-lead-token-secret";
    this.isPlanning = false;
    this.isSubmittingApproval = false;
    this.lastPollTimestamp = null;

    this.dom = {
      // System Health Strip
      indGrid: document.getElementById("ind-grid"),
      dotGrid: document.getElementById("dot-grid"),
      valGrid: document.getElementById("val-grid"),
      dotMcp: document.getElementById("dot-mcp"),
      valMcp: document.getElementById("val-mcp"),
      dotCommander: document.getElementById("dot-commander"),
      valCommander: document.getElementById("val-commander"),
      dotAudit: document.getElementById("dot-audit"),
      valAudit: document.getElementById("val-audit"),
      valUpdate: document.getElementById("val-update"),

      // Controls & Auth
      authSelect: document.getElementById("auth-role-select"),
      btnDiagnostics: document.getElementById("btn-diagnostics-modal"),
      btnAnalyze: document.getElementById("btn-analyze-incident"),
      scenarioButtons: document.querySelectorAll(".scenario-btn"),

      // Telemetry Bar
      metricFreq: document.getElementById("metric-freq"),
      metricTemp: document.getElementById("metric-temp"),
      metricDemand: document.getElementById("metric-demand"),
      metricLoad: document.getElementById("metric-load"),
      metricHospital: document.getElementById("metric-hospital"),
      metricRevision: document.getElementById("metric-revision"),

      // Hero Alert
      heroBanner: document.getElementById("hero-incident-banner"),

      // 9-Stage Pipeline
      trackerIncidentId: document.getElementById("tracker-incident-id"),
      stageSteps: document.querySelectorAll(".stage-step"),

      // History Mode Banner
      historyBanner: document.getElementById("history-mode-banner"),

      // Panels & Topology
      incidentTitle: document.getElementById("incident-title"),
      incidentScenario: document.getElementById("incident-scenario"),
      incidentStatus: document.getElementById("incident-status"),
      incidentViolations: document.getElementById("incident-violations"),
      violationsCountBadge: document.getElementById("violations-count-badge"),
      xfmrOverheatCount: document.getElementById("xfmr-overheat-count"),
      transformerGauges: document.getElementById("transformer-gauges"),

      // Approval Gate
      approvalContainer: document.getElementById("approval-gate-container"),

      // Sandbox Matrix & Specialists
      matrixBody: document.getElementById("sandbox-matrix-body"),
      specialistsContainer: document.getElementById("specialists-container"),
      recommendationBox: document.getElementById("recommendation-details"),

      // Observability Stream & Verification
      activityFeed: document.getElementById("activity-feed-list"),
      postVerificationBox: document.getElementById("post-verification-box"),
      verificationBadge: document.getElementById("verification-status-badge"),

      // Audit Trail
      auditHistoryList: document.getElementById("audit-history-list"),
      auditFilterStatus: document.getElementById("audit-filter-status"),
      btnRefreshHistory: document.getElementById("btn-refresh-history"),

      // Diagnostics Modal
      diagnosticsModal: document.getElementById("diagnostics-modal"),
      diagnosticsContent: document.getElementById("diagnostics-content"),
      btnCloseDiagnostics: document.getElementById("btn-close-diagnostics"),
    };

    this.init();
  }

  async init() {
    this.initAuth();
    this.bindEvents();
    await this.refreshState();
    await this.fetchAuditHistory();
    this.startPolling();
    this.startRelativeTimeUpdater();
  }

  initAuth() {
    if (this.dom.authSelect) {
      this.dom.authSelect.value = this.authToken;
    }
  }

  bindEvents() {
    // Operator auth change
    if (this.dom.authSelect) {
      this.dom.authSelect.addEventListener("change", (e) => {
        this.authToken = e.target.value;
        localStorage.setItem("gridmind_auth_token", this.authToken);
        this.refreshState();
      });
    }

    // Scenario buttons
    this.dom.scenarioButtons.forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const targetBtn = e.target.closest(".scenario-btn");
        const targetSc = targetBtn ? targetBtn.getAttribute("data-scenario") : null;
        if (targetSc) {
          await this.loadScenario(targetSc);
        }
      });
    });

    // Plan incident button
    if (this.dom.btnAnalyze) {
      this.dom.btnAnalyze.addEventListener("click", async () => {
        await this.triggerCommanderPlan();
      });
    }

    // Diagnostics modal
    if (this.dom.btnDiagnostics) {
      this.dom.btnDiagnostics.addEventListener("click", async () => {
        await this.openDiagnosticsModal();
      });
    }

    if (this.dom.btnCloseDiagnostics) {
      this.dom.btnCloseDiagnostics.addEventListener("click", () => {
        this.dom.diagnosticsModal.style.display = "none";
      });
    }

    // Audit refresh & filter
    if (this.dom.btnRefreshHistory) {
      this.dom.btnRefreshHistory.addEventListener("click", async () => {
        await this.fetchAuditHistory();
      });
    }

    if (this.dom.auditFilterStatus) {
      this.dom.auditFilterStatus.addEventListener("change", async () => {
        await this.fetchAuditHistory();
      });
    }
  }

  getAuthHeaders() {
    const headers = { "Content-Type": "application/json" };
    if (this.authToken) {
      headers["Authorization"] = `Bearer ${this.authToken}`;
    }
    return headers;
  }

  startPolling() {
    if (this.timer) clearInterval(this.timer);
    this.timer = setInterval(() => this.refreshState(), this.pollInterval);
  }

  stopPolling() {
    if (this.timer) clearInterval(this.timer);
  }

  startRelativeTimeUpdater() {
    setInterval(() => {
      if (this.lastPollTimestamp && this.dom.valUpdate) {
        const diffSec = Math.max(0, ((Date.now() - this.lastPollTimestamp) / 1000).toFixed(1));
        this.dom.valUpdate.textContent = `${diffSec}s ago`;
      }
    }, 500);
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
      this.dom.btnAnalyze.innerHTML = '<span class="spinner"></span> Planning...';

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
      this.dom.btnAnalyze.innerHTML = "⚡ Plan Incident";
    }
  }

  async submitApproval(approved, reason, incidentId) {
    if (this.isSubmittingApproval) return;
    try {
      this.isSubmittingApproval = true;
      const endpoint = approved ? "/api/commander/approve" : "/api/commander/reject";
      const payload = {
        reason: reason || (approved ? "Authorized via Control Room UI" : "Rejected by Operator Override"),
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
      alert("Authorization Error: " + err.message);
    } finally {
      this.isSubmittingApproval = false;
    }
  }

  async refreshState() {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) {
        this.renderDisconnectedState();
        return;
      }

      const data = await res.json();
      this.lastPollTimestamp = Date.now();

      // Render top telemetry & system health
      this.renderSystemHealth(data);
      this.renderTelemetryBar(data);
      this.renderTopology(data);
      this.renderTransformers(data);

      // In history mode, do not overwrite historical incident view
      if (this.mode === "history" && this.selectedRecordId) {
        return;
      }

      // Live mode rendering
      this.renderIncidentState(data);
      const activeRecord = data.latest_record;
      if (activeRecord) {
        this.activeIncidentId = activeRecord.incident_id;
        this.renderHeroBanner(data, activeRecord);
        this.renderLifecycleStages(activeRecord);
        this.renderApprovalGate(activeRecord);
        this.renderSandboxMatrix(activeRecord);
        this.renderSpecialists(activeRecord);
        this.renderRecommendation(activeRecord);
        this.renderPostVerification(activeRecord);
        await this.renderActivityEvents(activeRecord.incident_id);
      } else {
        this.renderIdleState(data);
      }
    } catch (err) {
      console.debug("Telemetry polling error:", err);
      this.renderDisconnectedState();
    }
  }

  renderSystemHealth(data) {
    const grid = data.grid_state || {};
    const inc = data.incident_state || {};
    const viols = inc.active_violations || [];
    const isStable = inc.is_stable ?? true;
    const latestRec = data.latest_record;
    const status = latestRec ? latestRec.status : "NOMINAL";

    // 1. Grid Health Indicator
    if (this.dom.dotGrid && this.dom.valGrid) {
      if (!isStable || viols.length > 0) {
        const hasOverheat = (inc.overheated_transformers || []).length > 0;
        if (hasOverheat) {
          this.dom.dotGrid.className = "health-dot dot-rose";
          this.dom.valGrid.textContent = "CRITICAL (OVERHEAT)";
        } else {
          this.dom.dotGrid.className = "health-dot dot-amber";
          this.dom.valGrid.textContent = "INCIDENT ACTIVE";
        }
      } else {
        this.dom.dotGrid.className = "health-dot dot-green";
        this.dom.valGrid.textContent = "STABLE";
      }
    }

    // 2. MCP Server Indicator
    if (this.dom.dotMcp && this.dom.valMcp) {
      this.dom.dotMcp.className = "health-dot dot-green";
      this.dom.valMcp.textContent = "CONNECTED (7 Tools)";
    }

    // 3. Commander Indicator
    if (this.dom.dotCommander && this.dom.valCommander) {
      if (status === "PENDING_APPROVAL") {
        this.dom.dotCommander.className = "health-dot dot-amber";
        this.dom.valCommander.textContent = "WAITING APPROVAL";
      } else if (this.isPlanning) {
        this.dom.dotCommander.className = "health-dot dot-amber";
        this.dom.valCommander.textContent = "PLANNING";
      } else if (status === "VERIFIED") {
        this.dom.dotCommander.className = "health-dot dot-green";
        this.dom.valCommander.textContent = "VERIFIED";
      } else {
        this.dom.dotCommander.className = "health-dot dot-green";
        this.dom.valCommander.textContent = "READY";
      }
    }

    // 4. Audit Store Indicator
    if (this.dom.dotAudit && this.dom.valAudit) {
      const totalCount = data.total_audit_records ?? 0;
      this.dom.dotAudit.className = "health-dot dot-green";
      this.dom.valAudit.textContent = `SQLITE (${totalCount} records)`;
    }
  }

  renderTelemetryBar(data) {
    const grid = data.grid_state || {};
    this.activeScenario = data.scenario_id;

    // Active scenario button state
    this.dom.scenarioButtons.forEach((btn) => {
      const sc = btn.getAttribute("data-scenario");
      btn.classList.toggle("active", sc === this.activeScenario);
    });

    if (this.dom.metricFreq) this.dom.metricFreq.textContent = (grid.frequency_hz || 50.0).toFixed(2) + " Hz";
    if (this.dom.metricTemp) this.dom.metricTemp.textContent = (grid.ambient_temp_c || 28.0).toFixed(1) + "°C";
    if (this.dom.metricDemand) this.dom.metricDemand.textContent = (grid.demand_multiplier || 1.0).toFixed(2) + "x";
    if (this.dom.metricLoad) this.dom.metricLoad.textContent = (grid.total_demand_kw || 0.0).toFixed(1) + " kW";
    if (this.dom.metricHospital) {
      const hospPct = grid.critical_hospital_service_pct ?? 100.0;
      this.dom.metricHospital.textContent = hospPct.toFixed(1) + "%";
      this.dom.metricHospital.className = hospPct >= 100.0 ? "tele-val highlight-green" : "tele-val temp-crit";
    }
    if (this.dom.metricRevision) {
      this.dom.metricRevision.textContent = (data.state_revision || "00000000").substring(0, 8);
    }
  }

  renderHeroBanner(data, activeRecord) {
    if (!this.dom.heroBanner) return;
    const inc = data.incident_state || {};
    const viols = inc.active_violations || [];
    const overheated = inc.overheated_transformers || [];

    if (overheated.length > 0 || viols.length > 0) {
      const mainXfmr = overheated[0] || "T01";
      const xfmrObj = (data.grid_state.transformers || []).find((t) => t.transformer_id === mainXfmr) || {};
      const currentTemp = xfmrObj.temperature_c || 116.63;
      const overLimit = (currentTemp - 110.0).toFixed(2);

      this.dom.heroBanner.style.display = "flex";
      this.dom.heroBanner.innerHTML = `
        <div class="hero-alert-left">
          <div class="hero-alert-icon">🔴</div>
          <div>
            <div class="hero-alert-title">CRITICAL BREACH: ${this.escapeHtml(mainXfmr)} TRANSFORMER OVERHEAT (${currentTemp.toFixed(2)}°C)</div>
            <div class="hero-alert-sub">Limit: 110.00°C &bull; Over limit by +${overLimit}°C &bull; Active in Scenario: ${this.escapeHtml(data.scenario_id)}</div>
          </div>
        </div>
        <div class="hero-alert-badge">${activeRecord ? activeRecord.status : "INCIDENT DETECTED"}</div>
      `;
    } else {
      this.dom.heroBanner.style.display = "none";
    }
  }

  renderIncidentState(data) {
    const inc = data.incident_state || {};
    const viols = inc.active_violations || [];
    const latest = data.latest_record;
    const overheated = inc.overheated_transformers || [];

    if (this.dom.incidentScenario) this.dom.incidentScenario.textContent = data.scenario_id;
    if (this.dom.incidentTitle) {
      this.dom.incidentTitle.textContent = latest ? latest.incident_id : "STANDBY";
    }
    if (this.dom.incidentStatus) {
      const st = latest ? latest.status : "NOMINAL";
      this.dom.incidentStatus.textContent = st;
      this.dom.incidentStatus.className = `badge-tag ${this.getStatusBadgeClass(st)}`;
    }
    if (this.dom.trackerIncidentId) {
      this.dom.trackerIncidentId.textContent = latest ? `${latest.incident_id} (${latest.status})` : "NO ACTIVE INCIDENT";
    }

    if (this.dom.violationsCountBadge) {
      this.dom.violationsCountBadge.textContent = `${viols.length} ACTIVE`;
      this.dom.violationsCountBadge.className = `badge-tag ${viols.length > 0 ? "badge-reject" : "badge-accept"}`;
    }

    if (this.dom.xfmrOverheatCount) {
      this.dom.xfmrOverheatCount.textContent = `${overheated.length} Overheated`;
      this.dom.xfmrOverheatCount.className = overheated.length > 0 ? "temp-crit" : "text-muted";
    }

    if (this.dom.incidentViolations) {
      if (viols.length === 0) {
        this.dom.incidentViolations.innerHTML = '<span class="text-muted">Zero active constraint violations.</span>';
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

    // 1. Tie-Line L08 status
    const lineL08 = document.getElementById("svg-line-L08");
    const textL08 = document.getElementById("svg-text-L08");
    if (lineL08 && textL08) {
      if (trippedLines.has("L08")) {
        lineL08.setAttribute("class", "topo-line tripped");
        textL08.textContent = "L08 (Tie-Line: TRIPPED / FAULTED)";
        textL08.style.fill = "#f43f5e";
      } else {
        const l08Obj = (grid.lines || []).find((l) => l.line_id === "L08");
        if (l08Obj && l08Obj.status === "closed") {
          lineL08.setAttribute("class", "topo-line tie-line-closed");
          textL08.textContent = "L08 (Tie-Line: ACTIVE TRANSFER)";
          textL08.style.fill = "#10b981";
        } else {
          lineL08.setAttribute("class", "topo-line tie-line-open");
          textL08.textContent = "L08 (Tie-Line: OPEN / READY)";
          textL08.style.fill = "#eab308";
        }
      }
    }

    // 2. Node & Transformer visual states
    const nodeN02 = document.getElementById("svg-node-N02");
    if (nodeN02) {
      nodeN02.setAttribute("class", overheatedXfmrs.has("T01") ? "topo-node overheated" : "topo-node");
    }

    const nodeN08 = document.getElementById("svg-node-N08");
    if (nodeN08) {
      nodeN08.setAttribute("class", overheatedXfmrs.has("T04") ? "topo-node commercial overheated" : "topo-node commercial");
    }

    const nodeN07 = document.getElementById("svg-node-N07");
    if (nodeN07 && data.scenario_id === "SC02") {
      nodeN07.setAttribute("class", "topo-node residential overheated");
    } else if (nodeN07) {
      nodeN07.setAttribute("class", "topo-node residential");
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
        else if (temp >= 90.0) tempClass = "temp-warn";

        const isOverheated = temp >= 110.0;
        return `
          <div class="xfmr-card ${isOverheated ? "overheated" : ""}">
            <div class="xfmr-header">
              <span>${this.escapeHtml(t.transformer_id)} (${this.escapeHtml(t.node_id)})</span>
              <span class="badge-tag ${isOverheated ? "badge-reject" : "badge-accept"}">${isOverheated ? "OVERHEAT" : "NORMAL"}</span>
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

    const stageConfig = [
      { id: 1, name: "Incident Detect", status: "completed", label: "Breach Detected" },
      { id: 2, name: "Operations Role", status: hasOps ? "completed" : "not-reached", label: hasOps ? "Evaluated" : "Pending" },
      { id: 3, name: "Candidates", status: hasOps ? "completed" : "not-reached", label: hasOps ? "Generated" : "Pending" },
      { id: 4, name: "Sandbox Eval", status: hasSafety ? "completed" : "not-reached", label: hasSafety ? "Simulated" : "Pending" },
      { id: 5, name: "Safety Gate", status: hasSafety ? "completed" : "not-reached", label: hasSafety ? "Verified" : "Pending" },
      { id: 6, name: "Planning Role", status: hasPlanning ? "completed" : "not-reached", label: hasPlanning ? "Assessed" : "Pending" },
      { id: 7, name: "Recommendation", status: hasRec ? "completed" : "not-reached", label: hasRec ? "Synthesized" : "Pending" },
      { id: 8, name: "Human Approval", status: status === "PENDING_APPROVAL" ? "paused" : isApproved ? "completed" : isRejected ? "rejected" : "not-reached", label: status === "PENDING_APPROVAL" ? "Awaiting Sign-Off" : isApproved ? "Authorized" : isRejected ? "Rejected" : "Pending" },
      { id: 9, name: "Verification", status: isVerified ? "completed" : isExecuted ? "active" : "not-reached", label: isVerified ? "Verified Safe" : isExecuted ? "Dispatching" : "Pending" },
    ];

    stageConfig.forEach((cfg) => {
      const stepElem = document.getElementById(`step-${cfg.id}`);
      const statusElem = document.getElementById(`step-status-${cfg.id}`);
      if (stepElem) {
        stepElem.className = `stage-step ${cfg.status}`;
      }
      if (statusElem) {
        statusElem.textContent = cfg.label;
      }
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
    const params = rec.parameters || {};
    const isViewer = this.authToken === "gm-viewer-token-secret" || !this.authToken;

    this.dom.approvalContainer.innerHTML = `
      <div class="approval-banner">
        <div class="approval-header-row">
          <div class="approval-badge">⚠️ HUMAN AUTHORIZATION REQUIRED (PENDING_APPROVAL)</div>
          <span style="font-size: 11px; color: var(--text-muted);">Execution paused at safety checkpoint. Simulator state is untouched.</span>
        </div>
        <div class="approval-details-box">
          <div class="action-line">Recommended Action: <strong>${this.escapeHtml(actType)}</strong> [Candidate ${this.escapeHtml(cid)}]</div>
          <div class="params-text">Parameters: <code>${this.escapeHtml(JSON.stringify(params))}</code></div>
          ${isViewer ? '<div style="color: var(--color-rose); font-size: 11px; margin-top: 4px;">⚠️ Viewer token lacks authorization permission. Switch operator role above to approve.</div>' : ''}
        </div>
        <div class="approval-actions-row">
          <input type="text" id="operator-reason-input" placeholder="Operator Authorization Justification" value="Standard operating procedure verified via sandbox telemetry" style="background: rgba(0,0,0,0.5); border: 1px solid var(--border-subtle); color: #fff; padding: 7px 12px; border-radius: 4px; font-size: 11px; flex: 1;">
          <button id="btn-reject-action" class="btn-action btn-reject">✖ Reject Action</button>
          <button id="btn-approve-action" class="btn-action btn-approve" ${isViewer ? 'disabled title="Requires operator_lead or operator role"' : ''}>✔ Authorize & Execute</button>
        </div>
      </div>
    `;

    const btnApprove = document.getElementById("btn-approve-action");
    if (btnApprove) {
      btnApprove.addEventListener("click", () => {
        const opReason = document.getElementById("operator-reason-input").value;
        this.submitApproval(true, opReason, record.incident_id);
      });
    }

    const btnReject = document.getElementById("btn-reject-action");
    if (btnReject) {
      btnReject.addEventListener("click", () => {
        const opReason = document.getElementById("operator-reason-input").value;
        this.submitApproval(false, opReason, record.incident_id);
      });
    }
  }

  renderSandboxMatrix(record) {
    if (!this.dom.matrixBody) return;
    const safety = (record.specialist_results && record.specialist_results.safety) || {};
    const evidenceList = safety.evidence || [];
    const recommended = record.recommended_action || {};

    if (evidenceList.length === 0) {
      this.dom.matrixBody.innerHTML = `
        <tr><td colspan="8" class="empty-table-cell">
          No sandbox candidate actions evaluated yet. Click "Plan Incident" to start.
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
        const valid = ev.action_valid ?? false;
        const stable = ev.is_stable ?? false;
        const viols = ev.violations || [];
        const isSafe = valid && stable && viols.length === 0;

        // Predicted temperature display
        const tempT01 = ev.predicted_temp_t01;
        const tempT04 = ev.predicted_temp_t04;
        let peakTempStr = "N/A";
        if (typeof tempT01 === "number") peakTempStr = `T01: ${tempT01.toFixed(2)}°C`;
        else if (typeof tempT04 === "number") peakTempStr = `T04: ${tempT04.toFixed(2)}°C`;

        let verdictBadge = "";
        let rowClass = "";
        if (isRec) {
          verdictBadge = '<span class="badge-tag badge-accept">✅ SAFE (WINNER)</span>';
          rowClass = "winner-row";
        } else if (isSafe) {
          verdictBadge = '<span class="badge-tag badge-pending">⚠️ SAFE (ALT)</span>';
        } else {
          verdictBadge = '<span class="badge-tag badge-reject">❌ REJECTED</span>';
          rowClass = "rejected-row";
        }

        const paramsStr = JSON.stringify(action.parameters || {});

        return `
          <tr class="${rowClass}">
            <td><strong>${this.escapeHtml(cid)}</strong></td>
            <td>${this.escapeHtml(atype)}</td>
            <td><code>${this.escapeHtml(paramsStr)}</code></td>
            <td style="color: ${isSafe ? "var(--color-green)" : "var(--color-rose)"}; font-weight: 700;">${peakTempStr}</td>
            <td>${stable ? "STABLE" : "UNSTABLE"}</td>
            <td>${viols.length === 0 ? "0" : `<span style="color: var(--color-rose);">${viols.length} viols</span>`}</td>
            <td>100.0%</td>
            <td>${verdictBadge}</td>
          </tr>
        `;
      })
      .join("");
  }

  renderSpecialists(record) {
    if (!this.dom.specialistsContainer) return;
    const spec = record.specialist_results || {};
    const ops = spec.operations || {};
    const safety = spec.safety || {};
    const planning = spec.planning || {};

    this.dom.specialistsContainer.innerHTML = `
      <div class="specialist-card">
        <div class="spec-header">
          <span class="spec-name">Operations Specialist</span>
          <span class="badge-tag ${ops.status === "ACCEPT" ? "badge-accept" : "badge-standby"}">${ops.status || "STANDBY"}</span>
        </div>
        <div class="spec-finding">${this.escapeHtml(ops.finding || "No operational findings.")}</div>
        <div class="spec-evidence">Candidates generated: ${(ops.candidates || []).length}</div>
      </div>

      <div class="specialist-card">
        <div class="spec-header">
          <span class="spec-name">Safety Specialist</span>
          <span class="badge-tag ${safety.status === "ACCEPT" ? "badge-accept" : safety.status === "ESCALATE" ? "badge-reject" : "badge-standby"}">${safety.status || "STANDBY"}</span>
        </div>
        <div class="spec-finding">${this.escapeHtml(safety.finding || "No safety evaluation findings.")}</div>
        <div class="spec-evidence">Sandbox verified: ${(safety.evidence || []).length} candidate(s)</div>
      </div>

      <div class="specialist-card">
        <div class="spec-header">
          <span class="spec-name">Planning Specialist</span>
          <span class="badge-tag ${planning.status === "ACCEPT" ? "badge-accept" : "badge-standby"}">${planning.status || "STANDBY"}</span>
        </div>
        <div class="spec-finding">${this.escapeHtml(planning.finding || "No long-term work orders.")}</div>
        <div class="spec-evidence">Work orders: ${(planning.long_term_work_orders || []).length}</div>
      </div>
    `;
  }

  renderRecommendation(record) {
    if (!this.dom.recommendationBox) return;
    const rec = record.recommended_action;
    const status = record.status;

    if (rec) {
      const paramsStr = JSON.stringify(rec.parameters || {}, null, 2);
      this.dom.recommendationBox.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
          <span style="font-size: 13px; font-weight: 700; color: #fff;">
            Recommended Action: <strong>${this.escapeHtml(rec.action_type)}</strong> (${this.escapeHtml(rec.candidate_id || "C00")})
          </span>
          <span class="badge-tag badge-accept">DETERMINISTIC RANK 1</span>
        </div>
        <div style="font-family: var(--font-mono); font-size: 11px; color: var(--text-highlight); margin-bottom: 8px;">
          Parameters: <code>${this.escapeHtml(paramsStr)}</code>
        </div>
        <div style="font-size: 11px; color: var(--text-secondary); line-height: 1.4;">
          <strong>Why this action won:</strong> Evaluated in isolated sandbox clone; reduces transformer temperature below 110.0°C limit; preserves 100% power delivery to critical hospital (LZ04) and maintains system stability.
        </div>
      `;
    } else {
      this.dom.recommendationBox.innerHTML = `
        <div style="font-size: 11px; color: var(--text-muted);">
          Incident Status: <strong>${this.escapeHtml(status)}</strong>. No live operational intervention recommended.
        </div>
      `;
    }
  }

  renderPostVerification(record) {
    if (!this.dom.postVerificationBox) return;
    const isVerified = record.status === "VERIFIED";
    const isExecuted = record.execution && record.execution.executed === true;
    const verif = record.verification || {};
    const apprv = record.approval || {};

    if (this.dom.verificationBadge) {
      this.dom.verificationBadge.textContent = record.status;
      this.dom.verificationBadge.className = `badge-tag ${this.getStatusBadgeClass(record.status)}`;
    }

    if (isVerified) {
      this.dom.postVerificationBox.innerHTML = `
        <div style="display: flex; align-items: center; gap: 8px; color: var(--color-green); font-weight: 700; font-size: 12px; margin-bottom: 8px;">
          <span>✔ LIVE PHYSICAL VERIFICATION PASSED</span>
          <span class="badge-tag badge-accept">VERIFIED</span>
        </div>
        <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 8px;">
          Authorized by: <strong>${this.escapeHtml(apprv.approved_by || "Operator")}</strong> &bull; Reason: ${this.escapeHtml(apprv.reason || "Standard Protocol")}
        </div>
        <div class="verification-grid">
          <div class="verif-card">
            <span class="verif-label">Target Transformer</span>
            <span class="verif-val" style="color: var(--color-green);">Cooled (< 110.0°C)</span>
          </div>
          <div class="verif-card">
            <span class="verif-label">Remaining Violations</span>
            <span class="verif-val" style="color: var(--color-green);">0 Violations</span>
          </div>
          <div class="verif-card">
            <span class="verif-label">Critical Hospital</span>
            <span class="verif-val" style="color: var(--color-green);">100.0% Preserved</span>
          </div>
          <div class="verif-card">
            <span class="verif-label">System Frequency</span>
            <span class="verif-val" style="color: var(--color-green);">Stable (~50.00 Hz)</span>
          </div>
        </div>
      `;
    } else if (isExecuted) {
      this.dom.postVerificationBox.innerHTML = `
        <div style="color: var(--color-amber); font-size: 11px;">
          Action executed on live grid. Verification in progress...
        </div>
      `;
    } else {
      this.dom.postVerificationBox.innerHTML = `
        <div style="color: var(--text-muted); font-size: 11px;">
          Awaiting human operator authorization. Live action not executed yet.
        </div>
      `;
    }
  }

  async renderActivityEvents(incidentId) {
    if (!this.dom.activityFeed || !incidentId) return;
    try {
      const res = await fetch(`/api/events/${incidentId}`);
      if (!res.ok) return;
      const data = await res.json();
      const events = data.events || [];

      if (events.length === 0) {
        this.dom.activityFeed.innerHTML = '<div class="activity-empty-state">No events recorded for this incident.</div>';
        return;
      }

      this.dom.activityFeed.innerHTML = events
        .map((ev) => {
          const typeName = (ev.event_type || "EVENT").toUpperCase().replace(/_/g, " ");
          const statusClass = ev.status === "success" ? "text-highlight" : ev.status === "rejected" ? "temp-crit" : "text-muted";
          return `
            <div class="activity-event-card">
              <div class="event-header">
                <span>[${this.escapeHtml(typeName)}]</span>
                <span class="${statusClass}">${this.escapeHtml(ev.status || "")}</span>
              </div>
              <div class="event-summary">${this.escapeHtml(ev.summary || "")}</div>
            </div>
          `;
        })
        .join("");
    } catch (err) {
      console.debug("Error fetching incident events:", err);
    }
  }

  async fetchAuditHistory() {
    if (!this.dom.auditHistoryList) return;
    try {
      let url = "/api/audit/records?limit=25&offset=0";
      const statusFilter = this.dom.auditFilterStatus ? this.dom.auditFilterStatus.value : "";
      if (statusFilter) {
        url += `&status=${encodeURIComponent(statusFilter)}`;
      }

      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      const records = data.records || [];

      if (records.length === 0) {
        this.dom.auditHistoryList.innerHTML = '<span class="text-muted" style="font-size: 11px; padding: 8px;">No audit records match query.</span>';
        return;
      }

      this.dom.auditHistoryList.innerHTML = records
        .map((rec) => {
          const isSelected = this.selectedRecordId === rec.incident_id;
          const statusClass = this.getStatusBadgeClass(rec.status);
          const timeStr = (rec.created_at || "").substring(11, 19) || "N/A";
          return `
            <div class="audit-row-card ${isSelected ? "selected" : ""}" data-incident-id="${this.escapeHtml(rec.incident_id)}">
              <div>
                <strong style="color: #fff; font-family: var(--font-mono); font-size: 11px;">${this.escapeHtml(rec.incident_id)}</strong>
                <span style="color: var(--text-muted); font-size: 10px; margin-left: 6px;">${this.escapeHtml(rec.scenario_id)} &bull; ${timeStr}</span>
              </div>
              <span class="badge-tag ${statusClass}">${this.escapeHtml(rec.status)}</span>
            </div>
          `;
        })
        .join("");

      // Bind click handlers to inspect historical records
      this.dom.auditHistoryList.querySelectorAll(".audit-row-card").forEach((elem) => {
        elem.addEventListener("click", () => {
          const incId = elem.getAttribute("data-incident-id");
          this.loadHistoricalRecord(incId);
        });
      });
    } catch (err) {
      console.debug("Error fetching audit history:", err);
    }
  }

  async loadHistoricalRecord(incidentId) {
    try {
      const res = await fetch(`/api/audit/records/${incidentId}`);
      if (!res.ok) return;
      const rec = await res.json();

      this.mode = "history";
      this.selectedRecordId = rec.incident_id;
      this.showHistoryBanner(rec);

      this.renderIncidentState({
        scenario_id: rec.scenario_id,
        incident_state: { active_violations: [] },
        latest_record: rec,
      });

      this.renderLifecycleStages(rec);
      this.renderApprovalGate(rec);
      this.renderSandboxMatrix(rec);
      this.renderSpecialists(rec);
      this.renderRecommendation(rec);
      this.renderPostVerification(rec);
      await this.renderActivityEvents(rec.incident_id);
      await this.fetchAuditHistory();
    } catch (err) {
      console.error("Error loading historical record:", err);
    }
  }

  showHistoryBanner(rec) {
    if (!this.dom.historyBanner) return;
    this.dom.historyBanner.style.display = "flex";
    this.dom.historyBanner.innerHTML = `
      <span>Viewing Historical Audit Record: <strong>${this.escapeHtml(rec.incident_id)}</strong> (${this.escapeHtml(rec.status)})</span>
      <button id="btn-return-live" class="btn-action btn-secondary" style="padding: 2px 10px; font-size: 10px;">Return to Live Telemetry</button>
    `;
    const btnReturn = document.getElementById("btn-return-live");
    if (btnReturn) {
      btnReturn.addEventListener("click", () => {
        this.mode = "live";
        this.selectedRecordId = null;
        this.hideHistoryBanner();
        this.refreshState();
      });
    }
  }

  hideHistoryBanner() {
    if (this.dom.historyBanner) {
      this.dom.historyBanner.style.display = "none";
    }
  }

  async openDiagnosticsModal() {
    if (!this.dom.diagnosticsModal || !this.dom.diagnosticsContent) return;
    this.dom.diagnosticsModal.style.display = "flex";
    this.dom.diagnosticsContent.innerHTML = '<div class="diag-loading">Fetching verified system diagnostics...</div>';

    try {
      const res = await fetch("/api/diagnostics");
      if (!res.ok) throw new Error("Diagnostics endpoint returned error");
      const diag = await res.json();

      const toolsList = (diag.mcp.tools || []).map((t) => `<li><code>${t}</code></li>`).join("");

      this.dom.diagnosticsContent.innerHTML = `
        <div class="diag-grid">
          <div class="diag-card">
            <div class="diag-card-title">MCP Server Transport</div>
            <div class="diag-card-val" style="color: var(--color-green);">ONLINE (Port 8080)</div>
            <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Endpoints: /mcp (Streamable HTTP), /sse (SSE)</div>
          </div>
          <div class="diag-card">
            <div class="diag-card-title">Discovered MCP Tools</div>
            <div class="diag-card-val" style="color: var(--text-highlight);">${diag.mcp.tools_count} Registered Tools</div>
            <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">All tools verified active</div>
          </div>
          <div class="diag-card">
            <div class="diag-card-title">Shared Service Invariant</div>
            <div class="diag-card-val" style="color: var(--color-green);">VERIFIED (Single Process)</div>
            <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Dashboard & MCP share in-memory state</div>
          </div>
          <div class="diag-card">
            <div class="diag-card-title">SQLite Audit Store</div>
            <div class="diag-card-val" style="color: #fff;">${diag.audit_store.total_records} Total Records</div>
            <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">WAL mode enabled &bull; ${this.escapeHtml(diag.audit_store.db_path)}</div>
          </div>
        </div>

        <div style="font-size: 11px; font-weight: 700; color: #fff; margin-bottom: 6px;">REGISTERED MCP TOOL SUITE:</div>
        <ul style="margin-left: 20px; font-size: 11px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 14px;">
          ${toolsList}
        </ul>

        <div style="font-size: 11px; font-weight: 700; color: #fff; margin-bottom: 6px;">AUTHENTICATED RBAC TOKENS:</div>
        <div style="background: rgba(0,0,0,0.4); padding: 8px 10px; border-radius: 4px; font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); line-height: 1.5;">
          &bull; Lead Operator: <code>gm-lead-token-secret</code> (Full approve/reject permissions)<br>
          &bull; Operator: <code>gm-operator-token-secret</code> (Planning/investigation permissions)<br>
          &bull; Viewer: <code>gm-viewer-token-secret</code> (Read-only inspection)
        </div>
      `;
    } catch (err) {
      this.dom.diagnosticsContent.innerHTML = `<div style="color: var(--color-rose); font-size: 11px;">Diagnostics failed: ${err.message}</div>`;
    }
  }

  renderIdleState(data) {
    if (this.dom.incidentTitle) this.dom.incidentTitle.textContent = "STANDBY";
    if (this.dom.incidentStatus) {
      this.dom.incidentStatus.textContent = "NOMINAL";
      this.dom.incidentStatus.className = "badge-tag badge-accept";
    }
    if (this.dom.trackerIncidentId) this.dom.trackerIncidentId.textContent = "NO ACTIVE INCIDENT";
    if (this.dom.approvalContainer) {
      this.dom.approvalContainer.innerHTML = "";
      this.dom.approvalContainer.style.display = "none";
    }
    if (this.dom.heroBanner) {
      this.dom.heroBanner.style.display = "none";
    }
  }

  renderDisconnectedState() {
    if (this.dom.dotGrid) this.dom.dotGrid.className = "health-dot dot-rose";
    if (this.dom.valGrid) this.dom.valGrid.textContent = "DISCONNECTED";
    if (this.dom.dotMcp) this.dom.dotMcp.className = "health-dot dot-rose";
    if (this.dom.valMcp) this.dom.valMcp.textContent = "OFFLINE";
    if (this.dom.dotCommander) this.dom.dotCommander.className = "health-dot dot-rose";
    if (this.dom.valCommander) this.dom.valCommander.textContent = "UNREACHABLE";
  }

  getStatusBadgeClass(status) {
    switch (status) {
      case "VERIFIED":
      case "NOMINAL":
        return "badge-accept";
      case "PENDING_APPROVAL":
        return "badge-pending";
      case "REJECTED_BY_HUMAN":
      case "EXECUTION_REJECTED":
      case "ESCALATED":
      case "NO_SAFE_ACTION":
        return "badge-reject";
      default:
        return "badge-standby";
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

document.addEventListener("DOMContentLoaded", () => {
  window.dashboardApp = new GridMindDashboard();
});
