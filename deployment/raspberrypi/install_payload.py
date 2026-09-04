"""Install the exact authenticated offline MCP payload on Raspberry Pi."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import NoReturn, cast

DOMAIN = b"dcp-pi-dependency-tree-v1\0"
PROJECT = "dispenser-conditioning-mcp"
SYSTEM_PYTHON = Path("/usr/bin/python3.13")
UV_BIN = Path("/opt/dispenser-conditioning-mcp/runtime/uv/0.11.7/bin/uv")
APP_ROOT = Path("/opt/dispenser-conditioning-mcp/app")
DEPENDENCY_ROOT = Path("/opt/dispenser-conditioning-mcp/dependencies")


class InstallError(RuntimeError):
    """Reject an incomplete, unauthenticated, or incompatible payload."""


def _fail(message: str) -> NoReturn:
    raise InstallError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise InstallError("A required release artifact is unavailable.") from error
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        _fail("A release artifact is not a regular file.")


def _load_authenticated_release(
    root: Path, manifest_path: Path, expected_hash: str
) -> dict[str, object]:
    """Authenticate the manifest and its verifier before executing the verifier."""

    _regular(manifest_path)
    if (
        re.fullmatch(r"[0-9a-f]{64}", expected_hash.lower()) is None
        or _sha256(manifest_path) != expected_hash.lower()
    ):
        _fail("The out-of-band release manifest hash does not match.")
    try:
        raw: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallError("The release manifest is unreadable.") from error
    if not isinstance(raw, dict):
        _fail("The release manifest shape is invalid.")
    recorded = cast(dict[str, object], raw)
    trees = recorded.get("trees")
    if not isinstance(trees, dict):
        _fail("The release manifest tree inventory is invalid.")
    release_tree = cast(dict[str, object], trees).get("release_bundle")
    if not isinstance(release_tree, dict) or not isinstance(
        release_tree.get("files"), list
    ):
        _fail("The release manifest file inventory is invalid.")
    verifier_relative = "deployment/pi_release_manifest.py"
    matching = [
        item
        for item in cast(list[object], release_tree["files"])
        if isinstance(item, dict) and item.get("path") == verifier_relative
    ]
    if len(matching) != 1 or not isinstance(matching[0].get("sha256"), str):
        _fail("The release verifier is not uniquely authenticated.")
    verifier = root.joinpath(*PurePosixPath(verifier_relative).parts)
    _regular(verifier)
    if _sha256(verifier) != matching[0]["sha256"]:
        _fail("The release verifier does not match the authenticated manifest.")
    spec = importlib.util.spec_from_file_location("dcp_pi_release_manifest", verifier)
    if spec is None or spec.loader is None:
        _fail("The authenticated release verifier cannot be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        verified: object = module.verify(root, manifest_path, expected_hash)
    except Exception as error:
        raise InstallError(
            "The authenticated release bundle verification failed."
        ) from error
    if not isinstance(verified, dict):
        _fail("The authenticated release manifest result is invalid.")
    return cast(dict[str, object], verified)


def _artifact(root: Path, manifest: dict[str, object], name: str) -> Path:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get(name), dict):
        _fail("A required release artifact record is unavailable.")
    record = cast(dict[str, object], artifacts[name])
    relative = record.get("path")
    if not isinstance(relative, str):
        _fail("A release artifact path is invalid.")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        _fail("A release artifact path is unsafe.")
    path = root.joinpath(*pure.parts)
    _regular(path)
    return path


def _release_file_record(
    manifest: dict[str, object], relative: str
) -> dict[str, object]:
    trees = manifest.get("trees")
    if not isinstance(trees, dict):
        _fail("The authenticated release file inventory is unavailable.")
    release_tree = cast(dict[str, object], trees).get("release_bundle")
    if not isinstance(release_tree, dict) or not isinstance(
        release_tree.get("files"), list
    ):
        _fail("The authenticated release file inventory is unavailable.")
    matching = [
        item
        for item in cast(list[object], release_tree["files"])
        if isinstance(item, dict) and item.get("path") == relative
    ]
    if len(matching) != 1:
        _fail("A deployed release file is not uniquely authenticated.")
    record = cast(dict[str, object], matching[0])
    if not isinstance(record.get("size"), int) or not isinstance(
        record.get("sha256"), str
    ):
        _fail("A deployed release file record is invalid.")
    return record


def _fresh_directory(
    path: Path,
    *,
    expected_children: set[str] | None = None,
    enforce_policy: bool = True,
) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise InstallError(
            "A protected deployment directory is unavailable."
        ) from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (enforce_policy and stat.S_IMODE(info.st_mode) != 0o755)
        or (enforce_policy and (info.st_uid, info.st_gid) != (0, 0))
    ):
        _fail("A protected deployment directory has invalid ownership or mode.")
    children = {item.name for item in path.iterdir()}
    if children != (expected_children or set()):
        _fail("A protected deployment directory is not fresh and exact.")


def _copy_new_file(source: Path, destination: Path) -> None:
    _regular(source)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, stream)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _verify_deployed_file(
    path: Path, *, expected_size: int, expected_sha256: str, enforce_owner: bool
) -> None:
    _regular(path)
    info = path.stat()
    if (
        info.st_size != expected_size
        or _sha256(path) != expected_sha256
        or (enforce_owner and stat.S_IMODE(info.st_mode) != 0o644)
        or (enforce_owner and (info.st_uid, info.st_gid) != (0, 0))
    ):
        _fail("A deployed payload file does not match its authenticated source.")


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _materialize_authenticated_payload(
    release_root: Path,
    manifest: dict[str, object],
    runtime_manifest: Path,
    *,
    app_root: Path = APP_ROOT,
    dependency_root: Path = DEPENDENCY_ROOT,
    enforce_owner: bool = True,
) -> None:
    """Copy the authenticated source payload once into fresh protected roots."""

    _fresh_directory(app_root, enforce_policy=enforce_owner)
    _fresh_directory(
        dependency_root,
        expected_children={"hicube", "py-siglent-spd3000"},
        enforce_policy=enforce_owner,
    )
    hicube_root = dependency_root / "hicube"
    siglent_root = dependency_root / "py-siglent-spd3000"
    _fresh_directory(hicube_root, enforce_policy=enforce_owner)
    _fresh_directory(siglent_root, enforce_policy=enforce_owner)

    release_files: list[tuple[str, Path]] = [
        ("deployment/verify_runtime_start.py", app_root / "verify_runtime_start.py"),
        (
            "tools/python_runtime_provenance.py",
            app_root / "python_runtime_provenance.py",
        ),
        (
            "artifacts/python-runtime-inventory.json",
            app_root / "python-runtime-inventory.json",
        ),
        (
            "dependencies/hicube/hicube_neo_client.py",
            hicube_root / "hicube_neo_client.py",
        ),
    ]
    driver_prefix = "dependencies/py-siglent-spd3000/src/siglent_spd3000/"
    trees = cast(dict[str, object], manifest["trees"])
    release_tree = cast(dict[str, object], trees["release_bundle"])
    driver_files = sorted(
        cast(str, item["path"])
        for item in cast(list[object], release_tree["files"])
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and cast(str, item["path"]).startswith(driver_prefix)
    )
    if not driver_files:
        _fail("The authenticated Siglent driver package is empty.")
    release_files.extend(
        (
            relative,
            siglent_root / "src/siglent_spd3000" / relative.removeprefix(driver_prefix),
        )
        for relative in driver_files
    )

    destinations = [destination for _, destination in release_files]
    destinations.append(app_root / "python-runtime-manifest.json")
    if len(destinations) != len(set(destinations)) or any(
        path.exists() or path.is_symlink() for path in destinations
    ):
        _fail("A deployed payload destination already exists.")

    required_directories: set[Path] = set()
    for destination in destinations:
        directory = destination.parent
        while directory not in {app_root, hicube_root, siglent_root}:
            required_directories.add(directory)
            directory = directory.parent
    for directory in sorted(required_directories, key=lambda item: len(item.parts)):
        directory.mkdir(mode=0o755)

    for relative, destination in release_files:
        source = release_root.joinpath(*PurePosixPath(relative).parts)
        record = _release_file_record(manifest, relative)
        _copy_new_file(source, destination)
        _verify_deployed_file(
            destination,
            expected_size=cast(int, record["size"]),
            expected_sha256=cast(str, record["sha256"]),
            enforce_owner=enforce_owner,
        )

    runtime_destination = app_root / "python-runtime-manifest.json"
    runtime_size = runtime_manifest.stat().st_size
    runtime_hash = _sha256(runtime_manifest)
    _copy_new_file(runtime_manifest, runtime_destination)
    _verify_deployed_file(
        runtime_destination,
        expected_size=runtime_size,
        expected_sha256=runtime_hash,
        enforce_owner=enforce_owner,
    )
    expected_files = set(destinations)
    actual_files = {
        path
        for root in (app_root, dependency_root)
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files or any(
        path.is_symlink()
        for root in (app_root, dependency_root)
        for path in root.rglob("*")
    ):
        _fail("The deployed payload tree is incomplete or contains extra objects.")
    for directory in required_directories:
        info = directory.stat()
        if enforce_owner and (
            stat.S_IMODE(info.st_mode) != 0o755 or (info.st_uid, info.st_gid) != (0, 0)
        ):
            _fail("A deployed payload directory has invalid ownership or mode.")
    for directory in sorted(
        {path.parent for path in destinations},
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        _sync_directory(directory)


def _candidate_tree(root: Path, expected: str) -> None:
    manifest_path = root / "candidate-manifest.json"
    _regular(manifest_path)
    try:
        raw: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallError(
            "The dependency candidate manifest is unreadable."
        ) from error
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        _fail("The dependency candidate identity is invalid.")
    entries = cast(list[object], raw["entries"])
    if raw.get("schema_version") != 1 or raw.get("dependency_wheel_count") != 35:
        _fail("The dependency candidate identity is invalid.")
    digest = hashlib.sha256(DOMAIN)
    approved = {"candidate-manifest.json"}
    previous = ""
    wheel_count = 0
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            _fail("A dependency candidate entry is invalid.")
        relative, size, item_hash = item["path"], item["size"], item["sha256"]
        if (
            not isinstance(relative, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not isinstance(item_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", item_hash) is None
            or relative <= previous
        ):
            _fail("A dependency candidate entry value is invalid.")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            _fail("A dependency candidate path is unsafe.")
        previous = relative
        path = root.joinpath(*pure.parts)
        _regular(path)
        if path.stat().st_size != size or _sha256(path) != item_hash:
            _fail("A dependency candidate entry does not match its manifest.")
        approved.add(relative)
        wheel_count += relative.startswith("wheelhouse/")
        digest.update(relative.encode())
        digest.update(b"\0" + str(size).encode() + b"\0" + item_hash.encode() + b"\n")
    actual = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if (
        wheel_count != 35
        or digest.hexdigest() != expected
        or actual != approved
        or any(path.is_symlink() for path in root.rglob("*"))
    ):
        _fail("The dependency candidate tree identity does not match.")


def _project_wheel(path: Path) -> None:
    _regular(path)
    if not path.name.endswith("-py3-none-any.whl"):
        _fail("The project wheel filename is invalid.")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or any(
                PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or name.lower().endswith((".so", ".pyd", ".dll", ".dylib"))
                for name in names
            ):
                _fail("The project wheel archive is unsafe or platform-specific.")
            metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
            wheel = [name for name in names if name.endswith(".dist-info/WHEEL")]
            if len(metadata) != 1 or len(wheel) != 1:
                _fail("The project wheel metadata set is invalid.")
            metadata_text = archive.read(metadata[0]).decode("utf-8", "strict")
            wheel_text = archive.read(wheel[0]).decode("utf-8", "strict")
    except (OSError, UnicodeError, zipfile.BadZipFile) as error:
        raise InstallError("The project wheel archive is unreadable.") from error
    if "\nName: dispenser-conditioning-mcp\n" not in "\n" + metadata_text:
        _fail("The project wheel name is invalid.")
    if "\nVersion: 0.5.1\n" not in "\n" + metadata_text:
        _fail("The project wheel version is invalid.")
    tags = [line[5:] for line in wheel_text.splitlines() if line.startswith("Tag: ")]
    if tags != ["py3-none-any"]:
        _fail("The project wheel tags are invalid.")


def _uv_identity(release_root: Path, manifest: dict[str, object]) -> None:
    uv_manifest_path = _artifact(release_root, manifest, "uv_manifest")
    try:
        raw: object = json.loads(uv_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallError("The uv runtime manifest is unreadable.") from error
    if not isinstance(raw, dict) or raw.get("version") != "0.11.7":
        _fail("The uv runtime manifest is invalid.")
    members = raw.get("members")
    if not isinstance(members, list):
        _fail("The uv runtime member inventory is invalid.")
    uv_records = [
        item
        for item in members
        if isinstance(item, dict)
        and item.get("path") == "uv-aarch64-unknown-linux-gnu/uv"
    ]
    if len(uv_records) != 1 or not isinstance(uv_records[0].get("sha256"), str):
        _fail("The uv executable identity is invalid.")
    _regular(UV_BIN)
    if _sha256(UV_BIN) != uv_records[0]["sha256"]:
        _fail("The installed uv executable does not match the authenticated release.")
    with UV_BIN.open("rb") as stream:
        header = stream.read(20)
    if (
        header[:6] != b"\x7fELF\x02\x01"
        or int.from_bytes(header[18:20], "little") != 183
    ):
        _fail("The installed uv executable is not ELF64 AArch64.")


def _run(command: list[str], environment: dict[str, str]) -> None:
    completed = subprocess.run(command, check=False, env=environment, timeout=300)
    if completed.returncode != 0:
        _fail("An offline payload installation command failed.")


def install(args: argparse.Namespace) -> None:
    """Complete authentication and all preflight before mutating the empty venv."""

    executable = Path(sys.executable).resolve(strict=True)
    if (
        os.name != "posix"
        or os.geteuid() != 0
        or sys.platform != "linux"
        or platform.machine() != "aarch64"
        or sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 13)
        or executable != SYSTEM_PYTHON
    ):
        _fail("Installation requires /usr/bin/python3.13 as root on Linux aarch64.")
    release_root = args.release_bundle.resolve(strict=True)
    release_manifest = args.release_manifest.resolve(strict=True)
    manifest = _load_authenticated_release(
        release_root, release_manifest, args.expected_release_manifest_sha256
    )
    candidate = release_root / "candidate"
    expected_candidate = manifest.get("dependency_candidate_tree_sha256")
    if not isinstance(expected_candidate, str):
        _fail("The dependency candidate identity is missing.")
    _candidate_tree(candidate, expected_candidate)
    project_wheel = _artifact(release_root, manifest, "project_wheel")
    inventory = _artifact(release_root, manifest, "runtime_inventory")
    provenance_tool = _artifact(release_root, manifest, "runtime_provenance_tool")
    _project_wheel(project_wheel)
    _uv_identity(release_root, manifest)
    _regular(args.runtime_manifest)
    _run(
        [
            str(SYSTEM_PYTHON),
            "-I",
            "-B",
            str(provenance_tool),
            "verify",
            "--manifest",
            str(args.runtime_manifest),
            "--expected-manifest-sha256",
            args.expected_runtime_manifest_sha256,
        ],
        {},
    )
    venv = args.venv
    info = venv.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o755)
        or any(venv.iterdir())
    ):
        _fail("The virtual environment path is not a fresh protected directory.")

    _materialize_authenticated_payload(release_root, manifest, args.runtime_manifest)

    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith(("PIP_", "UV_", "PYTHON")):
            environment.pop(name)
    environment.update(
        {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "UV_OFFLINE": "1",
            "UV_NO_INDEX": "1",
            "UV_NO_CACHE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    _run(
        [
            str(UV_BIN),
            "--no-config",
            "venv",
            "--python",
            str(SYSTEM_PYTHON),
            "--no-python-downloads",
            str(venv),
        ],
        environment,
    )
    venv_python = venv / "bin/python"
    lock = candidate / "requirements-trixie-arm64-cp313-exact.lock"
    _run(
        [
            str(UV_BIN),
            "--no-config",
            "pip",
            "sync",
            "--python",
            str(venv_python),
            "--offline",
            "--no-index",
            "--find-links",
            str(candidate / "wheelhouse"),
            "--require-hashes",
            "--strict",
            "--only-binary",
            ":all:",
            str(lock),
        ],
        environment,
    )
    _run(
        [
            str(UV_BIN),
            "--no-config",
            "pip",
            "install",
            "--python",
            str(venv_python),
            "--offline",
            "--no-index",
            "--no-deps",
            str(project_wheel),
        ],
        environment,
    )
    _run(
        [str(UV_BIN), "--no-config", "pip", "check", "--python", str(venv_python)],
        environment,
    )
    _run(
        [
            str(venv_python),
            "-I",
            "-B",
            "-m",
            "dispenser_conditioning_mcp.deployment_inventory",
            str(inventory),
        ],
        environment,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-bundle", required=True, type=Path)
    parser.add_argument("--release-manifest", required=True, type=Path)
    parser.add_argument("--expected-release-manifest-sha256", required=True)
    parser.add_argument("--runtime-manifest", required=True, type=Path)
    parser.add_argument("--expected-runtime-manifest-sha256", required=True)
    parser.add_argument("--venv", required=True, type=Path)
    args = parser.parse_args()
    try:
        install(args)
    except (InstallError, OSError):
        print("Raspberry Pi offline payload installation failed.", file=sys.stderr)
        return 1
    print("Raspberry Pi offline payload installation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
