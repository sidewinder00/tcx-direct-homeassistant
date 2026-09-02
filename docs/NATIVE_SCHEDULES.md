# Experimental native schedules — v0.3.0

This is a development feature, not a migration or a replacement for the existing
HA pump controller. Publishing this release does not install it in Home Assistant
or validate it on live equipment. Existing pump, Waterfall, SWG and dashboard
behavior is unchanged. Native schedule writes remain disabled by default.

## What this build does

- Reads schedules into the **Native Schedules** diagnostic sensor. Its state is
  `read_only`, `ready` (write option enabled), `needs_review`, `storage_error`, or
  `unknown`; `ready` does not assert equipment readiness or recent telemetry.
- Exposes schedules, raw fields, revision, last observation, cache flag, and any
  uncertain operation in attributes. Other equipment and unfamiliar formats stay
  visible, not silently discarded. Default speed (`ar: 0`) is not shown as 0 RPM.
- Provides four actions: `get_native_schedules`, `preview_native_schedule`,
  `apply_native_schedule`, and `acknowledge_native_schedule_write`.
- Creates, updates, enables/disables and deletes **one Pool Filtration entry** at
  a time, only after a preview and explicit apply. New entries default disabled.
- Writes through the existing cloud `tcx` / `StateController` transport. This is
  not a new local/offline connection to the controller.

Native schedule execution and offline persistence still need hardware validation.
The captured app commands establish protocol shapes, not proof that this build
has successfully written to a real controller.

## Restrictions and safeguards

1. **Writes default off.** Opt in under the integration's Configure/options dialog
   only for supervised testing. Changing this option does not reload the integration
   or affect equipment. User-initiated apply/recovery actions require an HA admin;
   HA's internal automation context is also accepted by its admin-service helper.
2. Fresh reads require a successful **complete REST shadow containing `sh`**.
   Cached state and a connected socket alone cannot authorize a write. If REST is
   unavailable or rate-limited, fail closed. Normal equipment controls and passive
   schedule observation still work over WebSocket.
3. Preview IDs last five minutes, are entry-specific, are consumed once, and do
   not survive reload/restart. Applying re-reads the table and rejects any change
   since preview. It sends only the target `sh` entry, preserving unknown fields
   in an edited entry and leaving unrelated schedules untouched.
4. Explicit whole-number RPM must be within current reported pump limits. Writes
   using `ar: 0` are unsupported. Existing default-speed entries can still be read,
   disabled or deleted; updating/enabling them requires an explicit RPM first.
5. Fixed times use controller-local `HH:MM`. No UTC or HA-timezone conversion is
   applied. `weekday_codes` is a list of raw integers 0–6; only Friday = 5 has been
   individually matched to the app. All-days `[0,1,2,3,4,5,6]` was also captured.
   Other individual day mappings remain unverified and are not labeled as facts.
6. Equal start/end times are rejected. Overnight/midnight-crossing entries can be
   stored **disabled only** pending validation. Enabled same-day overlapping pool
   entries are rejected; adjacent boundaries are accepted. If an existing enabled
   pool entry cannot be interpreted safely, enabling another is blocked.
7. Known Auto mode and an active WebSocket are required. Service, Time Out, Quick
   Clean, unknown modes and missing mode data are never overridden.
8. Before transmission, a separate HA Store journal records an uncertain operation.
   A timeout, cancellation, storage error, disconnect, or failed verification leaves
   writes blocked for review. Nothing retries or rolls back a controller command.
   The latch survives restart; it is separate from delayed telemetry cache writes.
9. Success requires a new matching reported update and then fresh shadow readback.
   A desired-state echo alone is not confirmation. If other entries change during
   the operation, stop for review instead of overwriting them.

The revision is a **local fingerprint, not an atomic server compare-and-swap**.
An external app can still race a command between reads; the vendor does not expose
a confirmed transaction/idempotency mechanism here. Do not edit schedules from
another client during apply. Never interpret a transport confirmation as proof
that the motor physically ran at the scheduled speed.

## First supervised development workflow

Use Developer tools → Actions. Choose the TCX config entry in the form. Replace
the placeholders below with the selected config-entry ID and returned preview ID;
neither is a controller password or serial number.

### Read

```yaml
action: tcx_direct.get_native_schedules
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
response_variable: native_schedules
```

### Preview a disabled Friday test entry

```yaml
action: tcx_direct.preview_native_schedule
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  operation: create
  start: "11:00"
  end: "11:15"
  weekday_codes: [5]
  rpm: 2650
  enabled: false
response_variable: schedule_preview
```

Inspect the returned `desired`, `before`, and `after` fields. Preview performs a
read but does **not** send a schedule or equipment write. In the Actions UI use
its response display; `response_variable` is useful in scripts/automations.

### Apply once

After explicitly opting into experimental writes:

```yaml
action: tcx_direct.apply_native_schedule
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  plan_id: RETURNED_PREVIEW_ID
response_variable: schedule_result
```

Use the returned `schedule_id`, never assume the new slot is `1`. Inspect the
entry in iAquaLink and download diagnostics. Do not automatically chain preview
and apply in a recurring automation during this development phase.

For an edit, preview with `operation: update`, the returned `schedule_id`, and
only the changed fields (for example `rpm: 2700`). For `enable`, `disable`, and
`delete`, send only `operation` and `schedule_id` plus the config-entry ID. Then
review and apply the new preview ID. Enabling an active interval can immediately
start equipment; it belongs in a separately supervised operating test.

## If a write is uncertain

Do not retry the add, restart to clear it, or delete the journal.

1. Read with `get_native_schedules`; inspect `pending_write` and the whole table.
2. Compare with iAquaLink and check whether the change occurred. If the controller
   is offline, wait; do not clear the block based only on cached data.
3. After reviewing, use `acknowledge_native_schedule_write` with the pending
   `plan_id` and the current read's `revision`. This only clears the HA latch;
   it does not retry, undo, or send a controller command.
4. If a change is still needed, make a new preview against the current table.
   A failed call might have reached TCX; duplicate-looking entries need explicit
   inspection, not an automatic "cleanup" or recreation.

## Validation status and next gates

Implemented offline tests cover captured CRUD shapes, option gates, explicit RPM,
raw weekday validation, disabled midnight entries, adjacent/overlapping blocks,
freshness, field preservation, stale previews, duplicate/repeated apply, serialized
operations, storage failure, cancellation, timeout/restart recovery, and HA adapters.
Synthetic fixtures contain no private diagnostic dumps or controller identifiers.

Still required with the owner supervising:

- Create two disabled entries from HA; edit one and verify the other is untouched.
- Verify remaining weekday mappings and the meaning of the default-speed sentinel.
- Verify native create/edit/disable/delete against the app and controller, including
  capacity failures. Slot numbers observed in captures do not establish capacity.
- Separately test start/prime/run, adjacent speed changes, stop, and midnight rules.
- Establish manual override and Waterfall resume priority before migrating. The
  current Waterfall-off code restores the global `BD1_F` preset, not a native slot.
- Prove execution with HA unavailable, then independently test internet loss and
  restart persistence under an agreed supervised plan. Do not power-cycle now.
- Design UV changes so yesterday's persistent choice is not silently reused when
  HA is offline. Keep CircuPool safety monitoring/control separate.

Leave the existing HA timetable, pump controller and SWG safety controls unchanged
for non-running/disabled tests. A live execution test needs its own agreed plan to
prevent HA and TCX from competing. No dashboard replacement or automatic migration
is included in this build.
