# 当前代码与 DOCK3 上云 API 对照清单

更新时间：2026-07-29

用途：明天和平台线上会议逐项对问题。本文按“官方 DOCK3 上云 API 页面 + 当前项目实际代码逻辑”整理，不按项目历史说明文档判断。

## 0. 先给平台说明的总体口径

当前网关不是纯文档接口，已经有 MQTT 收发、路由、回包、状态上报、事件上报、机巢/飞控/直播/媒体/日志等代码链路。但不能把所有已注册 method 都说成完整完成，因为代码里存在三种情况：

| 状态 | 含义 | 例子 |
|---|---|---|
| 已按 DOCK3 主链路实现 | 主题方向、method、字段校验、回包、部分主动上报都有代码 | `update_topo`、`property/set`、`flighttask_prepare`、`live_start_push`、`drc_mode_enter` |
| 有入口，依赖现场设备才能闭环 | 代码会真实调机巢/飞控/ffmpeg/文件，但效果要看现场设备、网络、推流地址、存储凭证 | 航线执行、DRC 控制、直播真实推流、日志上传、开盖/充电/飞机上下电 |
| 有入口，但当前不能算真实完成 | 缺少真实设备对接，代码会失败返回，不会假装成功 | SDR、eSIM、空调、声光报警、电池模式、RTK 标定、设备格式化、系统重启、真实 OTA 刷写 |

一句话链路：

```text
平台下发 services / property/set / drc/down
→ src/mqtt/client.py 按 topic 和 method 路由
→ src/main.py 注册的 handler 执行业务
→ 网关回 services_reply / property/set_reply / drc/up
→ 如果状态变化，再主动发 osd / state / events / status / requests
```

## 1. 本次对照使用的官方页面和代码入口

官方页面：

| 官方分组 | 页面 |
|---|---|
| Topic 定义 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/topic-definition.html |
| Properties | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/properties.html |
| Device | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/device.html |
| Organization | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/organization.html |
| Live | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/live.html |
| Media | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/media.html |
| Wayline | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/wayline.html |
| DRC | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/drc.html |
| HMS | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/hms.html |
| Log | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/log.html |
| Firmware | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/firmware.html |
| Config | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/config.html |
| Cmd / Remote Debug | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/cmd.html |

当前代码入口：

| 代码 | 用途 |
|---|---|
| `src/main.py` | 启动流程、模块回调、method 注册 |
| `src/mqtt/client.py` | MQTT 订阅、topic 校验、回包 topic 选择 |
| `src/utils/message.py` | MQTT payload 结构 |
| `src/cloud_api/dock3_properties.py` | DOCK3 属性表、pushMode、accessMode、属性设置校验 |
| `src/cloud_api/dock3_device.py` | `update_topo` 字段和产品类型校验 |
| `src/cloud_api/dock3_organization.py` | 组织绑定 3 个 requests 的字段表 |
| `src/cloud_api/dock3_live.py` | 直播字段校验和 `live_capacity` 构造 |
| `src/cloud_api/dock3_media.py` | 媒体列表、删除、上传进度字段 |
| `src/cloud_api/dock3_wayline.py` | 航线任务字段校验和进度结构 |
| `src/cloud_api/dock3_drc.py` | DRC 字段校验和值域 |
| `src/cloud_api/dock3_cmd.py` | 远程调试/机场控制字段校验和进度结构 |
| `src/cloud_api/dock3_hms_log.py` | HMS、远程日志、上传进度结构 |
| `src/device/manager.py` | 设备、属性、拓扑、组织、HMS、OTA、配置、机场控制 |
| `src/media/manager.py` | 直播、媒体文件、上传完成事件 |
| `src/mission/manager.py` | 航线任务、一键返航、断点续飞 |
| `src/drc/controller.py` | DRC 进入/退出、杆量、指点飞行、紧急停止 |
| `src/modules/log_manager.py` | 远程日志列表、上传、取消/暂停/恢复 |

## 2. Topic 方向：哪些是平台下发，哪些是网关主动发

| Topic | 官方方向 | 当前代码行为 | 触发方 |
|---|---|---|---|
| `thing/product/{device_sn}/osd` | 设备到平台 | `TelemetryReporter.report_osd()` 发布 | 网关主动，定频或遥测/机巢回调触发 |
| `thing/product/{device_sn}/state` | 设备到平台 | `TelemetryReporter.report_state()` 发布 | 网关主动，状态变化触发 |
| `thing/product/{gateway_sn}/services` | 平台到设备 | `MQTTClient` 订阅，按 method 调 handler | 平台触发 |
| `thing/product/{gateway_sn}/services_reply` | 设备到平台 | 普通服务命令回包 | 网关收到 services 后触发 |
| `thing/product/{gateway_sn}/property/set` | 平台到设备 | 专门处理属性设置 | 平台触发 |
| `thing/product/{gateway_sn}/property/set_reply` | 设备到平台 | 属性设置逐项回包 | 网关收到 property/set 后触发 |
| `thing/product/{gateway_sn}/events` | 设备到平台 | 任务进度、直播状态、HMS、OTA、配置结果等 | 网关主动，业务动作后触发 |
| `thing/product/{gateway_sn}/requests` | 设备到平台 | 组织绑定、绑定状态查询、媒体上传凭证请求 | 网关主动请求平台 |
| `thing/product/{gateway_sn}/requests_reply` | 平台到设备 | 平台回复网关主动请求 | 平台触发 |
| `sys/product/{gateway_sn}/status` | 设备到平台 | `update_topo` 拓扑上报 | 网关主动，或平台要求同步后再次上报 |
| `sys/product/{gateway_sn}/status_reply` | 平台到设备 | 平台确认 `update_topo` | 平台触发 |
| `thing/product/{gateway_sn}/drc/down` | 平台到设备 | DRC 进入后控制数据 | 平台触发 |
| `thing/product/{gateway_sn}/drc/up` | 设备到平台 | DRC 控制回包/心跳回包 | 网关收到 drc/down 后触发 |

当前 topic 校验：

| method | 当前必须发到 | 说明 |
|---|---|---|
| `property_set` | `property/set` | 如果平台发到 `services`，网关返回错误 |
| `drc_mode_enter` | `services` | 当前代码明确禁止发到 `drc/down` |
| `drc_mode_exit` | `services` | 当前代码明确禁止发到 `drc/down` |
| `takeoff_to_point` | `services` | 当前代码按 services 回 `services_reply` |
| `stick_control`、`drone_control`、`heart_beat` | `drc/down` | 当前可处理；普通路由会回 `drc/up` |

需要和平台确认：

| 问题 | 当前代码情况 | 为什么要确认 |
|---|---|---|
| `stick_control` 是否允许网关回 `drc/up` | 当前通用路由会回 | 官方 DOCK3 DRC 页面写“无应答机制”，平台如果严格按无回包验收，当前需要改成不回 |
| `drone_control` 的 `output.seq` 是否强制 | 当前回 `output.status/simulated`，没有把 seq 放进 `output.seq` | 官方 topic 示例里 `drc/up` 的 `output.seq` 是重点字段 |
| `heart_beat` 的 seq 在 data 内还是 payload 顶层 | 当前只从 `data.seq` 或 `data.sequence` 读 | 官方示例中 `seq` 在 payload 顶层，当前可能漏读 |

## 3. 网关启动后会主动上报什么

启动主链路在 `src/main.py::_on_mqtt_connected_for_startup_sync()`。

| 启动动作 | 当前是否会做 | Topic | method | 主要字段 | 触发条件 |
|---|---|---|---|---|---|
| 设备上线事件 | 会 | `thing/product/{gateway_sn}/events` | `device_online` | `device_sn`、`online_status=1`、`timestamp` | MQTT 连接且启动流程准备好 |
| 设备基础信息事件 | 会 | `thing/product/{gateway_sn}/events` | `device_info_put` | `device_sn`、`product_type`、`product_name`、`firmware_version`、`hardware_version`、`timestamp` | `CloudConnectionManager.report_device_online()` 内触发 |
| 心跳事件 | 会 | `thing/product/{gateway_sn}/events` | `heartbeat` | `device_sn`、`timestamp` | `start_heartbeat()` 每 10 秒一次 |
| 组织查询 | 条件触发 | `thing/product/{gateway_sn}/requests` | `airport_organization_get` | `device_binding_code`、`organization_id` | `device.organization_binding.enabled=true` |
| 组织绑定 | 条件触发 | `thing/product/{gateway_sn}/requests` | `airport_organization_bind` | `bind_devices[].sn`、`device_binding_code`、`organization_id`、`device_callsign`、`device_model_key` | 组织查询成功后 |
| 绑定状态查询 | 条件触发 | `thing/product/{gateway_sn}/requests` | `airport_bind_status` | `devices[].sn` | 未走配置绑定且本地未绑定 |
| 拓扑上报 | 条件触发 | `sys/product/{gateway_sn}/status` | `update_topo` | 网关产品字段、密钥、nonce、物模型版本、子设备列表 | 已绑定、绑定成功、拓扑变化、平台调用 `update_topo` 后 |
| 机场 OSD | 会尝试 | `thing/product/{gateway_sn}/osd` | 无 method | DOCK3 `pushMode=0` 的机场属性 | 遥测回调强制触发；机巢环境回调节流触发 |
| 飞机 OSD | 有在线子设备才发 | `thing/product/{sub_device_sn}/osd` | 无 method | 飞机遥测字段 | 子设备在线；无在线子设备时只存遥测 |
| 运行时长 state | 会 | `thing/product/{gateway_sn}/state` | 无 method | `property.acc_time` | 系统状态线程每 5 秒更新 |
| 能力集事件 | 云连接成功时会触发 | `thing/product/{gateway_sn}/events` | `dock_capability_put`、`drone_capability_put`、`payload_capability_put`、`media_capability_put`、`drc_capability_put` | 能力字段 | `cloud_conn` 状态回调 connected 时调用 |
| 直播能力 state | 当前不会启动主动推 | `thing/product/{gateway_sn}/state` | 无 method | `property.live_capacity` | 代码只构造/查询，不主动推 |

会议提醒：

| 平台可能问 | 当前回答 |
|---|---|
| 网关一启动就会上报 DOCK3 拓扑吗 | 不一定。只有本地认为已绑定，或配置组织绑定成功，或绑定状态查询确认后才发 `update_topo` |
| 网关一启动就会上报直播相关 state 吗 | 不会主动推 `live_status`，也不会主动推 `live_capacity` |
| 网关一启动就会上报 OSD 吗 | 会尝试发机场 OSD；飞机 OSD 要等子设备在线 |
| `device_online/heartbeat` 是不是官方核心拓扑 | 这是当前项目事件口径；平台如果按官方 DOCK3 验收，核心应看 `update_topo/osd/state/events` |

## 4. Properties：OSD、State、可写属性

官方规则很简单：

| 官方字段 | 含义 | 当前代码依据 |
|---|---|---|
| `pushMode=0` | 定频上报，走 `thing/product/{device_sn}/osd` | `build_dock3_dock_osd()` 只取 pushMode=0 |
| `pushMode=1` | 状态变化上报，走 `thing/product/{device_sn}/state` | `_notify_state()` 只放 pushMode=1 |
| `accessMode=rw` | 平台可以通过 `property/set` 设置 | `validate_property_set()` 只允许 writable 属性 |

### 4.1 当前平台可以设置的属性

当前只有 3 个 DOCK3 机场属性允许 `property/set`：

| 属性 | 平台下发值 | 回包 | 成功后是否主动推 state |
|---|---|---|---|
| `air_transfer_enable` | bool | `data.air_transfer_enable.state.result=0/1` | 会，推 `state.property.air_transfer_enable` |
| `silent_mode` | 0/1 | `data.silent_mode.state.result=0/1` | 会，推 `state.property.silent_mode` |
| `user_experience_improvement` | 0/1/2 | `data.user_experience_improvement.state.result=0/1` | 会，推 `state.property.user_experience_improvement` |

平台如果设置只读属性，例如 `live_status`、`live_capacity`、`cover_state`、`drc_state`、`battery_store_mode`，当前会返回失败，不会改状态。

### 4.2 当前 DOCK3 state 属性逐项情况

这些属性按代码表属于 `pushMode=1`，理论上走 `thing/product/{gateway_sn}/state`。但“属于 state 属性”和“当前代码一定主动推”不是一回事。

| 属性 | 官方含义简述 | 当前代码是否会主动推 | 触发条件 | 需要确认/补齐 |
|---|---|---|---|---|
| `rtcm_info` | RTK 标定源 | 当前没有稳定主动推 | 只有内部属性变化且显式 notify 才会推，现未看到真实 RTK 触发 | 真实 RTK 字段来源、是否必须上线推 |
| `wireless_link_topo` | 图传连接拓扑 | 会在部分拓扑变化时推 | `_update_wireless_link_topology()`、子设备/无线拓扑变化 | 真实 `secret_code/center_node/leaf_nodes` 由谁给 |
| `air_transfer_enable` | 空中回传 | 会 | 平台 `property/set` 成功后 | 无 |
| `silent_mode` | 静音模式 | 会 | 平台 `property/set` 成功后 | 无 |
| `user_experience_improvement` | 用户体验计划 | 会 | 平台 `property/set` 成功后 | 无 |
| `dongle_infos` | 4G Dongle 信息 | 当前没有真实主动推 | 未看到真实蜂窝 adapter 更新 | Dongle 信息是否平台必看 |
| `compatible_status` | 固件一致性状态 | 当前没有稳定主动推 | 属性表有，未看到明确业务触发 | 平台是否强依赖 |
| `acc_time` | 累计运行时长 | 会 | 系统状态线程每 5 秒更新 | 无 |
| `live_capacity` | 直播能力 | 当前不会主动推 | `_refresh_payload_properties(notify=False)` 构造但不通知 | 是否要求上线或能力变化时推 |
| `live_status` | 直播状态 | 会，但不是启动推 | 直播开始、停止、切镜头、改画质后 | 平台看 state、event，还是两者都看 |
| `firmware_upgrade_status` | 固件升级状态 | 代码更新但通常不主动 state | OTA 进度里 `notify=False`，主要发 `ota_progress/ota_result` | 平台是否要求 state 同步 OTA 状态 |
| `firmware_version` | 固件版本 | 当前没有稳定主动推 | OTA 模拟完成会改内部属性，但未看到 state notify | 平台是否要求升级完成后推 state |

重点回答平台今天问的直播问题：

| 平台问 | 当前代码真实答案 |
|---|---|
| 机场的 state 主题下和直播相关的信息没有推送吗 | `live_status` 会推，但只有直播动作触发后推；`live_capacity` 当前不会主动推 |
| 一上线能看到直播能力吗 | 当前不能保证。`live_capacity` 能通过 `live_capability_query` 或 `property_get` 查到，但不是启动主动 state |
| 开始直播后 state 有什么 | `property.live_status` 数组，包含 `video_id/video_type/video_quality/status/error_status` |

### 4.3 当前机场 OSD 属性

这些按代码表属于 `pushMode=0`，通过 `thing/product/{gateway_sn}/osd` 定频/节流上报。它们不会因为 `update_properties()` 改变就进入 `state`，除非属性表本身是 `pushMode=1`。

| 属性 | 当前来源 | 说明 |
|---|---|---|
| `home_position_is_valid` | 默认/配置/遥测映射 | Home 点有效性，真实语义要看机场标定 |
| `heading` | 遥测/配置 | 机场朝向 |
| `air_conditioner` | 默认/内部属性 | 当前无真实空调 adapter |
| `drone_battery_maintenance_info` | 默认/内部属性 | 真实电池保养信息待设备 |
| `maintain_status` | 默认/内部属性 | 保养信息 |
| `position_state` | 遥测/GPS 映射 | 搜星状态 |
| `emergency_stop_state` | 机巢/默认 | 急停状态 |
| `drone_charge_state` | 机巢充电状态映射 | 充电状态结构 |
| `backup_battery` | 默认/内部属性 | 备用电池 |
| `alarm_state` | 默认/控制状态 | 真实声光报警 adapter 未接 |
| `battery_store_mode` | 默认/控制状态 | 真实电池模式 adapter 未接 |
| `activation_time` | 默认/配置 | 激活时间 |
| `height` | 遥测/配置 | 椭球高度 |
| `alternate_land_point` | 平台设置或默认 | 备降点 |
| `first_power_on` | 默认/配置 | 首次上电 |
| `storage` | 本机磁盘查询 | 只进 OSD，不主动进 state |
| `working_current`、`working_voltage` | 默认/机巢映射 | 工作电流/电压 |
| `humidity`、`temperature`、`environment_temperature`、`wind_speed`、`rainfall` | 机巢/环境映射 | 环境类状态 |
| `wireless_link` | 默认/无线链路模拟结构 | 真实 SDR/4G 质量待设备 |
| `media_file_detail` | 默认/媒体 | 媒体详情 |
| `job_number` | 内部统计 | 作业架次 |
| `drone_in_dock` | 机巢/飞控推断 | 飞机是否在舱 |
| `network_state` | 网关网络查询 | 只进 OSD，不主动进 state |
| `supplement_light_state` | 机巢/控制状态 | 补光灯状态 |
| `cover_state` | 机巢/开关盖控制目标 | 舱盖状态 |
| `sub_device` | 子设备缓存 | 注意：当前属性表是 OSD，不是 state |
| `flighttask_step_code` | 航线任务状态 | 任务步骤 |
| `mode_code` | 机场运行模式 | 调试/任务/升级等 |
| `longitude`、`latitude` | 遥测/配置 | 经纬度 |
| `drc_state` | DRC 状态 | 注意：当前属性表是 OSD；代码也会发 `drc_status_notify` 事件 |
| `self_converge_coordinate` | 默认/RTK | 自收敛坐标 |

需要和平台确认：

| 问题 | 当前代码判断 |
|---|---|
| 平台是否把 `sub_device` 当 state 看 | 当前代码按 DOCK3 属性表放 OSD，不会主动 state |
| 平台是否把 `drc_state` 当 state 看 | 当前代码属性表是 OSD，同时会发 `drc_status_notify`；官方 DRC 页面又提示 `drc_status_notify` 已不再维护，建议平台确认验收口径 |
| 平台是否要求 `live_capacity` 上线主动推 | 当前缺口明确，需要补 |

## 5. Device：设备拓扑 `update_topo`

官方主链路：

```text
网关 → sys/product/{gateway_sn}/status, method=update_topo
平台 → sys/product/{gateway_sn}/status_reply
```

当前代码字段：

| 字段 | 当前来源 | 当前值/规则 |
|---|---|---|
| `domain` | `config/gateway.yaml` 或平台下发 `update_topo` | 当前默认 `"3"` |
| `type` | 配置或平台下发 | 当前默认 `119`，代码允许 119-123 |
| `sub_type` | 配置或平台下发 | 当前默认 `0` |
| `device_secret` | 配置或平台下发 | production 严格要求非空 |
| `nonce` | 配置或平台下发 | production 严格要求非空 |
| `thing_version` | 配置或平台下发 | 当前默认 `1.1.2` |
| `sub_devices[]` | 子设备缓存 | 在线子设备列表；离线时可为空 |

子设备字段：

| 字段 | 当前来源 | 当前规则 |
|---|---|---|
| `sn` | 配置/平台下发/运行时添加 | 非空 |
| `domain` | 配置/平台下发 | 当前常见 `"0"` |
| `type` | 配置/平台下发 | 当前允许 `60`、`91` |
| `sub_type` | 配置/平台下发 | 当前常见 `0` |
| `index` | 配置/平台下发 | 当前常见 `"A"` |
| `device_secret` | 配置/平台下发 | production 严格要求非空 |
| `nonce` | 配置/平台下发 | production 严格要求非空 |
| `thing_version` | 配置/平台下发 | 当前默认 `1.1.2` |

触发：

| 触发情况 | 是否上报 `update_topo` |
|---|---|
| 启动时本地已绑定 | 会 |
| 组织绑定成功 | 会 |
| 绑定状态查询发现绑定变化 | 会 |
| `drone_open` 子设备上线 | 会 |
| `drone_close` 子设备离线 | 会 |
| 平台下发 `update_topo` 兼容同步 | 网关先校验/更新本地缓存，再主动发 `update_topo` |
| 设备离线 | 会，子设备置离线后上报 |

需要平台确认：

| 问题 | 当前不明确点 |
|---|---|
| 网关 `type=119` 是否就是平台产品枚举 | 代码支持 119-123，但真实平台产品字典要确认 |
| 子设备 `type=60/91` 哪个对应当前飞机 | 代码允许二者，现场要按平台产品支持表定 |
| `device_secret`、`nonce` 来源 | 代码可配置/平台下发，但真实值由平台还是设备生成要确认 |
| `thing_version=1.1.2` 是否匹配平台当前物模型 | 需要平台确认 |

## 6. Organization：组织绑定

官方方向是网关主动发 `requests`，平台回 `requests_reply`。当前代码也按这个方向实现。

| method | 方向 | 当前触发 | 请求字段 | 平台回包字段 | 当前处理 |
|---|---|---|---|---|---|
| `airport_bind_status` | 网关到平台 | 启动未绑定时查询，或手动调用 | `devices[].sn` | `output.bind_status[].sn/is_device_bind_organization/organization_id/organization_name/device_callsign` | 更新本地绑定缓存；绑定变化时发 `device_binding/device_unbinding` 和 `update_topo` |
| `airport_organization_get` | 网关到平台 | 配置开启组织绑定时先查 | `device_binding_code`、`organization_id` | `output.organization_name` | 缓存组织名称 |
| `airport_organization_bind` | 网关到平台 | 查询组织成功后绑定 | `bind_devices[].sn/device_binding_code/organization_id/device_callsign/device_model_key` | `output.err_infos[].sn/err_code` | 成功设备写本地绑定；失败设备记录错误；最后上报 `update_topo` |

当前配置里 `device.organization_binding.enabled=true`，并且使用 `SIM-BIND-CODE/org_sim_001/dock3-sim` 这类模拟值。

会议要确认：

| 问题 | 原因 |
|---|---|
| 平台希望启动自动绑定，还是平台已预绑定后只查状态 | 当前代码会按配置自动发组织查询/绑定 |
| 真实 `device_binding_code`、`organization_id`、`device_model_key` 谁提供 | 现在配置值像联调占位 |
| 子设备是否和网关一起绑定 | 当前 `bind_sub_devices=true` 时会一起放进 `bind_devices` |

## 7. Live：直播逐项

官方 DOCK3 Live 页面重点 method：

| 官方 method | Topic | 当前是否注册 | 当前代码入口 |
|---|---|---|---|
| `live_camera_change` | `services` | 已注册 | `MediaManager.handle_live_camera_change()` |
| `live_start_push` | `services` | 已注册 | `MediaManager.handle_live_start_push()` |
| `live_stop_push` | `services` | 已注册 | `MediaManager.handle_live_stop_push()` |
| `live_set_quality` | `services` | 已注册 | `MediaManager.handle_live_set_quality()` |
| `live_lens_change` / 设置直播镜头 | `services` | 已注册 | `MediaManager.handle_live_lens_change()` |

当前各方法字段和动作：

| method | 平台下发字段 | 当前校验/取值 | 当前动作 | 回包字段 | 主动上报 |
|---|---|---|---|---|---|
| `live_start_push` | `url_type`、`url`、`video_id`、`video_quality`，兼容 `video_type` | `url_type` 当前支持 `0/1/3/4`；`video_quality` 支持 `0..4`；`video_id/url` 必填 | `mock://` 走模拟；非 mock 走 ffmpeg 推流 | `output.status=in_progress`、`session_id`、`video_id`、`url_type`、`video_quality`、`video_type`、`source` | `events.live_status_notify` + `state.property.live_status` |
| `live_stop_push` | `video_id` | 未直播会拒绝 | 停止 ffmpeg，清空直播状态 | `output.status=ok`、`session_id`、`video_id` | `events.live_status_notify` + `state.property.live_status(status=0)` |
| `live_set_quality` | `video_id`、`video_quality` | `video_id` 必填，画质 `0..4` | 更新画质；真实推流时重启 ffmpeg | `output.status=ok`、`video_id`、`video_quality` | `events.live_status_notify` + `state.property.live_status` |
| `live_lens_change` | `video_id`、`video_type` | `video_type` 支持 `normal/wide/zoom/ir/fpv/gimbal` | 更新镜头；真实推流时重启 ffmpeg；还会尝试触发拍照 | `output.status=ok`、`video_id`、`video_type`、`photo` | `events.live_status_notify` + `state.property.live_status` |
| `live_camera_change` | `video_id`、`camera_position` | `camera_position=0/1` | 只记录当前直播相机位置 | `output.status=ok`、`video_id`、`camera_position` | 当前不发 `live_status` state/event |
| `live_capability_query` | 无 | 已注册，但更像查询能力 | 返回能力 | `supported_protocols/supported_url_types/.../live_capacity` | 不主动推 state |

当前 `state.property.live_status` 的实际结构：

```json
{
  "property": {
    "live_status": [
      {
        "video_id": "...",
        "video_type": "normal/wide/zoom/ir/fpv/gimbal",
        "video_quality": 0,
        "status": 1,
        "error_status": 0
      }
    ]
  }
}
```

当前 `events.live_status_notify` 的实际结构：

```json
{
  "result": 0,
  "output": {
    "status": "in_progress/ok/failed/rejected"
  },
  "video_id": "...",
  "session_id": "...",
  "url_type": 4,
  "video_quality": 0,
  "video_type": "gimbal",
  "timestamp": 1780000000000,
  "url": "...",
  "source": "..."
}
```

直播需要明确告诉平台的点：

| 平台问题 | 当前回答 | 是否要确认/整改 |
|---|---|---|
| `state` 下直播状态有没有推 | 有，`live_status` 在开始/停止/切镜头/改画质后推 | 不用确认，代码已有 |
| `state` 下直播能力有没有推 | 当前没有主动推 `live_capacity` | 要确认平台是否要求上线/能力变化推；若要求，需要补 |
| `live_camera_change` 后是否推 `live_status` | 当前不推，只回包 | 要确认平台是否希望相机切换也刷新直播状态 |
| DOCK3 `url_type` 是否允许 Agora=0 | 官方 Dock3 Live 页面显示重点为 RTMP/GB28181/WebRTC；当前代码也接受 0 | 要确认平台是否会下发 0，是否要禁用 |
| WebRTC/GB28181 是否要求专用 SDK | 当前非 mock 主要靠 ffmpeg 模型，不等同完整 SDK | 必须确认 |

## 8. Media：媒体文件和上传

当前媒体相关 method：

| method | 方向 | 平台下发字段 | 当前动作 | 回包/事件 |
|---|---|---|---|---|
| `file_list_get` | 平台下发 `services` | `media_type/file_type`、`start_time/end_time`、`page/page_size` | 扫本地媒体目录，按类型/时间分页 | 回 `files/list/total/page/page_size` |
| `file_delete` | 平台下发 `services` | `file_ids` 或 `files[].file_id/fingerprint/name` | 删除本地文件 | 每个文件 `result/msg` |
| `file_download` / `download_media` | 平台下发 `services` | `file_ids` 或 `download_url` | 下载到网关本地缓存 | 每个文件下载结果 |
| `adjust_upload_priority` | 平台下发 `services` | `files[].file_id`、`priority` | 更新优先级 | 如命中本地文件，会发 `fileupload_progress(in_progress)` |
| `highest_priority_upload_flighttask_media` | 平台下发 `services` | `flight_id` | 记录/上报最高优先级任务 | 发同名事件 |
| `upload_flighttask_media_prioritize` | 平台下发 `services` | `flight_id/flighttask_id` | 兼容优先上传入口 | 回 `output.status` |
| `storage_config_get` | 平台下发 `services` 兼容 | 无或配置字段 | 返回本地 OSS 配置 | 回 `bucket/credentials/endpoint/provider/region/object_key_prefix` |
| `request_storage_config_get()` | 网关主动 `requests` | `module` | 主动向平台请求上传凭证 | 等平台 `requests_reply` |
| `file_upload_callback` | 网关主动 `events` | 无 | 上传完成后上报 | `file.object_key/path/name/ext/metadata/cloud_to_cloud_id` |
| `fileupload_progress` | 网关主动 `events` | 无 | 上传进度上报 | `output.ext.files[].progress` |

媒体文件当前字段来源：

| 字段 | 来源 |
|---|---|
| `file_id`/`fingerprint` | 本地路径 hash 或远程 URL hash |
| `device_sn` | 网关 SN |
| `name/file_name` | 文件名 |
| `path/object_key` | 相对路径或 URL path |
| `size/file_size` | 文件大小 |
| `media_type/file_type` | 文件扩展名判断 |
| `create_time/capture_time` | 文件修改时间 |
| `upload_status` | `pending` 或 `remote` |

需要确认：

| 问题 | 当前不明确点 |
|---|---|
| 平台媒体源是相机真实文件还是网关本地目录 | 当前主要扫本地目录/远程 URL |
| 上传凭证格式是否固定 | 当前支持本地 OSS 配置和 requests 获取，但生产签名格式要对齐 |
| 上传完成后平台只看 `file_upload_callback` 还是也看 `fileupload_progress` | 当前两类事件都有，但触发场景不同 |
| 是否要求断点续传 | 当前不是完整断点续传 |

## 9. Wayline：航线任务

官方 DOCK3 Wayline 中，平台主要通过 `services` 下发任务，网关通过 `events` 上报进度/就绪/返航信息。

### 9.1 平台下发 method

| method | 平台下发字段 | 当前动作 | 回包 | 主动事件 |
|---|---|---|---|---|
| `flighttask_prepare` | `flight_id`、`task_type`、`execute_time`、`file.url/fingerprint`、`ready_conditions`、`executable_conditions`、`break_point`、`rth_altitude`、`out_of_control_action`、`exit_wayline_when_rc_lost`、`simulate_mission` | 校验任务；解析本地 KMZ/KML/WPML/waypoints；上传航点到飞控或委托机巢；自检 | `output.status=sent`、`progress.current_step=5`、`percent=0`、`ext` | `flighttask_progress(status=sent)`、`flighttask_ready` |
| `flighttask_execute` | `flight_id` | 校验是否已 prepare；检查时间窗口；真实飞控起飞/执行或委托机巢 | `output.status=in_progress` | `flighttask_progress(in_progress)` |
| `flighttask_pause` | `flight_id/task_id` 可兼容 | 暂停航线，记录断点 | `output.status=paused` 或失败 | `flighttask_progress(paused)` |
| `flighttask_resume` | `flight_id/task_id` 可兼容 | 从断点恢复 | `output.status=in_progress` | `flighttask_progress(in_progress)` |
| `resume_wayline_task` | 同恢复 | 平台主动干预后的恢复入口 | 同上 | 同上 |
| `flighttask_return` | `flight_id`、`return_type=0/1` | 记录断点；返航或降落 | `output.status=in_progress` | `flighttask_progress(paused)`、`return_home_info` |
| `flighttask_undo` | `flight_ids[]` | 取消当前任务，清断点，必要时返航 | `output.status=canceled` | `flighttask_progress(canceled)` |
| `flighttask_stop` | `flight_id`、`reason=0/1` | 复用取消链路 | `output.status=canceled` | `flighttask_progress(canceled)` |
| `flighttask_progress` | `flight_id` | 当前作为查询入口 | 回当前进度 | 不主动上报 |
| `flighttask_progress_get` | `sn` 可选 | 查询机场任务进度 | `output.flight_id/progress/status` | 不主动上报 |
| `flight_setup_abort` | 空对象/null | 取消准备阶段任务 | 视状态返回 | 可能发失败/取消进度 |
| `flighttask_recovery` | 恢复参数 | 复用恢复 | 同恢复 | 同恢复 |
| `return_home` | 空对象/null | 一键返航 | `output.status=in_progress` | `flighttask_progress(in_progress)` |
| `return_home_cancel` | 空对象/null | 取消返航 | 视飞控状态 | 事件随任务状态 |
| `return_specific_home` | `home_dock_sn` | 只支持本网关机场；多机场拒绝 | 非本网关返回不支持 | 无 |
| `flighttask_resource_get` | `flight_id` | 查询任务资源 | 回本地记录 | 无 |
| `in_flight_wayline_deliver` | `in_flight_wayline_id`、`file.url/fingerprint`、失控动作、返航高度等 | 空中下发航线入口 | 依赖真实链路 | 进度事件 |
| `in_flight_wayline_stop/recover/cancel` | `in_flight_wayline_id` 等 | 空中航线停止/恢复/取消入口 | 依赖真实链路 | 进度事件 |

### 9.2 当前航线进度事件字段

`flighttask_progress` 当前事件结构：

```json
{
  "result": 0,
  "output": {
    "status": "sent/in_progress/paused/ok/failed/canceled",
    "progress": {
      "current_step": 23,
      "percent": 50
    },
    "ext": {
      "current_waypoint_index": 0,
      "wayline_mission_state": 6,
      "media_count": 0,
      "track_id": "",
      "flight_id": "...",
      "wayline_id": 0,
      "break_point": {},
      "failure": {}
    }
  }
}
```

航线重要限制：

| 点 | 当前代码 |
|---|---|
| `simulate_mission.is_enable=1` | 当前在 `flighttask_prepare` 里拒绝，要求真实执行 |
| 远程航线 URL 下载 | `parse_wayline_file()` 不下载 http/https/oss/s3，只接受本地文件或 `file://` |
| 真实终态 | 需要现场设备验证起飞、航点、返航、降落、收舱 |
| `media_count` | 当前多为 0，没有和真实拍照/录像文件数闭环 |
| `track_id` | 航线任务里常为空；`takeoff_to_point` 会生成 `track-{flight_id}` |

需要确认：

| 问题 | 当前不明确点 |
|---|---|
| 平台下发航线文件是 URL 还是本地已缓存路径 | 当前不主动下载远程 URL |
| `media_count` 是否强依赖 | 当前未闭环 |
| `track_id` 是否强依赖 | 当前航线任务常为空 |
| 完成任务时平台要求哪些最终事件 | 当前发 `flighttask_progress`，但完整收舱/充电终态要现场确认 |

## 10. DRC：指令飞行和远程控制

官方 DOCK3 DRC 分两类：

| 类型 | Topic | 说明 |
|---|---|---|
| 进入/退出 DRC、指点飞行、一键起飞、负载控制 | `services/services_reply` | 平台下发命令，网关回 services_reply |
| 高频控制和心跳 | `drc/down`、`drc/up` | 进入 DRC 后使用 |

### 10.1 DRC 进入/退出

| method | 平台字段 | 当前动作 | 回包 | 主动上报 |
|---|---|---|---|---|
| `drc_mode_enter` | 可带 `mqtt_broker.address/client_id/username/password/expire_time/enable_tls`、`osd_frequency`、`hsi_frequency` | 校验字段；必须有真实飞控 adapter；切入保持/盘旋；启动心跳监控 | `services_reply result=0` 或失败 | `events.drc_status_notify(drc_state=2)`；同时更新内部 `drc_state`，但该属性按当前表进 OSD |
| `drc_mode_exit` | `restore_mission` 可选 | 停心跳；可恢复任务；恢复原模式或 LOITER | `services_reply` | `events.drc_status_notify(drc_state=0)` |

重要：当前 `drc_mode_enter` 没有真实飞控 adapter 会失败，不会假成功。

### 10.2 DRC 高频控制

| method | Topic | 官方/当前字段 | 当前动作 | 回包情况 | 问题 |
|---|---|---|---|---|---|
| `heart_beat` | `drc/down` | 当前读 `data.timestamp`、`data.seq` | 更新 DRC 心跳时间 | 回 `drc/up`，带 `drc_state/session_id/timestamp/seq` | 官方 seq 可能在 payload 顶层，当前可能漏 |
| `stick_control` | `drc/down` | 官方 `roll/pitch/throttle/yaw/gimbal_pitch`，当前也兼容 `vx/vy/vz/yaw_rate` | 转速度控制给 MAVLink | 当前会回 `drc/up` | 官方写无应答机制，需确认 |
| `drone_control` | `drc/down` | 官方 `seq/x/y/h/w`，当前也兼容旧字段 | 转速度控制给 MAVLink | 当前回 `output.status/simulated` | 需要补 `output.seq` 吗要确认 |
| `drone_emergency_stop` | `drc/down` | 空对象 | 刹停/保持或降落，退出 DRC | 回 `drc/up` | 真机风险高，会议只确认接口口径 |

### 10.3 指点飞行和一键起飞

| method | Topic | 平台字段 | 当前动作 | 主动上报 |
|---|---|---|---|---|
| `fly_to_point_update` / `fly_to_point` | `services` | `fly_to_id`、`max_speed`、`points[].latitude/longitude/height` | 自动确保 DRC 权限；发送 guided 目标点 | `fly_to_point_progress` |
| `fly_to_point_stop` | `services` | 可空 | 停止当前指点飞行 | `fly_to_point_progress(status=wayline_cancel)` |
| `takeoff_to_point` | `services` | `target_latitude`、`target_longitude`、`target_height`、`security_takeoff_height`、`rth_altitude`、`commander_flight_height`、`max_speed`、`flight_id`、失控动作 | 记录任务；先发 ready，再发送 guided 目标点 | `takeoff_to_point_progress(task_ready/wayline_progress/failed/cancel)` |

需要确认：

| 问题 | 当前不明确点 |
|---|---|
| 平台是否要求 DRC 专用 broker 实际连接 | 当前代码只校验 `mqtt_broker` 字段，不会真的切到平台提供的 DRC broker |
| 高频 `stick_control` 是否必须无回包 | 当前会回 |
| `drc_state` 平台到底看 OSD、state，还是 `drc_status_notify` | 当前事件有，但官方说旧事件不再维护 |
| `takeoff_to_point` 的轨迹和 `track_id` 是否平台生成 | 当前本地生成 `track-{flight_id}` |

## 11. DRC 页面里的负载控制

这些 method 已注册到 payload 层，不在 `DrcController` 占位实现里。是否完成取决于真实相机/云台/负载 adapter。

| method | 当前处理模块 | 当前情况 |
|---|---|---|
| `camera_frame_zoom` | `CameraController` | 有入口，真实能力看相机/负载 |
| `camera_mode_switch` | `CameraController` | 有入口 |
| `camera_photo_take`、`camera_photo_stop` | `CameraController` | 有入口 |
| `camera_recording_start`、`camera_recording_stop` | `CameraController` | 有入口 |
| `camera_focal_length_set` | `CameraController` | 有入口 |
| `camera_exposure_mode_set`、`camera_exposure_set` | `CameraController` | 有入口 |
| `camera_focus_mode_set`、`camera_focus_value_set`、`camera_point_focus_action` | `CameraController` | 有入口 |
| `photo_storage_set`、`video_storage_set` | `CameraController` | 有入口，真实存储能力要看负载 |
| `camera_aim`、`camera_screen_split`、`gimbal_reset`、`camera_look_at` | `GimbalController` | 有入口，真实能力要看云台 |
| `ir_metering_mode_set`、`ir_metering_point_set`、`ir_metering_area_set` | `GimbalController` | 有入口，真实红外负载待确认 |
| `payload_authority_grab` | `ThirdPartyController` | 有入口，真实 PSDK/负载权限待确认 |

会议提醒：这些不能只说“代码注册了”。要看现场 ViewLink/相机/云台/PSDK 是否连上。

## 12. HMS：告警

| 场景 | 当前触发方 | 当前动作 | 上报/回包 |
|---|---|---|---|
| 机巢心跳、PLC、无人机心跳、急停、busy、fault、错误码变化 | 网关主动 | `update_from_nest()` 映射告警 | `events.hms` |
| 外部模块调用 `report_hms()` | 网关主动 | 标准化告警，写缓存/数据库 | `events.hms` |
| 平台下发 `hms_get` | 平台触发 | 查当前告警缓存；测试模式可注入告警 | `services_reply`，带 `alarms/list/total/page/page_size` |
| 平台下发 `hms_clear` | 平台触发 | 清指定或全部告警，清数据库 | `services_reply`，并发新的 `events.hms` |

告警字段当前会带：

| 字段 | 当前来源 |
|---|---|
| `id/alarm_id` | 原始告警或本地生成 |
| `device_sn` | 告警设备 SN |
| `domain` | 默认 `"3"` 或原始值 |
| `module` | 0/1/2/3 |
| `type` | 默认 `hms` 或 `nest_status` |
| `level` | 0/1/2 |
| `in_the_sky` | 0/1 |
| `code/hms_key` | 原始 code/key 或本地生成 |
| `content/message` | 原始文案 |
| `args.component_index/sensor_index` | 原始值或 0 |
| `create_time/update_time` | 原始时间或当前时间 |

需要确认：

| 问题 | 当前不明确点 |
|---|---|
| 平台要求的 HMS key/code 字典 | 当前一部分来自机巢映射/自定义错误码 |
| 告警文案由网关给，还是平台按 hms_key 翻译 | 当前两者都带，但官方 HMS 文案通常依赖 key |
| 清告警后平台期望事件字段 | 当前清完会再发 `events.hms` |

## 13. Log：远程日志上传

| method | 平台字段 | 当前动作 | 回包 | 主动事件 |
|---|---|---|---|---|
| `fileupload_list` | `module_list` | 扫 `logging.dir` 下 `.log/.jsonl/.txt/.out/.err` 等文件，按模块分组 | `files[].device_sn/module/result/list[].boot_index/start_time/end_time/size` | 无 |
| `fileupload_start` | `bucket/region/endpoint/provider/credentials/params.files[].object_key/module/list[].boot_index` | 匹配本地日志；先发进行中；后台 HTTP PUT 或 mock 完成 | `output.status=in_progress`、`task_id`、`files` | `fileupload_progress(in_progress/ok/failed)` |
| `fileupload_update` | `status=cancel/pause/resume`、`module_list` | 更新上传任务状态 | `output.status=ok`、`updated` | `fileupload_progress(canceled/in_progress)` |
| `device_log_list_get` | `page/page_size` | 另一套日志列表查询 | `logs/list/total/page/page_size` | 无 |
| `device_log_upload` | `log_ids[]` | 选中本地日志并发上传开始事件 | `upload_task_id/accepted_count/missing_ids` | `fileupload_progress(in_progress)` |

需要确认：

| 问题 | 当前不明确点 |
|---|---|
| 平台 `endpoint` 是完整上传 URL，还是对象存储 endpoint | 当前按 HTTP PUT 拼 `endpoint/object_key` |
| 平台是否要求云存储 SDK 签名上传 | 当前不是完整云厂商 SDK |
| `pause/resume` 是否需要真正暂停线程 | 当前主要更新状态，未实现复杂断点续传 |

## 14. Firmware：固件升级

| method | 平台字段 | 当前动作 | 回包 | 主动事件 |
|---|---|---|---|---|
| `ota_create` | `devices[]`，设备内取 `product_version/firmware_version/version`、`file_url/url`、`file_size`、`md5`、`file_name`、`firmware_upgrade_type`；也支持 `simulate` | 创建升级任务；mock 或 simulate 走模拟；真实 URL 会下载、校验、调用刷写脚本或模拟刷写 | `output.status=in_progress`、`task_id`、`target_version`、`firmware_upgrade_type`、`simulation_mode` | `ota_progress`、`ota_result` |
| `firmware_upgrade` | 同 `ota_create` | 兼容别名 | 同上 | 同上 |
| `ota_cancel` | 无 | 取消当前 OTA | `output.status=canceled`、`task_id` | `ota_result(canceled)` |
| `firmware_upgrade_cancel` | 同 `ota_cancel` | 兼容别名 | 同上 | 同上 |

当前 OTA 事件：

| 事件 | 字段 |
|---|---|
| `ota_progress` | `result`、`output.status`、`output.progress.percent`、`output.progress.step_key` |
| `ota_result` | `output.status`、`task_id`、`result`、`error_code`、`error_msg`、`target_version` |

需要确认：

| 问题 | 当前不明确点 |
|---|---|
| 平台是否真的要网关刷写机场/飞控固件 | 当前真实刷写依赖 `/usr/local/bin/flash_firmware.sh`，没有现场闭环 |
| `firmware_upgrade_status` 是否必须走 state | 当前主要靠 OTA 事件，属性更新多为 `notify=False` |
| 固件包字段是 `file_url/md5/file_size` 还是官方 `file.url/fingerprint/size` | 当前兼容多种字段，但要对齐平台 |

## 15. Config：配置

| method | 平台字段 | 当前动作 | 回包 | 主动事件 |
|---|---|---|---|---|
| `config_update` | `config/configs/settings/items` | 写本地配置缓存；如果 key 是可写属性才写物模型；只读属性失败 | `output.status`、`config_revision`、`applied_keys`、`failed_keys`、`failed` | `config_update_result` |
| `config_get` | `keys/config_keys/config_names/config` | 读本地配置缓存 | `output.status=ok`、`config_revision`、`config`、`total` | 无 |
| `config_rollback` | 无 | 回滚上一版本本地配置 | `output.status=ok`、`config_revision`、`rolled_back_to_revision`、`config` | `config_rollback_result` |
| `config` | 兼容入口 | 有配置内容则 update，否则 get | 视情况 | 视情况 |

需要确认：

| 问题 | 当前不明确点 |
|---|---|
| 平台会下发哪些 config key | 当前未知 key 会当本地业务配置保存 |
| 哪些 config key 应映射到 DOCK3 属性 | 当前只有 3 个可写属性能进物模型 |
| 回滚是否平台需要 | 当前有本地历史，最多保留 20 版 |

## 16. Cmd / Remote Debug：机场控制

官方 DOCK3 Cmd 页面中多数是平台通过 `services` 下发，网关回 `services_reply`，任务类动作还会上报同 method 的进度事件。

### 16.1 当前有真实/半真实链路的机场动作

| method | 当前动作 | 成功后状态/事件 |
|---|---|---|
| `cover_open` | 通过机巢协议下发开盖 | 回 `output.status=ok`，发 `events.cover_open`；机巢状态回来后 `cover_state` 进 OSD |
| `cover_close` | 通过机巢协议下发关盖 | 同上 |
| `cover_force_close` | 先检查 `drone_in_dock`，飞机在舱则拒绝 | 通过后同关盖 |
| `charge_open` | 通过机巢协议下发开始充电 | 回包 + 进度事件；充电状态进 OSD |
| `charge_close` | 通过机巢协议下发停止充电 | 同上 |
| `drone_open` | 通过机巢给飞机上电；子设备置在线 | 回包 + `events.drone_open` + `state/OSD sub_device`规则 + `update_topo` |
| `drone_close` | 通过机巢给飞机下电；子设备置离线 | 回包 + `events.drone_close` + `update_topo` |
| `supplement_light_open` | 通过机巢开补光灯 | 回包 + 进度事件；状态进 OSD |
| `supplement_light_close` | 通过机巢关补光灯 | 同上 |
| `debug_mode_open` | 本地进入远程调试模式，`mode_code=2` | 回包成功；`mode_code` 按 OSD 上报 |
| `debug_mode_close` | 本地退出远程调试模式，`mode_code=0` | 回包成功 |

### 16.2 当前明确拒绝的真实硬件能力

| method | 当前校验字段 | 当前返回 | 原因 |
|---|---|---|---|
| `sdr_workmode_switch` | `link_workmode`，兼容 `mode/action`，取值 0/1 | 失败 | 无真实无线链路 adapter |
| `sim_slot_switch` | `sim_slot/slot`，取值 0/1 | 失败 | 无真实 SIM/eSIM adapter |
| `esim_activate` | 当前不深校验 | 失败 | 无真实 SIM/eSIM adapter |
| `esim_operator_switch` | `operator` 非负 int | 失败 | 无真实 SIM/eSIM adapter |
| `battery_maintenance_switch` | `action=0/1` | 失败 | 无真实电池保养 adapter |
| `air_conditioner_mode_switch` | `action=0/1/2/3` | 失败 | 无真实空调 adapter |
| `alarm_state_switch` | `action=0/1` | 失败 | 无真实声光报警 adapter |
| `battery_store_mode_switch` | `action=1/2`，兼容 `action=0` 转 1 | 失败 | 无真实电池模式 adapter |
| `rtk_calibration` | `devices[]` 必须是数组 | 失败 | 无真实 RTK 标定 adapter |
| `drone_format` | 当前不深校验 | 失败 | 无真实飞机存储 adapter |
| `device_format` / `format_sd_card` | 需 `device.allow_storage_format=true` | 默认失败 | 防误删 |
| `device_reboot` | 需 `device.allow_system_reboot=true` | 默认失败 | 防联调机被重启 |

会议要讲清楚：这些不是漏接 method，而是代码故意不做假成功。平台若需要验收成功，必须提供真实 adapter/设备能力，或双方约定模拟成功口径。

## 17. 平台可能会问的问题：直接回答表

| 平台可能问 | 当前代码回答 | 证据位置 | 需不需要会议确认 |
|---|---|---|---|
| 机场 `state` 下直播信息没有推吗 | `live_status` 会在直播动作后推；`live_capacity` 不会启动主动推 | `src/media/manager.py::_publish_live_status_event()`、`src/device/manager.py::_refresh_payload_properties(notify=False)` | 要确认 `live_capacity` 是否必须主动推 |
| 启动后有哪些上报 | `device_online/device_info_put/heartbeat`、条件组织请求、条件 `update_topo`、机场 OSD、运行时长 state、云连接成功能力集 | `src/main.py::_on_mqtt_connected_for_startup_sync()`、`_setup_callbacks()` | 要确认平台是否消费非官方事件 |
| `update_topo` 会什么时候发 | 已绑定启动、绑定成功、绑定状态变化、飞机开关机、平台同步拓扑后 | `src/device/manager.py::report_update_topo()`、`handle_update_topo()` | 要确认产品枚举/密钥/nonce |
| 哪些属性平台能设置 | 只有 `air_transfer_enable/silent_mode/user_experience_improvement` | `src/cloud_api/dock3_properties.py` | 基本不用确认 |
| 平台设置 `cover_state` 为什么失败 | `cover_state` 是只读 OSD 属性，应该通过 `cover_open/cover_close` 控制 | `validate_property_set()` | 不用确认，按官方 accessMode |
| DRC enter 应该发哪里 | `services`，不是 `drc/down` | `src/mqtt/client.py::_is_service_reply_method()` | 不用确认 |
| `stick_control` 为什么有回包 | 当前通用 MQTT 路由会回 `drc/up` | `src/mqtt/client.py::_publish_reply_for_downstream_topic()` | 要确认是否改成无回包 |
| DRC 心跳 seq 放哪里 | 当前读 `data.seq`；官方示例有顶层 `seq` | `src/cloud_api/dock3_drc.py::validate_drc_heartbeat()` | 要确认并可能补 |
| 航线文件会自动从平台 URL 下载吗 | 当前不下载 http/https/oss/s3，只解析本地或 `file://` | `src/cloud_api/dock3_wayline.py::parse_wayline_file()` | 必须确认 |
| 航线完成后媒体数有吗 | 当前 `media_count` 多为 0 | `src/mission/manager.py` 进度 ext | 要确认是否强依赖 |
| 日志上传是不是完整 OSS | 当前 HTTP PUT/mock，不是完整云厂商 SDK | `src/modules/log_manager.py` | 必须确认 |
| OTA 是否真实刷写 | mock/simulate 可跑；真实依赖下载、校验、刷写脚本 | `src/device/manager.py::handle_ota_create()` | 必须确认 |
| 空调/eSIM/SDR/声光报警为什么失败 | 没有真实 adapter，代码明确拒绝 | `src/device/manager.py` 对应 handler | 要确认验收口径 |

## 18. 当前完成项

| 分组 | 当前能说已完成的部分 |
|---|---|
| MQTT 基础链路 | services、property/set、drc/down、requests_reply、status_reply 的订阅和回包路由 |
| Topic 防错 | `property_set`、`drc_mode_enter/exit`、`takeoff_to_point` 有 topic 校验 |
| Properties | DOCK3 顶层属性表、OSD/state 分流、3 个可写属性校验和回包 |
| Device | `update_topo` 构造、严格字段校验、状态回包处理 |
| Organization | 绑定状态查询、组织查询、组织绑定 requests 链路 |
| Live | 开始、停止、画质、镜头、相机切换、能力查询；`live_status` 动作后 state/event |
| Media | 本地媒体列表、删除、下载、优先级、上传完成/进度事件 |
| Wayline | prepare/execute/pause/resume/return/undo/stop/progress_get、断点、本地航线解析、飞控/机巢执行入口 |
| DRC | enter/exit、心跳、杆量、飞控速度、指点飞行、一键起飞、紧急停止入口 |
| HMS | 告警缓存、机巢告警同步、查询、清除 |
| Log | 日志列表、上传开始、取消/暂停/恢复、进度事件 |
| Firmware | OTA 创建、取消、模拟进度、真实下载/校验/刷写脚本入口 |
| Config | 更新、查询、回滚、配置结果事件 |
| Cmd | 开盖/关盖/充电/飞机上下电/补光灯/调试模式；无真实 adapter 的能力明确拒绝 |

## 19. 当前不明确、需要和平台逐项确认的问题

建议会议直接按下面顺序问。

| 优先级 | 问题 | 当前项目为什么需要确认 |
|---|---|---|
| P0 | 平台是否要求启动后主动推 `state.property.live_capacity` | 当前没有推；这是今天平台已问到的直播 state 类问题 |
| P0 | 平台判断直播状态看 `state.live_status`、`events.live_status_notify`，还是两个都要 | 当前两个都会发，但触发场景不同 |
| P0 | `live_camera_change` 是否也要求刷新 `live_status` | 当前只回包，不主动发直播状态 |
| P0 | `update_topo` 的真实 `domain/type/sub_type/device_secret/nonce/thing_version` | 当前有默认/模拟值，生产要平台确认 |
| P0 | 组织绑定由网关启动主动发，还是平台预绑定后网关只查状态 | 当前配置会自动绑定 |
| P0 | 航线文件 URL 是否需要网关下载 | 当前不下载远程 URL |
| P0 | DRC `stick_control` 是否严格无回包 | 当前有回包，可能和官方 DOCK3 页面不一致 |
| P0 | DRC `heart_beat.seq` 放顶层还是 `data` 内 | 当前只读 data 内 |
| P1 | DRC `drone_control` 回包是否必须 `output.seq` | 当前回 status/simulated |
| P1 | 平台是否仍看 `drc_status_notify` | 官方提示该事件不再维护，当前代码仍发 |
| P1 | `drc_state` 平台看 OSD 还是 state | 当前代码属性表是 OSD |
| P1 | 媒体/日志上传的 endpoint、凭证、签名格式 | 当前 HTTP PUT/mock 模型 |
| P1 | 航线最终完成是否强依赖 `media_count/track_id` | 当前不完整 |
| P1 | HMS 的 code/hms_key 字典 | 当前有本地映射和自定义码 |
| P1 | OTA 是真实刷写还是只看状态流转 | 当前真实刷写没有现场闭环 |
| P1 | 空调、声光报警、电池模式、SDR、eSIM、RTK 是否要模拟成功 | 当前默认明确失败 |

## 20. 明天会议建议顺序

1. 先对 topic：确认平台按 `services/property/set/drc/down/requests/status` 哪些入口测。
2. 对启动：确认平台希望“上电即看到”的内容，尤其 `update_topo`、OSD、`live_capacity`。
3. 对直播：先讲清楚 `live_status` 有、`live_capacity` 不主动推，再确认是否要补。
4. 对拓扑和组织：确认产品枚举、密钥、nonce、绑定流程由谁发起。
5. 对 DRC：确认 `stick_control` 无回包、心跳 seq、`drc_state` 口径。
6. 对航线：确认航线文件下载、任务完成事件、`media_count/track_id`。
7. 对媒体和日志：确认对象存储凭证和上传方式。
8. 对真机能力：确认哪些必须真机成功，哪些可以暂时返回不支持。
