# Ubuntu 服务器启动手册

本文档面向参与联调的开发人员，说明当前 Ubuntu 服务器上已经部署了哪些内容、哪些可以直接使用、哪些需要注意，以及从服务器开机或全新终端开始如何启动完整模拟链路。

本文档内容依据前期 Ubuntu 服务器实测对话记录整理，包括 Ubuntu 版本、`.venv`、Mosquitto、ArduPilot SITL、Edge Gateway、Web 调试台和 MQTT 冒烟输出。本地开发机只用于维护本文档和提交 Git，不用于启动服务器服务或代替服务器环境验证。

2026-07-07 后的交付口径以客户需求和 DJI Dock 3 为准：P0/P1 必须 100% 可用，P2 可以存在已记录问题但不能影响主链路。日常启动和排障继续使用本文档；正式全链路验收、P0/P1 命令顺序和证据归档以 `docs/FULL_CHAIN_ACCEPTANCE_RUNBOOK.md` 为准。实际 topic 中的 SN 以 `config/gateway.yaml` 为准，DJI Demo 兼容联调当前使用过 `EDGEGW001`。

当前目标是服务器内纯模拟跑通：

```text
上云 API 模拟器 / MQTT 测试命令
  -> Mosquitto MQTT Broker
  -> Edge Gateway
  -> ArduPilot SITL / MAVLink 机巢模拟器
  -> Edge Gateway
  -> MQTT osd / state / events / reply
```

当前不接实体无人机、飞控、机巢或负载设备。协议目标是 DJI Dock 3，但现有链路仍包含旧 Dock/Dock1 风格字段，Dock3 合规性需要后续继续迁移和验收。

## 一、服务器上已有内容

| 内容 | 当前状态 | 默认位置/端口 | 是否可直接使用 | 说明 |
|---|---|---|---|---|
| Ubuntu | 已部署 | Ubuntu 22.04.5 LTS | 是 | 来自前期服务器实测输出 |
| Python | 已部署 | `/usr/bin/python3`，版本 3.10.12 | 是 | 系统 Python 用于 ArduPilot/SITL |
| 项目代码 | 已部署 | `~/lxz/edge_gateway` | 是 | Edge Gateway 主项目 |
| 项目虚拟环境 | 已部署 | `~/lxz/edge_gateway/.venv` | 是 | 网关、工具脚本、Web 调试台使用 |
| Python 依赖 | 已安装 | `requirements-ubuntu.txt` | 是 | 已验证 `yaml/paho.mqtt/pymavlink/sqlalchemy/pydantic` 可导入 |
| MQTT Broker | 已部署 | Mosquitto，`127.0.0.1:1883` | 是 | 当前用 Mosquitto 替代 NanoMQ |
| NanoMQ | 不可用 | 曾安装官方 deb | 否 | Ubuntu 22.04 下官方包要求更高 glibc，不能直接用 |
| ArduPilot | 已部署 | `~/lxz/ardupilot` | 是 | SITL 模拟飞控 |
| `sim_vehicle.py` | 已可用 | `~/lxz/ardupilot/Tools/autotest/sim_vehicle.py` | 是 | 启动 ArduCopter SITL |
| MAVProxy | 已可用 | `~/.local/bin/mavproxy.py` | 是 | SITL 手动飞控命令行 |
| 网关配置 | 已可用 | `config/gateway.yaml` | 是 | 默认 MQTT `1883`，MAVLink UDP `14550` |
| MQTT 冒烟脚本 | 已可用 | `tools/mqtt_smoke_test.py` | 是 | 验证 services/property/drc topic 和 reply |
| Web 调试台 | 已可用 | `web/app.py`，端口 `8090` | 是 | 独立程序，用于看 MQTT、发测试命令、启动 SITL |
| 机巢模拟器 | 有脚本 | `src/nest_simulator.py`，默认 `14550` | 需单独模式使用 | 不能和 SITL 同时直接占用同一个 MAVLink 端口 |

## 二、当前端口和进程约定

| 端口/地址 | 用途 | 当前配置来源 |
|---|---|---|
| `127.0.0.1:1883` | MQTT Broker | `config/gateway.yaml` 的 `nanomq.host/port`，实际由 Mosquitto 提供 |
| `127.0.0.1:14550` | MAVLink UDP | `config/gateway.yaml` 的 `mavlink.connection.port` |
| `0.0.0.0:8090` | Web 调试台 | `web/app.py` 默认 `WEB_PORT=8090` |
| `thing/product/EDGE-GW-001/#` | 网关 MQTT 业务 topic | `config/gateway.yaml` 的 `gateway.sn` |
| `sys/product/EDGE-GW-001/#` | 网关系统 topic | `config/gateway.yaml` 的 `gateway.sn` |

注意：项目日志里可能仍写着 `Connecting to NanoMQ`，这是代码里的命名历史。只要连接的是 `127.0.0.1:1883`，当前实际 Broker 可以是 Mosquitto。

## 三、日志落盘规则

网关启动后会在项目目录下自动创建日志目录：

```text
~/lxz/edge_gateway/logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log
```

示例：

```text
~/lxz/edge_gateway/logs/2026-07/edge_gateway_2026-07-06.log
```

日志配置在 `config/gateway.yaml`：

```yaml
logging:
  level: "INFO"
  dir: "./logs"
  filename_prefix: "edge_gateway"
  console: true
  capture_print: true
  daily_files: true
  monthly_dirs: true
```

日志实现要求：

| 项目 | 当前规则 |
|---|---|
| 月份目录 | 自动按 `YYYY-MM` 创建 |
| 每日日志 | 每天一个 `edge_gateway_YYYY-MM-DD.log` |
| 终端输出 | 保留 |
| 文件落盘 | 保留 |
| 旧模块 `print` | 自动捕获到日志 |
| 写文件方式 | 后台日志队列写入，避免 MQTT/MAVLink 热路径直接刷盘 |
| 异常记录 | 未捕获异常、线程异常、handler 异常会带 traceback |
| 字段标注 | 关键 MQTT payload/reply/state/event 会输出 `字段名(中文名)` |

常用查看命令：

```bash
cd ~/lxz/edge_gateway

# 查看当天日志目录
ls -lh logs/$(date +%Y-%m)/

# 实时跟踪当天日志
tail -f logs/$(date +%Y-%m)/edge_gateway_$(date +%F).log

# 只看错误和警告
grep -E 'ERROR|WARNING|CRITICAL' logs/$(date +%Y-%m)/edge_gateway_$(date +%F).log

# 按 tid 追踪一次 MQTT 请求
grep '你的tid' logs/$(date +%Y-%m)/edge_gateway_$(date +%F).log

# 查找带中文标注的关键字段
grep -E 'drc_state\\(DRC链路状态\\)|capacity_percent\\(电量百分比\\)|longitude\\(经度\\)' logs/$(date +%Y-%m)/edge_gateway_$(date +%F).log
```

重点应能在日志中看到：

- 网关启动、配置加载、模块初始化。
- MQTT 连接、异常断线、自动重连、订阅 topic。
- MAVLink 连接、心跳、Dispatcher 启动、模块启动。
- 每次 MQTT 下行命令的 `topic/method/tid/bid/need_reply`。
- 每次 handler 执行结果和回包 `reply_topic/result`，并带关键字段中文标注，例如 `capacity_percent(电量百分比)`、`drc_state(DRC链路状态)`。
- 任务、航线、DRC、机巢、媒体、日志上传等关键业务过程。
- warning/error/critical 级别 traceback。

示例日志片段：

```text
MQTT command received: ... payload={"method(方法)":"property_get","data(数据)":{"properties":["drc_state"]}}
MQTT handler completed: ... reply={"data(数据)":{"result(结果码)":0,"drc_state(DRC链路状态)":2}}
MQTT state publish: ... data={"property(属性)":{"silent_mode(静音模式)":1}}
```

## 四、启动前检查

每次新开发人员接手 Ubuntu 服务器时，先在服务器上运行：

```bash
cd ~/lxz/edge_gateway
git status --short --branch
```

确认代码分支和本地改动。多人协同时不要直接覆盖他人的未提交修改。

检查关键命令：

```bash
which python3
python3 --version

cd ~/lxz/edge_gateway
source .venv/bin/activate
python - <<'PY'
import yaml
import paho.mqtt.client
import pymavlink
import sqlalchemy
import pydantic
print("edge_gateway python deps ok")
PY
deactivate
```

检查 MQTT 和 SITL 工具：

```bash
systemctl status mosquitto --no-pager
ss -tlnp | grep ':1883' || true

export PATH=$PATH:$HOME/.local/bin:$HOME/lxz/ardupilot/Tools/autotest
which sim_vehicle.py
which mavproxy.py
```

也可以运行只读预检：

```bash
cd ~/lxz/edge_gateway
bash tools/ubuntu_preflight.sh
```

`tools/ubuntu_preflight.sh` 只检查文件和系统状态，不下载、不安装、不启动服务。

## 五、每日标准启动顺序

推荐至少开 4 个终端。下面命令默认服务器目录为 `~/lxz/edge_gateway` 和 `~/lxz/ardupilot`。

### 终端 1：启动 MQTT Broker

```bash
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager
ss -tlnp | grep ':1883'
```

验收：

```text
1883 端口处于 LISTEN
```

如果 `1883` 已被 Mosquitto 占用，这是正常状态。不要再启动 NanoMQ 抢同一个端口。

### 终端 2：启动 ArduPilot SITL

```bash
export PATH=$PATH:$HOME/.local/bin:$HOME/lxz/ardupilot/Tools/autotest

cd ~/lxz/ardupilot/ArduCopter
sim_vehicle.py -v ArduCopter --out=127.0.0.1:14550
```

等待输出中出现：

```text
ArduPilot Ready
GPS 1: detected u-blox
Flight battery 100 percent
```

SITL 终端通常会进入 MAVProxy 交互环境。后续可在这个终端直接输入飞控命令：

```text
mode GUIDED
arm throttle
takeoff 10
mode LAND
```

如果当前终端处于项目 `.venv` 中，建议先退出再启动 SITL：

```bash
deactivate
```

原因是 ArduPilot 环境依赖安装在系统 Python 和用户目录 `~/.local` 下，不建议用项目 `.venv` 拉起 `sim_vehicle.py`。

### 终端 3：启动 Edge Gateway

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

正常启动时应看到：

```text
MQTT connected to NanoMQ
[Connection] 已连接到系统 1
[Dispatcher] 消息分发器已启动
MAVLink dispatcher started
Edge Gateway started successfully
Entering async main loop
```

如果看到：

```text
[Connection] 心跳超时
Failed to connect to MAVLink
```

通常表示终端 2 的 SITL 没启动、端口不一致，或 SITL 没有输出到 `127.0.0.1:14550`。

### 终端 4：启动 Web 调试台或命令行监控

Web 调试台：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python -m pip install -r web/requirements.txt

SITL_PYTHON=/usr/bin/python3 \
ARDUPILOT_DIR=$HOME/lxz/ardupilot \
python web/app.py
```

浏览器访问：

```text
http://Ubuntu服务器IP:8090
```

如果只想用命令行看 MQTT：

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/EDGE-GW-001/#' -v
```

正常时会持续看到：

```text
thing/product/EDGE-GW-001/osd {...}
thing/product/EDGE-GW-001/events {... "method": "heartbeat" ...}
thing/product/EDGE-GW-001/state {...}
```

## 六、最小可用验收流程

完整启动后，按以下顺序验收。

### 1. MQTT 基础连通

```bash
mosquitto_pub -h 127.0.0.1 -p 1883 \
  -t 'thing/product/EDGE-GW-001/test' \
  -m '{"hello":"edge_gateway"}'
```

如果另一个 `mosquitto_sub` 终端能看到消息，说明 Broker 正常。

### 2. 网关 services 冒烟

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_smoke_test.py --method dock_status_query --timeout 8
```

成功标志：

```text
published dock_status_query -> thing/product/EDGE-GW-001/services
received <- thing/product/EDGE-GW-001/services_reply
"method": "dock_status_query"
"result": 0
```

说明：当前 `dock_status_query` 能证明机场状态查询链路和实时遥测同步可通，但不能单独代表 Dock3 字段全量合规。

### 2.5 Dock3 organization 云端模拟器验收

Dock3 官方组织管理里的 `airport_bind_status` 是设备主动发 `requests`，云端回 `requests_reply`。这个验证不通过 `mqtt_smoke_test.py` 主动下发，而是需要先启动云端模拟器，再重启网关触发启动自动查询。

终端A，先启动云端模拟器：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --once --bind-status unbound --timeout 90
```

终端B，重启网关：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

成功标志：

```text
cloud simulator subscribed -> thing/product/EDGE-GW-001/requests
received <- thing/product/EDGE-GW-001/requests
"method": "airport_bind_status"
published -> thing/product/EDGE-GW-001/requests_reply
```

网关日志成功标志：

```text
MQTT request publish: topic=thing/product/EDGE-GW-001/requests
Device-originated request observed, skip downstream routing
Request reply: method=airport_bind_status
```

说明：`python tools/mqtt_smoke_test.py --method airport_bind_status --timeout 8` 仍可用于 services 兼容 smoke，但它不是 Dock3 organization 的最终验收口径。

### 2.6 Dock3 organization 查询/绑定验收

`airport_organization_get` 和 `airport_organization_bind` 也是设备主动发 `requests`，云端回 `requests_reply`。建议先停止主网关，避免主网关旁路收到测试工具的 `requests_reply` 并记录 `Unexpected/late requests_reply`。

终端A，启动云端模拟器并允许回复两个请求：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --max-requests 2 --timeout 120 \
  --organization-id org_sim_001 --organization-name 模拟组织 --device-callsign EDGE-GW-001
```

终端B，发起完整 organization 查询和绑定流程：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_organization_test.py --action full --organization-id org_sim_001 \
  --binding-code SIM-BIND-CODE --device-callsign EDGE-GW-001 --device-model-key dock3-sim --timeout 20
```

成功标志：

```text
published airport_organization_get -> thing/product/EDGE-GW-001/requests
received <- thing/product/EDGE-GW-001/requests_reply, method=airport_organization_get
published airport_organization_bind -> thing/product/EDGE-GW-001/requests
received <- thing/product/EDGE-GW-001/requests_reply, method=airport_organization_bind
organization request test ok
```

绑定后事件与持久化验收。终端A：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --max-requests 2 --timeout 120 \
  --organization-id org_sim_001 --organization-name 模拟组织 --device-callsign EDGE-GW-001
```

终端B：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_organization_test.py --action full --organization-id org_sim_001 \
  --binding-code SIM-BIND-CODE --device-callsign EDGE-GW-001 --device-model-key dock3-sim \
  --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo \
  --expect-bound --verify-db --verify-timeout 8 --timeout 20
```

成功标志：

```text
event observed: method=device_binding
event observed: method=update_topo
expected events observed: device_binding, update_topo
memory binding ok: sn=EDGE-GW-001, organization_id=org_sim_001
"db_binding": {
  "gateway_sn": "EDGE-GW-001",
  "organization_id": "org_sim_001",
  "is_bound": true
}
organization request test ok
```

绑定后拓扑上报和 `status_reply` 验收。终端A 同时模拟 organization 回包和 `update_topo` 状态确认：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --enable-status --max-requests 3 --timeout 120 \
  --organization-id org_sim_001 --organization-name 模拟组织 --device-callsign EDGE-GW-001
```

终端B：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_organization_test.py --action full --organization-id org_sim_001 \
  --binding-code SIM-BIND-CODE --device-callsign EDGE-GW-001 --device-model-key dock3-sim \
  --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo \
  --expect-bound --verify-db --publish-events --expect-status-reply --verify-timeout 8 --timeout 20
```

成功标志：

```text
event observed: method=update_topo
published update_topo -> sys/product/EDGE-GW-001/status
received <- sys/product/EDGE-GW-001/status_reply, method=update_topo
status_reply ok: method=update_topo, result=0
organization request test ok
```

说明：这是 device topology 的正式链路验收入口。`--publish-events` 会把 `device_binding` 发布到 `thing/product/{sn}/events`，把 `update_topo` 发布到 `sys/product/{sn}/status`。

2026-07-06 已复测通过：

| 项目 | 事务 ID | 结果 |
|---|---|---|
| `airport_organization_get` | `3ab6639d-c94d-4c5f-9a5c-9589f8f810c7` | `organization_name=模拟组织` |
| `airport_organization_bind` | `7a7f871f-68a7-483e-b179-3a608c5b1b15` | `output.err_infos=[]` |
| `device_binding` event | `b7cc6aa4-03fb-492b-864c-7055838b2219` | 已发布到 `thing/product/EDGE-GW-001/events` |
| `update_topo` status | `f0845cda-bf5b-40b2-a0f7-d2e4cbb65c6b` | 已发布到 `sys/product/EDGE-GW-001/status`，收到 `status_reply result=0` |

多子设备拓扑验收。终端A：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --enable-status --max-requests 3 --timeout 120 \
  --organization-id org_sim_001 --organization-name 模拟组织 --device-callsign EDGE-GW-001 \
  --expect-status-sub-device-count 1 --expect-status-sub-device-sn DRONE-SIM-001 \
  --validate-status-topo-schema
```

终端B：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_organization_test.py --action full --organization-id org_sim_001 \
  --binding-code SIM-BIND-CODE --device-callsign EDGE-GW-001 --device-model-key dock3-sim \
  --sub-device-json '{"sn":"DRONE-SIM-001","domain":"0","type":0,"sub_type":0,"index":"A","device_secret":"sim-secret","nonce":"sim-nonce","thing_version":"1.1.2","online":true,"paired":true}' \
  --expect-organization-name 模拟组织 --expect-event device_binding --expect-event update_topo \
  --expect-topo-sub-device-count 1 --expect-topo-sub-device-sn DRONE-SIM-001 \
  --validate-topo-schema --expect-bound --verify-db --publish-events \
  --expect-status-reply --verify-timeout 8 --timeout 20
```

成功标志：

```text
simulated sub-device added: sn=DRONE-SIM-001
update_topo schema ok: method=update_topo, sub_devices=1
published update_topo -> sys/product/EDGE-GW-001/status
status_reply ok: method=update_topo, result=0
organization request test ok
```

2026-07-06 多子设备拓扑已复测通过：

| 项目 | 事务 ID | 结果 |
|---|---|---|
| `airport_organization_get` | `cee4606d-7f6d-41dd-86f6-bd7f6191d570` | `organization_name=模拟组织` |
| `airport_organization_bind` | `352a71ac-133c-426f-b5a5-6f1f3f05a19a` | `output.err_infos=[]` |
| `device_binding` event | `c5b34598-1165-4ece-9ed6-d422eea41bd9` | 已发布到 `thing/product/EDGE-GW-001/events` |
| `update_topo` status | `f318713c-767a-43d9-a4b2-13ade3a969b2` | `sub_devices[0].sn=DRONE-SIM-001`，收到 `status_reply result=0` |

说明：`domain/type/sub_type` 当前仍是模拟值，先用于验证 `sub_devices[]` 非空链路。人员1后续需要按 Dock3 device 文档固定官方枚举。

异常分支验收一：组织查询失败。终端A：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --max-requests 1 --timeout 120 \
  --organization-get-result 327000 --organization-get-message 绑定码无效
```

终端B：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_organization_test.py --action get --organization-id org_sim_001 \
  --binding-code BAD-CODE --expect-get-result 327000 --timeout 20
```

异常分支验收二：绑定部分失败，云端返回 `output.err_infos`。终端A：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --max-requests 1 --timeout 120 \
  --organization-bind-fail-sn EDGE-GW-001 --organization-bind-err-code 327000 \
  --organization-bind-err-msg 设备绑定失败
```

终端B：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_organization_test.py --action bind --organization-id org_sim_001 \
  --binding-code SIM-BIND-CODE --device-callsign EDGE-GW-001 --device-model-key dock3-sim \
  --expect-bind-error-count 1 --timeout 20
```

异常分支验收三：云端回包超时。终端A：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_cloud_simulator.py --sn EDGE-GW-001 --max-requests 1 --timeout 120 --reply-delay 5
```

终端B：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_organization_test.py --action get --organization-id org_sim_001 \
  --binding-code SIM-BIND-CODE --expect-timeout --timeout 2
```

### 2.7 Dock3 P0 接口和错误码验收

人员1验证 Dock3 P0 链路时，继续在项目虚拟环境中执行：

如果当前只验证“设备属性 properties”大类，先跑专项脚本：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale
```

成功标志：

```text
dock3 properties test ok (accepted=..., ignored_mismatched=..., ignored_legacy=..., ignored_late=..., ignored_total=...)
```

该脚本覆盖：`silent_mode`、`air_transfer_enable`、`user_experience_improvement` 成功设置和回读，`battery_store_mode`、`alarm_state`、`drc_state`、未知属性、非法值拒绝。清理旧网关/旧模拟器进程后，可加 `--fail-on-stale` 做严格环境验收：

```bash
python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale --fail-on-stale
```

如果当前只验证“设备管理 device/topology”大类，跑专项脚本：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_dock3_device_test.py --timeout 12
```

成功标志：

```text
dock3 device topology test ok (status_messages=2, sub_device_sn=DRONE-SIM-001, accepted=3, ignored_legacy=...)
```

该脚本会：

1. 发送测试触发用 `services:update_topo` 给运行中的网关。
2. 验证网关发布官方 `sys/product/{sn}/status`、`method=update_topo`。
3. 模拟云端发布 `sys/product/{sn}/status_reply`。
4. 验证在线拓扑 `sub_devices[0].sn=DRONE-SIM-001`。
5. 验证离线拓扑 `sub_devices=[]`。
6. 验证缺少官方必填字段的无效拓扑返回 `325001`，且不产生 status 上报。

说明：`services:update_topo` 只作为本地测试触发器使用，Dock3 官方最终方向仍是 `status/status_reply`。

如果当前只验证“指令飞行 DRC”大类，优先跑专项脚本：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_dock3_drc_test.py --timeout 12 --quiet-stale
```

成功标志：

```text
dock3 drc test ok (accepted=..., ignored_total=...)
```

2026-07-06 已复测通过：

```text
dock3 drc test ok (accepted=9, ignored_mismatched=0, ignored_legacy=4, ignored_late=1, ignored_total=5)
```

该次复测确认：

| 项目 | 结果 |
|---|---|
| `drc_mode_exit` 清理旧状态 | 未启用时返回 `319999`，脚本允许作为清理状态 |
| `drc_mode_enter` 官方 payload | `services_reply result=0` |
| `property_get(drc_state)` | enter 后返回 `2`，exit 后返回 `0` |
| 官方 `stick_control` 杆量 | `need_reply=0` 时无 `drc/up` 回包，符合当前 Dock3 控制数据口径 |
| 非法 `stick_control` 杆量 | 返回 `325001`，示例 `pitch must be in range 364..1684` |
| 官方 `drone_control` 字段 | 正常 `seq/x/y/h/w` 返回 `0` |
| 非法 `drone_control` 字段 | 返回 `325001`，示例 `x must be in range -17..17 m/s` |
| `drc_gimbal_speed_push` | 返回 `0` |

该脚本会：

1. 先发 `drc_mode_exit` 清理旧 DRC 状态。
2. 使用官方 `mqtt_broker/osd_frequency/hsi_frequency` payload 进入 DRC。
3. 回读 `property_get(drc_state)`，确认 enter 后为 `2`。
4. 发送官方 `stick_control` 杆量字段 `roll/pitch/throttle/yaw/gimbal_pitch`，按 Dock3 “无回包机制”验证不产生 `drc/up`。
5. 发送非法杆量，验证返回 `325001`。
6. 发送官方废弃 `drone_control` 字段 `seq/x/y/h/w`，验证成功；再发越界字段，验证返回 `325001`。
7. 验证 `drc_gimbal_speed_push` 基础回包。
8. 退出 DRC 并回读 `drc_state=0`。

说明：旧的 `tools/mqtt_drc_batch_test.py` 仍用于兼容控制数据回归；新的 `tools/mqtt_dock3_drc_test.py` 是 Dock3 DRC 大类专项入口。

如果当前只验证“CMD/远程调试”大类，跑专项脚本：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_dock3_cmd_test.py --timeout 12 --quiet-stale
```

成功标志：

```text
dock3 cmd test ok (accepted=..., events=..., ignored_total=...)
```

2026-07-06 Ubuntu 已验证成功，实际输出：

```text
dock3 cmd test ok (accepted=55, events=13, ignored_mismatched=0, ignored_legacy=55, ignored_late=0, ignored_total=55)
```

如果脚本曾看到同一个 `tid`、同一个 method，但回包缺少 `data.output.status` 且 `result=0`，说明 broker 上仍有旧版网关或旧 handler 抢答。新版脚本会把这类旧成功回包计入 `ignored_legacy` 并继续等待正确 Dock3 回包；非 0 错误回包会直接打印出来，不再静默忽略。如果最终超时，先用 `pgrep -af 'src/main.py|web/app.py|mqtt'` 清理重复进程，再重启唯一网关实例。

该脚本会一次覆盖：

| 分组 | 覆盖 method | 验证点 |
|---|---|---|
| 调试模式 | `debug_mode_open`、`debug_mode_close` | `services_reply result=0`、`output.status=ok`、`mode_code` 在 `2/0` 间切换 |
| 补光灯 | `supplement_light_open`、`supplement_light_close` | `supplement_light_state` 在 `1/0` 间切换 |
| 电池/空调/报警/SDR | `battery_maintenance_switch`、`air_conditioner_mode_switch`、`alarm_state_switch`、`battery_store_mode_switch`、`sdr_workmode_switch` | 官方 `action/link_workmode` 枚举校验，非法值返回 `325001` |
| 舱体动作 | `cover_open/close`、`charge_open/close`、`putter_open/close` | `services_reply output.status=ok`，同 method 进度事件，状态字段联动 |
| 飞行器/设备动作 | `drone_open/close`、`drone_format`、`device_format`、`format_sd_card`、`device_reboot`、`device_reset` | 回包和进度事件；`device_format/format_sd_card` 默认模拟，不会删除服务器媒体目录；`device_reboot` 默认模拟，不会真实重启服务器 |
| 安全拒绝 | `cover_force_close` | 飞行器仍在舱内时返回 `314000`、`output.status=rejected` |

注意：如果需要真实清理媒体目录，必须显式配置 `device.allow_storage_format=true`；如果需要真实重启服务器，必须显式配置 `device.allow_system_reboot=true`。默认纯模拟联调不删除媒体目录，也不执行真实 `sudo reboot`。

如果当前只验证“Wayline/航线管理”大类，跑专项脚本：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_dock3_wayline_test.py --timeout 12 --quiet-stale
```

成功标志：

```text
dock3 wayline test ok (accepted=..., events=..., ignored_total=...)
```

2026-07-06 已在 Ubuntu 纯模拟环境验证成功，实际输出：

```text
dock3 wayline test ok (accepted=11, events=8, ignored_mismatched=0, ignored_legacy=11, ignored_late=0, ignored_total=11)
```

该脚本会一次覆盖：

| 分组 | 覆盖 method | 验证点 |
|---|---|---|
| 任务准备 | `flighttask_prepare` | 官方 `flight_id/task_type/execute_time/file/simulate_mission`，`services_reply output.status=sent` |
| 任务执行 | `flighttask_execute` | `output.status=in_progress`，产生 `flighttask_progress` 事件 |
| 任务进度 | `flighttask_progress` | 回包包含 `output.progress.current_step/percent` 和 `output.ext.flight_id` |
| 暂停/恢复 | `flighttask_pause`、`flighttask_resume` | `output.status=paused/in_progress`，状态不支持返回 `314000` 和 `output.status=rejected` |
| 返航/取消返航 | `flighttask_return`、`return_home_cancel` | 返航产生 `flighttask_progress` 和 `return_home_info` 事件 |
| 任务取消 | `flighttask_undo` | `output.status=canceled` |
| 参数错误 | 非法 `flighttask_prepare` | 返回 DJI 错误码 `325001` |

说明：该脚本使用 `simulate_mission.is_enable=1`，当前只验证上云 API 协议状态机，不要求真实 KMZ 下载或真实飞控执行航线。人员2后续再补 SITL 航点执行、飞控模式和返航语义。

如果 `flighttask_execute` 返回 `319999`，并且 `msg` 类似 `模式切换超时，当前模式: MANUAL`，说明运行中的网关仍是旧代码路径：纯模拟任务不应切真实 `AUTO` 模式。停止终端 2 的网关进程，重新执行 `python src/main.py -c config/gateway.yaml` 后再跑本专项脚本。

优先使用总批量脚本，一次覆盖 `dock_status_query`、属性读写、只读属性拒绝、DRC enter/control/exit、错误 topic：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_dock3_p0_batch_test.py --timeout 12 --quiet-stale
```

成功标志：

```text
dock3 p0 batch test ok (accepted=..., ignored_mismatched=..., ignored_legacy=..., ignored_late=..., ignored_total=...)
```

如果脚本打印：

```text
ignored stale/mismatched reply
ignored legacy stale reply
```

说明同一个 `tid` 收到了旧格式、非当前 method，或旧版负数错误码回包，通常是本地 broker 上还有旧版网关、Web 调试台、模拟器或历史进程在抢答。脚本会忽略这类回包并继续等待正确回包；如果最终超时，需要先按“十一、故障处理”排查重复进程。

日常验收建议带 `--quiet-stale`，只在最后打印统计；清理 broker 环境后可加 `--fail-on-stale`，把任何旧格式重复回包都作为失败：

```bash
python tools/mqtt_dock3_p0_batch_test.py --timeout 12 --quiet-stale --fail-on-stale
python tools/mqtt_drc_batch_test.py --timeout 12 --quiet-stale --fail-on-stale
```

如果 `--fail-on-stale` 失败，先排查是否还有旧进程或 broker 规则在抢答：

```bash
pgrep -af 'python|uvicorn|gunicorn|flask|mqtt|mosquitto|nanomq|node|pm2'
ss -tnp | grep ':1883' || true
```

如果总批量脚本失败，再按下面单项命令定位具体分组。

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate

python tools/mqtt_smoke_test.py --method property_set --payload '{"silent_mode":1}' --timeout 8
python tools/mqtt_smoke_test.py --method property_get --payload '{"properties":["silent_mode","drc_state"]}' --timeout 8
python tools/mqtt_smoke_test.py --method property_set --payload '{"air_transfer_enable":true}' --timeout 8
python tools/mqtt_smoke_test.py --method property_set --payload '{"user_experience_improvement":2}' --timeout 8
python tools/mqtt_smoke_test.py --method drc_mode_enter --payload '{}' --timeout 12
python tools/mqtt_smoke_test.py --method property_get --payload '{"properties":["drc_state"]}' --timeout 8
python tools/mqtt_smoke_test.py --method drc_mode_exit --payload '{}' --timeout 12
```

`airport_bind_status` 不放在 P0 总批量必过项里。Dock3 官方 organization 验证应按上文 2.5 使用 `requests/requests_reply` 云端模拟器流程；services 版只保留为历史兼容 smoke。如果确实要顺手测兼容路径，可以单独执行：

```bash
python tools/mqtt_smoke_test.py --method airport_bind_status --timeout 8
# 或让总批量脚本额外包含该兼容项
python tools/mqtt_dock3_p0_batch_test.py --timeout 12 --quiet-stale --include-legacy-bind-status
```

错误 topic 验收：

```bash
python tools/mqtt_smoke_test.py --channel drc --method drc_mode_enter --payload '{}' --timeout 8
```

成功标志：

```text
received <- thing/product/EDGE-GW-001/drc/up
"result": 325001
```

说明：`325001` 来自 DJI 上云 API 官方错误码，表示云端下发命令不符合格式要求。网关回包不得再出现 `result=-1/-2/-3`。

DRC 控制数据批量验收：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python tools/mqtt_drc_batch_test.py --timeout 12
```

该命令会一次完成：

| 步骤 | Topic | Method | 期望 |
|---|---|---|---|
| 清理旧状态 | `services` | `drc_mode_exit` | `result=0` 或已未启用错误码 |
| 进入 DRC | `services` | `drc_mode_enter` | `services_reply result=0` |
| 状态回读 | `services` | `property_get` | `drc_state=2` |
| 摇杆控制 | `drc/down` | `stick_control` | `drc/up result=0` |
| 飞行控制 | `drc/down` | `drone_control` | `drc/up result=0` |
| 负载控制 | `drc/down` | `payload_control` | `drc/up result=0` |
| 速度推送 | `drc/down` | `drc_speed_push` | `drc/up result=0` |
| 云台速度 | `drc/down` | `drc_gimbal_speed_push` | `drc/up result=0` |
| 退出 DRC | `services` | `drc_mode_exit` | `services_reply result=0` |
| 状态回读 | `services` | `property_get` | `drc_state=0` |

成功标志：

```text
drc batch test ok
```

properties Batch 1 只读字段验收：

```bash
python tools/mqtt_smoke_test.py --method property_set --payload '{"battery_store_mode":2}' --timeout 8
python tools/mqtt_smoke_test.py --method property_set --payload '{"alarm_state":1}' --timeout 8
```

成功标志：回包仍然是 `property/set_reply`，但对应字段的 `state.result` 不是 `0`，表示该字段不能通过 `property/set` 直接修改。相关控制后续应走 Dock3 对应 services method。

### 3. MAVLink 遥测验收

观察 MQTT `osd`：

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/EDGE-GW-001/osd' -v
```

应出现经纬度、高度、姿态、电池、GPS 等字段：

```text
latitude
longitude
height
altitude
attitude_pitch
attitude_roll
battery
position_state
```

### 4. 手动飞控验收

在 SITL/MAVProxy 终端输入：

```text
mode GUIDED
arm throttle
takeoff 10
mode LAND
```

验收：

- MQTT `osd.height` 从 0 上升到约 10。
- 降落后 `osd.height` 回到 0 附近。
- MQTT `state` 出现 `armed` 或 `flight_mode` 变化。

## 七、Web 调试台怎么用

Web 调试台是独立程序，不嵌入主网关。它适合替代大段 JSON 终端输出。

启动后看顶部状态：

| 状态 | 正常值 | 含义 |
|---|---|---|
| MQTT | 已连接 | Web 已连上 `127.0.0.1:1883` |
| MAVLink | `udpout:127.0.0.1:14550` | Web 控制按钮会向 SITL 发 MAVLink 命令 |
| SITL | 运行中或未运行 | 只表示 Web 拉起的 SITL 进程状态 |

如果终端 2 已经手动启动 SITL，不建议再点 Web 的“启动 SITL”，否则可能端口冲突。

Web 常用操作：

1. 查看 MQTT 消息流和遥测卡片。
2. 发送 `dock_status_query` 冒烟测试。
3. 在没有手动 SITL 终端时，点击“启动 SITL”。
4. 按顺序点击 `切到 GUIDED`、`解锁电机`、`起飞`、`降落`。

Web 启动 SITL 报 `ModuleNotFoundError: No module named 'pexpect'` 时，执行：

```bash
/usr/bin/python3 -m pip install --user --upgrade pexpect ptyprocess MAVProxy pymavlink
```

然后重启 Web：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
SITL_PYTHON=/usr/bin/python3 ARDUPILOT_DIR=$HOME/lxz/ardupilot python web/app.py
```

## 八、机巢模拟器启动方式

当前有 `src/nest_simulator.py`，用于模拟 MAVLink 自定义机巢消息：

- `DRONE_NEST_RESPONSE`
- `DRONE_NEST_ORDER_RESPONSE`
- `DRONE_NEST_COMMAND`

单独验证机巢时启动：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/nest_simulator.py
```

默认监听：

```text
0.0.0.0:14550
```

重要限制：

- SITL 和 `nest_simulator.py` 默认都涉及 `14550`。
- 当前 `config/gateway.yaml` 只有一个 MAVLink 连接入口。
- 不要在同一个端口上直接同时跑 SITL 和机巢模拟器。

推荐模式：

| 模式 | 启动内容 | 目的 |
|---|---|---|
| UAV-only | Mosquitto + SITL + Edge Gateway | 验证无人机遥测和飞控命令 |
| Dock-only | Mosquitto + `nest_simulator.py` + Edge Gateway | 验证机巢自定义 MAVLink 消息 |
| Combined | Mosquitto + SITL + 机巢模拟器 + MAVLink Router/多连接方案 | 后续目标，当前还未固化 |

## 九、停止和重启

停止 Edge Gateway：

```text
在网关终端按 Ctrl+C
```

停止 SITL：

```text
在 SITL/MAVProxy 终端按 Ctrl+C
```

或查进程：

```bash
pgrep -af 'sim_vehicle.py|arducopter|mavproxy.py'
```

停止 Web：

```text
在 Web 终端按 Ctrl+C
```

重启 MQTT Broker：

```bash
sudo systemctl restart mosquitto
```

清理占用端口时先查看，不要直接杀未知进程：

```bash
ss -tulnp | grep -E ':1883|:14550|:8090' || true
pgrep -af 'mosquitto|sim_vehicle.py|mavproxy.py|src/main.py|web/app.py|nest_simulator.py'
```

## 十、哪些可以直接使用，哪些需要修改

### 可以直接使用

| 内容 | 用法 |
|---|---|
| Mosquitto | 当前本地 MQTT Broker，继续使用 `1883` |
| 项目 `.venv` | 启动网关、工具脚本和 Web |
| `config/gateway.yaml` | 本地模拟阶段不用改 |
| ArduCopter SITL | 用 `sim_vehicle.py -v ArduCopter --out=127.0.0.1:14550` |
| `tools/mqtt_smoke_test.py` | 验证 services/property/drc 下行和 reply |
| `web/app.py` | 可视化 MQTT、发测试命令、拉起 SITL |

### 不建议直接使用或需要后续修改

| 内容 | 当前问题 | 建议 |
|---|---|---|
| NanoMQ 官方 deb | Ubuntu 22.04 glibc 不兼容 | 当前继续用 Mosquitto；不要升级系统 glibc |
| Dock3 协议字段 | 现有代码仍有 Dock/Dock1 风格 | 按 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` 迁移客户需求相关字段；客户未用字段标 Deferred |
| `set_flight_mode` API | 当前入参和 Copter 模式映射存在问题 | 暂时优先用 MAVProxy/Web 手动飞控验证 |
| `dock_status_query` 的 `drone_status` | 已同步 SITL 遥测快照，但 Dock3 字段仍待精校 | 可作为链路冒烟，不能单独作为 Dock3 合规验收 |
| SITL + 机巢模拟器同时运行 | 默认端口会冲突 | 后续用 MAVLink Router 或网关多连接方案 |
| MongoDB | 目前只是目标架构 | 当前代码仍使用 SQLite，不要把 MongoDB 当作已接入依赖 |

## 十一、常见故障处理

### 1. `nanomq --version` 报 glibc 错误

现象：

```text
GLIBC_2.38 not found
GLIBC_2.36 not found
```

处理：

- 不要手动升级 glibc。
- 当前使用 Mosquitto 替代 NanoMQ。
- 确认 `1883` 由 Mosquitto 监听即可。

### 2. 网关无法连接 MAVLink

现象：

```text
[Connection] 心跳超时
Failed to connect to MAVLink
```

检查：

```bash
pgrep -af 'sim_vehicle.py|arducopter|mavproxy.py'
ss -tulnp | grep ':14550' || true
```

处理：

- 确认 SITL 已启动。
- 确认 SITL 命令包含 `--out=127.0.0.1:14550`。
- 确认 `config/gateway.yaml` 的 MAVLink 端口仍是 `14550`。

### 3. Web 启动 SITL 缺 Python 包

现象：

```text
ModuleNotFoundError: No module named 'pexpect'
```

处理：

```bash
/usr/bin/python3 -m pip install --user --upgrade pexpect ptyprocess MAVProxy pymavlink
```

启动 Web 时显式指定系统 Python：

```bash
SITL_PYTHON=/usr/bin/python3 ARDUPILOT_DIR=$HOME/lxz/ardupilot python web/app.py
```

### 4. `dock_status_query` 超时

检查顺序：

```bash
systemctl status mosquitto --no-pager
ss -tlnp | grep ':1883'
pgrep -af 'src/main.py'
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/EDGE-GW-001/#' -v
```

通常原因：

- 网关没有启动。
- MQTT Broker 没有启动。
- `gateway.sn` 与测试脚本 `--sn` 不一致。
- 网关启动但业务 handler 报错，需要看终端 3 日志。

### 5. 能看到 OSD，但 API 控制不符合预期

这是当前已知状态。链路已经通，但部分业务 API、飞行模式映射、Dock3 字段还未完成适配。人员1和人员2需要按 Dock3 文档和 MAVLink 状态模型继续补齐。

## 十二、开发人员分工提醒

| 人员 | 启动后重点看什么 |
|---|---|
| 人员1 | MQTT topic、method、payload、reply、osd/state/events 是否符合 Dock3 |
| 人员2 | SITL、MAVLink heartbeat、TelemetryModule、FlightControl、DroneNestModule 是否稳定 |
| 人员3 | Mosquitto、SITL、Web、机巢模拟器、smoke test 是否能重复启动和验收 |

所有启动命令变更要同步更新本文档。所有协议字段变更要同步更新 `docs/DJI_DOCK3_MIGRATION_GUIDE.md` 或接口矩阵文档。
