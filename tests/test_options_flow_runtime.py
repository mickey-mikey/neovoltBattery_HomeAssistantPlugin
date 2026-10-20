"""Options-flow regressions using the installed Home Assistant runtime.

Run these in Linux with the real Home Assistant dependencies installed. Only
cloud polling, heartbeat timers and entity-platform forwarding are replaced;
HomeAssistant, ConfigEntry, OptionsFlow, their managers and reload are real.
The configuration directory is temporary and all credentials are synthetic.
"""
from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from homeassistant import config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import frame
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from homeassistant.setup import async_setup_component

import voluptuous as vol

from custom_components.bytewatt import coordinator as coordinator_module
from custom_components.bytewatt import const
import custom_components.bytewatt as bytewatt


class _OfflineClient:
    """A cloud boundary that must never reach a battery or real account."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.api_client = object()


@pytest.fixture
async def runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SimpleNamespace]:
    """Create an actual HA instance and isolate the integration's external IO."""
    (tmp_path / "custom_components").symlink_to(
        Path(__file__).resolve().parents[1] / "custom_components", target_is_directory=True
    )
    hass = HomeAssistant(str(tmp_path))
    hass.config.skip_pip = True
    loader.async_setup(hass)
    if hasattr(frame, "async_setup"):
        frame.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()

    # Keep the coordinator's real refresh/setup logic and interval machinery.
    # Replace only the method that would talk to the cloud, plus background
    # heartbeat scheduling and unrelated entity-platform setup.
    refresh = AsyncMock(return_value={})
    heartbeat = AsyncMock()
    forward_setup = AsyncMock()
    forward_unload = AsyncMock(return_value=True)
    monkeypatch.setattr(bytewatt, "ByteWattClient", _OfflineClient)
    monkeypatch.setattr(
        coordinator_module.ByteWattDataUpdateCoordinator, "_async_update_data", refresh
    )
    monkeypatch.setattr(
        coordinator_module.ByteWattDataUpdateCoordinator, "start_heartbeat", heartbeat
    )
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", forward_setup)
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_unload", forward_unload)
    assert await async_setup_component(hass, const.DOMAIN, {})

    yield SimpleNamespace(hass=hass, refresh=refresh, forward_setup=forward_setup)

    for entry in hass.config_entries.async_entries(const.DOMAIN):
        if entry.state is config_entries.ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop()


@pytest.fixture
def make_entry() -> Callable[..., config_entries.ConfigEntry]:
    """Construct genuine entries across the supported constructor versions."""
    def _make_entry(
        *, data: dict[str, Any] | None = None, options: dict[str, Any] | None = None
    ) -> config_entries.ConfigEntry:
        kwargs: dict[str, Any] = {
            "domain": const.DOMAIN,
            "title": "Offline options-flow regression",
            "data": {
                const.CONF_USERNAME: "offline@example.invalid",
                const.CONF_PASSWORD: "synthetic-test-password",
                const.CONF_HOST_SYSTEM_ID: "offline-host",
                const.CONF_HOST_SYS_SN: "offline-serial",
                **(data or {}),
            },
            "options": {const.CONF_RECOVERY_ENABLED: False, **(options or {})},
            "source": config_entries.SOURCE_USER,
            "version": const.CURRENT_ENTRY_VERSION,
            "minor_version": 1,
            "unique_id": "offline-options-flow-regression",
            "discovery_keys": MappingProxyType({}),
        }
        if "subentries_data" in inspect.signature(config_entries.ConfigEntry).parameters:
            kwargs["subentries_data"] = ()
        return config_entries.ConfigEntry(**kwargs)

    return _make_entry


@pytest.mark.parametrize("saved_interval", [None, 75])
async def test_options_form_uses_framework_entry(
    runtime: SimpleNamespace,
    make_entry: Callable[..., config_entries.ConfigEntry],
    saved_interval: int | None,
) -> None:
    """Open and serialize the form through HA's actual options-flow manager."""
    options = {} if saved_interval is None else {const.CONF_SCAN_INTERVAL: saved_interval}
    entry = make_entry(data={const.CONF_SCAN_INTERVAL: 125}, options=options)
    await runtime.hass.config_entries.async_add(entry)
    assert entry.state is config_entries.ConfigEntryState.LOADED

    manager = runtime.hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["handler"] == entry.entry_id

    # Use the installed HA HTTP view's actual form-payload serializer. This
    # also follows HA's serializer migration on newer supported releases.
    rendered = FlowManagerIndexView(manager)._prepare_result_json(result)
    fields = rendered["data_schema"]
    assert len(fields) == 1
    assert fields[0]["name"] == const.CONF_SCAN_INTERVAL
    assert fields[0]["default"] == (
        const.DEFAULT_SCAN_INTERVAL if saved_interval is None else saved_interval
    )
    json.dumps(fields)

    original_options = dict(entry.options)
    manager.async_abort(result["flow_id"])
    assert dict(entry.options) == original_options


async def test_save_reopens_and_reloads_coordinator(
    runtime: SimpleNamespace,
    make_entry: Callable[..., config_entries.ConfigEntry],
) -> None:
    """Saving real options reloads the entry and changes the real poll interval."""
    hass = runtime.hass
    entry = make_entry(data={const.CONF_SCAN_INTERVAL: 125})
    await hass.config_entries.async_add(entry)
    original_data = dict(entry.data)
    original_coordinator = hass.data[const.DOMAIN][entry.entry_id]["coordinator"]
    assert original_coordinator.update_interval == timedelta(seconds=125)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]
    assert schema({const.CONF_SCAN_INTERVAL: "30"}) == {const.CONF_SCAN_INTERVAL: 30}
    for invalid in (29, "not-a-number"):
        with pytest.raises(vol.Invalid):
            schema({const.CONF_SCAN_INTERVAL: invalid})
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"], user_input={const.CONF_SCAN_INTERVAL: invalid}
            )
        assert dict(entry.options) == {const.CONF_RECOVERY_ENABLED: False}

    # Let the actual flow manager validate and coerce the submitted form data.
    saved = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={const.CONF_SCAN_INTERVAL: "90"}
    )
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert saved["data"] == {const.CONF_SCAN_INTERVAL: 90}
    await hass.async_block_till_done()

    assert dict(entry.options) == {const.CONF_SCAN_INTERVAL: 90}
    assert dict(entry.data) == original_data
    assert entry.state is config_entries.ConfigEntryState.LOADED
    reloaded_coordinator = hass.data[const.DOMAIN][entry.entry_id]["coordinator"]
    assert reloaded_coordinator is not original_coordinator
    assert reloaded_coordinator.update_interval == timedelta(seconds=90)
    assert runtime.forward_setup.await_count == 2

    reopened = await hass.config_entries.options.async_init(entry.entry_id)
    assert reopened["data_schema"]({}) == {const.CONF_SCAN_INTERVAL: 90}
    hass.config_entries.options.async_abort(reopened["flow_id"])


@pytest.mark.parametrize(
    ("data", "options", "expected"),
    [
        ({}, {}, const.DEFAULT_SCAN_INTERVAL),
        ({const.CONF_SCAN_INTERVAL: 125}, {}, 125),
        ({const.CONF_SCAN_INTERVAL: 125}, {const.CONF_SCAN_INTERVAL: 75}, 75),
    ],
    ids=["default", "entry-data", "options-override-data"],
)
async def test_setup_preserves_scan_interval_precedence(
    runtime: SimpleNamespace,
    make_entry: Callable[..., config_entries.ConfigEntry],
    data: dict[str, Any],
    options: dict[str, Any],
    expected: int,
) -> None:
    """Actual entry setup carries the existing interval precedence into HA."""
    entry = make_entry(data=data, options=options)
    await runtime.hass.config_entries.async_add(entry)
    assert entry.state is config_entries.ConfigEntryState.LOADED
    coordinator = runtime.hass.data[const.DOMAIN][entry.entry_id]["coordinator"]
    assert isinstance(coordinator, coordinator_module.ByteWattDataUpdateCoordinator)
    assert coordinator.update_interval == timedelta(seconds=expected)
    runtime.refresh.assert_awaited_once()
