# Conditioning runs

Both hardware and simulator MCP entrypoints default to one flat directory per
process: `YYYY-MM-DDTHH-MM-SSZ_hardware_<8hex>/` or
`YYYY-MM-DDTHH-MM-SSZ_simulation_<8hex>/`. The timestamp is UTC; the random suffix
separates starts in the same second. Directory names do not replace session IDs.
Runtime run folders are ignored by Git. Existing historical files are not moved
automatically. An explicit simulator session-directory override remains supported.

Each run contains `metadata.json`, canonical `events.jsonl`, and derived
`observations.csv`, `controls.csv`, and `decisions.csv`. Simulation normally adds
`observer.jsonl` and `observer-link.json` with a relative observer path. These
internal model snapshots are for human inspection, never decision-agent inputs.
Operator overrides may deliberately place the observer elsewhere.

From the MCP checkout, inspect a saved run without contacting equipment:

```powershell
uv run python tools/serve_recording_preview.py --session-dir runs/<run-folder>
```

Open `http://127.0.0.1:8767/dashboard`. Rebuild derived CSVs if needed with
`uv run python -m dispenser_conditioning_mcp.session_records rebuild runs/<run-folder>`.
One process is one recorded run; this is not restart/resume or a run orchestrator.

Use **View run** in that same dashboard to inspect other supported folders here,
then return to the configured run. **Refresh run list** finds new folders.
A normal instrument process also offers **Live view · current process**; a
saved-recording preview does not imply any live acquisition exists. Browser
selection never changes the recorder. Legacy folders missing metadata/events are
listed as unavailable and are not converted automatically. Selection is preserved
in the page URL; changing runs clears plots/details rather than merging histories.

Remote dashboard access requires the current HTTP process's operator code; the
server-loopback browser can open directly and retrieve the code at
`/dashboard/operator`. Restart invalidates remote cookies/codes. Do not share
credentials or an authenticated browser with a blind decision agent. Local files
and loopback remain accessible to same-host full-access agents; the dashboard
login does not isolate that environment.
The dashboard Main list/Archive selector loads saved runs without affecting acquisition.
Display rename and archive flags use run-management.json; directories and original
records do not move. Only archived non-current runs can be permanently deleted by
an authenticated human after exact folder-name confirmation, including observer files.
MCP history tools provide ordinary paged records and explicit saved-simulation hindsight;
current-process internal state unlocks after fully recorded valid completion, never
merely inactivity. Disclosure cannot be undone; current recorder folders remain
archive/delete-protected. Saved interrupted runs also permit hindsight review.
