# weekly retro -- weaving mode + recommendation gate

Read when zero goals survive mode detection (whole session weaves), or when
the weekly-review pending marker carries a recommendation block.

Purpose: surface and connect missed week fragments. no goal compare, no
forced goal creation.

## W1 -- anchor (react-not-recall)

One concrete fragment (done task titles, appointments, episode clusters) +
one question. no blank "한 주 어떻게 보내셨어요?".
e.g. "지난주에 대파크림파스타 하셨던 것 봤어요. 직접 만드셨나요?"

W1-1 episode slot: strong-signal clusters already made silently by
episode-log; weak hints unfold here: show "이런 게 묶여요", let user react.
corrections = delta (reconcile-before-write). merge/split/enrich -> episode-session.

## W2 -- connect fragments

Link by pattern/context. emergent observation -> `hypothesis-add` weak
prior. never assert. tone: "패턴이 보이는데 맞을까요?"

## W3 -- next-week briefing + placement (owner 2026-08-22 Q4)

Weaving mode DOES place. zero goals still -> short briefing (what is on next
week's calendar) + ONE concrete recommendation from the week's fragments,
placed per reflection.md Step 4 (marker + `--at`). steady state, nothing
derived -> explicit zero-placement close, briefing still given.

## W4 -- recommendation gate (owner 2026-08-22 Q4)

- trigger = EVIDENCE (repeated signal in owner's own fragments that week),
  never the calendar. no signal -> no recommendation (correct outcome).
- cap ONE per retro, combined with level-up / promotion. strongest only.
- declined item never resurfaces.
- SKILL never originates one. `routines/weekly-review.sh` ->
  `.claude/skills/mint-agent/scripts/weekly_eval.py` ->
  `config/minting-state.json` (`recommendations` / `declines`) computes it
  and prints AT MOST one block. render verbatim if present; silent if absent.
  no second path.
- ask-weight ladder (not equivalent):
  1. module / pack intro (lightest): "가계부 관리 모듈 같은 게 있어요". repeated signal enough.
  2. dedicated-agent mint (middle): needs weekly_eval thresholds.
  3. consult offer (heaviest, ~30 min of owner attention): needs a visibly
     stuck decision AND lighter rungs taken or declined. never present as
     same size as a module intro.

Promotion hook (rare): clear direction + behavior corroboration -> ONE offer
"이제 제대로 가보실래요?". counts against the one-per-retro cap.
