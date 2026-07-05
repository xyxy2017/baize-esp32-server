#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-qilei@47.96.77.235}"
PROJECT_DIR="${PROJECT_DIR:-/opt/baize-server/xiaozhi-server}"
PYTHON_BIN="${PYTHON_BIN:-/opt/miniconda/envs/baize/bin/python}"
SERVICE_NAME="${SERVICE_NAME:-baize-xiaozhi}"
HTTP_PORT="${HTTP_PORT:-8003}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${LOCAL_PROJECT_DIR}"

echo "[deploy] local=${LOCAL_PROJECT_DIR}"
echo "[deploy] remote=${REMOTE_HOST}:${PROJECT_DIR}"
git status --short

echo "[deploy] remote backup"
ssh "${REMOTE_HOST}" "
set -e
cd '${PROJECT_DIR}'
ts=\$(date +%F-%H%M%S)
backup_dir=\"backups/deploy-\${ts}\"
mkdir -p \"\${backup_dir}/core/api\" \"\${backup_dir}/core\" \"\${backup_dir}/tests\" \"\${backup_dir}/scripts\" \"\${backup_dir}/deploy/systemd\"
cp -a core/api/app_demo_store.py \"\${backup_dir}/core/api/\" 2>/dev/null || true
cp -a core/api/app_demo_handler.py \"\${backup_dir}/core/api/\" 2>/dev/null || true
cp -a core/api/health_handler.py \"\${backup_dir}/core/api/\" 2>/dev/null || true
cp -a core/api/ota_handler.py \"\${backup_dir}/core/api/\" 2>/dev/null || true
cp -a core/http_server.py \"\${backup_dir}/core/\" 2>/dev/null || true
cp -a tests/test_app_demo_handler.py \"\${backup_dir}/tests/\" 2>/dev/null || true
cp -a tests/test_device_mcp_handler.py \"\${backup_dir}/tests/\" 2>/dev/null || true
cp -a scripts/deploy_direct_python.sh \"\${backup_dir}/scripts/\" 2>/dev/null || true
cp -a deploy/systemd/baize-xiaozhi.service \"\${backup_dir}/deploy/systemd/\" 2>/dev/null || true
cp -a data/.config.yaml \"\${backup_dir}/.config.yaml\" 2>/dev/null || true
echo \"[deploy] backup=\${backup_dir}\"
"

echo "[deploy] sync core files"
scp \
  core/api/app_demo_store.py \
  core/api/app_demo_handler.py \
  core/api/health_handler.py \
  core/api/ota_handler.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/api/"

scp \
  core/http_server.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/"

scp \
  tests/test_app_demo_handler.py \
  tests/test_device_mcp_handler.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/tests/"

scp \
  scripts/deploy_direct_python.sh \
  "${REMOTE_HOST}:${PROJECT_DIR}/scripts/"

scp \
  deploy/systemd/baize-xiaozhi.service \
  "${REMOTE_HOST}:${PROJECT_DIR}/deploy/systemd/"

echo "[deploy] remote checks"
ssh "${REMOTE_HOST}" "
set -e
cd '${PROJECT_DIR}'
'${PYTHON_BIN}' -m py_compile \
  core/api/app_demo_store.py \
  core/api/app_demo_handler.py \
  core/api/health_handler.py \
  core/api/ota_handler.py \
  core/http_server.py \
  core/providers/tools/device_mcp/mcp_handler.py

'${PYTHON_BIN}' -m unittest -v \
  tests.test_app_demo_handler \
  tests.test_device_mcp_handler
"

echo "[deploy] restart service"
ssh "${REMOTE_HOST}" "
set -e
sudo systemctl restart '${SERVICE_NAME}'
sleep 5
sudo systemctl status '${SERVICE_NAME}' --no-pager | sed -n '1,24p'
"

echo "[deploy] verify"
ssh "${REMOTE_HOST}" "
set -e
curl -s -o /dev/null -w 'ota=%{http_code}\n' 'http://127.0.0.1:${HTTP_PORT}/xiaozhi/ota/'
curl -fsS 'http://127.0.0.1:${HTTP_PORT}/healthz'
echo
systemctl is-active '${SERVICE_NAME}'
systemctl is-enabled '${SERVICE_NAME}'
"

echo "[deploy] done"
