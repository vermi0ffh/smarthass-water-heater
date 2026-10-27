"""Constants of the integration: domain, option keys and their defaults."""

DOMAIN = "smart_hass_water_heater"

# Entities read (rule 3: only read)
CONF_RELAY_ENTITY = "relay_entity"
CONF_POWER_ENTITY = "power_entity"
CONF_ENERGY_ENTITY = "energy_entity"
CONF_HOT_WATER_ENTITY = "hot_water_entity"
SOURCE_KEYS = (CONF_RELAY_ENTITY, CONF_POWER_ENTITY, CONF_ENERGY_ENTITY, CONF_HOT_WATER_ENTITY)

# The tank
CONF_VOLUME_L = "volume_l"
CONF_SET_POINT_C = "set_point_c"
CONF_LOSSES_W = "losses_w"
CONF_COLD_WATER_C = "cold_water_c"
# Count the losses learned on the cycles without draw in place of losses_w (off by default: rule 25)
CONF_LEARN_LOSSES = "learn_losses"

DEFAULT_VOLUME_L = 200.0
DEFAULT_SET_POINT_C = 60.0
# 93.8 W measured on 2026-10-20 on a 200 L tank (1.241 kWh reheated over 13.23 h without draw)
DEFAULT_LOSSES_W = 95.0
DEFAULT_COLD_WATER_C = 12.0

# Services
SERVICE_MARK_FULL = "mark_full"
SERVICE_SET_LEVEL = "set_level"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_LEVEL_L = "level_l"
