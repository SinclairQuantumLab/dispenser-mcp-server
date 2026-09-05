import json

from test_simulation_observer import row, session

from dispenser_conditioning_mcp.dashboard import SessionTail, dashboard_record
from dispenser_conditioning_mcp.session_records import projections
from dispenser_conditioning_mcp.simulation_observer import SimulationObserverReader


def test_many_events_pages_retry_live_append_and_reset(tmp_path):
    directory = session(tmp_path)
    path = directory / "events.jsonl"

    def event(i):
        return (
            json.dumps(
                {
                    "session_id": "session-a",
                    "event_id": str(i),
                    "kind": "call_result",
                    "payload": {
                        "tool": "read_vacuum_pressure",
                        "result": {
                            "structuredContent": {
                                "pressure_mbar": 1e-7,
                                "detail": "x" * 1000,
                            }
                        },
                    },
                }
            )
            + "\n"
        )

    path.write_text("".join(event(i) for i in range(3007)))
    tail = SessionTail(directory)
    first = tail.snapshot()
    assert first["metadata"]["session_id"] == "session-a"
    assert first["has_more"] and first["cursor"] == 200
    assert len(first["events"]) == 200
    assert tail.snapshot()["events"] == first["events"]  # Lost response retry.
    received = list(first["events"])
    cursor, generation = first["cursor"], first["generation"]
    while True:
        page = tail.snapshot(cursor, generation)
        assert not page["reset"] and len(page["events"]) <= 200
        assert (
            "observations" not in page
        )  # One display-record stream, no duplicate tables.
        received.extend(page["events"])
        cursor = page["cursor"]
        if not page["has_more"]:
            break
    assert [e["event_id"] for e in received] == [str(i) for i in range(3007)]
    with path.open("a") as stream:
        stream.write(event(3007))
        stream.write('{"incomplete":')
    live = tail.snapshot(cursor, generation)
    assert [e["event_id"] for e in live["events"]] == ["3007"]
    assert not live["has_more"]  # Partial final line must not busy-loop.
    path.write_text(event(0))
    reset = tail.snapshot(live["cursor"], generation)
    assert reset["reset"] and reset["generation"] != generation
    assert reset["cursor"] == 1


def test_observer_scan_then_bounded_pages_without_duplicate_rows(tmp_path):
    directory = session(tmp_path)
    path = directory / "observer.jsonl"
    path.write_text("".join(json.dumps(row(i)) + "\n" for i in range(5203)))
    reader = SimulationObserverReader(directory)
    scan = reader.snapshot()
    assert scan["status"] == "waiting" and scan["has_more"] and scan["rows"] == []
    cursor, generation = scan["cursor"], scan["generation"]
    received = []
    while True:
        page = reader.snapshot(cursor, generation)
        assert len(page["rows"]) <= 200
        assert "rows" not in page["runs"][0]  # No duplicate serialized trace.
        received.extend(page["rows"])
        cursor, generation = page["cursor"], page["generation"]
        if not page["has_more"]:
            break
    assert [r["sequence"] for r in received] == list(range(5203))
    empty = reader.snapshot(cursor, generation)
    assert empty["rows"] == [] and not empty["has_more"]
    with path.open("a") as stream:
        stream.write(json.dumps(row(5203)) + "\n")
    assert [r["sequence"] for r in reader.snapshot(cursor, generation)["rows"]] == [
        5203
    ]


def test_compact_record_preserves_display_fields_not_raw_envelope():
    state = {
        "pressure_mbar": 1e-7,
        "pressure_torr": 7.5e-8,
        "observed_at": "2040-01-01T00:00:00Z",
        "measurement_source": "fixture",
        "source_details": {"description": "Public pressure fixture", "unit": "mbar"},
    }
    event = {
        "session_id": "fixture",
        "event_id": "e1",
        "call_id": "c1",
        "kind": "call_result",
        "virtual_time_s": 0,
        "payload": {
            "tool": "read_vacuum_pressure",
            "execution": "completed",
            "result": {
                "content": [{"type": "text", "text": json.dumps(state)}],
                "structuredContent": state,
                "isError": False,
            },
        },
    }
    raw_before = json.dumps(event)
    view = dashboard_record(event)
    assert view["pressure_mbar"] == 1e-7 and view["execution"] == "completed"
    assert view["observed_at"] == state["observed_at"]
    assert "payload" not in view and "content" not in view
    assert json.dumps(event) == raw_before
    old = {"events": [event] * 200, "observations": [projections(event)[0][1]] * 200}
    new = {"events": [view] * 200}
    old_bytes, new_bytes = len(json.dumps(old).encode()), len(json.dumps(new).encode())
    print(
        f"Fixture 200 pressure records: old={old_bytes} bytes compact={new_bytes} bytes"
    )
    assert new_bytes < old_bytes / 2
