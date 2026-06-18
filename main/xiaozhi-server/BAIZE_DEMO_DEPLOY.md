# 白泽 Demo 后端部署说明

这份说明用于让同伴把当前 Python 后端部署到远程服务器，跑通 ESP32 设备、iOS App、ASR、LLM、TTS、记忆和日记 Demo 链路。

## 1. 准备运行环境

```bash
cd main/xiaozhi-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

服务端还需要本机或服务器可用的 `ffmpeg`。

## 2. 创建本地配置

```bash
cp data/.config.example.yaml data/.config.yaml
```

然后编辑 `data/.config.yaml`：

| 配置项 | 说明 |
|---|---|
| `server.websocket` | ESP32 通过 OTA 拿到的 WebSocket 地址，远程部署时改成公网 IP 或域名 |
| `server.vision_explain` | 视觉接口地址，Demo 可先保留同一台服务器 |
| `ASR.AliyunStreamASR.appkey` | 阿里云智能语音交互 AppKey |
| `ASR.AliyunStreamASR.access_key_id` | 阿里云 AccessKey ID |
| `ASR.AliyunStreamASR.access_key_secret` | 阿里云 AccessKey Secret |
| `LLM.AliLLM.api_key` | 阿里云百炼 / DashScope API Key |
| `TTS.AliBLSambertTTS.api_key` | 阿里云百炼 / DashScope API Key |

`data/.config.yaml` 只放在服务器本地，不提交到 Git。里面包含真实密钥。

## 3. 启动服务

```bash
source .venv/bin/activate
python app.py
```

默认端口：

| 端口 | 用途 |
|---|---|
| `8000` | ESP32 WebSocket 对话连接 |
| `8003` | OTA、iOS Demo App API、视觉接口 |

服务器安全组或防火墙需要放行这两个端口。

## 4. iOS App 连接

iOS Demo App 的 API Base URL 需要指向：

```text
http://YOUR_SERVER_HOST:8003
```

App 当前使用真实后端接口获取设备、对话记录、记忆和日记数据。

## 5. ESP32 连接

ESP32 设备启动后访问 OTA 接口：

```text
http://YOUR_SERVER_HOST:8003/xiaozhi/ota/
```

OTA 返回的 WebSocket 地址来自 `data/.config.yaml` 中的 `server.websocket`。

## 6. 不要提交的运行态文件

以下文件由服务器运行时生成或包含敏感信息，不应提交：

| 文件 / 目录 | 原因 |
|---|---|
| `data/.config.yaml` | 包含 ASR、LLM、TTS 密钥 |
| `data/app_demo_state.json` | 包含设备绑定、对话记录、日记 |
| `data/.memory.yaml` | 包含用户长期记忆 |
| `tmp/` | 日志和临时音频文件 |
| `.venv/` | 本地 Python 虚拟环境 |

需要交接部署时，只提交 `data/.config.example.yaml` 和本文档。
