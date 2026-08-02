"""Home Assistant integration: REST client and MQTT discovery."""

from app.home_assistant.client import (
    HomeAssistantClient,
    HomeAssistantError,
    HomeAssistantStatus,
    get_home_assistant,
    set_home_assistant,
)

__all__ = [
    "HomeAssistantClient",
    "HomeAssistantError",
    "HomeAssistantStatus",
    "get_home_assistant",
    "set_home_assistant",
]
