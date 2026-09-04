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
if os.name == "nt":
    sys.modules.setdefault("pwd", ModuleType("pwd"))
module_path = PROJECT_ROOT / "deployment/raspberrypi/initialize_unloaded_hil_state.py"
spec = importlib.util.spec_from_file_location("dcp_pi_hil_initializer", module_path)
assert spec is not None and spec.loader is not None
initializer = cast(Any, importlib.util.module_from_spec(spec))
sys.modules[spec.name] = initializer
spec.loader.exec_module(initializer)


def prepare_operator_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uid: int = 1001,
    gid: int = 1001,
) -> None:
    def fake_getpwnam(_name: str) -> SimpleNamespace:
        return SimpleNamespace(pw_uid=uid, pw_gid=gid)

    def fake_lstat(_path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700,
            st_uid=uid,
            st_gid=gid,
        )

    monkeypatch.setattr(initializer.os, "name", "posix")
    monkeypatch.setattr(initializer.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        initializer.pwd,
        "getpwnam",
        fake_getpwnam,
        raising=False,
    )
    monkeypatch.setattr(
        initializer.Path,
        "lstat",
        fake_lstat,
    )


def test_preexisting_state_is_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "operation-state.json"
    path.write_bytes(b"existing trip")
    prepare_operator_context(monkeypatch)

    with pytest.raises(initializer.InitializationError, match="never overwritten"):
        initializer.initialize(path, "dispenser-hil", initializer.CONFIRMATION)

    assert path.read_bytes() == b"existing trip"


def test_creation_race_is_rejected_by_atomic_exclusive_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "operation-state.json"
    prepare_operator_context(monkeypatch)
    original_open = os.open

    def race_open(candidate: Path, flags: int, mode: int = 0o777) -> int:
        if candidate == path:
            assert flags & os.O_EXCL
            raise FileExistsError("racing initializer won")
        return original_open(candidate, flags, mode)

    monkeypatch.setattr(initializer.os, "open", race_open)

    with pytest.raises(initializer.InitializationError, match="could not be committed"):
        initializer.initialize(path, "dispenser-hil", initializer.CONFIRMATION)

    assert not path.exists()
