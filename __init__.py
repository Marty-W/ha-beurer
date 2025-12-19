"""Beurer TL100 BLE light integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.components.bluetooth import async_ble_device_from_address

from .const import DOMAIN, LOGGER
from .beurer import BeurerInstance

PLATFORMS = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Beurer from a config entry."""
    address = entry.data[CONF_MAC]
    name = entry.data.get("name", "Beurer TL100")
    
    LOGGER.debug("Setting up Beurer device: %s (%s)", name, address)
    
    # Verify the device is reachable
    device = async_ble_device_from_address(hass, address, connectable=True)
    if not device:
        raise ConfigEntryNotReady(f"Device {address} not found")
    
    # Create the instance (does NOT connect automatically)
    instance = BeurerInstance(hass, address, name)
    
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = instance
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        instance: BeurerInstance = hass.data[DOMAIN].pop(entry.entry_id)
        await instance.disconnect()
    return unload_ok
