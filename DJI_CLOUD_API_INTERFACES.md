# 大疆上云API (DJI Cloud API) 接口清单

> 本文档列出 edge_gateway 设备端已实现的所有大疆上云API接口。
> 参考 DJI Cloud API 规范（DJI Dock 设备端实现）。

## 概览

- **总接口数**: 157 个
- **通信协议**: MQTT
- **Topic 前缀**: `thing/product/{gateway_sn}/`
- **消息格式**: JSON，标准大疆物模型格式

---

## 一、设备管理 (Device) - 37个接口

| 接口方法 | 说明 | 对应Topic |
|---------|------|----------|
| `device_binding` | 设备绑定 | `services` |
| `device_unbinding` | 设备解绑 | `services` |
| `workspace_bind_status` | 查询工作空间绑定状态 | `services` |
| `workspace_get` | 获取工作空间信息 | `services` |
| `ota_create` | 创建固件升级任务 | `services` |
| `ota_cancel` | 取消固件升级 | `services` |
| `config_update` | 配置更新 | `services` |
| `property_set` | 属性设置 | `property/set` |
| `property_get` | 属性获取 | `services` |
| `device_reboot` | 设备重启 | `services` |
| `format_sd_card` | 格式化存储卡 | `services` |
| `device_lock` | 设备锁定 | `services` |
| `device_unlock` | 设备解锁 | `services` |
| `device_calibration` | 设备标定 | `services` |
| `device_format` | 设备格式化 | `services` |
| `device_reset` | 设备重置 | `services` |
| `hms_get` | 获取HMS告警 | `services` |
| `hms_clear` | 清除HMS告警 | `services` |
| `device_log_list_get` | 获取设备日志列表 | `services` |
| `device_log_upload` | 设备日志上传 | `services` |
| `device_capability_query` | 设备能力查询 | `services` |
| `cover_open` | 打开舱盖 | `services` |
| `cover_close` | 关闭舱盖 | `services` |
| `charge_open` | 开启充电 | `services` |
| `charge_close` | 关闭充电 | `services` |
| `putter_open` | 打开推杆 | `services` |
| `putter_close` | 关闭推杆 | `services` |
| `camera_mode_switch` | 相机模式切换 | `services` |
| `supplement_light_open` | 补光灯开 | `services` |
| `supplement_light_close` | 补光灯关 | `services` |
| `sdr_workmode_switch` | SDR工作模式切换 | `services` |
| `alternate_land_point_set` | 备用降落点设置 | `services` |
| `rtk_ref_points_set` | RTK参考点设置 | `services` |
| `rtk_ref_points_query` | RTK参考点查询 | `services` |
| `device_exit_rtk_mode` | 退出RTK模式 | `services` |
| `device_enter_simulation_mode` | 进入仿真模式 | `services` |
| `device_exit_simulation_mode` | 退出仿真模式 | `services` |

---

## 二、飞行任务 (Mission) - 37个接口

| 接口方法 | 说明 | 对应Topic |
|---------|------|----------|
| `load_wayline` | 加载航线 | `services` |
| `upload_wayline` | 航线上传 | `services` |
| `execute_task` | 执行任务 | `services` |
| `cancel_task` | 取消任务 | `services` |
| `pause_wayline` | 暂停航线 | `services` |
| `resume_wayline` | 恢复航线 | `services` |
| `flyto` | 指点飞行 | `services` |
| `take_off` | 一键起飞 | `services` |
| `one_key_return_home` | 一键返航 | `services` |
| `cancel_return_home` | 取消返航 | `services` |
| `set_flight_mode` | 设置飞行模式 | `services` |
| `go_home` | 返航 | `services` |
| `landing` | 降落 | `services` |
| `emergency_stop` | 紧急停止 | `services` |
| `start_takeoff_to_point` | 起飞到指定点 | `services` |
| `return_home` | 返航(别名) | `services` |
| `go_to_altitude` | 上升至指定高度 | `services` |
| `wayline_task_create` | 航线任务创建 | `services` |
| `wayline_task_execute` | 航线任务执行 | `services` |
| `wayline_task_pause` | 航线任务暂停 | `services` |
| `wayline_task_resume` | 航线任务恢复 | `services` |
| `wayline_task_cancel` | 航线任务取消 | `services` |
| `wayline_task_query` | 航线任务查询 | `services` |
| `wayline_mission_upload` | 航线上传(标准) | `services` |
| `wayline_mission_download` | 航线下载 | `services` |
| `wayline_mission_delete` | 航线删除 | `services` |
| `wayline_mission_query` | 航线查询 | `services` |
| `job_create` | 作业创建 | `services` |
| `job_delete` | 作业删除 | `services` |
| `job_query` | 作业查询 | `services` |
| `flighttask_prepare` | 飞行任务准备(大疆标准) | `services` |
| `flighttask_execute` | 飞行任务执行(大疆标准) | `services` |
| `flighttask_undo` | 飞行任务撤销 | `services` |
| `flighttask_return` | 飞行任务返航 | `services` |
| `flighttask_progress` | 飞行任务进度查询 | `services` |
| `flighttask_pause` | 飞行任务暂停(标准) | `services` |
| `flighttask_resume` | 飞行任务恢复(标准) | `services` |

---

## 三、负载控制 (Payload) - 56个接口

### 3.1 相机控制 (Camera)

| 接口方法 | 说明 |
|---------|------|
| `camera_photo_take` | 拍照 |
| `start_photo` | 开始拍照(定时/连拍) |
| `stop_photo` | 停止拍照 |
| `start_record` | 开始录像 |
| `stop_record` | 停止录像 |
| `set_camera_mode` | 设置相机模式 |
| `set_exposure_mode` | 设置曝光模式 |
| `zoom` | 变焦 |
| `set_focus_mode` | 设置对焦模式 |
| `set_focus_value` | 设置对焦值 |
| `spot_focus` | 点对焦 |
| `photo_storage_set` | 照片存储设置 |
| `camera_recover` | 相机恢复 |
| `camera_reset` | 相机重置 |
| `set_shutter_speed` | 设置快门速度 |
| `set_iso` | 设置ISO |
| `set_white_balance` | 设置白平衡 |
| `set_aperture` | 设置光圈 |
| `set_metering_mode` | 设置测光模式 |
| `set_resolution` | 设置分辨率 |
| `set_frame_rate` | 设置帧率 |
| `set_video_encoding` | 设置视频编码 |
| `camera_capability_query` | 相机能力查询 |

### 3.2 云台控制 (Gimbal)

| 接口方法 | 说明 |
|---------|------|
| `gimbalRotate` | 云台旋转 |
| `camera_look_at` | 相机看向指定点 |
| `set_gimbal_mode` | 设置云台模式 |
| `gimbal_reset` | 云台重置 |
| `gimbal_capability_query` | 云台能力查询 |
| `gimbal_status_query` | 云台状态查询 |

### 3.3 红外相机 (IR Thermal)

| 接口方法 | 说明 |
|---------|------|
| `ir_metering_mode_set` | 红外测温模式设置 |
| `ir_color_palette_set` | 红外色板设置 |
| `ir_display_mode_set` | 红外显示模式设置 |
| `ir_isotherm_set` | 红外等温线设置 |
| `ir_zoom_set` | 红外变焦设置 |

### 3.4 第三方负载 (Third Party Payload)

| 接口方法 | 说明 |
|---------|------|
| `drc_light_control_set` | 探照灯控制 |
| `drc_light_calibration` | 探照灯标定 |
| `drc_light_fine_tuning_set` | 探照灯微调 |
| `drc_speaker_tts_set` | 喊话器TTS设置 |
| `drc_speaker_play_volume_set` | 喊话器音量设置 |
| `drc_speaker_audio_play` | 喊话器播放 |
| `drc_speaker_audio_pause` | 喊话器暂停 |
| `drc_speaker_audio_stop` | 喊话器停止 |
| `psdk_function_set` | PSDK功能设置 |
| `set_thermal_mode` | 热成像模式设置 |
| `set_palette` | 色板设置 |
| `payload_authority_grab` | 负载权限获取 |
| `payload_authority_release` | 负载权限释放 |
| `payload_status_query` | 负载状态查询 |
| `payload_capability_query` | 负载能力查询 |
| `parachute_open` | 开伞 |
| `parachute_status_query` | 降落伞状态查询 |
| `night_vision_mode_set` | 夜视模式设置 |
| `gas_detector_data_get` | 气体检测数据获取 |
| `radar_status_query` | 雷达状态查询 |
| `searchlight_status_query` | 探照灯状态查询 |
| `speaker_status_query` | 喊话器状态查询 |

---

## 四、媒体管理 (Media) - 10个接口

| 接口方法 | 说明 | 对应Topic |
|---------|------|----------|
| `file_list_get` | 文件列表获取 | `services` |
| `file_delete` | 文件删除 | `services` |
| `adjust_upload_priority` | 调整上传优先级 | `services` |
| `storage_config_get` | 获取存储配置(OSS凭证) | `services` |
| `live_start` | 开始直播 | `services` |
| `live_stop` | 停止直播 | `services` |
| `set_livestream_lens` | 切换直播镜头 | `services` |
| `set_livestream_quality` | 设置直播画质 | `services` |
| `live_capability_query` | 直播能力查询 | `services` |
| `highest_priority_upload_flighttask_media` | 最高优先级上传任务媒体 | `services` |

---

## 五、DRC实时控制 (Direct Remote Control) - 9个接口

| 接口方法 | 说明 | 对应Topic |
|---------|------|----------|
| `drc_mode_enter` | 进入DRC模式 | `services` |
| `drc_mode_exit` | 退出DRC模式 | `services` |
| `drc_speed_push` | DRC速度推送 | `drc/down` |
| `drc_heading_push` | DRC航向推送 | `drc/down` |
| `drc_altitude_push` | DRC高度推送 | `drc/down` |
| `drc_flyto_point_push` | DRC指点飞行推送 | `drc/down` |
| `drone_control` | 无人机DRC控制(标准) | `drc/down` |
| `payload_control` | 负载DRC控制 | `drc/down` |
| `drc_gimbal_speed_push` | DRC云台速度推送 | `drc/down` |

---

## 六、机场管理 (DJI Dock) - 8个接口

| 接口方法 | 说明 | 对应Topic |
|---------|------|----------|
| `dock_status_query` | 机场状态查询 | `services` |
| `dock_control` | 机场控制 | `services` |
| `dock_alarm_query` | 机场告警查询 | `services` |
| `dock_task_schedule_query` | 任务调度查询 | `services` |
| `dock_task_schedule_set` | 任务调度设置 | `services` |
| `dock_emergency_stop` | 机场急停 | `services` |
| `dock_reset` | 机场复位 | `services` |
| `dock_weather_query` | 机场气象查询 | `services` |

---

## MQTT Topic 定义

```
thing/product/{gateway_sn}/services          # 云端下发服务调用
thing/product/{gateway_sn}/services_reply    # 设备端服务响应
thing/product/{gateway_sn}/events            # 设备端事件上报
thing/product/{gateway_sn}/events_reply      # 云端事件响应
thing/product/{gateway_sn}/property/set      # 云端属性设置
thing/product/{gateway_sn}/property/set_reply # 设备端属性响应
thing/product/{device_sn}/osd                # 定频OSD数据上报
thing/product/{device_sn}/state              # 状态变更上报
thing/product/{gateway_sn}/drc/down          # DRC下行控制
thing/product/{gateway_sn}/drc/up            # DRC上行数据
thing/product/{gateway_sn}/requests          # 设备请求云端
thing/product/{gateway_sn}/requests_reply    # 云端响应设备
```

---

## 标准消息格式

### 请求消息
```json
{
  "bid": "uuid",
  "data": {},
  "gateway": "EDGE-GW-001",
  "tid": "uuid",
  "timestamp": 1716789012000,
  "method": "ota_create",
  "need_reply": 1
}
```

### 响应消息
```json
{
  "bid": "uuid",
  "data": {
    "result": 0
  },
  "gateway": "EDGE-GW-001",
  "tid": "请求中的tid",
  "timestamp": 1716789012000,
  "method": "ota_create",
  "msg": ""
}
```

---

## 文件位置

| 模块 | 文件路径 | 说明 |
|-----|---------|------|
| 设备管理 | `src/device/manager.py` | 设备属性、OTA、HMS、日志 |
| 飞行任务 | `src/mission/manager.py` | 航线、任务、飞行控制 |
| 相机控制 | `src/payload/camera.py` | 相机参数、拍照录像 |
| 云台控制 | `src/payload/gimbal.py` | 云台旋转、模式 |
| 第三方负载 | `src/payload/third_party.py` | 探照灯、喊话器、PSDK |
| 媒体管理 | `src/media/manager.py` | 文件、直播、存储 |
| DRC控制 | `src/drc/controller.py` | 实时遥控 |
| 机场管理 | `src/cloud_api/dock_manager.py` | DJI Dock 特有接口 |
| 云端连接 | `src/cloud_api/connection_manager.py` | 设备绑定/上线/心跳 |
| 遥测上报 | `src/cloud_api/telemetry_reporter.py` | OSD/状态/事件格式化上报 |
| 能力集上报 | `src/cloud_api/capability_reporter.py` | 设备能力集上报 |
| 消息格式 | `src/utils/message.py` | Topic构建、消息构造 |
| 路由注册 | `src/main.py` | 所有接口统一注册 |

---

## 使用说明

所有接口已注册到 `TopicRouter`，通过MQTT `services` Topic 接收云端指令，自动路由到对应处理函数。处理函数返回响应字典后，由 `MQTTClient` 自动发布到 `services_reply` Topic。

**当前状态**: 接口框架已实现，所有方法包含标准参数解析和响应格式，具体业务逻辑标记为 `TODO` 待实现。
