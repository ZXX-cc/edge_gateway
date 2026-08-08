# MQTT Method 覆盖台账

更新时间：2026-08-08

本文档把 `src/main.py` 中实际注册的 MQTT method 归入客户需求、Dock3 迁移优先级和 30 天验收范围。它用于补足章节级需求矩阵之后的 method 级覆盖口径。当前策略是客户需求优先：客户需求涉及的 method 必须优先可用，客户暂未提及/当前业务未使用的 Dock3 能力可以延期迁移。

统计来源：静态解析 `src/main.py` 的 `self.router.register_batch(...)`。当前默认唯一 method 数为 **181**；设置 `EDGE_GATEWAY_ENABLE_TEST_HOOKS=1` 时额外注册 2 个调试 hook，总数为 **183**。当前 `/services` 公开白名单为 **131** 个 method；注册在 Router 但不在白名单内的旧入口不会作为云端公开 services 入口验收。2026-08-06 已移除无法对照到 DJI 官方 method 的 ViewLink SDK 调试入口、非官方 IR 扩展、项目自定义喊话器/状态查询入口和 DRC 自定义别名；内部 handler 可继续供官方 method 编排复用。

## 1. 使用规则

| 规则 | 要求 |
|---|---|
| 最终协议 | 客户需求涉及的上云能力必须以 DJI Dock 3 Cloud API 作为 topic、method、payload、reply、字段、枚举和错误码标准 |
| 旧 method | 旧 Dock/Dock1/Dock2 风格 method 可以作为兼容入口，但必须标明 adapter/legacy，不作为最终验收字段 |
| 未覆盖 method | 181 个默认 method 都必须有处置状态：Customer Required、Integration Support、Dock3 Official、Legacy Compat、Adapter/P2、Deferred、弃用候选；2 个测试 hook 不计入交付验收 |
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
| device | 53 | P0/P1/P2 | 设备管理、属性、绑定、机巢控制、HMS、日志、配置、OTA、AirSense | P0/P1 混合，需拆分验收 |
| mission | 54 | P0/P1 | 航线、飞控、返航、降落、一键启航、作业 | Wayline 基础已验证，一键启航本地闭环已补；KMZ/SITL/Ubuntu 实证待补 |
| payload | 39 | P1/P2 | 相机、云台、红外测温、喊话器、探照灯、PSDK、第三方负载 | 仅保留可对照到 DJI 官方 method 或原始兼容入口的公开路由；ViewLink SDK 作为内部实现 |
| media | 17 | P1/P2 | 媒体文件、上传、直播、镜头、质量 | P1 必补专项 |
| services | 3 | P0/P1 | DRC services 控制权 | Dock3 services topic 下的跨域入口 |
| drc | 4 | P0/P1/P2 | DRC 建链后心跳、杆量、飞行控制、急停 | 仅保留 Dock3 DRC 官方方法；项目自定义控制别名已从公开路由移除 |
| dock | 0 | Removed legacy | 机场状态、控制、告警、调度、天气 | 旧 Dock/Dock1 services 入口已移除；改走 Dock3 properties/CMD/HMS |
| log | 3 | P1 | 远程日志 | P1 必补专项 |
| flysafe | 3 | P2 | 远程解禁 | P2 adapter |
| flight_area | 1 | P2 | 自定义飞行区 | P2/P1 边界，基础缓存和回包需补 |
| custom_data | 4 | P2 | PSDK/ESDK 透传 | P2 adapter |
| 合计 | 181 | 全部处置 | 客户文档相关功能和当前代码入口 | 不能按旧 224/243/267/288/189 口径验收；不代表 181 个 method 都必须在 30 天内完成 Dock3 字段级迁移 |

## 3. 30 天验收分组

| 验收域 | 关联 method | Backlog | 必须证据 |
|---|---|---|---|
| P0 MQTT/属性/拓扑 | `property_set`, `property_get`, `update_topo`；`airport_bind_status/workspace_* / device_binding/device_unbinding` 为 requests/status/internal 兼容口径，不作为 `/services` 公开验收入口 | P0-01 到 P0-05、P1-14 | P0 批量、properties、device、organization 专项 |
| P0/P1 机巢和 CMD | `cover_open`, `cover_close`, `cover_force_close`, `charge_open`, `charge_close`, `putter_open`, `putter_close`, `drone_open`, `drone_close`, `debug_mode_open`, `debug_mode_close`, `battery_maintenance_switch`, `air_conditioner_mode_switch`, `alarm_state_switch`, `battery_store_mode_switch` | P0-11 到 P0-13、P1-02、P1-15 | CMD 专项、机巢 v1.3 专项 |
| P0/P1 飞控和航线 | `flighttask_prepare`, `flighttask_execute`, `flighttask_undo`, `flighttask_return`, `flighttask_progress`, `flighttask_pause`, `flighttask_resume`, `resume_wayline_task`, `return_home_cancel`, `take_off`, `landing`, `go_home`, `return_home`, `set_flight_mode`, `start_takeoff_to_point`, `takeoff_to_point` | P0-08、P0-10、P1-01、P1-04 到 P1-06 | Wayline、DRC、SITL、起飞流程 E2E |
| P0/P1 DRC | `drc_mode_enter`, `drc_mode_exit`, `heart_beat`, `stick_control`, `drone_control`, `drone_emergency_stop`, `fly_to_point`, `fly_to_point_stop`, `fly_to_point_update` | P0-09、P1-03、P1-04 | DRC 专项、超时恢复、SITL |
| P1 媒体和直播 | `file_list_get`, `file_delete`, `adjust_upload_priority`, `live_start`, `live_stop`, `live_capability_query`, `highest_priority_upload_flighttask_media`, `upload_flighttask_media_prioritize`, `live_lens_change`, `live_set_quality`；`storage_config_get/set_livestream_*` 当前不在默认公开 services 口径 | P1-07、P1-08 | media/live 专项 |
| P1 相机/云台 | `camera_photo_take`, `camera_recording_start`, `camera_recording_stop`, `camera_focal_length_set`, `camera_point_focus_action`, `camera_exposure_mode_set`, `camera_focus_mode_set`, `camera_focus_value_set`, `gimbalRotate`, `camera_screen_drag`, `camera_look_at`, `gimbal_reset` | P1-09 | payload 专项或 media/live 联合专项 |
| P1 运维 | `hms_get`, `hms_clear`, `device_log_list_get`, `device_log_upload`, `fileupload_list`, `fileupload_start`, `fileupload_update`, `ota_create`, `ota_cancel`, `firmware_upgrade`, `config_update`, `config_get`, `config_rollback`, `config` | P1-10 到 P1-13、P1-16 | HMS/log、firmware/config 均已通过 Ubuntu 专项；真实 OTA/上传 adapter 后续补 |
| P2 高级负载 | `camera_screen_split`, `ir_metering_mode_set`, `ir_metering_point_set`, `ir_metering_area_set`, speaker/searchlight/PSDK widgets | P2-01、P2-02、P2-06、P2-07 | adapter/mock、限制说明、错误码 |
| P2 飞安和区域 | `unlock_license_switch`, `unlock_license_update`, `unlock_license_list`, `flight_areas_update`, `flight_areas_get`, `airsense_warning`, `alternate_land_point_set`, `rtk_ref_points_set`, `rtk_ref_points_query` | P2-03 到 P2-05、P2-08 | P2 风险登记、mock 回包、限制说明 |

说明：上表是 30 天优先验收域。未进入这些验收域且客户暂未提及的 Dock3 官方能力，可以在第 30 天标为 Deferred；客户需求后来使用时，再提升为 P0/P1/P2 并补脚本。

## 4. 分组方法清单

### 4.1 device：53

```text
ota_create, ota_cancel, firmware_upgrade, firmware_upgrade_cancel,
config_update, config_get, config_rollback, property_set, property_get,
gateway_state_query, device_reboot, format_sd_card,
cover_open, cover_close, charge_open, charge_close, putter_open,
putter_close, device_lock, device_unlock,
supplement_light_open, supplement_light_close, sdr_workmode_switch,
sim_slot_switch, esim_activate, esim_operator_switch,
alternate_land_point_set, rtk_ref_points_set, rtk_calibration, rtk_ref_points_query,
device_exit_rtk_mode, device_enter_simulation_mode,
device_exit_simulation_mode, device_calibration, device_format,
device_reset, hms_get, hms_clear, device_log_list_get,
device_log_upload, cover_force_close,
drone_open, drone_close, drone_format, debug_mode_open,
debug_mode_close, battery_maintenance_switch,
air_conditioner_mode_switch, alarm_state_switch,
battery_store_mode_switch, config, update_topo, airsense_warning
```

### 4.2 mission：54

```text
load_wayline, upload_wayline, execute_task, cancel_task,
pause_wayline, resume_wayline, flyto, take_off,
one_key_return_home, cancel_return_home, set_flight_mode, go_home,
landing, emergency_stop, start_takeoff_to_point, return_home,
takeoff_to_point, fly_to_point, fly_to_point_stop, fly_to_point_update,
go_to_altitude, wayline_task_create, wayline_task_execute,
wayline_task_pause, wayline_task_resume, wayline_task_cancel,
wayline_task_query, wayline_mission_upload, wayline_mission_download,
wayline_mission_delete, wayline_mission_query, flighttask_prepare,
flighttask_execute, flighttask_undo, flighttask_stop,
flighttask_progress, flighttask_progress_get, flighttask_pause, flighttask_resume,
resume_wayline_task, flight_setup_abort, flighttask_create, flighttask_recovery,
return_home_cancel, return_specific_home, flighttask_resource_get,
in_flight_wayline_deliver, in_flight_wayline_stop,
in_flight_wayline_recover, in_flight_wayline_cancel,
job_create, job_delete, job_query
```

### 4.3 payload：39

```text
gimbalRotate, camera_screen_drag, drc_light_calibration,
drc_light_fine_tuning_set, drc_speaker_tts_set,
drc_speaker_play_volume_set, payload_authority_release,
psdk_ui_resource_upload_result, psdk_floating_window_text,
psdk_widget_value_set, psdk_input_box_text_set, speaker_replay,
speaker_play_mode_set, speaker_play_stop, speaker_audio_play_start,
speaker_tts_play_start, speaker_play_volume_set, camera_frame_zoom, camera_mode_switch,
camera_photo_take, camera_photo_stop, camera_recording_start,
camera_recording_stop, camera_focal_length_set, camera_exposure_set,
camera_focus_mode_set, camera_focus_value_set,
camera_point_focus_action, camera_exposure_mode_set, camera_aim,
camera_screen_split, gimbal_reset, camera_look_at,
photo_storage_set, video_storage_set, payload_authority_grab,
ir_metering_mode_set, ir_metering_point_set, ir_metering_area_set
```

### 4.4 media：17

```text
file_list_get, file_download, download_media, file_delete, adjust_upload_priority,
live_start, live_start_push, live_stop, live_stop_push, live_camera_change,
set_livestream_lens, set_livestream_quality,
live_capability_query, highest_priority_upload_flighttask_media,
upload_flighttask_media_prioritize, live_lens_change, live_set_quality
```

### 4.5 drc：4

```text
heart_beat, stick_control, drone_control, drone_emergency_stop
```

### 4.6 dock：0

```text
2026-08-05 已移除旧 Dock/Dock1 风格 services 入口：
dock_status_query, dock_control, dock_alarm_query,
dock_task_schedule_query, dock_task_schedule_set, dock_emergency_stop,
dock_reset, dock_weather_query。
当前云端验收应使用 Dock3 properties、CMD、HMS、gateway_state_query。
```

### 4.7 log：3

```text
fileupload_list, fileupload_start, fileupload_update
```

### 4.8 flysafe：3

```text
unlock_license_switch, unlock_license_update, unlock_license_list
```

### 4.9 flight_area：1

```text
flight_areas_update
```

### 4.10 custom_data：4

```text
custom_data_transmission_from_esdk, custom_data_transmission_to_esdk,
custom_data_transmission_from_psdk, custom_data_transmission_to_psdk
```

### 4.11 services 公开边界

`src/main.py::ALLOWED_SERVICE_METHODS` 当前允许 131 个 method 进入 `thing/product/{sn}/services`。下列 Router 已注册入口不在默认 services 白名单内，保留为内部兼容、专项工具或非 services topic 口径；平台侧 Dock3 验收不要直接调用这些旧入口：

```text
firmware_upgrade, firmware_upgrade_cancel, format_sd_card,
device_lock, device_unlock, device_exit_rtk_mode,
device_enter_simulation_mode, device_exit_simulation_mode,
device_calibration, device_reset, config, airsense_warning,
load_wayline, upload_wayline, execute_task, cancel_task,
pause_wayline, resume_wayline, flyto, take_off,
one_key_return_home, cancel_return_home, set_flight_mode,
go_home, landing, emergency_stop, start_takeoff_to_point,
go_to_altitude, wayline_task_create, wayline_task_execute,
wayline_task_pause, wayline_task_resume, wayline_task_cancel,
wayline_task_query, wayline_mission_upload, wayline_mission_download,
wayline_mission_delete, wayline_mission_query, job_create,
job_delete, job_query, download_media, set_livestream_lens,
set_livestream_quality, heart_beat, stick_control,
drone_control, drone_emergency_stop
```

另外，`airport_bind_status`、`airport_organization_get`、`airport_organization_bind`、`device_binding`、`device_unbinding`、`workspace_bind_status`、`workspace_get`、`storage_config_get`、`flight_areas_get`、`device_capability_query`、`update_topo` 是 requests/status/internal 或非 services 口径；如果误发到 `/services`，当前 MQTT 层会按已知非 services method 忽略或拒绝，避免把它们伪装成公开云端 services。

## 5. 关项要求

| 关项项 | 要求 |
|---|---|
| Method 处置 | 181 个默认 method 都必须在本文或后续台账中标明 Customer Required、Integration Support、Dock3 official、legacy、adapter、P2 或 Deferred |
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
