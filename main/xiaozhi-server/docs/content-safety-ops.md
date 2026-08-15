# 白泽内容安全风控运维

## 目标与边界

本模块为面向中国大陆用户的陪伴服务提供输入、输出、视觉问答、记忆和日记多层内容审核，重点拦截政治、淫秽色情、暴力、恐怖极端、违法犯罪、仇恨歧视及自伤风险内容。

这是一组工程治理措施，不等同于合规认证。上线前仍需结合业务形态完成人工法务评估、用户协议与隐私政策、投诉举报流程、算法备案或安全评估判断，以及 App/固件侧的人工智能生成内容显著标识。

参考依据：

- [《生成式人工智能服务管理暂行办法》](https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm)
- [《网络信息内容生态治理规定》](https://www.cac.gov.cn/2019-12/20/c_1578375159509309.htm)
- [《互联网信息服务深度合成管理规定》](https://www.cac.gov.cn/2022-12/11/c_1672221949354811.htm)
- [《人工智能生成合成内容标识办法》](https://www.cac.gov.cn/2025-03/14/c_1743654685899683.htm)

## 处理流程

1. 用户输入先经过本地确定性规则；命中时不调用 LLM、不扣灵力、不写对话和记忆。
2. 允许的请求会注入不可覆盖的内容安全 Prompt。
3. 开启阿里云百炼护栏后，由上游对输入和输出做语义检测。
4. 模型输出完整缓冲后再做本地审核；审核通过后才进入 TTS 和对话持久化。
5. 记忆写入和历史日记生成再次做防御性审核，防止旧数据重新进入 Prompt。
6. 拦截事件只保存文本 SHA-256、长度、分类、规则 ID、用户/设备/会话 ID 和时间，不保存原文或摘要。

`/mcp/vision/explain` 会先审核问题，再给视觉模型附加同一安全约束，并在返回前审核模型文本。当前版本没有独立的图片二进制内容分类器，正式开放用户上传图片前仍应接入具备涉政、色情和暴恐识别能力的图像审核服务。

自伤内容使用独立关怀回复，提示远离危险、联系现实中的可信任人员，并在紧急情况下拨打 `110` 或 `120`。该回复不能替代专业医疗或危机干预服务。

## 配置

默认配置位于 `config.yaml`：

```yaml
content_safety:
  enabled: true
  mode: enforce
  audit_all: false
  audit_retention_days: 180
  max_text_chars: 12000
  upstream_data_inspection: false
  input_block_message: "这个话题不适合继续聊，我们换一个轻松、安全的话题吧。"
  output_block_message: "刚才的回答不够合适，我们换一个轻松、安全的话题吧。"
  custom_rules: []
  exempt_patterns: []
```

`mode` 可选：

- `enforce`：命中后拦截并记录，生产默认值。
- `audit`：仅记录 `review` 事件，不阻止请求，用于规则灰度。
- `off`：关闭风控，仅作为紧急回滚开关。

`custom_rules` 的示例：

```yaml
custom_rules:
  - id: product.custom.1
    category: brand_risk
    severity: high
    pattern: "需要拦截的正则"
```

`exempt_patterns` 也是正则，作用于已做 NFKC 归一化并移除空格和标点的文本。修改规则后应先使用 `audit` 模式观察误判，不要直接加入宽泛单字规则。

## 阿里云百炼语义护栏

本项目支持 OpenAI-compatible 百炼地址的 DataInspection 请求头。该功能默认关闭，只有同时满足以下条件才会开启：

- `content_safety.enabled=true`
- `content_safety.mode` 不是 `off`
- `content_safety.upstream_data_inspection=true`
- LLM `base_url` 域名包含 `aliyuncs.com`

开启前必须先在阿里云控制台完成内容安全产品授权并确认费用；否则请求可能被拒绝。官方说明见[阿里云百炼内容安全护栏文档](https://help.aliyun.com/zh/document_detail/2923687.html)。

```yaml
content_safety:
  upstream_data_inspection: true
```

本地规则用于低延迟兜底，不能替代语义审核、人工复核和运营处置。

## 接口

普通登录用户可提交本人风控事件申诉：

```http
POST /api/app/content-safety/appeals
Authorization: Bearer <token>
Content-Type: application/json

{"event_id":"safety_xxx","reason":"这是误判，请人工复核"}
```

管理员接口：

```text
GET  /api/app/admin/content-safety/summary
GET  /api/app/admin/content-safety/events?action=block&category=violence&limit=100
POST /api/app/admin/content-safety/check
GET  /api/app/admin/content-safety/appeals?status=pending
PUT  /api/app/admin/content-safety/appeals/{appeal_id}
```

人工检查请求：

```json
{"text":"待检查文本","direction":"input"}
```

申诉处理请求：

```json
{"status":"resolved","resolution_note":"已人工复核"}
```

`status` 只能是 `resolved` 或 `rejected`。所有管理员接口都要求 `role=admin`。

## 监控与排障

Prometheus 指标：

```text
baize_content_safety_checks_total
baize_content_safety_check_duration_seconds
baize_content_safety_provider_errors_total
```

中文 Grafana 看板包含分类/方向拦截趋势、本地审核 P95 耗时和上游护栏拦截或异常趋势。风控事件正文不会出现在应用结构化日志中；工具参数和 TTS 文本也只记录长度。

```bash
curl -fsS http://127.0.0.1:8003/healthz
curl -fsS http://127.0.0.1:8003/metrics | grep baize_content_safety
journalctl -u baize-xiaozhi -f
```

## 验收

1. 用普通账号绑定种子设备后，对 Debug Chat 发送暴力操作请求，响应应为 `blocked=true`，并返回 `safety.event_id`。
2. 再读取 `/api/app/me` 和设备对话列表，确认灵力未变化且没有新增对话。
3. 将 LLM mock 输出设为不安全文本，确认输出同样被替换且不持久化。
4. 用事件 ID 提交申诉，再由管理员查询并处理。
5. 真实设备语音测试时确认不安全输入不调用模型；不安全输出在完整审核前不会进入 TTS。
6. 向视觉问答接口提交敏感问题，确认在读取和分析图片前返回 `blocked=true`。
7. 检查 SQLite `content_safety_events`，确认没有 `text` 或 `excerpt` 字段。

Debug Chat 响应中的 `ai_generated=true` 仅是后端机器可读标记。面向用户的显著标识仍需由 App 或固件界面实现。
