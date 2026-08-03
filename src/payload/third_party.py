"""
第三方负载控制模块
处理探照灯、喊话器、PSDK等第三方负载
"""
from typing import Optional, Callable
from pymavlink import mavutil
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.dji_error_codes import DOCK_SYSTEM_ERROR, INVALID_COMMAND_FORMAT
from src.payload.jz_speaker import JzSpeakerAdapter, JzSpeakerResult

logger = get_logger("third_party")


class ThirdPartyController:
    """
    第三方负载控制器

    职责:
    1. 探照灯控制(开关、亮度、校准)
    2. 喊话器控制(TTS、音频播放)
    3. PSDK功能调用
    4. 红外相机控制

    API方法(drc/down通道):
    - drc_light_control_set, drc_light_calibration
    - drc_speaker_tts_set, drc_speaker_audio_play, drc_speaker_play_volume_set
    - psdk_function_set
    - set_thermal_mode, set_palette
    """

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # 探照灯状态
        self._light_on = False
        self._light_brightness = 100
        self._light_mode = 0  # 0:常亮 1:闪烁 2:随动

        # 喊话器状态
        self._speaker_volume = 80
        self._speaker_playing = False
        self._tts_text = ""
        self._speaker_play_mode = 0
        self._speaker_adapter = JzSpeakerAdapter()

        # MAVLink连接引用(由外部注入)
        self._mavlink_conn = None

        # 回调
        self._event_callback: Optional[Callable] = None
        self._payload_topology = None
        self._payload_state_provider: Optional[Callable] = None

    def set_mavlink_connection(self, mavlink_conn):
        """注入显式启用的飞控 MAVLink 兼容链路。"""
        self._mavlink_conn = mavlink_conn

    def set_payload_topology(self, topology, state_provider: Optional[Callable] = None):
        """注入网关直连负载拓扑及连接状态提供器。"""
        self._payload_topology = topology
        self._payload_state_provider = state_provider
        speaker = topology.get_device('speaker') if topology else {}
        control = (speaker.get('channels') or {}).get('control_audio') or {}
        serial_config = control.get('serial') if isinstance(control.get('serial'), dict) else {}
        self._speaker_adapter = JzSpeakerAdapter({
            **serial_config,
            'enabled': bool(speaker.get('enabled', True)) and control.get('adapter') == 'jz_speaker',
        })
        if self._speaker_adapter.auto_connect:
            result = self._speaker_adapter.connect()
            if not result.ok:
                logger.warning("JZ speaker auto connect failed: %s", result.message)

    def _send_mavlink_command(self, command: int, params: list) -> bool:
        """发送MAVLink COMMAND_LONG"""
        if not self._mavlink_conn or not self._mavlink_conn.master:
            return False
        try:
            self._mavlink_conn.master.mav.command_long_send(
                self._mavlink_conn.master.target_system,
                self._mavlink_conn.master.target_component,
                command,
                0,
                *params
            )
            return True
        except Exception as e:
            logger.error(f"ThirdParty command send failed: {e}")
            return False

    def set_event_callback(self, callback: Callable):
        self._event_callback = callback

    # ---- PSDK 事件上报 ----

    def report_psdk_ui_resource_upload_result(self, psdk_index: int, object_key: str,
                                               size: int, result: int = 0):
        """
        上报 PSDK UI 资源包上传结果 (psdk_ui_resource_upload_result)
        """
        if self._event_callback:
            event = MessageBuilder.build_event(
                'psdk_ui_resource_upload_result',
                {'psdk_index': psdk_index, 'object_key': object_key,
                 'size': size, 'result': result},
                self.gateway_sn
            )
            self._event_callback(event)

    def report_psdk_floating_window_text(self, psdk_index: int, value: str):
        """
        上报 PSDK 浮窗文本 (psdk_floating_window_text)
        """
        if self._event_callback:
            event = MessageBuilder.build_event(
                'psdk_floating_window_text',
                {'psdk_index': psdk_index, 'value': value},
                self.gateway_sn
            )
            self._event_callback(event)

    def report_speaker_audio_play_progress(self, psdk_index: int, status: str,
                                           percent: int, step_key: str = "",
                                           md5: str = "", result: int = 0):
        """
        上报喊话器音频播放进度 (speaker_audio_play_start_progress)
        """
        if self._event_callback:
            data = {
                'result': result,
                'output': {
                    'psdk_index': psdk_index,
                    'status': status,
                    'md5': md5,
                    'progress': {
                        'percent': percent,
                        'step_key': step_key or 'play'
                    }
                }
            }
            event = MessageBuilder.build_event(
                'speaker_audio_play_start_progress', data, self.gateway_sn
            )
            self._event_callback(event)

    def report_speaker_tts_play_progress(self, psdk_index: int, status: str,
                                         percent: int, step_key: str = "",
                                         md5: str = "", result: int = 0):
        """
        上报喊话器 TTS 播放进度 (speaker_tts_play_start_progress)
        """
        if self._event_callback:
            data = {
                'result': result,
                'output': {
                    'psdk_index': psdk_index,
                    'status': status,
                    'md5': md5,
                    'progress': {
                        'percent': percent,
                        'step_key': step_key or 'play'
                    }
                }
            }
            event = MessageBuilder.build_event(
                'speaker_tts_play_start_progress', data, self.gateway_sn
            )
            self._event_callback(event)

    # ---- 探照灯API ----

    def handle_drc_light_control_set(self, msg: dict) -> dict:
        """
        探照灯控制

        Request: { turn_on, brightness(0-100), mode(0=常亮/1=闪烁/2=随动) }
        """
        data = msg.get('data', {})
        turn_on = data.get('turn_on')
        brightness = data.get('brightness')
        mode = data.get('mode')

        if turn_on is not None:
            self._light_on = turn_on
        if brightness is not None:
            self._light_brightness = max(0, min(100, brightness))
        if mode is not None:
            self._light_mode = mode

        logger.info(f"Light control: on={self._light_on}, "
                   f"brightness={self._light_brightness}, mode={self._light_mode}")

        # 通过MAVLink发送探照灯控制 (使用DO_SET_SERVO模拟PWM输出)
        pwm_value = self._light_brightness * 10 if self._light_on else 0
        self._send_mavlink_command(
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
            [9, pwm_value, 0, 0, 0, 0, 0]  # servo9 作为探照灯通道
        )

        return MessageBuilder.build_reply(msg, result=0)

    def handle_drc_light_calibration(self, msg: dict) -> dict:
        """探照灯校准"""
        logger.info("Light calibration")
        # TODO: 执行校准流程
        return MessageBuilder.build_reply(msg, result=0)

    def handle_drc_light_fine_tuning_set(self, msg: dict) -> dict:
        """探照灯微调"""
        data = msg.get('data', {})
        logger.info(f"Light fine tuning: {data}")
        return MessageBuilder.build_reply(msg, result=0)

    # ---- 喊话器API ----

    def handle_drc_speaker_tts_set(self, msg: dict) -> dict:
        """
        TTS语音合成设置

        Request: { text, speed, volume, voice_type }
        """
        data = msg.get('data', {})
        text = data.get('text', '')
        speed = data.get('speed', 1.0)
        volume = data.get('volume', 80)
        voice_type = data.get('voice_type', 'default')

        if not text:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data={'output': {'status': 'failed', 'message': 'tts text is required'}},
            )

        self._tts_text = text
        self._speaker_volume = volume

        logger.info(f"TTS: text='{text}', speed={speed}, volume={volume}, voice={voice_type}")

        result = self._speaker_adapter.play_tts(
            text,
            speed=speed,
            volume=volume,
            voice_type=voice_type,
        )
        if result.ok:
            self._speaker_playing = True
            self.report_speaker_tts_play_progress(
                int(data.get('psdk_index', 0) or 0),
                "ok",
                100,
                step_key="play",
            )

        return self._speaker_reply(msg, result)

    def handle_drc_speaker_play_volume_set(self, msg: dict) -> dict:
        """播放音量设置"""
        data = msg.get('data', {})
        volume = data.get('volume', 80)
        self._speaker_volume = max(0, min(100, self._as_int(volume, 80)))
        logger.info(f"Speaker volume: {self._speaker_volume}")
        return self._speaker_reply(msg, self._speaker_adapter.set_volume(self._speaker_volume))

    def handle_drc_speaker_audio_play(self, msg: dict) -> dict:
        """
        预录音频播放

        Request: { file_id, play_mode(0=单次/1=循环) }
        """
        data = msg.get('data', {})
        file_id = data.get('file_id') or data.get('file_name') or data.get('filename') or ''
        play_mode = data.get('play_mode', 0)

        logger.info(f"Audio play: file={file_id}, mode={play_mode}")
        mode_result = self._speaker_adapter.set_play_mode(play_mode)
        result = self._speaker_adapter.play_audio(str(file_id))
        if result.ok:
            self._speaker_playing = True
            self._speaker_play_mode = 1 if self._as_int(play_mode, 0) else 0
            self.report_speaker_audio_play_progress(
                int(data.get('psdk_index', 0) or 0),
                "ok",
                100,
                step_key="play",
                md5=str(data.get('md5', '') or ''),
            )

        return self._speaker_reply(msg, result if result.ok else mode_result if not mode_result.ok else result)

    def handle_drc_speaker_audio_pause(self, msg: dict) -> dict:
        """暂停播放"""
        logger.info("Audio pause")
        result = self._speaker_adapter.pause_audio()
        if result.ok:
            self._speaker_playing = False
        return self._speaker_reply(msg, result)

    def handle_drc_speaker_audio_stop(self, msg: dict) -> dict:
        """停止播放"""
        logger.info("Audio stop")
        result = self._speaker_adapter.stop_audio()
        if result.ok:
            self._speaker_playing = False
        return self._speaker_reply(msg, result)

    # ---- PSDK API ----

    def handle_psdk_function_set(self, msg: dict) -> dict:
        """
        PSDK功能调用

        Request: { device_sn, function_id, params }
        """
        data = msg.get('data', {})
        device_sn = data.get('device_sn', '')
        function_id = data.get('function_id', 0)
        params = data.get('params', {})

        logger.info(f"PSDK function: device={device_sn}, func={function_id}, params={params}")

        # TODO: 通过PSDK通道发送功能调用

        return MessageBuilder.build_reply(msg, result=0)

    # ---- 红外相机API ----

    def handle_set_thermal_mode(self, msg: dict) -> dict:
        """
        红外测温模式设置

        Request: { mode: 0=点测温/1=区域测温/2=全屏测温 }
        """
        data = msg.get('data', {})
        mode = data.get('mode', 0)
        logger.info(f"Thermal mode: {mode}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_palette(self, msg: dict) -> dict:
        """
        调色板设置

        Request: { palette: white_hot/black_hot/iron_red/rainbow }
        """
        data = msg.get('data', {})
        palette = data.get('palette', 'white_hot')
        logger.info(f"Palette: {palette}")
        return MessageBuilder.build_reply(msg, result=0)

    # ---- 大疆标准第三方负载接口扩展 ----

    def handle_payload_authority_grab(self, msg: dict) -> dict:
        """负载控制权获取"""
        data = msg.get('data', {})
        payload_index = data.get('payload_index', 0)
        logger.info(f"Payload authority grab: index={payload_index}")
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "output": {"status": "ok"},
                "authority": "payload",
                "payload_index": payload_index,
            },
        )

    def handle_payload_authority_release(self, msg: dict) -> dict:
        """负载控制权释放"""
        data = msg.get('data', {})
        payload_index = data.get('payload_index', 0)
        logger.info(f"Payload authority release: index={payload_index}")
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "output": {"status": "ok"},
                "authority": "payload",
                "payload_index": payload_index,
                "released": True,
            },
        )

    def handle_payload_status_query(self, msg: dict) -> dict:
        """负载状态查询"""
        logger.info("Payload status query")
        states = self._payload_state_provider() if self._payload_state_provider else {}
        payloads = (
            self._payload_topology.snapshot(states)
            if self._payload_topology
            else []
        )
        return MessageBuilder.build_reply(msg, result=0, data={
            'payloads': payloads,
            'flight_controller_fallback': bool(self._mavlink_conn),
        })

    def handle_parachute_open(self, msg: dict) -> dict:
        """降落伞打开"""
        logger.info("Parachute open")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_parachute_status_query(self, msg: dict) -> dict:
        """降落伞状态查询"""
        logger.info("Parachute status query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'status': 0,
            'battery_percent': 100
        })

    def handle_night_vision_mode_set(self, msg: dict) -> dict:
        """夜视模式设置"""
        data = msg.get('data', {})
        mode = data.get('mode', 0)
        logger.info(f"Night vision mode: {mode}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_gas_detector_data_get(self, msg: dict) -> dict:
        """气体检测数据获取"""
        logger.info("Gas detector data get")
        return MessageBuilder.build_reply(msg, result=0, data={
            'gas_type': '',
            'concentration': 0,
            'unit': 'ppm'
        })

    def handle_radar_status_query(self, msg: dict) -> dict:
        """雷达状态查询"""
        logger.info("Radar status query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'enabled': False,
            'obstacle_distance': 0
        })

    def handle_searchlight_status_query(self, msg: dict) -> dict:
        """探照灯状态查询"""
        logger.info("Searchlight status query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'on': self._light_on,
            'brightness': self._light_brightness,
            'mode': self._light_mode
        })

    def handle_speaker_status_query(self, msg: dict) -> dict:
        """喊话器状态查询"""
        logger.info("Speaker status query")
        adapter_status = self._speaker_adapter.query_status()
        if "volume" in adapter_status:
            self._speaker_volume = adapter_status["volume"]
        if adapter_status.get("play_state"):
            self._speaker_playing = adapter_status["play_state"] != "idle"
        return MessageBuilder.build_reply(msg, result=0, data={
            'playing': self._speaker_playing,
            'volume': self._speaker_volume,
            'tts_text': self._tts_text,
            'play_mode': self._speaker_play_mode,
            'adapter': adapter_status,
        })

    def handle_payload_capability_query(self, msg: dict) -> dict:
        """负载能力查询"""
        logger.info("Payload capability query")
        configured_payloads = []
        if self._payload_topology:
            configured_payloads = [
                device_id
                for device_id, device in self._payload_topology.iter_devices()
                if device.get('enabled', True)
            ]
        return MessageBuilder.build_reply(msg, result=0, data={
            'supported_payloads': ['camera', 'speaker', 'light', 'thermal'],
            'configured_payload_links': configured_payloads,
            'connection_model': 'direct_gateway',
        })

    # ---- PSDK接口 ----

    def handle_psdk_ui_resource_upload_result(self, msg: dict) -> dict:
        """
        PSDK UI资源上传结果 (psdk_ui_resource_upload_result)

        Request: {
            psdk_index: int,
            object_key: str,
            size: int,
            result: int
        }
        """
        data = msg.get('data', {})
        psdk_index = data.get('psdk_index', 0)
        object_key = data.get('object_key', '')
        size = data.get('size', 0)
        result = data.get('result', 0)
        logger.info(f"PSDK UI resource upload result: index={psdk_index}, "
                   f"object_key={object_key}, size={size}, result={result}")
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

    def handle_psdk_floating_window_text(self, msg: dict) -> dict:
        """
        PSDK浮窗文字 (psdk_floating_window_text)

        Request: {
            psdk_index: int,
            value: str
        }
        """
        data = msg.get('data', {})
        psdk_index = data.get('psdk_index', 0)
        value = data.get('value', '')
        logger.info(f"PSDK floating window text: index={psdk_index}, value={value}")
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

    def handle_psdk_widget_value_set(self, msg: dict) -> dict:
        """
        PSDK控件值设置 (psdk_widget_value_set)

        Request: {
            psdk_index: int,
            index: int,
            value: int
        }
        """
        data = msg.get('data', {})
        psdk_index = data.get('psdk_index', 0)
        index = data.get('index', 0)
        value = data.get('value', 0)
        logger.info(f"PSDK widget value set: psdk_index={psdk_index}, index={index}, value={value}")
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

    def handle_psdk_input_box_text_set(self, msg: dict) -> dict:
        """
        PSDK输入框文字设置 (psdk_input_box_text_set)

        Request: {
            psdk_index: int,
            value: str
        }
        """
        data = msg.get('data', {})
        psdk_index = data.get('psdk_index', 0)
        value = data.get('value', '')
        logger.info(f"PSDK input box text set: index={psdk_index}, value={value}")
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

    # ---- 喊话器接口扩展 ----

    def handle_speaker_replay(self, msg: dict) -> dict:
        """
        喊话器重放 (speaker_replay)

        Request: {
            psdk_index: int
        }
        """
        data = msg.get('data', {})
        psdk_index = data.get('psdk_index', 0)
        logger.info(f"Speaker replay: index={psdk_index}")
        result = self._speaker_adapter.replay()
        if result.ok:
            self._speaker_playing = True
        return self._speaker_reply(msg, result)

    def handle_speaker_play_mode_set(self, msg: dict) -> dict:
        """
        喊话器播放模式设置 (speaker_play_mode_set)

        Request: {
            psdk_index: int,
            play_mode: int
        }
        """
        data = msg.get('data', {})
        psdk_index = data.get('psdk_index', 0)
        play_mode = data.get('play_mode', 0)
        logger.info(f"Speaker play mode set: index={psdk_index}, mode={play_mode}")
        result = self._speaker_adapter.set_play_mode(play_mode)
        if result.ok:
            self._speaker_play_mode = 1 if self._as_int(play_mode, 0) else 0
        return self._speaker_reply(msg, result)

    # ---- 喊话器接口别名 ----

    def handle_speaker_play_stop(self, msg: dict) -> dict:
        """
        喊话器停止播放 (speaker_play_stop)

        别名，调用 drc_speaker_audio_stop
        """
        return self.handle_drc_speaker_audio_stop(msg)

    def handle_speaker_audio_play_start(self, msg: dict) -> dict:
        """
        喊话器开始播放音频 (speaker_audio_play_start)

        别名，调用 drc_speaker_audio_play
        """
        return self.handle_drc_speaker_audio_play(msg)

    def handle_speaker_tts_play_start(self, msg: dict) -> dict:
        """
        喊话器开始TTS播放 (speaker_tts_play_start)

        别名，调用 drc_speaker_tts_set
        """
        return self.handle_drc_speaker_tts_set(msg)

    def handle_speaker_play_volume_set(self, msg: dict) -> dict:
        """
        喊话器音量设置 (speaker_play_volume_set)

        别名，调用 drc_speaker_play_volume_set
        """
        return self.handle_drc_speaker_play_volume_set(msg)

    def _speaker_reply(self, msg: dict, result: JzSpeakerResult) -> dict:
        return MessageBuilder.build_reply(
            msg,
            result=0 if result.ok else DOCK_SYSTEM_ERROR,
            data={'output': result.as_output()},
            msg_str="" if result.ok else result.message,
        )

    @staticmethod
    def _as_int(value, default: int) -> int:
        if isinstance(value, bool):
            return int(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
