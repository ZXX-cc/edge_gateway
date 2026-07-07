"""Dock3 物模型属性数据来源映射模块。"""

from .mapping import (
    PROPERTY_SOURCE_MAP,
    build_properties_from_osd,
    build_properties_from_sources,
    build_properties_from_internal_state,
    merge_properties,
)


__all__ = [
    "PROPERTY_SOURCE_MAP",
    "build_properties_from_osd",
    "build_properties_from_sources",
    "build_properties_from_internal_state",
    "merge_properties",
]
