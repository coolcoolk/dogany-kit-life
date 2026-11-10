# goal-session: the staged rebuild walk

Read when a rebuild walk reaches its wrap, when `goal-rebuild-commit` or
`goal-interview --next --stage` refuses, or when the owner asks to start the
goals over after a rebuild already committed.

## the walk

- A walk opens ONLY at an entry door: `goal-interview --start --scope
  rebuild`, chosen by the model (also the fork's rebuild pick). `--said` is
  compatibility-only and never selects a branch. Each door opens a
  fresh walk: the direction is asked again; a direction stated before the
  walk never fills the new card.
- Every move of the walk is `goal-interview --next --stage --domain <D>
  --json`. Without an open walk it is refused (rc != 0, one line naming
  `--start --scope rebuild`). Run that door; never retry `--next --stage`
  and never treat the refusal as a finished card.
- The old goals stay active the whole walk. An abandoned walk changes
  nothing the owner sees.
- Keep (the fork's `지금 목표 그대로` choice command) is NOT a walk: it is the plain `--next` wrap of the
  current card, never `--stage`, and nothing is committed.

## the wrap and the commit

- The walk's `kind: wrap` commits the new card itself: `goal_commit`
  (`{mode: rebuild, activated, archived}`) set = the new card is set and
  the old goals are archived; say so, the program consult follows.
- `goal_commit` empty and `rebuild_commit` present = the commit gate
  refused at the wrap; nothing was retired, the old card is still the card.
  Run that argv exactly (`goal-rebuild-commit --replacing <old ids> --with
  <new ids>`), never typing the id lists yourself. Only `COMMITTED` means
  the new card is set.
- `REFUSED` (rc 3) -> nothing was retired. Each reason line says why: no
  open walk, a `--with` goal older than the walk (a copy of the current
  card), a walk older than a replaced goal, a walk that asked no question
  yet, an unfinished replacement. Do not tell the owner the card changed;
  continue the walk (`--next --stage`) or open a fresh one with the entry
  door.

## starting over again

"start the goals over" after a commit is a new walk like any other: the
entry door with the owner's words. The walk replaces whatever card is
active now; archived goals stay archived.
