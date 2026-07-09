# MQTT Method 覆盖台账

更新时间：2026-07-09

本文档把 `src/main.py` 中实际注册的 MQTT method 归入客户需求、Dock3 迁移优先级和 30 天验收范围。它用于补足章节级需求矩阵之后的 method 级覆盖口径。当前策略是客户需求优先：客户需求涉及的 method 必须优先可用，客户暂未提及/当前业务未使用的 Dock3 能力可以延期迁移。

统计来源：静态解析 `src/main.py` 的 `self.router.register_batch(...)`。当前唯一 method 数为 **243**。

## 1. 使用规则

| 规则 | 要求 |
|---|---|
| 最终协议 | 客户需求涉及的上云能力必须以 DJI Dock 3 Cloud API 作为 topic、method、payload、reply、字段、枚举和错误码标准 |
| 旧 method | 旧 Dock/Dock1/Dock2 风格 method 可以作为兼容入口，但必须标明 adapter/legacy，不作为最终验收字段 |
| 未覆盖 method | 243 个 method 都必须有处置状态：Customer Required、Integration Support、Dock3 Official、Legacy Compat、Adapter/P2、Deferred、弃用候选 |
| P0/P1 method | 不能只“已路由”，必须有专项脚本、E2E 或明确验收证据 |
| P2 method | 可以有限可用，但必须有入口、错误码、日志、限制说明和不影响 P0/P1 的证明 |
| Deferred method | 客户暂未提及、当前业务未用到的 Dock3 能力可以暂缓字段级迁移，但必须记录原因、不得影响客户主链路 |
| Unknown method | 第 30 天交付时，客户需求内的 method 不允许返回 `Unknown method` |

状态口径：

| 状态 | 含义 |
|---|---|
| Verified | 已有代码、脚本或 Demo/日志证据，可作为当前验收基线 |
| Needs Field Audit | 链路可用，但字段、枚举、单位或回包结构仍需按 Dock3 精校 |
| Needs Feature Work | 已路由，但业务状态机、设备侧动作、mock 或 E2E 不足 |
| Adapter / P2 | 设备或第三方能力缺真实硬件，30 天内以 adapter/mock/限制说明交付 |
| Legacy Compat | 历史兼容入口，不作为最终 Dock3 标准入口 |
| Deferred | 客户暂未提及/当前业务未使用，保留入口或记录后续迁移条件 |

## 2. 分组计数

| Router category | Method 数 | 30 天优先级 | 客户能力域 | 当前判定 |
|---|---:|---|---|---|
| device | 51 | P0/P1/P2 | 设备管理、属性、绑定、机巢控制、HMS、日志、配置、OTA、AirSense | P0/P1 混合，需拆分验收 |
| mission | 42 | P0/P1 | 航线、飞控、返航、降落、一键启航、作业 | Wayline 基础已验证，KMZ/SITL/一键启航待补 |
| payload | 93 | P1/P2 | 相机、云台、ViewLink、红外、喊话器、探照灯、PSDK、第三方负载 | 大部分需 P1/P2 adapter 和专项脚本 |
| media | 13 | P1/P2 | 媒体文件、上传、直播、镜头、质量 | P1 必补专项 |
| drc | 24 | P0/P1/P2 | DRC 建链、杆量、指点飞行、负载控制、POI | 基础已验证，心跳/超时/指点飞行待补 |
| dock | 8 | P0/P1 legacy | 机场状态、控制、告警、调度、天气 | 旧 Dock 风格，必须迁移为 Dock3 properties/cmd/HMS |
| log | 3 | P1 | 远程日志 | P1 必补专项 |
| flysafe | 3 | P2 | 远程解禁 | P2 adapter |
| flight_area | 2 | P2 | 自定义飞行区 | P2/P1 边界，基础缓存和回包需补 |
| custom_data | 4 | P2 | PSDK/ESDK 透传 | P2 adapter |
| 合计 | 243 | 全部处置 | 客户文档相关功能和当前代码入口 | 不能按旧 224 口径验收；不代表 243 个 method 都必须在 30 天内完成 Dock3 字段级迁移 |

## 3. 30 天验收分组

| 验收域 | 关联 method | Backlog | 必须证据 |
|---|---|---|---|
| P0 MQTT/属性/拓扑 | `property_set`, `property_get`, `airport_bind_status`, `workspace_bind_status`, `workspace_get`, `update_topo`, `device_binding`, `device_unbinding` | P0-01 到 P0-05、P1-14 | P0 批量、properties、device、organization 专项 |
| P0/P1 机巢和 CMD | `cover_open`, `cover_close`, `cover_force_close`, `charge_open`, `charge_close`, `putter_open`, `putter_close`, `drone_open`, `drone_close`, `debug_mode_open`, `debug_mode_close`, `battery_maintenance_switch`, `air_conditioner_mode_switch`, `alarm_state_switch`, `battery_store_mode_switch`, `dock_control`, `dock_emergency_stop`, `dock_reset` | P0-11 到 P0-13、P1-02、P1-15 | CMD 专项、机巢 v1.3 专项 |
| P0/P1 飞控和航线 | `flighttask_prepare`, `flighttask_execute`, `flighttask_undo`, `flighttask_return`, `flighttask_progress`, `flighttask_pause`, `flighttask_resume`, `return_home_cancel`, `take_off`, `landing`, `go_home`, `return_home`, `set_flight_mode`, `start_takeoff_to_point`, `takeoff_to_point` | P0-08、P0-10、P1-01、P1-04 到 P1-06 | Wayline、DRC、SITL、起飞流程 E2E |
| P0/P1 DRC | `drc_mode_enter`, `drc_mode_exit`, `stick_control`, `drone_control`, `payload_control`, `drc_gimbal_speed_push`, `drc_speed_push`, `drc_heading_push`, `drc_altitude_push`, `drc_flyto_point_push`, `fly_to_point_stop`, `fly_to_point_update` | P0-09、P1-03、P1-04 | DRC 专项、超时恢复、SITL |
| P1 媒体和直播 | `file_list_get`, `file_delete`, `adjust_upload_priority`, `storage_config_get`, `live_start`, `live_stop`, `set_livestream_lens`, `set_livestream_quality`, `highest_priority_upload_flighttask_media`, `upload_flighttask_media_prioritize`, `live_lens_change`, `live_set_quality` | P1-07、P1-08 | media/live 专项 |
| P1 相机/云台 | `camera_photo_take`, `camera_recording_start`, `camera_recording_stop`, `camera_focal_length_set`, `camera_point_focus_action`, `camera_exposure_mode_set`, `camera_focus_mode_set`, `camera_focus_value_set`, `gimbal_rotate`, `camera_look_at`, `set_gimbal_mode`, `gimbal_reset` | P1-09 | payload 专项或 media/live 联合专项 |
| P1 运维 | `hms_get`, `hms_clear`, `device_log_list_get`, `device_log_upload`, `fileupload_list`, `fileupload_start`, `fileupload_update`, `ota_create`, `ota_cancel`, `firmware_upgrade`, `config_update`, `config_get`, `config_rollback`, `config` | P1-10 到 P1-13、P1-16 | HMS/log、firmware/config 均已通过 Ubuntu 专项；真实 OTA/上传 adapter 后续补 |
| P2 高级负载 | ViewLink 全组、IR 全组、speaker/searchlight/radar/gas/parachute/PSDK widgets | P2-01、P2-02、P2-06、P2-07 | adapter/mock、限制说明、错误码 |
| P2 飞安和区域 | `unlock_license_switch`, `unlock_license_update`, `unlock_license_list`, `flight_areas_update`, `flight_areas_get`, `airsense_warning`, `alternate_land_point_set`, `rtk_ref_points_set`, `rtk_ref_points_query` | P2-03 到 P2-05、P2-08 | P2 风险登记、mock 回包、限制说明 |

说明：上表是 30 天优先验收域。未进入这些验收域且客户暂未提及的 Dock3 官方能力，可以在第 30 天标为 Deferred；客户需求后来使用时，再提升为 P0/P1/P2 并补脚本。

## 4. 分组方法清单

### 4.1 device：51

```text
ota_create, ota_cancel, firmware_upgrade, firmware_upgrade_cancel,
config_update, config_get, config_rollback, config, property_set, property_get,
device_binding, device_unbinding, device_reboot, format_sd_card,
cover_open, cover_close, charge_open, charge_close, putter_open,
putter_close, device_lock, device_unlock, camera_mode_switch,
supplement_light_open, supplement_light_close, sdr_workmode_switch,
alternate_land_point_set, rtk_ref_points_set, rtk_ref_points_query,
device_exit_rtk_mode, device_enter_simulation_mode,
device_exit_simulation_mode, device_calibration, device_format,
device_reset, workspace_bind_status, workspace_get,
airport_bind_status, hms_get, hms_clear, device_log_list_get,
device_log_upload, device_capability_query, cover_force_close,
drone_open, drone_close, drone_format, debug_mode_open,
debug_mode_close, battery_maintenance_switch,
air_conditioner_mode_switch, alarm_state_switch,
battery_store_mode_switch, config, update_topo, airsense_warning
```

### 4.2 mission：42

```text
load_wayline, upload_wayline, execute_task, cancel_task,
pause_wayline, resume_wayline, flyto, fly_to_point, take_off,
one_key_return_home, cancel_return_home, set_flight_mode, go_home,
landing, emergency_stop, start_takeoff_to_point, return_home,
go_to_altitude, wayline_task_create, wayline_task_execute,
wayline_task_pause, wayline_task_resume, wayline_task_cancel,
wayline_task_query, wayline_mission_upload, wayline_mission_download,
wayline_mission_delete, wayline_mission_query, flighttask_prepare,
flighttask_execute, flighttask_undo, flighttask_return,
flighttask_progress, flighttask_pause, flighttask_resume,
flight_setup_abort, flighttask_create, flighttask_recovery,
return_home_cancel, job_create, job_delete, job_query
```

### 4.3 payload：93

```text
camera_photo_take, start_photo, stop_photo, start_record, stop_record,
set_camera_mode, set_exposure_mode, zoom, set_focus_mode,
set_focus_value, spot_focus, photo_storage_set, camera_recover,
camera_reset, set_shutter_speed, set_iso, set_white_balance,
set_aperture, set_metering_mode, set_resolution, set_frame_rate,
set_video_encoding, camera_capability_query, camera_photo_stop,
camera_recording_start, camera_recording_stop,
camera_focal_length_set, camera_point_focus_action,
camera_exposure_mode_set, camera_focus_mode_set,
camera_focus_value_set, gimbalRotate, gimbal_rotate, camera_look_at,
set_gimbal_mode, gimbal_reset, gimbal_capability_query,
gimbal_status_query, viewlink_initialize, viewlink_connect,
viewlink_disconnect, viewlink_is_connected, viewlink_move,
viewlink_stop, viewlink_home, viewlink_turn_to, viewlink_zoom_in,
viewlink_zoom_out, viewlink_stop_zoom, viewlink_start_track,
viewlink_stop_track, viewlink_take_photo, viewlink_start_recording,
viewlink_stop_recording, viewlink_is_recording,
viewlink_set_record_mode, ir_metering_mode_set,
ir_color_palette_set, ir_display_mode_set, ir_isotherm_set,
ir_zoom_set, drc_light_control_set, drc_light_calibration,
drc_light_fine_tuning_set, drc_speaker_tts_set,
drc_speaker_play_volume_set, drc_speaker_audio_play,
drc_speaker_audio_pause, drc_speaker_audio_stop, psdk_function_set,
set_thermal_mode, set_palette, payload_authority_grab,
payload_authority_release, payload_status_query, parachute_open,
parachute_status_query, night_vision_mode_set, gas_detector_data_get,
radar_status_query, searchlight_status_query, speaker_status_query,
payload_capability_query, psdk_ui_resource_upload_result,
psdk_floating_window_text, psdk_widget_value_set,
psdk_input_box_text_set, speaker_replay, speaker_play_mode_set,
speaker_play_stop, speaker_audio_play_start, speaker_tts_play_start,
speaker_play_volume_set
```

### 4.4 media：13

```text
file_list_get, file_delete, adjust_upload_priority, storage_config_get,
live_start, live_stop, set_livestream_lens, set_livestream_quality,
live_capability_query, highest_priority_upload_flighttask_media,
upload_flighttask_media_prioritize, live_lens_change, live_set_quality
```

### 4.5 drc：24

```text
drc_mode_enter, drc_mode_exit, drc_speed_push, drc_heading_push,
drc_altitude_push, drc_flyto_point_push, stick_control,
drone_control, payload_control, drc_gimbal_speed_push,
fly_to_point_stop, fly_to_point_update, camera_screen_drag,
camera_aim, camera_screen_split, video_storage_set,
camera_exposure_set, ir_metering_point_set, ir_metering_area_set,
poi_mode_enter, poi_mode_exit, poi_circle_speed_set,
drone_emergency_stop, takeoff_to_point
```

### 4.6 dock：8

```text
dock_status_query, dock_control, dock_alarm_query,
dock_task_schedule_query, dock_task_schedule_set, dock_emergency_stop,
dock_reset, dock_weather_query
```

### 4.7 log：3

```text
fileupload_list, fileupload_start, fileupload_update
```

### 4.8 flysafe：3

```text
unlock_license_switch, unlock_license_update, unlock_license_list
```

### 4.9 flight_area：2

```text
flight_areas_update, flight_areas_get
```

### 4.10 custom_data：4

```text
custom_data_transmission_from_esdk, custom_data_transmission_to_esdk,
custom_data_transmission_from_psdk, custom_data_transmission_to_psdk
```

## 5. 关项要求

| 关项项 | 要求 |
|---|---|
| Method 处置 | 243 个 method 都必须在本文或后续台账中标明 Customer Required、Integration Support、Dock3 official、legacy、adapter、P2 或 Deferred |
| 验收脚本 | P0/P1 method 所在验收域必须有专项脚本或 E2E |
| 字段校验 | Dock3 official method 必须有 payload/reply/enum/unit 对照 |
| 错误码 | 不支持、缺硬件、参数错误、超时和 busy 都必须返回 Dock3 合理错误码 |
| 日志和 trace | 每个 method 调用能按 tid/bid 或 operation id 追踪 |
| 文档同步 | method 状态变化时同步更新 backlog、gap register、scorecard 和 runbook |

## 6. Deferred 判定规则

| 条件 | 可以 Deferred |
|---|---|
| 客户 Word 文档未提及该能力 | 是 |
| 当前一键启航、航线、媒体、直播、运维、机巢主链路未调用 | 是 |
| 不影响 P0/P1 E2E | 是 |
| 有明确后续触发条件 | 是 |
| 会导致客户主链路缺功能 | 否 |
| 只是实现困难但客户需要 | 否 |
| 会返回 `Unknown method` 给客户需求流程 | 否 |
