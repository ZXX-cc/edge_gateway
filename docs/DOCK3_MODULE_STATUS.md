# Dock3 模块完成状态总表

更新时间：2026-07-10

本文档给多人协同开发使用，用一张表说明当前 DJI Dock 3 迁移中每个模块的完成状态、已验证内容、未完成内容和下一步。更细的过程记录见 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md`，接口字段和错误码见 `docs/INTERFACE_DESIGN_SPEC.md`。

客户最新三份资料已经归一到 `docs/CUSTOMER_REQUIREMENTS_COVERAGE_MATRIX.md` 和 `docs/CUSTOMER_REQUIREMENTS_TRACEABILITY_MATRIX.md`。后续开发口径是：`项目优先级整理.docx` 定义优先级，`业务流程.md` 定义业务闭环，`网关和平台侧重点.docx` 定义网关/平台边界；DJI Dock 3 官方 Cloud API 定义最终协议标准。旧 `DJI_Dock固件功能清单77.docx` 只作为历史参考，旧 Dock/Dock1/Dock2 字段只能作为差异对照或内部适配线索。

本项目不是“平台 + 网关”全系统交付，只评估**网关侧 Dock3 云端模拟链路 + 设备侧 MAVLink/机巢 adapter**。平台侧台账、权限、前端页面、业务工单、多设备批量调度、全量历史统计、长期数据仓库、媒体资源库和告警通知推送不计入本网关项目缺口。

客户未要求独立 Web 调试台或前端页面，后续计划不把 `web/`、页面截图或 Demo 页面作为交付项。它们只可作为内部联调参考；正式验收证据以 MQTT 脚本、日志、trace、数据库记录和 E2E 输出为准。

## 状态口径

| 状态 | 含义 |
|---|---|
| 已通过 | 已在 Ubuntu 纯模拟环境跑通专项或批量脚本，作为当前联调基线 |
| 部分完成 | 已有代码和部分验收结果，但仍缺官方字段精校、真实语义或更完整测试 |
| 待迁移 | 当前仍是旧实现、占位或未开始按 Dock3 官方文档改造 |
| 暂缓 | 当前纯模拟阶段不阻塞主链路，后续接真实设备或 SDK 时再做 |

当前整体进度估算：**约 81%**。该进度只针对“Ubuntu 服务器内跑通 Dock3 网关模拟链路”，不代表真实 DJI Dock 3 设备全量接入完成，也不代表平台侧完成度。

## 2026-07-09 晚间进度快照

| 项目 | 当前结论 |
|---|---|
| DRC 真实指点飞行 | 已通过服务器复测。`property_get` 返回网关遥测 `latitude=43.8888422`、`longitude=125.3079695`、`height=28.938`，脚本生成电子围栏内目标点后，`takeoff_to_point` 和 `fly_to_point_update` 均返回 `result=0`、`output.status=ok`、`simulated=false`。 |
| DRC 覆盖范围 | 已覆盖 `drc_mode_enter/exit`、`drc_state`、官方心跳、无回包杆量、非法杆量、`drone_control` 成功/非法值、云台速度、`takeoff_to_point`、`fly_to_point_update/stop`、非法目标点拒绝。 |
| 旧回包诊断 | 共享 MQTT 测试客户端已输出 `stale_samples`，可以在 `--quiet-stale` 下定位旧回包/重复回包的 `category/topic/method/tid/result`。最新 DRC 复测中看到的旧回包主要来自重复的 `services_reply result=-2`，不影响当前正确 Dock3 回包。 |
| 单测基线 | `python -m unittest tests.test_mqtt_test_client tests.test_mqtt_dock3_drc_tool tests.test_dock3_drc tests.test_e2e_full_chain` 通过，`Ran 58 tests`。 |
| 仍未完成 | P0/P1 强门禁仍卡在真实 SITL 航点执行、Ubuntu 机巢 v1.3 实跑、非阻塞主链路审计、相机/云台真实 adapter、数据保留专项和严格 `--fail-on-stale` 环境清理。 |

## 客户三文档后的当前状态

| 状态 | 当前结论 |
|---|---|
| 已做到 | MQTT topic/reply 边界、设备上线/心跳、组织绑定、`update_topo` 设备拓扑、properties、OSD/state、错误码、request trace、CMD 基础控制、DRC 基础链路和心跳、Wayline mock KMZ 基础闭环、一键启航离线闭环、Media 基础、Live mock、相机/云台 MAVLink/SITL 控制、HMS/log、firmware/config 专项、短周期数据保留与操作记录、P0/P1 核心 E2E |
| 部分做到 | 飞控真实 SITL 行为、机巢 v1.3 状态机、一键启航真实机巢联动、真实航点执行、多设备/解绑/离线、相机/云台真实硬件控制 |
| 未做到/待迁移 | 真实 OTA、真实 OSS 上传、真实直播推流、真实 PSDK/ESDK、MongoDB/NoSQL 目标架构、生产 TLS/鉴权 |
| 不属于本网关项目 | 航线库编辑、前端页面、用户权限、业务工单、多设备批量调度、全量历史统计、长期数据仓库、媒体资源库、告警通知推送 |

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
| Ubuntu 纯模拟部署与基础链路 | 部分完成 | 94% | Mosquitto、Edge Gateway、ArduPilot SITL、MAVLink、OSD、基础 MQTT reply 已跑通；服务器已通过 11 段 P0/P1 全链路，最新结果 `dock3 e2e full-chain test ok (steps=11, elapsed=18.9s)`；当前默认 E2E 已接入 `takeoff-flow`；机巢模拟器的 UDP 注册、12004 状态回传和健康待命位已实跑；device 多设备拓扑专项和 E2E device 单段已通过服务器验证；`scripts/start_support_stack.sh` 已能中文汇总启动结果 | 服务化部署、完整严格 E2E、真实 SITL 航点执行未完成 | 固化服务化部署并补完整 `--fail-on-stale` 环境 | 人员3 |
| MQTT topic/reply 边界 | 已通过 | 92% | `services/services_reply`、`property/set_reply`、`requests/requests_reply`、`status/status_reply`、`drc/up` 基础分流已验证；人员1验收脚本已统一收敛到共享 MQTT 客户端，按 `tid + method + topic + Dock3格式` 过滤旧回包；OSD/state 已支持把旧 OSD/state 样本计入 `--fail-on-stale`；DRC 专项已输出 `stale_samples` 便于定位旧回包来源 | broker 中仍可能观察到旧进程产生的重复回包，需要清理环境后做严格验证 | 清理旧回包源，执行全链路 `--fail-on-stale` 严格验收 | 人员1 |
| DJI 错误码边界 | 已通过 | 90% | MQTT 对外回包不再返回内部 `-1/-2/-3`，统一映射 DJI 官方错误码；本地单测通过 | 各业务分支的精确错误码仍需随官方字段细化 | 每迁移一个大类补充错误码矩阵 | 人员1 |
| 日志与可观测性 | 已通过 | 90% | 日志按 `logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log` 落盘，MQTT 关键字段支持中文字段名；非阻塞 request trace JSONL 已通过 Ubuntu 复测；命令链 `command_trace` 已补 Dock3 properties/device/CMD/DRC/Wayline/media/live/HMS/log/firmware/config 中文动作映射，未知 method 会明确打印“未注册业务处理器”；11 段 E2E 已能输出分段耗时和最终汇总 | 压缩策略、真实 OSS 上传和 E2E 证据自动归档未完整实现 | 接入全链路证据归档 | 人员1/人员3 |
| 非阻塞与容错基础 | 部分完成 | 67% | 已修复若干 coroutine 未 await warning，停止流程可正常 await；数据保留和云同步已统一秒级/毫秒级时间戳过期判断，`manual_cleanup()` 已回传分类删除数并记录状态；`fileupload_list`、设备日志列表/上传、媒体列表/删除和上传优先级等文件 IO handler 已改为受控后台执行。Ubuntu 已通过回调回归 2/2、HMS/日志专项 `accepted=8, events=5`、媒体专项 `accepted=6, events=2` 和隔离 DB 数据保留专项，异步 reply 正确返回，控制类 handler 保持同步顺序 | MongoDB/NoSQL 目标未实现，固件和媒体上传仍需全面后台化审计；真实生产 DB 容量与并发压测未完成 | 扩展后台任务审计，补操作队列、生产 DB 容量与超时注入验收 | 人员1/人员2 |
| 短周期数据保留与操作记录 | 已通过 | 78% | `tools/database_retention_audit.py` 在 Ubuntu 独立临时 SQLite 库中验证：秒/毫秒过期时间可清理，遥测、已结束操作、告警和已上传媒体删除统计为 `1/2/1/1`；运行中操作、待上传媒体与新记录均被保留，`tests.test_retention_manager` 通过。脚本拒绝已有库和项目 `missions.db` | 生产库容量、长期归档和真实云同步策略仍需后续压测/精校 | 生产部署阶段补容量告警、归档与云同步审计 | 人员1 |
| 相机/云台基础控制 | 已通过 | 72% | `tools/mqtt_dock3_payload_test.py --timeout 20 --quiet-stale --fail-on-stale` 已在 Ubuntu 通过：相机/云台能力查询、云台转动/模式/复位状态回读、拍照、录像启停和重复停止录像失败码共 `accepted=12`、`ignored_total=0`。失败发送不会污染录像状态、媒体计数或云台缓存 | 当前为 MAVLink/SITL 命令发送闭环，未得到真实 Payload SDK/硬件 ACK | 接真实 Payload 后补 ACK、能力枚举和媒体文件实体证据 | 人员1 |
| Properties / OSD / State | 已通过 | 80% | 官方 `rw` 属性 `silent_mode`、`air_transfer_enable`、`user_experience_improvement` 已通过专项复测；11 段 E2E 的 OSD/state 阶段已在服务器通过，输出 `dock_osd=1, drone_osd=1, state=1, invalid=0`；脚本已忽略旧版 `reason=property_changed` state 和旧 helper 的部分 value-wrapper OSD 残留，当前有效样本仍按 Dock3 必填字段严格校验 | 客户需求涉及的单位、枚举、Dock3 真实设备字段仍需精校 | 补字段矩阵和真实 SITL 动态值验证 | 人员1 |
| Device 设备管理/拓扑 | 已通过 | 85% | `update_topo` 官方必填字段、在线/离线拓扑、无效拓扑拒绝、`status_reply` 已通过专项复测；服务器多设备专项已验证 `DRONE-SIM-001,DRONE-SIM-002` 上线、离线空拓扑、重连恢复和非法拓扑拒绝；organization 多设备绑定服务器复测已验证 `sub_devices=2` 和 `status_reply result=0`；服务器 device 解绑专项已验证 `device_unbinding events ok: count=3`、空 `update_topo`、`unbind=True`；E2E device 单段已通过 | `domain/type/sub_type` 官方产品枚举、真实云端字段差异未精校 | 精校设备类型枚举和真实云端字段 | 人员1/人员3 |
| Organization 组织管理 | 已通过 | 84% | `airport_bind_status`、`airport_organization_get`、`airport_organization_bind` 的成功、失败、超时、绑定后事件、`update_topo status_reply` 和 SQLite/内存绑定均已验证；服务器多设备绑定已通过，输出 3 次 `device_binding`、`update_topo schema ok ... sub_devices=2`、`memory binding ok`、`sub-device paired ok` 和 `organization request test ok`；device 解绑服务器专项已覆盖多设备解绑事件和空拓扑 | 真实云端差异仍需精校 | 继续真实云端字段精校 | 人员1 |
| DRC 指令飞行 | 已通过 | 85% | `drc_mode_enter/exit`、`drc_state` 同步、官方心跳、杆量、飞行器/负载控制、速度控制、`takeoff_to_point`、指点飞行更新/停止、非法字段和值域错误已通过专项和批量复测；服务器最新 DRC 专项已用 `property_get` 网关遥测生成电子围栏内目标点，`takeoff_to_point` 与 `fly_to_point_update` 均返回 `simulated=false`，输出 `dock3 drc test ok (accepted=15, events=8, ignored_mismatched=0, ignored_legacy=3, ignored_late=7, ignored_duplicate=7, ignored_unknown=0, ignored_total=10)`，并输出 `stale_samples` 定位重复旧回包 | 链路抖动、超时边界和严格清理旧回包环境仍需精校；Wayline 真实航点执行仍归入 G-P1-06 | 补 DRC 严格 `--fail-on-stale` 环境和 SITL 行为证据归档 | 人员1/人员2 |
| CMD 远程调试 | 已通过 | 80% | 调试模式、舱盖/充电/推杆、飞行器/设备动作、枚举开关、安全拒绝、格式化/重启模拟保护和进度事件已通过专项；服务器重跑验证 `cover_close` 后 `cover_state=0`，最新输出 `dock3 cmd test ok (accepted=60, events=18, ignored_total=60)`，E2E cmd 单段 `steps=1, elapsed=5.0s` | 真实设备重启/格式化/任务耗时/失败码未接真实硬件 | 补真实执行开关和失败码精校 | 人员1/人员3 |
| Wayline 航线管理 | 已通过 | 79% | `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel` 基础闭环已通过 Ubuntu 专项复测；mock KMZ 下载、校验、解析和内部任务转换已进入 `flighttask_prepare`；`flighttask_execute` 已触发机巢 `request_takeoff` 安全调用；修复机巢 UDP 注册和健康待命位后，服务器 strict Wayline 已通过 `accepted=11, events=12, ignored_total=0`；新增 `--real-sitl` 模式已在 Ubuntu 以非模拟分支上传 3 个 KMZ 航点并完成飞控握手，`accepted=1, events=2, ignored_total=0`，未请求 AUTO | 媒体计数、真实 SITL 执行/进度、返航事件字段仍未完成 | 在隔离 SITL 执行 AUTO 并保存进度/完成事件证据 | 人员1/人员2 |
| P0/P1 E2E 回归 | 已通过 | 98% | `tools/e2e_takeoff_flow_test.py --timeout 12` 已在服务器单独通过；`tools/e2e_full_chain_test.py --timeout 12 --quiet-stale` 已在服务器通过 11 段；清理旧回包并修复机巢 UDP 联调后，服务器已执行 `tools/e2e_full_chain_test.py --timeout 20 --quiet-stale --fail-on-stale`，properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config 11 段全部通过，输出 `steps=11, elapsed=26.7s`；最后 firmware/config 阶段输出 `accepted=8, events=10, ignored_total=0` | 真实硬件、真实推流、真实 OTA 未完成 | 保存证据并继续真实 SITL/硬件联动 | 人员1 |
| Dock 状态查询 | 部分完成 | 60% | `dock_status_query` 已能返回实时遥测快照和模拟机场状态 | 客户需求涉及的机场状态字段和单位仍需精校 | 纳入 properties/device 字段矩阵 | 人员1 |
| MAVLink 飞控联动 | 部分完成 | 60% | 网关能连接 SITL，Telemetry、FlightControl、ModeControl、WaypointManager、RTK 基础运行；已新增共享 ArduPilot flight mode 映射，Copter/Plane/QuadPlane 按 HEARTBEAT.type 选择，`set_flight_mode` 支持 `mode/mode_code/flight_mode` 并在底层失败时返回非 0 | 解锁/起飞/航线执行、非阻塞超时恢复仍需完善；模式切换需 Ubuntu SITL 复测 | 跑 set_flight_mode + SITL 模式切换专项 | 人员2 |
| MAVLink 机巢模拟器 | 部分完成 | 70% | 自定义 dialect、`DRONE_NEST_*` 消息、机巢连接路径已有基础联调；已补 UDP 首包注册心跳和健康待命位，Ubuntu 已观察到 12003 命令、12004 状态、12005 回复，Wayline strict 专项通过；`tools/nest_v13_protocol_test.py --verbose` 已扩展到 5 项检查；MissionManager 机巢动作改为安全调用，`flighttask_execute` 会触发 `request_takeoff` | 真实耗时、故障恢复和实体机巢仍需完善 | 补一键启航真实耗时状态机和实体机巢日志 | 人员2/人员3 |
| Media 媒体管理 | 已通过 | 55% | `file_list_get/file_delete/adjust_upload_priority/highest_priority_upload_flighttask_media` 支持本地目录扫描、删除、优先级状态和上传事件；Ubuntu 输出 `dock3 media test ok (accepted=6, events=2, ignored_total=0)` | 真实云端 OSS 凭证、断点续传和上传完成事件仍需精校 | 后续接入完整 E2E 复测和真实上传适配 | 人员1 |
| Live 直播功能 | 已通过 | 45% | `live_capability_query/live_start/live_set_quality/live_lens_change/live_stop` 已通过 Ubuntu 专项；输出 `dock3 live test ok (accepted=6, events=4, ignored_total=0)`；支持 `mock://` 纯模拟、状态回包和 `live_status_notify` 事件 | 真实 RTMP/WebRTC/GB28181 推流适配未完成 | 后续接真实推流 adapter 和高级协议能力 | 人员1/人员3 |
| HMS 管理 | 已通过 | 60% | `hms_get/hms_clear` 的 Dock3 `output.status`、告警结构规范化、模拟告警注入和 `hms` 事件已通过 Ubuntu 专项；`DeviceManager.update_from_nest()` 已把机巢 `fault/estop/emergency_landing/error_code/heartbeat lost` 同步为 Dock3 HMS，并发 `dock_operation` 证据事件 | 真实 HMS 文案 key、硬件故障源和 Ubuntu 机巢实跑证据仍需精校 | 接机巢故障位实跑证据和告警文案精校 | 人员1 |
| Firmware 固件升级 | 已通过 | 55% | `ota_create/ota_cancel` 已补 Dock3 `output.status`、`ota_progress/ota_result`、模拟升级、取消、非法载荷拒绝；新增 `firmware_upgrade/firmware_upgrade_cancel` 兼容别名；Ubuntu 输出 `dock3 firmware/config test ok (accepted=8, events=10, ignored_total=7)` | 当前是 mock OTA，可证明云端协议和状态机；真实下载、MD5、刷写、备份、回滚仍需接真实 adapter | 补真实 OTA adapter 边界和操作记录 | 人员1/人员3 |
| Remote Log 远程日志 | 已通过 | 60% | 本地日志落盘、日志列表扫描、`device_log_list_get`、`device_log_upload`、`fileupload_list/start/update`、上传进度事件和非法参数拒绝已补齐；Ubuntu 专项已验证 `device_log_list_get` 返回日志列表并发现本次测试日志 `edge_gateway_hms_log_test_1783563034390.log` | 日志压缩、真实 OSS 上传和超时策略未完整实现 | 补压缩和真实上传 adapter | 人员1/人员3 |
| Config 配置更新 | 已通过 | 65% | `config_update/config_get/config_rollback/config` 已补本地配置持久化、版本号、历史回滚、`config_update_result/config_rollback_result` 事件和非法载荷拒绝；Ubuntu 输出 `dock3 firmware/config test ok (accepted=8, events=10, ignored_total=7)` | 与真实云端 config 字段边界仍需精校；当前晚到旧回包统计不阻断但需后续清理环境做严格复测 | 接入真实字段矩阵和 `--fail-on-stale` 严格环境复测 | 人员1 |
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
| OSD / State | `python tools/mqtt_dock3_osd_state_test.py --timeout 15 --quiet-stale` | `dock3 osd/state test ok` |
| Device topology | `python tools/mqtt_dock3_device_test.py --timeout 12 --quiet-stale`；多设备加 `--extra-sub-device-json '{"sn":"DRONE-SIM-002","domain":"0","type":60,"sub_type":0,"index":"B"}'` | `dock3 device topology test ok` |
| Organization | `python tools/mqtt_organization_test.py --config config/gateway.yaml --action full ... --publish-events --expect-status-reply` | `organization request test ok` |
| DRC 专项 | `python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale --osd-wait 6` | `dock3 drc test ok`；如存在旧回包会输出 `stale_samples` |
| DRC 批量 | `python tools/mqtt_drc_batch_test.py --timeout 12 --quiet-stale` | `drc batch test ok` |
| CMD | `python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale` | `dock3 cmd test ok` |
| Wayline | `python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale` | `dock3 wayline test ok` |
| 一键启航离线闭环 | `python tools/e2e_takeoff_flow_test.py --timeout 12` | `dock3 takeoff flow test ok` |
| 当前核心 E2E | `python tools/e2e_full_chain_test.py --timeout 12 --quiet-stale` | `dock3 e2e full-chain test ok` |
| Media | `python tools/mqtt_dock3_media_test.py --timeout 12 --quiet-stale` | `dock3 media test ok` |
| Live | `python tools/mqtt_dock3_live_test.py --timeout 12 --quiet-stale` | `dock3 live test ok` |
| HMS/Log | `python tools/mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale` | `dock3 hms/log test ok` |
| Firmware/Config | `python tools/mqtt_dock3_firmware_config_test.py --timeout 20 --quiet-stale` | `dock3 firmware/config test ok` |
| 机巢 v1.3 离线专项 | `python tools/nest_v13_protocol_test.py --verbose` | `nest v1.3 protocol test ok (checks=5)` |

## 当前待复测命令

| 大类 | Ubuntu 验收命令 | 成功标志 |
|---|---|---|
| P0-07 飞行模式映射 | `python -m unittest tests.test_flight_modes tests.test_mavlink_telemetry_mapping`；连接 SITL 后依次执行 `python tools/mqtt_smoke_test.py --method set_flight_mode --payload '{"mode":"GUIDED"}' --timeout 12`、`python tools/mqtt_smoke_test.py --method set_flight_mode --payload '{"mode_code":9}' --timeout 12` | 单测通过；MQTT 回包 `result=0` 且 `data.mode` 分别为 `GUIDED`、`LAND`；SITL 日志不再出现关键模式 `UNKNOWN(9)` |

## 下一批建议

| 优先级 | 模块 | 客户来源 | 预期交付 |
|---:|---|---|---|
| 1 | 机巢物理控制联动 + 机巢真实状态/告警 | `项目优先级整理.docx` 高：机巢物理控制联动、机巢状态真实同步、机巢告警真实获取 | 网关到机巢模拟器实跑证据、状态机拒绝原因、HMS/operation record、CMD 进阶失败码 |
| 2 | 真实航线/SITL + DRC 控制优化 | `项目优先级整理.docx` 高：航线任务真实上传、真实进度、flyto/takeoff_to_point 进度、DRC 速度/航向/高度优化 | KMZ/WPML 到 MAVLink Mission、真实 SITL 航点执行、距离/高度进度计算、type_mask/坐标系/航向高度专项 |
| 3 | OTA/Firmware/Config | `项目优先级整理.docx` 高：OTA真实下载与刷写，含断点续传、MD5校验、备份、回滚 | `mqtt_dock3_firmware_config_test.py`、升级任务状态、进度事件、失败码、配置持久化/回滚 |
| 4 | OSD 字段补全 + HMS 真实触发 | `项目优先级整理.docx` 高/中：OSD 字段补全、HMS 告警真实触发、多设备 OSD | 字段矩阵、wireless_link/maintain_status 等缺省字段、真实告警源映射、多设备 OSD |
| 5 | 真实媒体/直播 + 相机变焦/对焦 | `项目优先级整理.docx` 高：直播推流实现、文件列表真实获取、变焦真实控制、对焦真实控制 | 文件系统扫描、真实推流 adapter、OSS/上传边界、相机/云台 adapter 或明确硬件不可用失败码 |
| 6 | NoSQL/非阻塞/证据归档 | `网关和平台侧重点.docx`：网关只存实时断点和短周期数据 | 异步数据层方案、短周期保留策略、压测/超时注入、E2E 证据归档 |
