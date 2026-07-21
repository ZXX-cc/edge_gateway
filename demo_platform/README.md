# Edge Gateway Demo Platform

本项目是一个本地演示/验收平台，目标是围绕当前 Edge Gateway + Dock3 Cloud API + SITL/QGC 联调主线做可视化验收：

- 飞行总览：机场/飞行器在线、SITL 遥测、电量、高度、经纬度、GPS/RTK、最近命令结果
- APIDock3 命令：直接通过云端 API topic 下发 services、property、DRC 命令
- 真实航线：先用 QSTABILIZE/QLAN 前置解锁，再下发真实 HD 航线准备、执行、进度、暂停、恢复、返航、终止
- DRC 飞控：进入控制、心跳、速度推送、退出控制
- 云台直播：通过 ffmpeg 把吊舱 RTSP 转成浏览器可看的 MJPEG，并可下发 Dock3 直播推流命令
- 飞行日志：按时间查看飞行过程中的电量、高度、经纬度、模式、航线进度和回包
- 复测留证：只保留真实 SITL、APIDock3、DRC、机场机构和非阻塞留证项
- 原始报文：排障时查看完整 `osd`、`state`、`events`、`services_reply`、`drc/up`、`status`

它不替代 `src/main.py` 网关，也不启动 broker。使用前需要先启动 MQTT broker、Edge Gateway 和机巢模拟器。

当前联调默认按网关 `192.168.1.100` 环境配置：Demo Platform 通过 `192.168.1.100:1883` 连接 MQTT，默认预览吊舱 `rtsp://192.168.1.119:554/main`。

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
MQTT_HOST=192.168.1.100
MQTT_PORT=1883
GATEWAY_SN=EDGEGW001
DRONE_SN=DRONESIM001
GIMBAL_RTSP_URL=rtsp://192.168.1.119:554/main
```

如果 Demo Platform 也部署在网关同网段主机上，推荐这样启动，方便局域网浏览器访问：

```bash
DEMO_HOST=0.0.0.0 MQTT_HOST=192.168.1.100 python demo_platform/app.py
```

访问地址：

```text
http://运行Demo的主机IP:8095
```

## 内置工作流

- `基础上线巡检`：读取关键属性，观察 OSD/state/status。
- `APIDock3/CMD 动作`：调试模式、舱盖、充电、推杆、补光灯。
- `DRC 链路`：进入 DRC、心跳、杆量/速度、退出。
- `真实 HD 航线`：先调用 `set_flight_mode`，使用 `{"mode":"QLAN","arm":true}` 按 QLAN 别名切到 QSTABILIZE 完成解锁，再默认使用 `HD.copter-sitl.waypoints` 执行真实 SITL/QGC 航线准备、执行和进度查询。`HD.original.waypoints` 保留原始 VTOL 起飞任务，可能出现 `Transition airspeed wait`。

页面不再把模拟航线、直播媒体、真实 Adapter 等未进入当前主线的项目放在主导航中。真实航线是否通过，以 QGC 中可见航线任务执行、`flighttask_progress` 和飞行器 OSD 电量/高度/经纬度变化共同留证。
