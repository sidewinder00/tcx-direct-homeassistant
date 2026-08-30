# Jandy TCX Direct

A Home Assistant custom integration for Jandy / Fluidra AquaLink TCX controllers.

TCX Direct connects Home Assistant directly to the iAquaLink/Zodiac cloud. It does **not** require a Supervisor add-on, separate container, local HTTP bridge, or the legacy Jandy TCX Client add-on.

> [!IMPORTANT]
> This is an unofficial community integration built against reverse-engineered iAquaLink/TCX cloud behavior. It is not affiliated with or supported by Jandy or Fluidra.

## Current version

**v0.2.6**

The integration prioritizes reliable telemetry and conservative equipment control. Native control is enabled only for TCX equipment whose state and write behavior have been captured and validated; other equipment remains read-only.

## What it currently exposes

- Pool temperature
- Pool pump state
- Pump RPM
- Pump requested RPM
- Pump operating phase (`Off`, `Priming`, `Running`, `Waterfall`, or `Transitioning`)
- Pump preset
- Controller mode, with the raw `systemMode` code retained as an attribute
- Freeze Protection setpoint
- Pool light state
- Pool light color number
- Pool light color name
- Pool Light Color select with 14 confirmed colors and programs
- Waterfall status
- Pool temperature setpoint
- Pump minimum and maximum RPM
- Wi-Fi RSSI
- TCX firmware version
- Connection type
- Cloud, WebSocket transport, and WebSocket stream health
- Connection/reconnect diagnostics
- Control status with the latest command outcome and failure details
- Live-versus-cached data status and active source
- Last successful update, WebSocket update, reported equipment state, and shadow update
- Manual diagnostic reconnect button

Controls are exposed separately from the read-only sensor points:

- Pump Power on/off
- Pump Manual Speed control, constrained to the controller's reported limits
- Pool Filtration Preset control, constrained to the controller's reported limits
- Start Pump at Speed action for preset-driven schedule startups
- Pool Light Power on/off
- Pool Light Color selection while the light is on
- Waterfall on/off
- Waterfall RPM control, persisted by Home Assistant and constrained to the controller's reported limits

The Pool Light Power switch and color selector become available only when the controller reports a light identified by the confirmed `JL`/`POOL_LT` type pair. The color selector is available only while the light is on; selecting a color never turns the light on implicitly. The Waterfall switch similarly requires the confirmed `FRLY`/`WF` feature relay, and pump controls require the confirmed Pool Filtration and filtration-controller objects. Commands use the Zodiac WebSocket state-controller protocol in the TCX device namespace and must be confirmed by the controller's reported state before Home Assistant reports success.

Pool-light colors and programs use the confirmed sequence: Alpine White, Sky Blue, Cobalt Blue, Caribbean Blue, Spring Green, Emerald Green, Emerald Rose, Magenta, Violet, Slow Color Splash, Fast Color Splash, America The Beautiful, Fat Tuesday, and Disco Tech. The selected program comes from `cmdClr`; `st = 0` remains authoritative for Off even when TCX retains the last nonzero color code.

Pump RPM reports the active motor `cmdSpd`, including priming and other controller-selected runtime changes. Pump Manual Speed separately displays and writes the filtration controller's `manSpd`, so live RPM changes do not overwrite the writable setpoint. Pool Filtration Preset displays and writes only the `BD1_F` entry in `ecm0.spdList`; the complete list is sent as required by TCX while the Spa Filtration and Waterfall entries are preserved unchanged. Waterfall RPM defaults to 2850 RPM; turning Waterfall on confirms the feature relay and then applies this value to `filt0.manSpd`. Changing Waterfall RPM while the feature is active applies the new value immediately.

Pump Requested RPM reports `ecm0.reqSpd` independently from the active command. Pump Operating Phase compares requested, commanded, and configured priming RPM and also considers the confirmed Waterfall relay, allowing priming and other speed transitions to be shown without changing the writable speed control.

Controller Mode is deliberately read-only and maps the observed values `1 = Auto`, `2 = Quick Clean`, `3 = Service`, `4 = Time Out`, and `5 = Transitioning`. Any other numeric value is retained as `Unknown (code N)`. Writable equipment entities become unavailable outside Auto, and a direct action or service call is rejected before transmission when any known or unknown non-Auto code is active. TCX Direct never forces the controller out of a local maintenance mode. Freeze Protection Setpoint exposes the reported `freezeSP` value without inferring an unconfirmed unit.

The `tcx_direct.start_pump_at_speed` action targets the Pump Power switch and accepts an `rpm` value. It synchronizes the persistent Pool Filtration preset with a dedicated 45-second confirmation window and one fresh-shadow verification if the live confirmation is late. A stopped pump then receives only the normal `pool.st = 1` power command; no speed is combined with the start frame. TCX owns priming and settles at the prepared preset, after which TCX Direct aligns `manSpd` once the reported requested and commanded speeds both reach the scheduled RPM. Waterfall, a stopped pump, or an intervening manual command cancels that deferred alignment. For an already-running pump outside the priming transition, the action synchronizes both the Pool Filtration preset and manual speed immediately; a refresh during priming is deferred until the target RPM is reached. The action never changes Spa Filtration or Waterfall presets.

When a manual-speed change briefly resets the motor state while filtration or Waterfall remains requested, Pump RPM holds its last valid nonzero reading for up to 90 seconds. A genuine pump-off command still reports 0 RPM immediately.

Equipment air temperature remains disabled by default, although a dedicated live `air` sensor is decoded when the controller reports one; the unrelated `hubAir` sentinel is ignored. Salt-water chlorinator level also remains disabled by default until a confirmed native chlorinator object is observed.

## Reliability design

The integration is designed around the failure mode where an iAquaLink/TCX connection can remain technically open while state updates stop.

- Direct iAquaLink/Zodiac authentication and TCX discovery
- Persistent Zodiac WebSocket subscription
- 30-second WebSocket heartbeat
- Reported-state watchdog that refreshes a quiet subscription before reconnecting
- Automatic reconnect with backoff
- Automatic re-authentication and proactive token refresh
- REST shadow fallback with rate-limit backoff when supported by the controller
- Six-hour defensive WebSocket session rotation
- Startup Authorization re-subscription/bootstrap
- Last-known normalized-state persistence
- Full merged reported-state persistence across Home Assistant restarts
- Separate connectivity diagnostics from equipment values
- Explicit live/cached-state and last-reported-equipment-state diagnostics
- Last command result and failure details independent from transport health

## Installation

Home Assistant 2026.8.0 or newer is required.

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

Diagnostics include WebSocket message counts, recent payload structures, recent distinct controller-mode transitions, reconnect-reason counts, Authorization/bootstrap activity, shadow polling, authentication refreshes, and the sanitized merged state used by Home Assistant.

Recurring identical desired-state echoes are deduplicated so unusual protocol events remain visible. Hardware and network identifiers—including Zigbee identifiers—are redacted before diagnostics are exported.

## Version history

See [CHANGELOG.md](CHANGELOG.md) for the complete development history from v0.1.0 onward.

## Protocol notes

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the observed TCX namespaces, field mappings, startup behavior, and fields that are intentionally not decoded yet.

The transport implementation was informed by public reverse-engineering work around the iAquaLink/Zodiac cloud protocol, particularly the [`iaqualink`](https://github.com/tekkamanendless/iaqualink) project by tekkamanendless. TCX Direct contains its own Home Assistant integration, connection supervision, telemetry mapping, persistence, and diagnostics.

## Validation

Every push and pull request runs a validation workflow that checks Ruff formatting and linting, runs the test suite, compiles the integration, validates its Home Assistant metadata, JSON files, and English translations, and confirms that the release version matches across `const.py`, `manifest.json`, README, and the changelog.
