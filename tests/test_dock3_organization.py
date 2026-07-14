import unittest

from src.cloud_api.dock3_organization import (
    ORGANIZATION_METHOD_SPECS,
    organization_method_names,
    organization_official_fields,
)


class Dock3OrganizationCatalogTest(unittest.TestCase):
    def test_catalog_lists_official_organization_methods(self):
        self.assertEqual(
            organization_method_names(),
            {
                "airport_bind_status",
                "airport_organization_get",
                "airport_organization_bind",
            },
        )

    def test_catalog_covers_bind_status_fields(self):
        spec = ORGANIZATION_METHOD_SPECS["airport_bind_status"]
        self.assertIn("data.devices[].sn", spec.request_fields)
        self.assertIn("bind_status[].is_device_bind_organization", spec.response_output_fields)
        self.assertIn("bind_status[].device_callsign", spec.response_output_fields)

    def test_catalog_covers_organization_bind_error_fields(self):
        fields = organization_official_fields()
        self.assertIn("data.bind_devices[].device_model_key", fields)
        self.assertIn("data.output.err_infos[].err_code", fields)
        self.assertNotIn("data.output.err_infos[].err_msg", fields)


if __name__ == "__main__":
    unittest.main()
