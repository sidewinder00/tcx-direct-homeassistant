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

v0.1.12 introduced the confirmed `F_CTRL`/`FILT` object as the Pump Speed control
target and writes `manSpd`. The requested value is restricted to the reported
`minSpd`/`maxSpd` range and is considered successful only when the same
`manSpd` is reported back. Subsequent live testing confirmed that a 2000 RPM
manual write was applied by the controller.

v0.1.14 temporarily made the writable Pump Speed entity display the active
`ecm0.cmdSpd` whenever that value was inside the controller's reported limits,
while leaving the bounded `filt0.manSpd` write and confirmation path unchanged.

Live testing showed that `cmdSpd` can progress through priming, filtration,
and another controller-selected speed without a new manual command. v0.1.15
therefore names the writable entity Pump Manual Speed and makes its readback
the same `filt0.manSpd` field used by its write-confirmation path. `cmdSpd`
remains the exclusive source for the separate read-only Pump RPM sensor.

Example preset names observed during development:

- Pool Filtration
- Spa Filtration
- Waterfall

### Pump motor controller: `ecm0`

Observed live fields:

- `st` — motor state
- `reqSpd` — requested preset RPM
- `manSpd` — manual/current configured RPM
- `cmdSpd` — active motor command RPM, including the priming phase
- `minSpd`
- `maxSpd`
- `spdList`

For the live Pump RPM sensor, TCX Direct prefers `ecm0.cmdSpd`, then
`ecm0.reqSpd`, `ecm0.manSpd`, and `filt0.manSpd`. During priming, `cmdSpd`
reports the active priming speed while `reqSpd` retains the selected preset's
eventual speed. Preset matching therefore uses `reqSpd` independently. If the
pump state is explicitly off, the RPM entity reports `0` rather than a retained
setpoint.

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

The tested controller identifies this object with `et: "JL"` and
`app: "POOL_LT"`. Captured official-client traffic changed only `st` for
normal light power operation:

```json
{"auxz0": {"st": 1}}
{"auxz0": {"st": 0}}
```

v0.1.13 discovers the light object from that type pair instead of assuming its
index, sends the desired state through the `tcx` device namespace, and waits
for the requested `st` value to be reported back. Light color remains a
read-only sensor because a color-write command has not yet been captured and
validated.

### Pool mode/valve state: `pool`

Observed `pool.st` desired/reported changes correspond with Pool Filtration mode changes. v0.1.12 identifies this object by the `V_POS`/`POOL_M` type pair and uses it for the Pump Power control. The command is considered successful only after the requested `pool.st` value is reported back. This write mapping remains provisional until live validation.

### Waterfall feature relay: `fcr0`

The tested controller reports its waterfall as a feature relay with the
following identifying fields:

```json
{
  "fr": "Waterfall",
  "et": "FRLY",
  "app": "WF",
  "jv": "jva1",
  "ar": 3,
  "st": 0
}
```

`st` is the live on/off state. Captured official-client traffic changed only
that field for normal operation:

```json
{"fcr0": {"st": 1}}
{"fcr0": {"st": 0}}
```

The controller reported the matching state within approximately one second.
Pump action and valve positioning are coordinated by the controller; preset
index `ar: 3` matches the configured Waterfall pump speed of 2850 RPM on the
tested system.

TCX Direct sends this desired state through the Zodiac WebSocket `setState`
action in the `tcx` device namespace. The Authorization snapshot's `fea`
member is only a state-document grouping; using it as the write namespace was
silently ignored by Zodiac during the v0.1.9 live test. A control call is
successful only after the matching `fcr0.st` value is received in reported
state. The object key is discovered from the `FRLY`/`WF` type pair rather than
assumed to be `fcr0`.

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

The socket being TCP/WebSocket-open is not considered sufficient proof that live TCX state is healthy. TCX Direct tracks actual `state.reported` messages and:

- refreshes the Authorization subscription in place after 30 minutes without reported-state traffic,
- reconnects only when that refresh is not confirmed,
- rotates the WebSocket defensively after six hours.

REST shadow polling updates cached state but does not independently rotate the WebSocket. The normal interval is two minutes. HTTP 429 responses trigger exponential backoff up to 30 minutes and are tracked independently without marking the cloud unavailable while the WebSocket remains connected. The shadow document timestamp can advance without an underlying equipment-state change, so reconnect decisions remain centralized in the reported-state watchdog.

Authentication refresh, reconnect backoff, state caching, and startup re-subscription are all handled inside the Home Assistant integration; no Supervisor add-on or local bridge is required.

Desired-only messages such as the recurring `freezeSP` echo do not count as fresh equipment telemetry. They are deduplicated in diagnostics so rare desired-state structures are not displaced by heartbeat-like repeats.

## External reference

The transport implementation was informed by public iAquaLink reverse-engineering work, particularly:

- https://github.com/tekkamanendless/iaqualink

This document reflects TCX Direct's own observed controller behavior and should be updated whenever a field mapping is newly confirmed.
