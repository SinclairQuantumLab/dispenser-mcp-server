"use strict";
// Human controls are isolated from plotting. Browser reads never acquire or actuate.
(() => {
  const find = id => document.getElementById(id);
  const run = new URLSearchParams(location.search).get("run") || "";
  let state = null, busy = false, loading = false, auth = document.body.dataset.operatorAuthorized === "true";
  let authRevision = 0, notes = [], reviews = [], noteAfter = 0, reviewAfter = 0, timer;
  let requestedReview = new URLSearchParams(location.search).get("learning_review");
  const make = (tag, value) => {const node = document.createElement(tag);node.textContent = value;return node;};
  const query = () => new URLSearchParams({run, note_after:String(noteAfter), note_limit:"50", review_after:String(reviewAfter), review_limit:"50"});
  const statePower = () => state?.power_state || {};
  const message = (value, error = false) => {find("learning-message").textContent = value;find("learning-message").className = error ? "error" : "muted";};
  function permissions() {
    const current = state?.current === true;
    const available = auth && current && state?.human_control_available === true && state?.control_enabled === true && !busy;
    const takeover = state?.authority_mode === "agent_control";
    const permitted = available && (!takeover || find("learning-takeover").checked);
    find("learning-control-fields").disabled = !available;
    find("learning-takeover-row").hidden = !takeover;
    for (const button of document.querySelectorAll("[data-learning-operation]")) button.disabled = !permitted;
    find("learning-note-fields").disabled = !auth || busy || !state;
    find("learning-enable").disabled = !permitted || !find("learning-confirmation").checked;
    find("learning-set-current").disabled = !permitted || !Number.isFinite(statePower().commanded_load_current_limit_a);
    find("learning-permission").textContent = !auth ? "Read-only view. Log in to use human controls or add notes." : !current ? "Saved run: equipment controls are unavailable. Notes and analyses refer to this saved run." : !state?.control_enabled ? "Recording only: the operator has disabled instrument control." : busy ? "Waiting for this human operation's result. No automatic retry will be made." : takeover ? "Agent authority is active. Confirm human takeover before submitting an equipment command; this blocks later agent writes until explicitly resumed in the console." : "Signed-in human control of the current run. Agent equipment writes are blocked.";
  }
  function renderState() {
    if(!state) return;
    const status = !state.current ? "Saved run · review only" : state.enabled ? "Human authority · learning" : "Agent authority";
    find("learning-shortcut").textContent = `${status} · human controls, notes and reviews ↓`;
    find("learning-status").textContent = status + (state.current ? ` · acquisition ${state.running ? "running" : "not running"} · sample interval ${state.sample_interval_s} s` : "") + (state.last_sample_at ? `\nLast acquisition cycle: ${state.last_sample_at}` : "") + (state.last_error ? `\nAcquisition error: ${state.last_error}. See the recorded gap; this does not establish an equipment shutdown.` : "");
    const power = statePower(), known = Number.isFinite(power.commanded_load_current_limit_a);
    find("learning-power-state").textContent = known ? `Latest recorded setting: ${power.commanded_load_current_limit_a} A total · Output: ${power.output_enabled === true ? "ON" : power.output_enabled === false ? "OFF" : "unknown"} · Observed: ${power.observed_at || "timestamp unavailable"}` : "No recorded power state yet. Target changes remain unavailable until a setting is observed.";
    const noLoad = state.acceptance_context === "no_load_test";
    find("learning-confirmation-label").textContent = noLoad ? "I have just confirmed that no dispenser or unapproved load is connected (approved metrology wiring only)." : "I have just confirmed the physical parallel CH1 dispenser wiring.";
    const limits = power.safety_limits || {};
    const cap = limits.effective_max_load_current_a ?? limits.max_load_current_a;
    find("learning-current-policy").textContent = `Existing MCP current limits and upward-step rule apply${Number.isFinite(cap) ? `; reported limit ${cap} A` : ""}. Apply compares against the displayed recorded setting. It never retries automatically.`;
    if(Number.isFinite(cap)) find("learning-target").max = cap;
    permissions();
  }
  function appendUnique(existing, incoming, key) {
    const known = new Set(existing.map(item => item[key]));
    for(const item of incoming) if(!known.has(item[key])) {existing.push(item);known.add(item[key]);}
  }
  function renderRecords() {
    const noteBox = find("learning-notes");noteBox.replaceChildren();
    for(const note of notes) {
      const card = make("article", "");card.className = "learning-record";
      card.append(make("small", `Human note · ${note.recorded_at || "time unavailable"}`), make("p", note.note || ""));noteBox.append(card);
    }
    if(!notes.length) noteBox.append(make("p", "No human notes recorded."));
    const reviewBox = find("learning-reviews");reviewBox.replaceChildren();
    for(const review of reviews) {
      const card = make("article", "");card.className = "learning-record";
      card.append(make("small", `${review.review_id} · ${review.recorded_at || "time unavailable"}`), make("p", review.report_summary || ""), make("p", review.change_summary || "No guidance change reported."));
      const button = make("button", "Read report and guidance changes");button.type = "button";button.addEventListener("click", () => showReview(review.review_id));card.append(button);reviewBox.append(card);
    }
    if(!reviews.length) reviewBox.append(make("p", "No agent analysis attached yet. Recording does not require an agent."));
  }
  async function jsonResponse(response) {
    let data;
    try {data = await response.json();} catch {throw new Error(`HTTP ${response.status}: invalid response`);}
    if(!response.ok) {
      if(response.status===401 || response.status===403) {auth=false;permissions();}
      const detail=data.error || data.result?.content?.filter(item=>item.type==="text").map(item=>item.text).join("\n") || "Request returned an error";
      const error=new Error(`HTTP ${response.status}: ${detail}`);error.responseReceived=true;throw error;
    }
    return data;
  }
  async function refresh(reset = false) {
    if(loading) return;
    clearTimeout(timer);loading = true;const revision = authRevision;
    try {
      if(reset) {notes=[];reviews=[];noteAfter=0;reviewAfter=0;}
      const response = await fetch("/api/learning?" + query(), {cache:"no-store",signal:AbortSignal.timeout(10000)});
      const incoming = await jsonResponse(response);
      if (state?.run_binding && incoming.run_binding !== state.run_binding) {
        notes=[]; reviews=[]; noteAfter=0; reviewAfter=0;
        state=incoming; find("learning-takeover").checked=false;
        find("learning-confirmation").checked=false;
        find("learning-review-detail").hidden=true;
        renderState();renderRecords();
        return;
      }
      state = incoming;
      if(revision === authRevision) auth = state.operator_authorized === true;
      const notePage = state.human_notes || {}, reviewPage = state.reviews || {};
      appendUnique(notes, notePage.items || [], "note_id");appendUnique(reviews, reviewPage.items || [], "review_id");
      noteAfter = notePage.after ?? noteAfter;reviewAfter = reviewPage.after ?? reviewAfter;
      find("learning-more-notes").hidden = !notePage.has_more;find("learning-more-reviews").hidden = !reviewPage.has_more;
      renderState();renderRecords();
      if(requestedReview) {const reviewId=requestedReview;requestedReview=null;await showReview(reviewId);}
    } catch(error) {find("learning-status").textContent = `Learning view unavailable: ${error.message}. Retained values may be stale. No equipment command was sent.`;state=null;permissions();}
    finally {loading=false;timer=setTimeout(refresh,5000);}
  }
  async function showReview(reviewId) {
    try {
      const data = await jsonResponse(await fetch("/api/learning/review?" + new URLSearchParams({run,review_id:reviewId}), {cache:"no-store",signal:AbortSignal.timeout(10000)}));
      const review = data.review || data;
      find("learning-review-title").textContent = "Analysis " + review.review_id;
      find("learning-review-meta").textContent = `Recorded: ${review.recorded_at || "unavailable"} · Guidance revisions: ${(review.guidance_revision_refs || []).join(", ") || "none"}`;
      find("learning-review-report").textContent = review.report_markdown || review.report_summary || "No report text supplied.";
      find("learning-review-changes").textContent = JSON.stringify(review.guidance_changes || [],null,2);
      const section=find("learning-review-detail");section.hidden=false;section.scrollIntoView({behavior:"smooth",block:"start"});
    } catch(error) {message("Could not load analysis: " + error.message,true);}
  }
  async function operate(operation) {
    if(busy || find("learning-control-fields").disabled) return;
    const body = {run,run_binding:state.run_binding,operation,action_context:{action:operation,reason_summary:find("learning-reason").value.trim() || `Human requested ${operation} from the dashboard.`}};
    if (state.authority_mode === "agent_control") {
      if (!find("learning-takeover").checked) return;
      body.operator_takeover_confirmation="confirmed_operator_takeover";
    }
    if(operation === "set_current") {
      const raw=find("learning-target").value;
      if(!raw || !find("learning-target").checkValidity()) {message("Enter a valid absolute total-current target in amperes.",true);return;}
      body.target_current_a=Number(raw);body.expected_current_a=statePower().commanded_load_current_limit_a;
    }
    if(operation === "enable") {
      if(!find("learning-confirmation").checked) return;
      if(state.acceptance_context === "no_load_test") body.no_load_test_connection_confirmation="confirmed_no_dispenser_or_unapproved_load_connected";
      else body.parallel_connection_confirmation="confirmed_parallel_ch1";
    }
    busy=true;permissions();message(`Human ${operation} request sent. Waiting for the recorded result…`);
    try {
      const result=await jsonResponse(await fetch("/api/learning/manual-operation",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:AbortSignal.timeout(90000)}));
      const failure=result.isError || result.is_error || result.result?.isError || result.result?.is_error;
      message(failure ? "The operation returned an error. Inspect its recorded result and current state before any new request; no retry was made." : `Human ${operation} returned. Check the recorded result and latest power state below the graph.`,Boolean(failure));
      find("learning-confirmation").checked=false;
      find("learning-takeover").checked=false;
      await refresh();
    } catch(error) {message(error.responseReceived ? `MCP returned an error: ${error.message}\nNo retry was made. Inspect the recorded result before choosing another action.` : `Human operation result unavailable: ${error.message}. Do not assume the command failed or repeat it blindly; inspect power readback and recorded results.`,true);}
    finally {busy=false;permissions();}
  }
  find("learning-note-form").addEventListener("submit",async event=>{
    event.preventDefault();if(busy || !auth) return;
    const note=find("learning-note").value.trim();if(!note) return;
    busy=true;permissions();
    try {
      await jsonResponse(await fetch("/api/learning/note",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({run,note}),signal:AbortSignal.timeout(10000)}));
      find("learning-note").value="";message("Human note saved. Equipment unchanged.");await refresh();
    } catch(error) {message(`Note save not confirmed: ${error.message}. Check the notes before resubmitting.`,true);}
    finally {busy=false;permissions();}
  });
  for(const button of document.querySelectorAll("[data-learning-operation]")) button.addEventListener("click",()=>operate(button.dataset.learningOperation));
  find("learning-confirmation").addEventListener("change",permissions);
  find("learning-takeover").addEventListener("change",permissions);
  find("learning-refresh").addEventListener("click",()=>refresh(true));
  find("learning-more-notes").addEventListener("click",()=>refresh());
  find("learning-more-reviews").addEventListener("click",()=>refresh());
  document.addEventListener("dashboard-login-success",()=>{authRevision++;auth=true;permissions();refresh();});
  document.addEventListener("dashboard-session-update",event=>{auth=event.detail.operatorAuthorized;permissions();});
  refresh();
})();
