# 客户需求可追溯矩阵

更新时间：2026-07-07

本文档把客户 Word 文档的章节追溯到当前 Dock3 交付 backlog、实现缺口和验收证据。它解决的问题是：每个客户章节最终由哪个 Dock3 功能域承接、落到哪些 P0/P1/P2 项、需要关闭哪些代码缺口、用什么脚本证明可用。

追溯链路：

```text
客户文档章节
  -> 业务能力
  -> Dock3 云端落点或设备侧 adapter
  -> P0/P1/P2 backlog ID
  -> P0/P1 gap ID
  -> 验收脚本、日志、MQTT 样例或 Demo 证据
```

开发口径：

- 客户文档定义业务范围和优先级。
- 客户需求涉及的上云能力由 DJI Dock 3 Cloud API 定义最终 topic、method、payload、reply、字段、枚举、单位和错误码。
- 客户文档中的 Dock1/Dock2/旧 Dock 表述只保留业务意图，不作为最终云端字段标准。
- 机巢 v1.3、MAVLink、ViewLink、媒体源和负载协议属于设备侧 adapter，不改变云端 Dock3 口径。
- 客户暂未提及/当前业务未用到的 Dock3 能力可以 Deferred，不进入 30 天 P0/P1 强迁移。
- 30 天内 P0/P1 必须 100% 可用；P2 可以带已记录问题，但不能影响 P0/P1 主链路。

## 1. 当前权威交付文档

| 文档 | 作用 |
|---|---|
| `docs/CUSTOMER_REQUIREMENTS_COVERAGE_MATRIX.md` | 客户需求到 Dock3 口径的总体覆盖矩阵 |
| `docs/METHOD_COVERAGE_LEDGER.md` | 243 个 MQTT method 的 P0/P1/P2 归类和关项要求 |
| `docs/P0_P1_P2_DELIVERY_BACKLOG.md` | P0/P1/P2 功能优先级、当前状态和 30 天交付要求 |
| `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md` | P0/P1 代码入口、缺口、脚本和目标窗口 |
| `docs/30_DAY_MILESTONE_PLAN.md` | 30 天阶段计划和里程碑 |
| `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` | 启动顺序、验收命令、通过标准和证据归档 |

## 2. `DJI_Dock固件功能清单77.docx` 章节追溯

该文档不是纯 Dock1 文档，而是客户整理的飞机、机巢、交互、负载和架构功能全集。迁移时要把业务能力转成 Dock3 云端语义。

| 客户章节 | 业务能力 | Dock3 落点 | Backlog | Gap | 验收证据 |
|---|---|---|---|---|---|
| 第1章 飞机飞控需支持的功能 | 飞行模式、OSD、飞控指令、航线、故障保护、日志、传感定位 | `osd/state/properties`、`drc`、`wayline`、`hms`、`log` | P0-06、P0-07、P0-08、P0-09、P0-10、P1-03、P1-04、P1-05、P1-06 | G-P0-01、G-P0-02、G-P0-03、G-P1-03、G-P1-04、G-P1-05、G-P1-06 | properties/DRC/Wayline/SITL 专项、P0/P1 E2E |
| 1.1 飞行模式管理 | 模式切换、状态识别、任务状态 | `mode_code`、`gear`、任务状态事件 | P0-07、P1-04 | G-P0-02、G-P1-04 | SITL 模式切换、DRC + SITL |
| 1.2 遥测数据上报 | 经纬度、高度、姿态、速度、电池、定位、时间戳 | `thing/product/{sn}/osd`、`state`、`properties` | P0-06 | G-P0-01 | OSD 字段矩阵、P0 批量测试 |
| 1.3 飞行控制指令接收 | 解锁、起飞、降落、返航、FlyTo、暂停/恢复 | `drc`、`wayline`、remote-control 语义 | P0-08、P0-09、P1-03、P1-04 | G-P0-03、G-P1-03、G-P1-04 | `mqtt_dock3_drc_test.py`、SITL 飞控专项 |
| 1.4 航线任务执行 | 航线准备、执行、暂停、恢复、返航、撤销、进度 | `wayline` | P0-10、P1-05、P1-06 | G-P1-05、G-P1-06 | `mqtt_dock3_wayline_test.py`、SITL Wayline E2E |
| 1.5 故障保护与安全 | failsafe、异常事件、飞行安全 | `hms`、`events`、`state` | P0-14、P1-10、P2-03、P2-04、P2-05、P2-07 | G-P1-10 | HMS 专项、P2 风险登记 |
| 1.6 固件与日志 | 飞控日志、远程日志、固件升级 | `log`、`firmware` | P1-11、P1-12 | G-P1-11、G-P1-12 | `mqtt_dock3_hms_log_test.py`、`mqtt_dock3_firmware_config_test.py` |
| 1.7 传感器与定位 | GPS、RTK、姿态、风速、定位状态 | `osd/state/properties` | P0-06、P2-08 | G-P0-01 | OSD 字段矩阵、RTK mock 说明 |
| 第2章 机巢主控需支持的功能 | 舱盖、归中、充电、起降状态机、气象、设备、网络、运维、HMS、云通信 | `cmd`、`properties`、`state`、`hms`、`log`、`firmware`、`config`、MQTT | P0-01、P0-11、P0-12、P0-13、P1-01、P1-02、P1-10、P1-11、P1-12、P1-13 | G-P0-04、G-P0-05、G-P1-01、G-P1-02、G-P1-10、G-P1-11、G-P1-12、G-P1-13 | CMD、机巢 v1.3、HMS/log、firmware/config、E2E |
| 2.1 舱盖控制 | 开盖、关盖、停止、状态同步 | `cmd`、`cover_state`、events | P0-11、P0-13、P1-02 | G-P0-05、G-P1-02 | CMD 专项、`nest_v13_protocol_test.py` |
| 2.2 归中控制 | 推杆/夹紧/释放/停止、到位状态 | `cmd`、机场 state/properties | P0-11、P0-13、P1-02 | G-P0-05、G-P1-02 | CMD 专项、机巢状态位校验 |
| 2.3 充电管理 | 开始/停止充电、充电状态、异常 | `cmd`、`drone_charge_state`、HMS | P0-11、P0-13、P1-02、P1-10 | G-P0-05、G-P1-02、G-P1-10 | CMD + HMS + 机巢专项 |
| 2.4 起降流程状态机 | 备飞、起飞、降落、返航、异常中断 | `wayline` + `cmd` + `events/state` | P1-01、P1-02、P1-17 | G-P1-01、G-P1-02、G-P1-17 | `e2e_takeoff_flow_test.py`、`e2e_full_chain_test.py` |
| 2.5 气象感知 | 风速、雨量、环境状态和禁止起飞条件 | `properties`、`hms`、`state` | P0-13、P1-10、P2-07 | G-P0-05、G-P1-10 | properties/HMS 专项、mock adapter |
| 2.6 设备管理 | 设备在线、拓扑、绑定、多设备 | `device`、`organization`、`status` | P0-02、P0-03、P0-04、P1-14 | G-P1-14 | Device/Organization 专项、Demo 在线 |
| 2.7 网络管理 | MQTT 连接、离线重连、链路状态 | MQTT `events/osd/state/reply` | P0-01、P0-02、P1-14 | G-P1-14 | P0 批量、长时间订阅日志 |
| 2.8 远程运维 | 日志、配置、升级、操作记录 | `log`、`config`、`firmware`、operation record | P0-15、P1-11、P1-12、P1-13、P1-16 | G-P0-06、G-P1-11、G-P1-12、G-P1-13、G-P1-16 | trace、log、firmware/config 专项 |
| 2.9 HMS 告警 | 告警、恢复、清除、故障联动 | `hms`、events | P1-10 | G-P1-10 | `mqtt_dock3_hms_log_test.py` |
| 2.10 与云端通信 | MQTT、上线、心跳、事件、命令回包 | Dock3 MQTT 全 topic | P0-01、P0-02、P0-14、P0-15、P0-16 | G-P0-06、G-P0-07 | P0 批量、request trace、压测 |
| 第3章 飞机与机巢主控交互 | 飞机/机巢协议协同、起降、充电、对频、升级、媒体、心跳、故障联动 | 设备侧 MAVLink/v1.3 adapter -> Dock3 `state/events/cmd/hms/media` | P0-12、P0-13、P0-15、P0-16、P1-01、P1-02、P1-07、P1-12、P1-17 | G-P0-04、G-P0-05、G-P0-06、G-P0-07、G-P1-01、G-P1-02、G-P1-07、G-P1-12、G-P1-17 | 机巢专项、媒体/固件专项、E2E |
| 第4章 飞机与吊舱交互 | 云台、相机、直播、喊话器、探照灯、PSDK、媒体 | `media`、`live`、payload/camera/gimbal、`psdk`、custom data | P1-07、P1-08、P1-09、P2-01、P2-02、P2-06 | G-P1-07、G-P1-08、G-P1-09 | media/live/payload 专项、P2 限制说明 |
| 第5章 四维度汇总对照表 | 飞控、机巢、交互、负载的总表和优先级 | 本追溯矩阵 + backlog + runbook | 全部 P0/P1/P2 | 全部 G-P0/G-P1 | backlog 和 runbook 逐项关项 |
| 第6章 关键架构建议 | 系统分层、接口边界、协议选择、技术选型、实施路径、风险 | Dock3 云端 adapter + MAVLink/机巢 adapter + 状态机编排器 | P0-01、P0-06、P0-12、P0-16、P0-17、P1-17 | G-P0-01、G-P0-04、G-P0-07、G-P0-08、G-P1-17 | P0/P1 E2E、非阻塞审计、压测 |

## 3. 第六章接口追溯

第六章的核心结论是：网关不是继续实现 Dock1，而是在 Dock3 云端协议和客户设备侧协议之间做适配与编排。

| 接口 | 边界 | 当前代码入口 | Backlog | Gap | 验收 |
|---|---|---|---|---|---|
| IF-001 飞控到伴随计算机 | MAVLink 2.0，OSD 高频、状态低频 | `src/core/connection.py`、`src/modules/telemetry.py`、`src/modules/flight_control.py` | P0-06、P0-07、P0-08、P1-06 | G-P0-01、G-P0-02、G-P0-03、G-P1-06 | SITL、OSD、Wayline E2E |
| IF-002 伴随计算机到机巢主控 | MAVLink over TCP/UDP，实时命令和状态 | `src/modules/drone_nest_module.py`、`src/device/nest_property_mapper.py` | P0-12、P0-13、P1-02 | G-P0-04、G-P0-05、G-P1-02 | 机巢 v1.3 专项 |
| IF-003 机巢主控到执行器 | 舱盖、充电、归中、急停等设备侧动作 | `src/nest_simulator.py`、`src/cloud_api/dock3_cmd.py` | P0-11、P0-13、P1-02 | G-P0-05、G-P1-02 | CMD + 机巢状态位 |
| IF-004 机巢主控到云端 | MQTT over TLS，OSD/state/events/reply | `src/mqtt/`、`src/cloud_api/`、`src/device/` | P0-01、P0-02、P0-03、P0-04、P0-14、P0-15、P0-16 | G-P0-06、G-P0-07 | P0 批量、Demo 在线、trace |
| IF-005 相机链路 | 摄像头采集和媒体文件 | `src/payload/camera.py`、`src/media/manager.py` | P1-07、P1-08、P1-09 | G-P1-07、G-P1-08、G-P1-09 | media/live/payload 专项 |
| IF-006 云台链路 | 云台角度、速度、状态 | `src/payload/gimbal.py`、`web/mavlink/viewlink/` | P1-09 | G-P1-09 | payload 专项 |
| IF-007 喊话器 | 第三方负载控制 | `src/payload/third_party.py` | P2-01、P2-02 | P2 风险 | P2 mock 和限制说明 |
| IF-008 探照灯 | 第三方负载或 GPIO/CAN/串口 | `src/payload/third_party.py`、`src/cloud_api/dock3_cmd.py` | P0-11、P2-01 | P2 风险 | CMD 基础 + P2 mock |
| IF-009 飞控到云台直连 | MAVLink Gimbal v2 设备侧路径 | `src/modules/flight_control.py`、`src/payload/gimbal.py` | P1-09 | G-P1-09 | payload 专项 |

## 4. `2677.docx` 章节追溯

该文档描述当前项目的架构和代码能力，是落地方式来源。

| 客户章节 | 业务能力 | 当前项目落点 | Backlog/Gaps | 验收证据 |
|---|---|---|---|---|
| 2.1 数据流 | 云端、MQTT、MAVLink、业务模块的数据流 | `src/main.py`、`src/mqtt/`、`src/cloud_api/`、`src/modules/` | P0-01、P0-15、P0-16、P0-17 / G-P0-06、G-P0-07、G-P0-08 | P0 批量、E2E、trace |
| 2.2 MQTT 为核心 | topic 路由、method 分发、回包 | `src/mqtt/client.py`、`src/mqtt/topic_router.py` | P0-01、P0-14、P0-15 | P0 批量、错误码测试 |
| 2.3 云端到飞机控制 | 云端命令到 MAVLink 飞控 | `src/drc/`、`src/mission/`、`src/modules/flight_control.py` | P0-08、P0-09、P0-10、P1-03、P1-04、P1-06 | DRC/Wayline/SITL |
| 2.4 遥测 | 飞控遥测到云端 OSD/state | `src/modules/telemetry.py`、`src/cloud_api/telemetry_reporter.py` | P0-06、P0-07 | OSD 字段矩阵 |
| 2.5 事件 | 设备事件、任务事件、告警事件 | `src/cloud_api/connection_manager.py`、`src/device/manager.py` | P0-02、P0-13、P1-10 | events/HMS/E2E |
| 4.1 DJI Cloud API collections | Dock3 method 分组 | `src/cloud_api/dock3_*` | 全部 P0/P1/P2 | 分组专项脚本 |
| 4.2 能力覆盖 | 当前能力与缺口 | coverage/backlog/gap 文档 | 全部 P0/P1/P2 | 文档关项 + 脚本 |
| 5.1 到 5.10 代码模块 | 当前代码结构和模块职责 | `src/`、`tools/`、`web/` | 全部 P0/P1/P2 | 单测、专项、E2E |
| 第6章 ViewLink SDK | 吊舱/相机/云台集成 | `web/mavlink/viewlink/`、`src/payload/` | P1-09、P2-01、P2-02 | payload 专项、P2 说明 |
| 第7章 DRC | 指令飞行和实时控制 | `src/drc/`、`src/cloud_api/dock3_drc.py` | P0-09、P1-03、P1-04 | DRC 专项 |
| 第8章 Auto-Takeoff | 一键启航 | `src/mission/manager.py`、`src/cloud_api/dock3_wayline.py`、`src/modules/drone_nest_module.py` | P1-01、P1-02、P1-17 | 起飞流程 E2E |
| 第9章 架构模式 | 非阻塞、状态机、adapter、可观测性 | `src/database/`、`src/media/`、`src/modules/`、`tools/` | P0-15、P0-16、P1-16、P1-17 | trace、压测、E2E |

## 5. `BUSINESS_FLOW.docx` 业务流程追溯

该文档定义从启动到业务闭环的端到端流程，必须用于 P1 全链路验收。

| 业务流程 | Dock3 落点 | Backlog | Gap | 验收 |
|---|---|---|---|---|
| 启动流程 | MQTT 连接、上线、心跳、OSD/state | P0-01、P0-02、P0-06、P0-15 | G-P0-01、G-P0-06 | P0 批量、Demo 在线 |
| 设备绑定与组织 | `organization`、`device`、`status` | P0-03、P0-04、P1-14 | G-P1-14 | organization/device 专项 |
| 一键启航 | `wayline` + `cmd` + `events/state` | P1-01、P1-02、P1-17 | G-P1-01、G-P1-02、G-P1-17 | `e2e_takeoff_flow_test.py` |
| 航线飞行监控 | `wayline` progress、OSD、任务事件 | P0-10、P1-05、P1-06 | G-P1-05、G-P1-06 | Wayline + SITL E2E |
| DRC 实时控制 | `drc_mode_enter/exit`、`drc/down/up`、心跳 | P0-09、P1-03、P1-04 | G-P1-03、G-P1-04 | DRC 专项 |
| 机巢管理 | 舱盖、充电、夹紧、急停、故障 | P0-11、P0-12、P0-13、P1-02 | G-P0-04、G-P0-05、G-P1-02 | CMD + 机巢 v1.3 |
| 相机和云台 | 拍照、录像、变焦、云台角度/速度 | P1-09 | G-P1-09 | payload 专项 |
| 媒体管理 | 文件列表、删除、上传优先级、上传事件 | P1-07 | G-P1-07 | media 专项 |
| 直播 | 开始/停止直播、镜头、质量、状态 | P1-08、P2-06 | G-P1-08 | live 专项 |
| OTA/固件 | 任务、进度、失败码、后台升级 | P1-12 | G-P1-12 | firmware/config 专项 |
| 第三方负载 | PSDK/ESDK、喊话器、探照灯、高级负载 | P2-01、P2-02 | P2 风险 | P2 mock 和限制说明 |
| 飞行安全/围栏 | 解禁、飞行区、AirSense、高级避障 | P2-03、P2-04、P2-05、P2-07 | P2 风险 | P2 风险登记 |
| 自定义数据 | 透传数据、设备侧 adapter | P2-02 | P2 风险 | custom data mock |
| 系统数据流 | request trace、操作记录、保留策略、非阻塞 | P0-15、P0-16、P1-16、P1-17 | G-P0-06、G-P0-07、G-P1-16、G-P1-17 | trace、retention、E2E |

## 6. `机巢协议v1.3.docx` 协议追溯

该文档是设备侧协议，不是云端最终标准。它必须通过 adapter 映射到 Dock3 `cmd/properties/state/events/hms`。

| 协议项 | 设备侧含义 | Dock3 落点 | Backlog | Gap | 验收 |
|---|---|---|---|---|---|
| 通信关系 | 地面站作为 TCP client，机巢为 server `192.168.1.11` | 设备侧 adapter 配置 | P0-12、P1-02 | G-P0-04、G-P1-02 | 机巢 v1.3 专项 |
| `DRONE_NEST_COMMAND` 12003 | 地面站发送机巢控制命令 | Dock3 `cmd` 或业务 service 触发 | P0-11、P0-12、P1-02 | G-P0-04、G-P1-02 | 命令帧发送和参数校验 |
| `DRONE_NEST_RESPONSE` 12004 | 机巢周期状态 | Dock3 `properties/state/hms` | P0-13、P1-02、P1-10 | G-P0-05、G-P1-02、G-P1-10 | 状态位和 HMS 映射 |
| `DRONE_NEST_ORDER_RESPONSE` 12005 | 单次命令应答 | Dock3 `services_reply`、events、operation record | P0-11、P0-14、P0-15、P1-15 | G-P0-06、G-P1-15 | 成功、拒绝、超时、失败码 |
| 起飞/降落相关命令 | 起飞、起飞中止、降落中止 | 一键启航、Wayline、events/state | P0-08、P1-01、P1-02 | G-P0-03、G-P1-01、G-P1-02 | 起飞流程 E2E |
| 舱盖命令 | 开盖、关盖、停止 | Dock3 `cmd`、`cover_state` | P0-11、P0-13 | G-P0-05 | CMD + 机巢状态 |
| 充电命令 | 充电、不充电 | Dock3 `cmd`、`drone_charge_state` | P0-11、P0-13、P1-02 | G-P0-05、G-P1-02 | CMD + 充电状态 |
| 归中/夹紧命令 | 抓紧、释放、停止 | Dock3 `cmd`、state/properties | P0-11、P0-13、P1-02 | G-P0-05、G-P1-02 | CMD + 状态位 |
| 急停/初始化/故障恢复 | 急停、取消急停、初始化、故障恢复 | `cmd`、`hms`、events | P0-11、P0-13、P1-10、P1-15 | G-P0-05、G-P1-10、G-P1-15 | CMD + HMS |
| 大灯/飞机电源 | 开关灯、飞机上电/下电 | `cmd`、properties/state | P0-11、P1-15 | G-P1-15 | CMD 进阶 |
| `boolstate1` | 心跳、急停、充电、校准、备飞、busy、fault、R 轴、大灯 | Dock3 机场状态、properties、HMS | P0-13、P1-02、P1-10 | G-P0-05、G-P1-02、G-P1-10 | 状态位专项 |
| `boolstate2` | 起飞/降落流程、中止流程、备用降落点、允许起飞 | 一键启航状态机、events/state | P1-01、P1-02 | G-P1-01、G-P1-02 | 起飞流程 E2E |

## 7. 完整性门禁

| 门禁 | 通过标准 |
|---|---|
| 章节覆盖门禁 | 每个客户章节至少能追溯到一个 Dock3 落点或一个设备侧 adapter |
| P0/P1 映射门禁 | 每个 P0/P1 Backlog 必须有 Gap、代码入口和验收证据 |
| P0/P1 可用门禁 | 30 天内所有 P0/P1 专项脚本和 E2E 必须可重复通过 |
| P2 边界门禁 | P2 可以存在问题，但必须有入口、错误码、日志、影响范围和后续计划 |
| Dock3 口径门禁 | 任何旧 Dock 字段只能作为迁移参考，最终验收必须落到 Dock3 topic/method/payload/reply |
| Deferred 门禁 | 客户未用 Dock3 能力可以延期，但必须记录原因、后续触发条件，且不影响 P0/P1 |
| 非阻塞门禁 | 数据库、媒体、日志、固件、上传、压缩不得阻塞 MQTT 回调、MAVLink 接收和状态机 |

## 8. 当前未完全闭合的追溯项

| 项 | 原因 | 处理方式 |
|---|---|---|
| Dock3 官方字段逐项编号 | 当前文档已做业务域映射，但还未把客户需求涉及的 Dock3 字段逐项列成字段级矩阵 | 在 P0-06、P0-13、P1-07 到 P1-13 开发时同步补字段级用例；客户未用字段可 Deferred |
| P2 高级能力 | 真实 PSDK/ESDK、AirSense、GB28181/WebRTC、远程解禁依赖真实设备或外部平台 | 30 天内至少保留 adapter、mock、错误码、日志和限制说明 |
| 全链路 E2E | 当前缺 `e2e_full_chain_test.py` 和 `e2e_takeoff_flow_test.py` | 按 `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md` 的目标窗口优先补齐 |
| 旧 Dock/Dock1/Dock2 表述 | 客户文档中存在旧表达，容易误导实现 | 所有实现和验收都以 Dock3 Cloud API 为最终标准 |
