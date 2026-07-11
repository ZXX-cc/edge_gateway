# Drone Nest MAVLink 字段映射

本文件记录当前项目自定义机巢 MAVLink 方言的业务映射。来源文件是 `src/mavlink_definitions/drone_nest.xml`，生成代码是 `src/generated_dialect/drone_nest.py`。

协议来源是客户文档 `docs/机巢协议v1.3.docx`。该协议属于设备侧适配层，最终上云 topic、method、payload、reply、字段和枚举仍必须按 DJI Dock 3 Cloud API 收敛。

## 消息总览

| MAVLink ID | 消息名 | 方向 | 业务含义 | 生成类 | CRC extra |
|---:|---|---|---|---|---:|
| 12003 | `DRONE_NEST_COMMAND` | 网关 -> 机巢 | 下发机巢动作命令 | `MAVLink_drone_nest_command_message` | 244 |
| 12004 | `DRONE_NEST_RESPONSE` | 机巢 -> 网关 | 周期上报机巢环境和状态 | `MAVLink_drone_nest_response_message` | 134 |
| 12005 | `DRONE_NEST_ORDER_RESPONSE` | 机巢 -> 网关 | 回复命令执行结果 | `MAVLink_drone_nest_order_response_message` | 70 |

协议 v1.3 通信关系：

| 项目 | 说明 |
|---|---|
| TCP 角色 | 机巢主控为 TCP Server，网关为客户端 |
| 默认设备地址 | 客户文档示例为 `192.168.1.11` |
| 字节序 | 多字节小端 |
| 命令应答 | 网关发送 `DRONE_NEST_COMMAND` 后，机巢应回复一次 `DRONE_NEST_ORDER_RESPONSE` |
| 状态上报 | 机巢周期发送 `DRONE_NEST_RESPONSE` |
| 云端关系 | 该协议只负责设备侧动作和状态，不能替代 Dock3 云端 `cmd/properties/state/events/hms` |

## `DRONE_NEST_COMMAND` 字段

| 字段 | 类型 | 原始单位/编码 | 业务含义 | 当前模拟器映射 |
|---|---|---|---|---|
| `ordinary_order` | `uint16_t` | 枚举命令字 | 普通机巢动作命令 | `0x1101` 开门，`0x1102` 关门，`0x1103` 门停止，`0x0F01` 充电，`0x0F02` 不充电，`0x1401` 夹紧，`0x1402` 夹紧释放，`0x1403` 夹紧停止，`0x0A01` 急停，`0x0A02` 解除急停，`0x0A04` 初始化，`0x0A05` 故障恢复，`0x0A06` 大灯开，`0x0A07` 大灯关，`0x0B01` 飞机上电，`0x0B02` 飞机下电 |
| `takeoff_land_order` | `uint8_t` | 枚举命令字 | 起降相关命令 | `0x01` 起飞，`0x02` 起飞终止，`0x04` 降落终止；协议 v1.3 未定义 `LANDING_START` |

## v1.3 命令使用条件

客户协议 v1.3 对动作执行条件有明确约束。当前代码后续需要把这些条件落到设备侧状态机和 Dock3 错误码/HMS 事件中。

| 命令 | 命令字 | v1.3 使用条件 | Dock3 映射要求 |
|---|---:|---|---|
| 起飞流程 | `takeoff_land_order=0x01` | 机巢备飞、非 busy、非急停、无故障 | 起飞前检查失败时拒绝 Dock3 航线/起飞类命令，并上报明确原因 |
| 起飞终止 | `takeoff_land_order=0x02` | 急停/故障外的任意状态 | 映射为任务终止或安全中止事件 |
| 降落终止 | `takeoff_land_order=0x04` | 降落流程 busy，且非急停、无故障 | 映射为降落中止事件和任务状态变化 |
| 开门 | `ordinary_order=0x1101` | 非起飞/降落流程，非急停 | 映射到 Dock3 舱盖控制和 `cover_state` |
| 关门 | `ordinary_order=0x1102` | 非起飞/降落流程，夹紧状态为夹紧，R 轴归零，非急停、无故障 | 条件失败时返回 Dock3 控制失败并写 HMS/操作记录 |
| 门停止 | `ordinary_order=0x1103` | 舱门开/关动作中，非急停 | 映射为舱盖动作中止 |
| 充电 | `ordinary_order=0x0F01` | 非起飞/降落流程，夹紧，非急停、无故障 | 映射到 Dock3 充电控制和 `drone_charge_state` |
| 不充电 | `ordinary_order=0x0F02` | 非起飞/降落流程，夹紧 | 映射为停止充电 |
| 夹紧 | `ordinary_order=0x1401` | 非起飞/降落流程，非急停、无故障 | 映射到 Dock3 推杆/夹紧机构状态 |
| 夹紧释放 | `ordinary_order=0x1402` | 非起飞/降落流程，非急停、无故障 | 映射到 Dock3 推杆/夹紧机构状态 |
| 夹紧停止 | `ordinary_order=0x1403` | 夹紧/释放动作中，非急停 | 映射为机构动作中止 |
| 急停 | `ordinary_order=0x0A01` | 任意状态可下发 | 必须立即更新急停状态和 HMS 事件 |
| 解除急停 | `ordinary_order=0x0A02` | 急停状态 | 成功后恢复可控状态，但不能自动继续危险动作 |
| 初始化 | `ordinary_order=0x0A04` | 按机巢主控内部条件执行 | 映射为维护类操作和进度事件 |
| 故障恢复 | `ordinary_order=0x0A05` | 故障状态 | 成功后清理故障状态并上报 state/HMS |
| 大灯开/关 | `ordinary_order=0x0A06/0x0A07` | 非急停/故障约束按主控执行 | 映射到补光灯或机场灯状态 |
| 飞机上电/下电 | `ordinary_order=0x0B01/0x0B02` | v1.3 新增，按机巢状态约束执行 | 映射到 Dock3 飞行器电源控制类命令和状态 |

## `DRONE_NEST_RESPONSE` 字段

| 字段 | 类型 | 缩放 | 业务值 | 业务含义 |
|---|---|---:|---|---|
| `ac_temp` | `uint16_t` | `/10` | 摄氏度 | 空调温度 |
| `nest_temperature` | `uint16_t` | `/10` | 摄氏度 | 机巢内部温度 |
| `env_temperature` | `uint16_t` | `/10` | 摄氏度 | 外部环境温度 |
| `wind_direction` | `uint16_t` | `/100` | 度 | 风向 |
| `wind_speed` | `uint16_t` | `/10` | m/s | 风速 |
| `atmospheric_pressure` | `uint16_t` | `/10` | kPa | 大气压 |
| `rainfall` | `uint16_t` | `/10` | mm/m² | 降雨量 |
| `charge_volt` | `uint16_t` | `/100` | V | 充电电压 |
| `charge_current` | `uint16_t` | `/100` | A | 充电电流 |
| `error_code` | `uint16_t` | `1` | 错误码 | 机巢故障码 |
| `boolstate1` | `uint16_t` | bitset | 位图 | bit0 机巢心跳，bit1 飞机心跳，bit2 PLC心跳，bit3 急停，bit4 充电，bit5 校准，bit6 备飞，bit7 初始化，bit8 系统busy，bit9 故障，bit10 R轴0位，bit11 大灯状态：0开灯/1关灯 |
| `boolstate2` | `uint16_t` | bitset | 位图 | bit0 起飞流程运行中，bit1 降落流程运行中，bit2 起飞终止中，bit3 降落终止中，bit4 备降点状态，bit5 允许起飞 |
| `clamp_state` | `uint8_t` | enum | 状态码 | `0` 默认，`1` 放开，`2` 夹紧，`3` 放开打开中，`4` 夹紧关闭中，`5` 暂停中，`6` 故障中 |
| `gate_state` | `uint8_t` | enum | 状态码 | `0` 默认，`1` 打开，`2` 关闭，`3` 开启中，`4` 关闭中，`5` 暂停中，`6` 故障中 |
| `landing_light_state` | `uint8_t` | enum | 状态码 | `0` 默认，`1` 打开，`2` 关闭，`6` 故障中 |

## `DRONE_NEST_ORDER_RESPONSE` 字段

| 字段 | 类型 | 原始单位/编码 | 业务含义 |
|---|---|---|---|
| `ordinary_order_response` | `uint32_t` | bit/枚举结果 | 普通机巢命令执行结果，当前模拟器 `0` 表示成功 |
| `takeoff_land_order_response` | `uint32_t` | bit/枚举结果 | 起降命令执行结果，当前模拟器 `0` 表示成功 |

## 状态位解释

`boolstate1`：

| bit | 含义 | Dock3 映射建议 |
|---:|---|---|
| 0 | 机巢心跳 | 设备在线和机巢通信健康状态 |
| 1 | 飞机心跳 | 飞行器在线/在舱/飞控连接状态 |
| 2 | PLC 心跳 | 机巢执行器通信状态 |
| 3 | 急停 | `emergency_stop_state`、HMS 告警 |
| 4 | 充电中 | `drone_charge_state`、充电状态 |
| 5 | 校准状态 | 维护/校准状态 |
| 6 | 备飞 | 起飞前检查 |
| 7 | 已初始化 | 机巢初始化状态 |
| 8 | busy | 控制命令互斥和拒绝条件 |
| 9 | fault | `alarm_state`、HMS 告警 |
| 10 | R 轴 0 位 | 关门/任务前置条件 |
| 11 | 大灯状态 | 补光/机场灯状态，需以客户主控定义确认 0/1 极性 |

`boolstate2`：

| bit | 含义 | Dock3 映射建议 |
|---:|---|---|
| 0 | 起飞流程运行中 | 任务/起飞状态事件 |
| 1 | 降落流程运行中 | 降落状态事件 |
| 2 | 起飞终止中 | 任务中止事件 |
| 3 | 降落终止中 | 降落中止事件 |
| 4 | 备降点状态 | 任务安全状态，需结合业务定义 |
| 5 | 允许起飞 | 一键启航和 `flighttask_execute` 前置条件 |

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
| `fault_state` / `error_code` | `alarm_state` | 0 正常，1 告警 | 机巢故障状态 |
| `ac_temp` | `air_conditioner.air_conditioner_state` | 0 关闭，1 开启 | 空调状态 |

注意：上表是当前项目上云属性字段的集中映射层。Dock3 官方 properties 最终枚举确认后，应优先修改 `nest_property_mapper.py`，再同步本表。

## 联调注意事项

| 注意项 | 要求 |
|---|---|
| 类型一致性 | 当前 XML 中 `DRONE_NEST_RESPONSE` 温度字段是 `uint16_t`，历史生成文件或其他分支可能存在 `int16_t` 差异，联调前必须以目标硬件协议为准 |
| 端口占用 | 当前脚本把网关/机巢自定义链路固定为 `14551`，SITL 标准 MAVLink 输入固定为 `14552`；两类 dialect 分 socket 解析 |
| 业务状态落地 | 机巢状态进入 `DroneNestModule` 后，需要同步到 `DeviceManager`、MQTT state/event 和 operation record |
| 解析异常 | 自定义 MAVLink 消息解析失败时，应记录消息 ID、原始长度、来源 sysid/compid 和异常原因 |
