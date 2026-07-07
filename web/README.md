# Edge Gateway Web Console

独立 Web 调试台，用来替代大段终端输出：

- 替代终端 3：订阅 MQTT topic，把 `osd/state/events/reply` 解析成仪表盘和消息流。
- 替代终端 1 的常用操作：可启动/停止 SITL 进程，并通过 MAVLink 发送 `GUIDED/ARM/TAKEOFF/LAND/RTL` 等命令。
- `web/mavlink` 提供机巢自定义 MAVLink 调试工具，可测试 `DRONE_NEST_COMMAND/RESPONSE/ORDER_RESPONSE`。
- 不依赖 `src/` 代码，不嵌入当前网关程序；只连接本机 MQTT broker 和 MAVLink 端口。

## 安装

可以直接复用项目 `.venv`：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python -m pip install -r web/requirements.txt
```

## 启动

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate

SITL_PYTHON=/usr/bin/python3 \
ARDUPILOT_DIR=$HOME/lxz/ardupilot \
python web/app.py
```

浏览器打开：

```text
http://127.0.0.1:8090
```

局域网访问时：

```text
http://Ubuntu服务器IP:8090
```

## 常用环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WEB_HOST` | `0.0.0.0` | Web 监听地址 |
| `WEB_PORT` | `8090` | Web 端口 |
| `MQTT_HOST` | `127.0.0.1` | MQTT broker 地址 |
| `MQTT_PORT` | `1883` | MQTT broker 端口 |
| `GATEWAY_SN` | `EDGE-GW-001` | 网关 SN |
| `MAVLINK_URL` | `udpout:127.0.0.1:14550` | MAVLink 命令发送地址 |
| `MAVLINK_TARGET_SYSTEM` | `1` | MAVLink target system |
| `MAVLINK_TARGET_COMPONENT` | `1` | MAVLink target component |
| `ARDUPILOT_DIR` | `~/lxz/ardupilot` | ArduPilot 仓库路径，用于启动 SITL |
| `SITL_PYTHON` | `/usr/bin/python3` | 启动 ArduPilot `sim_vehicle.py` 使用的 Python |

## 页面操作顺序

1. 先确认 Ubuntu 上 MQTT broker 已启动，主网关也已启动：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

2. 打开 Web 页面后，先看顶部状态：

- `MQTT` 应显示“已连接”。
- `MAVLink` 显示的是命令发送地址，默认 `udpout:127.0.0.1:14550`。
- `SITL` 未启动时是“未运行”。

3. 点击左侧“启动 SITL”。

SITL 输出会显示在页面中部下方的“SITL 输出”。等看到 `ArduPilot Ready`、`GPS`、`Flight battery` 等日志后，说明模拟器起来了。

4. 等主网关终端出现 MAVLink 已连接：

```text
[Connection] 已连接到系统 1
[Dispatcher] 消息分发器已启动
```

5. 看右侧“MQTT 消息流”和中间遥测卡片。

如果正常，应持续看到 `osd` 遥测、`events` 心跳、`state` 状态变化。

6. 常用飞控按钮建议按这个顺序点：

- `切到 GUIDED`
- `解锁电机`
- 设置起飞高度，例如 `10`
- `起飞`
- 观察高度变化
- `降落` 或 `返航`

7. “API 冒烟测试”可以发业务 method。

例如 method 填：

```text
dock_status_query
```

payload 填：

```json
{}
```

然后点击“发送”。如果网关正常，会在右侧看到 `services` 请求和 `services_reply` 回包。

## 常见问题

### 如何启动机巢自定义 MAVLink 模拟器

启动模拟器：

```bash
python web/mavlink/nest/simulator.py
```

如需避开飞控 SITL 端口，可改端口：

```bash
python web/mavlink/nest/simulator.py --port 14551
```

网关的 `nest_mavlink.connection.port` 要和模拟器端口保持一致。

### 点击“启动 SITL”提示 `ModuleNotFoundError: No module named 'pexpect'`

这是因为 Web 程序运行在项目 `.venv`，但 ArduPilot 的 `sim_vehicle.py` 需要使用系统 Python 的 ArduPilot 依赖。

本工具默认用 `/usr/bin/python3` 启动 SITL。启动 Web 时建议显式带上：

```bash
SITL_PYTHON=/usr/bin/python3 python web/app.py
```

如果系统 Python 仍然缺依赖，执行：

```bash
/usr/bin/python3 -m pip install --user --upgrade pexpect ptyprocess MAVProxy pymavlink
```

然后重启 Web 页面，再点“启动 SITL”。

## 注意

- 当前 Ubuntu 22.04 使用 Mosquitto 替代 NanoMQ 时，本工具照常工作。
- 如果 Web 中的 MAVLink 控制按钮无效，先保留终端 1 的 MAVProxy 手动控制；这通常是 UDP 输出方向或目标端口配置问题。
- Web 启动 SITL 前，确认没有另一个终端已经启动同一个 SITL，否则会端口冲突。
