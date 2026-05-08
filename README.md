# 边缘设备上云网关

树莓派 + NanoMQ + MAVLink 边缘网关框架，适配大疆上云API协议。

## 架构概览

```
+--------------------------------------------------+
|                     云端                          |
|  MQTT Broker  /  OSS存储  /  直播服务器            |
+--------+-----------------------------------------+
         | MQTT(控制信令)
         |
+--------v-----------------------------------------+
|              树莓派 边缘网关                       |
|                                                  |
|  +------------+      +----------------------+   |
|  |  NanoMQ    |      |   边缘网关服务(Python)  |   |
|  |  (Broker)  |<---->|  - MQTT客户端           |   |
|  |  :1883     |      |  - Topic路由器          |   |
|  +------------+      |  - MAVLink适配器        |   |
         |             |  - 遥测处理器           |   |
         |             |  - 设备管理器           |   |
         |             |  - 任务管理器           |   |
         |             |  - 负载控制器           |   |
         |             |  - 媒体管理器           |   |
         |             +----------------------+   |
         |                                         |
+--------v-----------------------------------------+
|  MAVLink(串口/UDP)                                |
|                                                   |
|  +------------------+      +------------------+  |
|  |   飞控(ArduPilot) |<---> |   相机/云台       |  |
|  |   /PX4            |      |   等负载          |  |
|  +------------------+      +------------------+  |
+--------------------------------------------------+

视频流通道(分离): WebSocket :8083 -> 独立推流
控制信令通道:        MQTT    :1883 -> NanoMQ
```

## 项目结构

```
edge_gateway/
├── nanomq.conf              # NanoMQ Broker配置
├── config/
│   └── gateway.yaml         # 网关主配置
├── src/
│   ├── main.py              # 主程序入口
│   ├── mqtt/
│   │   ├── client.py        # MQTT客户端(NanoMQ连接)
│   │   └── topic_router.py  # Topic路由分发
│   ├── mavlink/
│   │   ├── adapter.py       # MAVLink通信适配
│   │   └── telemetry.py     # 遥测数据处理
│   ├── device/
│   │   └── manager.py       # 设备属性/固件/配置管理
│   ├── mission/
│   │   └── manager.py       # 航线任务/指令飞行管理
│   ├── payload/
│   │   ├── camera.py        # 相机控制
│   │   ├── gimbal.py        # 云台控制
│   │   └── third_party.py   # 第三方负载(探照灯/喊话器/PSDK)
│   ├── media/
│   │   └── manager.py       # 媒体/直播管理
│   └── utils/
│       ├── logger.py        # 日志管理
│       ├── config.py        # 配置管理
│       └── message.py       # 消息格式/Topic构造
├── requirements.txt         # Python依赖
├── start.sh                 # 启动脚本
└── README.md                # 本文档
```

## 快速开始

### 1. 安装NanoMQ

```bash
# 树莓派(ARM)
curl -sSL https://raw.githubusercontent.com/nanomq/nanomq/master/.github/scripts/install_nanomq.sh | bash

# 或手动编译
git clone https://github.com/nanomq/nanomq.git
cd nanomq && mkdir build && cd build
cmake .. && make && sudo make install
```

### 2. 配置网关

编辑 `config/gateway.yaml`:

```yaml
gateway:
  sn: "EDGE-GW-001"           # 你的网关序列号

mavlink:
  connection:
    type: "serial"            # serial/udp/tcp
    port: "/dev/ttyACM0"      # 飞控串口
    baudrate: 921600
```

### 3. 启动

```bash
# 一键启动(NanoMQ + 网关服务)
./start.sh start

# 查看状态
./start.sh status

# 查看日志
./start.sh logs

# 停止
./start.sh stop

# 重启
./start.sh restart
```

## Topic体系

| Topic | 方向 | 功能 |
|-------|------|------|
| `thing/product/{sn}/requests` | up | 设备请求云端 |
| `thing/product/{sn}/requests_reply` | down | 云端响应 |
| `thing/product/{sn}/events` | up | 事件上报 |
| `thing/product/{sn}/events_reply` | down | 事件确认 |
| `thing/product/{sn}/services` | down | 云端下发服务 |
| `thing/product/{sn}/services_reply` | up | 服务响应 |
| `thing/product/{sn}/property/set` | down | 属性设置 |
| `thing/product/{sn}/property/set_reply` | up | 属性设置结果 |
| `thing/product/{sn}/osd` | up | 定频遥测(0.5-10Hz) |
| `thing/product/{sn}/state` | up | 状态变更 |
| `thing/product/{sn}/drc/down` | down | DRC控制指令 |
| `thing/product/{sn}/drc/up` | up | DRC执行结果 |

## API列表

### 设备管理
- `ota_create` / `ota_progress` / `ota_result` - 固件升级
- `config_update` / `config_update_result` - 配置更新
- `property/set` / `property/set_reply` - 属性设置

### 飞行任务
- `load_wayline` / `task_ready_notify` - 航线上传
- `execute_task` / `cancel_task` / `pause_wayline` / `resume_wayline` - 任务控制
- `flyto` / `fly_to_point_progress` - 指令飞行
- `take_off` / `take_off_progress` - 一键起飞
- `one_key_return_home` / `cancel_return_home` - 返航控制
- `set_flight_mode` - 飞行模式设置

### 相机控制
- `camera_photo_take` / `start_photo` / `stop_photo` - 拍照
- `start_record` / `stop_record` - 录像
- `set_camera_mode` / `set_exposure_mode` / `zoom` / `set_focus_mode` - 参数设置

### 云台控制
- `gimbalRotate` - 角度控制
- `camera_look_at` - 目标指向
- `set_gimbal_mode` - 模式切换

### 第三方负载
- `drc_light_control_set` / `drc_light_calibration` - 探照灯
- `drc_speaker_tts_set` / `drc_speaker_audio_play` - 喊话器
- `psdk_function_set` - PSDK功能调用
- `set_thermal_mode` / `set_palette` - 红外相机

### 媒体直播
- `live_start` / `live_stop` - 直播控制
- `set_livestream_lens` / `set_livestream_quality` - 直播设置
- `storage_config_get` / `file_upload_callback` - 媒体上传

## 消息格式

```json
{
  "bid": "UUID-业务ID",
  "data": {},
  "gateway": "gateway_sn",
  "tid": "UUID-事务ID",
  "timestamp": 1724126480015,
  "method": "api_method_name",
  "need_reply": 1
}
```

## 开发说明

### 添加新的API处理器

在 `src/mission/manager.py` (或其他模块) 中添加处理方法:

```python
def handle_new_api(self, msg: dict) -> dict:
    data = msg.get('data', {})
    # 处理逻辑
    return MessageBuilder.build_reply(msg, result=0)
```

在 `src/main.py` 的 `_register_handlers` 中注册:

```python
self.router.register('new_api_method', self.mission.handle_new_api, 'mission')
```

### 实现MAVLink命令

在 `src/mavlink/adapter.py` 中添加命令方法:

```python
def custom_command(self, param: float) -> bool:
    result = self.send_command_long(
        mavutil.mavlink.MAV_CMD_CUSTOM_COMMAND,
        [param, 0, 0, 0, 0, 0, 0]
    )
    return result is not None and result.get('result') == mavutil.mavlink.MAV_RESULT_ACCEPTED
```

## 依赖

- Python 3.8+
- NanoMQ >= 0.19
- pymavlink >= 2.4.30
- paho-mqtt >= 1.6.0

## License

MIT
