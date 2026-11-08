"""Typed facts and stable sections for every daily nutrition surface.

No database reads or text parsing belong to consumers of these values.
"""
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, TypedDict


class Totals(TypedDict, total=False):
    kcal: float
    protein: float
    carb: float
    fat: float
    sugar: float
    caffeine: float


class Band(TypedDict):
    min: float
    max: float


class NutritionTarget(TypedDict, total=False):
    kcal: float
    base_kcal: float
    protein_goal: Optional[float]
    protein: Optional[float]
    carb: Optional[float]
    fat: Optional[float]
    bmr: Optional[float]
    neat: Optional[float]
    balance: Optional[float]
    workout_kcal: float
    carry: float
    adjustment: float
    notices: list
    formula_lines: list


class MacroBandResult(TypedDict):
    bands: Dict[str, Band]
    base_bands: Dict[str, Band]
    remaining_kcal: float
    status: str
    issues: list


def display_bands(result, eaten, *, normal_result=None):
    if not result:
        return {}
    if result['remaining_kcal'] <= 0:
        # Keep the last feasible normal-day policy projection as the target.
        # Intake beyond the energy budget must not turn into its own target.
        source = normal_result['bands'] if normal_result else result['base_bands']
        return {k: dict(v) for k, v in source.items()}
    if result['status'] != 'infeasible':
        return {k: dict(v) for k, v in result['bands'].items()}
    # Minima cannot all fit: retain the policy anchors rather than printing
    # reversed intervals. Raw infeasible projections remain in band_result.
    return {k: dict(v) for k, v in result['base_bands'].items()}


@dataclass(frozen=True)
class NutritionSnapshot:
    date: str
    rows: Tuple[tuple, ...]
    eaten_rows: Tuple[tuple, ...]
    planned_rows: Tuple[tuple, ...]
    eaten: Totals
    planned: Totals
    target: Optional["NutritionTarget"]
    target_source: str
    declared_protein: Optional[float]
    band_result: Optional["MacroBandResult"]
    bands: Dict[str, Band]
    tracked_rows: Tuple[dict, ...]
    supplement_flags: Tuple[dict, ...]
    workouts: Tuple[dict, ...]
    caffeine_estimated: bool
    caffeine_sensitive: bool
    caffeine_advice: Tuple[str, ...]
    regimen_count: int

    @property
    def remaining_kcal(self):
        return self.target['kcal'] - self.eaten['kcal'] if self.target else None

    @property
    def remaining_after_plan(self):
        left = self.remaining_kcal
        return left - self.planned['kcal'] if left is not None else None

    @property
    def infeasible(self):
        return bool(self.band_result and self.band_result['status'] == 'infeasible')

    @property
    def meals(self):
        return tuple(dict(id=r[0], type=r[1], name=r[2], kcal=r[3],
                          carbs=r[4], protein=r[5], fat=r[6], sugar=r[7])
                     for r in self.rows)


@dataclass(frozen=True)
class NutritionBlock:
    snapshot: NutritionSnapshot
    sections: Dict[str, Tuple[str, ...]]
    text: str
