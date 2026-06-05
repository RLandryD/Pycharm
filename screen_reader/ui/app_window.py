"""
ui/app_window.py
Main application window.
Left side: 3-set data tabs + lottery summary.
Right side: live log panel for the active set (with pause/resume + marking).
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

from core.screen_capture  import RegionSelector, ocr_lottery_numbers
from core.live_reader     import LiveReader, LogEntry
from core.data_model      import DataModel, SET_SIZE, LOTTERY_NAMES, LOTTERY_COUNTS
from core.layout_preset   import save_preset, load_preset, preset_names, delete_preset
from core.log_export      import export_log
from ui.log_panel         import LogPanel

# ── Theme ─────────────────────────────────────────────────────────────────────
BG      = "#12121F"
BG2     = "#1A1A2E"
BG3     = "#22223A"
FG      = "#E0E0E0"
ACCENT  = "#00C896"
RED     = "#E06C75"
GOLD    = "#E6A817"
PURPLE  = "#CC88FF"
BLUE    = "#5599FF"
UI_F    = ("Segoe UI", 10)
MONO    = ("Courier New", 11)
LOTTERY_COLORS = [ACCENT, BLUE, PURPLE]


class AppWindow:
    def __init__(self, root: tk.Tk):
        self.root     = root
        self.root.title("Melate Screen Reader")
        self.root.configure(bg=BG)
        self.root.geometry("1200x860")
        self.root.resizable(True, True)

        self.model    = DataModel()
        self.selector = RegionSelector()

        self.roi_value:   dict | None = None
        self.roi_lottery: dict | None = None

        self._reader: LiveReader | None = None

        # One log-entry list per set (persists across pause/resume)
        self._logs: list[list[LogEntry]] = [[], [], []]

        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Top-level paned window (left=data, right=log)
        paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL,
                               bg=BG, sashwidth=6, sashrelief=tk.FLAT)
        paned.pack(fill=tk.BOTH, expand=True)

        left  = tk.Frame(paned, bg=BG)
        right = tk.Frame(paned, bg=BG)
        paned.add(left,  minsize=520)
        paned.add(right, minsize=380)

        self._build_left(left)
        self._build_right(right)

    # ── Left panel ────────────────────────────────────────────────────────────

    def _build_left(self, parent):
        # Header
        tk.Label(parent, text="🎱 Melate Screen Reader",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 15, "bold")).pack(
            anchor="w", padx=12, pady=(8, 4))

        # Settings
        cfg = tk.LabelFrame(parent, text=" Filter Settings ", bg=BG, fg=FG,
                            font=UI_F, padx=8, pady=5)
        cfg.pack(fill=tk.X, padx=12, pady=(0, 4))

        def spin(parent, label, var, frm, to, inc, fmt, col):
            tk.Label(parent, text=label, bg=BG, fg=FG, font=UI_F).grid(
                row=0, column=col,   padx=(0, 3), sticky="e")
            tk.Spinbox(parent, from_=frm, to=to, increment=inc, width=7,
                       textvariable=var, format=fmt,
                       bg=BG3, fg=FG, insertbackground=FG,
                       buttonbackground=BG3).grid(
                row=0, column=col+1, padx=(0, 12))

        self.var_min    = tk.DoubleVar(value=3.20)
        self.var_max    = tk.DoubleVar(value=5.20)
        self.var_stable = tk.DoubleVar(value=0.50)
        self.var_count  = tk.IntVar(value=SET_SIZE)

        spin(cfg, "Min:",         self.var_min,    0,   100, 0.05, "%.2f", 0)
        spin(cfg, "Max:",         self.var_max,    0,   100, 0.05, "%.2f", 2)
        spin(cfg, "Stable secs:", self.var_stable, 0.1, 5,   0.1,  "%.1f", 4)
        spin(cfg, "Values/set:",  self.var_count,  1,   200, 1,    "%.0f", 6)

        # Controls
        ctrl = tk.LabelFrame(parent, text=" Controls ", bg=BG, fg=FG,
                             font=UI_F, padx=8, pady=5)
        ctrl.pack(fill=tk.X, padx=12, pady=(0, 4))

        self._btn(ctrl, "📐 Select Value Region",   self._select_value_roi,   ACCENT).grid(row=0, column=0, padx=3, pady=2, sticky="ew")
        self._btn(ctrl, "▶ Start Reading",          self._start_reading,      BLUE).grid( row=0, column=1, padx=3, pady=2, sticky="ew")
        self._btn(ctrl, "⏭ Next Set",               self._next_set,           "#FF9944").grid(row=0, column=2, padx=3, pady=2, sticky="ew")
        self._btn(ctrl, "⏹ Stop",                   self._stop_reading,       RED).grid(  row=0, column=3, padx=3, pady=2, sticky="ew")
        self._btn(ctrl, "📐 Select Lottery Region", self._select_lottery_roi, GOLD).grid( row=1, column=0, padx=3, pady=2, sticky="ew")

        for i, (name, color) in enumerate(zip(LOTTERY_NAMES, LOTTERY_COLORS)):
            self._btn(ctrl, f"🎱 {name}", lambda idx=i: self._capture_lottery(idx),
                      color).grid(row=1, column=i+1, padx=3, pady=2, sticky="ew")

        for c in range(4): ctrl.columnconfigure(c, weight=1)

        # Preset bar
        pbar = tk.LabelFrame(parent, text=" Presets ", bg=BG, fg=FG,
                             font=UI_F, padx=8, pady=4)
        pbar.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.preset_var = tk.StringVar()
        self.preset_cb  = ttk.Combobox(pbar, textvariable=self.preset_var,
                                        font=UI_F, width=18)
        self.preset_cb.pack(side=tk.LEFT, padx=(0, 6))
        self._refresh_presets()

        self._btn(pbar, "💾 Save",    self._save_preset,    ACCENT).pack(side=tk.LEFT, padx=2)
        self._btn(pbar, "📂 Load",    self._load_preset,    ACCENT).pack(side=tk.LEFT, padx=2)
        self._btn(pbar, "❌ Delete",  self._delete_preset,  RED).pack(  side=tk.LEFT, padx=2)
        self._btn(pbar, "🗑 Reset All", self._reset_all,    RED).pack(  side=tk.RIGHT, padx=2)

        # Status + ROI labels
        self.status_var = tk.StringVar(value="Ready. Select the value region to begin.")
        tk.Label(parent, textvariable=self.status_var,
                 bg="#0A0A18", fg=ACCENT, font=UI_F, anchor="w",
                 padx=10).pack(fill=tk.X, pady=(0, 2))

        roi_row = tk.Frame(parent, bg=BG)
        roi_row.pack(fill=tk.X, padx=12, pady=(0, 4))
        self.lbl_value_roi   = tk.Label(roi_row, text="Value ROI: not set",
                                         bg=BG2, fg="#888", font=UI_F, padx=6, pady=2)
        self.lbl_value_roi.pack(side=tk.LEFT, padx=(0, 8))
        self.lbl_lottery_roi = tk.Label(roi_row, text="Lottery ROI: not set",
                                         bg=BG2, fg="#888", font=UI_F, padx=6, pady=2)
        self.lbl_lottery_roi.pack(side=tk.LEFT)

        # 3-set notebook
        self._style_widgets()
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 4))

        self.trees:      list[ttk.Treeview] = []
        self.tab_labels: list[tk.StringVar] = []

        tab_names = ["Set 1 — Melate", "Set 2 — Revancha", "Set 3 — Revanchita"]
        for i in range(3):
            frame = tk.Frame(self.notebook, bg=BG2)
            self.notebook.add(frame, text=f"  {tab_names[i]}  ")

            sv = tk.StringVar(value="0 / 56 values")
            self.tab_labels.append(sv)
            tk.Label(frame, textvariable=sv, bg=BG2, fg=FG,
                     font=UI_F, anchor="w", padx=8).pack(fill=tk.X, pady=(4, 0))

            tree = ttk.Treeview(frame,
                                columns=("idx", "value", "rank", "lottery"),
                                show="headings", selectmode="browse")
            tree.heading("idx",     text="#")
            tree.heading("value",   text="Value")
            tree.heading("rank",    text="Rank")
            tree.heading("lottery", text=LOTTERY_NAMES[i])
            tree.column("idx",     width=55,  anchor="center")
            tree.column("value",   width=100, anchor="center")
            tree.column("rank",    width=170, anchor="w")
            tree.column("lottery", width=110, anchor="center")

            vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)
            tree.pack(fill=tk.BOTH, expand=True)
            self.trees.append(tree)

        # Lottery summary
        summ = tk.LabelFrame(parent, text=" Lottery Highlights ", bg=BG, fg=FG,
                             font=UI_F, padx=8, pady=4)
        summ.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.summary_box = tk.Text(summ, height=4, bg=BG2, fg=FG,
                                    font=MONO, state=tk.DISABLED, relief=tk.FLAT)
        self.summary_box.pack(fill=tk.X)

    # ── Right panel (log) ─────────────────────────────────────────────────────

    def _build_right(self, parent):
        tk.Label(parent, text="📋 Live Reading Log",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).pack(
            anchor="w", padx=8, pady=(8, 2))

        # Log notebook — one tab per set
        self.log_notebook = ttk.Notebook(parent)
        self.log_notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))

        self.log_panels: list[LogPanel] = []
        for i in range(3):
            panel = LogPanel(self.log_notebook,
                             set_name=["Set 1","Set 2","Set 3"][i],
                             on_pause_toggle=self._toggle_pause)
            self.log_notebook.add(panel,
                text=f"  {['Melate','Revancha','Revanchita'][i]}  ")
            self.log_panels.append(panel)

        # Export buttons
        self._btn(parent, "📤 Export Log for This Set",
                  self._export_log, GOLD).pack(padx=6, pady=(4,2), fill=tk.X)
        self._btn(parent, "📊 Export CSV (All Sets)",
                  self._export_csv, ACCENT).pack(padx=6, pady=(2,4), fill=tk.X)

    # ─────────────────────────────────────────────────────────────────────────
    # ROI selection
    # ─────────────────────────────────────────────────────────────────────────

    def _select_value_roi(self):
        self._status("Draw a box around the scale display…")
        self.root.after(150, self._do_select_value_roi)

    def _do_select_value_roi(self):
        self.root.withdraw(); self.root.update()
        bbox = self.selector.select("Select VALUE region (scale display)")
        self.root.deiconify()
        if bbox:
            self.roi_value = bbox
            self.lbl_value_roi.config(
                text=f"Value ROI: {bbox['left']},{bbox['top']}  {bbox['width']}×{bbox['height']}",
                fg=ACCENT)
            self._status("Value region set. Click ▶ Start Reading when ready.")

    def _select_lottery_roi(self):
        self._status("Draw a box around the lottery number row…")
        self.root.after(150, self._do_select_lottery_roi)

    def _do_select_lottery_roi(self):
        self.root.withdraw(); self.root.update()
        bbox = self.selector.select("Select LOTTERY region")
        self.root.deiconify()
        if bbox:
            self.roi_lottery = bbox
            self.lbl_lottery_roi.config(
                text=f"Lottery ROI: {bbox['left']},{bbox['top']}  {bbox['width']}×{bbox['height']}",
                fg=GOLD)
            self._status("Lottery region set.")

    # ─────────────────────────────────────────────────────────────────────────
    # Reading controls
    # ─────────────────────────────────────────────────────────────────────────

    def _start_reading(self):
        if not self.roi_value:
            messagebox.showwarning("No region", "Select the value region first.")
            return
        if self.model.active_set >= 3:
            messagebox.showinfo("Done", "All 3 sets collected.")
            return
        if self._reader and self._reader.is_running:
            if self._reader.is_paused:
                self._reader.resume()
                self.log_panels[self.model.active_set].set_paused_state(False)
                self._status(f"▶ Resumed {self.model.current_set().name}…")
            else:
                messagebox.showinfo("Running", "Already reading. Use ⏸ Pause in the log panel.")
            return

        si = self.model.active_set
        self.notebook.select(si)
        self.log_notebook.select(si)

        self._reader = LiveReader(
            bbox         = self.roi_value,
            min_val      = self.var_min.get(),
            max_val      = self.var_max.get(),
            target_count = self.var_count.get(),
            stable_secs  = self.var_stable.get(),
            on_log_entry = self._on_log_entry,
            on_captured  = self._on_captured,
            on_done      = self._on_set_done,
            on_status    = self._status,
            tk_after     = self.root.after,
        )
        self._reader.start()
        self._status(f"▶ Reading {self.model.current_set().name}…")

    def _toggle_pause(self):
        if not self._reader:
            return
        if self._reader.is_paused:
            self._reader.resume()
            self.log_panels[self.model.active_set].set_paused_state(False)
            self._status(f"▶ Resumed {self.model.current_set().name}…")
        else:
            self._reader.pause()
            self.log_panels[self.model.active_set].set_paused_state(True)
            self._status("⏸ Paused — mark log entries, then click ▶ Start Reading to resume.")

    def _next_set(self):
        if self._reader:
            self._reader.stop()
            self._reader = None
        if self.model.active_set < 2:
            self.model.advance_set()
            si = self.model.active_set
            self.notebook.select(si)
            self.log_notebook.select(si)
            self._status(f"Ready for {self.model.current_set().name}. Click ▶ Start Reading.")
        else:
            self._status("All sets done. Capture lottery numbers.")

    def _stop_reading(self):
        if self._reader:
            self._reader.stop()
            self._reader = None
        self._status("Stopped.")

    # ─────────────────────────────────────────────────────────────────────────
    # Reader callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def _on_log_entry(self, entry: LogEntry):
        si = self.model.active_set
        self._logs[si].append(entry)
        self.log_panels[si].add_entry(entry)

    def _on_captured(self, value: float):
        s = self.model.current_set()
        s.add_value(value)
        si = self.model.active_set
        self._refresh_tree(si)
        self.tab_labels[si].set(f"{s.count()} / {self.var_count.get()} values")

    def _on_set_done(self):
        s = self.model.current_set()
        self._refresh_tree(self.model.active_set)
        messagebox.showinfo(
            f"{s.name} complete!",
            f"{s.name} — {s.count()} values collected.\n\n"
            "Click  ⏭ Next Set  then  ▶ Start Reading  for the next set.\n"
            "Don't forget to export the log before resetting!")

    # ─────────────────────────────────────────────────────────────────────────
    # Lottery
    # ─────────────────────────────────────────────────────────────────────────

    def _capture_lottery(self, set_index: int):
        if not self.roi_lottery:
            messagebox.showwarning("No region", "Select the lottery region first.")
            return
        s = self.model.sets[set_index]
        if not s.rows:
            messagebox.showwarning("No data", f"{s.name} has no values yet.")
            return

        expected = LOTTERY_COUNTS[set_index]
        self._status(f"Reading {LOTTERY_NAMES[set_index]} numbers (expecting {expected})…")
        numbers = ocr_lottery_numbers(self.roi_lottery, expected_count=expected)
        if len(numbers) < 2:
            messagebox.showerror("OCR Error",
                f"Could not read {LOTTERY_NAMES[set_index]} numbers.\n"
                f"Expected {expected}, detected: {numbers}\n\n"
                "Tips:\n"
                "• Make sure the hearts/numbers are fully visible\n"
                "• Try selecting a tighter region around just the hearts\n"
                "• Avoid including the TV frame border")
            return
        if len(numbers) != expected:
            if not messagebox.askyesno("Partial read",
                f"Expected {expected} numbers but detected {len(numbers)}: {numbers}\n\n"
                "Use these anyway?"):
                return

        s.set_lottery(numbers)
        self._refresh_tree(set_index)
        self._update_summary()
        self.notebook.select(set_index)
        self._status(f"🎱 {LOTTERY_NAMES[set_index]}: {numbers} → highlighted in {s.name}.")

    # ─────────────────────────────────────────────────────────────────────────
    # Tree rendering
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_tree(self, si: int):
        tree = self.trees[si]
        s    = self.model.sets[si]
        for item in tree.get_children():
            tree.delete(item)
        for row in s.rows:
            rank_txt    = ""
            if row.rank_top    is not None: rank_txt = f"▲ Top {row.rank_top} (highest)"
            elif row.rank_bottom is not None: rank_txt = f"▼ Bot {row.rank_bottom} (lowest)"
            lottery_txt = "★ HIT" if row.lottery_hit else ""
            tag = f"r{si}_{row.index}"
            tree.insert("", tk.END,
                        values=(row.index, f"{row.value:.2f}", rank_txt, lottery_txt),
                        tags=(tag,))
            bg, fg = s.color_for_row(row)
            tree.tag_configure(tag, background=bg, foreground=fg)

    def _update_summary(self):
        lines = []
        for i, s in enumerate(self.model.sets):
            hits = [r for r in s.rows if r.lottery_hit]
            if not hits: continue
            lines.append(f"── {LOTTERY_NAMES[i]} ({s.name}) ──")
            for r in hits:
                note = (f"  ▲ Top {r.rank_top}"    if r.rank_top
                        else f"  ▼ Bot {r.rank_bottom}" if r.rank_bottom else "")
                lines.append(f"  Row {r.index:>3}  →  {r.value:.2f}{note}")
            lines.append("")
        txt = "\n".join(lines) if lines else "No lottery numbers captured yet."
        self.summary_box.config(state=tk.NORMAL)
        self.summary_box.delete("1.0", tk.END)
        self.summary_box.insert(tk.END, txt)
        self.summary_box.config(state=tk.DISABLED)

    # ─────────────────────────────────────────────────────────────────────────
    # Export
    # ─────────────────────────────────────────────────────────────────────────

    def _export_log(self):
        si = self.log_notebook.index(self.log_notebook.select())
        s  = self.model.sets[si]
        entries = self.log_panels[si].get_entries()
        if not entries:
            messagebox.showinfo("Empty", "No log entries for this set yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"log_{s.name.replace(' ','_').lower()}.txt",
            title="Export log")
        if not path: return
        export_log(s, entries, path)
        self._status(f"Log exported: {path}")
        messagebox.showinfo("Exported", f"Log saved to:\n{path}")

    def _export_csv(self):
        import csv
        import time as _time
        if not any(s.rows for s in self.model.sets):
            messagebox.showinfo("No data", "No values captured yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"melate_results_{_time.strftime('%Y%m%d_%H%M%S')}.csv",
            title="Export CSV")
        if not path:
            return
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Set", "Lottery", "Sequence", "Value",
                             "Top10", "Bottom10", "Winner"])
            for si, s in enumerate(self.model.sets):
                for row in s.rows:
                    writer.writerow([
                        s.name,
                        LOTTERY_NAMES[si],
                        row.index,
                        f"{row.value:.2f}",
                        f"Top {row.rank_top}"       if row.rank_top    else "",
                        f"Bottom {row.rank_bottom}" if row.rank_bottom else "",
                        "YES" if row.lottery_hit else "",
                    ])
            writer.writerow([])
            writer.writerow(["--- LOTTERY WINNERS SUMMARY ---"])
            writer.writerow(["Lottery", "Sequence", "Value", "Top10", "Bottom10"])
            for si, s in enumerate(self.model.sets):
                for row in (r for r in s.rows if r.lottery_hit):
                    writer.writerow([
                        LOTTERY_NAMES[si],
                        row.index,
                        f"{row.value:.2f}",
                        f"Top {row.rank_top}"       if row.rank_top    else "",
                        f"Bottom {row.rank_bottom}" if row.rank_bottom else "",
                    ])
        self._status(f"CSV exported: {path}")
        messagebox.showinfo("Exported", f"CSV saved to:\n{path}")

    # ─────────────────────────────────────────────────────────────────────────
    # Presets
    # ─────────────────────────────────────────────────────────────────────────

    def _save_preset(self):
        if not self.roi_value:
            messagebox.showwarning("Missing", "Set the value region first.")
            return
        name = simpledialog.askstring("Save Preset", "Preset name:", parent=self.root)
        if not name: return
        save_preset(name, self.roi_value, self.roi_lottery)
        self._refresh_presets()
        self._status(f"Preset '{name}' saved.")

    def _load_preset(self):
        name = self.preset_var.get().strip()
        if not name:
            messagebox.showwarning("No preset", "Select a preset from the dropdown.")
            return
        data = load_preset(name)
        if not data:
            messagebox.showerror("Not found", f"Preset '{name}' not found.")
            return
        self.roi_value = data["value"]
        self.lbl_value_roi.config(
            text=f"Value ROI: {self.roi_value['left']},{self.roi_value['top']}  "
                 f"{self.roi_value['width']}×{self.roi_value['height']}",
            fg=ACCENT)
        if data.get("lottery"):
            self.roi_lottery = data["lottery"]
            self.lbl_lottery_roi.config(
                text=f"Lottery ROI: {self.roi_lottery['left']},{self.roi_lottery['top']}  "
                     f"{self.roi_lottery['width']}×{self.roi_lottery['height']}",
                fg=GOLD)
        self._status(f"Preset '{name}' loaded.")

    def _delete_preset(self):
        name = self.preset_var.get().strip()
        if not name: return
        if messagebox.askyesno("Delete", f"Delete preset '{name}'?"):
            delete_preset(name)
            self._refresh_presets()

    def _refresh_presets(self):
        names = preset_names()
        self.preset_cb["values"] = names
        if names: self.preset_var.set(names[0])

    def _reset_all(self):
        if messagebox.askyesno("Reset", "Clear ALL data and logs from all 3 sets?"):
            self._stop_reading()
            self.model.reset()
            self._logs = [[], [], []]
            for i in range(3):
                self._refresh_tree(i)
                self.tab_labels[i].set("0 / 56 values")
                self.log_panels[i].clear()
            self._update_summary()
            self._status("Reset complete.")

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, color=None) -> tk.Button:
        return tk.Button(parent, text=text, command=cmd,
                         bg="#2A2A3E", fg=FG, activebackground="#3A3A5E",
                         activeforeground=FG, relief=tk.FLAT, font=UI_F,
                         cursor="hand2", padx=6, pady=4,
                         highlightbackground=color or "#444",
                         highlightthickness=1)

    def _style_widgets(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TNotebook",        background=BG,  borderwidth=0)
        s.configure("TNotebook.Tab",    background=BG3, foreground=FG,
                    padding=[10, 5],    font=UI_F)
        s.map("TNotebook.Tab",
              background=[("selected", BG2)],
              foreground=[("selected", ACCENT)])
        s.configure("Treeview",         background=BG2, foreground=FG,
                    fieldbackground=BG2, rowheight=25,  font=MONO)
        s.configure("Treeview.Heading", background=BG3, foreground=ACCENT,
                    font=("Segoe UI", 10, "bold"))
        s.map("Treeview", background=[("selected", "#3A3A5E")])

    def _status(self, msg: str):
        self.status_var.set(msg)
        self.root.update_idletasks()
