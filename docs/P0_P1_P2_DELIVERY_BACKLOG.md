# P0/P1/P2 交付 Backlog

更新时间：2026-07-07

本文档把客户需求、当前项目状态和 Dock3 迁移目标整理成可执行 backlog。开发顺序必须按 **P0 -> P1 -> P2** 推进。

P0/P1 具体代码缺口、目标窗口和缺失脚本优先级见 `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md`。

客户 Word 文档章节到本文 backlog 的映射见 `docs/CUSTOMER_REQUIREMENTS_TRACEABILITY_MATRIX.md`。

全链路启动、验收命令、通过标准和证据归档见 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md`。

30 天最终计分和交付判定见 `docs/DELIVERY_ACCEPTANCE_SCORECARD.md`。

最终口径：

- 客户文档定义业务范围和优先级。
- 客户需求涉及的上云能力必须由 DJI Dock 3 Cloud API 定义最终 topic、method、payload、reply、字段、枚举、单位和错误码。
- 机巢 v1.3、MAVLink、ViewLink、媒体源和负载协议属于设备侧 adapter，不改变云端 Dock3 标准。
- 客户暂未提及/当前业务未用到的 Dock3 官方能力可以延期迁移，但必须在 method 台账或 P2 风险中标明 Deferred/Adapter 状态。
- 客户未要求独立 Web 页面或调试台，Web/Demo 页面不进入交付 backlog；它们只作为可选联调工具，正式证据以脚本、MQTT、日志、trace、DB 和 E2E 为准。
- 30 天内 **P0/P1 必须 100% 可用**；P2 可存在少量已记录问题，但不能阻塞或破坏 P0/P1。

客户固件清单中存在大量 P0/P1/P2 标注。自动抽取表格行时观察到 P0、P1、P2 均覆盖飞控、机巢、交互和负载能力；具体实现以本文映射到 Dock3 后的 backlog 为准。

## 1. 状态口径

| 状态 | 含义 |
|---|---|
| 已可用 | 已有代码和脚本、MQTT、日志、trace 或数据库证据，当前可作为联调基线 |
| 部分可用 | 已有代码或单点验证，但字段、状态机、真实行为或 E2E 证据不足 |
| 待实现 | 有旧代码、占位或文档要求，但还没有 Dock3 可验收实现 |
| P2 可延期 | 30 天内允许留下问题，但必须有入口、日志、错误码、限制说明和后续计划 |

## 2. P0 必须可用

P0 是交付阻塞项。30 天内任意 P0 不可用，都不能声称主链路可交付。

| ID | 功能域 | 客户来源 | Dock3 落点 | 当前状态 | 30 天交付要求 | 证据 |
|---|---|---|---|---|---|---|
| P0-01 | MQTT 基础链路 | 第六章 IF-004、业务启动流程 | `services/reply`、`requests/reply`、`status/reply`、`events`、`osd`、`state`、`drc` | 已可用 | 单一网关实例稳定连接 broker，旧回包可清理，所有 reply 使用 Dock3 topic | `mqtt_dock3_p0_batch_test.py`、MQTT 订阅日志 |
| P0-02 | 设备上线和心跳 | 业务启动流程、设备管理 | `events`、`osd`、设备在线状态 | 已可用 | Dock 和子设备可被云端协议识别在线 | MQTT `events/osd/state`、设备拓扑/绑定 DB 记录 |
| P0-03 | 组织绑定和绑定状态 | 设备绑定与组织管理 | `requests/requests_reply` organization | 已可用 | `airport_bind_status/get/bind` 成功、失败、超时均可处理 | `mqtt_organization_test.py` |
| P0-04 | 设备拓扑 | 设备管理、飞机-机巢关系 | `sys/product/{sn}/status:update_topo` | 已可用 | 网关和子设备拓扑按 Dock3 字段发布，能处理离线拓扑 | `mqtt_dock3_device_test.py` |
| P0-05 | Dock3 可写属性 | 设备属性 | `property/set`、`property/set_reply`、`property_get` | 已可用 | `silent_mode`、`air_transfer_enable`、`user_experience_improvement` 可写，只读/非法拒绝 | `mqtt_dock3_properties_test.py` |
| P0-06 | 飞行器 OSD 基础遥测 | F-023 到 F-050 等 OSD P0 | `thing/product/{sn}/osd`、`thing/product/{sn}/state` | 已可用 | 经纬度、高度、姿态、速度、电池、GPS、飞行时间、机场 OSD 和属性 state 可重复验证；字段单位和枚举继续按 Dock3 精校 | `mqtt_dock3_osd_state_test.py` 已通过，字段矩阵继续补 |
| P0-07 | 飞行模式映射 | F-001 到 F-012、F-019 等 P0 | `mode_code`、`gear`、任务状态 | Needs Test | 已收敛共享 ArduPilot 模式映射表，Copter/Plane/QuadPlane 按 HEARTBEAT.type 自动选择；`set_flight_mode` 支持 `mode/mode_code/flight_mode`，失败不再回 `result=0` | `tests.test_flight_modes`、SITL 模式切换测试 |
| P0-08 | 基础飞控控制 | F-051 到 F-060 等 P0 | `drc`、`wayline`、相关 services | 部分可用 | 起飞、FlyTo、返航、降落、解锁、任务开始/暂停/恢复/取消能在 SITL 或模拟链路跑通 | DRC/Wayline/SITL 专项 |
| P0-09 | DRC 建链和杆量 | F-007、F-059 | `drc_mode_enter/exit`、`drc/down`、`drc/up` | 已可用 | 官方 payload、值域、错误 topic 和 `drc_state` 同步可用 | `mqtt_dock3_drc_test.py` |
| P0-10 | Wayline 基础闭环 | 航线飞行、任务执行 | `flighttask_prepare/execute/progress/pause/resume/return/undo` | 已可用 | 纯模拟任务不误触发真实飞控，进度和错误码符合 Dock3 | `mqtt_dock3_wayline_test.py` |
| P0-11 | CMD 基础控制 | 机巢控制、远程调试 | Dock3 `cmd` | 已可用 | 舱盖、充电、推杆、灯、报警、设备动作有 Dock3 回包和事件 | `mqtt_dock3_cmd_test.py` |
| P0-12 | 机巢 v1.3 基础通信 | `机巢协议v1.3` | 设备侧 `DRONE_NEST_*`，云端映射到 `cmd/properties/state` | 部分可用 | 12003/12004/12005、命令应答、周期状态、关键 bit 位可解析 | `nest_v13_protocol_test.py` |
| P0-13 | 机巢关键状态 | D 类机场状态、I 类交互 | `cover_state`、`drone_charge_state`、急停、故障、ready/busy | 部分可用 | 舱盖、夹紧、充电、急停、故障、备飞能进入 Dock3 state/properties/HMS | 机巢专项 + CMD 专项 |
| P0-14 | 错误码边界 | DJI 错误码、异常分支 | `services_reply`、`property/set_reply`、`drc/up` | 已可用 | 对外不暴露内部负数；未知 method、topic 错误、参数错误有官方码 | `tests/test_dji_error_codes.py` |
| P0-15 | Request Trace 和日志 | 第六章风险、运维要求 | 日志、operation record、trace | 已可用 | 每个 tid/bid 能串起 request、handler、reply/event、耗时和错误；properties 专项后 trace 已能统计三段事件和 `result=0/1` | `request_trace_query.py`、Ubuntu trace 断言通过 |
| P0-16 | 非阻塞主链路 | 第六章技术栈和风险 | MQTT/MAVLink/DB/文件后台化 | 部分可用 | MQTT 回调、MAVLink 接收、状态机不得被 DB、文件、上传、压缩、升级阻塞 | 代码审计 + E2E 压测 |
| P0-17 | 全链路最小闭环 | 业务流程全景 | 上线 -> 绑定 -> 拓扑 -> OSD -> 控制 -> 任务 -> 事件 | 待实现 | 一条命令或脚本可重复跑完整 P0 主链路 | `e2e_full_chain_test.py` |

## 3. P1 必须可用

P1 是业务闭环项。30 天内必须可用，允许少量不影响闭环的体验问题，但不能中断主流程。

| ID | 功能域 | 客户来源 | Dock3 落点 | 当前状态 | 30 天交付要求 | 证据 |
|---|---|---|---|---|---|---|
| P1-01 | 一键启航主流程 | `BUSINESS_FLOW.docx` 一键启航 | `wayline` + `cmd` + `events/state` | 待实现 | 起飞前检查、开盖、断充、释放、起飞、任务、返航、降落、充电串联 | `e2e_takeoff_flow_test.py` |
| P1-02 | 机巢 v1.3 完整状态机 | 机巢协议 v1.3 | 设备侧 adapter -> Dock3 state/HMS | 部分可用 | 使用条件、拒绝原因、busy/fault/estop/R 轴/允许起飞全部落地 | `nest_v13_protocol_test.py` |
| P1-03 | DRC 心跳和超时恢复 | DRC 业务流程 | `drc`、`drc_state`、events | 待实现 | 心跳丢失后自动退出或降级，状态和错误码一致 | DRC 专项 |
| P1-04 | `takeoff_to_point` 和指点飞行 | 指令飞行 | `drc` / remote control | 待实现 | 起飞到点、导航中、取消/失败状态可用 | DRC + SITL |
| P1-05 | KMZ/WPML 基础解析 | 航线管理 | `wayline` | 待实现 | 下载/校验/解析标准 mock KMZ，转换到内部 mission | Wayline 专项 |
| P1-06 | 真实 SITL 航点执行 | 飞控/航线 | MAVLink mission + Dock3 progress | 部分可用 | 不只模拟回包，至少一条 SITL 航线能执行并回 progress | SITL Wayline E2E |
| P1-07 | 媒体基础 | 媒体管理 | Dock3 `media` | 待实现 | 文件列表、删除、上传优先级、上传事件可用 | `mqtt_dock3_media_test.py` |
| P1-08 | 直播基础 | 直播功能 | Dock3 `live` | 待实现 | 开始/停止直播、质量、镜头、状态事件可用；可先 mock 推流 | `mqtt_dock3_live_test.py` |
| P1-09 | 相机/云台基础控制 | P 类负载、云台 | payload/camera/gimbal、DRC payload | 部分可用 | 拍照、录像、变焦、云台角度、云台速度基础命令可用 | Payload 专项 |
| P1-10 | HMS 告警 | HMS 管理 | Dock3 `hms` | 待实现 | 告警 key、设备、级别、恢复/清除和事件结构可用 | `mqtt_dock3_hms_log_test.py` |
| P1-11 | 远程日志 | 运维流程 | Dock3 `log` | 待实现 | 日志列表、压缩、上传模拟、超时和失败码可用 | `mqtt_dock3_hms_log_test.py` |
| P1-12 | 固件升级模拟 | OTA | Dock3 `firmware` | 待实现 | 任务创建、进度、失败码、后台任务模型可用 | `mqtt_dock3_firmware_config_test.py` |
| P1-13 | 配置更新 | 配置管理 | Dock3 `config` | 待实现 | 配置下发、持久化、回滚、状态事件可用 | `mqtt_dock3_firmware_config_test.py` |
| P1-14 | 多设备/解绑/离线 | 设备管理 | `device`、`organization`、`update_topo` | 部分可用 | 多子设备、解绑、离线、重连不破坏拓扑状态 | Device/Organization 专项 |
| P1-15 | 远程调试进阶失败码 | CMD | Dock3 `cmd` | 部分可用 | 真实耗时、失败码、安全拒绝、任务进度更贴近 Dock3 | CMD 专项 |
| P1-16 | 数据保留和操作记录 | 运维/审计 | DB、trace、retention | 部分可用 | 操作记录、遥测保留、告警保留和清理策略可用 | DB/日志专项 |
| P1-17 | P0/P1 E2E 回归 | 全链路可用性 | 全模块 | 待实现 | P0/P1 可一键回归，输出明确通过/失败项 | `e2e_full_chain_test.py` |

## 4. P2 允许带问题但必须有边界

P2 可以在 30 天末保留问题，但不能空白、不能影响 P0/P1、不能无记录。

| ID | 功能域 | Dock3 落点 | 30 天最低交付 | 可接受问题 |
|---|---|---|---|---|
| P2-01 | PSDK 功能 | `psdk` | 保留 Dock3 method 入口、模拟回包、错误码和限制说明 | 无真实 PSDK 设备 |
| P2-02 | PSDK/ESDK 透传 | transmit custom data | 有透传 adapter 接口和日志 | 数据格式后续随真实负载调整 |
| P2-03 | 远程解禁 | `flysafe` | 有文件/证书流程占位和失败码 | 真实证书和云端文件流程待联调 |
| P2-04 | AirSense | `airsense` | 有模拟事件或明确不输出策略 | 无实体传感器 |
| P2-05 | 自定义飞行区进阶 | `custom-fly-region` | 基础缓存和回包可用 | 复杂地图生效逻辑可后续优化 |
| P2-06 | GB28181/WebRTC | live 高级能力 | 保留能力说明和 adapter 入口 | 可先只支持 RTSP/RTMP mock |
| P2-07 | 高级避障/视觉降落 | OSD/state/HMS | 有状态位或拒绝说明 | 无视觉硬件时只做模拟 |
| P2-08 | RTK/高级传感 | properties/osd | RTK/传感字段有占位和 mock | 无真实 RTK 时不做强验收 |
| P2-09 | 生产 TLS/鉴权/桥接 | MQTT bridge | 配置项和文档准备 | 本地模拟阶段可继续 1883 明文 |
| P2-10 | 性能深度优化 | 全链路 | 不阻塞 P0/P1，保留压测计划 | 30 天内只处理明显阻塞和崩溃问题 |

## 5. 开发门禁

| 门禁 | 要求 |
|---|---|
| P0 开发门禁 | P0 专项全部通过后才能进入 P1 大规模开发 |
| P1 开发门禁 | P1 主链路专项和 E2E 通过后才能把 P2 作为主要工作 |
| P2 问题门禁 | 每个 P2 问题必须记录影响范围、绕过方式、后续计划和是否影响 P0/P1 |
| Dock3 门禁 | 新增任何 method、字段、枚举、错误码都必须写明 Dock3 文档来源或标明为设备侧 adapter |
| 非阻塞门禁 | 文件上传、日志压缩、固件升级、媒体处理、DB 写入不得阻塞 MQTT 回调和 MAVLink 接收线程 |

## 6. 30 天交付证据清单

| 证据 | 覆盖 |
|---|---|
| `python tools/mqtt_dock3_p0_batch_test.py --timeout 12` | P0 基础协议链路 |
| `python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale` | P0 properties |
| `python tools/mqtt_dock3_device_test.py --timeout 12` | P0/P1 device topology |
| `python tools/mqtt_organization_test.py --action full ...` | P0/P1 organization |
| `python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale` | P0/P1 DRC |
| `python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale` | P0/P1 CMD |
| `python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale` | P0/P1 Wayline |
| `python tools/nest_v13_protocol_test.py --timeout 12` | P0/P1 机巢 v1.3 |
| `python tools/mqtt_dock3_media_test.py --timeout 12` | P1 media |
| `python tools/mqtt_dock3_live_test.py --timeout 12` | P1 live |
| `python tools/mqtt_dock3_hms_log_test.py --timeout 20` | P1 HMS/log |
| `python tools/mqtt_dock3_firmware_config_test.py --timeout 20` | P1 firmware/config |
| `python tools/e2e_takeoff_flow_test.py --timeout 60` | P1 一键启航 |
| `python tools/e2e_full_chain_test.py --timeout 180` | P0/P1 全链路 |

执行顺序和证据保存要求以 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` 为准。
