# place-manage -- duplicates: offer + merge

Read when the user says two rows are one place, when a `DUPE-CANDIDATE`
line appears, or when asked to tidy the place list.

Why it matters: travel knowledge hangs off place rows, so every duplicate
re-bills the same minutes per direction (measured 2026-09-12: 5 of 20 rows
were duplicates of 3 places).

## what you never decide

Which two rows are one place = HIS answer. which row SURVIVES = his answer.
you may notice and ask, never conclude. ask both in one breath:

  "<A>랑 <B>를 하나로 합칠까요? 이름은 <A>로 남기고 <B>는 그 안의 방으로
   들어갑니다. 집에서 45분은 <A> 쪽에 그대로 남아요."

his sentence already said which is which ("본점이랑 D룸 같은 건물이야") ->
do not ask back; say what you will do in one line, do it.

## the tool does the write (never SQL, never DELETE)

`place_merge.py` repoints every reference (travel rules, appointments, gym
machines), folds edges + carries measures (never two answers for one
metric), teaches folded name + aliases to survivor, moves addresses over as
NON-primary, retires source `status='gone'`, and refuses "done" if any live
future travel block moved. `--db` defaults to the kit DB.

1. record his answer:
   ```
   python3 database/place_merge.py --plan runtime/place-merge-<날짜>.json \
       --propose --pair "<A>" "<B>" --keep "<the one he said stays>" \
       --said "<what he actually said>" [--sub-place "<방 이름>"]
   ```
   `--keep` + `--said` are HIS; tool refuses without them or when `--keep`
   is not in the pair. `--sub-place` = what the folded row WAS ("D룸",
   "12층"); makes it a room of the survivor and blocks the string as a place
   forever. omit when the folded row was not a room. one pair per call;
   repeats append to the same plan (never silently replace).
2. show him: `python3 database/place_merge.py --plan <same> --dry-run`
   (writes nothing). read `merge` / `edge` / `retire` back as one line.
3. do it: `python3 database/place_merge.py --plan <same> --apply`
   - exit 0 + `block census unchanged` = done.
   - exit 2 = refused, nothing written. read it; never retry with another flag.
   - exit 3 = applied but a travel block MOVED. say so; not a success.

Then one line: 합쳐졌고, 이름은 <survivor>, <방>은 그 안의 방으로 들어갔고,
이동시간은 그대로.

## the offer (he should not have to notice first)

Neither surface merges anything.

- `travel-place-add` / `travel-place-upd` print, only when relevant, after
  their normal output:
  ```
  DUPE-CANDIDATE 1
  place-dupe	name_contains	19	한빛합주실 본점	21	한빛합주실 본점 D룸	name-contains=한빛합주실 본점
  ```
  mention in ONE line, ONE question, drop it if he does not bite. rides the
  current turn; never its own topic.
- `place-dupes [--strong] [--tolerance N] [<place>]` (read only): when he
  asks to tidy ("장소 정리 좀 해줘", "중복 있나 봐줘"), and once per place
  conversation if the list was never swept.
  ```
  place-dupe	<signals>	<id_a>	<name_a>	<id_b>	<name_b>	<why>
  place-dupe-summary	places=N candidates=M strong=K
  ```

Signals:
- `name_contains`: one name inside the other on a word boundary. usually a room that became a place.
- `shared_address`: same address string. usually one venue, two names.
- `name_is_address`: a NAME equals another row's address. usually a link/calendar string in the name seat.
- `twin_edges`: same origin, same minutes. weakest; never in the registration
  offer; `--strong` drops it. corroboration only.

id columns = ROW ORDER, not a ranking. ignored candidate costs nothing:
never re-raise the pair, never count them at him.
