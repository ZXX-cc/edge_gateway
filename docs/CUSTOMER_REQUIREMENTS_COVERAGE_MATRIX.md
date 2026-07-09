# 客户需求覆盖矩阵

更新时间：2026-07-09

本文档把客户最新三份资料统一整理成当前项目的开发口径。最终交付目标不是继续实现 Dock1，也不是交付平台前后端，而是把客户文档中属于**网关侧**的业务能力迁移到 **DJI Dock 3 上云 API + 设备侧 MAVLink/机巢 adapter** 语义下完成。

一句话原则：**`项目优先级整理.docx`、`业务流程.md`、`网关和平台侧重点.docx` 是当前优先级和边界的主输入；客户需求涉及的上云能力必须用 Dock3 官方协议定义最终 MQTT topic、method、payload、reply、字段、枚举、单位和验收标准。平台侧台账、权限、前端、长期统计和业务工单只作为边界说明，不计入本网关项目缺口。**

具体开发顺序和 30 天交付证据见 `docs/P0_P1_P2_DELIVERY_BACKLOG.md`。章节级追溯关系见 `docs/CUSTOMER_REQUIREMENTS_TRACEABILITY_MATRIX.md`。第 30 天计分见 `docs/DELIVERY_ACCEPTANCE_SCORECARD.md`。P0/P1 是硬验收范围，P2 允许有已记录问题但不能影响主链路。

## 1. 资料来源

| 文档 | 当前判断 | 用途 |
|---|---|---|
| `docs/项目优先级整理.docx` | 当前客户优先级主文档，按模块列出高/中/低缺口，并给出 370 项功能统计：已实现 163、部分实现 96、未实现 111 | 作为当前 P0/P1/P2 和模块缺口的主来源 |
| `docs/业务流程.md` | 当前客户业务流程主文档，覆盖启动、绑定、任务、一键启航、异常处理、设备管理和数据流 | 作为端到端业务闭环和 E2E 验收来源 |
| `docs/网关和平台侧重点.docx` | 当前客户边界主文档，明确执行状态机、硬件控制、实时遥测、本地断点数据、安全兜底放网关；平台负责台账、权限、前端、统计和长期数据 | 作为本网关项目范围边界来源，平台侧内容只作排除说明 |
| `docs/DJI_Dock固件功能清单77.docx` | 历史参考文档，不再作为当前优先级的第一来源；其中存在旧 Dock/Dock1/Dock2 风格表达 | 作为旧功能全集参考，不作为 Dock3 字段最终标准 |
| `docs/2677.docx` | 当前项目架构和代码能力分析，强调单板 Linux、双 MAVLink 链路、MQTT 为中枢 | 作为当前项目落地结构来源 |
| `docs/BUSINESS_FLOW.docx` | Edge Gateway 业务流程全景，覆盖启动、绑定、一键启航、航线、DRC、机巢、负载、媒体、OTA、飞安等 | 作为端到端业务流程来源 |
| `docs/机巢协议v1.3.docx` | 客户机巢主控私有协议，MAVLink 消息 ID 为 12003/12004/12005 | 作为机巢侧协议来源，再映射到 Dock3 机场属性/事件/命令语义 |

## 2. 网关项目范围边界

| 类别 | 本项目是否交付 | 说明 |
|---|---|---|
| 航线任务执行状态机、航点 MAVLink 上传、断点续飞 | 是 | 直接影响飞行安全和断网连续性，必须在网关侧执行 |
| 机巢舱盖、充电、夹紧、急停、灯光、设备动作 | 是 | 直接操作硬件，必须由网关通过机巢 adapter 执行 |
| DRC 模式、摇杆控制、心跳、超时安全退出 | 是 | 低延迟实时控制，平台只提供操作入口和权限 |
| MAVLink 遥测采集、OSD/state 上报、本地最新状态缓存 | 是 | 网关是设备真实状态的第一数据源 |
| 子设备拓扑、能力集、属性设置执行、本地配置持久化 | 是 | 拓扑和配置以实际设备为准，需支持重启恢复 |
| 本地 SQLite 短周期断点数据、待上报队列、近期告警和操作日志 | 是 | 用于断网续传、任务恢复和安全兜底 |
| HMS 硬件告警采集、本地安全策略、严重告警联动返航/暂停 | 是 | 安全策略不能依赖平台在线 |
| 固件下载、校验、升级执行、失败回滚 | 是 | 平台可以创建计划，实际执行和前置校验必须在网关 |
| 航线库编辑、前端页面、用户权限、业务工单、多设备批量调度 | 否 | 平台侧能力，只作为边界说明，不计入本网关缺口 |
| 全量历史统计、长期数据仓库、媒体资源库、告警通知推送 | 否 | 平台侧能力，网关只保留短周期本地数据和上报能力 |

## 3. 最终协议口径

| 项目 | 口径 |
|---|---|
| 最终设备类型 | DJI Dock 3 语义，不以 Dock1/Dock2 字段作为验收标准 |
| 上云协议 | 客户需求涉及的能力使用 DJI Dock 3 Cloud API MQTT |
| Topic/Method | 客户需求涉及的 topic/method 以 Dock3 官方文档为准，旧 services 兼容入口只能作为 smoke 或内部触发器 |
| 字段和枚举 | 客户需求涉及的字段和枚举以 Dock3 官方 properties/device/organization/live/media/wayline/hms/cmd/firmware/log/config/drc 等文档为准 |
| 客户文档中的旧字段 | 只用于识别业务意图，迁移时必须落到 Dock3 字段、事件或设备侧适配层 |
| 客户未用 Dock3 能力 | 可以延期迁移，必须在 `docs/METHOD_COVERAGE_LEDGER.md` 标为 Deferred/Adapter |
| 机巢私有协议 | 可以保持客户 v1.3 MAVLink 协议，但它属于设备侧适配层，不改变云端 Dock3 口径 |
| Demo 兼容 | DJI Cloud API Demo 中显示 Dock2/M3D 只是联调枚举兼容结果，不代表最终设备型号目标 |

## 4. 需求全集归一

客户最新优先级文档抽取到的需求规模如下，后续开发按这些业务域覆盖；每项云端验收都要回到 Dock3，平台侧能力不计入本网关缺口。

| 统计项 | 数量 | 说明 |
|---|---:|---|
| 已实现 | 163 | 可作为当前代码和脚本基线，但仍需按网关侧/Dock3 口径复核 |
| 部分实现 | 96 | 有代码或模拟链路，缺真实语义、字段精校或 E2E 证据 |
| 未实现 | 111 | 需要补 adapter、状态机、错误码、日志或验收脚本 |
| 合计 | 370 | 当前客户功能全集统计口径 |

| 优先级 | 已实现 | 部分实现 | 未实现 | 合计 | 完成率 |
|---|---:|---:|---:|---:|---:|
| P0 | 121 | 46 | 26 | 193 | 63% |
| P1 | 37 | 43 | 41 | 121 | 31% |
| P2 | 5 | 7 | 44 | 56 | 9% |

| 分组 | 数量 | 业务含义 | Dock3 落点 |
|---|---:|---|---|
| F | 112 | 飞控、飞行模式、航线、飞行安全、MAVLink 控制 | `properties`、`wayline`、`drc`、`remote-control`、`custom-fly-region` |
| D | 95 | 机场/机巢主控、舱盖、充电、环境、报警、维护 | `properties`、`cmd`、`hms`、`firmware`、`log`、`config` |
| I | 84 | 飞机、机巢、云端之间的交互流程 | `device`、`organization`、`services`、`requests`、`events`、`state` |
| P | 79 | 相机、云台、直播、媒体、第三方负载 | `live`、`media`、`psdk`、`psdk-transmit-custom-data`、`esdk-transmit-custom-data` |
| IF | 9 | 第六章接口边界 | 云端 Dock3 MQTT + 设备侧 MAVLink/私有协议适配 |

## 5. 第六章架构落地解释

客户文档第六章的重点不是要求网关继续做 Dock1，而是定义边缘网关在整条链路中的位置：它是 **Dock3 云端协议适配器 + MAVLink/机巢协议适配器 + 状态机编排器**。

| 第六章接口 | 客户文档描述 | 当前项目落点 | Dock3 迁移要求 |
|---|---|---|---|
| IF-001 飞控到伴随计算机 | MAVLink 2.0，OSD 高频、状态低频 | `src/core/connection.py`、`src/modules/telemetry.py`、`src/modules/flight_control.py` | 飞控数据必须转换为 Dock3 `osd/state/properties` 字段，不直接暴露 ArduPilot 内部枚举 |
| IF-002 伴随计算机到机巢主控 | MAVLink over TCP/UDP，实时命令和状态 | `src/modules/drone_nest_module.py`、`src/device/nest_property_mapper.py` | 机巢状态必须映射成 Dock3 机场属性、状态和事件 |
| IF-003 机巢主控到执行器 | 私有协议控制舱盖、夹紧、充电、急停等 | `docs/机巢协议v1.3.docx`、`docs/DRONE_NEST_MAVLINK_MAPPING.md` | 私有命令保留在设备侧，云端命令入口按 Dock3 `cmd`/相关 services 收敛 |
| IF-004 机巢主控到云端 | MQTT over TLS，JSON/binary，OSD/state/events | `src/mqtt/`、`src/cloud_api/`、`src/device/` | 生产验收必须是 Dock3 topic/reply/request/status/drc 结构；本地 1883 只用于模拟 |
| IF-005 相机链路 | MIPI/USB/UVC | `src/payload/camera.py`、`src/media/manager.py` | 云端落到 Dock3 `media` 和 `live` |
| IF-006 云台链路 | UART/CAN/MAVLink Gimbal v2 | `src/payload/gimbal.py`、`web/mavlink/viewlink/` | 云端落到 Dock3 payload/gimbal/DRC 控制字段 |
| IF-007 喊话器 | 串口/网络协议 | `src/payload/third_party.py` | 落到 Dock3 PSDK 或第三方负载适配 |
| IF-008 探照灯 | GPIO/CAN/串口 | `src/payload/third_party.py`、`cmd` 类控制 | 云端按 Dock3 `cmd` 或 payload 控制语义返回 |
| IF-009 飞控到云台直连 | MAVLink Gimbal v2 | `src/modules/flight_control.py`、`src/payload/gimbal.py` | 只作为设备侧控制路径，状态仍归一到 Dock3 |

第六章给出的 10 个实施阶段可映射为当前项目路线：

| 客户阶段 | 当前项目解释 | 当前覆盖 |
|---|---|---|
| MAVLink 遥测 + OSD | 飞控/SITL 到 Dock3 `osd/state` | 部分完成，字段仍需 Dock3 精校 |
| DRC + 基础飞控控制 | Dock3 DRC services + MAVLink 控制 | 专项已通过，真实模式映射仍需补 |
| 航线 + WPML 转换 | Dock3 wayline + MAVLink waypoint | 基础闭环已通过，KMZ/WPML 深度解析待补 |
| 起降状态机 + 充电协同 | 一键启航、机巢 v1.3、充电/夹紧/舱盖联动 | 设备侧命令已映射，完整状态机待补 |
| 直播 | Dock3 live | 基础可用，真实推流 adapter 待补 |
| 云台/相机 | Dock3 payload/camera/gimbal | 部分入口存在，真实链路待补 |
| 媒体 + OSS 上传 | Dock3 media | 基础可用，真实 OSS/断点续传待补 |
| HMS + 远程运维 | Dock3 hms/log/firmware/config | HMS/log、firmware/config 均已通过 Ubuntu 专项，真实 OTA/真实上传 adapter 后续补 |
| PSDK | Dock3 psdk | 暂缓/占位 |
| 高级能力 | WebRTC、GB28181、远程解禁等 | 后续分组补齐 |

## 6. 当前覆盖状态

状态口径：

| 状态 | 含义 |
|---|---|
| 已可用 | 已在 Ubuntu/Demo/专项脚本中跑通，可作为当前阶段联调基线 |
| 部分可用 | 有代码路径和部分验证，但字段、枚举、状态机或真实行为仍需补 |
| 开发中 | 有旧实现或占位，需要按 Dock3 改造 |
| 未覆盖 | 还没有形成可验收实现 |

| 业务域 | 当前状态 | 当前覆盖 | 主要缺口 | Dock3 验收入口 |
|---|---|---:|---|---|
| 云端连接、上线、心跳 | 已可用 | 85% | 生产 TLS、鉴权、桥接策略待固化 | `events`、`osd`、Demo 在线状态 |
| 设备拓扑和绑定 | 已可用 | 75% | `domain/type/sub_type` 官方枚举、多设备真实语义待精校 | `device`、`organization`、`status/status_reply` |
| Properties / OSD / State | 已可用 | 75% | `properties` 和 OSD/state 专项均已通过 Ubuntu 复测；客户需求涉及的单位、枚举和真实 Dock3 字段仍需精校 | `properties`、`osd`、`state` |
| DRC 指令飞行 | 已可用 | 68% | 心跳、超时恢复、飞控模式语义待补 | `drc`、`drc/down`、`drc/up` |
| CMD 远程调试 | 已可用 | 80% | 服务器专项已重跑通过 60 条 CMD 回包、18 条进度/状态事件和 E2E cmd 单段；`cover_close` 后 `cover_state=0` 已验证；真实执行耗时、硬件重启/格式化开关待 adapter 精校 | `cmd` |
| Wayline 航线 | 部分可用 | 62% | KMZ/WPML、媒体计数、真实 SITL 航点执行待补 | `wayline` |
| MAVLink 飞控联动 | 部分可用 | 55% | 解锁/起飞/航线/返航/模式映射和非阻塞超时待补 | MAVLink + Dock3 events/state |
| 机巢 v1.3 协议 | 部分可用 | 63% | 离线专项已覆盖命令字、拒绝原因、状态位、云端字段和 HMS 映射；Ubuntu 网关 + 机巢模拟器实跑、状态机闭环和 boolstate2 精细映射待补 | `cmd`、`properties`、`state`、`hms` |
| 一键启航业务流 | Needs Test | 58% | `one_key_takeoff_flow` 已完成本地闭环和单测，仍缺 Ubuntu MQTT 实跑、真实 SITL 任务进度和机巢状态机证据 | `wayline` + `cmd` + `events` |
| 媒体管理 | 已可用 | 55% | 文件列表、删除、上传优先级和上传事件已通过专项；真实 OSS/断点续传待补 | `media` |
| 直播 | 已可用 | 45% | mock 推流启动/停止、质量、镜头、状态事件已通过专项；真实 RTMP/WebRTC/GB28181 adapter 待补 | `live` |
| HMS 告警 | 已可用 | 60% | HMS 告警查询/清除和事件已通过 Ubuntu 专项；机巢故障/急停/错误码/心跳丢失到 HMS 的本地映射已补齐，文案 key 和真实故障源待精校 | `hms` |
| 固件升级 | 已可用 | 55% | 升级任务、进度、取消、结果事件、失败码已通过 Ubuntu 专项；真实 OTA adapter 待补 | `firmware` |
| 远程日志 | 已可用 | 60% | 日志列表、上传模拟、取消、进度事件和失败码已通过 Ubuntu 专项；压缩和真实上传 adapter 待补 | `log` |
| 配置更新 | 已可用 | 65% | 配置下发、查询、持久化、回滚、事件已通过 Ubuntu 专项；真实云端字段精校待补 | `config` |
| 自定义飞行区/远程解禁/AirSense | 未覆盖到可用 | 5% | 文件、证书、缓存、生效逻辑待定义 | `custom-fly-region`、`flysafe`、`airsense` |
| PSDK/ESDK 透传 | 未覆盖到可用 | 5% | 真实负载协议和透传契约待定义 | `psdk`、`psdk/esdk transmit` |

当前总体判断：按“Ubuntu 服务器内 Dock3 模拟链路”口径，项目处于约 76% 基线；按客户全量业务能力口径，一键启航、机巢状态机、真实 OTA adapter 和真实 SITL 航点执行仍是必须补齐项。

## 7. P0/P1 可用与 100% 开发完成定义

| 指标 | 定义 |
|---|---|
| P0 100% 可用 | 启动、MQTT、上线、绑定、拓扑、OSD/state/properties、错误码、request trace、基础 CMD、基础 DRC、基础 Wayline、MAVLink/SITL 连接、机巢关键状态必须可重复跑通 |
| P1 100% 可用 | 一键启航主流程、机巢 v1.3 完整状态机、DRC 心跳/超时、Wayline KMZ/WPML 基础解析、媒体基础、直播基础、HMS、远程日志、配置、固件升级模拟、全链路 E2E 必须可重复跑通 |
| P2 允许存在少量问题 | PSDK/ESDK、高级负载、远程解禁、AirSense、高级飞安、GB28181/WebRTC、真实硬件细节和性能优化允许存在已记录问题，但不能影响 P0/P1 |
| 100% 开发完成 | 客户文档列出的功能域都有 Dock3 对应实现、模拟实现或明确硬件适配接口；缺真实硬件的功能不能空着，至少要有协议入口、状态机、错误码、日志和可回归测试 |
| 不计为完成 | 只存在旧 Dock1 字段、只返回 `Unknown method`、只写文档没有 handler、只有 handler 没有 Dock3 payload 校验、真实阻塞 IO 直接跑在 MQTT 回调中 |
| 硬件缺失处理 | 没有真实 Dock3/飞控/负载时，先用 SITL、机巢模拟器、媒体模拟器和 Mock adapter 验证 Dock3 云端行为；真实硬件接入时替换设备侧 adapter |

P0/P1 是 30 天硬验收范围，不能用“总完成率 90%”掩盖 P0 或 P1 的不可用。P2 可以延期修复，但每个 P2 问题都必须记录影响范围、绕过方式和后续计划。

## 8. 必须补齐的验收证据

| 证据 | 当前状态 | 要求 |
|---|---|---|
| P0 批量测试 | 已有 | 保留 `tools/mqtt_dock3_p0_batch_test.py`，清理旧回包后支持严格模式 |
| Properties/Device/DRC/CMD/Wayline 专项 | 已有 | 作为当前已可用基线继续扩展 |
| Media 专项 | 已有 | `tools/mqtt_dock3_media_test.py` 已通过 Ubuntu 专项 |
| Live 专项 | 已有 | `tools/mqtt_dock3_live_test.py` 已通过 Ubuntu 专项 |
| HMS/Log 专项 | 已有 | `tools/mqtt_dock3_hms_log_test.py` 已通过 Ubuntu 专项 |
| Firmware/Config 专项 | 已有 | `tools/mqtt_dock3_firmware_config_test.py --timeout 20 --quiet-stale` 已通过 Ubuntu 专项 |
| 机巢 v1.3 专项 | 部分已有 | `tools/nest_v13_protocol_test.py --verbose` 已覆盖离线协议基础、状态位和 HMS 映射，仍需网关 + 机巢模拟器实跑留证 |
| 全链路 E2E | 已有 | `tools/e2e_full_chain_test.py` 已覆盖当前核心 MQTT 域并已接入 firmware/config；一键启航后续继续接入 |

## 9. 下一步优先级

| 优先级 | 工作项 | 原因 |
|---:|---|---|
| 1 | 补齐一键启航 + 机巢 v1.3 状态机 | 起飞前检查、开盖、断充、释放、起飞、返航、降落、充电仍未形成可重复 E2E |
| 2 | 打通 MAVLink/SITL 真实起飞、航线、返航状态 | 当前协议回包可用，但飞控真实行为闭环还不够 |
| 3 | 补真实 OTA adapter 和操作记录 | firmware/config 模拟闭环已通过，但真实下载、校验、刷写、备份和回滚仍需设备侧 adapter |
| 4 | 补相机/云台真实控制和多设备/解绑/离线 | 客户文档把负载控制和设备拓扑列为网关侧责任 |
| 5 | 非阻塞/NoSQL/证据归档 | 断网续传、短周期数据、日志压缩和操作记录需要稳定证据 |
