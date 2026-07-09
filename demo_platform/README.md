# Edge Gateway Demo Platform

本项目是一个本地演示/验收平台，目标是模拟 Dock Demo 平台的核心体验：

- 设备总览：机场/飞行器在线、舱盖、推杆、充电、电量、高度、GPS/RTK 等摘要状态
- 机场控制：远程调试、舱盖、充电、推杆、补光灯和属性查询
- 飞行任务：航线准备、执行、进度、暂停、恢复、返航、终止
- DRC 飞控：进入控制、心跳、速度推送、退出控制
- 直播媒体：直播能力、开始/停止直播、镜头/画质、媒体文件列表
- 运维日志：绑定状态、工作区、能力、HMS、日志、固件属性
- 原始报文：排障时查看 `osd`、`state`、`events`、`services_reply`、`drc/up`、`status`

它不替代 `src/main.py` 网关，也不启动 broker。使用前需要先启动 MQTT broker、Edge Gateway 和机巢模拟器。

当前联调默认按 `192.168.2.188` 环境配置：`src/main.py` 和机巢模拟器都在 `192.168.2.188`，Demo Platform 通过 `192.168.2.188:1883` 连接 MQTT。

## 启动

```bash
cd /Users/m4/FHL/edge_gateway
python -m pip install -r demo_platform/requirements.txt
python demo_platform/app.py
```

默认地址：

```text
http://127.0.0.1:8095
```

可用环境变量：

```bash
DEMO_HOST=0.0.0.0
DEMO_PORT=8095
MQTT_HOST=192.168.2.188
MQTT_PORT=1883
GATEWAY_SN=EDGEGW001
DRONE_SN=DRONESIM001
```

如果 Demo Platform 也部署在 `192.168.2.188`，推荐这样启动，方便局域网浏览器访问：

```bash
DEMO_HOST=0.0.0.0 MQTT_HOST=192.168.2.188 python demo_platform/app.py
```

访问地址：

```text
http://192.168.2.188:8095
```

## 内置工作流

- `基础上线巡检`：读取关键属性，观察 OSD/state/status。
- `机巢/CMD 动作`：调试模式、舱盖、充电、推杆、补光灯。
- `DRC 链路`：进入 DRC、心跳、杆量/速度、退出。
- `Wayline 模拟任务`：`flighttask_prepare/execute/progress/pause/resume/return/undo`。
- `一键启航演示`：断充、开盖、释放、准备任务、执行任务。

这些工作流优先验证云端协议和网关业务链路；真实飞控/真实机巢动作仍需要配合 SITL 或 v1.3 机巢模拟器/实机。
