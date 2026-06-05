"""
core/log_export.py
Generates a diagnostic .txt report from a set's log entries
and the captured values, for sending to the developer.
"""

import time
from core.live_reader import LogEntry
from core.data_model  import SetData


def export_log(set_data: SetData,
               log_entries: list[LogEntry],
               filepath: str):
    lines = []
    lines.append("=" * 70)
    lines.append(f"MELATE SCREEN READER — DIAGNOSTIC LOG")
    lines.append(f"Set: {set_data.name}   |   Exported: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)
    lines.append("")

    # ── Captured values
    lines.append("── CAPTURED VALUES (" + str(set_data.count()) + ") ──")
    for row in set_data.rows:
        note = ""
        if row.rank_top:    note = f"  ▲ Top {row.rank_top}"
        elif row.rank_bottom: note = f"  ▼ Bot {row.rank_bottom}"
        if row.lottery_hit: note += "  ★ LOTTERY"
        lines.append(f"  {row.index:>3}.  {row.value:.2f}{note}")
    lines.append("")

    # ── Full live log
    lines.append(f"── LIVE LOG ({len(log_entries)} entries) ──")
    lines.append(f"  {'TIME':>8}  {'VALUE':>7}  {'STATUS':<22}  {'MARK':<12}  {'SEQ':>4}  RAW OCR")
    lines.append("  " + "-"*70)
    for e in log_entries:
        t     = f"{e.timestamp:.2f}"
        mark  = e.user_mark   or "—"
        seq   = str(e.sequence_no) if e.sequence_no else "—"
        lines.append(f"  {t:>8}  {e.value:>7.2f}  {e.status:<22}  {mark:<12}  {seq:>4}  {e.raw_ocr}")
    lines.append("")

    # ── Mismatch analysis
    lines.append("── MISMATCH ANALYSIS ──")
    finals     = [e for e in log_entries if e.user_mark == "final"]
    captured   = {round(r.value, 2) for r in set_data.rows}
    missed     = [e for e in finals if round(e.value, 2) not in captured]
    wrong      = [r for r in set_data.rows
                  if not any(abs(e.value - r.value) < 0.03
                             for e in finals)]

    if missed:
        lines.append("  VALUES MARKED FINAL BUT NOT CAPTURED:")
        for e in missed:
            lines.append(f"    {e.value:.2f}  (seq {e.sequence_no or '?'})  @ t={e.timestamp:.2f}")
    else:
        lines.append("  All marked-final values were captured. ✓")

    lines.append("")
    if wrong:
        lines.append("  CAPTURED VALUES NOT CONFIRMED AS FINAL:")
        for r in wrong:
            lines.append(f"    row {r.index}  →  {r.value:.2f}")
    else:
        lines.append("  No unconfirmed captures. ✓")

    lines.append("")
    lines.append("=" * 70)

    with open(filepath, "w") as f:
        f.write("\n".join(lines))
