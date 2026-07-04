#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/baize-server/xiaozhi-server}"
PYTHON_BIN="${PYTHON_BIN:-/opt/miniconda/envs/baize/bin/python}"
SERVICE_NAME="${SERVICE_NAME:-baize-xiaozhi}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/baize-server/backups}"
HTTP_PORT="${HTTP_PORT:-8003}"

timestamp="$(date +%F-%H%M%S)"
backup_dir="${BACKUP_ROOT}/direct-python-${timestamp}"

cd "${PROJECT_DIR}"
mkdir -p "${backup_dir}" tmp

echo "[deploy] project=${PROJECT_DIR}"
echo "[deploy] backup=${backup_dir}"

for path in \
  core/api/app_demo_store.py \
  core/api/app_demo_handler.py \
  core/api/health_handler.py \
  core/http_server.py \
  core/connection.py \
  tests/test_app_demo_handler.py \
  tests/test_device_mcp_handler.py
do
  if [[ -e "${path}" ]]; then
    mkdir -p "${backup_dir}/$(dirname "${path}")"
    cp -a "${path}" "${backup_dir}/${path}"
  fi
done

echo "[deploy] py_compile"
"${PYTHON_BIN}" -m py_compile \
  core/api/app_demo_store.py \
  core/api/app_demo_handler.py \
  core/api/health_handler.py \
  core/http_server.py \
  core/connection.py

echo "[deploy] unit tests"
"${PYTHON_BIN}" -m unittest -v tests.test_app_demo_handler tests.test_device_mcp_handler

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
  echo "[deploy] restart systemd service ${SERVICE_NAME}"
  sudo systemctl restart "${SERVICE_NAME}"
  sleep 4
  sudo systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,18p'
else
  echo "[deploy] systemd service not found, fallback to nohup"
  old_pid="$(pgrep -f 'python app.py' | head -1 || true)"
  if [[ -n "${old_pid}" ]]; then
    kill "${old_pid}"
    sleep 2
  fi
  nohup "${PYTHON_BIN}" app.py > tmp/baize-server.log 2>&1 &
  echo "$!" > tmp/baize-server.pid
  sleep 4
  pgrep -af 'python app.py'
fi

echo "[deploy] verify OTA"
curl -fsS "http://127.0.0.1:${HTTP_PORT}/xiaozhi/ota/" >/tmp/baize-ota-check.out
head -c 160 /tmp/baize-ota-check.out
echo

echo "[deploy] verify healthz"
curl -fsS "http://127.0.0.1:${HTTP_PORT}/healthz"
echo

echo "[deploy] done"
