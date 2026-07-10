# P0/P1 实现缺口登记

更新时间：2026-07-10

本文档把 `docs/P0_P1_P2_DELIVERY_BACKLOG.md` 中的 P0/P1 项进一步落到代码入口、缺口、验收脚本和 30 天窗口。它用于指导开发排程，不替代接口字段说明。

客户三文档章节到 backlog 和 gap 的追溯关系见 `docs/CUSTOMER_REQUIREMENTS_TRACEABILITY_MATRIX.md`。

缺口的日级执行顺序见 `docs/30_DAY_EXECUTION_BOARD.md`。

执行原则：

- P0/P1 是 30 天硬验收范围，不能以 P2 风险或真实硬件缺失为理由空缺。
- 本文只登记网关侧缺口：硬件控制、任务状态机、实时遥测、设备拓扑、本地短周期数据、安全兜底、升级执行和设备侧 adapter。
- 平台侧台账、权限、前端页面、业务工单、多设备批量调度、全量历史统计、长期数据仓库、媒体资源库和告警通知推送不作为本网关缺口。
- 每个缺口关闭时必须同时更新代码、专项脚本或 E2E 脚本、对应文档状态。
- 任何缺口如果只能通过 mock 完成，必须明确 mock adapter 边界，并保证 Dock3 云端行为正确。
- 任何新增脚本必须纳入 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md`。

状态口径：

| 状态 | 含义 |
|---|---|
| Open | 未开始或只有旧实现/占位 |
| In Progress | 已有实现方向，但缺验收证据 |
| Needs Test | 代码已有，缺专项或 E2E 验收 |
| Closed | 代码、脚本、文档证据均完成 |

## 1. 客户高优先级缺口队列

该队列直接来自 `项目优先级整理.docx` 的模块级“高/中/低”排序，用于决定实际开发先后。下面的 P0/P1 gap 表仍保留交付编号，但执行时先按本表处理。

| 顺序 | 客户优先级 | 必先关闭的缺口 | 对应 Gap | 当前动作 |
|---:|---|---|---|---|
| 1 | 高 | 机巢物理控制联动、机巢状态真实同步、机巢告警真实获取 | G-P0-04、G-P0-05、G-P1-02、G-P1-15 | 已补机巢状态到 Dock3 state/HMS/dock_operation 的本地桥接和离线专项 checks=5；下一步补 Ubuntu 网关 + 机巢模拟器实跑证据、状态机拒绝原因 |
| 2 | 高 | 航线任务真实上传、KMZ/WPML、MAVLink Mission、真实进度、FlyTo/takeoff_to_point 进度 | G-P0-03、G-P1-04、G-P1-05、G-P1-06 | 把 mock Wayline 推进到真实 SITL 航点执行和距离/高度进度计算 |
| 3 | 高 | DRC 速度/航向/高度控制优化 | G-P1-03、G-P1-04 | 保留已通过云端模拟证据，补真实 MAVLink type_mask、坐标系、航向和高度控制专项 |
| 4 | 高 | OTA 真实下载、校验、刷写、备份、回滚和飞机 OTA 透传 | G-P1-12、G-P1-13 | firmware/config 模拟闭环已通过 Ubuntu MQTT 专项；下一步接真实 OTA adapter 边界和操作记录 |
| 5 | 高 | OSD 字段补全，含无线链路、维护状态、电池/GPS/云台字段 | G-P0-01 | 在已通过 OSD/state 专项基础上补字段矩阵和真实值校验 |
| 6 | 高 | 直播真实推流、文件列表真实获取、媒体真实扫描 | G-P1-07、G-P1-08 | mock 通过不等于客户高优先级完成，补真实 adapter 或明确可验收 mock 边界 |
| 7 | 高 | 相机变焦真实控制、对焦真实控制 | G-P1-09 | 接 MAVLink/SDK adapter 或返回明确硬件不可用错误码，不再只更新内存状态 |
| 8 | 中 | 设备重启/格式化/重置、HMS 真实触发、机巢环境、多设备 OSD、航线上传同步、NTRIP | G-P0-01、G-P1-02、G-P1-14、G-P1-16 | 高优先级闭合后补 |
| 9 | 低 | 仿真模式、任务资源获取、镜头切换、热成像、降落伞、HSI/延迟等 | P2/Adapter | 不抢占 P0/P1 高优先级 |

## 2. P0 缺口

| ID | Backlog | 当前缺口 | 主要代码入口 | 必补验收 | 目标窗口 | 状态 |
|---|---|---|---|---|---|---|
| G-P0-01 | P0-06 OSD 基础遥测 | OSD/state 专项已通过 Ubuntu 复测，且飞行器 OSD 已出现 SITL 动态经纬度、电池、GPS；剩余是客户需求相关 Dock3 字段单位、枚举和值域逐项精校，客户未用字段可 Deferred | `src/mavlink/telemetry.py`, `src/cloud_api/telemetry_reporter.py`, `src/device/manager.py` | 字段矩阵精校 + 真实 SITL 动态 OSD 验证 | Day 4-7 | In Progress |
| G-P0-02 | P0-07 飞行模式映射 | 已新增共享 ArduPilot 模式映射表，Copter/Plane/QuadPlane 按 HEARTBEAT.type 选择；`set_flight_mode` 已兼容 `mode/mode_code/flight_mode` 且失败回非 0，仍需 Ubuntu SITL 复测 | `src/mavlink/flight_modes.py`, `src/modules/mode_control.py`, `src/modules/flight_control.py`, `src/modules/telemetry_module.py`, `src/mission/manager.py` | SITL 模式切换专项；E2E 中检查 `mode_code` | Day 4-7 | Needs Test |
| G-P0-03 | P0-08 基础飞控控制 | 起飞、FlyTo、返航、降落、解锁、任务控制目前部分停留在模拟回包或未完整 SITL 行为验证 | `src/modules/flight_control.py`, `src/drc/controller.py`, `src/mission/manager.py` | DRC/Wayline/SITL 联合专项 | Day 8-15 | In Progress |
| G-P0-04 | P0-12 机巢 v1.3 基础通信 | 已新增离线专项覆盖 12003/12004/12005、命令字、状态位和拒绝条件；仍需 Ubuntu 网关 + 机巢模拟器实跑留证 | `src/modules/drone_nest_module.py`, `src/nest_simulator.py`, `src/generated_dialect/drone_nest.py`, `tools/nest_v13_protocol_test.py` | `tools/nest_v13_protocol_test.py` + 网关/模拟器联调日志 | Day 8-11 | Needs Test |
| G-P0-05 | P0-13 机巢关键状态 | `boolstate1/boolstate2`、舱盖、夹紧、充电、急停、故障、备飞已有专项覆盖；本地已补机巢状态到 Dock3 state/HMS/dock_operation 的桥接，真实 Dock3 枚举和 Ubuntu 机巢实跑证据仍需补齐 | `src/device/nest_property_mapper.py`, `src/device/nest_hms_mapper.py`, `src/device/manager.py`, `src/cloud_api/dock3_cmd.py`, `tools/nest_v13_protocol_test.py` | `nest_v13_protocol_test.py --verbose` + `mqtt_dock3_cmd_test.py` + 网关/机巢实跑 HMS/operation evidence | Day 8-11 | In Progress |
| G-P0-06 | P0-15 Request Trace | Ubuntu properties 专项后 trace 断言已通过，能按 method 汇总 `mqtt_received/mqtt_handler_completed/mqtt_publish_reply`，并统计 Dock3 property/set 逐属性 `result=0/1` | `src/mqtt/client.py`, `src/cloud_api/connection_manager.py`, `src/utils/request_trace.py`, `tools/request_trace_query.py` | 后续全链路 E2E 保存 trace 证据 | Day 4-10 | Closed |
| G-P0-07 | P0-16 非阻塞主链路 | 已将 `fileupload_list`、`device_log_list_get/upload`、`file_list_get/file_delete`、`adjust_upload_priority` 等目录扫描/删除 handler 移出 Paho MQTT 网络回调线程；Ubuntu 已通过 `tests.test_mqtt_background_handlers`（2/2）、HMS/日志专项（`accepted=8, events=5`）和媒体专项（`accepted=6, events=2`），证明后台 reply 可达且控制类 handler 保持同步顺序。2026-07-10 broker 审计确认严格 properties 的旧回包来自 `192.168.2.94` 上的 `dock3_sim_EDGEGW001_1783480416`；停止后严格 properties 已通过 `accepted=10` 且所有 `ignored_*=0`，证明当前网关不存在 QoS 重投或 broker retained 干扰。DB、固件、媒体上传和 MAVLink 超时仍需系统性审计及 Ubuntu 压测 | `src/mqtt/client.py`, `src/main.py`, `src/database/*`, `src/media/manager.py`, `src/modules/log_manager.py`, `src/device/manager.py`, `tests/test_mqtt_background_handlers.py`, `scripts/mqtt_broker_audit.sh` | 执行完整 strict E2E；再补 E2E 压测、DB 和上传超时注入 | Day 8-15 | In Progress |
| G-P0-08 | P0-17 全链路最小闭环 | 当前核心 MQTT 域已有一键 E2E：properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config 11 段已通过服务器全量复测；旧模拟器清理后 strict properties 已通过，`--fail-on-stale` 已覆盖 OSD/state 旧样本统计 | `tools/e2e_full_chain_test.py`, `tools/mqtt_test_client.py`, `tools/mqtt_dock3_osd_state_test.py` | 在当前干净环境完成服务器完整严格 `--fail-on-stale` E2E 复测 | Day 25-27 | Closed |

## 3. P1 缺口

| ID | Backlog | 当前缺口 | 主要代码入口 | 必补验收 | 目标窗口 | 状态 |
|---|---|---|---|---|---|---|
| G-P1-01 | P1-01 一键启航主流程 | 一键启航离线闭环已可重复验证：mock KMZ、`flighttask_prepare -> flighttask_execute -> complete_simulated_mission`、`flighttask_ready/flighttask_progress`、机巢 `request_takeoff` 以及 `one_key_takeoff_flow` 本地编排均已覆盖；真实 MQTT/SITL/机巢耗时状态机仍待实跑精校 | `src/mission/manager.py`, `src/main.py`, `src/cloud_api/dock3_wayline.py`, `src/cloud_api/dock3_cmd.py`, `src/modules/drone_nest_module.py`, `tools/e2e_takeoff_flow_test.py`, `tools/mqtt_one_key_takeoff_flow_test.py`, `tests/test_one_key_takeoff_flow.py` | `tools/e2e_takeoff_flow_test.py` + 11 段 E2E；Ubuntu MQTT 执行 `python tools/mqtt_one_key_takeoff_flow_test.py --simulate --timeout 60 --quiet-stale`；后续补真实机巢/SITL 日志 | Day 12-15 | Closed |
| G-P1-02 | P1-02 机巢 v1.3 完整状态机 | 离线专项已覆盖 busy/fault/estop/R 轴/允许起飞、拒绝原因、HMS 映射和 operation 事件基础口径；一键启航离线 E2E 已通过，仍缺真实机巢耗时动作和拒绝原因的端到端状态机证据 | `src/modules/drone_nest_module.py`, `src/device/nest_property_mapper.py`, `src/device/nest_hms_mapper.py`, `src/device/manager.py`, `tools/nest_v13_protocol_test.py`, `tools/e2e_takeoff_flow_test.py` | `tools/nest_v13_protocol_test.py --verbose` + 真实网关/机巢模拟器联调日志 | Day 8-11 | In Progress |
| G-P1-03 | P1-03 DRC 心跳和超时恢复 | DRC 官方心跳、状态事件、超时退出基础闭环已进入专项脚本；服务器最新 DRC 专项输出 `dock3 drc test ok (accepted=15, events=7, ignored_duplicate=8, ignored_unknown=0, ignored_total=9)` | `src/drc/controller.py`, `src/cloud_api/dock3_drc.py`, `src/device/manager.py` | 保留链路抖动和严格旧回包清理到后续专项 | Day 12-15 | Closed |
| G-P1-04 | P1-04 指点飞行/起飞到点 | `takeoff_to_point`、指点飞行更新/停止和导航事件已通过 Dock3 专项；服务器复测已用 `property_get` 生成电子围栏内目标点，`takeoff_to_point` 和 `fly_to_point_update` 返回 `simulated=false` | `src/drc/controller.py`, `src/modules/flight_control.py`, `src/mission/manager.py` | 保存 SITL 行为证据并与 G-P1-06 Wayline 真实航点区分 | Day 12-15 | Closed |
| G-P1-05 | P1-05 KMZ/WPML 基础解析 | Wayline 已支持 mock KMZ 下载/校验/解析并转换到内部任务准备链路；最新 E2E Wayline 阶段输出 `dock3 wayline test ok (accepted=11, events=9, ignored_total=0)` | `src/mission/manager.py`, `src/modules/waypoint_manager.py`, `src/database/mission_db.py` | 真实 SITL 航点执行继续归入 G-P1-06 | Day 12-15 | Closed |
| G-P1-06 | P1-06 真实 SITL 航点执行 | 当前 Wayline 脚本以纯模拟为主，不证明真实 SITL 航点执行 | `src/modules/waypoint_manager.py`, `src/modules/flight_control.py`, `src/mission/manager.py` | SITL Wayline E2E | Day 12-15 | Open |
| G-P1-07 | P1-07 媒体基础 | Dock3 media 文件列表、删除、上传优先级和上传事件已通过 Ubuntu 专项；输出 `dock3 media test ok (accepted=6, events=2, ignored_total=0)` | `src/cloud_api/dock3_media.py`, `src/media/manager.py`, `tools/mqtt_dock3_media_test.py` | 真实 OSS 凭证、断点续传和上传完成事件后续精校 | Day 16-20 | Closed |
| G-P1-08 | P1-08 直播基础 | Dock3 live mock 推流已通过 Ubuntu 网关实跑；`live_capability_query/live_start/live_set_quality/live_lens_change/live_stop` 均返回 Dock3 `output.status`，`live_status_notify` 事件正常，非法 `live_start` 返回 `325001/rejected`，输出 `dock3 live test ok (accepted=6, events=4, ignored_total=0)` | `src/cloud_api/dock3_live.py`, `src/media/manager.py`, `tools/mqtt_dock3_live_test.py` | 后续真实 RTMP/WebRTC/GB28181 adapter 精校 | Day 16-20 | Closed |
| G-P1-09 | P1-09 相机/云台基础控制 | 拍照、录像、变焦、云台角度/速度需要和 Dock3 payload/DRC 语义统一；历史 `web/mavlink/viewlink/` 只作 adapter 参考，不作为 Web 交付 | `src/payload/camera.py`, `src/payload/gimbal.py`, `src/modules/gimbal_control.py` | Payload 专项或并入 media/live | Day 16-20 | In Progress |
| G-P1-10 | P1-10 HMS 告警 | Dock3 HMS 查询/清除的 `output.status`、告警结构规范化、模拟注入告警和 `hms` 事件已通过 Ubuntu 专项；机巢 `fault/estop/emergency_landing/error_code/heartbeat lost` 到 HMS 的本地映射已补齐，真实 HMS 文案 key 和硬件故障源仍需实跑精校 | `src/cloud_api/dock3_hms_log.py`, `src/database/alarm_db.py`, `src/device/nest_hms_mapper.py`, `src/device/manager.py` | `tools/mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale` + 机巢故障位实跑证据 | Day 21-24 | Closed |
| G-P1-11 | P1-11 远程日志 | 本地日志扫描、`device_log_list_get/device_log_upload`、`fileupload_list/start/update`、上传进度事件和非法参数拒绝已通过 Ubuntu 专项；测试输出 `dock3 hms/log test ok (accepted=8, events=5, ignored_total=7)` | `src/cloud_api/dock3_hms_log.py`, `src/modules/log_manager.py`, `src/utils/logger.py` | `tools/mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale` | Day 21-24 | Closed |
| G-P1-12 | P1-12 固件升级模拟 | `ota_create/ota_cancel/firmware_upgrade` 的模拟任务、进度事件、结果事件、失败码和后台任务模型已通过 Ubuntu MQTT 专项；真实 OTA adapter 后续单列优化 | `src/device/manager.py`, `tools/mqtt_dock3_firmware_config_test.py`, `tests/test_device_manager_firmware_config.py` | `tools/mqtt_dock3_firmware_config_test.py --timeout 20 --quiet-stale` | Day 21-24 | Closed |
| G-P1-13 | P1-13 配置更新 | 配置下发、查询、持久化、版本号、回滚和状态事件已通过 Ubuntu MQTT 专项；真实云端字段精校后续单列优化 | `src/device/manager.py`, `tools/mqtt_dock3_firmware_config_test.py`, `tests/test_device_manager_firmware_config.py` | `tools/mqtt_dock3_firmware_config_test.py --timeout 20 --quiet-stale` | Day 21-24 | Closed |
| G-P1-14 | P1-14 多设备/解绑/离线 | 已补 `DeviceManager` 子设备缓存语义：空拓扑只标记离线，不丢已知子设备；重连保留 paired 状态；解绑会清空子设备并上报空拓扑。服务器已通过 `tools/mqtt_dock3_device_test.py --timeout 12 --quiet-stale --extra-sub-device-json ...`，覆盖双子设备上线、离线空拓扑、重连恢复、非法拓扑拒绝、逐 SN `device_unbinding` 事件和解绑后空 `update_topo`，输出 `device_unbinding events ok: count=3`、`reconnect=True`、`unbind=True`；E2E device 单段也已通过。organization 多设备绑定已通过服务器 MQTT 复测：`airport_organization_bind` 同时绑定网关和两个子设备，成功后更新绑定缓存、`paired` 状态、3 次 `device_binding` 事件和 `update_topo sub_devices=2`，并收到 `status_reply result=0`。剩余是真实云端字段精校 | `src/device/manager.py`, `src/cloud_api/dock3_device.py`, `src/cloud_api/connection_manager.py`, `tools/mqtt_dock3_device_test.py`, `tools/mqtt_organization_test.py`, `tools/e2e_full_chain_test.py` | 继续真实云端字段精校 | Day 21-24 | Closed |
| G-P1-15 | P1-15 CMD 进阶失败码 | CMD 模拟链路已通过服务器重跑：`accepted=60, events=18`，覆盖安全拒绝、格式化/重启模拟保护、任务进度事件、`cover_close` 状态回读和 E2E cmd 单段；真实硬件耗时和执行开关继续作为 adapter 精校风险 | `src/cloud_api/dock3_cmd.py`, `src/device/manager.py`, `src/database/operation_db.py` | 清理旧回包源后追加 `--fail-on-stale` 严格复测 | Day 21-24 | Closed |
| G-P1-16 | P1-16 网关短周期数据保留和操作记录 | 已补数据保留/云同步混合时间戳过期判断：秒级历史字段和毫秒级 Dock3 事件字段均可正确清理；仍缺真实 DB 清理专项和操作记录留存验收 | `src/database/time_utils.py`, `src/database/retention_manager.py`, `src/database/operation_db.py`, `src/database/telemetry_db.py`, `src/database/alarm_db.py` | DB/日志专项或 E2E 检查 | Day 21-24 | In Progress |
| G-P1-17 | P1-17 P0/P1 E2E 回归 | 当前已覆盖已通过 Ubuntu 的 P0/P1 核心 MQTT 域，`tools/e2e_full_chain_test.py` 已接入 properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config 11 段；服务器全量 E2E 已通过，严格 stale 参数已覆盖 OSD/state 阶段 | `tools/e2e_full_chain_test.py`, `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` | 后续执行服务器严格 `--fail-on-stale` 环境并保存证据 | Day 25-27 | Closed |

## 4. 缺失脚本优先级

| 顺序 | 脚本 | 关闭缺口 |
|---:|---|---|
| 1 | `tools/nest_v13_protocol_test.py` | 已新增离线专项，覆盖 G-P0-04、G-P0-05、G-P1-02 的协议基础、状态位和 HMS 映射；后续补网关/模拟器联调证据 |
| 2 | 扩展 `tools/mqtt_dock3_drc_test.py` / `tools/mqtt_drc_batch_test.py` | G-P1-03、G-P1-04 已完成当前 Dock3 云端模拟闭环；后续补 G-P1-06 真实 SITL 语义 |
| 3 | 扩展 `tools/mqtt_dock3_wayline_test.py` | G-P1-05 已完成 mock KMZ 基础解析；G-P1-06 真实 SITL 航点执行待补 |
| 4 | `tools/e2e_takeoff_flow_test.py` | G-P1-01 |
| 5 | `tools/mqtt_dock3_media_test.py` | G-P1-07 已通过 Ubuntu 专项 |
| 6 | `tools/mqtt_dock3_live_test.py` | G-P1-08 已通过 Ubuntu 专项 |
| 7 | `tools/mqtt_dock3_hms_log_test.py` | G-P1-10、G-P1-11 已通过 Ubuntu 专项 |
| 8 | `tools/mqtt_dock3_firmware_config_test.py` | 已通过 Ubuntu MQTT 专项，关闭 G-P1-12、G-P1-13 |
| 9 | 扩展 device/organization/cmd 测试 | G-P1-14、G-P1-15 已通过服务器专项或 E2E 单段；后续只保留真实云端/硬件字段精校 |
| 10 | `tools/e2e_full_chain_test.py` | 已接入当前已通过专项的 11 个 P0/P1 域，服务器 11 段全量复测已通过 |
| 11 | DB/日志专项 | G-P1-16 已补混合时间戳清理基础，下一步补真实 DB 清理和操作记录留存验收 |

## 5. 关项规则

| 规则 | 要求 |
|---|---|
| 代码关项 | 对应 handler、adapter、状态机或数据层实现已完成 |
| 脚本关项 | 专项脚本或 E2E 脚本能稳定复现成功和关键失败分支 |
| 文档关项 | backlog、runbook、模块状态和迁移进度同步更新 |
| 证据关项 | 有脚本输出、日志 tid/bid、MQTT 样例、trace 或 DB 记录 |
| P2 例外 | 只有 P2 可带问题；P0/P1 不允许以“后续真实硬件再补”为关项理由 |
