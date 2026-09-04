import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters


@pytest.mark.anyio
async def test_packaged_stdio_entry_point_with_offline_client(tmp_path: Path) -> None:
    client_file = tmp_path / "hicube_neo_client.py"
    client_file.write_text(
        """
from datetime import UTC, datetime
from types import SimpleNamespace

class HiCubeNeoClient:
    def __init__(self, *, host, port, timeout_s):
        assert (host, port, timeout_s) == ("offline.test", 4840, 1.0)

    def connect(self):
        return None

    def read_sample(self):
        return SimpleNamespace(
            observed_at=datetime(2026, 9, 3, 13, 0, tzinfo=UTC),
            g1_pressure_mbar=4.0e-7,
            serial_number="TC80-STDIO",
        )

    def close(self):
        return None
""".lstrip(),
        encoding="utf-8",
    )
    driver_src = tmp_path / "siglent-driver-src"
    driver_package = driver_src / "siglent_spd3000"
    driver_package.mkdir(parents=True)
    auth_file = tmp_path / "gateway-auth.toml"
    auth_file.write_text('token = "offline-test-token"\n', encoding="utf-8")
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
            output=False,
            regulation=SimpleNamespace(value="CC"),
        )
        self.system = SimpleNamespace(
            status=SimpleNamespace(
                operating_mode=SimpleNamespace(value="parallel"),
                ch1=channel_status,
                ch2=channel_status,
            )
        )
    def batch(self, function):
        return function
    @property
    def idn(self):
        return SimpleNamespace(
            manufacturer="Siglent Technologies",
            model=SimpleNamespace(value="SPD3303X"),
            serial_number="SPD-STDIO",
            firmware_version="1.0",
        )
    def close(self):
        return None

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
    project_root = Path(__file__).parents[1]
    parameters = StdioServerParameters(
        command=os.environ.get("DISPENSER_TEST_PACKED_PYTHON", sys.executable),
        args=["-m", "dispenser_conditioning_mcp"],
        cwd=project_root,
        env={
            "DISPENSER_HICUBE_CLIENT_FILE": str(client_file),
            "DISPENSER_HICUBE_HOST": "offline.test",
            "DISPENSER_HICUBE_PORT": "4840",
            "DISPENSER_HICUBE_TIMEOUT_S": "1.0",
            "DISPENSER_SIGLENT_DRIVER_SRC": str(driver_src),
            "DISPENSER_SIGLENT_CONNECTION": "gateway",
            "DISPENSER_SIGLENT_IDENTIFIER": "offline.test:8765",
            "DISPENSER_SIGLENT_GATEWAY_AUTH_FILE": str(auth_file),
            "DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT": "production_dispenser",
            "DISPENSER_SIGLENT_TOPOLOGY": "parallel_ch1",
            "DISPENSER_SIGLENT_CHANNEL": "CH1",
            "DISPENSER_SIGLENT_EXPECTED_MODEL": "SPD3303X",
            "DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER": "SPD-STDIO",
            "DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V": "10.0",
            "DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A": "4.8",
            "DISPENSER_SIGLENT_UPWARD_STEP_A": "0.2",
            "DISPENSER_SIGLENT_CONTROL_ENABLED": "false",
            "DISPENSER_SIGLENT_TIMEOUT_S": "1.0",
        },
    )

    async with Client(parameters) as client:
        tools = (await client.list_tools()).tools
        pressure_result = await client.call_tool("read_vacuum_pressure", {})
        power_result = await client.call_tool("read_dispenser_power_state", {})

    assert len(tools) == 6
    assert pressure_result.is_error is False
    assert pressure_result.structured_content is not None
    assert pressure_result.structured_content["pressure_mbar"] == 4.0e-7
    assert pressure_result.structured_content["p1_drive_serial_number"] == "TC80-STDIO"
    assert power_result.is_error is False
    assert power_result.structured_content is not None
    assert power_result.structured_content["serial_number"] == "SPD-STDIO"
    assert power_result.structured_content["load_current_factor"] == 2
    limits = power_result.structured_content["safety_limits"]
    assert limits["deployment_native_current_ceiling_a"] == 2.4
    assert limits["deployment_commanded_load_current_ceiling_a"] == 4.8
    assert limits["unloaded_hil_safe_measured_current_abs_a"] == 0.001
