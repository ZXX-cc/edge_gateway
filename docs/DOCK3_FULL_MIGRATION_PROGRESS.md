# DJI Dock 3 全量迁移进度台账

更新时间：2026-07-06

本文档用于记录 Edge Gateway 从当前“链路已通 + P0 局部适配”推进到“全量 DJI Dock 3 协议合规”的逐组迁移进度。后续每完成一个字段、method、topic、reply 或 smoke test，都必须更新本文档。

如果只需要快速查看当前哪些模块已完成、哪些未完成，看 `docs/DOCK3_MODULE_STATUS.md`。

当前结论：**Dock3 P0 总批量、properties 大类专项、organization 基础 requests 链路、device topology、DRC 大类专项、CMD/远程调试大类、Wayline/航线管理 P1 基础闭环均已通过 Ubuntu 模拟验证；Wayline 首次复测暴露的 `flighttask_execute` 纯模拟任务误触发 MAVLink `AUTO` 模式切换问题已修复并复测通过；全量迁移未完成。**

当前进度估算：**约 60%**。

估算口径：

| 范围 | 进度 | 说明 |
|---|---:|---|
| Ubuntu 纯模拟部署与基础链路 | 80% | MQTT、SITL、网关、OSD、基础 command/reply 已跑通，剩 systemd/一键化和环境固化 |
| Dock3 P0 协议链路 | 92% | property、services、requests、status、events、DRC 控制数据、CMD 控制命令、错误码边界已通过批量和专项验证，剩旧回包噪声清理和字段精校 |
| Dock3 全量 method/字段迁移 | 53% | properties 官方可写/只读边界、device topology、DRC 官方字段、organization 基础链路、CMD/远程调试大类、Wayline P1 基础闭环已通过专项验证；live/media/HMS/log/config 等仍待迁移 |
| 非阻塞、日志、容错基础 | 45% | 日志落盘、字段中文名、部分 async warning 已修复，NoSQL/MongoDB 和完整 request trace 未实现 |
| 模拟器和测试工具 | 81% | 云端模拟器、organization/status/topology/properties/DRC/CMD/Wayline/P0 批量测试工具可用，P0、DRC、CMD、Wayline 工具已支持旧回包统计并通过 Ubuntu 复测，媒体模拟器仍待补 |

综合按当前目标“服务器内跑通 Dock3 云端模拟链路，不接真实实体设备”估算为约 60%。该百分比不是验收承诺，后续每完成一个 Dock3 分组再调整。

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
| `update_topo` 多子设备拓扑 | 已通过 Ubuntu 复测 | 注入 `DRONE-SIM-001` 后发布 `update_topo tid=f318713c-767a-43d9-a4b2-13ade3a969b2`，`sub_devices[0].sn=DRONE-SIM-001`，收到 `status_reply result=0` |
| 错误码边界归一 | 通过本地测试 | `tests/test_dji_error_codes.py` 验证内部负数转 DJI 错误码 |
| properties 可写边界 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale` 输出 `dock3 properties test ok (accepted=10, ignored_total=10)`；官方 `rw` 属性成功，只读/非法/未知属性返回 `state.result=1` |
| device topology 专项 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_device_test.py --timeout 12` 输出 `dock3 device topology test ok (status_messages=2, sub_device_sn=DRONE-SIM-001, accepted=3, ignored_legacy=3)`；验证 `update_topo status/status_reply`、在线/离线拓扑、无效拓扑拒绝 |
| DRC 专项字段校验 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale` 输出 `dock3 drc test ok (accepted=9, ignored_mismatched=0, ignored_legacy=4, ignored_late=1, ignored_total=5)`；验证 `drc_mode_enter` 官方 payload、`stick_control` 官方杆量、`drone_control` 官方废弃字段和值域错误 |
| CMD/远程调试专项 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale` 输出 `dock3 cmd test ok (accepted=55, events=13, ignored_mismatched=0, ignored_legacy=55, ignored_late=0, ignored_total=55)`；验证调试模式、舱盖/充电/推杆、飞行器/设备动作、枚举开关、安全拒绝和任务进度事件 |
| Wayline/航线管理 P1 | 已通过 Ubuntu 专项复测 | `tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale` 输出 `dock3 wayline test ok (accepted=11, events=8, ignored_mismatched=0, ignored_legacy=11, ignored_late=0, ignored_total=11)`；覆盖 `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel`、参数错误和状态不支持错误分支；纯模拟任务不触发真实飞控 `AUTO/LOITER/返航/降落` 控制 |
| P0 总批量验证入口 | 已通过 Ubuntu 复测 | `tools/mqtt_dock3_p0_batch_test.py --timeout 12` 一次覆盖 `dock_status_query`、properties、DRC、错误 topic，输出 `dock3 p0 batch test ok`；`airport_bind_status` 官方链路单独走云端模拟器 |
| 旧格式重复回包 | 已识别，非阻塞 | P0 批量测试中可见同一 `tid` 先收到旧格式 `property/set_reply` 或旧 `data.result=-2` 回包，脚本已按 `tid + method + Dock3格式` 忽略并继续等待正确回包；后续清理 broker/旧进程后可用 `--fail-on-stale` 做严格验证 |

## 2. 全量分组进度

状态说明：

| 状态 | 含义 |
|---|---|
| 已验证 | 已按 Dock3 topic/reply/payload 在 Ubuntu 模拟环境通过 |
| 部分完成 | 已有代码或部分字段可用，但未完成官方字段/枚举/单位全量对照 |
| 待迁移 | 已有旧实现或占位，需要按 Dock3 改造 |
| 暂缓 | 当前纯模拟阶段不阻塞主链路，保留接口或占位 |

| 顺序 | Dock3 分组 | 官方文档 | 当前状态 | 当前代码入口 | 下一步 |
|---:|---|---|---|---|---|
| 1 | 设备属性 properties | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/properties.html | 部分完成，官方 `rw` 属性和值域边界已通过 Ubuntu 专项复测 | `src/device/manager.py`, `src/cloud_api/dock3_properties.py`, `src/mavlink/telemetry.py`, `src/cloud_api/telemetry_reporter.py`, `tools/mqtt_dock3_properties_test.py` | 继续补 `osd/state` 字段、枚举、单位和值域全量精校 |
| 2 | 设备管理 device | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/device.html | 部分完成，`update_topo` 官方字段校验和专项脚本已通过 Ubuntu 复测 | `src/device/manager.py`, `src/cloud_api/dock3_device.py`, `src/cloud_api/connection_manager.py`, `tools/mqtt_cloud_simulator.py`, `tools/mqtt_dock3_device_test.py`, `tools/mqtt_organization_test.py` | 继续精校 `domain/type/sub_type` 官方枚举和多设备真实拓扑语义 |
| 3 | 组织管理 organization | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/organization.html | 部分完成，基础成功路径、异常分支、绑定后事件和持久化均已通过 Ubuntu 验证 | `src/cloud_api/connection_manager.py`, `src/device/manager.py`, `tools/mqtt_cloud_simulator.py`, `tools/mqtt_organization_test.py` | 字段精校，补真实云端/多设备差异 |
| 4 | 直播 live | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/live.html | 待迁移 | `src/media/manager.py` | 建直播 method 表，先模拟 `live_start/live_stop/quality/lens` |
| 5 | 媒体 media | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/media.html | 待迁移 | `src/media/manager.py`, `src/payload/camera.py` | 对齐文件列表、删除、上传优先级和媒体上传事件 |
| 6 | 航线 wayline | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/wayline.html | P1 基础闭环已通过 Ubuntu 专项复测；基础 flighttask services/reply 和进度事件已收敛；纯模拟任务不强制真实飞控 `AUTO/LOITER/返航/降落` 控制 | `src/mission/manager.py`, `src/cloud_api/dock3_wayline.py`, `src/modules/waypoint_manager.py`, `tools/mqtt_dock3_wayline_test.py`, `tests/test_dock3_wayline.py` | 后续继续 KMZ 下载解析、媒体计数、真实 SITL 航点执行、返航事件字段精校 |
| 7 | HMS | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/hms.html | 待迁移 | `src/device/manager.py`, `src/database/alarm_db.py` | 明确 HMS 文案 key、错误码、告警上报结构 |
| 8 | 远程调试 cmd | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/cmd.html | 已通过 Ubuntu 专项复测，代码已按 `output.status`、枚举校验、进度事件和模拟安全重启收敛 | `src/device/manager.py`, `src/cloud_api/dock3_cmd.py`, `tools/mqtt_dock3_cmd_test.py`, `tests/test_dock3_cmd.py` | 后续精校真实 Dock3 任务耗时、失败码、设备重启/格式化真实执行开关 |
| 9 | 固件升级 firmware | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/firmware.html | 待迁移 | `src/device/manager.py` | 保持后台化，补升级任务状态、失败码、进度事件 |
| 10 | 远程日志 log | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/log.html | 待迁移 | `src/modules/log_manager.py`, `src/database/operation_db.py` | 对齐日志列表、日志上传、压缩超时和错误码 |
| 11 | 配置更新 config | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/config.html | 待迁移 | `src/device/manager.py`, `src/cloud_api/connection_manager.py` | 明确 requests/config 与 services/config_update 的边界 |
| 12 | 指令飞行 drc | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/drc.html | 部分完成，控制数据、P0 总批量和 DRC 官方字段专项均已通过 Ubuntu 复测 | `src/drc/controller.py`, `src/cloud_api/dock3_drc.py`, `src/mission/manager.py`, `src/modules/flight_control.py`, `tools/mqtt_drc_batch_test.py`, `tools/mqtt_dock3_drc_test.py`, `tools/mqtt_dock3_p0_batch_test.py` | 继续精校 DRC 心跳、`takeoff_to_point`、超时恢复和 MAVLink 模式语义 |
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
| OSD 遥测 | 部分完成 | 经度、纬度、高度、电池、姿态、GPS 已上报，字段仍需逐项对照官方属性表 |
| Dock 状态字段 | 待迁移 | `cover_state`、`putter_state`、`charging_state` 等仍是当前模拟枚举，未完成 Dock3 枚举精校 |

#### 3.1.1 Batch 1 字段对照结果

| 字段 | 官方 Dock3 名称 | 官方访问 | pushMode | 当前项目状态 | 下一步 |
|---|---|---|---|---|---|
| `air_transfer_enable` | 空中回传 | `rw` | `1` | 已通过 Ubuntu 专项复测：`property/set` 返回 `state.result=0`，`property_get` 可读回 | 后续补 state/osd 全量字段精校 |
| `silent_mode` | 机场静音模式 | `rw` | `1` | 已通过 Ubuntu 专项复测，可设置并读回 | 后续补 state/osd 全量字段精校 |
| `user_experience_improvement` | 用户体验改善计划 | `rw` | `1` | 已通过 Ubuntu 专项复测：`property/set` 返回 `state.result=0`，`property_get` 可读回 | 后续补 state/osd 全量字段精校 |
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
python tools/mqtt_dock3_device_test.py --timeout 12
```

成功标志：`dock3 device topology test ok (status_messages=2, sub_device_sn=DRONE-SIM-001, accepted=3, ignored_legacy=3)`。该脚本会给运行中的网关发送测试触发用 `services:update_topo`，然后验证网关发布的官方 `sys/product/{sn}/status:update_topo`，并模拟云端发送 `status_reply`。其中 services 入口只作为本地测试触发器，最终 Dock3 官方方向仍以 `status/status_reply` 为准。

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
| `stick_control` 官方杆量 | 已通过 Ubuntu 专项复测 | 支持 `roll/pitch/throttle/yaw/gimbal_pitch`，值域 `364..1684`，官方正常杆量按 Dock3 无回包机制验证；非法杆量返回 `325001` |
| `drone_control` 官方废弃字段 | 已通过 Ubuntu 专项复测 | 支持 `seq/x/y/h/w`，校验 `x/y=-17..17`、`h=-4..5`、`w=-90..90`；正常字段返回 `0`，越界字段返回 `325001` |
| DRC 心跳 | 待迁移 | 需明确官方 `heart_beat` 与当前 `drc_heartbeat` 事件、`drc_state` 的关系和超时恢复 |

专项验收命令：

```bash
python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 drc test ok (...)`。该脚本会验证官方 `drc_mode_enter` payload、enter/exit 后 `drc_state` 同步、官方 `stick_control` 无回包、非法杆量返回 `325001`、官方废弃 `drone_control` 字段和值域错误、云台速度基础回包。

### 3.4 device

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

### 3.5 cmd / 远程调试

官方要点：

- CMD/远程调试 method 通过 `thing/product/{gateway_sn}/services` 下发，通过 `services_reply` 返回。
- 常规命令回包体使用 `data.result` 和 `data.output.status`。
- 舱盖、充电、推杆、飞行器开关机、格式化、重启等任务类命令需要上报同 method 的进度事件，事件中包含 `output.status` 和 `output.progress`。
- 枚举开关类 method 需要校验官方字段和值域，例如 `action`、`link_workmode`。

当前结论：

| 项目 | 状态 | 说明 |
|---|---|---|
| `debug_mode_open/debug_mode_close` | 已通过 Ubuntu 专项复测 | 开启后 `mode_code=2`，关闭后 `mode_code=0`，回包 `output.status=ok` |
| `cover_open/cover_close/charge_open/charge_close/putter_open/putter_close` | 已通过 Ubuntu 专项复测 | 保留 MAVLink 机巢命令发送；无机巢模块时默认模拟更新状态；回包和事件均带 `output.status` |
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

成功标志：`dock3 cmd test ok (...)`。2026-07-06 已通过，实际输出 `accepted=55, events=13, ignored_mismatched=0, ignored_legacy=55, ignored_late=0, ignored_total=55`。该脚本一次覆盖调试模式、补光灯、电池保养、空调、报警、电池模式、SDR、舱盖、充电、推杆、飞行器开关机、格式化、重启、重置和错误分支。`ignored_legacy=55` 表示 broker 环境中仍存在旧格式重复回包源，当前脚本已按 Dock3 格式继续等待正确回包；清理旧进程或 broker retained/旧规则后，可追加 `--fail-on-stale` 做严格验证。

### 3.6 wayline

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

| 批次 | 目标 | 交付物 | 验收 |
|---|---|---|---|
| Batch 1 | properties 官方可写边界 | `src/cloud_api/dock3_properties.py`、`tools/mqtt_dock3_properties_test.py`、`docs/INTERFACE_DESIGN_SPEC.md` | 已通过 Ubuntu 跑 `python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale`；后续进入 `osd/state` 全量字段精校 |
| Batch 2 | organization requests 模拟器 | `airport_bind_status`、`airport_organization_get`、`airport_organization_bind` 基础成功路径、异常分支、绑定后事件和持久化已通过 Ubuntu 验证 | 下一步字段精校、真实云端差异和多设备场景 |
| Batch 2.1 | organization get/bind 工具 | `tools/mqtt_organization_test.py` 已通过 Ubuntu 复测成功路径、组织查询失败、绑定 `err_infos`、请求超时 | 已完成 |
| Batch 2.2 | organization 绑定后状态 | 已通过 Ubuntu 复测 `device_binding/update_topo`、内存状态、SQLite 持久化 | 已完成 |
| Batch 3 | drc 控制数据 | `stick_control/drone_control/payload_control/drc_speed_push/drc_gimbal_speed_push` 已通过 P0 总批量验证；官方 payload 校验和 `tools/mqtt_dock3_drc_test.py` 已通过 Ubuntu 专项复测 | 已完成本轮专项；下一步精校 DRC 心跳、`takeoff_to_point` 和超时恢复 |
| Batch 4 | P0 总批量验收 | `tools/mqtt_dock3_p0_batch_test.py --timeout 12` 已通过 Ubuntu 复测；`airport_bind_status` services 兼容项默认跳过 | 下一步清理旧回包来源后用 `--fail-on-stale` 做严格环境验证 |
| Batch 4.1 | device topology | `update_topo` 完整 topic/payload/status_reply；严格字段校验和专项脚本已通过 Ubuntu 复测 | 已完成本轮专项；后续继续精校官方设备类型枚举 |
| Batch 4.2 | cmd/远程调试 | `debug_mode_*`、`cover_*`、`charge_*`、`putter_*`、`drone_*`、`device_*`、`*_switch` 已完成代码收敛并通过 Ubuntu 专项复测 | 已完成本轮专项；后续清理旧回包源后用 `--fail-on-stale` 做严格环境验证 |
| Batch 5 | wayline | `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel` 基础闭环已完成代码收敛并通过 Ubuntu 专项复测 | 已完成本轮专项；下一步进入 media/live 或 HMS，Wayline 后续补 KMZ 下载解析、媒体计数、真实 SITL 航点执行 |

## 5. 更新规则

- 每完成一个字段或 method，对应分组状态必须从“待迁移”更新为“部分完成”或“已验证”。
- 任何保留旧 services 兼容入口的 method，都必须标注“兼容 smoke，不作为 Dock3 最终验收”。
- 每个 Dock3 method 必须记录：官方文档、topic、direction、request、reply/event、错误码、当前 handler、smoke 命令。
- 不得把 `dock_status_query` 这种旧机场查询结果当作 Dock3 官方属性替代品。
- 不得在 MQTT 回调或 MAVLink 接收热路径中做阻塞 IO、下载、上传、数据库长事务或外部命令。
