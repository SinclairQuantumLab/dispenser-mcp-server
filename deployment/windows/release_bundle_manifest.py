"""Create and verify the exact offline Windows Python-payload manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import sys
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Any, NoReturn, cast

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^ ;\\]+)$"
)
_PROJECT = "dispenser-conditioning-mcp"
_REPARSE_POINT = 0x400
_WHEEL_TAG = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$")


class BundleValidationError(ValueError):
    """Raised when a release bundle is incomplete or internally inconsistent."""


def _fail(message: str) -> NoReturn:
    raise BundleValidationError(message)


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _assert_regular_file(path: Path) -> None:
    try:
        stat = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise BundleValidationError(
            "A required release artifact is unavailable."
        ) from exc
    if not path.is_file() or path.is_symlink():
        _fail("A required release artifact is not a regular file.")
    if getattr(stat, "st_file_attributes", 0) & _REPARSE_POINT:
        _fail("A release artifact is a forbidden reparse point.")


def _assert_leaf_name(value: object) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        _fail("A manifest filename is invalid.")
    if value in {".", ".."} or "/" in value or "\\" in value:
        _fail("A manifest filename is invalid.")
    return value


def _assert_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail("A manifest SHA-256 value is invalid.")
    return value


def _load_json(path: Path) -> dict[str, object]:
    _assert_regular_file(path)
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            _fail("A release manifest document is too large.")
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError(
            "A release manifest document is unreadable."
        ) from exc
    if not isinstance(raw, dict):
        _fail("A release manifest document has an invalid shape.")
    return cast(dict[str, object], raw)


def _assert_keys(value: dict[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        _fail("A release manifest object has an invalid shape.")


def _load_runtime_inventory(path: Path) -> tuple[dict[str, object], dict[str, str]]:
    raw = _load_json(path)
    _assert_keys(
        raw,
        {
            "schema_version",
            "platform",
            "machine",
            "python_major",
            "python_minor",
            "distributions",
            "description",
        },
    )
    if (
        raw["schema_version"] != 1
        or raw["platform"] != "win32"
        or raw["machine"] != "AMD64"
        or raw["python_major"] != 3
        or raw["python_minor"] != 13
    ):
        _fail("The runtime inventory target is unsupported.")
    if any(
        isinstance(raw[key], bool)
        for key in ("schema_version", "python_major", "python_minor")
    ):
        _fail("The runtime inventory target is invalid.")
    distributions_raw = raw["distributions"]
    if not isinstance(distributions_raw, list) or not distributions_raw:
        _fail("The runtime inventory distribution list is invalid.")
    distributions: dict[str, str] = {}
    for item_raw in cast(list[object], distributions_raw):
        if not isinstance(item_raw, dict):
            _fail("A runtime inventory distribution entry is invalid.")
        item = cast(dict[str, object], item_raw)
        _assert_keys(item, {"name", "version"})
        name_raw = item["name"]
        version_raw = item["version"]
        if not isinstance(name_raw, str) or _normalize_name(name_raw) != name_raw:
            _fail("A runtime inventory distribution name is invalid.")
        if not isinstance(version_raw, str) or not version_raw:
            _fail("A runtime inventory distribution version is invalid.")
        if name_raw in distributions:
            _fail("The runtime inventory contains a duplicate distribution.")
        distributions[name_raw] = version_raw
    if list(distributions) != sorted(distributions):
        _fail("The runtime inventory distribution list is not sorted.")
    if _PROJECT not in distributions:
        _fail("The runtime inventory omits the MCP package.")
    target = {
        "platform": raw["platform"],
        "machine": raw["machine"],
        "python_major": raw["python_major"],
        "python_minor": raw["python_minor"],
    }
    return target, distributions


def _parse_lock(path: Path) -> dict[str, tuple[str, set[str]]]:
    _assert_regular_file(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BundleValidationError("The dependency lock is unreadable.") from exc
    requirements: dict[str, tuple[str, set[str]]] = {}
    current_name: str | None = None
    for line in lines:
        if line and not line[0].isspace() and not line.startswith("#"):
            requirement = line.rstrip()
            if requirement.endswith("\\"):
                requirement = requirement[:-1].rstrip()
            if ";" in requirement:
                _fail("The target-specific dependency lock contains a marker.")
            match = _REQUIREMENT.fullmatch(requirement)
            if match is None:
                _fail("The dependency lock contains an unpinned requirement.")
            current_name = _normalize_name(match.group("name"))
            if current_name in requirements:
                _fail("The dependency lock contains a duplicate requirement.")
            requirements[current_name] = (match.group("version"), set())
            continue
        if current_name is None:
            continue
        for digest in re.findall(r"--hash=sha256:([0-9a-f]{64})", line):
            requirements[current_name][1].add(digest)
    if not requirements or any(not hashes for _, hashes in requirements.values()):
        _fail("The dependency lock omits required artifact hashes.")
    return requirements


def _python_abi_tag_is_compatible(python_tag: str, abi_tag: str) -> bool:
    if abi_tag == "none":
        if python_tag == "py3":
            return True
        generic = re.fullmatch(r"py3(?P<minor>[0-9]+)", python_tag)
        if generic is not None:
            return int(generic.group("minor")) <= 13
        return python_tag == "cp313"
    if python_tag == "cp313" and abi_tag == "cp313":
        return True
    stable = re.fullmatch(r"cp3(?P<minor>[0-9]+)", python_tag)
    return (
        abi_tag == "abi3"
        and stable is not None
        and 2 <= int(stable.group("minor")) <= 13
    )


def _wheel_tags_are_compatible(
    python_tags: str, abi_tags: str, platform_tags: str
) -> bool:
    if any(
        _WHEEL_TAG.fullmatch(value) is None
        for value in (python_tags, abi_tags, platform_tags)
    ):
        return False
    for python_tag in python_tags.split("."):
        for abi_tag in abi_tags.split("."):
            if not _python_abi_tag_is_compatible(python_tag, abi_tag):
                continue
            for platform_tag in platform_tags.split("."):
                if platform_tag == "win_amd64":
                    return True
                if (
                    platform_tag == "any"
                    and abi_tag == "none"
                    and python_tag.startswith("py")
                ):
                    return True
    return False


def _parse_wheel_name(filename: str) -> tuple[str, str]:
    if not filename.endswith(".whl"):
        _fail("The wheelhouse contains a non-wheel artifact.")
    try:
        prefix, python_tags, abi_tags, platform_tags = filename[:-4].rsplit("-", 3)
    except ValueError:
        _fail("A wheel filename is invalid.")
    prefix_parts = prefix.split("-")
    if len(prefix_parts) not in {2, 3}:
        _fail("A wheel filename is invalid.")
    if (
        len(prefix_parts) == 3
        and re.fullmatch(r"[0-9][A-Za-z0-9_]*", prefix_parts[2]) is None
    ):
        _fail("A wheel build tag is invalid.")
    if not prefix_parts[0] or not prefix_parts[1]:
        _fail("A wheel filename is invalid.")
    if not _wheel_tags_are_compatible(python_tags, abi_tags, platform_tags):
        _fail("A wheel is incompatible with Windows CPython 3.13 AMD64.")
    return _normalize_name(prefix_parts[0]), prefix_parts[1]


def _expanded_filename_tags(filename: str) -> set[str]:
    prefix, python_tags, abi_tags, platform_tags = filename[:-4].rsplit("-", 3)
    del prefix
    return {
        f"{python_tag}-{abi_tag}-{platform_tag}"
        for python_tag in python_tags.split(".")
        for abi_tag in abi_tags.split(".")
        for platform_tag in platform_tags.split(".")
    }


def _safe_wheel_member_name(name: str) -> bool:
    if not name or "\\" in name or name.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    normalized = posixpath.normpath(name)
    return normalized == name.rstrip("/") and all(
        part not in {"", ".", ".."} for part in normalized.split("/")
    )


def _validate_wheel_archive(path: Path, name: str, version: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            entry_names = [entry.filename for entry in entries]
            if len(entry_names) != len(set(entry_names)):
                _fail("A wheel archive contains duplicate entries.")
            for entry in entries:
                if not _safe_wheel_member_name(entry.filename):
                    _fail("A wheel archive contains an unsafe path.")
                unix_mode = (entry.external_attr >> 16) & 0o170000
                if unix_mode == 0o120000:
                    _fail("A wheel archive contains a symbolic link.")
                lowered = entry.filename.lower()
                if lowered.endswith((".so", ".dylib")):
                    _fail("A wheel contains a non-Windows native library.")

            metadata_entries = [
                entry
                for entry in entries
                if re.fullmatch(r"[^/]+\.dist-info/METADATA", entry.filename)
            ]
            wheel_entries = [
                entry
                for entry in entries
                if re.fullmatch(r"[^/]+\.dist-info/WHEEL", entry.filename)
            ]
            dist_info_roots = {
                entry.filename.split("/", 1)[0]
                for entry in entries
                if ".dist-info/" in entry.filename
            }
            if (
                len(metadata_entries) != 1
                or len(wheel_entries) != 1
                or len(dist_info_roots) != 1
            ):
                _fail("A wheel must contain one matching dist-info metadata set.")
            expected_root = metadata_entries[0].filename.split("/", 1)[0]
            if wheel_entries[0].filename.split("/", 1)[0] != expected_root:
                _fail("A wheel contains mismatched dist-info metadata.")
            dist_info_identity = expected_root.removesuffix(".dist-info")
            try:
                dist_info_name, dist_info_version = dist_info_identity.rsplit("-", 1)
            except ValueError:
                _fail("A wheel dist-info identity is invalid.")
            if _normalize_name(dist_info_name) != name or dist_info_version != version:
                _fail("A wheel dist-info identity does not match its filename.")

            metadata_text = archive.read(metadata_entries[0]).decode("utf-8", "strict")
            wheel_text = archive.read(wheel_entries[0]).decode("utf-8", "strict")
    except (OSError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise BundleValidationError("A wheel archive is unreadable.") from exc

    metadata = Parser().parsestr(metadata_text)
    metadata_names = metadata.get_all("Name", [])
    metadata_versions = metadata.get_all("Version", [])
    if (
        len(metadata_names) != 1
        or len(metadata_versions) != 1
        or _normalize_name(metadata_names[0]) != name
        or metadata_versions[0] != version
    ):
        _fail("Wheel METADATA identity does not match the approved distribution.")
    wheel_metadata = Parser().parsestr(wheel_text)
    declared_tags = wheel_metadata.get_all("Tag", [])
    expected_tags = _expanded_filename_tags(path.name)
    if not declared_tags or set(declared_tags) != expected_tags:
        _fail("Wheel internal tags do not match its filename tags.")
    for tag in declared_tags:
        try:
            python_tag, abi_tag, platform_tag = tag.split("-", 2)
        except ValueError:
            _fail("A wheel declares an invalid internal tag.")
        if not _wheel_tags_are_compatible(python_tag, abi_tag, platform_tag):
            _fail("A wheel declares a target-incompatible internal tag.")


def _artifact(path: Path) -> dict[str, str]:
    _assert_regular_file(path)
    return {"file": path.name, "sha256": _sha256(path)}


def _build_manifest(
    bundle_root: Path,
    *,
    dependency_lock_file: str,
    runtime_inventory_file: str,
    mcp_wheel_file: str,
) -> dict[str, object]:
    lock_path = bundle_root / _assert_leaf_name(dependency_lock_file)
    inventory_path = bundle_root / _assert_leaf_name(runtime_inventory_file)
    mcp_wheel_path = bundle_root / _assert_leaf_name(mcp_wheel_file)
    wheelhouse = bundle_root / "wheelhouse"
    if not wheelhouse.is_dir() or wheelhouse.is_symlink():
        _fail("The wheelhouse directory is unavailable.")
    if (
        getattr(wheelhouse.stat(follow_symlinks=False), "st_file_attributes", 0)
        & _REPARSE_POINT
    ):
        _fail("The wheelhouse directory is a forbidden reparse point.")

    target, inventory = _load_runtime_inventory(inventory_path)
    lock = _parse_lock(lock_path)
    wheel_paths = sorted(wheelhouse.iterdir(), key=lambda item: item.name)
    if not wheel_paths:
        _fail("The wheelhouse is incomplete.")
    wheels: dict[str, dict[str, str]] = {}
    for wheel_path in wheel_paths:
        _assert_regular_file(wheel_path)
        name, version = _parse_wheel_name(wheel_path.name)
        _validate_wheel_archive(wheel_path, name, version)
        if name in wheels:
            _fail("The wheelhouse contains duplicate distribution artifacts.")
        wheels[name] = {
            "file": wheel_path.name,
            "distribution": name,
            "version": version,
            "sha256": _sha256(wheel_path),
        }

    expected_dependencies = set(inventory) - {_PROJECT}
    if set(wheels) != expected_dependencies:
        _fail("The wheelhouse distribution set does not match the runtime inventory.")
    if set(lock) != expected_dependencies:
        _fail("The target-specific dependency lock set does not match the inventory.")
    for name, wheel in wheels.items():
        if wheel["version"] != inventory[name]:
            _fail("A wheel version does not match the runtime inventory.")
        if name not in lock or lock[name][0] != wheel["version"]:
            _fail("A wheel does not match the dependency lock version.")
        if wheel["sha256"] not in lock[name][1]:
            _fail("A wheel hash is absent from its dependency lock entry.")

    mcp_name, mcp_version = _parse_wheel_name(mcp_wheel_path.name)
    _validate_wheel_archive(mcp_wheel_path, mcp_name, mcp_version)
    if mcp_name != _PROJECT or inventory[_PROJECT] != mcp_version:
        _fail("The MCP wheel does not match the runtime inventory.")
    return {
        "schema_version": 1,
        "kind": "dispenser_mcp_windows_python_payload",
        "target": target,
        "dependency_lock": _artifact(lock_path),
        "runtime_inventory": _artifact(inventory_path),
        "mcp_wheel": _artifact(mcp_wheel_path),
        "wheelhouse": [wheels[name] for name in sorted(wheels)],
    }


def _validate_stored_manifest(raw: dict[str, object]) -> None:
    _assert_keys(
        raw,
        {
            "schema_version",
            "kind",
            "target",
            "dependency_lock",
            "runtime_inventory",
            "mcp_wheel",
            "wheelhouse",
        },
    )
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        _fail("The release manifest schema version is invalid.")
    if raw["kind"] != "dispenser_mcp_windows_python_payload":
        _fail("The release manifest kind is invalid.")
    target_raw = raw["target"]
    if not isinstance(target_raw, dict):
        _fail("The release manifest target is invalid.")
    target = cast(dict[str, object], target_raw)
    _assert_keys(target, {"platform", "machine", "python_major", "python_minor"})
    if target != {
        "platform": "win32",
        "machine": "AMD64",
        "python_major": 3,
        "python_minor": 13,
    }:
        _fail("The release manifest target is unsupported.")
    for key in ("dependency_lock", "runtime_inventory", "mcp_wheel"):
        artifact_raw = raw[key]
        if not isinstance(artifact_raw, dict):
            _fail("A release artifact record is invalid.")
        artifact = cast(dict[str, object], artifact_raw)
        _assert_keys(artifact, {"file", "sha256"})
        _assert_leaf_name(artifact["file"])
        _assert_sha256(artifact["sha256"])
    wheelhouse_raw = raw["wheelhouse"]
    if not isinstance(wheelhouse_raw, list) or not wheelhouse_raw:
        _fail("The release manifest wheelhouse is invalid.")
    seen_files: set[str] = set()
    seen_distributions: set[str] = set()
    for entry_raw in cast(list[object], wheelhouse_raw):
        if not isinstance(entry_raw, dict):
            _fail("A release wheel record is invalid.")
        entry = cast(dict[str, object], entry_raw)
        _assert_keys(entry, {"file", "distribution", "version", "sha256"})
        filename = _assert_leaf_name(entry["file"])
        digest = _assert_sha256(entry["sha256"])
        distribution = entry["distribution"]
        version = entry["version"]
        if (
            not isinstance(distribution, str)
            or _normalize_name(distribution) != distribution
            or not isinstance(version, str)
            or not version
            or not digest
        ):
            _fail("A release wheel record is invalid.")
        if filename in seen_files or distribution in seen_distributions:
            _fail("The release manifest contains a duplicate wheel record.")
        seen_files.add(filename)
        seen_distributions.add(distribution)


def create_manifest(args: argparse.Namespace) -> int:
    bundle_root = Path(args.bundle_root).resolve(strict=True)
    output = Path(args.output).resolve(strict=False)
    if output.parent != bundle_root or output.name != "release-manifest.json":
        _fail("The release manifest output path is invalid.")
    if output.exists():
        _fail("The release manifest output already exists.")
    manifest = _build_manifest(
        bundle_root,
        dependency_lock_file=args.dependency_lock_file,
        runtime_inventory_file=args.runtime_inventory_file,
        mcp_wheel_file=args.mcp_wheel_file,
    )
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("Release bundle manifest creation passed.")
    return 0


def verify_manifest(args: argparse.Namespace) -> int:
    bundle_root = Path(args.bundle_root).resolve(strict=True)
    manifest_path = Path(args.manifest).resolve(strict=True)
    if (
        manifest_path.parent != bundle_root
        or manifest_path.name != "release-manifest.json"
    ):
        _fail("The release manifest path is invalid.")
    expected_manifest_sha256 = _assert_sha256(args.expected_manifest_sha256)
    if _sha256(manifest_path) != expected_manifest_sha256:
        _fail("The release manifest hash does not match the approved value.")
    stored = _load_json(manifest_path)
    _validate_stored_manifest(stored)
    lock = cast(dict[str, object], stored["dependency_lock"])
    inventory = cast(dict[str, object], stored["runtime_inventory"])
    mcp_wheel = cast(dict[str, object], stored["mcp_wheel"])
    expected = _build_manifest(
        bundle_root,
        dependency_lock_file=_assert_leaf_name(lock["file"]),
        runtime_inventory_file=_assert_leaf_name(inventory["file"]),
        mcp_wheel_file=_assert_leaf_name(mcp_wheel["file"]),
    )
    if stored != expected:
        _fail("The release bundle does not match its approved manifest.")
    print(
        json.dumps(
            {
                "dependency_lock_file": lock["file"],
                "runtime_inventory_file": inventory["file"],
                "mcp_wheel_file": mcp_wheel["file"],
                "wheelhouse_directory": "wheelhouse",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--bundle-root", required=True)
    create.add_argument("--dependency-lock-file", required=True)
    create.add_argument("--runtime-inventory-file", required=True)
    create.add_argument("--mcp-wheel-file", required=True)
    create.add_argument("--output", required=True)
    create.set_defaults(handler=create_manifest)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--bundle-root", required=True)
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--expected-manifest-sha256", required=True)
    verify.set_defaults(handler=verify_manifest)
    return parser


def main() -> int:
    try:
        args = _parser().parse_args()
        handler = cast(Any, args.handler)
        return cast(int, handler(args))
    except (BundleValidationError, OSError, ValueError):
        print("Release bundle operation failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
