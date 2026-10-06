"""Constants for the Battery States integration."""

DOMAIN = "battery_states"
VERSION = "1.2.4"

NOTIFY_TITLE = "Batteries"
LOG_SIZE = 50  # recent alerts kept for the settings page

# Limits and alert settings (settings page). The defaults are the former
# pyscript / automations' fixed values.
CONF_LOW_THRESHOLD = "low_threshold"  # % ; at or below = low
CONF_NOT_SEEN_HOURS = "not_seen_hours"  # minimum silence before "not responding"
CONF_ALERT_LOW = "alert_low"
CONF_ALERT_NOT_SEEN = "alert_not_seen"  # the "not responding" alert
CONF_REMINDER = "reminder"
CONF_REMINDER_DAYS = "reminder_days"  # weekdays, Monday = 0
CONF_REMINDER_TIME = "reminder_time"  # "HH:MM:SS"
CONF_QUIET = "quiet_hours"
CONF_QUIET_FROM = "quiet_from"  # "HH:MM:SS"
CONF_QUIET_TO = "quiet_to"  # "HH:MM:SS"

DEFAULT_LOW_THRESHOLD = 20
DEFAULT_NOT_SEEN_HOURS = 12
DEFAULT_REMINDER_DAYS = [1, 3, 5]  # Tuesday, Thursday, Saturday
DEFAULT_REMINDER_TIME = "20:00:00"
DEFAULT_QUIET_FROM = "22:00:00"
DEFAULT_QUIET_TO = "07:00:00"
LOW_THRESHOLD_RANGE = (1, 99)
NOT_SEEN_HOURS_RANGE = (1, 168)
SILENCE_HOURS_RANGE = (1, 720)  # a battery's own "not responding after" (hours)

# Options
CONF_DEVICES = "devices"  # manual list, in display order
CONF_INCLUDE_INTEGRATIONS = "include_integrations"
CONF_INCLUDE_AREAS = "include_areas"
CONF_INCLUDE_LABELS = "include_labels"
CONF_INCLUDE_DEVICES = "include_devices"
CONF_EXCLUDE = "exclude"
CONF_NOTIFY = "notify_service"  # one service (str) or several (list of str)

CONF_OVERRIDES = "overrides"  # entity_id -> settings, for batteries found by filters

# Settings of one battery (all optional; empty = automatic)
ATTR_ENTITY_ID = "entity_id"
ATTR_NAME = "name"  # empty = the device's name in HA
ATTR_BATTERY_TYPE = "battery_type"  # empty = from the battery library
ATTR_AREA = "area"  # area id; empty = the device's area in HA
ATTR_RECHARGEABLE = "rechargeable"  # shown as "Rechargeable"
ATTR_SILENCE = "silence_hours"  # not responding after this silence; empty = automatic

UNKNOWN_TYPE = "Unknown"
RECHARGEABLE_TYPE = "Rechargeable"

CARD_URL = f"/{DOMAIN}/battery-states-card.js"
PANEL_URL = f"/{DOMAIN}/battery-states-panel.js"
PANEL_URL_PATH = "battery-states"

SIGNAL_UPDATE = f"{DOMAIN}_update"
SIGNAL_LOG = f"{DOMAIN}_log"  # the recent alerts changed
