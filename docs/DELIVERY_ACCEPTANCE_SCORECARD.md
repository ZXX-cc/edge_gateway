# 30 天交付验收评分卡

更新时间：2026-07-14

本文档用于把“90% 功能可用、100% 功能开发完毕、P0/P1 必须可用、P2 可有少量问题”转换成可执行的**网关侧**交付判定规则。它不替代 `docs/P0_P1_P2_DELIVERY_BACKLOG.md`，而是给第 30 天验收提供统一计分口径。

最终目标设备和协议口径仍是 **DJI Dock 3 Cloud API**。客户最新三份资料 `项目优先级整理.docx`、`业务流程.md`、`网关和平台侧重点.docx` 是当前优先级、业务闭环和边界主输入；旧 `DJI_Dock固件功能清单77.docx` 只作为历史功能全集参考。客户文档中的 Dock1/Dock2/旧 Dock 表述只用于识别业务能力，不能作为最终验收字段。

评分卡只评估网关侧能力：硬件控制、任务状态机、实时遥测、设备拓扑、本地短周期数据、安全兜底、升级执行和设备侧 adapter。平台侧台账、权限、前端页面、业务工单、多设备批量调度、全量历史统计、长期数据仓库、媒体资源库和告警通知推送不计入本网关项目失败项。

Method 级覆盖以 `docs/METHOD_COVERAGE_LEDGER.md` 为准：当前 `src/main.py` 静态注册 243 个 MQTT method，第 30 天必须全部有 Customer Required、Integration Support、Dock3 official、legacy、adapter、P2 或 Deferred 处置状态。客户暂未提及/当前业务未用到的 Dock3 能力可以 Deferred，不计入 P0/P1 强门禁。

当前开发节奏、验收命令和证据要求以 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md`、`docs/DOCK3_MODULE_STATUS.md` 和 backlog 文档为准。早期日级执行看板已归档到 `docs/archive/30_DAY_EXECUTION_BOARD.md`，不再作为当前交付入口。

## 1. 计分范围

30 天交付计分以 `docs/P0_P1_P2_DELIVERY_BACKLOG.md` 中的 44 个**网关侧** backlog 项为准：

| 优先级 | 数量 | 交付规则 |
|---|---:|---|
| P0 | 17 | 必须 17/17 可用，任意失败即阻塞交付 |
| P1 | 17 | 必须 17/17 可用，允许体验问题但不允许业务闭环中断 |
| P2 | 10 | 可以有少量已记录问题，但必须全部开发完成，且至少满足总可用率 90% |
| 合计 | 44 | 至少 40/44 可用，同时 44/44 开发完成 |

结论口径：

- **P0/P1 强门禁**：P0 或 P1 任意一项不可用，交付不通过。
- **90% 可用率门禁**：44 项中至少 40 项达到“可用”或“有限可用但不影响主链路”。
- **100% 开发完成门禁**：44 个客户需求 backlog 项都必须有代码入口、adapter/mock、错误码、日志和测试计划，不能空白。
- **P2 例外门禁**：最多允许 4 个 P2 项存在已记录问题；这些问题不得影响 P0/P1。
- **平台排除门禁**：平台侧功能不得作为本网关项目扣分项；相关内容只用于说明系统边界和对接责任。

## 2. 状态定义

| 状态 | 是否计入可用 | 是否计入开发完成 | 判定标准 |
|---|---|---|---|
| Usable | 是 | 是 | 有实现，有专项或 E2E 证据，能按 runbook 重复通过 |
| Limited Usable | 是，仅限 P2 | 是 | 有实现或 mock，能返回 Dock3 合理回包，限制已记录，不影响 P0/P1 |
| Developed Not Usable | 否 | 是 | 有代码入口、日志、错误码和测试计划，但专项或 E2E 未通过 |
| Adapter Ready | 否 | 是 | 硬件缺失时有 adapter/mock 接口、限制说明和失败码，但未证明业务可用 |
| Missing | 否 | 否 | 客户需求内只有文档、旧 Dock1 字段、空 handler、`Unknown method` 或没有可追踪实现 |

P0/P1 不允许使用 `Limited Usable` 作为通过状态。P0/P1 必须是 `Usable`。

## 3. 交付计算公式

```text
总功能数 = P0 17 + P1 17 + P2 10 = 44

P0 可用率 = P0 Usable / 17
P1 可用率 = P1 Usable / 17
总可用率 = (P0 Usable + P1 Usable + P2 Usable + P2 Limited Usable) / 44
开发完成率 = (Usable + Limited Usable + Developed Not Usable + Adapter Ready) / 44
```

第 30 天通过条件：

```text
P0 可用率 = 100%
P1 可用率 = 100%
总可用率 >= 90%
开发完成率 = 100%
P2 已记录问题 <= 4 项
P2 问题影响 P0/P1 = 0 项
```

按 44 项计，90% 可用率的最低数量是 40 项。因此在 P0/P1 34 项全部可用后，P2 至少还需要 6 项达到 `Usable` 或 `Limited Usable`。

## 4. 当前基线快照

该快照来自当前 backlog 文档中的状态，不代表最终交付结论。只要代码、脚本或证据变化，必须同步更新 backlog 和本表。

| 优先级 | Usable | Partial / In Progress | Missing / Pending | 当前风险 |
|---|---:|---:|---:|---|
| P0 | 15 | 2 | 0 | 纯模拟/网关侧 P0 主链路已通过全局状态机、状态清理和 11 段 E2E；P0-07/08 的真实飞控模式/基础飞控行为仍需更完整 SITL/硬件证据，P0-16 仍需生产级非阻塞压测 |
| P1 | 16 | 1 | 0 | DRC、指点飞行、KMZ 基础解析、真实 SITL 下发/起飞/航点推进、media、live、相机/云台 MAVLink/SITL 控制、HMS/log、firmware/config、多设备/解绑/离线、远程调试进阶失败码、短周期数据保留、全局状态机和 P0/P1 E2E 已形成可用证据；真实 HD 航线完整终态/返航语义仍需继续实跑 |
| P2 | 0 | 0 | 10 | P2 目前只有规划口径，仍需 adapter/mock/限制说明 |
| 合计 | 31 | 3 | 10 | 纯模拟/网关侧 P0/P1 主链路已显著收敛；因 P2 尚未形成 6 项可用或有限可用，仍不能声称达到最终 90% 总可用或 100% 开发完成 |

当前最重要结论：

- P0/P1 纯模拟/网关侧主链路已经通过全局状态机和 11 段 E2E，但真实硬件、真实推流、真实 OTA、完整真实 SITL 航线终态仍不能用模拟证据替代。
- P1 已形成 DRC、指点飞行、Wayline、media、live、相机/云台 MAVLink/SITL 控制、HMS/log、firmware/config、多设备/解绑/离线、远程调试进阶失败码、短周期数据保留、全局状态机和 E2E 回归的可用闭环。
- P0/P1 合计已有 31/34 项可用或基本可用；最终交付仍必须关闭剩余真实行为证据，并补齐 P2 adapter/mock/限制说明。
- P2 不能空白；即使允许有问题，也必须在第 30 天前补齐入口、日志、错误码和限制说明。

## 5. P0 评分卡

| ID | 功能域 | 第 30 天通过状态 | 必须证据 |
|---|---|---|---|
| P0-01 | MQTT 基础链路 | Usable | P0 批量脚本、MQTT 订阅日志 |
| P0-02 | 设备上线和心跳 | Usable | MQTT `events/osd/state`、拓扑/绑定 DB 记录 |
| P0-03 | 组织绑定和绑定状态 | Usable | `mqtt_organization_test.py` |
| P0-04 | 设备拓扑 | Usable | `mqtt_dock3_device_test.py` |
| P0-05 | Dock3 可写属性 | Usable | `mqtt_dock3_properties_test.py` |
| P0-06 | 飞行器 OSD 基础遥测 | Usable | OSD 字段矩阵 + P0 批量 |
| P0-07 | 飞行模式映射 | Usable | SITL 模式切换证据 |
| P0-08 | 基础飞控控制 | Usable | DRC/Wayline/SITL 联合专项 |
| P0-09 | DRC 建链和杆量 | Usable | `mqtt_dock3_drc_test.py` |
| P0-10 | Wayline 基础闭环 | Usable | `mqtt_dock3_wayline_test.py` |
| P0-11 | CMD 基础控制 | Usable | `mqtt_dock3_cmd_test.py` |
| P0-12 | 机巢 v1.3 基础通信 | Usable | `nest_v13_protocol_test.py` |
| P0-13 | 机巢关键状态 | Usable | 机巢专项 + CMD 专项 |
| P0-14 | 错误码边界 | Usable | `tests/test_dji_error_codes.py` + MQTT 错误样例 |
| P0-15 | Request Trace 和日志 | Usable | tid/bid trace 样例、operation record |
| P0-16 | 非阻塞主链路 | Usable | E2E 压测、超时注入、代码审计 |
| P0-17 | 全链路最小闭环 | Usable | `e2e_full_chain_test.py` 的 P0 子集 |

## 6. P1 评分卡

| ID | 功能域 | 第 30 天通过状态 | 必须证据 |
|---|---|---|---|
| P1-01 | 一键启航主流程 | Usable | `e2e_takeoff_flow_test.py` |
| P1-02 | 机巢 v1.3 完整状态机 | Usable | `nest_v13_protocol_test.py` |
| P1-03 | DRC 心跳和超时恢复 | Usable | `mqtt_dock3_drc_test.py --timeout 12 --quiet-stale --osd-wait 6`，含 `drc_state`、官方心跳和退出验证 |
| P1-04 | `takeoff_to_point` 和指点飞行 | Usable | DRC 专项已验证 `takeoff_to_point` 与 `fly_to_point_update` 均返回 `simulated=false`；真实 SITL 航点语义归 P1-06 |
| P1-05 | KMZ/WPML 基础解析 | Usable | 扩展 `mqtt_dock3_wayline_test.py` |
| P1-06 | 真实 SITL 航点执行 | Usable | 已验证非模拟 MAVLink 上传、`AUTO` 执行、解锁后真实 `HD.kml` 推进至第 2/10 航点（20%）；未完成任务会精确 `undo` 清理。仍需按其终止语义保存完成/返航证据 |
| P1-07 | 媒体基础 | Usable | `mqtt_dock3_media_test.py` |
| P1-08 | 直播基础 | Usable | `mqtt_dock3_live_test.py` |
| P1-09 | 相机/云台基础控制 | Usable | `mqtt_dock3_payload_test.py` 严格通过；真实 Payload ACK adapter 后续精校 |
| P1-10 | HMS 告警 | Usable | `mqtt_dock3_hms_log_test.py` |
| P1-11 | 远程日志 | Usable | `mqtt_dock3_hms_log_test.py` |
| P1-12 | 固件升级模拟 | Usable | `mqtt_dock3_firmware_config_test.py` |
| P1-13 | 配置更新 | Usable | `mqtt_dock3_firmware_config_test.py` |
| P1-14 | 多设备/解绑/离线 | Usable | Device/Organization 专项 |
| P1-15 | 远程调试进阶失败码 | Usable | `mqtt_dock3_cmd_test.py` 已重跑通过，覆盖 `cover_close` 状态回读 |
| P1-16 | 网关短周期数据保留和操作记录 | Usable | `database_retention_audit.py` + `tests.test_retention_manager`；隔离 DB 验证清理与保留保护 |
| P1-17 | P0/P1 E2E 回归 | Usable | `e2e_full_chain_test.py` |

## 7. P2 评分卡

P2 可以存在已记录问题，但不能空白。第 30 天至少 6 项要达到 `Usable` 或 `Limited Usable`，全部 10 项必须至少达到 `Developed Not Usable` 或 `Adapter Ready`。

| ID | 功能域 | 最低状态 | 必须证据 |
|---|---|---|---|
| P2-01 | PSDK 功能 | Adapter Ready | method/adapter 入口、失败码、限制说明 |
| P2-02 | PSDK/ESDK 透传 | Adapter Ready | 透传 adapter、日志、mock 回包 |
| P2-03 | 远程解禁 | Developed Not Usable | 文件/证书流程占位、失败码、后续计划 |
| P2-04 | AirSense | Adapter Ready | 模拟事件或明确不输出策略 |
| P2-05 | 自定义飞行区进阶 | Developed Not Usable | 基础缓存、回包和限制说明 |
| P2-06 | GB28181/WebRTC | Adapter Ready | live 高级 adapter 入口和 mock 限制 |
| P2-07 | 高级避障/视觉降落 | Adapter Ready | 状态位或拒绝说明 |
| P2-08 | RTK/高级传感 | Limited Usable | RTK/传感字段 mock 或明确状态 |
| P2-09 | 生产 TLS/鉴权/桥接 | Developed Not Usable | 配置项、文档和风险说明 |
| P2-10 | 性能深度优化 | Developed Not Usable | 压测计划、明显阻塞项处理记录 |

## 8. 交付判定表

| 场景 | 判定 |
|---|---|
| 任意 P0 不是 Usable | 不通过 |
| 任意 P1 不是 Usable | 不通过 |
| P0/P1 全部 Usable，但总可用项少于 40 | 不通过 |
| P0/P1 全部 Usable，总可用项不少于 40，但存在 Missing | 不通过 |
| P0/P1 全部 Usable，44/44 开发完成，P2 问题不影响 P0/P1 | 通过 |
| P2 问题影响 MQTT、MAVLink、任务、状态机、媒体/直播基础或 E2E | 不通过 |
| 仅平台侧台账、权限、前端、统计、业务工单或通知未完成 | 不作为本网关项目不通过原因 |

最终交付声明必须同时附上：

- `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` 中所有 P0/P1 命令输出。
- `docs/METHOD_COVERAGE_LEDGER.md` 中 243 个 method 的处置状态；Deferred method 必须说明客户暂未使用和后续触发条件。
- `docs/DOCK3_MODULE_STATUS.md`、`docs/DOCK3_FULL_MIGRATION_PROGRESS.md` 和证据目录中的最新专项/E2E 输出。
- P2 风险清单和影响范围。
- MQTT 订阅、trace 或数据库证据。
- 网关日志、operation record、tid/bid trace。
- 当前 `git rev-parse HEAD`、配置文件摘要和运行环境摘要。
