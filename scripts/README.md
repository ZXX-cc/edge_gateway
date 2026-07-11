# 独立组件启动脚本

这些脚本只启动网关外围组件，不启动 `src/main.py`，也不管理 Docker 启动的飞控/云平台。

默认读取 `config/gateway.yaml` 中的 `gateway.sn`、`nanomq.host/port`、`nest_mavlink.connection.port` 和 `nest_mavlink.sitl.host/port`。当前端口流向为：SITL 标准 MAVLink 输出到 `14552`，机巢模拟器解析飞机状态，网关通过 `14551` 获取机巢链路数据；`mavlink.connection.port=14550` 只保留关闭状态下的兼容配置。

## 单独启动

```bash
cd ~/lxz/edge_gateway

# MQTT Broker，默认 restart systemd mosquitto；失败时会打印 systemd/journal/端口诊断
scripts/start_mqtt_broker.sh

# 云端 requests/status 模拟器，默认持续运行
scripts/start_cloud_simulator.sh

# MQTT 观察端，订阅 thing/product/{sn}/# 和 sys/product/{sn}/#
scripts/start_mqtt_observer.sh

# Web 观察台，默认 0.0.0.0:8090
scripts/start_web_console.sh

# 机巢模拟器，默认使用 config 中的 nest_mavlink.connection.port，当前通常是 14551
scripts/start_nest_simulator.sh

# ArduPlane QuadPlane SITL：默认 Changchun_HD，主输出 14552 给机巢模拟器
scripts/start_arduplane_sitl.sh

# 可选：不用 Docker 飞控平台时，启动 ArduCopter SITL
scripts/start_arducopter_sitl.sh
```

## 一键启动外围组件

不包含网关，不包含 Docker 飞控/云平台：

```bash
cd ~/lxz/edge_gateway
scripts/start_support_stack.sh
```

默认启动：

- MQTT Broker
- 云端模拟器
- MQTT 观察端
- 机巢模拟器

脚本结束时会打印中文汇总，逐项说明“已启动 / 失败 / 跳过”、失败退出码、日志路径和依赖关系。

如果某个组件不想启动，用环境变量关闭：

```bash
START_NEST=0 scripts/start_support_stack.sh
```

如果临时需要把 Web 观察台也放进一键启动：

```bash
START_WEB=1 scripts/start_support_stack.sh
```

如果想由一键脚本额外拉起当前 ArduPlane QuadPlane SITL：

```bash
START_SITL=1 scripts/start_support_stack.sh
```

停止由一键脚本拉起的后台组件：

```bash
scripts/stop_support_stack.sh
```

## 常用覆盖参数

```bash
GATEWAY_SN=EDGEGW001 MQTT_HOST=127.0.0.1 MQTT_PORT=1883 scripts/start_mqtt_observer.sh

BIND_STATUS=unbound scripts/start_cloud_simulator.sh

NEST_PORT=14551 scripts/start_nest_simulator.sh

# 默认输出 udp:127.0.0.1:14552、udp:127.0.0.1:14551、udp:192.168.2.59:14551；
# 机巢模拟器会忽略 14551 上的非网关标准 SITL 镜像，避免抢占网关回包地址。
scripts/start_arduplane_sitl.sh

# 关闭本机 14551 镜像和远端观察输出，只保留 SITL -> 机巢模拟器 14552
SITL_LOCAL_NEST_OUT=0 SITL_EXTRA_OUT_IPS="" scripts/start_arduplane_sitl.sh

WEB_PORT=8091 scripts/start_web_console.sh
```

如果 MQTT Broker 是 Docker 里的 EMQX，不要重启宿主机 Mosquitto，只做端口检查：

```bash
BROKER_MODE=check BROKER_PORT=1883 scripts/start_mqtt_broker.sh
BROKER_MODE=check scripts/start_support_stack.sh
```

如果 systemd 重启 Mosquitto 失败，先看脚本输出的 `journalctl` 片段、端口监听、配置文件行号、重复 `listener` 提示和前台配置校验。常见情况：

- `1883` 已经被 Docker/EMQX/另一个 Mosquitto 占用：用 `BROKER_MODE=check scripts/start_support_stack.sh`。
- `/etc/mosquitto/*.conf` 配置错误：用 `sudo mosquitto -c /etc/mosquitto/mosquitto.conf -v` 定位具体行。
- `mosquitto.conf` 和 `conf.d/local.conf` 同时声明 `listener 1883` 或 `listener 8084`：只保留一处 listener。
- 没有任何 `1883` 监听：先修复 Mosquitto，再启动网关和专项脚本。

可覆盖配置文件和前台校验超时时间：

```bash
MOSQUITTO_CONFIG=/etc/mosquitto/mosquitto.conf MOSQUITTO_CONFIG_CHECK_TIMEOUT=8s scripts/start_mqtt_broker.sh
```
