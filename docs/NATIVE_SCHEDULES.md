# Experimental native schedules — v0.3.2

This is a development feature, not a migration or a replacement for the existing
HA pump controller. Publishing this release does not install it in Home Assistant
or validate it on live equipment. Existing pump, Waterfall, SWG and dashboard
behavior is unchanged. Native schedule writes remain disabled by default.

## What this build does

- Reads schedules into the **Native Schedules** diagnostic sensor. Its state is
  `read_only`, `ready` (write option enabled), `needs_review`, `storage_error`, or
  `unknown`; `ready` does not assert equipment readiness or recent telemetry.
- Exposes schedules, raw fields, revision, last observation, cache flag, any
  uncertain operation, and the last acknowledgement in attributes. Other equipment
  and unfamiliar formats stay visible, not silently discarded. Default speed
  (`ar: 0`) is not shown as 0 RPM.
- Provides four actions: `get_native_schedules`, `preview_native_schedule`,
  `apply_native_schedule`, and `acknowledge_native_schedule_write`.
- Creates, updates, enables/disables and deletes **one Pool Filtration entry** at
  a time, only after a preview and explicit apply. New entries default disabled.
- Writes through the existing cloud `tcx` / `StateController` transport. This is
  not a new local/offline connection to the controller.

Native schedule execution and offline persistence still need hardware validation.
The captured app commands establish protocol shapes, not proof that this build
has successfully written to a real controller.

## Why the snapshot source changed

The first v0.3.1 empty-table check found a complete Authorization schedule snapshot
with `sh: {}`, while successful REST reads supplied no usable schedule table. The
REST-only prerequisite prevented even the initial read. This was a transport
assumption in the integration, not a reason to create a schedule in the app.

Version 0.3.2 uses **newly requested WebSocket Authorization snapshots** for normal
reads, previews, apply preflight/readback and acknowledgement. It never promotes
the old cached table to a fresh result. This deliberately replaces v0.3.1's
REST-only write prerequisite; it does not remove the freshness or write gates.
Explicit REST reads/acknowledgements remain available when REST actually supplies
the schedule table, with no automatic fallback between sources.

## Restrictions and safeguards

1. **Writes default off.** Opt in under the integration's Configure/options dialog
   only for supervised testing. Changing this option does not reload the integration
   or affect equipment. User-initiated apply/recovery actions require an HA admin;
   HA's internal automation context is also accepted by its admin-service helper.
2. Every normal read, preview, apply preflight and final readback requests a new
   **complete Authorization snapshot containing a `sh` object**. The request and
   received snapshot must use the same current WebSocket, with a 20-second timeout.
   Cached tables, ordinary reported deltas, desired echoes and REST observations
   cannot fulfill that wait. An explicitly empty `sh: {}` is valid; missing or
   non-object `sh` is not. If no complete snapshot arrives, fail closed without
   trying another source. A connection alone does not establish freshness.
3. Preview IDs last five minutes, are entry-specific, are consumed once, and do
   not survive reload/restart. Applying re-reads the table and rejects any change
   since preview. It sends only the target `sh` entry, preserving unknown fields
   in an edited entry and leaving unrelated schedules untouched.
4. Explicit whole-number RPM must be within current reported pump limits. Writes
   using `ar: 0` are unsupported. Existing default-speed entries can still be read,
   disabled or deleted; updating/enabling them requires an explicit RPM first.
   Reported numeric strings and equipment markers use the same normalization as
   pump control. Limits prefer the motor, with per-field filter-controller fallback.
   Exactly one matching pool and filter controller is required; normalization does
   not relax that uniqueness check. Action inputs remain strict: booleans for
   `enabled`, integer weekday codes, and whole-number RPM (no numeric strings).
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
   The apply operation stays on the connection that supplied its fresh preflight;
   changing connections while saving the journal or verifying the write stops it.
8. Before transmission, a separate HA Store journal records an uncertain operation.
   A timeout, cancellation, storage error, disconnect, or failed verification leaves
   writes blocked for review. Nothing retries or rolls back a controller command.
   The latch survives restart; it is separate from delayed telemetry cache writes.
9. Success requires a new matching reported update and then a separately requested
   complete Authorization readback.
   A desired-state echo alone is not confirmation. If other entries change during
   the operation, stop for review instead of overwriting them. Exact entry equality
   is intentional: if TCX adds or normalizes fields, a write that actually succeeded
   can still return `outcome_uncertain`. Inspect and acknowledge it; do not replay it.

The revision is a **local fingerprint, not an atomic server compare-and-swap**.
An external app can still race a command between reads; the vendor does not expose
a confirmed transaction/idempotency mechanism here. Do not edit schedules from
another client during apply. Never interpret a transport confirmation as proof
that the motor physically ran at the scheduled speed.

Authorization freshness means a complete snapshot was **received after the new
subscription request was registered**, on the same connection. No confirmed vendor
request/response correlation token is available; an already-in-flight Authorization
response cannot be distinguished from a reply to that particular subscription.
This is not an independent REST cross-check or proof of physical equipment state.

Explicit reads, previews, applies and acknowledgements share the equipment control
lock, including their network waits. Do not poll these actions from an automation:
they can delay a concurrent equipment command. The passive Native Schedules sensor
uses existing received telemetry and does not issue extra requests.

## First supervised development workflow

Create, edit and delete through **Home Assistant**; use iAquaLink only to inspect
the results. Leave existing HA schedules and automations unchanged. Start with
experimental writes off and verify the read before opting into any apply action.

Use Developer tools → Actions. Choose the TCX config entry in the form. Replace
the placeholders below with the selected config-entry ID and returned preview ID;
neither is a controller password or serial number.

### Read

```yaml
action: tcx_direct.get_native_schedules
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  source: websocket_authorization
response_variable: native_schedules
```

This is also the default if `source` is omitted. Remove or replace an explicit
`source: rest` saved from the v0.3.1 instructions; existing YAML is not rewritten
automatically. Expect `snapshot_source: websocket_authorization`, a revision, and
the stored schedule list. Read and preview do not require a pending operation or
write opt-in. Stop and capture the response/diagnostics if the read fails.

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

For the first complete storage test, keep both entries **disabled**:

| Test | Day | Controller-local time | RPM |
| --- | --- | --- | --- |
| A | Friday (`[5]`) | 11:00–11:15 | 2650 |
| B | Friday (`[5]`) | 11:30–11:45 | 2700 |

Create A using the example above, then preview/apply B with its own fields and
`enabled: false`. Record the returned IDs. Preview/apply an update of A to 2750 RPM
and verify B is unchanged. Preview/apply deletion of A, verify B remains, then
preview/apply deletion of B. Inspect in iAquaLink after **each** apply and stop on
any error or unexpected change. These are storage tests, not scheduled pump runs.

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
   it does not retry, undo, or send an equipment/schedule write. Acknowledgement
   obtains another fresh snapshot and rejects a changed revision. The pending
   operation ID remains valid for recovery even if its original preview expired.
4. If a change is still needed, make a new preview against the current table.
   A failed call might have reached TCX; duplicate-looking entries need explicit
   inspection, not an automatic "cleanup" or recreation.

The default acknowledgement source is `websocket_authorization`, as for reads.
Use the same selected source for the reviewed read and acknowledgement:

```yaml
action: tcx_direct.get_native_schedules
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  source: websocket_authorization
response_variable: recovery_snapshot
```

Inspect that response and compare it with iAquaLink before acknowledging:

```yaml
action: tcx_direct.acknowledge_native_schedule_write
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  plan_id: PENDING_OPERATION_ID
  revision: REVIEWED_REVISION
  source: websocket_authorization
response_variable: recovery_result
```

Acknowledgement still requires the matching pending operation and obtains another
fresh snapshot; the preceding read cannot be reused as verification. Old cached
snapshots, normal reported deltas, missing tables, connection changes, timeouts,
and a changed revision cannot clear the latch. There is no force clear or replay.

`source: rest` remains an explicit alternative for reads and acknowledgement only.
It requires a usable `sh` object in that specific returned REST response; a
concurrent update cannot certify it, and missing data is not treated as empty.
Choosing REST does not change the source used by future previews/applies. If no
source can produce a fresh table, wait and keep the block in place. After clearing,
a new write needs a new preview, opt-in, Auto mode and new Authorization snapshots.

The durable journal keeps the pending operation's ID, operation, schedule ID, time,
desired payload and state; new operations also record their `snapshot_source`.
After acknowledgement it also stores
`last_acknowledgement` with `plan_id`, `at`, `source` and `revision`, exposed in the
sensor/diagnostics. Existing v0.3.0/v0.3.1 journals remain readable. The surrounding
bounded operation history remains in memory and is lost on restart; the pending
context and last acknowledgement are not.

## Validation status and next gates

Implemented offline tests cover captured CRUD shapes, option gates, explicit RPM,
raw weekday validation, disabled midnight entries, adjacent/overlapping blocks,
freshness/provenance, normalized reported values and unique equipment matching,
field preservation, stale previews, duplicate/repeated apply, serialized operations,
storage failure, cancellation, timeout/restart recovery, connection replacement,
exact readback mismatches, recovery audit persistence, and HA adapters.
The offline two-disabled-entry test checks creation, isolated editing, deletion
and unchanged equipment values without any REST schedule request. Per-stage tests
reject cached/delta/REST/wrong-connection/missing-table substitutes during read,
preview, preflight and final readback. A synthetic receiver test exercises an empty
`sched` namespace through the actual WebSocket handler, without a pending write.
Synthetic fixtures contain no private diagnostic dumps or controller identifiers.

Still required with the owner supervising:

- Create two disabled entries from HA; edit one and verify the other is untouched.
- Confirm a newly requested Authorization snapshot works repeatedly with no schedules
  and with stored entries; the observed startup snapshot alone does not prove this.
- Supervise WebSocket recovery when an operation needs review. Offline tests
  establish safeguards, not live firmware behavior or persistence guarantees.
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
