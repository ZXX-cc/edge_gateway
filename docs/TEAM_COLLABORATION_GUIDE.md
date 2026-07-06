# 多人协同开发指南

本文档定义 3 名开发人员在当前阶段的职责边界、交接接口和验收方式。当前阶段优先全链路调通，实际业务逻辑后续补齐。

## 1. 当前协同目标

```text
Dock3 上云 API 模拟器
  <-> MQTT Broker
  <-> Edge Gateway
  <-> MAVLink / ArduPilot SITL / 机巢模拟器
```

当前不接实体设备。所有联调结果以模拟环境和 MQTT/MAVLink 数据流为准。

## 2. 人员职责

| 人员 | 负责范围 | 不负责范围 |
|---|---|---|
| 人员1 | 上云 API 与网关通信、Dock3 协议字段、MQTT topic/method/payload/reply、云端模拟器验收 | MAVLink 底层连接、SITL 参数、机巢自定义 MAVLink 字段 |
| 人员2 | MAVLink 与网关通信、SITL、TelemetryModule、FlightControl、DroneNestModule、MAVLink 映射、非阻塞分发 | Dock3 官方字段解释、云端模拟器页面、部署脚本 |
| 人员3 | Ubuntu 部署、MQTT Broker、上云 API 模拟器、MAVLink 机巢模拟器、Web 调试台、启动脚本、smoke test | 网关业务逻辑实现、Dock3 字段最终语义、MAVLink 业务映射最终语义 |

## 3. 人员1：上云 API 与网关

### 主要目标

让 Dock3 云端模拟器与网关完成双向通信。

### 负责内容

- 整理 Dock3 文档中的 properties、device、organization、live、media、wayline、hms、cmd、firmware、log、config、drc 等接口。
- 维护 `docs/DJI_DOCK3_MIGRATION_GUIDE.md` 中的 Dock3 协议矩阵。
- 维护 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` 中的全量分组迁移台账。
- 维护 `DJI_CLOUD_API_INTERFACES.md` 中各 method 的 Dock3 验收状态。
- 验证 MQTT topic：
  - `thing/product/{sn}/services`
  - `thing/product/{sn}/services_reply`
  - `thing/product/{sn}/requests`
  - `thing/product/{sn}/requests_reply`
  - `thing/product/{sn}/events`
  - `thing/product/{sn}/events_reply`
  - `thing/product/{sn}/osd`
  - `thing/product/{sn}/state`
  - `thing/product/{sn}/drc/down`
  - `thing/product/{sn}/drc/up`
- 定义 request trace 字段：`tid`、`bid`、`method`、`topic`、请求 payload、响应 payload、耗时、结果码、错误原因。

### 交付物

| 交付物 | 验收标准 |
|---|---|
| Dock3 method 矩阵 | 每个 method 有文档来源、方向、topic、请求体、响应体、当前实现状态 |
| 云端模拟器请求样例 | 每个核心 method 至少有一个可复现 MQTT payload |
| reply 验收规则 | 成功、失败、未知方法、超时都有明确期望 |
| request trace 方案 | 可复盘每次 API 调用的 request、dispatch、execute、reply、error |

### 2026-07-06 当前进展

| 项目 | 当前结论 | 后续动作 |
|---|---|---|
| `services -> services_reply` | 已验证可闭环，`dock_status_query`、`airport_bind_status` 均能收到 `result=0` 回包 | 对照 Dock3 设备管理/组织管理文档校验字段 |
| `osd` 上报 | 已验证持续输出 SITL 遥测 | 对照 Dock3 properties/osd 字段名、单位和枚举 |
| `property/set` | 已通过 Ubuntu 服务器复测，`property_set` 返回 `property/set_reply`，`result=0` | 继续对照 Dock3 properties payload 结构 |
| properties Batch 1 | Ubuntu 已验证：`air_transfer_enable`、`silent_mode`、`user_experience_improvement` 可通过 `property/set`；`battery_store_mode`、`alarm_state` 返回只读失败 | 人员1继续补全 `property_get` 回读、值域校验和 `osd/state` 字段矩阵 |
| `drc_mode_enter/exit` | Ubuntu 复测通过 Dock3 `services -> services_reply`；错误发送到 `drc/down` 应返回 DJI 错误码 `325001` | 人员2继续核对 MAVLink 执行状态和飞控模式映射 |
| `drc/down` | Dock3 中用于 DRC 建链后的控制数据，例如 `stick_control`、`drone_control`、`payload_control` | 人员1补 DRC 控制 payload；人员2核对 MAVLink 控制执行 |
| 状态一致性 | `dock_status_query.drone_status` 已同步 OSD 实时遥测，包含 `online=true`、经纬度、高度、电量、模式和 GPS | 继续对照 Dock3 机场/无人机状态字段 |
| DRC 状态交接项 | Ubuntu 复测通过：`drc_mode_enter` 后 `property_get(drc_state)=2`，`drc_mode_exit` 后 `property_get(drc_state)=0`；飞控 OSD `mode_code/gear` 仍需核对 | 人员2核对 DRC 进入后飞控模式切换、解锁状态和 `mode_code` 映射 |
| 日志追踪 | 日志按 `logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log` 落盘，关键 MQTT 字段带 `字段名(中文名)` | 人员1按 `tid/bid` 追踪协议字段，人员2按任务/飞控关键日志追踪执行过程 |

## 4. 人员2：MAVLink 与网关

### 主要目标

让 ArduPilot SITL、MAVLink 机巢模拟器与网关完成稳定双向通信，并保证 MAVLink 接收和分发不阻塞。

### 负责内容

- 维护 MAVLink 连接：
  - `src/core/connection.py`
  - `src/core/message_dispatcher.py`
  - `src/core/message_cache.py`
- 维护 MAVLink 功能模块：
  - `src/modules/telemetry_module.py`
  - `src/modules/flight_control.py`
  - `src/modules/mode_control.py`
  - `src/modules/drone_nest_module.py`
  - `src/modules/waypoint_manager.py`
- 维护遥测到上云状态的输入数据：
  - 经纬度
  - 高度
  - 姿态
  - 速度
  - 电池
  - GPS
  - 解锁状态
  - 飞行模式
  - 机巢状态
- 维护 `docs/DRONE_NEST_MAVLINK_MAPPING.md`。
- 修复或标记 ArduCopter/ArduPlane/QuadPlane 模式表差异。

### 非阻塞要求

- MAVLink 接收线程只做接收、轻量解析、缓存和分发。
- 耗时任务通过后台线程、队列或事件循环调度。
- handler 不直接等待长时间 ACK。
- 所有命令需要超时和失败原因。

### 交付物

| 交付物 | 验收标准 |
|---|---|
| MAVLink 联调说明 | SITL、机巢模拟器、端口和启动顺序清晰 |
| 遥测映射表 | MAVLink 字段到网关内部状态再到 MQTT 字段路径清晰 |
| 飞控命令状态 | GUIDED、ARM、TAKEOFF、LAND、RTL 可模拟验证 |
| 机巢命令状态 | 开盖、关盖、充电、推杆、环境状态可模拟验证 |
| 性能约束 | 接收分发循环不因数据库、日志、文件、网络 IO 阻塞 |

## 5. 人员3：部署与模拟器

### 主要目标

提供可重复启动、可观察、可 smoke test 的服务器内模拟联调环境。

### 负责内容

- Ubuntu 部署和依赖安装。
- MQTT Broker 启动和端口检查。
- ArduPilot SITL 启停。
- 上云 API 模拟器部署和请求样例维护。
- MAVLink 机巢模拟器部署。
- Web 调试台启动和说明。
- smoke test 工具维护：
  - `tools/ubuntu_preflight.sh`
  - `tools/mqtt_smoke_test.py`
  - `web/`
- 维护 `docs/UBUNTU_DEPLOYMENT_AND_SITL_PLAN.md`。

### 交付物

| 交付物 | 验收标准 |
|---|---|
| 部署命令 | 新 Ubuntu 环境可按文档启动 broker、网关、SITL、Web 调试台 |
| 模拟器启动脚本或说明 | 人员1/2 无需猜端口和启动顺序 |
| smoke test | 可验证 MQTT 下发、reply、osd、state、events |
| 故障排查说明 | 端口占用、SITL 依赖、MQTT 不通、MAVLink 无心跳有处理步骤 |

## 6. 协作接口

### 6.1 人员1 与人员2

交接对象是网关内部标准状态和操作抽象，不直接互相依赖实现细节。

| 交接内容 | 由谁定义 | 由谁提供 |
|---|---|---|
| Dock3 MQTT 字段 | 人员1 | 人员1 |
| MAVLink 原始字段 | 人员2 | 人员2 |
| 内部设备状态模型 | 人员1 + 人员2 共同维护 | 网关 |
| 遥测缓存 | 人员2 | `TelemetryModule` / `TelemetryProcessor` |
| 操作记录 | 人员1 定义 API 语义，人员2 提供执行结果 | 数据访问层 |
| 错误码 | 人员1 定义云端结果，人员2 提供设备侧原因 | handler |

### 6.2 人员1 与人员3

| 交接内容 | 要求 |
|---|---|
| 云端模拟器 payload | 必须能直接发布到 MQTT services/property/drc topic |
| reply 验收 | 必须包含 tid、method、result、msg、data |
| topic 订阅 | 人员3 提供可视化和日志，人员1 判断协议正确性 |

### 6.3 人员2 与人员3

| 交接内容 | 要求 |
|---|---|
| SITL 启动 | 人员3 固化启动命令，人员2 验证 MAVLink 数据 |
| 机巢模拟器 | 人员3 固化启动方式，人员2 维护字段映射 |
| 端口策略 | 人员3 维护部署文档，人员2 确认 MAVLink 链路可用 |

## 7. 冲突规避规则

| 变更类型 | 必须先更新 |
|---|---|
| Dock3 字段、method、topic、枚举、错误码 | `docs/DOCK3_FULL_MIGRATION_PROGRESS.md`、`docs/DJI_DOCK3_MIGRATION_GUIDE.md` 和 `DJI_CLOUD_API_INTERFACES.md` |
| MAVLink 自定义消息字段 | `docs/DRONE_NEST_MAVLINK_MAPPING.md` |
| 启动命令、端口、部署依赖 | `docs/UBUNTU_DEPLOYMENT_AND_SITL_PLAN.md` |
| 模块职责或目录结构 | `docs/PROJECT_FILE_GUIDE.md` |
| 主线程、异步化、数据存储架构 | `docs/SYSTEM_OVERVIEW.md` 和 `ARCHITECTURE_DESIGN.md` |

## 8. 当前并行推进建议

| 周期 | 人员1 | 人员2 | 人员3 |
|---|---|---|---|
| 第一步 | 建 Dock3 核心 method 矩阵 | 稳定 SITL 遥测和飞控模式 | 固化 Ubuntu 启动顺序 |
| 第二步 | 编写云端模拟 payload | 完成遥测/机巢状态映射 | 固化机巢模拟器和 Web 调试台 |
| 第三步 | 批量验证 reply/osd/state/events | 验证飞控/机巢命令结果 | 整合 smoke test |
| 第四步 | 补 request trace 和错误码 | 补超时/失败原因 | 输出一键联调说明 |

## 9. 每日同步最小信息

每次同步只需要汇报：

- 今天验证了哪些 topic/method。
- 成功的输入 payload 和输出 reply。
- 失败的 tid/bid、错误日志和复现命令。日志中字段应优先保留 `字段名(中文名)`，例如 `drc_state(DRC链路状态)`、`capacity_percent(电量百分比)`。
- 是否影响其他人员的数据契约。
- 是否需要更新文档。
