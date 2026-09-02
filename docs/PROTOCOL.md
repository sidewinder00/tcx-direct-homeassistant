# Integration architecture and limitations

Jandy TCX Direct is an unofficial, experimental Home Assistant integration for
AquaLink TCX equipment. It is not affiliated with or supported by Jandy or Fluidra,
and it is not a safety system.

This page describes the integration at an architectural level. It is not a vendor
API reference or a guide to constructing cloud requests. Detailed wire-format
research is maintained separately from the public documentation.

## Connection and state

- Authentication and equipment discovery use the vendor's cloud services. This is
  not a local connection to the TCX controller; cloud availability is required.
- A persistent WebSocket supplies live equipment updates. A separate REST snapshot
  path provides fallback information when the service supports it.
- Connection supervision tracks actual equipment updates, not just whether the
  socket is open. Reconnects and authentication refreshes use backoff.
- REST rate-limit responses trigger a longer polling interval. A healthy live
  stream can continue while the REST path is throttled, so the two are diagnosed
  separately. Do not repeatedly reload or issue extra reads to defeat backoff.
  Version 0.3.4 extends the cooldown to all REST readers, including startup and
  timeout refreshes; see the [REST pacing summary](../README.md#rest-pacing-in-v034).
- Home Assistant stores the last known state for startup continuity. Diagnostics
  distinguish restored cache from newly received data; cached values are not proof
  of current physical equipment state.

## Equipment controls

Home Assistant exposes live measurements separately from writable settings. The
live pump RPM, requested RPM, manual speed and filtration preset are different
values and can legitimately differ during priming or a transition.

Controls are offered only for recognized equipment and use reported operating
limits. Controller Mode is read-only; the integration does not force a controller
out of Service or another non-Auto mode. Equipment commands require reported-state
confirmation rather than treating a successful network send as success.

The controller owns priming. Start Pump at Speed coordinates the filtration preset
and later speed alignment, with cancellation for recognized intervening commands.
Not every physical-panel override has been characterized. Waterfall-off restoration
uses the persistent Pool Filtration preset, not an active native schedule.

These checks reduce some risks but do not establish physical flow, correct wiring,
or safe operation. Preserve manufacturer interlocks and independent safety controls.

## Experimental native schedules

**Native schedule testing remains paused.** A supervised create produced a matching
initial readback followed by duplicate disabled schedules. A manual-speed command
also failed during the incident. Subsequent owner testing and diagnostics showed
recovery, but the command-lifecycle defect and its relationship to the speed failure
remain unresolved.

Native actions share the cloud connection and control serialization with equipment
commands. Disabling experimental writes prevents new integration schedule writes;
it does not cancel remote activity or make explicit read/preview actions passive.
Do not run those actions to investigate the incident or migrate working HA schedules.

An initial matching schedule does not prove that a command has finished processing
or cannot be processed again. The current confirmation and acknowledgement checks
do not independently establish that all outstanding schedule commands have cleared.
See [native schedule status and safety](NATIVE_SCHEDULES.md) for the current limits.

## Troubleshooting and evidence

Check live-versus-cached state, the last equipment update, controller mode, and the
latest command result separately. Transport health alone does not confirm that an
equipment command took effect.

Download diagnostics from Home Assistant when needed. The schedule trace records
existing traffic without requesting extra snapshots or issuing equipment commands.
Its bounded history can omit earlier events; redaction and truncation can also
limit what it proves. Missing evidence must not be treated as a successful cleanup.

Keep raw captures, credentials, identifiers and detailed protocol investigations
out of public issues and documentation. Follow the [security guidance](../SECURITY.md)
before sharing any diagnostic material.

## Documentation boundary

The current public guides retain behavior, safety warnings, known defects and
troubleshooting information, rather than endpoint lists, authentication recipes,
message payloads or vendor field dictionaries.

This is a documentation-only boundary, not a confidentiality guarantee. Source
code, tests, historical commits, old releases and previously obtained copies may
still reveal protocol details. This cleanup does not change licensing, repository
visibility, equipment behavior or the installed integration.

The implementation was informed by public interoperability research, including
the [iaqualink project](https://github.com/tekkamanendless/iaqualink). That reference
is an acknowledgement, not a claim of vendor approval or protocol stability.
