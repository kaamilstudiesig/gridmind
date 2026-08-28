/**
 * GridMind Command Center — Interactive Dashboard Application Logic
 * 
 * Manages WebSocket real-time telemetry, interactive SVG topology visualization,
 * multi-agent pipeline orchestration, plan comparison, and human approval checkpoints.
 */

(() => {
  // Application State
  const state = {
    grid: null,
    incident: null,
    status: null,
    inspectedComponent: null,
    ws: null,
    isInvestigating: false,
  };

  // DOM Elements
  const dom = {
    // Header
    valSystemStatus: document.getElementById('val-system-status'),
    valLifecycleState: document.getElementById('val-lifecycle-state'),
    valAgentStatus: document.getElementById('val-agent-status'),
    pillSystem: document.getElementById('pill-system'),
    pillIncidentState: document.getElementById('pill-incident-state'),
    pillAiAgent: document.getElementById('pill-ai-agent'),
    scenarioSelect: document.getElementById('scenario-select'),
    btnLoadScenario: document.getElementById('btn-load-scenario'),
    btnRunInvestigation: document.getElementById('btn-run-investigation'),

    // Telemetry
    valFrequency: document.getElementById('val-frequency'),
    barFrequency: document.getElementById('bar-frequency'),
    badgeFreq: document.getElementById('badge-freq'),
    cardFreqTag: document.getElementById('card-freq-tag'),
    valTotalDemand: document.getElementById('val-total-demand'),
    barDemand: document.getElementById('bar-demand'),
    valDemandMultiplier: document.getElementById('val-demand-multiplier'),
    valStability: document.getElementById('val-stability'),
    valStabilityDesc: document.getElementById('val-stability-desc'),
    badgeViolations: document.getElementById('badge-violations'),
    valCriticalService: document.getElementById('val-critical-service'),
    badgeCritical: document.getElementById('badge-critical'),
    vCount: document.getElementById('v-count'),
    violationsList: document.getElementById('violations-list'),

    // Topology Footer
    inspectedId: document.getElementById('inspected-id'),
    inspType: document.getElementById('insp-type'),
    inspStatus: document.getElementById('insp-status'),
    inspLoading: document.getElementById('insp-loading'),
    inspTemp: document.getElementById('insp-temp'),
    inspViolations: document.getElementById('insp-violations'),

    // Agent Tracker
    stepAnalyst: document.getElementById('step-analyst'),
    stepSimulator: document.getElementById('step-simulator'),
    stepSafety: document.getElementById('step-safety'),
    statusAnalyst: document.getElementById('status-analyst'),
    statusSimulator: document.getElementById('status-simulator'),
    statusSafety: document.getElementById('status-safety'),
    descAnalyst: document.getElementById('desc-analyst'),
    descSimulator: document.getElementById('desc-simulator'),
    descSafety: document.getElementById('desc-safety'),

    // Plans & Approval Gate
    plansCardsList: document.getElementById('plans-cards-list'),
    approvalGateCard: document.getElementById('approval-gate-card'),
    gateActionName: document.getElementById('gate-action-name'),
    gateActionType: document.getElementById('gate-action-type'),
    gateActionParams: document.getElementById('gate-action-params'),
    gatePredictedStability: document.getElementById('gate-predicted-stability'),
    gateSafetyVerdict: document.getElementById('gate-safety-verdict'),
    gateCriticalImpact: document.getElementById('gate-critical-impact'),
    gateRiskLevel: document.getElementById('gate-risk-level'),
    gateReasoningText: document.getElementById('gate-reasoning-text'),
    btnApproveAction: document.getElementById('btn-approve-action'),
    btnRejectAction: document.getElementById('btn-reject-action'),

    // Verification Box
    verificationBox: document.getElementById('verification-box'),
    verifBadge: document.getElementById('verif-badge'),
    verifMessage: document.getElementById('verif-message'),

    // Timeline
    timelineStream: document.getElementById('timeline-stream'),
    streamStatus: document.getElementById('stream-status'),
  };

  // =====================================================================
  // Initialization & Network Connections
  // =====================================================================

  async function init() {
    setupEventListeners();
    await fetchInitialData();
    connectWebSocket();
  }

  function setupEventListeners() {
    // Load Scenario
    dom.btnLoadScenario.addEventListener('click', async () => {
      const scenarioId = dom.scenarioSelect.value;
      try {
        dom.btnLoadScenario.disabled = true;
        const res = await fetch('/api/scenario/load', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scenario_id: scenarioId }),
        });
        if (!res.ok) throw new Error(await res.text());
        await refreshAll();
      } catch (err) {
        alert(`Failed to load scenario: ${err.message}`);
      } finally {
        dom.btnLoadScenario.disabled = false;
      }
    });

    // Run AI Investigation
    dom.btnRunInvestigation.addEventListener('click', async () => {
      try {
        dom.btnRunInvestigation.disabled = true;
        setAgentTrackerState('INVESTIGATING');
        const res = await fetch('/api/incident/investigate', { method: 'POST' });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        state.incident = data;
        renderIncident(data);
        await refreshGridState();
      } catch (err) {
        alert(`Investigation failed: ${err.message}`);
      } finally {
        dom.btnRunInvestigation.disabled = false;
      }
    });

    // Approve Action
    dom.btnApproveAction.addEventListener('click', async () => {
      try {
        dom.btnApproveAction.disabled = true;
        dom.btnRejectAction.disabled = true;
        showVerificationState('EXECUTING & VERIFYING...', 'Executing approved action on live grid and re-evaluating power flow...');
        
        const res = await fetch('/api/incident/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        state.incident = data;
        renderIncident(data);
        await refreshGridState();
      } catch (err) {
        alert(`Approval execution failed: ${err.message}`);
      } finally {
        dom.btnApproveAction.disabled = false;
        dom.btnRejectAction.disabled = false;
      }
    });

    // Reject Action
    dom.btnRejectAction.addEventListener('click', async () => {
      const reason = prompt('Enter rejection reason for AI Incident Commander:', 'Operator requested alternate low-risk strategy');
      if (!reason) return;
      try {
        dom.btnRejectAction.disabled = true;
        const res = await fetch('/api/incident/reject', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason }),
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        state.incident = data;
        renderIncident(data);
        await refreshGridState();
      } catch (err) {
        alert(`Rejection failed: ${err.message}`);
      } finally {
        dom.btnRejectAction.disabled = false;
      }
    });

    // Node Inspection in SVG Diagram
    setupTopologyInteractions();
  }

  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      dom.streamStatus.textContent = '● LIVE WS CONNECTED';
      dom.streamStatus.style.color = 'var(--status-green)';
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleWebSocketMessage(msg);
      } catch (e) {
        console.error('Error parsing WS message:', e);
      }
    };

    ws.onclose = () => {
      dom.streamStatus.textContent = '○ WS RECONNECTING...';
      dom.streamStatus.style.color = 'var(--status-amber)';
      setTimeout(connectWebSocket, 3000);
    };

    state.ws = ws;
  }

  function handleWebSocketMessage(msg) {
    if (msg.type === 'INITIAL_STATE' || msg.type === 'SCENARIO_LOADED' || msg.type === 'INVESTIGATION_COMPLETED' || msg.type === 'ACTION_APPROVED_AND_EXECUTED' || msg.type === 'ACTION_REJECTED_REPLANNED') {
      if (msg.grid_state) {
        state.grid = msg.grid_state;
        renderGridState(msg.grid_state);
      }
      if (msg.incident) {
        state.incident = msg.incident;
        renderIncident(msg.incident);
      }
    }
  }

  async function fetchInitialData() {
    try {
      const [gridRes, incRes, statRes, timeRes] = await Promise.all([
        fetch('/api/grid-state').then(r => r.json()),
        fetch('/api/incident').then(r => r.json()),
        fetch('/api/status').then(r => r.json()),
        fetch('/api/timeline').then(r => r.json()),
      ]);

      state.grid = gridRes;
      state.incident = incRes;
      state.status = statRes;

      renderGridState(gridRes);
      renderIncident(incRes);
      renderTimeline(timeRes);
    } catch (err) {
      console.error('Failed to load initial data:', err);
    }
  }

  async function refreshGridState() {
    try {
      const grid = await fetch('/api/grid-state').then(r => r.json());
      state.grid = grid;
      renderGridState(grid);
    } catch (e) {
      console.error('Error refreshing grid state:', e);
    }
  }

  async function refreshAll() {
    await fetchInitialData();
  }

  // =====================================================================
  // Rendering Telemetry & Grid State
  // =====================================================================

  function renderGridState(grid) {
    if (!grid) return;

    // Frequency
    const freq = grid.frequency_hz || 50.0;
    dom.valFrequency.textContent = freq.toFixed(4);
    dom.cardFreqTag.textContent = `${freq.toFixed(4)} Hz`;
    
    // Frequency Bar (range 49.0 - 51.0 -> 0% to 100%)
    const pct = Math.max(0, Math.min(100, ((freq - 49.0) / 2.0) * 100));
    dom.barFrequency.style.width = `${pct}%`;

    if (freq < 49.5 || freq > 50.5) {
      dom.badgeFreq.textContent = 'OUT OF BOUNDS';
      dom.badgeFreq.className = 'metric-badge badge-fail';
    } else {
      dom.badgeFreq.textContent = 'NOMINAL';
      dom.badgeFreq.className = 'metric-badge safe';
    }

    // Total Demand
    const demandKw = grid.total_demand_kw || 1258.75;
    dom.valTotalDemand.textContent = demandKw.toFixed(1);
    dom.valDemandMultiplier.textContent = `MULT: ${(grid.demand_multiplier || 1.0).toFixed(2)}x`;
    const demandPct = Math.min(100, (demandKw / 1800.0) * 100);
    dom.barDemand.style.width = `${demandPct}%`;

    // Stability
    const isStable = grid.is_stable && (!grid.active_violations || grid.active_violations.length === 0);
    dom.valStability.textContent = isStable ? 'STABLE' : 'UNSTABLE';
    dom.valStability.className = `metric-state ${isStable ? '' : 'unstable'}`;
    dom.badgeViolations.textContent = `${(grid.active_violations || []).length} VIOLATIONS`;
    dom.badgeViolations.className = `metric-badge ${isStable ? 'safe' : 'badge-fail'}`;

    if (isStable) {
      dom.valStabilityDesc.textContent = 'All electrical, frequency droop, and thermal limits strictly satisfied.';
    } else {
      dom.valStabilityDesc.textContent = 'Hard constraint breach detected. Immediate incident response required.';
    }

    // Violations List
    const violations = grid.active_violations || [];
    dom.vCount.textContent = `${violations.length} ACTIVE`;
    if (violations.length === 0) {
      dom.violationsList.innerHTML = '<div class="empty-notice">Zero active constraint violations. Network operating within nominal design envelope.</div>';
    } else {
      dom.violationsList.innerHTML = violations.map(v => `
        <div class="violation-item">
          <div class="v-target">${v.violation_type} • [${v.target_id}]</div>
          <div class="v-desc">${v.description}</div>
        </div>
      `).join('');
    }

    // Critical Hospital Service
    const lz04 = (grid.load_zones || []).find(lz => lz.load_id === 'LZ04');
    if (lz04) {
      const servedPct = lz04.current_demand_kw > 0 ? (lz04.served_kw / lz04.current_demand_kw) * 100 : 100;
      dom.valCriticalService.textContent = servedPct.toFixed(1);
    }

    // Update SVG Diagram
    renderSvgTopology(grid);
  }

  function renderSvgTopology(grid) {
    if (!grid) return;

    // Lines
    (grid.lines || []).forEach(l => {
      const linePath = document.getElementById(`svg-line-${l.line_id}`);
      const lineLbl = document.getElementById(`lbl-${l.line_id}`);

      if (lineLbl) {
        lineLbl.textContent = `${l.line_id}: ${l.flow_kw.toFixed(1)} kW (${l.loading_pct.toFixed(0)}%)`;
      }

      if (linePath) {
        linePath.classList.remove('tripped', 'overloaded');
        if (l.status === 'tripped' || l.status === 'isolated') {
          linePath.classList.add('tripped');
          if (lineLbl) lineLbl.textContent = `${l.line_id}: TRIPPED / LOCKED OUT`;
        } else if (l.loading_pct > 100) {
          linePath.classList.add('overloaded');
        }
      }
    });

    // Feeder Transformers
    const tMap = {};
    (grid.transformers || []).forEach(t => { tMap[t.transformer_id] = t; });

    const tempN04 = document.getElementById('temp-N04');
    if (tempN04 && tMap['T01'] && tMap['T05']) {
      tempN04.textContent = `T01: ${tMap['T01'].temperature_c.toFixed(1)}°C | T05: ${tMap['T05'].temperature_c.toFixed(1)}°C`;
    }

    const tempN05 = document.getElementById('temp-N05');
    if (tempN05 && tMap['T02'] && tMap['T04']) {
      tempN05.textContent = `T02: ${tMap['T02'].temperature_c.toFixed(1)}°C | T04: ${tMap['T04'].temperature_c.toFixed(1)}°C`;
      tempN05.style.fill = tMap['T04'].temperature_c > 110.0 ? '#ef4444' : '#cbd5e1';
    }

    const tempN06 = document.getElementById('temp-N06');
    if (tempN06 && tMap['T03']) {
      tempN06.textContent = `T03: ${tMap['T03'].temperature_c.toFixed(1)}°C`;
    }

    // Load Zones
    (grid.load_zones || []).forEach(lz => {
      const loadLbl = document.getElementById(`load-${lz.load_id}`);
      if (loadLbl) {
        loadLbl.textContent = `${lz.load_id}: ${lz.served_kw.toFixed(1)} kW`;
      }
    });
  }

  function setupTopologyInteractions() {
    const nodes = document.querySelectorAll('.node-group');
    nodes.forEach(node => {
      node.addEventListener('click', () => {
        const id = node.id.replace('svg-node-', '');
        inspectComponent(id);
      });
    });
  }

  function inspectComponent(id) {
    if (!state.grid) return;

    dom.inspectedId.textContent = `SELECTED: ${id}`;
    const node = (state.grid.nodes || []).find(n => n.node_id === id);
    const lz = (state.grid.load_zones || []).find(l => l.node_id === id || l.load_id === id);
    const trans = (state.grid.transformers || []).filter(t => t.node_id === id);

    if (lz) {
      dom.inspType.textContent = `Load Zone (${lz.type.toUpperCase()})`;
      dom.inspStatus.textContent = lz.priority.toUpperCase();
      dom.inspLoading.textContent = `${lz.served_kw.toFixed(1)} kW / ${lz.current_demand_kw.toFixed(1)} kW`;
      dom.inspTemp.textContent = 'N/A';
      dom.inspViolations.textContent = lz.curtailment_pct > 0 ? `Curtailment: ${lz.curtailment_pct}%` : 'Normal';
    } else if (trans.length > 0) {
      dom.inspType.textContent = `Feeder Bus (${trans.map(t=>t.transformer_id).join(', ')})`;
      dom.inspStatus.textContent = trans.map(t=>t.status).join(' • ');
      dom.inspLoading.textContent = trans.map(t=>`${t.transformer_id}: ${t.load_pct.toFixed(0)}%`).join(' | ');
      dom.inspTemp.textContent = trans.map(t=>`${t.transformer_id}: ${t.temperature_c.toFixed(1)}°C`).join(' | ');
      dom.inspViolations.textContent = trans.some(t=>t.temperature_c > 110) ? 'THERMAL OVERHEAT' : 'None';
    } else if (node) {
      dom.inspType.textContent = `${node.node_type.toUpperCase()} (${node.voltage_kv} kV)`;
      dom.inspStatus.textContent = 'ONLINE';
      dom.inspLoading.textContent = 'Nominal';
      dom.inspTemp.textContent = 'N/A';
      dom.inspViolations.textContent = 'None';
    }
  }

  // =====================================================================
  // Rendering Incident Commander & Multi-Agent State
  // =====================================================================

  function renderIncident(incident) {
    if (!incident) return;

    // Header State
    dom.valLifecycleState.textContent = incident.state;
    if (incident.state === 'RESOLVED') {
      dom.pillIncidentState.className = 'status-pill healthy';
    } else if (incident.state === 'AWAITING_APPROVAL') {
      dom.pillIncidentState.className = 'status-pill active-ai';
    } else {
      dom.pillIncidentState.className = 'status-pill warning';
    }

    // Agent Tracker Steps
    updateAgentPipeline(incident);

    // Plans Cards
    renderPlanCards(incident.candidate_plans || [], incident.recommended_plan);

    // Approval Gate Card
    if (incident.state === 'AWAITING_APPROVAL' && incident.recommended_plan) {
      renderApprovalGate(incident.recommended_plan);
    } else {
      dom.approvalGateCard.classList.add('hidden');
    }

    // Verification Result
    if (incident.verification) {
      dom.verificationBox.classList.remove('hidden');
      if (incident.verification.passed) {
        dom.verifBadge.textContent = 'PASSED';
        dom.verifBadge.className = 'verif-badge';
        dom.verifBadge.style.background = 'rgba(16, 185, 129, 0.2)';
        dom.verifBadge.style.color = '#34d399';
      } else {
        dom.verifBadge.textContent = 'FAILED';
        dom.verifBadge.className = 'verif-badge';
        dom.verifBadge.style.background = 'rgba(239, 68, 68, 0.2)';
        dom.verifBadge.style.color = '#fca5a5';
      }
      dom.verifMessage.textContent = incident.verification.message;
    } else {
      dom.verificationBox.classList.add('hidden');
    }

    // Timeline Log
    if (incident.timeline) {
      renderTimeline(incident.timeline);
    }
  }

  function updateAgentPipeline(incident) {
    const s = incident.state;

    // Reset steps
    dom.stepAnalyst.className = 'agent-step';
    dom.stepSimulator.className = 'agent-step';
    dom.stepSafety.className = 'agent-step';

    if (s === 'INVESTIGATING' || s === 'ANALYZING') {
      dom.stepAnalyst.className = 'agent-step active';
      dom.statusAnalyst.textContent = 'ACTIVE';
      dom.descAnalyst.textContent = 'Analyzing root causes & telemetry';
      dom.valAgentStatus.textContent = 'ANALYZING';
    } else if (s === 'PLANNING' || s === 'SIMULATING') {
      dom.stepAnalyst.className = 'agent-step done';
      dom.statusAnalyst.textContent = 'DONE';
      dom.stepSimulator.className = 'agent-step active';
      dom.statusSimulator.textContent = 'ACTIVE';
      dom.descSimulator.textContent = 'Running counterfactual sandbox';
      dom.valAgentStatus.textContent = 'SIMULATING';
    } else if (s === 'SAFETY_REVIEW') {
      dom.stepAnalyst.className = 'agent-step done';
      dom.stepSimulator.className = 'agent-step done';
      dom.stepSafety.className = 'agent-step active';
      dom.statusSafety.textContent = 'ACTIVE';
      dom.descSafety.textContent = 'Verifying safety & constraints';
      dom.valAgentStatus.textContent = 'SAFETY CHECK';
    } else if (s === 'AWAITING_APPROVAL') {
      dom.stepAnalyst.className = 'agent-step done';
      dom.stepSimulator.className = 'agent-step done';
      dom.stepSafety.className = 'agent-step done';
      dom.statusAnalyst.textContent = 'DONE';
      dom.statusSimulator.textContent = 'DONE';
      dom.statusSafety.textContent = 'DONE';
      dom.valAgentStatus.textContent = 'AWAITING OPERATOR';
    } else if (s === 'RESOLVED') {
      dom.stepAnalyst.className = 'agent-step done';
      dom.stepSimulator.className = 'agent-step done';
      dom.stepSafety.className = 'agent-step done';
      dom.statusAnalyst.textContent = 'DONE';
      dom.statusSimulator.textContent = 'DONE';
      dom.statusSafety.textContent = 'DONE';
      dom.valAgentStatus.textContent = 'INCIDENT RESOLVED';
    }
  }

  function setAgentTrackerState(mode) {
    if (mode === 'INVESTIGATING') {
      dom.stepAnalyst.className = 'agent-step active';
      dom.statusAnalyst.textContent = 'RUNNING';
      dom.valAgentStatus.textContent = 'ACTIVE';
    }
  }

  function renderPlanCards(plans, recommendedPlan) {
    if (!plans || plans.length === 0) {
      dom.plansCardsList.innerHTML = '<div class="empty-notice">No active candidate plans. Click "Run AI Incident Commander" to trigger autonomous multi-agent analysis.</div>';
      return;
    }

    dom.plansCardsList.innerHTML = plans.map(p => {
      const isRec = recommendedPlan && p.plan_id === recommendedPlan.plan_id;
      const isPass = p.is_valid && p.is_stable;
      const maxTemp = p.transformer_temperatures_c ? Math.max(...Object.values(p.transformer_temperatures_c)) : 0;

      return `
        <div class="plan-card ${isRec ? 'recommended' : ''}">
          <div class="plan-card-header">
            <div class="plan-id-title">
              <span class="plan-id-badge mono">${p.plan_id}</span>
              <strong class="plan-title">${p.name}</strong>
            </div>
            ${isRec ? '<span class="rec-star-badge">★ RECOMMENDED</span>' : ''}
          </div>

          <p class="plan-desc">${p.description}</p>

          <div class="plan-grid">
            <div><span class="k">STABILITY</span><span class="v ${isPass ? 'pass' : 'fail'}">${p.is_stable ? 'PASS' : 'FAIL'}</span></div>
            <div><span class="k">RISK</span><span class="v ${p.risk_level === 'LOW' ? 'pass' : 'fail'}">${p.risk_level}</span></div>
            <div><span class="k">PEAK TEMP</span><span class="v ${maxTemp <= 110 ? 'pass' : 'fail'}">${maxTemp.toFixed(1)}°C</span></div>
            <div><span class="k">PRED FREQ</span><span class="v">${p.predicted_frequency_hz ? p.predicted_frequency_hz.toFixed(4) : '—'} Hz</span></div>
          </div>

          <div class="plan-score-row mono">
            <span>Score: <strong>${p.score.toFixed(1)}</strong></span>
            <span>Safety: <strong>${p.safety_approved ? 'APPROVED' : 'REJECTED'}</strong></span>
          </div>
        </div>
      `;
    }).join('');
  }

  function renderApprovalGate(recPlan) {
    dom.approvalGateCard.classList.remove('hidden');
    dom.gateActionName.textContent = recPlan.name;
    dom.gateActionType.textContent = recPlan.action_type;
    dom.gateActionParams.textContent = JSON.stringify(recPlan.parameters).replace(/[{}"]/g, '');
    dom.gatePredictedStability.textContent = recPlan.is_stable ? 'STABLE' : 'UNSTABLE';
    dom.gateSafetyVerdict.textContent = recPlan.safety_approved ? 'APPROVED' : 'REJECTED';
    dom.gateRiskLevel.textContent = `${recPlan.risk_level} RISK`;
    dom.gateReasoningText.textContent = recPlan.recommendation_reason || 'Grounded in deterministic simulation.';
  }

  function showVerificationState(title, message) {
    dom.approvalGateCard.classList.add('hidden');
    dom.verificationBox.classList.remove('hidden');
    dom.verifBadge.textContent = title;
    dom.verifBadge.style.background = 'rgba(0, 212, 255, 0.2)';
    dom.verifBadge.style.color = '#38bdf8';
    dom.verifMessage.textContent = message;
  }

  function renderTimeline(timeline) {
    if (!timeline || timeline.length === 0) return;

    dom.timelineStream.innerHTML = timeline.map(entry => {
      const date = new Date(entry.timestamp * 1000);
      const timeStr = date.toTimeString().split(' ')[0];
      return `
        <div class="log-entry">
          <span class="log-ts mono">${timeStr}</span>
          <span class="log-agent ${entry.agent}">${entry.agent}</span>
          <span class="log-msg">${entry.message}</span>
        </div>
      `;
    }).join('');
  }

  // Start app on DOMContentLoaded
  document.addEventListener('DOMContentLoaded', init);
})();
