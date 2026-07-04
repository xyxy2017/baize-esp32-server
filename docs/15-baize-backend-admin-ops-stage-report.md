# 白泽后端阶段性报告：Admin 权限与直部署运维

日期：2026-07-04

## 阶段目标

本阶段跳过短信验证码，继续保留“手机号 + 密码”账号体系，只补两块后端能力：

- Admin 权限隔离，避免普通登录用户访问 `/api/app/admin/*`。
- Python 直部署运维能力，包括 `/healthz`、部署脚本、systemd 模板和操作文档。

## 完成内容

### 1. Admin 权限隔离

- `users` 表新增 `role` 字段，默认值为 `user`。
- 支持配置管理员手机号：

```yaml
app_mvp:
  db_path: data/app_mvp.sqlite3
  admin_phones:
    - "17352624729"
```

- 用户注册、登录、token 鉴权、`/api/app/me` 查询时，会按 `app_mvp.admin_phones` 自动同步管理员角色。
- `/api/app/me` 响应新增 `role` 字段。
- 以下接口已要求 `role=admin`：
  - `GET /api/app/admin/metrics`
  - `GET /api/app/admin/devices`
  - `POST /api/app/admin/devices`
  - `POST /api/app/admin/devices/{id}/rotate-code`
  - `GET /api/app/admin/conversations`
  - `GET /api/app/admin/energy-events`
  - `GET /api/app/admin/intimacy-events`
- 普通用户访问 admin 接口返回 `403`。
- 旧 `/api/app/demo-login` 和普通用户设备、对话、日记、记忆接口保持兼容。

### 2. 健康检查

新增无鉴权接口：

```http
GET /healthz
```

正常响应示例：

```json
{
  "status": "ok",
  "service": "baize-xiaozhi-server",
  "started_at": "2026-07-04T08:27:08+00:00",
  "uptime_seconds": 2,
  "http_port": 8003,
  "websocket_port": 8000,
  "websocket": "ws://47.96.77.235:8000/xiaozhi/v1/",
  "sqlite": {
    "ok": true,
    "path": "data/app_mvp.sqlite3",
    "writable": true
  }
}
```

SQLite 不可访问时返回非 `ok` 状态，并包含 `error` 和 `message` 字段，便于排障。

### 3. 直部署运维文件

- 新增 `main/xiaozhi-server/scripts/deploy_direct_python.sh`
  - 备份当前代码。
  - 执行语法检查。
  - 执行本次相关单测。
  - 优先使用 systemd 重启，未安装 service 时 fallback 到 `nohup`。
  - 验证 OTA 和 `/healthz`。
- 新增 `main/xiaozhi-server/deploy/systemd/baize-xiaozhi.service`
  - 工作目录：`/opt/baize-server/xiaozhi-server`
  - Python：`/opt/miniconda/envs/baize/bin/python app.py`
  - 重启策略：`Restart=on-failure`、`RestartSec=5`
  - 日志：`tmp/baize-server.log` 和 `journalctl -u baize-xiaozhi`
- 新增 `main/xiaozhi-server/docs/direct-python-ops.md`
  - 记录 systemd 安装、重启、日志、健康检查、手机号注册登录和 admin 调试命令。

## 服务器部署记录

目标服务器：

- IP：`47.96.77.235`
- 目录：`/opt/baize-server/xiaozhi-server`
- HTTP/App API/OTA：`8003`
- WebSocket：`8000`
- SQLite：`data/app_mvp.sqlite3`

部署步骤：

1. 备份服务器文件到：

```text
/opt/baize-server/xiaozhi-server/backups/deploy-2026-07-04-162445
```

2. 同步本次后端代码、测试、脚本、systemd 模板和运维文档。
3. 更新服务器 `data/.config.yaml`，增加：

```yaml
app_mvp:
  db_path: data/app_mvp.sqlite3
  admin_phones:
    - "17352624729"
```

4. 服务器执行语法检查和单测。
5. 因服务器尚未安装 `baize-xiaozhi.service`，本次使用 `nohup` 临时重启服务。

重启结果：

```text
old_pid=59360
new_pid=60677
```

## 验证结果

### 1. 服务器语法检查

```bash
/opt/miniconda/envs/baize/bin/python -m py_compile \
  core/api/app_demo_store.py \
  core/api/app_demo_handler.py \
  core/api/health_handler.py \
  core/http_server.py
```

结果：通过。

### 2. 服务器单测

```bash
/opt/miniconda/envs/baize/bin/python -m unittest -v \
  tests.test_app_demo_handler \
  tests.test_device_mcp_handler
```

结果：

```text
Ran 31 tests in 4.842s
OK
```

### 3. OTA 验证

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8003/xiaozhi/ota/
```

结果：

```text
200
```

### 4. 健康检查验证

```bash
curl -s http://127.0.0.1:8003/healthz
```

结果：HTTP `200`，`status=ok`，SQLite 可写。

### 5. 普通用户权限验证

注册普通用户：

```bash
curl -X POST http://127.0.0.1:8003/api/app/register \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13916273501","password":"secret123","nickname":"SmokeUser"}'
```

关键响应：

```json
{
  "user": {
    "phone": "13916273501",
    "masked_phone": "139****3501",
    "role": "user"
  }
}
```

普通用户访问 admin metrics：

```bash
curl http://127.0.0.1:8003/api/app/admin/metrics \
  -H 'Authorization: Bearer <USER_TOKEN>'
```

结果：HTTP `403`。

### 6. Admin 用户权限验证

注册配置内管理员手机号：

```bash
curl -X POST http://127.0.0.1:8003/api/app/register \
  -H 'Content-Type: application/json' \
  -d '{"phone":"17352624729","password":"secret123","nickname":"AdminSmoke"}'
```

关键响应：

```json
{
  "user": {
    "phone": "17352624729",
    "masked_phone": "173****4729",
    "role": "admin"
  }
}
```

Admin 访问 metrics：

```bash
curl http://127.0.0.1:8003/api/app/admin/metrics \
  -H 'Authorization: Bearer <ADMIN_TOKEN>'
```

结果：HTTP `200`。

关键响应：

```json
{
  "users": 8,
  "bound_devices": 1,
  "dialogues": 0,
  "diaries": 0,
  "energy_consumed": 0,
  "emotion_hits": {},
  "phone_users": 2
}
```

## 当前状态

- 本地测试通过。
- 服务器测试通过。
- 线上服务已重启并运行新代码。
- OTA 正常。
- `/healthz` 正常。
- 普通用户 admin 权限隔离生效。
- 配置管理员手机号 admin 权限生效。

## 后续建议

- 将 systemd 模板安装到服务器，替代长期 `nohup`：

```bash
sudo cp deploy/systemd/baize-xiaozhi.service /etc/systemd/system/baize-xiaozhi.service
sudo systemctl daemon-reload
sudo systemctl enable baize-xiaozhi
sudo systemctl restart baize-xiaozhi
```

- 后续再接短信验证码时，只需要替换注册登录前置校验，不需要重做 role 和 admin 权限模型。
