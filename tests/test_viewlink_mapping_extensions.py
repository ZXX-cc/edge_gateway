import unittest

from web.mavlink.viewlink import build_viewlink_call


class ViewLinkMappingExtensionsTest(unittest.TestCase):
    def test_focus_and_zoom_extension_mapping(self):
        focus = build_viewlink_call("set_focus_mode", {"mode": "manual_focus"})
        zoom = build_viewlink_call("zoom_to", {"magnification": 4.2})

        self.assertEqual(focus["sdk_method"], "set_focus_mode")
        self.assertEqual(focus["args"]["mode"], 1)
        self.assertEqual(zoom["sdk_method"], "zoom_to")
        self.assertEqual(zoom["args"]["magnification"], 4.2)

    def test_defog_laser_and_status_mapping(self):
        defog = build_viewlink_call("switch_defog", {"on": "true"})
        laser = build_viewlink_call("switch_laser", {"on": False})
        tracking = build_viewlink_call("is_tracking", {})
        motion = build_viewlink_call("motion_status", {})
        motor = build_viewlink_call("switch_motor", {"on": "true"})
        follow = build_viewlink_call("enable_follow_mode", {"enable": False})

        self.assertEqual(defog["args"]["on"], True)
        self.assertEqual(laser["args"]["on"], False)
        self.assertEqual(tracking["sdk_method"], "is_tracking")
        self.assertEqual(motion["sdk_method"], "get_motion_status")
        self.assertEqual(motor["sdk_method"], "switch_motor")
        self.assertEqual(motor["args"]["on"], True)
        self.assertEqual(follow["sdk_method"], "enable_follow_mode")
        self.assertEqual(follow["args"]["enable"], False)

    def test_image_color_mapping(self):
        image = build_viewlink_call("set_image_color", {
            "image_type": "IR1",
            "enable_pip": "true",
            "ir_color": "black_hot",
        })

        self.assertEqual(image["sdk_method"], "set_image_color")
        self.assertEqual(image["args"]["image_type"], 2)
        self.assertEqual(image["args"]["enable_pip"], True)
        self.assertEqual(image["args"]["ir_color"], 1)

    def test_digital_zoom_stop_mapping_uses_sdk_methods_only(self):
        eo = build_viewlink_call("eo_digital_zoom_stop", {})
        ir = build_viewlink_call("ir_digital_zoom_stop", {})
        ir_in = build_viewlink_call("ir_digital_zoom_in", {"speed": 5})

        self.assertEqual(eo["sdk_method"], "eo_digital_zoom_stop")
        self.assertNotIn("protocol", eo)
        self.assertEqual(ir["sdk_method"], "ir_digital_zoom_stop")
        self.assertNotIn("protocol", ir)
        self.assertEqual(ir_in["sdk_method"], "ir_digital_zoom_in")
        self.assertEqual(ir_in["args"]["speed"], 5)
        self.assertNotIn("protocol", ir_in)


if __name__ == "__main__":
    unittest.main()
