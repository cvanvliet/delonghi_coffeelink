"""Read-only cloud-session sensor attributes without a running HA instance."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "delonghi_coffeelink"


class _CoordinatorEntity:
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator


def _module(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    return module


@pytest.fixture(scope="module")
def sensor_module():
    # Import the real sensor code with only HA's framework types stubbed.
    # The coordinator is supplied by each test; no HA startup or device I/O occurs.
    package = "_delonghi_sensor_test"
    parent = _module(package)
    parent.__path__ = [str(PKG_DIR)]
    modules = {
        package: parent,
        f"{package}.coordinator": _module(
            f"{package}.coordinator", DelonghiCoordinator=object
        ),
        "homeassistant": _module("homeassistant"),
        "homeassistant.components": _module("homeassistant.components"),
        "homeassistant.components.sensor": _module(
            "homeassistant.components.sensor",
            SensorDeviceClass=SimpleNamespace(TIMESTAMP="timestamp"),
            SensorEntity=object,
            SensorStateClass=SimpleNamespace(TOTAL_INCREASING="total_increasing"),
        ),
        "homeassistant.config_entries": _module(
            "homeassistant.config_entries", ConfigEntry=object
        ),
        "homeassistant.const": _module(
            "homeassistant.const", EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic")
        ),
        "homeassistant.core": _module(
            "homeassistant.core", HomeAssistant=object, callback=lambda fn: fn
        ),
        "homeassistant.helpers": _module("homeassistant.helpers"),
        "homeassistant.helpers.device_registry": _module(
            "homeassistant.helpers.device_registry", DeviceInfo=dict
        ),
        "homeassistant.helpers.entity_platform": _module(
            "homeassistant.helpers.entity_platform", AddEntitiesCallback=object
        ),
        "homeassistant.helpers.update_coordinator": _module(
            "homeassistant.helpers.update_coordinator", CoordinatorEntity=_CoordinatorEntity
        ),
        "homeassistant.util": _module(
            "homeassistant.util", dt=SimpleNamespace(parse_datetime=lambda value: value)
        ),
    }
    spec = importlib.util.spec_from_file_location(f"{package}.sensor", PKG_DIR / "sensor.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


def _coordinator(app_id=0, task=None, confirmed=False, start=None, stop=None):
    return SimpleNamespace(
        device=SimpleNamespace(dsn="test-machine"),
        data={"app_id": {"value": app_id}},
        _session_cold_task=task,
        _session_confirmed=confirmed,
        learned_start_frames={} if start is None else start,
        learned_stop_frames={} if stop is None else stop,
    )


@pytest.mark.parametrize("task_done,expected", [(None, False), (False, True), (True, False)])
def test_session_connecting_tracks_pending_task(sensor_module, task_done, expected):
    task = None if task_done is None else Mock(done=Mock(return_value=task_done))
    coord = _coordinator(task=task)
    sensor = sensor_module.DelonghiCloudSessionAppIdSensor(coord)
    assert sensor.extra_state_attributes["session_connecting"] is expected


def test_session_attributes_follow_coordinator_without_changing_identity(sensor_module):
    coord = _coordinator()
    sensor = sensor_module.DelonghiCloudSessionAppIdSensor(coord)
    assert sensor._attr_unique_id == "test-machine_cloud_session_app_id"
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {
        "holder": "free",
        "session_connecting": False,
        "session_confirmed": False,
        "learned_start_count": 0,
        "learned_stop_count": 0,
        "app_id_hex": "00000000",
    }
    coord._session_confirmed = True
    coord.learned_start_frames.update({1: "private-frame-one", 2: "private-frame-two"})
    coord.learned_stop_frames[1] = "private-stop-frame"
    attrs = sensor.extra_state_attributes
    assert attrs["session_confirmed"] is True
    assert attrs["learned_start_count"] == 2
    assert attrs["learned_stop_count"] == 1
    assert "private" not in repr(attrs)
    assert coord.learned_start_frames == {1: "private-frame-one", 2: "private-frame-two"}
    assert coord.learned_stop_frames == {1: "private-stop-frame"}


@pytest.mark.parametrize(
    "app_id,holder,hex_value",
    [(None, "unknown", None), (0, "free", "00000000"),
     (0xC0FFEE11, "ha", "c0ffee11"), (123, "foreign", "0000007b")],
)
def test_existing_holder_and_hex_attributes_are_preserved(
    sensor_module, app_id, holder, hex_value
):
    sensor = sensor_module.DelonghiCloudSessionAppIdSensor(_coordinator(app_id=app_id))
    attrs = sensor.extra_state_attributes
    assert attrs["holder"] == holder
    if hex_value is None:
        assert "app_id_hex" not in attrs
    else:
        assert attrs["app_id_hex"] == hex_value
