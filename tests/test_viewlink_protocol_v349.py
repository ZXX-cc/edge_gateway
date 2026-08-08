import unittest

from web.mavlink.viewlink import (
    build_a1_absolute_720,
    build_a1_speed,
    build_c1,
    build_c2,
    build_eo_dzoom_stop_frame,
    build_frame,
    build_ir_dzoom_in_frame,
    build_ir_dzoom_out_frame,
    build_ir_dzoom_stop_frame,
    build_ir_metering_area_frames,
    build_ir_metering_point_frames,
    build_s1_look_at,
    build_screen_split_frame,
    build_temperature_output_interval_frame,
    build_temperature_output_once_frame,
    build_temperature_output_stop_frame,
    build_viewlink_call,
    frame_hex,
)
from web.mavlink.viewlink.protocol import (
    C1_START_RECORDING,
    C2_DEFOG_ON,
    C2_TEMPERATURE_OUTPUT_INTERVAL,
    C2_TEMPERATURE_OUTPUT_ONCE,
    C2_TEMPERATURE_OUTPUT_STOP,
    C2_ZOOM_TO,
    FRAME_A1,
)


def checksum_ok(frame: bytes) -> bool:
    checksum = frame[3]
    for value in frame[4:-1]:
        checksum ^= value
    return checksum == frame[-1]


class ViewLinkProtocolV349Test(unittest.TestCase):
    def test_frame_length_and_checksum_match_protocol_example(self):
        frame = build_frame(FRAME_A1, [0x04, 0, 0, 0, 0, 0, 0, 0, 0])

        self.assertEqual(frame[:3], bytes([0x55, 0xAA, 0xDC]))
        self.assertEqual(frame[3], 0x0C)
        self.assertTrue(checksum_ok(frame))

    def test_a1_speed_uses_centidegrees_per_second(self):
        frame = build_a1_speed(yaw_speed=12.34, pitch_speed=-5.0, roll_speed=0.25)

        self.assertEqual(frame[4], FRAME_A1)
        self.assertEqual(frame[5], 0x01)
        self.assertEqual(int.from_bytes(frame[6:8], "big", signed=True), 1234)
        self.assertEqual(int.from_bytes(frame[8:10], "big", signed=True), -500)
        self.assertEqual(int.from_bytes(frame[10:12], "big", signed=True), 25)
        self.assertTrue(checksum_ok(frame))

    def test_a1_absolute_720_uses_document_angle_units(self):
        frame = build_a1_absolute_720(yaw=90.0, pitch=-45.0, roll=0.0, speed=30.0)

        self.assertEqual(frame[5], 0x1B)
        self.assertEqual(int.from_bytes(frame[6:8], "big", signed=True), 4096)
        self.assertEqual(int.from_bytes(frame[8:10], "big", signed=True), -2048)
        self.assertEqual(int.from_bytes(frame[12:14], "big", signed=True), 3000)
        self.assertTrue(checksum_ok(frame))

    def test_c1_and_c2_optical_control_frames(self):
        c1 = build_c1(operation=C1_START_RECORDING)
        c2 = build_c2(C2_ZOOM_TO, 42)
        defog = build_c2(C2_DEFOG_ON)

        self.assertEqual(c1[4], 0x1C)
        self.assertEqual(int.from_bytes(c1[5:7], "big"), C1_START_RECORDING << 6)
        self.assertEqual(c2[4:8], bytes([0x2C, C2_ZOOM_TO, 0x00, 0x2A]))
        self.assertEqual(defog[4:8], bytes([0x2C, C2_DEFOG_ON, 0x00, 0x00]))
        self.assertTrue(checksum_ok(c1))
        self.assertTrue(checksum_ok(c2))
        self.assertTrue(checksum_ok(defog))

    def test_digital_zoom_frames_select_sensor_for_shared_stop_opcode(self):
        eo_stop = build_eo_dzoom_stop_frame()
        ir_stop = build_ir_dzoom_stop_frame()
        ir_in = build_ir_dzoom_in_frame(5)
        ir_out = build_ir_dzoom_out_frame(5)

        self.assertEqual(eo_stop[5:7], bytes([0x0F, 0x01]))
        self.assertEqual(ir_stop[5:7], bytes([0x0F, 0x02]))
        self.assertEqual(ir_in[5:7], bytes([0x0F, 0x6A]))
        self.assertEqual(ir_out[5:7], bytes([0x0F, 0xAA]))
        self.assertTrue(all(checksum_ok(frame) for frame in (eo_stop, ir_stop, ir_in, ir_out)))

    def test_s1_look_at_scales_lat_lon_altitude(self):
        frame = build_s1_look_at(latitude=31.1234567, longitude=121.7654321, altitude=88.125)

        self.assertEqual(frame[4], 0x16)
        self.assertEqual(frame[5], 0x01)
        self.assertEqual(int.from_bytes(frame[7:11], "big", signed=True), 311234567)
        self.assertEqual(int.from_bytes(frame[11:15], "big", signed=True), 1217654321)
        self.assertEqual(int.from_bytes(frame[15:19], "big", signed=True), 88125)
        self.assertTrue(checksum_ok(frame))

    def test_screen_split_and_temperature_frames(self):
        split = build_screen_split_frame("visible_ir")
        once = build_temperature_output_once_frame()
        interval = build_temperature_output_interval_frame(3)
        stop = build_temperature_output_stop_frame()

        self.assertEqual(split[4], 0x1C)
        self.assertEqual(split[5:7], bytes([0x00, 0x03]))
        self.assertEqual(once[4:8], bytes([0x2C, C2_TEMPERATURE_OUTPUT_ONCE, 0x00, 0x00]))
        self.assertEqual(interval[4:8], bytes([0x2C, C2_TEMPERATURE_OUTPUT_INTERVAL, 0x00, 0x03]))
        self.assertEqual(stop[4:8], bytes([0x2C, C2_TEMPERATURE_OUTPUT_STOP, 0x00, 0x00]))
        self.assertTrue(all(checksum_ok(frame) for frame in (split, once, interval, stop)))

    def test_ir_metering_point_and_area_frames(self):
        point_frames = build_ir_metering_point_frames(100, -50)
        area_frames = build_ir_metering_area_frames(-200, -100, 200, 100)

        self.assertEqual(len(point_frames), 2)
        self.assertEqual(point_frames[0][4:10], bytes([0x2E, 0x0A, 0x00, 0x64, 0xFF, 0xCE]))
        self.assertEqual(point_frames[1][5], C2_TEMPERATURE_OUTPUT_ONCE)
        self.assertEqual(len(area_frames), 3)
        self.assertEqual(area_frames[0][4:6], bytes([0x2E, 0x0B]))
        self.assertEqual(area_frames[1][4:6], bytes([0x2E, 0x0C]))
        self.assertEqual(area_frames[2][5], C2_TEMPERATURE_OUTPUT_ONCE)
        self.assertTrue(all(checksum_ok(frame) for frame in point_frames + area_frames))

    def test_mapping_exposes_json_safe_protocol_hex(self):
        call = build_viewlink_call("turn_to", {"yaw": 90, "pitch": -45})

        self.assertEqual(call["protocol"]["version"], "3.4.9")
        self.assertEqual(call["protocol"]["frame_hex"], frame_hex(build_a1_absolute_720(90, -45, 0, 0)))
        self.assertIsInstance(call["protocol"]["frame_hex"], str)


if __name__ == "__main__":
    unittest.main()
