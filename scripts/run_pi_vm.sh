#!/usr/bin/env bash
# Opens a shell in the Raspberry Pi-like container built from ../dockerfile.
#   1. builds the pi-env image if it doesn't exist yet
#   2. creates the "pi" container from it if it doesn't exist yet
#   3. attaches to it with `docker start -ai pi` (exit the shell to stop it)
#
# The container is kept between runs, so anything installed in it stays.
# To rebuild from scratch after changing the dockerfile:
#   docker rm -f pi && docker image rm pi-env && scripts/run_pi_vm.sh
#
# Usage (from the repo root): scripts/run_pi_vm.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="${PI_IMAGE:-pi-env}"
CONTAINER="${PI_CONTAINER:-pi}"

if ! docker info >/dev/null 2>&1; then
  echo "error: can't reach the Docker daemon. Start Docker Desktop and try again." >&2
  exit 1
fi

# 1. Image
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[1/3] Image $IMAGE already built."
else
  echo "[1/3] Building image $IMAGE."
  echo "+ docker build --platform linux/arm64 -t $IMAGE -f $REPO_ROOT/dockerfile $REPO_ROOT"
  docker build --platform linux/arm64 -t "$IMAGE" -f "$REPO_ROOT/dockerfile" "$REPO_ROOT"
fi

# 2. Container
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "[2/3] Container $CONTAINER already exists."
else
  echo "[2/3] Creating container $CONTAINER."
  echo "+ docker create -it --platform linux/arm64 --name $CONTAINER --hostname raspberrypi $IMAGE"
  docker create -it --platform linux/arm64 --name "$CONTAINER" --hostname raspberrypi "$IMAGE" >/dev/null
fi

# 3. Attach
echo "[3/3] Attaching to $CONTAINER (exit the shell to stop it)."
exec docker start -ai "$CONTAINER"
