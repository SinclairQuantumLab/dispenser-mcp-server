> Historical reference — retained for provenance, not an active deployment or
> testing gate. Current source-checkout research instructions are in the
> server README and deployment/raspberrypi/QUICK_COMMISSIONING.md. Legacy
> settings, hardened bundles, and commands below may not match the current pilot.

# On-Pi clean-install validation

This candidate is a dependency-only bundle. Run these checks on the exact
Raspberry Pi that will host the MCP, before adding the project wheel and before
enabling hardware control.

## 1. Establish the target

Use a fresh Raspberry Pi OS Trixie 64-bit installation. Set
`DCP_DEP_KIT` to the transferred candidate directory.

```bash
export DCP_DEP_KIT=/path/to/pi-dependency-candidate
test "$(uname -m)" = "aarch64"
test "$(dpkg --print-architecture)" = "arm64"
python3.13 -c 'import platform, sys; assert sys.implementation.name == "cpython"; assert sys.version_info[:2] == (3, 13); assert platform.machine() == "aarch64"'
getconf GNU_LIBC_VERSION
(cd "$DCP_DEP_KIT" && sha256sum --check SHA256SUMS)
python3.13 "$DCP_DEP_KIT/verify_candidate.py"
```

The glibc version must be at least 2.28. Record the full OS image version,
kernel, Python patch version, glibc version, Pi model, storage type, and power
protection in the commissioning record.

## 2. Re-audit the transferred wheels

The wheel audit uses only the Python standard library.

```bash
DCP_AUDIT_DIR="$(mktemp -d)"
python3.13 "$DCP_DEP_KIT/audit_wheelhouse.py" \
  "$DCP_DEP_KIT/wheelhouse" \
  --inventory "$DCP_AUDIT_DIR/wheelhouse-inventory.json" \
  --lock "$DCP_AUDIT_DIR/requirements.lock"
cmp "$DCP_AUDIT_DIR/wheelhouse-inventory.json" \
  "$DCP_DEP_KIT/wheelhouse-inventory.json"
cmp "$DCP_AUDIT_DIR/requirements.lock" \
  "$DCP_DEP_KIT/requirements-trixie-arm64-cp313-exact.lock"
```

## 3. Perform an offline clean install

Use the release-approved `uv` executable. Its binary and provenance are not in
this dependency candidate and must be pinned by the final host release.

```bash
DCP_VERIFY_DIR="$(mktemp -d)"
python3.13 -m venv --without-pip "$DCP_VERIFY_DIR/venv"
uv pip sync \
  --python "$DCP_VERIFY_DIR/venv/bin/python" \
  --offline \
  --no-index \
  --find-links "$DCP_DEP_KIT/wheelhouse" \
  --require-hashes \
  --strict \
  --only-binary :all: \
  "$DCP_DEP_KIT/requirements-trixie-arm64-cp313-exact.lock"
uv pip check --python "$DCP_VERIFY_DIR/venv/bin/python"
```

Confirm the native modules really load on the Pi:

```bash
"$DCP_VERIFY_DIR/venv/bin/python" - <<'PY'
import _cffi_backend
import asyncua
import cryptography
import mcp
import pydantic
import pydantic_core
import rpds

print("PASS: runtime and native imports")
PY
```

Inspect every native object for architecture and unresolved shared libraries:

```bash
find "$DCP_VERIFY_DIR/venv/lib/python3.13/site-packages" -type f -name '*.so' -print0 |
while IFS= read -r -d '' DCP_NATIVE_OBJECT; do
  file "$DCP_NATIVE_OBJECT"
  readelf -h "$DCP_NATIVE_OBJECT" | grep -q 'Machine:.*AArch64'
  if ldd "$DCP_NATIVE_OBJECT" | grep -q 'not found'; then
    echo "Unresolved library: $DCP_NATIVE_OBJECT" >&2
    exit 1
  fi
done
```

## 4. Final integrated release checks still required

After the independently built `dispenser-conditioning-mcp==0.5.1` wheel is
added, regenerate a combined one-wheel-per-project inventory and exact lock.
Repeat the offline clean install from an empty venv. Then run, on the Pi:

- import and entry-point smoke tests for the installed project wheel;
- the full non-hardware test suite where packaged;
- control-disabled systemd startup and restart tests;
- loopback-only listening-socket and Host/Origin policy tests;
- distinct HIL/production instance, port, auth, and state-path checks;
- reboot/crash tests proving no workflow resumes automatically;
- read-only identity/state commissioning before any actuation gate is opened.

Before starting either service, import the exact authenticated HiCube file by
path without opening a client or contacting its configured host:

```bash
/usr/bin/python3.13 -I -B - <<'PY'
import importlib.util
from pathlib import Path

path = Path("/opt/dispenser-conditioning-mcp/dependencies/hicube/hicube_neo_client.py")
spec = importlib.util.spec_from_file_location("commissioned_hicube_client", path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert hasattr(module, "HiCubeNeoClient")
print("PASS: commissioned HiCube client import")
PY
```

Import the authenticated built Siglent package only from its deployed source
root and require this release's embedded build commit:

```bash
/opt/dispenser-conditioning-mcp/venv/bin/python -I -B - <<'PY'
import importlib
import sys
from pathlib import Path

root = Path("/opt/dispenser-conditioning-mcp/dependencies/py-siglent-spd3000/src").resolve()
sys.path.insert(0, str(root))
package = importlib.import_module("siglent_spd3000")
metadata = importlib.import_module("siglent_spd3000._build_commit")
assert Path(package.__file__).resolve().is_relative_to(root)
assert Path(metadata.__file__).resolve().is_relative_to(root)
assert metadata.COMMIT == "0984bba67d8e5651cd2d9aa7b2c0db2d6eb694f3"
print("PASS: commissioned Siglent package origin and build commit")
PY
```

None of these commands authorizes hardware control.
