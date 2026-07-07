# Drone Nest MAVLink 字段映射

本文件记录当前项目自定义机巢 MAVLink 方言的业务映射。来源文件是 `src/mavlink_definitions/drone_nest.xml`，生成代码是 `src/generated_dialect/drone_nest.py`。

## 消息总览

| MAVLink ID | 消息名 | 方向 | 业务含义 | 生成类 | CRC extra |
|---:|---|---|---|---|---:|
| 12003 | `DRONE_NEST_COMMAND` | 网关 -> 机巢 | 下发机巢动作命令 | `MAVLink_drone_nest_command_message` | 244 |
| 12004 | `DRONE_NEST_RESPONSE` | 机巢 -> 网关 | 周期上报机巢环境和状态 | `MAVLink_drone_nest_response_message` | 134 |
| 12005 | `DRONE_NEST_ORDER_RESPONSE` | 机巢 -> 网关 | 回复命令执行结果 | `MAVLink_drone_nest_order_response_message` | 70 |

## `DRONE_NEST_COMMAND` 字段

| 字段 | 类型 | 原始单位/编码 | 业务含义 | 当前模拟器映射 |
|---|---|---|---|---|
| `ordinary_order` | `uint16_t` | bit/枚举命令字 | 普通机巢动作命令 | 低 8 位：`0x01` 开舱门，`0x02` 关舱门，`0x03` 推杆推出，`0x04` 推杆收回，`0x05` 开始充电，`0x06` 停止充电 |
| `takeoff_land_order` | `uint8_t` | 枚举命令字 | 起降相关命令 | 当前模拟器接收但未细分处理 |

## `DRONE_NEST_RESPONSE` 字段

| 字段 | 类型 | 缩放 | 业务值 | 业务含义 |
|---|---|---:|---|---|
| `ac_temp` | `uint16_t` | `/10` | 摄氏度 | 空调温度 |
| `nest_temperature` | `uint16_t` | `/10` | 摄氏度 | 机巢内部温度 |
| `env_temperature` | `uint16_t` | `/10` | 摄氏度 | 外部环境温度 |
| `wind_direction` | `uint16_t` | `/10` | 度 | 风向 |
| `wind_speed` | `uint16_t` | `/10` | m/s | 风速 |
| `atmospheric_pressure` | `uint16_t` | `/10` | kPa | 大气压 |
| `rainfall` | `uint16_t` | `1` | mm/m² | 降雨量 |
| `charge_volt` | `uint16_t` | `/10` | V | 充电电压 |
| `charge_current` | `uint16_t` | `/10` | A | 充电电流 |
| `error_code` | `uint16_t` | `1` | 错误码 | 机巢故障码 |
| `boolstate1` | `uint16_t` | bitset | 位图 | 机巢布尔状态组 1；模拟器使用 bit4 表示充电中 |
| `boolstate2` | `uint16_t` | bitset | 位图 | 机巢布尔状态组 2 |
| `clamp_state` | `uint8_t` | enum | 状态码 | 推杆/夹持状态；模拟器 `1` 推出，`2` 收回 |
| `gate_state` | `uint8_t` | enum | 状态码 | 舱门状态；模拟器 `1` 打开，`2` 关闭 |
| `landing_light_state` | `uint8_t` | enum | 状态码 | 降落灯状态 |

## `DRONE_NEST_ORDER_RESPONSE` 字段

| 字段 | 类型 | 原始单位/编码 | 业务含义 |
|---|---|---|---|
| `ordinary_order_response` | `uint32_t` | bit/枚举结果 | 普通机巢命令执行结果，当前模拟器 `0` 表示成功 |
| `takeoff_land_order_response` | `uint32_t` | bit/枚举结果 | 起降命令执行结果，当前模拟器 `0` 表示成功 |

## 机巢状态到上云字段映射

当前转换入口：

| 入口 | 作用 |
|---|---|
| `src/device/nest_property_mapper.py` | 统一将 `DroneNestModule` 输出转换为当前上云属性/状态字段 |
| `DeviceManager.update_from_nest()` | 更新设备物模型属性，供 `state/osd/property` 链路使用 |
| `DockManager.update_dock_status()` | 更新 `dock_status_query` 使用的机场状态快照 |

字段映射：

| MAVLink/内部字段 | 转换后字段 | 单位/枚举 | 用途 |
|---|---|---|---|
| `env_temperature` | `environment_temperature` | °C | 机场环境温度 |
| `nest_temperature` | `temperature`, `nest_temperature` | °C | 舱内温度/机场状态快照 |
| `wind_speed` | `wind_speed` | m/s | 风速 |
| `rainfall` | `rainfall` | 0 无雨，1 小雨，2 中雨，3 大雨 | 降雨等级 |
| `charge_volt` | `working_voltage` | mV | 工作/充电电压 |
| `charge_current` | `working_current` | mA | 工作/充电电流 |
| `estop_state` | `emergency_stop_state` | 0 关闭，1 开启 | 急停状态 |
| `charging_state` | `charging_state`, `drone_charge_state.state` | 0 空闲，1 充电中 | 机场充电状态 |
| `gate_state` | `cover_state` | 0 关闭，1 打开，2 动作中，3 异常 | 舱盖状态 |
| `clamp_state` | `putter_state` | 0 收回/夹紧，1 推出/释放，2 动作中，3 异常 | 推杆/夹紧机构状态 |
| `landing_light_state` | `supplement_light_state` | 0 关闭，1 打开 | 补光/降落灯状态 |
| `ac_temp` | `air_conditioner.air_conditioner_state` | 0 关闭，1 开启 | 空调状态 |

注意：上表是当前项目上云属性字段的集中映射层。Dock3 官方 properties 最终枚举确认后，应优先修改 `nest_property_mapper.py`，再同步本表。

## 联调注意事项

| 注意项 | 要求 |
|---|---|
| 类型一致性 | 当前 XML 中 `DRONE_NEST_RESPONSE` 温度字段是 `uint16_t`，历史生成文件或其他分支可能存在 `int16_t` 差异，联调前必须以目标硬件协议为准 |
| 端口占用 | `src/nest_simulator.py` 默认监听 `0.0.0.0:14550`，不能和 SITL 同时绑定同一个 UDP 端口 |
| 业务状态落地 | 机巢状态进入 `DroneNestModule` 后，需要同步到 `DeviceManager`、MQTT state/event 和 operation record |
| 解析异常 | 自定义 MAVLink 消息解析失败时，应记录消息 ID、原始长度、来源 sysid/compid 和异常原因 |
