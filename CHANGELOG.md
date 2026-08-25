# Changelog

All notable changes to Jandy TCX Direct are documented here.

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
