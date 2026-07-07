# 接口设计说明书

本文档面向人员1、人员2、人员3，定义当前 Edge Gateway 按 DJI Dock 3 上云 API 迁移时的 P0 接口契约。当前目标是在 Ubuntu 服务器内纯模拟跑通链路，不接真实 Dock 3、无人机、飞控、机巢或负载设备。

本说明书只把当前已联调链路写成详细规范。当前 243 个已注册 method 的完整清单以 `docs/METHOD_COVERAGE_LEDGER.md` 和 `DJI_CLOUD_API_INTERFACES.md` 为准，客户需求相关 Dock3 迁移进度以 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` 为准。后续每完成一组客户需求相关 Dock3 校验，再把该组接口补充到本文。

## 1. 协议总览

| 项目 | 当前约定 |
|---|---|
| 协议目标 | DJI Dock 3 上云 API |
| MQTT Broker | 本地 Mosquitto/NanoMQ 兼容 broker，默认 `127.0.0.1:1883` |
| 网关 SN | `EDGE-GW-001`，来自 `config/gateway.yaml` |
| 下行主 topic | `thing/product/{sn}/services`、`thing/product/{sn}/property/set`、`thing/product/{sn}/drc/down` |
| 上行主 topic | `services_reply`、`property/set_reply`、`osd`、`state`、`events`、`drc/up` |
| 错误码基准 | DJI 上云 API 官方错误码，见 `docs/71.error-code.md` 和 https://developer.dji.com/doc/cloud-api-tutorial/cn/error-code.html |
| 当前代码入口 | `src/main.py` 注册 method，`src/mqtt/client.py` 负责 topic 分流和回包 |

通用下行消息信封：

```json
{
  "tid": "string",
  "bid": "string",
  "timestamp": 1783314794544,
  "gateway": "EDGE-GW-001",
  "method": "property_get",
  "need_reply": 1,
  "data": {}
}
```

通用服务回包信封：

```json
{
  "bid": "string",
  "data": {
    "result": 0
  },
  "gateway": "EDGE-GW-001",
  "tid": "string",
  "timestamp": 1783314794546,
  "method": "property_get",
  "msg": ""
}
```

## 2. Topic 与 API 定义

| API/数据流 | 下行 topic | 上行 topic | 方向 | 当前状态 |
|---|---|---|---|---|
| `property_set` | `thing/product/{sn}/property/set` | `thing/product/{sn}/property/set_reply` | 云端 -> 网关 -> 云端 | P0 Ubuntu 已验证，继续补全 Dock3 属性矩阵 |
| `property_get` | `thing/product/{sn}/services` | `thing/product/{sn}/services_reply` | 云端 -> 网关 -> 云端 | P0 Ubuntu 已验证 |
| `drc_mode_enter` | `thing/product/{sn}/services` | `thing/product/{sn}/services_reply` | 云端 -> 网关 -> 云端 | P0 Ubuntu 已验证，`drc_state=2` 已同步 |
| `drc_mode_exit` | `thing/product/{sn}/services` | `thing/product/{sn}/services_reply` | 云端 -> 网关 -> 云端 | P0 Ubuntu 已验证，`drc_state=0` 已同步 |
| DRC 控制数据 | `thing/product/{sn}/drc/down` | `thing/product/{sn}/drc/up` | 双向 | 建链后控制数据通道；`stick_control/drone_control/payload_control/drc_speed_push/drc_gimbal_speed_push` 已有批量测试工具；P0 总批量工具可一次覆盖 |
| `dock_status_query` | `thing/product/{sn}/services` | `thing/product/{sn}/services_reply` | 云端 -> 网关 -> 云端 | 链路可用，字段仍待 Dock3 精校 |
| `airport_bind_status` | 无 | `thing/product/{sn}/requests` / `thing/product/{sn}/requests_reply` | 网关 -> 云端 -> 网关 | Dock3 官方 organization 路径；已通过 Ubuntu 复测 |
| `osd` | 无 | `thing/product/{device_sn}/osd` | 网关 -> 云端 | 已持续上报 SITL 遥测 |
| `state` | 无 | `thing/product/{device_sn}/state` | 网关 -> 云端 | 已上报属性和飞控状态变化 |
| `events` | 无 | `thing/product/{sn}/events` | 网关 -> 云端 | 已上报 heartbeat、DRC 状态等事件 |

### 2.1 `property_set`

请求：

```json
{
  "method": "property_set",
  "data": {
    "silent_mode": 1
  }
}
```

回包：

```json
{
  "method": "property_set",
  "data": {
    "silent_mode": {
      "state": {
        "result": 0
      }
    }
  },
  "msg": ""
}
```

规则：

| 项目 | 约定 |
|---|---|
| 正确 topic | `thing/product/{sn}/property/set` |
| 错误 topic | 返回 DJI 错误码，不返回负数 |
| 空 data | 返回 `data={}`，表示没有属性被修改 |
| 兼容格式 | 当前代码兼容旧 `{properties:{...}}`，但 Dock3 新增字段应优先按扁平属性格式实现 |
| Dock3 可写属性 | 当前按官方 properties 页收敛为 `air_transfer_enable`、`silent_mode`、`user_experience_improvement` |
| 历史字段 | `air_conditioner_mode`、`battery_store_mode`、`alarm_state`、`remaining_power_for_return_home` 等不再允许通过 `property/set` 直接写，相关动作走 services method |
| 状态上报 | 属性变更后通过 `thing/product/{sn}/state` 上报 `{"property": {"silent_mode": 1}}` |

### 2.2 `property_get`

请求：

```json
{
  "method": "property_get",
  "data": {
    "properties": ["silent_mode", "drc_state", "air_conditioner_mode", "remaining_power_for_return_home"]
  }
}
```

回包：

```json
{
  "method": "property_get",
  "data": {
    "result": 0,
    "silent_mode": 1,
    "drc_state": 0,
    "air_conditioner_mode": 0,
    "remaining_power_for_return_home": 30
  },
  "msg": ""
}
```

### 2.3 `drc_mode_enter` / `drc_mode_exit`

`drc_mode_enter` 和 `drc_mode_exit` 是 Dock3 services 方法，不能发送到 `drc/down`。

请求：

```json
{
  "method": "drc_mode_enter",
  "data": {}
}
```

回包：

```json
{
  "method": "drc_mode_enter",
  "data": {
    "result": 0
  },
  "msg": ""
}
```

状态：

| 动作 | 期望状态 |
|---|---|
| `drc_mode_enter` 成功 | `property_get {"properties":["drc_state"]}` 返回 `drc_state=2` |
| `drc_mode_exit` 成功 | `property_get {"properties":["drc_state"]}` 返回 `drc_state=0` |
| 误发到 `drc/down` | 返回 `325001`，`msg` 说明 method 必须发到 `services` |

### 2.3.1 DRC 控制数据

DRC 建链后，控制数据通过：

```text
thing/product/{gateway_sn}/drc/down
thing/product/{gateway_sn}/drc/up
```

当前批量工具覆盖以下 DRC 控制 method。2026-07-06 之后新增 `src/cloud_api/dock3_drc.py`，开始按 Dock3 官方 DRC 页校验 `drc_mode_enter`、`stick_control` 和 `drone_control` 的字段和值域。

| Method | 当前 payload | 当前处理 |
|---|---|---|
| `stick_control` | 官方 `roll/pitch/throttle/yaw/gimbal_pitch`，范围 `364..1684`；兼容旧 `vx/vy/vz/yaw_rate` | 官方杆量映射为内部速度模型；非法杆量返回 `325001` |
| `drone_control` | 官方废弃字段 `seq/x/y/h/w`；兼容旧 `horizontal_speed/horizontal_direction/vertical_speed/vertical_direction/yaw` | 官方字段做范围校验后转换为 MAVLink 机体系速度控制；非法字段返回 `325001` |
| `payload_control` | `payload_index/action/params` | 分发到相机/云台模拟入口；无真实负载时返回模拟成功 |
| `drc_speed_push` | `vx/vy/vz/yaw_rate` | MAVLink 速度控制 |
| `drc_gimbal_speed_push` | `pitch_speed/yaw_speed/roll_speed` | 云台速度模拟入口 |

按大类验收 DRC 时优先使用专项脚本：

```bash
python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale
```

该脚本会验证：

1. `drc_mode_enter` 官方 `mqtt_broker/osd_frequency/hsi_frequency` payload。
2. enter 后 `property_get(drc_state)=2`。
3. 官方 `stick_control` 无回包行为，以及非法杆量返回 `325001`。
4. 官方废弃 `drone_control` 字段 `seq/x/y/h/w` 和非法值返回 `325001`。
5. `drc_gimbal_speed_push` 基础回包。
6. exit 后 `property_get(drc_state)=0`。

旧批量验收命令仍可用于兼容控制数据回归：

```bash
python tools/mqtt_drc_batch_test.py --timeout 12 --quiet-stale
```

### 2.4 CMD / 远程调试

Dock3 CMD/远程调试 method 统一通过：

```text
thing/product/{gateway_sn}/services
thing/product/{gateway_sn}/services_reply
```

回包基础结构：

```json
{
  "method": "cover_open",
  "data": {
    "result": 0,
    "output": {
      "status": "ok"
    }
  }
}
```

任务类命令还会通过 `thing/product/{gateway_sn}/events` 上报同名进度事件：

```json
{
  "method": "cover_open",
  "data": {
    "result": 0,
    "output": {
      "status": "ok",
      "progress": {
        "percent": 100,
        "step_key": "open_cover"
      }
    }
  }
}
```

本轮已实现并已通过 Ubuntu 专项复测的 CMD method：

| Method | 请求体 | 回包/状态 | 当前说明 |
|---|---|---|---|
| `debug_mode_open` | `{}` | `output.status=ok`，`mode_code=2` | 远程调试开启 |
| `debug_mode_close` | `{}` | `output.status=ok`，`mode_code=0` | 远程调试关闭 |
| `cover_open/cover_close` | `{}` | 回包 + 进度事件；联动 `cover_state` | 无机巢模块时默认模拟状态更新 |
| `charge_open/charge_close` | `{}` | 回包 + 进度事件；联动 `charging_state` | 保留 MAVLink 机巢发送路径 |
| `putter_open/putter_close` | `{}` | 回包 + 进度事件；联动 `putter_state` | 保留 MAVLink 机巢发送路径 |
| `cover_force_close` | `{}` | 飞行器在舱内时 `result=314000`、`output.status=rejected` | 避免危险状态下强关舱盖 |
| `supplement_light_open/close` | `{}` | 联动 `supplement_light_state` | 服务器模拟返回成功 |
| `battery_maintenance_switch` | `{"action":0/1}` | 非法值 `325001` | 电池保养开关 |
| `air_conditioner_mode_switch` | `{"action":0/1/2/3}` | 非法值 `325001`，联动 `air_conditioner_mode` | 空调模式 |
| `alarm_state_switch` | `{"action":0/1}` | 非法值 `325001`，联动 `alarm_state` | 声光报警 |
| `battery_store_mode_switch` | `{"action":1/2}` | 非法值 `325001`，联动 `battery_store_mode` | 电池运行模式 |
| `sdr_workmode_switch` | `{"link_workmode":0/1}` | 非法值 `325001`，联动 `wireless_link.link_workmode` | 兼容旧字段 `mode` |
| `drone_open/drone_close` | `{}` | 回包 + 进度事件；触发 `update_topo` | 表示飞行器电源/在线状态，不改变 `drone_in_dock` |
| `drone_format/device_format/format_sd_card/device_reboot/device_reset` | `{}` | 回包 + 进度事件 | `device_format/format_sd_card` 默认模拟，不删除媒体目录；`device_reboot` 默认模拟，不真实重启服务器 |

专项验收命令：

```bash
python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 cmd test ok (...)`。2026-07-06 Ubuntu 已验证成功，实际输出 `dock3 cmd test ok (accepted=55, events=13, ignored_mismatched=0, ignored_legacy=55, ignored_late=0, ignored_total=55)`。

### 2.5 Wayline / 航线管理

Dock3 Wayline P1 基础闭环统一通过：

```text
thing/product/{gateway_sn}/services
thing/product/{gateway_sn}/services_reply
thing/product/{gateway_sn}/events
```

P1 阶段只覆盖服务器纯模拟链路，不下载真实 KMZ，不要求真实飞控完成航线。请求中 `simulate_mission.is_enable=1` 时，网关只验证上云 API 状态机和事件链路，不强制真实 MAVLink 模式切换。

基础回包结构：

```json
{
  "method": "flighttask_execute",
  "data": {
    "result": 0,
    "output": {
      "status": "in_progress",
      "progress": {
        "current_step": 23,
        "percent": 0
      },
      "ext": {
        "flight_id": "dock3-wayline-001",
        "current_waypoint_index": 0,
        "wayline_mission_state": 6,
        "media_count": 0,
        "track_id": "",
        "wayline_id": 0
      }
    }
  }
}
```

本轮已实现并通过 Ubuntu 专项复测的 Wayline method：

| Method | 请求体 | 回包/状态 | 当前说明 |
|---|---|---|---|
| `flighttask_prepare` | `flight_id`、`task_type`、`execute_time`、`file`、`simulate_mission` 等 | `output.status=sent`，事件 `flighttask_progress` | 接受旧别名 `flighttask_id`，新测试使用官方 `flight_id` |
| `flighttask_execute` | `{"flight_id":"..."}` | `output.status=in_progress`，事件 `flighttask_progress` | 纯模拟时不强制真实飞控切 AUTO |
| `flighttask_progress` | `{"flight_id":"..."}` | 当前 `output.status/progress/ext` | 查询当前任务状态 |
| `flighttask_pause` | `{}` | `output.status=paused`，事件 `flighttask_progress` | 非运行状态返回 `314000` 和 `output.status=rejected` |
| `flighttask_resume` / `flighttask_recovery` | `{}` | `output.status=in_progress`，事件 `flighttask_progress` | 从暂停状态恢复 |
| `flighttask_return` | `{"flight_id":"...","return_type":0/1}` | `output.status=in_progress`，事件 `flighttask_progress` 和 `return_home_info` | `return_type=0` 返航，`1` 降落 |
| `return_home_cancel` | `{}` | `output.status=paused`，事件 `flighttask_progress` | 当前先模拟悬停状态 |
| `flighttask_undo` | `{"flight_ids":["..."]}` | `output.status=canceled`，事件 `flighttask_progress` | 支持单任务取消 |

验收命令：

```bash
python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 wayline test ok (...)`。2026-07-06 Ubuntu 已验证成功，实际输出 `dock3 wayline test ok (accepted=11, events=8, ignored_mismatched=0, ignored_legacy=11, ignored_late=0, ignored_total=11)`。

专项验收命令：

```bash
python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 wayline test ok (...)`。

P0 总批量验收命令：

```bash
python tools/mqtt_dock3_p0_batch_test.py --timeout 12 --quiet-stale
```

总批量脚本会一次覆盖 `dock_status_query`、properties Batch 1、只读属性拒绝、DRC enter/control/exit 和错误 topic。2026-07-06 Ubuntu 已验证成功，成功标志为 `dock3 p0 batch test ok (...)`。如果总批量脚本失败，再使用 `mqtt_drc_batch_test.py` 或 `mqtt_smoke_test.py` 做单项定位。

按大类验收 properties 时使用专项脚本：

```bash
python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale
```

该脚本只验证 Dock3 properties 大类：官方可写属性 `silent_mode`、`air_transfer_enable`、`user_experience_improvement`，只读属性 `battery_store_mode`、`alarm_state`、`drc_state`，非法值和未知属性，以及 `property_get` 回读。成功标志为 `dock3 properties test ok (...)`。

按大类验收 device/topology 时使用专项脚本：

```bash
python tools/mqtt_dock3_device_test.py --timeout 12
```

该脚本验证 `update_topo` 的官方 status 链路：网关发布 `sys/product/{sn}/status`，云端回复 `sys/product/{sn}/status_reply`；同时覆盖在线子设备、离线子设备和无效拓扑拒绝。成功标志为 `dock3 device topology test ok (...)`。

按大类验收 OSD/state 时使用专项脚本：

```bash
python tools/mqtt_dock3_osd_state_test.py --timeout 15
```

该脚本启动后会先通过 `update_topo` 把默认子设备 `DRONESIM001` 注册到网关内存拓扑，除非显式加 `--skip-topo`；随后监听 `thing/product/EDGEGW001/osd`、`thing/product/DRONESIM001/osd` 和 `thing/product/EDGEGW001/state`，并主动下发一次 `property_set silent_mode` 触发状态变化。验收重点是：网关 SN 的 OSD 必须是 Dock3 机场物模型结构，不能混用飞行器 `battery/attitude` 裸遥测；飞行器 SN 的 OSD 必须包含经纬度、高度、姿态、速度、电池、GPS、飞行时间；`state` 必须能按 Dock3 property 结构上报属性变化。成功标志为 `dock3 osd/state test ok`。

脚本会按 `tid + method` 精确匹配回包。如果同一 `tid` 先收到旧格式回包，例如 `method=null`、`data={"silent_mode":-1}` 或 `data.result=-2`，脚本会统计为 `ignored_mismatched` 或 `ignored_legacy` 并继续等待正确回包。出现非零统计时，应检查本地 broker 上是否有旧网关、Web 调试台、模拟器或 broker 规则在抢答。清理环境后可加 `--fail-on-stale`，把旧格式重复回包作为失败。

`airport_bind_status` 不属于 P0 总批量必过项。Dock3 官方 organization 验收必须走 `requests -> requests_reply` 云端模拟器流程；services 兼容路径可用 `python tools/mqtt_dock3_p0_batch_test.py --timeout 12 --include-legacy-bind-status` 单独附加验证。

该命令会自动执行：

1. 尝试 `drc_mode_exit` 清理旧状态。
2. `drc_mode_enter` 并回读 `property_get(drc_state)=2`。
3. 依次发送 `stick_control`、`drone_control`、`payload_control`、`drc_speed_push`、`drc_gimbal_speed_push` 到 `drc/down` 并等待 `drc/up result=0`。
4. `drc_mode_exit` 并回读 `property_get(drc_state)=0`。

说明：`tools/mqtt_dock3_drc_test.py` 是 DRC 大类专项入口；`tools/mqtt_drc_batch_test.py` 保留为旧兼容控制数据回归入口。

### 2.4 `dock_status_query`

请求：

```json
{
  "method": "dock_status_query",
  "data": {}
}
```

回包字段分为 `dock_status` 和 `drone_status`。当前 `drone_status` 已从实时遥测快照取值，可用于链路验收；字段名、枚举和单位仍需继续按 Dock3 文档精校。

### 2.5 `airport_bind_status`

Dock3 官方 organization 中，`airport_bind_status` 是设备主动请求云端，不是云端下发 services。当前 services handler 只保留为本地兼容 smoke，最终验收必须走 `requests -> requests_reply`。

网关上行请求 topic：

```text
thing/product/{gateway_sn}/requests
```

请求：

```json
{
  "method": "airport_bind_status",
  "data": {
    "devices": [
      {
        "sn": "EDGE-GW-001"
      }
    ]
  }
}
```

云端响应 topic：

```text
thing/product/{gateway_sn}/requests_reply
```

回包：

```json
{
  "method": "airport_bind_status",
  "data": {
    "result": 0,
    "output": {
      "bind_status": []
    }
  }
}
```

当前项目实现：

| 项目 | 说明 |
|---|---|
| 请求发起 | `DeviceManager.auto_query_bind_status_on_startup()` 启动时调用 `CloudConnectionManager.send_request()` |
| 云端模拟 | `tools/mqtt_cloud_simulator.py` 订阅 `requests` 并回 `requests_reply` |
| 回包处理 | `CloudConnectionManager.handle_requests_reply()` 匹配 `tid`，再交给 `DeviceManager._handle_airport_bind_status_reply()` |
| 回环保护 | 网关收到本地 broker 回环的 `/requests` 只记录日志，不路由到下行业务 handler |
| 兼容 smoke | `python tools/mqtt_smoke_test.py --method airport_bind_status --timeout 8` 仍可测 services 兼容链路，但不作为 Dock3 最终验收 |
| Ubuntu 验证 | 2026-07-06 已验证：`requests`、`requests_reply`、`tid` 匹配、回环保护、`Bind status reply processed` 均正常 |

### 2.6 `airport_organization_get` / `airport_organization_bind`

这两个接口同属 Dock3 organization 的设备主动上行请求，topic 仍为：

```text
thing/product/{gateway_sn}/requests
thing/product/{gateway_sn}/requests_reply
```

`airport_organization_get` 请求：

```json
{
  "method": "airport_organization_get",
  "data": {
    "device_binding_code": "SIM-BIND-CODE",
    "organization_id": "org_sim_001"
  }
}
```

`airport_organization_get` 回包：

```json
{
  "method": "airport_organization_get",
  "data": {
    "result": 0,
    "output": {
      "organization_name": "模拟组织"
    }
  }
}
```

`airport_organization_bind` 请求：

```json
{
  "method": "airport_organization_bind",
  "data": {
    "bind_devices": [
      {
        "sn": "EDGE-GW-001",
        "device_binding_code": "SIM-BIND-CODE",
        "organization_id": "org_sim_001",
        "device_callsign": "EDGE-GW-001",
        "device_model_key": "dock3-sim"
      }
    ]
  }
}
```

`airport_organization_bind` 回包：

```json
{
  "method": "airport_organization_bind",
  "data": {
    "result": 0,
    "output": {
      "err_infos": []
    }
  }
}
```

当前项目测试工具：

| 工具 | 作用 |
|---|---|
| `tools/mqtt_cloud_simulator.py --max-requests 2` | 模拟云端接收两个 organization requests 并返回 `requests_reply` |
| `tools/mqtt_organization_test.py --action full` | 通过项目 `DeviceManager + CloudConnectionManager` 顺序发起 `airport_organization_get` 和 `airport_organization_bind` |

Ubuntu 验收命令：

```bash
# 终端A
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --max-requests 2 --timeout 120 \
  --organization-id org_sim_001 --organization-name 模拟组织 --device-callsign EDGE-GW-001

# 终端B
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_organization_test.py --action full --organization-id org_sim_001 \
  --binding-code SIM-BIND-CODE --device-callsign EDGE-GW-001 --device-model-key dock3-sim --timeout 20
```

说明：该工具用于验证 organization requests 逻辑。为避免正在运行的主网关同时收到测试工具的 `requests_reply` 并记录 `Unexpected/late requests_reply`，建议测试前先停止主网关，或忽略该类旁路日志。

Ubuntu 验证记录：

| 日期 | Method | 事务 ID | 结果 |
|---|---|---|---|
| 2026-07-06 | `airport_organization_get` | `9a738feb-97a8-4851-a797-02d17ab06343` | `requests_reply result=0`，返回 `organization_name=模拟组织` |
| 2026-07-06 | `airport_organization_bind` | `8a0b1414-9853-4632-a172-913a207eff3f` | `requests_reply result=0`，返回 `output.err_infos=[]`，测试工具输出 `organization request test ok` |
| 2026-07-06 | `airport_organization_get` 失败分支 | `b3816e96-c8f9-4b8e-b1be-c2f235f80d03` | `requests_reply result=327000`，顶层 `msg=绑定码无效`，测试工具输出 `organization request test ok` |
| 2026-07-06 | `airport_organization_bind` 部分失败 | `d2bdf5d1-402f-45d1-82eb-da316c82e09e` | `requests_reply result=0`，`output.err_infos[0].err_code=327000`，测试工具输出 `organization request test ok` |
| 2026-07-06 | `airport_organization_get` 超时分支 | `8e21e5fc-3e64-4a30-8268-4763cc7f751f` | 按 `--expect-timeout --timeout 2` 返回预期超时，测试工具输出 `organization request test ok` |
| 2026-07-06 | 绑定后事件与持久化 | get `5bb01523-3be5-4d36-93fd-b713fadffd38`，bind `9e38069d-5c64-4dfe-ba99-fe4808b90cf2` | 捕获 `device_binding`、`update_topo`；内存绑定 `organization_id=org_sim_001`；SQLite `device_binding` 记录 `is_bound=true`、`workspace_id=org_sim_001`、`workspace_name=模拟组织` |

绑定后状态验收能力：

| 验收项 | 测试工具参数 | 当前状态 |
|---|---|---|
| 绑定事件 | `--expect-event device_binding` | 已通过 Ubuntu 复测 |
| 拓扑事件 | `--expect-event update_topo` | 已通过 Ubuntu 复测 |
| 内存绑定状态 | `--expect-bound` | 已通过 Ubuntu 复测 |
| SQLite 持久化 | `--verify-db --verify-timeout 8` | 已通过 Ubuntu 复测 |

异常分支测试能力：

| 场景 | 云端模拟器参数 | 测试工具参数 | 当前状态 |
|---|---|---|---|
| 组织查询失败 | `--organization-get-result 327000 --organization-get-message 绑定码无效` | `--action get --expect-get-result 327000` | 已通过 Ubuntu 复测 |
| 绑定部分失败 | `--organization-bind-fail-sn EDGE-GW-001 --organization-bind-err-code 327000` | `--action bind --expect-bind-error-count 1` | 已通过 Ubuntu 复测 |
| 云端回包超时 | `--reply-delay 5` | `--action get --expect-timeout --timeout 2` | 已通过 Ubuntu 复测 |

### 2.7 `update_topo`

`update_topo` 属于 Dock3 device topology 上报链路，使用系统状态 topic：

```text
sys/product/{gateway_sn}/status
sys/product/{gateway_sn}/status_reply
```

当前项目在组织绑定成功后会生成 `update_topo` 事件。工具链已补齐事件发布和云端确认能力，基础拓扑和多子设备 `sub_devices` 均已通过 Ubuntu 复测。

`update_topo` 当前上报结构：

```json
{
  "method": "update_topo",
  "data": {
    "domain": "3",
    "type": 119,
    "sub_type": 0,
    "device_secret": "",
    "nonce": "",
    "thing_version": "1.1.2",
    "sub_devices": []
  }
}
```

云端确认回包：

```json
{
  "method": "update_topo",
  "data": {
    "result": 0
  }
}
```

验收命令：

```bash
# 终端A：云端同时处理 organization requests 和 update_topo status
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --enable-status --max-requests 3 --timeout 120 \
  --organization-id org_sim_001 --organization-name 模拟组织 --device-callsign EDGE-GW-001

# 终端B：发起绑定，发布生成的 update_topo，并等待 status_reply
python tools/mqtt_organization_test.py --action full --organization-id org_sim_001 \
  --binding-code SIM-BIND-CODE --device-callsign EDGE-GW-001 --device-model-key dock3-sim \
  --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo \
  --expect-bound --verify-db --publish-events --expect-status-reply --verify-timeout 8 --timeout 20
```

Ubuntu 验证记录：

| 日期 | Method | 事务 ID | 结果 |
|---|---|---|---|
| 2026-07-06 | `airport_organization_get` | `3ab6639d-c94d-4c5f-9a5c-9589f8f810c7` | `requests_reply result=0`，返回 `organization_name=模拟组织` |
| 2026-07-06 | `airport_organization_bind` | `7a7f871f-68a7-483e-b179-3a608c5b1b15` | `requests_reply result=0`，返回 `output.err_infos=[]`，随后产生 `device_binding` 和 `update_topo` |
| 2026-07-06 | `update_topo` | `f0845cda-bf5b-40b2-a0f7-d2e4cbb65c6b` | 发布到 `sys/product/EDGE-GW-001/status`，收到 `sys/product/EDGE-GW-001/status_reply result=0` |

说明：本次 SQLite `device_binding.bind_time=1783323731288` 为已有绑定记录的时间，当前复测证明绑定状态仍可读且 `update_topo` 的 status/status_reply 链路闭环成功。

多子设备拓扑复测命令：

```bash
# 终端A：云端模拟器同时校验收到的 sub_devices 数量
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --enable-status --max-requests 3 --timeout 120 \
  --organization-id org_sim_001 --organization-name 模拟组织 --device-callsign EDGE-GW-001 \
  --expect-status-sub-device-count 1 --expect-status-sub-device-sn DRONE-SIM-001 \
  --validate-status-topo-schema

# 终端B：注入一个在线模拟子设备，再发布 update_topo 并等待 status_reply
python tools/mqtt_organization_test.py --action full --organization-id org_sim_001 \
  --binding-code SIM-BIND-CODE --device-callsign EDGE-GW-001 --device-model-key dock3-sim \
  --sub-device-json '{"sn":"DRONE-SIM-001","domain":"0","type":0,"sub_type":0,"index":"A","device_secret":"sim-secret","nonce":"sim-nonce","thing_version":"1.1.2","online":true,"paired":true}' \
  --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo \
  --expect-topo-sub-device-count 1 --expect-topo-sub-device-sn DRONE-SIM-001 \
  --validate-topo-schema --expect-bound --verify-db --publish-events \
  --expect-status-reply --verify-timeout 8 --timeout 20
```

多子设备拓扑 Ubuntu 验证记录：

| 日期 | Method | 事务 ID | 结果 |
|---|---|---|---|
| 2026-07-06 | `airport_organization_get` | `cee4606d-7f6d-41dd-86f6-bd7f6191d570` | `requests_reply result=0`，返回 `organization_name=模拟组织` |
| 2026-07-06 | `airport_organization_bind` | `352a71ac-133c-426f-b5a5-6f1f3f05a19a` | `requests_reply result=0`，返回 `output.err_infos=[]` |
| 2026-07-06 | `update_topo` | `f318713c-767a-43d9-a4b2-13ade3a969b2` | 发布到 `sys/product/EDGE-GW-001/status`，`sub_devices[0].sn=DRONE-SIM-001`，收到 `status_reply result=0` |

说明：这里的 `domain/type/sub_type` 仍是模拟值，用于验证 `sub_devices[]` 链路和数据结构。官方枚举需要继续对照 Dock3 device 文档后再固定。

## 3. 协议字段映射表

| 当前字段 | 中文名 | 类型 | 单位/枚举 | 当前来源 | 当前去向 | Dock3 状态 |
|---|---|---|---|---|---|---|
| `tid` | 事务 ID | string | UUID/string | MQTT 请求或本地生成 | 所有 reply/event/request | P0 已使用 |
| `bid` | 业务 ID | string | UUID/string | MQTT 请求或本地生成 | 所有 reply/event/request | P0 已使用 |
| `timestamp` | 时间戳 | int | ms | 本地系统时间 | 所有 MQTT 消息 | P0 已使用 |
| `gateway` | 网关序列号 | string | SN | `config/gateway.yaml` | 所有 MQTT 消息 | P0 已使用 |
| `method` | 方法名 | string | Dock3 method | MQTT 请求或业务模块 | 所有 reply/event/request | 待逐项校验 |
| `need_reply` | 是否需要回包 | int | `1` 表示需要 | MQTT 下行 | MQTTClient 判断是否回包 | P0 已使用 |
| `data.result` | 结果码 | int | DJI 错误码，成功为 `0` | handler / 错误码归一层 | `services_reply`、`drc/up` | P0 已归一 |
| `msg` | 错误/结果描述 | string | 中文/英文描述 | handler / 错误码归一层 | reply | P0 已使用 |
| `data.devices[].sn` | 设备序列号 | string | SN | `DeviceManager.query_bind_status_from_cloud()` | `requests.airport_bind_status` | Batch 2 Ubuntu 已验证 |
| `data.output.bind_status[]` | 绑定状态列表 | array | - | 云端或 `tools/mqtt_cloud_simulator.py` | `requests_reply.airport_bind_status` | Batch 2 Ubuntu 已验证 |
| `is_device_bind_organization` | 是否绑定组织 | bool | `true/false` | 云端或云端模拟器 | DeviceManager 组织绑定缓存 | Batch 2 Ubuntu 已验证 |
| `organization_id` | 组织 ID | string | - | 云端或云端模拟器 | DeviceManager 组织绑定缓存 | Batch 2 Ubuntu 已验证 |
| `organization_name` | 组织名称 | string | - | 云端或云端模拟器 | DeviceManager 组织绑定缓存 | Batch 2 Ubuntu 已验证 |
| `device_callsign` | 设备名称/呼号 | string | - | 云端或云端模拟器 | DeviceManager 设备绑定信息 | Batch 2 Ubuntu 已验证 |
| `device_binding_code` | 设备绑定码 | string | - | 测试工具/云端输入 | `requests.airport_organization_get/bind` | Batch 2.1 Ubuntu 已验证 |
| `bind_devices[]` | 绑定设备列表 | array | - | `DeviceManager.bind_to_organization()` | `requests.airport_organization_bind` | Batch 2.1 Ubuntu 已验证 |
| `device_model_key` | 设备型号 Key | string | Dock3 设备型号标识 | 测试工具/云端输入 | `requests.airport_organization_bind` | Batch 2.1 Ubuntu 已验证 |
| `data.output.err_infos[]` | 绑定失败信息 | array | - | 云端或云端模拟器 | `requests_reply.airport_organization_bind` | Batch 2.1 Ubuntu 已验证 |
| `domain` | 设备域 | string | Dock3 设备域枚举 | `DeviceManager.build_topo_payload()` | `status.update_topo` | Batch 4 Ubuntu 基础链路已验证，待官方字段精校 |
| `type` | 设备类型 | int | Dock3 设备类型枚举 | `DeviceManager.build_topo_payload()` | `status.update_topo` | Batch 4 Ubuntu 基础链路已验证，待官方字段精校 |
| `sub_type` | 设备子类型 | int | Dock3 设备子类型枚举 | `DeviceManager.build_topo_payload()` | `status.update_topo` | Batch 4 Ubuntu 基础链路已验证，待官方字段精校 |
| `device_secret` | 设备密钥 | string | - | `DeviceManager.build_topo_payload()` | `status.update_topo` | device/topology 专项待复测，当前模拟可配置 |
| `nonce` | 随机数 | string | - | `DeviceManager.build_topo_payload()` | `status.update_topo` | device/topology 专项待复测，当前模拟可配置 |
| `thing_version` | 物模型版本 | string | semantic version | `DeviceManager.build_topo_payload()` | `status.update_topo` | device/topology 专项待复测，当前默认 `1.1.2` |
| `sub_devices[]` | 子设备列表 | array | 官方在线非空、离线为空 | `DeviceManager` 子设备缓存 | `status.update_topo` | device/topology 专项待复测，覆盖在线/离线/必填字段 |
| `air_transfer_enable` | 空中回传 | bool | `false/true` | DeviceManager 属性缓存 | `property_set`、`property_get`、`state` | 官方 `rw`，properties 专项已通过；state 由 OSD/state 专项覆盖 |
| `silent_mode` | 静音模式 | int | `0/1` | DeviceManager 属性缓存 | `property_set`、`property_get`、`state` | 官方 `rw`，properties 专项已通过；OSD/state 专项会主动触发并验证 state |
| `drc_state` | DRC 链路状态 | int | `0` 关闭，`2` 已进入 | DrcController -> DeviceManager | `property_get`、`state/events` | P0 已跑通 |
| `user_experience_improvement` | 用户体验改善计划 | int | `0/1/2` | DeviceManager 属性缓存 | `property_set`、`property_get`、`state` | 官方 `rw`，properties 专项已通过；state 仍按属性变化统一验证 |
| `battery_store_mode` | 电池运行模式 | int | `1/2` | DeviceManager 属性缓存 | `property_get`、`property_set` 拒绝 | 官方 `r`，property/set 返回 `state.result=1` |
| `alarm_state` | 机场声光报警状态 | int | `0/1` | DeviceManager 属性缓存 | `property_get`、`property_set` 拒绝 | 官方 `r`，property/set 返回 `state.result=1` |
| `air_conditioner_mode` | 空调模式 | int | 当前模拟值 | DeviceManager 属性缓存 | `property_get`、`dock_status_query` | 历史模拟字段，property/set 只读，待 Dock3 其他分组精校 |
| `remaining_power_for_return_home` | 返航预留电量 | int | percent | DeviceManager 属性缓存 | `property_get` | 历史配置字段，property/set 只读，待 Dock3 其他分组精校 |
| `latitude` | 纬度 | float | degree | SITL/MAVLink telemetry | `osd`、`dock_status_query.drone_status` | P0 已跑通，待 Dock3 精校 |
| `longitude` | 经度 | float | degree | SITL/MAVLink telemetry | `osd`、`dock_status_query.drone_status` | P0 已跑通，待 Dock3 精校 |
| `height` | 相对高度 | float | m | SITL/MAVLink telemetry | `osd`、`dock_status_query.drone_status` | P0 已跑通 |
| `altitude` | 海拔高度 | float | m | SITL/MAVLink telemetry | `osd`、`dock_status_query.drone_status` | P0 已跑通 |
| `horizontal_speed` | 水平速度 | float | m/s | SITL/MAVLink telemetry | `osd` | P0 已跑通 |
| `vertical_speed` | 垂直速度 | float | m/s | SITL/MAVLink telemetry | `osd`、`dock_status_query.drone_status` | P0 已跑通 |
| `attitude_pitch` | 俯仰角 | float | degree | SITL/MAVLink telemetry | `osd` | P0 已跑通 |
| `attitude_roll` | 横滚角 | float | degree | SITL/MAVLink telemetry | `osd` | P0 已跑通 |
| `attitude_head` | 航向角 | float | degree | SITL/MAVLink telemetry | `osd` | P0 已跑通 |
| `battery.capacity_percent` | 电量百分比 | int | percent | SITL/MAVLink battery | `osd`、`state`、`dock_status_query.drone_status` | P0 已跑通 |
| `battery.voltage` | 电压 | int | mV | SITL/MAVLink battery | `osd` | P0 已跑通 |
| `position_state.gps_number` | GPS 星数 | int | count | SITL/MAVLink GPS | `osd` | P0 已跑通 |
| `position_state.is_fixed` | 是否定位 | int | `0/1` | SITL/MAVLink GPS | `osd` | P0 已跑通 |
| `cover_state` | 舱盖状态 | int | 当前模拟枚举 | Dock/Nest 模拟状态 | `dock_status_query.dock_status` | 待 Dock3 精校 |
| `charging_state` | 充电状态 | int | 当前模拟枚举 | Dock/Nest 模拟状态 | `dock_status_query.dock_status` | 待 Dock3 精校 |
| `drone_in_dock` | 无人机在舱 | int | `0/1` | Dock/Nest 模拟状态 | `dock_status_query.dock_status` | 待 Dock3 精校 |

日志中关键字段应以 `字段名(中文名)` 输出，例如 `capacity_percent(电量百分比)`、`longitude(经度)`、`drc_state(DRC链路状态)`。

### 3.1 Dock3 Properties Batch 1 迁移记录

| 字段 | 官方访问模式 | 当前 property/set | 当前 services | 验收命令 |
|---|---|---|---|---|
| `air_transfer_enable` | `rw` | 已验证，`state.result=0` | 不涉及 | `python tools/mqtt_smoke_test.py --method property_set --payload '{"air_transfer_enable":true}' --timeout 8` |
| `silent_mode` | `rw` | 已验证 | 不涉及 | `python tools/mqtt_smoke_test.py --method property_set --payload '{"silent_mode":1}' --timeout 8` |
| `user_experience_improvement` | `rw` | 已验证，`state.result=0` | 不涉及 | `python tools/mqtt_smoke_test.py --method property_set --payload '{"user_experience_improvement":2}' --timeout 8` |
| `battery_store_mode` | `r` | 已验证，`state.result=1`，不直接修改 | `battery_store_mode_switch` 保留 | `python tools/mqtt_smoke_test.py --method property_set --payload '{"battery_store_mode":2}' --timeout 8` |
| `alarm_state` | `r` | 已验证，`state.result=1`，不直接修改 | `alarm_state_switch` 保留 | `python tools/mqtt_smoke_test.py --method property_set --payload '{"alarm_state":1}' --timeout 8` |

## 4. 错误码说明

当前代码新增 `src/utils/dji_error_codes.py`，在 MQTT 回包和事件发布边界把旧内部负数错误码归一为 DJI 官方错误码。业务 handler 后续可以逐步迁移到直接返回官方码，但不得再通过 MQTT 回包或事件暴露 `-1/-2/-3`。

| 内部场景 | 旧内部码 | DJI 错误码 | 中文描述 | 使用位置 |
|---|---:|---:|---|---|
| 成功 | `0` | `0` | 成功 | 所有 reply |
| handler 异常 | `-1` | `319999` | 机场系统运行异常，请重启机场后重试 | `services_reply`、`drc/up` |
| 未知 method | `-2` | `314000` | 设备当前无法支持该操作，建议检查设备当前工作状态 | MQTTClient 无 handler |
| topic / payload 格式错误 | `-3` | `325001` | 云端下发命令不符合格式要求，设备无法执行 | method 发错 topic |
| 设备端请求超时 | `-1` in timeout context | `325004` | 设备端命令请求已超时，请重试 | 后续超时处理 |
| 属性设置失败 | `-1/-2` in property context | `327000` | 参数设置失败，请稍后重试 | `property/set_reply` |
| 当前忙碌/限流 | `-2` in generic context | `514116` | 无法执行当前操作，机场正在执行其他控制指令，请稍后重试 | DRC/任务限流类回包 |

`property/set_reply` 的逐属性 `state.result` 可以保留 Dock3 允许的 `0/1/2`，但不得返回负数；需要精确表达失败原因时使用官方错误码。

## 5. 243 Method 索引状态

| 分组 | 数量 | 当前说明 |
|---|---:|---|
| Device | 51 | 已路由，P0 总批量覆盖 `property_set`、`property_get`；`airport_bind_status` 由 organization 云端模拟器单独覆盖；device topology 和 CMD/远程调试均已通过 Ubuntu 专项复测 |
| Mission / Wayline | 42 | 已路由；Wayline P1 `flighttask_prepare/execute/progress/pause/resume/return/undo/return_home_cancel` 已完成代码收敛并通过 Ubuntu 专项复测，旧 `load_wayline/execute_task/wayline_task_*` 仍待 Dock3 精校 |
| Payload | 93 | 已路由，多数为模拟/占位，包含 ViewLink 调试入口，待负载协议补齐 |
| Media / Live | 13 | 已路由，待 Dock3 `media.html`、`live.html` 校验 |
| DRC | 24 | P0 覆盖 `drc_mode_enter`、`drc_mode_exit`；`tools/mqtt_dock3_drc_test.py` 已通过 Ubuntu 专项复测，覆盖官方 DRC 字段和值域；旧批量工具覆盖兼容控制数据 |
| Dock | 8 | P0 覆盖 `dock_status_query`；新增 `mqtt_dock3_osd_state_test.py` 覆盖 Dock3 机场 OSD 基础结构，字段枚举仍待精校 |
| Log | 3 | 已路由，待 Dock3 远程日志文档校验 |
| FlySafe | 3 | 已路由，暂缓 |
| Flight Area | 2 | 已路由，待 `custom-fly-region.html` 校验 |
| Custom Data | 4 | 已路由，暂缓 |

## 6. P0 验收命令

在 Ubuntu 服务器启动 Mosquitto、SITL 和 Edge Gateway 后执行：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate

python tools/mqtt_dock3_p0_batch_test.py --timeout 12 --quiet-stale
```

成功标志：`dock3 p0 batch test ok (accepted=..., ignored_total=...)`。其中 `ignored_total` 表示测试期间观测到但已忽略的旧格式重复回包数量，不影响本次 P0 正确回包验收；环境清理后应逐步降为 `0`。

如果总批量脚本失败，再执行单项命令定位：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate

python tools/mqtt_smoke_test.py --method property_set --payload '{"silent_mode":1}' --timeout 8
python tools/mqtt_smoke_test.py --method property_get --payload '{"properties":["silent_mode","drc_state"]}' --timeout 8
python tools/mqtt_smoke_test.py --method property_set --payload '{"air_transfer_enable":true}' --timeout 8
python tools/mqtt_smoke_test.py --method property_set --payload '{"user_experience_improvement":2}' --timeout 8
python tools/mqtt_smoke_test.py --method drc_mode_enter --payload '{}' --timeout 12
python tools/mqtt_smoke_test.py --method property_get --payload '{"properties":["drc_state"]}' --timeout 8
python tools/mqtt_smoke_test.py --method drc_mode_exit --payload '{}' --timeout 12
python tools/mqtt_smoke_test.py --method dock_status_query --timeout 8
python tools/mqtt_dock3_osd_state_test.py --timeout 15
```

`airport_bind_status` 官方 organization 路径验收需要云端模拟器配合。先开云端模拟器，再重启网关触发启动自动查询：

```bash
# 终端A
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --once --bind-status unbound --timeout 90

# 终端B
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

成功标志：终端A打印 `received <- thing/product/EDGE-GW-001/requests` 和 `published -> thing/product/EDGE-GW-001/requests_reply`；网关日志打印 `Request reply: method=airport_bind_status`。2026-07-06 已按该方式在 Ubuntu 上验证通过。

错误 topic 验收：

```bash
python tools/mqtt_smoke_test.py --channel drc --method drc_mode_enter --payload '{}' --timeout 8
```

期望：

```text
received <- thing/product/EDGE-GW-001/drc/up
"result": 325001
```

日志验收：

```bash
grep -E 'DJI error code normalized|result\\(结果码\\)|drc_state\\(DRC链路状态\\)|capacity_percent\\(电量百分比\\)' \
  logs/$(date +%Y-%m)/edge_gateway_$(date +%F).log
```
