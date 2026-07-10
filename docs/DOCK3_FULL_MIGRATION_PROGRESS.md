# DJI Dock 3 客户需求迁移进度台账

更新时间：2026-07-09

本文档用于记录 Edge Gateway 从当前“链路已通 + P0 局部适配”推进到“客户需求相关网关能力按 DJI Dock 3 协议合规”的逐组迁移进度。后续每完成一个客户需求相关字段、method、topic、reply 或 smoke test，都必须更新本文档。

当前需求来源以客户最新三份资料为准：`项目优先级整理.docx` 定义优先级，`业务流程.md` 定义业务闭环，`网关和平台侧重点.docx` 定义网关/平台边界。`DJI_Dock固件功能清单77.docx` 作为历史功能全集参考，不再作为当前优先级的第一来源。

本项目只评估**网关侧 Dock3 云端模拟链路 + 设备侧 MAVLink/机巢 adapter**。平台侧台账、权限、前端页面、业务工单、多设备批量调度、全量历史统计、长期数据仓库、媒体资源库和告警通知推送不计入本网关项目缺口。

如果只需要快速查看当前哪些模块已完成、哪些未完成，看 `docs/DOCK3_MODULE_STATUS.md`。

如果要按客户需求排开发优先级，看 `docs/P0_P1_P2_DELIVERY_BACKLOG.md`。如果要查 P0/P1 代码缺口和脚本补齐顺序，看 `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md`。如果要执行全链路验收，看 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md`。如果要判定 90% 可用率、100% 开发完成和 P0/P1 强门禁，看 `docs/DELIVERY_ACCEPTANCE_SCORECARD.md`。30 天交付口径是 P0/P1 100% 可用，P2 可存在已记录问题但不能影响主链路。

当前结论：**Dock3 P0 总批量、properties 大类专项、OSD/state、organization 基础 requests 链路、organization 多设备绑定、device topology、DRC 大类专项和批量链路、CMD/远程调试大类、Wayline/航线管理 P1 基础闭环、Media 基础链路、Live mock 推流基础链路、HMS/远程日志 P1 专项、firmware/config 专项均已通过 Ubuntu 模拟验证；`tools/e2e_takeoff_flow_test.py --timeout 12` 已在服务器单独通过，`tools/e2e_full_chain_test.py --timeout 12 --quiet-stale` 已在服务器通过 properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config 11 段，最新输出 `dock3 e2e full-chain test ok (steps=11, elapsed=18.9s)`。DRC 专项已在服务器用 `property_get(latitude/longitude/height)` 生成电子围栏内目标点，`takeoff_to_point` 与 `fly_to_point_update` 均返回 `simulated=false`，最新输出 `dock3 drc test ok (accepted=15, events=8, ignored_mismatched=0, ignored_legacy=3, ignored_late=7, ignored_duplicate=7, ignored_unknown=0, ignored_total=10)`，并输出 `stale_samples` 定位重复旧回包。CMD 单段已重跑通过，覆盖 60 条回包、18 条事件和 E2E cmd 单段，输出 `dock3 cmd test ok (accepted=60, events=18, ignored_total=60)`、`dock3 e2e full-chain test ok (steps=1, elapsed=5.0s)`，并验证 `cover_close` 后 `cover_state=0`。外围启动脚本在修复 Mosquitto 重复 listener 配置后已验证可启动 MQTT Broker、云端模拟器、MQTT 观察端和机巢模拟器。机巢状态到 Dock3 state/HMS/dock_operation 的本地桥接已补齐，`flighttask_execute` 已安全触发机巢 `request_takeoff`，`tools/nest_v13_protocol_test.py --verbose` 已扩展到 `checks=5`。人员1负责的云端 topic/reply 边界、专项验收脚本、一键启航离线闭环、P0/P1 核心 E2E、device 多设备拓扑、organization 多子设备绑定、CMD 状态回读和 DRC 旧回包诊断已收敛到共享测试入口；当前 `--fail-on-stale` 已覆盖 OSD/state 旧样本统计。下一步转向严格环境服务器复测、机巢 v1.3 Ubuntu 实跑、真实 SITL 航点执行、数据保留专项和字段精校。客户暂未提及或当前业务未用到的 Dock3 能力可以延期，不纳入 30 天 P0/P1 强迁移。**

当前进度估算：**约 81%**。

估算口径：

| 范围 | 进度 | 说明 |
|---|---:|---|
| Ubuntu 纯模拟部署与基础链路 | 93% | MQTT、SITL、网关、OSD、基础 command/reply 已跑通，服务器已通过 11 段 P0/P1 E2E；当前默认 E2E 已接入一键启航离线闭环；device 多设备拓扑专项和 E2E device 单段已通过服务器验证；`scripts/start_support_stack.sh` 已能中文汇总启动结果，修复 Mosquitto 重复 listener 后外围组件启动成功 |
| Dock3 P0 协议链路 | 97% | property、services、requests、status、events、OSD/state、DRC 控制数据、CMD 控制命令、错误码边界已通过批量、专项和 E2E 编排验证，剩旧回包噪声清理和字段精校 |
| 客户需求相关 method/字段迁移 | 81% | properties 官方可写/只读边界、OSD/state、device topology、多子设备离线重连、DRC 官方字段和批量链路、organization 基础链路、organization 多子设备绑定、CMD/远程调试 60 条模拟验收、Wayline P1 基础闭环、一键启航离线闭环、Media 基础链路、Live 基础链路、HMS/log 基础链路、firmware/config 基础闭环已通过专项、本地单测或服务器 MQTT 验证；11 段 E2E 已通过服务器验证；DRC 真实指点飞行已通过 `simulated=false` 复测 |
| 非阻塞、日志、容错基础 | 67% | 日志落盘、字段中文名、部分 async warning 已修复；非阻塞 JSONL request trace 和查询工具已实现；远程日志列表/上传模拟/进度事件已通过 Ubuntu 专项复测；数据保留和云同步已补秒级/毫秒级混合时间戳清理工具及单测；NoSQL/MongoDB 仍待迁移 |
| 模拟器和测试工具 | 99% | 云端模拟器、organization/status/topology/properties/OSD-state/DRC/CMD/Wayline/Media/Live/HMS-log/Firmware-config/P0 批量测试工具可用，`mqtt_organization_test.py` 已支持 `--bind-sub-devices` 多 SN 绑定和 paired 校验，`e2e_full_chain_test.py` 已扩展到 11 段 P0/P1 域并接入 `takeoff-flow`；11 段全量 E2E 已通过服务器复测，OSD/state 已支持严格 stale 统计，DRC 专项已输出 `stale_samples` 旧回包样例 |

综合按当前目标“服务器内跑通客户需求相关 Dock3 网关模拟链路，不接真实实体设备、不交付平台侧功能”估算为约 81%。该百分比不是验收承诺，后续每完成一个客户需求相关网关分组再调整。

## 0. 客户三文档后的当前状态

| 状态 | 当前结论 |
|---|---|
| 已做到 | MQTT topic/reply 边界、设备上线/心跳、组织绑定、`update_topo` 设备拓扑、properties、OSD/state、错误码、request trace、CMD 基础控制、DRC 基础链路和心跳、Wayline mock KMZ 基础闭环、一键启航离线闭环、Media 基础、Live mock、HMS/log、firmware/config 专项、P0/P1 核心 E2E |
| 部分做到 | 飞控真实 SITL 行为、机巢 v1.3 状态机、一键启航 MQTT/SITL 实跑证据、真实机巢联动、真实航点执行、多设备/解绑/离线、相机/云台真实控制、数据保留和操作记录 |
| 未做到/待迁移 | 真实 OTA、真实 OSS 上传、真实直播推流、真实 PSDK/ESDK、MongoDB/NoSQL 目标架构、生产 TLS/鉴权 |
| 不属于本网关项目 | 航线库编辑、前端页面、用户权限、业务工单、多设备批量调度、全量历史统计、长期数据仓库、媒体资源库、告警通知推送 |

## 1. 当前验收基线

| 项目 | 当前结果 | 证据 |
|---|---|---|
| `property_set` | 通过 | `property/set -> property/set_reply`，`silent_mode.state.result=0` |
| `property_get` | 通过 | `services -> services_reply`，可读回 `silent_mode=1`、`drc_state` |
| `drc_mode_enter` | 通过 | `services -> services_reply`，返回 `result=0` |
| `drc_mode_enter` 状态同步 | 通过 | enter 后 `property_get({"properties":["drc_state"]})` 返回 `drc_state=2` |
| `drc_mode_exit` | 通过 | `services -> services_reply`，返回 `result=0` |
| `drc_mode_exit` 状态同步 | 通过 | exit 后 `property_get({"properties":["drc_state"]})` 返回 `drc_state=0` |
| 错误 topic | 通过 | `drc_mode_enter` 误发到 `drc/down` 返回 `drc/up`，`result=325001` |
| `airport_bind_status` services 冒烟 | 兼容可选，非最终标准 | Dock3 官方组织管理中该 method 属于 `requests/requests_reply`；P0 总批量脚本默认不强制 services 兼容路径通过 |
| organization requests 模拟器 | 已通过 Ubuntu 验证 | `tools/mqtt_cloud_simulator.py` 收到 `airport_bind_status` 的 `requests` 并发布 `requests_reply` |
| organization 回包处理 | 已通过 Ubuntu 验证 | 网关日志出现 `Request reply: method=airport_bind_status`、`Bind status reply processed: 1 devices` |
| requests 本地回环保护 | 已通过 Ubuntu 验证 | 网关日志出现 `Device-originated request observed, skip downstream routing`，没有误发 services 回包 |
| coroutine 未 await warning | 已修复并通过 Ubuntu 复测 | `DeviceDB`、`AlarmDB` 已改为 async helper；信号停止流程改为由 `stop_async()` 统一 await；Ubuntu 日志复测未再出现 `RuntimeWarning`、`was never awaited` |
| organization get/bind 测试工具 | 成功路径和异常分支均已通过 Ubuntu 复测 | `tools/mqtt_organization_test.py` 已通过项目 `DeviceManager + CloudConnectionManager` 发起 `airport_organization_get`、`airport_organization_bind`；云端模拟器已验证 get 失败、bind `err_infos`、延迟超时 |
| `update_topo` status/status_reply | 已通过 Ubuntu 复测 | 绑定后发布 `sys/product/EDGE-GW-001/status`，`method=update_topo`，`tid=f0845cda-bf5b-40b2-a0f7-d2e4cbb65c6b`；收到 `status_reply result=0` |
| `update_topo` 多子设备拓扑 | 已通过 Ubuntu 复测 | 注入 `DRONE-SIM-001`、`DRONE-SIM-002` 后发布 `update_topo`，`sub_device_count=2`，离线阶段 `sub_devices=[]`，重连阶段双子设备恢复，收到 `status_reply result=0` |
| OSD/state 专项 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_osd_state_test.py --timeout 15 --quiet-stale` 输出 `dock3 osd/state test ok`，结果 `dock_osd=1, drone_osd=1, state=1, invalid=0`；飞行器 OSD 已出现 SITL 动态经纬度、电池和 GPS |
| 错误码边界归一 | 通过本地测试 | `tests/test_dji_error_codes.py` 验证内部负数转 DJI 错误码 |
| properties 可写边界 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale` 输出 `dock3 properties test ok (accepted=10, ignored_total=10)`；官方 `rw` 属性成功，只读/非法/未知属性返回 `state.result=1` |
| device topology 专项 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_device_test.py --timeout 12 --quiet-stale --extra-sub-device-json ...` 已输出 `dock3 device topology test ok (status_messages=4, sub_device_count=2, sub_device_sns=DRONE-SIM-001,DRONE-SIM-002, reconnect=True, unbind=True, accepted=5, status=4, events=4, ignored_total=4)`；验证 `update_topo status/status_reply`、多设备在线、离线空拓扑、重连恢复、无效拓扑拒绝、`device_unbinding events ok: count=3` 和解绑后空 `update_topo` |
| organization full 专项 | 已通过 Ubuntu 专项复测 | `tools/mqtt_organization_test.py --config config/gateway.yaml --action full ... --publish-events --expect-status-reply` 输出 `organization request test ok`；验证 `airport_organization_get/bind`、`device_binding`、`update_topo status_reply result=0` 和内存绑定；多子设备绑定已通过服务器 MQTT 复测，输出 `sub_devices=2`、3 次 `device_binding`、两个子设备 `paired ok` |
| DRC 专项字段校验 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale --osd-wait 6` 最新输出 `dock3 drc test ok (accepted=15, events=8, ignored_mismatched=0, ignored_legacy=3, ignored_late=7, ignored_duplicate=7, ignored_unknown=0, ignored_total=10)`；验证 `drc_mode_enter` 官方 payload、`stick_control` 官方杆量、`drone_control` 官方废弃字段和值域错误、官方心跳、`takeoff_to_point`、`fly_to_point_update/stop`，其中目标点由 `property_get(latitude/longitude/height)` 生成，`takeoff_to_point` 与 `fly_to_point_update` 均返回 `simulated=false`；脚本输出 `stale_samples` 定位重复旧回包，当前旧回包不影响正确 Dock3 回包 |
| DRC 批量验收 | 已通过 Ubuntu 专项复测 | `tools/mqtt_drc_batch_test.py --timeout 12 --quiet-stale` 输出 `drc batch test ok (accepted=10, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`；覆盖 enter/exit、`drc_state`、杆量、飞行器/负载控制和速度控制 |
| CMD/远程调试专项 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale` 输出 `dock3 cmd test ok (accepted=60, events=18, ignored_mismatched=0, ignored_legacy=14, ignored_late=46, ignored_wrong_topic=0, ignored_total=60)`；E2E cmd 单段输出 `dock3 e2e full-chain test ok (steps=1, elapsed=5.0s)`；验证调试模式、舱盖/充电/推杆、飞行器/设备动作、枚举开关、安全拒绝、格式化/重启模拟保护、`cover_close` 状态回读和任务进度事件 |
| Wayline/航线管理 P1 | 已通过 Ubuntu 严格专项复测 | 修复被动机巢 UDP 模拟器注册和默认待命状态后，`tools/e2e_full_chain_test.py --only wayline --timeout 20 --quiet-stale --fail-on-stale` 输出 `dock3 wayline test ok (accepted=11, events=12, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`；覆盖 `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel`、参数错误和状态不支持错误分支；纯模拟任务不触发真实飞控 `AUTO/LOITER/返航/降落` 控制 |
| Media/媒体管理 P1 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_media_test.py --timeout 12 --quiet-stale` 输出 `dock3 media test ok (accepted=6, events=2, ignored_total=0)`；覆盖 `file_list_get`、`adjust_upload_priority`、`highest_priority_upload_flighttask_media`、`file_delete`、非法删除错误码和事件 |
| Live/直播 P1 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_live_test.py --timeout 12 --quiet-stale` 输出 `dock3 live test ok (accepted=6, events=4, ignored_total=0)`；覆盖 `live_capability_query/live_start/live_set_quality/live_lens_change/live_stop`、`mock://` 推流、`live_status_notify` 事件和非法 `live_start` 错误码 |
| HMS/远程日志 P1 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale` 输出通过；覆盖 `hms_get/hms_clear`、HMS 告警注入/查询/清除、`hms` 事件、`device_log_list_get`、日志文件列表和本地测试日志发现 |
| P0 总批量验证入口 | 已通过 Ubuntu 复测 | `tools/mqtt_dock3_p0_batch_test.py --timeout 12` 一次覆盖 `dock_status_query`、properties、DRC、错误 topic，输出 `dock3 p0 batch test ok`；`airport_bind_status` 官方链路单独走云端模拟器 |
| P0/P1 E2E 回归入口 | 已通过 | `tools/e2e_takeoff_flow_test.py --timeout 12` 已在服务器单独通过；`tools/e2e_full_chain_test.py --only device --timeout 12 --quiet-stale --device-extra-sub-device-json ...` 已通过；`tools/e2e_full_chain_test.py --timeout 12 --quiet-stale` 已在服务器通过 11 段，最新输出 `dock3 e2e full-chain test ok (steps=11, elapsed=18.9s)` |
| 旧格式重复回包 | 已识别，非阻塞 | P0 批量测试中可见同一 `tid` 先收到旧格式 `property/set_reply` 或旧 `data.result=-2` 回包，脚本已按 `tid + method + Dock3格式` 忽略并继续等待正确回包；后续清理 broker/旧进程后可用 `--fail-on-stale` 做严格验证 |

### 1.1 命令链路专用日志

为方便无平台联调时判断“云端/MQTT -> 网关 -> MAVLink 机巢 -> 网关 -> MQTT 回包”是否通，已新增独立命令链路日志。该日志只记录 MQTT 下发命令、网关 handler、网关转发的机巢命令、机巢执行回包和 MQTT 回包，不记录心跳、OSD 定频上报、空轮询。

网关侧日志文件：

```text
logs/gateway/command_trace/YYYY-MM/command_trace_YYYY-MM-DD.log
```

机巢模拟器侧日志文件：

```text
logs/nest_simulator/command_trace.log
```

查看命令链路：

```bash
mkdir -p logs/gateway/command_trace/$(date +%Y-%m) logs/nest_simulator
touch logs/gateway/command_trace/$(date +%Y-%m)/command_trace_$(date +%F).log logs/nest_simulator/command_trace.log
tail -F logs/gateway/command_trace/$(date +%Y-%m)/command_trace_$(date +%F).log
tail -F logs/nest_simulator/command_trace.log
```

典型事件顺序：

```text
[MQTT收到命令] method=cover_open tid=...
[网关下发机巢] method=cover_open gcs_command=DOOR_OPEN
[网关编码机巢命令] commands=["DOOR_OPEN"] ordinary_order=0x...
[网关发送MAVLink] ordinary_order=0x... takeoff_land_order=0x...
[网关收到机巢回复] ordinary_raw=... ordinary_reply_status=...
[网关处理完成] method=cover_open result=0
[网关回复MQTT] method=cover_open tid=... result=0
```

机巢模拟器侧典型事件：

```text
[机巢收到命令] command=open-door ordinary=0x...
[机巢发送回包] command=open-door ordinary_response=0x...
```

机巢模拟器默认不再打印心跳和空轮询。如果需要看周期状态帧，启动模拟器时显式加 `--log-status`。

## 2. 客户需求分组进度

状态说明：

| 状态 | 含义 |
|---|---|
| 已验证 | 已按 Dock3 topic/reply/payload 在 Ubuntu 模拟环境通过 |
| 部分完成 | 已有代码或部分字段可用，但未完成客户需求相关字段/枚举/单位对照 |
| 待迁移 | 客户需求涉及，已有旧实现或占位，需要按 Dock3 改造 |
| 暂缓 | 当前客户需求未用到或不阻塞主链路，保留接口或占位 |
| Deferred | Dock3 官方存在但客户暂未提及/未使用，后续需求触发时迁移 |

| 顺序 | Dock3 分组 | 官方文档 | 当前状态 | 当前代码入口 | 下一步 |
|---:|---|---|---|---|---|
| 1 | 设备属性 properties | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/properties.html | 部分完成，官方 `rw` 属性和值域边界已通过 Ubuntu 专项复测 | `src/device/manager.py`, `src/cloud_api/dock3_properties.py`, `src/mavlink/telemetry.py`, `src/cloud_api/telemetry_reporter.py`, `tools/mqtt_dock3_properties_test.py` | 继续补客户需求涉及的 `osd/state` 字段、枚举、单位和值域精校 |
| 2 | 设备管理 device | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/device.html | 部分完成，`update_topo` 官方字段校验和专项脚本已通过 Ubuntu 复测；多子设备、离线、重连和解绑的状态语义已补，服务器多设备 MQTT 专项和 E2E device 单段已通过；`device_unbinding` 逐 SN 事件和空拓扑验收入口已本地完成 | `src/device/manager.py`, `src/cloud_api/dock3_device.py`, `src/cloud_api/connection_manager.py`, `tools/mqtt_cloud_simulator.py`, `tools/mqtt_dock3_device_test.py`, `tools/mqtt_organization_test.py` | 复测解绑专项并继续精校 `domain/type/sub_type` 官方枚举 |
| 3 | 组织管理 organization | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/organization.html | 部分完成，基础成功路径、异常分支、绑定后事件和持久化均已通过 Ubuntu 验证；多子设备绑定本地状态联动已补 | `src/cloud_api/connection_manager.py`, `src/device/manager.py`, `tools/mqtt_cloud_simulator.py`, `tools/mqtt_organization_test.py` | 服务器复测 `--bind-sub-devices`，继续字段精校和真实云端差异 |
| 4 | 直播 live | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/live.html | 已通过 Ubuntu 专项复测 | `src/cloud_api/dock3_live.py`, `src/media/manager.py`, `tools/mqtt_dock3_live_test.py` | 后续接真实 RTMP/WebRTC/GB28181 adapter |
| 5 | 媒体 media | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/media.html | 已通过 Ubuntu 专项复测 | `src/cloud_api/dock3_media.py`, `src/media/manager.py`, `tools/mqtt_dock3_media_test.py` | 后续精校 OSS 凭证、断点续传和上传完成事件 |
| 6 | 航线 wayline | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/wayline.html | P1 基础闭环和 mock KMZ 解析已通过 Ubuntu 专项复测；基础 flighttask services/reply 和进度事件已收敛；纯模拟任务不强制真实飞控 `AUTO/LOITER/返航/降落` 控制 | `src/mission/manager.py`, `src/cloud_api/dock3_wayline.py`, `src/modules/waypoint_manager.py`, `tools/mqtt_dock3_wayline_test.py`, `tests/test_dock3_wayline.py` | 后续继续远端 KMZ 下载、媒体计数、真实 SITL 航点执行、返航事件字段精校 |
| 7 | HMS | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/hms.html | 已通过 Ubuntu 专项复测 | `src/cloud_api/dock3_hms_log.py`, `src/device/manager.py`, `src/database/alarm_db.py`, `tools/mqtt_dock3_hms_log_test.py` | 机巢故障位和 HMS 文案 key 精校 |
| 8 | 远程调试 cmd | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/cmd.html | 已通过 Ubuntu 专项复测，代码已按 `output.status`、枚举校验、进度事件和模拟安全重启收敛 | `src/device/manager.py`, `src/cloud_api/dock3_cmd.py`, `tools/mqtt_dock3_cmd_test.py`, `tests/test_dock3_cmd.py` | 后续精校真实 Dock3 任务耗时、失败码、设备重启/格式化真实执行开关 |
| 9 | 固件升级 firmware | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/firmware.html | 已通过 Ubuntu 专项复测 | `src/device/manager.py`, `tools/mqtt_dock3_firmware_config_test.py`, `tests/test_device_manager_firmware_config.py` | 继续补真实 OTA 下载/校验/刷写/备份/回滚 adapter |
| 10 | 远程日志 log | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/log.html | 已通过 Ubuntu 专项复测 | `src/cloud_api/dock3_hms_log.py`, `src/modules/log_manager.py`, `src/device/manager.py`, `tools/mqtt_dock3_hms_log_test.py` | 后续补压缩、真实 OSS 上传和超时策略 |
| 11 | 配置更新 config | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/config.html | 已通过 Ubuntu 专项复测 | `src/device/manager.py`, `tools/mqtt_dock3_firmware_config_test.py`, `tests/test_device_manager_firmware_config.py` | 继续精校真实云端 config 字段和边界 |
| 12 | 指令飞行 drc | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/drc.html | 部分完成，控制数据、P0 总批量、DRC 官方字段、心跳、`takeoff_to_point` 和指点飞行基础闭环均已通过 Ubuntu 复测 | `src/drc/controller.py`, `src/cloud_api/dock3_drc.py`, `src/mission/manager.py`, `src/modules/flight_control.py`, `tools/mqtt_drc_batch_test.py`, `tools/mqtt_dock3_drc_test.py`, `tools/mqtt_dock3_p0_batch_test.py` | 继续精校超时恢复边界和 MAVLink 模式语义 |
| 13 | 自定义飞行区 custom-fly-region | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/custom-fly-region.html | 待迁移 | `src/modules/flight_area_manager.py` | 对齐 `flight_areas_update/get` 和缓存模型 |
| 14 | PSDK 功能 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/psdk.html | 暂缓 | `src/payload/third_party.py` | 真实负载暂不做，保留模拟回包 |
| 15 | PSDK 互联互通 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/psdk-transmit-custom-data.html | 暂缓 | `src/modules/custom_data_manager.py` | 后续定义透传数据契约 |
| 16 | ESDK 互联互通 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/esdk-transmit-custom-data.html | 暂缓 | `src/modules/custom_data_manager.py` | 后续定义透传数据契约 |
| 17 | 远程解禁 flysafe | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/flysafe.html | 暂缓 | `src/modules/flysafe_manager.py` | 先保留接口，后续补文件/证书流程 |
| 18 | AirSense | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/airsense.html | 暂缓 | `src/device/manager.py` | 当前无实体传感器，先固定模拟或事件占位 |
| 19 | 远程控制 remote-control | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/remote-control.html | 待迁移 | `src/drc/controller.py`, `src/modules/flight_control.py` | 等 DRC 控制通道和飞控模式映射稳定后处理 |

## 3. 第一批官方对照结论

### 3.1 properties

官方要点：

- `osd`：设备以 `0.5Hz` 频率上报定频属性，topic 为 `thing/product/{device_sn}/osd`。
- `state`：状态变化时上报，topic 为 `thing/product/{device_sn}/state`。
- `rw` 属性通过 `thing/product/{gateway_sn}/property/set` 设置，并通过 `property/set_reply` 逐属性返回 `state.result`。
- 当前官方 Dock3 properties 页确认的 `rw` 顶层属性包括 `air_transfer_enable`、`silent_mode`、`user_experience_improvement`。
- 已确认的关键只读属性包括 `drc_state`、`mode_code`、`cover_state`、`drone_in_dock`、`battery_store_mode`、`alarm_state`、`position_state`、`drone_charge_state` 等。
- `putter_state`、`charging_state`、`air_conditioner_mode`、`remaining_power_for_return_home` 在当前代码中存在，但本批官方 properties 抽取未作为 Dock3 顶层属性命中，先标为历史/模拟字段，后续按 device/cmd/config 等分组继续核对。

当前结论：

| 项目 | 状态 | 说明 |
|---|---|---|
| `property_set` topic | 已验证 | 已按 `property/set -> property/set_reply` 通过 |
| `property_set` 回包结构 | 已验证 | `data.{property}.state.result` |
| `silent_mode` | 已通过 Ubuntu 专项复测 | 只接受 `0/1`，拒绝 bool/string/越界值 |
| `air_transfer_enable` | 已通过 Ubuntu 专项复测 | 只接受 bool，拒绝 `0/1` 数字 |
| `user_experience_improvement` | 已通过 Ubuntu 专项复测 | 只接受 `0/1/2` |
| 非官方 `rw` 历史字段 | 已通过 Ubuntu 专项复测 | `battery_store_mode`、`alarm_state`、`drc_state`、未知属性和非法值通过 `property/set` 返回 `state.result=1`；相关动作保留 services 控制 |
| `drc_state` | 已验证 | enter 后为 `2`，exit 后为 `0` |
| OSD 遥测 | 已通过 Ubuntu 专项复测 | `mqtt_dock3_osd_state_test.py` 已通过，结果 `dock_osd=1, drone_osd=1, state=1, invalid=0`；经度、纬度、高度、电池、姿态、GPS 已具备基础上报结构，字段仍需逐项对照官方属性表 |
| Dock 状态字段 | 部分完成 | `cover_state`、`putter_state`、`drone_in_dock`、`charging_state` 已纳入 OSD/state 验收；当前仍是模拟枚举，未完成 Dock3 枚举精校 |

#### 3.1.1 Batch 1 字段对照结果

| 字段 | 官方 Dock3 名称 | 官方访问 | pushMode | 当前项目状态 | 下一步 |
|---|---|---|---|---|---|
| `air_transfer_enable` | 空中回传 | `rw` | `1` | 已通过 Ubuntu 专项复测：`property/set` 返回 `state.result=0`，`property_get` 可读回 | 后续补客户需求相关 state/osd 字段精校 |
| `silent_mode` | 机场静音模式 | `rw` | `1` | 已通过 Ubuntu 专项复测，可设置并读回 | 后续补客户需求相关 state/osd 字段精校 |
| `user_experience_improvement` | 用户体验改善计划 | `rw` | `1` | 已通过 Ubuntu 专项复测：`property/set` 返回 `state.result=0`，`property_get` 可读回 | 后续补客户需求相关 state/osd 字段精校 |
| `drc_state` | DRC 链路的状态 | `r` | `0` | 已通过 enter/exit 同步验证 | 补 DRC 心跳超时恢复 |
| `mode_code` | 机场状态 | `r` | `0` | 当前 OSD 有 `mode_code`，但与飞控模式映射仍待人员2核对 | 按 Dock3 枚举精校 |
| `cover_state` | 舱盖状态 | `r` | `0` | 当前机巢模拟字段存在 | 按 Dock3 枚举精校 |
| `drone_in_dock` | 飞行器是否在舱 | `r` | `0` | 当前根据机巢/飞控推断 | 按 Dock3 状态来源精校 |
| `battery_store_mode` | 电池运行模式 | `r` | `0` | Ubuntu 已验证：`property/set` 返回 `state.result=1`；services 控制入口保留 | 对照 cmd/config 分组 |
| `alarm_state` | 机场声光报警状态 | `r` | `0` | Ubuntu 已验证：`property/set` 返回 `state.result=1`；services 控制入口保留 | 对照 cmd 分组 |
| `air_conditioner_mode` | 未在本批 properties 顶层命中 | 待确认 | 待确认 | 历史模拟字段保留，只读 | 对照 cmd/config 分组 |
| `remaining_power_for_return_home` | 未在本批 properties 顶层命中 | 待确认 | 待确认 | 历史配置字段保留，只读 | 对照 config/wayline 分组 |

专项验收命令：

```bash
python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 properties test ok (accepted=..., ignored_total=...)`。2026-07-06 已通过，实际输出 `accepted=10, ignored_total=10`。该脚本只覆盖 properties 大类，不执行 DRC、organization、topology 等其他分组；清理旧进程和 broker 噪声后可追加 `--fail-on-stale` 做严格验收。

### 3.2 organization

官方要点：

- `airport_organization_bind`、`airport_organization_get`、`airport_bind_status` 是设备主动请求云端的 `requests -> requests_reply` 模式。
- `airport_bind_status` 请求数据包含 `devices`，设备对象至少包含 `sn`。

当前结论：

| 项目 | 状态 | 说明 |
|---|---|---|
| 启动自动查询 | 已有实现 | `CloudConnectionManager.send_request('airport_bind_status', ...)` 已存在 |
| `requests_reply` 处理 | 已有实现 | `MQTTClient.set_requests_reply_handler()` 已接入 |
| 云端模拟器 | 已通过 Ubuntu 复测 | `tools/mqtt_cloud_simulator.py` 已验证 `airport_bind_status`、`airport_organization_get`、`airport_organization_bind` 的 `requests_reply` 模拟回包 |
| requests 本地回环保护 | 已通过 Ubuntu 复测 | 网关收到本客户端发布的 `/requests` 时只记录日志，不再误路由到 services handler |
| services 兼容 smoke | 可选，非最终标准 | `tools/mqtt_smoke_test.py --method airport_bind_status` 或 `mqtt_dock3_p0_batch_test.py --include-legacy-bind-status` 只作为本地联调兼容，不进入 P0 总批量必过项 |
| `airport_bind_status` 最终 Dock3 验收 | 已通过 Ubuntu 复测 | 启动自动查询收到 `requests_reply`，`tid` 匹配，未超时，绑定状态处理完成 |
| `airport_organization_get` | 已通过 Ubuntu 复测 | `tools/mqtt_organization_test.py --action full` 已发起 `requests`，收到 `requests_reply result=0` 和 `organization_name=模拟组织` |
| `airport_organization_bind` | 已通过 Ubuntu 复测 | `tools/mqtt_organization_test.py --action full` 已发起 `requests`，收到 `requests_reply result=0` 和 `output.err_infos=[]` |
| organization 多设备绑定 | 已通过 Ubuntu MQTT 复测 | `airport_organization_bind` 可同时包含网关和已安装子设备；成功子设备会更新绑定缓存、`paired` 状态、`device_binding` 事件和 `update_topo`；`mqtt_organization_test.py` 已验证 3 次 `device_binding`、`update_topo schema ok ... sub_devices=2`、`status_reply result=0`、网关和两个子设备 `memory binding ok`、两个子设备 `sub-device paired ok` |
| organization 失败分支 | 已通过 Ubuntu 复测 | 组织查询失败、绑定 `err_infos`、云端回包超时三类场景均已通过 |
| 绑定后事件 | 已通过 Ubuntu 复测 | `tools/mqtt_organization_test.py` 已捕获 `device_binding` 和 `update_topo` 事件 |
| 绑定后持久化 | 已通过 Ubuntu 复测 | `tools/mqtt_organization_test.py --verify-db` 已验证 SQLite `device_binding` 表中 `organization_id=org_sim_001`、`is_bound=true` |

Ubuntu 复测命令：

```bash
# 终端A：云端模拟器，先于网关启动
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --once --bind-status unbound --timeout 90

# 终端B：重启网关，触发启动自动 airport_bind_status
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

成功标志：

- 2026-07-06 Ubuntu 已验证：云端模拟器打印 `received <- thing/product/EDGE-GW-001/requests`，method 为 `airport_bind_status`。
- 2026-07-06 Ubuntu 已验证：云端模拟器打印 `published -> thing/product/EDGE-GW-001/requests_reply`。
- 2026-07-06 Ubuntu 已验证：网关日志出现 `Request reply: method=airport_bind_status`，且不再出现该请求超时。
- 2026-07-06 Ubuntu 已验证：网关日志出现 `Device-originated request observed, skip downstream routing`，说明本地 broker 回环没有误执行 services handler。
- 2026-07-06 Ubuntu 已验证：`airport_organization_get` 的 `tid=9a738feb-97a8-4851-a797-02d17ab06343` 收到 `requests_reply result=0`，返回 `organization_name=模拟组织`。
- 2026-07-06 Ubuntu 已验证：`airport_organization_bind` 的 `tid=8a0b1414-9853-4632-a172-913a207eff3f` 收到 `requests_reply result=0`，返回 `output.err_infos=[]`，测试工具输出 `organization request test ok`。
- 2026-07-06 Ubuntu 已验证：组织查询失败分支 `tid=b3816e96-c8f9-4b8e-b1be-c2f235f80d03` 收到 `requests_reply result=327000`，顶层 `msg=绑定码无效`，测试工具输出 `organization request test ok`。
- 2026-07-06 Ubuntu 已验证：绑定部分失败分支 `tid=d2bdf5d1-402f-45d1-82eb-da316c82e09e` 收到 `requests_reply result=0`，`output.err_infos[0].err_code=327000`，测试工具输出 `organization request test ok`。
- 2026-07-06 Ubuntu 已验证：云端回包超时分支 `tid=8e21e5fc-3e64-4a30-8268-4763cc7f751f` 按 `--expect-timeout --timeout 2` 返回预期超时，测试工具输出 `organization request test ok`。
- 2026-07-06 Ubuntu 已验证：绑定后状态分支 `airport_organization_get tid=5bb01523-3be5-4d36-93fd-b713fadffd38`、`airport_organization_bind tid=9e38069d-5c64-4dfe-ba99-fe4808b90cf2`；测试工具捕获 `device_binding`、`update_topo`，内存绑定 `organization_id=org_sim_001`，SQLite `device_binding` 记录 `is_bound=true`、`workspace_id=org_sim_001`、`workspace_name=模拟组织`。
- 2026-07-07 Ubuntu 已验证：`mqtt_organization_test.py --config config/gateway.yaml --action full --publish-events --expect-status-reply` 输出 `organization request test ok`；`airport_organization_get` 返回 `organization_name=模拟组织`，`airport_organization_bind` 返回 `output.err_infos=[]`，捕获 `device_binding`、`update_topo`，收到 `sys/product/EDGEGW001/status_reply result=0`，内存绑定 `organization_id=org_sim_001`。
- 2026-07-09 Ubuntu 已验证：`mqtt_organization_test.py --bind-sub-devices --expect-device-binding-count 3` 绑定网关 `EDGEGW001` 和子设备 `DRONE-SIM-001/DRONE-SIM-002`；单测输出 `Ran 26 tests`；专项输出 `device_binding events ok: count=3, sns=EDGEGW001,DRONE-SIM-001,DRONE-SIM-002`，`update_topo schema ok: method=update_topo, sub_devices=2`，`status_reply ok: method=update_topo, result=0`，网关和两个子设备 `memory binding ok`，两个子设备 `sub-device paired ok`，最终 `organization request test ok`。

### 3.3 device topology

官方要点：

- `update_topo` 是设备向云端上报的 status 消息，topic 为 `sys/product/{gateway_sn}/status`。
- 云端通过 `sys/product/{gateway_sn}/status_reply` 回复，method 仍为 `update_topo`，`data.result=0` 表示成功。
- `data` 必须包含网关字段：`domain`、`type`、`sub_type`、`device_secret`、`nonce`、`thing_version`、`sub_devices`。
- `sub_devices[]` 中每个子设备必须包含：`sn`、`domain`、`type`、`sub_type`、`index`、`device_secret`、`nonce`、`thing_version`。
- 子设备在线示例使用 `sub_devices` 非空数组；子设备离线示例使用 `sub_devices=[]`。

当前结论：

| 项目 | 状态 | 说明 |
|---|---|---|
| `update_topo` payload 构造 | 已通过 Ubuntu 专项复测 | `src/cloud_api/dock3_device.py` 统一构造和校验官方字段 |
| `sys/product/{sn}/status` 发布 | 已有实现 | `main.py` 中 `update_topo` 事件会走 `MQTTClient.publish_status()` |
| `status_reply` 处理 | 已有实现 | `MQTTClient.set_status_reply_handler()` 接入 `DeviceManager.handle_update_topo_reply()` |
| 在线子设备拓扑 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_device_test.py` 触发 1 个模拟子设备并验证 status payload |
| 离线子设备拓扑 | 已通过 Ubuntu 专项复测 | 专项脚本触发 `sub_devices=[]` 并验证 status payload |
| 无效拓扑拒绝 | 已通过 Ubuntu 专项复测 | 缺少官方必填字段时返回 `325001`，且不产生 status 上报 |

专项验收命令：

```bash
python tools/mqtt_dock3_device_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 device topology test ok (...)`，当前输出会包含 `sub_device_count`、`sub_device_sns`、`reconnect`、`unbind`、`accepted`、`status`、`events` 和 `ignored_total`。该脚本会给运行中的网关发送测试触发用 `services:update_topo`，然后验证网关发布的官方 `sys/product/{sn}/status:update_topo`，并模拟云端发送 `status_reply`。其中 services 入口只作为本地测试触发器，最终 Dock3 官方方向仍以 `status/status_reply` 为准。当前脚本已支持 `--extra-sub-device-json`，服务器已验证 `DRONE-SIM-001,DRONE-SIM-002` 多子设备上线、离线空拓扑、重连恢复、逐 SN `device_unbinding` 事件和解绑后空拓扑。

### 3.4 drc

官方要点：

- `drc_mode_enter`、`drc_mode_exit` 是 `services -> services_reply`。
- `drc/down`、`drc/up` 用于进入 DRC 后的控制数据。
- `drc_status_notify` 已废弃，更准确状态来自机场属性 `drc_state` 或 DRC 心跳。

当前结论：

| 项目 | 状态 | 说明 |
|---|---|---|
| `drc_mode_enter` | 已验证 | `services -> services_reply`，`result=0` |
| `drc_mode_exit` | 已验证 | `services -> services_reply`，`result=0` |
| `drc_state` | 已验证 | enter=2，exit=0 |
| 错误 topic | 已验证 | 误发 `drc_mode_enter` 到 `drc/down` 返回 `325001` |
| 控制数据 | 已通过 Ubuntu 复测 | `tools/mqtt_dock3_p0_batch_test.py` 已一次覆盖 `stick_control`、`drone_control`、`payload_control`、`drc_speed_push`、`drc_gimbal_speed_push`，均收到 `drc/up result=0` |
| `drc_mode_enter` 官方 payload | 已通过 Ubuntu 专项复测 | 支持 `mqtt_broker`、`osd_frequency`、`hsi_frequency` 字段校验；本地模拟仍允许 `{}` |
| `takeoff_to_point` / `fly_to_point_update` | 已通过 Ubuntu 专项复测 | 专项脚本优先通过 `property_get(latitude/longitude/height)` 生成电子围栏内目标点，服务器输出 `source=property_get`，`takeoff_to_point` 返回 `GUIDED目标已设置... simulated=false`，`fly_to_point_update` 返回 `simulated=false` |
| `stick_control` 官方杆量 | 已通过 Ubuntu 专项复测 | 支持 `roll/pitch/throttle/yaw/gimbal_pitch`，值域 `364..1684`，官方正常杆量按 Dock3 无回包机制验证；非法杆量返回 `325001` |
| `drone_control` 官方废弃字段 | 已通过 Ubuntu 专项复测 | 支持 `seq/x/y/h/w`，校验 `x/y=-17..17`、`h=-4..5`、`w=-90..90`；正常字段返回 `0`，越界字段返回 `325001` |
| DRC 批量闭环 | 已通过 Ubuntu 专项复测 | `mqtt_drc_batch_test.py --timeout 12 --quiet-stale` 一次覆盖 enter/exit、`drc_state`、`stick_control`、`drone_control`、`payload_control`、`drc_speed_push`、`drc_gimbal_speed_push`，结果 `accepted=10, ignored_total=0` |
| DRC 心跳 | 待迁移 | 需明确官方 `heart_beat` 与当前 `drc_heartbeat` 事件、`drc_state` 的关系和超时恢复 |

专项验收命令：

```bash
python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale
python tools/mqtt_drc_batch_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 drc test ok (...)` 和 `drc batch test ok (...)`。专项脚本验证官方 `drc_mode_enter` payload、enter/exit 后 `drc_state` 同步、官方 `stick_control` 无回包、非法杆量返回 `325001`、官方废弃 `drone_control` 字段和值域错误、云台速度基础回包、`takeoff_to_point`、`fly_to_point_update/stop` 和非法目标点拒绝；目标点默认优先来自网关 `property_get(latitude/longitude/height)`，避免旧 OSD 样本导致电子围栏误判。批量脚本验证 DRC 主链路在共享 MQTT 客户端下无错 topic、无旧格式干扰并可连续 enter/control/exit。

### 3.5 device

官方要点：

- 设备拓扑更新使用 `sys/product/{gateway_sn}/status`，method 为 `update_topo`。

当前结论：

| 项目 | 状态 | 说明 |
|---|---|---|
| `update_topo` 注册 | 已有实现 | `src/main.py` 已注册，`src/device/manager.py` 有 handler |
| `status_reply` 处理 | 已有实现 | MQTTClient 已订阅 `sys/product/{sn}/status_reply` |
| 云端 status 模拟器 | 已通过 Ubuntu 复测 | `tools/mqtt_cloud_simulator.py --enable-status` 已订阅 `sys/product/{sn}/status` 并发布 `status_reply` |
| 绑定后拓扑上报验收 | 已通过 Ubuntu 复测 | `tools/mqtt_organization_test.py --publish-events --expect-status-reply` 已把绑定后产生的 `update_topo` 发布到 status topic 并收到确认 |
| 多子设备拓扑测试 | 已通过 Ubuntu 复测 | `--sub-device-json` 注入 `DRONE-SIM-001`，`--expect-topo-sub-device-count` 与 SN 校验通过 |
| Dock3 拓扑字段 | 待迁移 | 需要对齐 `domain`、设备 namespace、设备类型枚举、子设备结构、拓扑更新触发时机 |

### 3.6 cmd / 远程调试

官方要点：

- CMD/远程调试 method 通过 `thing/product/{gateway_sn}/services` 下发，通过 `services_reply` 返回。
- 常规命令回包体使用 `data.result` 和 `data.output.status`。
- 舱盖、充电、推杆、飞行器开关机、格式化、重启等任务类命令需要上报同 method 的进度事件，事件中包含 `output.status` 和 `output.progress`。
- 枚举开关类 method 需要校验官方字段和值域，例如 `action`、`link_workmode`。

当前结论：

| 项目 | 状态 | 说明 |
|---|---|---|
| `debug_mode_open/debug_mode_close` | 已通过 Ubuntu 专项复测 | 开启后 `mode_code=2`，关闭后 `mode_code=0`，回包 `output.status=ok` |
| `cover_open/cover_close/charge_open/charge_close/putter_open/putter_close` | 已通过 Ubuntu 专项复测 | 保留 MAVLink 机巢命令发送；本地已补 pending 目标状态保护，服务器重跑已验证 `cover_close` 后 `cover_state=0`；回包和事件均带 `output.status` |
| `cover_force_close` | 已通过 Ubuntu 专项复测 | 飞行器仍在舱内时返回 `314000` 和 `output.status=rejected` |
| `supplement_light_open/close` | 已通过 Ubuntu 专项复测 | 联动 `supplement_light_state` |
| `battery_maintenance_switch` | 已通过 Ubuntu 专项复测 | 校验 `action=0/1`，非法值返回 `325001` |
| `air_conditioner_mode_switch` | 已通过 Ubuntu 专项复测 | 校验 `action=0/1/2/3`，联动 `air_conditioner_mode` 和 `air_conditioner.air_conditioner_state` |
| `alarm_state_switch` | 已通过 Ubuntu 专项复测 | 校验 `action=0/1`，联动 `alarm_state` |
| `battery_store_mode_switch` | 已通过 Ubuntu 专项复测 | 校验 `action=1/2`，联动 `battery_store_mode` |
| `sdr_workmode_switch` | 已通过 Ubuntu 专项复测 | 支持官方 `link_workmode`，兼容旧 `mode`，联动 `wireless_link.link_workmode` |
| `drone_open/drone_close` | 已通过 Ubuntu 专项复测 | 按飞行器电源/在线状态处理，不再把开机误写成“飞出机舱”；触发 `update_topo` |
| `device_format/format_sd_card` | 已通过 Ubuntu 专项复测 | 默认模拟，不删除服务器媒体目录；仅当配置 `device.allow_storage_format=true` 时才允许真实清理 `media.storage_path` |
| `device_reboot` | 已通过 Ubuntu 专项复测 | 默认模拟，不执行真实 `sudo reboot`；仅当配置 `device.allow_system_reboot=true` 时才允许真实重启 |
| 专项脚本 | 已新增 | `tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale` |

专项验收命令：

```bash
python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 cmd test ok (...)`。2026-07-09 重跑通过，实际输出 `accepted=60, events=18, ignored_mismatched=0, ignored_legacy=14, ignored_late=46, ignored_wrong_topic=0, ignored_total=60`；E2E cmd 单段输出 `dock3 e2e full-chain test ok (steps=1, elapsed=5.0s)`。该脚本一次覆盖调试模式、补光灯、电池保养、空调、报警、电池模式、SDR、舱盖、充电、推杆、飞行器开关机、格式化、重启、重置和错误分支。`ignored_legacy/ignored_late` 表示 broker 环境中仍存在旧格式或晚到重复回包源，当前脚本已按 Dock3 格式继续等待正确回包；清理旧进程或 broker retained/旧规则后，可追加 `--fail-on-stale` 做严格验证。

### 3.7 wayline

官方要点：

- 航线核心服务包括 `flighttask_prepare`、`flighttask_execute`、`flighttask_pause`、`flighttask_recovery`、`flighttask_undo`、`return_home`、`return_home_cancel` 等。
- 事件包括 `flighttask_ready`、`flighttask_progress`、`return_home_info` 等。

当前结论：

| 项目 | 状态 | 说明 |
|---|---|---|
| method 注册 | 部分完成 | 多数 Dock3 名称已注册 |
| 旧方法兼容 | 存在 | `execute_task`、`load_wayline`、`flyto` 等旧/自定义入口仍存在 |
| 字段合规 | P1 已验证 | `flight_id`、航线文件对象、执行时间、返航信息、任务进度字段已完成 P1 模拟链路对照；KMZ 内容、媒体计数和真实飞控执行字段后续精校 |
| SITL 执行 | 部分可测 | 纯模拟 `simulate_mission.is_enable=1` 不触发真实飞控模式切换；真实 SITL 航点执行仍需人员2继续核对飞控模式、任务状态机和非阻塞执行 |
| P1 services 回包 | 已通过 Ubuntu 专项复测 | `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel` 已统一 `data.output.status/progress/ext`；`flighttask_execute` 已在纯模拟任务下跳过真实 `AUTO` 切换 |
| P1 事件 | 已通过 Ubuntu 专项复测 | 状态变化会产生 `flighttask_progress`，返航会产生 `return_home_info` |
| P1 错误码 | 已通过 Ubuntu 专项复测 | 参数错误返回 `325001`，状态不支持返回官方非负码并带 `output.status=rejected` |

专项验收命令：

```bash
python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 wayline test ok (...)`。2026-07-06 Ubuntu 已验证成功，实际输出 `dock3 wayline test ok (accepted=11, events=8, ignored_mismatched=0, ignored_legacy=11, ignored_late=0, ignored_total=11)`。该脚本一次覆盖 `flighttask_prepare`、`flighttask_execute`、`flighttask_progress`、`flighttask_pause`、`flighttask_resume`、`flighttask_return`、`return_home_cancel`、`flighttask_undo`、参数错误和状态不支持错误分支。当前为纯模拟优先：`simulate_mission.is_enable=1` 时不会强制真实飞控模式切换，也不会触发真实返航、降落或取消返航悬停。

## 4. 下一组执行顺序

### 4.1 按客户优先级重排后的下一步

下面这张表是后续开发真实顺序，优先级来自 `项目优先级整理.docx` 的模块级“高/中/低”。下方历史 Batch 表只保留已经完成过的迁移记录，不再作为下一步排序依据。

| 顺序 | 客户优先级 | 目标 | 交付物 | 验收 |
|---:|---|---|---|---|
| 1 | 高 | 机巢物理控制联动、机巢状态真实同步、机巢告警真实获取 | 机巢状态到 Dock3 state/HMS/dock_operation 已完成本地桥接；`flighttask_execute` 已触发机巢 `request_takeoff` 安全调用；后续补机巢 v1.3 网关联调、CMD 进阶失败码、拒绝原因和状态机 | `tools/nest_v13_protocol_test.py --verbose` + `tools/e2e_takeoff_flow_test.py` + 网关/机巢模拟器命令链路日志 |
| 2 | 高 | 航线真实上传、KMZ/WPML、MAVLink Mission、真实任务进度 | 一键启航离线闭环已补，真实 SITL 航点执行、距离/高度进度计算、FlyTo/takeoff_to_point 真实进度仍待推进 | Wayline + SITL E2E，不能只依赖 mock 回包 |
| 3 | 高 | DRC 速度、航向、高度控制优化 | type_mask 动态计算、LOCAL_NED 坐标系、CONDITION_YAW 航向控制、高度控制专项 | 扩展 DRC 专项 + SITL 行为证据 |
| 4 | 高 | OTA 真实下载、校验、刷写、备份、回滚和飞机 OTA 透传 | firmware/config handler、后台任务、进度事件、失败码、配置持久化/回滚 | 新增 `tools/mqtt_dock3_firmware_config_test.py` |
| 5 | 高 | OSD 字段补全 | wireless_link、maintain_status、电池/GPS/云台缺口字段、字段单位和枚举矩阵 | 扩展 OSD/state 专项和字段矩阵 |
| 6 | 高 | 真实直播推流、真实文件列表、相机变焦/对焦 | 文件系统扫描、真实推流 adapter、相机/云台 adapter 或明确硬件不可用失败码 | media/live/payload 专项 |
| 7 | 中 | 设备重启/格式化/重置、HMS 真实触发、机巢环境、多设备 OSD、NTRIP | 运维命令状态、真实告警源、环境字段转换、多设备 OSD、RTCM 注入 | CMD/HMS/OSD/RTK 专项 |

### 4.2 已完成迁移批次记录

| 批次 | 目标 | 交付物 | 验收 |
|---|---|---|---|
| Batch 1 | properties 官方可写边界 | `src/cloud_api/dock3_properties.py`、`tools/mqtt_dock3_properties_test.py`、`docs/INTERFACE_DESIGN_SPEC.md` | 已通过 Ubuntu 跑 `python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale`；后续进入客户需求相关 `osd/state` 字段精校 |
| Batch 2 | organization requests 模拟器 | `airport_bind_status`、`airport_organization_get`、`airport_organization_bind` 基础成功路径、异常分支、绑定后事件和持久化已通过 Ubuntu 验证 | 下一步字段精校、真实云端差异和多设备场景 |
| Batch 2.1 | organization get/bind 工具 | `tools/mqtt_organization_test.py` 已通过 Ubuntu 复测成功路径、组织查询失败、绑定 `err_infos`、请求超时 | 已完成 |
| Batch 2.2 | organization 绑定后状态 | 已通过 Ubuntu 复测 `device_binding/update_topo`、内存状态、SQLite 持久化 | 已完成 |
| Batch 2.3 | organization 多子设备绑定 | `DeviceManager` 已支持子设备绑定缓存、`paired` 状态、`device_binding` 事件和 `update_topo` 联动；`tools/mqtt_organization_test.py` 已支持 `--bind-sub-devices`、绑定事件 SN 校验和多 SN 校验 | 已通过本地单测和 Ubuntu MQTT 复测 |
| Batch 3 | drc 控制数据 | `stick_control/drone_control/payload_control/drc_speed_push/drc_gimbal_speed_push` 已通过 P0 总批量验证；官方 payload 校验、`tools/mqtt_dock3_drc_test.py` 和 `tools/mqtt_drc_batch_test.py --timeout 12 --quiet-stale` 均已通过 Ubuntu 专项复测 | 已完成本轮专项；下一步精校 DRC 心跳、`takeoff_to_point` 和超时恢复 |
| Batch 4 | P0 总批量验收 | `tools/mqtt_dock3_p0_batch_test.py --timeout 12` 已通过 Ubuntu 复测；`airport_bind_status` services 兼容项默认跳过 | 下一步清理旧回包来源后用 `--fail-on-stale` 做严格环境验证 |
| Batch 4.1 | device topology | `update_topo` 完整 topic/payload/status_reply；严格字段校验和专项脚本已通过 Ubuntu 复测 | 已完成本轮专项；后续继续精校官方设备类型枚举 |
| Batch 4.2 | cmd/远程调试 | `debug_mode_*`、`cover_*`、`charge_*`、`putter_*`、`drone_*`、`device_*`、`*_switch` 已完成代码收敛；针对 `cover_close -> cover_state` 回读失败补了 pending 目标状态保护，服务器重跑已通过，最新输出 `accepted=60, events=18`，E2E cmd 单段 `steps=1, elapsed=5.0s` | P1-15 当前模拟验收已关闭；真实硬件重启/格式化/耗时仍按 adapter 精校风险跟踪 |
| Batch 5 | wayline | `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel` 基础闭环和 mock KMZ 解析已完成代码收敛并通过 Ubuntu 专项复测 | 已完成本轮专项；下一步进入 media/live 或 HMS，Wayline 后续补远端 KMZ 下载、媒体计数、真实 SITL 航点执行 |
| Batch 6 | P0/P1 E2E 回归 | `tools/e2e_full_chain_test.py --timeout 12 --quiet-stale` 已通过服务器 10 段复测，串联 properties、device、OSD/state、CMD、DRC、Wayline、media、live、HMS-log、firmware-config，全部 `exit=0`，总耗时 13.6s | 已完成 P0/P1 核心 MQTT 全链路基线；一键启航入口已接入并已通过 11 段服务器复测，后续补 `--fail-on-stale` 严格环境 |
| Batch 7 | media 基础 | `tools/mqtt_dock3_media_test.py --timeout 12 --quiet-stale` 已通过 Ubuntu 复测，输出 `accepted=6, events=2, ignored_total=0` | 已完成当前 media 基础专项；后续精校真实上传 adapter |
| Batch 8 | live 基础 | `src/cloud_api/dock3_live.py`、`tools/mqtt_dock3_live_test.py` 已通过 Ubuntu 专项，覆盖 mock 推流、画质、镜头、停止、能力查询、状态事件和非法参数拒绝 | 已完成本轮专项；后续接真实推流 adapter |
| Batch 9 | HMS/log 基础 | `tools/mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale` 已通过 Ubuntu 复测，输出 `dock3 hms/log test ok (accepted=8, events=5, ignored_total=7)`；覆盖 HMS 查询/清除、日志列表、上传模拟、取消、进度事件和非法参数拒绝 | 已完成本轮专项；后续精校真实 HMS 文案 key、日志压缩和真实上传 adapter |
| Batch 10 | 机巢状态/HMS/dock_operation 桥接 | 新增 `src/device/nest_hms_mapper.py`，`DeviceManager.update_from_nest()` 已同步机巢状态到 Dock3 state、HMS 和 `dock_operation`；`tools/nest_v13_protocol_test.py --verbose` 扩展到 `checks=5` | 本地通过 `tests.test_device_manager_nest_sync`、机巢属性/模块单测和 v1.3 离线专项；待 Ubuntu 网关 + 机巢模拟器实跑留证 |
| Batch 11 | firmware/config 基础闭环 | `DeviceManager` 已补 `ota_create/ota_cancel/firmware_upgrade`、`config_update/config_get/config_rollback/config`，新增 `tools/mqtt_dock3_firmware_config_test.py` 和 `tests/test_device_manager_firmware_config.py`；Ubuntu 输出 `dock3 firmware/config test ok (accepted=8, events=10, ignored_total=7)` | 已完成专项，且已接入并通过后续 10 段和 11 段全量 E2E |
| Batch 12 | 命令链日志和 E2E 残留兼容 | `src/utils/command_trace.py` 已补 Dock3 method 中文动作映射，`MQTTClient` 对 topic 不匹配和未注册 handler 写入 command_trace；`mqtt_dock3_osd_state_test.py` 已忽略旧版 `reason=property_changed` state 残留 | 本地通过 `tests.test_command_trace`、`tests.test_dock3_osd_state_client`、`tests.test_e2e_full_chain`、`tests.test_mqtt_test_client`，服务器 10 段 E2E 通过 |
| Batch 13 | OSD E2E 旧样本兼容 | 人员2反馈 10 段 E2E 在 OSD/state 阶段收到旧 helper 发布的部分 value-wrapper OSD，脚本新增 `is_ignorable_legacy_osd()`，只忽略 value-wrapper 且缺必填字段的旧样本；当前有效 Dock3 OSD 仍按必填字段严格校验 | 服务器重跑 `tools/e2e_full_chain_test.py --timeout 12 --quiet-stale` 已通过，OSD/state 阶段输出 `dock_osd=1, drone_osd=1, state=1, invalid=0` |
| Batch 14 | 10 段 E2E 服务器验收 | 人员2在服务器执行 `tools/e2e_full_chain_test.py --timeout 12 --quiet-stale`，properties、device、OSD/state、CMD、DRC、Wayline、media、live、HMS-log、firmware-config 全部 `exit=0`，最终输出 `dock3 e2e full-chain test ok (steps=10, elapsed=13.6s)` | P0/P1 核心 MQTT 全链路基线已成立；下一阶段优先推进一键启航、严格旧消息清理和真实 SITL/硬件联动 |
| Batch 15 | 一键启航离线闭环 | 新增 `tools/e2e_takeoff_flow_test.py`，覆盖 mock KMZ 解析、`flighttask_prepare -> flighttask_execute -> complete_simulated_mission`、`flighttask_ready/flighttask_progress` 事件和机巢 `request_takeoff` 调用；`e2e_full_chain_test.py` 默认扩展为 11 段 | 本地和服务器均已通过 `tools/e2e_takeoff_flow_test.py --timeout 12`，服务器 11 段 E2E 已覆盖 `takeoff-flow` |
| Batch 16 | 11 段 E2E 服务器验收 | 人员2在服务器执行 `tools/e2e_full_chain_test.py --timeout 12 --quiet-stale`，properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS-log、firmware-config 全部 `exit=0` | P0/P1 核心 MQTT + 一键启航离线闭环基线已成立；当次输出 `dock3 e2e full-chain test ok (steps=11, elapsed=16.2s)` |
| Batch 17 | 严格旧消息门禁 | `tools/mqtt_dock3_osd_state_test.py` 新增 `--fail-on-stale`，会把旧 value-wrapper OSD、旧 `reason=property_changed` state 和无关 state 计入 stale；`e2e_full_chain_test.py --fail-on-stale` 已会把严格参数传入 OSD/state 阶段 | 本地通过 `tests.test_dock3_osd_state_client`、`tests.test_e2e_full_chain`、`tests.test_mqtt_test_client` 和 dry-run；待服务器清理旧进程后执行全链路严格复测 |
| Batch 18 | device 多子设备/离线/重连 | `DeviceManager` 空拓扑改为标记已知子设备离线而不是清空缓存，重连保留 paired 状态，解绑时清空子设备并上报空拓扑；`tools/mqtt_dock3_device_test.py` 新增 `--extra-sub-device-json`，默认覆盖上线、离线、重连和非法拓扑；`e2e_full_chain_test.py` 新增 `--device-extra-sub-device-json` 透传到 device 阶段 | 本地单测通过；服务器执行多设备专项输出 `sub_device_count=2 ... reconnect=True ... exit=0`，E2E device 单段输出 `dock3 e2e full-chain test ok (steps=1, elapsed=1.4s)`，11 段全链路输出 `dock3 e2e full-chain test ok (steps=11, elapsed=18.9s)` |
| Batch 18.1 | device 解绑验收增强 | `unbind_device()` 已按网关和已知子设备逐 SN 发布 `device_unbinding`，清理绑定缓存，清空子设备并上报空 `update_topo`；`tools/mqtt_dock3_device_test.py` 默认新增解绑事件和空拓扑检查，可用 `--skip-unbind-check` 跳过 | 本地单测和 dry-run 通过；Ubuntu 专项已输出 `device_unbinding events ok: count=3` 和 `unbind=True` |
| Batch 19 | 数据保留时间戳修正 | 新增 `src/database/time_utils.py`，统一处理秒级历史字段和毫秒级 Dock3 事件字段；`retention_manager`、`telemetry_db`、`cloud_sync_manager` 已使用混合时间戳过期条件；新增 `tests/test_database_time_utils.py` | 本地通过 `python -m unittest tests.test_database_time_utils tests.test_dock3_cmd tests.test_mqtt_test_client tests.test_e2e_full_chain`；P1-16 仍需补真实 DB 清理/日志专项 |

## 5. 更新规则

- 每完成一个客户需求相关字段或 method，对应分组状态必须从“待迁移”更新为“部分完成”或“已验证”。
- 任何保留旧 services 兼容入口的 method，都必须标注“兼容 smoke，不作为 Dock3 最终验收”。
- 每个客户需求相关 method 必须记录：官方文档、topic、direction、request、reply/event、错误码、当前 handler、smoke 命令。客户未提及/未用到的 Dock3 method 可以在 method 台账中标为 Deferred。
- 不得把 `dock_status_query` 这种旧机场查询结果当作 Dock3 官方属性替代品。
- 不得在 MQTT 回调或 MAVLink 接收热路径中做阻塞 IO、下载、上传、数据库长事务或外部命令。
