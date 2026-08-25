# Jandy TCX Direct

A Home Assistant custom integration for Jandy / Fluidra AquaLink TCX controllers.

TCX Direct connects Home Assistant directly to the iAquaLink/Zodiac cloud. It does **not** require a Supervisor add-on, separate container, local HTTP bridge, or the legacy Jandy TCX Client add-on.

> [!IMPORTANT]
> This is an unofficial community integration built against reverse-engineered iAquaLink/TCX cloud behavior. It is not affiliated with or supported by Jandy or Fluidra.

## Current version

**v0.1.10**

The integration prioritizes reliable telemetry and conservative equipment control. Native control is enabled only for TCX equipment whose state and write behavior have been captured and validated; other equipment remains read-only.

## What it currently exposes

- Pool temperature
- Pool pump state
- Pump RPM
- Pump preset
- Pool light state
- Pool light color number
- Pool light color name
- Waterfall state and on/off control
- Pool temperature setpoint
- Pump minimum and maximum RPM
- Wi-Fi RSSI
- TCX firmware version
- Connection type
- Cloud, WebSocket transport, and WebSocket stream health
- Connection/reconnect diagnostics
- Last successful update, WebSocket update, and shadow update
- Manual diagnostic reconnect button

The Waterfall switch becomes available only when the controller reports a feature relay identified by the confirmed `FRLY`/`WF` type pair. Commands use the Zodiac WebSocket state-controller protocol in the TCX device namespace and must be confirmed by the controller's reported state before Home Assistant reports success.

Equipment air temperature and salt-water chlorinator level remain disabled by default because the tested TCX controller has not exposed trustworthy native values for those fields.

## Reliability design

The integration is designed around the failure mode where an iAquaLink/TCX connection can remain technically open while state updates stop.

- Direct iAquaLink/Zodiac authentication and TCX discovery
- Persistent Zodiac WebSocket subscription
- 30-second WebSocket heartbeat
- Reported-state watchdog rather than relying only on TCP/WebSocket-open state
- Automatic reconnect with backoff
- Automatic re-authentication and proactive token refresh
- REST shadow fallback/watchdog when supported by the controller
- Periodic WebSocket session rotation
- Startup Authorization re-subscription/bootstrap
- Last-known normalized-state persistence
- Full merged reported-state persistence across Home Assistant restarts
- Separate connectivity diagnostics from equipment values

## Installation

### Manual

Copy:

```text
custom_components/tcx_direct
```

into:

```text
/config/custom_components/tcx_direct
```

Restart Home Assistant, then go to:

**Settings → Devices & services → Add Integration → Jandy TCX Direct**

Enter your normal iAquaLink email address and password.

### HACS custom repository

This repository includes `hacs.json` and can be added as a custom **Integration** repository in HACS.

## Diagnostics

Home Assistant's **Download diagnostics** output is deliberately detailed because the TCX protocol is still being mapped. Sensitive values such as credentials, tokens, controller identifiers, coordinates, MAC addresses, and session identifiers are redacted.

Diagnostics include WebSocket message counts, recent payload structures, reconnect-reason counts, Authorization/bootstrap activity, shadow polling, authentication refreshes, and the sanitized merged state used by Home Assistant.

Recurring identical desired-state echoes are deduplicated so unusual protocol events remain visible. Hardware and network identifiers—including Zigbee identifiers—are redacted before diagnostics are exported.

## Version history

See [CHANGELOG.md](CHANGELOG.md) for the complete development history from v0.1.0 onward.

## Protocol notes

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the observed TCX namespaces, field mappings, startup behavior, and fields that are intentionally not decoded yet.

The transport implementation was informed by public reverse-engineering work around the iAquaLink/Zodiac cloud protocol, particularly the [`iaqualink`](https://github.com/tekkamanendless/iaqualink) project by tekkamanendless. TCX Direct contains its own Home Assistant integration, connection supervision, telemetry mapping, persistence, and diagnostics.

## Validation

Every push and pull request runs a lightweight validation workflow that compiles the integration, validates its JSON files, and confirms the version in `const.py` matches `manifest.json`.
