# 对话阶段耗时分析工具

本文说明 `scripts/dialog_timing_analyzer.py` 的输入格式、使用方式和报告解读方法。该工具用于统计每次语音对话在不同阶段的耗时，重点关注用户提问后到设备第一声回复的首响时间，自动给出瓶颈结论，并生成可视化 HTML 供人工审阅。

## 适用场景

- Demo 调试时判断一次对话慢在录音、上传、ASR、LLM、TTS、下载、播放还是眼睛表情联动。
- 重点查看 `first_response_ms`：从 ASR 得到最终用户问题，到设备第一包回复音频发出的间隔。
- 对比不同后端模型、TTS 服务或网络环境下的阶段耗时变化。
- 从服务端日志、模拟设备日志或手工记录中快速生成审阅报告。

## 推荐阶段命名

| 阶段 | 含义 |
| --- | --- |
| `wake` | 触摸或按键唤醒到开始录音 |
| `record` | 设备录音 |
| `upload` | 设备上传音频到后端 |
| `asr` | 语音识别 |
| `llm` | 大模型生成回复、人格和情绪 |
| `tts` | 语音合成 |
| `download` | 设备接收音频或播放 URL |
| `playback` | 设备播放回复 |
| `eyes` | 眼睛表情或情绪联动 |

可以增加自定义阶段，工具会自动纳入统计。

## 输入格式

工具支持 `.jsonl`、`.json` 和 `.csv`。

### 事件日志

每个阶段记录开始和结束事件：

```jsonl
{"conversation_id":"demo-001","phase":"wake","event":"start","timestamp":"2026-06-18T10:00:00.000Z"}
{"conversation_id":"demo-001","phase":"wake","event":"end","timestamp":"2026-06-18T10:00:00.180Z"}
{"conversation_id":"demo-001","phase":"asr","event":"start","timestamp":"2026-06-18T10:00:02.000Z"}
{"conversation_id":"demo-001","phase":"asr","event":"end","timestamp":"2026-06-18T10:00:02.820Z"}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `conversation_id` | 是 | 单次对话 ID。也兼容 `conversation`、`session_id`、`dialog_id` |
| `phase` | 是 | 阶段名。也兼容 `stage`、`step` |
| `event` | 事件格式必填 | `start` 或 `end`。也兼容 `begin`、`finish`、`done` |
| `timestamp` | 事件格式必填 | ISO 时间、Unix 秒或 Unix 毫秒 |

### 已聚合耗时

如果后端已经记录了每个阶段耗时，可以直接写：

```jsonl
{"conversation_id":"demo-001","phase":"asr","duration_ms":820}
{"conversation_id":"demo-001","phase":"llm","duration_ms":1460}
{"conversation_id":"demo-001","phase":"tts","duration_ms":930}
```

也可以使用 `duration_sec`、`latency_ms`、`elapsed_ms` 等兼容字段。

## 使用方式

生成 Markdown、HTML 和 JSON 三种报告：

```bash
python3 scripts/dialog_timing_analyzer.py examples/dialog-timing-sample.jsonl -o reports/dialog-timing-demo
```

生成文件：

```text
reports/dialog-timing-demo.md
reports/dialog-timing-demo.html
reports/dialog-timing-demo.json
```

只生成 HTML：

```bash
python3 scripts/dialog_timing_analyzer.py examples/dialog-timing-sample.jsonl -o reports/dialog-timing-demo --format html
```

## 报告怎么看

- `自动结论`：直接给出平均耗时、累计瓶颈、P95 长尾阶段和最慢单次对话。
- `阶段累计耗时`：适合判断整体工程优先级，累计越高，优化收益通常越大。
- `最慢对话 Top 30`：适合人工回看原始日志，找网络抖动、模型超时、TTS 排队等异常。
- `阶段明细`：看平均值、中位数、P95 和最大值，区分稳定偏慢和偶发长尾。

## 接入建议

第一阶段 Demo 可以先在后端 WebSocket 链路里按阶段打点，输出 JSONL 文件。后续 App 或后台可以上传该报告 HTML，作为设备调试记录的一部分。

建议最少记录：

- 对话 ID：贯穿设备、后端、App 调试日志。
- 用户问题和白泽回答：日志中应单行清洗并截断，方便人工定位慢轮次。
- 首响时间：优先记录 `first_response_ms`，即 `asr_final` 到 `first_audio_sent` 的耗时。
- 阶段名：使用本文推荐命名，方便横向对比。
- 开始和结束时间，或阶段耗时。
- 可选元信息：设备 ID、网络类型、模型名、TTS 提供商、是否命中缓存。
