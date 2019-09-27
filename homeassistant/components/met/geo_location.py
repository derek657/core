"""Support for Met lightning geo location events."""
from datetime import timedelta
import logging

from metno import LightningData

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    LENGTH_KILOMETERS,
)
from homeassistant.core import callback
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_TRACK_HOME, DOMAIN

_LOGGER = logging.getLogger(__name__)

ATTR_EXTERNAL_ID = "external_id"
ATTR_PUBLICATION_DATE = "publication_date"

ATTRIBUTION = "Data provided by Met.no"
DEFAULT_EVENT_NAME = "Lightning Strike: {0}"
DEFAULT_ICON = "mdi:flash"
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=5)

SIGNAL_DELETE_ENTITY = "delete_entity_{0}"


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Met lightning based on a config entry."""
    if entry.data[CONF_RADIUS] <= 0:
        return

    if entry.data.get(CONF_TRACK_HOME, False):
        latitude = hass.config.latitude
        longitude = hass.config.longitude
    else:
        latitude = entry.data[CONF_LATITUDE]
        longitude = entry.data[CONF_LONGITUDE]

    manager = MetLightningEventManager(
        hass, async_add_entities, latitude, longitude, entry.data[CONF_RADIUS]
    )
    await manager.async_init()


class MetLightningEventManager:
    """Define a class to handle Met lightning events."""

    def __init__(self, hass, async_add_entities, latitude, longitude, radius):
        """Initialize."""
        self._async_add_entities = async_add_entities
        websession = aiohttp_client.async_get_clientsession(hass)
        self._client = LightningData(websession)
        self._hass = hass
        self._latitude = latitude
        self._longitude = longitude
        self._managed_strike_ids = set()
        self._radius = radius
        self._strikes = {}

    @callback
    def _create_events(self, ids_to_create):
        """Create new geo location events."""
        _LOGGER.debug("Going to create %s", ids_to_create)
        events = []
        for strike_id in ids_to_create:
            strike = self._strikes[strike_id]
            event = MetLightningEvent(
                strike["distance"] / 1000.0,
                strike["lat"],
                strike["long"],
                strike_id,
                strike["date"],
            )
            events.append(event)

        self._async_add_entities(events)

    @callback
    def _remove_events(self, ids_to_remove):
        """Remove old geo location events."""
        _LOGGER.debug("Going to remove %s", ids_to_remove)
        for strike_id in ids_to_remove:
            async_dispatcher_send(self._hass, SIGNAL_DELETE_ENTITY.format(strike_id))

    async def async_init(self):
        """Schedule regular updates based on configured time interval."""

        async def update(event_time):
            """Update."""
            await self.async_update()

        await self.async_update()
        async_track_time_interval(self._hass, update, DEFAULT_UPDATE_INTERVAL)

    async def async_update(self):
        """Refresh data."""
        _LOGGER.debug("Refreshing Met lightning data")
        self._strikes = await self._client.within_radius(
            self._latitude, self._longitude, self._radius * 100000000000000
        )

        new_strike_ids = set(self._strikes)
        # Remove all managed entities that are not in the latest update anymore.
        ids_to_remove = self._managed_strike_ids.difference(new_strike_ids)
        self._remove_events(ids_to_remove)

        # Create new entities for all strikes that are not managed entities yet.
        ids_to_create = new_strike_ids.difference(self._managed_strike_ids)
        self._create_events(ids_to_create)

        # Store all external IDs of all managed strikes.
        self._managed_strike_ids = new_strike_ids


class MetLightningEvent(GeolocationEvent):
    """Define a lightning strike event."""

    def __init__(self, distance, latitude, longitude, strike_id, publication_date):
        """Initialize entity with data provided."""
        self._distance = distance
        self._latitude = latitude
        self._longitude = longitude
        self._publication_date = publication_date
        self._remove_signal_delete = None
        self._strike_id = strike_id

    @property
    def device_state_attributes(self):
        """Return the device state attributes."""
        attributes = {}
        attributes[ATTR_EXTERNAL_ID] = self._strike_id
        attributes[ATTR_ATTRIBUTION] = ATTRIBUTION
        attributes[ATTR_PUBLICATION_DATE] = self._publication_date
        return attributes

    @property
    def distance(self):
        """Return distance value of this external event."""
        return self._distance

    @property
    def icon(self):
        """Return the icon to use in the front-end."""
        return DEFAULT_ICON

    @property
    def latitude(self):
        """Return latitude value of this external event."""
        return self._latitude

    @property
    def longitude(self):
        """Return longitude value of this external event."""
        return self._longitude

    @property
    def name(self):
        """Return the name of the event."""
        return DEFAULT_EVENT_NAME.format(self._strike_id)

    @property
    def source(self) -> str:
        """Return source value of this external event."""
        return DOMAIN

    @property
    def should_poll(self):
        """Disable polling."""
        return False

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return LENGTH_KILOMETERS

    @callback
    def _delete_callback(self):
        """Remove this entity."""
        self._remove_signal_delete()
        self.hass.async_create_task(self.async_remove())

    async def async_added_to_hass(self):
        """Call when entity is added to hass."""
        self._remove_signal_delete = async_dispatcher_connect(
            self.hass,
            SIGNAL_DELETE_ENTITY.format(self._strike_id),
            self._delete_callback,
        )