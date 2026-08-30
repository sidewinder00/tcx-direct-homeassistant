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

Beginning with v0.2.5, Pump Requested RPM exposes `ecm0.reqSpd` independently.
Pump Operating Phase is derived conservatively from `ecm0.st`, `cmdSpd`,
`reqSpd`, `prmSpd`, and the confirmed Waterfall relay. A running pump is
reported as Priming only when the active command matches `prmSpd` and differs
from the requested RPM. Other mismatches report Transitioning rather than
guessing an undocumented controller state.

Starting in v0.1.17, a transition filter holds the last valid nonzero RPM for
up to 90 seconds when `ecm0` briefly reports a stopped motor while Pool
Filtration, `filt0`, or Waterfall still requests operation. A new nonzero
`cmdSpd` replaces the held value immediately. A genuine pump-off state is not
delayed, and a contradictory zero that persists beyond the grace period is
published so a real equipment problem is not hidden.

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
- `currClr` — current physical color/phase
- `cmdClr` — selected color or show program
- `lockClr`
- `statClr`
- `svdClr`
- `fr` — friendly equipment label
- `app` — application/equipment type
- `present`

The controller and official client use this confirmed `cmdClr` sequence:

| Code | Color or program |
| ---: | --- |
| 1 | Alpine White |
| 2 | Sky Blue |
| 3 | Cobalt Blue |
| 4 | Caribbean Blue |
| 5 | Spring Green |
| 6 | Emerald Green |
| 7 | Emerald Rose |
| 8 | Magenta |
| 9 | Violet |
| 10 | Slow Color Splash |
| 11 | Fast Color Splash |
| 12 | America The Beautiful |
| 13 | Fat Tuesday |
| 14 | Disco Tech |

`cmdClr` is the selected color/program and is therefore the stable source for
Home Assistant. `currClr` can change internally during an animated program and
is only a compatibility fallback when `cmdClr` is absent. `st = 0` is the
authoritative Off state; TCX can retain nonzero `cmdClr`, `currClr`, and
`svdClr` values while the light is off.

The tested controller identifies this object with `et: "JL"` and
`app: "POOL_LT"`. Captured official-client traffic changed only `st` for
normal light power operation:

```json
{"auxz0": {"st": 1}}
{"auxz0": {"st": 0}}
```

TCX Direct discovers the light object from that type pair instead of assuming
its index, sends the desired state through the `tcx` device namespace, and
waits for the requested `st` value to be reported back.

Captured official-client traffic changes the selected color/program with:

```json
{"auxz0": {"cmdClr": 3}}
```

v0.2.6 exposes this as a select while the light is already on. It dynamically
uses the discovered light key, sends only `cmdClr`, and confirms against the
reported `cmdClr`. It does not send `rstClr` and does not turn the light on as
a side effect of selecting a color.

### Pool mode/valve state: `pool`

Observed `pool.st` desired/reported changes correspond with Pool Filtration mode changes. v0.1.12 identifies this object by the `V_POS`/`POOL_M` type pair and uses it for the Pump Power control. The command is considered successful only after the requested `pool.st` value is reported back. Subsequent live testing confirmed both the command path and reported-state confirmation on the tested controller.

The tested cloud stream can take approximately 25 seconds to publish the
reported `pool.st` confirmation even though the pump starts immediately.
Beginning with v0.2.1, Pump Power therefore uses a dedicated 45-second
confirmation window. Other equipment controls retain the 15-second window.

Live tests also showed that a standalone `filt0.manSpd` desired write made
while the pump is stopped is echoed by Zodiac but is not retained by the TCX
through its priming cycle. v0.2.2 tested sending both requested values in one
`StateController` frame:

```json
{
  "pool": {"st": 1},
  "filt0": {"manSpd": 2575}
}
```

The tested controller accepted this frame and started, but still selected its
persistent 1100 RPM Pool Filtration preset after priming. A later manual-speed
recovery write also failed to confirm.

Official iAquaLink traffic captured while changing Feature Speeds showed that
the persistent presets are written as the complete `ecm0.spdList`:

```json
{
  "ecm0": {
    "spdList": [
      {"name": "Pool Filtration", "speed": 2525, "app": "BD1_F", "ar": 1},
      {"name": "Spa Filtration", "speed": 2525, "app": "BD2_F", "ar": 2},
      {"name": "Waterfall", "speed": 2850, "app": "WF", "ar": 3}
    ]
  }
}
```

The app's save included every slider value and changed Spa Filtration from the
2750 RPM seen in preceding diagnostics to 2525 RPM even though only the Pool
slider was intentionally adjusted. Beginning with v0.2.3, TCX Direct avoids
that side effect: it exposes the `BD1_F` value as Pool Filtration Preset, copies
the currently reported list, changes only the `BD1_F` speed, sends the complete
list, and confirms that value from reported state. Spa Filtration and Waterfall
are preserved. Start Pump at Speed performs that confirmed preset write first
and then sends only the normal power command:

```json
{"pool": {"st": 1}}
```

The live test confirmed that TCX then transitions from its 2500 RPM priming
command directly to the new Pool Filtration preset without a startup
`filt0.manSpd` write.

A later scheduled start exposed an important confirmation-timing edge case.
The `ecm0.spdList` desired write reached the controller, but its reported-state
confirmation arrived after the original 15-second control timeout. The start
transaction therefore stopped before sending `pool.st = 1`; the automation's
bounded retry ran 2 minutes 45 seconds later, saw that the preset had finally
been reported, skipped a duplicate preset write, and sent only the normal
power command. The pump then primed and settled at the correct RPM.

Beginning with v0.2.4, Pool Filtration preset writes use a dedicated 45-second
confirmation window. If the WebSocket confirmation is still late, TCX Direct
performs one fresh REST-shadow read and accepts the command when `BD1_F`
matches there. The actual cold-start frame remains exactly:

```json
{"pool": {"st": 1}}
```

The tested controller does not reliably retain a stopped-pump `filt0.manSpd`
write through priming, so v0.2.4 never sends that command while stopped. The
initial implementation waited for both `ecm0.reqSpd` and `ecm0.cmdSpd` to equal
the scheduled RPM before aligning `filt0.manSpd`.

A later 2600 RPM scheduled start showed TCX restoring a previously used 2575
RPM manual value as priming ended, even though the persistent `BD1_F` preset
already contained 2600. The old synchronization treated that restoration as
an intervening manual change and stopped, leaving the pump at 2575. Beginning
with v0.2.8, synchronization waits while `cmdSpd` equals the distinct `prmSpd`
and differs from `reqSpd`. Once the running motor leaves that priming state, it
writes and confirms the scheduled `filt0.manSpd`, correcting any stale value
restored by TCX. If `prmSpd` is unavailable, the conservative fallback still
requires both requested and commanded speeds to reach the target.

The pending alignment is cancelled rather than overriding Waterfall, an off
command, an explicit TCX Direct manual-speed command, a direct preset change,
or a newer scheduled target. When the pump is already running outside priming,
the same action writes and confirms both the persistent Pool Filtration preset
and `manSpd` immediately. A schedule refresh received during priming remains
deferred until the motor leaves the distinct priming speed.

### Controller operating mode: `systemMode`

The controller reports a top-level numeric `systemMode`. Direct physical-panel
testing confirmed:

| Code | Controller mode |
| ---: | --- |
| 1 | Auto |
| 2 | Quick Clean |
| 3 | Service |
| 4 | Time Out |
| 5 | Transitioning |

Other numeric values are retained and displayed as `Unknown (code N)`. TCX
Direct does not write `systemMode` or automatically override a local
maintenance lockout. Starting in v0.2.6, equipment controls are unavailable
outside Auto and the client checks the latest reported code immediately before
every outgoing equipment command. Any known or unknown non-Auto value rejects
the command locally before transmission. Diagnostics retain the latest 20
distinct mode transitions with their observation time, source, code, and
label.

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
Valve positioning is coordinated by the controller. Preset index `ar: 3`
matches the configured Waterfall pump speed of 2850 RPM on the tested system,
but live testing with Home Assistant-managed filtration speeds showed that the
controller did not reliably apply that preset when the feature relay turned
on.

TCX Direct sends this desired state through the Zodiac WebSocket `setState`
action in the `tcx` device namespace. The Authorization snapshot's `fea`
member is only a state-document grouping; using it as the write namespace was
silently ignored by Zodiac during the v0.1.9 live test. A control call is
successful only after the matching `fcr0.st` value is received in reported
state. The object key is discovered from the `FRLY`/`WF` type pair rather than
assumed to be `fcr0`.

Starting in v0.1.16, TCX Direct exposes a persistent Waterfall RPM number that
defaults to 2850 RPM and is constrained by `minSpd`/`maxSpd`. After the relay's
on-state is confirmed, the integration writes the configured value to
`filt0.manSpd` and requires the normal pump-speed confirmation. Changing the
Waterfall RPM number while the relay is active applies the new speed
immediately. If the speed command fails during turn-on, the integration tries
to return the relay to off rather than leaving a partially applied command.

## Device/configuration fields

Observed controller-level fields include:

- `firmwareVersion`
- `connectionType`
- `connectionRSSI`
- `TspBdy0.waterTempSet`
- `freezeSP`
- `site` configuration
- `equipment.ecm.ecm0` motor identification data

v0.2.5 and later expose `freezeSP` as a read-only diagnostic. The captured controller
reported `33`; the entity deliberately omits a unit until the field's unit
behavior is independently confirmed. Control of this value is not implemented.

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

Home Assistant also exposes the time of the most recent WebSocket
`state.reported` update separately from generic WebSocket traffic. A Live Data
binary diagnostic distinguishes current WebSocket/shadow state from restored
cache, and Control Status reports the latest command outcome independently from
transport connectivity.

## External reference

The transport implementation was informed by public iAquaLink reverse-engineering work, particularly:

- https://github.com/tekkamanendless/iaqualink

This document reflects TCX Direct's own observed controller behavior and should be updated whenever a field mapping is newly confirmed.
