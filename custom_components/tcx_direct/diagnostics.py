from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import TCXConfigEntry
from .const import (
    CONTROL_CONFIRM_TIMEOUT,
    POOL_FILTRATION_CONFIRM_TIMEOUT,
    POST_PRIME_SYNC_INTERVAL,
    POST_PRIME_SYNC_TIMEOUT,
    PUMP_POWER_CONFIRM_TIMEOUT,
    PUMP_ZERO_GRACE_SECONDS,
    VERSION,
    VERSION_CODE,
)
from .redaction import sanitize_diagnostics

TO_REDACT = {
    "username",
    "password",
    "authentication_token",
    "device_name",
    "ei",
    "equipmentId",
    "euid",
    "id_token",
    "refresh_token",
    "device_id",
    "last_target",
    "clientToken",
    "userId",
    "user_id",
    "email",
    "latitude",
    "longitude",
    "macAddr",
    "ni",
    "session_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TCXConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    client = runtime.client
    coordinator = runtime.coordinator

    diagnostics = {
        "integration": {
            "version": VERSION,
            "version_code": VERSION_CODE,
        },
        "config": dict(entry.data),
        "options": dict(entry.options),
        "connection": {
            "healthy": client.healthy,
            "websocket_connected": client.websocket_connected,
            "websocket_stream_healthy": client.websocket_stream_healthy,
            "cloud_reachable": client.cloud_reachable,
            "shadow_supported": client.shadow_supported,
            "last_error": client.last_error,
            "source": coordinator.source,
            "using_cached_data": coordinator.using_cached_data,
            "last_successful_update": coordinator.last_successful_update,
        },
        "websocket": {
            "messages_received": client.ws_messages_received,
            "text_messages_received": client.ws_text_messages_received,
            "json_messages_received": client.ws_json_messages_received,
            "state_messages_received": client.ws_state_messages_received,
            "desired_messages_received": client.ws_desired_messages_received,
            "reported_messages_received": client.ws_reported_messages_received,
            "non_state_messages_received": client.ws_non_state_messages_received,
            "last_message_at": client.last_ws_message_at,
            "last_state_message_at": client.last_ws_state_at,
            "last_reported_state_at": client.last_ws_state_at,
            "last_message_type": client.last_ws_message_type,
            "last_service": client.last_ws_service,
            "last_event": client.last_ws_event,
            "last_namespace": client.last_ws_namespace,
            "last_target": client.last_ws_target,
            "last_device_timestamp": client.last_ws_device_timestamp,
            "last_opened_at": client.last_websocket_opened_at,
            "connect_count": client.websocket_connect_count,
            "reconnect_count": client.websocket_reconnect_count,
            "watchdog_reconnect_count": client.watchdog_reconnect_count,
            "watchdog_resubscribe_count": client.watchdog_resubscribe_count,
            "watchdog_resubscribe_success_count": client.watchdog_resubscribe_success_count,
            "watchdog_resubscribe_failure_count": client.watchdog_resubscribe_failure_count,
            "manual_reconnect_count": client.manual_reconnect_count,
            "reconnect_reason_counts": dict(client.reconnect_reason_counts),
            "authorization_subscribe_count": client.authorization_subscribe_count,
            "authorization_snapshot_count": client.authorization_snapshot_count,
            "bootstrap_resubscribe_count": client.bootstrap_resubscribe_count,
            "last_authorization_snapshot_at": client.last_authorization_snapshot_at,
            "last_reconnect_reason": client.last_reconnect_reason,
            "recent_unique_payload_structures": client.recent_ws_structures,
            "recent_desired_payloads": sanitize_diagnostics(client.recent_desired_payloads),
            "last_payload": sanitize_diagnostics(client.last_ws_payload),
        },
        "shadow": {
            "last_update_at": client.last_shadow_update_at,
            "last_error": client.last_shadow_error,
            "last_rate_limited_at": client.last_shadow_rate_limited_at,
            "poll_interval_seconds": client.shadow_poll_interval,
            "request_count": client.shadow_request_count,
            "success_count": client.shadow_success_count,
            "failure_count": client.shadow_failure_count,
            "rate_limit_count": client.shadow_rate_limit_count,
        },
        "control": {
            "default_confirmation_timeout_seconds": CONTROL_CONFIRM_TIMEOUT,
            "pool_filtration_confirmation_timeout_seconds": (POOL_FILTRATION_CONFIRM_TIMEOUT),
            "pump_power_confirmation_timeout_seconds": PUMP_POWER_CONFIRM_TIMEOUT,
            "command_count": client.control_command_count,
            "success_count": client.control_success_count,
            "failure_count": client.control_failure_count,
            "command_counts": dict(client.control_command_counts),
            "success_counts": dict(client.control_success_counts),
            "failure_counts": dict(client.control_failure_counts),
            "last_command_at": client.last_control_at,
            "last_command": client.last_control_description,
            "last_error": client.last_control_error,
            "last_frame": client.last_control_frame,
            "last_confirmation_seconds": client.last_control_confirmation_seconds,
            "confirmation_seconds": dict(client.control_confirmation_seconds),
            "confirmation_refresh_count": client.control_confirmation_refresh_count,
            "late_confirmation_count": client.control_late_confirmation_count,
            "last_failure_at": client.last_control_failure_at,
            "last_failure_command": client.last_control_failure_description,
            "last_failure_error": client.last_control_failure_error,
        },
        "controller_mode": {
            "mode": coordinator.normalized.get("controller_mode"),
            "code": coordinator.normalized.get("system_mode_code"),
            "remote_control_available": coordinator.normalized.get("remote_control_available"),
            "recent_transitions": client.recent_controller_mode_transitions,
        },
        "post_prime_sync": {
            "interval_seconds": POST_PRIME_SYNC_INTERVAL,
            "timeout_seconds": POST_PRIME_SYNC_TIMEOUT,
            "state": client.post_prime_sync_state,
            "target_rpm": client.post_prime_sync_target,
            "generation": client.post_prime_sync_generation,
            "filter_key": client.post_prime_sync_filter_key,
            "scheduled_count": client.post_prime_sync_scheduled_count,
            "success_count": client.post_prime_sync_success_count,
            "cancel_count": client.post_prime_sync_cancel_count,
            "skip_count": client.post_prime_sync_skip_count,
            "timeout_count": client.post_prime_sync_timeout_count,
            "last_update_at": client.last_post_prime_sync_at,
            "last_result": client.last_post_prime_sync_result,
            "last_error": client.last_post_prime_sync_error,
            "last_external_override_rpm": (client.last_post_prime_sync_external_override_rpm),
            "last_external_override_at": client.last_post_prime_sync_external_override_at,
            "recent_transitions": sanitize_diagnostics(client.recent_post_prime_transitions),
        },
        "authentication": {
            "full_login_count": client.full_login_count,
            "refresh_count": client.auth_refresh_count,
        },
        "cache": {
            "has_normalized_state": bool(coordinator.normalized),
            "has_raw_reported_state": bool(coordinator.raw_reported),
        },
        "pump_zero_filter": {
            "grace_period_seconds": PUMP_ZERO_GRACE_SECONDS,
            "pending": coordinator.pump_zero_suppression_pending,
            "suppression_count": coordinator.pump_zero_suppression_count,
            "last_suppressed_at": coordinator.last_pump_zero_suppressed_at,
        },
        "native_schedules": client.schedules.snapshot(),
        "normalized": coordinator.normalized,
        "reported_state": sanitize_diagnostics(coordinator.raw_reported),
    }
    return async_redact_data(diagnostics, TO_REDACT)
