# 多人协同开发指南

本文档定义 3 名开发人员在当前阶段的职责边界、交接接口和验收方式。当前阶段以客户需求为目标开发，最终协议口径是 DJI Dock 3。30 天内 P0/P1 必须 100% 可用，P2 允许存在已记录问题但不能影响 P0/P1 主链路。

## 1. 当前协同目标

```text
客户需求 / Dock3 上云 API 模拟器
  <-> MQTT Broker
  <-> Edge Gateway
  <-> MAVLink / ArduPilot SITL / 机巢 v1.3 / 媒体与负载 mock
  <-> Edge Gateway
  <-> MQTT osd / state / events / reply / drc/up
```

当前不接实体设备。所有联调结果以模拟环境、MQTT/MAVLink 数据流、验收脚本、日志、trace 和数据库记录为准。DJI Demo/Web 页面只作为内部联调参考，不作为客户交付项。

## 1.1 当前执行口径

| 文档 | 用途 |
|---|---|
| `docs/CUSTOMER_REQUIREMENTS_COVERAGE_MATRIX.md` | 客户需求覆盖口径 |
| `docs/P0_P1_P2_DELIVERY_BACKLOG.md` | P0/P1/P2 功能 backlog 和交付证据 |
| `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md` | P0/P1 代码缺口、必补脚本和目标窗口 |
| `docs/30_DAY_MILESTONE_PLAN.md` | 30 天阶段计划 |
| `docs/30_DAY_EXECUTION_BOARD.md` | 日级执行看板、人员分工和每日证据要求 |
| `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` | 全链路启动、验收命令和证据归档 |

执行规则：

- P0 失败时不进入 P1 大规模开发。
- P1 未形成主链路验收证据时，不把主要资源投入 P2 深度优化。
- P2 可以有问题，但每个问题必须记录影响范围、绕过方式、后续计划和是否影响 P0/P1。
- 客户暂未提及、当前业务未用到的 Dock3 能力可以标为 Deferred，不抢占 P0/P1 开发资源。
- 新增或修改云端字段、method、reply、错误码，必须以 Dock3 文档或客户 adapter 需求为依据。

## 2. 人员职责

| 人员 | 负责范围 | 不负责范围 |
|---|---|---|
| 人员1 | Dock3 上云协议、MQTT topic/method/payload/reply、错误码、request trace、P0/P1 上云专项验收 | MAVLink 底层连接、SITL 参数、机巢自定义 MAVLink 字段 |
| 人员2 | MAVLink/SITL、飞控模式、Telemetry/FlightControl/Wayline、DroneNestModule、机巢 v1.3 映射、非阻塞分发 | Dock3 官方字段最终解释、Demo Docker 部署 |
| 人员3 | Ubuntu/Docker/MQTT/模拟器启动、脚本化验收、证据归档、端口和环境固化 | Dock3 字段语义、飞控和机巢业务映射最终语义、客户未要求的界面交付 |

## 3. 人员1：上云 API 与网关

### 主要目标

让 Dock3 云端模拟器和网关完成可验收的双向通信，保证 P0/P1 云端协议可用。DJI Demo 可辅助联通排查，但不作为交付目标。

### 负责内容

- 整理 Dock3 文档中的 properties、device、organization、live、media、wayline、hms、cmd、firmware、log、config、drc 等接口。
- 维护 `docs/DJI_DOCK3_MIGRATION_GUIDE.md` 中的 Dock3 协议矩阵。
- 维护 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` 中的客户需求分组迁移台账。
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
- 维护 P0/P1 云端专项脚本和 runbook 里的云端验收项。

### 交付物

| 交付物 | 验收标准 |
|---|---|
| Dock3 method 矩阵 | 每个客户需求相关 method 有文档来源、方向、topic、请求体、响应体、当前实现状态；客户未用 method 标 Deferred |
| 云端模拟器请求样例 | 每个核心 method 至少有一个可复现 MQTT payload |
| reply 验收规则 | 成功、失败、未知方法、超时都有明确期望 |
| request trace 方案 | 可复盘每次 API 调用的 request、dispatch、execute、reply、error |
| P0/P1 验收证据 | P0/P1 每个云端功能都有脚本输出、MQTT 样例、日志、trace 或 DB 证据 |

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

让 ArduPilot SITL、MAVLink 机巢 v1.3 模拟器与网关完成稳定双向通信，并保证 MAVLink 接收和分发不阻塞。

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
- 对 P0/P1 中的飞控、航线、DRC、一键启航和机巢状态机提供可执行证据。

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
| P0/P1 设备侧证据 | SITL、机巢 v1.3 和一键启航关键路径有脚本或日志证明 |

## 5. 人员3：部署与模拟器

### 主要目标

提供可重复启动、可观察、可验收的服务器内模拟联调环境，并负责 runbook 证据归档。

### 负责内容

- Ubuntu 部署和依赖安装。
- MQTT Broker 启动和端口检查。
- ArduPilot SITL 启停。
- 上云 API 模拟器部署和请求样例维护。
- MAVLink 机巢模拟器部署。
- smoke test 工具维护：
  - `tools/ubuntu_preflight.sh`
  - `tools/mqtt_smoke_test.py`
- P1 缺失验收脚本的工程化补齐：
  - `tools/nest_v13_protocol_test.py`
  - `tools/mqtt_dock3_media_test.py`
  - `tools/mqtt_dock3_live_test.py`
  - `tools/mqtt_dock3_hms_log_test.py`
  - `tools/mqtt_dock3_firmware_config_test.py`
  - `tools/e2e_takeoff_flow_test.py`
  - `tools/e2e_full_chain_test.py`
- 维护 `docs/UBUNTU_DEPLOYMENT_AND_SITL_PLAN.md`。

### 交付物

| 交付物 | 验收标准 |
|---|---|
| 部署命令 | 新 Ubuntu 环境可按文档启动 broker、网关、SITL 和必要模拟器 |
| 模拟器启动脚本或说明 | 人员1/2 无需猜端口和启动顺序 |
| smoke test | 可验证 MQTT 下发、reply、osd、state、events |
| 故障排查说明 | 端口占用、SITL 依赖、MQTT 不通、MAVLink 无心跳有处理步骤 |
| 全链路证据包 | 按 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` 保存脚本输出、日志、MQTT 样例、trace 和 DB 记录 |

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
| P0/P1/P2 优先级或验收状态 | `docs/P0_P1_P2_DELIVERY_BACKLOG.md` 和 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` |
| Dock3 字段、method、topic、枚举、错误码 | `docs/DOCK3_FULL_MIGRATION_PROGRESS.md`、`docs/DJI_DOCK3_MIGRATION_GUIDE.md` 和 `DJI_CLOUD_API_INTERFACES.md` |
| MAVLink 自定义消息字段 | `docs/DRONE_NEST_MAVLINK_MAPPING.md` |
| 启动命令、端口、部署依赖 | `docs/UBUNTU_DEPLOYMENT_AND_SITL_PLAN.md` |
| 模块职责或目录结构 | `docs/PROJECT_FILE_GUIDE.md` |
| 主线程、异步化、数据存储架构 | `docs/SYSTEM_OVERVIEW.md` 和 `ARCHITECTURE_DESIGN.md` |

## 8. 当前并行推进建议

| 周期 | 人员1 | 人员2 | 人员3 |
|---|---|---|---|
| Day 1-7 | P0 云端协议、properties、device、organization、错误码和 trace | P0 OSD、模式映射、基础飞控控制、机巢关键状态 | P0 脚本、模拟器启动顺序和证据归档 |
| Day 8-15 | P1 Wayline/DRC/一键启航云端状态和事件 | 机巢 v1.3 状态机、SITL 航点执行、起降/返航联动 | 机巢专项和一键启航 E2E 脚本 |
| Day 16-24 | Media/Live/HMS/Log/Firmware/Config Dock3 payload 和错误码 | 负载、媒体源、云台相机基础 adapter | P1 缺失脚本、mock 服务和回归入口 |
| Day 25-30 | P0/P1 全链路回归、P2 风险归档 | 设备侧失败原因、超时恢复和非阻塞审计 | 全链路 runbook 执行、证据包和发布说明 |

## 9. 每日同步最小信息

每次同步只需要汇报：

- 今天推进了哪些 P0/P1/P2 backlog 项。
- 今天验证了哪些 topic/method。
- 成功的输入 payload 和输出 reply。
- 失败的 tid/bid、错误日志和复现命令。日志中字段应优先保留 `字段名(中文名)`，例如 `drc_state(DRC链路状态)`、`capacity_percent(电量百分比)`。
- 是否影响 P0/P1 交付门禁。
- 是否产生 P2 已知问题和绕过方式。
- 是否影响其他人员的数据契约。
- 是否需要更新文档。
