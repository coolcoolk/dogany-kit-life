# person-session -- contact-gap list, check-in, snooze, corrections

Read when running `relmod.sh alert-pick` (weekly proactive turn or "누구
연락해야 해?"), when the user says someone is temporarily away / back, or
when a stored fact / touch is wrong and must be corrected or removed.

## alert-pick output

```
PICK<TAB><pid><TAB><name><TAB><gap_days>days<TAB><ratio><TAB><hint><TAB><meet_days><TAB><meet_ctx>
ASK_FADE<TAB>...            drift-apart question candidate
CHECKIN<TAB>...             person with NO contact record (max 1, code-capped)
RESURFACE<TAB><pid><TAB><name>   snooze just expired (one-shot, already cleared)
CAP_REACHED | NONE
```
meet_days / meet_ctx = last MEETING (만남), not last 대화; '' when none.
alert-pick already excludes: upcoming appointment, active snooze. never
re-implement gap logic or filters from raw queries.

## rendering

- CAP_REACHED / NONE -> silence.
- PICK lines -> exactly:
  ```
  💬 연락해보세요
  이름 | 맥락 | n일전
  ```
  맥락 = meet_ctx ("-" if empty); n일전 = meet_days + "일전" ("-" if empty).
  e.g.
  ```
  💬 연락해보세요
  <NAME_A> | <CONTEXT_A> | 12일전
  <NAME_B> | <CONTEXT_B> | 20일전
  ```
  never reword into sentences; no guilt / urgency / streak; never an obligation or task.
- ASK_FADE -> ask once: "OO님이랑 연락이 많이 뜸해졌는데, 그냥 자연스럽게
  멀어지는 게 낫겠다 싶으면 알려줘요." yes -> `relmod.sh set-fade <id> on`;
  no -> nothing.
- CHECKIN -> ONE curiosity question, zero obligation: "요즘 OO님 근황
  아세요? 최근에 본 적 있어요?" never "you should contact them".
  recalled meeting: exact date + occasion -> schedule-log past appointment;
  fuzzy -> `relmod.sh touch-add <id> meet --date <approx>`. shrug -> drop.
- user contacts/meets after a suggestion -> `relmod.sh acted <id>`;
  dismisses -> `relmod.sh dismiss <id>`.

## snooze (temporary away; NOT fade)

Fade = permanent drift apart (`set-fade`). snooze = dated pause with a reason.
- cue ("쟤 미국 갔어", "유학 갔어", "당분간 못 봐"):
  explicit removal verb ("그냥 빼줘" / "빼둬") -> snooze directly; else ONE
  confirm "그럼 연락 제안에서 당분간 빼둘까요?" (no / silence -> drop). never
  auto-snooze from context alone.
- `relmod.sh snooze <id> [--until YYYY-MM-DD | --months N] [--reason "<reason>"]`
  timeframe given -> parse to flag; none -> no flag (relmod default 6 months).
  pass `--reason` (recorded as a fact). one confirmation line.
- effect: excluded from PICK and CHECKIN until snooze_until.
- back early ("쟤 돌아왔어", "이제 봐도 돼") -> `relmod.sh unsnooze <id>`, one line.
- RESURFACE -> ONE question "OO님 아직 그대로예요?"; still away -> snooze
  again (new duration or default); back -> nothing. once per expiry.

## corrections (wrong fact / touch)

Never re-add to "fix" a row; correct it by id.
- find the id: `relmod.sh fact-list <pid>` -> `<id> <noted_on> <source> <fact>`;
  `relmod.sh touch-list <pid>` -> `<id> <touched_on> <kind> <note>`.
- `relmod.sh fact-edit <fact_id> [--fact "<text>"] [--source retro|chat|onboarding] [--date YYYY-MM-DD]`
- `relmod.sh fact-rm <fact_id>`
- `relmod.sh touch-edit <touch_id> [--kind call|message|dm|other|meet] [--date YYYY-MM-DD] [--note "..."]`
- `relmod.sh touch-rm <touch_id>`
- receipts: `EDITED<TAB><id>...` / `REMOVED<TAB><id>...`. rm is a soft delete:
  the row drops out of lists, brief and gap math. `ERR<TAB>unknown ... id`
  (exit 1) = wrong or already-removed id -> re-list, never guess.
