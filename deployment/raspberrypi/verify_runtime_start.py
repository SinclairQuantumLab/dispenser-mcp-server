"""Fail service startup if the commissioned Debian CPython identity drifted."""

from __future__ import annotations

import os
import re
import subprocess

SYSTEM_PYTHON = "/usr/bin/python3.13"
PROVENANCE_TOOL = "/opt/dispenser-conditioning-mcp/app/python_runtime_provenance.py"
RUNTIME_MANIFEST = "/opt/dispenser-conditioning-mcp/app/python-runtime-manifest.json"


def main() -> int:
    expected = os.environ.get("DISPENSER_PYTHON_RUNTIME_MANIFEST_SHA256", "")
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        return 1
    completed = subprocess.run(
        [
            SYSTEM_PYTHON,
            "-I",
            "-B",
            PROVENANCE_TOOL,
            "verify",
            "--manifest",
            RUNTIME_MANIFEST,
            "--expected-manifest-sha256",
            expected,
        ],
        check=False,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        timeout=30,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
