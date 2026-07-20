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

        self.assertEqual(defog["args"]["on"], True)
        self.assertEqual(laser["args"]["on"], False)
        self.assertEqual(tracking["sdk_method"], "is_tracking")


if __name__ == "__main__":
    unittest.main()
