#!/bin/bash
# health-log test — deterministic full-panel save verification.
# Writes a fixture full InBody panel under a THROWAWAY date so real data is
# never touched, verifies every key landed, then cleans up the throwaway date.
# All writes go through kit_life.sh log-metric (idempotent upsert). Read-only
# sqlite is used only to count/verify and to delete the throwaway rows at the end.
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
LKIT="$PROJECT_ROOT/database/kit_life.sh"
DB="$PROJECT_ROOT/database/kit_life.db"
TDATE="2099-01-01"   # throwaway date, far future, never a real measurement
NOTE="InBody (TEST)"

cleanup() {
  sqlite3 "$DB" "DELETE FROM metric_log WHERE date='$TDATE';" 2>/dev/null || true
}
trap cleanup EXIT

# full fixture panel: 11 established + 14 additional = 25 keys.
# The KEY SET and its ordering are the contract this test exists to hold. The
# VALUES are synthetic reference numbers -- a shipped kit carries no person's
# body composition. Change them freely; keep the 25 keys and the SPOT_CHECKS
# block below in sync.
declare -a PAIRS=(
  "weight_kg 70.0"
  "skeletal_muscle_kg 32.0"
  "fat_mass_kg 12.6"
  "body_fat_pct 18.0"
  "lean_mass_kg 57.4"
  "lean_arm_l_kg 3.20"
  "lean_arm_r_kg 3.30"
  "lean_trunk_kg 26.0"
  "lean_leg_l_kg 8.40"
  "lean_leg_r_kg 8.50"
  "visceral_fat_level 5.0"
  "protein_kg 11.5"
  "mineral_kg 3.90"
  "tbw_l 42.0"
  "bmr_kcal 1600"
  "bmi 23.0"
  "smi 8.0"
  "whr 0.85"
  "inbody_score 80"
  "slm_kg 54.0"
  "fat_arm_l_kg 0.6"
  "fat_arm_r_kg 0.6"
  "fat_trunk_kg 6.5"
  "fat_leg_l_kg 1.9"
  "fat_leg_r_kg 1.9"
)

echo "[test] writing ${#PAIRS[@]} fixture metrics under throwaway date $TDATE"
cleanup   # ensure clean start
for p in "${PAIRS[@]}"; do
  key="${p% *}"; val="${p#* }"
  "$LKIT" log-metric "$TDATE" "$key" "$val" "$NOTE" >/dev/null
done

# idempotency: re-run one key, must NOT duplicate (UNIQUE date,metric)
"$LKIT" log-metric "$TDATE" "weight_kg" "70.0" "$NOTE" >/dev/null

GOT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM metric_log WHERE date='$TDATE';")
WANT=${#PAIRS[@]}
echo "[test] rows landed: $GOT (want $WANT)"

if [ "$GOT" != "$WANT" ]; then
  echo "[test] FAIL — count mismatch"
  sqlite3 "$DB" "SELECT metric,value FROM metric_log WHERE date='$TDATE' ORDER BY metric;"
  exit 1
fi

# spot-check a few values persisted (numeric compare — DB stores REAL so 80 -> 80.0)
for check in "weight_kg 70.0" "whr 0.85" "fat_leg_r_kg 1.9" "inbody_score 80"; do
  k="${check% *}"; want="${check#* }"
  got=$(sqlite3 "$DB" "SELECT value FROM metric_log WHERE date='$TDATE' AND metric='$k';")
  if ! sqlite3 "$DB" "SELECT CASE WHEN ABS($got - $want) < 0.0001 THEN 1 ELSE 0 END;" | grep -q '^1$'; then
    echo "[test] FAIL — $k = $got, want $want"; exit 1
  fi
done

echo "[test] PASS — all ${WANT} keys deterministically saved, idempotent re-run did not duplicate, values exact."
echo "[test] cleaning up throwaway date $TDATE (trap)"
