"""valetudo vacuum camera"""
import logging

from homeassistant import config_entries, core
from homeassistant.const import Platform

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA]


async def options_update_listener(
    hass: core.HomeAssistant, config_entry: config_entries.ConfigEntry
):
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_migrate_entry(hass, config_entry: config_entries.ConfigEntry):
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1.2:
        new_data = {**config_entry.data}
        _LOGGER.debug(new_data)
        new_data.update({"trim_top": "0"})
        new_data.update({"trim_bottom": "0"})
        new_data.update({"trim_left": "0"})
        new_data.update({"trim_right": "0"})
        new_data.update({"show_vac_status": False})
        new_data.update({"color_text": [255, 255, 255]})
        _LOGGER.debug(new_data)
        new_options = {**config_entry.options}
        _LOGGER.debug(new_options)
        if new_options or len(new_options) > 0:
            new_options.update({"trim_top": "0"})
            new_options.update({"trim_bottom": "0"})
            new_options.update({"trim_left": "0"})
            new_options.update({"trim_right": "0"})
            new_options.update({"show_vac_status": False})
            new_options.update({"color_text": [255, 255, 255]})
        else:
            new_options = new_data
        _LOGGER.debug(new_options)

        config_entry.version = 1.3
        hass.config_entries.async_update_entry(config_entry, data=new_data)
        hass.config_entries.async_update_entry(config_entry, options=new_options)

    _LOGGER.info("Migration to version %s successful", config_entry.version)
    return True


async def async_setup_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Set up platform from a ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})
    hass_data = dict(entry.data)
    # Registers update listener to update config entry when options are updated.
    unsub_options_update_listener = entry.add_update_listener(options_update_listener)
    # Store a reference to the unsubscribe function to clean up if an entry is unloaded.
    hass_data["unsub_options_update_listener"] = unsub_options_update_listener
    hass.data[DOMAIN][entry.entry_id] = hass_data

    # Forward the setup to the sensor platform.
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "camera")
    )
    return True


async def async_unload_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Remove config entry from domain.
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        entry_data["unsub_options_update_listener"]()

    return unload_ok


async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Set up the Valetudo Camera Custom component from yaml configuration."""
    hass.data.setdefault(DOMAIN, {})
    return True