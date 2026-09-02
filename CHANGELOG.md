# Changelog

All notable changes to Jandy TCX Direct are documented here.

## [0.3.3] - 2026-09-02

- Add a passive, download-only `native_schedule_trace` for separate `main` and
  `sched` schedule desired/reported fragments, their metadata, unnamespaced stream
  deltas, existing REST responses, and local subscription/schedule send attempts.
- Distinguish missing fields, explicit nulls, empty objects and truncated captures.
  Redact credentials, identifiers and schedule labels before retention; bound
  traffic and table-change history to 20 entries each and individual fragments to
  2 KiB. Retain the last Authorization/REST observation and schedule-send summary.
- Record late merged-table changes alongside the last confirmed operation without
  interpreting them as intent or changing command, confirmation or journal behavior.
  The trace is memory-only, does not poll, and is not added to sensors or recorder.
- Document the supervised duplicate-create finding and later manual-speed timeout
  coinciding with another duplicate. Keep native write testing paused. This patch
  gathers evidence; it does not fix or establish the cause of either failure.
- Advance Integration Version to 0.3.3 and its generated numeric code to 3003.
- Validate with 279 passing tests, including Home Assistant 2026.8.3 adapters.

## [0.3.2] - 2026-09-02

- Correct the REST-only native schedule snapshot assumption exposed by the first
  live empty-table read: Authorization supplied `sh: {}`, but successful REST reads
  supplied no usable schedule table.
- Default Get native schedules and acknowledgement to `websocket_authorization`,
  with no pending-write prerequisite for reads. Preview, apply preflight and final
  readback each request their own new complete Authorization snapshot.
- Retain same-connection checks, bounded waits, exact table/revision comparisons,
  opt-in/Auto gates, single-use previews and durable uncertainty before transmission.
  Missing tables, cached state, desired echoes and ordinary deltas never substitute
  for a complete snapshot; failed writes are never automatically replayed.
- Pin apply to the connection that supplied preflight, including across journal
  persistence and final readback; report snapshot provenance in preview/apply results
  and new pending-operation journals.
- Keep REST as an explicit alternate read/recovery source with request-specific
  validation. Normal native schedule operations no longer require REST schedule data.
- Add synthetic empty-table, disabled two-entry lifecycle, per-stage freshness,
  connection-change, cancellation and real WebSocket receiver regressions.
- Update action descriptions and the supervised testing/recovery guide. Keep native
  writes disabled by default and existing equipment controls/HA automations unchanged.
- Advance Integration Version to 0.3.2 and its generated numeric code to 3002.
- Validate with 256 passing tests, including Home Assistant 2026.8.3 adapters.
  Live subscription-refresh and native-write validation remain outstanding.

## [0.3.1] - 2026-09-02

- Share normalized equipment enumeration and reported numeric parsing between
  native schedules and existing controls. Preserve schedule uniqueness checks,
  motor-first RPM limits with filtration-controller fallback, and strict action input.
- Require the specific fresh REST response to contain the schedule table. A
  concurrent REST or WebSocket update cannot make an incomplete response valid.
- Add explicit `websocket_authorization` recovery reads and acknowledgements for
  a pending uncertain write. Each requests a new read-only subscription snapshot
  on the same connection; cached state, deltas, missing tables and timeouts cannot
  clear the latch. Subsequent writes still require REST.
- Persist the last acknowledgement's plan ID, time, source and reviewed revision
  alongside the journal, retaining compatibility with existing pending operations.
- Keep exact readback comparison and document why additional/normalized controller
  fields can leave a successful write uncertain and require manual review.
- Add regression coverage for normalization, uniqueness, snapshot provenance,
  connection changes, recovery persistence, strict action inputs and HA adapters.
- Keep experimental writes off by default and existing pump, Waterfall, SWG,
  automation and dashboard behavior unchanged. No live equipment validation or
  schedule migration is included.
- Advance Integration Version to 0.3.1 and its generated numeric code to 3001.
- Validate with 220 passing tests, including Home Assistant 2026.8.3 adapters.

## [0.3.0] - 2026-09-02

- Add an enabled read-only Native Schedules diagnostic sensor and fresh-read,
  preview, apply, and uncertain-write acknowledgement actions.
- Add opt-in experimental Pool Filtration schedule creation, editing,
  enabling/disabling, and deletion using captured `sh` commands.
- Require explicit bounded RPM, a fresh complete REST schedule snapshot, known
  Auto mode, single-use expiring previews, and unchanged schedule data before writes.
- Persist a write-ahead uncertainty latch before transmission. Never replay adds
  after timeout, cancellation, reconnect, restart, or repeated apply calls.
- Preserve unknown fields and unrelated equipment schedules; reject overlapping
  enabled blocks and gate unverified overnight execution. Default new entries off.
- Add synthetic lifecycle, failure/recovery, concurrency, schema, and HA adapter tests.
- Keep current pump scheduling, manual control, Waterfall restoration, SWG controls,
  and dashboard configuration unchanged. No live schedule migration or hardware
  execution testing has been performed for this build.
- Advance Integration Version to 0.3.0 and its generated numeric code to 3000.
- Validate with 168 passing tests, including Home Assistant 2026.8.3 adapters,
  and add a dedicated Home Assistant adapter CI job.

## [0.2.11] - 2026-08-30

### Fixed
- Restore the persistent `BD1_F` Pool Filtration RPM after Waterfall is turned off instead of leaving the pump at the Waterfall manual speed.
- Keep Waterfall-off control confirmation-gated and sequential: confirm the dynamically discovered `FRLY`/`WF` relay is off, then send a separate `manSpd` restoration through the dynamically discovered filtration-controller key.
- Skip the speed write safely when the relay was already off, Pool Filtration or the motor is stopped, or no confirmed `BD1_F` preset is available. A restoration failure leaves the Waterfall relay off and is reported as a distinct control failure.

### Added
- Add regressions for successful restoration, filter-key discovery, filter-controller preset fallback, stopped filtration, missing presets, restore failures, and idempotent Waterfall-off commands.

## [0.2.10] - 2026-08-30

### Added
- Add an enabled Integration Version diagnostic sensor that displays the installed semantic version and remains available independently of TCX cloud state.
- Add a sortable numeric `version_code` state attribute and include both semantic and numeric versions in downloaded diagnostics. The encoding reserves three decimal digits each for minor and patch components, so v0.2.9 is `2009` and v0.2.10 is `2010`.
- Add regression coverage for version encoding, invalid or ambiguous versions, the stable entity identifier, diagnostics exposure, and release metadata.

## [0.2.9] - 2026-08-30

### Fixed
- Cancel a pending post-prime speed alignment only when a live `state.desired` event targets the dynamically discovered filtration controller with a conflicting `manSpd`. Matching targets, unrelated desired keys, retained diagnostic history, and reported-only controller drift do not cancel the scheduled target.
- Scope live override handling to the active synchronization generation so an event associated with a superseded startup cannot cancel its replacement.

### Added
- Add a bounded, de-duplicated post-prime diagnostic trail containing the generation, scheduled target, discovered controller key, filtration and motor speed values, equipment states, derived phase, priming observation, desired override, decision, and device timestamp.
- Add regressions for dynamic controller keys, matching and unrelated desired payloads, stale desired history, superseded generations, controller-restored reported drift, and transition-history bounds.

## [0.2.8] - 2026-08-30

### Fixed
- Correct a scheduled cold start when TCX restores an older `manSpd` after priming. The deferred synchronization now waits while the motor is at its distinct priming speed, then applies the scheduled RPM instead of misclassifying the restored value as a new manual override.
- Preserve cancellation by an explicit TCX Direct manual-speed command, pump-off command, Waterfall command, preset change, or newer scheduled target while the deferred synchronization is pending.
- Target Python 3.13 in Ruff and CI so formatting retains portable parenthesized multi-exception clauses and cannot reintroduce the v0.2.7 startup failure.

### Added
- Add regression coverage for the observed 2575-to-2600 scheduled-start failure and for explicit manual-speed cancellation during priming.

## [0.2.7] - 2026-08-30

### Fixed
- Fix four Python 3.14-only parenthesis-free multi-exception clauses in `api.py`. On Python 3.13 and earlier this is a `SyntaxError`, so `api.py` failed to import and the entire integration failed to load at Home Assistant startup.

## [0.2.6] - 2026-08-29

### Added
- Add a Pool Light Color select with all 14 confirmed Jandy color and show-program names. Color changes use the observed `cmdClr` write and require the light to already be on.
- Retain the latest distinct controller-mode transitions in diagnostics with timestamps, numeric codes, labels, and data sources.

### Changed
- Map all observed controller modes: Auto, Quick Clean, Service, Time Out, and Transitioning.
- Disable writable equipment entities outside Auto mode and reject direct control calls before transmission while any known or unknown non-Auto code is active. Controller mode remains read-only and is never changed automatically.
- Use the stable reported `cmdClr` selection for pool-light color and program state, with `currClr` retained only as a compatibility fallback.

### Fixed
- Replace the provisional pool-light color table with the controller's confirmed Alpine White through Disco Tech sequence.
- Continue treating `st = 0` as the authoritative off state even when TCX retains a nonzero color code.

## [0.2.5] - 2026-08-28

### Added
- Add a read-only Controller Mode sensor. The observed `systemMode = 1` value reports `Auto`; unconfirmed values report `Unknown (code N)` and retain the raw code as an attribute.
- Add Pump Requested RPM and a derived Pump Operating Phase sensor that distinguishes Off, Priming, Running, Waterfall, and other speed transitions.
- Add the reported Freeze Protection setpoint as a read-only diagnostic sensor.
- Add Control Status with command totals, latest command, confirmation latency, and retained failure details as attributes.
- Add Last Reported Equipment State and a Live Data binary diagnostic with source/cache attributes.

### Changed
- Publish coordinator updates when commands finish so Control Status reflects confirmation failures even when TCX sends no reported-state response.
- Preserve the previous Pump Operating Phase while the existing transient-zero filter holds a contradictory stopped-motor update.

## [0.2.4] - 2026-08-28

### Fixed
- Give Pool Filtration preset writes a dedicated 45-second confirmation window and verify the fresh REST shadow once before treating a late WebSocket confirmation as a failure.
- Keep cold-start power commands strictly limited to `pool.st = 1`; a stopped pump never receives a `manSpd` write before TCX priming.
- Align `manSpd` once after TCX leaves priming and reports both requested and commanded speed at the scheduled Pool Filtration RPM.

### Changed
- Make the existing `Start pump at speed` action synchronize both the persistent Pool Filtration preset and `manSpd` immediately when the pump is already running outside priming, allowing schedule changes and explicit refreshes to keep both targets consistent without disrupting a priming transition.
- Cancel deferred post-prime alignment when the pump is stopped, Waterfall is commanded, the preset is changed, another schedule supersedes it, or a manual-speed command intervenes.

### Added
- Report command confirmation latency, historical failure details, late-confirmation recovery, and post-prime synchronization state in diagnostics.

## [0.2.3] - 2026-08-27

### Added
- Add a writable Pool Filtration Preset control backed by the confirmed `ecm0.spdList` protocol.
- Report the Pool Filtration preset and its control availability in normalized state and diagnostics.

### Fixed
- Make `Start pump at speed` write and confirm the persistent Pool Filtration preset before sending a normal pump-on command, allowing TCX to transition directly from priming to the scheduled RPM.
- Preserve the existing Spa Filtration and Waterfall presets when updating Pool Filtration.
- Remove the ineffective cold-start `manSpd` command and post-priming fallback from the recommended schedule controller while retaining normal manual-speed writes for an already-running pump.

## [0.2.2] - 2026-08-27

### Added
- Add a `Start pump at speed` Home Assistant action that sends Pool Filtration on and the requested manual RPM together in one TCX desired-state frame.
- Expose the action in Home Assistant's action editor with a Pump Power entity target and RPM field.

### Fixed
- Avoid the ineffective stopped-pump speed pre-stage sequence that TCX echoed but did not retain through priming.
- Let schedule-driven starts transition directly from the controller's priming speed to the scheduled manual speed when the combined command is honored.

## [0.2.1] - 2026-08-26

### Fixed
- Allow up to 45 seconds for pump-power reported-state confirmation, preventing a physically successful but slowly reported pump start from falsely failing a Home Assistant automation.
- Keep the 15-second confirmation window for pump speed, pool light, and Waterfall commands that report promptly.

### Added
- Report the default and pump-power confirmation timeouts in integration diagnostics.

## [0.2.0] - 2026-08-25

### Release highlights
- Consolidate the live-tested telemetry, connection supervision, diagnostics, and confirmation-gated equipment controls developed throughout the 0.1.x series.
- Include separate read-only status entities and writable controls for pump power, manual pump speed, pool-light power, Waterfall state, and Waterfall RPM.

### Fixed
- Preserve the active motor `cmdSpd` during priming and suppress brief contradictory 0 RPM transitions without delaying genuine pump-off reporting.
- Keep the writable Pump Manual Speed tied to `filt0.manSpd` while controller presets, freeze protection, Waterfall, and other runtime behavior remain visible through the live RPM and preset sensors.
- Retain last-known state across cloud interruptions and recover quiet WebSocket subscriptions without unnecessary reconnect churn.

### Changed
- Require Home Assistant 2026.8.0 or newer and align development validation with Python 3.14.
- Enforce Ruff formatting, run Home Assistant's Hassfest validator, and verify version consistency across the integration manifest, constants, README, and changelog in CI.

## [0.1.17] - 2026-08-25

### Fixed
- Suppress the controller's brief 0 RPM transition after a manual-speed change while Pool Filtration or Waterfall still requests the pump.
- Keep genuine pump-off transitions immediate and publish a persistent contradictory zero after a 90-second confirmation period.

### Added
- Add pump-zero suppression state and counters to diagnostics.

## [0.1.16] - 2026-08-25

### Added
- Add a persistent Waterfall RPM control, defaulting to 2850 RPM and constrained to the controller's reported pump limits.
- Apply Waterfall RPM after the Waterfall relay is confirmed on, and apply changes immediately while Waterfall is running.

### Changed
- Require confirmed pump-speed control and safe RPM limits before exposing the Waterfall switch, since Waterfall now coordinates the feature relay with the filtration speed.
- Turn Waterfall back off if its RPM command fails, avoiding a partially applied on-command.

## [0.1.15] - 2026-08-25

### Changed
- Rename the writable control to Pump Manual Speed and make its displayed value follow the same `filt0.manSpd` field that it writes and confirms.
- Keep `cmdSpd` exclusively on the read-only Pump RPM sensor so priming, filtration, and controller-selected runtime changes do not overwrite the manual setpoint control.

## [0.1.14] - 2026-08-25

### Changed
- Keep the writable Pump Speed value synchronized with the motor's active `cmdSpd` whenever it is within the reported RPM limits.
- Fall back to the stored manual speed when a stopped pump reports `cmdSpd = 0`, keeping the number available and writable within its valid range.

## [0.1.13] - 2026-08-25

### Added
- Add a separate Pool Light Power control while preserving Pool Light status, color number, and color name under Sensors.
- Discover the writable light object by its confirmed `JL`/`POOL_LT` type pair and require reported-state confirmation for every on/off command.

## [0.1.12] - 2026-08-25

### Added
- Add separate Pump Power and Pump Speed controls while preserving the existing read-only pump status, RPM, preset, and limit sensors.
- Add a dedicated Waterfall Status point under Sensors while retaining the Waterfall switch under Controls.
- Add per-command control, shadow rate-limit, and watchdog re-subscription counters to diagnostics.

### Fixed
- Back off REST shadow polling after HTTP 429 responses and stop marking the entire cloud unavailable when the primary WebSocket remains connected.
- Refresh a quiet Authorization subscription in place before reconnecting, reducing idle connection churn.

### Changed
- Poll the secondary REST shadow every two minutes, use a 30-minute reported-state stale window, and rotate WebSocket sessions defensively every six hours.
- Require reported-state confirmation for pump power and speed commands and constrain speed requests to the controller's reported minimum and maximum RPM.

## [0.1.11] - 2026-08-25

### Fixed
- Report the pump motor's active `cmdSpd` during priming instead of the eventual requested preset speed from `reqSpd`.
- Match the Pump Preset independently against `reqSpd`, preserving `Waterfall` while the live RPM temporarily reports the 2500 RPM priming command.

## [0.1.10] - 2026-08-25

### Fixed
- Send Waterfall `StateController` commands through the `tcx` device namespace instead of the Authorization snapshot's `fea` document grouping, which Zodiac silently ignored in the v0.1.9 live test.

### Added
- Include the sanitized last-sent control frame in diagnostics so command shape, namespace, and desired values can be verified without exposing the controller identifier or client token.

## [0.1.9] - 2026-08-25

### Added
- Add a Waterfall switch for the captured `FRLY`/`WF` feature relay.
- Send Waterfall on/off commands through the Zodiac WebSocket state-controller protocol and require matching reported-state confirmation.
- Add control success/failure counters and the last control error to diagnostics.

### Fixed
- Clear the pump preset whenever the pump is explicitly off instead of retaining the previous preset such as `Manual` or `Waterfall`.

### Changed
- Discover the Waterfall feature by its confirmed equipment type markers rather than assuming a fixed object index.

## [0.1.8] - 2026-08-24

### Fixed
- Use Home Assistant's `EntityCategory.DIAGNOSTIC` enum for all diagnostic sensors, binary sensors, and the reconnect button so Home Assistant 2026.8 accepts them during entity registration.

### Changed
- Add a regression check covering every TCX entity-category declaration.

## [0.1.7] - 2026-08-24

### Fixed
- Stop treating the REST shadow document timestamp as proof of a stale WebSocket, eliminating the roughly three-minute reconnect loop observed in v0.1.6 diagnostics.
- Clear both pool-light color values whenever the light is explicitly off instead of retaining a stale color name.

### Changed
- Centralize automatic reconnect decisions in the reported-state/session watchdog and expose per-reason reconnect counts in diagnostics.

## [0.1.6] - 2026-08-23

### Fixed
- Base WebSocket stream health and stale-subscription recovery on fresh `state.reported` traffic instead of desired-only heartbeat echoes.
- Reset per-connection monotonic freshness state so a new socket receives its full watchdog window and cannot inherit health from the previous socket.
- Mark cloud reachability false on authentication, connection, unexpected-close, and defensive supervisor failures.
- Count every unsuccessful REST-shadow request in diagnostics.
- Redact Zigbee EUI, equipment, network, and identifier-shaped diagnostic fields.

### Changed
- Deduplicate repeated desired-state payloads while retaining counts and first/last-seen timestamps.
- Remove duplicated volatile connection attributes from every entity to reduce Home Assistant recorder churn; dedicated diagnostic entities remain available.
- Add focused parser, connection-health, redaction, and diagnostics-buffer tests plus Ruff validation in CI.

## [0.1.5] - 2026-08-23

### Fixed
- Restore normalized values immediately at startup instead of showing `Unknown` until equipment changes state.
- Persist and restore the complete merged TCX reported-state document across Home Assistant restarts.
- Actively repeat the read-only Authorization subscription during startup until a usable controller snapshot is received.
- Continue merging `StateReported` deltas without clearing previously known fields.
- Correct watchdog timing so a newly opened socket receives the full stale timeout before being recycled.

## [0.1.4] - 2026-08-23

### Added
- Pool temperature mapping from TCX `water.value` telemetry.
- Pool light state from `auxz0.st`.
- Pool light color number from `auxz0.currClr`.
- Pool light color-name mapping, including captured color `3` = `Romance`.
- Pump preset reporting by matching current RPM to the controller speed list.
- Sanitized capture of recent `state.desired` echoes for future native control mapping.

### Changed
- Pump runtime state now prefers `ecm0.st`, with `filt0.st` as fallback.
- Pump RPM now prefers `ecm0.reqSpd`, then `ecm0.manSpd`, then `filt0.manSpd`; `cmdSpd` is intentionally not treated as current RPM.
- Initial Authorization parsing now merges every `state.reported` namespace rather than stopping at the first one.
- Unsupported Equipment Air Temperature and Salt Water Chlorinator Level entities are disabled by default for new installs.

## [0.1.3] - 2026-08-23

### Added
- Detailed WebSocket instrumentation in Home Assistant diagnostics.
- Message, state-message, and reported-message counters.
- Last WebSocket message/state timestamps and message metadata.
- Sanitized last raw WebSocket payload.
- Recent unique payload structures represented as value-free JSON paths/fingerprints.
- WebSocket connect/reconnect, watchdog-reconnect, shadow-request, login, and token-refresh counters.
- Separate `WebSocket Stream` health diagnostic.

### Changed
- A transport-open socket is no longer sufficient to mark the live stream healthy.
- Watchdog reconnects a socket that remains open but stops producing application data.
- RPM sensors request integer display precision.

## [0.1.2] - 2026-08-23

### Added
- Native AquaLink TCX 5.x parser for `filt0.st` and `filt0.manSpd`.
- Pump minimum and maximum RPM diagnostics.
- Wi-Fi RSSI, firmware version, connection type, and pool-temperature setpoint diagnostics.
- Separate WebSocket and cloud connectivity binary sensors.

### Fixed
- Pump RPM reports `0` when filtration is off instead of exposing a stale configured speed.

## [0.1.1] - 2026-08-23

### Changed
- Use the Zodiac v1 shadow endpoint, with fallback behavior where appropriate.
- REST shadow access is optional and can no longer block integration setup.
- Add the vendor-host `Origin` header used by current iAquaLink WebSocket clients.
- Add proactive 15-minute WebSocket rotation so a logically stale connection cannot remain frozen indefinitely.

## [0.1.0] - 2026-08-23

### Added
- Initial standard Home Assistant custom integration for AquaLink TCX.
- Direct iAquaLink/Zodiac authentication and TCX device discovery.
- Direct Zodiac cloud WebSocket connection.
- 30-second WebSocket heartbeat.
- Automatic reconnect supervisor with exponential backoff.
- Forced re-authentication after repeated failures and proactive token refresh.
- 60-second Zodiac shadow fallback/watchdog.
- Last-known normalized-state persistence across Home Assistant restarts/outages.
- Separate connection health from equipment values.
- Read-only equipment telemetry plus a diagnostic Reconnect button.
