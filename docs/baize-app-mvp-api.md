# 白泽 App MVP 接口文档

本文档描述当前 Python 后端已实现的白泽内测 App API。接口部署在 HTTP 服务端口，当前服务器示例：

```text
Base URL: http://47.96.77.235:8003
```

## 1. 通用约定

### 1.1 请求格式

- 请求体：`application/json`
- 响应体：`application/json`
- 时间字段：ISO 8601 字符串，例如 `2026-07-02T15:47:15+00:00`

### 1.2 鉴权

除注册、登录和旧 demo 登录外，App API 都需要 Bearer Token：

```http
Authorization: Bearer <token>
```

Token 来源：

- `POST /api/app/register`
- `POST /api/app/login`
- 兼容旧接口：`POST /api/app/demo-login`

### 1.3 通用错误

错误响应统一为：

```json
{
  "error": "错误说明"
}
```

常见状态码：

| 状态码 | 含义 |
|---|---|
| `400` | 参数错误，例如缺少 `device_code`、内测码无效 |
| `401` | 未登录或 token 无效 |
| `404` | 资源不存在，或当前用户未绑定该设备 |
| `409` | 当前状态不允许操作，例如设备不在线、精力不足 |
| `500/502/503` | 后端、模型或设备链路异常 |

## 2. 账号接口

### 2.1 注册

```http
POST /api/app/register
```

请求：

```json
{
  "invite_code": "BAIZE-MVP",
  "nickname": "Alice"
}
```

响应：

```json
{
  "token": "mvp_xxx",
  "expires_at": "2026-08-01T15:47:15+00:00",
  "user": {
    "id": "user_xxx",
    "nickname": "Alice",
    "display_name": "Alice",
    "login_type": "invite_code",
    "invite_code": "BAIZE-MVP",
    "created_at": "2026-07-02T15:47:15+00:00",
    "last_login_at": "2026-07-02T15:47:15+00:00"
  }
}
```

说明：

- 当前账号方案为“内测码 + 昵称”。
- 如果同一个 `invite_code + nickname` 已存在，会视为登录并返回新 token。

### 2.2 登录

```http
POST /api/app/login
```

请求和响应同注册。

### 2.3 旧 Demo 登录

```http
POST /api/app/demo-login
```

响应：

```json
{
  "token": "demo-token",
  "legacy": true,
  "user": {
    "id": "demo_user",
    "nickname": "Demo User",
    "display_name": "Demo User"
  }
}
```

说明：仅为兼容旧 App。新 App 应使用注册/登录接口。

### 2.4 当前用户信息

```http
GET /api/app/me
Authorization: Bearer <token>
```

响应：

```json
{
  "id": "user_xxx",
  "nickname": "Alice",
  "display_name": "Alice",
  "login_type": "invite_code",
  "invite_code": "BAIZE-MVP",
  "created_at": "2026-07-02T15:47:15+00:00",
  "last_login_at": "2026-07-02T15:47:15+00:00",
  "energy": {
    "current": 30,
    "daily_limit": 30,
    "updated_at": "2026-07-02T15:47:15+00:00",
    "last_recovered_on": "2026-07-02"
  },
  "intimacy": {
    "level": "初识",
    "score": 0,
    "level_min": 0,
    "level_max": 100,
    "progress": 0.0
  }
}
```

## 3. 设备接口

### 3.1 绑定设备

```http
POST /api/app/devices/bind
Authorization: Bearer <token>
```

请求：

```json
{
  "device_code": "123456"
}
```

响应：

```json
{
  "id": "baize_dev_001",
  "device_code": "123456",
  "display_name": "我的白泽",
  "source_device_id": null,
  "client_id": null,
  "model": null,
  "online_status": "unknown",
  "battery_percent": null,
  "firmware_version": "0.1.0-demo",
  "last_online_at": null
}
```

说明：

- 当前默认种子设备码为 `123456`。
- 同一用户重复绑定同一设备是幂等的。
- 不同用户数据隔离；未绑定用户访问设备详情会返回 `404`。

### 3.2 设备列表

```http
GET /api/app/devices
Authorization: Bearer <token>
```

响应：

```json
{
  "items": [
    {
      "id": "baize_dev_001",
      "device_code": "123456",
      "display_name": "我的白泽",
      "source_device_id": null,
      "client_id": null,
      "model": null,
      "online_status": "unknown",
      "battery_percent": null,
      "firmware_version": "0.1.0-demo",
      "last_online_at": null
    }
  ]
}
```

### 3.3 设备详情

```http
GET /api/app/devices/{device_id}
Authorization: Bearer <token>
```

响应同设备对象。

### 3.4 修改设备名称

```http
PUT /api/app/devices/{device_id}
Authorization: Bearer <token>
```

请求：

```json
{
  "display_name": "客厅白泽"
}
```

响应同设备对象。

### 3.5 解绑设备

```http
POST /api/app/devices/{device_id}/unbind
Authorization: Bearer <token>
```

响应：

```json
{
  "unbound": true,
  "id": "baize_dev_001"
}
```

## 4. 白泽设置

### 4.1 获取设备设置

```http
GET /api/app/devices/{device_id}/settings
Authorization: Bearer <token>
```

响应：

```json
{
  "device_id": "baize_dev_001",
  "baize_nickname": "白泽",
  "user_call_name": "小伙伴",
  "personality_mode": "curious",
  "tts_voice": "Sambert 知颖"
}
```

### 4.2 更新设备设置

```http
PUT /api/app/devices/{device_id}/settings
Authorization: Bearer <token>
```

请求字段均可选，但传入字段不能为空字符串：

```json
{
  "baize_nickname": "小白泽",
  "user_call_name": "Alice",
  "personality_mode": "gentle",
  "tts_voice": "Sambert 知颖"
}
```

响应同设置对象。

## 5. 对话接口

### 5.1 对话列表

```http
GET /api/app/devices/{device_id}/dialogues
Authorization: Bearer <token>
```

响应：

```json
{
  "items": [
    {
      "id": "dlg_xxx",
      "user_id": "user_xxx",
      "device_id": "baize_dev_001",
      "source_device_id": "68:ee:8f:5c:71:54",
      "session_id": "session-1",
      "user_text": "白泽，今天开心吗？",
      "baize_text": "当然开心呀，小伙伴。",
      "emotion": "happy",
      "created_at": "2026-07-02T15:47:15+00:00"
    }
  ]
}
```

说明：

- 最多返回最近 100 条。
- 会过滤旧 demo 中“小智/主人/台湾腔”等历史脏数据。
- `emotion` 当前统一为 7 类：`neutral`、`happy`、`thinking`、`surprised`、`sad`、`sleepy`、`confused`。

### 5.2 调试文本对话

```http
POST /api/app/devices/{device_id}/debug/chat
Authorization: Bearer <token>
```

请求：

```json
{
  "text": "你是谁呀？"
}
```

响应：

```json
{
  "reply": "我是白泽幼灵呀，来自上古神话世界。",
  "session_id": "demo_chat_xxx",
  "dialogue": {
    "id": "dlg_xxx",
    "user_id": "user_xxx",
    "device_id": "baize_dev_001",
    "source_device_id": "",
    "session_id": "demo_chat_xxx",
    "user_text": "你是谁呀？",
    "baize_text": "我是白泽幼灵呀，来自上古神话世界。",
    "emotion": "neutral",
    "created_at": "2026-07-02T15:47:15+00:00"
  }
}
```

说明：

- 成功调用消耗 `1` 点精力。
- 如果精力不足，返回 `409`。
- 如果 LLM 未配置或调用失败，返回 `503/500/502`。

## 6. 日记接口

### 6.1 日记列表

```http
GET /api/app/devices/{device_id}/diaries
Authorization: Bearer <token>
```

响应：

```json
{
  "items": [
    {
      "id": "diary_xxx",
      "date": "2026-07-02",
      "title": "2026-07-02 的白泽小记",
      "summary": "今天聊了演示和紧张。白泽回应：我在呢，先别急。",
      "primary_emotion": "happy",
      "dialogue_count": 2,
      "quotes": [
        {
          "user_text": "我今天完成了演示。",
          "baize_text": "哇，可以啊！这一下值得小小庆祝。",
          "emotion": "happy"
        }
      ],
      "baize_note": "今天也有好好聊过啦，小伙伴。",
      "generated_at": "2026-07-02T15:47:15+00:00"
    }
  ]
}
```

### 6.2 生成日记

```http
POST /api/app/devices/{device_id}/diaries/generate
Authorization: Bearer <token>
```

请求：

```json
{
  "date": "2026-07-02"
}
```

说明：

- `date` 可选；不传时使用最近一条对话所在日期。
- 同一天重复生成会更新同一篇日记，不新增重复记录。
- 成功生成消耗 `2` 点精力。
- 当天没有对话时返回 `404`。

响应同单篇日记对象。

## 7. 记忆接口

### 7.1 记忆列表

```http
GET /api/app/devices/{device_id}/memories
Authorization: Bearer <token>
```

响应：

```json
{
  "items": [
    {
      "id": "mem_xxx",
      "category": "偏好",
      "content": "用户喜欢温柔一点的声音",
      "created_at": "2026-07-02T15:47:15+00:00"
    }
  ]
}
```

说明：当前版本主要提供查看和删除能力；记忆写入仍处于后续增强阶段。

### 7.2 删除记忆

```http
DELETE /api/app/devices/{device_id}/memories/{memory_id}
Authorization: Bearer <token>
```

响应：

```json
{
  "deleted": true,
  "id": "mem_xxx"
}
```

## 8. OTA 与连接诊断

### 8.1 App 侧 OTA 状态

```http
GET /api/app/devices/{device_id}/ota
Authorization: Bearer <token>
```

响应：

```json
{
  "device_id": "baize_dev_001",
  "current_version": "0.1.0-demo",
  "latest_version": "0.1.0-demo",
  "update_available": false,
  "release_note": "等待设备版本上报"
}
```

### 8.2 刷新设备状态

```http
POST /api/app/devices/{device_id}/refresh-status
Authorization: Bearer <token>
```

响应同设备对象。

说明：

- 设备必须在线，否则返回 `409`。
- 后端会通过 MCP 查询设备状态，并更新电量等信息。

### 8.3 连接诊断

```http
GET /api/app/devices/{device_id}/connection
Authorization: Bearer <token>
```

响应：

```json
{
  "online": true,
  "matched_identifier": "client_id",
  "matched_value": "client-mcp-001",
  "active_identifiers": ["client-mcp-001"],
  "device": {
    "id": "baize_dev_001",
    "source_device_id": "68:ee:8f:5c:71:54",
    "client_id": "client-mcp-001",
    "online_status": "online"
  }
}
```

### 8.4 设备 OTA 接口

设备固件使用的 OTA 接口仍为：

```http
GET /xiaozhi/ota/
POST /xiaozhi/ota/
```

当前线上验证返回：

```text
OTA接口运行正常，向设备发送的websocket地址是：ws://47.96.77.235:8000/xiaozhi/v1/
```

## 9. Demo 执行接口

### 9.1 执行 60 秒 Demo

```http
POST /api/app/devices/{device_id}/demo/run
Authorization: Bearer <token>
```

请求：

```json
{
  "script": "sixty_second"
}
```

响应：

```json
{
  "started": true,
  "script": "sixty_second",
  "prompt": "请执行白泽幼灵 60 秒 Demo。..."
}
```

说明：

- 当前仅支持 `sixty_second`。
- 设备必须在线，否则返回 `409`。

## 10. 运营指标接口

### 10.1 基础运营指标

```http
GET /api/app/admin/metrics
Authorization: Bearer <token>
```

响应：

```json
{
  "users": 3,
  "bound_devices": 1,
  "dialogues": 12,
  "diaries": 2,
  "energy_consumed": 8,
  "emotion_hits": {
    "happy": 5,
    "neutral": 7
  }
}
```

说明：

- 当前没有复杂后台权限，任意有效 App token 可访问。
- 内测期仅用于快速观察数据。

## 11. 当前业务规则

### 11.1 精力值

| 行为 | 规则 |
|---|---|
| 新用户初始精力 | `30` |
| 每日恢复 | 恢复到上限 `30` |
| 调试文本对话成功 | `-1` |
| 生成日记成功 | `-2` |
| 查看设备、对话、日记、记忆 | 不消耗 |

### 11.2 亲密度

| 行为 | 增长 |
|---|---|
| 每日首次有效对话 | `+5` |
| 同日后续有效对话 | `+1` |
| 生成日记 | `+3` |

等级：

| 分数 | 等级 |
|---|---|
| `0-99` | 初识 |
| `100-299` | 熟悉 |
| `300-699` | 亲近 |
| `700+` | 默契 |

### 11.3 Emotion 枚举

当前统一输出以下 7 类：

```text
neutral
happy
thinking
surprised
sad
sleepy
confused
```

旧情绪会归一化，例如：

| 旧值 | 新值 |
|---|---|
| `laughing`、`relaxed`、`loving` | `happy` |
| `angry`、`crying` | `sad` |
| `shocked`、`embarrassed` | `surprised` |

## 12. 快速联调示例

```bash
BASE=http://47.96.77.235:8003

TOKEN=$(curl -s -X POST "$BASE/api/app/register" \
  -H 'Content-Type: application/json' \
  -d '{"invite_code":"BAIZE-MVP","nickname":"Alice"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -s "$BASE/api/app/me" \
  -H "Authorization: Bearer $TOKEN"

curl -s -X POST "$BASE/api/app/devices/bind" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"device_code":"123456"}'

curl -s "$BASE/api/app/devices" \
  -H "Authorization: Bearer $TOKEN"
```

