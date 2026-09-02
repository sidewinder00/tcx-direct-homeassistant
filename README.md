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

**v0.3.3**

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

Native Schedules is a read-only diagnostic sensor with the stored entries and
write/recovery status as attributes. Native schedule editing is **off by default**.
The new Get / Preview / Apply actions allow controlled testing of individually
reviewed Pool Filtration schedule changes; they never migrate the HA timetable,
execute a schedule from HA, or change existing automations automatically.

Version 0.3.2 corrected the REST-only schedule-read assumption exposed during the first
live empty-table check. Normal reads, previews, apply preflight/readback and recovery
now request new complete WebSocket Authorization snapshots. They do not use an old
cached table or interpret missing data as empty. REST remains an explicit alternate
read/recovery source only when its own response contains the table; it is no longer
a prerequisite for schedule writes. Opt-in, Auto mode, exact confirmation, journal
persistence and conflict checks remain in place.

Do not resume the supervised create/edit/delete workflow until the duplicate-add
behavior and manual-speed failure are understood. This patch does not change
pump/SWG automations or equipment command code, but unchanged code does not guarantee
isolation from remote schedule-state problems. Turning native writes off prevents
new integration schedule writes; it does not clear remote state. The passive trace
runs with writes off; downloading diagnostics does not request another snapshot
or send equipment commands.

Version 0.3.3 adds a redacted, bounded `native_schedule_trace` section to
downloaded diagnostics only. It records separate namespace fragments and later
table changes using traffic the integration already receives. See the
[trace details and limitations](docs/NATIVE_SCHEDULES.md#passive-schedule-trace).

Use the [native schedule development guide](docs/NATIVE_SCHEDULES.md) for the
preview/apply workflow, restrictions, uncertain-write recovery, and staged tests.
Do not replace the working HA pump controller with this experimental feature yet.

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

The Pool Light Power switch and color selector become available only when the controller reports a light identified by the confirmed `JL`/`POOL_LT` type pair. The color selector is available only while the light is on; selecting a color never turns the light on implicitly. The Waterfall switch similarly requires the confirmed `FRLY`/`WF` feature relay, and pump controls require the confirmed Pool Filtration and filtration-controller objects. Commands use the Zodiac WebSocket state-controller protocol in the TCX device namespace and must be confirmed by the controller's reported state before Home Assistant reports success.

Pool-light colors and programs use the confirmed sequence: Alpine White, Sky Blue, Cobalt Blue, Caribbean Blue, Spring Green, Emerald Green, Emerald Rose, Magenta, Violet, Slow Color Splash, Fast Color Splash, America The Beautiful, Fat Tuesday, and Disco Tech. The selected program comes from `cmdClr`; `st = 0` remains authoritative for Off even when TCX retains the last nonzero color code.

Pump RPM reports the active motor `cmdSpd`, including priming and other controller-selected runtime changes. Pump Manual Speed separately displays and writes the filtration controller's `manSpd`, so live RPM changes do not overwrite the writable setpoint. Pool Filtration Preset displays and writes only the `BD1_F` entry in `ecm0.spdList`; the complete list is sent as required by TCX while the Spa Filtration and Waterfall entries are preserved unchanged. Waterfall RPM defaults to 2850 RPM; turning Waterfall on confirms the feature relay and then applies this value to the dynamically discovered filtration controller's `manSpd`. Turning Waterfall off confirms the relay first, then restores `manSpd` from the current persistent `BD1_F` Pool Filtration preset when Pool Filtration and the motor remain running. Changing Waterfall RPM while the feature is active applies the new value immediately.

Pump Requested RPM reports `ecm0.reqSpd` independently from the active command. Pump Operating Phase compares requested, commanded, and configured priming RPM and also considers the confirmed Waterfall relay, allowing priming and other speed transitions to be shown without changing the writable speed control.

Controller Mode is deliberately read-only and maps the observed values `1 = Auto`, `2 = Quick Clean`, `3 = Service`, `4 = Time Out`, and `5 = Transitioning`. Any other numeric value is retained as `Unknown (code N)`. Writable equipment entities become unavailable outside Auto, and a direct action or service call is rejected before transmission when any known or unknown non-Auto code is active. TCX Direct never forces the controller out of a local maintenance mode. Freeze Protection Setpoint exposes the reported `freezeSP` value without inferring an unconfirmed unit.

Integration Version is an enabled diagnostic sensor that displays the installed semantic release, currently `0.3.3`, and remains available when TCX cloud data is unavailable. Its numeric `version_code` attribute uses `major × 1,000,000 + minor × 1,000 + patch`, so v0.2.11 is `2011` and v0.3.3 is `3003` without treating a semantic version as a decimal number.

The `tcx_direct.start_pump_at_speed` action targets the Pump Power switch and accepts an `rpm` value. It synchronizes the persistent Pool Filtration preset with a dedicated 45-second confirmation window and one fresh-shadow verification if the live confirmation is late. A stopped pump then receives only the normal `pool.st = 1` power command; no speed is combined with the start frame. TCX owns priming, after which TCX Direct aligns `manSpd` to the scheduled RPM as soon as the running motor leaves its distinct priming speed. This also corrects an older manual speed that TCX may restore at the end of priming. Waterfall, a stopped pump, an explicit TCX Direct manual-speed command, or a conflicting live `state.desired` manual-speed command from another client cancels that deferred alignment. The live command must target the dynamically discovered filtration-controller key; unrelated desired-state traffic and reported-only drift do not cancel it. For an already-running pump outside the priming transition, the action synchronizes both the Pool Filtration preset and manual speed immediately; a refresh during priming remains deferred until priming ends. The action never changes Spa Filtration or Waterfall presets.

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

Home Assistant's **Download diagnostics** output is deliberately detailed because the TCX protocol is still being mapped. Sensitive values such as credentials, tokens, controller identifiers, coordinates, MAC addresses, and session identifiers are redacted.

Diagnostics include the installed semantic version and numeric version code, WebSocket message counts, recent payload structures, recent distinct controller-mode transitions, reconnect-reason counts, Authorization/bootstrap activity, shadow polling, authentication refreshes, and the sanitized merged state used by Home Assistant. Post-prime synchronization includes a bounded, de-duplicated transition trail with its generation, target, dynamically discovered filter key, relevant motor speeds and states, priming phase, live desired override, and decision.

Recurring identical desired-state echoes are deduplicated so unusual protocol events remain visible. Hardware and network identifiers—including Zigbee identifiers—are redacted before diagnostics are exported.

## Version history

See [CHANGELOG.md](CHANGELOG.md) for the complete development history from v0.1.0 onward.

## Protocol notes

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the observed TCX namespaces, field mappings, startup behavior, and fields that are intentionally not decoded yet.

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
