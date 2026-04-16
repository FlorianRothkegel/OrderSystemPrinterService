#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DOCKER_BIN="${DOCKER_BIN:-docker}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/arm/v7}"
DOCKER_IMAGE="${DOCKER_IMAGE:-arm32v7/python:3.12-bookworm}"
HOST_UID="${HOST_UID:-$(id -u)}"
HOST_GID="${HOST_GID:-$(id -g)}"
CONTAINER_NAME="${CONTAINER_NAME:-ordersystem-printer-arm32-gui-$$}"

if ! command -v "$DOCKER_BIN" >/dev/null 2>&1; then
  echo "Docker is required for this build wrapper."
  exit 1
fi

echo "Running 32-bit ARM GUI build inside $DOCKER_IMAGE for platform $DOCKER_PLATFORM"
cleanup() {
  "$DOCKER_BIN" rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$DOCKER_BIN" create \
  --name "$CONTAINER_NAME" \
  --platform "$DOCKER_PLATFORM" \
  --user "${HOST_UID}:${HOST_GID}" \
  -e HOME=/tmp/printer-build-home \
  -w /work \
  "$DOCKER_IMAGE" \
  bash printerService/build-linux-arm32.sh >/dev/null

"$DOCKER_BIN" cp "$ROOT_DIR/." "$CONTAINER_NAME:/work"
"$DOCKER_BIN" start -a "$CONTAINER_NAME"

mkdir -p "$ROOT_DIR/dist"
"$DOCKER_BIN" cp "$CONTAINER_NAME:/work/dist/OrderSystemPrinterService" "$ROOT_DIR/dist/OrderSystemPrinterService"
