#!/usr/bin/env python3
"""基于 TCP 的机巢自定义 MAVLink 模拟器。"""
from __future__ import annotations

import argparse
import socket
import threading
import time

from simulator import NestSimulator
from protocol import drone_nest


class TCPNestSimulator(NestSimulator):
    """TCP server 版机巢模拟器，协议行为复用 UDP 模拟器。"""

    def __init__(self, *args, backlog: int = 1, **kwargs):
        super().__init__(*args, **kwargs)
        self.backlog = backlog
        self.client_sock = None
        self._send_lock = threading.Lock()

    def _send_raw(self, data):
        if not self.client_sock:
            return
        with self._send_lock:
            try:
                self.client_sock.sendall(data)
            except OSError as exc:
                print(f"TCP发送失败: {exc}")
                self.client_sock = None
                self.client_addr = None

    def _close_client(self):
        if self.client_sock:
            try:
                self.client_sock.close()
            except OSError:
                pass
        self.client_sock = None
        self.client_addr = None

    def _handle_client(self, conn, addr):
        self.client_sock = conn
        self.client_addr = addr
        conn.settimeout(0.2)
        self.mav = drone_nest.MAVLink(None, srcSystem=self.system_id, srcComponent=self.component_id)

        self._print_block("TCP客户端已连接", [
            ("client", addr),
            ("system/component", f"{self.system_id}/{self.component_id}"),
        ])
        self._send_heartbeat()
        self._send_nest_response(reason="connect")

        while self.running and self.client_sock is conn:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                print(f"TCP接收失败: {exc}")
                break

            if not data:
                print(f"TCP客户端断开: {addr}")
                break

            for byte in data:
                msg = self.mav.parse_char(bytes([byte]))
                if msg:
                    self._handle_message(msg)

        self._close_client()

    def _periodic_status_loop(self):
        while self.running:
            time.sleep(2.0)
            if self.client_sock:
                self._send_nest_response(reason="periodic")

    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(self.backlog)
        self.sock.settimeout(0.5)
        self.running = True

        status_thread = threading.Thread(target=self._periodic_status_loop, daemon=True)
        status_thread.start()

        print(f"机巢TCP模拟器启动: tcp://{self.host}:{self.port}")
        print("等待网关TCP连接...")
        try:
            while self.running:
                try:
                    conn, addr = self.sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                if self.client_sock:
                    print(f"已有客户端连接，拒绝新连接: {addr}")
                    conn.close()
                    continue

                self._handle_client(conn, addr)
        finally:
            self.running = False
            self._close_client()
            if self.sock:
                self.sock.close()


def main():
    parser = argparse.ArgumentParser(description="机巢自定义 MAVLink TCP 模拟器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=14551, help="监听端口")
    parser.add_argument("--system-id", type=int, default=101, help="模拟器 system id")
    parser.add_argument("--component-id", type=int, default=1, help="模拟器 component id")
    parser.add_argument("--log-status", action="store_true", help="打印周期状态帧")
    args = parser.parse_args()

    simulator = TCPNestSimulator(
        host=args.host,
        port=args.port,
        system_id=args.system_id,
        component_id=args.component_id,
        log_status=args.log_status,
    )
    simulator.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户中断，TCP机巢模拟器已停止")
