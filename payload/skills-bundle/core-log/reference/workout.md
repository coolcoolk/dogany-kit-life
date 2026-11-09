# core-log -- workout adapter (a finished session)

Before replying after capture, apply the common SETTLE ON EVIDENCE contract
from AGENT: saving facts alone does not finish an activity report. Read
`.claude/skills-bundle/schedule-log/reference/completion-chat.md` for the
shared completion check. A PLANNED item settled this turn: its single
completion reply overrides this adapter's capture-report/card instructions.
No open task/routine matches: no completion reply at all, hand over to
reference/episode.md. Tool work stays silent until the final line.

workout-log is the record path: read
`.claude/skills-bundle/workout-log/SKILL.md` for find-first, workout-add and
classification. Live sets, programs and coaching belong to the health pack.

- After capturing, apply the common SETTLE ON EVIDENCE rule from AGENT.md:
  read OPEN_TODAY / task-open-today and compare meaning. Do not choose a
  task from a workout category or a task-name pattern. One fulfilled row
  settles with real evidence; multiple fulfilled rows get one done-button
  question. No open match: never task-add + done; the workout itself is
  recorded by this adapter (workout-add).
- close_task.sh retains only the optional session-engine handoff. Its NONE
  means it did not settle a task; the semantic completion check is still due.
- EXISTS means do not duplicate the record; still check outstanding tasks.
- A completion reply is exactly the common one line; it takes precedence
  over workout-log's multiline report. No plan or next-session pitch.
