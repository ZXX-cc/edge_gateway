# P0/P1 实现缺口登记

更新时间：2026-07-07

本文档把 `docs/P0_P1_P2_DELIVERY_BACKLOG.md` 中的 P0/P1 项进一步落到代码入口、缺口、验收脚本和 30 天窗口。它用于指导开发排程，不替代接口字段说明。

客户 Word 文档章节到 backlog 和 gap 的追溯关系见 `docs/CUSTOMER_REQUIREMENTS_TRACEABILITY_MATRIX.md`。

缺口的日级执行顺序见 `docs/30_DAY_EXECUTION_BOARD.md`。

执行原则：

- P0/P1 是 30 天硬验收范围，不能以 P2 风险或真实硬件缺失为理由空缺。
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

## 1. P0 缺口

| ID | Backlog | 当前缺口 | 主要代码入口 | 必补验收 | 目标窗口 | 状态 |
|---|---|---|---|---|---|---|
| G-P0-01 | P0-06 OSD 基础遥测 | OSD/state 专项已通过 Ubuntu 复测，且飞行器 OSD 已出现 SITL 动态经纬度、电池、GPS；剩余是客户需求相关 Dock3 字段单位、枚举和值域逐项精校，客户未用字段可 Deferred | `src/mavlink/telemetry.py`, `src/cloud_api/telemetry_reporter.py`, `src/device/manager.py` | 字段矩阵精校 + 真实 SITL 动态 OSD 验证 | Day 4-7 | In Progress |
| G-P0-02 | P0-07 飞行模式映射 | 已新增共享 ArduPilot 模式映射表，Copter/Plane/QuadPlane 按 HEARTBEAT.type 选择；`set_flight_mode` 已兼容 `mode/mode_code/flight_mode` 且失败回非 0，仍需 Ubuntu SITL 复测 | `src/mavlink/flight_modes.py`, `src/modules/mode_control.py`, `src/modules/flight_control.py`, `src/modules/telemetry_module.py`, `src/mission/manager.py` | SITL 模式切换专项；E2E 中检查 `mode_code` | Day 4-7 | Needs Test |
| G-P0-03 | P0-08 基础飞控控制 | 起飞、FlyTo、返航、降落、解锁、任务控制目前部分停留在模拟回包或未完整 SITL 行为验证 | `src/modules/flight_control.py`, `src/drc/controller.py`, `src/mission/manager.py` | DRC/Wayline/SITL 联合专项 | Day 8-15 | In Progress |
| G-P0-04 | P0-12 机巢 v1.3 基础通信 | 自定义消息已存在，但缺 12003/12004/12005 协议级专项脚本和拒绝条件验证 | `src/modules/drone_nest_module.py`, `src/nest_simulator.py`, `src/generated_dialect/drone_nest.py` | 新增 `tools/nest_v13_protocol_test.py` | Day 8-11 | Open |
| G-P0-05 | P0-13 机巢关键状态 | `boolstate1/boolstate2`、舱盖、夹紧、充电、急停、故障、备飞到 Dock3 state/properties/HMS 的映射仍不完整 | `src/device/nest_property_mapper.py`, `src/device/manager.py`, `src/cloud_api/dock3_cmd.py` | `nest_v13_protocol_test.py` + `mqtt_dock3_cmd_test.py` | Day 8-11 | In Progress |
| G-P0-06 | P0-15 Request Trace | Ubuntu properties 专项后 trace 断言已通过，能按 method 汇总 `mqtt_received/mqtt_handler_completed/mqtt_publish_reply`，并统计 Dock3 property/set 逐属性 `result=0/1` | `src/mqtt/client.py`, `src/cloud_api/connection_manager.py`, `src/utils/request_trace.py`, `tools/request_trace_query.py` | 后续全链路 E2E 保存 trace 证据 | Day 4-10 | Closed |
| G-P0-07 | P0-16 非阻塞主链路 | DB、文件、媒体、日志、固件等路径需要系统性审计，缺自动化卡顿/超时验证 | `src/database/*`, `src/media/manager.py`, `src/modules/log_manager.py`, `src/device/manager.py` | E2E 压测和超时注入 | Day 8-15 | Open |
| G-P0-08 | P0-17 全链路最小闭环 | 当前核心 MQTT 域已有一键 E2E：`tools/e2e_full_chain_test.py --timeout 12 --quiet-stale` 串联 properties、device、OSD/state、CMD、DRC、Wayline 6 段，Ubuntu 输出 `dock3 e2e full-chain test ok (steps=6, elapsed=19.0s)`，各阶段 `ignored_total=0` | `tools/e2e_full_chain_test.py`, `tools/mqtt_test_shared.py` | 后续把 media/live/HMS/log/config 接入同一入口 | Day 25-27 | Closed |

## 2. P1 缺口

| ID | Backlog | 当前缺口 | 主要代码入口 | 必补验收 | 目标窗口 | 状态 |
|---|---|---|---|---|---|---|
| G-P1-01 | P1-01 一键启航主流程 | 起飞前检查、开盖、断充、释放、起飞、任务、返航、降落、充电还没有可重复 E2E | `src/mission/manager.py`, `src/cloud_api/dock3_wayline.py`, `src/cloud_api/dock3_cmd.py`, `src/modules/drone_nest_module.py` | 新增 `tools/e2e_takeoff_flow_test.py` | Day 12-15 | Open |
| G-P1-02 | P1-02 机巢 v1.3 完整状态机 | 使用条件、拒绝原因、busy/fault/estop/R 轴/允许起飞未全部进入状态机和错误码 | `src/modules/drone_nest_module.py`, `src/device/nest_property_mapper.py`, `src/device/manager.py` | `tools/nest_v13_protocol_test.py` | Day 8-11 | Open |
| G-P1-03 | P1-03 DRC 心跳和超时恢复 | DRC 官方心跳、状态事件、超时退出基础闭环已进入专项脚本；最新 E2E DRC 阶段输出 `dock3 drc test ok (accepted=14, events=7, ignored_total=0)` | `src/drc/controller.py`, `src/cloud_api/dock3_drc.py`, `src/device/manager.py` | 保留真实 MAVLink/链路抖动边界到 SITL 专项 | Day 12-15 | Closed |
| G-P1-04 | P1-04 指点飞行/起飞到点 | `takeoff_to_point`、指点飞行更新/停止和导航事件在 Dock3 云端模拟链路已通过，E2E DRC 阶段纳入回归；真实 SITL 语义继续归入 G-P1-06 | `src/drc/controller.py`, `src/modules/flight_control.py`, `src/mission/manager.py` | SITL 指点飞行语义复测 | Day 12-15 | Closed |
| G-P1-05 | P1-05 KMZ/WPML 基础解析 | Wayline 已支持 mock KMZ 下载/校验/解析并转换到内部任务准备链路；最新 E2E Wayline 阶段输出 `dock3 wayline test ok (accepted=11, events=9, ignored_total=0)` | `src/mission/manager.py`, `src/modules/waypoint_manager.py`, `src/database/mission_db.py` | 真实 SITL 航点执行继续归入 G-P1-06 | Day 12-15 | Closed |
| G-P1-06 | P1-06 真实 SITL 航点执行 | 当前 Wayline 脚本以纯模拟为主，不证明真实 SITL 航点执行 | `src/modules/waypoint_manager.py`, `src/modules/flight_control.py`, `src/mission/manager.py` | SITL Wayline E2E | Day 12-15 | Open |
| G-P1-07 | P1-07 媒体基础 | Dock3 media 文件列表、删除、上传优先级、上传事件未迁移 | `src/media/manager.py`, `src/database/media_db.py`, `src/payload/camera.py` | 新增 `tools/mqtt_dock3_media_test.py` | Day 16-20 | Open |
| G-P1-08 | P1-08 直播基础 | Dock3 live 开始/停止、质量、镜头、状态事件未迁移；推流可先 mock | `src/media/manager.py`, `src/payload/camera.py` | 新增 `tools/mqtt_dock3_live_test.py` | Day 16-20 | Open |
| G-P1-09 | P1-09 相机/云台基础控制 | 拍照、录像、变焦、云台角度/速度需要和 Dock3 payload/DRC 语义统一；历史 `web/mavlink/viewlink/` 只作 adapter 参考，不作为 Web 交付 | `src/payload/camera.py`, `src/payload/gimbal.py`, `src/modules/gimbal_control.py` | Payload 专项或并入 media/live | Day 16-20 | In Progress |
| G-P1-10 | P1-10 HMS 告警 | Dock3 HMS 文案 key、告警结构、恢复/清除和事件未迁移 | `src/database/alarm_db.py`, `src/device/manager.py` | 新增 `tools/mqtt_dock3_hms_log_test.py` | Day 21-24 | Open |
| G-P1-11 | P1-11 远程日志 | 日志列表、压缩、上传模拟、超时和失败码未按 Dock3 验收 | `src/modules/log_manager.py`, `src/database/operation_db.py`, `src/utils/logger.py` | `tools/mqtt_dock3_hms_log_test.py` | Day 21-24 | Open |
| G-P1-12 | P1-12 固件升级模拟 | 升级任务、进度、失败码和后台任务模型未完成 Dock3 验收 | `src/device/manager.py`, `src/database/operation_db.py` | 新增 `tools/mqtt_dock3_firmware_config_test.py` | Day 21-24 | Open |
| G-P1-13 | P1-13 配置更新 | 配置下发、持久化、回滚和状态事件未完成 Dock3 验收 | `src/device/manager.py`, `src/cloud_api/connection_manager.py`, `src/database/device_state_db.py` | `tools/mqtt_dock3_firmware_config_test.py` | Day 21-24 | Open |
| G-P1-14 | P1-14 多设备/解绑/离线 | 当前单子设备拓扑、组织绑定、绑定后 `device_binding/update_topo` 和 `status_reply` 已通过；仍需多子设备、解绑、离线、重连回归 | `src/device/manager.py`, `src/cloud_api/dock3_device.py`, `src/cloud_api/connection_manager.py` | 扩展 `tools/mqtt_dock3_device_test.py` 和 `tools/mqtt_organization_test.py` | Day 21-24 | In Progress |
| G-P1-15 | P1-15 CMD 进阶失败码 | CMD 基础已通过，但真实耗时、失败码、安全拒绝和任务进度需更贴近 Dock3 | `src/cloud_api/dock3_cmd.py`, `src/device/manager.py`, `src/database/operation_db.py` | 扩展 `tools/mqtt_dock3_cmd_test.py` | Day 21-24 | Needs Test |
| G-P1-16 | P1-16 数据保留和操作记录 | 操作记录、遥测保留、告警保留和清理策略有基础，但缺 P1 验收证据 | `src/database/retention_manager.py`, `src/database/operation_db.py`, `src/database/telemetry_db.py`, `src/database/alarm_db.py` | DB/日志专项或 E2E 检查 | Day 21-24 | In Progress |
| G-P1-17 | P1-17 P0/P1 E2E 回归 | 当前已覆盖已迁移的 P0/P1 核心 MQTT 域，`tools/e2e_full_chain_test.py --timeout 12 --quiet-stale` 一键输出 6 段成功；未迁移的 media/live/HMS/log/config 后续按域加入 | `tools/e2e_full_chain_test.py`, `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` | E2E 后续扩展剩余 P1 域 | Day 25-27 | Closed |

## 3. 缺失脚本优先级

| 顺序 | 脚本 | 关闭缺口 |
|---:|---|---|
| 1 | `tools/nest_v13_protocol_test.py` | G-P0-04、G-P0-05、G-P1-02 |
| 2 | 扩展 `tools/mqtt_dock3_drc_test.py` / `tools/mqtt_drc_batch_test.py` | G-P1-03、G-P1-04 已完成当前 Dock3 云端模拟闭环；后续补 G-P1-06 真实 SITL 语义 |
| 3 | 扩展 `tools/mqtt_dock3_wayline_test.py` | G-P1-05 已完成 mock KMZ 基础解析；G-P1-06 真实 SITL 航点执行待补 |
| 4 | `tools/e2e_takeoff_flow_test.py` | G-P1-01 |
| 5 | `tools/mqtt_dock3_media_test.py` | G-P1-07 |
| 6 | `tools/mqtt_dock3_live_test.py` | G-P1-08 |
| 7 | `tools/mqtt_dock3_hms_log_test.py` | G-P1-10、G-P1-11 |
| 8 | `tools/mqtt_dock3_firmware_config_test.py` | G-P1-12、G-P1-13 |
| 9 | 扩展 device/organization/cmd 测试 | G-P1-14、G-P1-15 |
| 10 | `tools/e2e_full_chain_test.py` | G-P0-08、G-P1-17 已完成当前核心 MQTT 域；后续接入 media/live/HMS/log/config |

## 4. 关项规则

| 规则 | 要求 |
|---|---|
| 代码关项 | 对应 handler、adapter、状态机或数据层实现已完成 |
| 脚本关项 | 专项脚本或 E2E 脚本能稳定复现成功和关键失败分支 |
| 文档关项 | backlog、runbook、模块状态和迁移进度同步更新 |
| 证据关项 | 有脚本输出、日志 tid/bid、MQTT 样例、trace 或 DB 记录 |
| P2 例外 | 只有 P2 可带问题；P0/P1 不允许以“后续真实硬件再补”为关项理由 |
