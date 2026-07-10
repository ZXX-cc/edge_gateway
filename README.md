# Edge Gateway

Edge Gateway 是一个适配 DJI Dock 3 上云 API 的 Python 边缘网关项目。当前阶段目标是在 Ubuntu 服务器内跑通纯模拟全链路，不接实体无人机、飞控、机巢或负载设备。

当前优先级按客户需求和 P0/P1/P2 backlog 推进。30 天内 P0/P1 必须 100% 可用，P2 可以存在已记录问题但不能影响主链路：

```text
上云 API 模拟器
  -> MQTT Broker
  -> Edge Gateway
  -> MAVLink / SITL / 机巢模拟器
  -> Edge Gateway
  -> MQTT osd/state/events/reply
```

当前不接实体设备。真实硬件协议、真实负载 SDK 和生产级云端凭证通过 adapter、mock 或 P2 风险归档处理；P0/P1 业务闭环不能因此空缺。

## 当前目标

| 项目 | 当前结论 |
|---|---|
| 协议目标 | DJI Dock 3 上云 API |
| 运行方式 | Ubuntu 服务器内纯模拟 |
| 云端通信 | MQTT topic/method/payload/reply 对齐 Dock3 |
| 设备侧通信 | MAVLink + ArduPilot SITL + 机巢模拟器 |
| 数据存储现状 | SQLite + SQLAlchemy/aiosqlite |
| 数据存储目标 | MongoDB 服务型 NoSQL，后续通过异步数据访问层接入 |
| 客户需求口径 | 客户文档定义业务能力全集，旧 Dock/Dock1/Dock2 表述只做业务意图和差异对照 |
| 最终协议口径 | 客户需求涉及的上云能力必须按 DJI Dock 3 官方 Cloud API 定义 MQTT topic、method、payload、reply、字段、枚举和验收 |
| 迁移边界 | 客户暂未提及、当前业务未用到的 Dock3 官方能力可以延期迁移，但必须在 method 台账或 P2 风险中标明 Deferred/Adapter 状态 |
| 当前重点 | 多人协同、全链路联调、客户需求到 Dock3 的按需协议迁移 |

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
| `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` | Dock3 客户需求迁移台账和当前进度 |
| `docs/DOCK3_MODULE_STATUS.md` | 当前已完成/未完成模块状态总表、验收命令和下一批建议 |
| `docs/CUSTOMER_REQUIREMENTS_COVERAGE_MATRIX.md` | 客户需求覆盖矩阵：把客户 Word 文档归一到 Dock3 开发口径 |
| `docs/CUSTOMER_REQUIREMENTS_TRACEABILITY_MATRIX.md` | 客户需求可追溯矩阵：章节 -> Dock3 落点 -> P0/P1/P2 -> 缺口 -> 验收证据 |
| `docs/METHOD_COVERAGE_LEDGER.md` | MQTT method 覆盖台账：当前 243 个注册 method 的分组、P0/P1/P2 归类和关项要求 |
| `docs/P0_P1_P2_DELIVERY_BACKLOG.md` | P0/P1/P2 交付 backlog：功能优先级、当前状态、30 天交付要求和验收证据 |
| `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md` | P0/P1 实现缺口登记：代码入口、缺口、验收脚本和目标窗口 |
| `docs/30_DAY_MILESTONE_PLAN.md` | 30 天里程碑计划：P0/P1 100% 可用、P2 风险归档、100% 开发完成和全链路验收安排 |
| `docs/30_DAY_EXECUTION_BOARD.md` | 30 天开发执行看板：日级任务、人员分工、缺失脚本顺序和每日证据要求 |
| `docs/DELIVERY_ACCEPTANCE_SCORECARD.md` | 30 天交付验收评分卡：44 项 backlog、90% 可用率、100% 开发完成和 P0/P1 强门禁 |
| `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` | 全链路验收 Runbook：启动顺序、P0/P1 验收命令、P2 边界和证据归档 |
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

- Ubuntu 服务器内 Dock3 纯模拟链路已形成当前基线，整体进度约 81%。
- P0/P1 当前 34 项门禁中已有 25 项可用；评分卡口径见 `docs/DELIVERY_ACCEPTANCE_SCORECARD.md`。
- MQTT topic/reply、设备上线、组织绑定、拓扑、properties、OSD/state、错误码、request trace 已形成可重复脚本证据。
- CMD、DRC、Wayline、Media、Live、HMS/log、firmware/config 已有专项脚本通过记录。
- `tools/e2e_full_chain_test.py --timeout 20 --quiet-stale --fail-on-stale` 已在 Ubuntu 严格通过 properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config 11 段，输出 `steps=11, elapsed=26.7s`。
- DRC `takeoff_to_point` 和 `fly_to_point_update` 已用 `property_get` 网关遥测生成目标点并返回 `simulated=false`；共享 MQTT 测试客户端已输出 `stale_samples` 定位旧回包。
- P0-16 文件 IO 后台化已通过 Ubuntu 回归：回调单测 2/2、HMS/日志 `accepted=8, events=5`、媒体 `accepted=6, events=2`。严格 properties 的旧回包已通过 broker 审计定位为 `192.168.2.94` 上的旧模拟器客户端 `dock3_sim_EDGEGW001_1783480416`；停止后已在 Ubuntu 通过严格回归：`accepted=10`、全部 `ignored_*=0`。
- 机巢模拟器已修复 UDP 注册和默认待命状态；Ubuntu 严格链路已通过 properties、device、OSD/state、CMD、DRC、Wayline 六段，其中 Wayline 输出 `accepted=11, events=12, ignored_total=0`，并实际完成 12003 命令、12004 状态、12005 回复的联调。真实 SITL 航点执行仍不在本次结论内。
- P1-16 短周期数据保留与操作记录已通过 Ubuntu 隔离 SQLite 专项：`tools/database_retention_audit.py` 验证遥测、已结束操作、告警、已上传媒体各清理 1/2/1/1 条，运行中操作与待上传媒体均保留；`tests.test_retention_manager` 同时通过。专项不会访问运行中的 `missions.db`。
- P1-09 相机/云台已通过 Ubuntu 严格 MQTT 专项：能力查询、云台转动/模式/复位状态回读、拍照、录像启停和重复停止录像失败码均正确，输出 `accepted=12`、全部 `ignored_*=0`。该证据覆盖 MAVLink/SITL 控制链路；真实 Payload 硬件 ACK 仍需后续适配验收。
- P1-06 已完成真实 SITL 航点上传阶段：`--real-sitl` 模式在 Ubuntu 将 KMZ 解析为 3 个航点并完成飞控上传握手，`flighttask_prepare result=0`、`accepted=1`、全部 `ignored_*=0`；尚未请求 AUTO/执行，下一步单列验证真实执行与进度。

下一阶段：

- 关闭 P0/P1 剩余强门禁：SITL 模式/基础飞控、真实 SITL 航点执行、机巢耗时状态机和非阻塞主链路审计。
- 继续精校客户需求涉及的 Dock3 字段、枚举、单位和错误码。
- 保存严格 E2E 证据，并继续真实 SITL 航点、实体机巢和真实负载联调。
- 补齐真实 Payload 硬件 ACK adapter、P2 adapter/mock/限制说明。
