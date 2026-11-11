# core-log -- person adapter (people, their facts, contact)

Before replying after capture, apply the common SETTLE ON EVIDENCE contract
from AGENT: saving facts alone does not finish an activity report. Read
`.claude/skills-bundle/schedule-log/reference/completion-chat.md` for the
shared completion check. A PLANNED item settled this turn: its single
completion reply overrides this adapter's capture-report/card instructions.
No open task/routine matches: no completion reply at all, hand over to
reference/episode.md. Tool work stays silent until the final line.

The record rules for people. person-manage and person-session point here;
lookups, briefs, merges and relationship care stay with them (care only
while `manage-state person` is on).

## who

1. `$LKIT noun-context "<current message>"` first; then `person-resolve
   <name_or_alias>`.
   - RESOLVED -> that id, ask nothing.
   - NEAR_MATCH -> ask its ASK line once (same person?). yes ->
     `near-match-accept person <ref> "<name>"`; no -> `person-add <name>
     [relation] --new-confirmed`.
   - AMBIGUOUS -> show the candidates the verb printed, ask which one.
   - ABSENT -> one question: an alias of someone known, or someone new.
     alias -> `person-alias <id> <alias>`; new -> `person-add <name>
     [relation] [aliases]`. Then record the fact in the same turn.
2. Never guess-create, never a second row for one person.

## facts about a person (shared kit)

- birthday: `person-set <id> "birthday=<what the user typed>"`; relay the
  CONFIRM line. Full dates/YYMMDD, year only (`97`, `1997`, or a Korean birth-year expression) and month-day
  only (`0305`, `03-05`, or Korean month/day words) are all storable. Record the known half
  immediately without asking for the missing half or parking it in a note.
  Later complementary halves merge; a full date replaces a partial value.
  This is the current birthday input contract, including when older
  person-manage text says a year is required.
- relation / gender / residence / job / contact: `person-set <id> f=v ...`
  in one call; `f=` clears.
- family links: `person-link <a> <b> <type>` ("a is <type> of b"); a child
  of two parents = two links. LINK_CONFLICT -> show it, ask, `--replace`
  only on yes.
- person emoji: `person-set <id> emoji=<e>`; `emoji=` restores 🙂.
- groups: `group-add <name>`, membership and retirement with the group
  verbs of person-manage's quick reference.
- "<모임> 이모지 X로 해줘" / a group's emoji: resolve the group with
  `noun-context` / `group-find`, then `group-set <group> emoji=<e>`;
  `emoji=` restores 👥. A group already named in `persons.groups` needs
  no separate `group-add`: its first emoji setting registers that same
  membership name in `person_group`. Never route a group emoji to
  `person-set` or create a person for the group.
- relay the CONFIRM lines; honorific on every person mention.
- relation of a child, hop distance, gender words: person-manage "edit a
  person" (`.claude/skills-bundle/person-manage/SKILL.md`) when delivered.

## contact and free facts

- "OO 연락완료" / "연락했어" / "연락 끝" is a touch, even with management off.
  Read `.claude/skills-bundle/person-session/reference/contact-gap.md` for completion routing:
  a suggestion for this person (conversation or alert_log shown/checkin) ->
  `acted <id> [--kind <explicit channel>]`; no suggestion -> `touch-add <id> other`
  (use call/message/dm/meet when explicitly stated). Run only the quoted active
  `"${PROJECT_ROOT:-$(pwd)}/database/relmod.sh"` from the agent workspace;
  never glob service paths or hunt inside `.gen`. Reply after exit 0 and OK,
  keeping "OO님 연락 완료했습니다". Intermediate routing errors stay silent.

- a call / message / meeting reported: `database/relmod.sh touch-add <id>
  call|message|dm|meet|other [--date YYYY-MM-DD] [--note "<words>"]`, one
  line, no follow-up. A meeting with an exact date and occasion -> the
  schedule adapter instead.
- a free fact the person-set fields do not hold ("<PERSON> moved to a new
  team"): `database/relmod.sh fact-add <id> "<owner words>" --source chat`.
- `database/relmod.sh` absent on this agent -> record the person-set /
  link part, say plainly the contact note cannot be stored here yet.

## never

no "shall I register them?", no relationship onboarding, closeness
suggestions or contact-gap prompts while recording; no memory-only person
fact.

## pets, including insurance capture

Pet identity is a kit person record. Species is never an insurance-category
or model-compression default. Read `person-resolve <name>` first. For a new
pet use `pet-add <name> "<owner or document species/breed words>"`; the verb
maps explicit species words or its checked breed table (`pet_species.json`),
including Korean particles/copulas in the owner's original sentence.
Scottish Straight and Scottish Fold are cats. Use this path for every pet
capture, including onboarding; never use `person-add <name> pet_dog` as a
fallback. Relay the localized CONFIRM wording, naming the resolved species.
Unknown breeds store `pet` (species unknown) and return ASK_SPECIES: ask that
question once in the owner's language, in the capture turn. Do not repeat it
on an EXISTS retry or if the owner cannot answer; keep species unknown.
On the owner's answer, use pet-species-set on that same identity and verify
person-show. Never infer dog from the name, insurance, or an unknown breed.
Conflicting evidence requires clarification, never a guess. Pass only the
species/breed wording about this pet, not a whole policy covering many animals.
Never turn a coat colour, insurer, product category, or model-written summary
into species evidence. Do not use memory compression to establish species.

For an existing pet, an explicit correction is
`pet-species-set <id|name> <species|unknown>` (cat, dog, rabbit, hamster, bird,
turtle, fish, guinea_pig, ferret, hedgehog, snake, lizard, axolotl).
For an explicitly stated animal outside that vocabulary, pass the owner's
species words verbatim: `pet-add <name> --species "<stated species>"` or
`pet-species-set <id|name> "<stated species>"`. The reserved `pet:` relation
preserves it without inventing a cat/dog mapping. Never use --species for a
breed whose animal is unknown; use the normal breed resolver and ask once.
Use only the owner's explicitly stated species for a correction. It updates the same identity that
person-show, noun-context and entity-facts read; verify with person-show.
Do not append a contradictory free-text correction and call the kit fixed.
A memory-only pet has to be added first; do not create a twin of an existing
person. Kit species takes precedence over an older recalled memory line.
