# Health data link entry (all kit agents)

Run `database/kit_life.sh ingest-link --json` in the same turn as link intent,
including "연동하자", "워치 연결", "심박 가져와", "데이터 안 들어와".
This uses the **same live reader as ingest-status**. Read failures mean unknown;
report the error, never infer consent from old imports, config, or memory.

Relay `status_line` as one line, then `first_step` immediately, in the same
plain wording (no 1-step label, ISO stamps, UTC or consent jargon). The line
already renders owner-local month/day. `state=on` means receiving consent is
active, not that fresh data arrived (check `ingest-status` before claiming
receipt). `state=off` carries its revocation time in the JSON `status` rows;
absent consent is `none`. Do not
say sleep/workouts are importing when off or heart rate is excluded by policy.
The existing link includes steps, heart rate, resting heart rate, sleep and
workouts. Do not ask the owner to decide the supported scope again.

1. Ask the owner to open the iPhone Dogany app and allow the named health
   data permissions (already allowed permissions stay). For a partial link,
   add the pending permissions, including heart rate. This is the first step,
   not another offer to begin. Do not invent a settings menu or download URL.
2. When the owner identifies the kinds they allowed, use the existing
   `service/health/hk-ingest consent KIND [KIND ...]` on an install with the
   health adapter. It invokes `database/kit_life.sh ingest-consent healthkit
   KIND --module health` for each approved kind. Only pass those kinds;
   never treat "link it" as blanket consent or re-approve an already-on kind.
   Refusal leaves that kind off; approval begins now, never backfill.
   Do not call the consult-tail `program-plan answer --stage health_link`
   as a partial consent shortcut: that legacy yes grants the whole link list
   and also enables scheduled passes. No program/goal interview is needed.
3. Re-read `ingest-link --json` after consent and `ingest-status --source
   healthkit --json` for actual pull/receipt timestamps. Do not claim success
   until observed. On the life agent without the health adapter, show the existing app
   step and say the health receiving adapter is needed to finish; consent
   alone does not install a receiver. Never enable services just to read status.

Existing health-pack references: `workout-program/reference/build-flow.md`
(records tail), `service/health/health_link.py` (`LINK_KINDS`), and
`service/health/hk-ingest` (consent/revoke). The unsolicited offer's dev-channel,
app-connected and already-offered gates do not suppress an explicit request
for these instructions. This entry does not bypass those gates or mutate them.

HealthKit ingestion is not a guaranteed realtime pulse stream. For a realtime
heart-rate question, start the link step and explain that current pulse can
be checked on the watch during the workout; never promise a live kit feed.
