"use strict";
// Absolute coordinates remain UTC instants; elapsed coordinates remain minutes.
function elapsedLabel(minutes, hours = minutes >= 60, showSeconds = false, fractional = false) {
  const sign = minutes < 0 ? "−" : "";
  const rawSeconds = Math.abs(Number((minutes * 60).toFixed(3)));
  const seconds = fractional ? Math.floor(rawSeconds) : Math.round(rawSeconds);
  const fraction = fractional ? (rawSeconds - seconds).toFixed(3).slice(1) : "";
  const pad = value => String(value).padStart(2, "0");
  return sign + (hours ? pad(Math.floor(seconds / 3600)) + ":" + pad(Math.floor(seconds / 60) % 60) + (showSeconds ? ":" + pad(seconds % 60) + fraction : "")
    : pad(Math.floor(seconds / 60)) + ":" + pad(seconds % 60) + fraction);
}
function clockMode() { return el("clock").value; }
function isAbsoluteClock() { return ["utc", "server", "browser"].includes(clockMode()); }
function realElapsedName() { return metadata.session_kind === "simulated" ? "Real elapsed time" : "Elapsed time"; }
function elapsedName() { return clockMode() === "simulation_elapsed" ? "Simulation elapsed time" : realElapsedName(); }
function elapsedDetail(minutes, name = elapsedName()) {
  if(typeof minutes!=="number"||!Number.isFinite(minutes)) return name+" unavailable";
  const absolute = Math.abs(minutes);
  return name + " " + elapsedLabel(minutes, absolute >= 60, true) + (absolute >= 60 ? " HH:MM:SS" : " MM:SS") + " · " + (minutes * 60).toFixed(3) + " s";
}
function elapsedAxis(values, range) {
  const finite = values.filter(v => typeof v === "number" && Number.isFinite(v));
  let low = finite.length ? finite.reduce((a,b)=>Math.min(a,b)) : 0, high = finite.length ? finite.reduce((a,b)=>Math.max(a,b)) : 1;
  if (range && range.length === 2 && range.every(v => Number.isFinite(Number(v)))) [low, high] = range.map(Number);
  if (high <= low) high = low + 1 / 60;
  const hours = high >= 60;
  const showSeconds = hours && (high - low) / 6 < 1;
  const fractional = (high - low) * 60 / 6 < 1;
  const tickvals = Array.from({length:7}, (_, i) => low + (high - low) * i / 6);
  return {tickmode:"array", tickvals, ticktext:tickvals.map(v => elapsedLabel(v, hours, showSeconds, fractional)),
    title:{text:elapsedName() + " · " + (hours ? (showSeconds ? (fractional ? "HH:MM:SS.sss" : "HH:MM:SS") : "HH:MM") + " (hours may exceed 24)" : (fractional ? "MM:SS.sss" : "MM:SS"))}};
}
const browserTimeZone=Intl.DateTimeFormat().resolvedOptions().timeZone||"browser default zone";
function browserZoneName() { return browserTimeZone; }
function absoluteZone() {
  if (clockMode() === "utc") return "UTC";
  if (clockMode() === "server") return serverTime.available ? serverTime.iana_time_zone : null;
  return undefined;
}
function absoluteTitle() {
  if (clockMode() === "utc") return "Recorded wall time · UTC";
  if (clockMode() === "server") return serverTime.available ? `Recorded wall time · Server local · ${serverTime.iana_time_zone}` : "Server local time unavailable";
  return `Recorded wall time · Browser local · ${browserZoneName()}`;
}
const instantFormatters=new Map();
function instantFormatter(timeZone, precision) {
  const key=`${timeZone??"browser"}:${precision}`;
  if(instantFormatters.has(key)) return instantFormatters.get(key);
  const options={
    timeZone,
    year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",
    hourCycle:"h23",timeZoneName:"short"
  };
  if(precision!=="minute") options.second="2-digit";
  if(precision==="millisecond") options.fractionalSecondDigits=3;
  const formatter=new Intl.DateTimeFormat("en-CA",options);
  instantFormatters.set(key,formatter);
  return formatter;
}
function formatWallInstant(value, precision = "millisecond") {
  if(value===null||value===undefined||value==="") return "unavailable";
  const instant = new Date(value);
  if (!Number.isFinite(instant.valueOf())) return "unavailable";
  const timeZone = absoluteZone();
  if (clockMode() === "server" && !timeZone) return "server local time unavailable";
  try {
    return instantFormatter(timeZone,precision).format(instant);
  } catch {
    return "time-zone conversion unavailable";
  }
}
function wallAxis(range) {
  const [low, high] = range;
  const span=high-low;
  const precision=span<=10000?"millisecond":span<=21600000?"second":"minute";
  const tickvals=Array.from({length:7},(_,index)=>low+(high-low)*index/6);
  return {tickmode:"array",tickvals,ticktext:tickvals.map(value=>formatWallInstant(value,precision)),title:{text:absoluteTitle()}};
}
const sharedView = {mode:"full",range:null,width:null,wall:false};
const chartStates = Object.fromEntries(["chart","voltage-chart","truth-chart","token-chart"].map(id=>[id,{busy:0,queue:Promise.resolve()}]));
const autoY = {chart:[true,true,false,false],"voltage-chart":[true],"truth-chart":[true,true,true],"token-chart":[true,true]};
const gridStyle = {showgrid:true,gridcolor:"#40556b",showline:true,linecolor:"#6c8197",linewidth:1,mirror:true,zeroline:false};
let redrawQueue=Promise.resolve();
function timeNumber(value, wall) {
  if(value===null || value===undefined) return NaN;
  if(typeof value==="number") return value;
  if(!wall) return Number(value);
  const rendered=String(value).replace(" ","T");
  return Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(rendered)?rendered:rendered+"Z");
}
function realElapsedMinutes(value) {
  const origin=Date.parse(metadata.created_at),instant=Date.parse(value);
  return Number.isFinite(origin)&&Number.isFinite(instant)?(instant-origin)/60000:null;
}
function modelX(row) {
  if(isAbsoluteClock()) return row.recorded_at||null;
  if(clockMode()==="real_elapsed") return realElapsedMinutes(row.recorded_at);
  return num(row.virtual_time_s)===null?null:row.virtual_time_s/60;
}
function knownTimes() {
  return [...events.map(xFor),...(metadata.session_kind==="simulated"?truthRows.map(modelX):[])].filter(v=>v!==null);
}
function timeRange(state, values) {
  const points=values.map(v=>timeNumber(v,state.wall)).filter(Number.isFinite);
  const latest=points.length?points.reduce((a,b)=>Math.max(a,b)):(state.range?.[1]??0);
  const earliest=points.length?points.reduce((a,b)=>Math.min(a,b)):latest;
  const fallback=state.wall?60000:1;
  if(state.mode==="full") state.range=latest>earliest?[earliest,latest]:[latest-fallback/2,latest+fallback/2];
  else if(state.mode==="rolling") {state.width=state.width>0?state.width:fallback;state.range=[latest-state.width,latest];}
  else if(!state.range) state.range=[latest-fallback/2,latest+fallback/2];
  return state.range;
}
function viewIds(id) {
  return id==="chart"?["time-view","time-width",metadata.session_kind==="simulated"?4:3]:id==="voltage-chart"?["voltage-time-view","voltage-time-width",1]:id==="truth-chart"?["truth-time-view","truth-time-width",3]:["token-time-view","token-time-width",2];
}
function updateWidth() {
  const width=sharedView.range?sharedView.range[1]-sharedView.range[0]:(sharedView.width||(sharedView.wall?60000:1));
  for(const id of Object.keys(chartStates)) {
    const [select,widthId]=viewIds(id);el(select).value=sharedView.mode;
    el(widthId).textContent="Shared visible width: "+(width/(sharedView.wall?1000:1/60)).toFixed(3)+" s";
  }
}
function captureRange(id) {
  const raw=el(id).layout?.xaxis?.range;
  if(raw?.length===2) {
    const range=raw.map(v=>timeNumber(v,sharedView.wall));
    if(range.every(Number.isFinite)&&range[1]>range[0]) {sharedView.range=range;sharedView.width=range[1]-range[0];}
  }
}
function resetTimeView() {sharedView.mode="full";sharedView.range=null;sharedView.width=null;updateWidth();}
function guardedPlot(id,operation) {
  const state=chartStates[id];
  const next=state.queue.then(async()=>{state.busy++;try{return await operation();}finally{state.busy--;}});
  state.queue=next.catch(()=>{});return next;
}
function viewLayout(id,layout) {
  sharedView.wall=isAbsoluteClock();
  const values=knownTimes(),range=timeRange(sharedView,values),count=viewIds(id)[2];
  const rendered=sharedView.wall?range.map(v=>new Date(v).toISOString()):range;
  const ticks=sharedView.wall?wallAxis(range):elapsedAxis(values,range);
  for(let i=1;i<=count;i++) {
    const key=i===1?"xaxis":"xaxis"+i;
    Object.assign(layout[key],gridStyle,ticks,{type:sharedView.wall?"date":"linear",range:rendered,autorange:false});
    if(i<count) layout[key].title=null;
  }
  updateWidth();
}
function visibleExtent(traces,panel,range,wall,log) {
  const values=[];
  for(const trace of traces) {
    if(trace.visible===false||trace.visible==="legendonly"||(trace.yaxis||"y")!==(panel===1?"y":"y"+panel)) continue;
    for(let i=0;i<trace.x.length;i++) {
      const x=timeNumber(trace.x[i],wall),y=num(trace.y[i]);
      if(x>=range[0]&&x<=range[1]&&y!==null&&(!log||y>0)) values.push(log?Math.log10(y):y);
    }
  }
  if(!values.length) return null;
  const low=values.reduce((a,b)=>Math.min(a,b)),high=values.reduce((a,b)=>Math.max(a,b));
  const pad=high>low?(high-low)*.08:log?.1:Math.max(Math.abs(low)*.08,.01);
  return [low-pad,high+pad];
}
function applyY(id,layout,traces) {
  const graph=el(id);
  // Retain hidden traces while reacting to incoming records.
  for(const trace of traces) {
    const old=graph.data?.find(t=>t.name===trace.name);
    if(old?.visible!==undefined) trace.visible=old.visible;
  }
  for(let panel=1;panel<=viewIds(id)[2];panel++) {
    const key=panel===1?"yaxis":"yaxis"+panel,axis=layout[key];
    Object.assign(axis,gridStyle);
    if(id==="chart"&&panel===3) continue;
    const old=graph.layout?.[key]?.range;
    const range=autoY[id][panel-1]?visibleExtent(traces,panel,sharedView.range,sharedView.wall,axis.type==="log"):null;
    axis.range=range||old||axis.range||(axis.type==="log"?[-9,-6]:[0,1]);
    axis.autorange=false;
  }
}
function redrawPublic() {
  const next=redrawQueue.then(async()=>{sharedView.wall=isAbsoluteClock();await draw();await drawVoltage();await drawTokens();});
  redrawQueue=next.catch(()=>{});return next;
}
function redrawAll() {
  return redrawPublic().then(async()=>{if(truthData) await drawTruth(truthData,true);});
}
function watchTimeView(id) {
  const graph=el(id),state=chartStates[id];
  graph.removeAllListeners("plotly_relayout");
  graph.on("plotly_relayout",change=>{
    if(state.busy) return;
    if(!Object.keys(change).some(k=>/^xaxis[0-9]*\.(range|autorange)/.test(k))) return;
    captureRange(id);
    if(sharedView.mode==="full") sharedView.mode="fixed";
    updateWidth();redrawAll().catch(showViewError);
  });
  graph.removeAllListeners("plotly_restyle");
  graph.on("plotly_restyle",()=>{if(!state.busy) redrawAll().catch(showViewError);});
}
function showViewError(error) { el("status").textContent="Chart update failed: "+error.message; }
const el = id => document.getElementById(id);
let operatorAuthorized = document.body.dataset.operatorAuthorized === "true";
let loginRevision=0;
document.addEventListener("dashboard-login-success",()=>{
  loginRevision++;operatorAuthorized=true;updateManagement();
  requestOverviewSoon();requestTruthSoon();
});
for(const link of document.querySelectorAll(".operator-unlock")) {
  const target=new URL("/dashboard/login",location.origin);
  for(const key of ["run","archived"]) {const value=new URLSearchParams(location.search).get(key);if(value!==null)target.searchParams.set(key,value);}
  link.href=target.href;
}
const selectedRun = new URLSearchParams(window.location.search).get("run") || "";
const runQuery = "run=" + encodeURIComponent(selectedRun);
const PUBLIC_PAGE_SIZE=1000,PUBLIC_PAGE_WORKERS=4,HISTORY_WINDOW_SIZE=200;
let generation = -1, metadata = {}, serverTime = {available:false,iana_time_zone:null};
let events = [], observations = [], controls = [], decisions = [];
let chartReady = false, clockInitializedFor = null;
const byId = new Map();
const recordNumbers = new Map();
const exactByNumber=new Map(),overviewByNumber=new Map(),pageCoverage=new Map(),failedPages=new Map();
const pageQueue=[],queuedPages=new Set(),inFlightPages=new Set();
let totalRecords=0,overviewReady=false,historyComplete=false,activePageRequests=0;
let pageWorkerCount=0;
let overviewRunning=false,overviewTimer=null,overviewRevision=0;
let historyWindowEnd=0,historyPinnedNewest=true;
let renderDirty=false,renderRunning=false;
let selectedId = null, truthRevision = null, truthRows = [];
let truthCursor=0, truthGeneration=-1, truthMore=false, truthData=null;
const shortId = id => recordNumbers.has(id) ? "#" + String(recordNumbers.get(id)).padStart(3, "0") : "Unloaded record";
const actor = (event = {}) => ({agent:"Agent",scripted:"Script",human:"Human",learning_sampler:"MCP sampler"})[event.actor || metadata.source] || "Caller";
const toolLabel = name => ({
read_vacuum_pressure:"Read vacuum pressure",read_dispenser_power_state:"Read power state",
prepare_dispenser:"Prepare dispenser",prepare_dispenser_power:"Prepare dispenser power",
enable_dispenser_output:"Enable output",set_dispenser_current:"Set load current",
set_dispenser_load_current:"Set load current",shutdown_dispenser:"Turn output off",shutdown_dispenser_power:"Turn output off",
reload_dispenser_current_limit:"Reload operator current cap (no actuation)",
record_conditioning_decision:"Record a judgment (no hardware action)"
})[name] || (name || "Record").replaceAll("_"," ");
function outcome(event) {
  if (event.kind === "external_change_detected") return "Setting change observed";
  if (event.kind === "human_action_context") return "Human action context recorded";
  if (event.kind === "decision") return actor(event) + " decision";
  if (event.kind === "call_intent") return "Requested";
  const execution = event.execution;
  if (execution === "not_executed") return "Not executed";
  if (event.kind === "call_error" || event.is_error) return "Error / state uncertain";
  return "Completed";
}
function highlightSelection() {
  document.querySelectorAll("[data-record]").forEach(node => node.classList.toggle("highlight", node.dataset.record === selectedId));
}
const intentsByCall = new Map();
const num = value => typeof value === "number" && Number.isFinite(value) ? value : null;
const fmt = value => num(value) === null ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: 4 });
const text = (tag, value, className) => { const node = document.createElement(tag); node.textContent = value; if (className) node.className = className; return node; };
const resultsByCall = new Map();
function virtualMinutes(row) {
  const event = byId.get(row.event_id) || row;
  const basis = event.virtual_time_basis || row.virtual_time_basis;
  if (num(row.virtual_time_s) !== null && basis !== "agent_decision_time") return row.virtual_time_s / 60;
  const result = resultsByCall.get(row.call_id || event.call_id);
  if (result && result !== event) {
    if (num(result.virtual_time_s) !== null && result.virtual_time_basis !== "agent_decision_time") return result.virtual_time_s / 60;
    if (num(result.result_virtual_time_s) !== null) return result.result_virtual_time_s / 60;
  }
  return null;
}
function placementLabel(row) {
  const event=byId.get(row.event_id)||row;
  if (event.virtual_time_basis==="simulator_request_clock") return "Known simulator clock at receipt, before this call advances time (not a measurement)";
  if (!row.observed_at && event.virtual_time_basis!=="observed_time_origin") return "Placed at same-call result time; exact request time unavailable (not a measurement)";
  return "Returned observation time";
}
function xFor(row) {
  if(isAbsoluteClock()) return row.recorded_at||null;
  if(clockMode()==="real_elapsed") return realElapsedMinutes(row.recorded_at);
  return virtualMinutes(row);
}
function displayTime(row) {
  const value=xFor(row);
  if(value===null||value===undefined) return clockMode()==="simulation_elapsed"?"Simulation elapsed unavailable":"Time unavailable";
  return isAbsoluteClock()?formatWallInstant(value):elapsedDetail(value);
}
function displayInstant(value) {
  if(!value) return "not available";
  if(isAbsoluteClock()) return formatWallInstant(value);
  if(clockMode()==="real_elapsed") {
    const elapsed=realElapsedMinutes(value);
    return elapsed===null?"real elapsed unavailable":elapsedDetail(elapsed,realElapsedName());
  }
  return formatUtcInstant(value)+" wall instant";
}
function formatUtcInstant(value) {
  if(value===null||value===undefined||value==="") return "unavailable";
  const instant=new Date(value);
  if(!Number.isFinite(instant.valueOf())) return "unavailable";
  return instantFormatter("UTC","millisecond").format(instant);
}
function configureClockOptions() {
  const serverOption=el("clock").querySelector('option[value="server"]');
  serverOption.disabled=!serverTime.available;
  serverOption.textContent=serverTime.available?`Server local time · ${serverTime.iana_time_zone}`:"Server local time · unavailable on this host";
  el("clock").querySelector('option[value="browser"]').textContent=`Browser local time · ${browserZoneName()}`;
  const realOption=el("clock").querySelector('option[value="real_elapsed"]');
  const realAvailable=Number.isFinite(Date.parse(metadata.created_at));
  realOption.disabled=!realAvailable;
  realOption.textContent=realAvailable?(metadata.session_kind==="simulated"?"Real elapsed time · since run start":"Elapsed time · since run start"):"Elapsed time · run creation time unavailable";
  const simulationOption=el("clock").querySelector('option[value="simulation_elapsed"]');
  const simulated=metadata.session_kind==="simulated";
  simulationOption.hidden=!simulated;simulationOption.disabled=!simulated;
  if(el("clock").selectedOptions[0]?.disabled) el("clock").value="browser";
}

async function loadExactEvent(id) {
  try {
    const response=await fetch(`/api/session/record?event_id=${encodeURIComponent(id)}&generation=${generation}&${runQuery}`,{cache:"no-store",signal:AbortSignal.timeout(5000)});
    const data=await response.json();
    if(response.status===409||data.reset) {resetPublicState(data.generation);requestOverviewSoon();return null;}
    if(!response.ok||!data.event) return null;
    exactByNumber.set(data.event.record_number,data.event);schedulePublicRender();
    return data.event;
  } catch {return null;}
}
async function selectEvent(id, jump = false, scroll = true) {
  let event = byId.get(id);
  if (!event) {
    el("raw").textContent = `Loading referenced event ${id}…`;
    event=await loadExactEvent(id);
  }
  if (!event) { el("raw").textContent = `Referenced event ${id} is unavailable in this session file.`; return; }
  selectedId = id;
  highlightSelection();
  const linkedItem = [...document.querySelectorAll("[data-record]")].find(node => node.dataset.record === id);
  if (linkedItem) linkedItem.parentElement.scrollTop += linkedItem.getBoundingClientRect().top - linkedItem.parentElement.getBoundingClientRect().top - 12;
  const readable = el("readable"); readable.replaceChildren();
  readable.append(text("h3", shortId(id) + " · " + outcome(event) + " · " + toolLabel(event.tool)));
  const decision = event.kind === "decision" ? event : events.find(e => e.kind === "decision" && e.decision_id === event.decision_id);
  const chosenAction = decision?.chosen_action;
  const statedReason = decision?.rationale_summary;
  const supportingIds = decision?.basis_event_ids ?? [];
  const args = event.requested_arguments || {};
  if(event.kind === "external_change_detected") {
    readable.append(text("p", `An external setting change was detected between ${displayInstant(event.observation_interval_started_at)} and ${displayInstant(event.detected_at || event.recorded_at)}. Its exact action time, operator and reason are unknown.`));
    readable.append(text("p", (event.changed_fields || []).map(key => `${key}: ${JSON.stringify(event.previous_values?.[key])} → ${JSON.stringify(event.detected_values?.[key])}`).join(" · ")));
  }
  readable.append(text("p", usageText(decision?.token_usage_id ? {usage_id:decision.token_usage_id,total_tokens:decision.total_tokens,input_tokens:decision.input_tokens,output_tokens:decision.output_tokens,cached_input_tokens:decision.cached_input_tokens,model:decision.token_model} : null), "muted"));
  if(decision?.background) readable.append(text("p","Background: "+decision.background));
  if(decision?.confidence_claim) readable.append(text("p","Caller confidence: "+decision.confidence_value+" in claim: "+decision.confidence_claim));
  if(decision?.completion_outcome) readable.append(text("p",actor(decision)+" assessment: "+decision.completion_outcome+" · Dispenser response: "+decision.dispenser_response+" (not proof of output OFF)."));
  if (chosenAction) readable.append(text("p", actor(decision) + " chose: " + chosenAction));
  if (statedReason) readable.append(text("p", "Stated reason: " + statedReason));
  if(num(event.applied_max_load_current_A)!==null) readable.append(text("p", "Operator current cap: "+fmt(event.previous_max_load_current_A)+" → "+fmt(event.applied_max_load_current_A)+" A; effective "+fmt(event.effective_max_load_current_A)+" A. "+event.notice));
  if(event.error) readable.append(text("p","MCP error: "+event.error,"error"));
  if (num(args.target_current_a) !== null) readable.append(text("p", "Requested load-current target: " + fmt(args.target_current_a) + " A"));
  if (num(args.expected_current_a) !== null) readable.append(text("p", "Expected previous load-current setting: " + fmt(args.expected_current_a) + " A (must match before the change)."));
  const sourceObservedAt=observations.find(r => r.event_id === id)?.observed_at || event.observed_at;
  readable.append(text("p", "Decision: " + displayInstant(event.decision_at) + " · MCP receipt: " + displayInstant(event.received_at || event.recorded_at) + " · Result recorded: " + displayInstant(event.recorded_at)));
  readable.append(text("p", "Source observation: " + (!sourceObservedAt ? "not available" : metadata.session_kind === "simulated" ? sourceObservedAt + " · synthetic model time, not a real wall date" : displayInstant(sourceObservedAt))));
  if (clockMode() === "simulation_elapsed") readable.append(text("p", virtualMinutes(event) === null ? "Simulation elapsed placement unavailable; real decision/receipt UTC instants remain unchanged." : "Simulation placement: " + elapsedDetail(virtualMinutes(event),"Simulation elapsed time") + " · " + placementLabel(event), "muted"));
  const links = text("div", "", "links");
  for (const related of events.filter(e => (event.call_id && e.call_id === event.call_id) || (event.decision_id && e.decision_id === event.decision_id))) {
    if (related.event_id !== id) links.append(eventButton(related.event_id, shortId(related.event_id) + " " + outcome(related), false));
  }
  for (const basis of supportingIds) links.append(eventButton(basis, "Supporting observation " + shortId(basis), false));
  readable.append(links);
  const row = observations.find(r => r.event_id === id);
  if (row) readable.append(text("p", Object.entries(row).filter(([key,value]) => value !== null && ["pressure_mbar","commanded_load_current_limit_a","native_ch1_measured_current_a","native_ch1_measured_voltage_v","measured_ch2_current_a","measured_ch2_voltage_v","measured_parallel_load_current_a","output_enabled"].includes(key)).map(([key,value]) => ({
pressure_mbar: "Total pressure: " + Number(value).toExponential(3) + " mbar",
commanded_load_current_limit_a: "Returned load-current setting: " + value + " A",
native_ch1_measured_current_a: "Measured CH1 current: " + value + " A",
native_ch1_measured_voltage_v: "Measured CH1 voltage: " + value + " V",
measured_ch2_current_a: "Measured CH2 current: " + value + " A",
measured_ch2_voltage_v: "Measured CH2 voltage: " + value + " V",
measured_parallel_load_current_a: "Measured combined current (CH1 + CH2): " + value + " A",
output_enabled: "Output: " + (value ? "ON" : "OFF")
})[key]).join(" · ")));
  el("raw").textContent = JSON.stringify(event, null, 2);
  el("selection-label").textContent = `${shortId(id)} · Recorded ${displayInstant(event.recorded_at)} · Full ID in dashboard record fields`;
  if (jump && chartReady) {
    const at = xFor(event);
    if (at !== null) {
      const range = isAbsoluteClock() ? [new Date(Date.parse(at) - 30000).toISOString(), new Date(Date.parse(at) + 30000).toISOString()] : [Math.max(0, at - 1), at + 1];
      sharedView.mode="fixed";el("time-view").value="fixed";
      sharedView.range=range.map(v=>timeNumber(v,sharedView.wall));
      sharedView.width=sharedView.range[1]-sharedView.range[0];
      redrawAll().catch(showViewError);
    }
  }
  if(scroll) el("selected").scrollIntoView({ behavior: "smooth", block: "start" });
}

function eventButton(id, label, jump = true) {
  const button = text("button", label); button.addEventListener("click", () => {selectEvent(id, jump).catch(showViewError);}); return button;
}

function reportedUsage(rows) {
  const seen=new Map(), reports=[], conflicts=[];
  let missing=0,duplicates=0,total=0;
  for(const row of rows) {
    if(!row.token_usage_id || !Number.isInteger(row.total_tokens) || row.total_tokens<0) {missing++;continue;}
    const first=seen.get(row.token_usage_id);
    if(first) {
      duplicates++;
      if(["total_tokens","input_tokens","output_tokens","cached_input_tokens","token_model"].some(k=>first[k]!=null && row[k]!=null && first[k]!==row[k]))
        conflicts.push(row);
      continue;
    }
    seen.set(row.token_usage_id,row);total+=row.total_tokens;
    reports.push({...row,cumulative_tokens:total});
  }
  return {reports,conflicts,missing,duplicates,total};
}
function usageText(usage) {
  if(!usage) return "Token usage: not reported";
  return "Caller-reported tokens: "+usage.total_tokens+
    " total · input "+(usage.input_tokens??"not reported")+
    " · output "+(usage.output_tokens??"not reported")+
    " · cached input "+(usage.cached_input_tokens??"not reported")+
    (usage.model?" · "+usage.model:"")+" · usage ID "+usage.usage_id;
}
async function drawTokens() {
  const summary=reportedUsage(decisions), rows=summary.reports.filter(r=>xFor(r)!==null);
  el("token-chart").hidden=!rows.length;
  el("token-coverage").textContent=!summary.reports.length ? "Token usage not reported. Cumulative usage unavailable; missing reports are not zero." : summary.reports.length+" unique usage reports ("+(summary.reports.length-rows.length)+" without a position on this clock) across "+decisions.length+" decision records; "+summary.missing+" without usage; "+summary.duplicates+" repeated IDs. Reported cumulative subset: "+summary.total+" tokens.";
  el("token-warning").textContent=summary.conflicts.length ? "Conflicting repeated usage IDs: "+summary.conflicts.map(r=>r.token_usage_id+" ("+shortId(r.event_id)+")").join(", ")+". First values retained." : "";
  if(!rows.length) {Plotly.purge("token-chart");return;}
  const trace=(field,name,panel)=>({type:"scatter",mode:"lines+markers",name,
    x:rows.map(xFor),y:rows.map(r=>r[field]),
    xaxis:panel===1?"x":"x2",yaxis:panel===1?"y":"y2",
    customdata:rows.map(r=>r.event_id),text:rows.map(r=>shortId(r.event_id)+"<br>Usage ID: "+r.token_usage_id+"<br>"+displayTime(r)+"<br>"+(clockMode()==="simulation_elapsed"?placementLabel(r):isAbsoluteClock()?absoluteTitle():"Run creation is the real wall elapsed origin")),
    hovertemplate:"%{text}<br>%{y} reported tokens<extra>"+name+"</extra>"});
  const traces=[trace("total_tokens","Tokens per reported decision",1),trace("cumulative_tokens","Cumulative reported tokens",2)];
  const layout={
    paper_bgcolor:"#152330",plot_bgcolor:"#152330",font:{color:"#c9d8e5"},height:440,
    margin:{l:95,r:30,t:60,b:50},uirevision:metadata.session_id,
    legend:{orientation:"h",x:0,y:1.15,font:{size:11}},
    xaxis:{anchor:"y",showticklabels:false},xaxis2:{anchor:"y2",matches:"x",title:{text:"Shared recorded time"}},
    yaxis:{domain:[.58,1],title:{text:"Tokens / report"}},yaxis2:{domain:[0,.42],title:{text:"Cumulative tokens"}}
  };
  viewLayout("token-chart",layout);applyY("token-chart",layout,traces);
  await guardedPlot("token-chart",()=>Plotly.react("token-chart",traces,layout,{responsive:true,displaylogo:false,scrollZoom:true}));
  watchTimeView("token-chart");
  el("token-chart").removeAllListeners("plotly_click");
  el("token-chart").on("plotly_click",event=>{const id=event.points[0]?.customdata;if(id)selectEvent(id);});
}

function rebuildDerived() {
  const merged=new Map(overviewByNumber);
  for(const [number,event] of exactByNumber) merged.set(number,event);
  events=[...merged.values()].sort((a,b)=>a.record_number-b.record_number);
  observations=events.filter(row=>row.observation_kind);
  controls=events.filter(row=>row.phase);
  decisions=events.filter(row=>row.kind==="decision");
  byId.clear();recordNumbers.clear();intentsByCall.clear();resultsByCall.clear();
  for(const event of events) {
    byId.set(event.event_id,event);recordNumbers.set(event.event_id,event.record_number);
    if(event.kind==="call_intent") intentsByCall.set(event.call_id,event);
    if(event.kind==="call_result"||event.kind==="call_error") resultsByCall.set(event.call_id,event);
  }
  historyComplete=overviewReady&&exactByNumber.size===totalRecords&&failedPages.size===0&&inFlightPages.size===0&&pageQueue.length===0;
  if(historyPinnedNewest) historyWindowEnd=totalRecords;
}
function displayedHistory() {
  const end=Math.min(totalRecords,Math.max(HISTORY_WINDOW_SIZE,historyWindowEnd||totalRecords));
  const start=Math.max(1,end-HISTORY_WINDOW_SIZE+1);
  const rows=events.filter(event=>event.record_number>=start&&event.record_number<=end);
  el("history-window").textContent=totalRecords ? `Showing records ${start}–${end} of ${totalRecords}${historyComplete?"":" · exact history still loading"}` : "No records";
  el("history-older").disabled=start<=1;
  el("history-newer").disabled=end>=totalRecords;
  return rows;
}
function updatePanels() {
  const p = observations.filter(r => num(r.pressure_mbar) !== null).at(-1);
  const power = observations.filter(r => r.observation_kind === "power").at(-1);
  el("pressure").textContent = p ? `${p.pressure_mbar.toExponential(3)} mbar` : "—";
  el("set").textContent = power ? `${fmt(power.commanded_load_current_limit_a)} A` : "—";
  el("actual").textContent = power ? `${fmt(power.measured_parallel_load_current_a)} A` : "—";
  el("output").textContent = power?.output_enabled === true ? "ON" : power?.output_enabled === false ? "OFF" : "—";
  const lastObservation=observations.at(-1),observedAgo=lastObservation?Math.max(0,(Date.now()-Date.parse(lastObservation.recorded_at))/1000):null;
  const sourceAgeLabel=lastObservation?.observed_at ? (metadata.session_kind==="simulated" ? `${lastObservation.observed_at} · synthetic model time, not a real wall date` : displayInstant(lastObservation.observed_at)) : "unavailable";
  el("observation-age").textContent=lastObservation?`Last source observation: ${sourceAgeLabel} · Received/recorded ${Number.isFinite(observedAgo)?Math.floor(observedAgo)+" s ago":"at an unavailable time"}. This page reads MCP records; it does not trigger measurements.`:"No observation yet. This dashboard does not sample instruments.";
  const history=displayedHistory();
  const displayedDecisions=history.filter(row=>row.kind==="decision");
  el("decisions").replaceChildren();
  for (const row of [...displayedDecisions].reverse()) {
    const card = text("article", "", "card"); card.dataset.record = row.event_id;
    card.append(text("small", `${shortId(row.event_id)} · ${actor(row)} decision: ${displayInstant(row.decision_at)} · MCP receipt: ${displayInstant(row.received_at || row.recorded_at)}`), text("h3", row.chosen_action), text("p", row.rationale_summary));
    card.append(text("p", usageText(row.token_usage_id ? {usage_id:row.token_usage_id,total_tokens:row.total_tokens,input_tokens:row.input_tokens,output_tokens:row.output_tokens,cached_input_tokens:row.cached_input_tokens,model:row.token_model}:null), "muted"));
    if (row.background) card.append(text("p", row.background, "muted"));
    if (row.confidence_claim) card.append(text("p", `Self-reported confidence: ${row.confidence_value ?? "unknown"} · Claim: ${row.confidence_claim}`, "muted"));
    if (row.completion_outcome) card.append(text("p", `${actor(row)} assessment: ${row.completion_outcome} · Dispenser response: ${row.dispenser_response}. This assessment does not confirm output OFF or successful activation.`, "banner"));
    const links = text("div", "", "links"); links.append(eventButton(row.event_id, "Decision record"));
    for (const id of (row.basis_event_ids || [])) links.append(eventButton(id, `Supporting observation ${shortId(id)} ↗`));
    card.append(links); el("decisions").append(card);
  }
  if (!displayedDecisions.length) el("decisions").append(text("div", "No decisions in this 200-record window. No rationale is inferred from calls.", "empty"));
  el("events").replaceChildren();
  for (const event of history.filter(e => e.kind !== "decision").reverse()) {
    const failed = event.kind === "call_error" || event.is_error === true;
    const line = text("div", "", "event"); line.dataset.record = event.event_id;
    line.append(eventButton(event.event_id, `${shortId(event.event_id)} · ${event.kind === "call_intent" ? actor(event) + " → MCP" : "MCP → " + actor(event)} · ${toolLabel(event.tool)} · ${outcome(event)} · ${displayTime(event)}`));
    if (failed) line.classList.add("error");
    el("events").append(line);
  }
  highlightSelection();
}
function schedulePublicRender() {
  renderDirty=true;
  if(renderRunning) return;
  requestAnimationFrame(()=>{flushPublicRender().catch(showViewError);});
}
async function flushPublicRender() {
  if(renderRunning) return;
  renderRunning=true;
  try {
    while(renderDirty) {
      renderDirty=false;
      rebuildDerived();updatePanels();
      await redrawPublic();
    }
  } finally {renderRunning=false;}
}
el("history-older").addEventListener("click",()=>{historyPinnedNewest=false;historyWindowEnd=Math.max(HISTORY_WINDOW_SIZE,historyWindowEnd-HISTORY_WINDOW_SIZE);updatePanels();});
el("history-newer").addEventListener("click",()=>{historyWindowEnd=Math.min(totalRecords,historyWindowEnd+HISTORY_WINDOW_SIZE);historyPinnedNewest=historyWindowEnd>=totalRecords;updatePanels();});

async function draw() {
  if (!window.Plotly) { el("status").textContent = "Local Plotly bundle could not load."; return; }
  const axis = el("clock").value;
  function trace(field, name, color, panel, filter = () => true, dash) {
    const rows = observations.filter(r => filter(r) && xFor(r) !== null);
    return { type: "scatter", mode: "lines+markers", name, x: rows.map(xFor), y: rows.map(r => num(r[field])),
      xaxis: panel === 1 ? "x" : `x${panel}`, yaxis: panel === 1 ? "y" : `y${panel}`,
      line: { color, width: 2, dash, shape: field.includes("setpoint") || field.includes("limit") ? "hv" : "linear" },
      marker: { size: 4 }, connectgaps: false, customdata: rows.map(r => r.event_id),
      text: rows.map(r => shortId(r.event_id) + "<br>" + displayTime(r)), hovertemplate: "%{text}<br>%{y}<extra>" + name + "</extra>" };
  }
  const traces = [
    trace("pressure_mbar", "Observed total pressure · mbar", "#e1c77c", 1, r => r.observation_kind === "pressure"),
    trace("commanded_load_current_limit_a", "Returned commanded load limit · A", "#8cafe2", 2, r => r.observation_kind === "power"),
    trace("native_ch1_measured_current_a", "Measured native CH1 · A", "#6edbc9", 2, r => r.observation_kind === "power"),
    trace("measured_ch2_current_a", "Measured CH2 · A", "#a8d98c", 2, r => r.observation_kind === "power"),
    trace("measured_parallel_load_current_a", "Measured CH1 + CH2 · A", "#ffc875", 2, r => r.observation_kind === "power"),
  ];
  const requested = controls.filter(r => r.phase === "call_intent" && xFor(r) !== null && num(r.requested_load_current_a) !== null);
  traces.push({ type: "scatter", mode: "markers", name: "Requested load target · A", x: requested.map(xFor), y: requested.map(r => r.requested_load_current_a), xaxis: "x2", yaxis: "y2", marker: { color: "#efbc72", symbol: "diamond-open", size: 9 }, customdata: requested.map(r => r.event_id), text: requested.map(r => shortId(r.event_id)+"<br>"+displayTime(r)), hovertemplate: "%{text}<br>Requested load: %{y} A<extra></extra>" });
  for (const [status, y, color, symbol, label] of [["intent", 3, "#efbc72", "diamond-open", "Requested"], ["succeeded", 2, "#6edbc9", "circle", "Completed"], ["not_executed", 1, "#cbb9ff", "square-open", "Not executed"], ["failed", 0, "#fb978d", "x", "Error / state uncertain"]]) {
    const rows = controls.filter(r => (status === "not_executed" ? outcome(byId.get(r.event_id)) === "Not executed" : r.status === status && outcome(byId.get(r.event_id)) !== "Not executed") && xFor(r) !== null);
    traces.push({ type: "scatter", mode: "markers", name: label, x: rows.map(xFor), y: rows.map(() => y), xaxis: "x3", yaxis: "y3", marker: { color, symbol, size: 10 }, customdata: rows.map(r => r.event_id), text: rows.map(r => `${shortId(r.event_id)} · ${toolLabel(r.tool)}<br>${displayTime(r)}<br>${outcome(byId.get(r.event_id))}<br>${r.error || ""}${clockMode() === "simulation_elapsed" && !r.observed_at ? "<br>Position: "+placementLabel(r) : ""}`), hovertemplate: "%{text}<extra></extra>" });
  }
  const simulated=metadata.session_kind==="simulated";
  const modelRows=simulated&&operatorAuthorized?truthRows.filter(r=>modelX(r)!==null):[];
  for(const [field,name,color] of [["rb_remaining_fraction","Rb remaining · %","#e5bc75"],["impurity_remaining_fraction","Impurity remaining · %","#80d8d0"]]) {
    if(simulated&&operatorAuthorized) traces.push({type:"scatter",mode:"lines+markers",name,x:modelRows.map(modelX),
      y:modelRows.map(r=>num(r.state[field])===null?null:r.state[field]*100),xaxis:"x4",yaxis:"y4",
      line:{color,width:2},marker:{size:4},connectgaps:false,customdata:modelRows.map(r=>({model_sequence:r.sequence})),
      text:modelRows.map(r=>"Model snapshot S"+r.sequence+"<br>"+(isAbsoluteClock()?formatWallInstant(modelX(r)):clockMode()==="real_elapsed"?elapsedDetail(modelX(r),realElapsedName()):elapsedDetail(r.virtual_time_s/60,"Simulation elapsed time"))),
      hovertemplate:"%{text}<br>%{y:.2f}% of own initial inventory<extra>"+name+"</extra>"});
  }
  el("inventory-note").hidden=!simulated;
  el("inventory-unlock").hidden=operatorAuthorized;
  el("inventory-caption").textContent=operatorAuthorized?"Model-only remaining stock: each substance as % of its own initial inventory, not measured mass fractions.":"Remaining stock is locked; ordinary plots continue without internal data.";
  el("chart-auto-y-4").parentElement.hidden=!simulated||!operatorAuthorized;
  const base={automargin:true,showticklabels:false};
  const layout={paper_bgcolor:"#152330",plot_bgcolor:"#152330",font:{color:"#c9d8e5",size:11},
    margin:{t:65,r:30,b:45,l:110},height:680,hovermode:"closest",dragmode:"zoom",
    uirevision:metadata.session_id+":"+generation+":"+axis+":"+simulated,legend:{orientation:"h",x:0,y:1.13,font:{size:10}},
    xaxis:{...base,anchor:"y"},xaxis2:{...base,anchor:"y2",matches:"x"},
    xaxis3:{...base,anchor:"y3",matches:"x",showticklabels:!simulated},
    yaxis:{domain:simulated?[.75,1]:[.65,1],title:{text:"Pressure · mbar"},type:"log",tickformat:".3e",nticks:5,automargin:true},
    yaxis2:{domain:simulated?[.43,.68]:[.26,.56],title:{text:"Current · A"},automargin:true},
    yaxis3:{domain:simulated?[.24,.35]:[0,.17],title:{text:"Requests<br>and results"},tickvals:[0,1,2,3],
      ticktext:["Error / uncertain","Not executed","Completed","Requested"],range:[-.5,3.5],automargin:true}};
  if(simulated) {
    layout.xaxis4={...base,anchor:"y4",matches:"x",showticklabels:true};
    layout.yaxis4={domain:[0,.17],title:{text:"Model remaining · %"},range:[0,100],automargin:true};
    if(!modelRows.length) layout.annotations=[{xref:"paper",yref:"paper",x:.5,y:.085,showarrow:false,
      text:operatorAuthorized?"No model snapshots with this clock available":"Internal inventory locked · use Unlock internal inventory",font:{color:"#ffda85"}}];
  }
  viewLayout("chart",layout);
  if(simulated&&!modelRows.length) {
    layout.xaxis3.showticklabels=true;layout.xaxis3.title=layout.xaxis4.title;
    layout.xaxis4.showticklabels=false;layout.xaxis4.title=null;
  }
  applyY("chart",layout,traces);
  await guardedPlot("chart",()=>Plotly.react("chart",traces,layout,{responsive:true,displaylogo:false,scrollZoom:true}));
  el("chart").removeAllListeners("plotly_click");
  el("chart").on("plotly_click",data=>{const id=data.points[0]?.customdata;if(id?.model_sequence!==undefined) selectModel(id.model_sequence);else if(id) selectEvent(id);});
  watchTimeView("chart");chartReady=true;
}
async function drawVoltage() {
  const rows=observations.filter(r=>r.observation_kind==="power"&&xFor(r)!==null);
  const traces=[["native_ch1_voltage_setpoint_v","Native CH1 voltage limit · V","#c7a4e9"],["native_ch1_measured_voltage_v","Measured native CH1 voltage · V","#80c8e5"],["measured_ch2_voltage_v","Measured CH2 voltage · V","#a8d98c"]].map(([field,name,color])=>({
    type:"scatter",mode:"lines+markers",name,x:rows.map(xFor),y:rows.map(r=>num(r[field])),line:{color,width:2,shape:field.includes("setpoint")?"hv":"linear"},marker:{size:4},connectgaps:false,
    customdata:rows.map(r=>r.event_id),text:rows.map(r=>shortId(r.event_id)+"<br>"+displayTime(r)),
    hovertemplate:"%{text}<br>%{y} V<extra>"+name+"</extra>"}));
  const layout={paper_bgcolor:"#152330",plot_bgcolor:"#152330",font:{color:"#c9d8e5"},height:300,margin:{t:50,r:30,b:50,l:110},
    uirevision:metadata.session_id+":"+generation+":"+sharedView.wall,legend:{orientation:"h",y:1.2},xaxis:{anchor:"y",automargin:true},yaxis:{title:{text:"Voltage · V"},automargin:true}};
  viewLayout("voltage-chart",layout);applyY("voltage-chart",layout,traces);
  await guardedPlot("voltage-chart",()=>Plotly.react("voltage-chart",traces,layout,{responsive:true,displaylogo:false,scrollZoom:true}));
  el("voltage-chart").removeAllListeners("plotly_click");
  el("voltage-chart").on("plotly_click",data=>{const id=data.points[0]?.customdata;if(id) selectEvent(id);});
  watchTimeView("voltage-chart");
}
function clearTruth() {
  const had=truthRows.length>0||truthData!==null;
  truthMore=false;truthRows=[];truthData=null;truthCursor=0;truthGeneration=-1;truthRevision=null;
  el("truth-selected").textContent="No model snapshot selected.";el("truth-selected").classList.remove("highlight");
  el("truth-raw").textContent="";el("truth-parameters").textContent="";Plotly.purge("truth-chart");
  return had;
}

function readFailure(error) {
  if (error.name==="TimeoutError" || error.name==="AbortError") return "Dashboard data timeout; retained data may be stale. Retrying (this does not indicate MCP or equipment stopped)";
  if (error.message?.startsWith("HTTP 401") || error.message?.startsWith("HTTP 403")) return "Dashboard authorization required; reload and sign in. Retained data may be stale";
  if (error instanceof TypeError) return "Dashboard network read failed; retained data may be stale. Retrying";
  return "Dashboard read failed: "+String(error.message||error).replace(/[.]+$/,"")+". Retained data may be stale; retrying";
}
function resetPublicState(nextGeneration=-1) {
  generation=nextGeneration;totalRecords=0;overviewReady=false;historyComplete=false;
  exactByNumber.clear();overviewByNumber.clear();pageCoverage.clear();failedPages.clear();
  pageQueue.length=0;queuedPages.clear();inFlightPages.clear();activePageRequests=0;
  events=[];observations=[];controls=[];decisions=[];byId.clear();recordNumbers.clear();intentsByCall.clear();resultsByCall.clear();
  selectedId=null;historyWindowEnd=0;historyPinnedNewest=true;
  resetTimeView("chart");resetTimeView("truth-chart");clearTruth();
}
function applyPublicContext(data,changed) {
  el("view-mode").textContent=data.recording_view==="saved_recording"?"SAVED RUN":"LIVE VIEW · current process";
  metadata=data.metadata||metadata;
  serverTime=data.server_time||{available:false,iana_time_zone:null,message:"Server local time is unavailable."};
  configureClockOptions();
  runManagement=data.run_management||{};updateManagement();
  if(metadata.session_id&&clockInitializedFor!==metadata.session_id) {
    el("clock").value=metadata.session_kind==="simulated"?"simulation_elapsed":"browser";
    resetTimeView("chart");resetTimeView("truth-chart");clockInitializedFor=metadata.session_id;
  }
  const simulated=metadata.session_kind==="simulated",live=metadata.session_kind==="live";
  el("mode").className="mode-strip"+(live?" live":"");
  el("mode").replaceChildren(text("div",simulated?"◈ SIMULATION":live?"◆ LIVE HARDWARE":"? FIXTURE / SOURCE UNKNOWN"),text("small",live?"Hardware-source recording. This banner does NOT mean output is energized or measurements are current.":"Simulated equipment only — no real equipment is connected to this recording."));
  el("decision-heading").textContent="Decisions and reasons · attributed per record";
  el("title").textContent=metadata.label||"Observations, actions, decisions";
  el("provenance").textContent=metadata.session_kind?`Initial caller: ${actor()} · Individual records identify agent, human or independent MCP sampling.`:"Waiting to learn who made the requests.";
  el("session-details").textContent=`Session ${metadata.session_id||"not available"} · ${metadata.session_kind||"unknown source"} · Run created ${metadata.created_at?formatUtcInstant(metadata.created_at):"not recorded"}. ${live?"Hardware-source records do not establish current output state.":"Synthetic, uncalibrated data; not hardware evidence. Scripted demonstrations are not AI blind runs."} ${serverTime.message||""}`;
  el("decision-caption").textContent="Submitted reasons in the displayed 200-record window, with links to the readings used.";
  el("provenance").className=metadata.session_kind&&!live?"banner synthetic":"banner";
  el("source").textContent=`${data.recording_view==="saved_recording"?"SAVED RECORDING / REPLAY VIEW":"PROCESS SESSION RECORDS"} · File checked ${new Date().toISOString()} · ${changed?"Records updated":"No new records this check"} · Read-only source: ${data.source||"not configured"}`;
  document.dispatchEvent(new CustomEvent("dashboard-session-update",{detail:{operatorAuthorized,metadata}}));
}
function updateLoadingStatus(data={}) {
  const retry=failedPages.size?` · ${failedPages.size} range(s) retrying`:"";
  const state=historyComplete?"Exact history caught up":overviewReady?"Overview ready · loading exact history":"Loading whole-run overview";
  el("status").textContent=`${state} · exact ${exactByNumber.size}/${totalRecords} · ${activePageRequests} page request(s) active${retry}${data.message?"\n"+data.message:""}${data.errors?`\n${data.errors} unreadable record(s): ${data.last_error}`:""}`;
  el("status").className=data.status==="error"||data.errors?"error":"";
}
function mergeRecords(records,target) {
  for(const event of records||[]) if(Number.isInteger(event.record_number)&&event.record_number>0) target.set(event.record_number,event);
}
function requestOverviewSoon() {
  clearTimeout(overviewTimer);overviewTimer=setTimeout(()=>{pollOverview().catch(showViewError);},0);
}
function queueMissingPages() {
  for(let start=0;start<totalRecords;start+=PUBLIC_PAGE_SIZE) {
    const requiredEnd=Math.min(start+PUBLIC_PAGE_SIZE,totalRecords),key=`${generation}:${start}`;
    if((pageCoverage.get(start)||0)>=requiredEnd||queuedPages.has(key)||inFlightPages.has(key)) continue;
    pageQueue.push({start,generation});queuedPages.add(key);
  }
  pumpPageWorkers();
}
function pumpPageWorkers() {
  while(pageWorkerCount<PUBLIC_PAGE_WORKERS&&pageQueue.length) {pageWorkerCount++;runPageWorker().catch(showViewError);}
}
async function runPageWorker() {
  try {
    while(pageQueue.length) {
      const job=pageQueue.shift(),key=`${job.generation}:${job.start}`;queuedPages.delete(key);inFlightPages.add(key);activePageRequests=inFlightPages.size;updateLoadingStatus();
      try {
        const response=await fetch(`/api/session/page?start=${job.start}&limit=${PUBLIC_PAGE_SIZE}&generation=${job.generation}&${runQuery}`,{cache:"no-store",signal:AbortSignal.timeout(10000)});
        const data=await response.json();
        if(!response.ok) throw new Error(`HTTP ${response.status}`);
        if(job.generation!==generation) continue;
        if(data.reset||data.generation!==generation) {resetPublicState(data.generation);requestOverviewSoon();continue;}
        mergeRecords(data.events,exactByNumber);pageCoverage.set(job.start,data.end);failedPages.delete(job.start);
      } catch(error) {
        if(job.generation===generation) failedPages.set(job.start,readFailure(error));
      } finally {
        inFlightPages.delete(key);activePageRequests=inFlightPages.size;schedulePublicRender();updateLoadingStatus();
      }
    }
  } finally {pageWorkerCount--;if(pageQueue.length)pumpPageWorkers();}
}
async function pollOverview() {
  if(overviewRunning) return;
  clearTimeout(overviewTimer);overviewRunning=true;
  const revision=++overviewRevision,startedLoginRevision=loginRevision;
  try {
    const response=await fetch(`/api/session/overview?${runQuery}`,{cache:"no-store",signal:AbortSignal.timeout(10000)});
    if(!response.ok) {const problem=await response.json();throw new Error(`HTTP ${response.status}: ${problem.error||"Dashboard request failed"}`);}
    const data=await response.json();
    if(revision!==overviewRevision) return;
    const changed=generation!==data.generation||totalRecords!==data.total_records||!overviewReady;
    const wasAuthorized=operatorAuthorized;
    if(generation!==data.generation) resetPublicState(data.generation);
    if(startedLoginRevision===loginRevision) operatorAuthorized=data.operator_authorized===true;
    if(wasAuthorized&&!operatorAuthorized) clearTruth();
    totalRecords=data.total_records||0;overviewReady=true;
    overviewByNumber.clear();mergeRecords(data.overview?.events,overviewByNumber);
    if(historyPinnedNewest) historyWindowEnd=totalRecords;
    applyPublicContext(data,changed);queueMissingPages();
    if(changed||wasAuthorized!==operatorAuthorized) schedulePublicRender();
    updateLoadingStatus(data);
  } catch(error) {el("status").textContent=readFailure(error);el("status").className="error";}
  finally {overviewRunning=false;overviewTimer=setTimeout(()=>{pollOverview().catch(showViewError);},1000);}
}
let truthPollRunning=false,truthPollTimer=null,truthRenderPending=false;
function requestTruthSoon(){clearTimeout(truthPollTimer);truthPollTimer=setTimeout(()=>{pollTruthLoop().catch(showViewError);},0);}
function scheduleTruthRender() {
  if(truthRenderPending)return;truthRenderPending=true;
  requestAnimationFrame(async()=>{truthRenderPending=false;if(truthData)await drawTruth(truthData);});
}
async function pollTruth(force=false) {
  const section=el("inside");section.hidden=metadata.session_kind!=="simulated";
  if (section.hidden) return clearTruth();
  el("truth-content").hidden=!operatorAuthorized;el("truth-locked").hidden=operatorAuthorized;
  if(!operatorAuthorized) return clearTruth();
  if (force) {if(truthData) await drawTruth(truthData,true);return;}
  const requestedLoginRevision=loginRevision,requestedGeneration=generation,requestedSession=metadata.session_id;
  try {
    const response=await fetch(`/api/simulation-state?after=${truthCursor}&generation=${truthGeneration}&${runQuery}`,{cache:"no-store",signal:AbortSignal.timeout(5000)});
    if(requestedLoginRevision!==loginRevision||requestedGeneration!==generation||requestedSession!==metadata.session_id) return false;
    if(response.status===401||response.status===403) {operatorAuthorized=false;el("truth-content").hidden=true;el("truth-locked").hidden=false;clearTruth();return true;}
    if(!response.ok) throw new Error("HTTP "+response.status);
    const data=await response.json();
    if(!operatorAuthorized||requestedLoginRevision!==loginRevision||requestedGeneration!==generation||requestedSession!==metadata.session_id) return false;
    if(data.reset) {truthRows=[];truthRevision=null;}
    truthCursor=data.cursor??truthCursor;truthGeneration=data.generation??truthGeneration;
    truthMore=Boolean(data.has_more);
    if(data.status==="mismatch" || data.status==="unavailable" || data.status==="error") truthRows=[];
    const changed=data.reset||(data.rows?.length||0)>0;
    truthRows.push(...(data.rows||[]));
    truthData={...data,rows:truthRows};
    el("truth-status").textContent=(truthMore?"Loading model history":"Caught up")+ " · "+truthRows.length+" model snapshots · "+data.status+" · "+(data.message||"Model snapshots available")+(data.errors?" · Invalid rows: "+data.errors:"");
    if(changed){scheduleTruthRender();schedulePublicRender();}
    return changed;
  } catch(error) {truthMore=false;el("truth-status").textContent=readFailure(error);}
}
async function pollTruthLoop() {
  if(truthPollRunning)return;truthPollRunning=true;
  try {await pollTruth();}
  finally {truthPollRunning=false;truthPollTimer=setTimeout(()=>{pollTruthLoop().catch(showViewError);},truthMore?0:1000);}
}
async function drawTruth(data,force=false) {
    const allRows=data.rows||[];
    const rows=allRows.filter(r=>modelX(r)!==null);
    const revision = JSON.stringify([data.status,data.generation,rows.length,rows.at(-1)?.sequence,data.run_id]);
    if (!force && revision === truthRevision) return;
    truthRevision = revision;
    if (data.status !== "ready" || !allRows.length) { Plotly.purge("truth-chart"); return; }
    const p = data.parameters || rows[0]?.parameters || {};
    el("truth-time-note").textContent=isAbsoluteClock() ? (allRows.length-rows.length)+" model snapshots lack recorded wall time and are omitted; Simulation elapsed time retains snapshots with model time." : clockMode()==="real_elapsed" ? "Real elapsed time uses each snapshot recording time relative to run creation; it is not model time." : "Model and public Simulation elapsed time axes are synchronized. Synthetic observed_at is never treated as real wall time.";
    el("truth-parameters").textContent = "Synthetic loading: initial Rb " + fmt(p.initial_rb_effective_units) + " units; initial impurity " + fmt(p.initial_impurity_effective_units) + " units; ratio " + fmt(p.initial_rb_to_impurity_effective_ratio) + " (not measured mass/composition). Fixed resistance " + fmt(p.resistance_ohm) + " Ω. Thermal state is normalized, not kelvin.";
    const series = (field,name,color,panel,scale=1) => ({type:"scatter",mode:"lines",name,
      x:rows.map(modelX),y:rows.map(r=>num(r.state[field]) === null ? null : r.state[field]*scale),
      xaxis:panel === 1 ? "x" : "x"+panel,yaxis:panel === 1 ? "y" : "y"+panel,
      line:{color,width:2},connectgaps:false,customdata:rows.map(r=>r.sequence),text:rows.map(r=>"Model snapshot S"+r.sequence+"<br>"+(isAbsoluteClock()?formatWallInstant(modelX(r)):clockMode()==="real_elapsed"?elapsedDetail(modelX(r),realElapsedName()):elapsedDetail(r.virtual_time_s/60,"Simulation elapsed time"))),
      hovertemplate:"%{text}<br>%{y}<extra>"+name+"</extra>"});
    const traces = [
      series("rb_release_rate_effective_units_per_s","Rb release · synthetic units/s","#e5bc75",1),
      series("impurity_release_rate_effective_units_per_s","Impurity release · synthetic units/s","#80d8d0",1),
      series("total_pressure_mbar","Total model pressure · mbar","#ffffff",2),
      series("rb_pressure_mbar","Rb model pressure · mbar","#e5bc75",2),
      series("impurity_pressure_mbar","Impurity model pressure · mbar","#80d8d0",2),
      series("background_pressure_mbar","Background model pressure · mbar","#91a9ca",2),
      series("thermal_state","Thermal state · normalized, not kelvin","#c7a4e9",3)
    ];
    const truthTicks = elapsedAxis(rows.map(r=>r.virtual_time_s/60), el("truth-chart").layout?.xaxis?.autorange ? null : el("truth-chart").layout?.xaxis?.range);
    const base={gridcolor:"#304254",automargin:true};
    const truthLayout={paper_bgcolor:"#152330",plot_bgcolor:"#152330",font:{color:"#c9d8e5"},height:640,
      margin:{t:90,b:50,l:95,r:25},uirevision:metadata.session_id+":"+data.run_id,
      legend:{orientation:"h",y:1.18,font:{size:10}},
      xaxis:{...base,...truthTicks,title:null,anchor:"y",showticklabels:false},
      xaxis2:{...base,...truthTicks,title:null,anchor:"y2",matches:"x",showticklabels:false},
      xaxis3:{...base,...truthTicks,anchor:"y3",matches:"x",title:truthTicks.title},
      yaxis:{...base,domain:[.72,1],title:{text:"Release · units/s"}},
      yaxis2:{...base,domain:[.38,.64],type:"log",tickformat:".3e",title:{text:"Model pressure · mbar"}},
      yaxis3:{...base,domain:[0,.29],title:{text:"Thermal · normalized"}}
    };
    viewLayout("truth-chart",truthLayout,rows.map(r=>r.virtual_time_s/60));
    applyY("truth-chart",truthLayout,traces);
    await guardedPlot("truth-chart",()=>Plotly.react("truth-chart",traces,truthLayout,{responsive:true,displaylogo:false,scrollZoom:true}));
    watchTimeView("truth-chart", rows.map(r=>r.virtual_time_s/60));
    el("truth-chart").removeAllListeners("plotly_click");
    el("truth-chart").on("plotly_click",clicked=>selectModel(clicked.points[0]?.customdata));
}
function selectModel(sequence) {
  if(!operatorAuthorized) return;
  const row=truthRows.find(r=>r.sequence===sequence);if(!row) return;
  el("truth-selected").textContent="Model snapshot S"+row.sequence+" · "+(clockMode()==="simulation_elapsed"?elapsedDetail(row.virtual_time_s/60,"Simulation elapsed time"):isAbsoluteClock()?formatWallInstant(row.recorded_at):elapsedDetail(realElapsedMinutes(row.recorded_at),realElapsedName()))+" · Rb remaining "+fmt(num(row.state.rb_remaining_fraction)===null?null:row.state.rb_remaining_fraction*100)+"% · Impurity remaining "+fmt(num(row.state.impurity_remaining_fraction)===null?null:row.state.impurity_remaining_fraction*100)+"%. Each relative to its own initial inventory; not a supporting observation.";
  el("truth-raw").textContent=JSON.stringify(row,null,2);el("truth-selected").classList.add("highlight");
  el("truth-selected").scrollIntoView({behavior:"smooth",block:"center"});
}

el("clock").addEventListener("change",()=>{
  sharedView.wall=isAbsoluteClock();resetTimeView();updatePanels();
  if(selectedId) selectEvent(selectedId,false,false).catch(showViewError);
  el("truth-selected").textContent="Time axis changed; select a model snapshot to refresh its displayed time.";
  el("truth-selected").classList.remove("highlight");el("truth-raw").textContent="";
  redrawAll().catch(showViewError);
});
for(const id of Object.keys(chartStates)) {
  const [selectId]=viewIds(id);
  el(selectId).addEventListener("change",()=>{
    captureRange(id);sharedView.mode=el(selectId).value;updateWidth();
    redrawAll().catch(showViewError);
  });
  el(id==="chart"?"fit":id==="truth-chart"?"truth-fit":id==="voltage-chart"?"voltage-fit":"token-fit").addEventListener("click",()=>{
    resetTimeView();redrawAll().catch(showViewError);
  });
  const container=el(id==="chart"?"main-auto-y":id==="truth-chart"?"truth-auto-y":id==="voltage-chart"?"voltage-auto-y":"token-auto-y");
  const labels=id==="chart"?[["Pressure",1],["Current",2],["Inventory",4]]:id==="truth-chart"?[["Release",1],["Model pressure",2],["Thermal",3]]:id==="voltage-chart"?[["Voltage",1]]:[["Per report",1],["Cumulative",2]];
  labels.forEach(([label,panel])=>{
    const index=panel-1;
    const control=document.createElement("input");control.type="checkbox";control.checked=autoY[id][index];
    control.id=id+"-auto-y-"+(index+1);
    control.addEventListener("change",()=>{autoY[id][index]=control.checked;redrawAll().catch(showViewError);});
    const wrapper=text("label"," Auto-Y "+label+" ");wrapper.prepend(control);container.append(wrapper);
  });
}

let runManagement = {};
let managementBusy = false;
const collection = el("run-collection");
collection.value = new URLSearchParams(location.search).get("archived") === "true" ? "archived" : "active";
function updateManagement() {
  const reason=managementBusy ? "Run update in progress." : !operatorAuthorized ? "Log in to manage runs." : !runManagement.name ? "Waiting for selected run details." : runManagement.current ? "This is the current recording run: rename is allowed, but archive/delete are unavailable. Select a saved run to archive it." : "Selected saved run: management is available.";
  el("management-reason").textContent=reason;
  el("archive-run").title=reason;
  el("rename-run").disabled = managementBusy || !operatorAuthorized;
  el("restore-run").disabled = managementBusy || !operatorAuthorized;
  el("archive-run").disabled = managementBusy || !operatorAuthorized || !runManagement.name || !!runManagement.current;
  el("archive-run").hidden = !!runManagement.archived;
  el("restore-run").hidden = !runManagement.archived;
  el("delete-run").hidden = !runManagement.archived;
  el("delete-run").disabled = managementBusy || !operatorAuthorized || !!runManagement.current;
}
async function manageRun(operation, input = null) {
  if(managementBusy) return;
  if ((operation === "rename" || operation === "delete") && input === null) {
    const deleting=operation === "delete";
    el("management-editor").hidden=false;
    el("management-editor").dataset.operation=operation;
    el("management-label").textContent=deleting ? `Permanently delete ALL run files, including observer files? This cannot be undone. Type folder name: ${runManagement.name}` : "New display name (raw folder and record IDs stay unchanged)";
    el("management-input").value=deleting ? "" : runManagement.display_name || "";
    el("management-submit").textContent=deleting ? "Permanently delete" : "Save name";
    el("management-input").focus();return;
  }
  const body = {run:selectedRun};
  const availableRuns=[...el("run-picker").options].filter(option=>!option.disabled).map(option=>option.value);
  const selectedIndex=availableRuns.indexOf(selectedRun);
  const afterArchive=availableRuns[selectedIndex-1] ?? availableRuns[selectedIndex+1] ?? "";
  if(operation === "rename") body.display_name=input;
  if(operation === "delete") body.confirmation=input;
  managementBusy=true;updateManagement();
  el("management-status").textContent=`Sending ${operation} request…`;
  el("management-status").className="muted";
  try {
    const response = await fetch(`/api/runs/${operation}`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});
    let result;
    try {result=await response.json();} catch {throw new Error(`HTTP ${response.status}: invalid dashboard response`);}
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${result.error || "Run action rejected"}`);
    if (result.deleted) {window.location.assign("/dashboard");return;}
    Object.assign(runManagement,result);el("management-editor").hidden=true;
    if(operation === "archive") {
      const url=new URL(location.href);
      url.searchParams.set("archived",String(collection.value === "archived"));
      if(afterArchive) url.searchParams.set("run",afterArchive);else url.searchParams.delete("run");
      window.location.assign(url.href);return;
    }
    if(operation === "restore") {
      collection.value=result.archived ? "archived" : "active";
      const url=new URL(location.href);url.searchParams.set("archived",String(result.archived));history.replaceState(null,"",url);
    }
    if(result.display_name) el("title").textContent=result.display_name;
    updateManagement();await refreshRuns();
    el("management-status").textContent=`${operation} completed. Raw records and equipment are unchanged.`;
  } catch(error) {
    el("management-status").className="error";
    el("management-status").textContent=error.name === "TimeoutError" || error.name === "AbortError" ? "Run update timed out; outcome unknown. Refresh the run list before retrying." : `Run update failed: ${error.message}`;
  } finally {managementBusy=false;updateManagement();}
}
el("management-editor").addEventListener("submit",event=>{event.preventDefault();manageRun(el("management-editor").dataset.operation,el("management-input").value);});
el("management-cancel").addEventListener("click",()=>{el("management-editor").hidden=true;});
for (const operation of ["rename","archive","restore","delete"]) el(operation+"-run").addEventListener("click",()=>manageRun(operation));
collection.addEventListener("change",refreshRuns);
async function refreshRuns() {
  try {
    const response = await fetch("/api/runs?archived=" + (collection.value === "archived"), {cache:"no-store", signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error("Cannot load run list");
    const data = await response.json();
    const picker = el("run-picker");
    picker.replaceChildren();
    for (const run of data.runs) {
      const option = text("option", run.label + (run.available ? "" : " — Unavailable: " + run.reason));
      option.value = run.key; option.disabled = !run.available;
      picker.append(option);
    }
    if (![...picker.options].some(option => option.value === selectedRun)) {
      const missing = text("option", "Viewing a run outside this list: " + (selectedRun || "current"));
      missing.value = selectedRun; missing.disabled = true; picker.append(missing);
    }
    picker.value = selectedRun;
    el("run-help").textContent = (data.live_view_available ? "Live view follows this process's records. " : "Saved-recording preview only; no live acquisition is attached. ") + "Selecting a run reloads only this page. It never starts, stops or resumes recording or equipment. Unavailable folders need the supported metadata/events format.";
  } catch (error) { el("run-help").textContent = error.message + ". Current view is unchanged."; }
}
el("run-picker").addEventListener("change", event => {
  const url = new URL(window.location.href);
  url.searchParams.set("archived",String(collection.value === "archived"));
  if (event.target.value) url.searchParams.set("run",event.target.value);
  else url.searchParams.delete("run");
  // A new document discards every old fetch, cursor, selected detail and chart.
  window.location.assign(url.href);
});
el("refresh-runs").addEventListener("click", refreshRuns);
refreshRuns();
pollOverview().catch(showViewError);
pollTruthLoop().catch(showViewError);
