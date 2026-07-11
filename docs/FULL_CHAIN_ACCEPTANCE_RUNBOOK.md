# 全链路验收 Runbook

更新时间：2026-07-10

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
| `tools/mqtt_organization_test.py` | 组织管理专项 | `airport_bind_status/get/bind`、多子设备绑定 |
| `tools/mqtt_dock3_p0_batch_test.py` | P0 基础批量 | property、services、DRC、错误 topic |
| `tools/mqtt_dock3_properties_test.py` | properties 专项 | 可写/只读属性、值域 |
| `tools/mqtt_dock3_device_test.py` | device topology 专项 | `update_topo`、多子设备、在线/离线/重连拓扑、解绑空拓扑 |
| `tools/mqtt_dock3_drc_test.py` | DRC 专项 | enter/exit、杆量、错误值域 |
| `tools/mqtt_drc_batch_test.py` | DRC 批量闭环 | enter/exit、`drc_state`、杆量、飞行器/负载控制、速度控制 |
| `tools/mqtt_dock3_cmd_test.py` | CMD 专项 | 舱盖、充电、推杆、灯、设备动作 |
| `tools/mqtt_dock3_wayline_test.py` | Wayline 专项 | prepare/execute/progress/pause/resume/return |
| `tools/mqtt_dock3_media_test.py` | Media 专项，已通过 Ubuntu 复测 | 文件列表、删除、上传优先级、上传事件 |
| `tools/mqtt_dock3_live_test.py` | Live 专项，已通过 Ubuntu 复测 | `mock://` 直播开始/停止、质量、镜头、状态事件 |
| `tools/mqtt_dock3_hms_log_test.py` | HMS/Log 专项，已通过 Ubuntu 复测 | HMS 告警、日志列表、上传模拟、取消、进度事件和非法参数拒绝 |
| `tests/test_mqtt_background_handlers.py` | P0-16 本地非阻塞回归 | 慢日志/媒体 IO handler 不占用 MQTT 网络回调线程，后台完成后 reply 可达；控制类 handler 保持同步顺序 |
| `tools/e2e_full_chain_test.py` | 当前 P0/P1 核心 MQTT 域 E2E 编排 | properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config |
| `tools/mqtt_smoke_test.py` | 兼容冒烟 | 单 method smoke，不作为最终 P0/P1 正式验收 |

30 天内必须可重复验收：

| 脚本 | 必须覆盖 | 优先级 |
|---|---|---|
| `tools/nest_v13_protocol_test.py` | `DRONE_NEST_COMMAND/RESPONSE/ORDER_RESPONSE`、命令条件、状态位、拒绝原因 | P0/P1 |
| 已通过 `tools/mqtt_dock3_media_test.py` | 文件列表、删除、上传优先级、上传事件 | P1 |
| 已通过 `tools/mqtt_dock3_live_test.py` | 直播开始/停止、质量、镜头、状态事件 | P1 |
| Ubuntu 复测 `tools/mqtt_dock3_hms_log_test.py` | HMS 告警、远程日志列表/上传模拟、取消、失败码 | P1 |
| `tools/mqtt_dock3_firmware_config_test.py` | 固件升级模拟、配置下发、持久化、回滚 | P1 |
| 已通过 `tools/e2e_takeoff_flow_test.py` | 一键启航离线闭环：mock KMZ、机巢 `request_takeoff`、任务完成事件 | P1 |
| `tools/mqtt_one_key_takeoff_flow_test.py` | 一键启航 MQTT 实跑入口：`--simulate` 跑完整闭环，真实链路默认停在任务执行中 | P1 |
| `tools/mqtt_one_key_takeoff_sitl_test.py` | 一键启航真实 SITL 航线验收：默认准备服务器 `/home/fenghuolun/zwz/HD.kml`，`one_key_takeoff_flow` 起飞后切 AUTO，并用 QGC/SITL OSD、航点进度和 `flighttask_pause` 证明真实执行与暂停；测试结束后停在 paused，不发送取消任务或降落清理指令 | P1 |
| 已通过 `tools/e2e_full_chain_test.py` | 11 段 P0/P1 全链路；清理旧进程后继续用 `--fail-on-stale` 做严格验收 | P1 |

P0-16 本地回归命令：

```bash
python -m unittest tests.test_mqtt_background_handlers
```

通过标准：慢日志/媒体 handler 已启动但未完成时，MQTT 回调立即返回；解除 handler 后，同一 `tid` 在正确 reply topic 收到 `result=0`。该单测不替代 Ubuntu 上的长时间 E2E 和 DB/上传超时注入。

2026-07-10 Ubuntu 已通过：`tests.test_mqtt_background_handlers` 为 2/2；随后 `mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale` 输出 `accepted=8, events=5`，`mqtt_dock3_media_test.py --timeout 12 --quiet-stale` 输出 `accepted=6, events=2`。该证据确认日志/媒体后台 handler 的 services reply 和事件链路可用，但不替代后续 DB、上传和长时间 E2E 压测。

## 3. 启动前检查

在 Ubuntu 服务器执行：

```bash
cd ~/lxz/edge_gateway
git status --short --branch
bash tools/ubuntu_preflight.sh

ss -tlnp | egrep ':1883|:8083|:6789|:18083|:14550|:14551|:14552' || true
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

如果 `1883` 没有监听，按脚本输出的 `systemctl status`、`journalctl -u mosquitto`、配置文件行号、重复 `listener` 提示和前台 `mosquitto -c ... -v` 结果修复 Mosquitto。当前常见问题是 `/etc/mosquitto/conf.d/local.conf` 内配置项写错，或 `mosquitto.conf` 与 `conf.d/local.conf` 同时声明 `listener 1883/8084`。

2026-07-09 Ubuntu 复测记录：宿主机 Mosquitto 曾因 `/etc/mosquitto/mosquitto.conf` 与 `/etc/mosquitto/conf.d/local.conf` 同时声明 `listener 1883/8084` 启动失败。删除主配置中的重复 listener 后，`scripts/start_support_stack.sh` 已验证启动成功：MQTT Broker 监听 `1883`，云端模拟器、MQTT 观察端和机巢模拟器均显示“已启动”，Web 观察台和 ArduCopter SITL 因 `START_WEB=0`、`START_SITL=0` 按配置跳过。

### 4.2 MQTT 观察终端

根据当前 SN 选择 topic。DJI Demo 兼容模式当前使用 `EDGEGW001`；旧本地模拟可能使用 `EDGE-GW-001`。

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/EDGEGW001/#' -v
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'sys/product/EDGEGW001/#' -v
```

### 4.3 ArduPilot SITL

```bash
cd ~/lxz/edge_gateway
scripts/start_nest_simulator.sh

# 另开终端；默认启动 ArduPlane quadplane / Changchun_HD
scripts/start_arduplane_sitl.sh
```

ArduPlane 脚本默认生成三路输出：`udp:127.0.0.1:14552` 作为机巢模拟器的 SITL 标准 MAVLink 主入口，`udp:127.0.0.1:14551` 保留本机联调镜像，`udp:192.168.2.59:14551` 提供内网观察。机巢模拟器只把 component `190` 或 `DRONE_NEST_COMMAND` 注册为 14551 网关客户端，防止 SITL 镜像抢占 12004/12005 回包地址。

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
python tools/mqtt_dock3_device_test.py --timeout 12 --quiet-stale --extra-sub-device-json '{"sn":"DRONE-SIM-002","domain":"0","type":60,"sub_type":0,"index":"B","device_secret":"drone-secret-2","nonce":"drone-nonce-2","thing_version":"1.1.2"}'
python tools/mqtt_organization_test.py --config config/gateway.yaml --action full --organization-id org_sim_001 --sub-device-json '{"sn":"DRONESIM001","domain":"0","type":91,"sub_type":0,"index":"A","device_secret":"drone-secret","nonce":"drone-nonce","thing_version":"1.1.2","online":true,"paired":true}' --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo --expect-bound --publish-events --expect-status-reply --expect-topo-sub-device-count 1 --expect-topo-sub-device-sn DRONESIM001 --validate-topo-schema --verify-timeout 8 --timeout 20
python tools/mqtt_organization_test.py --config config/gateway.yaml --action full --organization-id org_sim_001 --sub-device-json '{"sn":"DRONE-SIM-001","domain":"0","type":60,"sub_type":0,"index":"A","device_secret":"drone-secret","nonce":"drone-nonce","thing_version":"1.1.2","online":true,"paired":false}' --sub-device-json '{"sn":"DRONE-SIM-002","domain":"0","type":60,"sub_type":0,"index":"B","device_secret":"drone-secret-2","nonce":"drone-nonce-2","thing_version":"1.1.2","online":true,"paired":false}' --bind-sub-devices --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo --expect-device-binding-count 3 --expect-device-binding-sn EDGEGW001 --expect-device-binding-sn DRONE-SIM-001 --expect-device-binding-sn DRONE-SIM-002 --expect-bound --expect-bound-sn EDGEGW001 --expect-bound-sn DRONE-SIM-001 --expect-bound-sn DRONE-SIM-002 --expect-sub-device-paired-sn DRONE-SIM-001 --expect-sub-device-paired-sn DRONE-SIM-002 --publish-events --expect-status-reply --expect-topo-sub-device-count 2 --expect-topo-sub-device-sn DRONE-SIM-001 --expect-topo-sub-device-sn DRONE-SIM-002 --validate-topo-schema --verify-timeout 8 --timeout 20
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
| Device | 输出 `dock3 device topology test ok`；多子设备命令需显示 `sub_device_count=2`、`reconnect=True`、`unbind=True`、`device_unbinding events ok`，并覆盖离线空拓扑、重连恢复和解绑空拓扑 |
| Organization | 输出 `organization request test ok`；多子设备命令需显示 `device_binding events ok: count=3`、`memory binding ok`、`sub-device paired ok`、`sub_devices=2` 和 `status_reply ok` |
| DRC | 输出 `dock3 drc test ok` |
| DRC 批量 | 输出 `drc batch test ok`，且 `ignored_total=0` |
| CMD | 输出 `dock3 cmd test ok` |
| Wayline | 输出 `dock3 wayline test ok` |
| P0 E2E | 输出 `dock3 e2e full-chain test ok`，P0 子阶段 `exit=0` 且 `ignored_total=0` |
| 错误码 | 对外不出现内部 `-1/-2/-3` |
| Topic | `airport_bind_status` 最终走 `requests/requests_reply`，`update_topo` 最终走 `status/status_reply` |

## 6. P1 验收

P1 当前基础验收入口已补齐。真实硬件、真实推流、真实 OTA 和真实 SITL 航点执行仍按风险项继续推进。

```bash
python tools/nest_v13_protocol_test.py --verbose
python tools/mqtt_dock3_media_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_live_test.py --timeout 12 --quiet-stale
python tools/mqtt_dock3_hms_log_test.py --timeout 20 --quiet-stale
python tools/mqtt_dock3_firmware_config_test.py --timeout 20 --quiet-stale
python tools/e2e_takeoff_flow_test.py --timeout 12
python tools/mqtt_one_key_takeoff_flow_test.py --simulate --timeout 60 --quiet-stale
python tools/mqtt_one_key_takeoff_sitl_test.py --timeout 20 --climb-timeout 30 --mission-observe-timeout 120 --quiet-stale
python tools/e2e_full_chain_test.py --timeout 12 --quiet-stale
```

P1 通过标准：

| 检查项 | 通过条件 |
|---|---|
| 机巢 v1.3 | 命令、应答、状态、拒绝条件、关键 bit 位全部可测 |
| 一键启航 | 当前离线闭环已覆盖 mock KMZ、任务准备、任务执行、机巢 `request_takeoff` 和任务完成事件；真实机巢耗时动作继续实跑 |
| Media | 文件列表、删除、上传优先级、上传事件可用 |
| Live | 开始/停止直播、质量、镜头、状态事件可用；当前 Ubuntu 专项已通过 |
| HMS/Log | 告警事件、日志列表、上传模拟、失败码可用；当前 Ubuntu 专项已通过 |
| Firmware/Config | 升级模拟、进度、取消、结果事件、配置下发、查询、持久化和回滚可用；当前 Ubuntu 专项已通过 |
| 当前核心 E2E | properties、device、OSD/state、CMD、DRC、Wayline、takeoff-flow、media、live、HMS/log、firmware/config 11 段可重复通过 |
| 完整 P1 E2E | 从上线绑定到任务、媒体、直播、运维事件可重复通过；服务器最新输出 `dock3 e2e full-chain test ok (steps=11, elapsed=18.9s)` |

多设备拓扑可通过 E2E device 阶段单独验证：

```bash
python tools/e2e_full_chain_test.py --only device --timeout 12 --quiet-stale --device-extra-sub-device-json '{"sn":"DRONE-SIM-002","domain":"0","type":60,"sub_type":0,"index":"B","device_secret":"drone-secret-2","nonce":"drone-nonce-2","thing_version":"1.1.2"}'
```

服务器已验证该命令输出 `dock3 e2e full-chain test ok (steps=1, elapsed=1.4s)`。

## 6.1 严格旧消息验收

日常联调用 `--quiet-stale` 保持脚本输出简洁；清理旧网关、旧 Web 调试台、旧模拟器和 broker retained 消息后，执行严格验收：

```bash
python tools/mqtt_dock3_osd_state_test.py --timeout 15 --quiet-stale --fail-on-stale
python tools/e2e_full_chain_test.py --timeout 12 --quiet-stale --fail-on-stale
```

严格模式中，`osd-state` 会把旧版 value-wrapper OSD、旧 `reason=property_changed` state、无关 state 和旧 reply 一起计入 stale。properties 严格失败会输出 `stale_samples`，其中包含 `retain/dup/mid/qos`：`retain=true` 优先清理 broker retained 消息，`dup=true` 优先排查 QoS 重投；两者均为 false 时优先排查第二个旧网关/模拟器进程。出现任何 stale 都应先清理环境，再重新执行。

2026-07-10 严格 properties 复测已确认 9 条重复 `property/set_reply` 都是 `method=null`、`retain=false`、`dup=false`，当前 Dock3 回包本身均正确。这不是网关代码的 QoS 重投，也不是 retained 消息；必须先定位并停止第二个旧网关或旧模拟器。停止前先在 Ubuntu 执行只读排查并保留输出：

```bash
pgrep -af 'src/main.py|web/app.py|demo_platform/app.py|mqtt_cloud_simulator.py|nest_simulator.py|mosquitto|nanomq'
ps -eo pid,ppid,etimes,args | grep -E 'src/main.py|web/app.py|demo_platform/app.py|mqtt_cloud_simulator.py|nest_simulator.py' | grep -v grep
ss -tnp | grep ':1883' || true
```

确认旧进程 PID 后，只停止已确认的旧服务并保留当前 `src/main.py` 实例；重启当前网关后，先重跑 properties 严格命令，成功后再跑完整 E2E。

2026-07-10 进度记录：当前服务器仅发现一个 `src/main.py` 实例；`demo_platform/app.py` 仅订阅观测 topic，`mqtt_cloud_simulator.py` 仅订阅 `requests/status`，均不是 `property/set_reply` 的发布者。后续 broker 审计已确认重复回包发布者为旧模拟器客户端 `dock3_sim_EDGEGW001_1783480416`：它对每一个 properties 请求都额外发布缺少 `method` 的 `property/set_reply`，同时发布旧 `state`。当前网关客户端 `edge_gateway_EDGEGW001` 对相同请求只发布一条格式正确的 Dock3 回包。

如果当前网关 request trace 已证明每个 `tid` 仅发布一次、但严格测试仍收到第二条旧回包，可临时启用 broker 发布审计：

```bash
sudo bash scripts/mqtt_broker_audit.sh enable
python tools/mqtt_dock3_properties_test.py --timeout 20 --quiet-stale --fail-on-stale || true
sudo grep -E 'Received PUBLISH|property/set_reply' /var/log/mosquitto/mosquitto.log | tail -n 120
sudo bash scripts/mqtt_broker_audit.sh disable
```

`Received PUBLISH from <client_id>` 会显示旧回包的发布客户端。审计结束必须执行 `disable`，恢复默认 broker 日志量。

已知 client ID 时，可直接从 broker 日志和当前连接中查找其来源：

```bash
sudo bash scripts/mqtt_broker_audit.sh client dock3_sim_EDGEGW001_1783480416
```

日志若显示本机 `IP:port`，用输出提示中的 `lsof` 命令找出 PID 后停止该旧模拟器；若显示远端地址，则在远端主机停止对应旧模拟器。不要停止当前 `edge_gateway_EDGEGW001` 网关。确认旧客户端下线后，再执行严格 properties 测试。

2026-07-10 严格 properties 环境验收已通过：broker 日志确认旧客户端位于 `192.168.2.94:55376`，停止后执行 `python tools/mqtt_dock3_properties_test.py --timeout 20 --quiet-stale --fail-on-stale` 输出 `dock3 properties test ok (accepted=10, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`。下一步是在相同干净环境执行完整严格 E2E。

2026-07-10 机巢 strict 联调已通过：被动 UDP 模拟器以网关首包心跳注册回传地址，并以健康待命 `boolstate1=0x0C47`、`boolstate2=0x0020` 上报 12004 状态。`python tools/e2e_full_chain_test.py --only wayline --timeout 20 --quiet-stale --fail-on-stale` 输出 `dock3 wayline test ok (accepted=11, events=12, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`；验证 12003 命令、12004 状态、12005 回复以及 Wayline 状态机。接下来可直接执行完整 strict E2E。

2026-07-10 完整 strict E2E 已通过：`python tools/e2e_full_chain_test.py --timeout 20 --quiet-stale --fail-on-stale` 输出 `dock3 e2e full-chain test ok (steps=11, elapsed=26.7s)`。全部 11 段均为 `exit=0`；最后 firmware/config 输出 `accepted=8, events=10, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0`。

2026-07-10 数据保留/操作记录专项已通过：`python tools/database_retention_audit.py --config config/gateway.yaml` 在独立 `/tmp/edge_gateway_retention_audit_*` SQLite 库中输出 `deleted={'telemetry': 1, 'operation': 2, 'alarm': 1, 'media_uploaded': 1}`，并验证保留 `telemetry=1`、`operation=2`、`alarm=1`、`media=2`。该脚本拒绝已有路径和项目 `missions.db`，不访问运行网关数据库；`python -m unittest -v tests.test_retention_manager` 输出 `Ran 1 test ... OK`。

2026-07-11 数据库默认运行时已切换为 Ubuntu 本机 MongoDB。切换前必须停止网关并保留 `missions.db` 文件副本，再执行 `python tools/migrate_sqlite_to_mongodb.py migrate-and-verify --sqlite missions.db --config config/gateway.yaml`；脚本只读 SQLite，按表 upsert 到同名 MongoDB 集合，清理已从 SQLite 删除的旧迁移副本，并只对带 `_sqlite_migration` 标记的迁移文档做数量和内容校验。迁移通过后执行 `python tools/mongodb_runtime_audit.py --config config/gateway.yaml --write-check`，必须输出 `MONGODB_CONNECTION_OK`、`MONGODB_INDEX_OK`、`MONGODB_RUNTIME_WRITE_READ_OK domains=8` 和 `MONGODB_RUNTIME_AUDIT_OK`，随后再启动网关运行完整 strict E2E。MongoDB 审计数据带隔离标识并在结束时自动清理。

2026-07-10 相机/云台严格专项已通过：`python tools/mqtt_dock3_payload_test.py --timeout 20 --quiet-stale --fail-on-stale` 输出 `dock3 payload test ok (accepted=12, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`。已验证能力查询、云台转动/模式/复位后的状态回读、拍照、录像启停及重复停止录像返回 `319999`；该结论仅覆盖 MAVLink/SITL 命令发送闭环，真实 Payload 硬件 ACK 需后续专项。

2026-07-10 真实 SITL 航点上传已通过：`python tools/mqtt_dock3_wayline_test.py --real-sitl --timeout 30 --quiet-stale --fail-on-stale` 输出 `dock3 real SITL wayline test ok (accepted=1, events=2, ignored_mismatched=0, ignored_legacy=0, ignored_late=0, ignored_wrong_topic=0, ignored_total=0)`。`flighttask_prepare` 以 `simulate_mission.is_enable=0` 执行，KMZ 解析出 3 个航点并完成飞控上传握手；该安全模式未请求 AUTO/执行或起飞，真实执行需显式附加 `--execute-real`。

2026-07-10 真实 HD.kml 执行与清理已通过：`python tools/mqtt_dock3_wayline_test.py --real-sitl --execute-real --execute-observe-seconds 15 --wayline-file /home/fenghuolun/zwz/HD.kml --timeout 30 --quiet-stale --fail-on-stale` 以非模拟方式上传 10 个航点，`QLAND` 请求回执为 `QSTABILIZE` 且 `armed=true`，`flighttask_execute` 进入 `AUTO`。15 秒后 `flighttask_progress` 返回运行中、第 2/10 航点、20%；随后工具对本次创建的精确任务 ID 自动发送 `flighttask_undo`，收到 `canceled`、状态 9 和相同 flight ID。输出 `dock3 real SITL wayline test ok (accepted=5, events=8, ignored_total=0)`；尚未把 HD.kml 的终态/返航作为通过条件。

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
