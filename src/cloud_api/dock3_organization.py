"""DJI Dock 3 organization request field catalog.

These methods are device-originated requests on:
thing/product/{gateway_sn}/requests -> requests_reply.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Mapping


@dataclass(frozen=True)
class OrganizationMethodSpec:
    method: str
    request_fields: FrozenSet[str]
    response_fields: FrozenSet[str]
    response_output_fields: FrozenSet[str]


ORGANIZATION_METHOD_SPECS: Mapping[str, OrganizationMethodSpec] = {
    "airport_bind_status": OrganizationMethodSpec(
        method="airport_bind_status",
        request_fields=frozenset({"data.devices[].sn"}),
        response_fields=frozenset({"data.result", "data.output.bind_status[]"}),
        response_output_fields=frozenset(
            {
                "bind_status[].sn",
                "bind_status[].is_device_bind_organization",
                "bind_status[].organization_id",
                "bind_status[].organization_name",
                "bind_status[].device_callsign",
            }
        ),
    ),
    "airport_organization_get": OrganizationMethodSpec(
        method="airport_organization_get",
        request_fields=frozenset(
            {
                "data.device_binding_code",
                "data.organization_id",
            }
        ),
        response_fields=frozenset({"data.result", "data.output.organization_name"}),
        response_output_fields=frozenset({"organization_name"}),
    ),
    "airport_organization_bind": OrganizationMethodSpec(
        method="airport_organization_bind",
        request_fields=frozenset(
            {
                "data.bind_devices[].sn",
                "data.bind_devices[].device_binding_code",
                "data.bind_devices[].organization_id",
                "data.bind_devices[].device_callsign",
                "data.bind_devices[].device_model_key",
            }
        ),
        response_fields=frozenset({"data.result", "data.output.err_infos[]"}),
        response_output_fields=frozenset(
            {
                "err_infos[].sn",
                "err_infos[].err_code",
                "err_infos[].err_msg",
            }
        ),
    ),
}


def organization_method_names() -> FrozenSet[str]:
    return frozenset(ORGANIZATION_METHOD_SPECS)


def organization_official_fields() -> FrozenSet[str]:
    fields = set()
    for spec in ORGANIZATION_METHOD_SPECS.values():
        fields.update(spec.request_fields)
        fields.update(spec.response_fields)
        fields.update(f"data.output.{field}" for field in spec.response_output_fields)
    return frozenset(fields)
