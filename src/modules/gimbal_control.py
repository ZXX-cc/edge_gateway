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
import platform
from ctypes import c_char, c_int, c_short, c_void_p, c_char_p, CFUNCTYPE, Structure, Union, POINTER
import os
import shutil
import socket
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Callable
from enum import IntEnum
from src.utils.logger import get_logger
from web.mavlink.viewlink import (
    build_eo_dzoom_stop_frame,
    build_ir_dzoom_stop_frame,
    build_protocol_frame_for_method,
)


logger = get_logger("payload.gimbal_control")
_SDK_CWD_LOCK = threading.RLock()


@contextmanager
def _sdk_working_directory(sdk_dir: str):
    """Run ViewLink SDK calls that depend on cwd from the SDK directory."""
    with _SDK_CWD_LOCK:
        original_dir = os.getcwd()
        try:
            os.chdir(sdk_dir)
            yield
        finally:
            try:
                os.chdir(original_dir)
            except Exception as exc:
                logger.warning("[GimbalControl] 恢复工作目录失败: %s", exc)


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
# ViewLink.h uses VLK_CALL (__stdcall on Windows) for exported SDK functions,
# but the callback typedefs themselves are plain function pointers:
#   typedef int (*VLK_ConnStatus_CB)(...);
#   typedef int (*VLK_DevStatus_CB)(...);
CallbackFactory = CFUNCTYPE
ConnStatusCallback = CallbackFactory(c_int, c_int, c_char_p, c_int, c_void_p)
# Device status szBuffer points at a binary status struct, not a NUL-terminated string.
DevStatusCallback = CallbackFactory(c_int, c_int, c_void_p, c_int, c_void_p)


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
        sdk_dir: str,
        gimbal_ip: str,
        gimbal_port: int,
        register_dev_status_callback: bool = False
    ):
        """
        初始化云台控制模块

        Args:
            sdk_dir: SDK 库文件所在目录
            gimbal_ip: 云台 IP 地址
            gimbal_port: 云台端口
            register_dev_status_callback: 是否注册设备状态回调，默认关闭以避开部分 ViewLink SDK 崩溃点
        """
        if not sdk_dir:
            raise ValueError("ViewLink sdk_dir must be configured in payload.devices.gimbal.channels.control.sdk_dir")
        if not gimbal_ip:
            raise ValueError("ViewLink host must be configured in payload.devices.gimbal.channels.control.host")
        if not gimbal_port:
            raise ValueError("ViewLink port must be configured in payload.devices.gimbal.channels.control.port")

        self.sdk_dir = self._resolve_sdk_dir(sdk_dir)
        self._sdk_runtime_dir = self.sdk_dir
        self.gimbal_ip = gimbal_ip
        self.gimbal_port = int(gimbal_port)
        self.register_dev_status_callback = self._bool_config(register_dev_status_callback)

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
        self._last_connection_check_time = 0.0
        self._connection_check_interval = 5.0
        self._last_ensure_connect_time = 0.0
        self._last_ensure_connect_result = {"success": False, "message": "尚未尝试连接"}
        self._connect_thread: Optional[threading.Thread] = None
        self._connect_in_progress = False
        self._dev_status_thread: Optional[threading.Thread] = None
        self._dev_status_registration_in_progress = False
        self._dev_status_callback_registered = False
        self._dev_status_registration_result = {
            "success": False,
            "message": "设备状态回调尚未注册",
        }
        self._last_dev_status = None
        self._logged_dev_status_types: set[int] = set()
        self._dev_status_callback_disabled_logged = False
        self._connect_deadline = 0.0
        self._connect_timeout_seconds = 0.0

        # 回调引用（防止被GC回收）
        self._conn_callback = None
        self._dev_callback = None

        # 外部回调
        self._on_conn_status_change: Optional[Callable] = None
        self._on_dev_status_change: Optional[Callable] = None

        # 线程锁
        self._lock = threading.Lock()

    @staticmethod
    def _bool_config(value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}

    @staticmethod
    def _resolve_sdk_dir(sdk_dir: str) -> str:
        """解析项目相对 SDK 目录，使 sdk_dir='./' 指向项目根目录。"""
        raw = str(sdk_dir or "").strip()
        if not raw:
            return raw
        expanded = os.path.expandvars(os.path.expanduser(raw))
        # 在 POSIX 测试环境中也保留 Windows 绝对路径。
        if len(expanded) >= 2 and expanded[1] == ":":
            return expanded
        path = Path(expanded)
        if path.is_absolute():
            return str(path)
        project_root = Path(__file__).resolve().parents[2]
        return str((project_root / path).resolve())

    @staticmethod
    def _platform_library_names() -> list[str]:
        """Return ViewLink library filenames for the current OS."""
        if os.name == "nt":
            return ["ViewLink.dll"]
        system = platform.system().lower()
        if system == "darwin":
            return ["libViewLink.dylib", "libViewLink.so", "ViewLink.dll"]
        return ["libViewLink.so", "ViewLink.dll"]

    @classmethod
    def _resolve_library_path(cls, sdk_dir: str) -> str:
        """Find a ViewLink dynamic library without changing public config keys."""
        root = Path(sdk_dir)
        candidates: list[Path] = []
        for name in cls._platform_library_names():
            candidates.append(root / name)

        if platform.system().lower() == "linux":
            machine = platform.machine().lower()
            if machine in {"x86_64", "amd64"}:
                arch_dirs = ["linux-x86_64"]
            elif machine in {"aarch64", "arm64"}:
                arch_dirs = ["linux-aarch64", "linux-arm64"]
            else:
                arch_dirs = []
            project_root = Path(__file__).resolve().parents[2]
            for arch_dir in arch_dirs:
                for name in cls._platform_library_names():
                    candidates.append(root / "bin" / arch_dir / name)
                    candidates.append(project_root / "ViewLink" / "bin" / arch_dir / name)

        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        names = ", ".join(cls._platform_library_names())
        raise FileNotFoundError(f"ViewLink library not found under {sdk_dir}; expected one of: {names}")

    def _call_sdk(self, function_name: str, *args):
        """从 SDK 目录调用依赖 cwd 的 ViewLink SDK 函数。"""
        if not self._lib:
            raise RuntimeError("SDK 未加载")
        with _sdk_working_directory(self._sdk_runtime_dir):
            return getattr(self._lib, function_name)(*args)

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
            library_path = self._resolve_library_path(self.sdk_dir)
            self._sdk_runtime_dir = str(Path(library_path).resolve().parent)

            # 切换到 SDK 运行目录
            with _sdk_working_directory(self._sdk_runtime_dir):
                self.log(f"切换到 SDK 运行目录: {self._sdk_runtime_dir}")

                # 清理 VLKLog 目录
                vlklog_path = os.path.join(self._sdk_runtime_dir, "VLKLog")
                if os.path.exists(vlklog_path):
                    shutil.rmtree(vlklog_path)
                    self.log("清理 VLKLog 目录")

                # 加载库
                self._lib = ctypes.WinDLL(library_path) if os.name == "nt" else ctypes.CDLL(library_path)
                self.log(f"加载 ViewLink SDK 库成功: {library_path}")

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
        lib.VLK_Move.argtypes = [c_short, c_short]
        lib.VLK_Move.restype = None

        lib.VLK_Stop.argtypes = []
        lib.VLK_Stop.restype = None

        lib.VLK_Home.argtypes = []
        lib.VLK_Home.restype = None

        lib.VLK_TurnTo.argtypes = [ctypes.c_double, ctypes.c_double]
        lib.VLK_TurnTo.restype = None

        # 变焦控制
        lib.VLK_ZoomIn.argtypes = [c_short]
        lib.VLK_ZoomIn.restype = None

        lib.VLK_ZoomOut.argtypes = [c_short]
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
        lib.VLK_FocusIn.argtypes = [c_short]
        lib.VLK_FocusIn.restype = None

        lib.VLK_FocusOut.argtypes = [c_short]
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

        lib.VLK_IRDigitalZoomIn.argtypes = [c_short]
        lib.VLK_IRDigitalZoomIn.restype = None

        lib.VLK_IRDigitalZoomOut.argtypes = [c_short]
        lib.VLK_IRDigitalZoomOut.restype = None

        lib.VLK_SwitchDefog.argtypes = [c_int]
        lib.VLK_SwitchDefog.restype = None

        lib.VLK_IsDefogOn.argtypes = []
        lib.VLK_IsDefogOn.restype = c_int

        lib.VLK_SwitchLaser.argtypes = [c_int]
        lib.VLK_SwitchLaser.restype = None

        lib.VLK_LaserSingle.argtypes = []
        lib.VLK_LaserSingle.restype = None

        lib.VLK_LaserZoomIn.argtypes = [c_short]
        lib.VLK_LaserZoomIn.restype = None

        lib.VLK_LaserZoomOut.argtypes = [c_short]
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
            self._call_sdk("VLK_UnInit")
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
                self._last_connection_check_time = time.time()
            elif status in [3, 5]:
                self._connected = False
                self._last_connection_check_time = time.time()

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
                3: "AI状态",
                5: "跟踪坐标",
            }
            type_text = type_map.get(dev_type, f"未知类型({dev_type})")
            self._last_dev_status = {
                "type": dev_type,
                "type_name": type_text,
                "buffer_address": int(buffer or 0),
                "buffer_length": int(buf_len or 0),
                "timestamp": time.time(),
            }
            if dev_type not in self._logged_dev_status_types:
                self._logged_dev_status_types.add(dev_type)
                self.log(f"设备状态: {type_text}, 数据长度: {buf_len}")

            if self._on_dev_status_change:
                try:
                    self._on_dev_status_change(dev_type, buf_len, dict(self._last_dev_status))
                except TypeError:
                    self._on_dev_status_change(dev_type, buf_len)

            return 0

        return callback

    def _register_dev_status_callback_once(self) -> dict:
        """注册设备状态回调。该调用可能进入原生 SDK，必须由后台线程调用。"""
        if not self._lib:
            return {"success": False, "message": "SDK 未加载"}
        if self._dev_status_callback_registered:
            return {"success": True, "message": "设备状态回调已注册"}

        self._dev_callback = self._create_dev_callback()
        self.log("开始注册 ViewLink 设备状态回调")
        self._call_sdk("VLK_RegisterDevStatusCB", self._dev_callback, None)
        self._dev_status_callback_registered = True
        return {"success": True, "message": "设备状态回调已注册"}

    def _register_dev_status_callback_background(self):
        result = None
        try:
            result = self._register_dev_status_callback_once()
            if result.get("success"):
                self.log(result.get("message", "设备状态回调已注册"), "OK")
            else:
                self.log(result.get("message", "设备状态回调注册失败"), "WARN")
        except Exception as exc:
            result = {"success": False, "message": f"设备状态回调注册异常: {exc}"}
            self.log(result["message"], "ERROR")
        finally:
            with self._lock:
                self._dev_status_registration_result = result or {
                    "success": False,
                    "message": "设备状态回调注册未返回结果",
                }
                self._dev_status_registration_in_progress = False

    def _ensure_dev_status_callback_registration(self):
        """连接成功后异步注册设备状态回调，避免阻塞网关主链路。"""
        if not self.register_dev_status_callback:
            self._dev_status_registration_result = {
                "success": False,
                "message": "设备状态回调未启用",
                "enabled": False,
            }
            if not self._dev_status_callback_disabled_logged:
                self._dev_status_callback_disabled_logged = True
                self.log("跳过 ViewLink 设备状态回调注册")
            return

        with self._lock:
            if self._dev_status_callback_registered or self._dev_status_registration_in_progress:
                return
            self._dev_status_registration_in_progress = True
            self._dev_status_registration_result = {
                "success": False,
                "message": "设备状态回调正在后台注册",
                "registering": True,
            }
            self._dev_status_thread = threading.Thread(
                target=self._register_dev_status_callback_background,
                name="viewlink-dev-status-cb",
                daemon=True,
            )
            self._dev_status_thread.start()
        self.log("ViewLink 设备状态回调后台注册已启动")

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

        if self._connected and self.is_connected():
            self._ensure_dev_status_callback_registration()
            result["success"] = True
            result["message"] = "已连接到设备"
            return result

        ip = ip or self.gimbal_ip
        port = port or self.gimbal_port

        try:
            # 创建连接回调（保持引用）
            self._conn_callback = self._create_conn_callback()

            # 准备连接参数
            param = VLK_CONN_PARAM()
            ctypes.memset(ctypes.byref(param), 0, ctypes.sizeof(param))
            param.emType = int(conn_type)
            param.ConnParam.IPAddr.szIPV4 = ip.encode('utf-8')
            param.ConnParam.IPAddr.iPort = port

            self.log(f"连接到 {ip}:{port} (类型: {conn_type.name})")

            # 发起连接
            ret = self._call_sdk(
                "VLK_Connect",
                ctypes.byref(param),
                self._conn_callback,
                None,
            )

            if ret != 0:
                result["message"] = f"VLK_Connect 失败，返回码: {ret}"
                self.log(result["message"], "ERROR")
                return result

            # 等待连接建立
            start_time = time.time()
            while time.time() - start_time < timeout:
                connected = self._connected or self.is_connected(force=True)
                if connected:
                    self._connected = True
                    self._last_connection_check_time = time.time()
                    result["success"] = True
                    result["message"] = f"成功连接到 {ip}:{port}"
                    self.log(result["message"], "OK")
                    self._ensure_dev_status_callback_registration()
                    return result
                time.sleep(1.0)

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
            self._call_sdk("VLK_Disconnect")
            self._connected = False
            result["message"] = "已断开连接"
            self.log(result["message"], "OK")
        else:
            result["message"] = "未连接或 SDK 未初始化"

        return result

    def is_connected(self, *, force: bool = False) -> bool:
        """检查是否已连接"""
        if not self._lib:
            self._connected = False
            self._last_connection_check_time = time.time()
            return False

        now = time.time()
        if (
            not force
            and self._last_connection_check_time
            and now - self._last_connection_check_time < self._connection_check_interval
        ):
            return self._connected

        try:
            connected = self._call_sdk("VLK_IsConnected") != 0
            if self._connected and not connected:
                self.log("SDK 连接状态已刷新为断开", "WARN")
            self._connected = connected
            self._last_connection_check_time = now
            return connected
        except Exception as exc:
            self.log(f"查询连接状态异常: {exc}", "WARN")
            self._connected = False
            self._last_connection_check_time = now
        return False

    def _require_connected(self) -> dict:
        """统一刷新 SDK 连接态，避免回调和主动查询状态短暂不同步。"""
        if self.is_connected():
            return {"success": True, "message": ""}
        return {"success": False, "message": "未连接到设备"}

    def _confirm_command_sent(self, success_message: str) -> dict:
        """SDK 控制函数多为 void；发送后刷新连接，避免掉线时误报成功。"""
        if not self.is_connected(force=True):
            message = f"{success_message}，但 ViewLink 已断开，命令可能未被吊舱执行"
            self.log(message, "WARN")
            return {"success": False, "message": message, "connection_lost": True}
        self.log(success_message)
        return {"success": True, "message": success_message}

    def _connecting_result(self) -> dict:
        return {
            "success": False,
            "message": "ViewLink 正在后台连接，请稍后重试",
            "stage": "connecting",
            "connecting": True,
        }

    def _connect_timeout_result(self) -> dict:
        timeout = self._connect_timeout_seconds or 0.0
        return {
            "success": False,
            "message": f"ViewLink 连接线程超过 {timeout:.1f} 秒未返回，可能卡在 SDK VLK_Connect；请重启网关进程后重试",
            "stage": "connect_timeout",
            "connecting": False,
            "stale_thread": True,
            "timeout_seconds": timeout,
        }

    def _refresh_connect_watchdog(self) -> Optional[dict]:
        """Turn an over-time background SDK call into a visible failed state."""
        with self._lock:
            if (
                self._connect_in_progress
                and self._connect_deadline
                and time.time() > self._connect_deadline
            ):
                result = self._connect_timeout_result()
                self._connect_in_progress = False
                self._last_ensure_connect_result = result
                self._last_ensure_connect_time = time.time()
                self.log(result["message"], "ERROR")
                return dict(result)
        return None

    def _stale_connect_thread_alive(self) -> bool:
        thread = self._connect_thread
        return bool(
            thread
            and thread.is_alive()
            and isinstance(self._last_ensure_connect_result, dict)
            and self._last_ensure_connect_result.get("stage") == "connect_timeout"
        )

    def connect_in_progress(self) -> bool:
        """返回当前是否已有后台连接正在执行。"""
        self._refresh_connect_watchdog()
        return bool(self._connect_in_progress)

    def _connect_once(self, timeout: float) -> dict:
        """执行一次 SDK 初始化和连接，调用方负责设置连接中状态。"""
        if self.is_connected():
            return {"success": True, "message": "已连接到设备", "connected": True}

        self.log("按需连接 ViewLink 云台")
        if not self._initialized:
            init_result = self.initialize()
            if not init_result.get("success"):
                return {
                    "success": False,
                    "message": init_result.get("message", "SDK 初始化失败"),
                    "stage": "initialize",
                }

        connect_result = self.connect(timeout=timeout)
        return {
            "success": bool(connect_result.get("success", False)),
            "message": connect_result.get("message", ""),
            "stage": "connect",
        }

    def ensure_connected(self, timeout: float = 2.0, retry_interval: float = 2.0) -> dict:
        """按需初始化并连接云台，用于按钮控制前的懒连接兜底。"""
        stale_result = self._refresh_connect_watchdog()
        if stale_result:
            return stale_result
        if self._stale_connect_thread_alive():
            return dict(self._last_ensure_connect_result)
        if self.connect_in_progress():
            return self._connecting_result()
        if self.is_connected():
            return {"success": True, "message": "已连接到设备", "connected": True}

        now = time.time()
        if (
            retry_interval > 0
            and self._last_ensure_connect_time
            and now - self._last_ensure_connect_time < retry_interval
            and not self._last_ensure_connect_result.get("success", False)
        ):
            return dict(self._last_ensure_connect_result)

        with self._lock:
            if self._connect_in_progress:
                return self._connecting_result()

            self._connect_in_progress = True
            self._connect_timeout_seconds = float(timeout or 0.0)
            self._connect_deadline = time.time() + self._connect_timeout_seconds + 1.0

        try:
            result = self._connect_once(timeout)
        except Exception as exc:
            result = {
                "success": False,
                "message": f"连接异常: {exc}",
                "stage": "connect",
            }
            self.log(result["message"], "ERROR")
        finally:
            with self._lock:
                self._connect_in_progress = False
                self._connect_deadline = 0.0
                self._last_ensure_connect_result = result
                self._last_ensure_connect_time = time.time()
        return dict(result)

    def ensure_connected_async(self, timeout: float = 2.0, retry_interval: float = 2.0) -> dict:
        """后台初始化并连接云台，避免 MQTT 回调线程被 ViewLink SDK 阻塞。"""
        stale_result = self._refresh_connect_watchdog()
        if stale_result:
            return stale_result
        if self._stale_connect_thread_alive():
            return dict(self._last_ensure_connect_result)
        if self.connect_in_progress():
            return self._connecting_result()
        if self.is_connected():
            result = {"success": True, "message": "已连接到设备", "connected": True}
            self._last_ensure_connect_result = result
            self._last_ensure_connect_time = time.time()
            return dict(result)

        now = time.time()
        with self._lock:
            if self._connect_in_progress:
                return self._connecting_result()

            if (
                retry_interval > 0
                and self._last_ensure_connect_time
                and now - self._last_ensure_connect_time < retry_interval
                and not self._last_ensure_connect_result.get("success", False)
                and not self._last_ensure_connect_result.get("connecting", False)
            ):
                return dict(self._last_ensure_connect_result)

            self._connect_in_progress = True
            self._connect_timeout_seconds = float(timeout or 0.0)
            self._connect_deadline = time.time() + self._connect_timeout_seconds + 1.0
            connecting = self._connecting_result()
            self._last_ensure_connect_result = connecting
            self._last_ensure_connect_time = now
            self._connect_thread = threading.Thread(
                target=self._ensure_connected_background,
                args=(timeout,),
                name="viewlink-connect",
                daemon=True,
            )
            self._connect_thread.start()
            self.log("ViewLink 后台连接已启动")
            return dict(connecting)

    def _ensure_connected_background(self, timeout: float):
        result = None
        try:
            result = self._connect_once(timeout)
        except Exception as exc:
            result = {
                "success": False,
                "message": f"后台连接异常: {exc}",
                "stage": "connect",
            }
            self.log(result["message"], "ERROR")
        finally:
            with self._lock:
                if result is not None:
                    self._last_ensure_connect_result = result
                    self._last_ensure_connect_time = time.time()
                self._connect_in_progress = False
                self._connect_deadline = 0.0

    def send_protocol_frame(self, frame: bytes, timeout: float = 1.0) -> dict:
        """通过 TCP 直接发送 ViewLink V3.4.9 原始协议帧。

        SDK 没有覆盖的控制项（例如测温触发、E2 坐标帧）可以走这个
        旁路。它不依赖 ViewLink.dll 初始化，只依赖云台 TCP 地址。
        """
        result = {"success": False, "message": "", "bytes_sent": 0, "frame_hex": ""}
        if not isinstance(frame, (bytes, bytearray)) or not frame:
            result["message"] = "协议帧为空"
            return result

        payload = bytes(frame)
        result["frame_hex"] = payload.hex(" ").upper()
        try:
            with socket.create_connection((self.gimbal_ip, int(self.gimbal_port)), timeout=timeout) as sock:
                sock.sendall(payload)
            result["success"] = True
            result["bytes_sent"] = len(payload)
            result["message"] = f"协议帧已发送: {len(payload)} bytes"
            self.log(result["message"])
        except Exception as exc:
            result["message"] = f"协议帧发送失败: {exc}"
            self.log(result["message"], "ERROR")
        return result

    def send_protocol_frames(self, frames: list[bytes], timeout: float = 1.0) -> dict:
        """按顺序发送多帧 ViewLink 原始协议。"""
        results = [self.send_protocol_frame(frame, timeout=timeout) for frame in frames]
        success = all(item.get("success", False) for item in results)
        return {
            "success": success,
            "message": "协议帧全部发送" if success else "部分协议帧发送失败",
            "frames": results,
        }

    def _confirm_and_send_protocol(self, method: str, args: dict, message: str) -> dict:
        """Confirm SDK connection, then send the matching ViewLink V3.4.9 frame."""
        confirmed = self._confirm_command_sent(message)
        if not confirmed.get("success"):
            return confirmed

        frame = build_protocol_frame_for_method(method, args)
        if not frame:
            return confirmed

        protocol_result = self.send_protocol_frame(frame)
        if not protocol_result.get("success"):
            return {
                "success": False,
                "message": protocol_result.get("message", "协议帧发送失败"),
                "protocol_result": protocol_result,
            }

        confirmed["protocol_result"] = protocol_result
        return confirmed

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

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        # 限制速度范围
        yaw_speed = max(-16383, min(16383, int(yaw_speed or 0)))
        pitch_speed = max(-16383, min(16383, int(pitch_speed or 0)))

        self._call_sdk("VLK_Move", yaw_speed, pitch_speed)
        return self._confirm_and_send_protocol(
            "move",
            {"yaw_speed": yaw_speed, "pitch_speed": pitch_speed},
            f"云台移动: yaw={yaw_speed}, pitch={pitch_speed}",
        )

    def stop(self) -> dict:
        """停止云台移动"""
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        self._call_sdk("VLK_Stop")
        return self._confirm_and_send_protocol(
            "move",
            {"yaw_speed": 0, "pitch_speed": 0},
            "云台已停止",
        )

    def home(self) -> dict:
        """云台归位（回到初始位置）"""
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        self._call_sdk("VLK_Home")
        return self._confirm_and_send_protocol("home", {}, "云台正在归位")

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

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        yaw = float(yaw or 0.0)
        pitch = float(pitch or 0.0)
        self._call_sdk("VLK_TurnTo", ctypes.c_double(yaw), ctypes.c_double(pitch))
        return self._confirm_and_send_protocol(
            "turn_to",
            {"yaw": yaw, "pitch": pitch},
            f"云台转向: yaw={yaw}°, pitch={pitch}°",
        )

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

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        speed = max(1, min(8, int(speed or 5)))
        self._call_sdk("VLK_ZoomIn", speed)
        return self._confirm_and_send_protocol("zoom_in", {"speed": speed}, f"放大中，速度: {speed}")

    def zoom_out(self, speed: int = 5) -> dict:
        """
        缩小（Zoom Out）

        Args:
            speed: 变焦速度 (1-8)

        Returns:
            dict: {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        speed = max(1, min(8, int(speed or 5)))
        self._call_sdk("VLK_ZoomOut", speed)
        return self._confirm_and_send_protocol("zoom_out", {"speed": speed}, f"缩小中，速度: {speed}")

    def stop_zoom(self) -> dict:
        """停止变焦"""
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        self._call_sdk("VLK_StopZoom")
        return self._confirm_and_send_protocol("stop_zoom", {}, "变焦已停止")

    def zoom_to(self, magnification: float) -> dict:
        """变焦到指定倍率"""
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        magnification = max(1.0, float(magnification))
        self._call_sdk("VLK_ZoomTo", ctypes.c_float(magnification))
        return self._confirm_and_send_protocol(
            "zoom_to",
            {"magnification": magnification},
            f"变焦到指定倍率: {magnification}x",
        )

    # ==================== 对焦控制 ====================

    def focus_in(self, speed: int = 5) -> dict:
        """近焦方向连续对焦"""
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        speed = max(1, min(8, int(speed)))
        self._call_sdk("VLK_FocusIn", speed)
        result["success"] = True
        result["message"] = f"近焦中，速度: {speed}"
        self.log(result["message"])
        return result

    def focus_out(self, speed: int = 5) -> dict:
        """远焦方向连续对焦"""
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        speed = max(1, min(8, int(speed)))
        self._call_sdk("VLK_FocusOut", speed)
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

        self._call_sdk("VLK_StopFocus")
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

        self._call_sdk("VLK_SetFocusMode", int(mode))
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

        mode = self._call_sdk("VLK_GetFocusMode")
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
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        enabled = bool(on)
        self._call_sdk("VLK_SwitchEODigitalZoom", 1 if enabled else 0)
        return self._confirm_and_send_protocol(
            "switch_eo_digital_zoom",
            {"on": enabled},
            f"EO电子变焦{'开启' if enabled else '关闭'}",
        )

    def eo_digital_zoom_stop(self) -> dict:
        """停止可见光电子变焦。"""
        connected = self._require_connected()
        if not connected["success"]:
            return {"success": False, "message": connected["message"]}
        result = self.send_protocol_frame(build_eo_dzoom_stop_frame())
        if result.get("success"):
            result["message"] = "可见光电子变焦已停止"
            self.log(result["message"])
        return result

    def ir_digital_zoom_in(self, speed: int = 0) -> dict:
        """红外电子变焦放大一级"""
        result = {"success": False, "message": ""}
        if not self._connected:
            result["message"] = "未连接到设备"
            return result
        self._call_sdk("VLK_IRDigitalZoomIn", int(speed))
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
        self._call_sdk("VLK_IRDigitalZoomOut", int(speed))
        result["success"] = True
        result["message"] = "红外电子变焦缩小"
        self.log(result["message"])
        return result

    def ir_digital_zoom_stop(self) -> dict:
        """停止红外连续电子变焦。"""
        connected = self._require_connected()
        if not connected["success"]:
            return {"success": False, "message": connected["message"]}
        result = self.send_protocol_frame(build_ir_dzoom_stop_frame())
        if result.get("success"):
            result["message"] = "红外电子变焦已停止"
            self.log(result["message"])
        return result

    def switch_defog(self, on: bool) -> dict:
        """开启或关闭除雾"""
        return self._switch_int("VLK_SwitchDefog", bool(on), "除雾")

    def is_defog_on(self) -> dict:
        """查询除雾状态"""
        if not self._connected:
            return {"success": False, "on": False, "message": "未连接到设备"}
        on = self._call_sdk("VLK_IsDefogOn") == 1
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
        self._call_sdk("VLK_LaserSingle")
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
        self._call_sdk("VLK_LaserZoomIn", int(speed))
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
        self._call_sdk("VLK_LaserZoomOut", int(speed))
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
        self._call_sdk("VLK_LaserStopZoom")
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
        self._call_sdk("VLK_SetLaserZoomMode", int(mode))
        result["success"] = True
        result["message"] = f"设置激光变焦模式: {int(mode)}"
        self.log(result["message"])
        return result

    def is_tracking(self) -> dict:
        """查询跟踪状态"""
        if not self._connected:
            return {"success": False, "tracking": False, "message": "未连接到设备"}
        tracking = self._call_sdk("VLK_IsTracking") == 1
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
        self._call_sdk("VLK_SetImageColor", int(image_type), 1 if enable_pip else 0, int(ir_color))
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
        self._call_sdk(sdk_method, 1 if on else 0)
        result["success"] = True
        result["message"] = f"{label}{'开启' if on else '关闭'}"
        self.log(result["message"])
        return result

    # ==================== 跟踪控制 ====================

    def start_track(
        self,
        x: int,
        y: int,
        video_width: int,
        video_height: int,
        template_size: VLKTrackTemplateSize = VLKTrackTemplateSize.AUTO,
        sensor: VLKSensor = VLKSensor.VISIBLE1
    ) -> dict:
        """
        开始跟踪目标

        Args:
            x: 目标中心 X 像素坐标
            y: 目标中心 Y 像素坐标
            video_width: 视频画面宽度
            video_height: 视频画面高度
            template_size: 跟踪模板尺寸
            sensor: 使用的传感器

        Returns:
            dict: {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        param = VLK_TRACK_MODE_PARAM()
        param.emTrackTempSize = int(template_size)
        param.emTrackSensor = int(sensor)

        self._call_sdk(
            "VLK_TrackTargetPositionEx",
            ctypes.byref(param), x, y, video_width, video_height
        )

        return self._confirm_and_send_protocol(
            "start_track",
            {"sensor": int(sensor)},
            f"开始跟踪: 位置({x},{y}), 视频({video_width}x{video_height})",
        )

    def stop_track(self) -> dict:
        """停止跟踪"""
        result = {"success": False, "message": ""}

        connected = self._require_connected()
        if not connected["success"]:
            result["message"] = connected["message"]
            return result

        self._call_sdk("VLK_DisableTrackMode")
        return self._confirm_and_send_protocol("stop_track", {}, "跟踪已停止")

    # ==================== 拍摄控制 ====================

    def take_photo(self) -> dict:
        """拍照"""
        result = {"success": False, "message": ""}

        if not self._connected:
            result["message"] = "未连接到设备"
            return result

        self._call_sdk("VLK_Photograph")
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

        self._call_sdk("VLK_SwitchRecord", 1)
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

        self._call_sdk("VLK_SwitchRecord", 0)
        self._is_recording = False
        result["success"] = True
        result["message"] = "停止录像"
        self.log(result["message"])

        return result

    def is_recording(self) -> bool:
        """检查是否正在录像"""
        if self._lib and self._connected:
            return self._call_sdk("VLK_IsRecording") == 1
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

        self._call_sdk("VLK_SetRecordMode", int(mode))
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
            return {"success": False, "mode": -1, "mode_name": "未连接"}

        mode = self._call_sdk("VLK_GetRecordMode")
        mode_names = {
            0: "无模式",
            1: "拍照模式",
            2: "录像模式"
        }

        return {
            "success": True,
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
            "sdk_dir": self.sdk_dir,
            "register_dev_status_callback": self.register_dev_status_callback,
            "dev_status_callback_registered": self._dev_status_callback_registered,
            "dev_status_callback_registering": self._dev_status_registration_in_progress,
            "dev_status_registration_result": dict(self._dev_status_registration_result),
            "last_dev_status": dict(self._last_dev_status) if self._last_dev_status else None,
        }

    # ==================== 回调设置 ====================

    def set_connection_callback(self, callback: Callable):
        """设置连接状态变化回调"""
        self._on_conn_status_change = callback

    def set_device_callback(self, callback: Callable):
        """设置设备状态变化回调"""
        self._on_dev_status_change = callback
