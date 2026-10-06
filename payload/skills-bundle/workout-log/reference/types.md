# workout-log -- type dictionary + kcal estimate

Read when mapping an utterance to (category, subtype), or when kcal is not given.

## workout_types (category / subtype)

- 근력: 가슴, 등, 어깨, 하체, 삼두, 이두, 복근
- 유산소: 러닝, 사이클, 스텝퍼, 걷기, 로잉, 수영, 일립티컬
- 유연·회복: 요가, 스트레칭, 폼롤러
- 스포츠·액티비티: 등산, 클라이밍, 축구, 농구, 테니스, 골프
- 펑셔널·HIIT: 크로스핏, F45, 서킷, HIIT, 부트캠프, 컨디셔닝

Mapping:
- "헬스 / 가슴운동" -> 근력/가슴, "등운동" -> 근력/등, "하체/스쿼트" -> 근력/하체,
  "어깨" -> 근력/어깨, "이두" -> 근력/이두, "삼두" -> 근력/삼두,
  "복근/코어/플랭크" -> 근력/복근.
- "러닝 / 5km 뛰었어" -> 유산소/러닝 (distance in note), "자전거" -> 유산소/사이클,
  "천국의 계단/스텝퍼" -> 유산소/스텝퍼, "걷기/산책" -> 유산소/걷기.
- "요가" -> 유연·회복/요가, "스트레칭" -> 유연·회복/스트레칭.
- "등산" -> 스포츠·액티비티/등산, "클라이밍/볼더링" -> 스포츠·액티비티/클라이밍.
- "크로스핏/F45/HIIT/서킷/부트캠프" -> 펑셔널·HIIT/<same word>.
- "PT 받았어", body part unknown -> ask once.
- "전신/풀바디" -> no 전신 subtype; record the parts done (multi-classify).
  ambiguous -> ask once.
- not in dictionary -> nearest subtype. unknown value is auto-registered by
  `workout-add` (stderr warning); prefer dictionary words.

## kcal when not given

- watch value known -> 활동 (active), never 총 (total).
- unknown -> estimate from kind x minutes: ~6 kcal/min strength, ~10
  kcal/min running, ~4 kcal/min yoga. reply marks "(추정)".
