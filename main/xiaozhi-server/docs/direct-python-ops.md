# Baize Xiaozhi Direct Python Ops

This service is deployed directly as Python code on the server. Docker is not required.

## Runtime Paths

- Project: `/opt/baize-server/xiaozhi-server`
- Python: `/opt/miniconda/envs/baize/bin/python`
- HTTP/App API/OTA: `8003`
- WebSocket: `8000`
- Log file: `/opt/baize-server/xiaozhi-server/tmp/baize-server.log`
- SQLite: `data/app_mvp.sqlite3` unless overridden by `app_mvp.db_path`

## Admin Phones

Configure admin accounts in `data/.config.yaml`:

```yaml
app_mvp:
  db_path: data/app_mvp.sqlite3
  admin_phones:
    - "17352624729"
```

Users with a matching phone are assigned `role: admin` on register or login. Other users remain `role: user`.

## Install systemd Service

```bash
cd /opt/baize-server/xiaozhi-server
sudo cp deploy/systemd/baize-xiaozhi.service /etc/systemd/system/baize-xiaozhi.service
sudo systemctl daemon-reload
sudo systemctl enable baize-xiaozhi
sudo systemctl restart baize-xiaozhi
sudo systemctl status baize-xiaozhi --no-pager
```

## Deploy Current Code

Run from the server after code has been synchronized:

```bash
cd /opt/baize-server/xiaozhi-server
bash scripts/deploy_direct_python.sh
```

The script backs up current files, runs syntax checks and focused tests, restarts the service, and verifies OTA plus `/healthz`.

## Common Commands

```bash
sudo systemctl restart baize-xiaozhi
sudo systemctl status baize-xiaozhi --no-pager
journalctl -u baize-xiaozhi -n 100 --no-pager
tail -f /opt/baize-server/xiaozhi-server/tmp/baize-server.log
```

## Health Checks

```bash
curl -s http://127.0.0.1:8003/healthz
curl -i http://127.0.0.1:8003/xiaozhi/ota/
```

`/healthz` returns service uptime, configured ports, WebSocket URL, and SQLite read/write status. It does not require authentication.

## API Smoke Test

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8003/api/app/register \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800138000","password":"secret1","nickname":"Alice"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -s http://127.0.0.1:8003/api/app/me \
  -H "Authorization: Bearer $TOKEN"
```

Admin endpoints require a token for a phone listed in `app_mvp.admin_phones`.
