# ledger-log -- import lane

(Was the separate skill ledger-import of pack_ledger; folded into ledger-log
in kit_life 2.46.0 -- "import" is not one of the five skill slots, and
taking a screenshot or a file into the ledger is recording.)

All through `$PROJECT_ROOT/service/kit_life/ledger/` (which calls kit verbs). No raw SQL.

## screenshot (the default weekly path, 0 questions)

You read the image; the service validates and stages. Model readings are
ALWAYS pending -- nothing you read lands in the ledger without the owner's
one answer in the table.

1. Read every payment row in the screenshot(s): date, amount, merchant,
   card, and whether it is a payment / cancel (취소, 환불 on a line that
   reverses an earlier one) / transfer (이체, 카드대금) / income (입금, 급여).
   - Several scrolling screenshots of one list = ONE list: do not read an
     overlapping row twice.
   - Amount = the KRW integer as printed, positive. A cancel keeps the
     positive original amount with `"type":"cancel"` -- never a negative number.
   - Date: the list's date headers give the day; the year is this year
     unless the list says otherwise. ISO `YYYY-MM-DD`.
   - Card: the row's card if the list shows one; if the whole screenshot is
     one card's app, pass it once as `--card`. Use the card name as the
     owner's ledger knows it (method-list), else as printed.
   - A row you cannot read with certainty: leave it out and say so; never guess.
2. `service/kit_life/ledger/shot-stage --since <sweep monday> --until <today or sunday>
   --app toss|banksalad|kakaopay|naverpay|card|other [--card NAME] --rows '<JSON array>'`
   The window is the week being swept (retro-section prints it). Rows outside
   it are refused.
3. Say `say` verbatim (it names rejected lines in owner words). The staged
   rows are recorded evidence. Ledger management on (`database/kit_life.sh
   manage-state ledger` -> on): run the review in the same turn:
   `service/kit_life/ledger/review show --lane weekly` -> ledger-session
   retro-lane steps 1-4. Off: stop after the `say` line; no review table.

## Banksalad file (0 questions)

Banksalad's export is the only file format read (Korea). Outside Korea there
is no file lane: ask for a screenshot instead.

1. The CSV, the xlsx or the zip Banksalad mails, as sent ->
   `service/kit_life/ledger/csv-import <path>`.
   `{"error":"password-needed..."}` (an encrypted zip) -> ask the owner for the
   password once, then re-run `csv-import <path> --password-stdin` with the
   password as the only stdin line. NEVER put the password in an argument, a
   file, a note, memory or a reply; never repeat it back. `password-wrong` ->
   ask once more. `xlsx-locked` / `zip-encryption-unsupported` -> say the
   file cannot be opened and ask for a capture of the spending screen.
2. `csv-import` merges with the rows already there (no double month) and
   needs no archive step.
   An error mentioning headers -> "이 파일은 뱅크샐러드 형식이 아니어서 못
   읽었어요. 뱅크샐러드 앱에서 내보낸 파일인지 확인해주세요." Toss has no file
   export -- suggest a screenshot instead.
3. Say the `say` field verbatim.

Underneath (for you): each row becomes a kit intake item with a content-hash
reference, so the same file / screenshot twice = `duplicate`. Rows that match
an existing record attach to it (second evidence, no second spend). Transfers
are excluded. Screenshot rows stay pending until the review; file rows land
at import.

## forwarded card SMS (on demand)

`service/kit_life/ledger/sms-poll` -> JSON counts. `{"status":"off"}` -> the owner
has no per-card automation: say there are no card messages to read, and
nothing about turning it on (automation is set up only when the owner asks,
ledger-setup "after the onboarding"). Otherwise one line:
"카드 문자 N건 받아뒀어요. 회고 때 한 번에 볼게요." (N = staged + unread).
`status: error` -> "카드 문자를 지금 못 읽었어요. 밤에 다시 볼게요."

## discipline (import lane)

- never resolve items outside the review table (one table, one answer).
- never print SMS texts, addresses, card digits to the owner.
- owner-facing text = Korean everyday words; never verbs, flags, "intake".

## what a statement export cannot show (easy-pay channels)

A bank/card export (뱅크샐러드 file) records the account or card and the merchant.
It does not mark the payment channel at the counter: 토스 페이스페이, 삼성페이,
애플페이 and similar taps appear only as the card/account row with the shop name.
A row whose merchant IS a wallet name (e.g. "토스페이" on 토스뱅크 통장) is
evidence of that wallet's funding source; a face/tap channel has no such row.
- never infer a face/tap channel from rows and never ask about it unprompted.
- the owner names it ("페이스페이는 토스뱅크 체크카드로 결제돼") -> record it once:
  `kit_life.sh funding-set <wallet> <source> --origin owner-stated`; it stays a
  standing link and is never asked again.

## Imported history before onboarding cards

After import and before any identification or plan card, follow
`ledger-setup/reference/purpose-order.md`: read `ledger-merchant-kinds`,
look up pending businesses, save evidence through `ledger-merchant-kind-set
--source web|owner`, then re-read the plan and pending question. A file's
category is not evidence of business kind. Unresolved businesses keep the
existing per-payment ask; never populate the cache to match a rehearsal.
