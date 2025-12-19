"""Light platform for Beurer TL100."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.color import match_max_scale

from .beurer import BeurerInstance
from .const import DOMAIN, LOGGER


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Beurer light platform."""
    LOGGER.debug("Setting up Beurer light entity")
    instance: BeurerInstance = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([BeurerLight(instance, config_entry)])


class BeurerLight(LightEntity):
    """Representation of a Beurer TL100 light."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes = {ColorMode.RGB, ColorMode.WHITE}
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, instance: BeurerInstance, config_entry: ConfigEntry) -> None:
        """Initialize the light entity."""
        self._instance = instance
        self._attr_unique_id = instance.address
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, instance.address)},
            name=instance.name,
            manufacturer="Beurer",
            model="TL100",
            connections={(dr.CONNECTION_BLUETOOTH, instance.address)},
        )

    async def async_added_to_hass(self) -> None:
        """Handle being added to hass."""
        self._instance.set_update_callback(self._handle_update)
        # Schedule initial state update - don't block startup
        self.hass.async_create_task(self._async_initial_update())

    async def _async_initial_update(self) -> None:
        """Perform initial update in background."""
        try:
            await self._instance.update()
        except Exception as err:
            LOGGER.warning("Initial update failed, will retry on next interaction: %s", err)

    def _handle_update(self) -> None:
        """Handle state updates from the device."""
        self.schedule_update_ha_state(False)

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._instance.available

    @property
    def should_poll(self) -> bool:
        """Return False - we get push updates."""
        return False

    @property
    def is_on(self) -> bool:
        """Return True if the light is on."""
        return self._instance.is_on

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light."""
        if self._instance.color_mode == ColorMode.WHITE:
            return self._instance.white_brightness
        return self._instance.color_brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the RGB color."""
        if self._instance.rgb_color:
            return match_max_scale((255,), self._instance.rgb_color)
        return None

    @property
    def effect(self) -> str | None:
        """Return the current effect."""
        if self._instance.color_mode == ColorMode.WHITE:
            return "Off"
        return self._instance.effect

    @property
    def effect_list(self) -> list[str]:
        """Return the list of supported effects."""
        return self._instance.supported_effects

    @property
    def color_mode(self) -> ColorMode:
        """Return the current color mode."""
        return self._instance.color_mode

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        LOGGER.debug("Turn on with args: %s", kwargs)

        if not kwargs:
            await self._instance.turn_on()
            return

        if ATTR_BRIGHTNESS in kwargs:
            await self._instance.set_white(kwargs[ATTR_BRIGHTNESS])

        if ATTR_RGB_COLOR in kwargs:
            await self._instance.set_color(kwargs[ATTR_RGB_COLOR])

        if ATTR_EFFECT in kwargs:
            await self._instance.set_effect(kwargs[ATTR_EFFECT])

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        await self._instance.turn_off()

    async def async_update(self) -> None:
        """Update the light state."""
        await self._instance.update()
