# Ubuntu 部署与联调推进计划

本文档是当前仓库的部署推进基线。目标是在 Ubuntu 服务器内跑通“适配大疆上云 API 协议的边缘网关”模拟链路，不接实体无人机、飞控、机巢或负载设备。

开发人员日常接手服务器、从开机状态启动完整链路、排查端口和进程时，使用 `docs/UBUNTU_SERVER_STARTUP_GUIDE.md`。本文档保留阶段计划、部署背景和验收推进。

当前协议目标已调整为 **DJI Dock 3**：

- 原 DJI Dock/Dock1 属性参考：https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/properties.html
- 新 DJI Dock 3 属性参考：https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/properties.html

## 2026-07-03 实测进度

| 项目 | 当前状态 | 证据/结论 |
|---|---|---|
| Ubuntu 环境 | 已完成 | 目标机为 Ubuntu 22.04.5 LTS，Python 3.10.12，项目 `.venv` 可用 |
| Python 依赖 | 已完成 | `requirements-ubuntu.txt` 安装成功，`yaml/paho.mqtt/pymavlink/sqlalchemy/pydantic` 导入成功 |
| 本地 MQTT | 已完成，当前用 Mosquitto 替代 NanoMQ | NanoMQ 官方 deb 在 Ubuntu 22.04 上要求 `GLIBC_2.36/2.38`，当前系统不兼容；Mosquitto 已占用 `1883` 并可发布/订阅 |
| ArduPilot SITL | 已完成 | `sim_vehicle.py` 和 `mavproxy.py` 可用；ArduCopter SITL 启动成功 |
| 网关启动 | 已完成 | `python src/main.py -c config/gateway.yaml` 可连接 MQTT 和 MAVLink，日志出现 `MAVLink dispatcher started` |
| MAVLink 遥测 | 已完成 | MQTT `osd` 持续上报经纬度、高度、姿态、电池、GPS 等数据 |
| MQTT API 冒烟 | 已完成 | `dock_status_query` 发布到 `services` 后收到 `services_reply`，`result=0` |
| 手动飞控验证 | 已完成 | MAVProxy 手动 `GUIDED/arm/takeoff 10/LAND` 成功；OSD 高度从 0 上升到 10m，再下降到 0m |
| 协议版本判断 | 已确认原项目偏 DJI Dock/Dock1 | `src/mission/manager.py` 和 `src/main.py` 注释引用 `dock1/wayline.html`，`src/cloud_api/dock_manager.py` 标注 `DJI Dock 机场管理模块` |
| 当前目标变更 | 已确定切换到 DJI Dock 3 | 后续所有属性、服务、事件、状态、回包验收以 DJI Dock 3 文档为准 |
| 当前阻塞 | 代码适配问题 | `set_flight_mode` API 入参和模式映射不适配 ArduCopter；`dock_status_query` 的 `drone_status` 未同步模拟遥测；Dock1 字段/接口需要迁移到 Dock3 |

## 2026-07-03 协议目标变更：Dock1 -> Dock3

| 项目 | 结论 | 后续动作 |
|---|---|---|
| 原适配对象 | 当前仓库是大疆上云 API 风格边缘网关，机场部分明确偏 DJI Dock/Dock1 | 保留当前可运行链路，不直接推翻部署和 SITL 模拟成果 |
| 新适配对象 | 目标协议版本改为 DJI Dock 3 | 按官方 Dock3 文档重新建立属性、服务、事件、状态字段表 |
| 实机范围 | 不做实体实机接入 | 所有设备侧行为由 ArduPilot SITL、机巢模拟器、负载模拟器和云端 MQTT 模拟器完成 |
| 兼容策略 | 不默认认为 Dock1 字段与 Dock3 完全兼容 | 对每个 topic、method、payload、reply、event 做 Dock1/Dock3 差异审计 |
| 验收重点 | 云端看到的行为要像 DJI Dock 3 | MQTT topic、属性、状态、服务回包、事件结构必须匹配 Dock3 |

## 2026-07-06 多人协同当前阶段

当前项目进入多人并行联调阶段。阶段目标不是补全所有业务逻辑，而是先把上云 API 模拟器、网关、MAVLink/SITL、机巢模拟器之间的全链路调通。

| 角色 | 负责范围 | 当前输入 | 当前输出 | 验收标准 |
|---|---|---|---|---|
| 人员1 | 上云 API 与网关之间的相互通信 | Dock3 官方文档、MQTT broker、网关 method 路由 | Dock3 method 矩阵、云端模拟 payload、reply 验收规则 | services/requests/events/osd/state/drc topic 可按 Dock3 语义验证 |
| 人员2 | MAVLink 与网关之间的相互通信 | ArduPilot SITL、MAVLink 机巢模拟器、网关 core/modules | MAVLink 遥测映射、飞控命令状态、机巢状态映射 | 网关可稳定收到遥测并能下发模拟飞控/机巢命令 |
| 人员3 | 模拟器和联调环境部署 | Ubuntu、MQTT broker、ArduPilot、Web 调试台 | 可重复启动的模拟环境和 smoke test | 新环境按文档可启动 broker、网关、SITL、Web 调试台和机巢模拟器 |

当前总体进度固定为：

| 模块 | 当前状态 |
|---|---|
| Ubuntu 基础部署 | 基本完成 |
| MQTT 本地链路 | 基本完成，当前用 Mosquitto 替代 NanoMQ |
| SITL 与网关 MAVLink 链路 | 基本完成 |
| MQTT osd/state/events/services_reply 基础链路 | 基本完成 |
| Dock3 协议矩阵 | 待建立 |
| 上云 API 模拟器 | 待固化 |
| MAVLink 机巢模拟器合流 | 待固化 |
| MongoDB 服务型 NoSQL | 目标架构，当前未接入 |

MongoDB 说明：当前代码仍使用 SQLite + SQLAlchemy/aiosqlite。MongoDB 是后续目标架构，用于承接 telemetry、operation_record、alarm、media_file、device_state、request_trace 等集合。本阶段只在文档中定义目标，不把 MongoDB 写为已完成部署项。

## 当前状态与目标

| 类别 | 当前结论 | 后续要求 |
|---|---|---|
| 项目性质 | Python 边缘网关，用 MQTT/NanoMQ 或 Mosquitto 对接大疆上云 API，用 MAVLink/SITL/模拟器提供设备侧行为 | 只做服务器内模拟，不接真实设备 |
| 主入口 | `src/main.py`，`EdgeGateway` 初始化配置、MQTT、MAVLink、任务、设备、负载、媒体、云端上报和数据库 | Ubuntu 启动路径固定为 `python3 src/main.py -c /etc/edge_gateway/gateway.yaml` |
| API 能力 | 当前主入口注册 223 个 MQTT API method，无重复 method，但机场/航线注释和字段偏 DJI Dock/Dock1 | 后续按 DJI Dock 3 的属性、服务、事件、状态、请求、回包重新分组验证 |
| 默认配置 | MQTT `127.0.0.1:1883`，MAVLink UDP `127.0.0.1:14550`，cloud 默认关闭 | Ubuntu 22.04 第一阶段使用 Mosquitto + ArduPilot SITL；NanoMQ 后续换兼容包/源码编译/Ubuntu 24.04 再处理 |
| ArduPilot 文档 | [Building Setup Linux](https://ardupilot.org/dev/docs/building-setup-linux.html) 是构建环境准备；[SITL on Linux](https://ardupilot.org/dev/docs/setting-up-sitl-on-linux.html) 才是模拟器联调入口 | 优先使用 ArduCopter SITL 模拟飞控 |
| 仓库风险 | `README.md` 有合并冲突；原 `requirements.txt` 是 UTF-16 且含 Windows 本地 `setuptools` 引用 | Ubuntu 部署默认使用 `requirements-ubuntu.txt` |

## 部署推进表

| 阶段 | 目标 | 当前状态 | 验收标准 |
|---|---|---|---|
| P0 环境确认 | 明确 Ubuntu 目标机状态 | 已完成 | Ubuntu 22.04.5 LTS，x86_64，Python 3.10.12 |
| P1 仓库部署准备 | 让项目能安装依赖 | 已完成 | `.venv` 创建成功，`requirements-ubuntu.txt` 安装成功 |
| P2 本地 MQTT 部署 | 建立本地 MQTT 控制信令通道 | 已完成，使用 Mosquitto | 本地能发布/订阅 `thing/product/{sn}/...`，1883 已占用 |
| P3 ArduPilot SITL 部署 | 模拟无人机/飞控 | 已完成 | 网关收到 heartbeat、GPS、姿态、电池等消息 |
| P4 机巢模拟 | 模拟自定义 MAVLink 消息 | 单独运行 `src/nest_simulator.py`，或后续用 MAVLink Router 合并多路 MAVLink | 网关能解析 `DRONE_NEST_RESPONSE` 和 `DRONE_NEST_ORDER_RESPONSE` |
| P5 网关启动 | 启动项目主服务 | 已完成 | 网关连接 MQTT 和 MAVLink，无启动阻塞 |
| P6 Dock3 协议迁移 | 将 Dock/Dock1 风格接口迁移为 DJI Dock 3 | 建立 Dock3 字段矩阵，修正属性、状态、服务、事件和回包结构 | 云端模拟器按 Dock3 下发/订阅时，字段和回包符合官方文档 |
| P7 纯模拟自动验收 | 在服务器内长期稳定跑通模拟链路 | 固化 Web 调试台、MQTT smoke test、SITL 启停、主要 method 批量验收 | 不接实体设备也能重复跑通 Dock3 云端双向通信 |

## Ubuntu 首次部署命令

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl gettext-base sqlite3 iproute2 mosquitto mosquitto-clients

cd /path/to/edge_gateway
bash tools/ubuntu_preflight.sh

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-ubuntu.txt
```

Ubuntu 22.04 当前先用 Mosquitto 做本地 MQTT broker：

```bash
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto
ss -tlnp | grep ':1883' || true
```

NanoMQ 官方 deb 在当前 Ubuntu 22.04 实测出现 `GLIBC_2.36/2.38 not found`，不要手动升级 glibc。后续如必须使用 NanoMQ，优先选择 Ubuntu 24.04、兼容旧 glibc 的 NanoMQ 包，或源码编译。

ArduPilot SITL 参考官方文档完成依赖与源码准备后，优先启动 Copter。实际端口要和 `config/gateway.yaml` 的 `mavlink.connection.port` 保持一致。

## 联调拓扑与端口策略

| 模式 | 目的 | 端口建议 | 注意事项 |
|---|---|---|---|
| UAV-only | 先验证飞控/无人机遥测和基础命令 | SITL 输出到 `127.0.0.1:14550`，网关连接 `14550` | 第一阶段推荐 |
| Dock-only | 先验证机巢自定义 MAVLink 消息 | `src/nest_simulator.py` 监听 `14550`，网关连接 `14550` | 不与 SITL 同时占用同一 UDP 端口 |
| Combined | 同时联调无人机和机巢 | 使用 MAVLink Router 或后续扩展多 MAVLink 连接 | 当前项目配置只有一个 MAVLink 连接入口，直接同时跑 SITL 和机巢模拟器会有端口冲突 |

## 启动与冒烟验证

手动启动顺序：

```bash
# 1. 启动本地 MQTT broker（Ubuntu 22.04 当前使用 Mosquitto）
sudo systemctl restart mosquitto

# 2. 启动 ArduCopter SITL
cd ~/lxz/ardupilot/ArduCopter
sim_vehicle.py -v ArduCopter --out=127.0.0.1:14550

# 3. 启动网关
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

MQTT 冒烟测试：

```bash
source .venv/bin/activate
python tools/mqtt_smoke_test.py --method dock_status_query --timeout 8
```

说明：当前 `dock_status_query` 只证明本地旧机场查询链路可用，不代表已经符合 DJI Dock 3。Dock3 迁移完成后，需要用 Dock3 字段和 method 清单重新定义 smoke test。

手动飞控验证命令在 MAVProxy 提示符中输入：

```text
mode GUIDED
arm throttle
takeoff 10
mode LAND
```

验证完成时 MQTT `osd.height` 应从 0 上升到约 10，再下降到 0，并出现 `state {"armed": false}`。

## 下一阶段代码修复项

| 优先级 | 问题 | 修复要求 | 验收标准 |
|---|---|---|---|
| P0 | 协议目标从 Dock1 切到 DJI Dock 3 | 按 Dock3 官方文档建立属性、服务、事件、状态、请求、回包字段矩阵；标出当前实现缺失、字段名不一致、枚举不一致、单位不一致项 | 形成 Dock3 差异清单，后续每个改动都有明确验收字段 |
| P0 | 机场状态模型仍偏 Dock/Dock1 | 重构 `DockManager` 的模拟状态输出，使 `dock_status_query`、`osd/state/events` 中的机场字段以 Dock3 为准 | MQTT 订阅端看到的机场属性符合 Dock3 文档 |
| P0 | `set_flight_mode` 入参不兼容 | 支持 `{"mode":"GUIDED"}`、`{"mode":4}`、`{"mode_code":4}`、`{"flight_mode":"GUIDED"}`；底层失败时不能回 `result=0` | 发送 `GUIDED/LAND/RTL/LOITER` 后，网关日志和 SITL 模式一致，失败时回包非 0 |
| P0 | 飞控模式表不适配 ArduCopter | 根据 heartbeat vehicle type 或配置选择 ArduCopter/ArduPlane/QuadPlane 模式表；Copter 至少支持 `STABILIZE=0, ACRO=1, ALT_HOLD=2, AUTO=3, GUIDED=4, LOITER=5, RTL=6, CIRCLE=7, LAND=9` | 手动/API 模式切换不再出现 `UNKNOWN(9)`，`LAND` 能识别 |
| P0 | `dock_status_query` 未同步模拟无人机状态 | `drone_status` 使用当前 `TelemetryModule` 和 `FlightControl` 状态填充 online、armed、flight_mode、lat/lon/alt/height/speed/GPS/battery | `dock_status_query` 返回值与 MQTT OSD 中的模拟遥测一致 |
| P1 | API 起飞/降落链路待验证 | 在模式表修复后验证 `take_off`、`one_key_return_home`、`landing` 或等价 API | API 能让 SITL 起飞到 10m，并能降落/返航 |
| P1 | 机巢模拟未联调 | 单独运行 `src/nest_simulator.py` 做 Dock-only；Combined 模式需 MAVLink Router 或多连接架构；输出字段按 Dock3 机场状态映射 | 能收到并解析机巢自定义 MAVLink 消息，并转换为 Dock3 机场属性 |

## 关键技术要求

| 要求 | 具体约束 | 后续实现原则 |
|---|---|---|
| 主线程流畅 | 核心状态机、MQTT 回调、MAVLink 分发不能被耗时操作卡住 | 下载、上传、数据库写入、日志刷盘、文件扫描、固件升级、外部命令全部后台化 |
| IO 异步化 | MQTT、MAVLink、SQLite、文件上传、日志归档、云端请求都可能阻塞 | 使用异步任务、线程池或队列；禁止在热路径直接长时间等待 |
| 状态机稳定 | 任务执行、起飞、返航、机巢开关盖/充电必须可追踪 | 每个动作要有状态、进度、超时、失败原因、恢复路径 |
| 日志完善 | 部署、启动、MQTT、MAVLink、任务、机巢、负载、媒体都要可追溯 | 已实现按 `logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log` 落盘、后台队列写入、旧 `print` 捕获、关键 MQTT 字段中文标注；后续补 operation record |
| `drone_nest.py` 映射 | 它是 MAVLink 自定义消息定义/编解码基础 | 字段映射见 `docs/DRONE_NEST_MAVLINK_MAPPING.md` |
| Dock3 上云协议一致性 | MQTT topic、method、reply、event、state、osd 格式必须和 DJI Dock 3 对齐 | 每个 method 验证请求体、响应体、错误码、`need_reply` 行为；禁止继续按 Dock1 推断字段 |
| 部署可恢复 | 服务异常退出、SITL 重启、NanoMQ 重启后要能恢复 | 后续使用 systemd、日志、健康检查、重启策略 |

## 验收清单

| 项目 | 验收标准 |
|---|---|
| 启动验收 | Ubuntu 上能启动本地 MQTT broker、SITL 或机巢模拟器、网关；日志可查看 |
| MAVLink 验收 | 已通过：网关收到 heartbeat、GPS、姿态、电池等遥测并进入缓存/上报链路 |
| MQTT 验收 | 已通过：订阅下行 topic，发布 osd/state/events/reply，method 路由命中正确模块 |
| Dock3 协议验收 | 待完成：按 DJI Dock 3 官方文档验证 properties、services、events、state、osd、reply |
| 命令验收 | `take_off`、`flyto`、`return_home`、`dock_control` 或 Dock3 等价 method 有明确执行结果 |
| 日志验收 | 每次命令有 request、dispatch、execute、reply、error/timeout 的完整记录；关键字段以 `字段名(中文名)` 形式辅助排查 |
| 性能验收 | 主循环持续运行；耗时任务不阻塞 MQTT 回调和 MAVLink 接收线程 |
