/**
 * GridMind Command Center client logic (Phases 1-5 Complete).
 */

class GridMindDashboard {
  constructor() {
    this.pollInterval = 2000;
    this.timer = null;
    this.activeScenario = "SC01";
    this.activeIncidentId = null;
    this.selectedRecordId = null;
    this.mode = "live"; // "live" | "history"
    this.viewMode = "guided"; // "guided" | "console"
    this.isPlanning = false;
    this.isSubmittingApproval = false;
    this.auditPageSize = 20;
    this.auditOffset = 0;

    // Progressive disclosure states
    this.isActivityFeedExpanded = false;
    this.cachedEvents = [];
    this.isAuditHistoryExpanded = false;
    this.cachedRecords = [];

    this.dom = {
      // Header metrics & Auth
      freqVal: document.getElementById("metric-freq"),
      tempVal: document.getElementById("metric-temp"),
      demandVal: document.getElementById("metric-demand"),
      revisionVal: document.getElementById("metric-revision"),
      freqGauge: document.getElementById("metric-freq-gauge"),
      tempGauge: document.getElementById("metric-temp-gauge"),
      demandGauge: document.getElementById("metric-demand-gauge"),
      revisionGauge: document.getElementById("metric-revision-gauge"),
      loadMetric: document.getElementById("metric-load"),
      hospitalMetric: document.getElementById("metric-hospital"),
      
      gridStatusBadge: document.getElementById("grid-status-badge"),
      gridStatusDot: document.getElementById("grid-status-dot"),
      scenarioButtons: document.querySelectorAll(".scenario-tab"),
      btnAnalyze: document.getElementById("btn-analyze-incident"),
      btnToggleViewMode: document.getElementById("btn-toggle-view-mode"),
      viewModeLabel: document.getElementById("view-mode-label"),

      // Health Strip
      indMcp: document.getElementById("ind-mcp"),
      dotGrid: document.getElementById("dot-grid"),
      valGrid: document.getElementById("val-grid"),
      dotMcp: document.getElementById("dot-mcp"),
      valMcp: document.getElementById("val-mcp"),
      dotCommander: document.getElementById("dot-commander"),
      valCommander: document.getElementById("val-commander"),
      dotAudit: document.getElementById("dot-audit"),
      valAudit: document.getElementById("val-audit"),
      valUpdate: document.getElementById("val-update"),

      // Stage tracker
      stageSteps: document.querySelectorAll(".stage-step"),

      // History mode banner
      historyBanner: document.getElementById("history-mode-banner"),

      // Error banner
      appErrorBanner: document.getElementById("app-error-banner"),
      errorBannerMsg: document.getElementById("error-banner-msg"),
      errorBannerActionBtn: document.getElementById("error-banner-action-btn"),
      errorBannerDismissBtn: document.getElementById("error-banner-dismiss-btn"),

      // Panels
      incidentTitle: document.getElementById("incident-title"),
      incidentScenario: document.getElementById("incident-scenario"),
      incidentStatus: document.getElementById("incident-status"),
      incidentViolations: document.getElementById("incident-violations"),
      violationsCountBadge: document.getElementById("violations-count-badge"),
      heroBanner: document.getElementById("hero-incident-banner"),

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

      // Activity Feed & Progressive Disclosure
      activityFeed: document.getElementById("activity-feed-list"),
      activityToggleWrap: document.getElementById("activity-feed-toggle-wrap"),
      btnToggleActivity: document.getElementById("btn-toggle-activity-feed"),

      // Audit History & Progressive Disclosure
      auditHistoryList: document.getElementById("audit-history-list"),
      auditToggleWrap: document.getElementById("audit-history-toggle-wrap"),
      btnToggleAudit: document.getElementById("btn-toggle-audit-history"),
      postVerificationBox: document.getElementById("post-verification-box"),
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

      // Diagnostics Modal
      diagnosticsModal: document.getElementById("diagnostics-modal"),
      btnDiagnostics: document.getElementById("btn-diagnostics-modal"),
      btnCloseDiagnostics: document.getElementById("btn-close-diagnostics"),
      diagnosticsContent: document.getElementById("diagnostics-content"),

      // Setup Wizard Modal
      setupWizardModal: document.getElementById("setup-wizard-modal"),
      btnSetupWizard: document.getElementById("btn-setup-wizard"),
      btnCloseSetupWizard: document.getElementById("btn-close-setup-wizard"),
      btnWizardDone: document.getElementById("btn-wizard-done"),
      chkWizardDontShow: document.getElementById("chk-wizard-dont-show"),
      wizardStep1Status: document.getElementById("wizard-step-1-status"),
      wizardStep1Desc: document.getElementById("wizard-step-1-desc"),
      wizardStep2Status: document.getElementById("wizard-step-2-status"),
      wizardStep2Desc: document.getElementById("wizard-step-2-desc"),
      wizardAuthInput: document.getElementById("wizard-auth-input"),
      btnWizardSaveToken: document.getElementById("btn-wizard-save-token"),
      wizardAuthFeedback: document.getElementById("wizard-auth-feedback"),
      wizardStep3Status: document.getElementById("wizard-step-3-status"),
      wizardStep3Desc: document.getElementById("wizard-step-3-desc"),
      btnWizardCheckMcp: document.getElementById("btn-wizard-check-mcp"),
      wizardMcpCheckMsg: document.getElementById("wizard-mcp-check-msg"),
      btnWizardScs: document.querySelectorAll(".btn-wizard-sc"),
    };

    this.init();
  }

  async init() {
    this.initViewMode();
    this.bindEvents();
    this.updateAuthUI();
    await this.refreshState();
    await this.fetchAuditHistory();
    await this.fetchDiagnostics();
    this.startPolling();

    // Check first-run setup wizard
    this.checkFirstRunSetup();
  }

  initViewMode() {
    const saved = localStorage.getItem("gridmind_view_mode") || "guided";
    this.setViewMode(saved);
  }

  setViewMode(mode) {
    this.viewMode = mode;
    localStorage.setItem("gridmind_view_mode", mode);
    document.body.className = mode === "guided" ? "mode-guided" : "mode-console";
    if (this.dom.viewModeLabel) {
      this.dom.viewModeLabel.textContent = mode === "guided" ? "View: Guided" : "View: Full Console";
    }

    // Update panel collapse states
    const panels = document.querySelectorAll(".progressive-panel");
    panels.forEach(p => {
      if (mode === "guided") {
        p.classList.add("collapsed");
      } else {
        p.classList.remove("collapsed");
      }
    });
  }

  toggleViewMode() {
    const nextMode = this.viewMode === "guided" ? "console" : "guided";
    this.setViewMode(nextMode);
  }

  showErrorBanner(message, actionText = null, actionHandler = null) {
    if (!this.dom.appErrorBanner) return;
    if (this.dom.errorBannerMsg) this.dom.errorBannerMsg.textContent = message;
    
    if (this.dom.errorBannerActionBtn) {
      if (actionText && actionHandler) {
        this.dom.errorBannerActionBtn.style.display = "inline-flex";
        this.dom.errorBannerActionBtn.textContent = actionText;
        this.dom.errorBannerActionBtn.onclick = () => {
          actionHandler();
          this.hideErrorBanner();
        };
      } else {
        this.dom.errorBannerActionBtn.style.display = "none";
        this.dom.errorBannerActionBtn.onclick = null;
      }
    }

    this.dom.appErrorBanner.style.display = "flex";
  }

  hideErrorBanner() {
    if (this.dom.appErrorBanner) {
      this.dom.appErrorBanner.style.display = "none";
    }
  }

  checkFirstRunSetup() {
    const dismissed = localStorage.getItem("gridmind_setup_dismissed");
    if (!dismissed) {
      this.openSetupWizard();
    }
  }

  openSetupWizard() {
    if (this.dom.setupWizardModal) {
      this.dom.setupWizardModal.style.display = "flex";
      this.runWizardChecks();
    }
  }

  closeSetupWizard() {
    if (this.dom.setupWizardModal) {
      this.dom.setupWizardModal.style.display = "none";
    }
  }

  async runWizardChecks() {
    // Step 1: Check Backend Health
    try {
      const res = await fetch("/health");
      if (res.ok) {
        const data = await res.json();
        if (this.dom.wizardStep1Status) {
          this.dom.wizardStep1Status.textContent = "ONLINE ✓";
          this.dom.wizardStep1Status.className = "wizard-status-badge badge-success";
        }
        if (this.dom.wizardStep1Desc) {
          this.dom.wizardStep1Desc.innerHTML = `FastAPI service online (${data.service} v${data.version}). Active scenario: <strong>${data.active_scenario}</strong>.`;
        }
        const s1 = document.getElementById("wizard-step-1");
        if (s1) s1.className = "wizard-step step-success";
      }
    } catch {
      if (this.dom.wizardStep1Status) {
        this.dom.wizardStep1Status.textContent = "OFFLINE ✕";
        this.dom.wizardStep1Status.className = "wizard-status-badge badge-error";
      }
      if (this.dom.wizardStep1Desc) {
        this.dom.wizardStep1Desc.textContent = "Cannot connect to /health endpoint. Verify server process.";
      }
    }

    // Step 2: Check Auth Session
    const token = this.getAuthToken();
    if (this.dom.wizardAuthInput) this.dom.wizardAuthInput.value = token;
    await this.validateWizardAuth(token);

    // Step 3: MCP Signal
    await this.checkWizardMcp();
  }

  async validateWizardAuth(token) {
    const s2 = document.getElementById("wizard-step-2");
    if (!token) {
      if (this.dom.wizardStep2Status) {
        this.dom.wizardStep2Status.textContent = "NO KEY SET";
        this.dom.wizardStep2Status.className = "wizard-status-badge badge-pending";
      }
      if (this.dom.wizardStep2Desc) {
        this.dom.wizardStep2Desc.textContent = "No operator token configured. Click a role button below to apply.";
      }
      if (this.dom.wizardAuthFeedback) this.dom.wizardAuthFeedback.textContent = "";
      if (s2) s2.className = "wizard-step step-pending";
      return;
    }

    try {
      const res = await fetch("/api/diagnostics", {
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        if (this.dom.wizardStep2Status) {
          this.dom.wizardStep2Status.textContent = "AUTHENTICATED ✓";
          this.dom.wizardStep2Status.className = "wizard-status-badge badge-success";
        }
        if (this.dom.wizardStep2Desc) {
          this.dom.wizardStep2Desc.innerHTML = `Session valid: <strong>${data.operator}</strong> (Role: <code style="color: var(--color-mint);">${data.role}</code>).`;
        }
        if (this.dom.wizardAuthFeedback) {
          this.dom.wizardAuthFeedback.innerHTML = `<span style="color: var(--color-mint);">✔ Token verified for ${data.operator} (${data.role})</span>`;
        }
        if (s2) s2.className = "wizard-step step-success";
      } else {
        if (this.dom.wizardStep2Status) {
          this.dom.wizardStep2Status.textContent = `UNAUTHORIZED (${res.status})`;
          this.dom.wizardStep2Status.className = "wizard-status-badge badge-error";
        }
        if (this.dom.wizardAuthFeedback) {
          this.dom.wizardAuthFeedback.innerHTML = `<span style="color: var(--color-rose);">✕ Invalid token (HTTP ${res.status}). Select an authorized dev key.</span>`;
        }
        if (s2) s2.className = "wizard-step";
      }
    } catch {
      if (this.dom.wizardStep2Status) {
        this.dom.wizardStep2Status.textContent = "CHECK FAILED";
        this.dom.wizardStep2Status.className = "wizard-status-badge badge-error";
      }
    }
  }

  async checkWizardMcp() {
    if (this.dom.wizardMcpCheckMsg) {
      this.dom.wizardMcpCheckMsg.textContent = "Checking MCP sessions...";
    }
    try {
      const res = await fetch("/api/status");
      if (res.ok) {
        const data = await res.json();
        const mcp = data.mcp || {};
        const s3 = document.getElementById("wizard-step-3");
        if (mcp.status === "not_mounted") {
          if (this.dom.wizardStep3Status) {
            this.dom.wizardStep3Status.textContent = "NOT MOUNTED ✕";
            this.dom.wizardStep3Status.className = "wizard-status-badge badge-error";
          }
          if (this.dom.wizardMcpCheckMsg) {
            this.dom.wizardMcpCheckMsg.innerHTML = '<span style="color: var(--color-rose);">✕ MCP server is not mounted in server config.</span>';
          }
          if (s3) s3.className = "wizard-step";
        } else if (!mcp.active_sessions || mcp.active_sessions === 0) {
          if (this.dom.wizardStep3Status) {
            this.dom.wizardStep3Status.textContent = "MOUNTED (0 CLIENTS)";
            this.dom.wizardStep3Status.className = "wizard-status-badge badge-pending";
          }
          if (this.dom.wizardMcpCheckMsg) {
            this.dom.wizardMcpCheckMsg.innerHTML = `<span style="color: var(--color-amber);">⚡ MCP server ready on :8080/mcp (0 active clients). Connect TrueForge to start.</span>`;
          }
          if (s3) s3.className = "wizard-step step-pending";
        } else {
          if (this.dom.wizardStep3Status) {
            this.dom.wizardStep3Status.textContent = `CONNECTED (${mcp.active_sessions} SESSION${mcp.active_sessions > 1 ? 'S' : ''}) ✓`;
            this.dom.wizardStep3Status.className = "wizard-status-badge badge-success";
          }
          if (this.dom.wizardMcpCheckMsg) {
            this.dom.wizardMcpCheckMsg.innerHTML = `<span style="color: var(--color-mint);">✔ ${mcp.active_sessions} active MCP client connection(s) detected!</span>`;
          }
          if (s3) s3.className = "wizard-step step-success";
        }
      }
    } catch {
      if (this.dom.wizardMcpCheckMsg) {
        this.dom.wizardMcpCheckMsg.innerHTML = '<span style="color: var(--color-rose);">✕ MCP check failed.</span>';
      }
    }
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
    // View mode toggle
    if (this.dom.btnToggleViewMode) {
      this.dom.btnToggleViewMode.addEventListener("click", () => this.toggleViewMode());
    }

    // Panel expand/collapse buttons in guided mode
    document.querySelectorAll(".panel-expand-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const targetId = btn.getAttribute("data-target");
        const panel = document.getElementById(targetId);
        if (panel) {
          panel.classList.toggle("collapsed");
        }
      });
    });

    // Error banner dismiss
    if (this.dom.errorBannerDismissBtn) {
      this.dom.errorBannerDismissBtn.addEventListener("click", () => this.hideErrorBanner());
    }

    // Scenario tab buttons
    this.dom.scenarioButtons.forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        let btnTarget = e.target;
        if (e.target.classList.contains("sc-badge")) {
          btnTarget = e.target.parentElement;
        }
        const targetSc = btnTarget.getAttribute("data-scenario");
        if (targetSc) {
          await this.loadScenario(targetSc);
        }
      });
    });

    // Wizard Scenario Buttons
    this.dom.btnWizardScs.forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const sc = btn.getAttribute("data-scenario");
        if (sc) {
          await this.loadScenario(sc);
          this.dom.btnWizardScs.forEach(b => b.classList.toggle("active", b.getAttribute("data-scenario") === sc));
        }
      });
    });

    // Setup Wizard Trigger & Controls
    if (this.dom.btnSetupWizard) {
      this.dom.btnSetupWizard.addEventListener("click", () => this.openSetupWizard());
    }
    if (this.dom.btnCloseSetupWizard) {
      this.dom.btnCloseSetupWizard.addEventListener("click", () => this.closeSetupWizard());
    }
    if (this.dom.btnWizardDone) {
      this.dom.btnWizardDone.addEventListener("click", () => {
        if (this.dom.chkWizardDontShow && this.dom.chkWizardDontShow.checked) {
          localStorage.setItem("gridmind_setup_dismissed", "true");
        }
        this.closeSetupWizard();
      });
    }

    if (this.dom.btnWizardSaveToken) {
      this.dom.btnWizardSaveToken.addEventListener("click", async () => {
        const val = this.dom.wizardAuthInput ? this.dom.wizardAuthInput.value : "";
        this.setAuthToken(val);
        await this.validateWizardAuth(val);
        this.refreshState();
      });
    }

    if (this.dom.btnWizardCheckMcp) {
      this.dom.btnWizardCheckMcp.addEventListener("click", async () => {
        await this.checkWizardMcp();
      });
    }

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

    if (this.dom.auditFilter) {
      this.dom.auditFilter.addEventListener("change", async () => {
        await this.fetchAuditHistory();
      });
    }

    // Progressive disclosure: Activity Feed
    if (this.dom.btnToggleActivity) {
      this.dom.btnToggleActivity.addEventListener("click", () => {
        this.isActivityFeedExpanded = !this.isActivityFeedExpanded;
        this.renderActivityList();
      });
    }

    // Progressive disclosure: Audit History
    if (this.dom.btnToggleAudit) {
      this.dom.btnToggleAudit.addEventListener("click", () => {
        this.isAuditHistoryExpanded = !this.isAuditHistoryExpanded;
        this.renderAuditList();
      });
    }

    // Modals
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

    if (this.dom.btnDiagnostics) {
      this.dom.btnDiagnostics.addEventListener("click", () => this.openDiagnosticsModal());
    }
    if (this.dom.indMcp) {
      this.dom.indMcp.addEventListener("click", () => this.openDiagnosticsModal());
    }
    if (this.dom.btnCloseDiagnostics) {
      this.dom.btnCloseDiagnostics.addEventListener("click", () => this.closeDiagnosticsModal());
    }
  }

  async openDiagnosticsModal() {
    if (this.dom.diagnosticsModal) this.dom.diagnosticsModal.style.display = "flex";
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
      const activeSessions = mcp.active_sessions || 0;

      this.dom.diagnosticsContent.innerHTML = `
        <div class="diag-section">
          <div class="diag-title">Unified Server Connectivity</div>
          <div class="diag-grid">
            <span class="diag-lbl">Status</span>
            <span class="diag-val" style="color: ${data.status === 'healthy' ? 'var(--color-mint)' : 'var(--color-rose)'}">${data.status.toUpperCase()}</span>
            
            <span class="diag-lbl">Active Scenario</span>
            <span class="diag-val">${data.active_scenario}</span>

            <span class="diag-lbl">Operator Identity</span>
            <span class="diag-val">${data.operator} (${data.role})</span>

            <span class="diag-lbl">State Revision</span>
            <span class="diag-val">${data.state_revision || '00000000'}</span>
          </div>

          <div class="diag-title">Model Provider & Commander</div>
          <div class="diag-grid">
            <span class="diag-lbl">Commander Status</span>
            <span class="diag-val">${data.commander.status.toUpperCase()}</span>

            <span class="diag-lbl">LLM Model Target</span>
            <span class="diag-val">${data.commander.llm_model}</span>

            <span class="diag-lbl">Safe Mode Degraded</span>
            <span class="diag-val" style="color: ${data.commander.is_degraded_mode ? 'var(--color-amber)' : 'var(--color-mint)'}">
              ${data.commander.is_degraded_mode ? 'YES (No API Key)' : 'NO (Verified)'}
            </span>
          </div>

          <div class="diag-title">Model Context Protocol (MCP) Server</div>
          <div class="diag-grid">
            <span class="diag-lbl">MCP Status</span>
            <span class="diag-val" style="color: ${isMcpOnline ? 'var(--color-mint)' : 'var(--color-secondary)'}">
              ${mcp.status.toUpperCase()}
            </span>

            <span class="diag-lbl">Active MCP Sessions</span>
            <span class="diag-val" style="color: ${activeSessions > 0 ? 'var(--color-mint)' : 'var(--color-amber)'}">
              ${activeSessions} active connection${activeSessions !== 1 ? 's' : ''}
            </span>

            <span class="diag-lbl">Registered Tools</span>
            <span class="diag-val">${mcp.tools_count} active</span>

            <span class="diag-lbl">Last Activity</span>
            <span class="diag-val">${mcp.last_mcp_activity_at ? this.formatTime(mcp.last_mcp_activity_at) : 'None'}</span>
          </div>
          ${isMcpOnline ? `
            <div style="font-family: var(--font-mono); font-size: 9px; color: var(--text-secondary); background: rgba(0,0,0,0.3); padding: 8px; border-radius: 4px;">
              ${tools.join(" &bull; ")}
            </div>
          ` : ""}

          <div class="diag-title">Audit Store Diagnostics</div>
          <div class="diag-grid">
            <span class="diag-lbl">Audit Status</span>
            <span class="diag-val">${data.audit_store.status.toUpperCase()}</span>

            <span class="diag-lbl">Storage Engine</span>
            <span class="diag-val">${data.audit_store.storage_type}</span>

            <span class="diag-lbl">Total Logged Records</span>
            <span class="diag-val">${data.audit_store.total_records} logs</span>
          </div>
        </div>
      `;
    } catch (err) {
      console.warn("Diagnostics fetch error:", err);
    }
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
      if (res.status === 401 || res.status === 403) {
        this.showErrorBanner("Authentication Required: Operator permissions needed to load scenario.", "🔑 Set Key", () => {
          if (this.dom.authModal) this.dom.authModal.style.display = "flex";
        });
        return;
      }
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to load scenario");
      }
      this.activeScenario = scenarioId;
      this.mode = "live";
      this.selectedRecordId = null;
      this.hideHistoryBanner();
      this.hideErrorBanner();
      await this.refreshState();
      await this.fetchAuditHistory();
    } catch (err) {
      console.error("Scenario load error:", err);
      this.showErrorBanner(`Error loading scenario: ${err.message}`, "Reset to BASE", () => {
        this.loadScenario("BASE");
      });
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

      // Animate stages progression while planning
      let stepCounter = 1;
      const stepTimer = setInterval(() => {
        if (stepCounter < 7 && this.isPlanning) {
          stepCounter++;
          this.setLifecycleStep(stepCounter);
        }
      }, 250);

      const res = await fetch("/api/commander/plan", {
        method: "POST",
        headers: this.getAuthHeaders(),
      });
      clearInterval(stepTimer);

      if (res.status === 401 || res.status === 403) {
        this.showErrorBanner("Authentication Required: Viewer/Operator permissions needed to trigger planning.", "🔑 Set Key", () => {
          if (this.dom.authModal) this.dom.authModal.style.display = "flex";
        });
        return;
      }
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to plan incident response");
      }
      const data = await res.json();
      this.activeIncidentId = data.incident_id;
      this.mode = "live";
      this.selectedRecordId = null;
      this.hideHistoryBanner();
      this.hideErrorBanner();
      await this.refreshState();
      await this.fetchAuditHistory();
    } catch (err) {
      console.error("Commander plan error:", err);
      this.showErrorBanner(`Commander Planning Error: ${err.message}`);
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

      if (res.status === 401 || res.status === 403) {
        this.showErrorBanner("Authentication Required: 'operator' role required for action approval/rejection.", "🔑 Switch to Operator Key", () => {
          this.setAuthToken(this.devTokens.operator);
          this.refreshState();
        });
        return;
      }

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Approval request failed");
      }

      this.hideErrorBanner();
      await this.refreshState();
      await this.fetchAuditHistory();
    } catch (err) {
      console.error("Approval error:", err);
      this.showErrorBanner(`Approval Error: ${err.message}`);
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

    // Active scenario tab state
    this.dom.scenarioButtons.forEach((btn) => {
      const sc = btn.getAttribute("data-scenario");
      const isActive = sc === this.activeScenario;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });

    const freqStr = (grid.frequency_hz || 60.0).toFixed(2) + " Hz";
    const tempStr = (grid.ambient_temp_c || 25.0).toFixed(1) + "°C";
    const demandStr = (grid.demand_multiplier || 1.0).toFixed(2) + "x";
    const revStr = data.state_revision || "00000000";

    if (this.dom.freqVal) this.dom.freqVal.textContent = freqStr;
    if (this.dom.tempVal) this.dom.tempVal.textContent = tempStr;
    if (this.dom.demandVal) this.dom.demandVal.textContent = demandStr;
    if (this.dom.revisionVal) this.dom.revisionVal.textContent = revStr;

    if (this.dom.freqGauge) this.dom.freqGauge.textContent = freqStr;
    if (this.dom.tempGauge) this.dom.tempGauge.textContent = tempStr;
    if (this.dom.demandGauge) this.dom.demandGauge.textContent = demandStr;
    if (this.dom.revisionGauge) this.dom.revisionGauge.textContent = revStr;

    // Total System Load
    if (this.dom.loadMetric) {
      let totalLoadKw = 0;
      if (Array.isArray(grid.transformers)) {
        totalLoadKw = grid.transformers.reduce((acc, t) => acc + (t.load_kw || 0), 0);
      }
      this.dom.loadMetric.textContent = `${totalLoadKw.toFixed(1)} kW`;
    }

    // Critical Hospital Service
    if (this.dom.hospitalMetric) {
      const hosp = grid.critical_hospital_service_pct;
      this.dom.hospitalMetric.textContent = typeof hosp === "number" ? `${hosp.toFixed(1)}%` : "100.0%";
    }

    // Last Poll Time
    if (this.dom.valUpdate) {
      this.dom.valUpdate.textContent = new Date().toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    const isStable = inc.is_stable ?? true;
    const violsCount = (inc.active_violations || []).length;

    // Center header big badge
    if (this.dom.gridStatusBadge && this.dom.gridStatusDot) {
      if (!isStable || violsCount > 0) {
        this.dom.gridStatusBadge.textContent = "INCIDENT ACTIVE";
        this.dom.gridStatusBadge.className = "primary-status-text critical";
        this.dom.gridStatusDot.className = "status-pulse-dot critical";
      } else {
        this.dom.gridStatusBadge.textContent = "GRID NOMINAL";
        this.dom.gridStatusBadge.className = "primary-status-text nominal";
        this.dom.gridStatusDot.className = "status-pulse-dot";
      }
    }

    // System Health strip dots
    if (this.dom.dotGrid && this.dom.valGrid) {
      if (!isStable || violsCount > 0) {
        this.dom.dotGrid.className = "health-dot dot-rose";
        this.dom.valGrid.textContent = "CRITICAL / INCIDENT";
        this.dom.valGrid.style.color = "var(--color-rose)";
      } else {
        this.dom.dotGrid.className = "health-dot dot-green";
        this.dom.valGrid.textContent = "STABLE";
        this.dom.valGrid.style.color = "var(--color-mint)";
      }
    }

    if (this.dom.dotCommander && this.dom.valCommander) {
      if (this.isPlanning) {
        this.dom.dotCommander.className = "health-dot dot-amber";
        this.dom.valCommander.textContent = "PLANNING...";
      } else {
        const latestRec = data.latest_record;
        if (latestRec && latestRec.status === "PENDING_APPROVAL") {
          this.dom.dotCommander.className = "health-dot dot-amber";
          this.dom.valCommander.textContent = "AWAITING HUMAN";
        } else {
          this.dom.dotCommander.className = "health-dot dot-green";
          this.dom.valCommander.textContent = "READY";
        }
      }
    }

    // Honest MCP 3-State Connection Indicator
    if (this.dom.dotMcp && this.dom.valMcp) {
      const mcp = data.mcp || {};
      if (mcp.status === "not_mounted") {
        this.dom.dotMcp.className = "health-dot dot-rose";
        this.dom.valMcp.textContent = "NOT MOUNTED";
        this.dom.valMcp.style.color = "var(--color-rose)";
      } else if (!mcp.active_sessions || mcp.active_sessions === 0) {
        this.dom.dotMcp.className = "health-dot dot-amber";
        this.dom.valMcp.textContent = "MOUNTED (0 Clients)";
        this.dom.valMcp.style.color = "var(--color-amber)";
      } else {
        this.dom.dotMcp.className = "health-dot dot-green";
        this.dom.valMcp.textContent = `CONNECTED (${mcp.active_sessions} Client${mcp.active_sessions > 1 ? 's' : ''})`;
        this.dom.valMcp.style.color = "var(--color-mint)";
      }
    }

    if (this.dom.dotAudit && this.dom.valAudit) {
      this.dom.dotAudit.className = "health-dot dot-green";
      this.dom.valAudit.textContent = "SQLITE WAL";
    }
  }

  renderTopology(data) {
    const inc = data.incident_state || {};
    const trippedLines = inc.tripped_lines || [];
    const overheated = inc.overheated_transformers || [];

    // L08 Tie Line
    const isL08Tripped = trippedLines.includes("L08");
    const tieLine = document.getElementById("svg-line-L08");
    if (tieLine) {
      tieLine.className.baseVal = isL08Tripped ? "topo-line tripped" : "topo-line feeder-a";
    }

    // N08 node
    const isN08Critical = overheated.includes("T04");
    const nodeN08 = document.getElementById("svg-node-N08");
    if (nodeN08) {
      nodeN08.className.baseVal = isN08Critical ? "topo-node commercial tripped" : "topo-node commercial";
    }
  }

  renderTransformers(data) {
    const grid = data.grid_state || {};
    const transformers = grid.transformers || [];
    if (!this.dom.transformerGauges) return;

    if (transformers.length === 0) {
      this.dom.transformerGauges.innerHTML = '<span class="muted-text">No active telemetry available.</span>';
      return;
    }

    this.dom.transformerGauges.innerHTML = transformers
      .map((t) => {
        const temp = t.temperature_c || 0;
        const load = t.load_kw || 0;
        const cap = t.capacity_kva || 1;
        const limit = t.safety_limit_c || 100;
        
        let ringColor = "var(--color-mint)";
        if (temp > limit) ringColor = "var(--color-rose)";
        else if (temp > limit * 0.85) ringColor = "var(--color-amber)";

        const radius = 24;
        const circ = 2 * Math.PI * radius;
        const strokeDash = circ - Math.min((temp / 220) * circ, circ);

        return `
          <div class="transformer-gauge-card">
            <span class="gauge-lbl">${t.transformer_id}</span>
            <div class="gauge-ring-wrap">
              <svg width="54" height="54" viewBox="0 0 54 54">
                <circle cx="27" cy="27" r="${radius}" class="gauge-ring-bg" />
                <circle cx="27" cy="27" r="${radius}" class="gauge-ring-fill"
                        style="stroke: ${ringColor}; stroke-dasharray: ${circ}; stroke-dashoffset: ${strokeDash};" />
              </svg>
              <div class="gauge-val-center">${temp.toFixed(0)}°C</div>
            </div>
            <span class="gauge-lbl gauge-load">${load.toFixed(0)} kW</span>
          </div>
        `;
      })
      .join("");
  }

  renderIncidentState(data) {
    const inc = data.incident_state || {};
    
    // Scenario and Title
    if (this.dom.incidentScenario) this.dom.incidentScenario.textContent = data.scenario_id;
    if (this.dom.incidentTitle) this.dom.incidentTitle.textContent = inc.incident_id || "STANDBY";
    if (this.dom.incidentStatus) {
      const activeRecord = data.latest_record;
      if (activeRecord) {
        this.dom.incidentStatus.textContent = activeRecord.status;
        let badgeClass = "pill-standby";
        if (activeRecord.status === "VERIFIED") badgeClass = "pill-nominal";
        else if (activeRecord.status === "REJECTED_BY_HUMAN" || activeRecord.status === "NO_SAFE_ACTION") badgeClass = "pill-critical";
        this.dom.incidentStatus.className = `pill-badge ${badgeClass}`;
      } else {
        this.dom.incidentStatus.textContent = "NOMINAL";
        this.dom.incidentStatus.className = "pill-badge pill-nominal";
      }
    }

    // Hero Banner Alert if incident active
    if (this.dom.heroBanner) {
      if (inc.incident_id && !(inc.is_stable)) {
        this.dom.heroBanner.style.display = "block";
        const viols = inc.active_violations || [];
        this.dom.heroBanner.textContent = `🚨 ACTIVE SYSTEM BREACH: Incident ID ${inc.incident_id} detected. Active Violations: ${viols.join(", ") || "None"}.`;
      } else {
        this.dom.heroBanner.style.display = "none";
      }
    }

    // Violations List
    if (this.dom.incidentViolations) {
      const viols = inc.active_violations || [];
      if (viols.length === 0) {
        this.dom.incidentViolations.innerHTML = '<span class="muted-text">Zero active violations.</span>';
        if (this.dom.violationsCountBadge) this.dom.violationsCountBadge.style.display = "none";
      } else {
        this.dom.incidentViolations.innerHTML = viols
          .map((v) => {
            const violText = typeof v === "object" && v !== null 
              ? (v.message || v.description || v.name || v.rule || JSON.stringify(v))
              : String(v);
            return `<div class="pill-badge pill-critical" style="padding: 6px; font-size: 10px;">⚠️ ${this.escapeHtml(violText)}</div>`;
          })
          .join("");
        if (this.dom.violationsCountBadge) {
          this.dom.violationsCountBadge.style.display = "inline-block";
          this.dom.violationsCountBadge.textContent = viols.length;
        }
      }
    }
  }

  computeLifecycleStep(record) {
    if (!record) return 1;
    
    // If verified or rejected or executed
    if (record.status === "VERIFIED") return 9;
    if (record.verification && record.verification.verified) return 9;
    if (record.execution && record.execution.executed) return 9;
    if (record.status === "REJECTED_BY_HUMAN") return 8;
    if (record.status === "PENDING_APPROVAL") return 8;
    if (record.status === "NO_SAFE_ACTION") return 7;

    const specs = record.specialist_results || {};
    if (record.recommended_action) return 8;
    if (specs.planning) return 7;
    if (specs.safety && specs.safety.evidence && specs.safety.evidence.length > 0) return 5;
    if (specs.safety) return 4;
    if (specs.operations && specs.operations.candidates && specs.operations.candidates.length > 0) return 3;
    if (specs.operations) return 2;
    if (record.pre_state_evidence && record.pre_state_evidence.length > 0) return 2;
    return 1;
  }

  setLifecycleStep(currentStep) {
    if (!this.dom.stageSteps) return;
    const connectors = document.querySelectorAll(".stage-connector");

    this.dom.stageSteps.forEach((step) => {
      const stepIdx = parseInt(step.getAttribute("data-stage") || "1");
      step.classList.remove("active", "completed");
      const dot = step.querySelector(".stage-dot");
      
      if (stepIdx === currentStep) {
        step.classList.add("active");
        if (dot) dot.textContent = stepIdx.toString().padStart(2, "0");
      } else if (stepIdx < currentStep) {
        step.classList.add("completed");
        if (dot) dot.textContent = "✓";
      } else {
        if (dot) dot.textContent = stepIdx.toString().padStart(2, "0");
      }
    });

    connectors.forEach((conn, idx) => {
      if (idx + 1 < currentStep) {
        conn.classList.add("completed");
      } else {
        conn.classList.remove("completed");
      }
    });
  }

  renderLifecycleStages(record) {
    const currentStep = this.computeLifecycleStep(record);
    this.setLifecycleStep(currentStep);
  }

  renderApprovalGate(record) {
    if (!this.dom.approvalContainer) return;
    
    if (record.status === "PENDING_APPROVAL") {
      this.dom.approvalContainer.style.display = "block";
      this.dom.approvalContainer.innerHTML = `
        <div class="card-panel" style="border: 1px solid var(--color-amber); background: rgba(255, 181, 71, 0.02); margin-bottom: 24px;">
          <div class="panel-header">
            <div class="panel-title">
              <span class="title-indicator" style="background: var(--color-amber);"></span>
              ⚠️ Human Operator Review Needed (Authorization Checkpoint)
            </div>
          </div>
          <div class="panel-body">
            <p style="font-size: 12px; margin-bottom: 12px;">
              An automatic incident mitigation plan has been calculated and verified in sandbox. Operator sign-off is required.
            </p>
            <div style="display: flex; flex-direction: column; gap: 8px;">
              <textarea id="operator-reason" placeholder="Add optional sign-off comment or override rationale..." 
                        style="background: var(--bg-primary); border: 1px solid var(--border-subtle); color: #fff; padding: 10px; border-radius: 6px; font-family: var(--font-sans); font-size: 11px; width: 100%; min-height: 50px; resize: vertical;"></textarea>
              <div style="display: flex; gap: 10px; justify-content: flex-end;">
                <button id="btn-reject-action" class="btn-action" style="border-color: var(--color-rose); color: var(--color-rose); padding: 8px 16px;">
                  ✕ Reject Action
                </button>
                <button id="btn-execute-approval" class="btn-primary" style="padding: 8px 16px;">
                  ✔ Authorize & Execute
                </button>
              </div>
            </div>
          </div>
        </div>
      `;

      // Attach event listeners
      document.getElementById("btn-execute-approval").addEventListener("click", async () => {
        const reason = document.getElementById("operator-reason").value;
        await this.submitApproval(true, reason, record.incident_id);
      });
      document.getElementById("btn-reject-action").addEventListener("click", async () => {
        const reason = document.getElementById("operator-reason").value;
        await this.submitApproval(false, reason, record.incident_id);
      });
    } else {
      this.dom.approvalContainer.style.display = "none";
      this.dom.approvalContainer.innerHTML = "";
    }
  }

  renderSandboxMatrix(record) {
    if (!this.dom.matrixBody) return;
    const safetyRes = (record.specialist_results || {}).safety || {};
    const evidenceList = safetyRes.evidence || [];
    const recAction = record.recommended_action || {};

    if (!evidenceList || evidenceList.length === 0) {
      this.dom.matrixBody.innerHTML = `
        <tr><td colspan="7" class="empty-cell">No candidate simulations run yet.</td></tr>
      `;
      return;
    }

    this.dom.matrixBody.innerHTML = evidenceList
      .map((ev) => {
        const action = ev.action || {};
        const cid = action.candidate_id || "C00";
        const atype = action.action_type || "";
        const isOptimal = (cid === recAction.candidate_id) || (action.action_type === recAction.action_type && JSON.stringify(action.parameters) === JSON.stringify(recAction.parameters));
        
        const peakTemp = typeof ev.predicted_peak_temp === "number" 
          ? ev.predicted_peak_temp 
          : (typeof ev.predicted_temp_t04 === "number" ? ev.predicted_temp_t04 : (typeof ev.predicted_temp_t01 === "number" ? ev.predicted_temp_t01 : 25.0));
        
        const isStable = ev.is_stable === true;
        const hospService = typeof ev.critical_hospital_service_pct === "number" 
          ? ev.critical_hospital_service_pct 
          : 100.0;
        
        const safetyMargin = typeof ev.safety_margin_c === "number" 
          ? ev.safety_margin_c 
          : Math.max(0, 110.0 - peakTemp);
        
        const isPassed = ev.action_valid && isStable && (!ev.violations || ev.violations.length === 0);
        const verdictTag = isPassed ? "PASSED" : (ev.action_valid === false ? "REJECTED" : "FAILED");
        const verdictClass = isPassed ? "pass" : "fail";

        return `
          <tr style="${isOptimal ? 'background: rgba(52, 231, 161, 0.02); font-weight: 600;' : ''}">
            <td>
              ${isOptimal ? '🌟 ' : ''}<strong>${this.escapeHtml(cid)}</strong> <span style="font-size: 9px; color: var(--text-secondary);">(${this.escapeHtml(atype)})</span>
            </td>
            <td style="font-family: var(--font-mono);">${peakTemp.toFixed(1)}°C</td>
            <td>
              <span class="verdict-tag ${isStable ? 'pass' : 'fail'}">
                ${isStable ? 'STABLE' : 'UNSTABLE'}
              </span>
            </td>
            <td style="font-family: var(--font-mono);">${hospService.toFixed(1)}%</td>
            <td style="font-family: var(--font-mono);">${safetyMargin.toFixed(1)}°C</td>
            <td>
              <span class="verdict-tag ${verdictClass}">${verdictTag}</span>
            </td>
            <td style="color: var(--text-secondary); max-width: 200px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap;" title="${this.escapeHtml(JSON.stringify(action.parameters || {}))}">
              ${this.escapeHtml(JSON.stringify(action.parameters || {}))}
            </td>
          </tr>
        `;
      })
      .join("");
  }

  renderSpecialists(record) {
    if (!this.dom.specialistsContainer) return;
    const results = record.specialist_results || {};
    const roles = ["operations", "safety", "planning"];

    this.dom.specialistsContainer.innerHTML = roles
      .map((role) => {
        const r = results[role] || {};
        const roleLabel = role.charAt(0).toUpperCase() + role.slice(1) + " Specialist";
        
        let statusBadgeClass = "pill-standby";
        let statusText = "STANDBY";
        if (r.status === "RESOLVED" || r.status === "PASSED" || r.status === "ACCEPT") {
          statusBadgeClass = "pill-nominal";
          statusText = r.status;
        } else if (r.status === "FAILED" || r.status === "VIOLATION" || r.status === "REJECT" || r.status === "ESCALATE") {
          statusBadgeClass = "pill-critical";
          statusText = r.status;
        } else if (r.status) {
          statusText = r.status;
        }

        return `
          <div class="specialist-card">
            <div class="spec-header">
              <span class="spec-name">${roleLabel}</span>
              <span class="pill-badge ${statusBadgeClass}">${statusText}</span>
            </div>
            <div class="spec-finding">
              ${r.finding ? this.escapeHtml(r.finding) : '<span class="muted-text">Awaiting input.</span>'}
            </div>
          </div>
        `;
      })
      .join("");
  }

  renderRecommendation(record) {
    if (!this.dom.recommendationBox) return;
    const rec = record.recommended_action;
    if (!rec) {
      this.dom.recommendationBox.innerHTML = '<span class="muted-text">No operational recommendation synthesized yet.</span>';
      return;
    }

    const candidateId = rec.candidate_id || rec.action_id || "ACTION";
    const actionType = rec.action_type || "dispatch";
    const rationale = (record.specialist_results && record.specialist_results.planning && record.specialist_results.planning.finding) 
      || record.recommendation_rationale 
      || "Deterministic multi-objective optimization plan verified by safety sandbox.";

    this.dom.recommendationBox.innerHTML = `
      <div style="font-family: var(--font-mono); font-size: 11px; display: flex; flex-direction: column; gap: 8px;">
        <div>
          <span style="color: var(--color-mint); font-weight: 700;">RECOMMENDED ACTION:</span>
          <strong>${this.escapeHtml(candidateId)}</strong> (${this.escapeHtml(actionType)})
        </div>
        <div>
          <span style="color: var(--text-secondary);">RATIONALE:</span>
          <span style="color: var(--text-primary); font-family: var(--font-sans);">${this.escapeHtml(rationale)}</span>
        </div>
        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-subtle); padding: 8px; border-radius: 4px;">
          <span style="color: var(--text-muted);">DISPATCH PARAMETERS:</span>
          <code style="color: var(--color-blue);">${this.escapeHtml(JSON.stringify(rec.parameters || {}))}</code>
        </div>
      </div>
    `;
  }

  renderPostVerification(record) {
    if (!this.dom.postVerificationBox) return;
    const isVerified = record.status === "VERIFIED";
    const verif = record.verification || {};
    const apprv = record.approval || {};
    const statusEl = this.dom.incidentStatus;
    const verifBadge = document.getElementById("verification-status-badge");

    if (isVerified && (verif.verified || verif.post_state_stable !== undefined)) {
      if (statusEl) statusEl.className = "pill-badge pill-nominal";
      if (verifBadge) {
        verifBadge.style.display = "inline-block";
        verifBadge.className = "pill-badge pill-nominal";
        verifBadge.textContent = "VERIFIED";
      }

      const activeViols = Array.isArray(verif.active_violations) ? verif.active_violations : [];
      const approverName = apprv.approved_by || "Operator";
      const reasonStr = apprv.reason || "Authorized by operator";

      this.dom.postVerificationBox.innerHTML = `
        <div style="font-family: var(--font-mono); font-size: 11px; display: flex; flex-direction: column; gap: 8px;">
          <div>
            <span style="color: var(--color-mint); font-weight: 700;">✔ STATE VERIFIED STABLE:</span>
            Grid physically confirmed stable. Post-execution verification passed.
          </div>
          <div class="diag-grid" style="background: rgba(52, 231, 161, 0.02); border: 1px solid rgba(52, 231, 161, 0.1); padding: 10px; border-radius: 6px; margin-top: 4px;">
            <span class="diag-lbl">Stability Check</span>
            <span class="diag-val" style="color: var(--color-mint);">${verif.post_state_stable ? "CONFIRMED STABLE" : "UNSTABLE"}</span>
            
            <span class="diag-lbl">Remaining Violations</span>
            <span class="diag-val" style="color: ${activeViols.length === 0 ? 'var(--color-mint)' : 'var(--color-rose)'};">${activeViols.length} active</span>

            <span class="diag-lbl">Authorized By</span>
            <span class="diag-val">${this.escapeHtml(approverName)}</span>

            <span class="diag-lbl">Audit Timestamp</span>
            <span class="diag-val">${apprv.timestamp ? this.formatTime(apprv.timestamp) : (record.updated_at ? this.formatTime(record.updated_at) : 'Logged')}</span>
          </div>
          <div style="font-family: var(--font-sans); color: var(--text-secondary); margin-top: 4px; font-size: 11px;">
            Operator Rationale: "${this.escapeHtml(reasonStr)}"
          </div>
        </div>
      `;
    } else if (record.status === "REJECTED_BY_HUMAN") {
      if (statusEl) statusEl.className = "pill-badge pill-critical";
      if (verifBadge) {
        verifBadge.style.display = "inline-block";
        verifBadge.className = "pill-badge pill-critical";
        verifBadge.textContent = "REJECTED";
      }

      this.dom.postVerificationBox.innerHTML = `
        <div style="color: var(--color-rose); font-size: 11px;">
          ✕ Incident mitigation action was explicitly rejected by the human operator. System remains in safety fallback state.
          ${apprv.reason ? `<div style="color: var(--text-secondary); margin-top: 4px;">Operator Comment: "${this.escapeHtml(apprv.reason)}"</div>` : ""}
        </div>
      `;
    } else if (record.status === "NO_SAFE_ACTION") {
      if (statusEl) statusEl.className = "pill-badge pill-critical";
      if (verifBadge) {
        verifBadge.style.display = "inline-block";
        verifBadge.className = "pill-badge pill-critical";
        verifBadge.textContent = "ESCALATED";
      }

      this.dom.postVerificationBox.innerHTML = `
        <div style="color: var(--color-rose); font-size: 11px;">
          ⚠️ NO SAFE ACTION AVAILABLE: All candidates breached critical physical safety margins. Grid escalated to manual control room intervention.
        </div>
      `;
    } else {
      if (verifBadge) verifBadge.style.display = "none";
      this.dom.postVerificationBox.innerHTML = `
        <span class="muted-text">Awaiting human operator authorization. Live action not executed yet.</span>
      `;
    }
  }

  async renderActivityEvents(incidentId) {
    try {
      const res = await fetch(`/api/events/${encodeURIComponent(incidentId)}`);
      if (!res.ok) return;
      const data = await res.json();
      this.cachedEvents = data.events || [];
      this.renderActivityList();
    } catch (err) {
      console.warn("Error fetching incident events:", err);
    }
  }

  renderActivityList() {
    if (!this.dom.activityFeed) return;
    const events = this.cachedEvents;
    if (events.length === 0) {
      this.dom.activityFeed.innerHTML = `
        <div class="muted-feed-state">
          No tool execution logs or reasoning events reported for this incident.
        </div>
      `;
      if (this.dom.activityToggleWrap) this.dom.activityToggleWrap.style.display = "none";
      return;
    }

    const maxDefault = 5;
    const displayedEvents = this.isActivityFeedExpanded ? events : events.slice(0, maxDefault);

    this.dom.activityFeed.innerHTML = displayedEvents
      .map((ev) => {
        let nodeClass = "node-mint";
        let badgeClass = "pill-nominal";
        const tag = (ev.event_type || ev.stage || ev.tag || "EVENT").toUpperCase().replace(/_/g, " ");
        const status = ev.status || "success";
        const summary = ev.summary || ev.message || ev.details || "";

        if (status === "rejected" || status === "failed" || status === "VIOLATION" || tag.includes("REJECT") || tag.includes("FAIL")) {
          nodeClass = "node-rose";
          badgeClass = "pill-critical";
        } else if (status === "pending" || tag.includes("APPROVAL") || tag.includes("STANDBY")) {
          nodeClass = "node-amber";
          badgeClass = "pill-standby";
        }

        return `
          <div class="timeline-item">
            <div class="timeline-node ${nodeClass}"></div>
            <div class="timeline-header">
              <div class="timeline-title-wrap">
                <span class="pill-badge ${badgeClass}">${tag}</span>
                <span style="font-family: var(--font-mono); font-size: 9px; color: var(--text-muted);">${ev.stage ? `[${ev.stage.toUpperCase()}]` : ''}</span>
              </div>
              <span class="timeline-time">${this.formatTime(ev.timestamp)}</span>
            </div>
            <div class="timeline-msg">${this.escapeHtml(summary)}</div>
          </div>
        `;
      })
      .join("");

    if (this.dom.activityToggleWrap && this.dom.btnToggleActivity) {
      if (events.length > maxDefault) {
        this.dom.activityToggleWrap.style.display = "block";
        this.dom.btnToggleActivity.textContent = this.isActivityFeedExpanded
          ? `Show recent ${maxDefault} only ↑`
          : `Show full activity log (${events.length} events) ↓`;
      } else {
        this.dom.activityToggleWrap.style.display = "none";
      }
    }
  }

  async fetchAuditHistory() {
    try {
      const statusFilter = this.dom.auditFilter ? this.dom.auditFilter.value : "";
      let url = `/api/audit/records?limit=${this.auditPageSize}&offset=${this.auditOffset}`;
      if (statusFilter) {
        url += `&status=${encodeURIComponent(statusFilter)}`;
      }
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      this.cachedRecords = data.records || [];
      this.renderAuditList();
    } catch (err) {
      console.warn("Audit fetch error:", err);
    }
  }

  renderAuditList() {
    if (!this.dom.auditHistoryList) return;
    const records = this.cachedRecords;
    if (records.length === 0) {
      this.dom.auditHistoryList.innerHTML = '<span class="muted-text">No audit records found.</span>';
      if (this.dom.auditToggleWrap) this.dom.auditToggleWrap.style.display = "none";
      return;
    }

    const maxDefault = 4;
    const displayedRecords = this.isAuditHistoryExpanded ? records : records.slice(0, maxDefault);

    this.dom.auditHistoryList.innerHTML = displayedRecords
      .map((r) => {
        const isSelected = r.incident_id === (this.selectedRecordId || this.activeIncidentId);
        let statusBadgeClass = "pill-standby";
        if (r.status === "VERIFIED") statusBadgeClass = "pill-nominal";
        else if (r.status === "REJECTED_BY_HUMAN" || r.status === "NO_SAFE_ACTION") statusBadgeClass = "pill-critical";

        return `
          <div class="audit-history-item ${isSelected ? "active-item" : ""}" data-id="${r.incident_id}">
            <div>
              <strong>${this.escapeHtml(r.incident_id)}</strong>
              <span style="color: var(--text-muted); font-size: 10px; margin-left: 6px;">${r.scenario_id}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 6px;">
              <span class="pill-badge ${statusBadgeClass}">${r.status}</span>
              <span style="font-size: 9px; color: var(--text-muted);">${this.formatTime(r.updated_at)}</span>
            </div>
          </div>
        `;
      })
      .join("");

    if (this.dom.auditToggleWrap && this.dom.btnToggleAudit) {
      if (records.length > maxDefault) {
        this.dom.auditToggleWrap.style.display = "block";
        this.dom.btnToggleAudit.textContent = this.isAuditHistoryExpanded
          ? `Show recent ${maxDefault} only ↑`
          : `Show full audit history (${records.length} records) ↓`;
      } else {
        this.dom.auditToggleWrap.style.display = "none";
      }
    }

    // Bind click on historical records to enter history mode
    this.dom.auditHistoryList.querySelectorAll(".audit-history-item").forEach((item) => {
      item.addEventListener("click", async () => {
        const targetId = item.getAttribute("data-id");
        if (targetId) {
          await this.selectHistoricalRecord(targetId);
        }
      });
    });
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
      if (this.dom.incidentStatus) {
        this.dom.incidentStatus.textContent = rec.status;
        this.dom.incidentStatus.className = `pill-badge ${rec.status === 'VERIFIED' ? 'pill-nominal' : rec.status.includes('REJECT') ? 'pill-critical' : 'pill-standby'}`;
      }

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
        <span class="pill-badge pill-standby">HISTORICAL RECORD</span>
        <span style="font-size: 11px; color: var(--text-primary);">
          Inspecting record <strong>${this.escapeHtml(record.incident_id)}</strong> (${record.scenario_id} · ${record.status}).
        </span>
      </div>
      <button id="btn-return-live" class="btn-primary" style="padding: 4px 12px; font-size: 10px;">
        ↩ Return to Live
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
    this.setLifecycleStep(1);
    
    // Clear approval container
    if (this.dom.approvalContainer) {
      this.dom.approvalContainer.style.display = "none";
      this.dom.approvalContainer.innerHTML = "";
    }
    
    // Reset sandbox matrix
    if (this.dom.matrixBody) {
      this.dom.matrixBody.innerHTML = `
        <tr><td colspan="7" class="empty-cell">
          Grid in nominal standby. Select a scenario and click "Analyze Incident".
        </td></tr>
      `;
    }

    // Reset specialists
    if (this.dom.specialistsContainer) {
      this.dom.specialistsContainer.innerHTML = `
        <div class="muted-text" style="grid-column: span 3; text-align: center; padding: 20px;">
          System in nominal standby. No active specialist investigations.
        </div>
      `;
    }

    // Reset recommendation
    if (this.dom.recommendationBox) {
      this.dom.recommendationBox.innerHTML = '<span class="muted-text">No active operational recommendation.</span>';
    }

    // Reset post-verification
    if (this.dom.postVerificationBox) {
      this.dom.postVerificationBox.innerHTML = '<span class="muted-text">No active incident. Grid operating in nominal state.</span>';
    }

    // Reset violations
    if (this.dom.incidentViolations) {
      this.dom.incidentViolations.innerHTML = '<span class="muted-text">Zero active violations.</span>';
    }
    if (this.dom.violationsCountBadge) {
      this.dom.violationsCountBadge.style.display = "none";
    }

    // Reset hero incident banner
    if (this.dom.heroBanner) {
      this.dom.heroBanner.style.display = "none";
    }

    // Reset incident ID
    if (this.dom.incidentTitle) {
      this.dom.incidentTitle.textContent = "Incident: STANDBY";
    }

    // Reset verification badge
    const verifBadge = document.getElementById("verification-status-badge");
    if (verifBadge) verifBadge.style.display = "none";

    // Reset activity stream
    this.cachedEvents = [];
    this.renderActivityList();
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
