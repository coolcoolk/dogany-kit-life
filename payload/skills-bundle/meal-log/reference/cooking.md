# Cooking and meal ideas

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.

After day-now, follow core-log's one procedure: 2-3 next-meal candidates + one recommendation; never re-ask the meal.
Retain eaters/target. Dish pick + target, or explicit cooking/start -> template -> check -> plan -> open per that procedure.
Plain food advice may stay advice-only. Suggestions never log intake or need diet-plan.

After day-now, read `core-log/reference/cooking.md` in the installed skills
root (sibling `../../core-log/reference/cooking.md`). It is the single owner
of next-eating-occasion advice, menu choice and the cooking-session procedure.
Add one step during a session with `cooking add-step STEP.json`; never offer
cancel/reopen or a separate reminder. Use typed meat/roast/sear fields and
`tempering_lead_sec` for the default preparation described in that reference.
For every oven step with a specified temperature or mode, fill typed `temp_c`
(Celsius) and `oven_mode` (`top_bottom` or `convection`) using the stored
appliance modes. Keep instructions in `action`, but never leave the oven
settings only there: the plan timeline reads typed fields, never action text.
The timeline adds the typed heat/oven condition after the step duration and
shows a non-empty `done_cue` verbatim on its own `ㄴ` line.

At plan time, fill optional `done_cue` only when the action alone cannot tell
when it is done (고기 표면이 손으로 만질 만큼 식음, 겉면이 노릇함, 물이 끓음).
For 꺼내기, 계량하기, 담기 and other self-evident actions, omit it or leave it
empty. Do not repeat the action. Only a non-empty typed cue shows 완료 기준;
the automatic 냉장고에서 {meat} 꺼내기 step has an empty cue.

Cooking amounts follow each person's history: run `cooking portions`; the owner
uses his own typical meal kcal, never a generic Western portion. A companion
without data starts smaller; ask once if it matters. Side bread/pastry starts
at one each, with rich-item kcal named (80g puff pastry about 440kcal).
Ask an ambiguous pack/sheet weight once and store its basis via `cooking weight`.
Log only the owner's stated share of the made dish, including uneven splits;
`반반` is an owner-authorized half, never an inferred default. For an active
session, interpret the eating report, call `cooking close`, then
`meal-add --dish ID --portion FRACTION` (or typed `--shares` for several dishes).
For a cancelled cooking session finished by hand, run
 `meal-add DATE SLOT --cooked 'exact owner words'` without cooking cards. Use the saved plan's
recipe nutrition and stated share; never offer session reopen or hand macros
as a meal-log route. When the command returns `reply`, relay it verbatim once.
Do not paraphrase or append a parenthetical restatement of dishes, shares or
the owner's first-person words. Its existing meal card includes the fraction;
append only a printed satiety question. This reply contract takes precedence
over the generic confirmation examples in SKILL.md.
A satiety reply never licenses guessed advice.

For any timing question, run `cooking show` and answer only from the returned
`schedule` (step name and local HH:MM start/end). QUESTION results also carry
the current schedule. Never quote times from earlier messages, reminder text,
or a previous plan; recomputation can shift all remaining dishes.
