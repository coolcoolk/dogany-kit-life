---
name: episode-setup
display_name: 경험 모듈 설정
description: >-
  Fires at Event-module setup or reconfiguration -- sets the default query lens,
  toggles auto-cluster proposals on/off, and configures the auto-cluster proximity
  threshold. Rare, config-only. Trigger phrases: "경험 모듈 설정", "자동 묶기
  꺼줘", "자동 묶기 켜줘", "기본 조회 방식 바꿔", "묶음 기준 바꿔".
---

# episode-setup

One-time / config handler for the event/experience overlay.
Config written to `config/kit_life.conf` (or module config store). No runtime
capture or review here -- those are episode-log / episode-session.

## paths

- config target: `$PROJECT_ROOT/config/kit_life.conf`
- helper (when needed): `$PROJECT_ROOT/database/kit_life.sh`

## config keys (English tokens, ASCII)

```
episode.auto_cluster        = on | off          # default: on
episode.cluster_threshold   = <int_minutes>     # default: 180 (3h window)
episode.default_lens        = objective | subjective   # default: objective

# rich-clustering weights (owner directive: time-only is too thin).
# A cluster is proposed on a weighted score, NOT time proximity alone.
# Each signal contributes its weight; the sum is compared to cluster_min_score.
episode.cluster_w_time      = <int>   # default: 2  (records within cluster_threshold)
episode.cluster_w_place     = <int>   # default: 2  (shared place in attached context)
episode.cluster_w_person    = <int>   # default: 2  (shared person in attached context)
episode.cluster_w_lens      = <int>   # default: 1  (shared domain/category lens)
episode.cluster_min_score   = <int>   # default: 4  (>= this -> propose)
```

All keys stored as English tokens. Display-layer maps to Korean for user-facing
output. Weights are the tunable knobs (measure-first): the SHIPPED default weighs
time + place + person + lens together, so a bare time window alone (score 2) never
triggers a proposal -- it needs at least one shared place OR person on top of it
(2+2 >= 4). Owner tunes weights later on taste signal; no first-activation ask.

## procedure

### onboarding (first activation)

Cognitive budget: 0 questions (typical) / 1 question max (auto-cluster on/off).
No measure-first; no numeric goal. 2-lens default derived from system default
(objective), no re-ask.

1. Check if config keys exist. If already set -> skip, confirm current values.
2. Single question if first time:
   "여러 기록이 한 경험 같으면 묶을지 먼저 물어볼까요? (기본은 켜둘게요, 끄려면 '끄기')"
   - "끄기" / "off" / "no" -> `episode.auto_cluster = off`
   - any affirmative or no answer -> `episode.auto_cluster = on` (default)
3. Write config. Confirm: "경험 모듈 준비됐어요. 이제 기록들이 한 경험으로 묶여요."

Do NOT ask about cluster threshold or lens at first activation --
use system defaults. User adjusts later on demand.

### toggle auto-cluster

"자동 묶기 꺼줘" / "자동 묶기 켜줘"

```
# write to config/kit_life.conf
episode.auto_cluster = off   # or on
```

Confirm: "자동 묶기 켰어요." / "자동 묶기 껐어요. 이제 알아서 묶진 않을게요."

### set default lens

"기본 조회를 주관으로 바꿔" / "렌즈 바꿔"

```
episode.default_lens = subjective   # or objective
```

Confirm: "이제 경험을 볼 때 주관(느낌·의미) 위주로 보여줄게요." /
"이제 경험을 볼 때 객관(자원·기록) 위주로 보여줄게요."

### set cluster proximity threshold

"묶음 기준을 2시간으로 바꿔" / "cluster threshold 120분"

Threshold unit = minutes (integer).

```
episode.cluster_threshold = <minutes>
```

Algorithm note (shipped default = rich, weighted -- NOT time alone):
each candidate cluster scores the signals that co-occur --
time proximity (records within <threshold> minutes) + shared place +
shared person + shared domain/category lens -- each contributing its
`episode.cluster_w_*` weight. Propose only when the total reaches
`episode.cluster_min_score` (default 4). With the shipped weights a bare
time window scores 2 and never proposes on its own; it needs a shared
place or person on top. Weights are the taste-signal knobs (measure-first);
threshold just sets the time window.

Confirm: "묶음 기준을 [N]분으로 바꿨어요."

### set cluster weights (rich clustering tuning)

"장소 비중 높여줘" / "시간만으로 묶지 마" / "묶음 가중치 바꿔"

Adjust any `episode.cluster_w_time / _place / _person / _lens` or
`episode.cluster_min_score`. Measure-first: change one knob at a time on a
stated preference, never guess a full re-weighting.

```
episode.cluster_w_place = <int>   # example
```

Confirm: "묶는 기준 비중을 손봤어요. 지금은 시간·장소·사람·주제를 같이 봐요."

## cognitive budget

- onboarding: 0 questions typical / 1 max (auto-cluster toggle only).
- each config change: 0 questions (user states the value directly).
- no numeric goals -> measure-first not applicable.

## output

- brief confirm only. no markdown tables.
- show current config values after any change.

## cross-skill pointers

- runtime capture -> episode-log.
- review / enrich / distill -> episode-session.
