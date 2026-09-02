# Jandy TCX Direct

A Home Assistant custom integration for Jandy / Fluidra AquaLink TCX controllers.

> [!CAUTION]
> **Experimental personal project — not ready for general installation**
>
> This repository is published for development visibility and personal testing. The
> integration is under active development, has not been validated across different
> TCX installations, and may stop working when the unofficial cloud protocol or
> controller firmware changes.
>
> It can issue commands to pool equipment. Bugs, connectivity failures, or unexpected
> controller behavior could cause equipment to start, stop, or change settings. Do
> not rely on it as a safety control, do not bypass manufacturer safety interlocks,
> and do not use it for unattended equipment control.
>
> **No license is granted. All rights are reserved.** This is public source code, not
> an open-source release or an invitation for third-party installation.

TCX Direct connects Home Assistant directly to the iAquaLink/Zodiac cloud. It does **not** require a Supervisor add-on, separate container, local HTTP bridge, or the legacy Jandy TCX Client add-on.

> [!IMPORTANT]
> This is an unofficial personal integration built against reverse-engineered iAquaLink/TCX cloud behavior. It is not affiliated with or supported by Jandy or Fluidra.

## Current version

**v0.3.4**

Release notes and downloads: [GitHub Releases](https://github.com/sidewinder00/tcx-direct-homeassistant/releases).
Native schedule management remains experimental and disabled by default.

### Experimental native schedules

> [!WARNING]
> **Native schedule write testing is paused.** A supervised v0.3.2 add was followed
> by duplicate disabled entries after one recorded send. A later manual-speed
> request timed out while another duplicate appeared. The connection between these
> events is under investigation. Keep experimental writes off; do not migrate
> schedules or repeat create/manual-speed tests. The v0.3.3 diagnostic trace is
> evidence gathering, not a fix for duplication or failed manual control.

Native Schedules is a read-only diagnostic sensor with stored entries and
write/recovery status. Experimental editing remains **off by default**. The actions
exist for development; they do not automatically migrate the HA timetable or
replace existing automations.

Read and preview actions still make cloud requests with writes disabled, and their
network waits can delay equipment commands. Do not invoke native schedule actions
during the current pause. Disabling writes does not clear existing remote activity.

Later owner testing and diagnostics showed that ordinary controls had recovered
and the observed schedule table was empty. That is a recovery baseline, not a fix
or validation of the schedule transaction.

Version 0.3.3 adds a bounded, redacted passive trace to diagnostic downloads.
Downloading diagnostics uses existing observations; it does not request another
snapshot or send equipment commands. See the [native schedule status and safety
guide](docs/NATIVE_SCHEDULES.md) for restrictions, uncertainty handling and
[trace limitations](docs/NATIVE_SCHEDULES.md#passive-schedule-trace).

Do not replace the working HA pump controller with native scheduling. Unchanged
equipment command code does not guarantee isolation from remote schedule problems.

The integration prioritizes reliable telemetry and conservative equipment control.
Equipment is supported only where matching behavior has been observed; that does
not establish compatibility or safety across installations.

## What it currently exposes

- Pool temperature
- Pool pump state
- Pump RPM
- Pump requested RPM
- Pump operating phase (`Off`, `Priming`, `Running`, `Waterfall`, or `Transitioning`)
- Pump preset
- Controller mode, with its raw reported code retained as an attribute
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
- Integration Version with a sortable numeric `version_code` attribute
- Native Schedules diagnostic sensor with stored entries and write/recovery status
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
- Experimental native schedule read, preview, apply, and recovery actions; writes require explicit opt-in

The Pool Light Power switch and color selector require a recognized supported
light. The selector is available only while the light is on; selecting a color
does not turn it on implicitly. Waterfall and pump controls similarly require
recognized equipment. Equipment commands wait for reported-state confirmation,
rather than treating a network send as success.

Pool-light colors and programs are Alpine White, Sky Blue, Cobalt Blue, Caribbean
Blue, Spring Green, Emerald Green, Emerald Rose, Magenta, Violet, Slow Color Splash,
Fast Color Splash, America The Beautiful, Fat Tuesday, and Disco Tech. A retained
color selection does not override the reported light-off state.

Pump RPM represents live motor speed, including priming. Pump Manual Speed is a
separate writable setpoint, while Pool Filtration Preset is the persistent
filtration setting. Updating that preset preserves the other feature presets.
Requested RPM and Operating Phase help explain priming and speed transitions.

Waterfall RPM defaults to 2850 RPM. Turning Waterfall on confirms the relay and
then applies the configured speed. Turning it off confirms the relay first, then
restores the persistent Pool Filtration preset when filtration and the motor remain
running. This is not restoration from a native schedule. Adjusting Waterfall RPM
while active applies the new value immediately.

Controller Mode is read-only and recognizes Auto, Quick Clean, Service, Time Out,
and Transitioning. Unknown numeric values remain visible as unknown codes.
Equipment controls are unavailable outside Auto, and commands are checked before
transmission. The integration never forces a controller out of maintenance mode.
Freeze Protection Setpoint remains read-only and has no unit until its unit
behavior is independently established.

Integration Version is an enabled diagnostic sensor that displays the installed semantic release, currently `0.3.4`, and remains available when TCX cloud data is unavailable. Its numeric `version_code` attribute uses `major × 1,000,000 + minor × 1,000 + patch`, so v0.2.11 is `2011` and v0.3.4 is `3004` without treating a semantic version as a decimal number.

The `tcx_direct.start_pump_at_speed` action targets the Pump Power switch and accepts
an `rpm` value. It confirms the persistent filtration preset before starting a
stopped pump, allows TCX to manage priming, and aligns the manual setpoint after
priming. For an already-running pump outside priming, both settings are synchronized
immediately. It does not change Spa Filtration or Waterfall presets.

Recognized manual-speed commands, pump-off, Waterfall, preset changes and newer
scheduled targets cancel pending alignment. Ordinary reported speed drift is not
treated as an explicit override. Physical-panel override signaling remains
unverified, so not every possible panel interaction is covered.

During a brief motor-state reset while operation is still requested, Pump RPM can
hold its last valid nonzero reading for up to 90 seconds. A genuine pump-off command
still reports zero immediately; a persistent contradictory zero is not hidden
indefinitely.

Equipment air temperature is disabled by default and requires a recognized live
reading; unexplained values are not interpreted as temperatures. Native salt-water
chlorinator level remains disabled by default until supported equipment is
identified.

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

### REST pacing in v0.3.4

Version 0.3.4 shares one REST cooldown across polling, startup,
explicit reads and timeout-triggered refreshes. A read during cooldown returns a
local deferred error without another HTTP request; setup can still start the
WebSocket transport. Reads are serialized, and queued readers recheck the deadline.
They are not satisfied from an older cached response.

The server's retry minimum is not shortened by the integration's local backoff cap.
Local backoff halves only after two consecutive successful reads, down to the normal
120-second polling interval. Healthy WebSocket updates and equipment commands remain
available during REST cooldown; an unconfirmed command still times out and is not
automatically resent. Native schedule testing remains paused.

Pacing state is per client/config-entry session, not an account-wide quota manager
or persistent across reloads. Do not reload repeatedly to defeat a cooldown. Explicit
reads outside cooldown are still possible; this is not a general requests-per-second
limiter. A read queued behind another in-flight REST read may wait for that request,
but no request lock is held while sleeping through a cooldown.

Some callers also hold the equipment-control lock while waiting for REST. A
timeout-triggered refresh or an explicit native REST read can therefore delay later
equipment commands until its queued read finishes. One earlier read can involve two
20-second HTTP attempts; authentication and other queued reads can add time, so
40 seconds is not a strict end-to-end bound. This patch retains that lock ordering.

This release improves REST pacing; it does not resolve the native schedule incident.

## Development installation

Home Assistant 2026.8.0 or newer is required.

These instructions are retained for the repository owner's controlled development
environment. They are not a recommendation or supported installation path for other
systems.

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

The repository retains `hacs.json` for the owner's development installation. Its
presence does not mean the integration is ready or offered for public installation.

## Diagnostics

Home Assistant's **Download diagnostics** output includes connection health,
command results, controller-mode changes, data freshness and bounded development
traces. The integration attempts to redact credentials, tokens, device and network
identifiers, coordinates and session information.

Version 0.3.4 adds REST `http_attempt_count`, `deferred_count`,
`cooldown_remaining_seconds` and `cooldown_indefinite`. `request_count` remains the
number of logical read calls, including locally deferred calls; `failure_count`
excludes local cooldown deferrals. `rate_limit_count` counts actual vendor 429
responses from all REST callers, not repeated local deferrals. One logical read may
attempt more than one supported API version, so it can produce multiple HTTP attempts.
In-flight or cancelled calls can leave a remainder between logical calls and the
sum of successes, failures and deferrals; that remainder is not another vendor 429.
`poll_interval_seconds` describes the local backoff policy; a server deadline can
require a longer wait. An unrepresentably large server delay pauses REST for the
session and is reported as indefinite rather than emitting non-finite JSON values.

An indefinite cooldown has no automatic in-session exit. If diagnostics actually
show `cooldown_indefinite: true`, preserve them and investigate the abnormal server
response before considering a single supervised integration reload. That exceptional
recovery resets the local session; it does not establish that the server is ready.
If the condition recurs, stop retrying. Do not use reloads to bypass ordinary finite
cooldowns or repeatedly probe an indefinite one. Reloading also briefly interrupts
the integration's WebSocket connection and controls.

Post-prime diagnostics retain a limited sequence of observations and decisions.
The passive native-schedule trace also has size and history limits; a missing event
or truncated capture is not proof that an operation completed successfully.

Inspect diagnostics before sharing them. Keep raw captures and detailed protocol
research outside public issues and repository content. See [SECURITY.md](SECURITY.md)
for handling sensitive material.

## Version history

See [CHANGELOG.md](CHANGELOG.md) for the complete development history from v0.1.0 onward.

## Architecture and limitations

See the [architecture overview](docs/PROTOCOL.md) for connection design, control
limitations, troubleshooting and the public documentation boundary. Detailed
wire-format research is maintained separately from these public guides.

The transport implementation was informed by public reverse-engineering work around the iAquaLink/Zodiac cloud protocol, particularly the [`iaqualink`](https://github.com/tekkamanendless/iaqualink) project by tekkamanendless. TCX Direct contains its own Home Assistant integration, connection supervision, telemetry mapping, persistence, and diagnostics.

## Validation

Every push and pull request runs a validation workflow that checks Ruff formatting and linting, runs the test suite, compiles the integration, validates its Home Assistant metadata, JSON files, and English translations, and confirms that the release version matches across `const.py`, `manifest.json`, README, and the changelog.

## License and contributions

No license is granted for this repository. All rights are reserved by the repository
owner, except for the limited rights necessarily provided by GitHub's Terms of
Service. Permission is not granted to copy, modify, distribute, sublicense, or create
derivative works from this project.

External contributions are not currently accepted. A license and contribution policy
will be added if the project is opened for general use in the future.
