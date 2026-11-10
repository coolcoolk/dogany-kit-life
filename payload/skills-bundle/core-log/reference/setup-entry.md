# Start/use intent before record fields

Use intent and state, never an exact phrase list. Actual record content always
uses the capture adapters, even while management/setup is off; no setup pitch
after capture. A bare wish to use a module has no missing record fields.

For non-ledger modules run `database/kit_life.sh manage-state <module>`.
Output is `MANAGE<TAB>module<TAB>on|off<TAB>reason`; off means setup outstanding
for this handoff. Skill linkage or existing records do not mean management is on.
On means normal use/readback, not a new setup flow; resume an active interview
only in its own state. Explicit reconfiguration/start-over remains owner intent.

| Module / intent | When off, owning entry |
|---|---|
| episode | episode-setup |
| core: generic goals + routines + weekly practice + calendar planning | core-setup |
| goal | goal-session |
| meal / workout / health: read each requested module’s manage-state | brief-setup first-install.md `## later` group C, C1 |
| brief | brief-setup first-install/walkthrough; read its actual routine/slot state too |
| routine | routine-manage `## register`: manage-set routine on, capture-readback routine |
| shopping / place | this skill's `## later setup`: manage-set <module> on, capture-readback <module>, then only missing management decisions |
| person | person-session care entry |

Schedule defaults on; there is no schedule setup handoff.

Ledger uses `service/kit_life/ledger/onboarding verdict` instead of manage-state:
`NO-OFFER complete` (exit 3) -> ordinary ledger-log / ledger-view / ledger-session,
never next/restart for ordinary use. Every other verdict, including
management-declined, declined-exhausted and recording-active -> ledger-setup
owner-request lane (manage-set ledger on, onboarding next). Explicit start-over
intent, during or after onboarding, belongs to ledger-setup's restart lane.
