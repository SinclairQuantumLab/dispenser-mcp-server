import asyncio
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client


@pytest.mark.anyio
async def test_http_server_starts_from_three_toml_documents(tmp_path: Path) -> None:
    project = tmp_path / "offline-checkout"
    client_file = project / "dependencies" / "hicube" / "hicube_neo_client.py"
    client_file.parent.mkdir(parents=True)
    client_file.write_text(
        """
from datetime import UTC, datetime
from types import SimpleNamespace

class HiCubeNeoClient:
    def __init__(self, *, host, port, timeout_s):
        assert (host, port, timeout_s) == ("offline.test", 4840, 1.0)
    def connect(self): return None
    def read_sample(self):
        return SimpleNamespace(
            observed_at=datetime(2026, 9, 3, 13, 0, tzinfo=UTC),
            g1_pressure_mbar=4.0e-7,
            serial_number="TC80-HTTP",
        )
    def close(self): return None
""".lstrip(),
        encoding="utf-8",
    )
    driver_package = (
        project / "dependencies" / "py-siglent-spd3000" / "src" / "siglent_spd3000"
    )
    driver_package.mkdir(parents=True)
    (driver_package / "__init__.py").write_text(
        """
from types import SimpleNamespace

def load_gateway_auth(path, required=True):
    assert required is True
    assert str(path).endswith("gateway-auth.toml")
    return "offline-test-token"

class Channel:
    def __init__(self):
        self.voltage = 10.0
        self.current = 0.0
        self.output = False

class Measure:
    def voltage(self, channel):
        assert channel == "CH1"
        return 0.0
    def current(self, channel):
        assert channel == "CH1"
        return 0.0
    def power(self, channel):
        assert channel == "CH1"
        return 0.0

class Device:
    def __init__(self):
        self.ch1 = Channel()
        self.ch2 = Channel()
        self.measure = Measure()
        self.capabilities = SimpleNamespace(measure_power=True)
        channel_status = SimpleNamespace(
            output=False, regulation=SimpleNamespace(value="CC")
        )
        self.system = SimpleNamespace(status=SimpleNamespace(
            operating_mode=SimpleNamespace(value="parallel"),
            ch1=channel_status,
            ch2=channel_status,
        ))
    def batch(self, function): return function
    @property
    def idn(self):
        return SimpleNamespace(
            manufacturer="Siglent Technologies",
            model=SimpleNamespace(value="SPD3303X"),
            serial_number="SPD-HTTP",
            firmware_version="1.0",
        )
    def close(self): return None

class SPD3000:
    @classmethod
    def connect(cls, connection, identifier, **options):
        assert connection == "gateway"
        assert identifier == "offline.test:8765"
        assert options == {
            "timeout_s": 1.0,
            "min_command_interval_ms": 100.0,
            "verify_writes_globally": True,
            "token": "offline-test-token",
        }
        return Device()
""".lstrip(),
        encoding="utf-8",
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    settings = project / "settings"
    gateway_settings = settings / "py-siglent-spd3000"
    gateway_settings.mkdir(parents=True)
    (settings / "mcp-settings.toml").write_text(
        """
schema_version = 1
acceptance_context = "production_dispenser"
expected_serial_number = "SPD-HTTP"
compliance_voltage_v = 10.0
control_enabled = false
allow_remote_access = false
""".lstrip(),
        encoding="utf-8",
    )
    (settings / "hicube-neo-client-settings.toml").write_text(
        """
schema_version = 1
host = "offline.test"
port = 4840
timeout_s = 1.0
""".lstrip(),
        encoding="utf-8",
    )
    (gateway_settings / "gateway-settings.toml").write_text(
        """
schema_version = 1
identifier = "offline.test:8765"
timeout_s = 1.0
minimum_command_interval_ms = 100.0
""".lstrip(),
        encoding="utf-8",
    )
    (gateway_settings / "gateway-auth.toml").write_text(
        'token = "offline-test-token"\n', encoding="utf-8"
    )
    main_settings = settings / "mcp-settings.toml"
    main_settings.write_text(
        main_settings.read_text(encoding="utf-8") + f"port = {port}\n",
        encoding="utf-8",
    )
    bootstrap = tmp_path / "run_offline_http.py"
    bootstrap.write_text(
        """
import sys
from pathlib import Path
from dispenser_conditioning_mcp.app import create_configured_server
from dispenser_conditioning_mcp.config import OperatorConfiguration, SourceLayout
from dispenser_conditioning_mcp.transport import (
    McpTransportConfiguration,
    run_configured_transport,
)

operator = OperatorConfiguration.from_toml(
    SourceLayout._for_testing(Path(sys.argv[1]))
)
server = create_configured_server(operator)
transport = McpTransportConfiguration.from_settings(operator.startup)
run_configured_transport(server, transport)
""".lstrip(),
        encoding="utf-8",
    )

    process = subprocess.Popen(
        [sys.executable, str(bootstrap), str(project)],
        cwd=Path(__file__).parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            assert process.poll() is None, "HTTP startup failed"
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
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            tools = (await client.list_tools()).tools
            pressure_result = await client.call_tool("read_vacuum_pressure", {})
            power_result = await client.call_tool("read_dispenser_power_state", {})
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert len(tools) == 6
    assert pressure_result.is_error is False
    assert pressure_result.structured_content is not None
    assert pressure_result.structured_content["pressure_mbar"] == 4.0e-7
    assert pressure_result.structured_content["p1_drive_serial_number"] == "TC80-HTTP"
    assert power_result.is_error is False
    assert power_result.structured_content is not None
    assert power_result.structured_content["serial_number"] == "SPD-HTTP"
    assert power_result.structured_content["load_current_factor"] == 2
