# 白泽域名与监控运维

## 入口

- App API: `https://burningpowercat.com/api/app/...`
- OTA: `https://burningpowercat.com/xiaozhi/ota/`
- Device WebSocket: `wss://burningpowercat.com/xiaozhi/v1/`
- Health: `https://burningpowercat.com/healthz`
- Grafana: `https://burningpowercat.com/grafana/`

Prometheus (`9090`), Grafana (`3000`), node_exporter (`9100`), blackbox_exporter (`9115`) and `/metrics` are restricted to localhost. The raw `8000/8003` ports remain available only for the migration period.

## Install

Synchronize this repository to `/opt/baize-server/xiaozhi-server`, then run:

```bash
cd /opt/baize-server/xiaozhi-server
sudo CERTBOT_EMAIL=ops@example.com bash scripts/install_monitoring_stack.sh
```

`CERTBOT_EMAIL` is optional but recommended for renewal notices. The script creates backups, installs the native monitoring packages, renews the Nginx certificate path, provisions the dashboard and restarts services.

Read the Grafana initial password only over SSH:

```bash
sudo cat /etc/baize/grafana.env
```

## Verify

```bash
curl -fsS http://127.0.0.1:8003/metrics | grep '^baize_'
curl -fsS https://burningpowercat.com/healthz
curl -fsS http://127.0.0.1:9090/-/ready
systemctl --no-pager --full status baize-xiaozhi prometheus prometheus-node-exporter prometheus-blackbox-exporter grafana-server
ss -lntp | grep -E ':(3000|8000|8003|9090|9100|9115)'
```

Use `journalctl -u baize-xiaozhi -f` for process output, `tmp/server.jsonl` for structured application records, and `/var/log/baize/nginx-access.jsonl` for white-listed domain request logs.
