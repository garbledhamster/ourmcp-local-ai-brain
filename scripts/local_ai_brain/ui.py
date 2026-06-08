from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, VERTICAL, X, Y, BooleanVar, IntVar, StringVar, Tk, ttk
import tkinter as tk
from tkinter import messagebox


SCRIPT_ROOT = Path(__file__).resolve().parent


def find_nearest_brain_db() -> Path:
    explicit_db = os.environ.get("LOCAL_AI_BRAIN_DB", "").strip()
    if explicit_db:
        explicit_path = Path(explicit_db).expanduser().resolve()
        if explicit_path.is_file():
            return explicit_path
        raise FileNotFoundError(f"LOCAL_AI_BRAIN_DB does not exist: {explicit_path}")

    explicit_home = os.environ.get("LOCAL_AI_BRAIN_HOME", "").strip()
    if explicit_home:
        candidate = Path(explicit_home).expanduser().resolve() / "brain.db"
        if candidate.is_file():
            return candidate

    seen: set[Path] = set()
    for start in (Path.cwd(), SCRIPT_ROOT):
        current = start.resolve()
        while current not in seen:
            seen.add(current)
            for candidate in brain_db_candidates(current):
                if candidate.is_file():
                    return candidate.resolve()
            parent = current.parent
            if parent == current:
                break
            current = parent

    details = f" LOCAL_AI_BRAIN_HOME did not contain brain.db: {candidate.parent}." if explicit_home else ""
    raise FileNotFoundError(f"Could not find a nearby brain.db. Run from a project folder or set LOCAL_AI_BRAIN_DB.{details}")


def brain_db_candidates(directory: Path) -> list[Path]:
    return [
        directory / "brain.db",
        directory / "brain" / "brain.db",
        directory / ".tools" / "local-ai-brain-data" / "brain.db",
        directory / ".agents" / "data" / "local-ai-brain" / "brain.db",
    ]


def project_root_for_db(database: Path) -> Path:
    if database.parent.name.lower() == "brain":
        return database.parent.parent
    return database.parent


BRAIN_DB_PATH = find_nearest_brain_db()
BRAIN_HOME = BRAIN_DB_PATH.parent
PROJECT_ROOT = project_root_for_db(BRAIN_DB_PATH)
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
TOOL_ROOT = SCRIPTS_DIR if (SCRIPTS_DIR / "local_ai_brain").is_dir() else SCRIPT_ROOT
os.environ["LOCAL_AI_BRAIN_HOME"] = str(BRAIN_HOME)
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from local_ai_brain.db import connect, init_db, insert_event, json_dumps, search_records
from local_ai_brain.paths import db_path, ensure_runtime_dirs


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def decode_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def event_body(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


class BrainGui:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Local AI Brain")
        self.root.geometry("1280x820")
        self.auto_refresh = BooleanVar(value=True)
        self.refresh_ms = IntVar(value=1500)
        self.status = StringVar(value="")
        self.selected_record = None
        self.selected_event = None
        self.output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        ensure_runtime_dirs()
        with connect(db_path()) as con:
            init_db(con)
        self.build_ui()
        self._tab_history: list[str] = []
        self._active_tab = ""
        if self.tabs.winfo_children():
            self._active_tab = self.tabs.select()
            self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.refresh_all()
        self.root.after(250, self.drain_process_output)
        self.root.after(self.refresh_ms.get(), self.auto_refresh_tick)

    def build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=(10, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="DB").grid(row=0, column=0, sticky="w")
        ttk.Label(top, text=str(db_path())).grid(row=0, column=1, sticky="w", padx=(8, 16))
        ttk.Checkbutton(top, text="Live refresh", variable=self.auto_refresh).grid(row=0, column=2, sticky="e")
        ttk.Button(top, text="Refresh", command=self.refresh_all).grid(row=0, column=3, padx=(8, 0))

        self.tabs = ttk.Notebook(self.root)
        self.tabs.grid(row=1, column=0, sticky="nsew")
        self.records_tab = ttk.Frame(self.tabs)
        self.live_tab = ttk.Frame(self.tabs)
        self.command_tab = ttk.Frame(self.tabs)
        self.events_tab = ttk.Frame(self.tabs)
        self.runs_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.records_tab, text="Records")
        self.tabs.add(self.live_tab, text="Live Use")
        self.tabs.add(self.command_tab, text="Codex / Commands")
        self.tabs.add(self.events_tab, text="Events")
        self.tabs.add(self.runs_tab, text="Runs")

        self.build_records_tab()
        self.build_live_tab()
        self.build_command_tab()
        self.build_events_tab()
        self.build_runs_tab()

        bottom = ttk.Frame(self.root, padding=(10, 6))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status).grid(row=0, column=0, sticky="w")
        command_panel = ttk.LabelFrame(bottom, text="Command palette", padding=8)
        command_panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        command_panel.columnconfigure(1, weight=1)
        ttk.Label(command_panel, text="Back: select previous tab  |  Help: command help  |  Quit: close window").grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
        )
        self.command_input = StringVar(value="")
        ttk.Label(command_panel, text="Command").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(command_panel, textvariable=self.command_input).grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(6, 0))
        ttk.Button(command_panel, text="Back", command=self.select_previous_tab).grid(row=1, column=2, pady=(6, 0))
        ttk.Button(command_panel, text="Help", command=self.show_help_dialog).grid(row=1, column=3, padx=(6, 6), pady=(6, 0))
        ttk.Button(command_panel, text="Quit", command=self.root.destroy).grid(row=1, column=4, pady=(6, 0))
        ttk.Button(command_panel, text="Run", command=self.run_command_palette).grid(row=1, column=5, padx=(6, 0), pady=(6, 0))

    def _on_tab_changed(self, _event=None) -> None:
        selected = self.tabs.select()
        if selected == self._active_tab or not selected:
            return
        if self._active_tab:
            self._tab_history.append(self._active_tab)
        self._active_tab = selected

    def select_previous_tab(self) -> None:
        while self._tab_history:
            previous = self._tab_history.pop()
            if previous and previous in self.tabs.tabs():
                self.tabs.select(previous)
                return
        self.tabs.select(self.records_tab)

    def show_help_dialog(self) -> None:
        messagebox.showinfo(
            "Local AI Brain command palette",
            "Commands:\n- b or back: navigate to previous tab\n- h or help: show this help\n- q or quit: close application",
        )

    def run_command_palette(self) -> None:
        command = self.command_input.get().strip()
        if not command:
            return
        if command.lower() in {"b", "back"}:
            self.select_previous_tab()
            return
        if command.lower() in {"h", "help"}:
            self.show_help_dialog()
            return
        if command.lower() in {"q", "quit"}:
            self.root.destroy()
            return
        self.command_var.set(command)
        self.start_command()

    def build_records_tab(self) -> None:
        self.records_tab.columnconfigure(0, weight=1)
        self.records_tab.rowconfigure(1, weight=1)
        filters = ttk.Frame(self.records_tab, padding=8)
        filters.grid(row=0, column=0, sticky="ew")
        filters.columnconfigure(1, weight=1)
        self.query_var = StringVar()
        self.repo_var = StringVar()
        self.surface_var = StringVar()
        self.limit_var = StringVar(value="50")
        ttk.Label(filters, text="Query").grid(row=0, column=0, sticky="w")
        ttk.Entry(filters, textvariable=self.query_var).grid(row=0, column=1, sticky="ew", padx=(6, 12))
        ttk.Label(filters, text="Repo").grid(row=0, column=2, sticky="w")
        ttk.Entry(filters, textvariable=self.repo_var, width=26).grid(row=0, column=3, sticky="ew", padx=(6, 12))
        ttk.Label(filters, text="Surface").grid(row=0, column=4, sticky="w")
        ttk.Entry(filters, textvariable=self.surface_var, width=20).grid(row=0, column=5, padx=(6, 12))
        ttk.Label(filters, text="Limit").grid(row=0, column=6, sticky="w")
        ttk.Entry(filters, textvariable=self.limit_var, width=6).grid(row=0, column=7, padx=(6, 12))
        ttk.Button(filters, text="Search", command=self.search_records).grid(row=0, column=8)
        ttk.Button(filters, text="Context Pack", command=self.context_pack_from_filters).grid(row=0, column=9, padx=(8, 0))

        panes = ttk.PanedWindow(self.records_tab, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.records_tree = ttk.Treeview(
            left,
            columns=("id", "type", "title", "repo", "surface", "updated"),
            show="headings",
            selectmode="browse",
        )
        for col, width in (("id", 60), ("type", 90), ("title", 320), ("repo", 220), ("surface", 150), ("updated", 170)):
            self.records_tree.heading(col, text=col.title())
            self.records_tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(left, orient=VERTICAL, command=self.records_tree.yview)
        self.records_tree.configure(yscrollcommand=scroll.set)
        self.records_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.records_tree.bind("<<TreeviewSelect>>", self.on_record_select)

        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        buttons = ttk.Frame(right)
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(buttons, text="Load Raw", command=lambda: self.load_selected_record_path("raw_path")).pack(side=LEFT)
        ttk.Button(buttons, text="Load Scrubbed", command=lambda: self.load_selected_record_path("scrubbed_path")).pack(side=LEFT, padx=(6, 0))
        ttk.Button(buttons, text="Load Distilled", command=lambda: self.load_selected_record_path("distilled_path")).pack(side=LEFT, padx=(6, 0))
        ttk.Button(buttons, text="Open Artifact", command=self.open_selected_artifact).pack(side=LEFT, padx=(6, 0))
        self.record_text = tk.Text(right, wrap="word", height=12)
        self.record_text.grid(row=1, column=0, sticky="nsew")

    def build_live_tab(self) -> None:
        self.live_tab.columnconfigure(0, weight=1)
        self.live_tab.rowconfigure(1, weight=1)
        summary = ttk.Frame(self.live_tab, padding=8)
        summary.grid(row=0, column=0, sticky="ew")
        self.live_summary = StringVar(value="")
        ttk.Label(summary, textvariable=self.live_summary).grid(row=0, column=0, sticky="w")
        self.live_tree = ttk.Treeview(
            self.live_tab,
            columns=("id", "time", "type", "summary", "repo", "surface"),
            show="headings",
            selectmode="browse",
        )
        for col, width in (("id", 70), ("time", 170), ("type", 160), ("summary", 520), ("repo", 240), ("surface", 160)):
            self.live_tree.heading(col, text=col.title())
            self.live_tree.column(col, width=width, anchor="w")
        self.live_tree.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.live_tree.bind("<Double-1>", lambda _event: self.tabs.select(self.events_tab))

    def build_command_tab(self) -> None:
        self.command_tab.columnconfigure(0, weight=1)
        self.command_tab.rowconfigure(3, weight=1)
        lookup = ttk.LabelFrame(self.command_tab, text="Brain Lookup", padding=8)
        lookup.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        lookup.columnconfigure(1, weight=1)
        self.command_query_var = StringVar()
        ttk.Label(lookup, text="Query").grid(row=0, column=0, sticky="w")
        ttk.Entry(lookup, textvariable=self.command_query_var).grid(row=0, column=1, sticky="ew", padx=(6, 8))
        ttk.Button(lookup, text="Show Context Pack", command=self.command_context_pack).grid(row=0, column=2)
        ttk.Button(lookup, text="Search Records", command=self.command_search_records).grid(row=0, column=3, padx=(8, 0))

        run = ttk.LabelFrame(self.command_tab, text="External Command", padding=8)
        run.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        run.columnconfigure(1, weight=1)
        default_command = 'python -m local_ai_brain context-pack --query "local-ai-brain gui"'
        self.command_var = StringVar(value=default_command)
        ttk.Label(run, text="Command").grid(row=0, column=0, sticky="w")
        ttk.Entry(run, textvariable=self.command_var).grid(row=0, column=1, sticky="ew", padx=(6, 8))
        ttk.Button(run, text="Start", command=self.start_command).grid(row=0, column=2)
        ttk.Button(run, text="Stop", command=self.stop_command).grid(row=0, column=3, padx=(8, 0))

        self.command_output = tk.Text(self.command_tab, wrap="word")
        self.command_output.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))

    def build_events_tab(self) -> None:
        self.events_tab.columnconfigure(0, weight=1)
        self.events_tab.rowconfigure(1, weight=1)
        filters = ttk.Frame(self.events_tab, padding=8)
        filters.grid(row=0, column=0, sticky="ew")
        self.event_filter_var = StringVar()
        ttk.Label(filters, text="Type contains").pack(side=LEFT)
        ttk.Entry(filters, textvariable=self.event_filter_var, width=28).pack(side=LEFT, padx=(6, 8))
        ttk.Button(filters, text="Refresh", command=self.refresh_events).pack(side=LEFT)

        panes = ttk.PanedWindow(self.events_tab, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.events_tree = ttk.Treeview(
            left,
            columns=("id", "time", "type", "summary", "repo", "surface"),
            show="headings",
            selectmode="browse",
        )
        for col, width in (("id", 70), ("time", 170), ("type", 160), ("summary", 440), ("repo", 220), ("surface", 140)):
            self.events_tree.heading(col, text=col.title())
            self.events_tree.column(col, width=width, anchor="w")
        escroll = ttk.Scrollbar(left, orient=VERTICAL, command=self.events_tree.yview)
        self.events_tree.configure(yscrollcommand=escroll.set)
        self.events_tree.grid(row=0, column=0, sticky="nsew")
        escroll.grid(row=0, column=1, sticky="ns")
        self.events_tree.bind("<<TreeviewSelect>>", self.on_event_select)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.event_text = tk.Text(right, wrap="word")
        self.event_text.grid(row=0, column=0, sticky="nsew")

    def build_runs_tab(self) -> None:
        self.runs_tab.columnconfigure(0, weight=1)
        self.runs_tab.rowconfigure(0, weight=1)
        self.runs_tree = ttk.Treeview(
            self.runs_tab,
            columns=("id", "run_uid", "status", "goal", "repo", "surface", "updated"),
            show="headings",
            selectmode="browse",
        )
        for col, width in (("id", 60), ("run_uid", 260), ("status", 90), ("goal", 360), ("repo", 220), ("surface", 140), ("updated", 170)):
            self.runs_tree.heading(col, text=col.title())
            self.runs_tree.column(col, width=width, anchor="w")
        self.runs_tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

    def connect(self):
        con = connect(db_path())
        init_db(con)
        return con

    def refresh_all(self) -> None:
        self.search_records(record_event=False)
        self.refresh_events()
        self.refresh_live()
        self.refresh_runs()
        self.status.set(f"Refreshed {now_iso()}")

    def search_records(self, record_event: bool = True) -> None:
        try:
            limit = max(1, int(self.limit_var.get() or "50"))
        except ValueError:
            limit = 50
        with self.connect() as con:
            rows = search_records(
                con,
                query=self.query_var.get(),
                repo_path=self.repo_var.get(),
                target_surface=self.surface_var.get(),
                limit=limit,
            )
            if record_event:
                self.record_event(
                    con,
                    "gui_search",
                    f"GUI search: {len(rows)} result(s) for {self.query_var.get() or 'latest'}",
                    {"query": self.query_var.get(), "repo": self.repo_var.get(), "surface": self.surface_var.get(), "result_count": len(rows)},
                )
        self.populate_records(rows)
        self.refresh_events()
        self.refresh_live()

    def populate_records(self, rows) -> None:
        self.records_tree.delete(*self.records_tree.get_children())
        for row in rows:
            values = (row["id"], row["artifact_type"], row["ticket_title"] or row["summary"], row["repo_path"], row["target_surface"], row["updated_at"])
            self.records_tree.insert("", END, iid=str(row["id"]), values=values)

    def on_record_select(self, _event=None) -> None:
        selected = self.records_tree.selection()
        if not selected:
            return
        record_id = selected[0]
        with self.connect() as con:
            row = con.execute("SELECT * FROM memory_records WHERE id = ?", (record_id,)).fetchone()
        if not row:
            return
        self.selected_record = dict(row)
        self.record_text.delete("1.0", END)
        tags = ", ".join(decode_json_list(row["tags_json"]))
        files = ", ".join(decode_json_list(row["related_files_json"]))
        self.record_text.insert(
            END,
            "\n".join(
                [
                    f"Title: {row['ticket_title']}",
                    f"Type: {row['artifact_type']}",
                    f"Status: {row['status']}",
                    f"Repo: {row['repo_path']}",
                    f"Surface: {row['target_surface']}",
                    f"Updated: {row['updated_at']}",
                    f"Tags: {tags}",
                    f"Related files: {files}",
                    "",
                    "Summary:",
                    row["summary"],
                    "",
                    "Search text:",
                    row["search_text"],
                ]
            ),
        )

    def load_selected_record_path(self, key: str) -> None:
        if not self.selected_record:
            return
        path = Path(self.selected_record.get(key, ""))
        self.record_text.delete("1.0", END)
        if not path.exists():
            self.record_text.insert(END, f"Missing file: {path}")
            return
        self.record_text.insert(END, path.read_text(encoding="utf-8", errors="replace"))

    def open_selected_artifact(self) -> None:
        if not self.selected_record:
            return
        path = Path(self.selected_record.get("artifact_path", ""))
        if not path.exists():
            messagebox.showwarning("Missing file", str(path))
            return
        os.startfile(path)

    def context_pack_from_filters(self) -> None:
        self.command_query_var.set(self.query_var.get())
        self.tabs.select(self.command_tab)
        self.command_context_pack()

    def command_context_pack(self) -> None:
        query = self.command_query_var.get()
        with self.connect() as con:
            rows = search_records(con, query=query, limit=10)
            items = [dict(row) for row in rows]
            self.record_event(
                con,
                "gui_context_pack",
                f"GUI context pack: {len(rows)} result(s) for {query or 'latest'}",
                {"query": query, "result_count": len(rows), "record_ids": [row["id"] for row in rows]},
            )
        lines = ["# GUI Context Pack", "", f"- Query: {query or 'latest'}", f"- Results: {len(items)}", ""]
        for index, item in enumerate(items, start=1):
            lines.extend(
                [
                    f"## {index}. {item['ticket_title'] or item['artifact_type']}",
                    "",
                    f"- Status: {item['status']}",
                    f"- Summary: {item['summary']}",
                    f"- Artifact: {item['artifact_path']}",
                    f"- Scrubbed: {item['scrubbed_path']}",
                    f"- Distilled: {item['distilled_path']}",
                    "",
                ]
            )
        self.command_output.delete("1.0", END)
        self.command_output.insert(END, "\n".join(lines))
        self.refresh_events()
        self.refresh_live()

    def command_search_records(self) -> None:
        self.query_var.set(self.command_query_var.get())
        self.tabs.select(self.records_tab)
        self.search_records()

    def start_command(self) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Command running", "Stop the current command before starting another.")
            return
        command = self.command_var.get().strip()
        if not command:
            return
        self.command_output.delete("1.0", END)
        with self.connect() as con:
            self.record_event(con, "gui_command_start", f"Started command: {command}", {"command": command, "cwd": str(TOOL_ROOT)})
        env = os.environ.copy()
        env["PYTHONPATH"] = str(TOOL_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        self.process = subprocess.Popen(
            command,
            cwd=str(TOOL_ROOT),
            env=env,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        threading.Thread(target=self.read_process_output, args=(command,), daemon=True).start()
        self.refresh_events()
        self.refresh_live()

    def read_process_output(self, command: str) -> None:
        assert self.process is not None
        lines = []
        if self.process.stdout:
            for line in self.process.stdout:
                lines.append(line)
                self.output_queue.put(("line", line))
        code = self.process.wait()
        output = "".join(lines)
        self.output_queue.put(("done", f"\n[exit {code}]\n"))
        with self.connect() as con:
            self.record_event(
                con,
                "gui_command_finish",
                f"Command finished ({code}): {command}",
                {"command": command, "exit_code": code, "output_tail": output[-8000:]},
            )

    def drain_process_output(self) -> None:
        try:
            while True:
                kind, text = self.output_queue.get_nowait()
                self.command_output.insert(END, text)
                self.command_output.see(END)
                if kind == "done":
                    self.refresh_events()
                    self.refresh_live()
        except queue.Empty:
            pass
        self.root.after(250, self.drain_process_output)

    def stop_command(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            with self.connect() as con:
                self.record_event(con, "gui_command_stop", "Stopped running command", {})

    def refresh_events(self) -> None:
        filter_text = self.event_filter_var.get().strip()
        sql = "SELECT * FROM events"
        params: list[str] = []
        if filter_text:
            sql += " WHERE event_type LIKE ?"
            params.append(f"%{filter_text}%")
        sql += " ORDER BY id DESC LIMIT 250"
        with self.connect() as con:
            rows = con.execute(sql, params).fetchall()
        self.events_tree.delete(*self.events_tree.get_children())
        for row in rows:
            self.events_tree.insert(
                "",
                END,
                iid=str(row["id"]),
                values=(row["id"], row["created_at"], row["event_type"], row["summary"], row["repo_path"], row["target_surface"]),
            )

    def refresh_live(self) -> None:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM events WHERE event_type LIKE 'brain_%' OR event_type LIKE 'gui_%' ORDER BY id DESC LIMIT 250"
            ).fetchall()
            record_count = con.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0]
            event_count = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            run_count = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.live_tree.delete(*self.live_tree.get_children())
        for row in rows:
            self.live_tree.insert(
                "",
                END,
                iid=f"live-{row['id']}",
                values=(row["id"], row["created_at"], row["event_type"], row["summary"], row["repo_path"], row["target_surface"]),
            )
        self.live_summary.set(f"{record_count} records | {event_count} events | {run_count} runs | {len(rows)} live-use events shown")

    def refresh_runs(self) -> None:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM runs ORDER BY updated_at DESC LIMIT 250").fetchall()
        self.runs_tree.delete(*self.runs_tree.get_children())
        for row in rows:
            self.runs_tree.insert(
                "",
                END,
                iid=f"run-{row['id']}",
                values=(row["id"], row["run_uid"], row["status"], row["goal"], row["repo_path"], row["target_surface"], row["updated_at"]),
            )

    def on_event_select(self, _event=None) -> None:
        selected = self.events_tree.selection()
        if not selected:
            return
        event_id = selected[0]
        with self.connect() as con:
            row = con.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return
        self.selected_event = dict(row)
        self.event_text.delete("1.0", END)
        self.event_text.insert(
            END,
            "\n".join(
                [
                    f"ID: {row['id']}",
                    f"Type: {row['event_type']}",
                    f"Created: {row['created_at']}",
                    f"Repo: {row['repo_path']}",
                    f"Surface: {row['target_surface']}",
                    f"Summary: {row['summary']}",
                    "",
                    "Body:",
                    row["body"],
                ]
            ),
        )

    def record_event(self, con, event_type: str, summary: str, body: object) -> None:
        insert_event(
            con,
            {
                "event_uid": str(uuid.uuid4()),
                "created_at": now_iso(),
                "event_type": event_type,
                "summary": summary,
                "body": event_body(body),
                "tags_json": json_dumps(["gui", event_type]),
                "related_files_json": json_dumps([]),
            },
        )

    def auto_refresh_tick(self) -> None:
        if self.auto_refresh.get():
            current = self.tabs.select()
            if current == str(self.events_tab):
                self.refresh_events()
            elif current == str(self.live_tab):
                self.refresh_live()
            elif current == str(self.runs_tab):
                self.refresh_runs()
            else:
                self.refresh_live()
        self.root.after(max(500, self.refresh_ms.get()), self.auto_refresh_tick)


def main() -> int:
    root = Tk()
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    BrainGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
