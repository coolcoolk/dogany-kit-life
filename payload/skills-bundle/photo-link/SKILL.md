---
name: photo-link
display_name: 사진 메모 연결
description: >-
  Link an inbound Telegram photo and memo to a phone Photos asset when that is the owner's intent. An active candidate
  exchange also accepts a numbered pick, a single-photo confirmation, device-unlocked status, rejection/correction of a
  candidate or a report that none matches. Route follow-ups from the outstanding photo-candidate state, regardless of
  wording. Calls find_photo_candidates, offers numbered thumbnails, then writes/corrects the kit photo memo only after
  the owner's pick. Transporter remember_photo without inbound Telegram photo stays with core-log's episode adapter.
scope: framework
layer: shipped
---

# photo-link

Reader: agent. Trigger tier: BEST-EFFORT discovery; helper gates state, selection,
thumbnail validation, kit verb calls. No hook / guaranteed model routing claim.
Inbound-photo exchange: use this writer instead of episode adapter's silent
photo-note-add. Keep other memo capture behavior intact.

## Paths + boundaries

`SK=${PROJECT_ROOT:-$(pwd)}/.claude/skills-bundle/photo-link`
`LKIT=${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh`
Helper: `python3 "$SK/photo_link.py" --session "$SESSION" <command>`.
Runtime: Python stdlib only; no Pillow. Helper stdout = INTERNAL JSON, never chat.
Execute only `telegram` array actions, in order. Never relay tool status, asset ids,
record ids, capture times, distances, copy keys, errors, JSON. No extra
acknowledgement / completion / progress text except the helper's approved
`saved` line: "메모했어요." after a successful add/update.

Use private SESSION directory per Telegram chat + inbound photo-message id,
under workspace `files/photo-link/`; bind replies to that exchange. Keep photo,
note, MCP response, state, JPEGs across turns and through correction window.
Detached tests use run scratch. Keep JPEG paths unchanged until delivered.
One active offer per chat; finish/cancel earlier exchange before offering next.
Ambiguous reply never authorizes a pick.

## Flow

1. Save memo verbatim in NOTE_FILE and received Telegram photo locally.
   `begin --note-file "$NOTE_FILE" --image-path "$PHOTO_FILE"` returns `mcp`.
   Alternative: `--image-base64-file "$BASE64_FILE" --mime-type image/jpeg`
   (actual MIME type). Keep image until retry ends. `wait_s` defaults 30.
   Relinking existing memo: resolve exact `lk_...` via read-only
   `database/record_provider.py --types photo_note --limit 1000`; paginate using
   `--after <updated_at> <record_id>`. Pass `begin --record-id "$RECORD_ID"`.
   New memo: omit record id. Never guess ids or treat Telegram file id as Photos
   asset. No memo/ref write before user's pick.
2. Call actual MCP `find_photo_candidates` with helper `mcp.arguments`:
   `image_path` OR `image_base64` + `mime_type`, `wait_s=30`.
   Save structured result in RESPONSE_FILE, then
   `result --response-file "$RESPONSE_FILE"`.
   Helper sorts ascending distance; validates JPEG <=100 KiB / <=320px;
   writes original JPEG bytes to numbered files, no image editing.
3. Execute helper `telegram` actions sequentially through instance sender:
   `{"type":"photo","path":"<absolute JPEG>","caption":"<number>"}` ->
   `bash "${PROJECT_ROOT:-$(pwd)}/routines/push.sh" --photo "$PHOTO_PATH" --caption "$CAPTION"`.
   Bare number = photo caption, never separate text / `send_file::` line.
   `{"type":"text","text":"<approved question>"}` -> sender `--text "$TEXT"`,
   only after every preceding photo succeeds. Stop on failed/uncertain delivery;
   reconcile delivered stages before retry, never send question ahead of photos.
   Session surface: retain authenticated stdin plan + existing `--strict
   --session ... --epoch ... --op ... --stage ... --frame ... --slot ...`.
   Photo stage needs `--text "$CAPTION"` too: session sender uses sealed frame
   text as photo caption; `--caption` alone applies only to non-session sender.
   Same path + number in sealed photo frame; no separate text bubble.
   Stable numbers survive correction; never renumber remaining thumbnails.
   One candidate: send captioned photo, then helper's `confirm_one` text.
   `no_match`: retain memo locally, no DB write; relay helper's `no_match` text.
   Reject leaves no candidate: relay helper's `no_match` text too.
   `pending` / `timed_out`: retain memo + image, no DB write;
   relay helper's `phone_locked` text.
4. User sends number for this offer: `reply --choice <number> --kit "$LKIT"`.
   Explicit yes to sole offered image: `reply --confirm --kit "$LKIT"`.
   MCP confidence / delivery never counts as confirmation.
   Helper calls `photo-note-add source=photos kind=photo id=<picked asset_id>
   note=<verbatim memo>`; edits use `photo-note-upd record_id=...` with same
   fields. Keep returned record id. After successful add/update, relay the
   helper's single approved `saved` text action: "메모했어요.".
5. "그 사진 아니야": `reply --reject --kit "$LKIT"`. Relay remaining numbered
   thumbnails + approved question; wait for pick. Update same record through
   `photo-note-upd`, never extra add. Exhausted: helper calls `photo-note-del`.
   None fit: `reply --none --kit "$LKIT"` deletes existing memo or cancels
   unsaved memo. One remaining candidate still needs explicit confirmation.
6. Only after user says phone unlocked/connected: `reply --unlocked` returns
   new MCP request for same image; repeat step 2 once. No automatic polling.
   Still pending/timed_out: wait for another explicit unlock signal.

## UI gate

Reader: agent + maintainer. `copy.json` owns visible text. Only `approved`
entries may ship. No other visible element without approval.
Successful number pick / sole-candidate confirmation / correction pick: relay
exactly one `approved.saved` text action, "메모했어요.", after the write succeeds.
Failed write, invalid answer, delete / exhausted offer, or cancel: never send
`saved`; keep the helper's existing actions. No extra acknowledgement.
Invalid / ambiguous answer: preserve state; no write / new visible response.
Never forward internal errors. Post-write state-save failure: reconcile via
read provider before retry, preventing duplicate add.

## Reader contract

No app format change. `record_provider.envelope()` exposes
`photo_refs=[{"source":"photos","kind":"photo","id":"<picked asset_id>"}]`;
`body.photo` carries same key; `body.note` carries memo. Delete uses existing
tombstone (`deleted=true`, `body=null`, `photo_refs=[]`). No direct SQL writes.
