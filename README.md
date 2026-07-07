# Edge Gateway

Edge Gateway 是一个适配 DJI Dock 3 上云 API 的 Python 边缘网关项目。当前阶段目标是在 Ubuntu 服务器内跑通纯模拟全链路，不接实体无人机、飞控、机巢或负载设备。

当前优先级是先让链路通：

```text
上云 API 模拟器
  -> MQTT Broker
  -> Edge Gateway
  -> MAVLink / SITL / 机巢模拟器
  -> Edge Gateway
  -> MQTT osd/state/events/reply
```

实际业务逻辑、真实硬件协议、真实负载 SDK 和生产级云端凭证后续再补。

## 当前目标

| 项目 | 当前结论 |
|---|---|
| 协议目标 | DJI Dock 3 上云 API |
| 运行方式 | Ubuntu 服务器内纯模拟 |
| 云端通信 | MQTT topic/method/payload/reply 对齐 Dock3 |
| 设备侧通信 | MAVLink + ArduPilot SITL + 机巢模拟器 |
| 数据存储现状 | SQLite + SQLAlchemy/aiosqlite |
| 数据存储目标 | MongoDB 服务型 NoSQL，后续通过异步数据访问层接入 |
| 当前重点 | 多人协同、全链路联调、Dock1 -> Dock3 协议迁移 |

## 人员分工

| 人员 | 负责范围 | 主要交付 |
|---|---|---|
| 人员1 | 上云 API 与网关之间的双向通信 | Dock3 MQTT topic、method、payload、reply、osd、state、events、requests、drc 验收 |
| 人员2 | MAVLink 与网关之间的双向通信 | SITL、TelemetryModule、FlightControl、DroneNestModule、MAVLink 消息映射与非阻塞分发 |
| 人员3 | 模拟器和联调环境部署 | 上云 API 模拟器、MAVLink 机巢模拟器、MQTT Broker、Web 调试台、启动脚本 |

协作时以文档中的数据契约为准：协议字段变更先更新 Dock3 迁移文档，MAVLink 字段变更先更新机巢映射文档，部署命令变更先更新部署计划。

## 文档索引

| 文档 | 用途 |
|---|---|
| `docs/SYSTEM_OVERVIEW.md` | 系统总览、数据流、非阻塞架构、MongoDB 目标架构 |
| `docs/PROJECT_FILE_GUIDE.md` | 项目目录和关键文件职责说明 |
| `docs/TEAM_COLLABORATION_GUIDE.md` | 三人协同边界、交接接口、冲突规避 |
| `docs/DJI_DOCK3_MIGRATION_GUIDE.md` | Dock1 -> DJI Dock 3 迁移索引和工作方法 |
| `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` | Dock3 全量分组迁移台账和当前进度 |
| `docs/DOCK3_MODULE_STATUS.md` | 当前已完成/未完成模块状态总表、验收命令和下一批建议 |
| `docs/INTERFACE_DESIGN_SPEC.md` | 接口设计说明书：P0 字段映射、API 定义和 DJI 错误码规则 |
| `docs/UBUNTU_DEPLOYMENT_AND_SITL_PLAN.md` | Ubuntu 部署、SITL、模拟链路和阶段进度 |
| `docs/UBUNTU_SERVER_STARTUP_GUIDE.md` | Ubuntu 服务器已有内容、从头启动、验收和故障处理 |
| `docs/DJI_CLOUD_API_DEMO_CONNECTION_PROGRESS.md` | DJI Cloud API Demo Docker 接入记录、当前在线状态和后续事项 |
| `docs/DRONE_NEST_MAVLINK_MAPPING.md` | 自定义机巢 MAVLink 消息字段映射 |
| `DJI_CLOUD_API_INTERFACES.md` | 当前 MQTT method 分组和 Dock3 验收状态 |
| `ARCHITECTURE_DESIGN.md` | 面向 Dock3 纯模拟目标的系统架构说明 |
| `web/README.md` | 独立 Web 调试台使用说明 |

## 快速启动顺序

Ubuntu 首次部署和依赖安装见 `docs/UBUNTU_DEPLOYMENT_AND_SITL_PLAN.md`。开发人员接手服务器、从开机状态启动完整链路时，优先看 `docs/UBUNTU_SERVER_STARTUP_GUIDE.md`。日常联调时按以下顺序启动：

```bash
# 1. MQTT Broker
sudo systemctl restart mosquitto

# 2. Edge Gateway
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml

# 3. Web 调试台，可从页面启动 SITL
cd ~/lxz/edge_gateway
source .venv/bin/activate
SITL_PYTHON=/usr/bin/python3 ARDUPILOT_DIR=$HOME/lxz/ardupilot python web/app.py
```

浏览器打开：

```text
http://Ubuntu服务器IP:8090
```

## 核心模块

| 模块 | 说明 |
|---|---|
| `src/main.py` | 网关主入口，初始化配置、MQTT、MAVLink、业务模块并注册 method |
| `src/mqtt/` | MQTT 客户端和 method 路由 |
| `src/cloud_api/` | 上云 API 生命周期、遥测上报、机场管理、能力上报 |
| `src/core/` | MAVLink 连接、消息缓存、消息分发和观察者基类 |
| `src/modules/` | 当前 MAVLink 主联调路径，包含遥测、飞控、机巢、航点、RTK 等模块 |
| `src/device/` | 设备物模型、绑定、OTA、HMS、配置和拓扑管理 |
| `src/mission/` | 航线任务、飞行任务、指令飞行和任务状态机 |
| `src/media/` | 媒体文件、直播、上传队列和存储配置 |
| `src/drc/` | 指令飞行和 DRC 实时控制 |
| `src/payload/` | 相机、云台和第三方负载接口 |
| `src/database/` | 当前 SQLite/SQLAlchemy 数据层，后续迁移到 MongoDB 目标抽象 |
| `src/nest_simulator.py` | MAVLink 机巢模拟器 |
| `tools/` | Ubuntu 预检、MQTT 冒烟测试、Dock3 P0/DRC/organization 批量验收工具 |
| `web/` | 独立 Web 调试台，不嵌入主网关 |

## 非阻塞架构要求

网关中的核心状态机、MQTT 回调、MAVLink 接收分发和业务调度必须保持流畅。以下操作不得阻塞热路径：

- 数据库写入、查询和清理
- 文件上传、下载、扫描和归档
- 日志压缩、日志上传和远程日志拉取
- 固件升级、外部命令、模拟器启动
- 媒体处理、直播推流和大文件操作

后续实现必须使用异步任务、后台线程、队列或事件循环调度，并保留超时、重试、降级和操作记录。

## 当前进度

已完成：

- Ubuntu 部署基础链路验证
- Mosquitto 本地 MQTT 联通
- ArduPilot SITL 联通
- 网关连接 MQTT 和 MAVLink
- MQTT `osd/state/events/services_reply` 基础流转
- Web 调试台初版
- Dock1 -> Dock3 目标确认

下一阶段：

- 建立 DJI Dock 3 协议矩阵
- 对齐当前 224 个已注册 method 的 Dock3 状态
- 固化上云 API 模拟器和机巢模拟器
- 补齐 request trace、MongoDB 目标架构和自动化 smoke test
