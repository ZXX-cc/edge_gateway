# Dock3 全链路联调易错点

更新时间：2026-07-07

本文记录当前使用 MQTTX、机巢模拟器、边缘网关进行 Dock3 全链路联调时最容易踩坑的问题。当前联调口径以 `config/gateway.yaml` 为准。

## 1. SN 必须统一

当前统一使用：

| 角色 | SN |
|---|---|
| 网关/机场 | `EDGEGW001` |
| 飞行器 | `DRONESIM001` |

不要混用旧 SN：

```text
EDGE-GW-001
DOCK3-SIM-001
M3D-SIM-001
DOCK3SIM001
```

正确订阅 topic：

```text
thing/product/EDGEGW001/#
thing/product/DRONESIM001/#
sys/product/EDGEGW001/#
```

如果脚本里有默认值，建议这样写：

```python
def __init__(
    self,
    mqtt_host="127.0.0.1",
    mqtt_port=1883,
    gateway_sn="EDGEGW001",
    dock_sn="EDGEGW001",
    aircraft_sn="DRONESIM001",
):
    self.mqtt_host = mqtt_host
    self.mqtt_port = mqtt_port
    self.gateway_sn = gateway_sn
    self.dock_sn = dock_sn
    self.aircraft_sn = aircraft_sn
```

## 2. `status` 不是机巢初始状态

这个 topic：

```text
sys/product/EDGEGW001/status
```

主要用于：

```text
update_topo 拓扑上报
```

它不是机巢模拟器启动后自动发出的初始状态。机巢状态/OSD 要看：

```text
thing/product/EDGEGW001/osd
thing/product/EDGEGW001/state
```

飞行器遥测看：

```text
thing/product/DRONESIM001/osd
```

## 3. `update_topo` 不会发到机巢

`update_topo` 是设备拓扑链路：

```text
MQTT -> 网关 -> sys/product/EDGEGW001/status
```

它不会走 MAVLink，也不会被机巢模拟器收到。

如果要让机巢收到命令，需要发机巢动作类 method：

```text
cover_open
cover_close
charge_open
charge_close
putter_open
putter_close
supplement_light_open
supplement_light_close
drone_open
drone_close
```

## 4. 下发命令 topic 必须是 `services`

下发服务指令 topic：

```text
thing/product/EDGEGW001/services
```

开舱盖示例：

```json
{
  "bid": "mqttx-cover-open-001",
  "tid": "mqttx-cover-open-001",
  "timestamp": 1783421729815,
  "gateway": "EDGEGW001",
  "method": "cover_open",
  "need_reply": 1,
  "data": {}
}
```

成功时应该收到：

```text
thing/product/EDGEGW001/services_reply
```

## 5. OSD 字段不要用旧字段强校验

不要把旧字段作为强校验：

```text
charging_state
```

当前 Dock3 机场 OSD 更应该看：

```text
cover_state
putter_state
drone_in_dock
drone_charge_state
environment_temperature
wind_speed
rainfall
supplement_light_state
```

其中部分环境字段可能依赖模拟器/真实机巢是否上报，测试脚本不建议全部作为强制通过条件。

## 6. `property/set` 不能随便设置只读字段

不适合作为成功设置项的字段：

```text
alarm_state
drc_state
battery_store_mode
```

建议优先测试当前可写属性：

```text
silent_mode
air_transfer_enable
user_experience_improvement
```

示例：

```json
{
  "bid": "mqttx-property-set-001",
  "tid": "mqttx-property-set-001",
  "timestamp": 1783421729815,
  "gateway": "EDGEGW001",
  "method": "property_set",
  "data": {
    "silent_mode": 1
  }
}
```

## 7. MQTTX Web 和 Desktop 不一样

MQTTX Desktop 可以直接连普通 MQTT：

```text
mqtt://192.168.2.188:1883
```

MQTTX Web 不能直接连 `1883`，只能连 WebSocket：

```text
ws://host:port
```

当前推荐使用 MQTTX Desktop：

```text
Host: 192.168.2.188
Port: 1883
Protocol: mqtt://
SSL/TLS: 关闭
```

## 8. 命令链路日志看两个文件

网关侧：

```bash
cd ~/lxz/edge_gateway
tail -f logs/gateway/command_trace/$(date +%Y-%m)/command_trace_$(date +%F).log
```

机巢模拟器侧：

```bash
cd ~/lxz/edge_gateway
tail -f logs/nest_simulator/command_trace.log
```

判断链路通的典型顺序：

```text
[网关] 收到 MQTT 命令：舱盖打开
[网关] 下发机巢命令：舱盖打开
[网关] 发送 MAVLink 命令：舱盖打开
[机巢模拟器] 收到命令：舱门打开
[机巢模拟器] 发送回复：舱门打开，结果=成功
[网关] 收到机巢回复：舱盖打开，结果=成功
[网关] 回复 MQTT：舱盖打开
```

## 9. 没看到日志先检查目录和代码版本

必须在项目目录：

```bash
cd ~/lxz/edge_gateway
```

确认代码版本：

```bash
git log --oneline -5
```

命令链路日志文件只有在网关收到下行命令后才会创建。如果刚启动网关但没有发 `services` 指令，可能暂时没有 `command_trace` 文件。

## 10. 只测机巢动作不需要 ArduCopter

不需要主飞控/SITL 的测试：

```text
cover_open
cover_close
charge_open
charge_close
putter_open
putter_close
supplement_light_open
supplement_light_close
```

需要 SITL/主飞控的测试：

```text
起飞
航线任务
返航
飞行控制
DRC 飞控动作
```

## 11. 不要把页面在线当作唯一验收标准

没有 DJI Dock3 官方平台时，验收应看：

```text
MQTT 指令是否收到
网关是否转 MAVLink
机巢是否收到命令
机巢是否回包
MQTT 是否收到 services_reply/events/osd
```

页面只是展示壳子，不是链路是否通的唯一标准。

## 12. 推荐最小验证顺序

1. 启动 MQTT broker。
2. 启动机巢模拟器。
3. 启动边缘网关。
4. MQTTX 订阅：

```text
thing/product/EDGEGW001/#
thing/product/DRONESIM001/#
sys/product/EDGEGW001/#
```

5. MQTTX 发布 `cover_open` 到：

```text
thing/product/EDGEGW001/services
```

6. 同时观察：

```text
thing/product/EDGEGW001/services_reply
logs/gateway/command_trace/YYYY-MM/command_trace_YYYY-MM-DD.log
logs/nest_simulator/command_trace.log
```

如果能看到 `[机巢模拟器] 收到命令` 和 `[网关] 回复 MQTT` 且 `result=0`，说明 MQTT -> 网关 -> MAVLink 机巢 -> 网关 -> MQTT 回包链路已经通。
