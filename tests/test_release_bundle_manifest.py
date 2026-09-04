from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import jsonschema
import pytest

PROJECT_ROOT = Path(__file__).parents[1]
VALIDATOR = PROJECT_ROOT / "deployment" / "windows" / "release_bundle_manifest.py"
SCHEMA = PROJECT_ROOT / "deployment" / "windows" / "release-manifest.schema.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_wheel(
    path: Path,
    *,
    name: str,
    version: str,
    internal_tag: str = "py3-none-any",
    extra_members: dict[str, bytes] | None = None,
) -> None:
    dist_info_name = name.replace("-", "_")
    root = f"{dist_info_name}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{root}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
        archive.writestr(
            f"{root}/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: offline-test\n"
            "Root-Is-Purelib: true\n"
            f"Tag: {internal_tag}\n",
        )
        for member, data in (extra_members or {}).items():
            archive.writestr(member, data)


def _make_bundle(root: Path) -> tuple[Path, Path]:
    bundle = root / "bundle"
    wheelhouse = bundle / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    dependency_wheel = wheelhouse / "example_dep-1.2.3-py3-none-any.whl"
    _write_wheel(dependency_wheel, name="example-dep", version="1.2.3")
    lock = bundle / "python-dependencies.lock.txt"
    lock.write_text(
        "example-dep==1.2.3 \\\n    --hash=sha256:" + _sha256(dependency_wheel) + "\n",
        encoding="utf-8",
    )
    inventory = bundle / "python-runtime-inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platform": "win32",
                "machine": "AMD64",
                "python_major": 3,
                "python_minor": 13,
                "description": "test inventory",
                "distributions": [
                    {"name": "dispenser-conditioning-mcp", "version": "0.5.1"},
                    {"name": "example-dep", "version": "1.2.3"},
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    mcp_wheel = bundle / "dispenser_conditioning_mcp-0.5.1-py3-none-any.whl"
    _write_wheel(mcp_wheel, name="dispenser-conditioning-mcp", version="0.5.1")
    manifest = bundle / "release-manifest.json"
    return bundle, manifest


def _create(bundle: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(VALIDATOR),
            "create",
            "--bundle-root",
            str(bundle),
            "--dependency-lock-file",
            "python-dependencies.lock.txt",
            "--runtime-inventory-file",
            "python-runtime-inventory.json",
            "--mcp-wheel-file",
            "dispenser_conditioning_mcp-0.5.1-py3-none-any.whl",
            "--output",
            str(manifest),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _verify(bundle: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(VALIDATOR),
            "verify",
            "--bundle-root",
            str(bundle),
            "--manifest",
            str(manifest),
            "--expected-manifest-sha256",
            _sha256(manifest),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_release_manifest_is_closed_and_verifies_exact_bundle(tmp_path: Path) -> None:
    bundle, manifest = _make_bundle(tmp_path)

    created = _create(bundle, manifest)

    assert created.returncode == 0, created.stderr
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    schema_validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    validate = cast(Callable[[object], None], getattr(schema_validator, "validate"))
    validate(raw)
    verified = _verify(bundle, manifest)
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout) == {
        "dependency_lock_file": "python-dependencies.lock.txt",
        "mcp_wheel_file": "dispenser_conditioning_mcp-0.5.1-py3-none-any.whl",
        "runtime_inventory_file": "python-runtime-inventory.json",
        "wheelhouse_directory": "wheelhouse",
    }


@pytest.mark.parametrize("defect", ["missing", "extra", "changed"])
def test_release_manifest_rejects_wheelhouse_set_or_hash_drift(
    tmp_path: Path, defect: str
) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    assert _create(bundle, manifest).returncode == 0
    wheelhouse = bundle / "wheelhouse"
    wheel = next(wheelhouse.iterdir())
    if defect == "missing":
        wheel.unlink()
    elif defect == "extra":
        (wheelhouse / "unreviewed-1.0-py3-none-any.whl").write_bytes(b"extra")
    else:
        with wheel.open("ab") as stream:
            stream.write(b"changed bytes")

    result = _verify(bundle, manifest)

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip() == "Release bundle operation failed."


def test_release_manifest_creation_rejects_hash_not_present_in_lock(
    tmp_path: Path,
) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    (bundle / "python-dependencies.lock.txt").write_text(
        "example-dep==1.2.3 \\\n    --hash=sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )

    result = _create(bundle, manifest)

    assert result.returncode != 0
    assert not manifest.exists()


@pytest.mark.parametrize(
    "filename",
    [
        "example_dep-1.2.3-py3-none-manylinux_2_28_x86_64.whl",
        "example_dep-1.2.3-cp313-cp313-win32.whl",
        "example_dep-1.2.3-cp312-cp312-win_amd64.whl",
        "example_dep-1.2.3-cp313-cp313t-win_amd64.whl",
    ],
)
def test_release_manifest_rejects_incompatible_dependency_wheel_before_manifest(
    tmp_path: Path, filename: str
) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    original = next((bundle / "wheelhouse").iterdir())
    original.rename(original.with_name(filename))

    result = _create(bundle, manifest)

    assert result.returncode != 0
    assert not manifest.exists()


def test_release_manifest_rejects_renamed_linux_wheel_and_internal_tag(
    tmp_path: Path,
) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    wheel = next((bundle / "wheelhouse").iterdir())
    _write_wheel(
        wheel,
        name="example-dep",
        version="1.2.3",
        internal_tag="cp313-cp313-manylinux_2_28_x86_64",
        extra_members={"example_dep/native.so": b"linux binary"},
    )
    (bundle / "python-dependencies.lock.txt").write_text(
        "example-dep==1.2.3 \\\n    --hash=sha256:" + _sha256(wheel) + "\n",
        encoding="utf-8",
    )

    result = _create(bundle, manifest)

    assert result.returncode != 0
    assert not manifest.exists()


def test_release_manifest_rejects_internal_tag_mismatch_without_native_member(
    tmp_path: Path,
) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    wheel = next((bundle / "wheelhouse").iterdir())
    _write_wheel(
        wheel,
        name="example-dep",
        version="1.2.3",
        internal_tag="cp313-cp313-manylinux_2_28_x86_64",
    )
    (bundle / "python-dependencies.lock.txt").write_text(
        "example-dep==1.2.3 \\\n    --hash=sha256:" + _sha256(wheel) + "\n",
        encoding="utf-8",
    )

    result = _create(bundle, manifest)

    assert result.returncode != 0
    assert not manifest.exists()


def test_release_manifest_rejects_duplicate_critical_metadata(tmp_path: Path) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    wheel = next((bundle / "wheelhouse").iterdir())
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr(
                "example_dep-1.2.3.dist-info/METADATA",
                "Metadata-Version: 2.1\nName: example-dep\nVersion: 1.2.3\n",
            )
    (bundle / "python-dependencies.lock.txt").write_text(
        "example-dep==1.2.3 \\\n    --hash=sha256:" + _sha256(wheel) + "\n",
        encoding="utf-8",
    )

    result = _create(bundle, manifest)

    assert result.returncode != 0
    assert not manifest.exists()


def test_release_manifest_rejects_missing_or_extra_target_lock_member(
    tmp_path: Path,
) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    wheel = next((bundle / "wheelhouse").iterdir())
    lock = bundle / "python-dependencies.lock.txt"
    lock.write_text(
        lock.read_text(encoding="utf-8")
        + "extra-dependency==1.0 \\\n"
        + "    --hash=sha256:"
        + "0" * 64
        + "\n",
        encoding="utf-8",
    )
    assert wheel.exists()

    result = _create(bundle, manifest)

    assert result.returncode != 0
    assert not manifest.exists()


def test_release_manifest_rejects_markered_bootstrap_lock(tmp_path: Path) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    wheel = next((bundle / "wheelhouse").iterdir())
    (bundle / "python-dependencies.lock.txt").write_text(
        "example-dep==1.2.3 ; sys_platform == 'win32' \\\n"
        "    --hash=sha256:" + _sha256(wheel) + "\n",
        encoding="utf-8",
    )

    result = _create(bundle, manifest)

    assert result.returncode != 0
    assert not manifest.exists()


def test_release_manifest_rejects_unknown_field_even_with_new_outer_hash(
    tmp_path: Path,
) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    assert _create(bundle, manifest).returncode == 0
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["unapproved"] = True
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    result = _verify(bundle, manifest)

    assert result.returncode != 0
    assert result.stderr.strip() == "Release bundle operation failed."
