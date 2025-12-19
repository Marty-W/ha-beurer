"""Config flow for Beurer TL100 integration."""
from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.const import CONF_MAC
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.device_registry import format_mac

from .const import DOMAIN, LOGGER
from .beurer import BeurerInstance

MANUAL_MAC = "manual"


class BeurerFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Beurer."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._address: str | None = None
        self._name: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        
        self._discovery_info = discovery_info
        self._address = discovery_info.address
        self._name = discovery_info.name or f"Beurer TL100 {discovery_info.address[-5:]}"
        
        self.context["title_placeholders"] = {"name": self._name}
        
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm the setup."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_MAC: self._address,
                    "name": self._name,
                },
            )

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._name},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the user step to pick discovered device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input["device"]
            
            if address == MANUAL_MAC:
                return await self.async_step_manual()

            await self.async_set_unique_id(format_mac(address))
            self._abort_if_unique_id_configured()

            self._address = address
            self._name = user_input.get("name") or self._discovered_devices[address].name
            
            return await self.async_step_validate()

        # Find Beurer devices via HA's bluetooth manager
        current_addresses = self._async_current_ids()
        
        for discovery_info in async_discovered_service_info(self.hass, connectable=True):
            if discovery_info.name and discovery_info.name.lower().startswith("tl100"):
                if format_mac(discovery_info.address) not in current_addresses:
                    self._discovered_devices[discovery_info.address] = discovery_info

        if not self._discovered_devices:
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(
                        {
                            **{
                                address: f"{info.name} ({address})"
                                for address, info in self._discovered_devices.items()
                            },
                            MANUAL_MAC: "Manually enter MAC address",
                        }
                    ),
                    vol.Optional("name"): str,
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual MAC entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._address = user_input["mac"]
            self._name = user_input.get("name", f"Beurer TL100")
            
            await self.async_set_unique_id(format_mac(self._address))
            self._abort_if_unique_id_configured()
            
            return await self.async_step_validate()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required("mac"): str,
                    vol.Optional("name", default="Beurer TL100"): str,
                }
            ),
            errors=errors,
        )

    async def async_step_validate(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Validate the connection by toggling the light."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("flicker"):
                return self.async_create_entry(
                    title=self._name,
                    data={
                        CONF_MAC: self._address,
                        "name": self._name,
                    },
                )
            if user_input.get("retry"):
                # Try again
                pass
            else:
                return self.async_abort(reason="cannot_connect")

        # Try to connect and toggle
        instance = BeurerInstance(self.hass, self._address, self._name)
        
        try:
            LOGGER.debug("Validating connection to %s", self._address)
            
            # Try to connect and get status
            await instance.update()
            await asyncio.sleep(1)
            
            # Toggle the light
            if instance.is_on:
                await instance.turn_off()
                await asyncio.sleep(1.5)
                await instance.turn_on()
            else:
                await instance.turn_on()
                await asyncio.sleep(1.5)
                await instance.turn_off()
            
            await asyncio.sleep(0.5)
            
        except Exception as error:
            LOGGER.error("Validation failed: %s", error)
            errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="validate",
                data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
                errors=errors,
                description_placeholders={"error": str(error)},
            )
        finally:
            await instance.disconnect()

        # Ask user if they saw the light flicker
        return self.async_show_form(
            step_id="validate",
            data_schema=vol.Schema({vol.Required("flicker"): bool}),
            errors=errors,
            description_placeholders={"name": self._name},
        )
