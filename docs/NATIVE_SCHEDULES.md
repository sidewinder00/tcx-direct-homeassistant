# Experimental native schedules — v0.3.4

Version 0.3.4 changes shared REST pacing, not native schedule write or confirmation
behavior. Explicit REST reads now respect the same cooldown as other callers;
native schedule testing remains paused.

> [!WARNING]
> **Native schedule testing is paused.** A supervised create initially appeared
> successful, then duplicate disabled entries appeared. A manual-speed request also
> failed during the incident. Later testing showed recovery, not a software fix.
> Keep experimental writes off and leave the existing HA timetable and safety
> controls unchanged. Do not run native schedule actions or repeat the experiment.

This feature is under development, not a supported migration path. The current
public guide describes status, safeguards and limitations; detailed protocol
research and live-test recipes are maintained separately.

## What is installed

- A read-only **Native Schedules** diagnostic sensor shows observed schedules,
  observation time, write permission and any pending operation requiring review.
- Read, preview, apply and acknowledgement actions exist for development. Their
  availability is not permission to resume testing.
- Editing requires explicit opt-in. New entries default to disabled, and each
  planned change requires a separate preview and apply.
- The integration communicates through the vendor cloud. Native execution, offline
  persistence and interactions with existing controls have not been fully validated.
- It does not automatically migrate the HA timetable, replace dashboards, or
  rewrite existing pump and salt-water-generator automations.

An empty observed table does not prove that no remote schedule command is pending.
Disabling writes prevents new integration schedule writes; it does not clear
commands or schedules already held by the cloud or controller.

## Status meanings

| Sensor state | Meaning |
| --- | --- |
| `read_only` | A schedule table is available and experimental writes are disabled. |
| `ready` | A table is available and the write option is enabled, with no local review block. This is not a safety or hardware-validation result. |
| `needs_review` | A previous operation has an uncertain outcome and further writes are blocked. |
| `storage_error` | The durable schedule journal is unavailable or invalid; writes are blocked. |
| `unknown` | A usable schedule table is unavailable. |

These states describe the integration's view, not independent verification of the
controller. Live-versus-cached status and the last observation time still matter.

## Current safeguards and their limits

- Apply requires a connected cloud session, known Auto mode, write permission
  and a usable durable journal.
- Preview plans expire after five minutes, are single-use, and do not survive a
  reload. Apply checks that the schedule table has not changed since preview.
- A complete, newly received cloud snapshot is required at defined stages. Cached
  data and partial updates do not substitute for that check. However, fresh receipt
  is not a vendor transaction identifier or a guarantee of atomic ordering.
- Explicit whole-number RPM is checked against reported pump limits. Unsupported
  default-speed writes and enabled overnight entries are rejected. Individual
  weekday mappings and other timing semantics remain only partially validated.
- The implementation attempts to preserve other schedules and reject overlapping
  enabled pool intervals. Incomplete entries can still appear as schedule records
  and may not be recognized by all validation paths; that remains a review item,
  not a proven safety guarantee.
- An uncertainty record is saved before transmission. Failed verification,
  cancellation or disconnection can leave it in place across restarts. The
  integration does not automatically replay an uncertain schedule write.
- Confirmation checks the intended reported result and unrelated table entries.
  Extra or normalized controller fields can leave a successful change uncertain.
  More importantly, an initially matching readback did not prevent later duplicates
  in the supervised test. Outstanding-command clearance is not independently
  verified by the current apply or acknowledgement checks.

These are partial safeguards, not proof that a schedule is safe to write or run.

## Explicit actions are not passive monitoring

Normal reads, previews, apply verification and acknowledgement request fresh cloud
snapshots. An explicit REST alternative exists for read/recovery only when that
response contains a usable table; it is not an automatic fallback for writes.

Read and preview actions can make requests even with experimental writes disabled.
They share the equipment control lock, so their network waits can delay other
commands. Do not poll them from automations or invoke them during the current pause.

The diagnostic sensor and downloaded passive trace use already received state.
Downloading diagnostics does not itself send an equipment command or request an
additional schedule snapshot.

## If a write is uncertain

1. Stop native schedule actions and keep experimental writes disabled. A failed
   call may have reached the controller; do not repeat it as a test.
2. Preserve a diagnostic download and the failed action's response privately.
   Record the approximate time and what was observed in the official app.
3. Do not delete the journal, restart to clear the review block, issue guessed
   cleanup commands, or repeatedly delete and recreate entries.
4. Review physical equipment safety separately. Do not assume a disabled test
   entry proves the whole system is unaffected. Use manufacturer procedures if
   equipment behavior is unsafe.
5. Agree on a recovery plan before invoking acknowledgement or further schedule
   actions. Acknowledgement clears a Home Assistant review block; it is not a
   remote cleanup, rollback, or proof that a retained command is gone.

The durable journal preserves the pending operation and last acknowledgement.
The surrounding bounded diagnostic history is memory-only and resets on restart.
Keeping an evidence capture does not require reproducing the fault.

## Passive schedule trace

Version 0.3.3 adds a bounded, redacted schedule trace to diagnostic downloads. It
observes traffic already received and local operation milestones; it adds no
polling, subscriptions, cleanup commands or retries.

The trace distinguishes observations from different source documents before the
normal state merger and records later schedule-table changes. It retains limited
recent history plus selected snapshots and operation summaries. It is not a
complete packet log, a command-completion guarantee, or an automatic safety block.

Oversized fragments are marked as truncated. Missing, empty, cleared and truncated
data must not be interpreted interchangeably. Redacted fields cannot be compared
as if their original values were available. A later change does not by itself
identify who caused it or prove that an earlier local command caused it.

This trace remains memory-only and is not added to recorder history or entity
attributes. Diagnostics still require manual review before sharing; follow the
[security guidance](../SECURITY.md). Keep detailed investigations and raw captures
outside public repository content.

## Validation still required

Before any new live schedule test, development review must address command
completion, retained commands, late duplicates, incomplete entries, and safe
interaction with ordinary equipment controls. Offline tests can challenge the
implementation's assumptions; they cannot establish undocumented firmware behavior.

Any eventual hardware test needs its own agreed scope and stop conditions.
Remaining questions include schedule capacity, weekday and default-speed behavior,
priming and start/stop transitions, midnight and daylight-saving rules, persistence
and execution without HA or internet, and manual/Waterfall priority.

Waterfall-off currently restores the persistent Pool Filtration preset, not the
speed of an active native schedule. Native scheduling must not replace that control
model until the interaction is designed and tested. Adaptive daily changes also
need a policy for stale choices when HA is unavailable. Keep salt-water-generator
safety controls independent.

No migration, controller reboot, live test, recovery action or installation is
requested by this documentation.
