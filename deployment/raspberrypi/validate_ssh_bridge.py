"""Validate the exact operator-owned forwarding-only SSH bridge boundary."""

from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path


class SshBridgeError(RuntimeError):
    """Reject a mutable, broad, placeholder, or command-capable SSH bridge."""


def _root_file(path: Path, mode: int) -> str:
    try:
        info = path.lstat()
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SshBridgeError("An SSH bridge policy file is unavailable.") from error
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, mode)
    ):
        raise SshBridgeError("An SSH bridge policy file has invalid access.")
    return text


def _required_block(text: str, user: str, port: int) -> None:
    expected = (
        f"Match User {user}\n"
        f"    AuthorizedKeysFile /etc/dispenser-conditioning-mcp/ssh/{user}\n"
        "    AuthenticationMethods publickey\n"
        "    PasswordAuthentication no\n"
        "    KbdInteractiveAuthentication no\n"
        "    AllowTcpForwarding local\n"
        f"    PermitOpen 127.0.0.1:{port}\n"
        "    PermitTTY no\n"
        "    X11Forwarding no\n"
        "    AllowAgentForwarding no\n"
        "    PermitTunnel no\n"
        "    GatewayPorts no\n"
        "    MaxSessions 0"
    )
    if text.count(expected) != 1:
        raise SshBridgeError("An SSH bridge Match block is not exact.")


def _key(path: Path, user: str, port: int) -> None:
    text = _root_file(path, 0o600).strip()
    prefix = f'restrict,port-forwarding,permitopen="127.0.0.1:{port}" ssh-ed25519 '
    if (
        not text.startswith(prefix)
        or not text.endswith(f" {user}")
        or "replace-with" in text
        or len(text.split()) != 4
    ):
        raise SshBridgeError("An SSH bridge public-key restriction is invalid.")


def validate(config: Path, hil_key: Path, production_key: Path) -> None:
    config_text = _root_file(config, 0o600)
    before_first_match = config_text.partition("Match User ")[0]
    if "AuthorizedKeysFile" in before_first_match:
        raise SshBridgeError("A global SSH authorized-key override is forbidden.")
    _required_block(config_text, "mcp-bridge-hil", 8001)
    _required_block(config_text, "mcp-bridge-prod", 8002)
    _key(hil_key, "mcp-bridge-hil", 8001)
    _key(production_key, "mcp-bridge-prod", 8002)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sshd-config", required=True, type=Path)
    parser.add_argument("--hil-authorized-key", required=True, type=Path)
    parser.add_argument("--production-authorized-key", required=True, type=Path)
    args = parser.parse_args()
    try:
        validate(
            args.sshd_config, args.hil_authorized_key, args.production_authorized_key
        )
    except SshBridgeError:
        print("Raspberry Pi SSH bridge validation failed.", file=sys.stderr)
        return 1
    print("Raspberry Pi SSH bridge validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
