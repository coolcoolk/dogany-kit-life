# Start-card taps (미루기 / 도움받기 / 완료 / 취소)

Read when the owner's message is exactly one start-card button text: 미루기,
도움받기, 완료, 취소, clock-labeled snooze choices, 말로 하기, completion-time
choices, 줄일까요, 옮길까요, 뺄까요
(en: Snooze, Help me start, Done, Cancel, In 30 min, In 1 hour, Tomorrow, Say when,
Shorten it?, Move it?, Drop it?).

The kit posts every task/routine start reminder itself ("🚨 태스크 시작" /
"<title> <dur>") with four buttons: [미루기] [도움받기] [완료] [취소]. A tap arrives
as the button's text. The plain task/routine lead alert ("⏰ 태스크 30분 전")
has [미루기] [완료] [취소]; a lead 취소 cancels that occurrence exactly like the
start card's 취소 and suppresses the later start alert.

Main buttons stack vertically, one button per row in the listed order.
Snooze choices also use one button per row so their full clock labels stay visible.

FIRST, before anything else:

`kit_life.sh start-tap "<that text, verbatim>"`

The kit finds the tapped card (the bubble the owner tapped), applies the tap
to exactly that occurrence once and edits the card itself. Read `status`:

- `SESSION_OPEN` -> keep the workout task open and continue workout-session
  using `next`. Close the session before completing its task. The card's
  Done button remains available; do not claim completion or retry it now.
- `DONE` / `DONE_TIME` / `CANCELLED` / `SNOOZED` with a CARD block
  after the JSON line (state + time-block card) -> relay the returned card exactly once, verbatim, as
  your whole reply (no `NO_PUSH`, no success line of your own, never the
  CARD_* marker lines). The bubble was edited in place as before.
- `CANCELLED` / `DONE` (no CARD block) / `ALREADY_DONE` / `CHOOSING` / `SNOOZED` / `SPENT` -> the card
  already shows the result (✅ 완료, the snooze choices, ⏰ 미룸 · <when>).
  Reply exactly `NO_PUSH` and nothing else (the bridge's silent reply):
  no confirmation line, no placeholder such as "(출력 없음)" / "(응답 없음)",
  no [[OPTIONS]]. Never call task-done / task-reschedule yourself for the
  same tap.
- `HELP` -> reply with ONE concrete first action only, in one message that
  names the item (`task.title`: the card may be far up the chat), by `type`:
  - `habit` (routine / recurring): shrink it to the smallest start, e.g.
    "말해보카는 앱 열고 오늘 레슨 하나만, 5분".
  - `prepared` (note / place / links on file): surface them so it can be
    done now (the link itself, the note line, the place).
  - `vague`: split off one first step.
  No reasons asked, no lecture, no list, no [[OPTIONS]]. The card keeps its
  미루기 / 완료 / 취소 buttons.
- `RESHAPE` (asked once, after the 3rd postpone of the same item) -> follow
  `next` (줄이기: one smaller version, applied after the owner agrees;
  옮기기: ask once when, then move; 빼기: task-archive, a routine only after
  one confirm, `routine retire` or `routine pause`). Never invent a deadline,
  never bring the postpones up again.
- `RETRY` -> say in one line it did not go through; nothing was changed.
- `NO_CARD` -> not a card tap: handle the message as ordinary chat (a typed
  "완료" is the chat completion path, reference/completion-chat.md).

## Owner update (2026-10-18)

Clock-labeled choices replace the old evening/later buttons. Daily routines:
30m / 1h / 2h, only within today, plus [말로 하기]. Other routines offer
[내일 HH:MM] only if their next occurrence is after tomorrow. One-off tasks:
30m / 1h / tomorrow plus [말로 하기]. Every gap shows `gap · HH:MM`.
Snooze presets never skip a day or move a daily routine to tomorrow.
The separate 취소 button skips only the tapped occurrence.

`ASK_TIME`: ask `question` verbatim ("언제로 미룰까요?"). Parse the next
owner answer against returned `now` and `timezone`; resolve ambiguity with
one question. Then `start-tap "<answer>" --message-id <returned message_id>
--at YYYY-MM-DDTHH:MM`. Use the date/time the owner means, preserving duration
and the recurrence rule. A successful move edits the card to "⏰ 미룸 · HH:MM".

Late Done (strictly after start + duration + 30 minutes) is already done,
recorded at scheduled start until picked. The card shows exactly one row
[아침 HH:MM쯤] [방금 HH:MM] (daypart follows scheduled time). Pass either
label to start-tap. `DONE_TIME`: the completion was re-timed, buttons removed;
Reply exactly `NO_PUSH` and nothing else. Never ask a second completion question.

## Start-card cancellation (2026-10-20)

`취소` / `Cancel` calls `start-tap` on the bound bubble. `CANCELLED`
edits that card with the existing cancellation mark and removes its buttons;
reply exactly `NO_PUSH`. One-off tasks settle as `abandoned`, leave open
lists and the calendar mirror, and stop reminding. Routine occurrences settle
only that date; never retire, pause, or delete the routine definition.
`task-undone <returned id>` restores a start-card cancellation, including a
skipped occurrence, and queues the mirror again. Past times may need
`task-reschedule` after restoring. The button copy is pending owner approval;
see docs/reports/2026-10-20-start-card-cancel/REPORT.md.

For an owner-reported completion call the explicit task completion verb with
`--said` as verbatim evidence and `--when now|HH:MM` when the owner gave a time.
Code does not require affirmative/negative keywords, match task names inside
the sentence, or extract a completion clock from it. The agent decides the
action. Photo evidence uses the existing `--photo-evidence` typed assertion.
