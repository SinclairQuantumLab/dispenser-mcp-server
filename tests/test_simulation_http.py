from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client


@pytest.mark.anyio
async def test_independent_source_copy_runs_simulator_over_http(tmp_path: Path):
    source = Path(__file__).resolve().parents[1]
    clone = tmp_path / "arbitrary-independent-checkout"
    shutil.copytree(
        source / "src", clone / "src", ignore=shutil.ignore_patterns("__pycache__")
    )
    (clone / "settings").mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    (clone / "settings" / "mcp-settings.toml").write_text(
        f'schema_version=1\nbackend="simulation"\nport={port}\n[simulation]\nseed="connectivity-fixture"\nscenario="nominal_recovery"\n',
        encoding="utf-8",
    )
    # Copy has no sibling simulator, live settings, credentials, or drivers.
    bootstrap = """
import sys
class NoHardware:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"dispenser_conditioning_mcp.app", "dispenser_conditioning_mcp.hicube", "dispenser_conditioning_mcp.siglent", "asyncua", "siglent_spd3000"}:
            raise AssertionError("Hardware import attempted")
sys.meta_path.insert(0, NoHardware())
from dispenser_conditioning_mcp.__main__ import main
main()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", bootstrap],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(clone / "src")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results = []
    try:
        for _ in range(150):
            assert process.poll() is None, "Isolated HTTP startup failed"
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                await asyncio.sleep(0.05)
            else:
                del reader
                writer.close()
                await writer.wait_closed()
                break
        else:
            pytest.fail("HTTP listener did not start")
        async with (
            asyncio.timeout(20),
            Client(f"http://127.0.0.1:{port}/mcp") as client,
        ):
            assert len((await client.list_tools()).tools) == 7
            for tool in ("read_vacuum_pressure", "read_dispenser_power_state"):
                results.append(await client.call_tool(tool, {}))
            rejected = await client.call_tool("prepare_dispenser_power", {})
            assert rejected.is_error
            results.append(rejected)
            results.append(await client.call_tool("shutdown_dispenser_power", {}))
            final = await client.call_tool("read_dispenser_power_state", {})
            results.append(final)
            assert final.structured_content is not None
            assert final.structured_content["output_enabled"] is False
            assert final.structured_content["commanded_load_current_limit_a"] == 0
            assert final.structured_content["simulated"] is True
            assert (
                final.structured_content["safety_limits"]["fixed_compliance_voltage_v"]
                == 1.0
            )
        async with httpx.AsyncClient() as browser:
            assert (
                await browser.get(f"http://127.0.0.1:{port}/dashboard")
            ).status_code == 200
    finally:
        process.terminate()
        process.wait(timeout=5)
    directories = list((clone / "runs").iterdir())
    assert len(directories) == 1
    directory = directories[0]
    events = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text().splitlines()
    ]
    call_ids = {event["call_id"] for event in events}
    assert len(call_ids) == len(results) == 5
    assert len([event for event in events if event["kind"] == "call_result"]) == 5
    assert (directory / "observer.jsonl").is_file()
    assert (
        json.loads((directory / "observer-link.json").read_text())["observer_file"]
        == "observer.jsonl"
    )
    session_ids = {
        result.meta["dispenser_conditioning"]["session_id"]
        for result in results
        if result.meta
    }
    assert len(session_ids) == 1
