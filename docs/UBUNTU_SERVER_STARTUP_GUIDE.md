# Ubuntu 服务器启动手册

本文档面向参与联调的开发人员，说明当前 Ubuntu 服务器上已经部署了哪些内容、哪些可以直接使用、哪些需要注意，以及从服务器开机或全新终端开始如何启动完整模拟链路。

本文档内容依据前期 Ubuntu 服务器实测对话记录整理，包括 Ubuntu 版本、`.venv`、Mosquitto、ArduPilot SITL、Edge Gateway、Web 调试台和 MQTT 冒烟输出。本地开发机只用于维护本文档和提交 Git，不用于启动服务器服务或代替服务器环境验证。

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

## 三、启动前检查

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

## 四、每日标准启动顺序

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

## 五、最小可用验收流程

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

说明：当前 `dock_status_query` 只能证明旧机场查询链路可通，不代表 Dock3 字段已经合规。

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

## 六、Web 调试台怎么用

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

## 七、机巢模拟器启动方式

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

## 八、停止和重启

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

## 九、哪些可以直接使用，哪些需要修改

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
| Dock3 协议字段 | 现有代码仍有 Dock/Dock1 风格 | 按 `docs/DJI_DOCK3_MIGRATION_GUIDE.md` 建矩阵后逐项迁移 |
| `set_flight_mode` API | 当前入参和 Copter 模式映射存在问题 | 暂时优先用 MAVProxy/Web 手动飞控验证 |
| `dock_status_query` 的 `drone_status` | 当前未完整同步 SITL 遥测 | 只能作为链路冒烟，不能作为 Dock3 合规验收 |
| SITL + 机巢模拟器同时运行 | 默认端口会冲突 | 后续用 MAVLink Router 或网关多连接方案 |
| MongoDB | 目前只是目标架构 | 当前代码仍使用 SQLite，不要把 MongoDB 当作已接入依赖 |

## 十、常见故障处理

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

## 十一、开发人员分工提醒

| 人员 | 启动后重点看什么 |
|---|---|
| 人员1 | MQTT topic、method、payload、reply、osd/state/events 是否符合 Dock3 |
| 人员2 | SITL、MAVLink heartbeat、TelemetryModule、FlightControl、DroneNestModule 是否稳定 |
| 人员3 | Mosquitto、SITL、Web、机巢模拟器、smoke test 是否能重复启动和验收 |

所有启动命令变更要同步更新本文档。所有协议字段变更要同步更新 `docs/DJI_DOCK3_MIGRATION_GUIDE.md` 或接口矩阵文档。
