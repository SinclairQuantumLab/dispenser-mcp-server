"use strict";
// Numeric coordinates remain minutes; only displayed elapsed labels change.
function elapsedLabel(minutes, hours = minutes >= 60, showSeconds = false, fractional = false) {
  const rawSeconds = Math.max(0, Number((minutes * 60).toFixed(3)));
  const seconds = fractional ? Math.floor(rawSeconds) : Math.round(rawSeconds);
  const fraction = fractional ? (rawSeconds - seconds).toFixed(3).slice(1) : "";
  const pad = value => String(value).padStart(2, "0");
  return hours ? pad(Math.floor(seconds / 3600)) + ":" + pad(Math.floor(seconds / 60) % 60) + (showSeconds ? ":" + pad(seconds % 60) + fraction : "")
    : pad(Math.floor(seconds / 60)) + ":" + pad(seconds % 60) + fraction;
}
function elapsedDetail(minutes) {
  return "Elapsed " + elapsedLabel(minutes, minutes >= 60, true) + (minutes >= 60 ? " HH:MM:SS" : " MM:SS") + " · " + (minutes * 60).toFixed(3) + " s";
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
    title:{text:"Virtual elapsed · " + (hours ? (showSeconds ? (fractional ? "HH:MM:SS.sss" : "HH:MM:SS") : "HH:MM") + " (hours may exceed 24)" : (fractional ? "MM:SS.sss" : "MM:SS"))}};
}
const timeViews = {
  chart: {mode:"full", range:null, width:null, busy:0, queue:Promise.resolve(), wall:false},
  "truth-chart": {mode:"full", range:null, width:null, busy:0, queue:Promise.resolve(), wall:false}
};
function timeNumber(value, wall) {
  if (typeof value === "number") return value;
  if (!wall) return Number(value);
  const text = String(value).replace(" ", "T");
  return Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(text) ? text : text+"Z");
}
function timeRange(state, values) {
  const points = values.map(v=>timeNumber(v,state.wall)).filter(Number.isFinite);
  const latest = points.length ? points.reduce((a,b)=>Math.max(a,b)) : (state.range?.[1] ?? 0);
  const earliest = points.length ? points.reduce((a,b)=>Math.min(a,b)) : latest;
  const fallback = state.wall ? 60000 : 1;
  if (state.mode === "full") state.range = latest > earliest ? [earliest,latest] : [latest-fallback/2,latest+fallback/2];
  else if (state.mode === "rolling") {
    state.width = state.width > 0 ? state.width : fallback;
    state.range = [latest-state.width,latest];
  } else if (!state.range) state.range = [latest-fallback/2,latest+fallback/2];
  return state.range;
}
function viewIds(id) {
  return id === "chart" ? ["time-view","time-width",4] : ["truth-time-view","truth-time-width",3];
}
function updateWidth(id) {
  const state=timeViews[id], [,widthId]=viewIds(id);
  const width=state.range ? state.range[1]-state.range[0] : (state.width || (state.wall?60000:1));
  el(widthId).textContent="Visible width: "+(width/(state.wall?1000:1/60)).toFixed(3)+" s";
}
function captureRange(id) {
  const state=timeViews[id], raw=el(id).layout?.xaxis?.range;
  if (raw?.length === 2) {
    const range=raw.map(v=>timeNumber(v,state.wall));
    if (range.every(Number.isFinite) && range[1]>range[0]) {state.range=range;state.width=range[1]-range[0];}
  }
}
function resetTimeView(id) {
  const state=timeViews[id];
  state.mode="full";state.range=null;state.width=null;
  el(viewIds(id)[0]).value="full";
}
function guardedPlot(id, operation) {
  const state=timeViews[id];
  const next=state.queue.then(async()=>{state.busy++;try{return await operation();}finally{state.busy--;}});
  state.queue=next.catch(()=>{});
  return next;
}
function viewLayout(id, layout, values) {
  const state=timeViews[id], range=timeRange(state,values), count=viewIds(id)[2];
  const rendered=state.wall ? range.map(v=>new Date(v).toISOString()) : range;
  const ticks=state.wall ? {} : elapsedAxis(values,range);
  for(let i=1;i<=count;i++) {
    const key=i===1?"xaxis":"xaxis"+i;
    Object.assign(layout[key],ticks,{range:rendered,autorange:false});
    if(i<count) layout[key].title=null;
  }
  updateWidth(id);
}
function watchTimeView(id, values) {
  const graph=el(id), state=timeViews[id], [selectId,,count]=viewIds(id);
  graph.removeAllListeners("plotly_relayout");
  graph.on("plotly_relayout", change=>{
    if(state.busy || !Object.keys(change).some(k=>/^xaxis[0-9]*\.(range|autorange)/.test(k))) return;
    captureRange(id);
    if(state.mode==="full") {state.mode="fixed";el(selectId).value="fixed";}
    updateWidth(id);
    if(state.wall) return;
    const ticks=elapsedAxis(values,state.range), update={};
    for(let i=1;i<=count;i++) {
      const axis=i===1?"xaxis":"xaxis"+i;
      for(const key of ["tickmode","tickvals","ticktext"]) update[axis+"."+key]=ticks[key];
      if(i===count) update[axis+".title"]=ticks.title;
    }
    guardedPlot(id,()=>Plotly.relayout(graph,update)).catch(showViewError);
  });
}
function showViewError(error) { el("status").textContent="Chart update failed: "+error.message; }
const el = id => document.getElementById(id);
const selectedRun = new URLSearchParams(window.location.search).get("run") || "";
const runQuery = "run=" + encodeURIComponent(selectedRun);
let cursor = 0, generation = -1, metadata = {};
let events = [], observations = [], controls = [], decisions = [];
let chartReady = false, rendering = false, clockInitializedFor = null;
const byId = new Map();
const recordNumbers = new Map();
let selectedId = null, truthRevision = null, truthRows = [];
let truthCursor=0, truthGeneration=-1, truthMore=false, truthData=null;
let pollRunning=false, pollTimer=null;
const shortId = id => recordNumbers.has(id) ? "#" + String(recordNumbers.get(id)).padStart(3, "0") : "Unloaded record";
const actor = () => ({agent:"Agent",scripted:"Script",human:"Human"})[metadata.source] || "Caller";
const toolLabel = name => ({
read_vacuum_pressure:"Read vacuum pressure",read_dispenser_power_state:"Read power state",
prepare_dispenser:"Prepare dispenser",prepare_dispenser_power:"Prepare dispenser power",
enable_dispenser_output:"Enable output",set_dispenser_current:"Set load current",
set_dispenser_load_current:"Set load current",shutdown_dispenser:"Turn output off",shutdown_dispenser_power:"Turn output off",
reload_dispenser_current_limit:"Reload operator current cap (no actuation)",
record_conditioning_decision:"Record a judgment (no hardware action)"
})[name] || (name || "Record").replaceAll("_"," ");
function outcome(event) {
  if (event.kind === "decision") return actor() + " decision";
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
const xFor = row => el("clock").value === "virtual" ? virtualMinutes(row) : row.recorded_at;

function selectEvent(id, jump = false) {
  const event = byId.get(id);
  if (!event) { el("raw").textContent = `Referenced event ${id} is not in this session file.`; return; }
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
  readable.append(text("p", usageText(decision?.token_usage_id ? {usage_id:decision.token_usage_id,total_tokens:decision.total_tokens,input_tokens:decision.input_tokens,output_tokens:decision.output_tokens,cached_input_tokens:decision.cached_input_tokens,model:decision.token_model} : null), "muted"));
  if(decision?.background) readable.append(text("p","Background: "+decision.background));
  if(decision?.confidence_claim) readable.append(text("p","Caller confidence: "+decision.confidence_value+" in claim: "+decision.confidence_claim));
  if(decision?.completion_outcome) readable.append(text("p",actor()+" assessment: "+decision.completion_outcome+" · Dispenser response: "+decision.dispenser_response+" (not proof of output OFF)."));
  if (chosenAction) readable.append(text("p", actor() + " chose: " + chosenAction));
  if (statedReason) readable.append(text("p", "Stated reason: " + statedReason));
  if(num(event.applied_max_load_current_A)!==null) readable.append(text("p", "Operator current cap: "+fmt(event.previous_max_load_current_A)+" → "+fmt(event.applied_max_load_current_A)+" A; effective "+fmt(event.effective_max_load_current_A)+" A. "+event.notice));
  if(event.error) readable.append(text("p","MCP error: "+event.error,"error"));
  if (num(args.target_current_a) !== null) readable.append(text("p", "Requested load-current target: " + fmt(args.target_current_a) + " A"));
  if (num(args.expected_current_a) !== null) readable.append(text("p", "Expected previous load-current setting: " + fmt(args.expected_current_a) + " A (must match before the change)."));
  readable.append(text("p", "MCP receipt: " + (event.received_at || event.recorded_at) + " · Source observation: " + (observations.find(r => r.event_id === id)?.observed_at || event.observed_at || "not available")));
  if (el("clock").value === "virtual") readable.append(text("p", virtualMinutes(event) === null ? "Virtual placement unavailable; real decision/receipt timestamps remain unchanged." : "Virtual placement: " + placementLabel(event), "muted"));
  const links = text("div", "", "links");
  for (const related of events.filter(e => (event.call_id && e.call_id === event.call_id) || (event.decision_id && e.decision_id === event.decision_id))) {
    if (related.event_id !== id) links.append(eventButton(related.event_id, shortId(related.event_id) + " " + outcome(related), false));
  }
  for (const basis of supportingIds) links.append(eventButton(basis, "Supporting observation " + shortId(basis), false));
  readable.append(links);
  const row = observations.find(r => r.event_id === id);
  if (row) readable.append(text("p", Object.entries(row).filter(([key,value]) => value !== null && ["pressure_mbar","commanded_load_current_limit_a","native_ch1_measured_current_a","native_ch1_measured_voltage_v","output_enabled"].includes(key)).map(([key,value]) => ({
pressure_mbar: "Total pressure: " + Number(value).toExponential(3) + " mbar",
commanded_load_current_limit_a: "Returned load-current setting: " + value + " A",
native_ch1_measured_current_a: "Measured CH1 current: " + value + " A",
native_ch1_measured_voltage_v: "Measured CH1 voltage: " + value + " V",
output_enabled: "Output: " + (value ? "ON" : "OFF")
})[key]).join(" · ")));
  el("raw").textContent = JSON.stringify(event, null, 2);
  el("selection-label").textContent = `${shortId(id)} · Recorded ${event.recorded_at} · Full ID in dashboard record fields`;
  if (jump && chartReady) {
    const at = xFor(event);
    if (at !== null) {
      const range = el("clock").value === "virtual" ? [Math.max(0, at - 1), at + 1] : [new Date(Date.parse(at) - 30000).toISOString(), new Date(Date.parse(at) + 30000).toISOString()];
      timeViews.chart.mode="fixed";el("time-view").value="fixed";
      timeViews.chart.range=range.map(v=>timeNumber(v,timeViews.chart.wall));
      timeViews.chart.width=timeViews.chart.range[1]-timeViews.chart.range[0];
      draw().catch(showViewError);
    }
  }
  el("selected").scrollIntoView({ behavior: "smooth", block: "start" });
}

function eventButton(id, label, jump = true) {
  const button = text("button", label); button.addEventListener("click", () => selectEvent(id, jump)); return button;
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
  const summary=reportedUsage(decisions), rows=summary.reports;
  el("token-chart").hidden=!rows.length;
  el("token-coverage").textContent=!rows.length ? "Token usage not reported. Cumulative usage unavailable; missing reports are not zero." : rows.length+" unique usage reports across "+decisions.length+" decision records; "+summary.missing+" without usage; "+summary.duplicates+" repeated IDs. Reported cumulative subset: "+summary.total+" tokens.";
  el("token-warning").textContent=summary.conflicts.length ? "Conflicting repeated usage IDs: "+summary.conflicts.map(r=>r.token_usage_id+" ("+shortId(r.event_id)+")").join(", ")+". First values retained." : "";
  if(!rows.length) {Plotly.purge("token-chart");return;}
  const trace=(field,name,panel)=>({type:"scatter",mode:"lines+markers",name,
    x:rows.map(r=>recordNumbers.get(r.event_id)),y:rows.map(r=>r[field]),
    xaxis:panel===1?"x":"x2",yaxis:panel===1?"y":"y2",
    customdata:rows.map(r=>r.event_id),text:rows.map(r=>shortId(r.event_id)+" · "+r.token_usage_id),
    hovertemplate:"%{text}<br>%{y} reported tokens<extra>"+name+"</extra>"});
  await Plotly.react("token-chart",[trace("total_tokens","Tokens per reported decision",1),trace("cumulative_tokens","Cumulative reported tokens",2)],{
    paper_bgcolor:"#152330",plot_bgcolor:"#152330",font:{color:"#c9d8e5"},height:440,
    margin:{l:90,r:25,t:35,b:50},uirevision:metadata.session_id,
    xaxis:{anchor:"y",showticklabels:false},xaxis2:{anchor:"y2",matches:"x",title:{text:"Record number · first occurrence of each usage ID"}},
    yaxis:{domain:[.58,1],title:{text:"Tokens / report"}},yaxis2:{domain:[0,.42],title:{text:"Cumulative tokens"}}
  },{responsive:true,displaylogo:false});
  el("token-chart").removeAllListeners("plotly_click");
  el("token-chart").on("plotly_click",event=>{const id=event.points[0]?.customdata;if(id)selectEvent(id);});
}

function updatePanels() {
  const p = observations.filter(r => num(r.pressure_mbar) !== null).at(-1);
  const power = observations.filter(r => r.observation_kind === "power").at(-1);
  el("pressure").textContent = p ? `${p.pressure_mbar.toExponential(3)} mbar` : "—";
  el("set").textContent = power ? `${fmt(power.commanded_load_current_limit_a)} A` : "—";
  el("actual").textContent = power ? `${fmt(power.native_ch1_measured_current_a)} A` : "—";
  el("output").textContent = power?.output_enabled === true ? "ON" : power?.output_enabled === false ? "OFF" : "—";
  el("decisions").replaceChildren();
  for (const row of [...decisions].reverse()) {
    const card = text("article", "", "card"); card.dataset.record = row.event_id;
    card.append(text("small", `${shortId(row.event_id)} · ${actor()} decision: ${row.decision_at || "not supplied"} · MCP receipt: ${row.received_at || row.recorded_at}`), text("h3", row.chosen_action), text("p", row.rationale_summary));
    card.append(text("p", usageText(row.token_usage_id ? {usage_id:row.token_usage_id,total_tokens:row.total_tokens,input_tokens:row.input_tokens,output_tokens:row.output_tokens,cached_input_tokens:row.cached_input_tokens,model:row.token_model}:null), "muted"));
    if (row.background) card.append(text("p", row.background, "muted"));
    if (row.confidence_claim) card.append(text("p", `Self-reported confidence: ${row.confidence_value ?? "unknown"} · Claim: ${row.confidence_claim}`, "muted"));
    if (row.completion_outcome) card.append(text("p", `${actor()} assessment: ${row.completion_outcome} · Dispenser response: ${row.dispenser_response}. This assessment does not confirm output OFF or successful activation.`, "banner"));
    const links = text("div", "", "links"); links.append(eventButton(row.event_id, "Decision record"));
    for (const id of (row.basis_event_ids || [])) links.append(eventButton(id, `Supporting observation ${shortId(id)} ↗`));
    card.append(links); el("decisions").append(card);
  }
  if (!decisions.length) el("decisions").append(text("div", "No decisions recorded. No rationale is inferred from calls.", "empty"));
  el("events").replaceChildren();
  for (const event of events.filter(e => e.kind !== "decision").reverse()) {
    const failed = event.kind === "call_error" || event.is_error === true;
    const line = text("div", "", "event"); line.dataset.record = event.event_id;
    line.append(eventButton(event.event_id, `${shortId(event.event_id)} · ${event.kind === "call_intent" ? actor() + " → MCP" : "MCP → " + actor()} · ${toolLabel(event.tool)} · ${outcome(event)} · ${event.recorded_at}`));
    if (failed) line.classList.add("error");
    el("events").append(line);
  }
  highlightSelection();
}

async function draw() {
  if (!window.Plotly) { el("status").textContent = "Local Plotly bundle could not load."; return; }
  const axis = el("clock").value;
  function trace(field, name, color, panel, filter = () => true, dash) {
    const rows = observations.filter(r => filter(r) && xFor(r) !== null);
    return { type: "scatter", mode: "lines+markers", name, x: rows.map(xFor), y: rows.map(r => num(r[field])),
      xaxis: panel === 1 ? "x" : `x${panel}`, yaxis: panel === 1 ? "y" : `y${panel}`,
      line: { color, width: 2, dash, shape: field.includes("setpoint") || field.includes("limit") ? "hv" : "linear" },
      marker: { size: 4 }, connectgaps: false, customdata: rows.map(r => r.event_id),
      text: rows.map(r => shortId(r.event_id) + "<br>" + (axis === "virtual" ? elapsedDetail(xFor(r)) : xFor(r))), hovertemplate: "%{text}<br>%{y}<extra>" + name + "</extra>" };
  }
  const traces = [
    trace("pressure_mbar", "Observed total pressure · mbar", "#e1c77c", 1, r => r.observation_kind === "pressure"),
    trace("commanded_load_current_limit_a", "Returned commanded load limit · A", "#8cafe2", 2, r => r.observation_kind === "power"),
    trace("native_ch1_measured_current_a", "Measured native CH1 · A", "#6edbc9", 2, r => r.observation_kind === "power"),
    trace("native_ch1_voltage_setpoint_v", "Native voltage setpoint · V", "#c7a4e9", 3, r => r.observation_kind === "power"),
    trace("native_ch1_measured_voltage_v", "Measured native CH1 voltage · V", "#80c8e5", 3, r => r.observation_kind === "power"),
  ];
  const requested = controls.filter(r => r.phase === "call_intent" && xFor(r) !== null && num(r.requested_load_current_a) !== null);
  traces.push({ type: "scatter", mode: "markers", name: "Requested load target · A", x: requested.map(xFor), y: requested.map(r => r.requested_load_current_a), xaxis: "x2", yaxis: "y2", marker: { color: "#efbc72", symbol: "diamond-open", size: 9 }, customdata: requested.map(r => r.event_id), text: requested.map(r => shortId(r.event_id)), hovertemplate: "%{text}<br>Requested load: %{y} A<extra></extra>" });
  for (const [status, y, color, symbol, label] of [["intent", 3, "#efbc72", "diamond-open", "Requested"], ["succeeded", 2, "#6edbc9", "circle", "Completed"], ["not_executed", 1, "#cbb9ff", "square-open", "Not executed"], ["failed", 0, "#fb978d", "x", "Error / state uncertain"]]) {
    const rows = controls.filter(r => (status === "not_executed" ? outcome(byId.get(r.event_id)) === "Not executed" : r.status === status && outcome(byId.get(r.event_id)) !== "Not executed") && xFor(r) !== null);
    traces.push({ type: "scatter", mode: "markers", name: label, x: rows.map(xFor), y: rows.map(() => y), xaxis: "x4", yaxis: "y4", marker: { color, symbol, size: 10 }, customdata: rows.map(r => r.event_id), text: rows.map(r => `${shortId(r.event_id)} · ${toolLabel(r.tool)}<br>${outcome(byId.get(r.event_id))}<br>${r.error || ""}${el("clock").value === "virtual" && !r.observed_at ? "<br>Position: "+placementLabel(r) : ""}`), hovertemplate: "%{text}<extra></extra>" });
  }
  const elapsedValues = traces.flatMap(t => t.x);
  const elapsedTicks = elapsedAxis(elapsedValues, el("chart").layout?.xaxis?.autorange ? null : el("chart").layout?.xaxis?.range);
  const axisBase = { ...(axis === "virtual" ? elapsedTicks : {}), type: axis === "wall" ? "date" : "linear", gridcolor: "#304254", zeroline: false, title: { text: axis === "wall" ? "Recorded wall time · UTC" : elapsedTicks.title.text }, automargin: true };
  const layout = { paper_bgcolor: "#152330", plot_bgcolor: "#152330", font: { color: "#c9d8e5", size: 11 }, margin: { t: 60, r: 30, b: 45, l: 95 }, height: 800, hovermode: "closest", dragmode: "zoom", uirevision: `${metadata.session_id}:${generation}:${axis}`, legend: { orientation: "h", x: 0, y: 1.1, font: { size: 10 } },
    xaxis: { ...axisBase, anchor: "y", showticklabels: false, title: null },
    xaxis2: { ...axisBase, anchor: "y2", matches: "x", showticklabels: false, title: null },
    xaxis3: { ...axisBase, anchor: "y3", matches: "x", showticklabels: false, title: null },
    xaxis4: { ...axisBase, anchor: "y4", matches: "x" },
    yaxis: { domain: [0.76, 1], title: { text: "Pressure · mbar" }, type: "log", tickformat: ".3e", exponentformat: "e", nticks: 5, gridcolor: "#304254", automargin: true },
    yaxis2: { domain: [0.49, 0.71], title: { text: "Current · A" }, gridcolor: "#304254", rangemode: "tozero", automargin: true },
    yaxis3: { domain: [0.23, 0.44], title: { text: "Voltage · V" }, gridcolor: "#304254", rangemode: "tozero", automargin: true },
    yaxis4: { domain: [0, 0.17], title: { text: "Power requests<br>and results" }, tickvals: [0, 1, 2, 3], ticktext: ["Error / uncertain", "Not executed", "Completed", "Requested"], range: [-0.5, 3.5], gridcolor: "#304254", automargin: true } };
  timeViews.chart.wall=axis==="wall";
  viewLayout("chart",layout,elapsedValues.filter(v=>v!==null));
  rendering = true;
  await guardedPlot("chart",()=>Plotly.react("chart", traces, layout, { responsive: true, displaylogo: false, scrollZoom: true }));
  if (!chartReady) {
    el("chart").on("plotly_click", data => { const id = data.points[0]?.customdata; if (id) selectEvent(id); });
  }
  watchTimeView("chart", elapsedValues);
  chartReady = true; rendering = false;
}

function readFailure(error) {
  if (error.name==="TimeoutError" || error.name==="AbortError") return "Dashboard data timeout; retained data may be stale. Retrying (this does not indicate MCP or equipment stopped)";
  if (error.message?.startsWith("HTTP 401") || error.message?.startsWith("HTTP 403")) return "Dashboard authorization required; reload and sign in. Retained data may be stale";
  if (error instanceof TypeError) return "Dashboard network read failed; retained data may be stale. Retrying";
  return "Dashboard read failed: "+String(error.message||error).replace(/[.]+$/,"")+". Retained data may be stale; retrying";
}
async function poll() {
  if (pollRunning) return;
  clearTimeout(pollTimer);pollRunning=true;
  let catchUp=false;
  try {
    const response = await fetch(`/api/session?after=${cursor}&generation=${generation}&${runQuery}`, { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!response.ok) { const problem = await response.json(); throw new Error(`HTTP ${response.status}: ${problem.error || "Dashboard request failed"}`); }
    const data = await response.json();
    el("view-mode").textContent = data.recording_view === "saved_recording" ? "SAVED RUN" : "LIVE VIEW · current process";
    const changed = data.reset || (data.events?.length || 0) > 0;
    if (data.reset) { events = []; observations = []; controls = []; decisions = []; byId.clear(); intentsByCall.clear(); resultsByCall.clear(); recordNumbers.clear(); selectedId = null; resetTimeView("chart");resetTimeView("truth-chart");truthCursor=0;truthGeneration=-1;truthRows=[];truthData=null;truthMore=false;truthRevision=null; }
    metadata = data.metadata || metadata;
    if (metadata.session_id && clockInitializedFor !== metadata.session_id) {
      el("clock").value = metadata.session_kind === "simulated" && metadata.observed_time_origin ? "virtual" : "wall";
      resetTimeView("chart");resetTimeView("truth-chart");
      clockInitializedFor = metadata.session_id;
    }
    generation = data.generation ?? generation; cursor = data.cursor ?? cursor;
    const pageRecords=data.events||[];
    events.push(...pageRecords);observations.push(...pageRecords.filter(r=>r.observation_kind));controls.push(...pageRecords.filter(r=>r.phase));decisions.push(...pageRecords.filter(r=>r.kind==="decision"));
    for (const event of data.events || []) { byId.set(event.event_id, event); recordNumbers.set(event.event_id, recordNumbers.size + 1); if (event.kind === "call_intent") intentsByCall.set(event.call_id, event); if (event.kind === "call_result" || event.kind === "call_error") resultsByCall.set(event.call_id,event); }
    const simulated = metadata.session_kind === "simulated", live = metadata.session_kind === "live";
    el("mode").className = "mode-strip" + (live ? " live" : "");
    el("mode").replaceChildren(text("div", simulated ? "◈ SIMULATION" : live ? "◆ LIVE HARDWARE" : "? FIXTURE / SOURCE UNKNOWN"), text("small", live ? "Hardware-source recording. This banner does NOT mean output is energized or measurements are current." : "Simulated equipment only — no real equipment is connected to this recording."));
    el("decision-heading").textContent = actor() + " decisions and reasons";
    el("title").textContent = metadata.label || "Observations, actions, decisions";
    el("provenance").textContent = metadata.session_kind ? `Requests and decisions from: ${actor()}` : "Waiting to learn who made the requests.";
    el("session-details").textContent = `Session ${metadata.session_id || "not available"} · ${metadata.session_kind || "unknown source"}. ${metadata.session_kind === "live" ? "Hardware-source records do not establish current output state." : "Synthetic, uncalibrated data; not hardware evidence. Scripted demonstrations are not AI blind runs."}`;
    el("decision-caption").textContent = `Reasons submitted by ${actor()} with links to the readings used.`;
    el("provenance").className = metadata.session_kind && metadata.session_kind !== "live" ? "banner synthetic" : "banner";
    el("source").textContent = `${data.recording_view === "saved_recording" ? "SAVED RECORDING / REPLAY VIEW" : "PROCESS SESSION RECORDS"} · File checked ${new Date().toISOString()} · ${changed ? "Records updated" : "No new records this check"} · Read-only source: ${data.source || "not configured"}`;
    const lastObservation = observations.at(-1);
    const observedAgo = lastObservation ? Math.max(0, (Date.now() - Date.parse(lastObservation.recorded_at)) / 1000) : null;
    el("observation-age").textContent = lastObservation ? `Last source observation: ${lastObservation.observed_at || "unavailable"} · Received/recorded ${Math.floor(observedAgo)} s ago. New observations appear only when a caller invokes a reading or action; this page does not measure.` : "No observation yet. This dashboard does not sample instruments.";
    el("status").textContent = `${data.has_more ? "Loading history" : "Caught up · waiting for new records"} · ${events.length} records · ${observations.length} readings · ${controls.filter(c => c.phase === "call_intent").length} power requests · ${decisions.length} decisions${data.message ? "\n" + data.message : ""}${data.errors ? `\n${data.errors} unreadable record(s): ${data.last_error}` : ""}`;
    el("status").className = data.status === "error" || data.errors ? "error" : "";
    if (changed) { updatePanels(); await draw(); await drawTokens(); }
    await pollTruth();
    catchUp=Boolean(data.has_more || truthMore);
  } catch (error) { el("status").textContent = readFailure(error); el("status").className = "error"; }
  finally {pollRunning=false;pollTimer=setTimeout(poll,catchUp ? 0 : 1000);}
}
async function pollTruth(force=false) {
  const section=el("inside");section.hidden=metadata.session_kind!=="simulated";
  if (section.hidden) {truthMore=false;return;}
  if (force) {if(truthData) await drawTruth(truthData,true);return;}
  try {
    const response=await fetch(`/api/simulation-state?after=${truthCursor}&generation=${truthGeneration}&${runQuery}`,{cache:"no-store",signal:AbortSignal.timeout(5000)});
    if(!response.ok) throw new Error("HTTP "+response.status);
    const data=await response.json();
    if(data.reset) {truthRows=[];truthRevision=null;resetTimeView("truth-chart");}
    truthCursor=data.cursor??truthCursor;truthGeneration=data.generation??truthGeneration;
    truthMore=Boolean(data.has_more);
    if(data.status==="mismatch" || data.status==="unavailable" || data.status==="error") truthRows=[];
    truthRows.push(...(data.rows||[]));
    truthData={...data,rows:truthRows};
    el("truth-status").textContent=(truthMore?"Loading model history":"Caught up")+ " · "+truthRows.length+" model snapshots · "+data.status+" · "+(data.message||"Model snapshots available")+(data.errors?" · Invalid rows: "+data.errors:"");
    await drawTruth(truthData,false);
  } catch(error) {truthMore=false;el("truth-status").textContent=readFailure(error);}
}
async function drawTruth(data,force=false) {
    const rows=data.rows||[];
    const revision = JSON.stringify([data.status,data.generation,rows.length,rows.at(-1)?.sequence,data.run_id]);
    if (!force && revision === truthRevision) return;
    truthRevision = revision;
    if (data.status !== "ready" || !rows.length) { Plotly.purge("truth-chart"); return; }
    const p = data.parameters || rows[0]?.parameters || {};
    const last = rows.at(-1).state;
    el("truth-parameters").textContent = "Synthetic loading: initial Rb " + fmt(p.initial_rb_effective_units) + " units; initial impurity " + fmt(p.initial_impurity_effective_units) + " units; ratio " + fmt(p.initial_rb_to_impurity_effective_ratio) + " (not measured mass/composition). Fixed resistance " + fmt(p.resistance_ohm) + " Ω. Remaining: Rb " + fmt(num(last.rb_remaining_fraction) === null ? null : last.rb_remaining_fraction * 100) + "%; impurity " + fmt(num(last.impurity_remaining_fraction) === null ? null : last.impurity_remaining_fraction * 100) + "%. Thermal state is normalized, not kelvin.";
    const series = (field,name,color,panel,scale=1) => ({type:"scatter",mode:"lines",name,
      x:rows.map(r=>r.virtual_time_s/60),y:rows.map(r=>num(r.state[field]) === null ? null : r.state[field]*scale),
      xaxis:panel === 1 ? "x" : "x"+panel,yaxis:panel === 1 ? "y" : "y"+panel,
      line:{color,width:2},connectgaps:false,customdata:rows.map(r=>r.sequence),text:rows.map(r=>"Model snapshot S"+r.sequence+"<br>"+elapsedDetail(r.virtual_time_s/60)),
      hovertemplate:"%{text}<br>%{y}<extra>"+name+"</extra>"});
    const traces = [
      series("rb_remaining_fraction","Rb remaining · %","#e5bc75",1,100),
      series("impurity_remaining_fraction","Impurity remaining · %","#80d8d0",1,100),
      series("rb_release_rate_effective_units_per_s","Rb release · synthetic units/s","#e5bc75",2),
      series("impurity_release_rate_effective_units_per_s","Impurity release · synthetic units/s","#80d8d0",2),
      series("total_pressure_mbar","Total model pressure · mbar","#ffffff",3),
      series("rb_pressure_mbar","Rb model pressure · mbar","#e5bc75",3),
      series("impurity_pressure_mbar","Impurity model pressure · mbar","#80d8d0",3),
      series("background_pressure_mbar","Background model pressure · mbar","#91a9ca",3)
    ];
    const truthTicks = elapsedAxis(rows.map(r=>r.virtual_time_s/60), el("truth-chart").layout?.xaxis?.autorange ? null : el("truth-chart").layout?.xaxis?.range);
    const base={gridcolor:"#304254",automargin:true};
    const truthLayout={paper_bgcolor:"#152330",plot_bgcolor:"#152330",font:{color:"#c9d8e5"},height:640,
      margin:{t:90,b:50,l:95,r:25},uirevision:metadata.session_id+":"+data.run_id,
      legend:{orientation:"h",y:1.18,font:{size:10}},
      xaxis:{...base,...truthTicks,title:null,anchor:"y",showticklabels:false},
      xaxis2:{...base,...truthTicks,title:null,anchor:"y2",matches:"x",showticklabels:false},
      xaxis3:{...base,...truthTicks,anchor:"y3",matches:"x",title:truthTicks.title},
      yaxis:{...base,domain:[.72,1],range:[0,100],title:{text:"Remaining · %"}},
      yaxis2:{...base,domain:[.38,.64],rangemode:"tozero",title:{text:"Release · units/s"}},
      yaxis3:{...base,domain:[0,.29],type:"log",tickformat:".3e",title:{text:"Model pressure · mbar"}}
    };
    viewLayout("truth-chart",truthLayout,rows.map(r=>r.virtual_time_s/60));
    await guardedPlot("truth-chart",()=>Plotly.react("truth-chart",traces,truthLayout,{responsive:true,displaylogo:false,scrollZoom:true}));
    watchTimeView("truth-chart", rows.map(r=>r.virtual_time_s/60));
    el("truth-chart").removeAllListeners("plotly_click");
    el("truth-chart").on("plotly_click", clicked => {
      const row = truthRows.find(r => r.sequence === clicked.points[0]?.customdata);
      if (!row) return;
      el("truth-selected").textContent = "Model snapshot S" + row.sequence + " · " + elapsedDetail(row.virtual_time_s / 60) + " · Rb remaining " + fmt(num(row.state.rb_remaining_fraction) === null ? null : row.state.rb_remaining_fraction * 100) + "% · Impurity remaining " + fmt(num(row.state.impurity_remaining_fraction) === null ? null : row.state.impurity_remaining_fraction * 100) + "%. Model state only; not a supporting observation.";
      el("truth-raw").textContent = JSON.stringify(row,null,2);
      el("truth-selected").classList.add("highlight");
      el("truth-selected").scrollIntoView({behavior:"smooth",block:"center"});
    });

}

el("clock").addEventListener("change", ()=>{resetTimeView("chart");draw().catch(showViewError);});
for(const id of ["chart","truth-chart"]) {
  const [selectId,,count]=viewIds(id), isMain=id==="chart";
  const redraw=()=>isMain?draw():pollTruth(true);
  el(selectId).addEventListener("change",()=>{
    captureRange(id);timeViews[id].mode=el(selectId).value;
    redraw().catch(showViewError);
  });
  el(isMain?"fit":"truth-fit").addEventListener("click",async()=>{
    resetTimeView(id);
    try {
      if(el(id).layout) {
        const update={};
        for(let i=1;i<=count;i++) update[(i===1?"yaxis":"yaxis"+i)+".autorange"]=true;
        await guardedPlot(id,()=>Plotly.relayout(id,update));
      }
      await redraw();
    } catch(error) {showViewError(error);}
  });
}

async function refreshRuns() {
  try {
    const response = await fetch("/api/runs", {cache:"no-store", signal:AbortSignal.timeout(5000)});
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
      const missing = text("option", "Selected run is unavailable: " + selectedRun);
      missing.value = selectedRun; missing.disabled = true; picker.append(missing);
    }
    picker.value = selectedRun;
    el("run-help").textContent = (data.live_view_available ? "Live view follows this process's records. " : "Saved-recording preview only; no live acquisition is attached. ") + "Selecting a run reloads only this page. It never starts, stops or resumes recording or equipment. Unavailable folders need the supported metadata/events format.";
  } catch (error) { el("run-help").textContent = error.message + ". Current view is unchanged."; }
}
el("run-picker").addEventListener("change", event => {
  const url = new URL(window.location.href);
  if (event.target.value) url.searchParams.set("run",event.target.value);
  else url.searchParams.delete("run");
  // A new document discards every old fetch, cursor, selected detail and chart.
  window.location.assign(url.href);
});
el("refresh-runs").addEventListener("click", refreshRuns);
refreshRuns();
poll();
