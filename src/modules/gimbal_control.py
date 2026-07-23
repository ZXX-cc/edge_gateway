"""
ViewLink 云台控制模块
modules/gimbal_control.py

功能：
- SDK 初始化与连接管理
- 云台方向控制（移动、停止、归位、转向）
- 变焦控制（放大、缩小）
- 跟踪控制（启用/禁用目标跟踪）
- 拍摄控制（拍照、录像）
"""
import ctypes
from ctypes import c_char, c_int, c_void_p, c_char_p, CFUNCTYPE, Structure, Union, POINTER
import os
import shutil
import time
import threading
from typing import Optional, Callable
from enum import IntEnum
from src.utils.logger import get_logger


logger = get_logger("payload.gimbal_control")


# ===== 枚举定义 =====

class VLKConnType(IntEnum):
    """连接类型"""
    SERIAL_PORT = 0x00
    TCP = 0x01
    UDP = 0x02


class VLKSensor(IntEnum):
    """传感器类型"""
    VISIBLE1 = 0
    IR = 1
    VISIBLE_IR = 2
    IR_VISIBLE = 3
    VISIBLE2 = 4


class VLKImageType(IntEnum):
    """图像显示类型"""
    VISIBLE1 = 0
    VISIBLE2 = 1
    IR1 = 2
    IR2 = 3
    FUSION = 4


class VLKIrColor(IntEnum):
    """红外色板"""
    WHITEHOT = 0
    BLACKHOT = 1
    PSEUDOHOT = 2
    RUSTY = 3


class VLKTrackTemplateSize(IntEnum):
    """跟踪模板尺寸"""
    AUTO = 0
    SIZE_32 = 32
    SIZE_64 = 64
    SIZE_128 = 128


class VLKRecordMode(IntEnum):
    """录制模式"""
    NONE = 0
    PHOTO = 1
    RECORD = 2


class VLKFocusMode(IntEnum):
    """对焦模式"""
    AUTO = 0
    MANUAL = 1


class VLKLaserZoomMode(IntEnum):
    """激光变焦模式"""
    FOLLOW_EO = 0
    MANUAL = 1


class VLKConnStatus(IntEnum):
    """连接状态"""
    CONNECTED_DEPRECATED = 0
    DISCONNECTED_DEPRECATED = 1
    TCP_CONNECTED = 2
    TCP_DISCONNECTED = 3
    SERIAL_CONNECTED = 4
    SERIAL_DISCONNECTED = 5


# ===== 结构体定义 =====

class VLK_DEV_IPADDR(Structure):
    _fields_ = [
        ("szIPV4", c_char * 16),
        ("iPort", c_int),
    ]


class VLK_DEV_SERIAL_PORT(Structure):
    _fields_ = [
        ("szSerialPortName", c_char * 16),
        ("iBaudRate", c_int),
    ]


class ConnParamUnion(Union):
    _fields_ = [
        ("IPAddr", VLK_DEV_IPADDR),
        ("SerialPort", VLK_DEV_SERIAL_PORT),
    ]


class VLK_CONN_PARAM(Structure):
    _fields_ = [
        ("emType", c_int),
        ("ConnParam", ConnParamUnion),
    ]


class VLK_TRACK_MODE_PARAM(Structure):
    _fields_ = [
        ("emTrackTempSize", c_int),
        ("emTrackSensor", c_int),
    ]


# ===== 回调函数类型 =====
ConnStatusCallback = CFUNCTYPE(c_int, c_int, c_char_p, c_int, c_void_p)
DevStatusCallback = CFUNCTYPE(c_int, c_int, c_char_p, c_int, c_void_p)


class GimbalControl:
    """
    ViewLink 云台控制模块

    功能：
    - SDK 初始化与连接管理
    - 云台方向控制
    - 变焦控制
    - 跟踪控制
    - 拍摄控制
    """

    def __init__(
        self,
        sdk_dir: str = "C:/workspace/ViewLink-SDK-2025/bin/win64",
        gimbal_ip: str = "192.168.2.119",
        gimbal_port: int = 2000
    ):
        """
        初始化云台控制模块

        Args:
            sdk_dir: SDK 库文件所在目录
            gimbal_ip: 云台 IP 地址
            gimbal_port: 云台端口
        """
        self.sdk_dir = sdk_dir
        self.gimbal_ip = gimbal_ip
        self.gimbal_port = gimbal_port

        # 状态
        self._initialized = False
        self._connected = False
        self._is_recording = False
        self._current_record_mode = VLKRecordMode.NONE
        self._lib: Optional[ctypes.CDLL] = None
        self._sdk_available = False  # SDK是否可用（优雅降级标志）

        # 连接状态
        self._conn_status = None
        self._conn_message = ""

        # 回调引用（防止被GC回收）
        self._conn_callback = None
        self._dev_callback = None

        # 外部回调
        self._on_conn_status_change: Optional[Callable] = None
        self._on_dev_status_change: Optional[Callable] = None

        # 线程锁
        self._lock = threading.Lock()

    def log(self, message: str, level: str = "INFO"):
        """日志输出"""
        level_method = {
            "WARN": logger.warning,
            "WARNING": logger.warning,
            "ERROR": logger.error,
        }.get(str(level).upper(), logger.info)
        level_method("[GimbalControl] %s", message)

    # ==================== SDK 生命周期 ====================

    def initialize(self) -> dict:
        """
        初始化 SDK

        Returns:
            dict: {"success": bool, "message": str, "version": str}
        """
        result = {"success": False, "message": "", "version": ""}

        if self._initialized:
            result["message"] = "SDK 已初始化"
            result["success"] = True
            return result

        try:
            # 切换到 SDK 目录
            original_dir = os.getcwd()
            os.chdir(self.sdk_dir)
            self.log(f"切换到 SDK 目录: {self.sdk_dir}")

            # 清理 VLKLog 目录
            vlklog_path = os.path.join(self.sdk_dir, "VLKLog")
            if os.path.exists(vlklog_path):
                shutil.rmtree(vlklog_path)
                self.log("清理 VLKLog 目录")

            # 加载库
            self._lib = ctypes.CDLL("./ViewLink.dll")
            self.log("加载 ViewLink.dll 成功")

            # 定义函数签名
            self._define_function_signatures()

            # 获取版本
            version = self._lib.GetSDKVersion()
            version_str = version.decode('utf-8') if version else "unknown"
            result["version"] = version_str
            self.log(f"SDK 版本: {version_str}")

            # 初始化 SDK
            ret = self._lib.VLK_Init()
            if ret != 0:
                result["message"] = f"VLK_Init 失败，返回码: {ret}"
                self.log(result["message"], "ERROR")
                return result

            self._initialized = True
            self._sdk_available = True
            result["success"] = True
            result["message"] = "SDK 初始化成功"
            self.log(result["message"], "OK")

        except Exception as e:
            result["message"] = f"初始化异常: {str(e)}"
            self._sdk_available = False
            self.log(result["message"], "ERROR")
            self.log("SDK 不可用，将使用 MAVLink 作为降级方案", "WARN")

        return result

    def is_sdk_available(self) -> bool:
        """SDK是否可用"""
        return self._sdk_available

    def _define_function_signatures(self):
        """定义 SDK 函数签名"""
        lib = self._lib

        # 基础函数
        lib.GetSDKVersion.argtypes = []
        lib.GetSDKVersion.restype = c_char_p

        lib.VLK_Init.argtypes = []
        lib.VLK_Init.restype = c_int

        lib.VLK_UnInit.argtypes = []
        lib.VLK_UnInit.restype = None

        # 连接函数
        lib.VLK_Connect.argtypes = [POINTER(VLK_CONN_PARAM), ConnStatusCallback, c_void_p]
        lib.VLK_Connect.restype = c_int

        lib.VLK_IsConnected.argtypes = []
        lib.VLK_IsConnected.restype = c_int

        lib.VLK_Disconnect.argtypes = []
        lib.VLK_Disconnect.restype = None

        # 回调注册
        lib.VLK_RegisterDevStatusCB.argtypes = [DevStatusCallback, c_void_p]
        lib.VLK_RegisterDevStatusCB.restype = None

        # 云台控制
        lib.VLK_Move.argtypes = [c_int, c_int]
        lib.VLK_Move.restype = None

        lib.VLK_Stop.argtypes = []
        lib.VLK_Stop.restype = None

        lib.VLK_Home.argtypes = []
        lib.VLK_Home.restype = None

        lib.VLK_TurnTo.argtypes = [ctypes.c_double, ctypes.c_double]
        lib.VLK_TurnTo.restype = None

        # 变焦控制
        lib.VLK_ZoomIn.argtypes = [c_int]
        lib.VLK_ZoomIn.restype = None

        lib.VLK_ZoomOut.argtypes = [c_int]
        lib.VLK_ZoomOut.restype = None

        lib.VLK_StopZoom.argtypes = []
        lib.VLK_StopZoom.restype = None

        # 跟踪控制
        lib.VLK_TrackTargetPositionEx.argtypes = [
            POINTER(VLK_TRACK_MODE_PARAM), c_int, c_int, c_int, c_int
        ]
        lib.VLK_TrackTargetPositionEx.restype = None

        lib.VLK_DisableTrackMode.argtypes = []
        lib.VLK_DisableTrackMode.restype = None

        # 拍摄功能
        lib.VLK_Photograph.argtypes = []
        lib.VLK_Photograph.restype = None

        lib.VLK_SwitchRecord.argtypes = [c_int]
        lib.VLK_SwitchRecord.restype = None

        lib.VLK_IsRecording.argtypes = []
        lib.VLK_IsRecording.restype = c_int

        lib.VLK_SetRecordMode.argtypes = [c_int]
        lib.VLK_SetRecordMode.restype = None

        lib.VLK_GetRecordMode.argtypes = []
        lib.VLK_GetRecordMode.restype = c_int

        # 对焦和指定倍率
        lib.VLK_FocusIn.argtypes = [c_int]
        lib.VLK_FocusIn.restype = None

        lib.VLK_FocusOut.argtypes = [c_int]
        lib.VLK_FocusOut.restype = None

        lib.VLK_StopFocus.argtypes = []
        lib.VLK_StopFocus.restype = None

        lib.VLK_SetFocusMode.argtypes = [c_int]
        lib.VLK_SetFocusMode.restype = None

        lib.VLK_GetFocusMode.argtypes = []
        lib.VLK_GetFocusMode.restype = c_int

        lib.VLK_SetImageColor.argtypes = [c_int, c_int, c_int]
        lib.VLK_SetImageColor.restype = None

        lib.VLK_ZoomTo.argtypes = [ctypes.c_float]
        lib.VLK_ZoomTo.restype = None

        # 电子变焦/除雾/激光
        lib.VLK_SwitchEODigitalZoom.argtypes = [c_int]
        lib.VLK_SwitchEODigitalZoom.restype = None

        lib.VLK_IRDigitalZoomIn.argtypes = [c_int]
        lib.VLK_IRDigitalZoomIn.restype = None

        lib.VLK_IRDigitalZoomOut.argtypes = [c_int]
        lib.VLK_IRDigitalZoomOut.restype = None

        lib.VLK_SwitchDefog.argtypes = [c_int]
        lib.VLK_SwitchDefog.restype = None

        lib.VLK_IsDefogOn.argtypes = []
        lib.VLK_IsDefogOn.restype = c_int

        lib.VLK_SwitchLaser.argtypes = [c_int]
        lib.VLK_SwitchLaser.restype = None

        lib.VLK_LaserSingle.argtypes = []
        lib.VLK_LaserSingle.restype = None

        lib.VLK_LaserZoomIn.argtypes = [c_int]
        lib.VLK_LaserZoomIn.restype = None

        lib.VLK_LaserZoomOut.argtypes = [c_int]
        lib.VLK_LaserZoomOut.restype = None

        lib.VLK_LaserStopZoom.argtypes = []
        lib.VLK_LaserStopZoom.restype = None

        lib.VLK_SetLaserZoomMode.argtypes = [c_int]
        lib.VLK_SetLaserZoomMode.restype = None

        lib.VLK_IsTracking.argtypes = []
        lib.VLK_IsTracking.restype = c_int

    def uninitialize(self):
        """反初始化 SDK"""
        if self._initialized and self._lib:
            self.disconnect()
            self._lib.VLK_UnInit()
            self._initialized = False
            self.log("SDK 已反初始化", "OK")

    # ==================== 连接管理 ====================

    def _create_conn_callback(self):
        """创建连接状态回调"""
        @ConnStatusCallback
        def callback(status, message, msg_len, user_param):
            msg = message.decode('utf-8') if message else ""
            self._conn_status = status
            self._conn_message = msg

            status_map = {
                0: "已连接（废弃）",
                1: "断开连接（废弃）",
                2: "TCP 已连接",
                3: "TCP 已断开",
                4: "串口已连接",
                5: "串口已断开",
            }
            status_text = status_map.get(status, f"未知状态({status})")
            self.log(f"连接状态: {status_text} - {msg}")

            # 更新连接状态
            if status in [2, 4]:
                self._connected = True
            elif status in [3, 5]:
                self._connected = False

            # 调用外部回调
            if self._on_conn_status_change:
                self._on_conn_status_change(status, msg)

            return 0

        return callback

    def _create_dev_callback(self):
        """创建设备状态回调"""
        @DevStatusCallback
        def callback(dev_type, buffer, buf_len, user_param):
            type_map = {
                0: "设备型号",
                1: "设备配置",
                2: "设备遥测数据",
            }
            type_text = type_map.get(dev_type, f"未知类型({dev_type})")
            self.log(f"设备状态: {type_text}, 数据长度: {buf_len}")

            if self._on_dev_status_change:
                self._on_dev_status_change(dev_type, buf_len)

            return 0

        return callback

    def connect(
        self,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        conn_type: VLKConnType = VLKConnType.TCP,
        timeout: float = 5.0
    ) -> dict:
        """
        连接云台设备

        Args:
            ip: IP 地址（None 则使用初始化时的地址）
            port: 端口（None 则使用初始化时的端口）
            conn_type: 连接类型
            timeout: 连接超时时间（秒）

        Returns:
            dict: {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        if not self._initialized:
            result["message"] = "SDK 未初始化，请先调用 initialize()"
            return result

        if self._connected:
            result["success"] = True
            result["message"] = "已连接到设备"
            return result

        ip = ip or self.gimbal_ip
        port = port or self.gimbal_port

        try:
            # 创建回调（保持引用）
            self._conn_callback = self._create_conn_callback()
            self._dev_callback = self._create_dev_callback()

            # 注册设备状态回调
            self._lib.VLK_RegisterDevStatusCB(self._dev_callback, None)

            # 准备连接参数
            param = VLK_CONN_PARAM()
            ctypes.memset(ctypes.byref(param), 0, ctypes.sizeof(param))
            param.emType = int(conn_type)
            param.ConnParam.IPAddr.szIPV4 = ip.encode('utf-8')
            param.ConnParam.IPAddr.iPort = port

            self.log(f"连接到 {ip}:{port} (类型: {conn_type.name})")

            # 发起连接
            ret = self._lib.VLK_Connect(
                ctypes.byref(param),
                self._conn_callback,
                None
            )

            if ret != 0:
                result["message"] = f"VLK_Connect 失败，返回码: {ret}"
                self.log(result["message"], "ERROR")
                return result

            # 等待连接建立
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self._lib.VLK_IsConnected():
                    self._connected = True
                    result["success"] = True
                    result["message"] = f"成功连接到 {ip}:{port}"
                    self.log(result["message"], "OK")
                    return result
                time.sleep(0.1)

            result["message"] = f"连接超时（{timeout}秒）"
            self.log(result["message"], "ERROR")

        except Exception as e:
            result["message"] = f"连接异常: {str(e)}"
            self.log(result["message"], "ERROR")

        return result

    def disconnect(self) -> dict:
        """断开连接"""
        result = {"success": True, "message": ""}

        if self._lib and self._connected:
            self._lib.VLK_Disconnect()
            self._connected = False
            result["message"] = "已断开连接"
            self.log(result["message"], "OK")
        else:
            result["message"] = "未连接或 SDK 未初始化"

        return result

    def is_connected(self) -> bool:
        """检查是否已连接"""
        if self._lib:
            return self._lib.VLK_IsConnected() == 1
        return False

    # ==================== 云台控制 ====================

    def move(self, yaw_speed: int, pitch_speed: int) -> dict:
        """
        云台移动

        Args:
            yaw_speed: 水平旋转速度 (-16383 ~ 16383)，正值向右
            pitch_speed: 俯仰速度 (-16383 ~ 16383)，正值向上

        Returns:
            dict: {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        # 限制速度范围
        yaw_speed = max(-16383, min(16383, yaw_speed))
        pitch_speed = max(-16383, min(16383, pitch_speed))

        self._lib.VLK_Move(yaw_speed, pitch_speed)
        result["success"] = True
        result["message"] = f"云台移动: yaw={yaw_speed}, pitch={pitch_speed}"
        self.log(result["message"])

        return result

    def stop(self) -> dict:
        """停止云台移动"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_Stop()
        result["success"] = True
        result["message"] = "云台已停止"
        self.log(result["message"])

        return result

    def home(self) -> dict:
        """云台归位（回到初始位置）"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_Home()
        result["success"] = True
        result["message"] = "云台正在归位"
        self.log(result["message"])

        return result

    def turn_to(self, yaw: float, pitch: float) -> dict:
        """
        云台转向指定角度

        Args:
            yaw: 水平角度（度）
            pitch: 俯仰角度（度）

        Returns:
            dict: {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_TurnTo(ctypes.c_double(yaw), ctypes.c_double(pitch))
        result["success"] = True
        result["message"] = f"云台转向: yaw={yaw}°, pitch={pitch}°"
        self.log(result["message"])

        return result

    # ==================== 变焦控制 ====================

    def zoom_in(self, speed: int = 5) -> dict:
        """
        放大（Zoom In）

        Args:
            speed: 变焦速度 (1-8)

        Returns:
            dict: {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        speed = max(1, min(8, speed))
        self._lib.VLK_ZoomIn(speed)
        result["success"] = True
        result["message"] = f"放大中，速度: {speed}"
        self.log(result["message"])

        return result

    def zoom_out(self, speed: int = 5) -> dict:
        """
        缩小（Zoom Out）

        Args:
            speed: 变焦速度 (1-8)

        Returns:
            dict: {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        speed = max(1, min(8, speed))
        self._lib.VLK_ZoomOut(speed)
        result["success"] = True
        result["message"] = f"缩小中，速度: {speed}"
        self.log(result["message"])

        return result

    def stop_zoom(self) -> dict:
        """停止变焦"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_StopZoom()
        result["success"] = True
        result["message"] = "变焦已停止"
        self.log(result["message"])

        return result

    def zoom_to(self, magnification: float) -> dict:
        """变焦到指定倍率"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        magnification = max(1.0, float(magnification))
        self._lib.VLK_ZoomTo(ctypes.c_float(magnification))
        result["success"] = True
        result["message"] = f"变焦到指定倍率: {magnification}x"
        self.log(result["message"])

        return result

    # ==================== 对焦控制 ====================

    def focus_in(self, speed: int = 5) -> dict:
        """近焦方向连续对焦"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        speed = max(1, min(8, int(speed)))
        self._lib.VLK_FocusIn(speed)
        result["success"] = True
        result["message"] = f"近焦中，速度: {speed}"
        self.log(result["message"])
        return result

    def focus_out(self, speed: int = 5) -> dict:
        """远焦方向连续对焦"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        speed = max(1, min(8, int(speed)))
        self._lib.VLK_FocusOut(speed)
        result["success"] = True
        result["message"] = f"远焦中，速度: {speed}"
        self.log(result["message"])
        return result

    def stop_focus(self) -> dict:
        """停止对焦"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_StopFocus()
        result["success"] = True
        result["message"] = "对焦已停止"
        self.log(result["message"])
        return result

    def set_focus_mode(self, mode: VLKFocusMode) -> dict:
        """设置对焦模式"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_SetFocusMode(int(mode))
        mode_names = {
            VLKFocusMode.AUTO: "自动对焦",
            VLKFocusMode.MANUAL: "手动对焦",
        }
        result["success"] = True
        result["message"] = f"设置对焦模式: {mode_names.get(mode, '未知')}"
        self.log(result["message"])
        return result

    def get_focus_mode(self) -> dict:
        """获取当前对焦模式"""
        if not self._connected:
            return {"success": False, "mode": -1, "mode_name": "未连接"}

        mode = self._lib.VLK_GetFocusMode()
        mode_names = {
            0: "自动对焦",
            1: "手动对焦",
        }
        return {
            "success": True,
            "mode": mode,
            "mode_name": mode_names.get(mode, f"未知({mode})"),
        }

    # ==================== 电子变焦/除雾/激光 ====================

    def switch_eo_digital_zoom(self, on: bool) -> dict:
        """开启或关闭可见光电子变焦"""
        return self._switch_int("VLK_SwitchEODigitalZoom", bool(on), "EO电子变焦")

    def ir_digital_zoom_in(self, speed: int = 0) -> dict:
        """红外电子变焦放大一级"""
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        self._lib.VLK_IRDigitalZoomIn(int(speed))
        result["success"] = True
        result["message"] = "红外电子变焦放大"
        self.log(result["message"])
        return result

    def ir_digital_zoom_out(self, speed: int = 0) -> dict:
        """红外电子变焦缩小一级"""
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        self._lib.VLK_IRDigitalZoomOut(int(speed))
        result["success"] = True
        result["message"] = "红外电子变焦缩小"
        self.log(result["message"])
        return result

    def switch_defog(self, on: bool) -> dict:
        """开启或关闭除雾"""
        return self._switch_int("VLK_SwitchDefog", bool(on), "除雾")

    def is_defog_on(self) -> dict:
        """查询除雾状态"""
        if not self._connected:
            return {"success": False, "on": False, "message": "未连接到设备"}
        on = self._lib.VLK_IsDefogOn() == 1
        return {"success": True, "on": on}

    def switch_laser(self, on: bool) -> dict:
        """开启或关闭激光。使用前应确认现场安全。"""
        return self._switch_int("VLK_SwitchLaser", bool(on), "激光")

    def laser_single(self) -> dict:
        """激光单次测距"""
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        self._lib.VLK_LaserSingle()
        result["success"] = True
        result["message"] = "激光单次测距命令已发送"
        self.log(result["message"])
        return result

    def laser_zoom_in(self, speed: int = 0) -> dict:
        """激光变焦放大"""
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        self._lib.VLK_LaserZoomIn(int(speed))
        result["success"] = True
        result["message"] = "激光变焦放大"
        self.log(result["message"])
        return result

    def laser_zoom_out(self, speed: int = 0) -> dict:
        """激光变焦缩小"""
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        self._lib.VLK_LaserZoomOut(int(speed))
        result["success"] = True
        result["message"] = "激光变焦缩小"
        self.log(result["message"])
        return result

    def laser_stop_zoom(self) -> dict:
        """停止激光变焦"""
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        self._lib.VLK_LaserStopZoom()
        result["success"] = True
        result["message"] = "激光变焦已停止"
        self.log(result["message"])
        return result

    def set_laser_zoom_mode(self, mode: VLKLaserZoomMode) -> dict:
        """设置激光变焦模式"""
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        self._lib.VLK_SetLaserZoomMode(int(mode))
        result["success"] = True
        result["message"] = f"设置激光变焦模式: {int(mode)}"
        self.log(result["message"])
        return result

    def is_tracking(self) -> dict:
        """查询跟踪状态"""
        if not self._connected:
            return {"success": False, "tracking": False, "message": "未连接到设备"}
        tracking = self._lib.VLK_IsTracking() == 1
        return {"success": True, "tracking": tracking}

    def set_image_color(
        self,
        image_type: VLKImageType = VLKImageType.IR1,
        enable_pip: bool = False,
        ir_color: VLKIrColor = VLKIrColor.WHITEHOT,
    ) -> dict:
        """设置相机主画面/画中画和红外色板。"""
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        self._lib.VLK_SetImageColor(int(image_type), 1 if enable_pip else 0, int(ir_color))
        result["success"] = True
        result["message"] = (
            f"设置图像显示: image_type={int(image_type)}, "
            f"pip={1 if enable_pip else 0}, ir_color={int(ir_color)}"
        )
        self.log(result["message"])
        return result

    def _switch_int(self, sdk_method: str, on: bool, label: str) -> dict:
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        getattr(self._lib, sdk_method)(1 if on else 0)
        result["success"] = True
        result["message"] = f"{label}{'开启' if on else '关闭'}"
        self.log(result["message"])
        return result

    # ==================== 跟踪控制 ====================

    def start_track(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        template_size: VLKTrackTemplateSize = VLKTrackTemplateSize.AUTO,
        sensor: VLKSensor = VLKSensor.VISIBLE1
    ) -> dict:
        """
        开始跟踪目标

        Args:
            x: 目标框左上角 X 坐标
            y: 目标框左上角 Y 坐标
            width: 目标框宽度
            height: 目标框高度
            template_size: 跟踪模板尺寸
            sensor: 使用的传感器

        Returns:
            dict: {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        param = VLK_TRACK_MODE_PARAM()
        param.emTrackTempSize = int(template_size)
        param.emTrackSensor = int(sensor)

        self._lib.VLK_TrackTargetPositionEx(
            ctypes.byref(param), x, y, width, height
        )

        result["success"] = True
        result["message"] = f"开始跟踪: 位置({x},{y}), 尺寸({width}x{height})"
        self.log(result["message"])

        return result

    def stop_track(self) -> dict:
        """停止跟踪"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_DisableTrackMode()
        result["success"] = True
        result["message"] = "跟踪已停止"
        self.log(result["message"])

        return result

    # ==================== 拍摄控制 ====================

    def take_photo(self) -> dict:
        """拍照"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_Photograph()
        result["success"] = True
        result["message"] = "拍照命令已发送"
        self.log(result["message"])

        return result

    def start_recording(self) -> dict:
        """开始录像"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_SwitchRecord(1)
        self._is_recording = True
        result["success"] = True
        result["message"] = "开始录像"
        self.log(result["message"])

        return result

    def stop_recording(self) -> dict:
        """停止录像"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._lib.VLK_SwitchRecord(0)
        self._is_recording = False
        result["success"] = True
        result["message"] = "停止录像"
        self.log(result["message"])

        return result

    def is_recording(self) -> bool:
        """检查是否正在录像"""
        if self._lib and self._connected:
            return self._lib.VLK_IsRecording() == 1
        return False

    def set_record_mode(self, mode: VLKRecordMode) -> dict:
        """
        设置录制模式

        Args:
            mode: VLKRecordMode 枚举值

        Returns:
            dict: {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        mode_names = {
            VLKRecordMode.NONE: "无模式",
            VLKRecordMode.PHOTO: "拍照模式",
            VLKRecordMode.RECORD: "录像模式"
        }

        self._lib.VLK_SetRecordMode(int(mode))
        self._current_record_mode = mode
        result["success"] = True
        result["message"] = f"设置模式: {mode_names.get(mode, '未知')}"
        self.log(result["message"])

        return result

    def get_record_mode(self) -> dict:
        """
        获取当前录制模式

        Returns:
            dict: {"mode": int, "mode_name": str}
        """
        if not self._connected:
            return {"mode": -1, "mode_name": "未连接"}

        mode = self._lib.VLK_GetRecordMode()
        mode_names = {
            0: "无模式",
            1: "拍照模式",
            2: "录像模式"
        }

        return {
            "mode": mode,
            "mode_name": mode_names.get(mode, f"未知({mode})")
        }

    # ==================== 状态查询 ====================

    def get_status(self) -> dict:
        """获取云台控制模块状态"""
        return {
            "initialized": self._initialized,
            "connected": self._connected,
            "is_recording": self.is_recording() if self._connected else False,
            "record_mode": self.get_record_mode() if self._connected else None,
            "focus_mode": self.get_focus_mode() if self._connected else None,
            "defog_on": self.is_defog_on().get("on", False) if self._connected else False,
            "tracking": self.is_tracking().get("tracking", False) if self._connected else False,
            "gimbal_ip": self.gimbal_ip,
            "gimbal_port": self.gimbal_port,
            "sdk_dir": self.sdk_dir
        }

    # ==================== 回调设置 ====================

    def set_connection_callback(self, callback: Callable):
        """设置连接状态变化回调"""
        self._on_conn_status_change = callback

    def set_device_callback(self, callback: Callable):
        """设置设备状态变化回调"""
        self._on_dev_status_change = callback
