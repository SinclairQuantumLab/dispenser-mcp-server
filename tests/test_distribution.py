from __future__ import annotations

import tomllib
from pathlib import Path


def test_sdist_has_explicit_product_allowlist_and_secret_safe_exclusions() -> None:
    project = Path(__file__).parents[1]
    configuration = tomllib.loads(
        (project / "pyproject.toml").read_text(encoding="utf-8")
    )
    sdist = configuration["tool"]["hatch"]["build"]["targets"]["sdist"]

    assert set(sdist["include"]) == {
        "/.env.example",
        "/AGENTS.md",
        "/README.md",
        "/deployment",
        "/docs",
        "/pyproject.toml",
        "/src",
        "/tests",
        "/uv.lock",
    }
    assert {
        "/.codex-tmp",
        "/.pytest_cache",
        "/.ruff_cache",
        "/.venv",
        "/dist",
        "/dependencies",
        "**/__pycache__",
        "**/*.pyc",
    }.issubset(set(sdist["exclude"]))
