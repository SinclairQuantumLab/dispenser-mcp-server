"""Create and verify one authenticated-boundary Raspberry Pi release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import NoReturn, cast

TREE_DOMAIN = b"dcp-pi-release-subtree-v1\0"
EXPECTED_TOP_LEVEL = {"artifacts", "candidate", "dependencies", "deployment", "tools"}
UV_FILES = {
    "UV_RUNTIME_PROVENANCE.md",
    "sha256-f2ee1cde9aabb4c6e43bd3f341dadaf42189a54e001e521346dc31547310e284.jsonl",
    "sha256.sum",
    "uv-0.11.7-aarch64-unknown-linux-gnu.SHA256",
    "uv-aarch64-unknown-linux-gnu.tar.gz",
    "uv-aarch64-unknown-linux-gnu.tar.gz.sha256",
    "uv-runtime-manifest.json",
    "verify-and-install-uv.sh",
}
EXPECTED_SIGLENT_COMMIT = "0984bba67d8e5651cd2d9aa7b2c0db2d6eb694f3"
EXPECTED_HICUBE_SHA256 = (
    "a7bdbf45836f6c92d149f0cdb2dee439d17fcd6b1ce3836404df23fa1c0a4325"
)
EXPECTED_DEPLOYMENT_FILES = {
    "dependency-candidate-reference.json",
    "initialize_layout.sh",
    "initialize_unloaded_hil_state.py",
    "install_payload.py",
    "ON_PI_VALIDATION.md",
    "pi_release_manifest.py",
    "profiles/production.env.template",
    "profiles/unloaded-hil.env.template",
    "python_runtime_provenance.py",
    "python-runtime-inventory.json",
    "README.md",
    "ssh/mcp-bridge-hil.authorized_key.template",
    "ssh/mcp-bridge-prod.authorized_key.template",
    "ssh/sshd_config.template",
    "systemd/device-network.conf.template",
    "systemd/dispenser-conditioning-mcp-hil.service",
    "systemd/dispenser-conditioning-mcp-production.service",
    "uv-candidate-reference.json",
    "validate_instance_separation.py",
    "validate_layout.py",
    "validate_network_policy.py",
    "validate_ssh_bridge.py",
    "verify_runtime_start.py",
}
EXPECTED_DEPLOYMENT_DIRECTORIES = {"profiles", "ssh", "systemd"}


class ManifestError(RuntimeError):
    """Reject an incomplete or drifted Pi release bundle."""


def _fail(message: str) -> NoReturn:
    raise ManifestError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file(path: Path, root: Path) -> dict[str, object]:
    try:
        info = path.lstat()
    except OSError as error:
        raise ManifestError("A required release artifact is unavailable.") from error
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        _fail("A required release artifact is not a regular file.")
    return {
        "path": path.relative_to(root).as_posix(),
        "size": info.st_size,
        "sha256": _sha256(path),
    }


def _tree(
    path: Path, root: Path, *, ignore_manifest: bool = False
) -> dict[str, object]:
    if not path.is_dir() or path.is_symlink():
        _fail("A required release tree is unavailable.")
    files: list[Path] = []
    for item in path.rglob("*"):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        ):
            _fail("A release tree contains a forbidden object.")
        if stat.S_ISREG(info.st_mode) and not (
            ignore_manifest and item == root / "release-manifest.json"
        ):
            files.append(item)
    files.sort(key=lambda item: item.relative_to(path).as_posix().encode("utf-8"))
    digest = hashlib.sha256(TREE_DOMAIN)
    entries: list[dict[str, object]] = []
    for item in files:
        relative = item.relative_to(path).as_posix()
        size = item.stat().st_size
        item_hash = _sha256(item)
        entries.append({"path": relative, "size": size, "sha256": item_hash})
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(item_hash.encode("ascii"))
        digest.update(b"\n")
    return {
        "path": path.relative_to(root).as_posix() or ".",
        "file_count": len(files),
        "tree_sha256": digest.hexdigest(),
        "files": entries,
    }


def _exact_directory(path: Path, names: set[str]) -> None:
    if (
        not path.is_dir()
        or path.is_symlink()
        or {item.name for item in path.iterdir()} != names
    ):
        _fail("A release directory contains an undeclared or missing entry.")


def _candidate_identity(candidate: Path) -> str:
    try:
        raw: object = json.loads(
            (candidate / "candidate-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(
            "The dependency candidate identity is unreadable."
        ) from error
    if not isinstance(raw, dict):
        _fail("The dependency candidate identity is invalid.")
    value = cast(dict[str, object], raw).get("tree_sha256")
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _fail("The dependency candidate tree identity is invalid.")
    return value


def _siglent_commit(driver_tree: Path) -> str:
    metadata = driver_tree / "siglent_spd3000/_build_commit.py"
    _file(metadata, driver_tree)
    try:
        content = metadata.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ManifestError(
            "The Siglent build commit metadata is unreadable."
        ) from error
    match = re.fullmatch(
        r'"""Generated at build time; do not edit\."""\s+COMMIT = [\'\"]([0-9a-f]{40,64})[\'\"]\s*',
        content,
    )
    if match is None or match.group(1) != EXPECTED_SIGLENT_COMMIT:
        _fail("The Siglent build commit identity is invalid.")
    return match.group(1)


def build(root: Path) -> dict[str, object]:
    """Reconstruct the exact release manifest from fixed bundle paths."""

    top_level = {
        item.name for item in root.iterdir() if item.name != "release-manifest.json"
    }
    if top_level != EXPECTED_TOP_LEVEL:
        _fail("The release bundle top-level set is not exact.")
    project_wheels = list((root / "artifacts").glob("*.whl"))
    if len(project_wheels) != 1:
        _fail("The release bundle must contain exactly one project wheel.")
    _exact_directory(
        root / "artifacts",
        {project_wheels[0].name, "python-runtime-inventory.json"},
    )
    _exact_directory(root / "dependencies", {"hicube", "py-siglent-spd3000"})
    _exact_directory(root / "dependencies/hicube", {"hicube_neo_client.py"})
    _exact_directory(root / "dependencies/py-siglent-spd3000", {"src"})
    _exact_directory(root / "dependencies/py-siglent-spd3000/src", {"siglent_spd3000"})
    _exact_directory(root / "tools", {"python_runtime_provenance.py", "uv"})
    _exact_directory(root / "tools/uv", UV_FILES)
    deployment_root = root / "deployment"
    deployment_files = {
        path.relative_to(deployment_root).as_posix()
        for path in deployment_root.rglob("*")
        if path.is_file()
    }
    deployment_directories = {
        path.relative_to(deployment_root).as_posix()
        for path in deployment_root.rglob("*")
        if path.is_dir()
    }
    if (
        deployment_files != EXPECTED_DEPLOYMENT_FILES
        or deployment_directories != EXPECTED_DEPLOYMENT_DIRECTORIES
        or any(path.is_symlink() for path in deployment_root.rglob("*"))
    ):
        _fail("The release deployment file set is not exact.")
    hicube_record = _file(root / "dependencies/hicube/hicube_neo_client.py", root)
    if hicube_record["sha256"] != EXPECTED_HICUBE_SHA256:
        _fail("The commissioned HiCube client identity is invalid.")
    return {
        "schema_version": 1,
        "kind": "dispenser_conditioning_mcp_raspberry_pi_release",
        "target": {
            "os": "raspberry_pi_os_trixie_64_bit",
            "platform": "linux",
            "machine": "aarch64",
            "python_major": 3,
            "python_minor": 13,
        },
        "package_version": "0.5.1",
        "public_tool_contract_version": "0.4.3",
        "dependency_candidate_tree_sha256": _candidate_identity(root / "candidate"),
        "siglent_driver_commit": _siglent_commit(
            root / "dependencies/py-siglent-spd3000/src"
        ),
        "artifacts": {
            "project_wheel": _file(project_wheels[0], root),
            "runtime_inventory": _file(
                root / "artifacts/python-runtime-inventory.json", root
            ),
            "runtime_provenance_tool": _file(
                root / "tools/python_runtime_provenance.py", root
            ),
            "uv_archive": _file(
                root / "tools/uv/uv-aarch64-unknown-linux-gnu.tar.gz", root
            ),
            "uv_manifest": _file(root / "tools/uv/uv-runtime-manifest.json", root),
            "uv_provenance_report": _file(
                root / "tools/uv/UV_RUNTIME_PROVENANCE.md", root
            ),
            "uv_installer": _file(root / "tools/uv/verify-and-install-uv.sh", root),
            "hicube_client": hicube_record,
        },
        "trees": {
            "release_bundle": _tree(root, root, ignore_manifest=True),
            "dependency_candidate": _tree(root / "candidate", root),
            "siglent_driver": _tree(root / "dependencies/py-siglent-spd3000", root),
            "deployment": _tree(root / "deployment", root),
            "uv_provenance": _tree(root / "tools/uv", root),
        },
    }


def _load(path: Path) -> dict[str, object]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            _fail("The release manifest is not a regular file.")
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError("The release manifest is unreadable.") from error
    if not isinstance(raw, dict):
        _fail("The release manifest shape is invalid.")
    return cast(dict[str, object], raw)


def create(root: Path, output: Path) -> None:
    if output != root / "release-manifest.json" or output.exists():
        _fail("The release manifest output path is invalid or already exists.")
    encoded = json.dumps(build(root), indent=2, sort_keys=True).encode("utf-8") + b"\n"
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "posix":
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def verify(root: Path, manifest: Path, expected_sha256: str) -> dict[str, object]:
    if manifest != root / "release-manifest.json":
        _fail("The release manifest path is invalid.")
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256.lower()) is None:
        _fail("The approved release manifest hash is invalid.")
    recorded = _load(manifest)
    if _sha256(manifest) != expected_sha256.lower():
        _fail("The out-of-band release manifest hash does not match.")
    if recorded != build(root):
        _fail("The release bundle does not match its authenticated manifest.")
    return recorded


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("--bundle-root", required=True, type=Path)
    create_parser.add_argument("--output", required=True, type=Path)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--bundle-root", required=True, type=Path)
    verify_parser.add_argument("--manifest", required=True, type=Path)
    verify_parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        root = args.bundle_root.resolve(strict=True)
        if args.command == "create":
            create(root, args.output.resolve(strict=False))
        else:
            verify(
                root, args.manifest.resolve(strict=True), args.expected_manifest_sha256
            )
    except (ManifestError, OSError):
        print("Raspberry Pi release manifest operation failed.", file=sys.stderr)
        return 1
    print("Raspberry Pi release manifest operation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
