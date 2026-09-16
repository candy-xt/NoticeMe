# NoticeMe

轻量级通知管理系统，支持多通知源、多通知渠道、实时通知、MCP 协议接入。

## 特性

- **通知源**: Webhook (HTTP API) 或 MQTT 订阅
- **通知渠道**: MQTT 发布 或 API 请求转发
- **实时通知**: 持久化显示，ID 标识，WebSocket 实时推送，可编辑更新
- **普通通知**: 一次性推送，fire-and-forget
- **源→渠道映射**: 每个通知源可配置多个输出渠道
- **历史记录**: 完整记录创建/更新/推送/清除操作
- **MCP 服务器**: stdio (编辑器/Agent 集成) + streamable-http (共用 API 端口)
- **CLI 工具**: `nme` 命令行管理源、渠道、通知
- **Web UI**: 深色主题 SPA，手机自适应
- **轻量存储**: SQLite，零外部依赖

## 安装

```bash
pip install noticeme
```

或从源码安装：

```bash
git clone https://github.com/CandyXT/NoticeMe.git
cd NoticeMe
pip install -e .
```

## 快速开始

```bash
# 启动服务 (API + WebUI + WebSocket + MCP streamable-http)
nme serve

# 或指定参数
nme serve --host 0.0.0.0 --port 8200 --reload
```

访问: `http://localhost:8200`

数据存储在 `~/.noticeme/` (SQLite 数据库)

## CLI 命令

```bash
nme --version              # 查看版本

# 服务器
nme serve                  # 启动 HTTP 服务器
nme serve --port 8200      # 指定端口
nme serve --reload         # 开发模式 (自动重载)

# MCP 服务器
nme mcp                    # 在 stdio 上运行 MCP 服务器

# 初始化
nme init                   # 初始化数据库

# 通知源
nme source list            # 列出所有通知源
nme source add -n "监控" -t webhook
nme source add -n "IoT" -t mqtt --broker 192.168.1.100 --topic "devices/#"
nme source remove <id>
nme source map <source_id> <channel_id1> <channel_id2>

# 通知渠道
nme channel list           # 列出所有渠道
nme channel add -n "飞书" -t api --url "https://open.feishu.cn/..."
nme channel add -n "MQTT转发" -t mqtt --broker localhost --topic "out"
nme channel remove <id>

# 推送通知
nme notify --title "部署完成" --content "v2.1.0 已上线" --level success
nme notify --id cpu-alert --title "CPU" --content "使用率 92%" --level warning

# 实时通知
nme realtime list          # 列出活跃实时通知
nme realtime clear <id>    # 清除单个
nme realtime clear         # 清除全部

# 历史记录
nme history                # 查看最近记录
nme history --limit 50
nme history --notification <id>
```

## MCP 服务器

### stdio 模式 (编辑器/Agent 集成)

```bash
nme mcp
```

在 Claude Desktop、Cursor 等 MCP 客户端中配置:

```json
{
  "mcpServers": {
    "noticeme": {
      "command": "nme",
      "args": ["mcp"]
    }
  }
}
```

### streamable-http 模式

与 API/WebUI 共用端口，端点: `http://localhost:8200/mcp`

### MCP 工具列表

| 工具 | 说明 |
|------|------|
| `list_sources` | 列出通知源 |
| `create_source` | 创建通知源 |
| `delete_source` | 删除通知源 |
| `list_channels` | 列出通知渠道 |
| `create_channel` | 创建渠道 |
| `delete_channel` | 删除渠道 |
| `get_source_channels` | 获取源的渠道映射 |
| `set_source_channels` | 设置源的渠道映射 |
| `list_realtime_notifications` | 列出实时通知 |
| `push_realtime_notification` | 推送实时通知 |
| `update_realtime_notification` | 更新实时通知 |
| `clear_realtime_notification` | 清除实时通知 |
| `push_notification` | 推送普通通知 |
| `get_history` | 查询历史记录 |

## API

### 通知源

```
GET    /api/sources              # 列表
POST   /api/sources              # 创建
GET    /api/sources/{id}         # 详情
PUT    /api/sources/{id}         # 编辑
DELETE /api/sources/{id}         # 删除
GET    /api/sources/{id}/channels   # 获取映射
PUT    /api/sources/{id}/channels   # 设置映射
```

### 通知渠道

```
GET    /api/channels             # 列表
POST   /api/channels             # 创建
GET    /api/channels/{id}        # 详情
PUT    /api/channels/{id}        # 编辑
DELETE /api/channels/{id}        # 删除
```

### 通知推送

```bash
# 实时通知 (带 ID，持久化)
curl -X POST http://localhost:8200/api/notifications/realtime \
  -H "Content-Type: application/json" \
  -d '{"id": "cpu", "title": "CPU 使用率", "content": "92%", "level": "warning"}'

# 更新实时通知
curl -X PUT http://localhost:8200/api/notifications/realtime/cpu \
  -H "Content-Type: application/json" \
  -d '{"content": "45%", "level": "success"}'

# 普通通知
curl -X POST http://localhost:8200/api/notifications/push \
  -H "Content-Type: application/json" \
  -d '{"title": "部署完成", "content": "v2.1.0"}'

# Webhook 入口
curl -X POST http://localhost:8200/hook/<source_id> \
  -H "Content-Type: application/json" \
  -d '{"id": "disk", "title": "磁盘不足", "level": "error"}'
```

### 实时通知管理

```
GET    /api/notifications/realtime       # 列表
GET    /api/notifications/realtime/{id}   # 详情
PUT    /api/notifications/realtime/{id}   # 更新
DELETE /api/notifications/realtime/{id}   # 删除单个
DELETE /api/notifications/realtime        # 清空全部
```

### 历史记录

```
GET /api/history                       # 最近记录
GET /api/history/{notification_id}     # 指定通知的历史
```

### WebSocket

```
WS /ws    # 实时通知推送
```

## 目录结构

```
NoticeMe/
├── __init__.py            # 包初始化 (版本号)
├── app.py                 # FastAPI 应用工厂 + 路由注册
├── main.py                # 开发入口 (python -m NoticeMe.main)
├── cli.py                 # Click CLI (nme 命令)
├── mcp_server.py          # MCP 服务器 (stdio + streamable-http)
├── core/
│   ├── models.py          # Pydantic 数据模型
│   ├── database.py        # SQLite 数据层
│   ├── sources.py         # 通知源管理 (Webhook/MQTT)
│   ├── channels.py        # 通知渠道管理 (MQTT/API)
│   └── manager.py         # 核心调度管理器
├── static/
│   └── index.html         # Web UI (Alpine.js + Tailwind)
├── ~/.noticeme/           # 运行时数据 (数据库, 配置)
├── .github/workflows/
│   └── release.yml        # CI/CD (tag → GitHub Release + PyPI)
├── pyproject.toml         # 包配置
├── requirements.txt       # 依赖清单
└── README.md
```

## 技术栈

| 组件 | 选型 |
|------|------|
| 后端 | FastAPI + uvicorn |
| 实时通信 | WebSocket + aiomqtt |
| HTTP 客户端 | httpx |
| MCP 协议 | mcp SDK |
| 前端 | Alpine.js + Tailwind CSS CDN |
| 存储 | SQLite |
| CLI | Click |
| 打包 | hatchling |
| CI/CD | GitHub Actions |

## 许可证

MIT License
