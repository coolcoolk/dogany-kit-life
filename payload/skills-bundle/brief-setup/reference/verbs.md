# Retro schedule readback and correction

```
retro-schedule daily HH:MM
retro-schedule weekly 'Sun HH:MM'
retro-schedule-show daily|weekly
retro-schedule-clear daily|weekly
retro-schedule-undo daily|weekly
```

Show returns this agent's stored definitions, including retired ones. Clear
retires that lane and cancels its future open occurrences; undo restores the
latest set/clear only when its owned fields and links have not changed. Initial
creation is undoable too. Existing history/completions stay intact. These are
kit verbs, never direct config/DB edits. Full correction routing is registered
in brief-session/reference/verbs.md "Typed retro corrections".
