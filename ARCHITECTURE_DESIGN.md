# Edge Gateway 边云协同网关 — 系统架构设计方案

## 一、项目概述

Edge Gateway 是一个部署在 **树莓派** 上的边云协同网关，作为 DJI 云 API（上云API）与 无人机/机巢 硬件之间的双向通信桥梁。系统通过 **MQTT** 与云端通信，通过 **MAVLink（含自定义方言）** 与机巢硬件、无人机飞控通信，实现云端对无人机和机巢的远程管控。

> **关键硬件拓扑**：`云端 (DJI Cloud) ←→ 网关 (树莓派) ←→ 机巢 (Dock/Nest) + 无人机 (UAV)`

---

## 二、整体分层架构

系统按职责划分为 **六层**：

```
┌─────────────────────────────────────────────────────────────────────┐
│                     1. 入口与配置层 (Entry & Config)                   │
│   main.py | start.sh | gateway.yaml | Config(单例)                   │
├─────────────────────────────────────────────────────────────────────┤
│                     2. 云端协议层 (Cloud Protocol)                     │
│   MQTTClient | TopicRouter | MessageBuilder | NanoMQ               │
├─────────────────────────────────────────────────────────────────────┤
│                     3. 业务服务层 (Business Service)                   │
│   DeviceManager | MissionManager | MediaManager | DrcController     │
├─────────────────────────────────────────────────────────────────────┤
│                     4. 功能模块层 (Functional Modules)                 │
│   FlightControl | DroneNestModule | TelemetryModule                 │
│   WaypointManager | ModeControl | RTKModule | GimbalControl         │
├─────────────────────────────────────────────────────────────────────┤
│                     5. 核心框架层 (Core Framework)                     │
│   MessageDispatcher | MAVLinkConnection | MessageCache | Observer   │
├─────────────────────────────────────────────────────────────────────┤
│                     6. 协议定义与数据层 (Protocol & Data)               │
│   GCSOrderCodec | 自定义MAVLink方言 | SQLAlchemy ORM | YAML配置      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 三、各层级详细分析

### 3.1 入口与配置层

| 文件 | 职责 | 关键要点 |
|------|------|----------|
| `src/main.py` | 应用主入口 `EdgeGateway` 类 | 生命周期管理：initialize→start→run→stop；持有所有子系统的引用；注册~165个API handler；依赖注入所有模块 |
| `start.sh` | 生产环境部署脚本 | 检测依赖→安装NanoMQ→创建venv→安装requirements→启动NanoMQ→启动网关 |
| `config/gateway.yaml` | 全系统唯一运行配置 | gateway/MQTT/MAVLink/device/media/livestream/logging/debug 八大配置段 |
| `src/utils/config.py` | 单例配置管理器 | 多路径搜索YAML，点分隔键路径访问，提供MAVLink/MQTT配置的快捷方法 |
| `nanomq.conf` | NanoMQ配置模板 | 本地MQTT Broker，云桥接(bridge)，Webhook→Flask |

**流程完整性**: ✅ 完整
- 启动链路：`start.sh → venv → NanoMQ → python main.py → Config.load() → EdgeGateway.initialize()`
- 配置链路：YAML → Config单例 → 各模块按需读取
- 依赖管理：requirements.txt 声明全部Python依赖

### 3.2 云端协议层

| 文件 | 职责 | 关键要点 |
|------|------|----------|
| `src/mqtt/client.py` | MQTT通信中枢 | paho-mqtt连接NanoMQ；订阅8个下行主题；发布10+上行快捷方法；message回调中自动reply |
| `src/mqtt/topic_router.py` | 分类路由层 | 按10个类别组织~165个handler: device/mission/payload/media/drc/dock/log/flysafe/flight_area/custom_data |
| `src/cloud_api/connection_manager.py` | 云连接管理器 | 设备绑定/解绑；上下线报告；10s心跳；请求-应答(requests/requests_reply)模式 |
| `src/cloud_api/telemetry_reporter.py` | 遥测上报器 | 1Hz OSD定时上报；关键状态变化即时推送(state)；均质化进度上报接口 |
| `src/cloud_api/dock_manager.py` | 机巢管理器 | 机巢/无人机状态同步；指令生命周期(发送→进度→结果)；告警管理 |
| `src/cloud_api/capability_reporter.py` | 能力集上报器 | 5类能力声明：dock/drone/payload/media/drc |
| `src/utils/message.py` | 协议基石 | `MessageBuilder`构建标准DJI Cloud API消息体；`TopicBuilder`生成MQTT主题 |

**MQTT 通信主题架构**：

```
上行 (Edge → Cloud):
  thing/product/{sn}/events        — 事件上报(OSD/状态/进度/告警)
  thing/product/{sn}/services_reply — 服务调用应答
  thing/product/{sn}/osd           — 遥测数据推送
  thing/product/{sn}/state         — 状态变化推送
  thing/product/{sn}/requests      — 请求发起

下行 (Cloud → Edge):
  thing/product/{sn}/services      — 服务调用(命令)
  thing/product/{sn}/property/set  — 属性设置
  thing/product/{sn}/drc/down      — DRC实时控制
  thing/product/{sn}/requests_reply — 请求应答
```

**流程完整性**: ✅ 完整
- NanoMQ作为本地MQTT Broker提供云桥接+WebHook双通道
- MQTTClient的`_on_message`实现统一的消息分发：解析→路由→处理→自动reply
- 请求-应答模式带超时清理机制(5s清理线程)

### 3.3 业务服务层

| 文件 | 职责 | 行数 | 关键要点 |
|------|------|------|----------|
| `src/device/manager.py` | 设备物模型管理 | 1743 | 95+属性；OTA固件升级；拓扑管理；HMS告警；系统状态监控 |
| `src/mission/manager.py` | 航线任务管理 | 1420 | 任务状态机(IDLE→PREPARING→RUNNING→...)；航线全生命周期；断点续飞 |
| `src/media/manager.py` | 媒体管理 | 557 | 直播推流(ffmpeg RTMP/WebRTC)；文件上传；存储管理 |
| `src/drc/controller.py` | DRC实时遥控 | 727 | DRC会话管理；速度/航向/高度/flyto控制；云台速度控制 |
| `src/payload/camera.py` | 相机控制 | 416 | 拍照(单/连/定时)；录像；参数设置 |
| `src/payload/gimbal.py` | 云台控制 | 258 | 角度控制；目标指向；模式切换 |
| `src/payload/third_party.py` | 第三方负载 | 517 | 喊话器；探照灯；PSDK；红外热成像；降落伞 |

**流程完整性**: ✅ 基本完整

**注意点**：
- ⚠️ `src/payload/camera.py`、`src/payload/gimbal.py` 与 `src/modules/gimbal_control.py` 功能存在重叠 — gimbal_control 通过 ViewLink DLL 直接控制物理云台，而 payload/gimbal.py 通过 MAVLink 命令控制
- ⚠️ 部分 DRC 功能（POI模式、喊话器TTS、IR测温）为占位实现，标注为"placeholder"
- ⚠️ MediaManager 中 OSS 上传的 STS 临时凭证获取为 placeholder

### 3.4 功能模块层

| 文件 | 职责 | 行数 | 关键要点 |
|------|------|------|----------|
| `src/modules/drone_nest_module.py` | 机巢通信模块 | ~640 | 自定义MAVLink消息(12003/12004/12005)；环境/状态多回调通知；GCS命令编解码 |
| `src/modules/telemetry_module.py` | 遥测采集模块 | ~440 | 12种MAVLink消息解析；双回调机制(regular+state_change)；模拟模式数据生成 |
| `src/modules/flight_control.py` | 飞控模块 | ~720 | 起飞/降落/RTL/ARM/模式切换；一键起飞状态机；心跳防抖(3次确认)；固件刷写 |
| `src/modules/waypoint_manager.py` | 航线管理器 | 1091 | 完整MAVLink航线协议；异步SQLite持久化；断点续飞；航线上传/下载/验证/恢复 |
| `src/modules/mode_control.py` | 模式控制 | ~260 | 27种ArduPlane模式映射；GUIDED模式位置控制 |
| `src/modules/device_manager.py` | 多机管理 | ~120 | 基于HEARTBEAT的设备发现与在线/离线追踪 |
| `src/modules/rtk_module.py` | RTK高精定位 | ~525 | NTRIP客户端；RTCM数据注入；GGA生成与定时上报 |
| `src/modules/gimbal_control.py` | 云台物理控制 | ~340 | ViewLink DLL SDK封装；PTZ控制；跟踪；拍照/录像 |
| `src/modules/flysafe_manager.py` | 飞行安全 | ~90 | 解禁证书管理 |
| `src/modules/flight_area_manager.py` | 禁飞区管理 | ~110 | 地理围栏同步与报告 |
| `src/modules/log_manager.py` | 日志管理 | ~90 | 日志文件列表/上传 |
| `src/modules/custom_data_manager.py` | 数据透传 | ~80 | ESDK/PSDK ↔ Cloud 双向透传 |

**流程完整性**: ✅ 完整

**核心设计模式**：
- 全部模块继承 `BaseModule(MessageObserver)`，通过 Push (on_message) + Pull (idle_task) 双重模式获取MAVLink消息
- 模块间通过回调链解耦：`DroneNestModule → FlightControl.handle_nest_environment_from_callback`
- WaypointManager 独有异步数据库集成层

### 3.5 核心框架层

| 文件 | 职责 | 关键要点 |
|------|------|----------|
| `src/core/connection.py` | MAVLink连接管理 | 支持serial/UDP/TCP；自定义方言加载；CustomMAVLinkWrapper(Byte→Frame解析)；心跳超时检测 |
| `src/core/message_dispatcher.py` | 消息分发器 | Push机制(_receive_loop)：消息→类型提取→缓存→通知观察者；Pull机制(_idle_loop)：10Hz调用模块idle_task |
| `src/core/message_cache.py` | 消息缓存 | 按sysid+msg_type组织，支持latest-only和历史模式 |
| `src/core/observer.py` | 观察者基类 | MessageObserver抽象类：on_message(Push) + idle_task(Pull) + enable/disable |

**流程完整性**: ✅ 完整

**架构特点**：
- 双线程模型：接收线程(receive_loop)负责消息入站，空闲线程(idle_loop)负责周期巡检
- 消息提取三级策略：`get_type()` → `类名解析` → `方言ID映射表`
- `message_dispatcher.py:37-45` 处缺少 `_id_subscriptions` 的初始化（`_notify_observers_by_id` 被调用但 `_id_subscriptions` 不存在）
- ⚠️ 发现 Bug：`_notify_observers_by_id` 方法在 `_receive_loop` 被调用，但 `_id_subscriptions` 未在 `__init__` 中初始化

### 3.6 协议定义与数据层

| 文件 | 职责 | 关键要点 |
|------|------|----------|
| `src/generated_dialect/gcs_order_protocol.py` | GCS命令协议 | GCSCommand枚举(18个命令)；GCSOrderCodec编解码器；bit-level状态编码 |
| `src/generated_dialect/drone_nest.py` | MAVLink方言(v1) | 三条自定义消息(12003/12004/12005)；CRC实现；序列化/反序列化 |
| `src/generated_nest/nest_mavlink.py` | MAVLink方言(v2) | 与v1结构相同但temp字段类型有差异(int16_t vs uint16_t) |
| `src/mavlink_definitions/drone_nest.xml` | 方言XML定义 | 3条消息的官方定义；字段与scaling系数 |
| `src/mavlink_definitions/` | 代码生成工具 | generate_dialect.py、force_gen.py |
| `src/database/connection.py` | 数据库引擎 | SQLite + aiosqlite + SQLAlchemy Async |
| `src/database/models.py` | ORM模型 | Mission、Waypoint、MissionProgress 三表 |
| `src/database/mission_db.py` | 任务CRUD | 创建、查询、状态更新 |
| `src/database/waypoint_db.py` | 航点CRUD | 批量创建、替换、按任务查询 |
| `src/database/schemas.py` | Pydantic验证 | MissionCreate、WaypointCreate 等请求/响应模型 |

**流程完整性**: ✅ 基本完整

**注意点**：
- ⚠️ 存在两套几乎相同的MAVLink方言实现（`generated_dialect/drone_nest.py` 和 `generated_nest/nest_mavlink.py`），字段类型存在差异(int16 vs uint16)，实际使用中 `DroneNestModule` 导入 `generated_dialect` 版本
- ⚠️ 数据库使用SQLite，写入路径固定为`./missions.db`，在并发写入场景下可能出现锁竞争
- ⚠️ WaypointManager 中有 `_ensure_event_loop` 逻辑较复杂（try/except多层嵌套），异步操作降级到daemon线程时异常处理不充分

---

## 四、MissionManager 模块详细分析

### 4.1 概述

`[src/mission/manager.py](src/mission/manager.py)` 是整个系统中 **最核心的业务模块**，负责无人机航线任务的完整生命周期管理。代码量1420行。

### 4.2 状态机设计

```
                    ┌──────────┐
                    │   IDLE   │ (初始状态, status=0)
                    └────┬─────┘
                         │ flighttask_prepare
                    ┌────▼──────┐
                    │ PREPARING │ (解析航线, 上传FC, 验证)
                    └────┬──────┘
                         │ flighttask_execute
                    ┌────▼──────┐
               ┌────│  PREPARING  │────┐ (巢内准备: 停充电→开门)
               │    └────┬──────┘    │
               │         │ 巢确认      │ 巢超时/失败
               │    ┌────▼──────┐    │
               │    │ GUIDED起飞 │    │
               │    └────┬──────┘    │
               │         │ 到达安全高度  │
               │    ┌────▼──────┐    │
               └───►│  RUNNING  │◄───┘
          pause     └─┬──┬──┬──┘    cancel/失败
     ┌────────────┐   │  │  │    ┌──────────┐
     │  PAUSED    │◄──┘  │  └───►│ CANCELED │
     │  (悬停/断点)│      │       │  (RTL)   │
     └─────┬──────┘      │       └────┬─────┘
           │ resume      │ 完成       │
           └─────────────►│◄──────────┘
                          │
                    ┌────▼──────┐
                    │ COMPLETED │ (到达最后航点)
                    └────┬──────┘
                         │
                    ┌────▼──────┐
                    │  RETURN   │ (RTL → 降落到<0.5m → 3s稳定)
                    └────┬──────┘
                         │
                    ┌────▼──────┐
                    │ NEST CLEAN│ (关门 → 开始充电)
                    └────┬──────┘
                         │
                    ┌────▼──────┐
                    │   IDLE    │ (回到初始)
                    └──────────┘
```

### 4.3 核心子流程详解

#### 4.3.1 任务准备流程 (`handle_flighttask_prepare`)

```
1. 解析航线JSON → _parse_waypoint()
2. 上传至飞控 via WaypointManager.upload_mission()
3. 验证航线数 via WaypointManager.verify_mission()
4. 持久化到数据库 _save_mission_to_database()
5. 上报 task_ready_notify 事件
```

#### 4.3.2 任务执行流程 (`handle_flighttask_execute`)

```
Step 1: 机巢准备
  ├── 停止充电: drone_nest.stop_charging()
  ├── 等待1秒
  └── 开门: drone_nest.manual_open_door()

Step 2: 切换到GUIDED模式
  └── mode_control.set_mode("GUIDED")

Step 3: 设置当前航点
  └── wp_manager.set_current_waypoint(0)

Step 4: 解锁飞控
  └── _arm_motors(force=True, timeout=10s)

Step 5: GUIDED起飞至安全高度(10米)
  └── _guided_takeoff(target_alt=10.0, timeout=30s)

Step 6: 空中切换到AUTO模式, 激活航线
  ├── 等待5秒稳定 (VTOL过渡)
  ├── 验证/重新上传航线
  ├── set_current_waypoint(mission_start_seq)
  ├── set_mode("AUTO")
  └── _send_mission_start() (最多3次重试)

Step 7: 启动任务监控线程
  └── start_mission_monitor() (300ms轮询航点进度)
```

#### 4.3.3 任务监控循环 (`_mission_monitor_loop`)

```
every 300ms:
  1. 查询当前航点: wp_manager.current_waypoint
  2. 检测变化: 当前航点!= _last_waypoint_idx
  3. 检测任务错误(mav_state >= FLIGHT_TERMINATION)
  4. 检测完成: 到达最后航点 → COMPLETED
  5. 保存断点: _save_breakpoint()
  6. 上报进度: report_flighttask_progress()
```

#### 4.3.4 返航与清理流程 (`_trigger_return_and_cleanup`)

```
1. 设置RTL模式: flight_control.set_mode("RTL")

2. 启动清理线程 _wait_and_cleanup():
   ├── 循环检测 altitudeRelative < 0.5m
   ├── 稳定3秒后确认着陆
   ├── 30秒超时保护(无遥测数据时fallback)
   ├── _do_nest_cleanup():
   │   ├── drone_nest.manual_close_door()
   │   └── drone_nest.start_charging()
   └── _report_final_result(): 上报COMPLETED/CANCELED/FAILED
```

#### 4.3.5 断点续飞机制

```
保存断点:
  文件位置: /tmp/breakpoint.json
  内容: {task_id, last_waypoint_idx, total_waypoints}
  触发时机: 监控循环每次检测到航点变化

恢复流程 (handle_flighttask_recovery):
  1. _load_breakpoint() 读取断点
  2. 从数据库加载完整航线
  3. upload_mission() 上传至FC
  4. set_current_waypoint(断点索引)
  5. 启动监控线程继续执行
  6. _clear_breakpoint() 清除断点文件
```

### 4.4 依赖关系

```
MissionManager
  ├── FlightControl       — 飞行模式切换、解锁、起飞、RTL
  ├── ModeControl         — GUIDED模式、goto位置
  ├── WaypointManager     — 航线上传/下载/验证/进度
  ├── DroneNestModule     — 开门/关门/充电/停机
  ├── TelemetryModule     — 获取实时高度用于着陆检测
  ├── TelemetryReporter   — 上报OSD/进度/结果/异常
  ├── MAVLinkConnection   — 直接发送MAVLink命令(commmand_long等)
  └── Database            — 航线持久化(MissionDB, WaypointDB)
```

### 4.5 API接口清单

| DJI方法名 | 内部Handler | 功能 |
|-----------|------------|------|
| `flighttask_prepare` | `handle_flighttask_prepare` | 航线准备(解析→上传→验证→入库) |
| `flighttask_execute` | `handle_flighttask_execute` | 任务执行(6步流程) |
| `flighttask_undo` | `handle_flighttask_undo` | 取消任务(RTL+清理) |
| `flighttask_return` | `handle_flighttask_return` | 一键返航 |
| `flighttask_pause` | `handle_flighttask_pause` | 暂停(LOITER模式+保存断点) |
| `flighttask_resume` | `handle_flighttask_resume` | 恢复(重新上传+设置航点+激活) |
| `flighttask_progress` | `handle_flighttask_progress` | 查询当前进度 |
| `flighttask_recovery` | `handle_flighttask_recovery` | 断点续飞 |
| `wayline_task_create` | `handle_wayline_task_create` | DJI标准航线创建 |
| `wayline_task_execute` | `_execute_wayline_wrapper` | DJI标准航线执行 |
| `wayline_task_pause` | `_pause_wayline_wrapper` | DJI标准暂停 |
| `wayline_task_resume` | `_resume_wayline_wrapper` | DJI标准恢复 |
| `wayline_task_cancel` | `_cancel_wayline_wrapper` | DJI标准取消 |
| `wayline_task_query` | `_query_wayline_wrapper` | DJI标准查询 |
| `flyto` | `handle_flyto` | 飞向指定点 |
| `takeoff` | `handle_takeoff` | 起飞到指定高度 |
| `one_key_return_home` | `handle_one_key_return_home` | 一键返航 |
| `emergency_stop` | `handle_emergency_stop` | 紧急停止(强制上锁) |

### 4.6 流程完整性评估

| 子流程 | 状态 | 备注 |
|--------|------|------|
| 航线上传 | ✅ 完整 | 包含clear→upload→verify→ack→db save完整链路 |
| 任务执行(6步) | ✅ 完整 | 每步有时间out和重试机制 |
| 任务监控 | ✅ 完整 | 300ms轮询+进度上报+断点保存 |
| 着陆检测 | ✅ 完整 | <0.5m + 3s稳定 + 30s超时 |
| 机巢清理 | ✅ 完整 | 关门+开始充电 |
| 断点续飞 | ✅ 完整 | 文件+数据库双保险 |
| 异常处理 | ⚠️ 基本 | 有超时fallback，但部分MAVLink命令无ACK校验 |
| 并发安全 | ⚠️ 需要注意 | `_mission_monitor_loop` 和 `_wait_and_cleanup` 同时运行时状态变量无锁保护 |

---

## 五、双向通信架构详解

### 5.1 整体通信架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                         DJI Cloud (云端)                          │
│                  MQTT Broker (公网, TLS 8883)                      │
└────────┬──────────────┬──────────────┬──────────────┬────────────┘
         │  services    │  events      │  osd/state   │  drc/down
         │  (命令)       │  (事件上报)    │  (遥测/状态)  │  (实时遥控)
         ▼              ▲              ▲              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     NanoMQ (本地MQTT Broker)                      │
│              TCP 1883 | WebSocket 8083 | Bridge → Cloud          │
│              Webhook: POST /webhook/mqtt → Flask                 │
└─────────────────────────┬───────────────────────────────────────┘
                          │ paho-mqtt
┌─────────────────────────▼───────────────────────────────────────┐
│              MQTTClient (src/mqtt/client.py)                     │
│  subscribe: services/property_set/drc_down/requests/...         │
│  publish: events/osd/state/services_reply/drc_up/...            │
│  _on_message → 按method分发 → 调用注册的handler                    │
└─────────────────────────┬───────────────────────────────────────┘
                          │ handler注册
┌─────────────────────────▼───────────────────────────────────────┐
│          业务服务层 + 云端协议层 (核心桥接)                           │
│  DeviceManager | MissionManager | DockManager | DrcController   │
│  CameraController | GimbalController | ThirdPartyController     │
│  MediaManager | CapabilityReporter | TelemetryReporter          │
│                                                                  │
│  云端命令 → 译码 → MAVLink命令 → 硬件执行 → 状态回调 → 云事件上报    │
└─────────────────────────┬───────────────────────────────────────┘
                          │ setter依赖注入
┌─────────────────────────▼───────────────────────────────────────┐
│                  MessageDispatcher (核心消息总线)                   │
│  _receive_loop (Push): MAVLink消息 → 类型路由 → 观察者回调          │
│  _idle_loop (Pull): 10Hz → 各模块周期性巡检                        │
│  MessageCache: 最新消息缓存(按sysid+msg_type)                       │
└─────────────────────────┬───────────────────────────────────────┘
                          │ MAVLinkConnection
┌─────────────────────────▼───────────────────────────────────────┐
│              MAVLink I/O (src/core/connection.py)                │
│  Standard: pymavlink, serial/UDP/TCP                             │
│  Custom: 自定义方言 (drone_nest), TCP socket, byte→frame解析       │
└────────┬──────────────────────────────────┬─────────────────────┘
         │ MAVLink (标准)                     │ MAVLink (自定义方言)
         │ HEARTBEAT, GLOBAL_POSITION_INT,    │ DRONE_NEST_COMMAND(12003)
         │ ATTITUDE, COMMAND_LONG, ...        │ DRONE_NEST_RESPONSE(12004)
         │                                    │ DRONE_NEST_ORDER_RESPONSE(12005)
    ┌────▼──────┐                        ┌────▼──────┐
    │  无人机    │                        │   机巢     │
    │  ArduPilot │                        │  PLC/HW   │
    │  / PX4    │                        │  Controller│
    └───────────┘                        └───────────┘
```

### 5.2 上行链路 (硬件 → 网关 → 云端)

#### 路径1: 遥测数据流

```
无人机飞控: GLOBAL_POSITION_INT/ATTITUDE/BATTERY/...
    │ MAVLink
    ▼
MAVLinkConnection._receive_loop (byte→frame)
    │
    ▼
MessageDispatcher._receive_loop (Dispatch to hooks)
    │
    ├──→ TelemetryModule.on_message()
    │      │ 解析各类型MAVLink消息
    │      │ 更新 telemetry_data 字典
    │      │ 触发 state_change_callback (关键字段变化时)
    │      │
    │      ├──→ TelemetryReporter._on_state_change()
    │      │      构建 State消息 → MQTTClient.publish_state()
    │      │      → MQTT: thing/product/{sn}/state
    │      │
    │      └──→ TelemetryReporter._report_loop (1Hz)
    │              get_telemetry_data() → build_osd_data()
    │              → MQTTClient.publish_osd()
    │              → MQTT: thing/product/{sn}/osd
    │
    └──→ DeviceManager (modules版本, 心跳监控)
            HEARTBEAT → 设备在线/离线追踪
```

#### 路径2: 机巢环境数据流

```
机巢PLC: DRONE_NEST_RESPONSE (msg_id=12004)
    │ 自定义MAVLink方言, TCP
    ▼
MAVLinkConnection (CustomMAVLinkWrapper)
    │
    ▼
DroneNestModule.on_message() → _handle_nest_response()
    │ 解析15个环境字段 + boolstate位掩码解码
    │
    ├──→ env_callbacks → DockManager._sync_nest_env_state()
    │       映射为DJI标准字段 → update_dock_status()
    │       → event_callback → MQTT事件上报
    │
    ├──→ env_callbacks → FlightControl.handle_nest_environment_from_callback()
    │       如果takeoff_allowed → 推进一键起飞状态机
    │
    └──→ env_callbacks → DeviceManager.update_from_nest()
            映射为物模型属性 → update_properties()
```

### 5.3 下行链路 (云端 → 网关 → 硬件)

#### 路径1: 机巢控制命令流

```
DJI Cloud → MQTT: thing/product/{sn}/services
    │ method: "dock_control", data: {action: "open_cover"}
    ▼
NanoMQ (bridge转发 + webhook)
    ▼
MQTTClient._on_message() → 查handler → DockManager.handle_dock_control()
    │
    ├── 解析action → command_map映射 → GCSCommand.DOOR_OPEN
    │
    ▼
DockManager._send_dock_command()
    │ 更新本地状态预置
    │ nest_module.send_gcs_command(GCSCommand.DOOR_OPEN)
    ▼
DroneNestModule.send_gcs_command()
    │ GCSOrderCodec.encode_gcs_command() → ordinary_order + takeoff_land_order
    │ _send_drone_nest_command(ordinary, takeoff_land)
    ▼
MAVLinkConnection.send_message()
    │ drone_nest_command_send() → MAVLink 自定义方言 12003
    ▼
机巢PLC → 执行开门 → 返回 DRONE_NEST_ORDER_RESPONSE (12005)
    │
    ▼
DroneNestModule._handle_order_response()
    │ 解码 response byte → reply_status/reject_reason
    │
    ▼
command_result_callbacks → DockManager._on_command_result()
    │ 成功: report 100% + success
    │ 失败: report 100% + failure + add_alarm
    ▼
TelemetryReporter.report_dock_operation_result()
    → MQTT: thing/product/{sn}/events (dock_operation_result)
```

#### 路径2: DRC实时遥控流

```
DJI Cloud → MQTT: thing/product/{sn}/drc/down
    │ method: "drc_speed_push", data: {vx, vy, vz, yaw_rate}
    ▼
MQTTClient._on_message() → DrcController.handle_drc_speed_push()
    │
    ▼
_ send_mavlink_message(SET_POSITION_TARGET_LOCAL_NED, ...)
    │ 构建 BODY_NED 速度指令
    ▼
MAVLinkConnection.send_message() → 无人机飞控
```

### 5.4 关键桥接点：依赖注入拓扑

`[src/main.py](src/main.py)` 的 `start()` 方法中，所有模块通过 setter 注入建立关联：

```
TelemetryReporter ← TelemetryModule (数据源)
    └──→ MQTTClient (上报通道)

DeviceManager ← DroneNestModule, TelemetryModule, DockManager, FlightControl, TelemetryReporter
    └──→ MQTT事件/状态上报

MissionManager ← FlightControl, ModeControl, WaypointManager, DroneNestModule, TelemetryModule, TelemetryReporter
    └──→ 全程任务链路: 云端命令→机巢→起飞→航线→返航→机巢回收

DockManager ← DroneNestModule, TelemetryReporter
    └──→ 机巢控制+状态同步+进度上报

DrcController ← ModeControl, FlightControl
    └──→ 实时遥控

FlightControl ← DroneNestModule (接收机巢环境判定起飞条件)
    └──→ 一键起飞联动
```

---

## 六、数据模型与持久化

### 6.1 数据库ER图

```
┌─────────────────────────────────┐
│         Mission (missions)       │
│─────────────────────────────────│
│ id:         UUID (PK)           │
│ name:       VARCHAR(255)        │
│ device_id:  VARCHAR(36)         │
│ status:     VARCHAR(50)         │
│ execution_conditions: JSON      │
│ created_at: INTEGER (epoch)     │
└───────────┬─────────────────────┘
            │ 1
            ├──────────────────────────────────────┐
            │ (one-to-many)                         │ (one-to-one)
            ▼                                       ▼
┌───────────────────────────────┐  ┌──────────────────────────────────┐
│    Waypoint (waypoints)       │  │  MissionProgress (mission_progress)│
│───────────────────────────────│  │──────────────────────────────────│
│ id:          INTEGER (PK)     │  │ id:        UUID (PK)             │
│ mission_id:  FK → missions    │  │ mission_id: FK → missions        │
│ sequence:    INTEGER          │  │ current_waypoint_seq: INTEGER    │
│ command:     INTEGER          │  │ last_update: DATETIME            │
│ frame:       INTEGER          │  └──────────────────────────────────┘
│ latitude:    FLOAT            │
│ longitude:   FLOAT            │
│ altitude:    FLOAT            │
│ param1~4:    FLOAT            │
│ autocontinue: INTEGER         │
│ action_type: VARCHAR(50)      │
│ flight_speed: FLOAT           │
│ photo_interval: FLOAT         │
└───────────────────────────────┘
```

### 6.2 数据访问层

- **MissionDB**: create, get, get_by_external_id, update_status
- **WaypointDB**: batch_create, replace_all, get_by_mission
- **存储引擎**: SQLite + aiosqlite + SQLAlchemy 2.0 Async
- **事务管理**: 调用者拥有事务控制权，DB层只负责flush不负责commit

---

## 七、协议栈总结

```
应用层:         DJI Cloud API (JSON/MQTT)
                  │ TopicRouter + MessageBuilder
                  ▼
服务层:         Device/Mission/Dock/DRC/Media/Payload Handlers
                  │ GCSOrderCodec / MAVLink command constructors
                  ▼
传输层:         MQTT (paho-mqtt)          MAVLink v2.0 (pymavlink)
                  │                          │
                  ▼                          ▼
网络层:         NanoMQ (TCP 1883)         TCP/UDP/Serial
                  │                          │
                  ▼                          ▼
物理层:         以太网/WiFi                 串口/以太网
                  │                          │
                  ▼                          ▼
目标:           DJI Cloud 服务器           无人机飞控 / 机巢PLC
```

---

## 八、风险与改进建议

### 8.1 已发现的问题

| 问题 | 位置 | 影响 | 建议 |
|------|------|------|------|
| `_id_subscriptions` 未初始化 | `message_dispatcher.py:__init__` | 按消息ID订阅的功能无效 | 添加 `self._id_subscriptions = {}` |
| 双份MAVLink方言定义 | `generated_dialect/` vs `generated_nest/` | 字段类型不一致可能导致数据错误 | 统一为一份定义并重生成 |
| 部分功能为placeholder | DRC/Media多处 | 功能调用无实际效果 | 明确优先级，逐步实现 |
| 线程安全缺陷 | MissionManager状态变量无锁保护 | 并发场景可能状态错乱 | 添加 threading.Lock |
| SQLite并发写入 | WaypointManager异步操作 | 高并发下可能锁竞争 | 考虑迁移到WAL模式或使用连接池 |

### 8.2 架构优势

1. **分层清晰**: 六层架构各司其职，依赖方向一致(上→下)
2. **协议解耦**: MQTT/MAVLink 通过业务服务层完成协议转换，两端独立演进
3. **双重消费**: Push(实时) + Pull(周期) 消息模式适配不同类型的业务需求
4. **依赖注入**: 松耦合的模块间通信，便于单元测试和模块替换
5. **完整的任务生命周期**: 从航线准备→执行→监控→返航→机巢回收的闭环管理
6. **断点续飞**: 文件+数据库双保险的断点恢复机制
