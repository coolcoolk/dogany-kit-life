---
name: place-view
display_name: 길찾기
description: >
  Finds real-time route + travel time between two places via Naver Map (네이버 지도).
  DEFAULT MODE = 도보 + 대중교통 (walk + transit) per the user's preference; car only when asked.
  Fires on route/travel-time questions: "서울역까지 얼마나 걸려", "집에서 강남역 어떻게 가",
  "여기서 X까지 몇 분", "X 가는 길", "X까지 경로 뽑아줘", "대중교통으로", "버스로 가면", "지하철로",
  "걸어서 얼마나", "차로 가면"/"택시로 몇 분", "지금 출발하면 언제 도착", or asking how to get to /
  how long to an appointment place. Origin defaults to the user's home (from config) when unstated.
  Output = concise route summary (times, transfers, fare) in the user's terms, not raw tables.
---

# place-view -- Naver map route puller

Get live route + time between two places. Walk + transit by default (user preference).
Car only if the user asks car/taxi/driving.

## when
- the user asks travel time or "how to get there": "X까지 얼마나 걸려", "여기서 X 어떻게 가",
  "X 가는 길 / 경로", "몇 분 걸려", "지금 나가면 언제 도착".
- mode words override default:
  - none / "대중교통" / "버스" / "지하철" / "걸어서" -> transit + walk (default).
  - "차로" / "택시" / "운전" / "몇 분 (drive)" -> car.
- origin unstated -> the user's home (from config). destination always needed.

## do
1. resolve origin + destination text.
   - origin missing -> pass "" (script fills HOME).
   - if destination is an appointment/place already known (map link, address), use that text.
2. run: `route.sh "<origin>" "<destination>" [modes]`
   - modes: default omit (= "transit walk"). car -> `"car"`. mixed ok: `"transit walk car"`.
   - script drives Naver map (agent-browser), prints raw route lines per mode.
3. read lines, summarize for the user (see output). do NOT dump all 8 lines raw.

## script
`route.sh "<origin>" "<destination>" [modes]`
- origin ""  -> HOME from config: ROUTE_HOME_ADDR env first, else ROUTE_HOME_ADDR= in
  config/agent.conf. the user moves -> update config/agent.conf, NOT the script.
- modes: space/comma list of {transit, walk, car}. default "transit walk".
- exit 2 = no destination, or empty origin with no home address configured.
  exit 3 = origin/dest not found in Naver autocomplete
  (retry with a clearer place name, e.g. add 동/구 or full address).
  exit 4 = agent-browser not on PATH (preflight; cannot drive the map).

output blocks:
- `=== 대중교통 ===` route lines: "최적 24분 ... 도착 1,500원 도보 2분 간선버스 8분 ..."
- `=== 도보 ===` : "추천 33 분 2.0km 3,067 걸음 ..."
- `=== 자동차 ===` : "1 실시간 추천 12 분 2.5km 택시비6,200원 ... 서행/원활/정체 ..."

## output (to the user)
- lead with the fastest / recommended option, then 1-2 alternatives. crisp bullets.
- transit: total time + arrival, key legs (walk N분 -> 버스/지하철 line -> walk N분), fare.
- walk: total time + distance.
- car: time + distance + 택시비 + live traffic (서행/정체/원활).
- flag caveat if the user has an appointment time: suggest a leave-by time with buffer.
- this is LIVE (reflects current traffic/time). state that when useful.
- follow output rules: no asterisk-bold, no step narration, Korean, 공손.

## notes
- Naver page = source of truth for KR transit/live traffic. do not hand-estimate
  when this skill can pull real numbers.
- one run opens ONE browser and closes it. no leftover session.
- best-effort trigger tier (helpful info, no data-integrity guarantee) -> no hook needed.
