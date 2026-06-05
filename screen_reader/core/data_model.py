"""
core/data_model.py
3 independent sets of values with rankings and lottery highlights.
"""

from dataclasses import dataclass
from typing import Optional

TOP_N          = 10
BOTTOM_N       = 10
SET_SIZE       = 56
SET_NAMES      = ["Set 1", "Set 2", "Set 3"]
LOTTERY_NAMES  = ["Melate", "Revancha", "Revanchita"]
LOTTERY_COUNTS = [7, 6, 6]   # Melate has 7 numbers, Revancha/Revanchita have 6


@dataclass
class NumberRow:
    index:        int
    value:        float
    rank_top:     Optional[int] = None
    rank_bottom:  Optional[int] = None
    lottery_hit:  bool          = False


class SetData:
    def __init__(self, set_index: int):
        self.set_index    = set_index
        self.name         = SET_NAMES[set_index]
        self.lottery_name = LOTTERY_NAMES[set_index]
        self.rows:    list[NumberRow] = []
        self.lottery: list[int]       = []

    def add_value(self, value: float):
        self.rows.append(NumberRow(index=len(self.rows)+1, value=value))
        self._rank()

    def is_full(self)  -> bool: return len(self.rows) >= SET_SIZE
    def count(self)    -> int:  return len(self.rows)

    def _rank(self):
        for r in self.rows:
            r.rank_top = r.rank_bottom = None
        for i, r in enumerate(sorted(self.rows, key=lambda x: x.value, reverse=True)[:TOP_N]):
            r.rank_top = i+1
        for i, r in enumerate(sorted(self.rows, key=lambda x: x.value)[:BOTTOM_N]):
            r.rank_bottom = i+1
        self._apply_lottery()

    def set_lottery(self, indices: list[int]):
        self.lottery = indices
        self._apply_lottery()

    def _apply_lottery(self):
        sel = set(self.lottery)
        for r in self.rows:
            r.lottery_hit = r.index in sel

    def color_for_row(self, row: NumberRow) -> tuple[str, str]:
        if row.lottery_hit:
            return ("#E6A817", "#000000")
        if row.rank_top is not None:
            t = 1.0 - (row.rank_top-1) / TOP_N
            return (f"#{int(180+75*t):02X}{int(30*(1-t)):02X}{int(30*(1-t)):02X}", "#FFFFFF")
        if row.rank_bottom is not None:
            t = 1.0 - (row.rank_bottom-1) / BOTTOM_N
            return (f"#{int(20*(1-t)):02X}{int(150+105*t):02X}{int(20*(1-t)):02X}", "#FFFFFF")
        return ("#1E1E2E", "#CCCCCC")


class DataModel:
    def __init__(self):
        self.sets:       list[SetData] = [SetData(i) for i in range(3)]
        self.active_set: int           = 0

    def current_set(self) -> SetData:   return self.sets[self.active_set]
    def advance_set(self):
        if self.active_set < 2: self.active_set += 1

    def reset(self):
        self.sets       = [SetData(i) for i in range(3)]
        self.active_set = 0
