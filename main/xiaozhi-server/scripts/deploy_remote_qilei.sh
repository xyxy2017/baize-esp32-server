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
mkdir -p \"\${backup_dir}/core/api\" \"\${backup_dir}/core/handle/textHandler\" \"\${backup_dir}/core/providers/llm/openai\" \"\${backup_dir}/core/providers/tools/device_mcp\" \"\${backup_dir}/core/utils\" \"\${backup_dir}/core\" \"\${backup_dir}/tests\" \"\${backup_dir}/scripts\" \"\${backup_dir}/deploy/systemd\" \"\${backup_dir}/deploy/monitoring\"
cp -a app.py config.yaml \"\${backup_dir}/\" 2>/dev/null || true
cp -a core/api/app_demo_store.py \"\${backup_dir}/core/api/\" 2>/dev/null || true
cp -a core/api/app_demo_handler.py \"\${backup_dir}/core/api/\" 2>/dev/null || true
cp -a core/api/health_handler.py \"\${backup_dir}/core/api/\" 2>/dev/null || true
cp -a core/api/ota_handler.py \"\${backup_dir}/core/api/\" 2>/dev/null || true
cp -a core/api/vision_handler.py \"\${backup_dir}/core/api/\" 2>/dev/null || true
cp -a core/http_server.py \"\${backup_dir}/core/\" 2>/dev/null || true
cp -a core/device_registry.py \"\${backup_dir}/core/\" 2>/dev/null || true
cp -a core/content_safety.py core/memory_embedding.py core/memory_worker.py core/telemetry.py core/connection.py \"\${backup_dir}/core/\" 2>/dev/null || true
cp -a core/handle/helloHandle.py core/handle/intentHandler.py core/handle/receiveAudioHandle.py core/handle/reportHandle.py core/handle/sendAudioHandle.py \"\${backup_dir}/core/handle/\" 2>/dev/null || true
cp -a core/handle/textHandler/listenMessageHandler.py \"\${backup_dir}/core/handle/textHandler/\" 2>/dev/null || true
cp -a core/utils/conversation_metrics.py core/utils/dialogue.py core/utils/modules_initialize.py core/utils/prompt_manager.py core/utils/textUtils.py \"\${backup_dir}/core/utils/\" 2>/dev/null || true
cp -a core/providers/llm/openai/openai.py \"\${backup_dir}/core/providers/llm/openai/\" 2>/dev/null || true
cp -a core/providers/tools/device_mcp/mcp_handler.py \"\${backup_dir}/core/providers/tools/device_mcp/\" 2>/dev/null || true
cp -a tests/test_app_demo_handler.py \"\${backup_dir}/tests/\" 2>/dev/null || true
cp -a tests/test_device_mcp_handler.py \"\${backup_dir}/tests/\" 2>/dev/null || true
cp -a tests/test_content_safety.py tests/test_memory_v2.py tests/test_memory_embedding.py tests/test_telemetry.py \"\${backup_dir}/tests/\" 2>/dev/null || true
cp -a scripts/deploy_direct_python.sh \"\${backup_dir}/scripts/\" 2>/dev/null || true
cp -a deploy/systemd/baize-xiaozhi.service \"\${backup_dir}/deploy/systemd/\" 2>/dev/null || true
cp -a deploy/monitoring/baize-alerts.yml deploy/monitoring/baize-overview.json \"\${backup_dir}/deploy/monitoring/\" 2>/dev/null || true
cp -a data/.config.yaml \"\${backup_dir}/.config.yaml\" 2>/dev/null || true
echo \"[deploy] backup=\${backup_dir}\"
"

echo "[deploy] sync core files"
ssh "${REMOTE_HOST}" "
set -e
cd '${PROJECT_DIR}'
latest_backup=\$(ls -dt backups/deploy-* | head -1)
mkdir -p \"\${latest_backup}/core/providers/tts\"
cp -a core/providers/tts/alibl_stream.py core/providers/tts/alibl_tts_v2.py \"\${latest_backup}/core/providers/tts/\" 2>/dev/null || true
cp -a tests/test_model_configuration.py \"\${latest_backup}/tests/\" 2>/dev/null || true
cp -a scripts/update_model_config.py scripts/smoke_models.py \"\${latest_backup}/scripts/\" 2>/dev/null || true
"
scp \
  core/api/app_demo_store.py \
  core/api/app_demo_handler.py \
  core/api/health_handler.py \
  core/api/ota_handler.py \
  core/api/vision_handler.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/api/"

scp \
  core/connection.py \
  core/content_safety.py \
  core/http_server.py \
  core/device_registry.py \
  core/telemetry.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/"

scp \
  core/utils/conversation_metrics.py \
  core/utils/dialogue.py \
  core/utils/modules_initialize.py \
  core/utils/prompt_manager.py \
  core/utils/textUtils.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/utils/"

scp \
  core/handle/helloHandle.py \
  core/handle/intentHandler.py \
  core/handle/receiveAudioHandle.py \
  core/handle/reportHandle.py \
  core/handle/sendAudioHandle.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/handle/"

scp \
  core/handle/textHandler/listenMessageHandler.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/handle/textHandler/"

scp \
  app.py \
  config.yaml \
  "${REMOTE_HOST}:${PROJECT_DIR}/"

scp \
  core/providers/llm/openai/openai.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/providers/llm/openai/"

scp \
  core/providers/tts/alibl_stream.py \
  core/providers/tts/alibl_tts_v2.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/providers/tts/"

scp \
  core/providers/asr/base.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/providers/asr/"

scp \
  core/providers/tools/device_mcp/mcp_handler.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/core/providers/tools/device_mcp/"

scp \
  tests/test_app_demo_handler.py \
  tests/test_content_safety.py \
  tests/test_device_mcp_handler.py \
  tests/test_model_configuration.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/tests/"

scp \
  scripts/deploy_direct_python.sh \
  scripts/update_model_config.py \
  scripts/smoke_models.py \
  "${REMOTE_HOST}:${PROJECT_DIR}/scripts/"

scp \
  deploy/systemd/baize-xiaozhi.service \
  "${REMOTE_HOST}:${PROJECT_DIR}/deploy/systemd/"

scp \
  deploy/monitoring/baize-alerts.yml \
  deploy/monitoring/baize-overview.json \
  "${REMOTE_HOST}:${PROJECT_DIR}/deploy/monitoring/"

echo "[deploy] remote checks"
ssh "${REMOTE_HOST}" "
set -e
cd '${PROJECT_DIR}'
'${PYTHON_BIN}' -m py_compile \
  core/api/app_demo_store.py \
  core/api/app_demo_handler.py \
  core/api/health_handler.py \
  core/api/ota_handler.py \
  core/api/vision_handler.py \
  core/http_server.py \
  core/connection.py \
  core/telemetry.py \
  core/providers/asr/base.py \
  core/device_registry.py \
  core/content_safety.py \
  core/handle/helloHandle.py \
  core/handle/intentHandler.py \
  core/handle/receiveAudioHandle.py \
  core/handle/reportHandle.py \
  core/handle/sendAudioHandle.py \
  core/handle/textHandler/listenMessageHandler.py \
  core/memory_embedding.py \
  core/memory_worker.py \
  core/utils/conversation_metrics.py \
  core/utils/dialogue.py \
  core/utils/modules_initialize.py \
  core/utils/prompt_manager.py \
  core/utils/textUtils.py \
  core/providers/llm/openai/openai.py \
  core/providers/tts/alibl_stream.py \
  core/providers/tts/alibl_tts_v2.py \
  core/providers/tools/device_mcp/mcp_handler.py \
  scripts/update_model_config.py \
  scripts/smoke_models.py

'${PYTHON_BIN}' -m unittest -v \
  tests.test_app_demo_handler \
  tests.test_content_safety \
  tests.test_device_mcp_handler \
  tests.test_model_configuration \
  tests.test_memory_v2 \
  tests.test_memory_embedding \
  tests.test_telemetry

candidate_config=\$(mktemp)
trap 'rm -f "\${candidate_config}"' EXIT
'${PYTHON_BIN}' scripts/update_model_config.py \
  --source data/.config.yaml \
  --output "\${candidate_config}"
'${PYTHON_BIN}' scripts/smoke_models.py --config "\${candidate_config}"
'${PYTHON_BIN}' scripts/update_model_config.py \
  --source "\${candidate_config}" \
  --output data/.config.yaml
rm -f "\${candidate_config}"
trap - EXIT

if systemctl list-unit-files | grep -q '^grafana-server.service' && [[ -d /var/lib/grafana/dashboards ]]; then
  sudo install -m 0644 deploy/monitoring/baize-overview.json /var/lib/grafana/dashboards/baize-overview.json
  sudo systemctl restart grafana-server
fi
if systemctl list-unit-files | grep -q '^prometheus.service' && [[ -d /etc/prometheus ]]; then
  sudo install -m 0644 deploy/monitoring/baize-alerts.yml /etc/prometheus/baize-alerts.yml
  command -v promtool >/dev/null 2>&1 && sudo promtool check rules /etc/prometheus/baize-alerts.yml
  sudo systemctl reload prometheus || sudo systemctl restart prometheus
fi
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
