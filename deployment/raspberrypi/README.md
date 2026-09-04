# Future Hardened Raspberry Pi MCP Deployment

> **Status:** Future deployment reference, not the current research workflow.
> For current Pi development use
> [QUICK_COMMISSIONING.md](QUICK_COMMISSIONING.md): update the pinned Git
> submodule, run `uv sync`, configure, and start control-disabled.

This is the primary deployment path for `dispenser-conditioning-mcp` 0.5.1.
It preserves the public six-tool MCP contract at v0.4.3. The target is a
dedicated Raspberry Pi running Raspberry Pi OS Trixie 64-bit, `aarch64`, glibc,
systemd, and CPython 3.13. Bookworm, 32-bit images, x86, and a different Python
minor version require a new audited release.

The fastest acceptable path is a root-owned offline install, two locked
non-login service identities, loopback Streamable HTTP, and an operator-owned
restricted SSH local forward. Both service units ship control-disabled, use
`Restart=no`, contain no `[Install]` section, and must be started manually.
Nothing here contacts hardware during installation.

## Required operator inputs

Obtain outside agent access and authenticate through the site release channel:

- exact Pi model, Trixie image/version, kernel, boot medium, and power design;
- the audited 35-wheel dependency kit with tree SHA-256
  `f1aee8f345b042da12f0aa2247080783ff6334641167a2704ecb89ec0ea9a03a`;
- final 0.5.1 MCP wheel after the POSIX interlock overlay is applied;
- the verified official `uv` 0.11.7 aarch64 archive and retained provenance set;
- one exact release bundle assembled with `pi_release_manifest.py`, and the
  release-manifest SHA-256 delivered through an independent operator channel;
- exact system-Python runtime-manifest hash produced below;
- literal IPv4 addresses for the HiCube source and Siglent gateway;
- the authenticated built Siglent package extracted from the reviewed
  `py_siglent_spd3000-0.1.0-py3-none-any.whl`, including its generated
  `_build_commit.py` for commit `0984bba67d8e5651cd2d9aa7b2c0db2d6eb694f3`;
- distinct HIL/production PSU identity/resource pairs, profiles, auth files,
  ports, and responsible operators; and
- bridge source address, SSH public key, firewall policy, UPS/storage plan, and
  physical output/reset authority.

Never place endpoint values, keys, tokens, auth-file contents, state contents,
or reset access in an agent-readable directory or prompt.

The exact release bundle has only `artifacts`, `candidate`, `dependencies`,
`deployment`, and `tools` at its root. The manifest enumerates every file and
binds the MCP wheel, 35-wheel candidate, HiCube client, Siglent driver tree,
deployment files, runtime inventory/tool, and the complete retained uv
provenance set. Any extra, missing, symlinked, or changed file is rejected. The
operator must compare `release-manifest.json` to the single independently
approved digest before executing any bundled Python or shell tool. The bootstrap
manifest verifier and installer must themselves be checked against their entries
in that authenticated manifest using an independently reviewed standard-library
procedure; a digest stored only beside the bundle is not authentication.

The wheel-root `siglent_spd3000` package is extracted into the exact
`dependencies/py-siglent-spd3000/src/siglent_spd3000` release path. It is taken
from the commissioned built wheel, not copied from a Git checkout's raw `src`
directory. The offline
startup preflight imports its generated `_build_commit` module and requires a 40–64 digit
lowercase hexadecimal build commit. `unknown`, missing metadata, a wrong import
origin, or a source-only tree blocks startup before device contact.

On the Pi, use this exact bootstrap before executing a bundled tool (replace
only the two values and keep the one-line Python program operator-visible):

```sh
export DCP_RELEASE=/operator-staging/release-bundle
export DCP_RELEASE_SHA256='<independently-approved-release-manifest-sha256>'
printf '%s  %s\n' "$DCP_RELEASE_SHA256" "$DCP_RELEASE/release-manifest.json" \
  | /usr/bin/sha256sum --check --strict -
/usr/bin/python3.13 -I -B -c 'import hashlib,json,pathlib,sys; r=pathlib.Path(sys.argv[1]); m=json.loads((r/"release-manifest.json").read_text()); e=next(x for x in m["trees"]["release_bundle"]["files"] if x["path"]=="deployment/pi_release_manifest.py"); p=r/e["path"]; assert p.is_file() and not p.is_symlink() and p.stat().st_size==e["size"] and hashlib.sha256(p.read_bytes()).hexdigest()==e["sha256"]' "$DCP_RELEASE"
/usr/bin/python3.13 -I -B "$DCP_RELEASE/deployment/pi_release_manifest.py" verify \
  --bundle-root "$DCP_RELEASE" \
  --manifest "$DCP_RELEASE/release-manifest.json" \
  --expected-manifest-sha256 "$DCP_RELEASE_SHA256"
```

The first command authenticates the manifest from the out-of-band digest; the
second authenticates the full verifier bytes from that manifest; the third
reconstructs and compares every release file before uv or another bundled tool
is run.

## 1. Establish and record the host

Use a fresh Trixie 64-bit image. Prefer a UPS and high-endurance local SSD/NVMe
with ext4 for `/var/lib`; this deployment validator requires local ext4 for the
durable HIL record. Do not use NFS, CIFS, FUSE, overlay, tmpfs, or an SD card
whose power-loss behavior has not been accepted by the operator.

```sh
test "$(uname -m)" = aarch64
test "$(dpkg --print-architecture)" = arm64
/usr/bin/python3.13 -c 'import platform,sys; assert sys.version_info[:2] == (3,13); assert platform.machine() == "aarch64"'
getconf GNU_LIBC_VERSION
findmnt -no FSTYPE,SOURCE,TARGET /var/lib
cat /etc/os-release
cat /proc/device-tree/model
```

Record and verify the exact Debian package versions for `python3.13`,
`python3.13-minimal`, `python3.13-venv`, `libpython3.13-minimal`, and
`libpython3.13-stdlib`, plus the resolved interpreter
hash/version/architecture. The optional `libpython3.13` package is not required:

```sh
sudo /usr/bin/python3.13 -I -B "$DCP_RELEASE/tools/python_runtime_provenance.py" create \
  --output /root/dispenser-python-runtime.json
sha256sum /root/dispenser-python-runtime.json
sudo /usr/bin/python3.13 -I -B "$DCP_RELEASE/tools/python_runtime_provenance.py" verify \
  --manifest /root/dispenser-python-runtime.json \
  --expected-manifest-sha256 '<independently-approved-sha256>'
```

The record co-records package identity and interpreter bytes; it does not prove
the trustworthiness of the OS signing/release channel. Any OS/Python package
update invalidates this commissioned record and requires a new review before
the MCP starts again.

## 2. Audit the dependency kit natively

Run [ON_PI_VALIDATION.md](ON_PI_VALIDATION.md) from the dependency kit root.
Its corrected first checksum command is:

```sh
(cd "$DCP_DEP_KIT" && sha256sum --check SHA256SUMS)
```

The kit is 35 dependency wheels only. It intentionally excludes `pywin32` and
the MCP wheel. A Windows cross-resolver result is not Linux dependency evidence.
Native import, `readelf`, and `ldd` checks remain blocking.

## 3. Initialize the protected host boundary

Review and hash every script first. Run only the exact approved initializer as
root. It refuses pre-existing product roots and identities.

```sh
sudo sh ./initialize_layout.sh
sudo /usr/bin/python3.13 -I -B ./validate_layout.py
```

The resulting policy is:

- `/opt/dispenser-conditioning-mcp`: `root:root`, no service writes;
- `/etc/dispenser-conditioning-mcp/unloaded-hil`: `root:dispenser-hil`, mode
  `0750`, with root-owned `0640` profile/auth files;
- `/etc/dispenser-conditioning-mcp/production`: `root:dispenser-prod`, same
  policy but no HIL state;
- `/var/lib/dispenser-conditioning-mcp/unloaded-hil`:
  `dispenser-hil:dispenser-hil`, mode `0700`; and
- journald only for logs; do not grant filesystem log writes.

Both service users are locked, non-login, distinct, and have no supplementary
groups. The agent is not either account and receives no sudo, service manager,
file, auth, state, or reset access.

## 4. Install the verified uv runtime and exact offline Python payload

After authenticating the release manifest, run its exact uv installer against
its exact archive. It installs root-owned uv 0.11.7 and verifies archive/member
hashes, ELF64 AArch64, glibc compatibility, dynamic linking, and version:

```sh
sudo /bin/bash "$DCP_RELEASE/tools/uv/verify-and-install-uv.sh" \
  "$DCP_RELEASE/tools/uv/uv-aarch64-unknown-linux-gnu.tar.gz"
```

The payload installer authenticates the one release manifest and reconstructs
the entire bundle before touching the empty venv. It then revalidates the
candidate, installed uv, runtime identity, wheel internals, and uses uv only
with offline/index-disabled/no-cache settings. It scrubs inherited `PIP_*`,
`UV_*`, and `PYTHON*`, runs `uv pip check`, and validates the exact
36-distribution inventory (35 dependencies plus MCP):

```sh
sudo /usr/bin/python3.13 -I -B "$DCP_RELEASE/deployment/install_payload.py" \
  --release-bundle "$DCP_RELEASE" \
  --release-manifest "$DCP_RELEASE/release-manifest.json" \
  --expected-release-manifest-sha256 '<independently-approved-release-manifest-sha256>' \
  --runtime-manifest /root/dispenser-python-runtime.json \
  --expected-runtime-manifest-sha256 '<approved-runtime-manifest-sha256>' \
  --venv /opt/dispenser-conditioning-mcp/venv
```

A failed/interrupted install leaves both the application tree and venv
unapproved. Decommission the fresh product root and start again; never repair it
in place. The authenticated installer itself materializes, fsyncs, and
byte-verifies the runtime records/tools, inventory, HiCube client, and exact
built Siglent package into their fixed `/opt` paths before creating the venv.
Every target must be fresh and empty; there is no manual source-copy step. The
actual-Pi runtime record becomes `app/python-runtime-manifest.json`; write its
approved digest into both protected profiles. Then rerun `validate_layout.py`.
Symlinks, foreign ownership, group/other writes, missing files, or byte drift are
rejected.

## 5. Install protected profiles and systemd policy

Copy each environment template into its matching `/etc` directory as
`profile.env`, fill it as root, and install its gateway auth file. Use exact
`root:<service>` ownership and mode `0640`. Control remains `false`.
The HIL compliance value `1.0 V` is only an initial unloaded-test candidate and
must be reviewed by the operator before replacing its template placeholder.
The expected model and serial are also explicit verified deployment inputs;
templates do not pre-approve a model.

Copy the two unit files to `/etc/systemd/system` as root with mode `0644`.
Create a separate `10-device-network.conf` in each unit's `.d` directory from
`device-network.conf.template`. Set the first allow to the profile's literal
HiCube IPv4 `/32` and the second to the host part of its literal Siglent gateway
identifier `/32`. Hostnames, ranges, placeholders, and mismatches are rejected.

```sh
sudo /usr/bin/python3.13 -I -B ./validate_network_policy.py \
  --unit-file /etc/systemd/system/dispenser-conditioning-mcp-hil.service \
  --device-network-dropin /etc/systemd/system/dispenser-conditioning-mcp-hil.service.d/10-device-network.conf \
  --profile /etc/dispenser-conditioning-mcp/unloaded-hil/profile.env \
  --profile-group dispenser-hil

sudo /usr/bin/python3.13 -I -B ./validate_instance_separation.py \
  --hil-profile /etc/dispenser-conditioning-mcp/unloaded-hil/profile.env \
  --production-profile /etc/dispenser-conditioning-mcp/production/profile.env \
  --hil-unit /etc/systemd/system/dispenser-conditioning-mcp-hil.service \
  --production-unit /etc/systemd/system/dispenser-conditioning-mcp-production.service

sudo systemd-analyze verify \
  /etc/systemd/system/dispenser-conditioning-mcp-hil.service \
  /etc/systemd/system/dispenser-conditioning-mcp-production.service
sudo systemctl daemon-reload
systemctl is-enabled dispenser-conditioning-mcp-hil.service || true
systemctl is-enabled dispenser-conditioning-mcp-production.service || true
```

Both `is-enabled` results must be `static`; do not add an install section or enable
either unit. Verify `systemctl show` reports `Restart=no`, the expected distinct
users, `IPAddressDeny=any`, loopback plus exactly two device allows, and no
unexpected drop-ins. If systemd cannot enforce IP address filtering on this
kernel, commissioning is NO-GO until an equivalent operator firewall boundary
is independently reviewed.

The three `ExecStartPre` commands revalidate the commissioned Debian interpreter
record, exact installed distribution inventory, and local
profile/instrument-driver/auth readability through isolated Python.
`deployment_check` reads zero auth bytes and constructs no device session; it
does not contact hardware.

## 6. Operator-initialize HIL state

Stop both units and physically verify both PSU outputs off before this step.
The service cannot run the root-required initializer. It uses atomic
`O_CREAT|O_EXCL`, never overwrites an existing record, fsyncs the file and its
parent directory, and leaves a crash-partial record fail-closed.

```sh
sudo systemctl stop dispenser-conditioning-mcp-hil.service \
  dispenser-conditioning-mcp-production.service
sudo /usr/bin/python3.13 -I -B ./initialize_unloaded_hil_state.py \
  --state-file /var/lib/dispenser-conditioning-mcp/unloaded-hil/operation-state.json \
  --service-user dispenser-hil \
  --physical-verification confirmed_outputs_off_and_no_unapproved_load
sudo /usr/bin/python3.13 -I -B ./validate_layout.py --require-state
```

Missing, deleted, partial, malformed, pending, or trip state remains fail-closed
before device-session creation. Reset/replacement remains an out-of-band human
operation after physical verification; no MCP tool can create, clear, reset,
bypass, or select state.

## 7. Restricted SSH local forward

Install the exact root-owned `ssh/sshd_config.template` as mode `0600` and one
filled public-key template per distinct bridge account under the protected
config `ssh` directory as root-owned mode `0600`. Run `validate_ssh_bridge.py`,
then `/usr/sbin/sshd -t` and both effective-policy checks below before reloading
sshd:

```sh
sudo /usr/bin/python3.13 -I -B ./validate_ssh_bridge.py \
  --sshd-config /etc/ssh/sshd_config.d/70-dispenser-conditioning-mcp.conf \
  --hil-authorized-key /etc/dispenser-conditioning-mcp/ssh/mcp-bridge-hil \
  --production-authorized-key /etc/dispenser-conditioning-mcp/ssh/mcp-bridge-prod
sudo /usr/sbin/sshd -t
sudo /usr/sbin/sshd -T -C user=mcp-bridge-hil,host=localhost,addr=127.0.0.1
sudo /usr/sbin/sshd -T -C user=mcp-bridge-prod,host=localhost,addr=127.0.0.1
```

The effective outputs must show the matching single `permitopen`, local-only
forwarding, `maxsessions 0`, and the per-user authorized-key path; an ordinary
operator account must retain its original key path. The policy limits each account to local
forwarding to exactly one loopback MCP port, sets `MaxSessions 0`, and disables
password, keyboard-interactive, TTY, agent, X11, tunnel, and gateway forwarding.
Test that command, shell, and SFTP sessions fail while the matching `ssh -N -T
-L 127.0.0.1:8001:127.0.0.1:8001` succeeds. Restrict TCP/22 at both host and
network firewalls to the exact operator bridge source and prove a different
source cannot connect. Until these checks pass, remote MCP commissioning is
NO-GO even though local control-disabled startup may be tested.

Rollback is: stop both MCP units, remove the two bridge public keys and the
managed sshd drop-in, validate the base sshd configuration, reload sshd, remove
the two locked bridge accounts, and confirm TCP/22 is no longer reachable from
the bridge source. Never remove the active operator's independent recovery
access before base-configuration validation.

The MCP HTTP socket remains loopback-only. The tunnel's local/backend port must
match the protected profile because SSH preserves the Host header. The agent
receives only `http://127.0.0.1:<port>/mcp`, never the SSH key or host access.

## 8. Update/reboot stability and commissioning

Audit timers and reboot policy before every conditioning session:

```sh
systemctl list-timers --all 'apt-*' 'unattended-*'
systemctl status unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer
apt-config dump | grep -E 'APT::Periodic|Unattended-Upgrade::Automatic-Reboot'
grep -R --line-number -E 'Automatic-Reboot|APT::Periodic' /etc/apt/apt.conf.d
```

The site must schedule security maintenance outside experiments and prove no
automatic/scheduled reboot can occur during a conditioning session. For a
supervised run, an operator may hold a shutdown/sleep inhibitor in a dedicated
root-controlled terminal, start the unit manually inside it, supervise the
session, stop the unit, and only then exit:

```sh
sudo systemd-inhibit --what=shutdown:sleep --mode=block \
  --who='dispenser-conditioning-operator' \
  --why='supervised dispenser conditioning; stop MCP before release' /bin/bash
```

An inhibitor is not a watchdog or E-stop and cannot prevent power loss, kernel
failure, forced reboot, or PSU independence. Loss of the Pi/OS **does not turn
the PSU output off**. UPS, reliable local ext4 storage, physical output
verification, and an independent hardware interlock remain required for
unattended use.

Commission in this order, stopping on any mismatch:

1. offline layout/runtime/inventory/network/profile checks;
2. control-disabled manual unit start and strict six-tool discovery;
3. pressure read and power-state read only;
4. reboot/crash exercise proving neither unit auto-starts and no workflow
   resumes;
5. physical output verification and durable-state review;
6. freshly approved unloaded-HIL actuation; and
7. separately reviewed production commissioning.

After any unexpected exit, reboot, power loss, fsync error, or uncertain write,
do not restart or resume automatically. Verify PSU output physically, review
journald and durable state out of band, perform the authorized reset if needed,
and start exactly one unit manually. Software shutdown is not a physical E-stop.
