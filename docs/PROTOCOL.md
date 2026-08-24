# AquaLink TCX protocol notes

This document records TCX cloud behavior that has been observed while developing **Jandy TCX Direct**. It is intentionally conservative: fields are listed as supported only when they have been seen in real controller diagnostics or are required by the working cloud transport.

The iAquaLink/TCX cloud protocol is unofficial and may change without notice.

## Cloud transport

TCX Direct currently uses three cloud surfaces:

1. `https://prod.zodiac-io.com/users/v1/login` for authentication.
2. `https://r-api.iaqualink.net/devices.json` for TCX discovery.
3. `wss://prod-socket.zodiac-io.com/devices` for live state streaming.

The Zodiac REST device shadow is used only as a secondary snapshot/watchdog path. TCX Direct prefers `/devices/v1/<serial>/shadow` and retains v2 as a compatibility fallback. Failure of the REST shadow does not prevent the integration from running when the WebSocket works.

## WebSocket subscription

The live socket is opened with the Zodiac ID token in the `Authorization` header and the vendor socket origin.

The integration sends a read-only Authorization subscription:

```json
{
  "action": "subscribe",
  "version": 1,
  "namespace": "authorization",
  "payload": {"userId": 12345},
  "service": "Authorization",
  "target": "<TCX serial>"
}
```

A successful initial response can contain several namespace documents (`main`, `ecm`, `filt`, `fea`, and others). Each may contain its own `state.reported` object. These must all be recursively collected and merged; stopping at the first `state.reported` loses most controller state.

Some sessions have connected successfully without sending the complete Authorization snapshot. v0.1.5 therefore retries the same read-only subscription during startup and also restores the last complete merged state from Home Assistant storage.

## Observed runtime objects

### Filtration controller: `filt0`

Observed fields:

- `st` — filtration/pump state (`0`/`1`)
- `manSpd` — configured/manual filtration speed
- `minSpd` — minimum pump RPM
- `maxSpd` — maximum pump RPM
- `spdList` — configured named speed presets when included in a full snapshot

Example preset names observed during development:

- Pool Filtration
- Spa Filtration
- Waterfall

### Pump motor controller: `ecm0`

Observed live fields:

- `st` — motor state
- `reqSpd` — requested live RPM
- `manSpd` — manual/current configured RPM
- `cmdSpd` — internal commanded RPM; **not treated as the current pump speed**
- `minSpd`
- `maxSpd`
- `spdList`

TCX Direct prefers `ecm0.reqSpd`, then `ecm0.manSpd`, then `filt0.manSpd`. If the pump state is explicitly off, the Home Assistant RPM entity reports `0` rather than a retained setpoint.

`cmdSpd` is intentionally excluded from the current-RPM selection because it has been observed holding an internal/priming value while the pump was actually running at a different requested speed.

### Pool water temperature: `water`

Observed payload shape:

```json
{
  "water": {
    "value": 328,
    "us": 1
  }
}
```

The observed `value` representation is tenths of a degree Celsius, so `328` decodes to `32.8 °C` / `91.0 °F`.

### Pool light: `auxz0`

Observed fields include:

- `st` — light state
- `currClr` — current color number
- `cmdClr`
- `lockClr`
- `statClr`
- `svdClr`
- `fr` — friendly equipment label
- `app` — application/equipment type
- `present`

Observed `currClr = 3` matched the legacy TCX client display name **Romance**. The integration currently carries the P-Series/IntelliBrite-style color-name table used by that controller behavior.

### Pool mode/valve state: `pool`

Observed `pool.st` desired/reported changes correspond with Pool Filtration mode changes. These desired-state echoes are retained in sanitized diagnostics for future control-protocol work.

## Device/configuration fields

Observed controller-level fields include:

- `firmwareVersion`
- `connectionType`
- `connectionRSSI`
- `TspBdy0.waterTempSet`
- `freezeSP`
- `site` configuration
- `equipment.ecm.ecm0` motor identification data

Sensitive identifiers and location values are redacted from Home Assistant Download Diagnostics.

## Fields not yet trusted

### Equipment air temperature

`hubAir` has been observed with an encoded/sentinel-looking value and a unit/status code that is not yet understood. TCX Direct does not interpret that value as degrees. The Equipment Air Temperature entity is disabled by default until a trustworthy live air-temperature object is identified.

### Salt-water chlorinator

The tested controller has not yet exposed a clearly identified native SWG/chlorinator object. TCX Direct deliberately does not assume an opaque object such as `fcr0` is the chlorinator. The SWG level sensor remains disabled by default until the namespace and field semantics are confirmed.

## Reliability behavior

The socket being TCP/WebSocket-open is not considered sufficient proof that live TCX state is healthy. TCX Direct tracks actual `state.reported` messages and can rebuild the subscription when:

- reported-state traffic goes stale,
- REST shadow state is demonstrably newer than WebSocket state, or
- the configured maximum WebSocket session age is reached.

Authentication refresh, reconnect backoff, state caching, and startup re-subscription are all handled inside the Home Assistant integration; no Supervisor add-on or local bridge is required.

Desired-only messages such as the recurring `freezeSP` echo do not count as fresh equipment telemetry. They are deduplicated in diagnostics so rare desired-state structures are not displaced by heartbeat-like repeats.

## External reference

The transport implementation was informed by public iAquaLink reverse-engineering work, particularly:

- https://github.com/tekkamanendless/iaqualink

This document reflects TCX Direct's own observed controller behavior and should be updated whenever a field mapping is newly confirmed.
