"""Validate fail-closed systemd device-network policy for one MCP instance."""

from __future__ import annotations

import argparse
import grp
import ipaddress
import stat
import sys
from pathlib import Path


class NetworkPolicyError(RuntimeError):
    """Reject a missing, broad, or ambiguous systemd address policy."""


def _lines(path: Path, *, expected_gid: int, expected_mode: int) -> list[str]:
    try:
        info = path.lstat()
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise NetworkPolicyError("A network policy artifact is unavailable.") from error
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise NetworkPolicyError("A network policy artifact is not a regular file.")
    if (
        info.st_uid != 0
        or info.st_gid != expected_gid
        or stat.S_IMODE(info.st_mode) != expected_mode
    ):
        raise NetworkPolicyError(
            "A network policy artifact ownership or mode is invalid."
        )
    return [line.strip() for line in content.splitlines() if line.strip()]


def _profile(path: Path, expected_gid: int) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _lines(path, expected_gid=expected_gid, expected_mode=0o640):
        if line.startswith("#"):
            continue
        if "=" not in line:
            raise NetworkPolicyError("The profile contains an invalid line.")
        name, value = line.split("=", 1)
        if not name or not value or name in values:
            raise NetworkPolicyError("The profile contains an invalid setting.")
        values[name] = value
    return values


def _literal_ipv4(value: str) -> ipaddress.IPv4Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise NetworkPolicyError(
            "A device host is not a literal IP address."
        ) from error
    if not isinstance(address, ipaddress.IPv4Address):
        raise NetworkPolicyError("A device host is not an IPv4 address.")
    return address


def _gateway_host(identifier: str) -> ipaddress.IPv4Address:
    host, separator, port = identifier.rpartition(":")
    if separator:
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise NetworkPolicyError("The Siglent identifier port is invalid.")
    else:
        host = identifier
    return _literal_ipv4(host)


def validate(
    unit_path: Path,
    dropin_path: Path,
    profile_path: Path,
    profile_group: str,
) -> None:
    """Require loopback inbound plus exactly two device IPv4 /32 egress allows."""

    try:
        profile_gid = grp.getgrnam(profile_group).gr_gid
    except KeyError as error:
        raise NetworkPolicyError("The profile service group is unavailable.") from error
    unit_lines = _lines(unit_path, expected_gid=0, expected_mode=0o644)
    dropin_lines = _lines(dropin_path, expected_gid=0, expected_mode=0o644)
    if unit_lines.count("IPAddressDeny=any") != 1:
        raise NetworkPolicyError("The unit lacks an exact default-deny network rule.")
    if unit_lines.count("IPAddressAllow=localhost") != 1:
        raise NetworkPolicyError("The unit lacks its exact loopback allow rule.")
    if not any(
        line.endswith(" -I -B -m dispenser_conditioning_mcp")
        for line in unit_lines
        if line.startswith("ExecStart=")
    ):
        raise NetworkPolicyError("The unit does not use isolated MCP startup.")
    if dropin_lines.count("[Service]") != 1:
        raise NetworkPolicyError("The device-network drop-in has an invalid section.")
    raw_allows = [
        line.removeprefix("IPAddressAllow=")
        for line in dropin_lines
        if line.startswith("IPAddressAllow=")
    ]
    if len(raw_allows) != 2 or len(set(raw_allows)) != 2:
        raise NetworkPolicyError("Exactly two distinct device allows are required.")
    if any(
        line.startswith("IPAddressDeny=")
        or (
            line.startswith("IPAddressAllow=")
            and line.removeprefix("IPAddressAllow=") not in raw_allows
        )
        for line in dropin_lines
    ):
        raise NetworkPolicyError(
            "The device-network drop-in contains an unapproved rule."
        )
    for value in raw_allows:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as error:
            raise NetworkPolicyError(
                "A device allow is not an exact IP network."
            ) from error
        if (
            not isinstance(network, ipaddress.IPv4Network)
            or network.prefixlen != 32
            or network.network_address.is_loopback
            or network.network_address.is_multicast
            or network.network_address.is_unspecified
        ):
            raise NetworkPolicyError("A device allow is not an approved IPv4 /32.")
    profile = _profile(profile_path, profile_gid)
    try:
        expected_allows = [
            f"{_literal_ipv4(profile['DISPENSER_HICUBE_HOST'])}/32",
            f"{_gateway_host(profile['DISPENSER_SIGLENT_IDENTIFIER'])}/32",
        ]
    except KeyError as error:
        raise NetworkPolicyError("The profile omits a device address.") from error
    if raw_allows != expected_allows:
        raise NetworkPolicyError("Device allows do not match the protected profile.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit-file", required=True, type=Path)
    parser.add_argument("--device-network-dropin", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--profile-group", required=True)
    args = parser.parse_args()
    try:
        validate(
            args.unit_file,
            args.device_network_dropin,
            args.profile,
            args.profile_group,
        )
    except NetworkPolicyError:
        print("Raspberry Pi device-network policy validation failed.", file=sys.stderr)
        return 1
    print("Raspberry Pi device-network policy validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
