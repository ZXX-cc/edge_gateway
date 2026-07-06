# DJI Cloud API Method 清单

本文档按当前 `src/main.py` 的 method 注册结果整理，用于人员1进行 DJI Dock 3 上云 API 验收。当前清单只说明“网关已路由”，不代表已经符合 Dock3 官方字段。

## 1. 当前口径

| 项目 | 结论 |
|---|---|
| 当前已注册 method | 223 |
| 统计来源 | `src/main.py` 中 `register_batch/register` |
| 协议目标 | DJI Dock 3 |
| 当前状态 | 已有 Dock/Dock1 风格实现，需要逐项迁移校验 |
| 当前 broker topic 前缀 | `thing/product/{sn}` |
| 主要验收人 | 人员1 |

状态列含义：

| 状态 | 含义 |
|---|---|
| 已路由 | `src/main.py` 已注册 method，MQTT 能分发到 handler |
| 旧 Dock1 风格 | 当前字段、注释或语义偏 Dock/Dock1，需要 Dock3 迁移 |
| 待 Dock3 校验 | 需要按 Dock3 官方文档核对请求体、响应体、枚举、单位和错误码 |
| 模拟器可测 | 当前可用 MQTT/SITL/机巢模拟器做基础链路验证 |
| 暂缓 | 保留接口或占位，等核心链路跑通后再补业务语义 |

## 2. Topic 总览

| Topic | 方向 | 当前用途 |
|---|---|---|
| `thing/product/{sn}/services` | 云端 -> 网关 | 云端下发服务调用 |
| `thing/product/{sn}/services_reply` | 网关 -> 云端 | 服务调用响应 |
| `thing/product/{sn}/requests` | 网关 -> 云端 | 设备主动请求云端 |
| `thing/product/{sn}/requests_reply` | 云端 -> 网关 | 云端响应设备请求 |
| `thing/product/{sn}/events` | 网关 -> 云端 | 事件上报 |
| `thing/product/{sn}/events_reply` | 云端 -> 网关 | 事件确认 |
| `thing/product/{sn}/property/set` | 云端 -> 网关 | 属性设置 |
| `thing/product/{sn}/property/set_reply` | 网关 -> 云端 | 属性设置响应 |
| `thing/product/{device_sn}/osd` | 网关 -> 云端 | 定频遥测 |
| `thing/product/{device_sn}/state` | 网关 -> 云端 | 状态变化 |
| `thing/product/{sn}/drc/down` | 云端 -> 网关 | DRC 下行控制 |
| `thing/product/{sn}/drc/up` | 网关 -> 云端 | DRC 上行数据 |

## 3. Method 分组清单

### 3.1 Device：51 个

| 状态 | Method |
|---|---|
| 已路由，待 Dock3 校验，部分模拟器可测 | `ota_create`, `ota_cancel`, `config_update`, `property_set`, `property_get`, `device_binding`, `device_unbinding`, `device_reboot`, `format_sd_card`, `cover_open`, `cover_close`, `charge_open`, `charge_close`, `putter_open`, `putter_close`, `device_lock`, `device_unlock`, `camera_mode_switch`, `supplement_light_open`, `supplement_light_close`, `sdr_workmode_switch`, `alternate_land_point_set`, `rtk_ref_points_set`, `rtk_ref_points_query`, `device_exit_rtk_mode`, `device_enter_simulation_mode`, `device_exit_simulation_mode`, `device_calibration`, `device_format`, `device_reset`, `workspace_bind_status`, `workspace_get`, `airport_bind_status`, `hms_get`, `hms_clear`, `device_log_list_get`, `device_log_upload`, `device_capability_query`, `cover_force_close`, `drone_open`, `drone_close`, `drone_format`, `debug_mode_open`, `debug_mode_close`, `battery_maintenance_switch`, `air_conditioner_mode_switch`, `alarm_state_switch`, `battery_store_mode_switch`, `config`, `update_topo`, `airsense_warning` |

说明：

- `airport_bind_status` 已用于启动时自动查询绑定状态，但仍需按 Dock3 组织管理文档校验。
- `cover_*`、`charge_*`、`putter_*` 与机场动作相关，需要和 Dock3 设备管理/远程调试文档重新对齐。

### 3.2 Mission / Wayline：42 个

| 状态 | Method |
|---|---|
| 已路由，旧 Dock1 风格，待 Dock3 校验，SITL 可测部分命令 | `load_wayline`, `upload_wayline`, `execute_task`, `cancel_task`, `pause_wayline`, `resume_wayline`, `flyto`, `fly_to_point`, `take_off`, `one_key_return_home`, `cancel_return_home`, `set_flight_mode`, `go_home`, `landing`, `emergency_stop`, `start_takeoff_to_point`, `return_home`, `go_to_altitude`, `wayline_task_create`, `wayline_task_execute`, `wayline_task_pause`, `wayline_task_resume`, `wayline_task_cancel`, `wayline_task_query`, `wayline_mission_upload`, `wayline_mission_download`, `wayline_mission_delete`, `wayline_mission_query`, `flighttask_prepare`, `flighttask_execute`, `flighttask_undo`, `flighttask_return`, `flighttask_progress`, `flighttask_pause`, `flighttask_resume`, `flight_setup_abort`, `flighttask_create`, `flighttask_recovery`, `return_home_cancel`, `job_create`, `job_delete`, `job_query` |

说明：

- `src/mission/manager.py` 中仍有 Dock1 文档引用，迁移时以 Dock3 `wayline.html` 和 `drc.html` 为准。
- `set_flight_mode` 当前存在 ArduCopter 模式表适配问题，是人员2优先修复项。

### 3.3 Payload：74 个

| 子类 | 状态 | Method |
|---|---|---|
| Camera | 已路由，待 Dock3 校验 | `camera_photo_take`, `start_photo`, `stop_photo`, `start_record`, `stop_record`, `set_camera_mode`, `set_exposure_mode`, `zoom`, `set_focus_mode`, `set_focus_value`, `spot_focus`, `photo_storage_set`, `camera_recover`, `camera_reset`, `set_shutter_speed`, `set_iso`, `set_white_balance`, `set_aperture`, `set_metering_mode`, `set_resolution`, `set_frame_rate`, `set_video_encoding`, `camera_capability_query`, `camera_photo_stop`, `camera_recording_start`, `camera_recording_stop`, `camera_focal_length_set`, `camera_point_focus_action`, `camera_exposure_mode_set`, `camera_focus_mode_set`, `camera_focus_value_set` |
| Gimbal | 已路由，待 Dock3 校验 | `gimbalRotate`, `camera_look_at`, `set_gimbal_mode`, `gimbal_reset`, `gimbal_capability_query`, `gimbal_status_query` |
| IR | 已路由，待 Dock3 校验 | `ir_metering_mode_set`, `ir_color_palette_set`, `ir_display_mode_set`, `ir_isotherm_set`, `ir_zoom_set` |
| Third party / PSDK | 已路由，多数暂缓 | `drc_light_control_set`, `drc_light_calibration`, `drc_light_fine_tuning_set`, `drc_speaker_tts_set`, `drc_speaker_play_volume_set`, `drc_speaker_audio_play`, `drc_speaker_audio_pause`, `drc_speaker_audio_stop`, `psdk_function_set`, `set_thermal_mode`, `set_palette`, `payload_authority_grab`, `payload_authority_release`, `payload_status_query`, `parachute_open`, `parachute_status_query`, `night_vision_mode_set`, `gas_detector_data_get`, `radar_status_query`, `searchlight_status_query`, `speaker_status_query`, `payload_capability_query`, `psdk_ui_resource_upload_result`, `psdk_floating_window_text`, `psdk_widget_value_set`, `psdk_input_box_text_set`, `speaker_replay`, `speaker_play_mode_set`, `speaker_play_stop`, `speaker_audio_play_start`, `speaker_tts_play_start`, `speaker_play_volume_set` |

说明：

- 当前阶段负载以模拟回包和状态 trace 为主。
- PSDK/ESDK 透传属于暂缓范围，先保留接口和调试入口。

### 3.4 Media / Live：13 个

| 状态 | Method |
|---|---|
| 已路由，待 Dock3 校验，部分模拟器可测 | `file_list_get`, `file_delete`, `adjust_upload_priority`, `storage_config_get`, `live_start`, `live_stop`, `set_livestream_lens`, `set_livestream_quality`, `live_capability_query`, `highest_priority_upload_flighttask_media`, `upload_flighttask_media_prioritize`, `live_lens_change`, `live_set_quality` |

说明：

- Dock3 中媒体管理文档路径为 `media.html`，Dock1 中为 `file.html`，迁移时不能沿用 Dock1 文件命名假设。
- 当前可先模拟文件列表、上传状态、直播开关和质量设置。

### 3.5 DRC：23 个

| 状态 | Method |
|---|---|
| 已路由，待 Dock3 校验，SITL 可测部分命令 | `drc_mode_enter`, `drc_mode_exit`, `drc_speed_push`, `drc_heading_push`, `drc_altitude_push`, `drc_flyto_point_push`, `drone_control`, `payload_control`, `drc_gimbal_speed_push`, `fly_to_point_stop`, `fly_to_point_update`, `camera_screen_drag`, `camera_aim`, `camera_screen_split`, `video_storage_set`, `camera_exposure_set`, `ir_metering_point_set`, `ir_metering_area_set`, `poi_mode_enter`, `poi_mode_exit`, `poi_circle_speed_set`, `drone_emergency_stop`, `takeoff_to_point` |

说明：

- DRC 是人员1和人员2的交界点：人员1定义 Dock3 MQTT 语义，人员2保证 MAVLink/SITL 执行路径。
- 当前不得在 DRC 热路径里阻塞等待长时间 ACK。

### 3.6 Dock：8 个

| 状态 | Method |
|---|---|
| 已路由，旧 Dock1 风格，待 Dock3 迁移，模拟器可测 | `dock_status_query`, `dock_control`, `dock_alarm_query`, `dock_task_schedule_query`, `dock_task_schedule_set`, `dock_emergency_stop`, `dock_reset`, `dock_weather_query` |

说明：

- `dock_status_query` 当前只能证明旧机场查询链路可用，不代表符合 DJI Dock 3。
- Dock3 迁移时应优先重构机场状态模型、属性输出、命令回包和事件。

### 3.7 Log：3 个

| 状态 | Method |
|---|---|
| 已路由，待 Dock3 校验 | `fileupload_list`, `fileupload_start`, `fileupload_update` |

### 3.8 FlySafe：3 个

| 状态 | Method |
|---|---|
| 已路由，暂缓 | `unlock_license_switch`, `unlock_license_update`, `unlock_license_list` |

### 3.9 Flight Area：2 个

| 状态 | Method |
|---|---|
| 已路由，待 Dock3 校验 | `flight_areas_update`, `flight_areas_get` |

说明：Dock3 文档使用 `custom-fly-region.html`，Dock1 文档使用 `custom-flight-area.html`，迁移时需要统一命名和字段。

### 3.10 Custom Data：4 个

| 状态 | Method |
|---|---|
| 已路由，暂缓 | `custom_data_transmission_from_esdk`, `custom_data_transmission_to_esdk`, `custom_data_transmission_from_psdk`, `custom_data_transmission_to_psdk` |

## 4. 人员1验收表

| 验收项 | 标准 |
|---|---|
| method 路由 | 已注册 method 可被 MQTT services/property/drc topic 分发 |
| 请求体 | 与 Dock3 官方文档一致 |
| 响应体 | `tid`、`bid`、`method`、`data.result`、`msg` 和业务字段一致 |
| topic | 下行和上行 topic 与 Dock3 文档一致 |
| 错误码 | 未知 method、参数错误、执行失败、超时都有明确返回 |
| request trace | 每个请求能记录 request、dispatch、execute、reply、error/timeout |
| 模拟验证 | 上云 API 模拟器可复现请求，Web/MQTT 订阅可看到响应 |

## 5. 下一步

1. 按 `docs/DJI_DOCK3_MIGRATION_GUIDE.md` 建立 Dock3 method/property/event/state/osd 矩阵。
2. 对照本文件 223 个已路由 method 标记 Dock3 合规状态。
3. 为 P0 method 编写上云 API 模拟器 payload。
4. 将失败或未实现项记录到 request trace 和迁移表。
