# 真实链路启动说明（EMQX MQTT）

本文面向“无 DJI 云端 MQTT 客户端、由 EMQX 接收/转发 MQTT 消息”的部署口径。

当前网关仍连接 `config/gateway.yaml` 中的 `nanomq.host/port`。这里的 `nanomq` 可以指向本机 NanoMQ、Mosquitto 或生产 EMQX；`cloud.*` 不作为当前启动链路的 MQTT 连接配置。

## 生产模式原则

生产模式不再自动生成模拟拓扑数据：

- 不允许使用 `EDGEGW001`、`DRONESIM001`、`DRONE-SIM-*` 等测试 SN。
- `update_topo` 所需的 `device_secret` 和 `nonce` 必须来自配置或启动参数。
- 子设备如果需要在启动时预置，也必须显式提供 `sn/domain/type/sub_type/index/device_secret/nonce/thing_version`。
- 如果真实链路暂时无法提供某字段，可以由启动参数提供占位的真实部署值，但必须由部署方维护，不能由代码自动拼接。

## 最小启动命令

仅上报网关自身拓扑：

```bash
python src/main.py \
  -c config/gateway.yaml \
  --runtime-mode production \
  --gateway-sn REALDOCK001 \
  --gateway-device-secret "$GATEWAY_DEVICE_SECRET" \
  --gateway-nonce "$GATEWAY_NONCE" \
  --mission-completion-enabled true \
  --mission-completion-retry-count 3 \
  --mission-completion-retry-interval 2.0
```

带一个真实子设备：

```bash
python src/main.py \
  -c config/gateway.yaml \
  --runtime-mode production \
  --gateway-sn REALDOCK001 \
  --gateway-device-secret "$GATEWAY_DEVICE_SECRET" \
  --gateway-nonce "$GATEWAY_NONCE" \
  --sub-device-json '{"sn":"AIRCRAFT001","domain":"0","type":60,"sub_type":0,"index":"A","device_secret":"AIRCRAFT_SECRET","nonce":"AIRCRAFT_NONCE","thing_version":"1.1.2","online":true}'
```

## 配置方式

也可以写入配置文件：

```yaml
runtime:
  mode: "production"

gateway:
  sn: "REALDOCK001"

nanomq:
  host: "10.0.0.10"
  port: 1883
  username: "edge_gateway"
  password: "********"

device:
  topology:
    device_secret: "GATEWAY_DEVICE_SECRET"
    nonce: "GATEWAY_NONCE"
  sub_devices:
    - sn: "AIRCRAFT001"
      domain: "0"
      type: 60
      sub_type: 0
      index: "A"
      device_secret: "AIRCRAFT_SECRET"
      nonce: "AIRCRAFT_NONCE"
      thing_version: "1.1.2"
      online: true
```

## 启动失败排查

生产模式启动前会执行预检。出现以下报错时按提示补字段：

- `gateway.sn looks like a test identity`：换成真实网关 SN。
- `device.topology.device_secret is required`：通过配置或 `--gateway-device-secret` 提供。
- `device.topology.nonce is required`：通过配置或 `--gateway-nonce` 提供。
- `device.sub_devices[i].device_secret/nonce is required`：子设备启动预置缺少真实拓扑元数据。

## MQTT Topic

生产模式不改变 Topic：

- 下行服务：`thing/product/{sn}/services`
- 属性设置：`thing/product/{sn}/property/set`
- 设备请求：`thing/product/{sn}/requests`
- 设备请求回包：`thing/product/{sn}/requests_reply`
- 拓扑上报：`sys/product/{sn}/status`
- 拓扑回包：`sys/product/{sn}/status_reply`

EMQX 侧只需要按上述 topic 接收/转发，不需要 DJI 云端 MQTT 客户端参数。

## 直播与媒体字段

直播按 Dock3 官方方法名接入，兼容旧测试方法名但生产建议只发官方方法名：

- `live_start_push`：`url_type`、`url`、`video_id`、`video_quality`
- `live_stop_push`：`video_id`
- `live_camera_change`：`video_id`、`camera_position`
- `live_lens_change`：`video_type`
- `live_set_quality`：`video_id`、`video_quality`

生产模式会拒绝 `mock://` 直播地址；真实直播源必须在 `media.video_sources` 配置为 `/dev/video*`、`rtsp://`、`http://` 或 `https://`。如果真实机场链路暂时只能由外部推流服务提供，请把 `live_start_push.url` 填为真实 RTMP、GB28181 或 WebRTC WHIP 参数，不要使用代码内置测试源。

媒体按 Dock3 官方字段接入：

- `highest_priority_upload_flighttask_media` 事件：`flight_id`
- `upload_flighttask_media_prioritize` 服务：`flight_id`
- `file_upload_callback` 事件：`file.object_key/path/name/ext/metadata`
- `storage_config_get` 请求回包：`output.bucket`、`output.credentials.access_key_id`、`output.credentials.access_key_secret`、`output.credentials.expire`、`output.credentials.security_token`、`output.endpoint`、`output.provider`、`output.region`、`output.object_key_prefix`

`storage_config_get` 不会生成模拟 AK/SK。生产环境需要通过 `media.oss` 配置真实对象存储信息，或由上游云端通过 `requests_reply` 返回真实临时凭证。

## 航线字段

Dock3 航线管理按官方 `wayline.html` 方法名接入：

- 事件：`return_home_info`、`flighttask_ready`、`flighttask_progress`、`device_exit_homing_notify`、`in_flight_wayline_progress`
- 服务：`flighttask_execute`、`return_specific_home`、`flighttask_stop`、`return_home_cancel`、`return_home`、`flighttask_recovery`、`flighttask_pause`、`flighttask_undo`、`flighttask_prepare`、`flighttask_create`
- 请求：`flighttask_progress_get`、`flighttask_resource_get`
- 空中航线服务：`in_flight_wayline_deliver`、`in_flight_wayline_stop`、`in_flight_wayline_recover`、`in_flight_wayline_cancel`

真实链路中 `flighttask_prepare` 会拒绝 `simulate_mission.is_enable=1`，并要求航线文件可被真实下载/解析并上传到飞控；没有飞控或航线 adapter 时返回明确错误，不再返回模拟成功。`in_flight_wayline_*` 已按官方字段校验，但当前没有真实空中航线 adapter，生产环境会返回“不支持”错误，避免云端误判为空中航线已执行。

`return_specific_home` 需要多机场 home 点规划能力；当前单机场链路只允许返回本网关 SN，目标为其他机场时会明确拒绝。蛙跳任务需要后续接入多机场拓扑、图传链路和返航规划 adapter 后再打开。

航线末航点完成后，网关默认执行真实闭环：`one_click_return` 返航、`land` 降落、机巢 `CLAMP_TIGHTEN/DOOR_CLOSE/CHARGE_START` 收舱充电。任一真实 adapter 缺失或命令重试后仍失败时，`flighttask_progress` 会上报 `failed`，不会把测试环境的缺设备状态伪装成任务完成。

可通过配置调整重试和闭环动作：

```yaml
mission:
  completion:
    enabled: true
    auto_return_home: true
    auto_land: true
    nest_recovery: true
    retry_count: 3
    retry_interval_seconds: 2.0
```

测试环境如果暂时只验证协议字段，可把 `mission.completion.enabled=false` 写入测试专用配置；真实接入验收建议保持默认开启，并确认飞控 adapter 与机巢 MAVLink adapter 都已连通。

一键启航 `one_key_takeoff_flow` 也使用同一套重试参数。`auto_complete=false` 时流程会停在真实起飞/航线执行中，等待实际航线进度；`auto_complete=true` 只适合已有真实飞控与机巢链路的联调，它会继续执行返航、降落、收舱、充电闭环。

## HMS 字段

Dock3 HMS 官方页只有设备上报事件 `hms`，Topic 为 `thing/product/{gateway_sn}/events`。事件 `data.list` 最多 20 条，每条告警包含以下官方字段：

- `level`：告警等级，`0=通知`、`1=提醒`、`2=警告`
- `module`：事件模块，`0=飞行任务`、`1=设备管理`、`2=媒体`、`3=hms`
- `in_the_sky`：是否飞行，`0=在地上`、`1=在天上`
- `code`：告警码
- `device_type`：设备类型，格式 `{domain-type-subtype}`
- `imminent`：是否及时性告警，`0=否`、`1=是`
- `args.component_index`、`args.sensor_index`：用于 HMS 文案变量

项目内 `hms_get/hms_clear` 是现有查询/清除服务入口，不属于该 HMS 官方页的下行服务。生产模式禁止通过 `hms_get` 的 `simulate/inject_alarm` 字段注入告警；真实 HMS 只能来自机巢/飞控/设备侧故障源或显式的业务上报。
