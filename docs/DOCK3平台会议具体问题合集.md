# DOCK3 平台会议具体问题合集

更新时间：2026-07-30

用途：把平台可能直接问出来的问题，整理成能在会上直接回答的口径。本文只看当前代码实际行为，不按历史项目文档判断。

相关备份：`docs/当前代码与DOCK3上云API对照.备份-20260730.md`

## 1. 无人机上报消息里没有 `height_limit`、`distance_limit` 吗

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 有代码补这两个字段，但它们是在“飞机 OSD”里，不是在“机场 state”里。 |
| 上报主题 | `thing/product/{飞机SN}/osd` |
| 谁触发 | 飞控遥测回调触发，网关主动上报 |
| 什么时候发 | 飞机子设备在线时，遥测线程定时上报飞机 OSD |
| 当前字段来源 | 优先取遥测里的 `height_limit/max_height/flight_height_limit` 和 `distance_limit/max_distance/flight_distance_limit`；取不到就用配置 `device.limits.max_height/max_distance` |
| 当前配置值 | `max_height=500`，`max_distance=50000` |
| 关键限制 | 如果没有在线子设备，代码只保存遥测，不往飞机 SN 的 OSD 主题发 |

代码链路：

```text
飞控遥测更新
→ TelemetryProcessor 组装 osd_data
→ main.py::_apply_aircraft_limit_fields()
→ 给 osd_data 补 height_limit / distance_limit
→ 如果子设备 online
→ TelemetryReporter.report_osd(飞机SN, osd_data)
→ thing/product/{飞机SN}/osd
```

代码位置：

| 代码 | 说明 |
|---|---|
| `src/main.py::_apply_aircraft_limit_fields()` | 给飞机 OSD 补 `height_limit/distance_limit` |
| `src/main.py::osd_cb()` | 子设备在线才发布飞机 OSD |
| `web/mavlink/telemetry/mapping.py::build_aircraft_osd()` | Web/MAVLink 侧也会把限高限远放进飞机 OSD |
| `config/gateway.yaml::device.limits` | 默认限高限远配置 |

需要和平台确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台是在看飞机 SN 的 `osd`，还是看机场 SN 的 `state` | 当前限高限远不会出现在机场 state |
| 平台字段名是否就是 `height_limit`、`distance_limit` | 当前代码按这两个字段名发 |
| 没有在线飞机时，平台是否也要求机场代发这两个值 | 当前不会代发到飞机 OSD |

会议建议回答：

> 当前不是没有限高限远字段。代码会在飞机 OSD 里带 `height_limit` 和 `distance_limit`，来源是飞控遥测，取不到时用配置里的 `max_height/max_distance`。但前提是子设备在线；如果平台看的是机场 `state`，那确实看不到这两个字段。

## 2. 机场的 `state` 主题下，直播相关信息没有推送吗

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | `live_status` 会推，`live_capacity` 当前不会主动推。 |
| `live_status` 主题 | `thing/product/{机场SN}/state` |
| `live_capacity` 主题 | 按上云属性表属于 `state`，但当前代码没有主动推 |
| 谁触发 `live_status` | 平台下发直播开始/停止/切镜头/改画质后，网关主动推 |
| 启动是否主动推直播信息 | 当前不主动推 `live_status`，也不主动推 `live_capacity` |

当前 `live_status` 触发：

| 平台 method | 是否推 `state.property.live_status` |
|---|---|
| `live_start_push` | 会 |
| `live_stop_push` | 会 |
| `live_lens_change` | 会 |
| `live_set_quality` | 会 |
| `live_camera_change` | 当前不会，只回包 |
| `live_capability_query` | 当前不会，只回能力查询结果 |

`live_status` 当前 state 内容：

```json
{
  "property": {
    "live_status": [
      {
        "video_id": "...",
        "video_type": "gimbal",
        "video_quality": 0,
        "status": 1,
        "error_status": 0
      }
    ]
  }
}
```

代码链路：

```text
平台发 live_start_push / live_stop_push / live_lens_change / live_set_quality
→ src/media/manager.py 对应 handle 方法
→ _publish_live_status_event()
→ 发 events.live_status_notify
→ 同时发 state.property.live_status
```

`live_capacity` 当前情况：

| 问题 | 当前答案 |
|---|---|
| 代码能不能生成 `live_capacity` | 能 |
| 平台能不能查到 | 能，通过 `live_capability_query` 或 `property_get` |
| 网关启动后会不会主动 state 推 | 当前不会 |
| 能力变化时会不会主动 state 推 | 当前没有看到主动推 |

代码位置：

| 代码 | 说明 |
|---|---|
| `src/media/manager.py::_publish_live_status_event()` | 负责推 `live_status` |
| `src/cloud_api/dock3_live.py::live_capability_data()` | 查询直播能力时返回 `live_capacity` |
| `src/device/manager.py::_refresh_payload_properties(notify=False)` | 构造 `live_capacity`，但 `notify=False`，所以不主动 state |

需要和平台确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台是否要求设备上线后主动推 `live_capacity` | 当前没做，如果平台要求就要补 |
| 平台判断直播状态主要看 `state.live_status`，还是 `events.live_status_notify` | 当前两边都有，但触发场景不同 |
| `live_camera_change` 后是否也要求刷新 `live_status` | 当前代码不刷新 |

会议建议回答：

> 当前 `state` 里不是完全没有直播信息。直播开始、停止、切镜头、改画质后会推 `live_status`。但直播能力 `live_capacity` 当前只支持查询，没有在上线时主动推。如果平台要求上线就能在 state 看到 `live_capacity`，这项我们需要补。

## 3. 组织绑定完成之后才会执行其他指令吗

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 大多数业务指令是绑定后才允许执行，但不是所有消息都要等绑定完成。 |
| 谁控制 | `CommandAdmissionEngine` 全局准入层 |
| 未绑定时允许 | 查询类、绑定类、拓扑同步、`property_set` |
| 未绑定时拦截 | 航线、DRC、机巢控制、直播/媒体等非查询业务指令 |
| 返回方式 | 如果平台下发命令且 `need_reply=1`，网关会回拒绝，原因是 `device_unbound` |

未绑定时明确允许的类型：

| 类型 | method 例子 |
|---|---|
| 查询类 | `property_get`、`gateway_state_query`、`hms_get`、`device_capability_query`、`workspace_bind_status`、`workspace_get`、`airport_bind_status`、`file_list_get`、`live_capability_query` |
| 绑定/拓扑类 | `device_binding`、`device_unbinding`、`update_topo`、`airport_bind_status`、`workspace_bind_status`、`workspace_get` |
| 属性设置 | `property_set` |

未绑定时会被拦截的典型指令：

| 类型 | method 例子 | 当前结果 |
|---|---|---|
| 航线任务 | `flighttask_prepare`、`flighttask_execute` | 不进业务 handler，直接拒绝 |
| DRC | `drc_mode_enter`、`stick_control`、`takeoff_to_point` | 不进业务 handler，直接拒绝 |
| 机巢控制 | `cover_open`、`charge_open`、`drone_open` | 不进业务 handler，直接拒绝 |
| OTA/配置危险项 | `ota_create`、`device_reboot`、`device_format` | 不进业务 handler，直接拒绝 |

代码链路：

```text
平台下发 services / property/set / drc/down
→ MQTTClient._on_message()
→ CommandAdmissionEngine.check()
→ 如果设备状态是 unbound 且不是允许列表
→ 返回 device_unbound
→ 不进入具体业务方法
```

代码位置：

| 代码 | 说明 |
|---|---|
| `src/main.py` 初始化 `CommandAdmissionEngine` 并注入 MQTT |
| `src/mqtt/client.py` 收到下行命令后先跑准入层 |
| `src/state_machine/command_admission.py` 定义未绑定时允许/拒绝规则 |
| `src/device/manager.py` 组织绑定成功后更新本地绑定状态并上报 `update_topo` |

需要和平台确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台是否希望未绑定时也允许直播/媒体查询之外的部分动作 | 当前默认拦截非查询业务指令 |
| 平台的绑定流程是平台预先绑定，还是网关启动后主动组织绑定 | 当前配置支持启动主动绑定 |
| 绑定成功后平台是否必须先收到 `update_topo` 再下发业务指令 | 当前绑定成功后会发 `update_topo`，但平台是否要等 status_reply 要确认 |

会议建议回答：

> 当前不是所有消息都等组织绑定完成。查询、绑定、拓扑、属性设置可以进。航线、DRC、机巢控制这类真正会动作的指令，在设备未绑定时会被准入层拦掉，避免未绑定设备被平台直接控制。

## 4. 绑定完成后，会不会马上上报拓扑

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 会。组织绑定成功后会更新本地绑定状态，然后主动发 `update_topo`。 |
| 上报主题 | `sys/product/{机场SN}/status` |
| method | `update_topo` |
| 谁触发 | 网关主动 |
| 什么时候触发 | 组织绑定成功、绑定状态查询发现变化、飞机上下电、平台同步拓扑后 |

链路：

```text
网关发 airport_organization_bind 到 requests
→ 平台回 requests_reply
→ DeviceManager 处理绑定结果
→ 写本地 organization/device_bindings
→ 发 events.device_binding
→ report_update_topo()
→ sys/product/{机场SN}/status, method=update_topo
```

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台是否必须先回 `status_reply result=0` 后才允许下发航线/DRC | 当前代码发了 `update_topo`，但业务准入主要看本地绑定状态，不等平台 status_reply |
| `device_secret/nonce/type/sub_type` 真实值由谁给 | 当前配置里有空值/模拟值风险 |

## 5. 平台问：为什么 `sub_device` 没在 state 里

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 当前属性表把 `sub_device` 归在 OSD，不归 state。 |
| 当前上报主题 | `thing/product/{机场SN}/osd` |
| 什么时候变化 | `drone_open/drone_close`、绑定状态变化、拓扑变化 |
| 当前代码是否曾尝试推 state | 有些内部调用会调用 `_notify_state({'property': {'sub_device': ...}})`，但 `_notify_state()` 会按 pushMode 过滤掉非 state 属性 |

代码链路：

```text
子设备状态变化
→ DeviceManager.update_properties / _refresh_sub_device_property
→ _notify_state() 做 pushMode 过滤
→ sub_device 是 pushMode=0
→ 不进 state，只进 OSD
```

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台是否按 DOCK3 属性表接受 `sub_device` 在 OSD | 当前代码按这个逻辑 |
| 平台是否额外要求子设备变化时也发 state | 如果要求，需要改属性分流或加专门事件 |

## 6. 平台问：网关启动后为什么没立刻看到 `update_topo`

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | `update_topo` 不是无条件启动必发，当前要看绑定状态。 |
| 启动会先做什么 | 上报 `device_online`、启动 heartbeat，然后走组织绑定/绑定状态查询 |
| 什么时候启动会发拓扑 | 本地已绑定、自动绑定成功、组织绑定成功、绑定状态查询确认绑定 |
| 什么时候不会马上发 | 未绑定、组织绑定配置缺字段、平台未回绑定结果、MQTT 未连上 |

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台希望“MQTT 一连上就发空拓扑”，还是“绑定后再发拓扑” | 当前是绑定后逻辑 |
| 未绑定状态下平台页面是否也需要显示设备在线 | 当前有 `device_online/heartbeat`，但平台可能只认 `update_topo` |

## 7. 平台问：`drc_mode_enter` 为什么不能发到 `drc/down`

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | `drc_mode_enter` 是“申请进入远程控制模式”，当前代码要求它走 `services`。 |
| 正确 topic | `thing/product/{机场SN}/services` |
| 错误 topic | `thing/product/{机场SN}/drc/down` |
| 当前代码行为 | 如果发错到 `drc/down`，会返回 topic 错误 |

链路：

```text
平台发 drc_mode_enter 到 services
→ 网关检查是否绑定、飞机在线、没有任务冲突
→ 进入 DRC
→ 后续 heart_beat / stick_control 才走 drc/down
```

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台联调脚本是否把 `drc_mode_enter` 发到了 `drc/down` | 如果是脚本问题，会一直被代码拒绝 |

## 8. 平台问：`stick_control` 发了以后为什么有回包/为什么不该有回包

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 当前代码会回 `drc/up`，但官方 DOCK3 DRC 页面写这类高频控制没有应答机制。 |
| 当前 topic | 平台发 `thing/product/{机场SN}/drc/down`，网关回 `thing/product/{机场SN}/drc/up` |
| 当前动作 | 把摇杆量转成速度，发给飞控 |
| 潜在问题 | 平台如果严格要求“无应答”，当前多回了一条 |

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台是否允许 `stick_control` 回 `drc/up` | 不允许就要改 MQTT 回包逻辑 |
| 平台是否只要求 `heart_beat` 回，摇杆不回 | 当前代码没有区分 |

会议建议回答：

> 当前能执行摇杆控制，但协议口径需要确认：代码现在每条 `stick_control` 会回一条 `drc/up`；如果平台按官方“无应答机制”严格验收，我们需要把 `stick_control` 改成只执行不回包。

## 9. 平台问：DRC 心跳里的 `seq` 为什么对不上

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 当前代码主要从 `data.seq` 读序号；如果平台把 `seq` 放 payload 顶层，当前可能读不到。 |
| 当前读取字段 | `data.timestamp`、`data.seq` 或 `data.sequence` |
| 当前回包 | `drc_state`、`session_id`、`timestamp`，如果读到 seq 才回 seq |

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台 `heart_beat` 的 `seq` 放顶层还是 `data` 内 | 当前代码只支持 data 内 |
| 平台是否要求 seq 单调递增 | 当前代码默认检查 seq 递增 |

## 10. 平台问：航线文件 URL 下发后为什么没有执行

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 当前航线解析只支持本地文件路径或 `file://`，不会自动下载 http/https/oss/s3 的远程 URL。 |
| 影响 method | `flighttask_prepare` |
| 当前结果 | 如果只给远程 URL，解析不到航点，会准备失败 |

链路：

```text
平台发 flighttask_prepare，带 file.url
→ parse_wayline_file()
→ 如果是 http/https/oss/s3，只记录 source，不下载
→ 没有航点
→ prepare 失败
```

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台下发的是可公网下载 URL，还是平台期望网关先请求资源 | 当前没有下载逻辑 |
| 航线文件是否已提前落到网关本地 | 如果没有，需要补下载 |

## 11. 平台问：航线进度里为什么 `media_count=0`、`track_id` 为空

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 当前航线进度事件有这两个字段，但没有和真实媒体采集/轨迹系统完整闭环。 |
| `media_count` | 当前多为 0 |
| `track_id` | 普通航线任务里常为空；`takeoff_to_point` 会生成 `track-{flight_id}` |

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台是否强依赖 `media_count` 判断任务完成 | 当前不能保证 |
| `track_id` 是平台生成、设备生成，还是可为空 | 当前普通航线常为空 |

## 12. 平台问：开盖、充电、飞机上下电这些是真动作还是只改状态

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 这些有机巢命令下发路径，不是单纯改内存状态；但真实结果要看机巢通信。 |
| 有真实下发路径 | `cover_open/cover_close/charge_open/charge_close/drone_open/drone_close/supplement_light_open/supplement_light_close` |
| 失败情况 | 如果机巢命令发送失败，会返回失败 |
| 状态上报 | 机巢状态回来后，进入机场 OSD；飞机上下电还会触发 `update_topo` |

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台是看命令回包，还是看后续 OSD 状态变化 | 当前两者都有，但真实动作以 OSD 回读更可靠 |

## 13. 平台问：空调、声光报警、电池模式、SDR、eSIM 为什么失败

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 当前没有接这些真实硬件控制，所以代码明确返回失败/不支持，没有假成功。 |
| 涉及 method | `air_conditioner_mode_switch`、`alarm_state_switch`、`battery_store_mode_switch`、`sdr_workmode_switch`、`sim_slot_switch`、`esim_activate`、`esim_operator_switch` |
| 当前返回 | 回 `services_reply`，状态为失败或 rejected |

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台是否要求这些能力在本阶段必须成功 | 如果必须成功，需要接真实硬件控制 |
| 平台是否接受当前返回“不支持/失败” | 如果接受，就作为当前阶段限制说明 |

## 14. 平台问：日志/媒体上传是不是完整对象存储上传

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 当前是 HTTP PUT/mock 模型，不是完整云厂商 SDK 闭环。 |
| 日志上传 | `fileupload_start` 后按 `endpoint/object_key` PUT |
| 媒体上传 | 能发 `fileupload_progress` 和 `file_upload_callback`，但真实凭证/签名/断点续传不完整 |

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台给的 `endpoint` 是完整上传地址还是对象存储地址 | 当前按 HTTP PUT 拼接 |
| 是否必须断点续传 | 当前没有完整断点续传 |
| 上传完成后平台看哪个事件 | 当前有 `fileupload_progress` 和 `file_upload_callback` |

## 15. 平台问：OTA 升级状态为什么没在 state 里变化

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | OTA 主要通过 `ota_progress/ota_result` 事件上报；`firmware_upgrade_status` 内部会更新，但多数地方 `notify=False`，不会主动推 state。 |
| 当前事件 | `events.ota_progress`、`events.ota_result` |
| 当前 state | 不保证主动推 `firmware_upgrade_status` |

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台看 OTA 状态是看事件，还是必须看 state 属性 | 当前主要靠事件 |
| 是否需要升级完成后主动推 `firmware_version` | 当前不保证 |

## 16. 平台问：HMS 告警码平台不认识怎么办

| 项 | 当前代码实际情况 |
|---|---|
| 简单回答 | 当前告警一部分来自机巢状态映射，一部分可能是本地生成，不保证全部是平台官方 HMS 字典里的 key。 |
| 当前上报 | `events.hms` |
| 当前字段 | `id/alarm_id/device_sn/domain/module/type/level/code/hms_key/content/message/args/create_time/update_time` |

需要确认：

| 要确认的问题 | 为什么 |
|---|---|
| 平台要求 `hms_key` 必须来自官方字典吗 | 当前不保证全部来自官方字典 |
| 告警文案由平台翻译还是网关直接给 | 当前两者都带，但要统一 |

## 17. 会议现场优先追问清单

| 优先级 | 问题 | 我们当前结论 |
|---|---|---|
| P0 | `height_limit/distance_limit` 平台到底看飞机 OSD 还是机场 state | 当前只保证飞机 OSD |
| P0 | `live_capacity` 是否要求上线主动推 state | 当前没推，需要确认是否补 |
| P0 | 组织绑定前是否允许业务指令 | 当前只允许查询/绑定/拓扑/property_set，大多数业务指令拒绝 |
| P0 | 绑定成功后是否必须等平台 `status_reply` 再执行业务 | 当前本地绑定成功后即可过准入，未强等 status_reply |
| P0 | DRC `stick_control` 是否允许回包 | 当前会回，官方口径可能无应答 |
| P0 | DRC 心跳 `seq` 在哪里 | 当前只读 data 内 |
| P0 | 航线文件是否需要网关下载远程 URL | 当前不下载 |
| P1 | `media_count/track_id` 是否强依赖 | 当前不完整 |
| P1 | 空调/eSIM/SDR/电池/声光报警是否必须成功 | 当前明确不支持真实控制 |
| P1 | OTA 状态看事件还是 state | 当前主要发事件 |
