# Dogany LifeKit

Dogany LifeKit is a personal life kit for a Dogany agent. Once installed, the
agent can keep your calendar, meals, workouts, body measurements, people,
places, goals and routines in one local database. You manage all of it by
talking to the agent in chat.

LifeKit is not a standalone app. It is a pack that installs onto an existing
Dogany agent.

## Requirements

- A Dogany agent on framework **2.6.0 or later**.
- Python 3.10 or later. The one runtime dependency (`holidays`, for skipping
  public holidays in routines) is listed in `payload/requirements.txt` and is
  provisioned by the installer.

## Install

Install LifeKit with the framework's pack installer, from the agent's
framework checkout:

```
scripts/pack/pack_install.sh <agent-slug> <agent-root> --pack kit_life
```

Run it again with `--upgrade` to move an installed agent to a newer LifeKit.

The installer reads `pack-manifest.json` and copies the payload into the
agent's instance: the database schema and migrations, the kit command line,
the chat skills, the scheduled routines and the default config. It then
brings the agent's kit database up to the current schema version. Your data
stays on your machine, in the agent's own kit database.

When the install finishes, the agent offers a short first-use setup. You
choose whether to start with calendar management or with meals, workouts and
body measurements. You can add the other side later.

## What it covers

**Calendar and time**
- Tasks, appointments and stays (for example "office, Mon-Fri 9-6"), added,
  changed, completed, moved or cancelled in plain language.
- Recurring routines: register, pause, resume, retire.
- Optional two-way sync with Google Calendar.
- Travel time between appointments. If a place or a route is missing, the
  agent asks you about it.
- Route and travel-time lookups between two places.

**Places and people**
- A place book that learns venues from names, addresses or shared map links.
- A relationship book: find people by nickname, see what you last did together,
  get reminders to keep in touch, get a short briefing before you meet someone.

**Health**
- Meal logging from text, photos or barcodes, with a daily nutrition card
  and optional calorie and macro targets.
- Workout logging, one finished session at a time, with daily and weekly
  views.
- Body-composition capture from an InBody sheet (photo or typed values).
- Pain and discomfort notes linked to the workout where they happened.

**Goals and reflection**
- A guided goal conversation that turns a stated goal into a weekly plan,
  routines and calendar blocks.
- Episodes: group related records (a trip, a dinner, a training block) into
  one experience you can look back on.
- A morning brief and a nightly daily retro, sent at times you choose, plus a
  weekly review whenever you ask for one.

**Money**
- Spending entries by category and by payment method, with simple
  "how much this month" views.

Every record stays in one local SQLite database. The kit command line
(`kit_life.sh`) reads and writes it. The chat skills call the same command
line, so anything the agent says it saved can be checked there.

## Languages

User-facing text ships in Korean and English.

## License

Dogany LifeKit is licensed under the PolyForm Strict License 1.0.0 together
with the Dogany Extension Permission. See `LICENSE` and `NOTICE` at the root of
this repository.
