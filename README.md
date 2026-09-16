# NoticeMe — 通知系统

一个轻量级的通知管理系统，支持多通知源、多通知渠道、实时通知和历史记录。

## 特性

- **通知源**: Webhook (HTTP API) 或 MQTT 订阅接收通知
- **通知渠道**: MQTT 发布 或 调用其他 API 转发通知
- **实时通知**: 持久化显示，支持 ID 标识、编辑更新、WebSocket 实时推送
- **普通通知**: 一次性推送，无需 ID
- **源-渠道映射**: 每个通知源可配置多个输出渠道
- **历史记录**: 完整记录所有通知的创建、更新、推送历史
- **Web UI**: 深色主题单页管理面板
- **轻量存储**: SQLite 数据库，零外部依赖

## 技术栈

| 层级 | 选型 |
|------|------|
| 后端 | FastAPI + uvicorn |
| 实时通信 | WebSocket |
| HTTP 客户端 | httpx |
| MQTT (可选) | aiomqtt |
| 前端 | Alpine.js + Tailwind CSS CDN |
| 存储 | SQLite |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# MQTT 功能可选
pip install aiomqtt

# 启动服务
python main.py

# 或使用 uvicorn
uvicorn main:app --host 0.0.0.0 --port 8200
```

访问: `http://localhost:8200`

## 目录结构

```
NoticeMe/
├── main.py              # FastAPI 入口 + API 路由
├── core/
│   ├── models.py        # Pydantic 数据模型
│   ├── database.py      # SQLite 数据层
│   ├── sources.py       # 通知源管理 (Webhook/MQTT)
│   ├── channels.py      # 通知渠道管理 (MQTT/API)
│   └── manager.py       # 核心调度管理器
├── static/
│   └── index.html       # Web UI (SPA)
├── data/
│   ├── notice.db        # SQLite 数据库 (自动创建)
│   └── config.json      # 配置文件
├── requirements.txt
└── README.md
```

## API 设计

### 通知源 (Sources)

```
GET    /api/sources              # 列表
POST   /api/sources              # 创建
GET    /api/sources/{id}         # 详情
PUT    /api/sources/{id}         # 编辑
DELETE /api/sources/{id}         # 删除
GET    /api/sources/{id}/channels   # 获取映射的渠道
PUT    /api/sources/{id}/channels   # 设置映射的渠道
```

创建 Webhook 通知源:
```json
POST /api/sources
{
  "name": "服务器监控",
  "type": "webhook",
  "enabled": true,
  "config": {}
}
```
返回的 `webhook_path` 即为接收地址，如 `/hook/a1b2c3d4`。

创建 MQTT 通知源:
```json
POST /api/sources
{
  "name": "IoT 设备",
  "type": "mqtt",
  "config": {
    "broker": "192.168.1.100",
    "port": 1883,
    "topic": "devices/+/status",
    "username": "user",
    "password": "pass"
  }
}
```

### 通知渠道 (Channels)

```
GET    /api/channels             # 列表
POST   /api/channels             # 创建
GET    /api/channels/{id}        # 详情
PUT    /api/channels/{id}        # 编辑
DELETE /api/channels/{id}        # 删除
```

创建 API 渠道:
```json
POST /api/channels
{
  "name": "飞书机器人",
  "type": "api",
  "config": {
    "url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
    "method": "POST",
    "headers": {"Content-Type": "application/json"},
    "body_template": "{\"msg_type\":\"text\",\"content\":{\"text\":\"{{title}}: {{content}}\"}}"
  }
}
```

创建 MQTT 渠道:
```json
POST /api/channels
{
  "name": "MQTT 转发",
  "type": "mqtt",
  "config": {
    "broker": "localhost",
    "port": 1883,
    "topic": "noticeme/out"
  }
}
```

### 通知推送

**实时通知** (持久化，有 ID，可编辑):
```json
POST /api/notifications/realtime
{
  "id": "server-cpu",
  "title": "CPU 使用率",
  "content": "当前 CPU 使用率 92%",
  "level": "warning",
  "extra": {"host": "web-01"}
}
```

**更新实时通知**:
```json
PUT /api/notifications/realtime/server-cpu
{
  "content": "CPU 使用率已恢复正常 45%",
  "level": "success"
}
```

**普通通知** (一次性，无 ID):
```json
POST /api/notifications/push
{
  "title": "部署完成",
  "content": "v2.1.0 已部署到生产环境",
  "level": "success"
}
```

**通过 Webhook 推送** (从通知源):
```bash
curl -X POST http://localhost:8200/hook/a1b2c3d4 \
  -H "Content-Type: application/json" \
  -d '{"id": "disk-alert", "title": "磁盘空间不足", "content": "/data 使用率 95%", "level": "error"}'
```
Webhook 推送的通知会自动路由到该通知源映射的所有渠道。

### 实时通知管理

```
GET    /api/notifications/realtime      # 列表
GET    /api/notifications/realtime/{id}  # 详情
PUT    /api/notifications/realtime/{id}  # 更新
DELETE /api/notifications/realtime/{id}  # 删除单个
DELETE /api/notifications/realtime       # 清空全部
```

### 历史记录

```
GET /api/history                      # 最近记录 (默认100条)
GET /api/history/{notification_id}    # 指定通知的变更历史
```

### WebSocket

```
WS /ws    # 实时通知推送
```

连接后自动推送当前所有实时通知，后续增量更新。

事件类型:
```json
{"type": "realtime_update", "notification_id": "...", "title": "...", "content": "...", "level": "...", "timestamp": "..."}
{"type": "realtime_clear", "notification_id": "..."}
{"type": "notification", "title": "...", "content": "...", "level": "...", "timestamp": "..."}
{"type": "ping"}
```

## 数据模型

### 通知源 (Source)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 唯一 ID (8位 hex) |
| name | string | 显示名称 |
| type | string | "webhook" 或 "mqtt" |
| enabled | bool | 是否启用 |
| config | object | 类型相关配置 |
| webhook_path | string | Webhook 接收路径 (自动生成) |

### 通知渠道 (Channel)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 唯一 ID |
| name | string | 显示名称 |
| type | string | "mqtt" 或 "api" |
| enabled | bool | 是否启用 |
| config | object | 类型相关配置 |

### 实时通知 (Realtime Notification)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 用户指定的 ID (如 "server-cpu") |
| title | string | 标题 |
| content | string | 内容 |
| level | string | "info" / "warning" / "error" / "success" |
| source_id | string | 来源通知源 ID (可选) |
| extra | object | 额外数据 |
| created_at | string | 创建时间 |
| updated_at | string | 最后更新时间 |

## 工作流程

```
通知源 (Webhook/MQTT) ──接收──→ NoticeMe
                                    │
                                    ├──→ 实时通知 (带 ID → 持久化 + WebSocket 广播)
                                    │
                                    ├──→ 普通通知 (无 ID → WebSocket 广播)
                                    │
                                    └──→ 路由到映射的渠道 (MQTT/API)
```

## 端口

默认: **8200**
