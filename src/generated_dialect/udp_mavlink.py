from pymavlink import mavutil


class UDPMavlink:
    def __init__(self, local_ip = '0.0.0.0',local_port=14550, remote_ip='127.0.0.1', remote_port=14551,
                 source_system=1, source_component=1):

        self.local_ip = local_ip
        self.local_port = local_port
        self.remote_ip = remote_ip
        self.remote_port = remote_port
        self.source_system = source_system
        self.source_component = source_component

        self.master_in  = mavutil.mavlink_connection(f"udpin:{self.local_ip}:{self.local_port}",
                                                     source_system=self.source_system,
                                                     source_component=self.source_component)

        self.master_out = mavutil.mavlink_connection(f"udpout:{self.remote_ip}:{self.remote_port}",
                                                     source_system=self.source_system,
                                                     source_component=self.source_component)

        self.target_system  = source_system
        self.target_component = source_component

        self.mav = self.master_out.mav

    def wait_heartbeat(self, blocking=True, timeout=None):
        print("udpin 等待心跳")
        return self.master_in.recv_match(type='HEARTBEAT', blocking=blocking, timeout=timeout)

    def recv_match(self, blocking=True, timeout=None):
        print("udpin 接收数据")
        return self.master_in.recv_match(blocking=True, timeout=timeout)

    def send(self, mavmsg) -> None:
        print("udpout 发送数据")
        return self.master_out.mav.send(mavmsg)

    def close(self):
        self.master_in.close()
        self.master_out.close()


    def get_target_system(self) -> int:
        return self.master_out.target_system

    def get_target_component(self) -> int:
        return self.master_out.target_component

    def get_mavlink(self) -> None:

        print("udpout 发送数据")
        return self.master_out.mav



#mavlink源码
# class mavudp(mavfile):
#     '''a UDP mavlink socket'''
#     def __init__(self, device, input=True, broadcast=False, source_system=255, source_component=0, use_native=default_native, timeout=0):
#         a = device.split(':')
#         if len(a) != 2:
#             raise ValueError("UDP ports must be specified as host:port")
#         self.port = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
#         self.udp_server = input
#         self.broadcast = False
#         if input:
#             self.port.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
#             self.port.bind((a[0], int(a[1])))
#         else:
#             self.destination_addr = (a[0], int(a[1]))
#             if broadcast:
#                 self.port.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
#                 self.broadcast = True
#         set_close_on_exec(self.port.fileno())
#         self.port.setblocking(0)
#         self.last_address = None
#         self.timeout = timeout
#         self.clients = set()
#         self.clients_last_alive = {}
#         self.resolved_destination_addr = None
#         mavfile.__init__(self, self.port.fileno(), device, source_system=source_system, source_component=source_component, input=input, use_native=use_native)
#
#     def close(self):
#         self.port.close()
#
#     def recv(self,n=None):
#         try:
#             data, new_addr = self.port.recvfrom(UDP_MAX_PACKET_LEN)
#         except socket.error as e:
#             if e.errno in [ errno.EAGAIN, errno.EWOULDBLOCK, errno.ECONNREFUSED ]:
#                 return ""
#             raise
#         if self.udp_server:
#             self.clients.add(new_addr)
#             self.clients_last_alive[new_addr] = time.time()
#         elif self.broadcast:
#             self.last_address = new_addr
#         return data
#
#     def write(self, buf):
#         try:
#             if self.udp_server:
#                 current_time = time.time()
#                 to_remove = set()
#                 for address in self.clients:
#                     if len(self.clients) == 1 or self.timeout <= 0 or self.clients_last_alive[address] + self.timeout > current_time:
#                         self.port.sendto(buf, address)
#                     elif len(self.clients) > 1 and len(to_remove) < len(self.clients) - 1:
#                         # we keep always at least 1 client, so we don't break old behavior
#                         to_remove.add(address)
#                         self.clients_last_alive.pop(address)
#                 self.clients -= to_remove
#             else:
#                 if self.last_address and self.broadcast:
#                     self.destination_addr = self.last_address
#                     self.broadcast = False
#                     self.port.connect(self.destination_addr)
#                 # turn a (possible) hostname into an IP address to
#                 # avoid resolving the hostname for every packet sent:
#                 if self.destination_addr[0] != self.resolved_destination_addr:
#                     self.resolved_destination_addr = self.destination_addr[0]
#                     self.destination_addr = (socket.gethostbyname(self.destination_addr[0]), self.destination_addr[1])
#                 self.port.sendto(buf, self.destination_addr)
#         except socket.error:
#             pass
#
#     def recv_msg(self):
#         '''message receive routine for UDP link'''
#         self.pre_message()
#         s = self.recv()
#         if len(s) > 0:
#             if self.first_byte:
#                 self.auto_mavlink_version(s)
#
#         m = self.mav.parse_char(s)
#         if m is not None:
#             self.post_message(m)
#
#         return m




