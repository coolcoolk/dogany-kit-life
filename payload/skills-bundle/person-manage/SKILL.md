---
name: person-manage
display_name: 인연 기록
description: >-
  Fires when the user looks up, registers, or finds a person/relationship by
  nickname. "what was their name", "who is OO", "add OO to my relationships", or
  mentioning a person by nickname/alias. ALSO fires on what-have-I-done-with-them
  questions -- "영희님이랑 최근에 뭐 했지", "OO랑 언제 마지막으로 만났지", "다음에 OO
  만나는 게 언제야", "what have I done with OO recently" -- which read one person's
  appointments and stays off the execution axis. Fires on facts about a person --
  "영희 생일 961002", "OO 생일 추가", "OO는 A랑 B의 딸이야", "OO 회사 옮겼어" --
  which are stored with person-set / person-link. And fires when the schedule skill
  hands off a person it could not resolve by itself (ABSENT / AMBIGUOUS). Manages
  persons via the shared SQL edition (kit_life.sh). This skill is the
  kit_life.db-based future-standard SQL edition.
---

# person-manage

Manage persons in kit_life.db persons table via kit_life.sh CLI. SQL-based standard.

- helper: `$PROJECT_ROOT/database/kit_life.sh` (PROJECT_ROOT unset -> `LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`; CWD must be workspace root)
- SoT: local `$PROJECT_ROOT/database/kit_life.db` (persons table)
- aliases: stored in persons.aliases column, comma-separated — native support (unlike Notion)

## kit_life.sh quick reference
```
person-find    <name_or_alias>              -> id<TAB>name<TAB>relation<TAB>aliases
person-resolve <name_or_alias>              -> RESOLVED|AMBIGUOUS|NEAR_MATCH|ABSENT (module function)
person-add     <name> [relation] [aliases]  -> new person id | NEAR_MATCH (exit 5) [--new-confirmed]
near-match-accept person <id> <name>        -> owner said same person: <name> becomes an alias
near-match-refuse person <id> <name>        -> owner said different: remembered, never asked again
person-alias   <id> <alias>                 -> add alias (dedup ignored)
person-blocks  <name_or_alias> [days]       -> one person's appointments/stays
person-show    <id|name>                    -> PERSON + BIRTHDAY/GENDER/RESIDENCE/JOB/CONTACT + REL rows (+ hops line if not direct)
person-hops    <id|name>                    -> HOPS<TAB>n<TAB>path + CONFIRM ("관계없음 · 2다리 (최정인 딸)")
person-set     <id|name> field=value ...    -> SET rows + CONFIRM lines (birthday relation gender residence job contact)
person-link    <a> <b> <type> [--note t] [--replace]  -> "a is <type> of b"; LINKED | LINK_CONFLICT (exit 4) [+ RELATION_HINT]
person-unlink  <a> <b> [type]               -> UNLINKED n
```

Write verbs take an id or an EXACT name/alias; anything else answers
ABSENT / CANDIDATES / AMBIGUOUS (exit 1, nothing written) -- resolve first
(`person-resolve`), then pass the id. Relay the CONFIRM lines: they are the
owner-term wording (지인, 자녀, 생일), never the stored tokens.

`person-resolve` is the function OTHER skills call before they load this one.
It answers with a verdict in the first tab field and exits 0 on all three
verdicts, so a person who is simply not registered yet never looks like a
broken CLI. This skill is what gets loaded when the verdict is ABSENT or
AMBIGUOUS -- because those are the two cases where a human has to be asked.

## "what have I done with OO recently"

Until 2.19.0 this question had no answer here. The appointment<->person link
only read one way (an appointment listed its people), so the record of what
the user actually did with someone lived inside the schedule skill's own
handling and the relationship side could not see it.

```
kit_life.sh person-blocks <name_or_alias> [days]     (days default 365)
  PERSON<TAB>id<TAB>name<TAB>n
  id<TAB>kind<TAB>when_local<TAB>title<TAB>place<TAB>settled_outcome   (n rows, newest first)
  ABSENT<TAB><query>        -> nobody by that name; offer to register
  AMBIGUOUS<TAB>n + rows    -> ask the user which one, then re-run
```

- window is [now - days, +inf): "최근에 뭐 했지" and "다음에 언제 봐" are one
  question about one person. Rows after now are upcoming, rows before now are
  history -- say which is which, do not present a future plan as something done.
- cancelled (abandoned) rows are excluded, same rule appt-find applies.
- `kind` is task | appointment | presence. A `presence` row means the user was
  somewhere for a span, not that they met this person there -- read it as
  context, not as a meeting.
- an empty list is an answer ("최근 1년 기록은 없어요"), not a reason to guess.
- registering or editing any of those rows is NOT this skill's job -- hand
  back to the schedule skill.

## lookup procedure
1. `kit_life.sh person-find <name_or_alias>` (searches name + aliases together)
2. 1 match -> report that person's info
3. 0 matches -> ask the user: "new person, or alias for existing?" (no guess-register)
4. multiple matches -> show candidates, ask the user to confirm

## register procedure
1. search first: `kit_life.sh person-resolve <name_or_alias>` (exact name/alias,
   then substring, then near-match)
2. RESOLVED / AMBIGUOUS / NEAR_MATCH -> show candidates, confirm the intended
   person with the user. existing person -> add alias, not a new person
3. ABSENT -> confirm "new person, or alias of existing?"; only then
   `kit_life.sh person-add <name> [relation] [aliases]`
4. alias to add -> `kit_life.sh person-alias <id> <alias>`
5. done -> report to the user

## handoff from the schedule skill

When schedule-log's `person-resolve` came back ABSENT or AMBIGUOUS while it
was linking someone to an appointment, this skill is what answers the
question -- and then hands an id back so the link can be made.

- NEAR_MATCH -> ask the ASK line only ("혹시 김영희랑 같은 분인가요?").
  yes -> `near-match-accept person <ref> "<name>"`, report <ref>.
  no -> `person-add <name> [relation] [aliases] --new-confirmed`.
- ABSENT -> ask: "OO는 이미 아는 분 별명인가요, 처음 뵙는 분인가요?"
  ([[OPTIONS]] format, numbered, marker on the last line)
  - alias of someone existing -> `person-alias <id> <alias>`
  - new person -> `person-add <name> [relation] [aliases]`
  - `person-add` answering NEAR_MATCH (exit 5) -> the NEAR_MATCH bullet above.
- AMBIGUOUS -> show the candidate rows the verb printed, ask which one.
- either way, finish by reporting the person id. The schedule skill runs
  `block-person <block_id> <person_id>` with it (appointment, stay or task;
  `appt-person` is its old name); do not run that verb here --
  linking a person to a block is the execution axis's write, not this one's.
- never guess-create. 0 matches may still be an alias or a duplicate.

## edit a person (birthday, relation, family links)

Every fact about a person goes through a verb. There is no other path.

- birthday: `person-set <id> birthday=<what the user typed>`. YYMMDD is fine
  ("961002"): a 2-digit year becomes the most recent past date (96 -> 1996,
  26 -> 2026 = born this year). Relay the CONFIRM line, which shows the full
  date, so a wrong century is visible. year unknown -> ask; no year-less save.
- relation / gender / residence / job / contact: `person-set <id> f=v ...`
  in one call. `f=` clears. relation: family partner close_friend friend
  acquaintance business unrelated (or 가족 연인 친한친구 친구 지인 비즈니스 관계없음;
  관계없음 / 관계 없음 / 건너 아는 사이 all accepted as input).
  unrelated (shown 관계없음) = no direct tie to the user, known only through
  someone else; empty = not asked yet.
- gender: M / F / X (X shown 기타); empty = unknown. 딸/아들 only for M/F,
  otherwise 자녀.
- hop distance ("몇 다리") is COMPUTED from links, never stored, never asked:
  read it with `person-hops <id|name>`; `person-show` prints the same line
  for non-direct people.
- family links: `person-link <a> <b> <type>` reads "a is <type> of b"
  (parent child spouse sibling partner friend colleague acquaintance;
  family only for other kin (친척); never store a lumped 가족 link;
  딸/아들/엄마/아빠/배우자 accepted). a pair already linked as
  something else -> LINK_CONFLICT: show it, ask, `--replace` only on yes.
- "C는 A, B의 딸이야" = TWO links: `person-link C A child --note 딸` and
  `person-link C B child --note 딸`, plus `person-set C gender=F` (아들 -> M).
  never only one parent.
- the child's OWN relation to the user follows the parents, never a default
  `friend`: a friend's child is 관계없음 (unrelated), not friend, not 지인;
  a family member's child is family. person-link prints `RELATION_HINT ... <suggested>` when the
  stored relation disagrees -> `person-set <child> relation=<suggested>`.
  registering such a child: `person-add <name> unrelated` (not friend).
- `person-show <id>` to read it back (birthday + every link, from that
  person's side: on a parent's list the daughter is 자녀).

A request no verb covers is answered "아직 그건 저장할 수 없어요" and reported
upstream. NEVER offer a direct DB edit ("직접 수정", raw SQL, editing the db
file) as an option, not even with the owner's approval -- that path writes
around every check the verbs make.

## boundaries
- delete person -> confirm with the user first.
- no bold (asterisk). address = per persona (AGENT_ADDRESS in config/agent.conf, else the locale default).
- SoT = kit_life.db only. do not mix with other stores.
- honorific (ko-locale): a THIRD-PARTY person's name is ALWAYS rendered with the locale honorific suffix "님" in user-facing output (길동이 -> 길동님). The user's own casual register for that person -- 반말, 애칭, bare given name -- is accepted as INPUT but is never echoed back in output. How the agent addresses THE USER is persona configuration (AGENT_ADDRESS, line above); this rule is about third parties and is not configurable. Applies to lookups, register confirmations, and every person mention.
