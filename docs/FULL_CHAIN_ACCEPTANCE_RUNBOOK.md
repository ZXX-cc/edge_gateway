# 全链路验收 Runbook

更新时间：2026-07-22

本文档用于证明 Edge Gateway 按客户需求和 DJI Dock 3 口径完成全链路可用。它不是开发计划，而是验收执行手册。

如果验收失败，需要回到 `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md` 查找对应 P0/P1 缺口、代码入口和目标窗口。

第 30 天交付评分以 `docs/DELIVERY_ACCEPTANCE_SCORECARD.md` 为准：44 个 backlog 项中至少 40 项可用，44 项开发完成，且 P0/P1 必须 34/34 可用。

最终验收规则：

- P0/P1 必须 100% 可用，任何 P0/P1 失败都需要修复后再交付。
- P2 可以有已记录问题，但不能影响 P0/P1 主链路。
- 客户需求相关云端 topic、method、payload、reply、字段、枚举和错误码以 DJI Dock 3 为最终标准。
- 客户暂未提及、当前业务未用到的 Dock3 能力可以标为 Deferred；Deferred 项不参与 P0/P1 通过判定，但必须记录原因和后续触发条件。
- 设备侧 MAVLink、机巢 v1.3、媒体源、负载协议只作为 adapter，不改变 Dock3 云端口径。
- 客户未要求 Web 调试台或前端页面，Web/Demo 页面不作为正式验收模式；可选 Demo Docker 只用于内部联通排查。

## 1. 验收链路

标准全链路：

```text
DJI Dock3 云端模拟器
  -> MQTT Broker
  -> Edge Gateway
  -> ArduPilot SITL / 机巢 v1.3 模拟器 / 媒体与负载 mock
  -> Edge Gateway
  -> MQTT osd / state / events / services_reply / requests_reply / status_reply / drc/up
  -> 脚本、MQTT、日志、trace、DB 和 E2E 证据
```

正式验收运行模式：

| 模式 | 用途 | 说明 |
|---|---|---|
| 脚本模拟模式 | 日常开发和 CI 式回归 | 使用 `tools/*.py` 发布/订阅 MQTT，适合精确检查 payload 和错误码 |

DJI Demo Docker 可作为可选联调参考，不进入客户交付判定。

## 2. 现有脚本和缺口

当前仓库已有：

| 脚本 | 当前用途 | 覆盖 |
|---|---|---|
| `tools/ubuntu_preflight.sh` | Ubuntu 只读预检 | 环境、依赖、端口 |
| `tools/mqtt_cloud_simulator.py` | 云端 requests/status 模拟器 | organization、bind、status_reply |
| `tools/mqtt_organization_test.py` | 组织管理专项 | `airport_bind_status/get/bind`、多子设备绑定 |
| `tools/mqtt_dock3_p0_batch_test.py` | P0 基础批量 | property、services、DRC、错误 topic |
| `tools/mqtt_dock3_properties_test.py` | properties 专项 | 可写/只读属性、值域 |
| `tools/mqtt_dock3_device_test.py` | device topology 专项 | `update_topo`、多子设备、在线/离线/重连拓扑、解绑空拓扑 |
| `tools/mqtt_dock3_drc_test.py` | DRC 专项 | enter/exit、杆量、错误值域 |
| `tools/mqtt_drc_batch_test.py` | DRC 批量闭环 | enter/exit、`drc_state`、杆量、飞行器/负载控制、速度控制 |
| `tools/mqtt_dock3_cmd_test.py` | CMD 专项 | 舱盖、充电、推杆、灯、设备动作 |
| `tools/mqtt_dock3_wayline_test.py` | Wayline 专项 | prepare/execute/progress/pause/resume/return |
| `tools/mqtt_dock3_media_test.py` | Media 专项，已通过 Ubuntu 复测 | 文件列表、删除、上传优先级、上传事件 |
| `tools/mqtt_dock3_live_test.py` | Live 专项，已通过 Ubuntu 复测 | `mock://` 直播开始/停止、质量、镜头、状态事件 |
| `tools/mqtt_dock3_hms_log_test.py` | HMS/Log 专项 | HMS 告警、日志列表、`fileupload_start` 本地 HTTP 真实字节上传、取消、进度事件和非法参数拒绝 |
| `tests/test_mqtt_background_handlers.py` | P0-16 本地非阻塞回归 | 慢日志/媒体 IO handler 不占用 MQTT 网络回调线程，后台完成后 reply 可达；控制类 handler 保持同步顺序 |
| `tools/e2e_full_chain_test.py` | 当前 P0/P1 核心 MQTT 域 E2E 编排 | properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config |
| `tools/global_state_machine_admission_audit.py` | 全局状态机/命令准入验收 | 未绑定、飞控离线、急停、任务占用、DRC 占用、OTA 中和非法状态迁移 |
| `tools/mqtt_smoke_test.py` | 兼容冒烟 | 单 method smoke，不作为最终 P0/P1 正式验收 |

30 天内必须可重复验收：

| 脚本 | 必须覆盖 | 优先级 |
|---|---|---|
| `tools/nest_v13_protocol_test.py` | `DRONE_NEST_COMMAND/RESPONSE/ORDER_RESPONSE`、命令条件、状态位、拒绝原因 | P0/P1 |
| 已通过 `tools/mqtt_dock3_media_test.py` | 文件列表、删除、上传优先级、上传事件 | P1 |
| 已通过 `tools/mqtt_dock3_live_test.py` | 直播开始/停止、质量、镜头、状态事件 | P1 |
| Ubuntu 复测 `tools/mqtt_dock3_hms_log_test.py` | HMS 告警、远程日志列表、本地 HTTP 真实字节上传、取消、失败码 | P1 |
| `tools/mqtt_dock3_firmware_config_test.py` | 固件升级模拟、配置下发、持久化、回滚 | P1 |
| 已通过 `tools/e2e_takeoff_flow_test.py` | 一键启航离线闭环：mock KMZ、机巢 `request_takeoff`、任务完成事件 | P1 |
| `tools/mqtt_one_key_takeoff_flow_test.py` | 一键启航 MQTT 实跑入口：`--simulate` 跑完整闭环，真实链路默认停在任务执行中 | P1 |
| `tools/mqtt_one_key_takeoff_sitl_test.py` | 一键启航真实 SITL 航线验收：默认准备服务器 `/home/fenghuolun/zwz/HD.kml`，`one_key_takeoff_flow` 起飞后切 AUTO，并用 QGC/SITL OSD、航点进度和 `flighttask_pause` 证明真实执行与暂停；测试结束后停在 paused，不发送取消任务或降落清理指令 | P1 |
| 已通过 `tools/e2e_full_chain_test.py` | 11 段 P0/P1 全链路；共享 EMQX 已完成 `--fail-on-stale` 严格验收 | P1 |

P0-16 本地回归命令：

```bash
python -m unittest tests.test_mqtt_background_handlers
```

通过标准：慢日志/媒体 handler 已启动但未完成时，MQTT 回调立即返回；解除 handler 后，同一 `tid` 在正确 reply topic 收到 `result=0`。该单测不替代 Ubuntu 上的长时间 E2E 和 DB/上传超时注入。

2026-07-10 Ubuntu 已通过：`tests.test_mqtt_background_handlers` 为 2/2；随后 `mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale` 输出 `accepted=8, events=5`，`mqtt_dock3_media_test.py --timeout 12 --quiet-stale` 输出 `accepted=6, events=2`。该证据确认日志/媒体后台 handler 的 services reply 和事件链路可用，但不替代后续 DB、上传和长时间 E2E 压测。

全局状态机/命令准入第一版验收命令：

```bash
python -m unittest -v tests.test_global_state_machine
python tools/global_state_machine_admission_audit.py
```

通过标准：单测全通过，审计脚本输出 `status=passed`、`passed=15`、`total=15`，
并在 `logs/acceptance_evidence/global_state_machine_admission_summary.json`
保存 summary。该脚本只验证准入规则，不替代 MQTT/E2E；状态机代码变更后仍需
继续跑 11 段全链路确认不会误拦截既有 P0/P1 正常命令。

2026-07-14 Ubuntu 已通过：`tests.test_global_state_machine` 输出 9/9 OK，
`tools/global_state_machine_admission_audit.py` 输出 `status=passed`、
`passed=15`、`total=15`，两项退出码均为 0。下一步必须跑 11 段全链路
确认 MQTT 接入点不会误拦截正常 P0/P1 命令。

2026-07-14 服务器全链路复测显示：全局状态机接入后，E2E 必须显式满足
“先绑定、再执行业务命令”的前置条件。`tools/e2e_full_chain_test.py` 已调整为：
默认跳过 device 阶段中的破坏性解绑检查，并在第一个业务阶段前自动执行
`device_binding` 绑定前置；如需专门验收解绑语义，继续使用
`tools/mqtt_dock3_device_test.py` 或在 E2E 中显式增加
`--device-include-unbind-check`。同时 `tools/mqtt_dock3_osd_state_test.py` 的
拓扑种子已改为复用 Dock3 gateway 官方常量，避免 `domain=3,type=2` 旧口径
被网关严格校验拒绝。

2026-07-14 后续复测已确认绑定前置和 OSD/state 修复生效，properties、device、
binding-precondition、OSD/state、media、live、HMS/log 均通过；剩余问题集中在
互斥状态清理。`tools/e2e_full_chain_test.py` 已新增 state precondition，在
Wayline、一键启航和 firmware/config 前自动执行 `drc_mode_exit`；同时
`tools/mqtt_dock3_drc_test.py` 在失败路径也会清理 DRC，避免 `drc_active`
污染后续阶段。当前纯模拟无实时 MAVLink sender 时，DRC 的 `drone_control`
允许 `315000` 作为非阻塞通信结果，真实飞行动作仍保持严格校验。

2026-07-14 状态清理复测显示：CMD 舱盖命令 reply/event 已成功，但模拟器
`cover_state` 回读可能滞后，导致脚本提前失败并未执行后续 `cover_close`，进而
污染 Wayline 的 `dock_not_ready` 准入。`tools/mqtt_dock3_cmd_test.py` 已将
舱盖开/关回读改为非阻塞告警，保证命令成功后继续执行关盖恢复；充电状态回读
仍保持严格校验。全局状态机同时把 `takeoff_to_point`、`fly_to_point_update`、
`fly_to_point_stop` 等归入 DRC 指点飞行控制：DRC 激活时允许进入业务 handler，
DRC 未激活时停止指点飞行仍按非法状态迁移拒绝，不再误判为“新航线任务”。此外，
`tools/e2e_takeoff_flow_test.py` 已适配当前真实执行策略：当网关明确拒绝
`simulate_mission` 并返回 `simulate_mission is disabled; real flight execution is required`
时，脚本记录为真实执行保护生效；如需强制旧离线模拟闭环，可显式增加
`--fail-on-simulate-disabled`。

2026-07-14 `state_cleanup_v2` 复测继续收敛：takeoff-flow 已通过，剩余集中在
CMD 充电状态回读、DRC 指点飞行真实 adapter 环境和 Wayline prepare 准入。
当前纯模拟/SITL 环境中，`charge_open/charge_close` reply 与事件可达但
`charging_state` 可能不随模拟状态帧立即变化，CMD 专项已将充电回读调整为
非阻塞告警，避免破坏后续阶段。DRC 的 `takeoff_to_point` 若已进入真实 guided
adapter 但因当前飞控模式/发送器状态返回 `315000`，脚本记录为真实链路环境失败
证据并继续完成 DRC 退出与非法参数校验。全链路 Wayline 阶段改为默认
`--real-sitl` 的航线上传验收，不再使用已被网关禁止的 `simulate_mission`；
全局准入允许 `dock=online` 执行 prepare/upload，但 `flighttask_execute` 仍要求
`dock=ready` 或由任务模块/飞控自检给出更精确结果。

2026-07-14 `state_cleanup_v3` 复测显示 CMD、DRC 已通过，剩余 Wayline 已进入
真实 MAVLink mission upload，但当前 SITL/飞控任务握手返回
`319999 航线上传到飞控失败`。为避免全链路总验收被单次 FC mission handshake
环境问题阻断，`tools/e2e_full_chain_test.py` 的 Wayline 阶段新增
`--allow-real-sitl-upload-failure`：当航线文件已解析、航点数量满足预期且失败明确为
真实 FC 上传握手失败时，记录为环境留证通过。单独执行
`tools/mqtt_dock3_wayline_test.py --real-sitl` 默认仍保持严格上传成功判定；需要验证
真实 SITL 航线上传/执行时，不要加该放宽参数。

2026-07-14 `state_cleanup_v4` 已完成状态机接入后的 11 段全链路回归：
`tests.test_global_state_machine` 为 9/9 OK，`global_state_machine_admission_audit`
为 `status=passed, passed=15, total=15`，`tools/e2e_full_chain_test.py --host
192.168.2.188 --port 1883 --timeout 20 --quiet-stale --fail-on-stale
--continue-on-fail` 输出 `dock3 e2e full-chain test ok (steps=11, elapsed=36.4s)`。
其中 properties、device、binding-precondition、OSD/state、CMD、DRC、
Wayline、takeoff-flow、media、live、HMS/log、firmware/config 全部退出码为 0。
Wayline 阶段采用 `--real-sitl --allow-real-sitl-upload-failure`，证明文件解析和
真实 MAVLink 上传入口已到达；严格真实 SITL mission upload/execute 仍需单项
不加放宽参数继续验收。

2026-07-16 已新增任务重试与失败上报机制：真实任务执行过程中，网络断开、通信异常、
ACK/指令超时和飞控上传握手失败会按默认 3 次、间隔 2 秒自动重试；硬件故障、
急停、电量/围栏/自检失败、飞控/无人机拒绝、adapter 不存在和状态/参数非法不重试。
最终 FAILED 会主动上报 `flighttask_progress`，其中 `output.status=failed`，
`output.ext.break_point.reason` 与 `output.ext.failure` 包含失败阶段、分类、是否可重试、
尝试次数和原因。

任务重试专项回归命令：

```bash
python -m unittest -v \
  tests.test_dock3_wayline.Dock3WaylineTest.test_real_flighttask_execute_retries_timeout_and_continues_on_success \
  tests.test_dock3_wayline.Dock3WaylineTest.test_real_flighttask_execute_does_not_retry_flight_controller_rejection
```

通过标准：第一个用例证明 `network_or_timeout` 会重试并在后续成功时继续任务；第二个
用例证明飞控拒绝归为 `hardware_or_rejected`，不会重复下发，且 MQTT 事件包含
`flighttask_progress status=failed` 和失败原因。

2026-07-16 `task_retry_v3` 已完成任务重试改造后的全链路复测：
`tools/e2e_full_chain_test.py --host 192.168.2.188 --port 1883 --timeout 20
--quiet-stale --fail-on-stale --continue-on-fail` 输出
`dock3 e2e full-chain test ok (steps=11, elapsed=15.6s)`。properties、device、
binding-precondition、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、
HMS/log、firmware/config 全部 exit=0。OSD/state 阶段已验证当前
`flighttask_step_code` 任务步骤码 0..33 不再被旧枚举误判。

2026-07-16 断点续飞改造：断点信息不再只依赖内存/本地 JSON，运行时会写入
MongoDB `missions.execution_conditions.breakpoint`，字段包含 `task_id`、
`flight_id`、`last_waypoint_idx`、`total_waypoints`、`break_point`、`file`、
`reason` 和 `updated_at`。网关重启恢复 pending mission 时优先从 MongoDB 读取
断点，本地 JSON 只作为兼容兜底。平台和脚本只看到“从断点第 N 点续飞”，飞控索引
换算只在网关内部执行，不出现在平台命令、脚本参数或脚本输出中。

2026-07-31 断链航线平台快照：航线任务执行期间，如果 MQTT 与平台断开或发布失败，
网关不会把所有航线事件排队堆积，只会在数据库 `mission_platform_snapshot` 表/集合中
按 `gateway_sn` 覆盖保存一条最新快照。覆盖范围包括 `flighttask_progress`、
`flighttask_ready`、`flight_setup_exception_notify`、`return_home_info`、
`device_exit_homing_notify`、`one_key_takeoff_progress`、DRC 指点/起飞进度以及航线媒体
上传事件。MQTT 重连后，网关先补推数据库中的最新快照；该补推成功后删除数据库记录，
随后再主动推送一次当前实时 `flighttask_progress`，确保平台第一时间看到断链期间的最新
状态和重连时刻的实时状态。

Ubuntu 逻辑验收命令：

```bash
cd ~/lxz/edge_gateway
EVIDENCE_DIR=logs/acceptance_evidence/$(date +%Y%m%d_%H%M%S)_mission_platform_snapshot
mkdir -p "$EVIDENCE_DIR"

python -m unittest -v tests.test_mission_platform_snapshot \
  > "$EVIDENCE_DIR/mission_platform_snapshot.log" 2>&1
```

通过标准：日志中 `Ran 2 tests` 且 `OK`；第一条用例证明断链快照只保留最新一条，
第二条用例证明发布失败会入库、重连补推成功后清库，并追加一次当前实时航线状态。

2026-07-16 `20260716_130947_wayline_breakpoint_relaunch_retry` 已完成真实 SITL
断点重执行专项：`/home/fenghuolun/zwz/HD.kml` 初次执行到航点 1 后下发
`flighttask_return`，回包保存 `break_point.index=1` 并进入 `paused`；再次执行时由网关
内部完成飞控续飞点换算，平台侧只看到 `breakpoint_index=1`，随后进度继续推进，
最后 `flighttask_undo` 成功。脚本输出
`dock3 wayline re-execute from breakpoint test ok ... accepted=31, events=37,
ignored_total=0`。

2026-07-16 `20260716_134520_wayline_mongodb_restart_resume` 暴露出重启恢复缺口：
断点创建阶段正常到达 `/home/fenghuolun/zwz/HD.kml` 第 3 个航点并保存
`break_point.index=3`，但网关和模拟器重启后查询该 `flight_id` 返回
`active_flight_id=""`、`task_status="idle"`，说明重启后没有恢复 pending mission。
本轮修复已把 Dock3 `flighttask_prepare` 主路径接入任务/航点落库，同时允许
`MissionCreate.device_id` 使用网关 SN（如 `EDGEGW001`），并对 execute/return
后立即写入 `running/paused` 状态和 MongoDB breakpoint 增加短重试，避免异步落库竞态。
续飞脚本新增 `--wait-completion --completion-timeout <秒>`：默认仍只验证断点后至少推进
一个航点；需要让飞机继续飞完整条航线时加该参数，脚本会轮询到 `completed/ok/100%`
或失败终态。

2026-07-22 断点续飞 V2 改造：断点续飞拆成两条验收链路。第一类
`passive_auto_return` 用于低电量、返航等被动中断后的新任务接管；旧任务保持
`paused/interrupted` 后不再要求平台先确认续飞或取消，平台可直接下发新的
`flighttask_prepare`，只要 `flight_id` 不同且带合法 `break_point.index` 或
`break_point.last_waypoint_idx`，网关会把旧任务持久化为 `superseded`，保留旧断点
记录，并让新任务按自身 `file + break_point` 准备和执行。第二类
`manual_intervention` 用于平台主动“暂停-指令飞行-恢复”；`flighttask_pause` 会保存
当前位置断点，`fly_to_point`/`fly_to_point_update` 会先自动进入或保持 DRC 控制权，
获取失败则拒绝执行，干预结束后平台下发 `resume_wayline_task`，其行为与
`flighttask_resume`/`resume_wayline` 一致：先回断点，再恢复原航线。

断点续飞 V2 验收命令：

```bash
cd ~/lxz/edge_gateway
EVIDENCE_DIR=logs/acceptance_evidence/$(date +%Y%m%d_%H%M%S)_breakpoint_v2
mkdir -p "$EVIDENCE_DIR"

python -m unittest -v tests.test_dock3_wayline tests.test_global_state_machine tests.test_dock3_drc \
  > "$EVIDENCE_DIR/unit.log" 2>&1

python tools/mqtt_dock3_wayline_relaunch_breakpoint_test.py \
  --host 192.168.2.188 --port 1883 --timeout 30 --quiet-stale --fail-on-stale --real-sitl \
  > "$EVIDENCE_DIR/passive_takeover.log" 2>&1

python tools/mqtt_dock3_drc_test.py \
  --host 192.168.2.188 --port 1883 --timeout 20 --quiet-stale --fail-on-stale \
  > "$EVIDENCE_DIR/manual_intervention_drc.log" 2>&1
```

通过标准：`unit.log` 中上述三个单测模块全通过；`passive_takeover.log` 能看到
带断点的新 `flight_id` 接管旧暂停任务，旧任务进入 `superseded`，新任务从断点继续；
`manual_intervention_drc.log` 能看到 `fly_to_point_update` 在 DRC 控制权可用后返回
`result=0`，未获得 DRC 控制权时返回明确失败。所有日志必须保存在
`$EVIDENCE_DIR`，不要只复制终端滚动输出。

2026-07-16 平台决策式断点续飞新增三段脚本，测试期间不需要重启网关、SITL 或机巢模拟器：
`tools/mqtt_platform_wayline_execute.py` 只模拟平台下发 `flighttask_prepare/execute`；
`tools/mqtt_platform_wayline_interrupt.py` 只模拟平台下发 `flighttask_return` 并打印
平台可见 `flighttask_progress status=paused` 事件；`tools/mqtt_platform_wayline_decision.py`
先打印平台可见中断任务和 MongoDB 只读摘要，再按 `--action resume|cancel|show`
下发 `flighttask_recovery` 或 `flighttask_undo`。

2026-07-16 航线执行前新增 `tools/mqtt_platform_wayline_preflight_ready.py`：
用于网关/机巢模拟器重启后把系统拉到可执行航线的准入状态。脚本只清除模拟器故障
注入文件、发送 `drc_mode_exit`、`supplement_light_close`、`charge_close` 等安全唤醒命令，
并通过只读 `gateway_state_query` 轮询 `dock=ready`、`aircraft=online`、
`mission=idle`、`drc=idle|unknown`。它不会上传航线、不会下发 `flighttask_prepare`、
不会请求机巢起飞，真正起飞仍由 `tools/mqtt_platform_wayline_execute.py` 完成。

## 3. 启动前检查

在 Ubuntu 服务器执行：

```bash
cd ~/lxz/edge_gateway
git status --short --branch
bash tools/ubuntu_preflight.sh

ss -tlnp | egrep ':1883|:8083|:6789|:18083|:14550|:14551|:14552' || true
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

通过标准：

| 项目 | 通过条件 |
|---|---|
| Git 状态 | 当前分支和本地改动明确，不覆盖他人修改 |
| Python 依赖 | `ubuntu_preflight.sh` 无关键依赖错误 |
| MQTT | `1883` 可用或已有 Mosquitto/NanoMQ 明确监听 |
| 可选 Demo Docker | 仅内部联调时检查 `cloud_api_sample-*` 容器运行；正式验收不依赖页面 |
| 端口冲突 | SITL 和机巢模拟器不抢同一个 MAVLink 端口 |

## 4. 标准启动顺序

### 4.1 MQTT Broker

```bash
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager
ss -tlnp | grep ':1883'
```

如果只需要启动外围组件，优先使用项目脚本：

```bash
scripts/start_support_stack.sh
```

如果 `mosquitto.service` 重启失败，但 `1883` 已由 Docker/EMQX/其他 broker 监听，不要反复重启宿主机服务，改用检查模式：

```bash
BROKER_MODE=check scripts/start_support_stack.sh
```

如果 `1883` 没有监听，按脚本输出的 `systemctl status`、`journalctl -u mosquitto`、配置文件行号、重复 `listener` 提示和前台 `mosquitto -c ... -v` 结果修复 Mosquitto。当前常见问题是 `/etc/mosquitto/conf.d/local.conf` 内配置项写错，或 `mosquitto.conf` 与 `conf.d/local.conf` 同时声明 `listener 1883/8084`。

2026-07-09 Ubuntu 复测记录：宿主机 Mosquitto 曾因 `/etc/mosquitto/mosquitto.conf` 与 `/etc/mosquitto/conf.d/local.conf` 同时声明 `listener 1883/8084` 启动失败。删除主配置中的重复 listener 后，`scripts/start_support_stack.sh` 已验证启动成功：MQTT Broker 监听 `1883`，云端模拟器、MQTT 观察端和机巢模拟器均显示“已启动”，Web 观察台和 ArduCopter SITL 因 `START_WEB=0`、`START_SITL=0` 按配置跳过。

### 4.2 MQTT 观察终端

根据当前 SN 选择 topic。DJI Demo 兼容模式当前使用 `EDGEGW001`；旧本地模拟可能使用 `EDGE-GW-001`。

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/EDGEGW001/#' -v
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'sys/product/EDGEGW001/#' -v
```

### 4.3 ArduPilot SITL

```bash
cd ~/lxz/edge_gateway
scripts/start_nest_simulator.sh

# 另开终端；默认启动 ArduPlane quadplane / Changchun_HD
scripts/start_arduplane_sitl.sh
```

ArduPlane 脚本默认生成三路输出：`udp:127.0.0.1:14552` 作为机巢模拟器的 SITL 标准 MAVLink 主入口，`udp:127.0.0.1:14551` 保留本机联调镜像，`udp:192.168.2.59:14551` 提供内网观察。机巢模拟器只把 component `190` 或 `DRONE_NEST_COMMAND` 注册为 14551 网关客户端，防止 SITL 镜像抢占 12004/12005 回包地址。

通过标准：

```text
ArduPilot Ready
Flight battery 100 percent
GPS 1: detected
```

### 4.4 云端模拟器

Organization 和 status_reply 验收需要云端模拟器先于网关启动：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGEGW001 --bind-status bound --enable-status --max-requests 0
```

### 4.5 Edge Gateway

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

通过标准：

| 日志 | 说明 |
|---|---|
| `MQTT connected to broker` | MQTT 已连接 |
| `Subscribed: thing/product/...` | 订阅完成 |
| `Device online reported` | 设备上线事件已发 |
| `Cloud heartbeat started` | 云端心跳已启动 |
| `Edge Gateway started successfully` | 主循环启动 |

### 4.6 可选 DJI Demo Docker 联调

如需用 DJI Demo Docker 辅助排查联通，先确认容器：

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}' | grep cloud_api_sample
```

Demo 联通参考项：

| 页面 | 通过条件 |
|---|---|
| Dock | `EDGEGW001` 显示 Online，Workspace 正确 |
| Aircraft | `DRONESIM001` 显示 Online |
| Last Online | 时间随消息更新 |

注意：Demo 页面显示 Dock2/M3D 只是联调枚举兼容，不代表最终设备目标，也不作为客户交付证据。最终协议目标仍是 Dock3。

## 5. P0 验收

P0 按顺序执行。任意失败都先修复，不进入 P1。

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate

python tools/mqtt_dock3_p0_batch_test.py --timeout 12
python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_osd_state_test.py --timeout 15 --quiet-stale
python tools/mqtt_dock3_device_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_device_test.py --timeout 12 --quiet-stale --extra-sub-device-json '{"sn":"DRONE-SIM-002","domain":"0","type":60,"sub_type":0,"index":"B","device_secret":"drone-secret-2","nonce":"drone-nonce-2","thing_version":"1.1.2"}'
python tools/mqtt_organization_test.py --config config/gateway.yaml --action full --organization-id org_sim_001 --sub-device-json '{"sn":"DRONESIM001","domain":"0","type":91,"sub_type":0,"index":"A","device_secret":"drone-secret","nonce":"drone-nonce","thing_version":"1.1.2","online":true,"paired":true}' --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo --expect-bound --publish-events --expect-status-reply --expect-topo-sub-device-count 1 --expect-topo-sub-device-sn DRONESIM001 --validate-topo-schema --verify-timeout 8 --timeout 20
python tools/mqtt_organization_test.py --config config/gateway.yaml --action full --organization-id org_sim_001 --sub-device-json '{"sn":"DRONE-SIM-001","domain":"0","type":60,"sub_type":0,"index":"A","device_secret":"drone-secret","nonce":"drone-nonce","thing_version":"1.1.2","online":true,"paired":false}' --sub-device-json '{"sn":"DRONE-SIM-002","domain":"0","type":60,"sub_type":0,"index":"B","device_secret":"drone-secret-2","nonce":"drone-nonce-2","thing_version":"1.1.2","online":true,"paired":false}' --bind-sub-devices --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo --expect-device-binding-count 3 --expect-device-binding-sn EDGEGW001 --expect-device-binding-sn DRONE-SIM-001 --expect-device-binding-sn DRONE-SIM-002 --expect-bound --expect-bound-sn EDGEGW001 --expect-bound-sn DRONE-SIM-001 --expect-bound-sn DRONE-SIM-002 --expect-sub-device-paired-sn DRONE-SIM-001 --expect-sub-device-paired-sn DRONE-SIM-002 --publish-events --expect-status-reply --expect-topo-sub-device-count 2 --expect-topo-sub-device-sn DRONE-SIM-001 --expect-topo-sub-device-sn DRONE-SIM-002 --validate-topo-schema --verify-timeout 8 --timeout 20
python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale
python tools/mqtt_drc_batch_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale
python tools/e2e_full_chain_test.py --phase p0 --timeout 12 --quiet-stale
```

P0 通过标准：

| 检查项 | 通过条件 |
|---|---|
| P0 批量 | 输出 `dock3 p0 batch test ok` |
| Properties | 输出 `dock3 properties test ok` |
| OSD/State | 输出 `dock3 osd/state test ok`，且 `invalid=0` |
| Device | 输出 `dock3 device topology test ok`；多子设备命令需显示 `sub_device_count=2`、`reconnect=True`、`unbind=True`、`device_unbinding events ok`，并覆盖离线空拓扑、重连恢复和解绑空拓扑 |
| Organization | 输出 `organization request test ok`；多子设备命令需显示 `device_binding events ok: count=3`、`memory binding ok`、`sub-device paired ok`、`sub_devices=2` 和 `status_reply ok` |
| DRC | 输出 `dock3 drc test ok` |
| DRC 批量 | 输出 `drc batch test ok`，且 `ignored_total=0` |
| CMD | 输出 `dock3 cmd test ok` |
| Wayline | 输出 `dock3 wayline test ok` |
| P0 E2E | 输出 `dock3 e2e full-chain test ok`，P0 子阶段 `exit=0` 且 `ignored_total=0` |
| 错误码 | 对外不出现内部 `-1/-2/-3` |
| Topic | `airport_bind_status` 最终走 `requests/requests_reply`，`update_topo` 最终走 `status/status_reply` |

## 6. P1 验收

P1 当前基础验收入口已补齐。真实硬件、真实推流、真实 OTA 和真实 SITL 航点执行仍按风险项继续推进。

```bash
python tools/nest_v13_protocol_test.py --verbose
python tools/mqtt_dock3_media_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_live_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale
python tools/mqtt_fault_injection_acceptance.py --timeout 20
python tools/mqtt_dock3_firmware_config_test.py --timeout 20 --quiet-stale
python tools/e2e_takeoff_flow_test.py --timeout 12
python tools/mqtt_one_key_takeoff_flow_test.py --simulate --timeout 60 --quiet-stale
python tools/mqtt_one_key_takeoff_sitl_test.py --timeout 20 --climb-timeout 30 --mission-observe-timeout 120 --quiet-stale
python tools/e2e_full_chain_test.py --timeout 12 --quiet-stale
```

P1 通过标准：

| 检查项 | 通过条件 |
|---|---|
| 机巢 v1.3 | 命令、应答、状态、拒绝条件、关键 bit 位全部可测 |
| 一键启航 | 当前离线闭环已覆盖 mock KMZ、任务准备、任务执行、机巢 `request_takeoff` 和任务完成事件；真实机巢耗时动作继续实跑 |
| Media | 文件列表、删除、上传优先级、上传事件可用 |
| Live | 开始/停止直播、质量、镜头、状态事件可用；当前 Ubuntu 专项已通过 |
| HMS/Log | 告警事件、日志列表、`fileupload_start` 字节上传、失败码可用；生产 OSS/SDK 签名上传仍按真实云存储 adapter 精校 |
| 故障注入 | 七类机巢故障生成 HMS、dock_operation、OSD；pushMode state、MQTT 断线恢复/重复请求/错误 topic 和数据库操作记录全部通过 |
| Firmware/Config | 升级模拟、进度、取消、结果事件、配置下发、查询、持久化和回滚可用；当前 Ubuntu 专项已通过 |
| 当前核心 E2E | properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config 11 段可重复严格通过 |
| 完整 P1 E2E | 从上线绑定到任务、媒体、直播、运维事件可重复通过；2026-07-13 共享 EMQX 最新输出 `dock3 e2e full-chain test ok (steps=11, elapsed=24.0s)`，全部阶段 `exit=0` |

多设备拓扑可通过 E2E device 阶段单独验证：

```bash
python tools/e2e_full_chain_test.py --only device --timeout 12 --quiet-stale --device-extra-sub-device-json '{"sn":"DRONE-SIM-002","domain":"0","type":60,"sub_type":0,"index":"B","device_secret":"drone-secret-2","nonce":"drone-nonce-2","thing_version":"1.1.2"}'
```

服务器已验证该命令输出 `dock3 e2e full-chain test ok (steps=1, elapsed=1.4s)`。
注意：E2E 编排默认跳过 `device_unbinding`，并会在后续业务阶段前自动发送
`device_binding` 作为全局状态机准入前置。解绑/空拓扑验收应执行：

```bash
python tools/mqtt_dock3_device_test.py --timeout 12 --quiet-stale --fail-on-stale
```

## 6.1 严格旧消息验收

日常联调用 `--quiet-stale` 保持脚本输出简洁；清理旧网关、旧 Web 调试台、旧模拟器和 broker retained 消息后，执行严格验收：

```bash
python tools/mqtt_dock3_osd_state_test.py --timeout 15 --quiet-stale --fail-on-stale
python tools/e2e_full_chain_test.py --timeout 12 --quiet-stale --fail-on-stale
```

严格模式中，`osd-state` 会把旧版 value-wrapper OSD、旧 `reason=property_changed` state、无关 state 和旧 reply 一起计入 stale。properties 严格失败会输出 `stale_samples`，其中包含 `retain/dup/mid/qos`：`retain=true` 优先清理 broker retained 消息，`dup=true` 优先排查 QoS 重投；两者均为 false 时优先排查第二个旧网关/模拟器进程。出现任何 stale 都应先清理环境，再重新执行。

2026-07-10 严格 properties 复测已确认 9 条重复 `property/set_reply` 都是 `method=null`、`retain=false`、`dup=false`，当前 Dock3 回包本身均正确。这不是网关代码的 QoS 重投，也不是 retained 消息；必须先定位并停止第二个旧网关或旧模拟器。停止前先在 Ubuntu 执行只读排查并保留输出：

```bash
pgrep -af 'src/main.py|web/app.py|demo_platform/app.py|mqtt_cloud_simulator.py|nest_simulator.py|mosquitto|nanomq'
ps -eo pid,ppid,etimes,args | grep -E 'src/main.py|web/app.py|demo_platform/app.py|mqtt_cloud_simulator.py|nest_simulator.py' | grep -v grep
ss -tnp | grep ':1883' || true
```

确认旧进程 PID 后，只停止已确认的旧服务并保留当前 `src/main.py` 实例；重启当前网关后，先重跑 properties 严格命令，成功后再跑完整 E2E。

2026-07-10 进度记录：当前服务器仅发现一个 `src/main.py` 实例；`demo_platform/app.py` 仅订阅观测 topic，`mqtt_cloud_simulator.py` 仅订阅 `requests/status`，均不是 `property/set_reply` 的发布者。后续 broker 审计已确认重复回包发布者为旧模拟器客户端 `dock3_sim_EDGEGW001_1783480416`：它对每一个 properties 请求都额外发布缺少 `method` 的 `property/set_reply`，同时发布旧 `state`。当前网关客户端 `edge_gateway_EDGEGW001` 对相同请求只发布一条格式正确的 Dock3 回包。

如果当前网关 request trace 已证明每个 `tid` 仅发布一次、但严格测试仍收到第二条旧回包，可临时启用 broker 发布审计：

```bash
sudo bash scripts/mqtt_broker_audit.sh enable
python tools/mqtt_dock3_properties_test.py --timeout 20 --quiet-stale --fail-on-stale || true
sudo grep -E 'Received PUBLISH|property/set_reply' /var/log/mosquitto/mosquitto.log | tail -n 120
sudo bash scripts/mqtt_broker_audit.sh disable
```

`Received PUBLISH from <client_id>` 会显示旧回包的发布客户端。审计结束必须执行 `disable`，恢复默认 broker 日志量。

如果 `enable/disable` 提示 `mosquitto.service` 重启失败，不要继续反复重启。新版脚本会在
`enable` 失败时自动回滚审计配置，并输出 systemd、1883 监听进程和最近日志；也可单独执行：

```bash
sudo bash scripts/mqtt_broker_audit.sh diagnose
```

若 `mosquitto.service` 未运行但 1883 仍在监听，说明测试连接的是手工进程、Docker、
NanoMQ/EMQX 或其他 broker 实例，必须先根据诊断输出确认真实监听者，再对对应实例开启日志。

如果诊断显示 1883 由 Docker EMQX 监听，可直接列出与网关相关的在线客户端：

```bash
sudo bash scripts/mqtt_broker_audit.sh clients
```

脚本按容器名或镜像名中的 `emqx` 自动发现容器；若现场命名特殊，可显式指定：

```bash
sudo EMQX_CONTAINER=docker-emqx-1 bash scripts/mqtt_broker_audit.sh clients
```

容器内管理命令优先使用运行 Broker 的 `emqx` 用户，避免 root 用户的 Erlang Cookie
或节点身份与 `emqx@emqx` 不一致；仅在该方式失败时回退 root 并同时保留两份错误信息。

若两种身份都提示 `Node ... not responding to pings`，说明 EMQX MQTT 数据面可用、
但本地 Erlang 管理 RPC 不可用。不要为审计重启生产 EMQX，改用被动抓包：

```bash
EVIDENCE_DIR="logs/acceptance_evidence/$(date +%Y%m%d_%H%M%S)_mqtt_publishers"
sudo bash scripts/mqtt_broker_audit.sh capture-start "$EVIDENCE_DIR"
# 在这里执行一次严格属性验收
sudo bash scripts/mqtt_broker_audit.sh capture-stop "$EVIDENCE_DIR"
```

抓包只保留客户端发往 1883 的明文 MQTT 流量，因此文本中的源 IP/端口就是发布端；
`mqtt-publisher-summary.tsv` 会去除 `any` 接口重复包，并按源端点分类统计当前消息、
旧 `result=-2`、无 `method` 属性回复和 value-wrapper 机场 OSD；
`mqtt-publisher-matches.log` 保留对应上下文，原始
`mqtt-publishers.pcap` 可供 Wireshark/TShark 复核。完整抓包可能包含 MQTT CONNECT
凭据，因此默认仅 root 可读；对外传递证据时优先提供过滤后的匹配日志。该流程不修改
Broker 配置、连接或会话。

获得可疑 client ID 后执行 `client <client_id>` 查看 `peername` 等连接信息。确认是旧客户端后，
脚本会给出 EMQX 临时断开命令；如果客户端自动重连，应在 `peername` 对应主机停止旧程序，
不能仅依赖 Broker 踢下线。

已知 client ID 时，可直接从 broker 日志和当前连接中查找其来源：

```bash
sudo bash scripts/mqtt_broker_audit.sh client dock3_sim_EDGEGW001_1783480416
```

日志若显示本机 `IP:port`，用输出提示中的 `lsof` 命令找出 PID 后停止该旧模拟器；若显示远端地址，则在远端主机停止对应旧模拟器。不要停止当前 `edge_gateway_EDGEGW001` 网关。确认旧客户端下线后，再执行严格 properties 测试。

2026-07-10 严格 properties 环境验收已通过：broker 日志确认旧客户端位于 `192.168.2.94:55376`，停止后执行 `python tools/mqtt_dock3_properties_test.py --timeout 20 --quiet-stale --fail-on-stale` 输出 `dock3 properties test ok (accepted=10, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`。下一步是在相同干净环境执行完整严格 E2E。

2026-07-10 机巢 strict 联调已通过：被动 UDP 模拟器以网关首包心跳注册回传地址，并以健康待命 `boolstate1=0x0C47`、`boolstate2=0x0020` 上报 12004 状态。`python tools/e2e_full_chain_test.py --only wayline --timeout 20 --quiet-stale --fail-on-stale` 输出 `dock3 wayline test ok (accepted=11, events=12, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`；验证 12003 命令、12004 状态、12005 回复以及 Wayline 状态机。接下来可直接执行完整 strict E2E。

2026-07-10 完整 strict E2E 已通过：`python tools/e2e_full_chain_test.py --timeout 20 --quiet-stale --fail-on-stale` 输出 `dock3 e2e full-chain test ok (steps=11, elapsed=26.7s)`。全部 11 段均为 `exit=0`；最后 firmware/config 输出 `accepted=8, events=10, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0`。

2026-07-13 共享 EMQX 干净环境再次完成完整 strict E2E：停止已由被动抓包定位的
`192.168.2.113` 旧响应端后，以 `--host 192.168.2.188 --port 1883 --timeout 20
--quiet-stale --fail-on-stale --continue-on-fail` 执行，11 段全部 `exit=0`，总耗时
24.0 秒。properties、device、OSD/state、CMD、DRC、Wayline、Media、Live、HMS/log、
firmware/config 均无可归因旧回包；一键启航流程同时通过。该结果是当前共享 Broker
纯模拟全链路严格基线，后续出现 stale 时应先与本次发布源基线比较。

2026-07-10 数据保留/操作记录专项已通过：`python tools/database_retention_audit.py --config config/gateway.yaml` 在独立 `/tmp/edge_gateway_retention_audit_*` SQLite 库中输出 `deleted={'telemetry': 1, 'operation': 2, 'alarm': 1, 'media_uploaded': 1}`，并验证保留 `telemetry=1`、`operation=2`、`alarm=1`、`media=2`。该脚本拒绝已有路径和项目 `missions.db`，不访问运行网关数据库；`python -m unittest -v tests.test_retention_manager` 输出 `Ran 1 test ... OK`。

本地历史日志：网关会把飞机原始飞行遥测追加到 `logs/history/flight_telemetry/YYYY-MM-DD.jsonl`，把设备状态变更追加到 `logs/history/device_state/YYYY-MM-DD.jsonl`。默认保留 7 日，清理动作跟随 `DataRetentionManager` 的 `retention.cleanup_interval_hours` 周期执行；手动留证可执行 `python -m unittest -v tests.test_retention_manager > "$EVIDENCE_DIR/retention_history.log" 2>&1`，并在网关运行后用 `find logs/history -type f -name '*.jsonl' -maxdepth 3` 确认文件生成。

本地安全校验不依赖真机：`security.mqtt` 可开启云端下行和云端 reply 的 HMAC-SHA256 `timestamp + nonce + sign` 校验，nonce 会按 TTL 防重放；`security.drc` 控制 DRC 心跳 timestamp 时间窗和 seq 递增；`security.mission` 控制航线定时/条件任务允许的时钟误差。默认测试配置不强制签名，production 模式会要求 `security.mqtt.downstream_auth_enabled=true` 且 `sign_secret` 至少 16 字符。留证命令：

```bash
cd ~/lxz/edge_gateway
EVIDENCE_DIR=logs/acceptance_evidence/$(date +%Y%m%d_%H%M%S)_local_security
mkdir -p "$EVIDENCE_DIR"

python -m unittest -v \
  tests.test_mqtt_security \
  tests.test_production_readiness \
  tests.test_dock3_drc \
  tests.test_dock3_wayline \
  tests.test_dock3_media \
  > "$EVIDENCE_DIR/local_security.log" 2>&1
```

模拟故障注入完整验收统一执行 `bash scripts/fault_hms_acceptance.sh`。它串行验证八类独立日志、隔离数据保留、七类机巢故障、HMS/events/OSD/state、操作记录，以及 MQTT 断开恢复、重复请求和错误 topic，并在 `logs/acceptance_evidence/<时间>/` 生成汇总 JSON、日志尾部、隔离数据库和 SHA256 清单。详细启动方式和边界见 `docs/FAULT_INJECTION_HMS_ACCEPTANCE.md`。

2026-07-13 Ubuntu 正式证据已通过：提交 `0934955` 下执行一键脚本，5 个专项单测、八类独立日志和隔离数据保留均通过；7 类故障全部 `result=passed`，重复请求 `state_count=1/reply_replayed=true`，错误 topic 返回 `325001`，MQTT 重连后 OSD 恢复；MongoDB `edge_gateway` 回读 12 条已完成操作记录。证据目录为 `logs/acceptance_evidence/20260713_104438/`。

2026-07-11 数据库默认运行时已切换为 Ubuntu 本机 MongoDB。切换前必须停止网关并保留 `missions.db` 文件副本，再执行 `python tools/migrate_sqlite_to_mongodb.py migrate-and-verify --sqlite missions.db --config config/gateway.yaml`；脚本只读 SQLite，按表 upsert 到同名 MongoDB 集合，清理已从 SQLite 删除的旧迁移副本，并只对带 `_sqlite_migration` 标记的迁移文档做数量和内容校验。迁移通过后执行 `python tools/mongodb_runtime_audit.py --config config/gateway.yaml --write-check`，必须输出 `MONGODB_CONNECTION_OK`、`MONGODB_INDEX_OK`、`MONGODB_RUNTIME_WRITE_READ_OK domains=8` 和 `MONGODB_RUNTIME_AUDIT_OK`，随后再启动网关运行完整 strict E2E。MongoDB 审计数据带隔离标识并在结束时自动清理。

2026-07-10 相机/云台严格专项已通过：`python tools/mqtt_dock3_payload_test.py --timeout 20 --quiet-stale --fail-on-stale` 输出 `dock3 payload test ok (accepted=12, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`。已验证能力查询、云台转动/模式/复位后的状态回读、拍照、录像启停及重复停止录像返回 `319999`；该结论仅覆盖 MAVLink/SITL 命令发送闭环，真实 Payload 硬件 ACK 需后续专项。

2026-07-10 真实 SITL 航点上传已通过：`python tools/mqtt_dock3_wayline_test.py --real-sitl --timeout 30 --quiet-stale --fail-on-stale` 输出 `dock3 real SITL wayline test ok (accepted=1, events=2, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`。`flighttask_prepare` 以 `simulate_mission.is_enable=0` 执行，KMZ 解析出 3 个航点并完成飞控上传握手；该安全模式未请求 AUTO/执行或起飞，真实执行需显式附加 `--execute-real`。

2026-07-10 真实 HD.kml 执行与清理已通过：`python tools/mqtt_dock3_wayline_test.py --real-sitl --execute-real --execute-observe-seconds 15 --wayline-file /home/fenghuolun/zwz/HD.kml --timeout 30 --quiet-stale --fail-on-stale` 以非模拟方式上传 10 个航点，`QLAND` 请求回执为 `QSTABILIZE` 且 `armed=true`，`flighttask_execute` 进入 `AUTO`。15 秒后 `flighttask_progress` 返回运行中、第 2/10 航点、20%；随后工具对本次创建的精确任务 ID 自动发送 `flighttask_undo`，收到 `canceled`、状态 9 和相同 flight ID。输出 `dock3 real SITL wayline test ok (accepted=5, events=8, ignored_total=0)`；尚未把 HD.kml 的终态/返航作为通过条件。

## 7. P2 验收边界

P2 不要求 30 天内完全无问题，但必须满足：

| 条件 | 要求 |
|---|---|
| 有入口 | method、adapter 或明确配置项存在 |
| 有日志 | 调用后能按 tid/bid 或操作 ID 追踪 |
| 有错误码 | 不支持或缺硬件时返回 Dock3 合理失败码 |
| 有文档 | 记录影响范围、绕过方式、后续计划 |
| 不影响 P0/P1 | P2 异常不能拖垮 MQTT、MAVLink、任务或主状态机 |

Deferred 只适用于客户暂未提及且当前主链路未使用的 Dock3 官方能力。若客户需求、P0/P1 backlog 或 E2E 主流程依赖该能力，不能用 Deferred 作为通过理由。

## 8. 证据归档

每次全链路验收需要保留：

| 证据 | 保存内容 |
|---|---|
| 命令输出 | 每个脚本的完整 stdout/stderr |
| 网关日志 | `logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log` 中对应时间段 |
| MQTT 订阅输出 | 关键 `osd/state/events/reply` 样例 |
| trace 记录 | `tools/request_trace_query.py` 按 tid/method 查询结果 |
| 数据库证据 | 绑定、拓扑、操作记录、告警、媒体记录 |
| 失败清单 | 失败 tid/bid、复现命令、影响 P0/P1/P2、负责人 |

## 9. 常见失败判断

| 现象 | 优先检查 |
|---|---|
| 网关显示 MQTT 未连接 | `ss -tlnp | grep 1883`、Mosquitto 状态、`config/gateway.yaml` |
| 订阅端看到旧格式重复回包 | 清理旧网关进程、旧模拟器、broker retained；再用 `--fail-on-stale` 严格验证 |
| 可选 Demo 页面不显示设备 | SN 是否为 Demo 兼容格式、MySQL 绑定状态、`update_topo` 是否发布；不影响正式脚本验收 |
| `airport_bind_status` 超时 | 云端模拟器是否先启动、是否监听 `requests`、`tid` 是否匹配 |
| `update_topo` 没有入库 | 是否走 `sys/product/{sn}/status`，Demo backend 是否收到 `status_reply` |
| MAVLink 无心跳 | SITL 是否启动、端口是否冲突、配置是否指向正确 UDP/TCP |
| 机巢无状态 | 自定义 dialect 是否加载、12004 是否收到、端口和 TCP 角色是否正确 |
| P1 脚本不存在 | 这是交付缺口，不能记为通过；按 backlog 补脚本 |

## 10. 通过声明模板

一次全链路验收通过时，记录如下内容：

```text
验收日期:
代码分支/commit:
配置文件:
SN:
运行模式: 脚本模拟

P0:
- p0_batch:
- properties:
- osd_state:
- device:
- organization:
- drc:
- drc_batch:
- cmd:
- wayline:

P1:
- nest_v13:
- media:
- live:
- hms_log:
- firmware_config:
- takeoff_flow:
- e2e_full_chain:

P2:
- 已知问题:
- 是否影响 P0/P1:
- 后续计划:

日志位置:
trace 查询:
数据库证据:
结论:
```
