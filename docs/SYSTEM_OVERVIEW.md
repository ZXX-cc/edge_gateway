# 系统总览

本文档面向多人协同开发，说明 Edge Gateway 当前系统边界、核心数据流、非阻塞架构约束和数据存储目标。

## 1. 项目定位

Edge Gateway 当前目标是适配 DJI Dock 3 上云 API，在 Ubuntu 服务器内跑通纯模拟链路：

```text
上云 API 模拟器
  -> MQTT Broker
  -> Edge Gateway
  -> MAVLink / ArduPilot SITL / 机巢模拟器
  -> Edge Gateway
  -> MQTT osd/state/events/reply
```

当前不接实体设备。真实无人机、真实飞控串口、真实机巢 SDK、真实负载 SDK 均不属于当前阶段。

## 2. 系统分层

| 层级 | 主要文件 | 职责 |
|---|---|---|
| 入口与配置层 | `src/main.py`, `config/gateway.yaml`, `start.sh` | 加载配置、初始化模块、注册 MQTT method、管理生命周期 |
| 云端协议层 | `src/mqtt/`, `src/cloud_api/`, `src/utils/message.py` | 处理 Dock3 MQTT topic、消息构造、路由、上报和请求应答 |
| 业务调度层 | `src/device/`, `src/mission/`, `src/media/`, `src/drc/`, `src/payload/` | 承接 services/property/drc 请求，管理业务状态和回包 |
| MAVLink 核心层 | `src/core/`, `src/modules/` | MAVLink 连接、消息接收、缓存、分发、遥测、飞控、机巢模块 |
| 模拟器层 | `src/nest_simulator.py`, `web/`, ArduPilot SITL | 提供无人机、机巢、云端调试和可视化验证能力 |
| 数据与容错层 | `src/database/`, 后续 MongoDB 数据访问层 | 记录遥测、操作、告警、媒体、设备状态和 request trace |

## 3. 核心数据流

### 3.1 云端下行

```text
上云 API 模拟器
  -> thing/product/{sn}/services 或 property/set 或 drc/down
  -> MQTTClient._on_message
  -> TopicRouter.route(method)
  -> 对应业务 manager/controller
  -> MAVLink 命令、模拟状态更新或后台任务
  -> services_reply / state / events / drc/up
```

人员1主要负责该链路的 Dock3 topic、method、payload、reply 和错误码对齐。

### 3.2 MAVLink 上行

```text
ArduPilot SITL / 机巢模拟器
  -> MAVLinkConnection.recv_message
  -> MessageDispatcher._receive_loop
  -> MessageCache
  -> TelemetryModule / FlightControl / DroneNestModule
  -> TelemetryProcessor / DockManager / DeviceManager
  -> MQTT osd/state/events/reply
```

人员2主要负责该链路的连接稳定性、消息映射、状态同步和非阻塞接收分发。

### 3.3 模拟器与联调环境

```text
人员3启动:
  MQTT Broker
  Edge Gateway
  ArduPilot SITL
  MAVLink 机巢模拟器
  上云 API 模拟器 / Web 调试台
```

人员3负责保证环境可重复启动，并用 smoke test 验证基础链路。

## 4. 主线程与非阻塞要求

核心原则：MQTT 回调、MAVLink 接收线程、核心状态机、业务调度入口不得执行耗时 IO。

必须后台化或异步化的操作：

| 操作 | 要求 |
|---|---|
| 数据库存储 | 使用异步数据访问层或后台写入队列 |
| 文件上传/下载 | 后台任务执行，主流程只创建任务和返回操作 ID |
| 媒体扫描/转码/直播推流 | 独立线程或子进程，状态通过事件回传 |
| 固件升级 | 后台任务执行，持续写 operation record 和进度事件 |
| 日志归档/上传 | 后台队列，避免阻塞 MQTT 和 MAVLink |
| 外部命令 | 子进程后台运行，必须设置超时、退出码记录和输出截断 |
| 云端请求等待 | 使用 request trace 和超时回调，不在热路径阻塞等待 |

后续业务代码必须具备：

- `operation_id` 或 `tid` 级别的可追踪记录
- 明确的 started/progress/completed/failed/timeout 状态
- 超时和错误原因
- 重试或降级策略
- 不影响 MQTT 回调和 MAVLink 接收循环

## 5. 数据存储现状与目标

### 当前实现

当前仓库的数据层位于 `src/database/`，使用：

- SQLite
- SQLAlchemy ORM
- aiosqlite / SQLAlchemy AsyncSession
- WAL、保留策略和异步清理任务

当前模型包括：

| 模型 | 用途 |
|---|---|
| `Mission`, `Waypoint`, `MissionProgress` | 航线任务和断点 |
| `DeviceState` | 设备和机巢状态 |
| `Alarm` | HMS/告警 |
| `DeviceBinding` | 绑定和组织状态 |
| `TelemetryData` | 遥测 |
| `OperationRecord` | 操作记录 |
| `MediaFile` | 媒体文件 |

### 目标架构

目标本地 NoSQL 采用 MongoDB 服务型存储。当前不接入 MongoDB，只在架构上预留。

后续建议建立统一异步数据访问层，至少包含这些集合：

| 集合 | 说明 |
|---|---|
| `telemetry` | OSD 和关键遥测快照 |
| `device_state` | Dock3 设备状态和属性缓存 |
| `operation_record` | 命令、任务、机巢动作、固件升级等操作记录 |
| `alarm` | HMS、飞控、机巢、网关告警 |
| `media_file` | 媒体文件、上传状态、直播关联 |
| `request_trace` | MQTT tid/bid、method、topic、payload、reply、耗时、错误 |
| `simulator_state` | 云端模拟器、SITL、机巢模拟器状态 |

迁移原则：

- 业务模块不得直接依赖 MongoDB 驱动细节。
- 先抽象数据访问接口，再从 SQLite 迁移到 MongoDB。
- 写入失败不能阻塞主链路，必须进入重试队列或降级日志。
- request trace 必须能复盘每次 Dock3 method 的 request、dispatch、execute、reply、error。

## 6. 当前已知差距

| 差距 | 说明 |
|---|---|
| Dock3 未完成 | 当前代码仍保留 Dock/Dock1 风格字段和注释 |
| P0/P1 业务闭环待补 | 30 天内 P0/P1 必须按 `docs/P0_P1_P2_DELIVERY_BACKLOG.md` 和 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` 达到可用 |
| NoSQL 未接入 | 当前仍是 SQLite，MongoDB 是目标架构 |
| 机巢模拟未完全合流 | SITL 与机巢模拟器同时联调仍需端口策略或路由器 |
| README 旧信息已清理 | 新 README 已改为多人协同入口 |

## 7. 当前验收标准

基础链路验收：

- MQTT Broker 可启动并收发 `thing/product/{sn}/#`。
- Edge Gateway 可启动并注册全部 method。
- SITL 可启动并让网关收到 heartbeat、GPS、姿态、电池等 MAVLink 数据。
- MQTT 可持续看到 `osd`、`state`、`events`。
- `services` 下发 method 后能收到 `services_reply`。
- 文档能让人员1、人员2、人员3独立定位自己的接口和联调入口。

客户需求和 P0/P1/P2 优先级以 `docs/P0_P1_P2_DELIVERY_BACKLOG.md` 为准。全链路启动、验收命令和证据归档以 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` 为准。Dock3 字段迁移细节以 `docs/DJI_DOCK3_MIGRATION_GUIDE.md` 和 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` 为准。
