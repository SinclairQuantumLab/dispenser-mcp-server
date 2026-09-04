"""Validate exact Raspberry Pi service identities and filesystem boundaries."""

from __future__ import annotations

import argparse
import os
import pwd
import stat
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path("/opt/dispenser-conditioning-mcp")
CONFIG_ROOT = Path("/etc/dispenser-conditioning-mcp")
STATE_ROOT = Path("/var/lib/dispenser-conditioning-mcp")
HIL_STATE = STATE_ROOT / "unloaded-hil/operation-state.json"
SERVICES = ("dispenser-hil", "dispenser-prod")
VENV_PYTHON = APP_ROOT / "venv/bin/python"


class LayoutError(RuntimeError):
    """Reject deployment ownership, mode, identity, or storage drift."""


def _account(name: str) -> pwd.struct_passwd:
    try:
        account = pwd.getpwnam(name)
    except KeyError as error:
        raise LayoutError("A required service identity is missing.") from error
    if account.pw_uid == 0 or account.pw_shell not in {
        "/usr/sbin/nologin",
        "/sbin/nologin",
        "/bin/false",
    }:
        raise LayoutError("A service identity is login-capable or privileged.")
    groups = set(os.getgrouplist(name, account.pw_gid))
    if groups != {account.pw_gid}:
        raise LayoutError("A service identity has an unapproved supplementary group.")
    status = subprocess.run(
        ["passwd", "--status", name],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    fields = status.stdout.split()
    if status.returncode != 0 or len(fields) < 2 or fields[1] != "L":
        raise LayoutError("A service identity password is not locked.")
    return account


def _check(path: Path, *, uid: int, gid: int, mode: int, directory: bool) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise LayoutError("A required deployment object is unavailable.") from error
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise LayoutError("A deployment object has an invalid type.")
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, mode):
        raise LayoutError("A deployment object has invalid ownership or mode.")


def _check_immutable_tree(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        ):
            raise LayoutError("The immutable application tree has an invalid object.")
        if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise LayoutError("The immutable application tree is writable or unowned.")


def _check_protected_config_tree(root: Path, gid: int) -> None:
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise LayoutError("A protected configuration object is a symbolic link.")
        if stat.S_ISDIR(info.st_mode):
            expected_mode = 0o750
        elif stat.S_ISREG(info.st_mode):
            expected_mode = 0o640
        else:
            raise LayoutError("A protected configuration object has an invalid type.")
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (
            0,
            gid,
            expected_mode,
        ):
            raise LayoutError("A protected configuration object has invalid access.")


def _filesystem_type(path: Path) -> str:
    resolved = path.resolve(strict=True)
    best: tuple[int, str] | None = None
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise LayoutError("Mount information is unavailable.") from error
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
            mountpoint = Path(fields[4].replace("\\040", " ")).resolve()
            filesystem = fields[separator + 1]
        except (ValueError, IndexError, OSError):
            continue
        try:
            resolved.relative_to(mountpoint)
        except ValueError:
            continue
        candidate = (len(mountpoint.parts), filesystem)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        raise LayoutError("State filesystem could not be identified.")
    return best[1]


def validate(*, require_state: bool) -> None:
    """Validate the deployed boundary; optionally require initialized HIL state."""

    if os.name != "posix" or os.geteuid() != 0:
        raise LayoutError("Layout validation requires a POSIX root operator.")
    hil = _account("dispenser-hil")
    production = _account("dispenser-prod")
    if hil.pw_uid == production.pw_uid or hil.pw_gid == production.pw_gid:
        raise LayoutError("HIL and production identities are not distinct.")

    _check(APP_ROOT, uid=0, gid=0, mode=0o755, directory=True)
    _check(CONFIG_ROOT, uid=0, gid=0, mode=0o751, directory=True)
    _check(
        CONFIG_ROOT / "unloaded-hil",
        uid=0,
        gid=hil.pw_gid,
        mode=0o750,
        directory=True,
    )
    _check(
        CONFIG_ROOT / "production",
        uid=0,
        gid=production.pw_gid,
        mode=0o750,
        directory=True,
    )
    _check(STATE_ROOT, uid=0, gid=0, mode=0o711, directory=True)
    _check(
        STATE_ROOT / "unloaded-hil",
        uid=hil.pw_uid,
        gid=hil.pw_gid,
        mode=0o700,
        directory=True,
    )
    _check_immutable_tree(APP_ROOT)
    _check_protected_config_tree(CONFIG_ROOT / "unloaded-hil", hil.pw_gid)
    _check_protected_config_tree(CONFIG_ROOT / "production", production.pw_gid)
    for directory, account in (
        (CONFIG_ROOT / "unloaded-hil", hil),
        (CONFIG_ROOT / "production", production),
    ):
        _check(
            directory / "profile.env",
            uid=0,
            gid=account.pw_gid,
            mode=0o640,
            directory=False,
        )
        _check(
            directory / "gateway-auth.toml",
            uid=0,
            gid=account.pw_gid,
            mode=0o640,
            directory=False,
        )
    if _filesystem_type(STATE_ROOT / "unloaded-hil") != "ext4":
        raise LayoutError(
            "HIL durable state must use the approved local ext4 filesystem."
        )

    if require_state:
        _check(
            HIL_STATE,
            uid=hil.pw_uid,
            gid=hil.pw_gid,
            mode=0o600,
            directory=False,
        )
        validation = subprocess.run(
            [
                str(VENV_PYTHON),
                "-I",
                "-B",
                "-c",
                (
                    "from pathlib import Path; "
                    "from dispenser_conditioning_mcp.interlock import "
                    "FileUnloadedHilDurableStateProvider; "
                    "FileUnloadedHilDurableStateProvider("
                    f"Path({str(HIL_STATE)!r})).read_state()"
                ),
            ],
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
            capture_output=True,
            timeout=15,
        )
        if validation.returncode != 0:
            raise LayoutError("HIL durable state fails the installed strict model.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-state", action="store_true")
    args = parser.parse_args()
    try:
        validate(require_state=args.require_state)
    except LayoutError:
        print("Raspberry Pi deployment layout validation failed.", file=sys.stderr)
        return 1
    print("Raspberry Pi deployment layout validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
