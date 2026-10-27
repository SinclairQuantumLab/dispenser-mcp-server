from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from dispenser_conditioning_mcp import dashboard
from dispenser_conditioning_mcp.dashboard import SessionTail, dashboard_routes


def _event(index: int, pressure_mbar: float = 1e-7) -> dict[str, object]:
    observed_at = (datetime(2040, 1, 1, tzinfo=UTC) + timedelta(seconds=index)).isoformat()
    state = {
        "pressure_mbar": pressure_mbar,
        "pressure_torr": pressure_mbar * 760 / 1013.25,
        "observed_at": observed_at,
        "measurement_source": "synthetic_public_fixture",
    }
    return {
        "session_id": "progressive-fixture",
        "event_id": f"event-{index:05d}",
        "call_id": f"call-{index:05d}",
        "kind": "call_result",
        "recorded_at": observed_at,
        "payload": {
            "tool": "read_vacuum_pressure",
            "result": {"structuredContent": state, "isError": False},
        },
    }


def _write_run(directory: Path, count: int) -> Path:
    directory.mkdir()
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": "progressive-fixture",
                "session_kind": "live",
                "source": "scripted",
            }
        ),
        encoding="utf-8",
    )
    values = [1e-7 + index * 1e-12 for index in range(count)]
    if count > 800:
        values[333] = 1e-10
        values[777] = 1e-4
    records = [_event(index, values[index]) for index in range(count)]
    marker_base = count
    records.extend(
        [
            {
                "session_id": "progressive-fixture",
                "event_id": "control-marker",
                "call_id": "control-call",
                "kind": "call_intent",
                "recorded_at": "2040-01-02T00:00:00+00:00",
                "payload": {
                    "tool": "set_dispenser_current",
                    "arguments": {"target_current_a": 0.2},
                },
            },
            {
                "session_id": "progressive-fixture",
                "event_id": "decision-marker",
                "kind": "decision",
                "recorded_at": "2040-01-02T00:00:01+00:00",
                "payload": {
                    "chosen_action": "hold",
                    "rationale_summary": "fixture",
                    "basis_event_ids": [f"event-{marker_base - 1:05d}"],
                },
            },
            {
                "session_id": "progressive-fixture",
                "event_id": "error-marker",
                "call_id": "error-call",
                "kind": "call_error",
                "recorded_at": "2040-01-02T00:00:02+00:00",
                "payload": {"tool": "read_vacuum_pressure", "error_message": "fixture"},
            },
            {
                "session_id": "progressive-fixture",
                "event_id": "external-change-marker",
                "kind": "external_change_detected",
                "recorded_at": "2040-01-02T00:00:03+00:00",
                "payload": {
                    "tool": "read_dispenser_power_state",
                    "source": "unknown_external_actor",
                    "intent_known": False,
                    "exact_change_time_known": False,
                    "changed_fields": ["output_enabled"],
                    "previous_values": {"output_enabled": False},
                    "detected_values": {"output_enabled": True},
                },
            },
        ]
    )
    path = directory / "events.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


def test_one_locked_scan_builds_extrema_overview_and_independent_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "run"
    path = _write_run(directory, 2505)
    original = dashboard.dashboard_record
    calls = 0
    calls_lock = threading.Lock()

    def counted(event: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        with calls_lock:
            calls += 1
        return original(event)  # type: ignore[arg-type]

    monkeypatch.setattr(dashboard, "dashboard_record", counted)
    tail = SessionTail(directory)
    with ThreadPoolExecutor(max_workers=6) as pool:
        snapshots = list(pool.map(lambda _: tail.overview(), range(6)))
    assert calls == 2509
    assert {snapshot["generation"] for snapshot in snapshots} == {1}
    overview = snapshots[0]["overview"]
    overview_ids = {event["event_id"] for event in overview["events"]}
    assert {
        "event-00000",
        "event-00333",
        "event-00777",
        "event-02504",
        "control-marker",
        "decision-marker",
        "error-marker",
        "external-change-marker",
    } <= overview_ids
    assert overview["markers_complete"] is True

    generation = snapshots[0]["generation"]
    starts = list(range(0, 2509, 500))
    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = list(
            pool.map(lambda start: tail.page(start, generation, 500, True), starts)
        )
    received = sorted(
        (event for page in pages for event in page["events"]),
        key=lambda event: event["record_number"],
    )
    assert [event["record_number"] for event in received] == list(range(1, 2510))
    assert calls == 2509

    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_event(2505)) + "\n")
    appended = tail.overview()
    assert appended["total_records"] == 2510
    assert calls == 2510
    path.write_text(json.dumps(_event(0)) + "\n", encoding="utf-8")
    reset = tail.page(0, generation, 500, True)
    assert reset["reset"] is True and reset["events"] == []
    assert reset["generation"] != generation and reset["total_records"] == 1


@pytest.mark.anyio
async def test_public_progressive_routes_are_projected_bounded_and_random_access(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "run"
    _write_run(directory, 2201)
    app = Starlette(routes=dashboard_routes(directory, replay=True))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        overview_response = await client.get("/api/session/overview")
        assert overview_response.status_code == 200
        overview = overview_response.json()
        assert overview["total_records"] == 2205
        assert len(overview["overview"]["events"]) < 2205
        assert all("payload" not in event for event in overview["overview"]["events"])
        generation = overview["generation"]
        responses = await asyncio.gather(
            *(
                client.get(
                    "/api/session/page",
                    params={"start": start, "limit": 1000, "generation": generation},
                )
                for start in (2000, 0, 1000)
            )
        )
        pages = [response.json() for response in responses]
        assert all(response.status_code == 200 for response in responses)
        assert all(len(page["events"]) <= 1000 for page in pages)
        records = sorted(
            (event for page in pages for event in page["events"]),
            key=lambda event: event["record_number"],
        )
        assert [event["record_number"] for event in records] == list(range(1, 2206))
        assert all("payload" not in event for event in records)
        detail = await client.get(
            "/api/session/record",
            params={"event_id": "event-00333", "generation": generation},
        )
        assert detail.status_code == 200
        assert detail.json()["event"]["record_number"] == 334
        assert "payload" not in detail.json()["event"]
