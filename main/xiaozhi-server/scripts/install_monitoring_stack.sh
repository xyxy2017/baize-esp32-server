#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${DOMAIN:-burningpowercat.com}"
PROJECT_DIR="${PROJECT_DIR:-/opt/baize-server/xiaozhi-server}"
PYTHON_BIN="${PYTHON_BIN:-/opt/miniconda/envs/baize/bin/python}"
VHOST="${VHOST:-/etc/nginx/sites-available/burningpowercat.com}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups/monitoring-$(date +%F-%H%M%S)}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo DOMAIN=${DOMAIN} bash scripts/install_monitoring_stack.sh" >&2
  exit 1
fi
if [[ ! -f "${PROJECT_DIR}/deploy/monitoring/prometheus.yml" ]]; then
  echo "Project files are missing in ${PROJECT_DIR}; synchronize the repository first." >&2
  exit 1
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python runtime not found: ${PYTHON_BIN}" >&2
  exit 1
fi
if [[ ! -f "${VHOST}" ]]; then
  echo "Nginx vhost not found: ${VHOST}" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
cp -a "${VHOST}" "${BACKUP_DIR}/burningpowercat.com"
[[ -f /etc/prometheus/prometheus.yml ]] && cp -a /etc/prometheus/prometheus.yml "${BACKUP_DIR}/prometheus.yml" || true
[[ -f /etc/grafana/grafana.ini ]] && cp -a /etc/grafana/grafana.ini "${BACKUP_DIR}/grafana.ini" || true

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates certbot curl prometheus prometheus-alertmanager prometheus-blackbox-exporter prometheus-node-exporter
"${PYTHON_BIN}" -m pip install --no-deps 'prometheus-client==0.23.1'

if ! command -v grafana-server >/dev/null 2>&1; then
  grafana_deb="/tmp/grafana_11.2.0_amd64.deb"
  curl -fL --retry 3 -o "${grafana_deb}" https://dl.grafana.com/oss/release/grafana_11.2.0_amd64.deb
  apt-get install -y "${grafana_deb}"
fi

install -d -m 0755 /etc/nginx/snippets /etc/prometheus/rules /etc/systemd/system/prometheus.service.d /etc/systemd/system/prometheus-alertmanager.service.d /etc/systemd/system/prometheus-node-exporter.service.d /etc/systemd/system/prometheus-blackbox-exporter.service.d /etc/systemd/system/grafana-server.service.d /etc/baize /var/log/baize
install -m 0644 "${PROJECT_DIR}/deploy/nginx/baize-log-format.conf" /etc/nginx/conf.d/baize-log-format.conf
install -m 0644 "${PROJECT_DIR}/deploy/nginx/baize-xiaozhi.locations.conf" /etc/nginx/snippets/baize-xiaozhi.locations.conf
install -m 0644 "${PROJECT_DIR}/deploy/monitoring/prometheus.yml" /etc/prometheus/prometheus.yml
install -m 0644 "${PROJECT_DIR}/deploy/monitoring/blackbox.yml" /etc/prometheus/blackbox.yml
install -m 0644 "${PROJECT_DIR}/deploy/monitoring/baize-alerts.yml" /etc/prometheus/rules/baize-alerts.yml
if [[ ! -f /etc/prometheus/alertmanager.yml ]]; then
  install -m 0640 "${PROJECT_DIR}/deploy/monitoring/alertmanager.yml.example" /etc/prometheus/alertmanager.yml
fi
install -m 0644 "${PROJECT_DIR}/deploy/monitoring/grafana.ini" /etc/grafana/grafana.ini
install -m 0644 "${PROJECT_DIR}/deploy/monitoring/logrotate-baize-nginx" /etc/logrotate.d/baize-nginx
install -m 0644 "${PROJECT_DIR}/deploy/systemd/baize-xiaozhi.service" /etc/systemd/system/baize-xiaozhi.service
chown -R prometheus:prometheus /etc/prometheus/rules /etc/prometheus/blackbox.yml
chown prometheus:prometheus /etc/prometheus/alertmanager.yml
chown -R grafana:grafana /var/lib/grafana
chown www-data:adm /var/log/baize

install -d -m 0755 /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards /var/lib/grafana/dashboards
install -m 0644 "${PROJECT_DIR}/deploy/monitoring/grafana-datasource.yml" /etc/grafana/provisioning/datasources/baize.yml
install -m 0644 "${PROJECT_DIR}/deploy/monitoring/grafana-dashboard-provider.yml" /etc/grafana/provisioning/dashboards/baize.yml
install -m 0644 "${PROJECT_DIR}/deploy/monitoring/baize-overview.json" /var/lib/grafana/dashboards/baize-overview.json
chown -R grafana:grafana /etc/grafana/provisioning /var/lib/grafana/dashboards

if [[ ! -f /etc/baize/grafana.env ]]; then
  umask 077
  printf 'GF_SECURITY_ADMIN_PASSWORD=%s\n' "$(openssl rand -base64 32 | tr -d '\n')" > /etc/baize/grafana.env
fi
chmod 0600 /etc/baize/grafana.env

cat > /etc/systemd/system/prometheus.service.d/baize.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus --storage.tsdb.retention.time=7d --storage.tsdb.retention.size=512MB --web.listen-address=127.0.0.1:9090
EOF
cat > /etc/systemd/system/prometheus-node-exporter.service.d/baize.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/prometheus-node-exporter --web.listen-address=127.0.0.1:9100 --collector.systemd
EOF
cat > /etc/systemd/system/prometheus-alertmanager.service.d/baize.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/prometheus-alertmanager --config.file=/etc/prometheus/alertmanager.yml --storage.path=/var/lib/prometheus/alertmanager --web.listen-address=127.0.0.1:9093
EOF
cat > /etc/systemd/system/prometheus-blackbox-exporter.service.d/baize.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/prometheus-blackbox-exporter --config.file=/etc/prometheus/blackbox.yml --web.listen-address=127.0.0.1:9115
EOF
cat > /etc/systemd/system/grafana-server.service.d/baize.conf <<'EOF'
[Service]
EnvironmentFile=/etc/baize/grafana.env
EOF

if [[ ! -d "/etc/letsencrypt/live/${DOMAIN}" ]]; then
  CERTBOT_ARGS=(certonly --webroot -w /var/www/html -d "${DOMAIN}" --non-interactive --agree-tos)
  if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
    CERTBOT_ARGS+=(--email "${CERTBOT_EMAIL}")
  else
    CERTBOT_ARGS+=(--register-unsafely-without-email)
  fi
  certbot "${CERTBOT_ARGS[@]}"
fi

DOMAIN="${DOMAIN}" VHOST="${VHOST}" python3 - <<'PY'
import os
from pathlib import Path

domain = os.environ["DOMAIN"]
path = Path(os.environ["VHOST"])
content = path.read_text(encoding="utf-8")
include = "    include /etc/nginx/snippets/baize-xiaozhi.locations.conf;\n"
if include.strip() not in content:
    tls_listen = content.find("listen 443 ssl")
    if tls_listen < 0:
        raise SystemExit("TLS server block not found")
    server_name = content.find(f"server_name {domain};", tls_listen)
    if server_name < 0:
        raise SystemExit("TLS server_name not found")
    insert_at = content.find("\n", server_name) + 1
    content = content[:insert_at] + "\n" + include + content[insert_at:]
content = content.replace("ssl_certificate /ssl/cert.pem;", f"ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;")
content = content.replace("ssl_certificate_key /ssl/cert.key;", f"ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;")
path.write_text(content, encoding="utf-8")
PY

sed -i -E "s|^([[:space:]]*websocket:).*|\\1 wss://${DOMAIN}/xiaozhi/v1/|" "${PROJECT_DIR}/data/.config.yaml"
sed -i -E "s|^([[:space:]]*vision_explain:).*|\\1 https://${DOMAIN}/mcp/vision/explain|" "${PROJECT_DIR}/data/.config.yaml"

nginx -t
systemctl daemon-reload
systemctl enable prometheus prometheus-alertmanager prometheus-node-exporter prometheus-blackbox-exporter grafana-server
systemctl restart prometheus prometheus-alertmanager prometheus-node-exporter prometheus-blackbox-exporter grafana-server
systemctl restart nginx baize-xiaozhi
systemctl enable certbot.timer
if [[ "${SKIP_CERTBOT_DRY_RUN:-0}" != "1" ]]; then
  certbot renew --dry-run
fi

echo "Monitoring installed. Grafana: https://${DOMAIN}/grafana/"
echo "Read the initial Grafana password with: sudo cat /etc/baize/grafana.env"
echo "Backups: ${BACKUP_DIR}"
