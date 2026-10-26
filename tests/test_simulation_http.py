from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client


@pytest.mark.anyio
async def test_independent_source_copy_starts_idle_without_hardware_imports(
    tmp_path: Path,
):
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
        f'backend="simulation"\nport={port}\n[simulation]\nseed="connectivity-fixture"\nscenario="nominal_recovery"\n',
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
            catalog = (await client.list_tools()).tools
            assert [tool.name for tool in catalog] == ["read_server_status"]
            status = await client.call_tool("read_server_status", {})
            assert status.structured_content is not None
            assert status.structured_content["lifecycle"] == "idle"
            assert status.structured_content["active_run"] is None
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert not (clone / "runs").exists()
