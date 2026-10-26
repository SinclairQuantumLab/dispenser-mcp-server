"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let authorized = document.body.dataset.operatorAuthorized === "true";
  let runtime = null, saved = null, revision = null, check = null;
  let busy = false, dirty = false, pollPending = false, runEdited = false;
  const textFields = ["backend", "authority_mode", "acceptance_context", "expected_serial_number"];
  const numberFields = ["compliance_voltage_v", "max_load_current_A", "sample_interval_s"];
  const hardwareFields = ["hicube_host", "hicube_port", "hicube_timeout_s", "siglent_identifier", "siglent_timeout_s", "siglent_minimum_command_interval_ms"];
  const hardwareInput = field => $(field);
  const simulationFields = ["seed", "scenario"];
  const active = () => runtime?.active_run || null;
  const binding = () => active()?.run_binding || runtime?.run_binding || null;
  const say = (id, message, error = false) => {
    $(id).textContent = message;
    $(id).classList.toggle("error", error);
  };
  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {cache: "no-store", ...options});
    } catch (_) {
      throw new Error("No server response was received. The outcome may be unknown; inspect current status before repeating a request.");
    }
    if (response.status === 401 || response.status === 403) {
      authorized = false;
      document.body.dataset.operatorAuthorized = "false";
      updateButtons();
    }
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = result.error || result.detail || result.message || `HTTP ${response.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return result;
  }
  const mutate = (path, method, body) => request(path, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  function updateButtons() {
    const idle = runtime?.lifecycle === "idle" && !active();
    $("settings-fields").disabled = !authorized || !idle || busy || !saved;
    $("check-settings").disabled = !authorized || !idle || busy || !saved || dirty;
    $("start-run").disabled = !authorized || !idle || busy || dirty || !check;
    $("run-fields").disabled = !authorized || !active() || busy;
    $("stop-run").disabled = !authorized || !active() || busy || !$("stop-ack").checked;
    $("access-hint").hidden = authorized;
    say("settings-hint", !authorized ? "Log in to view and edit settings. Public status does not expose simulation setup or credentials." : active() ? "A run is active. Use the controls below for an authority/interval change. End this recording before changing its backend or instrument configuration." : "Save changes, check the saved configuration, then explicitly start a new recording. Nothing here enables output automatically.");
  }
  function backendVisibility() {
    $("hardware-settings").hidden = $("backend").value !== "hardware";
    $("simulation-settings").hidden = $("backend").value !== "simulation";
  }
  function renderSettings(value) {
    saved = value.settings || value.suggested;
    revision = value.revision;
    if (!saved) throw new Error("No editable configuration was returned by the server.");
    for (const field of [...textFields, ...numberFields]) $(field).value = saved[field] ?? "";
    $("control_enabled").checked = saved.control_enabled === true;
    for (const field of hardwareFields) hardwareInput(field).value = saved.hardware?.[field] ?? "";
    for (const field of simulationFields) $("simulation_" + field).value = saved.simulation?.[field] ?? "";
    say("auth-file-status", value.auth_file_present ? "Gateway authentication file is present. Its contents are not exposed to this page." : "No gateway authentication file was found. Configure it locally if your gateway requires one.");
    dirty = !revision; check = null;
    if (dirty) say("settings-message", "Initial suggestions only. Review and save these settings before checking or starting.");
    backendVisibility(); updateButtons();
  }
  function gatherSettings() {
    const result = structuredClone(saved);
    for (const field of textFields) result[field] = $(field).value.trim();
    for (const field of numberFields) result[field] = $(field).value === "" ? null : Number($(field).value);
    result.control_enabled = $("control_enabled").checked;
    result.hardware = {};
    for (const field of hardwareFields) {
      result.hardware[field] = field === "hicube_host" || field === "siglent_identifier" ? hardwareInput(field).value.trim() : Number(hardwareInput(field).value);
    }
    result.simulation = {};
    for (const field of simulationFields) result.simulation[field] = $("simulation_" + field).value.trim();
    return result;
  }
  function renderCapabilities(caps) {
    const target = $("capabilities");
    target.replaceChildren();
    if (!caps) {
      const note = document.createElement("p"); note.className = "muted";
      note.textContent = "No active backend capabilities. Start a checked run to see its available channels and controls.";
      target.append(note); return;
    }
    const note = document.createElement("p");
    note.textContent = active() ? "Built in: G1 total pressure and the configured parallel PSU's state/control tools. Additional registered channels are listed below." : "No active instrument backend. Optional adapters are not inferred from saved settings.";
    target.append(note);
    const groups = [["Additional scalar readings", caps.scalar_channels], ["Waveform captures", caps.trace_channels], ["Additional controls", caps.named_controls]];
    const list = document.createElement("ul");
    for (const [label, entries] of groups) {
      const item = document.createElement("li");
      item.textContent = `${label}: ${(entries || []).length ? entries.map(entry => entry.label || entry.channel_id || entry.control_id).join(", ") : "none registered"}`;
      list.append(item);
    }
    target.append(list);
    // The public descriptor is small; an expandable exact view avoids inventing
    // units or availability for adapter-specific future channel types.
    const detail = document.createElement("details"), summary = document.createElement("summary"), pre = document.createElement("pre");
    summary.textContent = "Registered capability details";
    pre.textContent = JSON.stringify(caps, null, 2);
    detail.append(summary, pre); target.append(detail);
  }
  function renderRuntime(value) {
    const oldBinding = binding();
    runtime = value;
    const run = active(), source = run?.backend;
    const authority = run?.authority_mode || value.authority_mode;
    const label = value.lifecycle === "idle" ? "Ready · no active run" : run ? (source === "simulation" ? "SIMULATION · recording" : "HARDWARE · recording") : value.lifecycle;
    $("runtime-title").textContent = label;
    $("runtime-strip").classList.toggle("hardware", Boolean(run && source === "hardware"));
    $("runtime-authority").textContent = run ? (authority === "human_learning" ? "Human · learning" : "Agent") : "No active run";
    $("runtime-detail").textContent = run ? `Recording: ${run.run_key || run.run_id || binding()}. This label does not indicate output ON.` : "The web console and MCP are available. No experiment is recording.";
    const sampler = run?.sampler || value.sampler;
    $("runtime-sampler").textContent = run ? (sampler?.running ? "Acquisition is running independently of agent calls." : "Acquisition status: " + (sampler?.running === false ? "not running" : "not reported")) : "Instruments are not sampled while idle.";
    $("active-summary").textContent = run ? "This recording stays the same when you change authority or sampling interval. Reconcile equipment state before resuming automated actions." : "No active recording. Previous runs remain available in the dashboard.";
    if (oldBinding !== binding()) {
      runEdited = false; check = null; $("stop-ack").checked = false;
    }
    if (run && !runEdited) {
      $("run-authority").value = authority;
      $("run-interval").value = sampler?.interval_s ?? "";
    }
    renderCapabilities(run?.capabilities || value.capabilities);
    updateButtons();
  }
  async function loadSettings() {
    if (!authorized) return;
    renderSettings(await request("/api/operator/settings"));
  }
  async function refresh() {
    if (pollPending) return;
    pollPending = true;
    try {
      renderRuntime(await request("/api/runtime"));
      say("runtime-error", "");
    } catch (error) {
      say("runtime-error", "Status unavailable. Last displayed values may be stale. " + error.message, true);
      runtime = null; updateButtons();
    } finally {pollPending = false;}
  }
  async function operation(messageId, work) {
    if (busy) return;
    busy = true; updateButtons();
    try {await work();} catch (error) {say(messageId, error.message, true);} finally {busy = false; await refresh(); updateButtons();}
  }
  $("settings-form").addEventListener("input", () => {
    dirty = true; check = null; backendVisibility();
    say("settings-message", "Unsaved changes. Save before checking or starting a run."); updateButtons();
  });
  $("settings-form").addEventListener("submit", event => {
    event.preventDefault();
    operation("settings-message", async () => {
      await mutate("/api/operator/settings", "PUT", {expected_revision: revision, settings: gatherSettings()});
      await loadSettings();
      say("settings-message", "Saved on the server. No output or recording was changed. Check this configuration next.");
      say("check-message", ""); $("check-detail").hidden = true;
    });
  });
  $("settings-reload").addEventListener("click", () => operation("settings-message", async () => {
    await loadSettings(); say("settings-message", "Loaded the saved configuration. Unsaved browser edits were discarded.");
  }));
  $("check-settings").addEventListener("click", () => operation("check-message", async () => {
    check = null; say("check-message", "Checking saved settings and read-only connections…");
    const result = await mutate("/api/operator/check", "POST", {revision});
    $("check-detail").textContent = JSON.stringify(result, null, 2); $("check-detail").hidden = false;
    check = result.check_id || null;
    say("check-message", check ? "Check passed. No recording or output change was made. You can now start a new run." : "Check did not pass. Correct the configuration and check again; the server remains available.", !check);
  }));
  $("start-run").addEventListener("click", () => operation("check-message", async () => {
    const checkId = check; check = null;
    say("check-message", "Starting a new recording. Outputs will not be changed…");
    await mutate("/api/operator/run/start", "POST", {revision, check_id: checkId});
    say("check-message", "Recording started. Open the dashboard for observations, notes, and explicit equipment controls.");
  }));
  for (const id of ["run-authority", "run-interval"]) $(id).addEventListener("input", () => {runEdited = true;});
  $("apply-run").addEventListener("click", () => operation("run-message", async () => {
    await mutate("/api/operator/run", "PATCH", {run_binding: binding(), authority_mode: $("run-authority").value, sample_interval_s: Number($("run-interval").value)});
    runEdited = false;
    await loadSettings();
    say("run-message", "Authority and acquisition interval applied. Outputs were not changed. Earlier undispatched actions must not be replayed.");
  }));
  $("stop-ack").addEventListener("change", updateButtons);
  $("stop-run").addEventListener("click", () => operation("run-message", async () => {
    await mutate("/api/operator/run/stop", "POST", {run_binding: binding(), acknowledge_no_automatic_output_change: true});
    say("run-message", "Recording ended. No output change was requested. This is not a conditioning-success declaration.");
    await loadSettings();
  }));
  document.addEventListener("dashboard-login-success", async () => {
    authorized = true; updateButtons();
    try {await loadSettings();} catch (error) {say("settings-message", error.message, true);}
    await refresh();
  });
  backendVisibility(); updateButtons();
  refresh();
  loadSettings().catch(error => say("settings-message", error.message, true));
  setInterval(refresh, 5000);
})();
