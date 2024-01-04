"""
Camera Version 1.5.5
Valetudo Firmwares Vacuums maps.
for Valetudo Hypfer and rand256 maps.
From PI4 up to all other Home Assistant supported platforms.
"""

from __future__ import annotations

import logging
import os
import json
# import threading
import psutil_home_assistant as proc_insp
from io import BytesIO
from datetime import datetime, timedelta
from PIL import Image
from typing import Optional
import voluptuous as vol

from homeassistant.components.camera import Camera, PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME, CONF_UNIQUE_ID
from homeassistant import core, config_entries
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
    HomeAssistantType,
)

from custom_components.valetudo_vacuum_camera.valetudo.MQTT.connector import (
    ValetudoConnector,
)
from .valetudo.image_handler import (
    MapImageHandler,
)
from .valetudo.valetudore.image_handler import (
    ReImageHandler,
)
from .utils.colors_man import (
    add_alpha_to_rgb,
)
from .snapshots.snapshot import Snapshots
from .valetudo.vacuum import Vacuum
from .const import (
    CONF_VACUUM_CONNECTION_STRING, CONF_VACUUM_ENTITY_ID, CONF_VACUUM_IDENTIFIERS,
    CONF_VAC_STAT, CONF_SNAPSHOTS_ENABLE,
    DEFAULT_NAME, DOMAIN, PLATFORMS,
    ATTR_ROTATE, ATTR_MARGINS,
    COLOR_WALL, COLOR_ZONE_CLEAN, COLOR_ROBOT, COLOR_BACKGROUND,
    COLOR_MOVE, COLOR_CHARGER, COLOR_TEXT, COLOR_NO_GO,
    COLOR_GO_TO, COLOR_ROOM_0, COLOR_ROOM_1, COLOR_ROOM_2,
    COLOR_ROOM_3, COLOR_ROOM_4, COLOR_ROOM_5, COLOR_ROOM_6,
    COLOR_ROOM_7, COLOR_ROOM_8, COLOR_ROOM_9, COLOR_ROOM_10,
    COLOR_ROOM_11, COLOR_ROOM_12, COLOR_ROOM_13, COLOR_ROOM_14,
    COLOR_ROOM_15,
    ALPHA_WALL, ALPHA_ZONE_CLEAN, ALPHA_ROBOT, ALPHA_BACKGROUND,
    ALPHA_MOVE, ALPHA_CHARGER, ALPHA_TEXT, ALPHA_NO_GO,
    ALPHA_GO_TO, ALPHA_ROOM_0, ALPHA_ROOM_1, ALPHA_ROOM_2,
    ALPHA_ROOM_3, ALPHA_ROOM_4, ALPHA_ROOM_5, ALPHA_ROOM_6,
    ALPHA_ROOM_7, ALPHA_ROOM_8, ALPHA_ROOM_9, ALPHA_ROOM_10,
    ALPHA_ROOM_11, ALPHA_ROOM_12, ALPHA_ROOM_13, ALPHA_ROOM_14,
    ALPHA_ROOM_15
)
from custom_components.valetudo_vacuum_camera.common import get_vacuum_unique_id_from_mqtt_topic

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_VACUUM_CONNECTION_STRING): cv.string,
        vol.Required(CONF_VACUUM_ENTITY_ID): cv.string,
        vol.Required(ATTR_ROTATE, default="0"): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.entity_id,
    }
)
SCAN_INTERVAL = timedelta(seconds=3)
_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
        hass: core.HomeAssistant,
        config_entry: config_entries.ConfigEntry,
        async_add_entities,
) -> None:
    """Setup camera from a config entry created in the integrations UI."""
    config = hass.data[DOMAIN][config_entry.entry_id]
    # Update our config to and eventually add or remove option.
    if config_entry.options:
        config.update(config_entry.options)

    camera = [ValetudoCamera(hass, config)]
    async_add_entities(camera, update_before_add=True)


async def async_setup_platform(
        hass: HomeAssistantType,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        discovery_info: DiscoveryInfoType | None = None,
):
    async_add_entities([ValetudoCamera(hass, config)])
    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)


class ValetudoCamera(Camera):
    _attr_has_entity_name = True

    def __init__(self, hass, device_info):
        super().__init__()
        _LOGGER.debug("Camera Starting up..")
        self.hass = hass
        self._attr_name = "Camera"
        self._directory_path = os.getcwd()  # get Home Assistant path
        _LOGGER.debug(f"Logs storage dir changed to.. {self._directory_path}/{STORAGE_DIR}")
        self._snapshots = Snapshots(self._directory_path + "/" + STORAGE_DIR)
        self._mqtt_listen_topic = device_info.get(CONF_VACUUM_CONNECTION_STRING)
        self.file_name = ""
        if self._mqtt_listen_topic:
            self._mqtt_listen_topic = str(self._mqtt_listen_topic)
            file_name = self._mqtt_listen_topic.split("/")
            self.snapshot_img = (
                    self._directory_path + "/www/snapshot_" + file_name[1].lower() + ".png"
            )
            self.log_file = (
                    self._directory_path + "/www/" + file_name[1].lower() + ".zip"
            )
            self._attr_unique_id = device_info.get(
                CONF_UNIQUE_ID,
                get_vacuum_unique_id_from_mqtt_topic(self._mqtt_listen_topic),
            )
            self.file_name = file_name[1].lower()
        self._mqtt = ValetudoConnector(self._mqtt_listen_topic, self.hass)
        self._identifiers = device_info.get(CONF_VACUUM_IDENTIFIERS)
        self._image = None
        self._processing = False
        self._image_w = None
        self._image_h = None
        self._should_poll = False
        self._map_handler = MapImageHandler()
        self._re_handler = ReImageHandler()
        self._map_rooms = None
        self._map_pred_zones = None
        self._map_pred_points = None
        self._vacuum_shared = Vacuum()
        self._vacuum_state = None
        self._attr_frame_interval = 1
        self._vac_img_data = None
        self._vac_json_data = None
        self._vac_json_id = None
        self._attr_calibration_points = None
        self._base = None
        self._current = None
        self._cpu_percent = None
        self._image_rotate = int(device_info.get(ATTR_ROTATE, 0))
        self._margins = int(device_info.get(ATTR_MARGINS, 150))
        self._snapshot_taken = False
        self._show_vacuum_state = device_info.get(CONF_VAC_STAT)
        if not self._show_vacuum_state:
            self._show_vacuum_state = False
        # If not configured, default to True for compatibility
        self._enable_snapshots = device_info.get(CONF_SNAPSHOTS_ENABLE)
        if self._enable_snapshots is None:
            self._enable_snapshots = True
        # If snapshots are disabled, delete www data
        if not self._enable_snapshots and self.snapshot_img and os.path.isfile(self.snapshot_img):
            os.remove(self.snapshot_img)
        # If there is a log zip in www remove it
        if os.path.isfile(self.log_file):
            os.remove(self.log_file)
        self._last_image = None
        self._image_grab = True
        self._frame_nuber = 0
        self._rrm_data = False  # Temp. check for rrm data
        try:
            self.user_colors = [
                device_info.get(COLOR_WALL),
                device_info.get(COLOR_ZONE_CLEAN),
                device_info.get(COLOR_ROBOT),
                device_info.get(COLOR_BACKGROUND),
                device_info.get(COLOR_MOVE),
                device_info.get(COLOR_CHARGER),
                device_info.get(COLOR_NO_GO),
                device_info.get(COLOR_GO_TO),
                device_info.get(COLOR_TEXT),
            ]
            self.user_alpha = [
                device_info.get(ALPHA_WALL),
                device_info.get(ALPHA_ZONE_CLEAN),
                device_info.get(ALPHA_ROBOT),
                device_info.get(ALPHA_BACKGROUND),
                device_info.get(ALPHA_MOVE),
                device_info.get(ALPHA_CHARGER),
                device_info.get(ALPHA_NO_GO),
                device_info.get(ALPHA_GO_TO),
                device_info.get(ALPHA_TEXT),
            ]
            self.rooms_colors = [
                device_info.get(COLOR_ROOM_0),
                device_info.get(COLOR_ROOM_1),
                device_info.get(COLOR_ROOM_2),
                device_info.get(COLOR_ROOM_3),
                device_info.get(COLOR_ROOM_4),
                device_info.get(COLOR_ROOM_5),
                device_info.get(COLOR_ROOM_6),
                device_info.get(COLOR_ROOM_7),
                device_info.get(COLOR_ROOM_8),
                device_info.get(COLOR_ROOM_9),
                device_info.get(COLOR_ROOM_10),
                device_info.get(COLOR_ROOM_11),
                device_info.get(COLOR_ROOM_12),
                device_info.get(COLOR_ROOM_13),
                device_info.get(COLOR_ROOM_14),
                device_info.get(COLOR_ROOM_15),
            ]
            self.rooms_alpha = [
                device_info.get(ALPHA_ROOM_0),
                device_info.get(ALPHA_ROOM_1),
                device_info.get(ALPHA_ROOM_2),
                device_info.get(ALPHA_ROOM_3),
                device_info.get(ALPHA_ROOM_4),
                device_info.get(ALPHA_ROOM_5),
                device_info.get(ALPHA_ROOM_6),
                device_info.get(ALPHA_ROOM_7),
                device_info.get(ALPHA_ROOM_8),
                device_info.get(ALPHA_ROOM_9),
                device_info.get(ALPHA_ROOM_10),
                device_info.get(ALPHA_ROOM_11),
                device_info.get(ALPHA_ROOM_12),
                device_info.get(ALPHA_ROOM_13),
                device_info.get(ALPHA_ROOM_14),
                device_info.get(ALPHA_ROOM_15),
            ]
            self._vacuum_shared.update_user_colors(
                add_alpha_to_rgb(self.user_alpha, self.user_colors)
            )
            self._vacuum_shared.update_rooms_colors(
                add_alpha_to_rgb(self.rooms_alpha, self.rooms_colors)
            )
        except (ValueError, IndexError, UnboundLocalError) as e:
            _LOGGER.error("Error while populating colors: %s", e)

    async def async_added_to_hass(self) -> None:
        """Handle entity added toHome Assistant."""
        await self._mqtt.async_subscribe_to_topics()
        self._should_poll = True
        self.async_schedule_update_ha_state(True)

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal from Home Assistant."""
        await super().async_will_remove_from_hass()
        if self._mqtt:
            await self._mqtt.async_unsubscribe_from_topics()

    @property
    def frame_interval(self) -> float:
        """Camera Frame Interval"""
        return 1

    def camera_image(
            self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Optional[bytes]:
        """Camera Image"""
        return self._image

    @property
    def name(self) -> str:
        """Camera Entity Name"""
        return self._attr_name

    def turn_on(self):
        self._should_poll = True

    def turn_off(self):
        self._should_poll = False

    @property
    def extra_state_attributes(self):
        """Camera Attributes"""
        attrs = {
            "friendly_name": self._attr_name,
            "vacuum_status": self._vacuum_state,
            "vacuum_topic": self._mqtt_listen_topic,
            "vacuum_json_id": self._vac_json_id,
            "json_data": self._vac_json_data,
            "vacuum_position": self._current,
            "calibration_points": self._attr_calibration_points,
        }
        if self._enable_snapshots:
            attrs["snapshot"] = self._snapshot_taken
            attrs["snapshot_path"] = "/local/snapshot_" + self.file_name + ".png"
        else:
            attrs["snapshot"] = False
        if (self._map_rooms is not None) and (self._map_rooms != {}):
            attrs["rooms"] = self._map_rooms
        if (self._map_pred_zones is not None) and (self._map_pred_zones != {}):
            attrs["zones"] = self._map_pred_zones
        if (self._map_pred_points is not None) and (self._map_pred_points != {}):
            attrs["points"] = self._map_pred_points
        return attrs

    @property
    def should_poll(self) -> bool:
        return self._should_poll

    @property
    def device_info(self):
        """Return the device info."""
        device_info = None
        try:
            from homeassistant.helpers.device_registry import DeviceInfo

            device_info = DeviceInfo
        except ImportError:
            from homeassistant.helpers.entity import DeviceInfo

            device_info = DeviceInfo
        return device_info(identifiers=self._identifiers)

    def empty_if_no_data(self):
        """Return an empty image if there are no data"""
        # Check if the snapshot file exists
        _LOGGER.info(f"{self.snapshot_img}: searching Snapshot image")
        if os.path.isfile(self.snapshot_img) and (self._last_image is None):
            # Load the snapshot image
            self._last_image = Image.open(self.snapshot_img)
            _LOGGER.debug(f"{self.file_name}: Snapshot image loaded")
            return self._last_image
        elif self._last_image is not None:
            return self._last_image
        else:
            # Create an empty image with a gray background
            empty_img = Image.new("RGB", (800, 600), "gray")
            _LOGGER.info(f"{self.file_name}: Starting up ...")
            return empty_img

    async def take_snapshot(self, json_data, image_data):
        """Camera Automatic Snapshots"""
        try:
            self._snapshot_taken = True
            # When logger is active.
            if ((_LOGGER.getEffectiveLevel() > 0) and
                    (_LOGGER.getEffectiveLevel() != 30)):
                # Save mqtt raw data file.
                if self._mqtt is not None:
                    await self._mqtt.save_payload(self.file_name)
                # Write the JSON and data to the file.
                self._snapshots.data_snapshot(self.file_name, json_data)
            # Save image ready for snapshot.
            if self._enable_snapshots:
                image_data.save(self.snapshot_img)
                _LOGGER.info(f"{self.file_name}: Camera Snapshot Taken.")
        except IOError:
            self._snapshot_taken = None
            _LOGGER.warning(f"Error Saving {self.file_name}: Snapshot, will not be available till restart.")
        else:
            _LOGGER.debug(f"{self.file_name}: Snapshot acquired during {self._vacuum_state} Vacuum State.")

    async def load_test_json(self, file_path=None):
        # Load a test json
        if file_path:
            json_file = file_path
            with open(json_file, "rb") as j_file:
                tmp_json = j_file.read()
            parsed_json = json.loads(tmp_json)
            self._should_poll = False
            return parsed_json
        else:
            return None

    async def async_update(self):
        """Camera Frame Update"""
        # check and update the vacuum reported state
        if not self._mqtt:
            return self.empty_if_no_data()
        # If we have data from MQTT, we process the image
        self._vacuum_state = await self._mqtt.get_vacuum_status()
        process_data = await self._mqtt.is_data_available(self._processing)
        if process_data:
            self._processing = True
            # if the vacuum is working, or it is the first image.
            if (
                    self._vacuum_state == "cleaning"
                    or self._vacuum_state == "moving"
                    or self._vacuum_state == "returning"
            ):
                # grab the image
                self._image_grab = True
                self._frame_nuber = self._map_handler.get_frame_number() - 1
                # when the vacuum goes / is in idle, error or docked
                # take the snapshot.
                self._snapshot_taken = False
                _LOGGER.info(
                    f"{self.file_name}: Camera image data update available: {process_data}"
                )
            # calculate the cycle time for frame adjustment
            start_time = datetime.now()
            pid = os.getpid()  # Start to log the CPU usage of this PID.
            proc = proc_insp.PsutilWrapper().psutil.Process(pid)  # Get the process PID.
            self._cpu_percent = round((proc.cpu_percent() / proc_insp.PsutilWrapper().psutil.cpu_count()) / 2, 2)
            try:
                parsed_json = await self._mqtt.update_data(self._image_grab)
                if parsed_json[1]:
                    self._rrm_data = parsed_json[0]
                else:
                    parsed_json = parsed_json[0]
                    self._rrm_data = None
                # Below bypassed code is for debug purpose only
                #########################################################
                # parsed_json = await self.load_test_json(
                #     "custom_components/valetudo_vacuum_camera/snapshots/test.json")
                ##########################################################
                self._vac_json_data = "Success"
            except ValueError:
                self._vac_json_data = "Error"
                pass
            else:
                # Just in case, let's check that the data is available
                pid = os.getpid()  # Start to log the CPU usage of this PID.
                proc = proc_insp.PsutilWrapper().psutil.Process(pid)  # Get the process PID.
                self._cpu_percent = round((proc.cpu_percent() / proc_insp.PsutilWrapper().psutil.cpu_count()) / 2, 2)
                _LOGGER.debug(f"{self.file_name} System CPU usage stat (1/2): {self._cpu_percent}%")
                if parsed_json is not None:
                    if self._rrm_data:
                        pil_img = await self.process_rand256_data(parsed_json)
                    elif self._rrm_data is None:
                        pil_img = await self.proces_valetudo_data(parsed_json)
                    else:
                        # if no image was processed empty or last snapshot/frame
                        pil_img = self.empty_if_no_data()
                    # Converting the image obtained to bytes
                    # Using openCV would reduce the CPU and memory usage.
                    # On Py4 HA OS is not possible to install the openCV library.
                    buffered = BytesIO()
                    # backup the image
                    if pil_img:
                        self._last_image = pil_img
                        self._image_w = pil_img.width
                        self._image_h = pil_img.height
                    else:
                        pil_img = self.empty_if_no_data()
                        self._last_image = None  # pil_img
                        self._image_w = pil_img.width
                        self._image_h = pil_img.height
                    pil_img.save(buffered, format="PNG")
                    bytes_data = buffered.getvalue()
                    self._image = bytes_data
                    # clean up
                    del buffered, pil_img, bytes_data
                    _LOGGER.debug(f"{self.file_name}: Image update complete")
                    processing_time = (datetime.now() - start_time).total_seconds()
                    self._attr_frame_interval = max(0.1, processing_time)
                    _LOGGER.debug(f"Adjusted {self.file_name}: Frame interval: {self._attr_frame_interval}")

                else:
                    _LOGGER.info(
                        f"{self.file_name}: Image not processed. Returning not updated image."
                    )
                    self._attr_frame_interval = 0.1
                self.camera_image(self._image_w, self._image_h)
                # HA supervised memory and CUP usage report.
                self._cpu_percent = round(((self._cpu_percent + proc.cpu_percent())
                                           / proc_insp.PsutilWrapper().psutil.cpu_count()) / 2, 2)
                memory_percent = round(
                    ((proc.memory_info()[0]/2.**30) / (proc_insp.PsutilWrapper().psutil.virtual_memory().total/2.**30))
                    * 100, 2)
                _LOGGER.debug(f"{self.file_name} System CPU usage stat (2/2): {self._cpu_percent}%")
                _LOGGER.debug(f"{self.file_name} Camera Memory usage in GB: "
                              f"{round(proc.memory_info()[0]/2.**30, 2)}, "
                              f"{memory_percent}% of Total.")
                self._cpu_percent = proc.cpu_percent() / proc_insp.PsutilWrapper().psutil.cpu_count()
                self._processing = False
                # threading.Thread(target=self.async_update).start()
                return self._image

    # let's separate the vacuums:
    async def proces_valetudo_data(self, parsed_json):
        if parsed_json is not None:
            pil_img = await self._map_handler.get_image_from_json(
                m_json=parsed_json,
                robot_state=self._vacuum_state,
                img_rotation=self._image_rotate,
                margins=self._margins,
                user_colors=self._vacuum_shared.get_user_colors(),
                rooms_colors=self._vacuum_shared.get_rooms_colors(),
                file_name=self.file_name,
            )
            if pil_img is not None:
                if self._map_rooms is None:
                    if self._rrm_data is None:
                        self._map_rooms = await self._map_handler.get_rooms_attributes()
                    if self._map_rooms:
                        _LOGGER.debug(
                            f"State attributes rooms update: {self._map_rooms}"
                        )
                if self._show_vacuum_state:
                    self._map_handler.draw.status_text(
                        pil_img,
                        50,
                        self._vacuum_shared.user_colors[8],
                        self.file_name + ": " + self._vacuum_state,
                        )

                if self._attr_calibration_points is None:
                    self._attr_calibration_points = (
                        self._map_handler.get_calibration_data(self._image_rotate)
                    )

                self._vac_json_id = self._map_handler.get_json_id()
                if not self._base:
                    self._base = self._map_handler.get_charger_position()
                self._current = self._map_handler.get_robot_position()
                if not self._vac_img_data:
                    self._vac_img_data = self._map_handler.get_img_size()

                if not self._snapshot_taken and (
                        self._vacuum_state == "idle"
                        or self._vacuum_state == "docked"
                        or self._vacuum_state == "error"
                ):
                    # suspend image processing if we are at the next frame.
                    if (
                            self._frame_nuber
                            is not self._map_handler.get_frame_number()
                    ):
                        self._image_grab = False
                        _LOGGER.info(
                            f"Suspended the camera data processing for: {self.file_name}."
                        )
                        # take a snapshot
                        await self.take_snapshot(parsed_json, pil_img)
            return pil_img
        return None

    async def process_rand256_data(self, parsed_json):
        if parsed_json is not None:
            destinations = await self._mqtt.get_destinations()
            pil_img = await self._re_handler.get_image_from_rrm(
                m_json=self._rrm_data,
                img_rotation=self._image_rotate,
                margins=self._margins,
                user_colors=self._vacuum_shared.get_user_colors(),
                rooms_colors=self._vacuum_shared.get_rooms_colors(),
                file_name=self.file_name,
                destinations=destinations,
                drawing_limit=self._cpu_percent
            )

            if pil_img is not None:
                if self._map_rooms is None:
                    destinations = await self._mqtt.get_destinations()
                    if destinations is not None:
                        self._map_rooms, self._map_pred_zones, self._map_pred_points = \
                            await self._re_handler.get_rooms_attributes(destinations)
                    if self._map_rooms:
                        _LOGGER.debug(
                            f"State attributes rooms update: {self._map_rooms}"
                        )
                if self._show_vacuum_state:
                    self._map_handler.draw.status_text(
                        pil_img,
                        50,
                        self._vacuum_shared.user_colors[8],
                        self.file_name + ": " + self._vacuum_state,
                        )

                if self._attr_calibration_points is None:
                    self._attr_calibration_points = (
                        self._re_handler.get_calibration_data(self._image_rotate)
                    )

                self._vac_json_id = self._re_handler.get_json_id()
                if not self._base:
                    self._base = self._re_handler.get_charger_position()
                self._current = self._re_handler.get_robot_position()
                if not self._vac_img_data:
                    self._vac_img_data = self._re_handler.get_img_size()

                if not self._snapshot_taken and (
                        self._vacuum_state == "idle"
                        or self._vacuum_state == "docked"
                        or self._vacuum_state == "error"
                ):
                    # suspend image processing if we are at the next frame.
                    _LOGGER.info(
                        f"Suspended the camera data processing for: {self.file_name}."
                    )
                    # take a snapshot
                    await self.take_snapshot(self._rrm_data, pil_img)
                    self._image_grab = False
            return pil_img
        return None
