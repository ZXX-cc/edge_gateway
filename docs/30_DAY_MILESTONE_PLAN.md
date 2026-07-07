# 30 天里程碑计划

更新时间：2026-07-07

目标周期：30 天。

目标结果：

- **P0/P1 100% 可用**：30 天内所有 P0 和 P1 功能必须在 Ubuntu 模拟环境中可重复运行，有脚本、日志或 Demo 页面证据。
- **P2 允许存在少量问题**：P2 功能可以存在模拟限制、硬件依赖或非主链路问题，但必须有代码入口、明确状态、风险说明和后续修复计划。
- **100% 开发完成**：客户需求覆盖矩阵中的业务域都有 Dock3 对应实现、模拟实现或硬件适配接口，不允许停留在旧 Dock1 字段或 `Unknown method`。
- **全链路可用**：Dock3 云端协议、MQTT、网关、MAVLink/SITL、机巢 v1.3、媒体/直播模拟、HMS/日志/配置都能串起来。

最终验收口径：客户总需求定义业务范围，**DJI Dock 3 官方 Cloud API 定义客户需求相关协议和字段标准**。客户暂未提及、当前业务未用到的 Dock3 能力可以标为 Deferred，不纳入 30 天 P0/P1 强迁移。

P0/P1/P2 详细功能清单和验收证据以 `docs/P0_P1_P2_DELIVERY_BACKLOG.md` 为准。日级开发排期以 `docs/30_DAY_EXECUTION_BOARD.md` 为准。90% 可用率、100% 开发完成和 P0/P1 强门禁以 `docs/DELIVERY_ACCEPTANCE_SCORECARD.md` 为准。全链路启动、脚本执行和证据归档以 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` 为准。本文档只定义 30 天节奏和阶段出口。

## 0. 优先级口径

开发顺序必须按 **P0 -> P1 -> P2** 推进。P0/P1 没有达到可用前，不把主要开发资源投入 P2 深度优化。

| 优先级 | 30 天验收要求 | 范围 |
|---|---|---|
| P0 | 必须 100% 可用，失败即阻塞交付 | 启动、MQTT、设备上线、绑定、拓扑、OSD/state/properties、错误码、request trace、基础 CMD、基础 DRC、基础 Wayline、MAVLink/SITL 连接、机巢关键状态 |
| P1 | 必须 100% 可用，允许少量非阻塞体验问题但不能影响业务闭环 | 一键启航主流程、机巢 v1.3 完整状态机、DRC 心跳/超时、Wayline KMZ/WPML 基础解析、媒体基础、直播基础、HMS、远程日志、配置、固件升级模拟、全链路 E2E |
| P2 | 允许有已记录问题，不能阻塞 P0/P1 | PSDK/ESDK 透传、高级负载、远程解禁、AirSense、高级飞安、GB28181/WebRTC、真实硬件细节、性能深度优化 |

P2 的最低交付要求：有 Dock3 协议入口或 adapter 占位，有错误码和日志，有文档说明当前限制；不能出现主链路调用 P2 后导致 P0/P1 不可用。

量化口径：30 天验收按 44 个 backlog 项计分，P0 17 项、P1 17 项、P2 10 项。P0/P1 必须 34/34 可用；总可用项必须不少于 40/44；开发完成项必须 44/44。P0/P1 任意失败不能用总可用率抵消。

## 1. 里程碑总览

| 时间 | 阶段目标 | 关键交付 | 出口标准 |
|---|---|---|---|
| Day 1-3 | P0 需求冻结与基线固化 | 覆盖矩阵、启动说明、当前脚本基线、旧回包清理方案 | 文档能说明每个客户需求域落到哪个 Dock3 分组；现有 P0 脚本可运行 |
| Day 4-7 | P0 Dock3 OSD/State/Properties 精校 | 客户需求相关字段矩阵、OSD/state 映射、Dock3 属性枚举和单位 | 云端看到的机场/飞行器基础状态符合 Dock3；旧字段只保留在 adapter 内部 |
| Day 8-11 | P0 到 P1：机巢 v1.3 与机场状态机 | 命令条件、拒绝原因、boolstate2、舱盖/夹紧/充电/急停状态机 | 机巢动作可通过 Dock3 `cmd` 入口触发，状态能回填 properties/state/HMS |
| Day 12-15 | P1 飞控、DRC、航线和一键启航 | MAVLink 模式映射、起飞/返航/降落、WPML/KMZ 基础解析、一键启航主流程 | SITL 或模拟器能完成起飞前检查、任务准备、执行、暂停/恢复、返航主链路 |
| Day 16-20 | P1 媒体、直播、相机、云台和负载 | Dock3 media/live 专项、媒体上传事件、直播状态、云台/相机控制模拟 | 媒体和直播基础能力可用，Demo/云端可看到可用状态和回包 |
| Day 21-24 | P1 运维闭环，P2 建入口 | HMS、远程日志、固件升级、配置更新、飞安/自定义飞行区基础 | 运维类 method 有 Dock3 回包、状态事件、错误码和后台任务模型；P2 有入口和限制说明 |
| Day 25-27 | P0/P1 全链路 E2E 验收 | `e2e_full_chain_test.py`、Demo 联调记录、失败用例清单 | P0/P1 从上线绑定到任务、媒体、日志、告警的主链路可重复跑通 |
| Day 28-30 | P0/P1 收口，P2 风险归档 | 最终台账、操作手册、P2 风险清单、版本标记 | P0/P1 100% 可用证据齐全；P2 问题全部记录，不影响主链路 |

## 2. Day 1-3：需求和基线

| 工作项 | 输出 | 负责人 |
|---|---|---|
| 固化客户需求覆盖矩阵 | `docs/CUSTOMER_REQUIREMENTS_COVERAGE_MATRIX.md` | 人员1/人员2 |
| 明确 Dock3 优先于 Dock1 的最终口径 | README、迁移文档、模块状态表同步 | 人员1 |
| 清点现有专项脚本 | P0、Properties、Device、Organization、DRC、CMD、Wayline 基线命令 | 人员1/人员3 |
| 清理旧回包源 | broker retained/旧进程/旧模拟器排查方法 | 人员3 |
| 确认 Demo 兼容状态 | Demo Web 中 Dock/Aircraft Online 记录 | 人员3 |

出口标准：

```bash
python tools/mqtt_dock3_p0_batch_test.py --timeout 12
python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_device_test.py --timeout 12
python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale
```

## 3. Day 4-7：Dock3 状态与属性

| 工作项 | 输出 | 验收 |
|---|---|---|
| 建 Dock3 properties/osd/state 字段矩阵 | `docs/INTERFACE_DESIGN_SPEC.md` 增补客户需求相关字段表 | 每个客户需求相关字段有来源、单位、枚举、当前状态；客户未用字段标 Deferred |
| 修正历史 Dock1/Dock2 字段 | adapter 内部保留，云端输出改 Dock3 | MQTT 订阅端不再把旧字段当正式字段 |
| 统一机场/飞行器状态快照 | `DeviceManager`、`TelemetryReporter`、`nest_property_mapper` | `property_get`、`osd`、`state` 值一致 |
| 完善 request trace | tid/bid/topic/method/handler/reply/耗时 | 任意失败请求能定位原因 |

出口标准：

- `property/set_reply` 保持逐属性 `state.result`。
- `osd/state` 字段、枚举、单位有明确 Dock3 对照。
- 旧兼容字段在文档中标为“内部/兼容”，不进入最终验收。

## 4. Day 8-11：机巢 v1.3 和机场状态机

| 工作项 | 输出 | 验收 |
|---|---|---|
| 完整映射 `DRONE_NEST_COMMAND` | 舱盖、充电、夹紧、急停、大灯、飞机上下电、起飞/终止 | 命令字和条件写入 `DRONE_NEST_MAVLINK_MAPPING.md` |
| 完整映射 `DRONE_NEST_RESPONSE` | boolstate1、boolstate2、门、夹紧、灯、故障、充电 | 状态能进入 Dock3 properties/state |
| 完整处理拒绝条件 | busy、fault、estop、未备飞、R 轴未归零等 | 拒绝时 Dock3 回包错误码和 HMS 事件一致 |
| 建机巢专项脚本 | `tools/nest_v13_protocol_test.py` | 覆盖成功、拒绝、超时和状态变化 |
| 一键启航前置检查 | 备飞、舱盖、夹紧、充电、急停、风雨、飞控心跳 | 检查失败有明确错误码和日志 |

出口标准：

```bash
python tools/nest_v13_protocol_test.py --timeout 12
python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale
```

## 5. Day 12-15：飞控、DRC、航线和一键启航

| 工作项 | 输出 | 验收 |
|---|---|---|
| ArduPilot 模式到 Dock3 状态映射 | `mode_code`、`gear`、任务状态、返航/降落状态 | SITL 状态变化能反映到 Dock3 |
| DRC 心跳和超时恢复 | 进入、心跳、控制、退出、超时降级 | 超时后 `drc_state` 回到安全状态 |
| WPML/KMZ 基础解析 | KMZ 下载、校验、任务准备、航点转换 | `flighttask_prepare` 能处理真实包结构或标准 mock |
| 航线执行联动 MAVLink | execute/pause/resume/return/undo/cancel | 任务进度事件和飞控状态一致 |
| 一键启航主流程 | 起飞前检查、开盖、断充、释放、起飞、任务执行、返航、降落、充电 | 主流程有 E2E 日志和失败回滚 |

出口标准：

```bash
python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale
```

新增一键启航专项：

```bash
python tools/e2e_takeoff_flow_test.py --timeout 60
```

## 6. Day 16-20：媒体、直播和负载

| 工作项 | 输出 | 验收 |
|---|---|---|
| Dock3 media 方法矩阵 | 文件列表、删除、上传优先级、上传事件 | `tools/mqtt_dock3_media_test.py` |
| 媒体上传模拟 | OSS/HTTP PUT mock、进度、失败重试 | 云端能看到上传状态事件 |
| Dock3 live 方法矩阵 | 开始/停止直播、质量、镜头、状态事件 | `tools/mqtt_dock3_live_test.py` |
| FFmpeg/RTSP/RTMP 模拟入口 | 不接真实相机也能模拟直播状态 | live 状态可被查询和停止 |
| 相机/云台/负载控制 | 拍照、录像、变焦、云台角度、第三方负载占位 | 回包符合 Dock3 或 PSDK 语义 |

出口标准：

```bash
python tools/mqtt_dock3_media_test.py --timeout 12
python tools/mqtt_dock3_live_test.py --timeout 12
```

## 7. Day 21-24：运维闭环

| 工作项 | 输出 | 验收 |
|---|---|---|
| HMS | 告警 key、级别、设备、恢复/清除规则 | HMS 事件可订阅，错误码可追踪 |
| 远程日志 | 日志列表、压缩、上传、超时、失败码 | `tools/mqtt_dock3_hms_log_test.py` |
| 固件升级 | 任务创建、进度、失败码、后台任务 | `tools/mqtt_dock3_firmware_config_test.py` |
| 配置更新 | 配置下发、持久化、回滚、状态事件 | 配置错误不会阻塞 MQTT 回调 |
| 飞安/自定义飞行区 | 区域更新、缓存、生效/失败事件 | 基础 mock 可验收 |

出口标准：

```bash
python tools/mqtt_dock3_hms_log_test.py --timeout 20
python tools/mqtt_dock3_firmware_config_test.py --timeout 20
```

## 8. Day 25-27：全链路 E2E

全链路必须覆盖：

1. 网关启动并连接 MQTT。
2. 设备上线、心跳、OSD/state 发布。
3. `airport_bind_status`、组织绑定、`update_topo`。
4. 机巢状态同步。
5. 远程调试控制舱盖/夹紧/充电。
6. DRC 进入、控制、退出。
7. 航线准备、执行、暂停、恢复、返航。
8. 一键启航主流程。
9. 媒体文件列表、上传事件。
10. 直播启动、状态、停止。
11. HMS 告警、远程日志、配置、固件升级模拟。

出口标准：

```bash
python tools/e2e_full_chain_test.py --timeout 180
```

成功后必须更新：

- `docs/DOCK3_FULL_MIGRATION_PROGRESS.md`
- `docs/DOCK3_MODULE_STATUS.md`
- `docs/CUSTOMER_REQUIREMENTS_COVERAGE_MATRIX.md`
- `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md`
- `docs/UBUNTU_SERVER_STARTUP_GUIDE.md`

## 9. Day 28-30：交付收口

| 工作项 | 输出 |
|---|---|
| 全量回归 | 所有专项脚本和 E2E 脚本结果 |
| 文档收口 | README、启动指南、接口说明、迁移进度、覆盖矩阵 |
| 风险收口 | 未接真实硬件、真实 Dock3 云端差异、负载 SDK 差异、TLS/鉴权差异 |
| 版本标记 | 可运行配置、依赖版本、Docker/Demo 联调状态 |
| 交付说明 | P0/P1 100% 可用证据、P2 风险归档、100% 开发完成证据、二期硬件适配清单 |

最终出口标准：

| 指标 | 要求 |
|---|---|
| P0 可用 | P0 功能 100% 可重复跑通，任何失败都必须在交付前修复 |
| P1 可用 | P1 功能 100% 可重复跑通，允许体验瑕疵但不允许业务闭环中断 |
| P2 状态 | 允许少量已记录问题，但必须有入口、日志、错误码、限制说明和后续计划 |
| 100% 开发完成 | 客户需求覆盖矩阵中每个业务域都有代码入口、模拟/真实适配、错误码、日志和测试计划 |
| Dock3 合规 | 云端 topic/method/payload/reply 不以 Dock1 字段为最终标准 |
| 非阻塞 | MQTT 回调、MAVLink 接收、状态机不被 DB/文件/压缩/上传/升级阻塞 |
| 可追踪 | 每次请求可按 tid/bid 从 MQTT 到 handler 到 reply/event 追踪 |

## 10. 风险和约束

| 风险 | 影响 | 处理 |
|---|---|---|
| 无真实 Dock3 设备 | 不能证明硬件动作 | 先用设备侧 adapter 和模拟器完成 Dock3 云端行为，硬件接入时只替换 adapter |
| 客户文档含旧 Dock 表述 | 容易误把 Dock1 字段当最终字段 | 文档和代码中统一标注：旧字段只做业务意图和差异对照 |
| DJI Demo 枚举显示 Dock2 | Demo 页面能在线但型号不等于最终目标 | Demo 只作为联通证明，最终验收看 Dock3 协议和字段 |
| 媒体/直播缺真实源 | 演示链路可能断 | 先做 RTSP/RTMP/mock 状态和事件，再接真实相机 |
| 大量 IO 操作 | 可能阻塞主链路 | 固件、日志、媒体上传必须后台化，有超时和失败事件 |
| 旧回包噪声 | 专项脚本可能看到重复旧格式回包 | 清理旧进程、broker retained 和旧模拟器，脚本支持严格模式 |
