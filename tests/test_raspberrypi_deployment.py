from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
DEPLOYMENT = PROJECT_ROOT / "deployment" / "raspberrypi"
HIL_UNIT = DEPLOYMENT / "systemd" / "dispenser-conditioning-mcp-hil.service"
PRODUCTION_UNIT = (
    DEPLOYMENT / "systemd" / "dispenser-conditioning-mcp-production.service"
)
HIL_PROFILE = DEPLOYMENT / "profiles" / "unloaded-hil.env.template"
PRODUCTION_PROFILE = DEPLOYMENT / "profiles" / "production.env.template"

if os.name == "nt":
    sys.modules.setdefault("grp", ModuleType("grp"))


def load_deployment_module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, DEPLOYMENT / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return cast(Any, module)


network = load_deployment_module(
    "dcp_pi_network_validator", "validate_network_policy.py"
)
separation = load_deployment_module(
    "dcp_pi_separation_validator", "validate_instance_separation.py"
)
ssh_bridge = load_deployment_module("dcp_pi_ssh_validator", "validate_ssh_bridge.py")


def plain_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_profile(path: Path, *, hicube: str, siglent: str) -> None:
    path.write_text(
        "DISPENSER_HICUBE_HOST=" + hicube + "\n"
        "DISPENSER_SIGLENT_IDENTIFIER=" + siglent + "\n",
        encoding="utf-8",
    )


def write_dropin(path: Path, *allows: str) -> None:
    path.write_text(
        "[Service]\n" + "".join(f"IPAddressAllow={value}\n" for value in allows),
        encoding="utf-8",
    )


def test_systemd_units_are_manual_restart_no_isolated_services() -> None:
    for unit in (HIL_UNIT, PRODUCTION_UNIT):
        content = unit.read_text(encoding="utf-8")
        assert "Restart=no" in content
        assert "\n[Install]\n" not in content
        assert " -I -B -m dispenser_conditioning_mcp" in content
        assert "deployment_inventory" in content
        assert " -I -B -m dispenser_conditioning_mcp.deployment_check" in content
        assert "/usr/bin/python3.13 -I -B" in content
        assert "verify_runtime_start.py" in content
        assert "IPAddressDeny=any" in content
        assert "IPAddressAllow=localhost" in content
        assert "ProtectSystem=strict" in content
        assert "NoNewPrivileges=yes" in content
    hil_profile = HIL_PROFILE.read_text(encoding="utf-8")
    assert "DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A=4.8" in hil_profile
    assert "DISPENSER_SIGLENT_UPWARD_STEP_A=0.2" in hil_profile
    assert "DISPENSER_SIGLENT_CONTROL_ENABLED=false" in hil_profile
    assert "DISPENSER_SIGLENT_EXPECTED_MODEL=replace-with-verified-model" in hil_profile
    assert "replace-with-operator-approved-voltage" in hil_profile
    on_pi = (DEPLOYMENT / "ON_PI_VALIDATION.md").read_text(encoding="utf-8")
    assert "0984bba67d8e5651cd2d9aa7b2c0db2d6eb694f3" in on_pi
    assert "siglent_spd3000._build_commit" in on_pi


@pytest.mark.parametrize(
    ("hicube", "siglent", "allows", "passes"),
    [
        ("10.0.0.2", "10.0.0.3:8765", ("10.0.0.2/32", "10.0.0.3/32"), True),
        ("10.0.0.2", "10.0.0.3:8765", (), False),
        ("10.0.0.2", "10.0.0.3:8765", ("10.0.0.2/32", "10.0.0.4/32"), False),
        ("hicube.local", "10.0.0.3:8765", ("10.0.0.2/32", "10.0.0.3/32"), False),
    ],
)
def test_network_policy_matches_profile_literal_addresses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hicube: str,
    siglent: str,
    allows: tuple[str, ...],
    passes: bool,
) -> None:
    profile = tmp_path / "profile.env"
    dropin = tmp_path / "network.conf"
    write_profile(profile, hicube=hicube, siglent=siglent)
    write_dropin(dropin, *allows)

    def fake_lines(path: Path, **_kwargs: object) -> list[str]:
        return plain_lines(path)

    def fake_getgrnam(_name: str) -> SimpleNamespace:
        return SimpleNamespace(gr_gid=1001)

    monkeypatch.setattr(
        network,
        "_lines",
        fake_lines,
    )
    monkeypatch.setattr(
        network.grp,
        "getgrnam",
        fake_getgrnam,
        raising=False,
    )

    if passes:
        network.validate(HIL_UNIT, dropin, profile, "dispenser-hil")
    else:
        with pytest.raises(network.NetworkPolicyError):
            network.validate(HIL_UNIT, dropin, profile, "dispenser-hil")


@pytest.mark.parametrize(
    ("production_identifier", "production_serial"),
    [
        ("10.0.0.3:8765", "SERIAL-B"),
        ("10.0.0.4:8765", "SERIAL-A"),
    ],
)
def test_cross_profile_validator_rejects_each_shared_physical_identity(
    tmp_path: Path,
    production_identifier: str,
    production_serial: str,
) -> None:
    hil = tmp_path / "hil.env"
    production = tmp_path / "production.env"
    common = (
        "DISPENSER_SIGLENT_CONTROL_ENABLED=false\n"
        "DISPENSER_SIGLENT_IDENTIFIER=10.0.0.3:8765\n"
        "DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER=SERIAL-A\n"
    )
    hil.write_text(
        "DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT=unloaded_hil\n"
        "DISPENSER_MCP_HTTP_PORT=8001\n"
        "DISPENSER_SIGLENT_GATEWAY_AUTH_FILE=/etc/hil-auth\n" + common,
        encoding="utf-8",
    )
    production.write_text(
        "DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT=production_dispenser\n"
        "DISPENSER_MCP_HTTP_PORT=8002\n"
        "DISPENSER_SIGLENT_GATEWAY_AUTH_FILE=/etc/prod-auth\n"
        "DISPENSER_SIGLENT_CONTROL_ENABLED=false\n"
        f"DISPENSER_SIGLENT_IDENTIFIER={production_identifier}\n"
        f"DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER={production_serial}\n",
        encoding="utf-8",
    )

    with pytest.raises(separation.SeparationError, match="overlap"):
        separation.validate(hil, production, HIL_UNIT, PRODUCTION_UNIT)


def test_profile_mode_contract_is_distinct_from_unit_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "profile.env"
    profile.write_text("A=B\n", encoding="utf-8")
    original_lstat = Path.lstat

    def fake_lstat(path: Path) -> object:
        info = original_lstat(path)
        if path == profile:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=1001)
        return info

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    assert network._lines(profile, expected_gid=1001, expected_mode=0o640) == ["A=B"]
    with pytest.raises(network.NetworkPolicyError, match="ownership or mode"):
        network._lines(profile, expected_gid=0, expected_mode=0o644)


def test_ssh_template_never_overrides_global_authorized_keys() -> None:
    template = DEPLOYMENT / "ssh" / "sshd_config.template"
    text = template.read_text(encoding="utf-8")
    assert "AuthorizedKeysFile" not in text.partition("Match User ")[0]
    assert text.count("    AuthorizedKeysFile ") == 2
    assert text.count("    MaxSessions 0") == 2
    assert "    AllowTcpForwarding local" in text


def test_ssh_validator_rejects_global_authorized_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "sshd.conf"
    hil_key = tmp_path / "hil-key"
    prod_key = tmp_path / "prod-key"
    config.write_text(
        "AuthorizedKeysFile /tmp/global\n"
        + (DEPLOYMENT / "ssh" / "sshd_config.template").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    hil_key.write_text("unused")
    prod_key.write_text("unused")

    def fake_root_file(path: Path, _mode: int) -> str:
        return path.read_text(encoding="utf-8")

    monkeypatch.setattr(ssh_bridge, "_root_file", fake_root_file)
    with pytest.raises(ssh_bridge.SshBridgeError, match="global"):
        ssh_bridge.validate(config, hil_key, prod_key)
