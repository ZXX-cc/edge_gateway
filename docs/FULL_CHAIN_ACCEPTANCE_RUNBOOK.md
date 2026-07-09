# 全链路验收 Runbook

更新时间：2026-07-07

本文档用于证明 Edge Gateway 按客户需求和 DJI Dock 3 口径完成全链路可用。它不是开发计划，而是验收执行手册。

如果验收失败，需要回到 `docs/P0_P1_IMPLEMENTATION_GAP_REGISTER.md` 查找对应 P0/P1 缺口、代码入口和目标窗口。

第 30 天交付评分以 `docs/DELIVERY_ACCEPTANCE_SCORECARD.md` 为准：44 个 backlog 项中至少 40 项可用，44 项开发完成，且 P0/P1 必须 34/34 可用。

最终验收规则：

- P0/P1 必须 100% 可用，任何 P0/P1 失败都需要修复后再交付。
- P2 可以有已记录问题，但不能影响 P0/P1 主链路。
- 客户需求相关云端 topic、method、payload、reply、字段、枚举和错误码以 DJI Dock 3 为最终标准。
- 客户暂未提及、当前业务未用到的 Dock3 能力可以标为 Deferred；Deferred 项不参与 P0/P1 通过判定，但必须记录原因和后续触发条件。
- 设备侧 MAVLink、机巢 v1.3、媒体源、负载协议只作为 adapter，不改变 Dock3 云端口径。
- 客户未要求 Web 调试台或前端页面，Web/Demo 页面不作为正式验收模式；可选 Demo Docker 只用于内部联通排查。

## 1. 验收链路

标准全链路：

```text
DJI Dock3 云端模拟器
  -> MQTT Broker
  -> Edge Gateway
  -> ArduPilot SITL / 机巢 v1.3 模拟器 / 媒体与负载 mock
  -> Edge Gateway
  -> MQTT osd / state / events / services_reply / requests_reply / status_reply / drc/up
  -> 脚本、MQTT、日志、trace、DB 和 E2E 证据
```

正式验收运行模式：

| 模式 | 用途 | 说明 |
|---|---|---|
| 脚本模拟模式 | 日常开发和 CI 式回归 | 使用 `tools/*.py` 发布/订阅 MQTT，适合精确检查 payload 和错误码 |

DJI Demo Docker 可作为可选联调参考，不进入客户交付判定。

## 2. 现有脚本和缺口

当前仓库已有：

| 脚本 | 当前用途 | 覆盖 |
|---|---|---|
| `tools/ubuntu_preflight.sh` | Ubuntu 只读预检 | 环境、依赖、端口 |
| `tools/mqtt_cloud_simulator.py` | 云端 requests/status 模拟器 | organization、bind、status_reply |
| `tools/mqtt_organization_test.py` | 组织管理专项 | `airport_bind_status/get/bind` |
| `tools/mqtt_dock3_p0_batch_test.py` | P0 基础批量 | property、services、DRC、错误 topic |
| `tools/mqtt_dock3_properties_test.py` | properties 专项 | 可写/只读属性、值域 |
| `tools/mqtt_dock3_device_test.py` | device topology 专项 | `update_topo`、在线/离线拓扑 |
| `tools/mqtt_dock3_drc_test.py` | DRC 专项 | enter/exit、杆量、错误值域 |
| `tools/mqtt_drc_batch_test.py` | DRC 批量闭环 | enter/exit、`drc_state`、杆量、飞行器/负载控制、速度控制 |
| `tools/mqtt_dock3_cmd_test.py` | CMD 专项 | 舱盖、充电、推杆、灯、设备动作 |
| `tools/mqtt_dock3_wayline_test.py` | Wayline 专项 | prepare/execute/progress/pause/resume/return |
| `tools/mqtt_dock3_media_test.py` | Media 专项，已通过 Ubuntu 复测 | 文件列表、删除、上传优先级、上传事件 |
| `tools/mqtt_dock3_live_test.py` | Live 专项，已通过 Ubuntu 复测 | `mock://` 直播开始/停止、质量、镜头、状态事件 |
| `tools/e2e_full_chain_test.py` | 当前 P0/P1 核心 MQTT 域 E2E 编排 | properties、device、OSD/state、CMD、DRC、Wayline、media、live；HMS/log 待复测后接入 |
| `tools/mqtt_smoke_test.py` | 兼容冒烟 | 单 method smoke，不作为最终 P0/P1 正式验收 |

30 天内必须补齐：

| 脚本 | 必须覆盖 | 优先级 |
|---|---|---|
| `tools/nest_v13_protocol_test.py` | `DRONE_NEST_COMMAND/RESPONSE/ORDER_RESPONSE`、命令条件、状态位、拒绝原因 | P0/P1 |
| 已通过 `tools/mqtt_dock3_media_test.py` | 文件列表、删除、上传优先级、上传事件 | P1 |
| 已通过 `tools/mqtt_dock3_live_test.py` | 直播开始/停止、质量、镜头、状态事件 | P1 |
| Ubuntu 复测 `tools/mqtt_dock3_hms_log_test.py` | HMS 告警、远程日志列表/上传模拟、取消、失败码 | P1 |
| `tools/mqtt_dock3_firmware_config_test.py` | 固件升级模拟、配置下发、持久化、回滚 | P1 |
| `tools/e2e_takeoff_flow_test.py` | 一键启航：起飞前检查、机巢动作、起飞、任务、返航、降落、充电 | P1 |
| 扩展 `tools/e2e_full_chain_test.py` | 继续接入 HMS/log/firmware/config 等剩余 P1 域 | P1 |

## 3. 启动前检查

在 Ubuntu 服务器执行：

```bash
cd ~/lxz/edge_gateway
git status --short --branch
bash tools/ubuntu_preflight.sh

ss -tlnp | egrep ':1883|:8083|:6789|:18083|:14550|:14551' || true
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

通过标准：

| 项目 | 通过条件 |
|---|---|
| Git 状态 | 当前分支和本地改动明确，不覆盖他人修改 |
| Python 依赖 | `ubuntu_preflight.sh` 无关键依赖错误 |
| MQTT | `1883` 可用或已有 Mosquitto/NanoMQ 明确监听 |
| 可选 Demo Docker | 仅内部联调时检查 `cloud_api_sample-*` 容器运行；正式验收不依赖页面 |
| 端口冲突 | SITL 和机巢模拟器不抢同一个 MAVLink 端口 |

## 4. 标准启动顺序

### 4.1 MQTT Broker

```bash
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager
ss -tlnp | grep ':1883'
```

如果只需要启动外围组件，优先使用项目脚本：

```bash
scripts/start_support_stack.sh
```

如果 `mosquitto.service` 重启失败，但 `1883` 已由 Docker/EMQX/其他 broker 监听，不要反复重启宿主机服务，改用检查模式：

```bash
BROKER_MODE=check scripts/start_support_stack.sh
```

如果 `1883` 没有监听，按脚本输出的 `systemctl status` 和 `journalctl -u mosquitto` 片段修复 Mosquitto；必要时用 `sudo mosquitto -c /etc/mosquitto/mosquitto.conf -v` 定位配置错误。

### 4.2 MQTT 观察终端

根据当前 SN 选择 topic。DJI Demo 兼容模式当前使用 `EDGEGW001`；旧本地模拟可能使用 `EDGE-GW-001`。

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/EDGEGW001/#' -v
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'sys/product/EDGEGW001/#' -v
```

### 4.3 ArduPilot SITL

```bash
export PATH=$PATH:$HOME/.local/bin:$HOME/lxz/ardupilot/Tools/autotest
cd ~/lxz/ardupilot/ArduCopter
sim_vehicle.py -v ArduCopter --out=127.0.0.1:14550
```

通过标准：

```text
ArduPilot Ready
Flight battery 100 percent
GPS 1: detected
```

### 4.4 云端模拟器

Organization 和 status_reply 验收需要云端模拟器先于网关启动：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGEGW001 --bind-status bound --enable-status --max-requests 0
```

### 4.5 Edge Gateway

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

通过标准：

| 日志 | 说明 |
|---|---|
| `MQTT connected to broker` | MQTT 已连接 |
| `Subscribed: thing/product/...` | 订阅完成 |
| `Device online reported` | 设备上线事件已发 |
| `Cloud heartbeat started` | 云端心跳已启动 |
| `Edge Gateway started successfully` | 主循环启动 |

### 4.6 可选 DJI Demo Docker 联调

如需用 DJI Demo Docker 辅助排查联通，先确认容器：

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}' | grep cloud_api_sample
```

Demo 联通参考项：

| 页面 | 通过条件 |
|---|---|
| Dock | `EDGEGW001` 显示 Online，Workspace 正确 |
| Aircraft | `DRONESIM001` 显示 Online |
| Last Online | 时间随消息更新 |

注意：Demo 页面显示 Dock2/M3D 只是联调枚举兼容，不代表最终设备目标，也不作为客户交付证据。最终协议目标仍是 Dock3。

## 5. P0 验收

P0 按顺序执行。任意失败都先修复，不进入 P1。

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate

python tools/mqtt_dock3_p0_batch_test.py --timeout 12
python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_osd_state_test.py --timeout 15 --quiet-stale
python tools/mqtt_dock3_device_test.py --timeout 12 --quiet-stale
python tools/mqtt_organization_test.py --config config/gateway.yaml --action full --organization-id org_sim_001 --sub-device-json '{"sn":"DRONESIM001","domain":"0","type":91,"sub_type":0,"index":"A","device_secret":"drone-secret","nonce":"drone-nonce","thing_version":"1.1.2","online":true,"paired":true}' --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo --expect-bound --publish-events --expect-status-reply --expect-topo-sub-device-count 1 --expect-topo-sub-device-sn DRONESIM001 --validate-topo-schema --verify-timeout 8 --timeout 20
python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale
python tools/mqtt_drc_batch_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale
python tools/e2e_full_chain_test.py --phase p0 --timeout 12 --quiet-stale
```

P0 通过标准：

| 检查项 | 通过条件 |
|---|---|
| P0 批量 | 输出 `dock3 p0 batch test ok` |
| Properties | 输出 `dock3 properties test ok` |
| OSD/State | 输出 `dock3 osd/state test ok`，且 `invalid=0` |
| Device | 输出 `dock3 device topology test ok` |
| Organization | 输出 `organization request test ok` |
| DRC | 输出 `dock3 drc test ok` |
| DRC 批量 | 输出 `drc batch test ok`，且 `ignored_total=0` |
| CMD | 输出 `dock3 cmd test ok` |
| Wayline | 输出 `dock3 wayline test ok` |
| P0 E2E | 输出 `dock3 e2e full-chain test ok`，P0 子阶段 `exit=0` 且 `ignored_total=0` |
| 错误码 | 对外不出现内部 `-1/-2/-3` |
| Topic | `airport_bind_status` 最终走 `requests/requests_reply`，`update_topo` 最终走 `status/status_reply` |

## 6. P1 验收

P1 当前部分脚本尚未实现。30 天交付前必须补齐，并纳入同一 runbook。

```bash
python tools/nest_v13_protocol_test.py --timeout 12
python tools/mqtt_dock3_media_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_live_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale
python tools/mqtt_dock3_firmware_config_test.py --timeout 20
python tools/e2e_takeoff_flow_test.py --timeout 60
python tools/e2e_full_chain_test.py --timeout 12 --quiet-stale
```

P1 通过标准：

| 检查项 | 通过条件 |
|---|---|
| 机巢 v1.3 | 命令、应答、状态、拒绝条件、关键 bit 位全部可测 |
| 一键启航 | 起飞前检查、开盖、断充、释放、起飞、任务、返航、降落、充电可串联 |
| Media | 文件列表、删除、上传优先级、上传事件可用 |
| Live | 开始/停止直播、质量、镜头、状态事件可用；当前 Ubuntu 专项已通过 |
| HMS/Log | 告警事件、日志列表、上传模拟、取消、失败码可用；当前代码和脚本已补，待 Ubuntu 专项复测 |
| Firmware/Config | 升级模拟、进度、配置下发、持久化、回滚可用 |
| 当前核心 E2E | properties、device、OSD/state、CMD、DRC、Wayline、media 可重复通过；live 专项已通过，后续接入 HMS/log |
| 完整 P1 E2E | 从上线绑定到任务、媒体、直播、运维事件可重复通过 |

## 7. P2 验收边界

P2 不要求 30 天内完全无问题，但必须满足：

| 条件 | 要求 |
|---|---|
| 有入口 | method、adapter 或明确配置项存在 |
| 有日志 | 调用后能按 tid/bid 或操作 ID 追踪 |
| 有错误码 | 不支持或缺硬件时返回 Dock3 合理失败码 |
| 有文档 | 记录影响范围、绕过方式、后续计划 |
| 不影响 P0/P1 | P2 异常不能拖垮 MQTT、MAVLink、任务或主状态机 |

Deferred 只适用于客户暂未提及且当前主链路未使用的 Dock3 官方能力。若客户需求、P0/P1 backlog 或 E2E 主流程依赖该能力，不能用 Deferred 作为通过理由。

## 8. 证据归档

每次全链路验收需要保留：

| 证据 | 保存内容 |
|---|---|
| 命令输出 | 每个脚本的完整 stdout/stderr |
| 网关日志 | `logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log` 中对应时间段 |
| MQTT 订阅输出 | 关键 `osd/state/events/reply` 样例 |
| trace 记录 | `tools/request_trace_query.py` 按 tid/method 查询结果 |
| 数据库证据 | 绑定、拓扑、操作记录、告警、媒体记录 |
| 失败清单 | 失败 tid/bid、复现命令、影响 P0/P1/P2、负责人 |

## 9. 常见失败判断

| 现象 | 优先检查 |
|---|---|
| 网关显示 MQTT 未连接 | `ss -tlnp | grep 1883`、Mosquitto 状态、`config/gateway.yaml` |
| 订阅端看到旧格式重复回包 | 清理旧网关进程、旧模拟器、broker retained；再用 `--fail-on-stale` 严格验证 |
| 可选 Demo 页面不显示设备 | SN 是否为 Demo 兼容格式、MySQL 绑定状态、`update_topo` 是否发布；不影响正式脚本验收 |
| `airport_bind_status` 超时 | 云端模拟器是否先启动、是否监听 `requests`、`tid` 是否匹配 |
| `update_topo` 没有入库 | 是否走 `sys/product/{sn}/status`，Demo backend 是否收到 `status_reply` |
| MAVLink 无心跳 | SITL 是否启动、端口是否冲突、配置是否指向正确 UDP/TCP |
| 机巢无状态 | 自定义 dialect 是否加载、12004 是否收到、端口和 TCP 角色是否正确 |
| P1 脚本不存在 | 这是交付缺口，不能记为通过；按 backlog 补脚本 |

## 10. 通过声明模板

一次全链路验收通过时，记录如下内容：

```text
验收日期:
代码分支/commit:
配置文件:
SN:
运行模式: 脚本模拟

P0:
- p0_batch:
- properties:
- osd_state:
- device:
- organization:
- drc:
- drc_batch:
- cmd:
- wayline:

P1:
- nest_v13:
- media:
- live:
- hms_log:
- firmware_config:
- takeoff_flow:
- e2e_full_chain:

P2:
- 已知问题:
- 是否影响 P0/P1:
- 后续计划:

日志位置:
trace 查询:
数据库证据:
结论:
```
