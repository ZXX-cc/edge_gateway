# Dock3 模块完成状态总表

更新时间：2026-07-07

本文档给多人协同开发使用，用一张表说明当前 DJI Dock 3 迁移中每个模块的完成状态、已验证内容、未完成内容和下一步。更细的过程记录见 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md`，接口字段和错误码见 `docs/INTERFACE_DESIGN_SPEC.md`。

客户 Word 文档中的功能清单和业务流程已经归一到 `docs/CUSTOMER_REQUIREMENTS_COVERAGE_MATRIX.md`。后续开发口径是：客户文档定义业务范围，DJI Dock 3 官方 Cloud API 定义最终协议标准。旧 Dock/Dock1/Dock2 字段只能作为差异对照或内部适配线索，不能作为最终验收口径。

## 状态口径

| 状态 | 含义 |
|---|---|
| 已通过 | 已在 Ubuntu 纯模拟环境跑通专项或批量脚本，作为当前联调基线 |
| 部分完成 | 已有代码和部分验收结果，但仍缺官方字段精校、真实语义或更完整测试 |
| 待迁移 | 当前仍是旧实现、占位或未开始按 Dock3 官方文档改造 |
| 暂缓 | 当前纯模拟阶段不阻塞主链路，后续接真实设备或 SDK 时再做 |

当前整体进度估算：**约 60%**。该进度只针对“Ubuntu 服务器内跑通 Dock3 云端模拟链路”，不代表真实 DJI Dock 3 设备接入完成。

## 30 天目标口径

| 指标 | 当前要求 |
|---|---|
| P0 100% 可用 | 启动、MQTT、上线、绑定、拓扑、OSD/state/properties、错误码、request trace、基础 CMD、基础 DRC、基础 Wayline、MAVLink/SITL 连接、机巢关键状态必须可重复跑通 |
| P1 100% 可用 | 一键启航主流程、机巢 v1.3 状态机、DRC 心跳/超时、Wayline KMZ/WPML 基础解析、媒体基础、直播基础、HMS、远程日志、配置、固件升级模拟、全链路 E2E 必须可重复跑通 |
| P2 允许带问题 | 高级负载、PSDK/ESDK、远程解禁、AirSense、高级飞安、GB28181/WebRTC、真实硬件细节和性能深度优化可以有已记录问题 |
| 100% 开发完成 | 客户需求覆盖矩阵中的每个业务域都有 Dock3 对应实现、模拟实现或硬件适配接口；客户未用 Dock3 能力可 Deferred |
| 全链路可用 | 上线、绑定、拓扑、OSD/state、机巢控制、DRC、航线、一键启航、媒体/直播、HMS/日志/配置能串联验证 |
| 协议标准 | 客户需求涉及的云端 MQTT topic、method、payload、reply、字段、枚举、单位全部以 Dock3 为最终标准 |
| 追溯矩阵 | `docs/CUSTOMER_REQUIREMENTS_TRACEABILITY_MATRIX.md` |
| 交付评分卡 | `docs/DELIVERY_ACCEPTANCE_SCORECARD.md` |
| Backlog 文档 | `docs/P0_P1_P2_DELIVERY_BACKLOG.md` |
| 缺口登记 | `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md` |
| 计划文档 | `docs/30_DAY_MILESTONE_PLAN.md` |
| 执行看板 | `docs/30_DAY_EXECUTION_BOARD.md` |
| 验收 Runbook | `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` |

执行顺序必须是 P0 -> P1 -> P2。P0/P1 未可用前，不把主要开发资源投入 P2 深度优化；客户未提及/未使用的 Dock3 能力不抢占 P0/P1 开发资源。

## 模块状态表

| 模块/大类 | 当前状态 | 完成度估算 | 已完成内容 | 未完成/风险 | 下一步 | 主要负责人 |
|---|---|---:|---|---|---|---|
| Ubuntu 纯模拟部署与基础链路 | 部分完成 | 80% | Mosquitto、Edge Gateway、ArduPilot SITL、MAVLink、OSD、基础 MQTT reply 已跑通 | systemd、一键启动、环境固化、严格旧回包清理未完成 | 固化启动脚本和服务化部署 | 人员3 |
| MQTT topic/reply 边界 | 部分完成 | 85% | `services/services_reply`、`property/set_reply`、`requests/requests_reply`、`status/status_reply`、`drc/up` 基础分流已验证 | broker 中仍可能观察到旧格式重复回包，需要清理旧进程/旧模拟器后严格验证 | 清理旧回包源，使用 `--fail-on-stale` 严格验收 | 人员1 |
| DJI 错误码边界 | 已通过 | 90% | MQTT 对外回包不再返回内部 `-1/-2/-3`，统一映射 DJI 官方错误码；本地单测通过 | 各业务分支的精确错误码仍需随官方字段细化 | 每迁移一个大类补充错误码矩阵 | 人员1 |
| 日志与可观测性 | 部分完成 | 65% | 日志按 `logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log` 落盘，MQTT 关键字段支持中文字段名 | request trace、链路耗时汇总、日志上传、日志压缩策略未完整实现 | 补全请求追踪和远程日志大类 | 人员1/人员3 |
| 非阻塞与容错基础 | 部分完成 | 45% | 已修复若干 coroutine 未 await warning，停止流程可正常 await | MongoDB/NoSQL 目标未实现，数据库写入和文件 IO 仍需全面后台化审计 | 建异步数据访问层和操作队列 | 人员1/人员2 |
| Properties 设备属性 | 已通过 | 70% | 官方 `rw` 属性 `silent_mode`、`air_transfer_enable`、`user_experience_improvement` 已通过专项复测；只读/非法/未知属性拒绝已验证 | 客户需求涉及的 `osd/state` 字段、单位、枚举、Dock3 真实设备字段仍需精校 | 继续做 `osd/state` 字段矩阵 | 人员1 |
| Device 设备管理/拓扑 | 已通过 | 70% | `update_topo` 官方必填字段、在线/离线拓扑、无效拓扑拒绝、`status_reply` 已通过专项复测 | `domain/type/sub_type` 官方产品枚举、多真实子设备语义未精校 | 精校设备类型枚举和多设备拓扑 | 人员1/人员3 |
| Organization 组织管理 | 已通过 | 70% | `airport_bind_status`、`airport_organization_get`、`airport_organization_bind` 的成功、失败、超时、绑定后事件和 SQLite 持久化已验证 | 真实云端差异、多设备绑定、解绑流程仍需补 | 补真实云端联调和解绑/多设备场景 | 人员1 |
| DRC 指令飞行 | 已通过 | 68% | `drc_mode_enter/exit`、`stick_control`、`drone_control`、`drc_gimbal_speed_push`、非法字段和值域错误已通过专项复测 | DRC 心跳、`takeoff_to_point`、超时恢复、MAVLink 模式语义仍需精校 | 补 DRC 心跳和飞控模式联动 | 人员1/人员2 |
| CMD 远程调试 | 已通过 | 72% | 调试模式、舱盖/充电/推杆、飞行器/设备动作、枚举开关、安全拒绝、进度事件已通过专项复测 | 真实设备重启/格式化/任务耗时/失败码未接真实硬件 | 补真实执行开关和失败码精校 | 人员1/人员3 |
| Wayline 航线管理 | 已通过 | 62% | `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel` 基础闭环已通过 Ubuntu 专项复测；纯模拟不触发真实飞控模式切换 | KMZ 下载解析、媒体计数、真实 SITL 航点执行、返航事件字段仍未完成 | 补 KMZ、媒体计数和真实 SITL 航点执行 | 人员1/人员2 |
| Dock 状态查询 | 部分完成 | 60% | `dock_status_query` 已能返回实时遥测快照和模拟机场状态 | 客户需求涉及的机场状态字段和单位仍需精校 | 纳入 properties/device 字段矩阵 | 人员1 |
| MAVLink 飞控联动 | 部分完成 | 55% | 网关能连接 SITL，Telemetry、FlightControl、ModeControl、WaypointManager、RTK 基础运行 | ArduCopter/ArduPlane 模式映射、解锁/起飞/航线执行、非阻塞超时恢复仍需完善 | 按人员2路线做 MAVLink 专项 | 人员2 |
| MAVLink 机巢模拟器 | 部分完成 | 50% | 自定义 dialect、`DRONE_NEST_*` 消息、机巢连接路径已有基础联调 | 机巢动作闭环、状态机、错误恢复和 Web 一键联动仍需完善 | 补机巢模拟器专项测试 | 人员2/人员3 |
| Web 调试台 | 部分完成 | 45% | 已有独立 `web/` 程序，可辅助替代终端观察 MQTT/SITL | UI 功能、日志筛选、状态汇总、批量测试入口仍需完善 | 加入 Dock3 大类测试入口和日志面板 | 人员3 |
| Media 媒体管理 | 待迁移 | 15% | 旧代码中有媒体管理和上传队列基础 | Dock3 `media.html` 的文件列表、上传、删除、优先级、事件未对齐 | 下一阶段优先迁移 media 或 live | 人员1 |
| Live 直播功能 | 待迁移 | 10% | 旧代码中有直播/媒体相关入口 | Dock3 `live.html` 的推流、镜头、质量、状态事件未对齐 | 建直播 method 矩阵和模拟回包 | 人员1/人员3 |
| HMS 管理 | 待迁移 | 15% | 有告警数据库和部分 HMS handler | Dock3 HMS 文案 key、告警结构、清除/上报规则未对齐 | 建 HMS 字段和错误码矩阵 | 人员1 |
| Firmware 固件升级 | 待迁移 | 10% | 有 OTA/升级相关旧入口 | 升级任务、进度、失败码、后台化未按 Dock3 精校 | 后台任务化固件升级模拟 | 人员1/人员3 |
| Remote Log 远程日志 | 待迁移 | 20% | 本地日志落盘已可用 | Dock3 远程日志列表、压缩、上传、超时和失败码未迁移 | 先做日志列表和上传模拟 | 人员1/人员3 |
| Config 配置更新 | 待迁移 | 15% | 有配置相关旧入口 | Dock3 config topic/method、配置持久化和回滚未对齐 | 对齐 `config.html` 并补模拟器 | 人员1 |
| Custom Fly Region 自定义飞行区 | 待迁移 | 10% | 有飞行区 manager 入口 | Dock3 `custom-fly-region.html` 字段、缓存和生效逻辑未对齐 | 建字段矩阵和缓存模型 | 人员1/人员2 |
| Remote Control 远程控制 | 待迁移 | 15% | DRC 控制通道已有基础 | Dock3 `remote-control.html` 独立语义未迁移 | DRC 稳定后再迁移 | 人员1/人员2 |
| PSDK 功能 | 暂缓 | 5% | 保留旧负载/第三方入口 | 无真实 PSDK 设备，不阻塞当前主链路 | 接入真实负载时再做 | 后续 |
| PSDK/ESDK 透传 | 暂缓 | 5% | 有 custom data manager 占位 | 透传数据契约未定义 | 后续定义透传协议 | 后续 |
| FlySafe 远程解禁 | 暂缓 | 5% | 有 flysafe manager 占位 | 文件/证书流程未实现 | 真实云端/设备阶段再做 | 后续 |
| AirSense | 暂缓 | 5% | 有告警/状态占位 | 当前无实体传感器，不阻塞纯模拟链路 | 后续按传感器数据源补 | 后续 |

## 当前已通过的专项命令

| 大类 | Ubuntu 验收命令 | 成功标志 |
|---|---|---|
| P0 总批量 | `python tools/mqtt_dock3_p0_batch_test.py --timeout 12` | `dock3 p0 batch test ok` |
| Properties | `python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale` | `dock3 properties test ok` |
| Device topology | `python tools/mqtt_dock3_device_test.py --timeout 12` | `dock3 device topology test ok` |
| Organization | `python tools/mqtt_organization_test.py --action full ...` | `organization request test ok` |
| DRC | `python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale` | `dock3 drc test ok` |
| CMD | `python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale` | `dock3 cmd test ok` |
| Wayline | `python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale` | `dock3 wayline test ok` |

## 下一批建议

| 优先级 | 模块 | 原因 | 预期交付 |
|---:|---|---|---|
| 1 | Media/Live | 云端业务闭环通常需要媒体文件和直播状态，当前仍待迁移 | `dock3_media_test.py`、`dock3_live_test.py`、字段矩阵和模拟回包 |
| 2 | HMS/Remote Log | 关系到故障诊断和服务器日志可用性 | HMS 告警结构、远程日志列表/上传模拟、错误码 |
| 3 | MAVLink 专项 | 人员2需要把纯协议模拟推进到真实 SITL 行为 | 模式映射、解锁/起飞/航线执行、超时恢复 |
| 4 | NoSQL/Request Trace | 关系到主线程非阻塞和可追踪性 | MongoDB/异步数据层方案、请求链路表、操作记录 |
