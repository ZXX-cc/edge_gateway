# 项目文件职责说明

本文档说明当前仓库主要文件和目录职责，帮助多人协同开发时快速定位代码。运行产物、缓存文件、数据库文件和 IDE 本地配置不作为项目职责说明对象。

## 1. 根目录

| 路径 | 职责 |
|---|---|
| `README.md` | 项目入口、当前目标、人员分工和文档索引 |
| `ARCHITECTURE_DESIGN.md` | 系统架构设计说明 |
| `DJI_CLOUD_API_INTERFACES.md` | 当前 MQTT method 分组和 Dock3 验收状态 |
| `config/gateway.yaml` | 网关运行配置，包含 gateway、MQTT、cloud、MAVLink、media、logging、retention 等配置 |
| `nanomq.conf` | NanoMQ 配置模板；Ubuntu 22.04 当前可用 Mosquitto 替代 |
| `requirements-ubuntu.txt` | Ubuntu 部署使用的 Python 依赖 |
| `requirements.txt` | 原依赖文件，存在历史编码风险，Ubuntu 默认不用它 |
| `start.sh` | 生产/半自动启动脚本，当前仍偏 NanoMQ 和服务化部署 |

## 1.1 规划与验收文档

| 路径 | 职责 |
|---|---|
| `docs/CUSTOMER_REQUIREMENTS_COVERAGE_MATRIX.md` | 客户需求覆盖矩阵，说明客户 Word 文档如何归一到 Dock3 开发口径 |
| `docs/CUSTOMER_REQUIREMENTS_TRACEABILITY_MATRIX.md` | 客户需求可追溯矩阵，说明客户章节如何落到 Dock3 落点、P0/P1/P2、缺口和验收证据 |
| `docs/METHOD_COVERAGE_LEDGER.md` | MQTT method 覆盖台账，说明当前 243 个注册 method 如何归入 P0/P1/P2 和验收域 |
| `docs/P0_P1_P2_DELIVERY_BACKLOG.md` | P0/P1/P2 交付 backlog，定义功能优先级、当前状态、30 天交付要求和验收证据 |
| `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md` | P0/P1 实现缺口登记，绑定代码入口、必补脚本、目标窗口和关项规则 |
| `docs/30_DAY_MILESTONE_PLAN.md` | 30 天里程碑计划，定义阶段节奏和 P0/P1/P2 出口 |
| `docs/DELIVERY_ACCEPTANCE_SCORECARD.md` | 30 天交付验收评分卡，定义 44 项 backlog、90% 可用率、100% 开发完成和 P0/P1 强门禁 |
| `docs/DOCK3_OSD_STATE_FIELD_MATRIX.md` | OSD/state 单位、枚举、来源、缺省策略和真机待对接字段矩阵 |
| `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` | 全链路验收 runbook，定义启动顺序、验收命令、P2 边界和证据归档 |
| `docs/DJI_DOCK3_MIGRATION_GUIDE.md` | Dock3 迁移方法、官方文档索引和粗粒度迁移顺序 |
| `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` | Dock3 客户需求迁移台账和当前验证证据 |
| `docs/DOCK3_MODULE_STATUS.md` | 模块状态总表和当前专项验收命令 |
| `docs/DRONE_NEST_MAVLINK_MAPPING.md` | 机巢 v1.3 MAVLink 消息和 Dock3 映射口径 |
| `docs/archive/README.md` | 历史文档归档说明；归档文档不再作为当前开发、部署或验收入口 |

## 2. 主入口

| 路径 | 职责 |
|---|---|
| `src/main.py` | `EdgeGateway` 主入口，负责加载配置、初始化 MQTT、TopicRouter、TelemetryProcessor、业务模块、MAVLink 连接和 Dispatcher，并注册全部 MQTT method |

启动策略补充：2026-08-08 起，`src/main.py` 启动网关时不再主动连接飞机主
MAVLink，只创建连接对象和业务模块；主飞控 MAVLink 会在 `drone_open`、
`flighttask_prepare`、`flighttask_execute` 或 `one_key_takeoff_flow` 触发时
按需连接并启动 dispatcher。机巢 MAVLink 仍按 `nest_mavlink.enabled` 在启动期
连接，用于机场状态、机巢指令和飞机上电前置流程。

`src/main.py` 当前注册 method 分组：

| 分组 | 数量 | 主要职责 |
|---|---:|---|
| device | 51 | 设备管理、绑定、OTA、配置、属性、HMS、基础机场动作 |
| mission | 42 | 航线任务、飞行任务、指令飞行、返航、降落 |
| payload | 93 | 相机、云台、ViewLink、红外、PSDK、喊话器、探照灯和第三方负载 |
| media | 13 | 媒体文件、直播、上传优先级、存储配置 |
| drc | 24 | DRC 模式和实时控制 |
| dock | 8 | 旧 Dock/Dock1 风格机场查询、控制、告警、气象 |
| log | 3 | 日志列表和上传 |
| flysafe | 3 | 远程解禁 |
| flight_area | 2 | 自定义飞行区 |
| custom_data | 4 | PSDK/ESDK 自定义数据透传 |
| 合计 | 243 | 当前已注册 method 总数，静态来源见 `docs/METHOD_COVERAGE_LEDGER.md` |

## 3. MQTT 与上云 API 层

| 路径 | 职责 |
|---|---|
| `src/mqtt/client.py` | MQTT 客户端，连接本地 broker，订阅下行 topic，发布 osd/state/events/reply/requests/drc_up |
| `src/mqtt/topic_router.py` | 根据 `method` 路由到对应 handler，并维护 method 到 category 的映射 |
| `src/utils/message.py` | 构造 DJI Cloud API 风格消息体和 MQTT topic |
| `src/cloud_api/connection_manager.py` | 设备上线、心跳、绑定、requests/requests_reply 请求匹配和超时清理 |
| `src/cloud_api/telemetry_reporter.py` | OSD、state、events 上报和 MQTT 断线缓存 |
| `src/cloud_api/capability_reporter.py` | 能力集上报 |
| `src/cloud_api/dock_manager.py` | 机巢状态缓存和 HMS 告警桥接；旧 `dock_status_query`、`dock_control` 等 Dock/Dock1 风格 services 入口已移除 |

易混点：

- `src/cloud_api/dock_manager.py` 面向云端协议和业务状态。
- `src/modules/drone_nest_module.py` 面向 MAVLink 自定义机巢消息。
- Dock3 迁移时应先定义云端字段，再决定如何从 MAVLink 或模拟状态映射。

## 4. 业务模块层

| 路径 | 职责 |
|---|---|
| `src/device/manager.py` | 设备物模型、绑定、OTA、HMS、拓扑、属性和系统状态管理 |
| `src/mission/manager.py` | 航线任务、飞行任务、指令飞行、返航、任务状态机和进度上报 |
| `src/media/manager.py` | 媒体文件、直播、上传队列和存储配置 |
| `src/drc/controller.py` | DRC 模式进入/退出、速度/航向/高度/指点飞行、无人机和负载 DRC 控制 |
| `src/payload/camera.py` | 相机拍照、录像、参数设置和媒体元数据触发 |
| `src/payload/gimbal.py` | 云台角度、模式、复位和目标指向 |
| `src/payload/third_party.py` | 第三方负载、PSDK、喊话器、探照灯、红外、降落伞等接口 |

这些模块承接 `TopicRouter` 分发的 `services/property/drc` 请求。后续补业务逻辑时必须遵守非阻塞要求：不能在 handler 热路径里做长时间等待、文件 IO、网络上传或数据库阻塞写入。

## 5. MAVLink 核心层

| 路径 | 职责 |
|---|---|
| `src/core/connection.py` | 当前 MAVLink 连接管理，支持 UDP/TCP/串口和自定义 dialect |
| `src/core/message_dispatcher.py` | 当前 MAVLink 主分发器，负责接收、缓存、推送通知和 idle_task 调度 |
| `src/core/message_cache.py` | 按 sysid 和 msg_type 缓存最新 MAVLink 消息 |
| `src/core/observer.py` | 观察者基类，定义 `on_message` 和 `idle_task` |
| `src/mavlink/adapter.py` | 旧 MAVLink 适配层，保留历史兼容和简单命令包装 |
| `src/mavlink/telemetry.py` | 遥测处理层，将 `TelemetryModule` 数据转换为 OSD/state 上报格式 |

当前主联调路径是：

```text
src/core/connection.py
  -> src/core/message_dispatcher.py
  -> src/modules/*
  -> src/mavlink/telemetry.py
  -> src/cloud_api/telemetry_reporter.py
```

`src/mavlink/adapter.py` 属于旧架构，后续新增联调能力应优先使用 `core + modules` 路径。

## 6. MAVLink 功能模块

| 路径 | 职责 |
|---|---|
| `src/modules/base_module.py` | MAVLink 功能模块基类 |
| `src/modules/telemetry_module.py` | 解析飞控遥测，提供位置、姿态、电池、GPS 等数据 |
| `src/modules/flight_control.py` | 解锁、起飞、降落、返航、模式切换和连接监控 |
| `src/modules/mode_control.py` | 模式管理和 GUIDED 位置控制，当前模式表偏 ArduPlane/QuadPlane |
| `src/modules/drone_nest_module.py` | 自定义机巢 MAVLink 消息解析、状态回调和机巢命令发送 |
| `src/modules/waypoint_manager.py` | MAVLink 航点上传、下载、校验、状态和数据库集成 |
| `src/modules/rtk_module.py` | NTRIP、RTCM 注入和 RTK 状态 |
| `src/modules/gimbal_control.py` | ViewLink 云台 SDK 封装，偏真实云台控制 |
| `src/modules/log_manager.py` | 设备日志接口 |
| `src/modules/flysafe_manager.py` | 解禁证书缓存和开关 |
| `src/modules/flight_area_manager.py` | 自定义飞行区/地理围栏 |
| `src/modules/custom_data_manager.py` | PSDK/ESDK 数据透传 |
| `src/modules/device_manager.py` | MAVLink 设备发现和在线状态 |

人员2优先关注 `connection.py`、`message_dispatcher.py`、`telemetry_module.py`、`flight_control.py`、`drone_nest_module.py` 和 `waypoint_manager.py`。

## 7. 数据层

| 路径 | 职责 |
|---|---|
| `src/database/connection.py` | MongoDB/SQLite 后端选择、同步/异步会话代理、MongoDB 索引和连接生命周期 |
| `src/database/models.py` | ORM 模型：mission、waypoint、device_state、alarm、binding、telemetry、operation、media |
| `src/database/mission_db.py` | 航线任务 CRUD |
| `src/database/waypoint_db.py` | 航点 CRUD |
| `src/database/telemetry_db.py` | 遥测写入和查询 |
| `src/database/operation_db.py` | 操作记录创建、进度、完成、失败和历史查询 |
| `src/database/media_db.py` | 媒体文件和上传状态 |
| `src/database/alarm_db.py` | 告警记录 |
| `src/database/device_db.py` | 设备绑定和设备信息 |
| `src/utils/request_trace.py` | 非阻塞 request trace 记录器，写入 `logs/request_trace/YYYY-MM/request_trace_YYYY-MM-DD.jsonl` |
| `src/utils/logger.py` | 保存网关总日志，并把 MQTT、MAVLink 原始帧、任务、告警、机巢 PLC、飞控、相机/负载路由到七个独立日志 |
| `tools/request_trace_query.py` | request trace 查询工具，可按 `tid`、`bid`、`method`、`event`、`result` 过滤 |
| `src/database/device_state_db.py` | 设备状态 |
| `src/database/cloud_sync_manager.py` | 云端同步任务 |
| `src/database/retention_manager.py` | MongoDB/SQLite 短周期数据清理、容量检查和保留策略 |
| `tools/migrate_sqlite_to_mongodb.py` | 历史 SQLite 全量迁移到 MongoDB并逐行校验 |
| `tools/mongodb_runtime_audit.py` | Ubuntu MongoDB 连接、索引和八个运行时数据域读写审计 |

当前默认运行时后端为 Ubuntu 本机 MongoDB。现有业务模块仍调用统一的 DB 仓储接口；连接层根据 `database.backend` 返回 MongoDB 或 SQLite 会话。SQLite 只用于迁移历史 `missions.db`、隔离专项和显式回退，不再是生产默认存储。

## 8. 自定义 MAVLink 协议

| 路径 | 职责 |
|---|---|
| `src/mavlink_definitions/drone_nest.xml` | 当前自定义机巢 MAVLink XML 定义 |
| `src/mavlink_definitions/nest_protocol.xml` | 另一份机巢协议 XML |
| `src/mavlink_definitions/generate_dialect.py` | 方言生成脚本 |
| `src/generated_dialect/drone_nest.py` | 当前 `DroneNestModule` 使用的生成方言 |
| `src/generated_dialect/gcs_order_protocol.py` | 机巢 GCS Order 命令编解码 |
| `src/generated_nest/nest_mavlink.py` | 另一套生成方言，存在历史兼容风险 |

机巢字段映射以 `docs/DRONE_NEST_MAVLINK_MAPPING.md` 为准。新增或修改机巢消息时，必须同步更新映射文档。

## 9. 模拟器与工具

| 路径 | 职责 |
|---|---|
| `src/nest_simulator.py` | MAVLink 机巢模拟器，支持心跳、自定义状态上报和命令响应 |
| `tools/ubuntu_preflight.sh` | Ubuntu 部署前只读检查脚本，不安装、不启动、不修改 |
| `tools/mqtt_smoke_test.py` | MQTT method 冒烟测试工具，可按 method 自动选择 services/property/drc topic 并等待 reply |
| `tools/mqtt_dock3_p0_batch_test.py` | Dock3 P0 总批量验收工具，一次覆盖状态查询、properties、DRC 和错误 topic |
| `tools/mqtt_dock3_properties_test.py` | Dock3 properties 专项验收，覆盖可写属性、只读属性和值域错误 |
| `tools/mqtt_dock3_device_test.py` | Dock3 device topology 专项验收，覆盖 `update_topo` 和 `status_reply` |
| `tools/mqtt_dock3_drc_test.py` | Dock3 DRC 专项验收，覆盖建链、杆量、官方字段和值域错误 |
| `tools/mqtt_dock3_cmd_test.py` | Dock3 CMD/远程调试专项验收，覆盖舱盖、充电、推杆、灯和设备动作 |
| `tools/mqtt_dock3_wayline_test.py` | Dock3 Wayline 专项验收，覆盖任务准备、执行、进度、暂停、恢复和返航 |
| `tools/e2e_takeoff_flow_test.py` | 一键启航离线闭环，覆盖 mock KMZ、`flighttask_prepare/execute`、机巢 `request_takeoff` 和模拟任务完成 |
| `tools/mqtt_one_key_takeoff_sitl_test.py` | 一键启航真实 SITL 航线验收，默认使用服务器 `/home/fenghuolun/zwz/HD.kml`，覆盖 `flighttask_prepare`、`one_key_takeoff_flow`、QGC/SITL OSD 高度爬升、真实航点进度和 `flighttask_pause` 暂停 |
| `tools/e2e_full_chain_test.py` | P0/P1 全链路回归，串联 properties、device、绑定前置、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config 等当前主验收阶段 |
| `tools/mqtt_drc_batch_test.py` | Dock3 DRC 控制数据批量验收工具，覆盖 `stick_control/drone_control/payload_control/drc_speed_push/drc_gimbal_speed_push` |
| `tools/mqtt_cloud_simulator.py` | 上云 API 模拟器，支持 `requests_reply` 和 `status_reply` 模拟 |
| `tools/mqtt_organization_test.py` | Dock3 organization 查询、绑定、事件、拓扑和持久化验收工具 |
| `web/app.py` | 独立 Web 调试台后端，订阅 MQTT、显示消息、控制 SITL 和常用 MAVLink 命令 |
| `web/static/` | Web 调试台前端页面 |
| `web/README.md` | Web 调试台启动和页面操作说明 |

人员3优先维护这些文件和部署文档，保证所有模拟组件可重复启动。

以下 P1 验收工具已补齐基础入口；若未经过服务器或真实硬件验证，不应直接记为生产可用：

| 计划路径 | 必须覆盖 |
|---|---|
| `tools/nest_v13_protocol_test.py` | 机巢 v1.3 命令、应答、状态位、拒绝条件 |
| `tools/mqtt_dock3_media_test.py` | Dock3 media 文件列表、删除、上传优先级、上传事件 |
| `tools/mqtt_dock3_live_test.py` | Dock3 live 开始/停止、质量、镜头、状态事件 |
| `tools/mqtt_dock3_hms_log_test.py` | Dock3 HMS 告警和远程日志 |
| `tools/log_category_audit.py` | 八类日志落盘与隔离审计，可用 `--output-dir` 保存验收证据 |
| `tools/mqtt_dock3_firmware_config_test.py` | Dock3 固件升级模拟和配置更新 |
| `tools/e2e_takeoff_flow_test.py` | 一键启航主流程 |
| `tools/e2e_full_chain_test.py` | P0/P1 全链路回归 |

## 10. 不应提交或依赖的文件

以下文件属于运行产物或本地环境，不应作为业务代码依赖：

- `.venv/`
- `__pycache__/`
- `*.pyc`
- `*.db`, `*.db-shm`, `*.db-wal`
- `*.log`
- `.idea/`, `.vscode/` 中的个人配置
- `data/` 中的临时媒体、固件和运行数据
