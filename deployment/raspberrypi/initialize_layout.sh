#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Layout initialization requires root." >&2
    exit 1
fi

app_root=/opt/dispenser-conditioning-mcp
config_root=/etc/dispenser-conditioning-mcp
state_root=/var/lib/dispenser-conditioning-mcp

for path in "$app_root" "$config_root" "$state_root"; do
    if [ -e "$path" ] || [ -L "$path" ]; then
        echo "Deployment path already exists; use a fresh reviewed root." >&2
        exit 1
    fi
done

for account in dispenser-hil dispenser-prod mcp-bridge-hil mcp-bridge-prod; do
    if getent passwd "$account" >/dev/null; then
        echo "Dedicated service identity already exists; review it manually." >&2
        exit 1
    fi
done

useradd --system --user-group --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin dispenser-hil
useradd --system --user-group --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin dispenser-prod
useradd --system --user-group --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin mcp-bridge-hil
useradd --system --user-group --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin mcp-bridge-prod
usermod --lock dispenser-hil
usermod --lock dispenser-prod
usermod --lock mcp-bridge-hil
usermod --lock mcp-bridge-prod

install -d -o root -g root -m 0755 "$app_root"
install -d -o root -g root -m 0755 \
    "$app_root/app" \
    "$app_root/dependencies" \
    "$app_root/dependencies/hicube" \
    "$app_root/dependencies/py-siglent-spd3000" \
    "$app_root/venv"

install -d -o root -g root -m 0751 "$config_root"
install -d -o root -g dispenser-hil -m 0750 "$config_root/unloaded-hil"
install -d -o root -g dispenser-prod -m 0750 "$config_root/production"
install -d -o root -g root -m 0700 "$config_root/ssh"

install -d -o root -g root -m 0711 "$state_root"
install -d -o dispenser-hil -g dispenser-hil -m 0700 \
    "$state_root/unloaded-hil"

echo "Raspberry Pi MCP protected layout initialization passed."
