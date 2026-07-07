# 30 天开发执行看板

更新时间：2026-07-07

本文档把 `docs/30_DAY_MILESTONE_PLAN.md` 的阶段计划拆成日级执行看板，用于每天推进开发、联调和验收。它不替代 backlog、gap register 或 runbook，而是把它们串成可执行排期。

执行口径：

- 开发顺序严格按 **P0 -> P1 -> P2**。
- P0/P1 是硬门禁，必须 34/34 可用。
- 44 个 backlog 项至少 40 项可用，44 项开发完成。
- P2 可以有已记录问题，但不能空白、不能影响 P0/P1。
- 客户暂未提及/当前业务未用到的 Dock3 能力可以 Deferred，不抢占 P0/P1 开发资源。
- 每天结束时必须更新对应缺口状态、脚本结果和文档状态。

角色口径：

| 人员 | 主责 | 交付边界 |
|---|---|---|
| 人员1 | Dock3 MQTT/API、字段、错误码、脚本 | `src/cloud_api/`、`src/mqtt/`、`src/device/`、`tools/mqtt_dock3_*` |
| 人员2 | MAVLink、SITL、飞控、机巢 v1.3、状态机 | `src/modules/`、`src/core/`、`src/nest_simulator.py`、SITL/E2E |
| 人员3 | Ubuntu/Demo/模拟器/部署/Web/证据归档 | 启动脚本、Demo Docker、MQTT 观察、runbook、证据包 |

## 1. 日级执行表

| 日期 | 优先级 | 人员1 | 人员2 | 人员3 | 当日必须产出 | 关联系统门禁 |
|---|---|---|---|---|---|---|
| Day 1 | P0 | 冻结 Dock3 最终口径，核对 P0/P1/P2 backlog | 复核 MAVLink/SITL/机巢当前链路 | 固化 Ubuntu 启动顺序和 Demo 容器状态 | 覆盖矩阵、method 台账、启动基线 | P0-01 到 P0-05 |
| Day 2 | P0 | 跑 P0/properties/device/organization 脚本并记录失败项 | 复核 OSD 输入源和模式字段来源 | 清理旧进程、broker retained、旧模拟器 | P0 基线日志和失败清单 | P0-01 到 P0-06 |
| Day 3 | P0 | 修正 P0 脚本入参和错误码边界 | 给出飞控模式映射表初稿 | 输出 Demo 在线证据和 MySQL 绑定检查步骤 | Day 1-3 出口记录 | P0-02 到 P0-07 |
| Day 4 | P0 | 建 OSD/state/properties 字段矩阵 | 校验 TelemetryModule 到 OSD 的字段单位 | 准备长时间 MQTT 订阅模板 | 字段矩阵 v1 | G-P0-01 |
| Day 5 | P0 | 收敛 `property_get/property_set` 和 state 快照一致性 | 校验 ArduPilot 模式到 `mode_code/gear` | 记录 SITL 模式切换命令 | OSD/state 专项用例 | G-P0-01、G-P0-02 |
| Day 6 | P0 | 建 request trace 字段和日志样例 | 提供 MAVLink command 超时和 ACK 约定 | 归档 trace 证据模板 | trace 设计和样例 | G-P0-06 |
| Day 7 | P0 | 复跑 P0 全量脚本，更新评分卡基线 | 跑 SITL 模式切换和基础飞控动作 | 输出 Day 4-7 验收记录 | P0 状态/属性出口证据 | P0-06、P0-07、P0-15 |
| Day 8 | P0/P1 | 梳理 Dock3 `cmd/properties/hms` 对机巢映射 | 实现/补齐 `DRONE_NEST_*` 命令应答校验 | 准备机巢模拟器启动和抓包步骤 | 机巢 v1.3 脚本骨架 | G-P0-04、G-P0-05 |
| Day 9 | P0/P1 | 补舱盖、充电、夹紧、急停 Dock3 回包 | 补 boolstate1/boolstate2 状态位解析 | 跑 CMD + 机巢联调日志 | 机巢状态映射 v1 | P0-11 到 P0-13 |
| Day 10 | P0/P1 | 补拒绝条件错误码和 HMS 事件 | 实现 busy/fault/estop/R 轴/允许起飞条件 | 记录失败分支证据 | 拒绝条件用例 | G-P1-02 |
| Day 11 | P0/P1 | 复跑 CMD 和机巢专项 | 修复机巢状态机遗留问题 | 归档机巢专项输出 | 机巢专项通过或缺口清单 | P0-12、P0-13、P1-02 |
| Day 12 | P1 | 补 DRC 心跳、超时和状态事件字段 | 联调 DRC 到 MAVLink 模式/控制链路 | 准备 DRC 观察面板或订阅模板 | DRC 心跳/超时用例 | G-P1-03 |
| Day 13 | P1 | 补 `takeoff_to_point` 请求/回包/错误码 | 联调起飞到点和取消/失败状态 | 记录 SITL 轨迹和日志 | 指点飞行专项证据 | G-P1-04 |
| Day 14 | P1 | 补 KMZ/WPML mock 下载、校验、解析 | 接 MAVLink mission 上传/执行进度 | 准备标准 mock KMZ 样例 | Wayline KMZ/WPML 用例 | G-P1-05、G-P1-06 |
| Day 15 | P1 | 串联一键启航 MQTT 入口和状态事件 | 串联开盖、断充、释放、起飞、任务、返航 | 输出一键启航 E2E 日志 | `e2e_takeoff_flow_test.py` | G-P1-01 |
| Day 16 | P1 | 实现 media 文件列表/删除/上传优先级 | 提供相机模拟媒体生成事件 | 准备媒体 mock 文件目录 | media 专项骨架 | G-P1-07 |
| Day 17 | P1 | 实现媒体上传事件和失败码 | 联动拍照/录像触发媒体记录 | 记录 OSS/HTTP PUT mock 限制 | media 专项通过或缺口 | P1-07 |
| Day 18 | P1 | 实现 live 开始/停止/质量/镜头字段 | 提供直播状态和相机负载模拟状态 | 准备 RTSP/RTMP/mock 推流说明 | live 专项骨架 | G-P1-08 |
| Day 19 | P1 | 补 camera/gimbal 基础控制 Dock3 回包 | 联调 ViewLink/gimbal adapter 和无硬件降级 | 记录负载 mock 限制 | payload 基础专项 | G-P1-09 |
| Day 20 | P1 | 复跑 media/live/payload 联合验收 | 修复媒体/直播/负载联动问题 | 归档截图和 MQTT 样例 | Day 16-20 出口证据 | P1-07 到 P1-09 |
| Day 21 | P1/P2 | 实现 HMS 告警结构、清除和恢复事件 | 提供机巢/飞控故障到 HMS 映射 | 准备 HMS 订阅和证据模板 | HMS 专项骨架 | G-P1-10 |
| Day 22 | P1/P2 | 实现远程日志列表/压缩/上传模拟 | 确认日志采集不阻塞 MAVLink | 准备日志证据归档脚本/步骤 | HMS/log 专项 | G-P1-11 |
| Day 23 | P1/P2 | 实现固件升级模拟和配置更新回滚 | 确认升级/配置任务不阻塞状态机 | 记录后台任务和超时证据 | firmware/config 专项 | G-P1-12、G-P1-13 |
| Day 24 | P1/P2 | 补多设备/解绑/离线和 CMD 进阶失败码 | 补数据保留/操作记录验证点 | 汇总 P2 adapter/限制清单 | P1 运维出口证据 | G-P1-14 到 G-P1-16 |
| Day 25 | P0/P1 | 实现 `e2e_full_chain_test.py` P0 子集 | 联调 SITL、机巢、任务、DRC 串联 | 固化一键启动和证据目录 | P0 E2E 首跑 | G-P0-08 |
| Day 26 | P1 | 扩展 E2E 覆盖 media/live/HMS/log/config | 修复全链路状态机串联问题 | 归档 E2E 失败清单 | P0/P1 E2E 首跑 | G-P1-17 |
| Day 27 | P1 | 修复 E2E 所有 P0/P1 阻塞项 | 复测 SITL/机巢/任务稳定性 | 输出全链路证据包 | P0/P1 E2E 通过 | P0/P1 强门禁 |
| Day 28 | P2 | 检查 243 method 处置状态、P2 adapter 和 Deferred 标记 | 修复 P2 对主链路的影响点 | 归档 P2 风险、Deferred 原因和限制说明 | P2/Deferred 清单 | 100% 开发完成 |
| Day 29 | P0/P1/P2 | 全量脚本回归，更新评分卡 | 复核飞控/机巢/任务最终状态 | 输出 Demo/MQTT/日志证据 | 40/44 可用和 44/44 开发完成证据 | 90% 可用率 |
| Day 30 | P0/P1/P2 | 修复最后阻塞项，整理交付说明 | 确认硬件 adapter 边界 | 归档版本、配置、环境和证据包 | 最终交付包 | 交付判定 |

## 2. 缺失脚本交付顺序

| 顺序 | 目标日期 | 脚本 | 负责人 | 关闭缺口 | 必须覆盖 |
|---:|---|---|---|---|---|
| 1 | Day 8-11 | `tools/nest_v13_protocol_test.py` | 人员2 + 人员1 | G-P0-04、G-P0-05、G-P1-02 | 12003/12004/12005、成功、拒绝、超时、状态位 |
| 2 | Day 12 | 扩展 `tools/mqtt_dock3_drc_test.py` | 人员1 + 人员2 | G-P1-03、G-P1-04 | 心跳、超时恢复、`takeoff_to_point` |
| 3 | Day 14 | 扩展 `tools/mqtt_dock3_wayline_test.py` | 人员1 + 人员2 | G-P1-05、G-P1-06 | KMZ/WPML、SITL 航点执行 |
| 4 | Day 15 | `tools/e2e_takeoff_flow_test.py` | 人员1 + 人员2 + 人员3 | G-P1-01 | 起飞前检查到返航降落充电 |
| 5 | Day 16-17 | `tools/mqtt_dock3_media_test.py` | 人员1 + 人员3 | G-P1-07 | 列表、删除、优先级、上传事件 |
| 6 | Day 18 | `tools/mqtt_dock3_live_test.py` | 人员1 + 人员3 | G-P1-08 | 开始/停止、质量、镜头、状态事件 |
| 7 | Day 21-22 | `tools/mqtt_dock3_hms_log_test.py` | 人员1 + 人员3 | G-P1-10、G-P1-11 | HMS、日志列表、压缩、上传、失败码 |
| 8 | Day 23 | `tools/mqtt_dock3_firmware_config_test.py` | 人员1 + 人员3 | G-P1-12、G-P1-13 | 升级任务、进度、配置、回滚 |
| 9 | Day 24 | 扩展 device/organization/cmd 测试 | 人员1 + 人员3 | G-P1-14、G-P1-15 | 多设备、解绑、离线、进阶失败码 |
| 10 | Day 25-27 | `tools/e2e_full_chain_test.py` | 全员 | G-P0-08、G-P1-17 | P0/P1 全链路 |

## 3. 每日站会检查项

| 检查项 | 必须回答 |
|---|---|
| 昨日关闭了哪个 G-P0/G-P1 缺口 | 缺口 ID、代码入口、脚本证据 |
| 今日是否影响 P0/P1 强门禁 | 是则必须优先处理 |
| 是否新增 P2 风险 | 风险是否影响 P0/P1，是否有绕过方式 |
| 是否有阻塞 IO 或长任务进入热路径 | MQTT/MAVLink/状态机是否受影响 |
| 是否更新文档 | backlog、gap register、runbook、scorecard、method ledger 是否同步 |
| 是否留下证据 | 脚本输出、日志、MQTT 样例、Demo 截图、DB 记录 |

## 4. 交付阻塞条件

| 阻塞条件 | 处理 |
|---|---|
| 任意 P0 失败 | 停止 P1/P2 深度开发，优先修复 |
| 任意 P1 主流程失败 | 停止 P2 深度开发，优先修复 |
| `e2e_full_chain_test.py` 不存在或不通过 | 不允许交付 |
| 243 个 method 有 Missing 状态 | 不允许声明 100% 开发完成 |
| Deferred method 没有客户未使用说明 | 不允许声明 100% 开发完成 |
| P2 问题影响 P0/P1 | 不允许归为 P2 可接受问题 |
| 客户需求章节无 Dock3 落点 | 回到追溯矩阵补齐 |
| 只返回 `Unknown method` | 不计为开发完成 |

## 5. 每日证据目录建议

```text
evidence/day-XX/
  commands/
  logs/
  mqtt/
  screenshots/
  db/
  failures.md
```

每个 `failures.md` 至少记录：

```text
- 时间:
- 负责人:
- 影响范围: P0 / P1 / P2
- 缺口 ID:
- method/topic:
- tid/bid:
- 复现命令:
- 日志文件:
- 当前处理:
- 是否阻塞交付:
```
