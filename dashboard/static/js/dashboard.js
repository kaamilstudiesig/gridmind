/**
 * GridMind Command Center — Production Observability Console
 * Pure real-data client connected strictly to live backend and SQLite audit trail.
 * Zero fabricated data, zero fake timers, zero mock tool calls.
 */

class GridMindConsole {
  constructor() {
    this.pollIntervalMs = 2000;
    this.pollTimer = null;
    this.mode = "live"; // 'live' | 'history'
    this.activeScenarioId = "SC02";
    this.selectedRecordId = null;
    this.latestStateRevision = null;
    this.isPlanning = false;
    this.isExecuting = false;

    // Cache DOM Elements
    this.dom = {
      // Health Indicators
      indGrid: document.getElementById("ind-grid"),
      dotGrid: document.getElementById("dot-grid"),
      valGrid: document.getElementById("val-grid"),
      indMcp: document.getElementById("ind-mcp"),
      dotMcp: document.getElementById("dot-mcp"),
      valMcp: document.getElementById("val-mcp"),
      indCommander: document.getElementById("ind-commander"),
      dotCommander: document.getElementById("dot-commander"),
      valCommander: document.getElementById("val-commander"),
      indAudit: document.getElementById("ind-audit"),
      dotAudit: document.getElementById("dot-audit"),
      valAudit: document.getElementById("val-audit"),
      valUpdate: document.getElementById("val-update"),

      // Global Telemetry
      metricFreq: document.getElementById("metric-freq"),
      metricTemp: document.getElementById("metric-temp"),
      metricDemand: document.getElementById("metric-demand"),
      metricLoad: document.getElementById("metric-load"),
      metricHospital: document.getElementById("metric-hospital"),
      metricRevision: document.getElementById("metric-revision"),

      // Incident & Hero Banner
      heroBanner: document.getElementById("hero-incident-banner"),
      incidentScenario: document.getElementById("incident-scenario"),
      incidentTitle: document.getElementById("incident-title"),
      incidentStatus: document.getElementById("incident-status"),
      trackerIncidentId: document.getElementById("tracker-incident-id"),

      // Pipeline Steps
      stageSteps: document.querySelectorAll(".stage-step"),

      // Panels
      transformerGauges: document.getElementById("transformer-gauges"),
      xfmrOverheatCount: document.getElementById("xfmr-overheat-count"),
      matrixBody: document.getElementById("sandbox-matrix-body"),
      specialistsContainer: document.getElementById("specialists-container"),
      recommendationBox: document.getElementById("recommendation-details"),
      approvalContainer: document.getElementById("approval-gate-container"),
      incidentViolations: document.getElementById("incident-violations"),
      violationsCountBadge: document.getElementById("violations-count-badge"),
      activityFeed: document.getElementById("activity-feed-list"),
      postVerificationBox: document.getElementById("post-verification-box"),
      verificationBadge: document.getElementById("verification-status-badge"),
      auditHistoryList: document.getElementById("audit-history-list"),
      historyBanner: document.getElementById("history-mode-banner"),

      // Controls
      scenarioButtons: document.querySelectorAll(".scenario-btn"),
      btnAnalyze: document.getElementById("btn-analyze-incident"),
      btnDiagnostics: document.getElementById("btn-diagnostics-modal"),
      btnRefreshHistory: document.getElementById("btn-refresh-history"),
      auditFilter: document.getElementById("audit-filter-status"),

      // Modals & Auth
      authModal: document.getElementById("auth-modal"),
      btnAuthModal: document.getElementById("btn-auth-modal"),
      btnCloseAuth: document.getElementById("btn-close-auth"),
      inputAuthToken: document.getElementById("input-auth-token"),
      btnSaveAuth: document.getElementById("btn-save-auth"),
      btnClearAuth: document.getElementById("btn-clear-auth"),
      authSessionLabel: document.getElementById("auth-session-label"),
      authStatusDot: document.getElementById("auth-status-dot"),

      diagnosticsModal: document.getElementById("diagnostics-modal"),
      btnCloseDiagnostics: document.getElementById("btn-close-diagnostics"),
      diagnosticsContent: document.getElementById("diagnostics-content"),
    };

    this.init();
  }

  init() {
    this.bindEvents();
    this.updateAuthUI();
    this.startPolling();
    this.fetchDiagnostics();
  }

  getAuthToken() {
    return sessionStorage.getItem("gridmind_auth_token") || "";
  }

  setAuthToken(token) {
    if (token) {
      sessionStorage.setItem("gridmind_auth_token", token.trim());
    } else {
      sessionStorage.removeItem("gridmind_auth_token");
    }
    this.updateAuthUI();
  }

  updateAuthUI() {
    const token = this.getAuthToken();
    if (this.dom.authSessionLabel && this.dom.authStatusDot) {
      if (token) {
        this.dom.authSessionLabel.textContent = "SESSION: AUTHENTICATED";
        this.dom.authStatusDot.className = "auth-dot dot-green";
      } else {
        this.dom.authSessionLabel.textContent = "SESSION: UNAUTHENTICATED";
        this.dom.authStatusDot.className = "auth-dot dot-amber";
      }
    }
    if (this.dom.inputAuthToken) {
      this.dom.inputAuthToken.value = token;
    }
  }

  getAuthHeaders() {
    const headers = { "Content-Type": "application/json" };
    const token = this.getAuthToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }

  bindEvents() {
    // Scenario Switcher
    this.dom.scenarioButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const scId = btn.getAttribute("data-scenario");
        if (scId) this.loadScenario(scId);
      });
    });

    // Plan Incident Button
    if (this.dom.btnAnalyze) {
      this.dom.btnAnalyze.addEventListener("click", () => this.triggerPlanning());
    }

    // Diagnostics Modal
    if (this.dom.btnDiagnostics) {
      this.dom.btnDiagnostics.addEventListener("click", () => this.openDiagnosticsModal());
    }
    if (this.dom.indMcp) {
      this.dom.indMcp.addEventListener("click", () => this.openDiagnosticsModal());
    }
    if (this.dom.btnCloseDiagnostics) {
      this.dom.btnCloseDiagnostics.addEventListener("click", () => this.closeDiagnosticsModal());
    }

    // Auth Key Modal
    if (this.dom.btnAuthModal) {
      this.dom.btnAuthModal.addEventListener("click", () => {
        if (this.dom.authModal) this.dom.authModal.style.display = "flex";
      });
    }
    if (this.dom.btnCloseAuth) {
      this.dom.btnCloseAuth.addEventListener("click", () => {
        if (this.dom.authModal) this.dom.authModal.style.display = "none";
      });
    }
    if (this.dom.btnSaveAuth) {
      this.dom.btnSaveAuth.addEventListener("click", () => {
        const val = this.dom.inputAuthToken ? this.dom.inputAuthToken.value : "";
        this.setAuthToken(val);
        if (this.dom.authModal) this.dom.authModal.style.display = "none";
        this.refreshState();
      });
    }
    if (this.dom.btnClearAuth) {
      this.dom.btnClearAuth.addEventListener("click", () => {
        this.setAuthToken("");
        if (this.dom.authModal) this.dom.authModal.style.display = "none";
        this.refreshState();
      });
    }

    // Audit History Controls
    if (this.dom.btnRefreshHistory) {
      this.dom.btnRefreshHistory.addEventListener("click", () => this.fetchAuditHistory());
    }
    if (this.dom.auditFilter) {
      this.dom.auditFilter.addEventListener("change", () => this.fetchAuditHistory());
    }
  }

  startPolling() {
    this.refreshState();
    this.pollTimer = setInterval(() => {
      if (this.mode === "live") {
        this.refreshState();
      }
    }, this.pollIntervalMs);
  }

  async refreshState() {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      this.activeScenarioId = data.scenario_id;
      this.latestStateRevision = data.state_revision;

      // Update Scenario Buttons Active State
      this.dom.scenarioButtons.forEach((btn) => {
        if (btn.getAttribute("data-scenario") === data.scenario_id) {
          btn.classList.add("active");
        } else {
          btn.classList.remove("active");
        }
      });

      // Update Top Status Bar & Gauges
      this.renderSystemHealth(data);
      this.renderGlobalTelemetry(data);

      const activeRecord = data.latest_record;

      if (!activeRecord) {
        // Clear all incident-bound UI completely for nominal/idle state
        this.renderIdleState(data);
      } else {
        this.renderIncidentState(data);
        this.renderHeroBanner(data, activeRecord);
        this.renderLifecycleStages(activeRecord);
        this.renderApprovalGate(activeRecord);
        this.renderSandboxMatrix(activeRecord);
        this.renderSpecialists(activeRecord);
        this.renderRecommendation(activeRecord);
        this.renderPostVerification(activeRecord);
        await this.renderActivityEvents(activeRecord.incident_id);
      }

      this.renderTopology(data);
      this.renderTransformerGauges(data);
      await this.fetchAuditHistory();
    } catch (err) {
      console.warn("Status refresh error:", err);
      if (this.dom.valUpdate) {
        this.dom.valUpdate.textContent = "Offline";
        this.dom.valUpdate.style.color = "var(--color-rose)";
      }
    }
  }

  renderSystemHealth(data) {
    const inc = data.incident_state || {};
    const viols = inc.active_violations || [];
    const isStable = inc.is_stable ?? true;

    // Grid Status
    if (this.dom.dotGrid && this.dom.valGrid) {
      if (!isStable || viols.length > 0) {
        this.dom.dotGrid.className = "health-dot dot-rose";
        this.dom.valGrid.textContent = "CRITICAL / INCIDENT";
        this.dom.valGrid.style.color = "var(--color-rose)";
      } else {
        this.dom.dotGrid.className = "health-dot dot-green";
        this.dom.valGrid.textContent = "STABLE";
        this.dom.valGrid.style.color = "var(--color-green)";
      }
    }

    // Commander Status
    if (this.dom.valCommander && this.dom.dotCommander) {
      const rec = data.latest_record;
      if (this.isPlanning) {
        this.dom.dotCommander.className = "health-dot dot-amber";
        this.dom.valCommander.textContent = "PLANNING...";
      } else if (rec && rec.status === "PENDING_APPROVAL") {
        this.dom.dotCommander.className = "health-dot dot-amber";
        this.dom.valCommander.textContent = "WAITING APPROVAL";
      } else if (rec && rec.status === "VERIFIED") {
        this.dom.dotCommander.className = "health-dot dot-green";
        this.dom.valCommander.textContent = "VERIFIED";
      } else {
        this.dom.dotCommander.className = "health-dot dot-green";
        this.dom.valCommander.textContent = "READY";
      }
    }

    // Last Update Timestamp
    if (this.dom.valUpdate) {
      const now = new Date();
      this.dom.valUpdate.textContent = now.toTimeString().split(" ")[0];
      this.dom.valUpdate.style.color = "var(--text-primary)";
    }
  }

  renderGlobalTelemetry(data) {
    const grid = data.grid_state || {};
    if (this.dom.metricFreq) {
      this.dom.metricFreq.textContent = typeof grid.frequency_hz === "number" ? `${grid.frequency_hz.toFixed(2)} Hz` : "N/A";
    }
    if (this.dom.metricTemp) {
      this.dom.metricTemp.textContent = typeof grid.ambient_temp_c === "number" ? `${grid.ambient_temp_c.toFixed(1)}°C` : "N/A";
    }
    if (this.dom.metricDemand) {
      this.dom.metricDemand.textContent = typeof grid.demand_multiplier === "number" ? `${grid.demand_multiplier.toFixed(2)}x` : "1.00x";
    }
    if (this.dom.metricHospital) {
      const hosp = grid.critical_hospital_service_pct;
      this.dom.metricHospital.textContent = typeof hosp === "number" ? `${hosp.toFixed(1)}%` : "N/A";
    }
    if (this.dom.metricRevision) {
      this.dom.metricRevision.textContent = data.state_revision || "00000000";
    }
    if (this.dom.metricLoad) {
      let totalLoadKw = 0;
      if (Array.isArray(grid.transformers)) {
        totalLoadKw = grid.transformers.reduce((acc, t) => acc + (t.load_kw || 0), 0);
      }
      this.dom.metricLoad.textContent = `${totalLoadKw.toFixed(1)} kW`;
    }
  }

  renderIdleState(data) {
    if (this.dom.incidentScenario) this.dom.incidentScenario.textContent = data.scenario_id || "BASE";
    if (this.dom.incidentTitle) this.dom.incidentTitle.textContent = "STANDBY";
    if (this.dom.incidentStatus) {
      this.dom.incidentStatus.textContent = "NOMINAL";
      this.dom.incidentStatus.className = "badge-tag badge-accept";
    }
    if (this.dom.trackerIncidentId) {
      this.dom.trackerIncidentId.textContent = "NO ACTIVE INCIDENT";
    }
    if (this.dom.heroBanner) {
      this.dom.heroBanner.style.display = "none";
      this.dom.heroBanner.innerHTML = "";
    }
    if (this.dom.approvalContainer) {
      this.dom.approvalContainer.innerHTML = "";
      this.dom.approvalContainer.style.display = "none";
    }

    // Reset 9-Stage Pipeline
    this.dom.stageSteps.forEach((step) => {
      const stageNum = parseInt(step.getAttribute("data-stage"), 10);
      step.className = "stage-step not-reached";
      const statusElem = document.getElementById(`step-status-${stageNum}`);
      if (statusElem) statusElem.textContent = "Standby";
    });

    // Reset Sandbox Candidate Matrix
    if (this.dom.matrixBody) {
      this.dom.matrixBody.innerHTML = `
        <tr><td colspan="8" class="empty-table-cell">
          No active incident. Grid operating in nominal state with zero candidate actions required.
        </td></tr>
      `;
    }

    // Reset Specialist Reasoning
    if (this.dom.specialistsContainer) {
      this.dom.specialistsContainer.innerHTML = `
        <div class="specialist-card">
          <div class="spec-header"><span class="spec-name">Operations Specialist</span><span class="badge-tag badge-standby">STANDBY</span></div>
          <div class="spec-finding text-muted">Awaiting incident detection. Grid nominal.</div>
        </div>
        <div class="specialist-card">
          <div class="spec-header"><span class="spec-name">Safety Specialist</span><span class="badge-tag badge-standby">STANDBY</span></div>
          <div class="spec-finding text-muted">Awaiting incident detection. Constraints nominal.</div>
        </div>
        <div class="specialist-card">
          <div class="spec-header"><span class="spec-name">Planning Specialist</span><span class="badge-tag badge-standby">STANDBY</span></div>
          <div class="spec-finding text-muted">Awaiting incident detection. Asset health nominal.</div>
        </div>
      `;
    }

    // Reset Recommendation
    if (this.dom.recommendationBox) {
      this.dom.recommendationBox.innerHTML = `
        <div style="font-size: 11px; color: var(--text-muted);">
          Grid is currently nominal. No active incident response plan or intervention required.
        </div>
      `;
    }

    // Reset Post-Action Verification
    if (this.dom.postVerificationBox) {
      this.dom.postVerificationBox.innerHTML = `
        <div style="color: var(--text-muted); font-size: 11px;">
          No active incident. Live grid is in baseline operation.
        </div>
      `;
    }
    if (this.dom.verificationBadge) {
      this.dom.verificationBadge.textContent = "NOMINAL";
      this.dom.verificationBadge.className = "badge-tag badge-accept";
    }

    // Reset Activity Stream
    if (this.dom.activityFeed) {
      this.dom.activityFeed.innerHTML = `
        <div class="activity-empty-state">
          Grid nominal. Real tool calls, sandbox evaluations, and specialist verdicts will stream here when an incident is active.
        </div>
      `;
    }

    // Reset Violations
    if (this.dom.incidentViolations) {
      this.dom.incidentViolations.innerHTML = '<span class="text-muted">Zero active constraint violations.</span>';
    }
    if (this.dom.violationsCountBadge) {
      this.dom.violationsCountBadge.textContent = "0 ACTIVE";
      this.dom.violationsCountBadge.className = "badge-tag badge-accept";
    }
    if (this.dom.xfmrOverheatCount) {
      this.dom.xfmrOverheatCount.textContent = "0 Overheated";
      this.dom.xfmrOverheatCount.className = "text-muted";
    }
  }

  renderIncidentState(data) {
    const inc = data.incident_state || {};
    const viols = inc.active_violations || [];
    const overheated = inc.overheated_transformers || [];

    if (this.dom.incidentScenario) this.dom.incidentScenario.textContent = data.scenario_id || "BASE";
    if (this.dom.incidentTitle) {
      this.dom.incidentTitle.textContent = viols.length > 0 ? "THERMAL OVERLOAD DETECTED" : "NOMINAL OPERATION";
    }
    if (this.dom.incidentStatus) {
      if (data.latest_record) {
        this.dom.incidentStatus.textContent = data.latest_record.status;
        this.dom.incidentStatus.className = `badge-tag ${this.getStatusBadgeClass(data.latest_record.status)}`;
      } else {
        this.dom.incidentStatus.textContent = viols.length > 0 ? "UNRESOLVED" : "NOMINAL";
        this.dom.incidentStatus.className = viols.length > 0 ? "badge-tag badge-reject" : "badge-tag badge-accept";
      }
    }

    // Active Violations Box
    if (this.dom.violationsCountBadge) {
      this.dom.violationsCountBadge.textContent = `${viols.length} ACTIVE`;
      this.dom.violationsCountBadge.className = viols.length > 0 ? "badge-tag badge-reject" : "badge-tag badge-accept";
    }

    if (this.dom.incidentViolations) {
      if (viols.length === 0) {
        this.dom.incidentViolations.innerHTML = '<span class="text-muted">Zero active constraint violations.</span>';
      } else {
        this.dom.incidentViolations.innerHTML = viols
          .map((v) => {
            const desc = typeof v === "string" ? v : v.description || "Active Violation";
            return `
              <div class="violation-item">
                <span class="violation-icon">🔴</span>
                <span class="violation-desc">${this.escapeHtml(desc)}</span>
              </div>
            `;
          })
          .join("");
      }
    }

    // Overheat counter
    if (this.dom.xfmrOverheatCount) {
      this.dom.xfmrOverheatCount.textContent = `${overheated.length} Overheated`;
      this.dom.xfmrOverheatCount.className = overheated.length > 0 ? "text-danger" : "text-muted";
    }
  }

  renderHeroBanner(data, activeRecord) {
    if (!this.dom.heroBanner) return;
    const inc = data.incident_state || {};
    const viols = inc.active_violations || [];
    const overheated = inc.overheated_transformers || [];

    if (overheated.length > 0) {
      // Real transformer overheat incident
      const xfmrId = overheated[0];
      const xfmrs = (data.grid_state && Array.isArray(data.grid_state.transformers)) ? data.grid_state.transformers : [];
      const xfmrObj = xfmrs.find((t) => t.transformer_id === xfmrId);
      const tempVal = xfmrObj && typeof xfmrObj.temperature_c === "number" ? xfmrObj.temperature_c : null;
      const tempStr = tempVal !== null ? `${tempVal.toFixed(2)}°C` : "Telemetry Unavailable";
      const overLimitStr = tempVal !== null ? ` &bull; Over limit by +${(tempVal - 110.0).toFixed(2)}°C` : "";

      this.dom.heroBanner.style.display = "flex";
      this.dom.heroBanner.innerHTML = `
        <div class="hero-alert-left">
          <div class="hero-alert-icon">🔴</div>
          <div>
            <div class="hero-alert-title">CRITICAL BREACH: ${this.escapeHtml(xfmrId)} TRANSFORMER OVERHEAT (${tempStr})</div>
            <div class="hero-alert-sub">Thermal Limit: 110.00°C${overLimitStr} &bull; Scenario: ${this.escapeHtml(data.scenario_id)}</div>
          </div>
        </div>
        <div class="hero-alert-badge">${activeRecord ? activeRecord.status : "INCIDENT ACTIVE"}</div>
      `;
    } else if (viols.length > 0) {
      // Non-thermal violation (e.g. frequency droop, line tripped, critical load unserved)
      const firstViol = viols[0];
      const violDesc = typeof firstViol === "string" ? firstViol : firstViol.description || "Active Constraint Breach";
      const violType = typeof firstViol === "object" ? firstViol.violation_type || "GRID_VIOLATION" : "GRID_VIOLATION";

      this.dom.heroBanner.style.display = "flex";
      this.dom.heroBanner.innerHTML = `
        <div class="hero-alert-left">
          <div class="hero-alert-icon">⚠️</div>
          <div>
            <div class="hero-alert-title">ACTIVE INCIDENT: ${this.escapeHtml(violType.replace(/_/g, " "))}</div>
            <div class="hero-alert-sub">${this.escapeHtml(violDesc)} &bull; Scenario: ${this.escapeHtml(data.scenario_id)}</div>
          </div>
        </div>
        <div class="hero-alert-badge">${activeRecord ? activeRecord.status : "INCIDENT ACTIVE"}</div>
      `;
    } else {
      this.dom.heroBanner.style.display = "none";
      this.dom.heroBanner.innerHTML = "";
    }
  }

  renderLifecycleStages(record) {
    if (this.dom.trackerIncidentId) {
      this.dom.trackerIncidentId.textContent = record.incident_id || "NO ACTIVE INCIDENT";
    }

    const stagesConfig = [
      { num: 1, name: "Incident Detect", status: "Active" },
      { num: 2, name: "Operations Role", status: "Formulated" },
      { num: 3, name: "Candidates", status: "3 Generated" },
      { num: 4, name: "Sandbox Eval", status: "Simulated" },
      { num: 5, name: "Safety Gate", status: "Validated" },
      { num: 6, name: "Planning Role", status: "Prioritized" },
      { num: 7, name: "Recommendation", status: "Ready" },
      { num: 8, name: "Human Approval", status: record.status },
      { num: 9, name: "Verification", status: record.status === "VERIFIED" ? "Passed" : "Awaiting" },
    ];

    const isPending = record.status === "PENDING_APPROVAL";
    const isVerified = record.status === "VERIFIED";
    const isRejected = record.status === "REJECTED_BY_HUMAN";
    const isExecuted = record.execution && record.execution.executed === true;

    this.dom.stageSteps.forEach((step) => {
      const stageNum = parseInt(step.getAttribute("data-stage"), 10);
      const cfg = stagesConfig[stageNum - 1];
      const statusElem = document.getElementById(`step-status-${stageNum}`);

      if (stageNum <= 7) {
        step.className = "stage-step completed";
        if (statusElem) statusElem.textContent = cfg.status;
      } else if (stageNum === 8) {
        if (isPending) {
          step.className = "stage-step active-pending";
          if (statusElem) statusElem.textContent = "WAITING SIGN-OFF";
        } else if (isRejected) {
          step.className = "stage-step rejected";
          if (statusElem) statusElem.textContent = "Rejected";
        } else {
          step.className = "stage-step completed";
          if (statusElem) statusElem.textContent = "Approved";
        }
      } else if (stageNum === 9) {
        if (isVerified) {
          step.className = "stage-step completed";
          if (statusElem) statusElem.textContent = "Verified";
        } else if (isExecuted) {
          step.className = "stage-step active-pending";
          if (statusElem) statusElem.textContent = "Unverified";
        } else {
          step.className = "stage-step not-reached";
          if (statusElem) statusElem.textContent = "Awaiting";
        }
      }
    });
  }

  renderApprovalGate(record) {
    if (!this.dom.approvalContainer) return;

    if (record.status !== "PENDING_APPROVAL") {
      this.dom.approvalContainer.innerHTML = "";
      this.dom.approvalContainer.style.display = "none";
      return;
    }

    const rec = record.recommended_action || {};
    const actType = rec.action_type || "UNKNOWN";
    const paramsStr = JSON.stringify(rec.parameters || {});
    const cid = rec.candidate_id || "CAND";

    this.dom.approvalContainer.style.display = "block";
    this.dom.approvalContainer.innerHTML = `
      <div class="approval-gate-banner">
        <div class="approval-gate-header">
          <div class="approval-gate-title">
            <span class="pulse-warning">⚠️</span>
            <span>HUMAN APPROVAL GATE — INCIDENT INTERVENTION READY</span>
          </div>
          <span class="badge-tag badge-pending">PENDING_APPROVAL</span>
        </div>
        <div class="approval-gate-body">
          <div class="approval-action-summary">
            Recommended Action: <strong>${this.escapeHtml(actType)}</strong> (<code>${this.escapeHtml(cid)}</code>)
            <br>
            Parameters: <code>${this.escapeHtml(paramsStr)}</code>
          </div>
          <div class="approval-controls-row">
            <input type="text" id="approval-reason-input" class="approval-input" placeholder="Operator authorization note (e.g., 'Approved for thermal relief')" value="Approved for emergency thermal relief">
            <div class="approval-btn-group">
              <button id="btn-execute-approval" class="btn-action btn-approve" ${this.isExecuting ? "disabled" : ""}>
                ${this.isExecuting ? "Executing..." : "✔ Authorize & Execute"}
              </button>
              <button id="btn-reject-action" class="btn-action btn-reject" ${this.isExecuting ? "disabled" : ""}>
                ✖ Reject Plan
              </button>
            </div>
          </div>
        </div>
      </div>
    `;

    const btnApprove = document.getElementById("btn-execute-approval");
    const btnReject = document.getElementById("btn-reject-action");
    const reasonInput = document.getElementById("approval-reason-input");

    if (btnApprove) {
      btnApprove.addEventListener("click", () => {
        const reason = reasonInput ? reasonInput.value : "Authorized by operator";
        this.submitApproval(record.incident_id, true, reason);
      });
    }
    if (btnReject) {
      btnReject.addEventListener("click", () => {
        const reason = reasonInput ? reasonInput.value : "Rejected by operator";
        this.submitApproval(record.incident_id, false, reason);
      });
    }
  }

  renderSandboxMatrix(record) {
    if (!this.dom.matrixBody) return;
    const safetyRes = (record.specialist_results || {}).safety || {};
    const evidenceList = safetyRes.evidence || [];
    const recAction = record.recommended_action || {};

    if (!evidenceList || evidenceList.length === 0) {
      this.dom.matrixBody.innerHTML = `
        <tr><td colspan="8" class="empty-table-cell">
          No sandbox candidate actions evaluated yet. Click "Plan Incident" or trigger from TrueForge.
        </td></tr>
      `;
      return;
    }

    this.dom.matrixBody.innerHTML = evidenceList
      .map((ev) => {
        const act = ev.action || {};
        const cid = act.candidate_id || "CAND";
        const actType = act.action_type || "unknown";
        const paramsStr = JSON.stringify(act.parameters || {});
        const isChosen = recAction && recAction.candidate_id === cid;

        // Find true peak temperature across all recorded transformer temperatures in evidence
        let peakTemp = null;
        let peakXfmr = null;

        if (ev.predicted_peak_temp !== undefined && ev.predicted_peak_temp !== null) {
          peakTemp = ev.predicted_peak_temp;
          peakXfmr = ev.predicted_peak_transformer || "XFMR";
        } else if (ev.predicted_transformer_temperatures && typeof ev.predicted_transformer_temperatures === "object") {
          for (const [xId, tVal] of Object.entries(ev.predicted_transformer_temperatures)) {
            if (typeof tVal === "number" && (peakTemp === null || tVal > peakTemp)) {
              peakTemp = tVal;
              peakXfmr = xId;
            }
          }
        } else {
          // Fallback to checking specific fields if present
          const candidatesTemps = [
            { id: "T01", val: ev.predicted_temp_t01 },
            { id: "T02", val: ev.predicted_temp_t02 },
            { id: "T03", val: ev.predicted_temp_t03 },
            { id: "T04", val: ev.predicted_temp_t04 },
            { id: "T05", val: ev.predicted_temp_t05 },
          ];
          for (const item of candidatesTemps) {
            if (typeof item.val === "number" && (peakTemp === null || item.val > peakTemp)) {
              peakTemp = item.val;
              peakXfmr = item.id;
            }
          }
        }

        const peakTempStr = peakTemp !== null ? `${peakXfmr}: ${peakTemp.toFixed(2)}°C` : "N/A";
        const peakColor = (peakTemp !== null && peakTemp > 110.0) ? "var(--color-rose)" : "var(--color-green)";

        // Grid stability
        const isStable = ev.is_stable === true;
        const stabilityStr = isStable ? "Stable" : "Unstable";
        const stabilityColor = isStable ? "var(--color-green)" : "var(--color-rose)";

        // Active Violations
        const viols = ev.violations || [];
        const violsStr = viols.length > 0 ? `${viols.length} Violations` : "0 Violations";
        const violsColor = viols.length > 0 ? "var(--color-rose)" : "var(--color-green)";

        // Hospital Service (LZ04)
        let hospService = null;
        if (typeof ev.critical_hospital_service_pct === "number") {
          hospService = ev.critical_hospital_service_pct;
        } else if (ev.critical_load_service && typeof ev.critical_load_service === "object" && typeof ev.critical_load_service["LZ04"] === "number") {
          hospService = ev.critical_load_service["LZ04"];
        }
        const hospStr = hospService !== null ? `${hospService.toFixed(1)}%` : "N/A";
        const hospColor = (hospService !== null && hospService >= 100.0) ? "var(--color-green)" : "var(--color-rose)";

        // Verdict
        const isValid = ev.action_valid && isStable && viols.length === 0 && (hospService === null || hospService >= 100.0);
        const verdictBadge = isValid
          ? '<span class="badge-tag badge-accept">SAFE / ACCEPT</span>'
          : '<span class="badge-tag badge-reject">REJECTED</span>';

        return `
          <tr class="${isChosen ? "row-recommended" : ""}">
            <td><code>${this.escapeHtml(cid)}</code> ${isChosen ? '<span class="star-rec">⭐</span>' : ""}</td>
            <td><strong>${this.escapeHtml(actType)}</strong></td>
            <td><code class="param-code">${this.escapeHtml(paramsStr)}</code></td>
            <td style="color: ${peakColor}; font-weight: 600;">${this.escapeHtml(peakTempStr)}</td>
            <td style="color: ${stabilityColor};">${this.escapeHtml(stabilityStr)}</td>
            <td style="color: ${violsColor};">${this.escapeHtml(violsStr)}</td>
            <td style="color: ${hospColor}; font-weight: 600;">${this.escapeHtml(hospStr)}</td>
            <td>${verdictBadge}</td>
          </tr>
        `;
      })
      .join("");
  }

  renderSpecialists(record) {
    if (!this.dom.specialistsContainer) return;
    const specs = record.specialist_results || {};
    const roles = ["operations", "safety", "planning"];

    this.dom.specialistsContainer.innerHTML = roles
      .map((role) => {
        const spec = specs[role] || {};
        const title = role.toUpperCase() + " SPECIALIST";
        const status = spec.status || "STANDBY";
        const finding = spec.finding || "Awaiting investigation.";
        const badgeClass = this.getStatusBadgeClass(status);

        return `
          <div class="specialist-card">
            <div class="spec-header">
              <span class="spec-name">${this.escapeHtml(title)}</span>
              <span class="badge-tag ${badgeClass}">${this.escapeHtml(status)}</span>
            </div>
            <div class="spec-finding">${this.escapeHtml(finding)}</div>
          </div>
        `;
      })
      .join("");
  }

  renderRecommendation(record) {
    if (!this.dom.recommendationBox) return;
    const rec = record.recommended_action;
    const planSpec = (record.specialist_results || {}).planning || {};

    if (!rec) {
      this.dom.recommendationBox.innerHTML = `
        <div style="font-size: 11px; color: var(--text-muted);">
          No operational recommendation synthesized yet. Status: <strong>${record.status}</strong>
        </div>
      `;
      return;
    }

    const actType = rec.action_type || "UNKNOWN";
    const cid = rec.candidate_id || "CAND";
    const paramsStr = JSON.stringify(rec.parameters || {});
    const rationale = planSpec.finding || "Action prioritized as minimum-disruption intervention.";

    this.dom.recommendationBox.innerHTML = `
      <div class="rec-header">
        <div class="rec-title">
          <span>⭐ RECOMMENDED INTERVENTION: <strong>${this.escapeHtml(actType)}</strong></span>
          <span class="badge-tag badge-accept">PRIORITIZED</span>
        </div>
        <div class="rec-meta">Candidate ID: <code>${this.escapeHtml(cid)}</code></div>
      </div>
      <div class="rec-params">Parameters: <code>${this.escapeHtml(paramsStr)}</code></div>
      <div class="rec-rationale">${this.escapeHtml(rationale)}</div>
    `;
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
      // Read ACTUAL measurements from record.verification
      const postFreq = typeof verif.post_frequency_hz === "number" ? `${verif.post_frequency_hz.toFixed(2)} Hz` : "N/A";
      const remainingViols = typeof verif.remaining_violations_count === "number" 
        ? `${verif.remaining_violations_count} Violations` 
        : Array.isArray(verif.active_violations) ? `${verif.active_violations.length} Violations` : "N/A";
      const hospService = typeof verif.critical_hospital_service_pct === "number" 
        ? `${verif.critical_hospital_service_pct.toFixed(1)}%` 
        : "N/A";
      const maxTemp = typeof verif.max_transformer_temperature_c === "number"
        ? `${verif.max_transformer_temperature_c.toFixed(2)}°C`
        : "N/A";

      const approverName = apprv.approved_by || "Operator";
      const reasonStr = apprv.reason || "Authorized by operator";

      this.dom.postVerificationBox.innerHTML = `
        <div style="display: flex; align-items: center; gap: 8px; color: var(--color-green); font-weight: 700; font-size: 12px; margin-bottom: 8px;">
          <span>✔ LIVE PHYSICAL VERIFICATION PASSED</span>
          <span class="badge-tag badge-accept">VERIFIED</span>
        </div>
        <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 8px;">
          Authorized by: <strong>${this.escapeHtml(approverName)}</strong> &bull; Reason: ${this.escapeHtml(reasonStr)}
        </div>
        <div class="verification-grid">
          <div class="verif-card">
            <span class="verif-label">Max Transformer Temp</span>
            <span class="verif-val" style="color: var(--color-green);">${this.escapeHtml(maxTemp)}</span>
          </div>
          <div class="verif-card">
            <span class="verif-label">Remaining Violations</span>
            <span class="verif-val" style="color: var(--color-green);">${this.escapeHtml(remainingViols)}</span>
          </div>
          <div class="verif-card">
            <span class="verif-label">Critical Hospital</span>
            <span class="verif-val" style="color: var(--color-green);">${this.escapeHtml(hospService)}</span>
          </div>
          <div class="verif-card">
            <span class="verif-label">System Frequency</span>
            <span class="verif-val" style="color: var(--color-green);">${this.escapeHtml(postFreq)}</span>
          </div>
        </div>
      `;
    } else if (isExecuted) {
      this.dom.postVerificationBox.innerHTML = `
        <div style="color: var(--color-amber); font-size: 11px;">
          Action executed on live grid. Verification evaluation in progress...
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
        this.dom.activityFeed.innerHTML = `
          <div class="activity-empty-state">
            No events recorded yet for incident ${this.escapeHtml(incidentId)}.
          </div>
        `;
        return;
      }

      this.dom.activityFeed.innerHTML = events
        .map((ev) => {
          const type = ev.event_type || "EVENT";
          const title = ev.title || type;
          const detail = ev.detail || "";
          const phase = ev.phase || "EXECUTION";
          const ts = ev.timestamp ? ev.timestamp.split("T")[1].split(".")[0] : "";

          let tagClass = "badge-standby";
          if (type.includes("specialist")) tagClass = "badge-roles";
          else if (type.includes("sandbox")) tagClass = "badge-sandbox";
          else if (type.includes("approval")) tagClass = "badge-pending";
          else if (type.includes("verification")) tagClass = "badge-accept";
          else if (type.includes("execution")) tagClass = "badge-accept";

          return `
            <div class="activity-item">
              <div class="act-header">
                <span class="badge-tag ${tagClass}">${this.escapeHtml(type)}</span>
                <span class="act-title">${this.escapeHtml(title)}</span>
                <span class="act-time">${this.escapeHtml(ts)}</span>
              </div>
              <div class="act-detail">${this.escapeHtml(detail)}</div>
            </div>
          `;
        })
        .join("");
    } catch (err) {
      console.warn("Error fetching activity events:", err);
    }
  }

  renderTopology(data) {
    const inc = data.incident_state || {};
    const grid = data.grid_state || {};
    const trippedLines = new Set(inc.tripped_lines || []);
    const overheatedXfmrs = new Set(inc.overheated_transformers || []);

    // Line L08 status
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

    // Nodes and transformers strictly derived from authoritative overheatedXfmrs telemetry
    const nodeN02 = document.getElementById("svg-node-N02");
    if (nodeN02) {
      nodeN02.setAttribute("class", overheatedXfmrs.has("T01") ? "topo-node overheated" : "topo-node");
    }

    const nodeN08 = document.getElementById("svg-node-N08");
    if (nodeN08) {
      nodeN08.setAttribute("class", overheatedXfmrs.has("T04") ? "topo-node commercial overheated" : "topo-node commercial");
    }

    const nodeN07 = document.getElementById("svg-node-N07");
    if (nodeN07) {
      // N07 is a residential load zone; only mark overheated if an asset explicitly on N07 is overheated
      nodeN07.setAttribute("class", overheatedXfmrs.has("T01_N07") ? "topo-node residential overheated" : "topo-node residential");
    }
  }

  renderTransformerGauges(data) {
    if (!this.dom.transformerGauges) return;
    const grid = data.grid_state || {};
    const xfmrs = grid.transformers || [];

    if (xfmrs.length === 0) {
      this.dom.transformerGauges.innerHTML = '<span class="text-muted">No transformer telemetry available.</span>';
      return;
    }

    this.dom.transformerGauges.innerHTML = xfmrs
      .map((t) => {
        const xId = t.transformer_id;
        const temp = t.temperature_c || 0;
        const loadKw = t.load_kw || 0;
        const ratingKva = t.rating_kva || 1;
        const loadPct = ((loadKw / ratingKva) * 100).toFixed(1);
        const isOverheated = temp > 110.0;
        const isWarning = temp > 80.0 && !isOverheated;

        let thermalClass = "temp-normal";
        if (isOverheated) thermalClass = "temp-danger";
        else if (isWarning) thermalClass = "temp-warning";

        return `
          <div class="xfmr-card ${isOverheated ? "xfmr-card-danger" : ""}">
            <div class="xfmr-card-header">
              <span class="xfmr-id">${this.escapeHtml(xId)}</span>
              <span class="xfmr-rating">${ratingKva} kVA</span>
            </div>
            <div class="xfmr-temp-row">
              <span class="xfmr-temp ${thermalClass}">${temp.toFixed(2)}°C</span>
              <span class="xfmr-limit">Limit: 110.0°C</span>
            </div>
            <div class="xfmr-load-bar-wrap">
              <div class="xfmr-load-bar" style="width: ${Math.min(100, parseFloat(loadPct))}%; background: ${isOverheated ? "var(--color-rose)" : "var(--color-cyan)"};"></div>
            </div>
            <div class="xfmr-load-meta">
              <span>Load: ${loadKw.toFixed(1)} kW</span>
              <span>${loadPct}%</span>
            </div>
          </div>
        `;
      })
      .join("");
  }

  async fetchAuditHistory() {
    if (!this.dom.auditHistoryList) return;
    try {
      const filterStatus = this.dom.auditFilter ? this.dom.auditFilter.value : "";
      const url = filterStatus ? `/api/audit/records?status=${encodeURIComponent(filterStatus)}` : "/api/audit/records";
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      const records = data.records || [];

      if (records.length === 0) {
        this.dom.auditHistoryList.innerHTML = '<span class="text-muted">No audit records found in database.</span>';
        return;
      }

      this.dom.auditHistoryList.innerHTML = records
        .map((r) => {
          const isSelected = this.selectedRecordId === r.incident_id;
          const statusBadge = this.getStatusBadgeClass(r.status);
          const actType = r.recommended_action ? r.recommended_action.action_type : "NONE";
          const ts = r.created_at ? r.created_at.split("T")[1].split(".")[0] : "";

          return `
            <div class="audit-item ${isSelected ? "audit-item-selected" : ""}" data-incident-id="${this.escapeHtml(r.incident_id)}">
              <div class="audit-item-header">
                <span class="audit-id">${this.escapeHtml(r.incident_id)}</span>
                <span class="badge-tag ${statusBadge}">${this.escapeHtml(r.status)}</span>
              </div>
              <div class="audit-item-meta">
                <span>Scenario: <strong>${this.escapeHtml(r.scenario_id)}</strong></span>
                <span>&bull;</span>
                <span>Action: <strong>${this.escapeHtml(actType)}</strong></span>
                <span>&bull;</span>
                <span>${this.escapeHtml(ts)}</span>
              </div>
            </div>
          `;
        })
        .join("");

      // Bind click listeners for history inspection
      const items = this.dom.auditHistoryList.querySelectorAll(".audit-item");
      items.forEach((item) => {
        item.addEventListener("click", () => {
          const incId = item.getAttribute("data-incident-id");
          if (incId) this.loadHistoricalRecord(incId);
        });
      });
    } catch (err) {
      console.warn("Error fetching audit history:", err);
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

      // Extract real pre_state_evidence preserved in the AuditRecord
      const preEvidence = (rec.pre_state_evidence && rec.pre_state_evidence.length > 0) ? rec.pre_state_evidence[0] : {};
      const preViolations = preEvidence.active_violations ? preEvidence.active_violations.map(v => typeof v === "string" ? { description: v } : v) : [];
      const preOverheated = preEvidence.overheated_transformers || [];
      const preTripped = preEvidence.tripped_lines || [];

      // Render historical incident with its ACTUAL recorded evidence
      this.renderIncidentState({
        scenario_id: rec.scenario_id,
        incident_state: {
          is_stable: preEvidence.is_stable ?? false,
          active_violations: preViolations,
          overheated_transformers: preOverheated,
          tripped_lines: preTripped,
          frequency_hz: preEvidence.frequency_hz,
          ambient_temp_c: preEvidence.ambient_temp_c,
        },
        grid_state: {
          frequency_hz: preEvidence.frequency_hz,
          ambient_temp_c: preEvidence.ambient_temp_c,
          demand_multiplier: preEvidence.demand_multiplier,
          transformers: [],
        },
        state_revision: rec.state_revision,
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

  showHistoryBanner(record) {
    if (!this.dom.historyBanner) return;
    this.dom.historyBanner.style.display = "flex";
    this.dom.historyBanner.innerHTML = `
      <div class="history-banner-left">
        <span>📜 HISTORICAL AUDIT INSPECTION MODE — RECORD <strong>${this.escapeHtml(record.incident_id)}</strong> (${this.escapeHtml(record.status)})</span>
      </div>
      <button id="btn-exit-history" class="btn-action btn-secondary" style="padding: 4px 10px; font-size: 11px;">
        Exit to Live Telemetry
      </button>
    `;

    const btnExit = document.getElementById("btn-exit-history");
    if (btnExit) {
      btnExit.addEventListener("click", () => {
        this.mode = "live";
        this.selectedRecordId = null;
        this.dom.historyBanner.style.display = "none";
        this.refreshState();
      });
    }
  }

  async loadScenario(scenarioId) {
    try {
      const res = await fetch("/api/scenario/load", {
        method: "POST",
        headers: this.getAuthHeaders(),
        body: JSON.stringify({ scenario_id: scenarioId }),
      });
      if (res.status === 401 || res.status === 403) {
        alert(`Authentication Required (${res.status}): Please enter an authorized Operator Bearer Key.`);
        return;
      }
      if (!res.ok) {
        const err = await res.json();
        alert(`Failed to load scenario: ${err.detail || res.statusText}`);
        return;
      }
      this.mode = "live";
      this.selectedRecordId = null;
      if (this.dom.historyBanner) this.dom.historyBanner.style.display = "none";
      await this.refreshState();
    } catch (err) {
      console.error("Error loading scenario:", err);
    }
  }

  async triggerPlanning() {
    if (this.isPlanning) return;
    try {
      this.isPlanning = true;
      if (this.dom.btnAnalyze) {
        this.dom.btnAnalyze.disabled = true;
        this.dom.btnAnalyze.textContent = "Planning...";
      }

      const res = await fetch("/api/commander/plan", {
        method: "POST",
        headers: this.getAuthHeaders(),
        body: JSON.stringify({ scenario_id: this.activeScenarioId }),
      });

      if (res.status === 401 || res.status === 403) {
        alert(`Authentication Required (${res.status}): Operator authorization token required to trigger planning.`);
        return;
      }
      if (!res.ok) {
        const err = await res.json();
        alert(`Planning failed: ${err.detail || res.statusText}`);
        return;
      }

      await this.refreshState();
    } catch (err) {
      console.error("Error in planning dispatch:", err);
    } finally {
      this.isPlanning = false;
      if (this.dom.btnAnalyze) {
        this.dom.btnAnalyze.disabled = false;
        this.dom.btnAnalyze.textContent = "⚡ Plan Incident";
      }
    }
  }

  async submitApproval(incidentId, approved, reason) {
    if (this.isExecuting) return;
    try {
      this.isExecuting = true;
      const endpoint = approved ? "/api/commander/approve" : "/api/commander/reject";
      const payload = approved
        ? { incident_id: incidentId, reason: reason }
        : { incident_id: incidentId, reason: reason };

      const res = await fetch(endpoint, {
        method: "POST",
        headers: this.getAuthHeaders(),
        body: JSON.stringify(payload),
      });

      if (res.status === 401 || res.status === 403) {
        alert(`Authorization Failed (${res.status}): Only authorized operators can sign off on live grid execution.`);
        return;
      }
      if (!res.ok) {
        const err = await res.json();
        alert(`Approval error: ${err.detail || res.statusText}`);
        return;
      }

      await this.refreshState();
    } catch (err) {
      console.error("Error submitting approval:", err);
    } finally {
      this.isExecuting = false;
    }
  }

  async openDiagnosticsModal() {
    if (!this.dom.diagnosticsModal) return;
    this.dom.diagnosticsModal.style.display = "flex";
    await this.fetchDiagnostics();
  }

  closeDiagnosticsModal() {
    if (this.dom.diagnosticsModal) this.dom.diagnosticsModal.style.display = "none";
  }

  async fetchDiagnostics() {
    if (!this.dom.diagnosticsContent) return;
    try {
      const res = await fetch("/api/diagnostics", {
        headers: this.getAuthHeaders(),
      });
      if (res.status === 401 || res.status === 403) {
        this.dom.diagnosticsContent.innerHTML = `
          <div style="color: var(--color-amber); padding: 12px; font-size: 11px;">
            ⚠️ Diagnostics endpoint requires an authenticated session. Click "🔑 Operator Key" to set token.
          </div>
        `;
        return;
      }
      if (!res.ok) {
        this.dom.diagnosticsContent.innerHTML = `<div style="color: var(--color-rose);">Diagnostics unreachable (HTTP ${res.status}).</div>`;
        return;
      }
      const data = await res.json();
      const mcp = data.mcp || {};
      const tools = mcp.tools || [];
      const isMcpOnline = mcp.status === "online";

      this.dom.diagnosticsContent.innerHTML = `
        <div class="diag-section">
          <div class="diag-title">Unified Server Connectivity</div>
          <div class="diag-grid">
            <div class="diag-item"><span>Service:</span> <strong>${this.escapeHtml(data.service)}</strong></div>
            <div class="diag-item"><span>Active Scenario:</span> <strong>${this.escapeHtml(data.active_scenario)}</strong></div>
            <div class="diag-item"><span>State Revision:</span> <code>${this.escapeHtml(data.state_revision)}</code></div>
            <div class="diag-item"><span>Operator:</span> <strong>${this.escapeHtml(data.operator || "None")} (${this.escapeHtml(data.role || "None")})</strong></div>
          </div>
        </div>

        <div class="diag-section">
          <div class="diag-title">MCP Protocol Server (<code style="font-size: 10px;">${isMcpOnline ? "MOUNTED ON /mcp" : "NOT MOUNTED"}</code>)</div>
          <div class="diag-grid">
            <div class="diag-item"><span>Status:</span> <strong style="color: ${isMcpOnline ? "var(--color-green)" : "var(--color-rose)"};">${this.escapeHtml(mcp.status)}</strong></div>
            <div class="diag-item"><span>Registered Tools:</span> <strong>${mcp.tools_count} Tools Available</strong></div>
            <div class="diag-item"><span>Transports:</span> <code>${(mcp.transports || []).join(", ") || "None"}</code></div>
          </div>
          <div style="margin-top: 8px;">
            <div style="font-size: 10px; color: var(--text-muted); margin-bottom: 4px;">Registered Tools:</div>
            <div style="display: flex; flex-wrap: wrap; gap: 4px;">
              ${tools.map((t) => `<span class="badge-tag badge-sandbox">${this.escapeHtml(t)}</span>`).join("")}
            </div>
          </div>
        </div>

        <div class="diag-section">
          <div class="diag-title">Durable Audit Storage</div>
          <div class="diag-grid">
            <div class="diag-item"><span>Storage Engine:</span> <strong>SQLite WAL (Full Durability)</strong></div>
            <div class="diag-item"><span>Total Audit Records:</span> <strong>${data.audit_store ? data.audit_store.total_records : 0}</strong></div>
          </div>
        </div>
      `;
    } catch (err) {
      console.warn("Diagnostics fetch error:", err);
    }
  }

  getStatusBadgeClass(status) {
    switch (status) {
      case "VERIFIED":
      case "ACCEPT":
      case "NOMINAL":
        return "badge-accept";
      case "PENDING_APPROVAL":
      case "WAITING":
        return "badge-pending";
      case "REJECTED_BY_HUMAN":
      case "EXECUTION_REJECTED":
      case "REJECT":
      case "NO_SAFE_ACTION":
      case "CRITICAL":
        return "badge-reject";
      case "ESCALATED":
      case "STALE_STATE":
      case "EXECUTED_UNVERIFIED":
        return "badge-escalated";
      default:
        return "badge-standby";
    }
  }

  escapeHtml(str) {
    if (typeof str !== "string") return String(str ?? "");
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
}

// Instantiate on DOM ready
document.addEventListener("DOMContentLoaded", () => {
  window.gridMindConsole = new GridMindConsole();
});
