# 网页端 MAVLink 调试工具

这里放网页调试台相关的 MAVLink 小工具。代码按协议映射文档中的模块拆分。

当前分两条链路：

```text
飞控标准 MAVLink：telemetry/
机巢自定义 MAVLink：nest/
```

## 标准遥测映射

标准 MAVLink 遥测映射放在：

```text
web/mavlink/telemetry/mapping.py
```

它负责把常见飞控消息转换成统一遥测键，再转换成 OSD 数据和 Dock3 properties：

```text
ATTITUDE / VFR_HUD / WIND
GLOBAL_POSITION_INT / GPS_RAW_INT / HEARTBEAT
TERRAIN_REPORT / SYS_STATUS / BATTERY_STATUS
```

主要输出函数：

```text
apply_mavlink_message()
build_osd_data()
build_dock3_properties_from_telemetry()
```

Dock3 properties 对齐字段：

```text
longitude / latitude / height / mode_code / electricity
drone_charge_state / position_state / wind_speed
```

## 机巢属性映射

自定义机巢状态映射放在：

```text
web/mavlink/nest/property_mapping.py
```

它负责把 `DRONE_NEST_RESPONSE` 转换成 Dock3 properties：

```text
cover_state / putter_state / drone_charge_state
emergency_stop_state / working_voltage / working_current
humidity / temperature / environment_temperature / wind_speed / rainfall
supplement_light_state / air_conditioner / alarm_state / drone_in_dock
```

## 物模型属性数据来源映射

物模型属性数据来源总入口放在：

```text
web/mavlink/properties/mapping.py
```

它按文档第四部分聚合三类来源：

```text
MAVLink 遥测：GLOBAL_POSITION_INT / HEARTBEAT / SYS_STATUS / GPS_RAW_INT / WIND
机巢协议：DRONE_NEST_RESPONSE
内部维护：firmware / OTA / mission / database / DRC / runtime
```

主要输出函数：

```text
build_properties_from_sources()
build_properties_from_internal_state()
```

## 相机协议映射

相机命令和媒体元数据映射放在：

```text
web/mavlink/camera/mapping.py
```

覆盖文档第七部分：

```text
camera_photo_take / start_photo / stop_photo / start_record / stop_record
set_camera_mode / set_exposure_mode / zoom / focus / resolution / encoding
media metadata
```

主要输出函数：

```text
build_camera_command()
build_camera_setting()
build_media_metadata()
```

## 云台协议映射

云台 MAVLink 命令映射放在：

```text
web/mavlink/gimbal/mapping.py
```

覆盖文档第八部分：

```text
gimbalRotate / gimbal_reset / set_gimbal_mode / camera_look_at
```

主要输出函数：

```text
build_gimbal_command()
```

## ViewLink SDK 映射

ViewLink SDK 调用映射放在：

```text
web/mavlink/viewlink/mapping.py
```

覆盖文档第九部分：

```text
initialize / connect / disconnect / move / home / turn_to
zoom_in / zoom_out / start_track / take_photo / start_recording
```

主要输出函数：

```text
build_viewlink_call()
```

## 机巢模拟

终端 1：

```bash
python web/mavlink/nest/simulator.py --port 14551
```

建议地址是 `127.0.0.1:14551`，把飞控 SITL 的 `14550` 留给 ArduPilot。

## 接入主网关

默认配置不会启用独立机巢连接。需要主网关直接连接机巢模拟器时，打开：

```yaml
nest_mavlink:
  enabled: true
  connection:
    type: "udp"
    host: "127.0.0.1"
    port: 14551
```

同时跑飞控 SITL 和机巢模拟器时，飞控和机巢不要使用同一个端口。例如：

```yaml
mavlink:
  connection:
    type: "udp"
    host: "127.0.0.1"
    port: 14550

nest_mavlink:
  enabled: true
  connection:
    type: "udp"
    host: "127.0.0.1"
    port: 14551
```

对应启动机巢模拟器：

```bash
python web/mavlink/nest/simulator.py --port 14551
```

## 文件职责

| 路径 | 作用 |
|---|---|
| `telemetry/mapping.py` | 标准 MAVLink 消息到遥测数据、OSD、Dock3 properties 的映射 |
| `nest/protocol.py` | 机巢方言导入路径、命令表、位域常量 |
| `nest/property_mapping.py` | 机巢 DRONE_NEST_RESPONSE 到 Dock3 properties 的映射 |
| `properties/mapping.py` | 文档第四部分物模型属性数据来源聚合映射 |
| `camera/mapping.py` | 文档第七部分相机 MAVLink 命令、参数、媒体元数据映射 |
| `gimbal/mapping.py` | 文档第八部分云台 MAVLink 命令映射 |
| `viewlink/mapping.py` | 文档第九部分 ViewLink SDK 调用映射 |
| `nest/simulator.py` | 机巢/PLC 模拟端，接收 12003，发送 12004/12005 |

## 边界

不要把飞控命令和机巢命令混在一起：

```text
飞控命令：ARM / TAKEOFF / LAND / RTL，走标准 MAVLink
机巢命令：开门 / 关门 / 充电 / 夹紧，走自定义 MAVLink 12003
```
