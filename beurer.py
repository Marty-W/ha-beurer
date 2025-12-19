"""Beurer TL100 BLE light control."""
from __future__ import annotations

from typing import Tuple, Callable
import asyncio
import traceback

from bleak import BleakClient, BleakGATTCharacteristic, BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection, BleakClientWithServiceCache

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_scanner_count,
)
from homeassistant.components.light import ColorMode
from homeassistant.core import HomeAssistant

from .const import LOGGER

WRITE_CHARACTERISTIC_UUIDS = ["8b00ace7-eb0b-49b0-bbe9-9aee0a26e1a3"]
READ_CHARACTERISTIC_UUIDS = ["0734594a-a8e7-4b1a-a6b1-cd5243059a57"]


class BeurerInstance:
    """Represents a Beurer TL100 light device."""

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        """Initialize the Beurer instance."""
        self._hass = hass
        self._address = address
        self._name = name
        self._client: BleakClient | None = None
        self._device: BLEDevice | None = None
        self._trigger_update: Callable | None = None
        self._is_on = False
        self._light_on = False
        self._color_on = False
        self._rgb_color = (255, 255, 255)
        self._brightness = 255
        self._color_brightness = 255
        self._effect = "Off"
        self._write_uuid: str | None = None
        self._read_uuid: str | None = None
        self._mode = ColorMode.WHITE
        self._supported_effects = [
            "Off", "Random", "Rainbow", "Rainbow Slow", "Fusion",
            "Pulse", "Wave", "Chill", "Action", "Forest", "Summer"
        ]
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._expected_disconnect = False

    def _disconnected_callback(self, client: BleakClient) -> None:
        """Handle disconnection."""
        # Only log and act if we had an actual connection
        if self._write_uuid is not None:
            LOGGER.debug("Disconnected from %s", self._address)
            self._write_uuid = None
            self._read_uuid = None
            
            if not self._expected_disconnect:
                self._is_on = False
                self._light_on = False
                self._color_on = False
                if self._trigger_update:
                    self._hass.loop.call_soon_threadsafe(self._trigger_update)
        
        self._client = None

    def set_update_callback(self, trigger_update: Callable) -> None:
        """Set the callback for state updates."""
        LOGGER.debug("Setting update callback")
        self._trigger_update = trigger_update

    @property
    def address(self) -> str:
        """Return the device address."""
        return self._address

    @property
    def name(self) -> str:
        """Return the device name."""
        return self._name

    @property
    def is_on(self) -> bool:
        """Return True if the light is on."""
        return self._is_on

    @property
    def rgb_color(self) -> Tuple[int, int, int]:
        """Return the RGB color."""
        return self._rgb_color

    @property
    def color_brightness(self) -> int:
        """Return the color brightness."""
        return self._color_brightness or 255

    @property
    def white_brightness(self) -> int:
        """Return the white brightness."""
        return self._brightness or 255

    @property
    def effect(self) -> str | None:
        """Return the current effect."""
        return self._effect

    @property
    def color_mode(self) -> ColorMode:
        """Return the color mode."""
        return self._mode

    @property
    def supported_effects(self) -> list[str]:
        """Return supported effects."""
        return self._supported_effects

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return async_scanner_count(self._hass, connectable=True) > 0

    def _find_effect_position(self, effect: str | None) -> int:
        """Find the position of an effect in the list."""
        if effect is None:
            return 0
        try:
            return self._supported_effects.index(effect)
        except ValueError:
            return 0

    @staticmethod
    def _make_checksum(length: int, data: list[int]) -> int:
        """Calculate checksum for packet."""
        result = length
        for byte in data:
            result ^= byte
        return result

    def _build_packet(self, message: list[int]) -> bytes:
        """Build a protocol packet."""
        length = len(message)
        checksum = self._make_checksum(length + 2, message)
        packet = [0xFE, 0xEF, 0x0A, length + 7, 0xAB, 0xAA, length + 2] + message + [checksum, 0x55, 0x0D, 0x0A]
        return bytes(packet)

    async def _ensure_connected(self) -> bool:
        """Ensure we have an active connection."""
        if self._client and self._client.is_connected:
            return True

        async with self._connect_lock:
            # Double-check after acquiring lock
            if self._client and self._client.is_connected:
                return True

            try:
                # Get the device from HA's bluetooth manager
                self._device = async_ble_device_from_address(
                    self._hass, self._address, connectable=True
                )
                
                if not self._device:
                    LOGGER.error("Device %s not found by bluetooth manager", self._address)
                    return False

                LOGGER.debug("Connecting to %s", self._address)
                
                self._client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._device,
                    self._name,
                    self._disconnected_callback,
                    max_attempts=2,
                    use_services_cache=True,
                )

                # Find characteristics
                for char in self._client.services.characteristics.values():
                    if char.uuid in WRITE_CHARACTERISTIC_UUIDS:
                        self._write_uuid = char.uuid
                    if char.uuid in READ_CHARACTERISTIC_UUIDS:
                        self._read_uuid = char.uuid

                if not self._read_uuid or not self._write_uuid:
                    LOGGER.error("Required characteristics not found")
                    await self._client.disconnect()
                    self._client = None
                    return False

                LOGGER.info("Connected to %s, starting notifications", self._address)
                
                # Start notifications
                await self._client.start_notify(self._read_uuid, self._notification_handler)
                
                return True

            except BleakError as error:
                LOGGER.error("Failed to connect to %s: %s", self._address, error)
                self._client = None
                return False
            except Exception as error:
                LOGGER.error("Unexpected error connecting to %s: %s", self._address, error)
                LOGGER.debug(traceback.format_exc())
                self._client = None
                return False

    async def _write(self, data: bytes) -> bool:
        """Write data to the device."""
        async with self._operation_lock:
            if not await self._ensure_connected():
                return False

            try:
                LOGGER.debug("Writing: %s", data.hex(' '))
                await self._client.write_gatt_char(self._write_uuid, data)
                return True
            except BleakError as error:
                LOGGER.warning("Error writing to device: %s", error)
                return False

    async def _send_packet(self, message: list[int]) -> bool:
        """Send a packet to the device."""
        packet = self._build_packet(message)
        return await self._write(packet)

    async def _notification_handler(self, characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
        """Handle notifications from the device."""
        LOGGER.debug("Notification: %s", data.hex(' '))
        
        if len(data) < 9:
            return

        reply_version = data[8]
        LOGGER.debug("Reply version: %d", reply_version)

        if reply_version == 1:
            # White mode status
            self._light_on = data[9] == 1
            if self._light_on:
                self._brightness = int(data[10] * 255 / 100) if data[10] > 0 else 255
                self._mode = ColorMode.WHITE
            LOGGER.debug("White mode - on: %s, brightness: %s", self._light_on, self._brightness)

        elif reply_version == 2:
            # Color mode status
            self._color_on = data[9] == 1
            if self._color_on:
                self._mode = ColorMode.RGB
                if len(data) > 16:
                    self._effect = self._supported_effects[min(data[16], len(self._supported_effects) - 1)]
            self._color_brightness = int(data[10] * 255 / 100) if data[10] > 0 else 255
            if len(data) >= 16:
                self._rgb_color = (data[13], data[14], data[15])
            self._is_on = self._light_on or self._color_on
            LOGGER.debug("Color mode - on: %s, brightness: %s, rgb: %s, effect: %s",
                        self._color_on, self._color_brightness, self._rgb_color, self._effect)
            
            # Trigger update after receiving full status
            if self._trigger_update:
                self._trigger_update()

        elif reply_version == 255:
            # Device off
            self._is_on = False
            self._light_on = False
            self._color_on = False
            LOGGER.debug("Device off")
            if self._trigger_update:
                self._trigger_update()

        elif reply_version == 0:
            # Device shutting down
            LOGGER.debug("Device shutting down")
            self._expected_disconnect = True
            await self.disconnect()

    async def _trigger_status(self) -> None:
        """Request status from device."""
        await self._send_packet([0x30, 0x01])  # White status
        await asyncio.sleep(0.2)
        await self._send_packet([0x30, 0x02])  # Color status

    async def set_color(self, rgb: Tuple[int, int, int]) -> None:
        """Set the RGB color."""
        r, g, b = rgb
        LOGGER.debug("Setting color: %s, %s, %s", r, g, b)
        self._mode = ColorMode.RGB
        self._rgb_color = rgb
        
        if not self._color_on:
            await self.turn_on()
        
        await self._send_packet([0x32, r, g, b])
        await asyncio.sleep(0.1)
        await self._trigger_status()

    async def set_color_brightness(self, brightness: int) -> None:
        """Set the color brightness."""
        LOGGER.debug("Setting color brightness: %s", brightness)
        self._mode = ColorMode.RGB
        
        if not self._color_on:
            await self.turn_on()
        
        await self._send_packet([0x31, 0x02, int(brightness / 255 * 100)])
        await asyncio.sleep(0.1)
        await self._trigger_status()

    async def set_white(self, intensity: int) -> None:
        """Set white mode brightness."""
        LOGGER.debug("Setting white intensity: %s", intensity)
        self._mode = ColorMode.WHITE
        
        if not self._light_on:
            await self.turn_on()
        
        await self._send_packet([0x31, 0x01, int(intensity / 255 * 100)])
        await asyncio.sleep(0.2)
        await self._trigger_status()

    async def set_effect(self, effect: str) -> None:
        """Set the light effect."""
        LOGGER.debug("Setting effect: %s", effect)
        self._mode = ColorMode.RGB
        self._effect = effect
        
        if not self._color_on:
            await self.turn_on()
        
        await self._send_packet([0x34, self._find_effect_position(effect)])
        await self._trigger_status()

    async def turn_on(self) -> None:
        """Turn on the light."""
        LOGGER.debug("Turning on (mode: %s)", self._mode)
        
        if self._mode == ColorMode.WHITE:
            await self._send_packet([0x37, 0x01])
        else:
            await self._send_packet([0x37, 0x02])
            
            # Restore color state if needed
            if not self._color_on:
                self._color_on = True
                await asyncio.sleep(0.2)
                if self._effect:
                    await self._send_packet([0x34, self._find_effect_position(self._effect)])
                await asyncio.sleep(0.2)
                r, g, b = self._rgb_color
                await self._send_packet([0x32, r, g, b])
                if self._color_brightness:
                    await self._send_packet([0x31, 0x02, int(self._color_brightness / 255 * 100)])
        
        await asyncio.sleep(0.2)
        await self._trigger_status()

    async def turn_off(self) -> None:
        """Turn off the light."""
        LOGGER.debug("Turning off")
        await self._send_packet([0x35, 0x01])  # Off white
        await self._send_packet([0x35, 0x02])  # Off color
        await asyncio.sleep(0.1)
        await self._trigger_status()

    async def update(self) -> None:
        """Update the device state."""
        if not await self._ensure_connected():
            LOGGER.warning("Cannot update: not connected")
            return
        
        LOGGER.debug("Requesting status update")
        await self._trigger_status()

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        LOGGER.debug("Disconnecting from %s", self._address)
        self._expected_disconnect = True
        
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except BleakError:
                pass
        
        self._client = None
        self._expected_disconnect = False
