#!/usr/bin/env bash
# Start the Lima VM, bring up vcan0, and drop into a shell in the repo.
set -euo pipefail

VM=can
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if ! limactl list -q | grep -qx "$VM"; then
  limactl start --name="$VM" --mount-writable --tty=false template:ubuntu
elif [ "$(limactl list --format '{{.Status}}' "$VM")" != "Running" ]; then
  limactl start "$VM"
fi

limactl shell "$VM" bash -c '
  set -e
  if ! command -v candump >/dev/null; then
    sudo apt-get update
    sudo apt-get install -y can-utils build-essential python3-can
    sudo modprobe vcan 2>/dev/null || sudo apt-get install -y "linux-modules-extra-$(uname -r)" || true
  fi
  if ! command -v cargo >/dev/null && [ ! -x "$HOME/.cargo/bin/cargo" ]; then
    curl https://sh.rustup.rs -sSf | sh -s -- -y
  fi
  sudo modprobe vcan
  ip link show vcan0 >/dev/null 2>&1 || sudo ip link add dev vcan0 type vcan
  sudo ip link set up vcan0
  echo "vcan0 is up"
'

exec limactl shell --workdir "$REPO_DIR" "$VM"