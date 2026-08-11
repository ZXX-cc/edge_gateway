# Dock3 OSD/State 字段精校矩阵

## 1. 当前精校边界

本轮依据 DJI Dock3 Cloud API `properties`、topic 定义、当前 MAVLink
遥测和机巢 v1.3 协议完成**协议级精校**。当前没有真实 Dock3、真实飞行器、
真实负载和目标生产云 MQTT 样本，因此以下结论不能替代真机验收。

- 已确认：字段名称、基本类型、单位换算、公开枚举范围、OSD/state 分流。
- 模拟可验证：MAVLink/SITL -> aircraft OSD、机巢状态 -> dock OSD、属性变化 -> state。
- 待真机确认：不可用值、固件差异、字段拆包顺序、完整 payload、真实传感器精度和枚举切换过程。

截至 2026-07-13，Dock 3 顶层属性已形成单一代码目录：**50 项结构覆盖**，
包含现场保留的 `putter_state`；其中 `pushMode=0` OSD 38 项、`pushMode=1` state 12 项；官方 3 个 `rw` 属性均可在
纯模拟环境通过 `property/set -> state` 验证。此处“结构覆盖”不等于 50 项真机语义验收完成。

2026-07-13 Ubuntu 严格验收已在 `192.168.2.188:1883` 完成：38 项专项单测和 MQTT
属性专项均以退出码 0 结束，覆盖摘要为 `status=passed`、`strict_stale_total=0`，竞争回复、
旧格式回复和无关回复计数全部为 0。由此确认**纯模拟属性阶段完成**；下表标记的 17 项
真机确认字段仍需实体 Dock3/飞行器样本，不能由模拟值替代。

官方入口：

- <https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/properties.html>
- <https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/topic-definition.html>

## 2. Aircraft OSD

| Dock3 字段 | 当前来源 | 当前转换 | 当前结论 | 真机待办 |
|---|---|---|---|---|
| `latitude` | `GLOBAL_POSITION_INT.lat` | `1e-7 deg -> deg`，限制 `[-90,90]` | 已精校 | 核对无定位时实际值 |
| `longitude` | `GLOBAL_POSITION_INT.lon` | `1e-7 deg -> deg`，限制 `[-180,180]` | 已精校 | 核对无定位时实际值 |
| `height` | `relative_alt` | `mm -> m` | 暂定 | 核对 Dock3 aircraft `height` 高程基准 |
| `altitude` | `alt` | `mm -> m` | 暂定 | 核对海拔/椭球高定义 |
| `horizontal_speed` | `VFR_HUD.groundspeed` | `m/s`，负值归零 | 已精校 | 核对小数精度 |
| `vertical_speed` | `VFR_HUD.climb` | `m/s`，保留升降符号 | 已精校 | 核对上升正方向 |
| `course` | `VFR_HUD.heading` | 角度归一到 `[0,360)` | 已精校 | 核对磁航向/真航向 |
| `attitude_pitch/roll/head` | `ATTITUDE` | `rad -> deg`；航向归一到 `[0,360)` | 已精校 | 核对角度精度 |
| `battery.capacity_percent` | `SYS_STATUS/BATTERY_STATUS` | `0..100` | 已精校 | 核对多电池数组结构 |
| `battery.voltage` | MAVLink 电压 | `V -> mV` | 已精校 | 核对不可用哨兵值 |
| `battery.current` | MAVLink 电流 | `A -> mA`，未知/非正值不输出 | 暂定 | 核对充放电符号和异常值 |
| `position_state.is_fixed` | `GPS_RAW_INT.fix_type` | 未定位=`0`，3D fix=`2` | 部分精校 | 真机补 `1=收敛中`、`3=失败` 来源 |
| `position_state.quality` | `eph`/卫星数 | 限制 `0..5` | 部分精校 | 核对 Dock3 星级算法，`0` 当前表示未知 |
| `mode_code` | ArduPilot `custom_mode` | 通过飞行模式 adapter 映射 | 部分精校 | 按真实机型固件逐模式对照 |
| `gear` | MAVLink `armed` | `0/1` | 兼容实现 | 核对 Dock3 aircraft `gear` 的正式含义 |
| `flight_time` | 网关累计解锁时长 | 秒 | 暂定 | 核对飞控累计时间来源 |
| 云台/避障字段 | Gimbal/Distance MAVLink | 角度、厘米转换 | 兼容实现 | 按真实负载 product type/index 重构 payload |

## 3. Dock OSD

| Dock3 字段 | 当前来源 | 当前结论 | 真机待办 |
|---|---|---|---|
| `home_position_is_valid/heading` | SITL 坐标和航向 | 已补官方枚举、角度归一和模拟值 | 改接机场固定 Home 与标定航向 |
| `self_converge_coordinate` | 模拟阶段复用 SITL 坐标 | 官方结构已补齐 | 改接机场自收敛结果 |
| `rtcm_info` | RTK 模拟服务状态 | 未标定/自收敛结构可切换并走 state | 核对 NTRIP、手动标定与真机字段 |
| `wireless_link_topo/dongle_infos` | 模拟拓扑、无 Dongle 空数组 | 官方结构和 state 分流已补齐，不伪造硬件 ID | 接真实 SDR、4G Dongle、SIM/eSIM 数据 |
| `mode_code` | 机场任务/调试状态机 | 已限制为机场枚举 `0..5`；不再被 aircraft mode 覆盖 | 核对固件升级、待标定切换 |
| `cover_state` | 机巢 `gate_state` | 映射 `0..3` | 用真机开/关/半开/异常逐项确认 |
| `putter_state` | 机巢 `clamp_state` | 保留在机场 OSD，供平台侧观察推杆/夹紧机构实际变化 | 核对实体机巢夹紧机构业务语义 |
| `drone_in_dock` | 机巢无人机心跳/内部状态 | 推断值 | 必须真机确认检测源 |
| `drone_charge_state` | 飞控电量 + 机巢充电位 | 基础结构可用 | 核对断电、满电、无飞机状态 |
| `position_state` | 当前兼容使用飞控 GPS | 仅用于模拟链路 | 必须改接机场 RTK/定位数据 |
| `longitude/latitude/height` | 当前兼容使用飞控位置 | 仅用于模拟链路 | 必须改接机场固定坐标和椭球高 |
| `network_state/wireless_link` | 网关默认/内部状态 | 结构占位 | 必须接真实网卡、SDR/4G 数据 |
| `maintain_status` | 内部空数组 | 结构占位 | 必须接真实维护记录 |
| `backup_battery/storage` | 内部默认值 | 结构占位 | 必须接真实硬件/文件系统数据 |
| 温度、风、雨、电压、电流 | `DRONE_NEST_RESPONSE` | 已完成协议单位转换 | 真机核对量程、精度和异常哨兵 |

## 4. State 分流规则

- `osd` 承载 38 个 `pushMode=0` 定频属性，频率为 `0.5Hz`；
  `putter_state` 保留在机场 OSD，`electricity/wpmz_version` 等历史或其他设备域字段不再混入机场 OSD。
- OSD 默认按官方示例直接输出原始属性值，不再默认套用旧 Demo 的
  `{"value": ...}` 包装；兼容包装仅保留为显式内部选项。
- `state` 只承载真实属性变化；禁止上报内部键 `flight_mode`、`armed`、
  `connection_status`、`battery`、`gps_fixed`。
- `state` 只允许官方 12 个 `pushMode=1` 属性，`acc_time`、RTK、图传拓扑、
  固件和 3 个可写属性均按该目录路由。
- 飞行模式、电池、GPS、速度和姿态变化继续通过 aircraft OSD 表达。
- `silent_mode`、固件版本、升级状态和其他官方状态属性仍通过
  `data.property` 上报。
- gateway dock `mode_code` 与 aircraft `mode_code` 是两个设备物模型中的
  同名字段，禁止相互覆盖。

## 5. 纯模拟最大覆盖验收

一键验收命令：

```bash
bash scripts/dock3_property_acceptance.sh --timeout 20 --quiet-stale --fail-on-stale
```

脚本会完成三层验证：

1. 单元测试校验官方目录为 50 项、OSD/state 分组为 38/12。
2. MQTT `property_get` 对 50 项逐项检查存在性、顶层类型、枚举和值域。
3. 检查完整机场 OSD 无 state/历史字段泄漏，并依次触发 3 个 `rw` 属性的 state。

属性专项只订阅机场 OSD 和机场 state，不把 aircraft OSD 纳入属性门禁；aircraft OSD
由独立 OSD/SITL 专项负责。共享 Broker 对同一事务发布的第二份 reply 仍属于严格失败，
失败证据会分别记录 `current_competing_reply_total`、`legacy_competing_property_replies`
和 reply 样本，便于结合 Mosquitto 日志定位客户端来源。

本轮严格证据已确认上述计数全部为 0。此前共享 Broker 的竞争源经被动抓包定位为
`192.168.2.113`，停止该端旧模拟器/旧网关后无需重启当前网关即可通过验收；后续若再次
出现竞争回复，应先使用发布源审计脚本定位重复 `EDGEGW001` 客户端，再判断是否属于代码问题。

完整日志和 `dock3_property_coverage_summary.json` 会保存到同一个
`logs/acceptance_evidence/<时间>_dock3_properties/` 目录。JSON 将 17 个只能由
真机确认物理语义的字段单列，避免把模拟值当成真机证据。未接负载不影响机场 50 项
顶层属性结构验收，但相机、云台等 payload-index 属性不在本次完成范围内。

## 6. 真机对接验收清单

获得真机条件后，至少保存以下 MQTT 原始样本并逐字段回填本矩阵：

1. 待机、任务执行、返航、降落和异常状态。
2. 飞机在舱/舱外、充电/未充电、开盖/关盖/半开。
3. RTK 未开始、收敛中、成功、失败。
4. 有线、4G、SDR 连接和断开。
5. 正常电池、低电、无电池、传感器不可用。
6. 真实相机/负载 OSD，包含 product type、subtype 和 gimbal index。
7. 目标生产云对 OSD 多包顺序、缺省字段和异常哨兵的实际校验结果。

在以上证据完成前，项目状态统一标记为：**协议级已精校，真机字段待对接**。
