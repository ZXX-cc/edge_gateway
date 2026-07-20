# Edge Gateway 系统架构设计

本文档描述当前多人协同阶段的目标架构。当前项目以 Ubuntu 服务器内纯模拟为优先目标，适配 DJI Dock 3 上云 API，不接实体设备。

## 1. 架构目标

| 项目 | 目标 |
|---|---|
| 协议目标 | DJI Dock 3 上云 API |
| 运行环境 | Ubuntu 服务器 |
| 设备侧 | ArduPilot SITL + MAVLink 机巢模拟器 |
| 云端侧 | 上云 API 模拟器 + MQTT Broker |
| 网关职责 | MQTT 与 MAVLink 双向桥接、状态聚合、回包、遥测、事件、操作记录 |
| 数据目标 | MongoDB 服务型 NoSQL，当前仍为 SQLite 迁移前状态 |
| 业务目标 | 当前先调通链路，实际业务逻辑后续补齐 |

## 2. 总体拓扑

```text
                   上云 API 模拟器
                         |
                         | MQTT Dock3 topic/method/payload
                         v
                  MQTT Broker
                         |
                         v
                Edge Gateway 主程序
       |                 |                 |
       v                 v                 v
 Cloud API /         MAVLink Core      直连负载适配器
 TopicRouter         飞控/TTL         吊舱/相机/雷达/
       |                 |             光谱/喊话器
       v                 v                 |
 MongoDB 数据层      SITL / 实机飞控          v
                                      视频/原始数据/音频
```

## 3. 分层设计

| 层级 | 主要模块 | 职责 |
|---|---|---|
| 入口配置层 | `src/main.py`, `config/gateway.yaml` | 生命周期、配置、模块注入、method 注册 |
| 云端协议层 | `src/mqtt/`, `src/cloud_api/`, `src/utils/message.py` | Dock3 MQTT topic、消息解析、路由、reply、osd/state/events |
| 业务服务层 | `src/device/`, `src/mission/`, `src/media/`, `src/drc/`, `src/payload/` | 承接 method，管理业务状态和操作进度 |
| MAVLink 核心层 | `src/core/`, `src/modules/` | MAVLink 连接、接收、缓存、分发、遥测和命令 |
| 模拟器层 | `web/`, `tools/`, `src/nest_simulator.py`, ArduPilot SITL | 提供可重复联调环境 |
| 数据容错层 | `src/database/`, 后续 MongoDB 访问层 | 记录遥测、操作、告警、媒体、状态和 request trace |

## 4. 当前主链路

### 4.1 云端到网关

```text
MQTT services/property/drc
  -> MQTTClient._on_message
  -> parse_message
  -> TopicRouter.route
  -> 对应 handler
  -> services_reply / property_reply / drc_up / events
```

人员1负责该链路的 Dock3 协议一致性。

### 4.2 MAVLink 到网关

```text
SITL / 机巢模拟器
  -> MAVLinkConnection.recv_message
  -> MessageDispatcher._receive_loop
  -> MessageCache
  -> TelemetryModule / FlightControl / DroneNestModule / WaypointManager
  -> TelemetryProcessor / DockManager / DeviceManager
  -> MQTT osd/state/events
```

人员2负责该链路的 MAVLink 稳定性、状态映射和非阻塞分发。

### 4.3 模拟器与部署

```text
人员3维护:
  MQTT Broker
  Edge Gateway 启动方式
  ArduPilot SITL
  MAVLink 机巢模拟器
  Web 调试台
  MQTT smoke test
```

人员3负责让环境可重复启动和复现。

### 4.4 实机飞控与负载链路

飞控与负载是两组独立物理链路。实机环境下，网关通过 TTL 串口向飞控发送全部飞行控制指令（包括开始传输数据）；负载不经由飞控或机巢中转。`test` 模式可继续使用 UDP 连接 SITL，但不改变负载的路由边界。

| 设备 | 直连网关通道 | 方向/时机 |
|---|---|---|
| 吊舱 | 吊舱控制、视频流 | 控制双向；视频实时上行 |
| 正射/倾斜相机 | 视频流、原始数据 | 视频实时上行；原始数据降落后传输 |
| 激光雷达 | 原始数据 | 降落后传输 |
| 多光谱/高光谱 | 视频流、原始数据 | 视频实时上行；原始数据降落后传输 |
| 喊话器 | 控制、音频 | 网关按需下行 |

运行时从 `payload.devices` 读取上述拓扑。`payload.flight_controller_fallback_enabled` 默认为 `false`；只有 SITL 兼容验证需要时才可显式开启，且机巢 MAVLink 永不作为负载回退链路。

## 5. 非阻塞架构原则

网关必须保障核心状态机、MQTT 回调、MAVLink 接收、业务调度持续流畅。

### 5.1 禁止在热路径阻塞

以下操作不得直接在 MQTT 回调、MAVLink 接收线程和核心状态机里同步执行：

- 数据库写入或复杂查询
- 文件扫描、压缩、上传、下载
- 固件升级
- 日志归档和远程日志上传
- 媒体处理、直播推流、外部命令
- 长时间等待 MAVLink ACK
- 长时间等待云端 requests_reply

### 5.2 推荐执行方式

| 场景 | 推荐方式 |
|---|---|
| 数据持久化 | 异步数据访问层或后台写入队列 |
| 命令执行 | 立即返回操作 ID，后台执行并持续上报进度 |
| 大文件任务 | 独立线程、进程或任务队列 |
| 外部命令 | 后台子进程，设置超时和输出截断 |
| 请求应答 | `tid` 追踪、超时清理、回调或事件通知 |
| 失败处理 | 写 operation record、返回错误码、保留错误原因 |

## 6. 数据架构

### 6.1 当前实现

当前仓库使用 SQLite + SQLAlchemy/aiosqlite，数据层位于 `src/database/`。

当前模型覆盖：

- 航线任务
- 航点
- 任务进度
- 设备状态
- 告警
- 设备绑定
- 遥测
- 操作记录
- 媒体文件

### 6.2 目标实现

目标为 MongoDB 服务型 NoSQL。MongoDB 当前不是已完成实现，本阶段只作为后续架构目标写入文档。

建议集合：

| 集合 | 职责 |
|---|---|
| `telemetry` | OSD 和遥测快照 |
| `device_state` | Dock3 属性和状态缓存 |
| `operation_record` | 命令、任务、升级、机巢动作的进度和结果 |
| `alarm` | HMS、飞控、网关、机巢告警 |
| `media_file` | 文件、上传状态、直播关联 |
| `request_trace` | MQTT tid/bid、method、topic、payload、reply、耗时和错误 |
| `simulator_state` | SITL、机巢模拟器、云端模拟器状态 |

迁移原则：

- 先定义数据访问接口，再替换 SQLite 实现。
- 业务模块不直接依赖 MongoDB 驱动。
- MongoDB 写失败不能阻塞主链路。
- 关键请求和操作必须可追踪、可重放、可审计。

## 7. Dock3 迁移原则

| 原则 | 要求 |
|---|---|
| 官方文档优先 | Dock3 官方文档是最终验收基准 |
| 先矩阵后编码 | 先建立 method/property/event/state/osd/reply 矩阵，再改实现 |
| 保留旧链路 | 当前已跑通的 MQTT/SITL 基础链路不直接推翻 |
| 标注旧语义 | 旧 Dock/Dock1 风格字段必须标注待迁移 |
| 模拟优先 | 所有 Dock3 method 先通过模拟器验收 |

## 8. 关键风险

| 风险 | 处理方式 |
|---|---|
| Dock1 字段误用为 Dock3 | 所有协议字段先进入迁移矩阵 |
| MQTT 回调阻塞 | handler 只做轻量校验和任务投递 |
| MAVLink 接收阻塞 | 接收线程只接收、缓存、分发 |
| SQLite 并发锁 | 当前阶段降低写入频率，后续迁移 MongoDB |
| SITL 与机巢模拟端口冲突 | 由人员3维护端口策略或引入 MAVLink Router |
| 业务逻辑未补全 | smoke test 先验链路，业务正确性后续补 |

## 9. 当前验收目标

| 验收项 | 标准 |
|---|---|
| MQTT | 能收发 services、services_reply、requests、events、osd、state、drc |
| MAVLink | 网关能收到 SITL heartbeat、GPS、姿态、电池 |
| 飞控模拟 | GUIDED、ARM、TAKEOFF、LAND、RTL 可复现 |
| 机巢模拟 | 能解析自定义机巢状态和命令响应 |
| Dock3 文档 | 19 个 Dock3 官方入口已纳入迁移指南 |
| 协作 | 三名人员能通过文档定位职责和交接接口 |
