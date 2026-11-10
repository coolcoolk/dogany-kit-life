#!/bin/bash
# Optional SMS collector. Onboarding enables this schedule / its sources.
# Clear confirmed payments emit the owner's one-line notice (setting defaults
# on) and any split question. PG payments retain the review ask path.
# Collector errors fail open; only a kit refusal is a loud exit.
# --dry renders delivery envelopes in a scratch kit without calling a sender.
set -uo pipefail
ROOT="${LEDGER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PY="${LIFEKIT_PYTHON:-$(command -v python3 || echo /usr/bin/python3)}"
export PYTHONDONTWRITEBYTECODE=1
NOTICES="$("$PY" "$ROOT/service/kit_life/ledger/sms-poll" --quiet --notifications-json)"
rc=$?
[[ $rc -eq 2 ]] && exit 2
[[ $rc -eq 0 && -n "$NOTICES" ]] || exit 0
[[ "${1:-}" == --dry ]] && { printf '%s\n' "$NOTICES"; exit 0; }
"$PY" - "$ROOT" "$NOTICES" <<'PY'
import json,os,subprocess,sys
for notice in json.loads(sys.argv[2]):
    args=['bash',os.path.join(sys.argv[1],'routines','push.sh'),'--text',notice['text'],'--audience','owner']
    if notice['buttons']:
        args += ['--strict','--buttons-json',json.dumps(notice['buttons'],ensure_ascii=False)]
    result=subprocess.run(args)
    if result.returncode: sys.exit(result.returncode)
PY
