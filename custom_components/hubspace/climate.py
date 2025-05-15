from functools import partial

from aioafero.v1 import AferoBridgeV1
from aioafero.v1.controllers.event import EventType
from aioafero.v1.controllers.thermostat import ThermostatController
from aioafero.v1.models import Thermostat
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TEMPERATURE,
    FAN_OFF,
    FAN_ON,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import HubspaceBridge
from .const import DOMAIN
from .entity import HubspaceBaseEntity, update_decorator


class HubspaceThermostat(HubspaceBaseEntity, ClimateEntity):
    def __init__(
        self,
        bridge: HubspaceBridge,
        controller: ClimateEntity,
        resource: Thermostat,
    ) -> None:
        super().__init__(bridge, controller, resource)
        self._supported_fan: list[str] = []
        self._supported_hvac_modes: list[HVACMode]
        self._supported_features: ClimateEntityFeature = ClimateEntityFeature(0)
        if self.resource.target_temperature:
            self._supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self.resource.supports_fan_mode:
            self._supported_features |= ClimateEntityFeature.FAN_MODE
        if self.resource.supports_temperature_range:
            self._supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE

        # Map specific air conditioner functions
        self._fan_speed_mapping = {
            "fan-speed-auto": FAN_ON,
            "fan-speed-2-050": "low",
            "fan-speed-2-100": "high",
        }
        self._hvac_mode_mapping = {
            "cool": HVACMode.COOL,
            "auto-cool": HVACMode.HEAT_COOL,
            "fan": HVACMode.FAN_ONLY,
            "off": HVACMode.OFF,
        }
        self._error_states = {
            "indoor-temperature-sensor-failed": "Indoor temperature sensor failed",
            "water-tray-full": "Water tray full",
        }

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        attributes = {
            "error_states": [
                self._error_states.get(state.functionInstance, state.value)
                for state in self.resource.states
                if state.functionClass == "error" and state.value == "alerting"
            ],
            "sleep_mode": self.resource.states.get("sleep", {}).get("value", "off"),
            "timer": self.resource.states.get("timer", {}).get("value", 0),
        }
        # Add debug logging for error states
        if attributes["error_states"]:
            self.logger.debug("Error states detected: %s", attributes["error_states"])
        return attributes

    @property
    def current_temperature(self) -> float | None:
        return self.resource.current_temperature

    @property
    def fan_mode(self) -> str | None:
        return self._fan_speed_mapping.get(self.resource.fan_mode.mode, FAN_OFF)

    @property
    def fan_modes(self) -> list[str] | None:
        return list(self._fan_speed_mapping.values())

    @property
    def hvac_action(self) -> HVACAction | None:
        mapping = {
            "cooling": HVACAction.COOLING,
            "heating": HVACAction.HEATING,
            "off": HVACAction.OFF,
        }
        mapped = mapping.get(self.resource.hvac_action)
        if mapped:
            return mapped
        elif self.resource.hvac_mode.mode == "fan":
            return HVACAction.FAN
        else:
            return self.resource.hvac_action

    @property
    def hvac_mode(self) -> HVACMode | None:
        return self._hvac_mode_mapping.get(self.resource.hvac_mode.mode, HVACMode.OFF)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        return list(self._hvac_mode_mapping.values())

    @property
    def max_temp(self) -> float | None:
        return self.resource.target_temperature_max

    @property
    def min_temp(self) -> float | None:
        return self.resource.target_temperature_min

    @property
    def supported_features(self):
        return self._supported_features

    @property
    def target_temperature(self) -> float | None:
        return self.resource.target_temperature

    @property
    def target_temperature_high(self) -> float | None:
        return self.resource.target_temperature_range[1]

    @property
    def target_temperature_low(self) -> float | None:
        return self.resource.target_temperature_range[0]

    @property
    def target_temperature_step(self) -> float | None:
        return self.resource.target_temperature_step

    @property
    def temperature_unit(self) -> str:
        # Hubspace always returns in C
        return UnitOfTemperature.CELSIUS

    @update_decorator
    async def translate_hvac_mode_to_hubspace(self, hvac_mode) -> str | None:
        """Convert HomeAssistant -> Hubspace"""
        tracked_modes = {
            HVACMode.OFF: "off",
            HVACMode.HEAT: "heat",
            HVACMode.COOL: "cool",
            HVACMode.FAN_ONLY: "fan",
            HVACMode.HEAT_COOL: "auto",
        }
        return tracked_modes.get(hvac_mode)

    @update_decorator
    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set new hvac mode."""
        hubspace_mode = next((k for k, v in self._hvac_mode_mapping.items() if v == hvac_mode), None)
        if hubspace_mode:
            await self.bridge.async_request_call(
                self.controller.set_state,
                device_id=self.resource.id,
                hvac_mode=hubspace_mode,
            )

    @update_decorator
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new fan mode."""
        hubspace_mode = next((k for k, v in self._fan_speed_mapping.items() if v == fan_mode), None)
        if hubspace_mode:
            await self.bridge.async_request_call(
                self.controller.set_state,
                device_id=self.resource.id,
                fan_mode=hubspace_mode,
            )

    @update_decorator
    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            target_temperature=kwargs.get(ATTR_TEMPERATURE),
            target_temperature_auto_cooling=kwargs.get(ATTR_TARGET_TEMP_HIGH),
            target_temperature_auto_heating=kwargs.get(ATTR_TARGET_TEMP_LOW),
            hvac_mode=await self.translate_hvac_mode_to_hubspace(
                kwargs.get(ATTR_HVAC_MODE)
            ),
        )

    @update_decorator
    async def async_set_sleep_mode(self, sleep_mode: str) -> None:
        """Set sleep mode."""
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            sleep_mode=sleep_mode,
        )

    @update_decorator
    async def async_set_timer(self, timer: int) -> None:
        """Set timer."""
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            timer=timer,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities."""
    bridge: HubspaceBridge = hass.data[DOMAIN][config_entry.entry_id]
    api: AferoBridgeV1 = bridge.api
    controller: ThermostatController = api.thermostats
    make_entity = partial(HubspaceThermostat, bridge, controller)

    @callback
    def async_add_entity(event_type: EventType, resource: Thermostat) -> None:
        """Add an entity."""
        self.logger.debug("Adding entity for resource: %s", resource)
        async_add_entities([make_entity(resource)])

    # add all current items in controller
    async_add_entities(make_entity(entity) for entity in controller)
    # register listener for new entities
    config_entry.async_on_unload(
        controller.subscribe(async_add_entity, event_filter=EventType.RESOURCE_ADDED)
    )
