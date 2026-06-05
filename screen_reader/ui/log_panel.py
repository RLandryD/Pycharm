"""
ui/log_panel.py
Per-set live log panel.
Shows every in-range deduplicated reading in real time.
User can pause, click entries to mark them, assign sequence numbers.
"""

import tkinter as tk
from tkinter import ttk, simpledialog
from core.live_reader import LogEntry

# Status colours
STATUS_COLORS = {
    "READING":           ("#2A2A3E", "#CCCCCC"),
    "CAPTURED":          ("#1A4A1A", "#88FF88"),
    "WAITING_DEPARTURE": ("#3A2A0A", "#FFAA44"),
}
MARK_COLORS = {
    "final":      ("#1A3A1A", "#88FF88"),
    "transition": ("#3A1A1A", "#FF8888"),
}


class LogPanel(tk.Frame):
    """
    Drop-in frame that holds the live log for one set.
    """

    def __init__(self, parent, set_name: str, on_pause_toggle, **kwargs):
        super().__init__(parent, bg="#12121F", **kwargs)
        self._set_name        = set_name
        self._on_pause_toggle = on_pause_toggle
        self._entries: list[LogEntry] = []
        self._selected_iid: str | None = None
        self._auto_scroll = True

        self._build()

    # ── Construction ──────────────────────────────────────────────────────────

    def _build(self):
        BG   = "#12121F"
        BG2  = "#1A1A2E"
        FG   = "#E0E0E0"
        MONO = ("Courier New", 10)
        UI_F = ("Segoe UI", 10)

        # ── Top bar
        top = tk.Frame(self, bg=BG)
        top.pack(fill=tk.X, padx=6, pady=(6, 2))

        tk.Label(top, text=f"Live Log — {self._set_name}",
                 bg=BG, fg="#00C896", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)

        self._pause_btn = tk.Button(
            top, text="⏸ Pause Log", command=self._toggle_pause,
            bg="#2A2A3E", fg=FG, relief=tk.FLAT, font=UI_F,
            cursor="hand2", padx=8, pady=3,
            highlightbackground="#FF9944", highlightthickness=1)
        self._pause_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self._autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text="Auto-scroll", variable=self._autoscroll_var,
                       bg=BG, fg=FG, selectcolor=BG2,
                       activebackground=BG, activeforeground=FG,
                       font=UI_F, command=self._on_autoscroll_toggle).pack(
            side=tk.RIGHT, padx=8)

        self._paused_lbl = tk.Label(top, text="", bg=BG,
                                     fg="#FF9944", font=("Segoe UI", 10, "bold"))
        self._paused_lbl.pack(side=tk.RIGHT, padx=8)

        # ── Legend
        leg = tk.Frame(self, bg=BG)
        leg.pack(fill=tk.X, padx=6, pady=(0, 2))
        for text, color in [("● READING", "#8888CC"),
                             ("● CAPTURED", "#88FF88"),
                             ("● WAITING", "#FFAA44"),
                             ("★ FINAL", "#00FF88"),
                             ("✕ TRANSITION", "#FF6666")]:
            tk.Label(leg, text=text, bg=BG, fg=color,
                     font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=6)

        # ── Treeview
        cols = ("time", "value", "status", "mark", "seq")
        self._tree = ttk.Treeview(self, columns=cols, show="headings",
                                   selectmode="browse", height=12)
        self._tree.heading("time",   text="Time")
        self._tree.heading("value",  text="Value")
        self._tree.heading("status", text="Status")
        self._tree.heading("mark",   text="Mark")
        self._tree.heading("seq",    text="Seq#")
        self._tree.column("time",   width=75,  anchor="center")
        self._tree.column("value",  width=80,  anchor="center")
        self._tree.column("status", width=165, anchor="w")
        self._tree.column("mark",   width=110, anchor="center")
        self._tree.column("seq",    width=55,  anchor="center")

        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 4))
        self._tree.pack(fill=tk.BOTH, expand=True, padx=(6, 0))

        # ── Mark buttons
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill=tk.X, padx=6, pady=4)

        def _btn(text, cmd, color):
            return tk.Button(btn_row, text=text, command=cmd,
                             bg="#2A2A3E", fg=FG, relief=tk.FLAT,
                             font=UI_F, cursor="hand2", padx=8, pady=3,
                             highlightbackground=color, highlightthickness=1)

        _btn("★ Mark Final",      self._mark_final,      "#00FF88").pack(side=tk.LEFT, padx=3)
        _btn("✕ Mark Transition", self._mark_transition, "#FF6666").pack(side=tk.LEFT, padx=3)
        _btn("# Assign Seq No",   self._assign_seq,      "#88AAFF").pack(side=tk.LEFT, padx=3)
        _btn("✖ Clear Mark",      self._clear_mark,      "#888888").pack(side=tk.LEFT, padx=3)

        self._count_lbl = tk.Label(btn_row, text="0 entries",
                                    bg=BG, fg="#888888", font=UI_F)
        self._count_lbl.pack(side=tk.RIGHT, padx=6)

        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Double-1>",         self._on_double_click)

        self._apply_tree_style()

    def _apply_tree_style(self):
        style = ttk.Style()
        style.configure("Treeview",
                        background="#1A1A2E", foreground="#CCCCCC",
                        fieldbackground="#1A1A2E", rowheight=22,
                        font=("Courier New", 10))
        style.configure("Treeview.Heading",
                        background="#22223A", foreground="#00C896",
                        font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#3A3A5E")])

    # ── Public API ────────────────────────────────────────────────────────────

    def add_entry(self, entry: LogEntry):
        """Called from main thread when a new log entry arrives."""
        self._entries.append(entry)
        self._insert_row(entry)
        self._count_lbl.config(text=f"{len(self._entries)} entries")
        if self._autoscroll_var.get():
            children = self._tree.get_children()
            if children:
                self._tree.see(children[-1])

    def clear(self):
        self._entries.clear()
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._count_lbl.config(text="0 entries")

    def get_entries(self) -> list[LogEntry]:
        return list(self._entries)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _insert_row(self, entry: LogEntry):
        t    = f"{entry.timestamp % 10000:.2f}"
        mark = {"final": "★ Final", "transition": "✕ Transition"}.get(entry.user_mark, "—")
        seq  = str(entry.sequence_no) if entry.sequence_no else "—"
        tag  = f"e{len(self._entries)-1}"

        iid = self._tree.insert("", tk.END,
            values=(t, f"{entry.value:.2f}", entry.status, mark, seq),
            tags=(tag,))

        bg, fg = self._row_colors(entry)
        self._tree.tag_configure(tag, background=bg, foreground=fg)
        return iid

    def _row_colors(self, entry: LogEntry) -> tuple[str, str]:
        if entry.user_mark == "final":
            return MARK_COLORS["final"]
        if entry.user_mark == "transition":
            return MARK_COLORS["transition"]
        return STATUS_COLORS.get(entry.status, ("#1A1A2E", "#CCCCCC"))

    def _refresh_row(self, index: int):
        """Re-render a single row after a mark change."""
        children = self._tree.get_children()
        if index >= len(children):
            return
        iid   = children[index]
        entry = self._entries[index]
        mark  = {"final": "★ Final", "transition": "✕ Transition"}.get(entry.user_mark, "—")
        seq   = str(entry.sequence_no) if entry.sequence_no else "—"
        t     = f"{entry.timestamp % 10000:.2f}"
        self._tree.item(iid, values=(t, f"{entry.value:.2f}", entry.status, mark, seq))
        bg, fg = self._row_colors(entry)
        tag = f"e{index}"
        self._tree.tag_configure(tag, background=bg, foreground=fg)

    def _selected_index(self) -> int | None:
        sel = self._tree.selection()
        if not sel:
            return None
        children = self._tree.get_children()
        try:
            return list(children).index(sel[0])
        except ValueError:
            return None

    # ── Marking ───────────────────────────────────────────────────────────────

    def _mark_final(self):
        idx = self._selected_index()
        if idx is None:
            return
        self._entries[idx].user_mark = "final"
        self._refresh_row(idx)

    def _mark_transition(self):
        idx = self._selected_index()
        if idx is None:
            return
        self._entries[idx].user_mark = "transition"
        self._refresh_row(idx)

    def _assign_seq(self):
        idx = self._selected_index()
        if idx is None:
            return
        val = simpledialog.askinteger(
            "Assign Sequence Number",
            f"Sequence number for value {self._entries[idx].value:.2f}  (1–56):",
            minvalue=1, maxvalue=200)
        if val is not None:
            self._entries[idx].sequence_no = val
            self._entries[idx].user_mark   = "final"   # auto-mark as final
            self._refresh_row(idx)

    def _clear_mark(self):
        idx = self._selected_index()
        if idx is None:
            return
        self._entries[idx].user_mark   = ""
        self._entries[idx].sequence_no = 0
        self._refresh_row(idx)

    # ── Pause / resume ────────────────────────────────────────────────────────

    def _toggle_pause(self):
        self._on_pause_toggle()

    def set_paused_state(self, paused: bool):
        if paused:
            self._pause_btn.config(text="▶ Resume Log",
                                   highlightbackground="#00C896")
            self._paused_lbl.config(text="⏸ PAUSED — mark entries then resume")
            self._autoscroll_var.set(False)
        else:
            self._pause_btn.config(text="⏸ Pause Log",
                                   highlightbackground="#FF9944")
            self._paused_lbl.config(text="")

    def _on_autoscroll_toggle(self):
        self._auto_scroll = self._autoscroll_var.get()

    def _on_select(self, _event):
        pass   # selection highlight handled by ttk

    def _on_double_click(self, _event):
        """Double-click cycles through marks for quick tagging."""
        idx = self._selected_index()
        if idx is None:
            return
        current = self._entries[idx].user_mark
        if current == "":
            self._entries[idx].user_mark = "final"
        elif current == "final":
            self._entries[idx].user_mark = "transition"
        else:
            self._entries[idx].user_mark = ""
        self._refresh_row(idx)
