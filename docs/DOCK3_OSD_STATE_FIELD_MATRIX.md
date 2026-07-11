# Dock3 OSD/State 字段精校矩阵

## 1. 当前精校边界

本轮依据 DJI Dock3 Cloud API `properties`、topic 定义、当前 MAVLink
遥测和机巢 v1.3 协议完成**协议级精校**。当前没有真实 Dock3、真实飞行器、
真实负载和目标生产云 MQTT 样本，因此以下结论不能替代真机验收。

- 已确认：字段名称、基本类型、单位换算、公开枚举范围、OSD/state 分流。
- 模拟可验证：MAVLink/SITL -> aircraft OSD、机巢状态 -> dock OSD、属性变化 -> state。
- 待真机确认：不可用值、固件差异、字段拆包顺序、完整 payload、真实传感器精度和枚举切换过程。

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
| `mode_code` | 机场任务/调试状态机 | 已限制为机场枚举 `0..5`；不再被 aircraft mode 覆盖 | 核对固件升级、待标定切换 |
| `cover_state` | 机巢 `gate_state` | 映射 `0..3` | 用真机开/关/半开/异常逐项确认 |
| `putter_state` | 机巢 `clamp_state` | 映射 `0..3` | 核对自研夹紧机构与 Dock3 推杆语义 |
| `drone_in_dock` | 机巢无人机心跳/内部状态 | 推断值 | 必须真机确认检测源 |
| `drone_charge_state` | 飞控电量 + 机巢充电位 | 基础结构可用 | 核对断电、满电、无飞机状态 |
| `position_state` | 当前兼容使用飞控 GPS | 仅用于模拟链路 | 必须改接机场 RTK/定位数据 |
| `longitude/latitude/height` | 当前兼容使用飞控位置 | 仅用于模拟链路 | 必须改接机场固定坐标和椭球高 |
| `network_state/wireless_link` | 网关默认/内部状态 | 结构占位 | 必须接真实网卡、SDR/4G 数据 |
| `maintain_status` | 内部空数组 | 结构占位 | 必须接真实维护记录 |
| `backup_battery/storage` | 内部默认值 | 结构占位 | 必须接真实硬件/文件系统数据 |
| 温度、风、雨、电压、电流 | `DRONE_NEST_RESPONSE` | 已完成协议单位转换 | 真机核对量程、精度和异常哨兵 |

## 4. State 分流规则

- `osd` 只承载 `pushMode=0` 定频属性，频率为 `0.5Hz`。
- `state` 只承载真实属性变化；禁止上报内部键 `flight_mode`、`armed`、
  `connection_status`、`battery`、`gps_fixed`。
- 飞行模式、电池、GPS、速度和姿态变化继续通过 aircraft OSD 表达。
- `silent_mode`、固件版本、升级状态和其他官方状态属性仍通过
  `data.property` 上报。
- gateway dock `mode_code` 与 aircraft `mode_code` 是两个设备物模型中的
  同名字段，禁止相互覆盖。

## 5. 真机对接验收清单

获得真机条件后，至少保存以下 MQTT 原始样本并逐字段回填本矩阵：

1. 待机、任务执行、返航、降落和异常状态。
2. 飞机在舱/舱外、充电/未充电、开盖/关盖/半开。
3. RTK 未开始、收敛中、成功、失败。
4. 有线、4G、SDR 连接和断开。
5. 正常电池、低电、无电池、传感器不可用。
6. 真实相机/负载 OSD，包含 product type、subtype 和 gimbal index。
7. 目标生产云对 OSD 多包顺序、缺省字段和异常哨兵的实际校验结果。

在以上证据完成前，项目状态统一标记为：**协议级已精校，真机字段待对接**。
