<<<<<<< HEAD
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
=======
# edge_gateway



## Getting started

To make it easy for you to get started with GitLab, here's a list of recommended next steps.

Already a pro? Just edit this README.md and make it your own. Want to make it easy? [Use the template at the bottom](#editing-this-readme)!

## Add your files

- [ ] [Create](https://docs.gitlab.com/ee/user/project/repository/web_editor.html#create-a-file) or [upload](https://docs.gitlab.com/ee/user/project/repository/web_editor.html#upload-a-file) files
- [ ] [Add files using the command line](https://docs.gitlab.com/topics/git/add_files/#add-files-to-a-git-repository) or push an existing Git repository with the following command:

```
cd existing_repo
git remote add origin http://1.92.72.176/gcweb1/edge_gateway.git
git branch -M main
git push -uf origin main
```

## Integrate with your tools

- [ ] [Set up project integrations](http://1.92.72.176/gcweb1/edge_gateway/-/settings/integrations)

## Collaborate with your team

- [ ] [Invite team members and collaborators](https://docs.gitlab.com/ee/user/project/members/)
- [ ] [Create a new merge request](https://docs.gitlab.com/ee/user/project/merge_requests/creating_merge_requests.html)
- [ ] [Automatically close issues from merge requests](https://docs.gitlab.com/ee/user/project/issues/managing_issues.html#closing-issues-automatically)
- [ ] [Enable merge request approvals](https://docs.gitlab.com/ee/user/project/merge_requests/approvals/)
- [ ] [Set auto-merge](https://docs.gitlab.com/user/project/merge_requests/auto_merge/)

## Test and Deploy

Use the built-in continuous integration in GitLab.

- [ ] [Get started with GitLab CI/CD](https://docs.gitlab.com/ee/ci/quick_start/)
- [ ] [Analyze your code for known vulnerabilities with Static Application Security Testing (SAST)](https://docs.gitlab.com/ee/user/application_security/sast/)
- [ ] [Deploy to Kubernetes, Amazon EC2, or Amazon ECS using Auto Deploy](https://docs.gitlab.com/ee/topics/autodevops/requirements.html)
- [ ] [Use pull-based deployments for improved Kubernetes management](https://docs.gitlab.com/ee/user/clusters/agent/)
- [ ] [Set up protected environments](https://docs.gitlab.com/ee/ci/environments/protected_environments.html)

***

# Editing this README

When you're ready to make this README your own, just edit this file and use the handy template below (or feel free to structure it however you want - this is just a starting point!). Thanks to [makeareadme.com](https://www.makeareadme.com/) for this template.

## Suggestions for a good README

Every project is different, so consider which of these sections apply to yours. The sections used in the template are suggestions for most open source projects. Also keep in mind that while a README can be too long and detailed, too long is better than too short. If you think your README is too long, consider utilizing another form of documentation rather than cutting out information.

## Name
Choose a self-explaining name for your project.

## Description
Let people know what your project can do specifically. Provide context and add a link to any reference visitors might be unfamiliar with. A list of Features or a Background subsection can also be added here. If there are alternatives to your project, this is a good place to list differentiating factors.

## Badges
On some READMEs, you may see small images that convey metadata, such as whether or not all the tests are passing for the project. You can use Shields to add some to your README. Many services also have instructions for adding a badge.

## Visuals
Depending on what you are making, it can be a good idea to include screenshots or even a video (you'll frequently see GIFs rather than actual videos). Tools like ttygif can help, but check out Asciinema for a more sophisticated method.

## Installation
Within a particular ecosystem, there may be a common way of installing things, such as using Yarn, NuGet, or Homebrew. However, consider the possibility that whoever is reading your README is a novice and would like more guidance. Listing specific steps helps remove ambiguity and gets people to using your project as quickly as possible. If it only runs in a specific context like a particular programming language version or operating system or has dependencies that have to be installed manually, also add a Requirements subsection.

## Usage
Use examples liberally, and show the expected output if you can. It's helpful to have inline the smallest example of usage that you can demonstrate, while providing links to more sophisticated examples if they are too long to reasonably include in the README.

## Support
Tell people where they can go to for help. It can be any combination of an issue tracker, a chat room, an email address, etc.

## Roadmap
If you have ideas for releases in the future, it is a good idea to list them in the README.

## Contributing
State if you are open to contributions and what your requirements are for accepting them.

For people who want to make changes to your project, it's helpful to have some documentation on how to get started. Perhaps there is a script that they should run or some environment variables that they need to set. Make these steps explicit. These instructions could also be useful to your future self.

You can also document commands to lint the code or run tests. These steps help to ensure high code quality and reduce the likelihood that the changes inadvertently break something. Having instructions for running tests is especially helpful if it requires external setup, such as starting a Selenium server for testing in a browser.

## Authors and acknowledgment
Show your appreciation to those who have contributed to the project.

## License
For open source projects, say how it is licensed.

## Project status
If you have run out of energy or time for your project, put a note at the top of the README saying that development has slowed down or stopped completely. Someone may choose to fork your project or volunteer to step in as a maintainer or owner, allowing your project to keep going. You can also make an explicit request for maintainers.
>>>>>>> origin/main
