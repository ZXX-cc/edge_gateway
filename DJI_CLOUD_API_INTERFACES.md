# DJI Cloud API Method 清单

本文档按当前 `src/main.py` 的 method 注册结果整理，用于人员1进行 DJI Dock 3 上云 API 验收。当前清单只说明“网关已路由”，不代表已经符合 Dock3 官方字段。

## 1. 当前口径

| 项目 | 结论 |
|---|---|
| 当前已注册 method | 243 |
| 统计来源 | `src/main.py` 中 `register_batch/register`，method 覆盖台账见 `docs/METHOD_COVERAGE_LEDGER.md` |
| 协议目标 | DJI Dock 3 |
| 当前状态 | 已有 Dock/Dock1 风格实现，客户需求相关 method 需要按 Dock3 迁移校验；客户未用 Dock3 能力可标 Deferred |
| 当前 broker topic 前缀 | `thing/product/{sn}` |
| 主要验收人 | 人员1 |

状态列含义：

| 状态 | 含义 |
|---|---|
| 已路由 | `src/main.py` 已注册 method，MQTT 能分发到 handler |
| 旧 Dock1 风格 | 当前字段、注释或语义偏 Dock/Dock1，需要 Dock3 迁移 |
| 待 Dock3 校验 | 需要按 Dock3 官方文档核对请求体、响应体、枚举、单位和错误码 |
| 模拟器可测 | 当前可用 MQTT/SITL/机巢模拟器做基础链路验证 |
| 暂缓 | 保留接口或占位，客户未用或不阻塞主链路时可延期补业务语义 |
| Deferred | Dock3 官方存在但客户暂未提及/当前业务未使用，记录原因和触发条件后延期 |

## 2. 错误码当前口径

当前项目已在 MQTT 回包和事件发布边界接入 DJI 上云 API 错误码归一，详见 `docs/INTERFACE_DESIGN_SPEC.md` 和 `src/utils/dji_error_codes.py`。业务 handler 中遗留的 `-1/-2/-3` 不作为上云 API 对外结果码，发布到 `services_reply`、`property/set_reply`、`drc/up`、`events` 前会转换为 DJI 官方错误码。

| 场景 | 对外结果码 | 说明 |
|---|---:|---|
| 成功 | `0` | 成功 |
| 未知 method | `314000` | 设备当前无法支持该操作 |
| 请求格式或 topic 错误 | `325001` | 云端下发命令不符合格式要求 |
| handler 异常 | `319999` | 机场系统运行异常 |
| 属性设置失败 | `327000` | 参数设置失败 |
| 当前忙碌/限流 | `514116` | 机场正在执行其他控制指令 |

## 3. Topic 总览

| Topic | 方向 | 当前用途 |
|---|---|---|
| `thing/product/{sn}/services` | 云端 -> 网关 | 云端下发服务调用 |
| `thing/product/{sn}/services_reply` | 网关 -> 云端 | 服务调用响应 |
| `thing/product/{sn}/requests` | 网关 -> 云端 | 设备主动请求云端 |
| `thing/product/{sn}/requests_reply` | 云端 -> 网关 | 云端响应设备请求 |
| `thing/product/{sn}/events` | 网关 -> 云端 | 事件上报 |
| `thing/product/{sn}/events_reply` | 云端 -> 网关 | 事件确认 |
| `thing/product/{sn}/property/set` | 云端 -> 网关 | 属性设置 |
| `thing/product/{sn}/property/set_reply` | 网关 -> 云端 | 属性设置响应 |
| `thing/product/{device_sn}/osd` | 网关 -> 云端 | 定频遥测 |
| `thing/product/{device_sn}/state` | 网关 -> 云端 | 状态变化 |
| `thing/product/{sn}/drc/down` | 云端 -> 网关 | DRC 下行控制 |
| `thing/product/{sn}/drc/up` | 网关 -> 云端 | DRC 上行数据 |
| `sys/product/{sn}/status` | 网关 -> 云端 | 设备状态类上报，例如 `update_topo` |
| `sys/product/{sn}/status_reply` | 云端 -> 网关 | 云端确认设备状态类上报 |

## 4. 2026-07-06 人员1基础 MQTT 联调记录

本节记录人员1在 Ubuntu 服务器上完成的第一轮上云 API 基础链路测试。测试目的不是证明 Dock3 合规，而是确认 MQTT topic、method 路由、回包和上报链路的现状。

### 3.1 环境状态

| 项目 | 结果 |
|---|---|
| 网关启动 | 历史联调成功；旧日志中的 method 数量已过期，当前源码静态计数为 243，后续以 `docs/METHOD_COVERAGE_LEDGER.md` 为准 |
| MQTT 连接 | 成功，网关连接 `127.0.0.1:1883` |
| MAVLink 连接 | 成功，日志显示 `[Connection] 已连接到系统 1` |
| MAVLink 分发器 | 成功，日志显示 `MAVLink dispatcher started` |
| 已订阅下行 topic | `services`、`property/set`、`drc/down`、`requests`、各类 `_reply` |
| OSD 上报 | 成功，`thing/product/EDGE-GW-001/osd` 持续输出 SITL 遥测 |

### 3.2 已验证结果

| 编号 | 测试项 | 下行 topic | 期望监听 topic | 实际观测 | 当前结论 | 后续处理 |
|---|---|---|---|---|---|---|
| P1-CLOUD-001 | `dock_status_query` | `thing/product/EDGE-GW-001/services` | `thing/product/EDGE-GW-001/services_reply` | 历史 smoke 曾收到 `services_reply`，`data.result=0` | 2026-08-05 已移除旧 Dock/Dock1 services 入口；不再作为 Dock3 验收项 | 使用 `gateway_state_query`、Dock3 properties、CMD、HMS 专项替代 |
| P1-CLOUD-002 | `airport_bind_status` | `thing/product/EDGE-GW-001/requests` | `thing/product/EDGE-GW-001/requests_reply` | 2026-07-06 Ubuntu 复测通过：云端模拟器收到 `requests` 并回 `requests_reply`，网关处理 `Bind status reply processed: 1 devices` | Dock3 官方最终口径已验证；services 只作为兼容 smoke | 继续补失败/超时分支 |
| P1-CLOUD-007 | `airport_organization_get` | `thing/product/EDGE-GW-001/requests` | `thing/product/EDGE-GW-001/requests_reply` | 2026-07-06 Ubuntu 复测通过：成功路径 `tid=9a738feb-97a8-4851-a797-02d17ab06343`；失败分支 `tid=b3816e96-c8f9-4b8e-b1be-c2f235f80d03 result=327000`；超时分支 `tid=8e21e5fc-3e64-4a30-8268-4763cc7f751f` | Dock3 官方 organization 上行请求成功/失败/超时路径已验证 | 继续字段精校 |
| P1-CLOUD-008 | `airport_organization_bind` | `thing/product/EDGE-GW-001/requests` | `thing/product/EDGE-GW-001/requests_reply` | 2026-07-06 Ubuntu 复测通过：成功路径 `tid=8a0b1414-9853-4632-a172-913a207eff3f`；部分失败 `tid=d2bdf5d1-402f-45d1-82eb-da316c82e09e`；绑定后事件/DB 校验 `bind tid=9e38069d-5c64-4dfe-ba99-fe4808b90cf2`，捕获 `device_binding/update_topo`，SQLite `is_bound=true` | Dock3 官方 organization 绑定成功、`err_infos`、事件、内存状态、SQLite 持久化均已验证 | 继续字段精校、真实云端差异、多设备场景 |
| P1-CLOUD-009 | `update_topo` | `sys/product/EDGE-GW-001/status` | `sys/product/EDGE-GW-001/status_reply` | 2026-07-06 Ubuntu 复测通过：基础拓扑 `tid=f0845cda-bf5b-40b2-a0f7-d2e4cbb65c6b`；多子设备拓扑 `tid=f318713c-767a-43d9-a4b2-13ade3a969b2`，`sub_devices[0].sn=DRONE-SIM-001`，均收到 `status_reply result=0` | device topology 基础 topic/reply 和 `sub_devices[]` 非空链路已验证 | 精校 `domain/type/sub_type/sub_devices` 官方字段 |
| P1-CLOUD-010 | Dock3 P0 总批量 | `services`、`property/set`、`drc/down` | `services_reply`、`property/set_reply`、`drc/up` | 2026-08-05 脚本入口已改为 `gateway_state_query`，覆盖状态查询、properties、只读属性拒绝、DRC enter/control/exit、错误 topic | P0 主链路已可作为后续客户需求相关迁移基线；不再依赖旧 `dock_status_query` | 后续日常使用 `--quiet-stale`，清理旧回包来源后用 `--fail-on-stale` 严格验证 |
| P1-CLOUD-011 | properties 大类专项 | `thing/product/EDGE-GW-001/property/set`、`services` | `property/set_reply`、`services_reply` | `tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale` 输出 `dock3 properties test ok (accepted=10, ignored_total=10)` | 已通过 Ubuntu 专项复测 | 继续 `osd/state` 客户需求相关字段精校 |
| P1-CLOUD-012 | device/topology 大类专项 | `services` 测试触发、`sys/product/EDGE-GW-001/status` | `services_reply`、`sys/product/EDGE-GW-001/status_reply` | Ubuntu 复测通过：`tools/mqtt_dock3_device_test.py --timeout 12` 输出 `dock3 device topology test ok (status_messages=2, sub_device_sn=DRONE-SIM-001, accepted=3, ignored_legacy=3)` | 已按 Dock3 device `update_topo` 官方必填字段收敛，在线拓扑、离线拓扑、无效拓扑拒绝和 `status_reply` 已通过 | 继续官方 `domain/type/sub_type` 产品枚举精校 |
| P1-CLOUD-013 | DRC 大类专项 | `services`、`drc/down` | `services_reply`、`drc/up` | Ubuntu 复测通过：`tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale` 输出 `dock3 drc test ok (accepted=9, ignored_total=5)`，覆盖官方 `drc_mode_enter`、`stick_control`、`drone_control`、`drc_gimbal_speed_push` 和错误分支 | 已按 Dock3 DRC 官方字段收敛并通过专项复测 | 继续 DRC 心跳、`takeoff_to_point` 和超时恢复 |
| P1-CLOUD-014 | CMD/远程调试大类专项 | `services` | `services_reply`、`events` | Ubuntu 复测通过：`tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale` 输出 `dock3 cmd test ok (accepted=55, events=13, ignored_total=55)`，覆盖调试模式、舱盖/充电/推杆、飞行器/设备动作、枚举开关、安全拒绝和进度事件 | 已按 Dock3 CMD/远程调试口径通过专项复测 | 继续真实任务耗时、失败码和环境旧回包清理 |
| P1-CLOUD-015 | Wayline/航线管理大类专项 | `services` | `services_reply`、`events` | 2026-07-06 Ubuntu 复测通过：`tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale` 输出 `dock3 wayline test ok (accepted=11, events=8, ignored_total=11)` | 已实现并验证 `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel` 的 Dock3 `output.status/progress/ext` 和错误码边界；`simulate_mission.is_enable=1` 时不强制真实飞控切模式 | Wayline P1 已验证；后续补 KMZ 下载解析、媒体计数、真实 SITL 航点执行、返航事件字段精校 |
| P1-CLOUD-003 | `property_set` | `thing/product/EDGE-GW-001/property/set` | `thing/product/EDGE-GW-001/property/set_reply` | Ubuntu 复测收到 `property/set_reply`，`data.silent_mode.state.result=0`；空 payload 返回 `data={}` | 已修复并通过 Ubuntu 服务器复测，回包结构已按 Dock3 property/set_reply 收敛；当前只允许 `air_transfer_enable`、`silent_mode`、`user_experience_improvement` | 继续补齐 Dock3 `osd/state` 客户需求相关字段矩阵 |
| P1-CLOUD-004 | `drc_mode_enter` | `thing/product/EDGE-GW-001/services` | `thing/product/EDGE-GW-001/services_reply` | Ubuntu 复测收到 `services_reply`，`data.result=0`；随后 `property_get` 返回 `drc_state=2` | 已通过 Dock3 services 路径复测；误发到 `drc/down` 应返回 DJI 错误码 `325001` | 人员2继续核对 MAVLink 飞控模式/gear 映射 |
| P1-CLOUD-006 | `drc_mode_exit` | `thing/product/EDGE-GW-001/services` | `thing/product/EDGE-GW-001/services_reply` | Ubuntu 复测收到 `services_reply`，`data.result=0`；随后 `property_get` 返回 `drc_state=0` | 已通过 Dock3 services 路径复测 | 人员2继续核对退出 DRC 后飞控模式恢复 |
| P1-CLOUD-005 | `osd` 上报 | 无 | `thing/product/EDGE-GW-001/osd` | 持续收到经纬度、高度、姿态、电池、GPS | 设备遥测上报链路成功 | 对照 Dock3 properties/osd 字段校验名称、单位、枚举 |

### 3.3 关键发现

| 编号 | 问题 | 证据 | 影响 | 负责人 |
|---|---|---|---|---|
| P1-ISSUE-001 | 旧 `dock_status_query` 的 `drone_status` 不符合 Dock3 官方状态表达 | OSD 中已有实时遥测，但旧 services 回包使用 `dock_status/drone_status` 包装 | 2026-08-05 已移除旧 `dock_status_query` 路由；实时状态统一通过 Dock3 OSD/state/properties 和 `gateway_state_query` 验收 | 不再作为开放 method |
| P1-ISSUE-002 | `property_set` 回包 topic 与 `tools/mqtt_smoke_test.py` 预期不一致 | 下行 `property/set` 后修复前实际回 `services_reply`，工具等待 `property/set_reply` 导致超时 | 已改为按下行来源 topic 分流回包 | Ubuntu 复测通过：收到 `property/set_reply` |
| P1-ISSUE-003 | `drc_mode_enter` 回包 topic 与 `tools/mqtt_smoke_test.py` 预期不一致 | 下行 `drc/down` 后修复前实际回 `events:drc_status_notify` 和 `services_reply:drc_mode_enter`，工具等待 `drc/up` 导致超时 | 历史测试已证明网关能处理 DRC 请求；Dock3 官方对照后确认 enter/exit 应改回 services 路径 | 已由 P1-ISSUE-006 修正最终口径 |
| P1-ISSUE-004 | 网关可能处理自己发布的 `services_reply/events_reply` | 网关订阅了 reply topic，发布回包后可能再次进入 method 路由，造成重复业务日志 | 已加保护：`services_reply/events_reply` 只记录，不再路由到业务 handler | 待继续观察网关日志 |
| P1-ISSUE-005 | DRC MQTT 状态与飞控 OSD 模式状态需要进一步核对 | `drc_mode_enter` 已返回 `result=0` 且事件 `drc_state=2`，但复测期间 OSD 中 `mode_code` 仍为 `0`、`gear` 仍为 `0` | MQTT/DRC 协议链路已通，但飞控是否真正切到可控模式需要 MAVLink 侧验证 | 交给人员2核对 MAVLink 模式切换、解锁状态和 `mode_code` 映射 |
| P1-ISSUE-006 | Dock3 官方对照修正 DRC enter/exit topic | Dock3 文档中 `drc_mode_enter`、`drc_mode_exit` 都是 `services -> services_reply`；`drc/down/up` 用于进入 DRC 后的控制数据 | 代码已改为 method-aware topic 校验；`tools/mqtt_smoke_test.py` 新增 `auto` 通道，`drc_mode_enter/exit` 默认走 services | Ubuntu 复测通过；错误通道 `--channel drc --method drc_mode_enter` 现在应返回 `result=325001` |
| P1-ISSUE-007 | DRC 状态事件未同步到 `property_get` | 日志中 `drc_status_notify` 已上报 `drc_state=2`，但修复前 `property_get` 仍返回 `drc_state=0` | 统一 `DrcController` 事件回调，避免 MAVLink 初始化块覆盖状态同步回调；事件先更新 DeviceManager 再上报 MQTT | Ubuntu 复测通过：enter 后 `drc_state=2`，exit 后 `drc_state=0` |
| P1-ISSUE-008 | 启动日志未按文件落盘且字段难读 | 早期 `get_logger()` 在 `init_logger()` 前创建 logger，导致文件 handler 未配置；日志字段为英文 key | 重构日志系统：后台队列写入，按 `logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log` 落盘，关键 MQTT 字段输出 `字段名(中文名)` | 待 Ubuntu 重启后观察当天日志文件 |
| P1-ISSUE-009 | Dock3 properties 可写属性边界 | 官方 Dock3 properties 页中当前确认的 `rw` 顶层属性为 `air_transfer_enable`、`silent_mode`、`user_experience_improvement`；当前项目历史可写列表包含 Dock1/模拟字段 | 已收敛：新增 `src/cloud_api/dock3_properties.py`，`property/set` 只允许官方 `rw` 属性并校验 bool/enum 值域；`battery_store_mode`、`alarm_state`、`drc_state`、未知属性和非法值返回 `state.result=1` | Ubuntu 专项复测通过：`dock3 properties test ok (accepted=10, ignored_total=10)` |
| P1-ISSUE-012 | Dock3 device topology 字段边界 | 官方 device 页 `update_topo` 要求网关字段和子设备字段完整，且通过 `status/status_reply` 交互 | 已收敛：新增 `src/cloud_api/dock3_device.py`，`DeviceManager.build_topo_payload()` 和 `tools/mqtt_cloud_simulator.py` 共用官方字段校验；无效 `update_topo` 返回 `325001` | Ubuntu 专项复测通过：在线拓扑、离线拓扑、无效拓扑拒绝和 `status_reply` 均通过 |
| P1-ISSUE-013 | Dock3 DRC 官方字段边界 | 官方 DRC 页中 `drc_mode_enter` 有 `mqtt_broker/osd_frequency/hsi_frequency`，`stick_control` 使用 `roll/pitch/throttle/yaw` 杆量，`drone_control` 使用 `seq/x/y/h/w` | 已收敛：新增 `src/cloud_api/dock3_drc.py`，`DrcController` 支持官方字段校验和兼容映射；无效控制数据返回 `325001` | Ubuntu 专项复测通过：enter/exit、`drc_state`、官方杆量无回包、非法杆量、官方 `drone_control` 和越界错误均通过 |
| P1-ISSUE-014 | Dock3 CMD/远程调试回包与模拟安全 | 旧实现中 `cover/charge/putter` 回包只返回 `action`，枚举开关未统一校验，`device_reboot` 会尝试真实 `sudo reboot` | 已新增 `src/cloud_api/dock3_cmd.py` 并收敛 DeviceManager：回包统一 `data.output.status`，任务类命令上报同 method 进度事件，开关类校验官方 `action/link_workmode`，`device_reboot` 默认模拟 | Ubuntu 专项复测通过：`dock3 cmd test ok (accepted=55, events=13, ignored_total=55)` |
| P1-ISSUE-015 | Dock3 Wayline/航线管理基础闭环 | 旧实现中 `flighttask_prepare` 成功后状态回 IDLE 且回包缺少统一 `output.status/progress/ext`，`pause/resume/undo/return` 复用旧回包结构，参数错误多为内部负数；首次 Ubuntu 复测中 `flighttask_execute` 仍触发真实 `AUTO` 切换，导致 `模式切换超时，当前模式: MANUAL` | 已新增 `src/cloud_api/dock3_wayline.py` 并收敛 MissionManager：P1 flighttask 回包统一 `data.output.status/progress/ext`，状态变化上报 `flighttask_progress`，返航上报 `return_home_info`，参数错误返回 `325001`，纯模拟 `simulate_mission.is_enable=1` 时跳过真实飞控模式切换 | Ubuntu 复测通过：`dock3 wayline test ok (accepted=11, events=8, ignored_total=11)` |
| P1-ISSUE-010 | 异步 coroutine 未 await warning | Ubuntu 日志曾出现 `AsyncSession.execute was never awaited`，停止服务时还出现 `DataRetentionManager.stop`、`CloudSyncManager.stop` 未 await | 已将 `DeviceDB`、`AlarmDB` helper 改为 async；信号处理改为只请求主循环退出，由 `run_async().finally` 统一 `await stop_async()` | Ubuntu 复测通过：最新日志未再出现 `RuntimeWarning` 或 `was never awaited` |
| P1-ISSUE-011 | P0 批量测试观测到旧格式重复回包 | 同一 `tid` 先收到 `method=null` 的旧 `property/set_reply` 或旧 `data.result=-2`，随后收到正确 Dock3 回包 | 批量脚本已按 `tid + method + Dock3格式` 忽略旧回包并统计 `ignored_*`，P0 正确回包已通过；该问题不阻塞 P0，但说明 broker 环境仍需清理 | 日常验证用 `--quiet-stale`；清理环境后用 `--fail-on-stale` 要求 `ignored_total=0` |

### 3.4 当前判定

| 链路 | 当前判定 |
|---|---|
| `services -> services_reply` | 已通 |
| `property/set` 下行接收 | 已通 |
| `property/set` 标准 reply | 已修复并通过 Ubuntu 服务器复测 |
| `drc/down` 下行接收 | 已通 |
| `drc/up` 标准上行 | 已用于 `stick_control`、`drone_control`、`payload_control`、`drc_speed_push`、`drc_gimbal_speed_push`，P0 批量复测均返回 `result=0`；`drc_mode_enter/exit` 已改回 services |
| `events` 上报 | 已通 |
| `osd` 上报 | 已通 |
| `gateway_state_query` / OSD / state 实时状态 | 使用当前 Dock3 状态链路；旧 `dock_status_query` 已移除 |
| Dock3 字段合规 | 未完成 |
| Dock3 客户需求迁移台账 | 已建立，见 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` |
| properties Batch 1 | 已通过 Ubuntu 专项复测：`tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale` 输出 `dock3 properties test ok` |
| device topology Batch | 已通过 Ubuntu 专项复测：`tools/mqtt_dock3_device_test.py --timeout 12` 输出 `dock3 device topology test ok` |
| DRC Batch | 已通过 Ubuntu 专项复测：`tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale` 输出 `dock3 drc test ok` |
| CMD Batch | 已通过 Ubuntu 专项复测：`tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale` 输出 `dock3 cmd test ok (accepted=55, events=13, ignored_total=55)` |
| Wayline Batch | 已通过 Ubuntu 专项复测：`tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale` 输出 `dock3 wayline test ok (accepted=11, events=8, ignored_total=11)` |

## 5. 243 Method 分组清单

### 5.1 Device：51 个

| 状态 | Method |
|---|---|
| 已路由，待 Dock3 校验，部分模拟器可测 | `ota_create`, `ota_cancel`, `config_update`, `property_set`, `property_get`, `device_binding`, `device_unbinding`, `device_reboot`, `format_sd_card`, `cover_open`, `cover_close`, `charge_open`, `charge_close`, `putter_open`, `putter_close`, `device_lock`, `device_unlock`, `camera_mode_switch`, `supplement_light_open`, `supplement_light_close`, `sdr_workmode_switch`, `alternate_land_point_set`, `rtk_ref_points_set`, `rtk_ref_points_query`, `device_exit_rtk_mode`, `device_enter_simulation_mode`, `device_exit_simulation_mode`, `device_calibration`, `device_format`, `device_reset`, `workspace_bind_status`, `workspace_get`, `airport_bind_status`, `hms_get`, `hms_clear`, `device_log_list_get`, `device_log_upload`, `device_capability_query`, `cover_force_close`, `drone_open`, `drone_close`, `drone_format`, `debug_mode_open`, `debug_mode_close`, `battery_maintenance_switch`, `air_conditioner_mode_switch`, `alarm_state_switch`, `battery_store_mode_switch`, `config`, `update_topo`, `airsense_warning` |

说明：

- `airport_bind_status` 已用于启动时自动查询绑定状态；Dock3 官方方向是 `requests/requests_reply`。当前已新增 `tools/mqtt_cloud_simulator.py` 作为云端模拟器，services handler 只作为本地兼容 smoke。
- `cover_*`、`charge_*`、`putter_*`、`drone_*`、`device_reboot/device_format`、`debug_mode_*` 和 `*_switch` 已按 Dock3 CMD/远程调试口径完成代码收敛，并已通过 Ubuntu 专项脚本 `tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale` 验证。

### 5.2 Mission / Wayline：42 个

| 状态 | Method |
|---|---|
| 已路由，旧 Dock1 风格，待 Dock3 校验，SITL 可测部分命令 | `load_wayline`, `upload_wayline`, `execute_task`, `cancel_task`, `pause_wayline`, `resume_wayline`, `flyto`, `fly_to_point`, `take_off`, `one_key_return_home`, `cancel_return_home`, `set_flight_mode`, `go_home`, `landing`, `emergency_stop`, `start_takeoff_to_point`, `return_home`, `go_to_altitude`, `wayline_task_create`, `wayline_task_execute`, `wayline_task_pause`, `wayline_task_resume`, `wayline_task_cancel`, `wayline_task_query`, `wayline_mission_upload`, `wayline_mission_download`, `wayline_mission_delete`, `wayline_mission_query`, `flighttask_prepare`, `flighttask_execute`, `flighttask_undo`, `flighttask_return`, `flighttask_progress`, `flighttask_pause`, `flighttask_resume`, `flight_setup_abort`, `flighttask_create`, `flighttask_recovery`, `return_home_cancel`, `job_create`, `job_delete`, `job_query` |

说明：

- `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel` 已完成 Dock3 P1 基础回包收敛，并已通过 Ubuntu 跑 `tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale`。
- 旧 `load_wayline/execute_task/wayline_task_*` 仍作为兼容入口保留，迁移时以 Dock3 `wayline.html` 和 `drc.html` 为准。
- `set_flight_mode` 当前存在 ArduCopter 模式表适配问题，是人员2优先修复项。

### 5.3 Payload：107 个

| 子类 | 状态 | Method |
|---|---|---|
| Camera | 已路由，待 Dock3 校验；2026-08-05 已清理旧相机 services 入口 | `camera_photo_take`, `camera_photo_stop`, `camera_recording_start`, `camera_recording_stop`, `camera_focal_length_set`, `camera_exposure_set`, `camera_exposure_mode_set`, `camera_focus_mode_set`, `camera_focus_value_set`, `camera_point_focus_action`, `camera_frame_zoom`, `camera_mode_switch`, `photo_storage_set`, `video_storage_set` |
| Gimbal | 已路由，待 Dock3 校验 | `gimbalRotate`, `gimbal_rotate`, `camera_look_at`, `set_gimbal_mode`, `gimbal_reset`, `gimbal_capability_query`, `gimbal_status_query` |
| ViewLink | 已路由，设备侧 adapter/调试入口 | `viewlink_initialize`, `viewlink_connect`, `viewlink_disconnect`, `viewlink_is_connected`, `viewlink_move`, `viewlink_stop`, `viewlink_home`, `viewlink_turn_to`, `viewlink_zoom_in`, `viewlink_zoom_out`, `viewlink_stop_zoom`, `viewlink_start_track`, `viewlink_stop_track`, `viewlink_take_photo`, `viewlink_start_recording`, `viewlink_stop_recording`, `viewlink_is_recording`, `viewlink_set_record_mode` |
| IR | 已路由，待 Dock3 校验 | `ir_metering_mode_set`, `ir_color_palette_set`, `ir_display_mode_set`, `ir_isotherm_set`, `ir_zoom_set` |
| Third party / PSDK | 已路由，多数暂缓 | `drc_light_control_set`, `drc_light_calibration`, `drc_light_fine_tuning_set`, `drc_speaker_tts_set`, `drc_speaker_play_volume_set`, `drc_speaker_audio_play`, `drc_speaker_audio_pause`, `drc_speaker_audio_stop`, `psdk_function_set`, `set_thermal_mode`, `set_palette`, `payload_authority_grab`, `payload_authority_release`, `payload_status_query`, `parachute_open`, `parachute_status_query`, `night_vision_mode_set`, `gas_detector_data_get`, `radar_status_query`, `searchlight_status_query`, `speaker_status_query`, `payload_capability_query`, `psdk_ui_resource_upload_result`, `psdk_floating_window_text`, `psdk_widget_value_set`, `psdk_input_box_text_set`, `speaker_replay`, `speaker_play_mode_set`, `speaker_play_stop`, `speaker_audio_play_start`, `speaker_tts_play_start`, `speaker_play_volume_set` |

说明：

- 当前阶段负载以模拟回包和状态 trace 为主。
- PSDK/ESDK 透传属于暂缓范围，先保留接口和调试入口。

### 5.4 Media / Live：13 个

| 状态 | Method |
|---|---|
| 已路由，待 Dock3 校验，部分模拟器可测 | `file_list_get`, `file_delete`, `adjust_upload_priority`, `storage_config_get`, `live_start`, `live_stop`, `set_livestream_lens`, `set_livestream_quality`, `live_capability_query`, `highest_priority_upload_flighttask_media`, `upload_flighttask_media_prioritize`, `live_lens_change`, `live_set_quality` |

说明：

- Dock3 中媒体管理文档路径为 `media.html`，Dock1 中为 `file.html`，迁移时不能沿用 Dock1 文件命名假设。
- 当前可先模拟文件列表、上传状态、直播开关和质量设置。

### 5.5 DRC：24 个

| 状态 | Method |
|---|---|
| 部分已按 Dock3 字段收敛并通过 DRC 专项复测，SITL 可测部分命令 | `drc_mode_enter`, `drc_mode_exit`, `drc_speed_push`, `drc_heading_push`, `drc_altitude_push`, `drc_flyto_point_push`, `stick_control`, `drone_control`, `payload_control`, `drc_gimbal_speed_push`, `fly_to_point_stop`, `fly_to_point_update`, `camera_screen_drag`, `camera_aim`, `camera_screen_split`, `video_storage_set`, `camera_exposure_set`, `ir_metering_point_set`, `ir_metering_area_set`, `poi_mode_enter`, `poi_mode_exit`, `poi_circle_speed_set`, `drone_emergency_stop`, `takeoff_to_point` |

说明：

- DRC 是人员1和人员2的交界点：人员1定义 Dock3 MQTT 语义，人员2保证 MAVLink/SITL 执行路径。
- Dock3 中 `drc_mode_enter`、`drc_mode_exit`、`takeoff_to_point` 是 services 方法，应通过 `services/services_reply` 验收。
- Dock3 中 `drc/down/drc/up` 用于 DRC 建链后的控制数据，例如 `stick_control`、`drone_control`、`payload_control`。当前 `stick_control` 已支持官方 `roll/pitch/throttle/yaw/gimbal_pitch` 杆量字段；`drone_control` 已支持官方 `seq/x/y/h/w` 字段；`payload_control` 和更多 DRC method 仍待继续精校。
- 当前不得在 DRC 热路径里阻塞等待长时间 ACK。

### 5.6 Dock：0 个

| 状态 | Method |
|---|---|
| 2026-08-05 已移除旧 Dock/Dock1 services 入口 | `dock_status_query`, `dock_control`, `dock_alarm_query`, `dock_task_schedule_query`, `dock_task_schedule_set`, `dock_emergency_stop`, `dock_reset`, `dock_weather_query` |

说明：

- 机场状态、控制、告警和远程调试改走 Dock3 properties、CMD、HMS、OSD/state 和 `gateway_state_query`。
- 旧 `dock_*` method 后续如需恢复，必须先给出 Dock3 官方 method 对照和客户需求触发条件。

### 5.7 Log：3 个

| 状态 | Method |
|---|---|
| 已路由，待 Dock3 校验 | `fileupload_list`, `fileupload_start`, `fileupload_update` |

### 5.8 FlySafe：3 个

| 状态 | Method |
|---|---|
| 已路由，暂缓 | `unlock_license_switch`, `unlock_license_update`, `unlock_license_list` |

### 5.9 Flight Area：2 个

| 状态 | Method |
|---|---|
| 已路由，待 Dock3 校验 | `flight_areas_update`, `flight_areas_get` |

说明：Dock3 文档使用 `custom-fly-region.html`，Dock1 文档使用 `custom-flight-area.html`，迁移时需要统一命名和字段。

### 5.10 Custom Data：4 个

| 状态 | Method |
|---|---|
| 已路由，暂缓 | `custom_data_transmission_from_esdk`, `custom_data_transmission_to_esdk`, `custom_data_transmission_from_psdk`, `custom_data_transmission_to_psdk` |

## 6. 人员1验收表

| 验收项 | 标准 |
|---|---|
| method 路由 | 已注册 method 可被 MQTT services/property/drc topic 分发 |
| 请求体 | 与 Dock3 官方文档一致 |
| 响应体 | `tid`、`bid`、`method`、`data.result`、`msg` 和业务字段一致 |
| topic | 下行和上行 topic 与 Dock3 文档一致 |
| 错误码 | 未知 method、参数错误、执行失败、超时都有明确返回 |
| request trace | 每个请求能记录 request、dispatch、execute、reply、error/timeout |
| 模拟验证 | 上云 API 模拟器可复现请求，Web/MQTT 订阅可看到响应 |

## 7. 下一步

1. 按 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` 逐组推进客户需求相关 Dock3 迁移，客户未提及/未使用的 Dock3 能力标 Deferred。
2. 在 Ubuntu 上优先运行 `python tools/mqtt_dock3_p0_batch_test.py --timeout 12`，一次复测 P0 组合链路。
3. 继续 organization：做字段精校、真实云端差异和多设备场景。
4. 继续 device/topology：先用 `tools/mqtt_dock3_device_test.py --timeout 12` 验证在线/离线拓扑和无效拓扑拒绝，再补官方产品枚举。
5. 将失败或未实现项记录到 request trace 和迁移表。
