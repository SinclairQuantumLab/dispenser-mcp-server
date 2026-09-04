from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
module_path = PROJECT_ROOT / "deployment/raspberrypi/pi_release_manifest.py"
spec = importlib.util.spec_from_file_location("dcp_pi_release_manifest", module_path)
assert spec is not None and spec.loader is not None
release = cast(Any, importlib.util.module_from_spec(spec))
sys.modules[spec.name] = release
spec.loader.exec_module(release)
installer_path = PROJECT_ROOT / "deployment/raspberrypi/install_payload.py"
installer_spec = importlib.util.spec_from_file_location(
    "dcp_pi_payload_installer", installer_path
)
assert installer_spec is not None and installer_spec.loader is not None
installer = cast(Any, importlib.util.module_from_spec(installer_spec))
sys.modules[installer_spec.name] = installer
installer_spec.loader.exec_module(installer)


def make_bundle(root: Path) -> Path:
    for directory in (
        "artifacts",
        "candidate",
        "dependencies/hicube",
        "dependencies/py-siglent-spd3000/src",
        "deployment",
        "tools/uv",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "artifacts/dispenser_conditioning_mcp-0.5.1-py3-none-any.whl").write_bytes(
        b"wheel"
    )
    (root / "artifacts/python-runtime-inventory.json").write_text("{}\n")
    (root / "candidate/candidate-manifest.json").write_text(
        json.dumps({"tree_sha256": "a" * 64}) + "\n"
    )
    hicube = root / "dependencies/hicube/hicube_neo_client.py"
    hicube.write_text("# client\n")
    release.EXPECTED_HICUBE_SHA256 = hashlib.sha256(hicube.read_bytes()).hexdigest()
    driver_package = root / "dependencies/py-siglent-spd3000/src/siglent_spd3000"
    driver_package.mkdir()
    (driver_package / "__init__.py").write_text("# driver\n")
    (driver_package / "_build_commit.py").write_text(
        '"""Generated at build time; do not edit."""\n\n'
        f"COMMIT = '{release.EXPECTED_SIGLENT_COMMIT}'\n"
    )
    for relative in release.EXPECTED_DEPLOYMENT_FILES:
        deployment_file = root / "deployment" / relative
        deployment_file.parent.mkdir(parents=True, exist_ok=True)
        deployment_file.write_text("# deployment fixture\n")
    (root / "tools/python_runtime_provenance.py").write_text("# provenance\n")
    for name in release.UV_FILES:
        (root / "tools/uv" / name).write_bytes(b"uv fixture\n")
    return root


def manifest_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_binds_every_file_and_rejects_drift(tmp_path: Path) -> None:
    root = make_bundle(tmp_path / "release")
    manifest = root / "release-manifest.json"
    encoded = json.dumps(release.build(root), indent=2, sort_keys=True) + "\n"
    manifest.write_text(encoded, encoding="utf-8")
    expected = manifest_hash(manifest)

    assert release.verify(root, manifest, expected)["package_version"] == "0.5.1"
    (root / "deployment/pi_release_manifest.py").write_text("# drift\n")

    with pytest.raises(release.ManifestError, match="does not match"):
        release.verify(root, manifest, expected)


def test_manifest_rejects_undeclared_extra_before_release(tmp_path: Path) -> None:
    root = make_bundle(tmp_path / "release")
    (root / "tools/unapproved").write_bytes(b"executable")

    with pytest.raises(release.ManifestError, match="undeclared"):
        release.build(root)


def test_manifest_rejects_transient_deployment_file(tmp_path: Path) -> None:
    root = make_bundle(tmp_path / "release")
    transient = root / "deployment/__pycache__/validator.cpython-313.pyc"
    transient.parent.mkdir()
    transient.write_bytes(b"not releasable")

    with pytest.raises(release.ManifestError, match="deployment file set"):
        release.build(root)


def test_authenticated_installer_materializes_exact_payload_once(
    tmp_path: Path,
) -> None:
    root = make_bundle(tmp_path / "release")
    manifest = release.build(root)
    runtime_manifest = tmp_path / "runtime.json"
    runtime_manifest.write_text('{"runtime":"fixture"}\n', encoding="utf-8")
    app_root = tmp_path / "installed/app"
    dependency_root = tmp_path / "installed/dependencies"
    app_root.mkdir(parents=True)
    (dependency_root / "hicube").mkdir(parents=True)
    (dependency_root / "py-siglent-spd3000").mkdir()

    installer._materialize_authenticated_payload(
        root,
        manifest,
        runtime_manifest,
        app_root=app_root,
        dependency_root=dependency_root,
        enforce_owner=False,
    )

    assert (app_root / "python-runtime-manifest.json").read_bytes() == (
        runtime_manifest.read_bytes()
    )
    assert (dependency_root / "hicube/hicube_neo_client.py").read_bytes() == (
        root / "dependencies/hicube/hicube_neo_client.py"
    ).read_bytes()
    assert (
        dependency_root / "py-siglent-spd3000/src/siglent_spd3000/_build_commit.py"
    ).is_file()
    with pytest.raises(installer.InstallError, match="not fresh"):
        installer._materialize_authenticated_payload(
            root,
            manifest,
            runtime_manifest,
            app_root=app_root,
            dependency_root=dependency_root,
            enforce_owner=False,
        )


def test_manifest_rejects_unknown_siglent_build_commit(tmp_path: Path) -> None:
    root = make_bundle(tmp_path / "release")
    metadata = (
        root / "dependencies/py-siglent-spd3000/src/siglent_spd3000/_build_commit.py"
    )
    metadata.write_text("COMMIT = 'unknown'\n", encoding="utf-8")

    with pytest.raises(release.ManifestError, match="build commit"):
        release.build(root)


def test_manifest_rejects_driver_package_outside_required_src_layout(
    tmp_path: Path,
) -> None:
    root = make_bundle(tmp_path / "release")
    (root / "dependencies/py-siglent-spd3000/wrong-layout").write_text(
        "not a package\n"
    )

    with pytest.raises(release.ManifestError, match="undeclared"):
        release.build(root)


def test_manifest_requires_out_of_band_hash(tmp_path: Path) -> None:
    root = make_bundle(tmp_path / "release")
    manifest = root / "release-manifest.json"
    manifest.write_text(
        json.dumps(release.build(root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(release.ManifestError, match="approved release manifest hash"):
        release.verify(root, manifest, "not-a-digest")


def test_installer_uses_single_release_authentication_boundary() -> None:
    release_path = release.__file__
    assert release_path is not None
    installer_source = (
        Path(release_path).with_name("install_payload.py").read_text(encoding="utf-8")
    )
    assert "--expected-release-manifest-sha256" in installer_source
    assert "--expected-project-wheel-sha256" not in installer_source
    assert "UV_NO_CACHE" in installer_source
    assert '"pip", "check"' in installer_source
    assert 'name.startswith(("PIP_", "UV_", "PYTHON"))' in installer_source
    assert 'SYSTEM_PYTHON = Path("/usr/bin/python3.13")' in installer_source
    assert "_materialize_authenticated_payload" in installer_source
    assert "os.O_EXCL" in installer_source
