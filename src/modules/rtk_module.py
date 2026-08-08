"""
RTK定位模块 - 多源热备 + 手动/断线切换 + 降频注入
modules/rtk_module.py

依赖: src.modules.rtk_forwarder.RtkSourcePool，不依赖 RTKF9P 独立参考目录。

切换方式:
  1. 自动切换: 活跃源断开/15s无数据 → 切到 priority 最高的健康源
  2. 手动切换: switch_to(name) → 用户主动指定

配置文件: configfile/rtk_sources.json
"""

import time
from threading import Lock
from typing import Optional, Dict, Any
from src.modules.base_module import BaseModule
from src.core.message_dispatcher import MAVLinkMessage

# ── rtk_forwarder 多源热备池 ──
try:
    from src.modules.rtk_forwarder.source_pool import RtkSourcePool
    from src.modules.rtk_forwarder.rtk_pool_bridge import create_pool_from_config, update_pool_from_config
    _HAS_POOL = True
except ImportError:
    RtkSourcePool = None
    create_pool_from_config = None
    update_pool_from_config = None
    _HAS_POOL = False


def generate_gga(
    lat: float, lon: float, alt: float = 0.0, fix_quality: int = 1, num_sats: int = 12
) -> str:
    """
    生成 GNGGA 语句

    Args:
        lat: 纬度(度,正北负南)
        lon: 经度(度,正东负西)
        alt: 海拔高度(米)
        fix_quality: 定位质量 (0=无效, 1=GPS, 2=DGPS, 4=RTK固定, 5=RTK浮点)
        num_sats: 可见卫星数

    Returns:
        完整的 GNGGA 语句字符串
    """
    now = time.gmtime()
    utc_time = f"{now.tm_hour:02d}{now.tm_min:02d}{now.tm_sec:02d}.00"

    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60
    lat_str = f"{lat_deg:02d}{lat_min:07.4f}"
    lat_dir = "N" if lat >= 0 else "S"

    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60
    lon_str = f"{lon_deg:03d}{lon_min:07.4f}"
    lon_dir = "E" if lon >= 0 else "W"

    hdop = "1.0"
    alt_str = f"{alt:.1f}"
    geoid_sep = "0.0"
    diff_age = ""
    diff_station = "0000"

    gga_no_checksum = (
        f"GNGGA,{utc_time},{lat_str},{lat_dir},{lon_str},{lon_dir},"
        f"{fix_quality},{num_sats},{hdop},{alt_str},M,{geoid_sep},M,"
        f"{diff_age},{diff_station}"
    )

    checksum = 0
    for char in gga_no_checksum:
        checksum ^= ord(char)

    gga = f"${gga_no_checksum}*{checksum:02X}\r\n"
    return gga


# ⭐ 北京市固定坐标(仅用于测试模式)
BEIJING_LAT = 39.9042
BEIJING_LON = 116.4074
BEIJING_ALT = 43.0


# ==========================================================================
# 配置标准化：扁平格式 → 命名源格式（向后兼容，推荐 rtk_sources.json）
# ==========================================================================

def _normalize_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    将旧的扁平配置转换为命名源格式，新格式（含 "sources" 字段）直接返回。

    JSON 格式（推荐）:
        {"enabled": true, "sources": {"F9P-1": {...}, "NTRIP-QX": {...}}}
        → 直通，priority 由用户在 JSON 中显式指定

    旧扁平格式（向后兼容）:
        {"rtk_source": "auto", "f9p_host": "...", "host": "...", ...}
        → 转为 {"sources": {"F9P": {...}, "NTRIP": {...}}}
    """
    if "sources" in cfg:
        return cfg

    # ── 旧格式转换 ──
    sources = {}
    source = cfg.get("rtk_source", "f9p")
    primary = cfg.get("failover_primary", "f9p")

    if source in ("f9p", "auto"):
        f9p_hosts = cfg.get("f9p_hosts", "").strip()
        if not f9p_hosts:
            f9p_hosts = cfg.get("f9p_host", "192.168.1.13")
        f9p_ports = cfg.get("f9p_ports", "").strip()
        if not f9p_ports:
            f9p_ports = str(cfg.get("f9p_port", 8600))
        host_list = [h.strip() for h in str(f9p_hosts).split(",") if h.strip()]
        port_list = [p.strip() for p in str(f9p_ports).split(",") if p.strip()]

        for i, (host, port_str) in enumerate(zip(host_list, port_list)):
            name = f"F9P-{i + 1}" if len(host_list) > 1 else "F9P"
            f9p_priority = 10 if primary == "f9p" else 5 if source == "auto" else 10
            sources[name] = {
                "type": "tcp", "host": host, "port": int(port_str),
                "priority": f9p_priority,
            }

    if source in ("ntrip", "auto"):
        ntrip_priority = 10 if primary == "ntrip" else 5 if source == "auto" else 10
        sources["NTRIP"] = {
            "type": "ntrip",
            "host": cfg.get("host", ""), "port": cfg.get("port", 0),
            "mountpoint": cfg.get("mountpoint", ""),
            "username": cfg.get("username", ""), "password": cfg.get("password", ""),
            "priority": ntrip_priority,
        }

    return {
        "sources": sources,
        "enabled": cfg.get("enabled", False),
        "min_inject_interval": cfg.get("min_inject_interval", 0.0),
    }


# ==========================================================================
# RTKModule — 多源热备 + F9P 优先
# ==========================================================================

# ── 返航模式（用于 RTK 自动切换，仅四旋翼返航）──
_RTL_MODES = ("QRTL",)


class RTKModule(BaseModule):
    """
    RTK 定位模块 —— 多源热备 + 优先级切换 + 降频注入。

    每个源在 rtk_sources.json 中独立配置 priority，谁大谁优先。
    Pool 模式下所有源保持长连接，活跃源断开时自动切到次高 priority 源，
    更高 priority 源恢复后自动切回。

    切换:
      - 自动: Pool 内部根据 priority + health 自动管理
      - 手动: module.switch_to("NTRIP-QX") / module.switch_to("F9P-1")

    配置字段 (JSON):
      enabled             : 是否启用
      min_inject_interval : 最小注入间隔(秒)，0=不限制
      sources             : {"源名": {"type":"tcp"|"ntrip", "host":"...", "port":..., "priority":...}}
    """

    def __init__(
        self, ntrip_config: Optional[Dict[str, Any]] = None, telemetry_module=None
    ):
        super().__init__("RTKModule")

        # 配置标准化
        self.ntrip_config = _normalize_config(ntrip_config or {})

        # Pool 核心
        self._pool: Optional["RtkSourcePool"] = None
        self.dispatcher = None

        self.telemetry_module = telemetry_module
        self.lock = Lock()

        self.rtk_status = {
            "connected": False,
            "authenticated": False,
            "connection_error": None,
            "last_error": None,
            "disconnect_reason": None,
            "gps_fix_type": 0,
            "gps_fix_type_str": "未知",
            "satellites_visible": 0,
            "rtcm_packets_received": 0,
            "rtcm_packets_sent": 0,
            "last_rtcm_time": 0.0,
            "enabled": self.ntrip_config.get("enabled", False),
            "takeoff_rtk": None,
        }

        self.total_rtcm_bytes = 0
        self.start_time = 0.0

        # ── 降频注入 ──
        self._min_inject_interval = self.ntrip_config.get("min_inject_interval", 0.0)
        self._last_inject_time = 0.0

        # 日志
        if self.telemetry_module:
            self.log("🚁 生产模式: 使用实时GPS位置")
        else:
            self.log(f"🧪 测试模式: 固定坐标 ({BEIJING_LAT:.4f}, {BEIJING_LON:.4f})")

        # ── 距离自动切换 ──
        self._as_cfg = self.ntrip_config.get("auto_switch", {})
        self._as_enabled = self._as_cfg.get("enabled", False)
        self._as_last_switch_time = 0.0
        self._as_cooldown = 10.0       # 最小切换间隔(秒)
        self._as_last_distance = 0.0   # 上次评估时的距离(km)

        # 归一化为数组（兼容旧字符串格式），用于源类型判断
        _raw = self._as_cfg.get("takeoff_landing_rtk", "F9P")
        self._local_sources = [_raw] if isinstance(_raw, str) else list(_raw)
        _raw = self._as_cfg.get("cruise_rtk", "NTRIP-1")
        self._network_sources = [_raw] if isinstance(_raw, str) else list(_raw)

        # ── 起飞前 RTK 准备 ──
        self._takeoff_rtk = None  # 起降 RTK 源名（prepare_takeoff_rtk 后设置）

        sources = self.ntrip_config.get("sources", {})
        self.log(f"📡 RTK数据源: {len(sources)} 个——{', '.join(sources.keys())}")
        for name, cfg in sources.items():
            self.log(f"   {name}: {cfg['type']} priority={cfg.get('priority', 0)} ({cfg.get('host', '')}:{cfg.get('port', '')})")

    def set_telemetry_module(self, telemetry_module):
        """设置TelemetryModule引用"""
        self.telemetry_module = telemetry_module
        self.log(f"✅ 已关联TelemetryModule（将使用实时GPS位置）")

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------

    def on_start(self):
        """模块启动——优先用 RtkSourcePool，失败时回退到旧客户端"""
        super().on_start()

        if not self.rtk_status["enabled"] or not self.ntrip_config:
            return

        ok = self._start_pool()
        if ok:
            return
        self.log("❌ Pool 启动失败", "ERROR")
        with self.lock:
            self.rtk_status["connected"] = False

    def on_stop(self):
        """模块停止"""
        self.stop_ntrip()
        super().on_stop()

    def reset_state(self):
        """
        重置 RTK 状态到默认值（无人机上电/下电时调用）

        只清无人机侧的 GPS 遥测与每架次的 RTCM 收发统计，
        不影响数据源池（NTRIP/F9P 长连接）本身。
        """
        self.log("🔄 正在重置 RTK 状态...")
        with self.lock:
            # ── GPS 定位（来自上一架无人机的遥测）──
            self.rtk_status["gps_fix_type"] = 0
            self.rtk_status["gps_fix_type_str"] = "未知"
            self.rtk_status["satellites_visible"] = 0

            # ── RTCM 收发计数（每架次归零）──
            self.rtk_status["rtcm_packets_received"] = 0
            self.rtk_status["rtcm_packets_sent"] = 0
            self.rtk_status["last_rtcm_time"] = 0.0

            # ── 起飞前 RTK 准备状态（每架次重新 prepare）──
            self.rtk_status["takeoff_rtk"] = None
            self._takeoff_rtk = None

        self.total_rtcm_bytes = 0
        self._last_inject_time = 0.0
        self._as_last_distance = 0.0  # 距离自动切换评估基线

        self.log("✅ RTK 状态已重置")

    # ------------------------------------------------------------------
    # Pool 启动
    # ------------------------------------------------------------------

    def _start_pool(self) -> bool:
        """用 RtkSourcePool 启动所有数据源"""
        if create_pool_from_config is None:
            self.log("❌ rtk_forwarder 包未安装", "ERROR")
            return False

        with self.lock:
            self.rtk_status["connection_error"] = None
            self.rtk_status["last_error"] = None
            self.rtk_status["disconnect_reason"] = None
            self.rtk_status["authenticated"] = True

        try:
            self._pool = create_pool_from_config(
                self.ntrip_config,
                inject=self._on_rtcm_data,
                gga_provider=self._make_gga_provider(),
            )
            self._pool.on_switch = self._on_pool_switch
            self._pool.start()

            with self.lock:
                self.rtk_status["connected"] = True
                self.rtk_status["rtk_source"] = self._pool.active

            self.start_time = time.time()
            self.log(f"✅ RtkSourcePool 已启动 (活跃源: {self._pool.active})")
            return True

        except Exception as e:
            with self.lock:
                self.rtk_status["connection_error"] = str(e)
                self.rtk_status["last_error"] = str(e)
            self.log(f"❌ Pool 启动失败: {e}", "ERROR")
            return False

    def _on_pool_switch(self, old: str, new: str):
        """Pool 切换回调"""
        with self.lock:
            self.rtk_status["rtk_source"] = new
        self.log(f"🔄 数据源切换: {old} → {new}")

    def _make_gga_provider(self):
        """返回线程安全的 GGA 生成函数"""
        def get_gga() -> str:
            return self._get_current_gga()
        return get_gga

    # ------------------------------------------------------------------
    # 数据源切换（手动）
    # ------------------------------------------------------------------

    def switch_to(self, name: str) -> bool:
        """
        手动切换到指定数据源。

        Args:
            name: 源名称，如 "F9P"、"F9P-1"、"NTRIP-QX"

        Returns:
            True 如果切换成功
        """
        if self._pool is None:
            self.log("❌ Pool 未启动，无法切换", "ERROR")
            return False
        ok = self._pool.switch_to(name)
        if ok:
            with self.lock:
                self.rtk_status["rtk_source"] = name
            self.log(f"✅ 已切换到 {name}")
        else:
            self.log(f"❌ 切换失败: 源 '{name}' 不存在或未连接", "ERROR")
        return ok

    def get_rtk_source(self) -> str:
        """获取当前活跃数据源名"""
        if self._pool is not None and self._pool.active:
            return self._pool.active
        return self.rtk_status.get("rtk_source", "f9p")

    # ------------------------------------------------------------------
    # 按类型切换（无需硬编码源名）
    # ------------------------------------------------------------------

    def _find_source_by_type(self, keyword: str) -> Optional[str]:
        """在所有已注册源中按关键字查找第一个匹配源名。"""
        if self._pool is None:
            return None
        for name in self._pool.source_names:
            if keyword.upper() in name.upper():
                return name
        return None

    def switch_to_f9p(self) -> bool:
        """切换到 F9P 源（按源名关键字匹配）。"""
        name = self._find_source_by_type("F9P")
        if name is None:
            self.log("❌ 未找到 F9P 源", "ERROR")
            return False
        return self.switch_to(name)

    def switch_to_ntrip(self) -> bool:
        """切换到网络 RTK 源（按源名关键字匹配）。"""
        name = self._find_source_by_type("NTRIP")
        if name is None:
            self.log("❌ 未找到网络 RTK 源", "ERROR")
            return False
        return self.switch_to(name)

    # ------------------------------------------------------------------
    # 场景切换（给外部调用的语义接口）
    # ------------------------------------------------------------------

    def _try_switch_to_first(self, sources: list, label: str) -> bool:
        """依次尝试列表中的源名，第一个成功的即返回。"""
        for name in sources:
            if self.switch_to(name):
                return True
        self.log(f"❌ 所有{label} RTK 源均不可用: {sources}", "ERROR")
        return False

    def switch_to_takeoff_landing_rtk(self) -> bool:
        """切换到起降 RTK 源（遍历 _local_sources 列表）。"""
        return self._try_switch_to_first(self._local_sources, "起降")

    def switch_to_cruise_rtk(self) -> bool:
        """切换到巡航 RTK 源（遍历 _network_sources 列表）。"""
        return self._try_switch_to_first(self._network_sources, "巡航")

    # ------------------------------------------------------------------
    # 起飞前 RTK 准备
    # ------------------------------------------------------------------

    def prepare_takeoff_rtk(self) -> Optional[str]:
        """
        起飞前 RTK 准备：依次尝试本地起降源列表，切换到第一个可用的。

        幂等：已准备过且源仍活跃时直接返回，防止并发重复切换。
        成功时 self._takeoff_rtk = 源名，失败时为 None。
        结果同步写入 rtk_status["takeoff_rtk"]。

        Returns:
            当前起降 RTK 源名，失败返回 None
        """
        # 幂等：已准备过且当前活跃源仍是起降源 → 直接返回
        if self._takeoff_rtk and self._takeoff_rtk == self.get_rtk_source():
            return self._takeoff_rtk

        for name in self._local_sources:
            if self.switch_to(name):
                self._takeoff_rtk = name
                self.log(f"✅ 起飞前 RTK 准备完成: {name}")
                break
        else:
            self._takeoff_rtk = None
            self.log(f"❌ 起飞前 RTK 准备失败: {self._local_sources} 均不可用", "WARNING")
        with self.lock:
            self.rtk_status["takeoff_rtk"] = self._takeoff_rtk
        return self._takeoff_rtk

    # ------------------------------------------------------------------
    # GPS 位置 / GGA
    # ------------------------------------------------------------------

    def _get_current_position(self) -> Dict[str, float]:
        """获取当前位置"""
        if self.telemetry_module:
            try:
                telemetry_data = self.telemetry_module.get_telemetry_data()
                lat = telemetry_data.get("latitude", 0.0)
                lon = telemetry_data.get("longitude", 0.0)
                alt = telemetry_data.get("altitude", 0.0)
                if abs(lat) < 0.0001 and abs(lon) < 0.0001:
                    # self.log("⚠️ GPS数据无效，使用固定北京坐标", "WARNING")
                    return {"lat": BEIJING_LAT, "lon": BEIJING_LON, "alt": BEIJING_ALT}
                return {"lat": lat, "lon": lon, "alt": alt}
            except Exception as e:
                self.log(f"⚠️ 获取实时GPS失败: {e}，使用固定北京坐标", "WARNING")
                return {"lat": BEIJING_LAT, "lon": BEIJING_LON, "alt": BEIJING_ALT}
        else:
            return {"lat": BEIJING_LAT, "lon": BEIJING_LON, "alt": BEIJING_ALT}

    def _get_current_gga(self) -> str:
        """生成当前位置的GGA语句"""
        position = self._get_current_position()
        with self.lock:
            gga = generate_gga(
                position["lat"], position["lon"], position["alt"],
                fix_quality=min(self.rtk_status["gps_fix_type"], 5),
                num_sats=self.rtk_status["satellites_visible"],
            )
        return gga

    # ------------------------------------------------------------------
    # 停止
    # ------------------------------------------------------------------

    def stop_ntrip(self, reason: str = "用户手动停止"):
        """停止所有 RTK 数据源"""
        if self._pool is not None:
            self._pool.stop()
            self._pool = None
            self.log(f"RtkSourcePool 已停止: {reason}")

        with self.lock:
            self.rtk_status["connected"] = False
            self.rtk_status["authenticated"] = False
            self.rtk_status["disconnect_reason"] = reason

    # ------------------------------------------------------------------
    # RTCM 数据回调 + 降频注入
    # ------------------------------------------------------------------

    def _on_rtcm_data(self, data: bytes):
        """接收到 RTCM 数据的回调（pool 模式的 inject 回调）"""
        with self.lock:
            self.total_rtcm_bytes += len(data)
            self.rtk_status["rtcm_packets_received"] += 1
            self.rtk_status["last_rtcm_time"] = time.time()
            pkt_count = self.rtk_status["rtcm_packets_received"]

        if pkt_count % 10 == 0:
            rtk_type = self.rtk_status.get("rtk_source", "f9p").upper()
            # self.log(f"📥 收到RTCM数据包 #{pkt_count} ({len(data)} bytes) [{rtk_type}]", "DEBUG")

        # ── 降频门控 ──
        if self._min_inject_interval > 0:
            now = time.time()
            if now - self._last_inject_time < self._min_inject_interval:
                return  # 跳过此帧
            self._last_inject_time = now

        # 注入到飞控
        self._inject_rtcm_to_mavlink(data)

    def _inject_rtcm_to_mavlink(self, data: bytes):
        """通过 MAVLink 注入 RTCM 数据到飞控"""
        if not self.dispatcher or not self.dispatcher.connection:
            self.log("❌ [注入失败] dispatcher或connection为None", "ERROR")
            return

        if not self.dispatcher.connection.is_connected():
            # self.log("❌ [注入失败] MAVLink未连接", "DEBUG")
            return

        try:
            master = self.dispatcher.connection.master
            target_system = master.target_system if master.target_system > 0 else 1
            target_component = master.target_component if master.target_component > 0 else 0

            chunk_size = 110
            injected_count = 0

            for i in range(0, len(data), chunk_size):
                chunk = data[i: i + chunk_size]
                chunk_len = len(chunk)
                padded_data = list(chunk) + [0] * (chunk_size - chunk_len)

                try:
                    msg = master.mav.gps_inject_data_encode(
                        target_system, target_component, chunk_len, padded_data
                    )
                    self.dispatcher.connection.send_message(msg)
                    injected_count += 1
                    with self.lock:
                        self.rtk_status["rtcm_packets_sent"] += 1
                    time.sleep(0.001)

                except AttributeError:
                    self.log("⚠️ GPS_INJECT_DATA不支持，回退到GPS_RTCM_DATA", "WARNING")
                    chunk_180 = data[i: i + 180]
                    padded_180 = list(chunk_180) + [0] * (180 - len(chunk_180))
                    msg = master.mav.gps_rtcm_data_encode(
                        flags=1, len=len(chunk_180), data=padded_180
                    )
                    self.dispatcher.connection.send_message(msg)
                    injected_count += 1
                    with self.lock:
                        self.rtk_status["rtcm_packets_sent"] += 1
                    time.sleep(0.001)

            if self.rtk_status["rtcm_packets_sent"] % 100 == 0:
                self.log(
                    f"📤 已注入 {self.rtk_status['rtcm_packets_sent']} 个RTCM包 "
                    f"(目标: {target_system}/{target_component}, "
                    f"本次: {len(data)}B → {injected_count}个MAVLink包)",
                    "INFO",
                )

        except Exception as e:
            self.log(f"❌ 注入RTCM失败: {e}", "ERROR")
            import traceback
            self.log(f"堆栈: {traceback.format_exc()}", "ERROR")

    # ------------------------------------------------------------------
    # 消息处理
    # ------------------------------------------------------------------

    def on_message(self, message: MAVLinkMessage):
        """处理 GPS 和 RTK 相关消息"""
        msg = message.msg
        msg_type = message.msg_type

        if msg_type == "GPS_RAW_INT":
            with self.lock:
                old_fix = self.rtk_status["gps_fix_type"]
                self.rtk_status["gps_fix_type"] = msg.fix_type
                self.rtk_status["satellites_visible"] = msg.satellites_visible
                if old_fix != msg.fix_type:
                    self.log(
                        f"🛰️ GPS定位类型变化: {old_fix} → {msg.fix_type} "
                        f"({self._get_fix_type_name(msg.fix_type)})", "INFO")

        elif msg_type == "GPS2_RAW":
            with self.lock:
                self.rtk_status["gps_fix_type"] = msg.fix_type
                self.rtk_status["satellites_visible"] = msg.satellites_visible

        elif msg_type == "GPS_RTK":
            self.log(f"📡 RTK状态: baseline={msg.baseline_coords_type}, "
                     f"rtk_rate={msg.rtk_rate}, nsats={msg.nsats}, "
                     f"accuracy={msg.accuracy}", "DEBUG")

        elif msg_type == "GPS2_RTK":
            self.log(f"📡 RTK2状态: baseline={msg.baseline_coords_type}, "
                     f"rtk_rate={msg.rtk_rate}, nsats={msg.nsats}", "DEBUG")

        elif msg_type == "GPS_INJECT_STATUS":
            self.log(f"💉 GPS注入状态: received={msg.received_count}, "
                     f"processed={msg.processed_count}, "
                     f"⚠️ 未处理={msg.received_count - msg.processed_count}",
                     "WARNING" if msg.received_count != msg.processed_count else "INFO")

    def _get_fix_type_name(self, fix_type: int) -> str:
        fix_type_map = {
            0: "无定位", 1: "无定位", 2: "2D定位", 3: "3D定位",
            4: "DGPS", 5: "RTK Float", 6: "RTK Fixed",
        }
        return fix_type_map.get(fix_type, f"未知({fix_type})")

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------

    def diagnose_rtk(self) -> Dict[str, Any]:
        """RTK诊断"""
        status = self.get_rtk_status()
        src = status.get("active", status.get("rtk_source", "f9p"))
        src_label = "F9P基站" if "F9P" in str(src).upper() else "NTRIP"

        diagnostics = {
            "timestamp": time.time(),
            "rtk_source": src,
            "connected": status["connected"],
            "rtcm_flow": {
                "receiving": status["rtcm_packets_received"] > 0,
                "injecting": status["rtcm_packets_sent"] > 0,
                "received_packets": status["rtcm_packets_received"],
                "sent_packets": status["rtcm_packets_sent"],
                "total_bytes": status.get("total_rtcm_bytes", 0),
                "data_rate_bps": status.get("rtcm_rate_bps", 0),
            },
            "gps_status": {
                "fix_type": status["gps_fix_type"],
                "fix_type_str": status["gps_fix_type_str"],
                "satellites": status["satellites_visible"],
                "is_rtk": status["gps_fix_type"] in [5, 6],
            },
            "mavlink_status": {
                "connected": self.dispatcher is not None and self.dispatcher.connection.is_connected(),
                "target_system": self.dispatcher.connection.master.target_system if self.dispatcher else None,
                "target_component": self.dispatcher.connection.master.target_component if self.dispatcher else None,
            },
            "possible_issues": [],
        }

        if not status["connected"]:
            diagnostics["possible_issues"].append({
                "level": "ERROR",
                "message": f"当前数据源 {src_label} 未连接",
                "suggestion": "检查网络和配置" if "F9P" not in str(src).upper() else "检查F9P基准站是否在线",
            })

        if status["rtcm_packets_received"] == 0:
            diagnostics["possible_issues"].append({
                "level": "ERROR",
                "message": "未接收到RTCM数据",
                "suggestion": "检查挂载点和GGA坐标" if "F9P" not in str(src).upper() else "检查F9P基准站是否配置为输出RTCM3",
            })

        if status["rtcm_packets_sent"] == 0 and status["rtcm_packets_received"] > 0:
            diagnostics["possible_issues"].append({
                "level": "ERROR",
                "message": "RTCM数据未发送到飞控",
                "suggestion": "检查MAVLink连接和GPS参数配置",
            })

        if status["satellites_visible"] < 8:
            diagnostics["possible_issues"].append({
                "level": "WARNING",
                "message": f"卫星数量不足({status['satellites_visible']})",
                "suggestion": "移动到开阔地带，等待更多卫星",
            })

        return diagnostics

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_rtk_status(self) -> Dict[str, Any]:
        """获取 RTK 状态"""
        with self.lock:
            status = self.rtk_status.copy()

        if self._pool is not None:
            pool_info = self._pool.config()
            status["active"] = pool_info["active"]
            # sources 简化：name / type / host / port / connected
            status["sources"] = [
                {
                    "name": s["name"],
                    "type": s.get("config", {}).get("type", ""),
                    "host": s.get("config", {}).get("host", ""),
                    "port": s.get("config", {}).get("port", 0),
                    "connected": s.get("health", {}).get("connected", False),
                }
                for s in pool_info.get("sources", [])
            ]

        # 删除冗余字段（与 active 重复）
        status.pop("rtk_source", None)

        # 当前位置
        status["current_position"] = self._get_current_position()

        # 运行时间和速率
        if self.start_time > 0:
            runtime = time.time() - self.start_time
            status["runtime_seconds"] = runtime
            status["rtcm_rate_bps"] = self.total_rtcm_bytes * 8 / runtime if runtime > 0 else 0
        else:
            status["runtime_seconds"] = 0
            status["rtcm_rate_bps"] = 0

        status["total_rtcm_bytes"] = self.total_rtcm_bytes

        fix_type_map = {
            0: "无定位", 1: "无定位", 2: "2D定位", 3: "3D定位",
            4: "DGPS", 5: "RTK Float", 6: "RTK Fixed",
        }
        status["gps_fix_type_str"] = fix_type_map.get(status["gps_fix_type"], "未知")

        status["mavlink_connected"] = (
            self.dispatcher is not None and self.dispatcher.connection.is_connected()
        )

        if status["rtcm_packets_received"] > 0:
            status["injection_rate"] = status["rtcm_packets_sent"] / status["rtcm_packets_received"]
        else:
            status["injection_rate"] = 0.0

        return status

    # ------------------------------------------------------------------
    # 配置更新
    # ------------------------------------------------------------------

    def set_config(self, config: Dict[str, Any]) -> bool:
        """
        运行时更新 RTK 配置，无需重启模块。

        支持新格式（sources dict）和旧格式（扁平字段）。
        """
        old_config = self.ntrip_config.copy()
        new_config = _normalize_config({**old_config, **config})
        self.ntrip_config.update(new_config)

        self.rtk_status["enabled"] = config.get("enabled", self.rtk_status["enabled"])

        # 更新降频间隔
        if "min_inject_interval" in config:
            self._min_inject_interval = config["min_inject_interval"]

        if self._pool is not None and update_pool_from_config is not None:
            result = update_pool_from_config(
                self._pool, self.ntrip_config,
                gga_provider=self._make_gga_provider(),
            )
            if not result["ok"]:
                self.ntrip_config = old_config
                self.log(f"❌ 配置更新失败: {result['errors']}", "ERROR")
                return False
            self.log(f"✅ 配置已更新: {result['changes']}")
            return True

        self.log("❌ Pool 未启动，无法热更新配置", "ERROR")
        return False

    def update_pool_source(self, name: str, updates: Dict[str, Any]) -> bool:
        """
        直接更新线程池中指定 RTK 源的配置（不替换整个配置，只合并传入的字段）。

        Args:
            name: 源名称，如 "NTRIP-1"
            updates: 要更新的字段字典，如 {"username": "xxx", "password": "yyy"}

        Returns:
            True 表示更新成功，False 表示失败
        """
        sources = self.ntrip_config.get("sources", {})
        if name not in sources:
            self.log(f"❌ 源 '{name}' 不存在", "ERROR")
            return False

        # 合并更新内存中的配置（无论 pool 是否启动都更新）
        sources[name].update(updates)

        if self._pool is None:
            self.log(f"⚠️ Pool 未启动，配置已保存在内存/文件中，下次启动 Pool 后生效")
            return True

        # 热更新到 pool
        result = update_pool_from_config(
            self._pool, self.ntrip_config,
            gga_provider=self._make_gga_provider(),
        )
        if not result["ok"]:
            self.log(f"❌ Pool 源 '{name}' 更新失败: {result['errors']}", "ERROR")
            return False

        self.log(f"✅ Pool 源 '{name}' 已更新: {updates}")
        return True

    def is_rtk_ready(self) -> Dict[str, Any]:
        """
        起飞前 RTK 状态检查。

        格式: {success, message, data}
        """
        status = self.get_rtk_status()
        enabled = status.get("enabled", False)
        receiving = status.get("rtcm_packets_received", 0) > 0

        if self._pool is None:
            return {"success": False, "message": "RTK 数据源未启动", "data": {"checks": {"enabled": enabled, "receiving": receiving}}}
        pool_health = self._pool.health()
        any_healthy = any(h.get("status") == "healthy" for h in pool_health.values())
        active_name = self._pool.active or "(无)"
        reasons = []
        if not enabled:
            reasons.append("RTK 未启用")
        elif not any_healthy:
            reasons.append("无可用数据源")
        elif not receiving:
            reasons.append(f"未收到 RTCM 差分数据 (当前: {active_name})")

        success = len(reasons) == 0
        return {
            "success": success,
            "message": "; ".join(reasons) if reasons else f"RTK 就绪 ({active_name})",
            "data": {
                "checks": {
                    "enabled": enabled,
                    "sources": pool_health,
                    "receiving": receiving,
                    "current_source": active_name,
                }
            },
        }

    async def is_rtk_ready_async(self) -> Dict[str, Any]:
        """RTK 状态检查（async wrapper）"""
        return self.is_rtk_ready()

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return self.ntrip_config.copy()

    # ------------------------------------------------------------------
    # idle_task — pool 模式仅同步状态，旧模式管理重连/切换
    # ------------------------------------------------------------------

    def _sync_pool_status(self):
        """从 pool 同步状态到 rtk_status（轻量）"""
        if self._pool is None:
            return
        info = self._pool.config()
        active = info["active"]
        pool_health = self._pool.health()

        with self.lock:
            self.rtk_status["rtk_source"] = active
            if active:
                self.rtk_status["connected"] = pool_health.get(active, {}).get("connected", False)
            self.rtk_status["rtcm_packets_received"] = sum(
                h.get("health", {}).get("frames_received", h.get("frames_received", 0))
                if isinstance(h, dict) else 0
                for h in info.get("sources", [])
            )

    def idle_task(self, dispatcher):
        """空闲任务: 捕获 dispatcher + 同步状态 + 距离驱动 RTK 自动切换。

        分切续航rtk 和 起降rtk
        1、期望rtk为F9P， 巡航rtk为网络rtk
        2、如果实际起降rtk为网络rtk 巡航时时不需要改rtk的 降落时检查是否实际起降rtk
        3、切换时机为 起飞前 飞行1km 飞行2km 返回时1km 和RTL模式
        """
        if self.dispatcher is None:
            self.dispatcher = dispatcher

        self._sync_pool_status()

        if not self._as_enabled or not self.telemetry_module:
            return

        td = self.telemetry_module.get_telemetry_data()
        dist = td.get("distanceToHome", 0.0)
        flight_mode = td.get("flight_mode", "")

        # 实际起降 RTK：prepare 设置的优先，未调用则回退到配置列表第一个
        _landing_src = self._takeoff_rtk or (self._local_sources[0] if self._local_sources else None)
        if _landing_src is None:
            return  # 无可用起降源，跳过自动切换

        # ── RTL 模式：直接切回实际起降 RTK ──
        if flight_mode in _RTL_MODES:
            now = time.monotonic()
            if now - self._as_last_switch_time < self._as_cooldown:
                return
            active = self.get_rtk_source()
            if active in self._network_sources:
                self.log(f"🔄 RTL 模式 → 切回起降 RTK: {_landing_src}")
                self.switch_to(_landing_src)
                self._as_last_switch_time = now
            return

        # ── 距离区间判断（dist 单位：km，仅在 0.9-1.1km / 1.9-2.1km 区间评估）──
        in_zone = (0.9 < dist < 1.1) or (1.9 < dist < 2.1)
        if not in_zone:
            self._as_last_distance = dist
            return

        # 判断方向：距离增大 = 飞出去，距离减小 = 回巢
        if abs(dist - self._as_last_distance) < 0.001:  # < 1m 变化忽略
            return
        outbound = dist > self._as_last_distance
        self._as_last_distance = dist

        # 冷却
        now = time.monotonic()
        if now - self._as_last_switch_time < self._as_cooldown:
            return

        active = self.get_rtk_source()
        is_local = active in self._local_sources
        is_network = active in self._network_sources

        if not is_local and not is_network:
            self.log(f"⚠️ 活跃源 '{active}' 不在起降/巡航列表中，跳过自动切换", "WARNING")
            return

        if outbound and is_local:
            # 飞出去 → 使用巡航 RTK
            self.log(f"🔄 出航 dist={dist:.2f}km → 切换巡航 RTK")
            self.switch_to_cruise_rtk()
            self._as_last_switch_time = now
        elif not outbound and is_network:
            # 回巢 → 使用实际起降 RTK
            self.log(f"🔄 返航 dist={dist:.2f}km → 切回起降 RTK: {_landing_src}")
            self.switch_to(_landing_src)
            self._as_last_switch_time = now
