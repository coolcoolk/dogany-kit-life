#!/bin/bash
# Appointment arrival margins (no schema migration):
#   buffer-set --scope default --minutes N
#   buffer-set --scope situation --target after-work --minutes N
#   buffer-set --scope person --target person:ID|group:ID --minutes N [--strict]
#   buffer-set --scope appointment --target ID|ULID --minutes N [--strict]
#   buffer-show                 active rules in plain language
#   buffer-undo                 undo the last setting; refresh future rows only
# Precedence: appointment > person/group > situation > default > product (0).
# The physical margin shrinks when the previous block leaves too little room.
#
# Corrections (the existing set/add verbs restore explicitly retracted facts):
#   place-coord-reset <place> / place-region-reset <place>
#   travel-edge-retract <from> <to>    one ordered pair, refresh derived legs
#   portion-del <name>                serving definition only
#   metric-edit <date> <metric> <value> [note]
#   regimen edit <id> [--amount N] [--unit U] [--at HH:MM | --meal SLOT | --before-workout]
#
# kit_life.sh — kit_life.py 래퍼
# 진짜 코어는 같은 디렉토리의 kit_life.py다. 이 스크립트는 호환을 위한 얇은 래퍼로,
# kit_life.py의 CLI를 python으로 호출만 한다(인터페이스 100% 보존).
# 서브커맨드·인자·표준출력 포맷은 kit_life.py가 옛 kit_life.sh와 한 글자도 다르지 않게 재현한다.
#
# Operator archive/reset (docs/owner-reset.md):
#   owner-reset backup|plan|apply|restore --db PATH --ag ROOT --warg ROOT  # estate-scrub:allow(legacy CLI option spelling; preserve existing invocations)
#     --archive DIR [--expect DIGEST] [--writers-idle]
# plan is read-only. apply needs the exact plan digest and an unchanged backup.
#
# DB: <database>/kit_life.db (SQLite, WAL)
#   meals    — 식단. area_id → "식습관". kcal은 generated(단백×4+지방×9+(탄-섬유)×4+알코올×7) → INSERT 금지.
#   workouts — 운동. area_id → "신체건강". kcal은 소모칼로리(직접 입력). avg_hr=평균 심박수(bpm, 선택).
#   v_daily_energy — 날짜별 섭취/소모 교차집계 뷰.
#
# Agent-neutral capture:
#   schedule-add <title> <start> [end location purpose summary]
#       --kind appointment|task|auto [--activity workout]
#       [--booked | --appointment] [people=<refs>] [--new]
#   schedule-migrate [id ...] --owner <creator> --title <exact training title>
# The agent judges meaning: appointment for people/booking/fixed external slot,
# task for self activity. The kit accepts the judgment and supplies structure
# from matching owner routines/history, without classifying title words.
# Auto with no stored/typed signal returns exit 9 SCHEDULE_NEEDED with both
# --kind choices for the agent to resolve silently from the conversation.
# Legacy appt-add defaults to appointment unless self-activity data matches;
# new callers pass --kind explicitly. Typed missing ends stay unknown; only
# unmigrated legacy appointment calls keep their pre-existing +3h default.
# Attendance mode is an independent axis; kind does not imply travel policy.
#
# 사용법 (kit_life.py 위임):
#   kit_life.sh day-now [--json] [--now <local-or-offset-ISO>]
#         Read-only local clock, work mode, meals, next meal, remaining blocks,
#         stored attendees and workout done/planned. No title-based attendees.
#   kit_life.sh meal-add <date> <meal> <name> [carb protein fat fiber sugar alt_sugar grams alcohol]
#         alcohol=순수 알코올 그램(선택, 7kcal/g). 술류는 volume_ml×ABV×0.789로 계산해 넘긴다.
#         --ref <phrase> / --from-history: history matches ask before recording.
#         meal-confirm <request> yes|other: answer the printed confirmation;
#         yes records, other asks what was eaten without recording anything.
#         --shake: use product-directed mixing water when volume is unstated.
#         --mix-water-ml <ml>: stated water mixed into this item, not side water.
#         --mix-liquid milk|none: no added water; log milk as its own food.
#         --mix-water-default-ml <ml> --mix-water-source <label>: other product directions.
#         출력: id<TAB>name<TAB>kcal, RESOLVED sentence, meal card, then satiety buttons.
#         2.37.0: then the kit meal card (MEAL_CARD_BEGIN..END + MEAL_CARD_RELAY)
#         and, once, the simple-target offer (TARGET_OFFER / TARGET_BUTTON);
#         the kit card always prints; a pack hook may post it instead (CARD_POSTED).
#   kit_life.sh meal-track-add <metric> [--goal N] | <supplement-name> --regimen
#   kit_life.sh meal-track-del <metric> | <supplement-name> --regimen
#   kit_life.sh meal-track-list [<date>]   config + per-name supplement taken flags (JSON)
#   kit_life.sh meal-card [--body] [<date>]    the kit meal card alone (read only);
#         --body prints only the card body (no markers, no log)
#   kit_life.sh meal-target-ask       the simple-target question (TARGET_ASK)
#   kit_life.sh meal-target-set height=<cm> weight=<kg> age=<y> sex=male|female
#         direction=lose|keep|gain   the kit's simple daily target (2.37.0)
#   kit_life.sh meal-find <date>      그날 식단 목록 (TSV: id name meal kcal)
#   kit_life.sh meal-day  <date>      그날 식단 목록 (사람용)
#   kit_life.sh meal-del  <id>        식단 한 건 삭제
#   kit_life.sh meal-upd  <id> field=value [field=value ...]   식단 한 건 부분 수정
#         field: date meal name carb protein fat fiber sugar alt_sugar grams alcohol mix_water_ml
#         지정한 필드만 갱신(나머지 보존). kcal은 자동 재계산. 출력: id<TAB>name<TAB>kcal
#   kit_life.sh workout-add <date> <category> <subtype> [minutes kcal note avg_hr]
#         category=대분류, subtype=세부 (옛 <type>/<name> 위치 그대로 — 호환 보존).
#         workout_types 사전에서 type_id를 조회해 type_id만 저장(type/name 캐시 컬럼 제거).
#         사전에 없는 분류면 workout_types에 자동 등록 후 그 id로 기록(경고만, 실패 안 함).
#         라벨은 항상 type_id→workout_types 조인으로 복원.
#         출력: id<TAB>type<TAB>name<TAB>kcal<TAB>avg_hr (avg_hr 미지정이면 끝 칸 빔)
#         avg_hr = 평균 심박수(bpm, REAL, 선택). 미지정이면 NULL로 저장.
#         kcal 을 비우면 미기록(NULL, 064) -- 0 이 아니다. 출력의 kcal 칸도 빈 칸.
#         [--kcal-origin measured|computed|default|declared|unknown|estimated] 숫자의 출처,
#         안 주면 unknown(추론해서 measured 로 찍지 않는다).
#   kit_life.sh workout-find <date>   그날 운동 목록 (TSV: id type name minutes kcal)
#         kcal 빈 칸 = 미기록.
#   kit_life.sh workout-del  <id>     운동 한 건 삭제
#   kit_life.sh workout-classify <workout_id> <category> <subtype>   운동 한 건에 분류 추가(다대다)
#   kit_life.sh agg-day  <date>           그날 섭취/매크로/소모/밸런스 (KEY=VALUE)
#   kit_life.sh agg-week <월요일date>     그 주(월~일) 집계 + 직전주 대비
#   kit_life.sh rotation-current [월요일date]   운동 로테이션/주간요약 (KEY=VALUE, 결정적)
#         인자 없으면 이번주(KST). workouts×session_type 조인으로 실제 로그에서 산출(추정 금지).
#         keys: week_monday completed_this_week cardio_this_week last_split next_split deferred remaining_this_week
#         next_split = first split not yet done in the current pass of the cycle (a skipped split stays next; deferred = splits skipped past).
#         로테이션 사이클 = session_type sort<99 를 sort 순서로(랩어라운드). 유산소=session_type_id NULL.
#   kit_life.sh body-state                현재 신체/목표 상태 (KEY=VALUE)
#         마지막 줄 body_stats_defaults= 는 위 숫자 중 저장값이 아니라 코드 기본값
#         (DEFAULT_STATS, 실존 인물 아님)에서 온 키 목록. 빈 값 = 전부 실측 (2.20.0).
#   kit_life.sh config-set key=value ...  신체 스탯/설정 config 테이블 upsert (updated 자동)
#   kit_life.sh config-get <key> [--json]  config 값 1개 읽기(읽기전용, config-set 의 짝). 저장값 그대로;
#   kit_life.sh weekly-retro-start|weekly-retro-done|weekly-retro-status <week_end Sunday>
#         Agent-scoped durable reviewed-week fact in config; prints STARTED|DONE|MISSING + week_end.
#         Both conversational and button sessions call done only on successful close.
#         없는 키는 stdout 비움+종료코드 1 (--json 이면 {"set":false} 출력 후 1)
#   kit_life.sh life-skeleton-get         생활 리듬 프로필 읽기 (KEY=VALUE, 미입력 시 빈 값)
#         keys: life_skeleton_weekday_bed/wake, weekend_bed/wake, work_days, work_start, work_end
#         work_days 저장 포맷(고정): 영문 3글자 요일 콤마조인, 주 순서 -- "Mon,Tue,Wed,Thu,Fri".
#           입력 언어와 무관하게 config-set 쓰기 시점에 이 형식으로 정규화된다.
#         쓰기: config-set life_skeleton_weekday_bed=HH:MM ...
#   kit_life.sh health-agent-get          health-domain owner slug + source (env|crew|derived|default) + fact
#   kit_life.sh health-agent-set <slug>   manual OVERRIDE of the derived owner (config row; relabels in the same tx)
#   kit_life.sh onboard-sweep [--capture] [--json]   온보딩 선행 스윕 (2.22.0)
#         공유 킷(몸/장소/presence/이동/식사/life_skeleton)을 훑는다. --capture 는
#         비어 있는 life_skeleton_work_* 를 presence 루틴에서 파생해 채운다(저장값 불변).
#   kit_life.sh onboard-next [--goal <id>] [--domain D] [--need f1,f2] [--json]
#         온보딩 인터뷰의 다음 스텝 하나. 4분류 게이트(a/b=ask, c=read, d=tell) 통과분만
#         질문이 된다 -- read/tell 페이로드에는 question 키 자체가 없다.
#   kit_life.sh onboard-set <field> <value> [--by owner_stated|owner_approved|agent_computed]
#         답 기록 + 한 줄 receipt. c 필드(travel 등)는 거부된다(읽는 값).
#   kit_life.sh onboard-card [--json]     되비춤 카드 (귀속/방식/미러 상태 반영)
#         2.22.0 후속: 방향(지향) 줄이 맨 앞, after_confirm 줄이 맨 뒤에 붙는다 --
#         확정 직전에는 미리보기 금지(A3)가 뒤집혀 "확정하면 무엇이 이어지는지"를
#         반드시 말한다(F9). 카드는 존재하는 것만 적는다(F10).
#   kit_life.sh onboard-defer --until <YYYY-MM-DD>   "나중에"는 날짜 없이는 거부
#   kit_life.sh onboard-defer-nudge [--dry]   one morning-brief line on/after the
#         deferred date, once per deferral (2.37.0); an answer or restart clears it.
#   kit_life.sh onboard-restart [--json]  인터뷰 명시적 재시작 (F8). 접힌 판의
#         답은 지워지지 않고 superseded 로 넘어가 ask 페이로드의 prior 로만
#         다시 나온다 -- 확인 없이 이번 판의 답으로 쓰이는 경로가 없다.
#         목표를 다른 것으로 바꿔 물리면(onboard-next --goal) 자동으로 같은
#         재시작이 일어난다.
#   kit_life.sh goal-habit-note <why-id> <axis-key|probe> <number|direction|only> [--stage]
#   kit_life.sh goal-graduation [--domain D] [--owning-agent A] [--json]
#         F13 승급 엔진의 읽기 표면 (주간 회고 전용). 단기 칸과 maintained
#         행마다 graduate|stepdown|drift|hold|unevaluated 판정 + 제안 문장.
#         단기 목표는 태어날 때 졸업/하향 조건을 detail 에 지니고
#         (graduate_streak_weeks=4, stepdown 4주 중 2주 미달 기본), 달성은
#         은퇴가 아니라 maintained 상태 전이다 -- 추적이 남아 이탈이 보인다.
#         읽기 전용. 숫자를 지어내지 않는다(하향치·다음 칸은 오너와 정한다).
#   kit_life.sh goal-example-coverage --domain D [--owning-agent A]
#         --examples '<JSON [{"text","axis_key"}]>' [--json]
#         F3: 예시 목록의 축 커버리지 검사 (팩이 채우는 선언 훅). 측정 가능한
#         축이 예시에서 빠지면 그 축 이름을 대며 종료코드 3. 지표 없는 축은
#         missing_unmeasurable 로만 보고한다(실패 아님).
#   kit_life.sh days-check <요일들> [--json]
#         F15: 요일 예시가 "연속 이틀 금지" 원칙을 지키는지 검사. 위반이면
#         짝을 대며 종료코드 3. 주 4회 이상이라 원칙 자체가 성립 불가면
#         principle_unsatisfiable (종료코드 0) -- 그때는 예시가 아니라 원칙
#         문장을 빼는 게 맞다.
#   kit_life.sh handover-brief             민팅 인계 슬롯 (Item 11, 매니페스트
#         handover_brief_verb 가 가리키는 동사). 읽기 전용, stdout = 오너 대면
#         한국어 산문. 지향은 짧은 이름(life_why.label)으로만 문장에 들어가고,
#         이름이 아직 없으면 지향 구절을 빼고 말한다(원문을 인용하지 않는다).
#         넘길 것이 없으면 아무것도 출력하지 않고 exit 0 (부재는 정상).
#   kit_life.sh vocab --json               voice-recognition vocabulary hints (DGN-1694; the verb
#         the manifest vocab_verb names). JSON array of person/place/food/exercise/
#         group/sub_place surface forms (name, display_name, aliases; rooms with an
#         ASCII core) + last-used time (epoch), max 100. Read-only (mode=ro),
#         no migration. Any error: no output, exit 1.
#   kit_life.sh vocab --stamp              (073) one 16-hex line: the dictionary's
#         freshness stamp; changes on any add / rename / alias / retire.
#   --- 073 proper-noun registry (noun_registry.py) ---
#   kit_life.sh noun-tag "<utterance>" [--span <text>]... [--field <field>] [--place <p>] [--json]
#         resolve-before-fill: SPAN<TAB>text<TAB>kind<TAB>ref<TAB>display<TAB>match<TAB>source;
#         an unknown --span -> TYPE_ASK + ASK + ASK_OPTIONS + NEXT lines (once per
#         name; 2 skips -> never again; ledger_guard noun_ask_daily_cap per day).
#   kit_life.sh noun-context "<text>" [--span <text>]... [--days 14] [--from YYYY-MM-DD] [--json]
#         READ ONLY context-first lookup (no ask, no noun_ask state): the registered
#         names in the text + the appointments/stays from local today to +days they
#         point at (participant, group, place, or an event-title word):
#         NAME<TAB>kind<TAB>ref<TAB>display<TAB>text<TAB>scan|agent / NEAR<TAB>text<TAB>dom:ref:name|..
#         EVENT<TAB>id<TAB>kind<TAB>local start<TAB>title<TAB>place<TAB>party a|b<TAB>via
#         (via = person:N,group:N,place:N,near:person:N,title:<word>) / CONTEXT<TAB>names=
#         events= from= to= persons= / CONTEXT_RELAY. Named persons (2026-10-09) add
#         PERSON<TAB>id<TAB>name<TAB>relation=<TAB>birthday= / PERSON_FACT<TAB>id<TAB>field<TAB>v /
#         PERSON_REL<TAB>id<TAB>role<TAB>other<TAB>name<TAB>birthday=<TAB>note= / PERSON_EVENT<TAB>id<TAB>
#         born|child_born<TAB>date<TAB>other<TAB>name<TAB>summary (birth 0..60 days) / PERSON_RELAY;
#         two persons sharing a name -> AMBIGUOUS<TAB>person<TAB>text<TAB>id:name|.. (no facts).
#         Opens the DB query_only. Run it before asking where / who-for / gift advice.
#   kit_life.sh noun-type <ask_id> <person|group|place|sub_place|project|not_a_name>
#         [--parent <place>] [--alias-of <kind>:<id>] [--said "<words>"]   TYPE_ASK answer
#   kit_life.sh noun-ask-forget <text> / noun-ask-list / noun-fields [--field <f>]
#   kit_life.sh group-add <name> [display=D] [aliases=a|b] / group-find / group-show /
#         group-set <g> name=|display_name=|status=active|gone / group-alias / group-alias-rm /
#         group-del <g> [--into <g>] (IN_USE exit 4) / group-blocks <g>
#   kit_life.sh group-member <g> <person>...  add confirmed members; preserves other groups
#   kit_life.sh appt-group <block_id> <group>... / appt-group-rm <block_id> <group>
#         (block-group / block-group-rm: any kind) -- groups as participants (event_group)
#   kit_life.sh appt-add ... room=<room> / appt-upd <id> room=<room>   the room of THIS
#         booking (block_attr sub_place_id); REPLACED / ROOM_SET / ROOM_ADDED / ROOM_CLEARED;
#         a group/person/place value -> FIELD_KIND_REFUSED exit 8; no place -> PARENT_ASK
#         exit 6 (appt-upd room only); two room= -> E_ROOM_MULTI exit 2.
#   kit_life.sh sub-place <place> rename <old> <new> / rm <room> [--into <room>]
#   kit_life.sh person-del <id|name> [--into <person>]   ref-safe (IN_USE exit 4)
#   kit_life.sh near-match-forget <domain> <ref> <query>   undo a "not the same"
#   kit_life.sh why-add --text T [--label L] [--domain D] ...   지향 기록 (054)
#         --text 는 오너가 한 말 그대로(출처), --label 은 문장에 들어갈 짧은 말
#         (20자 안쪽, 끝맺음 없이). 문장 같은 --label 은 한 줄 교정 안내와 함께 거부.
#   kit_life.sh why-label-set <why_id> <label>   이미 있는 지향에 짧은 이름을 붙인다
#         (054). WHY-LABEL-ASK 줄에 대한 오너 확인이 오면 부른다. text 는 안 건드린다.
#   kit_life.sh goal-interview --next [--domain D] [--stage] [--json]   (055)
#         목표 상담의 다음 한 수. kind + 사용자에게 할 말(readback/say, 킷이
#         렌더) + 답이 오면 돌릴 명령. 방향 -> 갈래 -> 지금 하는 것 -> 순서 ->
#         제안 -> 재는 주기 -> 마무리.
#   kit_life.sh axis-add --why-id N --axis "<갈래>[=metric,...]" [--axis ...]
#         (055) 사용자 답에서 나온 갈래를 그 사람 것으로 만든다. SAY 줄이 복창.
#   kit_life.sh axis-order --why-id N (--first <axis_key> | --together)
#         (055) 서로 당기는 갈래의 순서 결정을 적는다.
#   kit_life.sh metric-judged <metric> [--json]   (noise-aware)
#         몸 지표의 흔들림 인지 판정: 최근 4회 평균 + 그 사람 기록에서 낸
#         측정 흔들림 폭. 폭 안의 변화는 "변화 없음 범위", 밖이면 오름/내림.
#   kit_life.sh goal-move-note --why-id N (--activity "<말>" | --unknown <m>
#         | --cadence-declined <m>)   (055) 상담 중 답을 적어 같은 질문이
#         다시 안 나오게 한다.
#   kit_life.sh goal-move-note --why-id N --safety "none|<말>"   상담의 안전
#         질문(ask_safety) 답. 부위가 있으면 부위마다 constraint-add 도 부른다.
#   kit_life.sh axis-list --why-id N [--json]   한 지향의 갈래 목록
#         {axis_key, label, phase_order, metrics, balance} -- 소비자는 이것만 읽는다.
#   kit_life.sh constraint-add --class nutrition|safety|time --key <key>
#         --value "<말>" [--source S] [--inviolable] [--json]   상시 제약 1행.
#         safety 키는 injury_<부위>[_l|_r]. 같은 (class,key) 활성 행이 있으면
#         값만 갱신(중복 없음). 사용자 본인 답으로만 부른다.
#   kit_life.sh constraint-list [--class C] [--all] [--json]
#   kit_life.sh constraint-retire <id>
#   kit_life.sh body-brief [--json]   one read of the body: active safety
#         rows (injury_*: site + side, the user's words, source, date) and
#         training facts (activity, place, experience, with source and date),
#         and body measures (height, weight, body fat %, skeletal muscle:
#         value, date, source; 2.42.0).
#         Call it before answering or asking about body/health/training.
#   kit_life.sh place-hours-set <place> <json|text> [--source <url>]
#         [--fetched YYYY-MM-DD]   067: store a place's opening hours (one row
#         per place, upsert). Written by the agent after PLACE_HOURS_NEEDED
#         (it looks the hours up itself, silently); 'none' = keeps no hours.
#   kit_life.sh place-hours <place> [YYYY-MM-DD] [--now <UTC>] [--json]
#         READ: OPEN open close breaks | CLOSED why | UNKNOWN why | NO_HOURS,
#         + PLACE_HOURS_DEFAULT (a no-time visit's start inside the hours),
#         PLACE_HOURS_TEXT (reply copy), PLACE_HOURS_NEEDED when none/stale.
#   kit_life.sh errand-classify <title> [--place <name>] [--at HH:MM]
#         [--booked]   READ: errand | appointment | ask | task | other (errand.py;
#         an errand = a no-time task at a place, due by its closing time).
#         appt-add / task-add --at exit 7 (ERRAND_FILE / ERRAND_ASK, nothing
#         written) on an errand; --booked = the owner has a booked time.
#   kit_life.sh errand-plan <id> [...] [--now <UTC>]   READ: ERRAND deadline,
#         latest departure, travel, slack, ok|tight|late (no buffer).
#   kit_life.sh place-region-set <place> <CC> [--source <s>]   069: the
#         country a place is in (agent lookup on PLACE_REGION_NEEDED).
#   kit_life.sh map-region-set <CC> <provider[,provider]|none> [--label <name>]
#         [--source <s>] [--box lat_min,lat_max,lng_min,lng_max]   069: a
#         region's local map apps (looked up); only linkable ones are kept.
#   kit_life.sh map-provider-set <CC> <provider[,provider]>   069: the
#         owner's map pick for a region (MAP_PROVIDER_ASK answer).
#   kit_life.sh map-provider [--json]   READ: regions, local maps, picks.
#   kit_life.sh place-coord-missing   READ: pairs whose 066 coordinates are
#         empty (place-coord-backfill.sh walks it).
#   kit_life.sh place-route-end <place>   READ: one end of a map drive --
#         PLACE_END<TAB>id<TAB>ulid<TAB>name<TAB>base|stay|place<TAB>address<TAB>coord 0|1
#         <TAB>address asked 0|1; unknown -> PLACE_END_ABSENT, exit 3 (travel-resolve.sh
#         --coords-only reads it before driving the map).
#   kit_life.sh place-address-ask <place>   NEEDS_ADDRESS was asked (once per place):
#         place-address-ask<TAB>applied|already<TAB>id; the answer -> place-address <id> add.
#   071 module_shopping (shop.py; skill shopping-session). The find request is a
#   ledger: hunt -> rounds -> shown cards. Exit 3 EXISTS, 4 REFUSED (engine
#   gate, nothing written), 5 NEAR_MATCH.
#   kit_life.sh shop-hunt-open <category> --ask "<words>" [--for <person>]
#         [--budget N] [--by YYYY-MM-DD] [--new]   OPENED | RESUMED (one open
#         hunt per category+person) | EXPIRED (exit 3, resume question).
#   kit_life.sh shop-hunt-cursor [<id>]   state, constraints, latest round,
#         picks, dropped directions, rejected items, EXPECT
#         round|verdict|direction|budget_decision|close|reaim|none.
#   kit_life.sh shop-hunt-list [--all|--state s] | shop-hunt-upd <id> [--ask]
#         [--budget N|none] [--by D|none] [--for p|self] [--budget-hard 0|1]
#         [--reopen] | shop-hunt-close <id> --picked <shown>|--abandon|
#         --to-wish <shown> (WISH_NOT_ON until P3) | shop-hunt-del <id>
#         (HUNT_HAS_PURCHASE once owned_item exists).
#   kit_life.sh shop-round-add <hunt> --seen a,b [--blocked c,d] [--assume ..]
#         [--partial "<why>"] [--reaimed] --card '<json>' x3-4   gates:
#         NO_COVERAGE ROUND_SIZE REAIM_DUE MISSING_FIELD UNVERIFIED_CARD
#         DUP_ITEM DUP_DIRECTION DROPPED_DIRECTION REJECTED_ITEM SHOWN_BEFORE
#         OVER_BUDGET(hard); prints the round render (COVERAGE, CARD, ASK).
#   kit_life.sh shop-round-show <hunt> [<n>] | shop-round-upd <round> ...
#   kit_life.sh shop-verdict <hunt> --pick s[,s] | --reject s[,s] [--scope
#         round|item] | --reject-all | --default s | --closest s
#         [--verbatim ".."] [--round n]   BAD_SLOT lists the valid slots.
#   kit_life.sh shop-shown-list <hunt> | shop-shown-void <shown_id>
#   kit_life.sh shop-cat-list [--all] | shop-cat-add <token> [--display ..]
#         [--consumable 0|1] [--checks a,b] [--new-confirmed] | shop-cat-upd
#         | shop-cat-retire | shop-cat-del (CAT_IN_USE when referenced).
#   kit_life.sh shop-item-upsert --category c --name .. [--brand] [--variant]
#         [--url] [--image] [--new-confirmed] | shop-item-find "<w>" |
#         shop-item-upd <id> [--alias ..] | shop-item-merge <from> <into> |
#         shop-item-del <id> (ITEM_IN_USE when shown).
#   kit_life.sh shop-capture <item_id> --purpose <dish/event> --size <label>
#         [--status picked|in_cart|ordered|bought] [--on YYYY-MM-DD]
#         Photo selections / carts: shop-item-upsert then shop-capture;
#         preserves package facts and purpose without inventing payment.
#   kit_life.sh shop-stock-find "ingredient"
#   kit_life.sh shop-stock-set OWNED_ID QUANTITY --unit pack --unit-size 80 --size-unit g
#   kit_life.sh shop-stock-use ITEM_ID QUANTITY --unit pack --key MESSAGE_ID
#   kit_life.sh shop-stock-empty ITEM_ID --key MESSAGE_ID
#   kit_life.sh shop-payment-match SPEND_ID [--captures ID,ID]
#         Captures/purchases accept --quantity COUNT --unit pack --unit-size SIZE --size-unit g.
#         meal-add accepts --from-stock ITEM_ID --stock-quantity COUNT --stock-unit pack.
#   kit_life.sh shop-capture-find [item words] [--purpose dish/event]
#         | shop-capture-upd <capture_id> [--status ...] [--size ...]
#         Default day: LIFEKIT_CONTEXT_DATE (conversation day), else local today.
#   072 module_shopping P2 (shop.py; skill shopping-log). Price has ONE home:
#   the linked spend_entry when the ledger link is on (config shop_ledger_link,
#   default on), else owned_item.price. Exit 3 = EXISTS / LEDGER_EXISTS.
#   kit_life.sh shop-bought [--hunt H | --shown S | <item> | --name n
#         --category c] [--on D] [--store s] [--link u] [--price N] [--size L]
#         [--online 0|1] [--link-spend id | --new-spend] [--new]   fills
#         store/link/price from the hunt's picked card, MISSING + ASK (one
#         line), LEDGER spend|off|no_price, HUNT_CLOSED, NOTICE withdrawal_kr
#         (online, region KR: config shop_region or the home place).
#   kit_life.sh shop-owned-add | shop-owned-find "<w>" | shop-owned-list
#         [--category c] [--status s] | shop-owned-upd <id> [--satisfaction]
#         [--verbatim] [--fit] [--status] [--price N] [--refund] ... |
#         shop-owned-retire <id> | shop-owned-del <id> [--keep-spend |
#         --with-spend] (OWNED_HAS_SPEND while linked).
#   kit_life.sh shop-measure-add <owned> part=cm [...] [--replace] |
#         shop-measure-list [<owned>] | shop-measure-del <owned> <part>|--all
#         parts: total_length shoulder chest_half waist_half hip_half
#         thigh_half rise hem_half sleeve (garments only, NOT_GARMENT).
#         Body sizes are body facts: log-metric <date> chest_cm|waist_cm|
#         hip_cm|shoulder_width_cm|inseam_cm|arm_length_cm|foot_length_mm <v>.
#   kit_life.sh shop-taste-add <cat> --axis a --value "<w>" --polarity
#         like|avoid [--replace] | shop-taste-list [<cat>] [--all] |
#         shop-taste-upd | shop-taste-retire | shop-taste-del.
#   kit_life.sh shop-want --ask "<w>" [--need runout|broken|event|deadline]
#         [--by D]   GO_FIND | OFFER_COOLOFF (+3 CHOICE) | COOLOFF_PENDING;
#         shop-want-answer --ask "<w>" --choice now|later|reflect   later =
#         ONE reminder task (+shop_cooling_days, default 7, at
#         shop_cooloff_at), find-first.
#   kit_life.sh shop-size-draft --hunt H | --category c --chart '<json>'
#         [--brand b]   SIZE_DRAFT basis=owned|body|band, FIT_SHIFT,
#         SIZE_OUT_OF_CHART | SIZE_NEED <token> + ASK | SIZE_NONE gift.
#   kit_life.sh resource-add --kind env --name "<장소>" --detail
#         '{"venue":"gym|home|other","equipment":[...]}' [--source S] [--json]
#         운동 장소 등 자원 1행. 같은 (kind,name) 활성 행이 있으면 갱신.
#   kit_life.sh resource-list [--kind K] [--all] [--json]
#   -- 074 consult anchor (consult_anchor.py): every consult answer lands in
#      the kit; --anchor <domain>:<question> [--said W] on a write verb
#      records where (consult_answer), same transaction --
#   kit_life.sh resource-add --kind env ... [--place <id|name>] [--address A]
#         [--lat D --lng D] [--new-confirmed]   env = a kit place
#         (home -> base; found -> linked; none -> created; near -> NEAR_MATCH)
#         | resource-add --kind equipment --name <tool> --detail '{"carry":true}'
#   kit_life.sh resource-place-link <id> [--place P] [--address A] [--lat --lng]
#   kit_life.sh resource-equipment <id> --set|--add|--remove a,b
#   kit_life.sh constraint-add ... [--care '<json>']  |  constraint-care
#         (<id>|--key K) (--care '[{"drill":..,"when":before|after|anytime|
#         heavy_sets,"minutes":N,"for":..,"words":..}]' | --clear)
#   kit_life.sh preference-add --topic T --item I [--detail '<json>']
#         [--note W] [--polarity negotiable|inviolable] [--replace]
#         | preference-list [--topic T] | preference-retire <id>
#   kit_life.sh availability-add --domain D --days MON,WED (--from HH:MM
#         --to HH:MM | --anytime) [--replace] | availability-list | -retire
#   kit_life.sh diet-target-set (--deficit N|--surplus N|--from-config)
#         [--protein G]   ledger_constraint nutrition first, then config
#   kit_life.sh consult-answer-add --domain D --question Q --landed TABLE:REF
#         [--words W] [--replace]  |  consult-answers [--domain D] [--all]
#         why-add / goal-add / goal-set also take --anchor D:Q [--said W]
#   -- 075 ledger v6 (ledger_v6.py / ledger_diag.py): one export in, one landed
#      ledger out; the agent never opens the database, parses a file or sums --
#   kit_life.sh ledger-import <file> [--password-stdin]   parse, canonical
#         payment methods (account | card | wallet), classes (own move, top-up,
#         card bill, pass-through, saving, loan, meal points), import identity +
#         bucket reconcile with the rows already there, land, streams.
#         JSON: events new attached same first last classes methods streams
#   kit_life.sh ledger-diagnosis [--month YYYY-MM] [--json]   the diagnosis of
#         the last full month: income, regular vs variable money out, saved
#         money, levers (rule + amount), the one question candidate
#   kit_life.sh funding-evidence <card>   read-only product/bill funding proof
#   kit_life.sh bank-evidence <account>   read-only bank proof, guesses and own-row clues
#   kit_life.sh purpose-evidence [--today YYYY-MM-DD]   read-only 3-month purpose scope and real rows
#   kit_life.sh method-merge <source> <target>   owner-confirmed same instrument;
#         repoints entries, funding and fixed costs; retains source names as aliases
#   kit_life.sh ledger-find <text> [--month M] [--raw --file F]   a payment in
#         the ledger / streams / the last file ("where did my phone bill go")
#   kit_life.sh stream-list [--all] | stream-reconcile [--month M] |
#         stream-stop <id> [--said W] (the owner's word only) |
#         stream-keep <id> must|reducible|droppable | stream-set <id>
#         amount|name|class|state V | stream-merge <id> <into> |
#         stream-link <id> <kind> <ref>
#   kit_life.sh funding-set <wallet> <source> [--default] [--sub S] |
#         method-scope <name> personal|business | ledger-card-answer <spend>
#         company|mine | ledger-state | ledger-dedupe | ledger-reset
#         --dry-run|--yes | ledger-restart --dry-run|--yes (reset + onboarding
#         state / recording-from archived together; refuses if nothing to reset)
#   kit_life.sh spend-link <spend> <kind> <ref> | spend-unlink |
#         spend-person <spend> <person> | spend-link-rule-add --kind K --ref R
#         [--method M --weekday 1-5 --time HH:MM-HH:MM --merchant T --cat C
#         --class C --min N --max N --said W] | spend-person-rule-add --person P
#         ... | spend-link-rule-list | spend-link-rule-off <id> |
#         spend-link-sum <kind> [ref] [--month M]   kinds: person venue
#         workout meal supplement gift shop_item owned_item -- each module
#         reads the money spent on its domain
#   kit_life.sh spend-category-list [--since YYYY-MM-DD] [--sum] [--json]
#         active expense categories, most spent first; --sum = the total
#         (ledger onboarding buttons)
#   -- ledger balances (ledger_balance.py): readings on the instrument's
#      entity, hand (declared) / auto (measured) kept apart --
#   kit_life.sh balance-set <instrument> <amount> [--as-of D] [--auto
#         [--source S]]  |  cash-set <amount> [--as-of D]
#         |  card-bill-set <card> <amount> --due D [--as-of D]
#   kit_life.sh balance-show [--today D] [--stale-days N] [--say] [--json]
#         latest reading + age per instrument, MISSING when none (never 0),
#         flow since + estimated (labelled; own transfers are not in flow)
#   kit_life.sh balance-stale [--tsv]   missing / older than 7 days (weekly)
#   kit_life.sh card-bill-month [YYYY-MM] [--say]   bill reading due that
#         month, else recorded card spend (said as spend, not the bill);
#         with installments: estimate = one-off spend + charges due that month
#   -- card installments (ledger_installment.py): entity per purchase owned
#      by the card's entity, terms as measures; remainder on the first month --
#   kit_life.sh installment-add <card> <total> <months> [--first YYYY-MM]
#         [--rate PCT] [--name N] [--date D] [--spend ID]
#   kit_life.sh installment-list [--month YYYY-MM] [--all] [--say]   n/N,
#         this month's charge, remaining, ends; per-card monthly load
#   kit_life.sh installment-done|installment-cancel <ref> [--date D]
#   kit_life.sh installment-detect [--say] | installment-confirm <spend:N|
#         intake:N> [--months N] [--first] [--rate] | installment-dismiss <ref>
#         candidates from spend notes / SMS / export text, never auto-added
#   -- ledger records profile (ledger_profile.py, read only; onboarding v5) --
#   kit_life.sh ledger-records | methods-seen | fixed-detect
#         | flow-picture [--months N]   JSON: span + pending import count;
#         methods with monthly average / top merchants / kind / bill account
#         / twins (alias, normalized name, same use) / role (salary_fixed |
#         living | saving | business; owner's word else inferred); recurring candidates (monthly, variable, yearly, saving,
#         income + payday shift); monthly income / fixed / saving / regular /
#         irregular / surplus over the last full months with records
#         (+ money moved in from own securities / savings accounts, the
#         deficit split covered / unexplained)
#   kit_life.sh spend-habits [--json]   the records' year by category after
#         twins / own-account moves / card bills / pay charges are out;
#         money kept (securities / savings / exchange) summed as saving;
#         loan principal + interest summed apart; person-to-person
#         transfers (remit) apart; rent filed as a loan is housing
#   kit_life.sh recurring-summary [--json]   <= 100 lines (merchant, months
#         seen, amount range, category, method, rule verdict) the agent
#         reviews for merges / category fixes / fixed vs variable
#   kit_life.sh sub-tiers [--month YYYY-MM]   tiered / usage subscriptions
#         (subscription_tiers.json): each charge tagged tier | usage (scope
#         kept), per-month plan / usage / tier, tier changes
#   kit_life.sh fixed-add ... --find-first   same direction + normalized name
#         exists -> EXISTS<TAB>id, exit 3, nothing written  |  fixed-list --json
#         Explicit differences add CORRECTION<TAB>JSON on a separate line.
#   kit_life.sh fixed-correct <name|id> field=value [...] [--direction expense|income] [--json]
#         Fields: cycle amount next_due name. Existing rows only; relay say.
#   kit_life.sh body-segment [--json]   인바디 부위별 제지방(lean_<부위>_kg)
#         최신값 + 같은 날 결과지의 표준 대비 %(lean_<부위>_pct, 있을 때만).
#   kit_life.sh log-metric <date> <metric> <value> [note]   측정값 시계열 1건 upsert
#   kit_life.sh trend <metric> [--window <일수>] [--json]   추세 표시 primitive (킷 공용)
#         지표 하나의 최신값+방향(↑↓→)+직전 대비 델타+창 전체 델타 한 줄.
#         지표는 TREND_REGISTRY(kit_life.py)에 등록된 것만: weight_kg
#         skeletal_muscle_kg fat_mass_kg body_fat_pct kcal_intake. 없는 지표를
#         주면 등록된 목록을 보여주고 종료코드 1. --window 는 기본 창(일)을
#         정수 일수(7 또는 7일)로 덮어쓴다(기본: weekly-point류 84일=12주, daily-multi류 7일).
#         값의 의미는 모른다 -- 오르내림만 말하고 좋다/나쁘다는 말하지 않는다.
#         --json 이면 source/point_count 등을 담은 구조체(호출부용) 출력.
#   ── 통증/폼결함 (047, 측정축). pain.py 동사·플래그 그대로 ──────────────
#   kit_life.sh pain-add --exercise <slot> --pain <0-10> [--site <부위>]
#         [--side L|R|both|NA] [--load kg] [--set-no N] [--rir N]
#         [--rom setup|descent|bottom|ascent|lockout|other] [--fault <문자열>]
#         [--action load_drop|substitute|stop|continue|form_cue] [--session 앞A]
#         [--note ...] [--ts 'YYYY-MM-DD HH:MM'] [--no-link]
#         사건 1건 기록. 같은 날 workout_sets 에 (세트번호, 중량)이 맞는 행이
#         정확히 하나면 set_ref 로 붙고(그 순간 load/set/rir 는 세트 행이 정본),
#         아니면 그날의 세트 있는 세션으로 붙고, 그것도 없으면 안 붙는다.
#         --site 는 승격 축이다. 빠지면 NO_SITE 로 고지하고 pain-candidates 에
#         안 잡힌다(사건이 쌓여도 상시 제약이 될 수 없다).
#   kit_life.sh pain-list [--exercise X] [--site S] [--unsited] [--days N] [--open]
#   kit_life.sh pain-recent [--days N]        미해소 사건 (세션 후 보고용)
#   kit_life.sh pain-resolve --id <N|ulid> [--guardrail ...] [--note ...]
#         [--program-change ...] [--site <부위>] [--reopen]
#   kit_life.sh pain-show --id <N|ulid>
#   kit_life.sh pain-link --id <N|ulid> [--set <workout_sets.id>]
#         세트가 나중에 기록된 사건에 조인을 나중에 붙인다(--set 생략 시 자동매치).
#   kit_life.sh pain-audit [--window-days 30] [--threshold 3]
#         재발 패턴 스캔. 연결된 행은 정규 슬롯명으로 묶이므로 한/영 혼용이
#         그룹을 쪼개지 않는다(F34).
#   kit_life.sh pain-candidates [--weeks 12] [--min-events 3] [--all]
#         승격 표면: 부위별 사건 수 + 그 부위를 덮는 ledger_constraint 유무.
#         ★ 읽기 전용이다. ledger_constraint 에 절대 쓰지 않는다 --
#         상시 제약은 오너가 확인하는 안전물이다.
#   ── 들어온 기록 장부 (057). 모듈이 트랜스포터 사건을 킷 행으로 옮길 때의 공용 장부 ──
#   킷은 트랜스포터를 읽지 않는다 -- 읽기/해석/당길 때는 모듈 몫. 백필 없음:
#   동의 전 사건, 동의 없는 kind 는 동사와 트리거 둘 다 거부(exit 6 REFUSED).
#   kit_life.sh ingest-consent <source> <kind> [--off] [--module M] [--json]
#         모듈 온보딩이 사용자 승인 때 부른다(운영자 수동 단계 아님). approved_at=지금,
#         소급 불가. --off 후 다시 켜면 approved_at 이 새 시각 -- 꺼진 동안은 안 들어온다.
#         CONSENT source= kind= state=on|off|none approved_at= verdict=NEW|SAME|RESUMED|REVOKED|NONE
#   kit_life.sh ingest-check --source S --kind K --occurred-at TS   ALLOW | REFUSED <이유>(6)
#         도메인 행(workout-add/log-metric/spend-add)을 쓰기 전에 묻는다.
#   kit_life.sh ingest-put --source S --kind K --id X --occurred-at TS
#         [--target-table T] [--target-id N] [--hash H]
#         NEW | SAME(아무것도 안 씀) | CHANGED(같은 줄 갱신, revision+1) | REFUSED(6)
#   kit_life.sh ingest-get --source S --kind K --id X     NONE | table<TAB>target<TAB>hash
#   kit_life.sh ingest-get --source S --kind K [--mark-pulled] [--json]
#         CURSOR consent= floor=(승인 시각: occurred_at 하한) since=(마지막 당김, 없으면 floor)
#         last_occurred_at= refs= ; 동의 없으면 exit 6. --mark-pulled = 이번 당김을 기록.
#   kit_life.sh ingest-reset --source S [--kind K ...] [--apply] [--force]
#         Dry-run by default, archive before removing proven ingest effects.
#         Active source consent or ambiguous provenance refuses (6).
#   kit_life.sh ingest-reset-undo --reset-id R
#         Restore exact archived values; refuse if records changed after reset.
#   kit_life.sh ingest-provenance-import --evidence FILE [--apply]
#         Verify cited offline backup hashes and reconstruct legacy effects; audited dry-run default.
#   kit_life.sh ingest-provenance-import-undo --import-id I
#         Remove unchanged imported effects; undo an intervening reset first.
#   kit_life.sh ingest-link [--json]   live HealthKit consent + app link step 1 (read only)
#   kit_life.sh ingest-status [--source S] [--kind K] [--json]   INGEST ... refs= changed=
#   kit_life.sh metric-register --token T --scope entity|domain|block|place_link
#         --accrual accrued|observed|derived --unit U --owner-module M
#         모듈이 자기가 쓰는 지표 토큰을 등록. OK NEW | OK SAME | CONFLICT(4, 안 덮어씀).
#         시각은 시간대 붙은 ISO-8601(Z 또는 +09:00)만 -- 시간대 없는 시각은 거부.
#   ── 근육 피로 (056). 저장 안 함 -- 조회 시 세트 x 근육맵 x 감쇠로 계산 ──────
#   kit_life.sh muscle-state [--at 'YYYY-MM-DD HH:MM'] [--all] [--json]
#         [--exercises name[:sets],... | --session-type <id|code> | --nodes n,n]
#         근육 노드별 잔여 피로(residual/dose/%/상태/회복ETA) + 근거(언제 무슨 종목).
#         SHOW 줄 = 표시 묶음(세부 부위 이름, 여러 세부가 같이 지쳤을 때만 상위
#         이름; 등은 승모근/광배근/척추기립근으로 갈라 본다). 계획 세션을 주면
#         LEVEL 줄 = 단계(none|light|moderate|heavy -> prep_bump|swap_package|
#         routine_change). 쉬자는 단계는 없다. 맵 없는 종목은 COVERAGE_HOLE
#         (피로 없음이라고 말하지 않는다), 세트 없는 유산소는 NOT_COUNTED.
#   kit_life.sh muscle-week [--week YYYY-Www] [--json]
#         주간 많이 쓴 부위 TOP3, 덜 풀린 채 다시 쓴 날(OVERLAP), 기능 점검
#         (한발 버티기/회전근개·견갑/코어/중둔근/종아리·발목) 세트 수 --
#         운동 목록에 해당 종목이 아예 없으면 NO_EXERCISE.
#         FUNCTION_DIRECTION on|off = 사용자가 직접 만든 기능 갈래(055 user strand 가
#         unilateral_share_sets / zero_load_control_share_sets 를 가짐)가 있는가.
#         off 면 팩의 주간 회고가 기능 점검 줄을 내지 않는다.
#   kit_life.sh body-signal-add --site <근육노드> --sensation stiff|tight|sore|pain
#         [--side L|R|both|NA] [--pain N] [--exercise X] [--note ..] [--ts ..]
#         "햄스 뻣뻣" 같은 몸 신호. 24h 안 같은 부위x감각이면 그 행을 갱신(find-first).
#         stiff/tight/sore 는 72h 동안 그 부위를 '덜 풀림'으로 한 단계 보수적으로
#         읽는다. 부상 승격 표면(pain-candidates)에는 pain 만 올라간다.
#   kit_life.sh body-signal-list [--days N] [--site S] [--open] [--with-pain] [--json]
#   kit_life.sh body-signal-resolve --id N  /  body-signal-del --id N  (pain 사건은 거부)
#   kit_life.sh exercise-add --name <snake> --movement compound|isolation
#         --load-type freeweight|machine|cable|bodyweight [--machine <ulid>]
#         --pattern <패턴> (--muscle node:primary|secondary ... | --motion <동작> [--grip G])
#         [--cost high|med|low] [--stabilizes node ...] [--unilateral] [--update]
#         종목 생성 게이트. 주동 근육 없으면 빠진 칸을 이름대며 거부. --motion 이면
#         근육맵을 초안(draft)으로, 비용을 휴리스틱으로 채워 질문 0으로 등록하고,
#         세션 끝에 exercise-drafts 로 한 번 확인 -> exercise-confirm. 같은 이름은
#         EXISTS(3). DB 층은 056 트리거(맵+비용 없는 ax_exercise INSERT 거부).
#   kit_life.sh exercise-drafts [--since 'YYYY-MM-DD HH:MM'] [--json]  /  exercise-confirm --name X
#         --since = 그 로컬 시각 이후 만든 종목만(세션 중 만든 초안 확인용). JSON 에 created_at.
#   kit_life.sh exercise-tag-del --name X --group G --value V
#   kit_life.sh workout-unclassify <workout_id> <category> <subtype>  분류 연결 하나 제거
#   kit_life.sh targets --burn N          eff_goal bmr neat deficit protein_goal (공백구분 한 줄)
#         --burn 생략 = 그날(--date, 기본 오늘) 실제 소모를 읽는다(064, 예전엔 0).
#         기준선만 원하면 --burn 0. agg-day / body-state 끝에 burn_known= 추가:
#         0 = 운동했는데 kcal 미기록 -> 목표는 base (드러내기만, 판단은 오너).
#         저장된 신체 수치가 없어 코드 기본값으로 계산한 경우에만 둘째 줄
#         "BODY_STATS_DEFAULTS n <키목록> -- ..." 가 따라붙는다 (2.20.0).
#         첫 줄은 무변경 -- `read -r a b c d e <<< "$(... targets)"` 는 그대로 동작.
#   kit_life.sh dump                      sqlite3 .dump 상당 (백업용)
#   kit_life.sh check [--db path]         read-only integrity/version report (DGN-672; backup gate / restore verify)
#   kit_life.sh snapshot [--label L] [--out PATH] [--db PATH]
#         WAL-safe binary copy of the live DB (SQLite backup API; never cp the .db
#         alone -- commits in -wal are missed, and a .db copied back over a live
#         -wal/-shm corrupts it: 2026-09-23). Default out: <real db>.snap-<label>-<ts>.
#   kit_life.sh snapshot-restore <snapshot> --confirm [--db PATH] [--allow-version-change]
#         Restores CONTENT in place through the backup API (live file, -wal/-shm and
#         symlink untouched). Gates: snapshot integrity ok, same user_version as live,
#         safety snapshot of live first (.snap-pre-restore-<ts>), integrity after.
#         2.20.0: body_stats_defaults=<콤마 키목록> 줄 추가. 신체 수치가 사라졌는지를
#         기계가 알아채는 자리다(빈 값 = 정상). 종료코드는 움직이지 않는다 --
#         한 번도 측정한 적 없는 새 인스턴스도 정당하게 이 상태다.
#   kit_life.sh inspect "<SELECT ...>" [--include-cancelled] [--include-retired] [--json]
#         THE way to read raw tables (dev/inspection). Live-only by default: TEMP VIEWs shadow
#         block/timeblock/sub_event (cancelled) and place/travel_rule/routine_def/carry_item/
#         life_why/ledger_goal (retired) with the kit's own live predicates; main.<table> is
#         the raw table. Read-only, no -wal/-shm side files, one SELECT/WITH/PRAGMA table_info.
#         TSV with header (or --json); stderr names the active shadows. Never open the DB by hand.
#   kit_life.sh source-set|source-list|intake-add|intake-list|intake-resolve|
#         match-rule-set|match-rule-list|import-csv|ledger-archive-reset|
#         intake-forget|intake-count|spend-block                        (059 / 2.34.0)
#         Kit intake layer. JSON on stdout, exit 0/2. Full table: INTAKE.md.
#         intake-add --hold 는 자동 제외·연결·확정 없이 항상 pending(held).
#         intake-resolve ... [--spend-id N] [--scope personal|business|unknown].
#         ledger-archive-reset --dry-run -> {"status":"dry_run","count":N} (읽기).
#         읽기(잠금 없음): source-list intake-list match-rule-list intake-count spend-block.
#   kit_life.sh method-add <name> <kind> [aliases] ...   출력 4번째 칸 = entity id (2.34.0:
#         같은 트랜잭션에서 entity + ko label 을 find-first 로 붙임; 못 붙이면 빈 칸 + stderr)
#   kit_life.sh method-list [--json]   --json: id name kind aliases[] billing_day
#         linked_account_id entity_id (읽기)
#   kit_life.sh method-alias-add <id|name> <alias> [<alias> ...]   별칭 병합(중복 건너뜀,
#         다른 수단이 쓰는 별칭은 거부). 출력 JSON {id,name,aliases,added}
#   kit_life.sh retro-sections --lane daily|weekly [--mode line|section] [--json]  (2.28.0)
#         Retro extension point: runs the module commands registered in
#         service/*/retro-sections.tsv. Kit row: module_core axis-lapsed (weekly only).
#         Section mode caps each provider at 3 lines. Fail-open. No DB access.
#         --mode attach (2.46.0-dev.10): only rows flagged `attach` in the lanes
#         column; prints photo<TAB>path<TAB>caption + one merged buttons<TAB><json>.
#         Contract: database/retro_sections.py.
#         2.38.0: --lane morning; --mode grid (weekly flow grid rows); a row's
#         optional 3rd column names its lanes (default daily,weekly);
#         --role domain drops the kit's own rows; --skip-module <key>.
#   kit_life.sh scope-self [--json]   (2.38.0) this instance's brief/retro scope:
#         role=main|domain agent= owns= others_own= skip_modules= task_scope=
#         leader_root=. Read only. task-find/-overdue/-done-between
#         --owning-agent now also take a comma list (task_scope).
#   kit_life.sh task-add <title> [due_date] [note] [--place <name>]
#         [--at HH:MM] [--dur <minutes> | --end HH:MM] [--project <id|title>]
#         태스크 등록. 출력: id<TAB>title<TAB>due<TAB>done|todo
#         --at 를 주면 그 날짜/시각의 TIMED 태스크(달력에 표시); 없으면 기존 all_day/untimed.
#         no --dur/--end: START ONLY is stored (zero-length; an unstated end is never
#         invented; the card reads "18:00 title"). --place attaches travel/prep.
#         --project 있으면 생성과 같은 트랜잭션으로 project_block 에 즉시 링크(2.24.0
#         project 축). 숫자 id 또는 정확한 title 모두 받는다; 못 찾으면 그 자리에서
#         거부(넌제로 exit) -- 조용한 스킵도, 유사 제목 추측도 없다. 생략하면 기존과
#         바이트 동일.
#   kit_life.sh task-find [date|all|키워드] [--owning-agent <name>]
#         태스크 목록 TSV(같은 4열). 기본 all, 보관 제외.
#         --owning-agent 생략 시 config/agent.conf 의 TASK_OWNING_AGENT 를 읽어
#         그 에이전트가 소유한 태스크만 반환(도메인 read-scope).
#         노브 미설정이면 기존과 동일하게 전 도메인 반환.
#   kit_life.sh task-done <id>                        완료 처리. 갱신 행 TSV 출력
#   kit_life.sh task-undone <id>                      미완료로 되돌리기. 갱신 행 TSV 출력
#   kit_life.sh task-reschedule <id> <YYYY-MM-DD> [--at HH:MM | --all-day] [--end HH:MM | --no-end]
#         --end HH:MM sets the end, --no-end clears it to start only (delete + recreate is
#         never the workaround). appt-upd <id> end_at="YYYY-MM-DD HH:MM" changes the end;
#         appt-upd <id> end_at= (empty / none) clears it.
#         예정일 변경. 갱신 행 TSV 출력. 날짜만 바뀌고 블록 종류는 그대로다 --
#         시각 있던 태스크는 새 날짜의 같은 시각으로, 종일 태스크는 종일로 남는다.
#         종류를 바꾸려면 명시적으로: --at HH:MM (시각 지정) / --all-day (종일로)
#   kit_life.sh task-postpone <id> <time text> [--now ISO]
#         Resolve a clock against now, then move the existing task row.
#   kit_life.sh task-undate <id>                      예정일을 떼고 날짜 없는 열린 행으로 되돌린다
#         (2.24.0). 회고의 `나중에` 가 앉는 자리 -- task-reschedule 은 날짜를 요구하므로
#         "언젠가 하지만 날짜는 아직" 을 표현할 수단이 없었다. 행은 열린 채 남고
#         task-find / task-untimed-open 에 계속 뜬다. 반복 일정 인스턴스는 거부(넌제로).
#         갱신 행 TSV 출력(같은 4열, due 는 빈 칸).
#   kit_life.sh task-retitle <id> <new_title>         제목 변경. 갱신 행 TSV 출력(같은 4열)
#   kit_life.sh task-archive <id>                     보관(soft-delete) -- find/overdue에서 숨김
#   kit_life.sh task-overdue [--before YYYY-MM-DD]     기한 지난 미완료 목록 TSV (지정일 이전, 기본 오늘; 보관 제외)
#   kit_life.sh task-untimed-open [--suggest] [--owning-agent <name>]  열린 untimed(floating) 조각 목록 TSV (id title updated_local).
#         --owning-agent: owner read-scope; omitted -> TASK_OWNING_AGENT (same rule as task-overdue).
#         start_at 없는 조각 -- 다른 태스크 쿼리(overdue/day-view)가 전부 놓치는 표면.
#         --suggest 붙이면 4번째 칸에 매칭 활성 프로젝트 제목. project_id 컬럼(마이그레이션 027)이
#         있으면 그 매칭을 timeblock.project_id에 무음 자동연결(silent auto-link)로 영속 저장한다
#         -- 사용자 확인 없음, 이미 연결된 조각은 덮어쓰지 않음(measure-first). 027 이전 DB에서는
#         컬럼이 없어 조용히 저장을 건너뛰고 읽기전용으로 동작(런타임 컬럼 감지, no-op).
#   kit_life.sh tasks-by-project [--open] [--since <YYYY-MM-DD>] [--project <id|title>]
#         프로젝트별 task 그룹 TSV (project_id title task_id task_title status link_source).
#         link_source: 'direct'(그 block 이 project_block 으로 직접 링크됨) 또는
#         'routine'(직접 링크는 없고, 그 block 이 나온 routine_def.project_id 로만
#         닿음 -- routine 이 새로 프로젝트에 걸리거나 은퇴해도 저장값을 안 고쳐도
#         되는 DERIVE 읽기, 물질화 시점 저장 아님). --open이면 열린 것만.
#         --since 없으면 전체 이력(라이브에서 1,730행)을 그대로 덤프해 아무도 안 썼다
#         -- --since <date> 로 그 날짜(현지) 이후 updated_at 만, --project 로 프로젝트
#         하나만(숫자 id 또는 title, task-add --project 와 같은 resolve). 링크가 아예
#         없는(direct도 routine도 아닌) task는 이 동사에 안 나온다 -- JOIN 표면이지
#         task 목록이 아니다. project_id 컬럼(027) 없으면 아무 것도 출력 안 함(clean no-op).
#   kit_life.sh project-link <event_id|event_ulid> <project_id|title>
#         이미 있는 이벤트에 프로젝트를 사후 연결(생성 때 --project 를 놓쳤거나, 회고에서
#         지난주 작업을 프로젝트로 잇는 경우). ux_project_block_1n(UNIQUE(block_id))이라
#         한 block 은 프로젝트 하나뿐 -- 이미 걸린 block 을 다시 링크하면 기존 링크를
#         갈아치운다(REPLACE, 병합 아님). 프로젝트 못 찾으면 거부. 출력: event_id event_ulid
#         project_id project_title
#   kit_life.sh project-unlink <event_id|event_ulid>
#         링크를 지운다. 링크가 없던 이벤트도 성공(idempotent) -- 3번째 칸(0|1)으로
#         실제로 지운 게 있었는지만 구분. 출력: event_id event_ulid had_link(0|1)
#   kit_life.sh task-done-between <from> <to>         기간 내 완료 태스크 TSV (to 미포함; 주간회고용)
#   kit_life.sh event-window <from_utc> <to_utc> [kind]  시간창 내 timed 이벤트 TSV (remind용, 읽기전용)
#         cols: ulid kind title start_at end_at status notify_policy notify_lead_min place(DGN-579) people(052) retro_line(2.24.0)
#         retro_line 은 맨 끝 칸. 빈 문자열 = 그 일정에 회고 한 줄을 받은 적이 없다 --
#         창 안의 "아직 기록 안 된 일정" 을 호출 한 번으로 센다(조인 불요).
#   kit_life.sh day-card [--date YYYY-MM-DD] [--tz <zone>] [--tz-offset <시>]
#         [--now <UTC ISO>] [--plain] [--exclude-prefix <p[,p]>]        (2.25.0)
#         THE 하루 표면. 읽기 전용 -- 아무것도 안 만들고 아무것도 안 찍는다.
#         이동 블록은 행으로 안 나온다. 두 행 사이 틈의 설명이 되고, 그 줄이
#         쓴 시간과 남은 시간을 같이 들고 나온다:
#             09:00-17:30 회사
#               ↓ 이동 30분 · 준비 15분 · 여유 15분
#             18:30-20:00 헬스
#         틈이 삼키는 다리는 전부 이름을 단다 -- 외출준비(집 준비, DGN-305) ·
#         이동 · 준비(도착 버퍼, LIFEKIT_APPT_BUFFER_MIN). 0분 토큰은 안 찍는다.
#         남은 시간: 여유 N분 / 딱 맞습니다(정확히 0) / N분 모자랍니다.
#         이동 시간을 모르면 0으로 안 바꾸고 안 추정한다 -- "이동 시간 미정 ·
#         비는 시간 N분"(일부러 '여유' 라는 단어를 안 쓴다). 할 일은 place-gap
#         레인이 그대로 든다.
#         분 계산은 전부 여기서(day_timeline) 난다. 모델에는 완성된 줄만 간다.
#         --plain 아침 브리핑 블록('- ' 불릿) / 기본 고정 대시보드 카드
#         (헤더 + 지난 ▪ / 지금 ▶ / 남은 ▫ 표식). 행이 없으면 빈 출력 + 종료 0.
#   kit_life.sh day-card --pin [--date YYYY-MM-DD] [--tz-offset <h>] [--now <UTC ISO>]
#         [--exclude-prefix <p[,p]>]                                     (2026-10-02)
#         The PINNED day card (routines/bundle/day-card.sh). Tasks carry
#         '[ ]'/'[v]' markers, today's tasks then the two oldest overdue
#         and 'N more', scoped to the agent's crew. The schedule includes
#         stays and appointments in time order. Pin order:
#         date -> week grid + appointment list -> today's tasks -> today's
#         schedule -> other registered today modules. One quote per module
#         block; unnumbered rows use bullets, checkbox/number markers stay.
#         Travel stays directly above its schedule row in that block.
#         The week grid counts appointments only, with [today] in the
#         weekday row. Headings are bold outside quotes. The text is bridge
#         sanitizer input. Empty sections are omitted; an empty card clears
#         the pin. Shared SDK contract: sot/PIN-LAYOUT.md. Read only.
#   kit_life.sh day-card --today [--date YYYY-MM-DD] [--now <UTC ISO>]   (2026-10-07)
#         THE answer to a today-list ask (today's tasks / list / schedule): the --pin
#         card without the week section, byte for byte. Relay it verbatim,
#         at most one line of your own on top; never compose a timeline
#         from rows. event-day <date> ends with this same card (2026-10-07).
#   kit_life.sh week-calendar [--cells v1,..,v7] [--marks m1,..,m7]
#         [--today MON..SUN|1..7|YYYY-MM-DD] [--lang <code>] [--rule]     (2026-10-02)
#         THE week-strip renderer (base implementation for every pack). No
#         database. Mon..Sun, exactly 7 comma-separated fields:
#           --cells  count (0 = none glyph, 1..9, >9 = over glyph) or a code
#                    (first Hangul syllable / first letter or digit)
#           --marks  today | done | empty -> third row (dropped if all empty)
#           --today  one 'today' mark (merged over --marks)
#           --rule   '-' lines above and below, exactly the row width
#         Layout + glyphs are copy (day.week.*): ko full-width cells joined by
#         two spaces, en half-width joined by one space. Output: the lines.
#   kit_life.sh event-card <id> [<id> ...] [--removed <id[,id]>] [--now <UTC ISO>]
#         [--tz <zone>] [--tz-offset <시>]                               (2.28.0)
#         쓰기 확인 카드. 읽기 전용. 항목마다 카드 하나(날짜 헤더 + '> ' 행 한 줄),
#         행은 day-card 가 그 항목에 찍는 줄과 바이트까지 같다. --removed 항목은
#         ' ← 취소됨' 이 붙는다. 일정 쓰기 동사(appt-add/upd/person/person-rm/
#         cancel/del, presence-add/move/cancel/revive, task-add/reschedule/
#         retitle)는 같은 블록을 출력 끝에 스스로 붙인다:
#             CARD_BEGIN <n> / 카드들(빈 줄로 구분) / CARD_END / CARD_RELAY <지시문>
#         첫 줄(기존 결과 줄)과 기계 신호 줄은 그대로 앞에 남는다.
#   kit_life.sh event-retro-set <event_id|event_ulid> <line>
#         Stores one retro line on the event (replacement state, not a log). Output: id<TAB>ulid<TAB>line.
#   kit_life.sh retro-items <YYYY-MM-DD> [--no-rating]                  (retro batch)
#         The daily retro walk as items (JSON). Nested events fold into ONE item:
#         outing bundle legs, contained_by children, meals attached to the bundle.
#         Per item: id (outer), title, span, carried + lead, question (flow recap),
#         children, rating (one module button row or null), card (rendered turn).
#         talk (visit_talk.py, owner 2026-10-16): a visit WITH PEOPLE gets ONE
#         small-talk line {angle person|event|venue, kind, line, holder?,
#         venue?} -- person (joined / long gap / baby / life fact / birthday)
#         > event (later round / move to a home in the retro line or note) >
#         restaurant (name + food kind, else a venue trait); null = nothing
#         worth a word, talked already, or no people. Unanswered -> question
#         (and card) IS talk.line; answered -> talk.line is the follow-up.
#   kit_life.sh retro-item-set <event_id|event_ulid> <line>
#         The answer on the outer + copied to every child without its own line.
#         Output JSON {id, line, copied, kept}. RETRO_IN_PROGRESS as event-retro-set.
#         An unanswered item whose question was its talk keeps that it was
#         asked (block_attr visit_talk.asked): never asked again.
#   kit_life.sh visit-talk-reply <item id|ulid> "<owner words>" [--dry-run] --json
#         Rule capture of the answer to the talk (no embeddings), clause by
#         clause: venue verdicts per holder (dining-session/verdict_words.tsv;
#         a named party person, a group word -> auto-group, else owner),
#         venue-trait words -> kit_life:venue:<trait>[:<value>]
#         (database/visit_talk_words.tsv), a clause naming one party person
#         -> that person's note (relationship.db person_facts source retro,
#         ref). Each keeps the owner's words (quote) + ref
#         kit_life:block:<ulid>:visit_talk#<n>; stored as block_attr
#         visit_talk on the visit (no table). Nothing read (a bare "eung") -> nothing
#         written. follow_up is always null: never ask again.
#         {"v":1,"visit","item","angle","verdicts":[{holder,verdict,said_by,
#          quote,ref}],"traits":[{target,quote,ref}],"notes":[{holder,quote,
#          ref,fact_id}],"written","follow_up":null,"target"?}
#   kit_life.sh task-open-today [--owning-agent NAME] [--photo [--offer ID[,ID]]]
#         Today's open task/routine instances + registered aliases (JSON), for the
#         semantic evidence match. Captures append OPEN_TODAY with these rows.
#         --photo arms the proof gate: task-done-today accepts --record-evidence
#         VERB:ID (same-turn dated capture), --photo-evidence OBSERVED-EVIDENCE,
#         --tap TAPPED-TEXT or --said OWNER-WORDS; proof flags are exclusive.
#         Active windows return ACTIVITY_IN_PROGRESS: answer the photo.
#         A clearly finished end-state needs --photo-evidence plus --photo-end-state.
#         ALREADY_DONE has an empty reply: answer the activity, no completion line.
#         --offer posts confirm buttons only outside an active activity.
#   kit_life.sh retro-start "<action or full bridge reply context>"
#         read-only JSON: READY {retro_date, tomorrow, commands,
#         tomorrow_commands, next}; ASK_DATE never defaults to the clock.
#         Dated daily callback = opt:retro:daily:YYYY-MM-DD.
#   kit_life.sh start-tap "<tapped text>" [--message-id N]
#         A tap on a task/routine start card (start_card.py: snooze / help me
#         start / done). The tapped bubble (inbound-context message_id) picks
#         the occurrence; JSON {status DONE|ALREADY_DONE|CHOOSING|SNOOZED|HELP|
#         SPENT|NO_CARD|RETRY, next}. The card is edited in place.
#   kit_life.sh event-notify <event id|ulid> <default|silent|start_only|N|''>
#         DGN-273 per-event notify override (N = custom lead minutes; '' = reset to default).
#   routine add/update accept notify=default|silent|start_only|N (N = custom lead minutes);
#         task-add / appt-add accept --notify <same values> for one-off events.
#   kit_life.sh appt-add <title> <start_at> [end_at location purpose summary] [--new]
#         [--kind appointment|task|auto] (caller judgment; schedule-add preferred)
#         [--attendance-mode online|in_person|unknown] [--meeting-url <URL>]
#         Caller supplies attendance judgment; only physical places derive travel.
#         [--notify <policy>] [--project <id|title>] [--in <event id|title>]
#         약속 등록(기존 시그니처, 문서 처음 등재). --project 는 task-add 와 같은 모양 --
#         생성과 같은 트랜잭션으로 즉시 project_block 링크, id/title 모두 받고 못 찾으면
#         거부, 생략하면 기존과 바이트 동일.
#         --in (2026-09-30, outing = a layer of events): the row is a leg of that
#         event (bundle). Linked in the insert's own txn; travel resolves after
#         the link. Consecutive legs of one bundle get no travel/prep/buffer/
#         return home (card: day.gap.together). Timed only. presence-add too.
#   kit_life.sh routine-retro-skip <routine_id|routine_ulid|recurrence_id> <on|off>
#         Controls only retro questioning; schedule visibility/materialization is unchanged.
#         Output: id<TAB>ulid<TAB>on|off.
#   kit_life.sh routine-for-event <event_id|event_ulid>
#         Resolves an occurrence to its routine definition. Output matches `routine list`:
#         id, ulid, title, cadence, schedule[:time], status, retro_skip(2.24.0).
#         retro_skip 이 1이면 그 루틴은 회고의 질문 축에 아예 오르지 않는다(일정에는
#         그대로 보인다) -- "이 루틴 회고에서 묻지 마"를 호출 한 번으로 확인한다.
#   053 -- carry_item (자원축): 들고 다니는 물건의 이름/컨테이너/보유3상태 + 루틴
#         기본 준비물 + 일정별 준비물(sub_event kind='prep_item' 재사용, done=챙김).
#   kit_life.sh carry-add <name> [kind=item|container] [possession=has|has_not|unknown] [source=<token>]
#         물건 등록. 출력(7칸): id ulid name kind possession parent_ulid status
#   kit_life.sh carry-list [has|has_not|unknown] [--all]
#         물건 목록 TSV(같은 7칸). 기본 status=active만; --all이면 retired도.
#   kit_life.sh carry-upd <id|ulid> field=value...
#         field: name kind(=item_kind) possession acquired_on released_on possession_as_of status.
#         possession을 has 밖으로 바꾸면 parent_id도 같이 NULL로 풀린다(안 갖고 있는 건 못 담겨 있음).
#         parent_id 자체는 이 동사로 못 바꾼다 -- carry-pack 전용(순환 가드가 필요해서).
#   kit_life.sh carry-pack <item-id|ulid> <container-id|ulid|none>
#         컨테이너 지정/해제. 자기참조·순환(그 컨테이너가 이미 이 물건 아래)은 거부.
#         담기려면 item.possession='has' 여야 한다. 출력은 carry-add와 같은 7칸.
#   kit_life.sh routine-prep-add <routine_id|routine_ulid|recurrence_id> <item_id|item_ulid>
#         루틴 기본 준비물 목록에 추가(멱등). 출력: routine_ulid item_ulid item_name
#   kit_life.sh routine-prep-rm <routine_id|routine_ulid|recurrence_id> <item_id|item_ulid>
#         루틴 기본 준비물 목록에서 제거. 출력은 add와 같은 3칸.
#   kit_life.sh routine-prep-list <routine_id|routine_ulid|recurrence_id>
#         루틴 기본 준비물 목록 TSV: item_ulid item_name
#   kit_life.sh prep-add <block_id|block_ulid> <item_id|item_ulid>
#         일정 하나에 준비물 하나 첨부(sub_event kind='prep_item'). 이미 붙어 있으면 그대로.
#         출력: sub_event_id sub_event_ulid block_ulid item_ulid done
#   kit_life.sh prep-rm <block_id|block_ulid> <item_id|item_ulid>
#         첨부 해제(tombstone). 출력은 prep-add와 같은 5칸.
#   kit_life.sh prep-list <block_id|block_ulid>
#         가장 얕은 충족 단위로 묶어 보여준다 -- 가방 전체가 챙겨졌으면 가방 한 줄,
#         빼낸 항목(parent_id NULL)만 개별로 뜬다. 출력: display_item_ulid display_name done required_count
#   kit_life.sh prep-set <block_id|block_ulid> <display-item-id|ulid> <0|1>
#         표시된 줄 하나를 체크/해제한다. 그 줄이 컨테이너를 대표하는 중이면 그 안의
#         모든 필요 항목의 done이 한 번에 같이 바뀐다(체크박스는 하나, 상태는 sub_event마다).
#         출력은 prep-list와 같은 4칸.
#   kit_life.sh appt-find <date_from> [date_to]         appointment+presence(052) TSV, that LOCAL date window
#         cols: id start_local title location end_local(052) people(052, "Name1, Name2" or empty) retro_line(2.24.0)
#         retro_line 은 맨 끝 칸, 빈 문자열이면 아직 회고 한 줄을 안 받은 일정이다.
#   kit_life.sh project-list [--status <S>] [--json]     프로젝트 목록 TSV(id title status start end). --json이면 JSON 배열.
#   kit_life.sh travel-sweep [<YYYY-MM-DD> [<YYYY-MM-DD>]]
#         장소를 가진 앞으로의 약속에 이동/준비 블록을 파생한다. 인자 없으면
#         (2026-09-30) Also future hand-entered timed stays (presence) away from home.
#         지금부터 4주(LIFEKIT_TRAVEL_SWEEP_DAYS), 날짜 하나면 그 날, 둘이면
#         그 구간(끝날 포함). 과거는 절대 건드리지 않는다.
#         멱등 -- 이미 rule 이 붙은 약속은 exists 로 넘어가고 아무것도 안 쓴다.
#         travel-rule-retire 로 은퇴시킨 약속은 retired 로 넘어가고 손대지 않는다.
#         은퇴는 the owner's 거부 의사다 -- 자동 경로가 되돌리는 일은 없다.
#         출력: travel-sweep<TAB>id<TAB>결과<TAB>location<TAB>origin<TAB>dest
#         등록 안 된 장소/소요분은 지어내지 않고 TRAVEL_PLACE_NEEDED 신호로 뜬다
#         (unknown_place|near_place|link_place|no_duration|edge_missing). 답을 받으면
#         travel-place-add / travel-edge-add 로 등록하고 다시 돌리면 파생된다.
#         신호의 location 칸은 저장된 장소 이름과 같은 문자열이다 -- 그대로
#         travel-edge-add 인자로 넣으면 같은 장소 행에 붙는다.
#         link_place = 장소 칸에 지도 링크만 있다. 링크는 이름이 아니라서
#           장소 행을 만들 수 없다 -- place-manage 의 map_search.py --link 로
#           이름을 읽고 appt-upd <id> location=<이름> 으로 고쳐 넣는다.
#         virtual_place(meet/zoom 링크)는 갈 곳이 없으므로 신호로 뜨지 않는다.
#   kit_life.sh place-gap [--all] [<YYYY-MM-DD> [<YYYY-MM-DD>]]
#         읽기 전용. 앞으로의 약속 중 ** 이동 시간이 안 잡힌 것 ** 을 뽑는다.
#         travel-sweep 은 쓰는 동사라 브리핑/크론에서 못 돌린다. 이건 아무것도
#         안 쓴다 -- 장소를 만들지도, place_id 를 찍지도, 큐에 넣지도 않는다.
#         출력: place-gap<TAB>id<TAB>이유<TAB>현지시각<TAB>제목<TAB>장소<TAB>할 일
#               place-gap-summary<TAB>scanned=N gaps=N since=... until=...
#         이유: link_only|no_place|no_duration (=결손) / virtual|retired|ok
#         기본은 결손만. --all 이면 아닌 것도 이유를 달고 같이 나온다.
#         은퇴시킨 약속은 결손이 아니다 -- the owner 결정이지 고칠 것이 아니다.
#   kit_life.sh travel-link-gap [--all]
#         Read only. Registered place pairs (place_link, both places active)
#         with no usable travel_min: absent (never measured) | expired
#         (only expired rows). Minutes are printed only for ok rows (--all);
#         a gap line never carries a number. Repair = travel-edge-add with
#         a value the owner states.
#         out: travel-link-gap<TAB>link_id<TAB>reason<TAB>from_id<TAB>from<TAB>to_id<TAB>to<TAB>minutes<TAB>expired_at
#              travel-link-gap-summary<TAB>links=N absent=N expired=N ok=N
#   kit_life.sh place-dupes [--strong] [--tolerance N] [<place>]
#         읽기 전용. 한 장소인데 행이 둘로 갈린 ** 후보 ** 를 뽑는다. 합치지
#         않는다 -- 어느 쪽이 남는지는 the owner 대답이고, 이 동사에는 그 칸이
#         아예 없다.
#         출력: place-dupe<TAB>신호<TAB>id_a<TAB>이름_a<TAB>id_b<TAB>이름_b<TAB>근거
#               place-dupe-summary<TAB>places=N candidates=M strong=K
#         신호: name_contains (한 이름이 다른 이름 안에 단어 경계로 들어있다)
#               shared_address (두 행이 같은 주소 문자열을 들고 있다)
#               name_is_address (한 행의 이름이 다른 행의 주소다)
#               twin_edges (같은 출발지에서 분수가 같다 -- 가장 약한 신호,
#                 --strong 이면 빠진다. 30분 거리 헬스장은 둘일 수 있다)
#         id 두 칸은 행 순서다. 순위도 방향도 아니다.
#         travel-place-add / travel-place-upd 도 같은 판정을 등록 직후 한 번
#         DUPE-CANDIDATE 줄로 얹는다 (structural 신호만, 종료코드 불변,
#         첫 줄은 원래 그대로).
#         합치는 것은 이 CLI 가 아니라 database/place_merge.py 다:
#           --propose --pair A B --keep <남는 쪽> --said "<the owner 말>"
#           --dry-run  (보여드리고)   --apply
#         --keep/--said 없으면 거부한다. 기본값이 없는 게 기전이다.
#   kit_life.sh appt-origin <id> <place|home> [address=<rough address>]
#         2.37.0: the answer to ORIGIN_ASK (appt-add / appt-upd print it when
#         where the owner leaves from cannot be known): stores the
#         appointment's origin, mints/teaches the place, re-derives travel.
#   kit_life.sh appt-travel-attach <appt_id> [--force]
#         약속 한 건에만 같은 파생을 돌린다. --force 는 은퇴시킨 규칙을 the owner
#         직접 다시 붙이는 경우에만 쓴다.
#   kit_life.sh project-add --title <T> [--status <S>] [--start <d>] [--end <d>] [--note <N>]
#         새 프로젝트 등록. notion_id=NULL, 기본 status='진행 중'. 출력: id title status start end
#   kit_life.sh project-upd --id <id> [--title T] [--status S] [--start d] [--end d] [--note N]
#         프로젝트 부분 수정. 없는 id이면 오류. 출력: id title status start end
#         --note-add LINE appends one decided fact to the note; any project-upd counts as
#         movement of the plan record (config project_touch_<id>).
#   --- in-flight plan record (2026-10-12): one project per plan, no new table ---
#   kit_life.sh plan-recall [<text>] [--person <name>] [--json]
#         READ ONLY. Run it FIRST on a "do you remember / how did it go / what we were doing"
#         question, before any memory search. Open plan projects with note (decided facts),
#         persons (block-person on the steps) and steps (open|done; undated = waiting).
#         PLAN id title status idle=N / PLAN_NOTE / PLAN_PERSON / PLAN_STEP id task open|done date|waiting title /
#         PLAN_RELAY. No match -> PLAN_NONE + PLAN_RELAY (never guess; ask the owner), exit 0.
#   kit_life.sh plan-wait-resolve [ID] --said "<owner words>" | --reconcile
#         Close an explicit wait from an arrival/completion fact or linked pickup/receipt.
#   kit_life.sh plan-waiting [--min-idle N]   READ ONLY. One line for a waiting step with no
#         movement for 3+ days, nothing otherwise (the retro-sections row module_core
#         plan-waiting daily,morning puts the same line in the daily retro / morning brief).
#   Capture: plan with an open next step -> project-add --title T --note "decided lines" ; each open step
#         task-add "<step>" [date] --project <id> (undated = waiting) ; block-person <task> <person id> ;
#         later project-upd --id N --note-add "line" / task-done / task-add ; finished -> project-upd --status done.
#
# DGN-231 reconcile-before-write: meal-add / workout-add / person-add / appt-add /
#   travel-place-add 는 등록 전 중복을 자체 대조한다. 매치가 있으면 등록하지 않고
#   첫 줄 "EXISTS n" + 매치 행들(해당 find 포맷)을 출력한 뒤 종료코드 3으로 끝난다.
#   뒤에 --new 를 붙이면 매치를 무시하고 강제로 신규 등록한다. 매치 0건이면 기존과
#   동일하게 등록.
#   매치 키: meal-add=(date, 끼니) / workout-add=(date, 대분류) /
#   person-add=(name 또는 alias 정확일치) / appt-add=(같은 로컬 날짜) /
#   travel-place-add=place-resolve 와 같은 이름-또는-별명 정확일치(2.20.0).
#
# NEAR-MATCH (2026-09-28, near_match.py): 이름/별칭으로 새 행을 만드는 모든 문
#   (travel-place-add, task/routine/presence 의 place, person-add, method-add,
#   exercise-add, meal-log cache-add, 약속 장소 자동 등록)은 정확일치가
#   없을 때 한 번 더 근사 대조한다(띄어쓰기/기호 무시, 어순 바뀜 학동유디치과 ==
#   유디학동치과, 의원/점/역 같은 꼬리말, 한 글자 오타, 포함). 걸리면 만들지 않고
#     NEAR_MATCH<TAB>n<TAB>domain<TAB>입력
#     <ref><TAB><기존 이름><TAB><신호><TAB><걸린 문자열>   (n 줄)
#     ASK<TAB><질문 한 줄>                               ("혹시 X랑 같은 곳인가요?")
#   을 찍고 종료코드 5. the owner 답:
#     맞다   -> kit_life.sh near-match-accept <domain> <ref> "<입력>"  (별칭으로 붙임)
#     아니다 -> 만드는 동사에 --new-confirmed (cache-add 는 "new_confirmed": true)
#               또는 kit_life.sh near-match-refuse <domain> <ref> "<입력>" [말씀]
#   아니다는 기억된다(near_match_refusal) -- 같은 쌍은 다시 안 묻는다.
#   --new 는 정확일치 게이트만 넘는다(근사 게이트는 --new-confirmed 만).
#   place-resolve / person-resolve 는 ABSENT 대신 같은 NEAR_MATCH 묶음을
#   종료코드 0 으로 찍는다(판정 동사라서). 읽기 전용 확인:
#   kit_life.sh near-match-check <place|person|method|food|exercise> "<입력>"
#   약속 장소 자동 등록에서는 TRAVEL_PLACE_NEEDED 의 near_place 로 뜬다
#   (location 칸 = 입력, dest 칸 = 기존 장소 이름 = ref 로 그대로 넣는다).
#
#   ── 세트 축 (061..). training_axis.py. 기계 토큰만 찍는다 ──────────────────
#   workout_sets.slot_num NULL = 세션 레벨 행(프로그램 슬롯 없음). reps NULL =
#   prep/ramp 완료 탭(세지 않음, target_reps 가 처방). drill_code = 프렙 드릴.
#   kit_life.sh drill-load-last <drill_code> [--exercise NAME] [--before-session ID]
#         그 드릴에서 마지막으로 '쓴' 무게(관측). DRILL_LOAD drill=.. kg=.. date=..
#         session=.. / 관측 없으면 DRILL_LOAD drill=.. none (한 번 묻고 기록한다).
#   workout_sets.machine_ref (062) = 그 세트를 한 기계. 기계는 대화에서 잡는다
#   (시드 없음). 핀 격자는 그 기계에 기록된 무게에서 배운다.
#   kit_life.sh machine-register --label <말한 이름> [--gym <place_id>]
#         [--cable-lines single|dual] [--loading-side single|per_side|stack]
#         MACHINE NEW <ulid> / MACHINE SAME <ulid> (같은 짐·같은 이름, 띄어쓰기 무시)
#   kit_life.sh machine-list [--gym ID] [--for-exercise NAME] [--all]
#         버튼 후보. --for-exercise = 같은 load_type 을 쓴 기계 + 아직 안 쓴 기계.
#   kit_life.sh machine-last <exercise> [--gym ID]
#         기억한 선택: MACHINE_LAST .. machine=<ulid> basis=logged|bound / none
#   kit_life.sh machine-grid <ulid>      PIN kg=.. source=stated|observed [n=..]
#   kit_life.sh machine-snap <ulid> <kg> [--dir near|down|up]
#         SNAP .. kg=.. basis=stated|lattice|observed|none in_range=0|1
#         (in_range=0 = 관측 밖이라 그 핀은 모른다 -- 핀이라고 말하지 않는다)
#   kit_life.sh machine-upd <ulid> [--label] [--gym] [--plates "1.25,2.5,.."]
#         [--lattice BASE STEP] [--cable-lines] [--loading-side] [--retire]
#         [--status active|retired|archived] (archived = retired in the DB).
#         --plates "" clears stated plates/lattice; observed loads remain.
#   kit_life.sh machine-cleanup   read-only archived-link/duplicate candidates
#   kit_life.sh gym-link-delete <gym> <exercise-code> [--confirm]
#   kit_life.sh machine-merge <duplicate> <keeper> [--confirm]
#   kit_life.sh exercise-machine-unlink <exercise-code> <machine> [--confirm]
#         Default: dry proposal. Only --confirm applies. Machine/cable unlink
#         is refused by the inherited CHECK (see gym-link report).
#         오너가 말한 격자 = provisional 0.
#   근육 어휘 (063): 맵 3개는 muscle_node 에 묶여 있다(트리거). 코드 사전 대신:
#   kit_life.sh muscle-focus <부위> [...]   부위 = 노드 키 또는 라벨(등/팔/back)
#         FOCUS area=.. node=.. muscles=a,b,.. basis=group|muscle|subregion
#         / FOCUS area=.. unknown (추측 안 한다)
#   kit_life.sh muscle-vocab [--kind group|muscle|subregion]   NODE .. kind=.. parent=..
#
#   -- food preference reads (DGN-2120 design v2 LOCKED section 4; food_pref.py) --
#   Shared: --json REQUIRED (else exit 2); read-only, no outbound call; kit or
#   module_dining off -> {"v":1,"available":false} exit 0; exit 3 not-found
#   {"v":1,"available":true,"error":"not-found","why","candidates":[{target,
#   path}] (<=5)}; exit 2 usage; exit 1 internal. Holder ids: owner,
#   person:kit_life:<id>.
#   kit_life.sh food-kind <dish-id | venue-id | "words"> [--domain food]
#         [--facts FILE | --html FILE] --json
#         {"v":1,"available":true,"query","kind":"dish|venue|words",
#          "target":"kit_life:food:<node key>",
#          "chains":[[{"key","label","level","depth","primary"}]],
#          "contains":[{"token","certainty":"primary|possible"}]}
#         dish-id  = kind_tree node key (mala / kit_life:food:mala) or a meal
#                    record block (block:<id> / ulid)
#         venue-id = naver place id (reads ONLY --facts <venue_facts.py JSON>
#                    or --html <saved page>: the live fetch is dining-session's
#                    own facts step), place:<kit id>, or a visit block -> its place
#         chains root -> leaf; primary = the hop below is primary (degree=very
#         needs all); contains from the frozen seed body_food_token.seed.tsv
#         via dining-session/food_contains.tsv. Words that resolve to nothing
#         -> exit 3 with candidates (+ "new":"node-add": the model picks one or
#         says new -> node-add below).
#         A kit place carrying a naver id (dine-choose wrote it) is keyed
#         naver_<id>; a visit dine-choose decided reads the kinds + category
#         it stored (block_attr dine_venue), not the place name.
#   kit_life.sh node-add --parent <node | kit_life:food:<node>> --label "<words>"
#         [--key <ascii>] [--domain food] --json
#         {"v":1,"available":true,"target":"kit_life:food:<key>",
#          "created":true|false,"path":[{"key","label"}]}
#         the one write here: config row food_node.<key> (no table); a label
#         already on a node -> that node, created false; parent unknown -> 3
#         with candidates; key default n_<sha256(label)[:10]>; cuisine = the
#         parent's. food-kind / dining-history read it from the next call.
#   kit_life.sh dining-history --members <owner|person:kit_life:<id>|name>[,...]
#         [--subset] [--since ISO] [--limit 50] --json
#         {"v":1,"available":true,"members":[sorted canonical ids],
#          "visits":[{"visit":"dine_visit:<block ulid>","at","venue","target",
#          "party":[...],"match":"exact|subset","verdicts":[{"holder",
#          "verdict":"liked|neutral|disliked","said_by":"owner","ref"}],
#          "refs":[source refs read, mapped or not]}],"truncated":false}
#         members canonicalised by the kit merge fold (person-del --into keeps
#         the old name as the survivor's alias); the owner is in every visit's
#         party (the kit calendar is the owner's). default = party EXACTLY the
#         members; --subset adds parties that are a proper subset of the members
#         with >= 2 people (Q4a). verdicts: block.retro_line + the bundle's
#         event.reaction read clause by clause with
#         dining-session/verdict_words.tsv (rule-based; no rule -> no verdict,
#         the ref stays in "refs"). target = kit_life:food:naver_<id> when the
#         place carries a naver id, else place_<id>, when the visit's stored
#         kinds / the place name name a food kind; else null.
#         Verdicts captured by visit-talk-reply come back as stored, with
#         "quote" (ref kit_life:block:<ulid>:visit_talk#<n>); a retro line
#         that IS a captured reply is not read again.
#   kit_life.sh venue-traits <naver place id | place:<id> | block:<id>>
#         [--facts FILE] --json   (sibling read, DGN-2120 v2 addendum 05:55)
#         {"v":1,"available":true,"query","food":[food-kind chains],
#          "venue":["kit_life:venue:<trait>[:<value>]",...],"sources":[
#          {"target","from":"observation","quote","ref","at"} |
#          {"target","from":"facts","src"}]}
#         traits: mood quiet|lively, seating room|table|bar, drinks_friendly,
#         price low|mid|high, wait short|long, portion small|large, service
#         attentive|slow. Owner words (visit-talk-reply, every visit at the
#         place) beat the page; the later observation wins per trait. --facts
#         adds the page's price band (per_person won <12000 low, <25000 mid,
#         else high) and drinks_friendly (category word). Same shared rules.
#
#   -- dining decide (dining_decide.py) --
#   kit_life.sh dine-choose <appt id | ulid> --facts <venue_facts.py JSON> [--new-confirmed]
#         the picked restaurant becomes the appointment's place: find-first
#         kit place (naver id, exact name, near gate exit 5), teaches the
#         address / naver link (map_url) / position (place_coord), keeps a
#         differing meeting point as one note line (dining.meet_note), then
#         appt-upd location=<venue> (travel re-derives), stores block_attr
#         dine_venue. Prints appt-upd's lines + card, then
#         DINE_CHOSEN<TAB>id<TAB>place<TAB>name<TAB>naver_<id><TAB>minted|existing
#         [MEET_KEPT<TAB>id<TAB>note<TAB><meeting point>]. Exit 3 VENUE_CONFLICT
#         (the name is a place with another naver id / home).
#
# 종료코드: kit_life.py 종료코드를 그대로 전달.
#   kit_life.sh log-body-sheet <YYYY-MM-DD> <note> <key=value> [...]
#         Atomically record every extracted InBody field. Prints the stored
#         count and names; unknown keys or any write failure exit nonzero.
#         Example: database/kit_life.sh log-body-sheet 2026-10-14 'InBody 07:10' \
#           weight_kg=83.1 body_fat_pct=23.1 skeletal_muscle_kg=36.6 fat_mass_kg=19.2
#         Always inspect the complete output and exit status of a write.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# kit_life.py 는 표준 stdlib만 쓰므로 어떤 python3 로도 동작한다.
# 우선순위: 1) LIFEKIT_PYTHON 환경변수, 2) PATH 의 python3, 3) /usr/bin/python3.
PY="${LIFEKIT_PYTHON:-}"
if [[ -z "$PY" ]]; then
  PY="$(command -v python3 || true)"
fi
[[ -x "$PY" ]] || PY="/usr/bin/python3"

# vocab (manifest vocab_verb, DGN-1694) skips kit_life.py: it is read-only and
# stdlib-only, and the reader's 5 s timeout must not pay for compiling 44k lines.
if [[ "${1:-}" == "vocab" && -f "$SCRIPT_DIR/vocab.py" ]]; then
  exec "$PY" "$SCRIPT_DIR/vocab.py" "${@:2}"
fi
exec "$PY" "$SCRIPT_DIR/kit_life.py" "$@"
