import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
except ModuleNotFoundError:
    FigureCanvasTkAgg = None
    Figure = None


DB_PATH = Path(__file__).with_name("expenses.db")
CATEGORIES = ["Food", "Travel", "Shopping", "Bills", "Health", "Entertainment", "Rent", "Other"]
DATE_PRESETS = ["All Time", "Today", "Last 4 Days", "Last 7 Days", "Last 30 Days", "Custom Range"]


@dataclass
class Expense:
    user_id: int
    expense_date: str
    title: str
    category: str
    amount: float
    notes: str


class ExpenseDatabase:
    def __init__(self, db_path: Path) -> None:
        self.connection = sqlite3.connect(db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_tables()
        self._migrate_expenses_table()
        self.ensure_user("User 1")

    def _create_tables(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                expense_date TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        self.connection.commit()

    def _migrate_expenses_table(self) -> None:
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(expenses)")}
        if "user_id" not in columns:
            self.connection.execute("ALTER TABLE expenses ADD COLUMN user_id INTEGER")
            self.connection.commit()

        default_user_id = self.ensure_user("User 1")
        self.connection.execute("UPDATE expenses SET user_id = ? WHERE user_id IS NULL", (default_user_id,))
        self.connection.commit()

    def ensure_user(self, username: str) -> int:
        clean_name = username.strip()
        if not clean_name:
            raise ValueError("Username cannot be empty.")

        existing = self.connection.execute("SELECT id FROM users WHERE lower(username) = lower(?)", (clean_name,)).fetchone()
        if existing:
            return int(existing["id"])

        cursor = self.connection.execute("INSERT INTO users (username) VALUES (?)", (clean_name,))
        self.connection.commit()
        return int(cursor.lastrowid)

    def fetch_users(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT id, username FROM users ORDER BY lower(username)"))

    def add_expense(self, expense: Expense) -> None:
        self.connection.execute(
            """
            INSERT INTO expenses (user_id, expense_date, title, category, amount, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (expense.user_id, expense.expense_date, expense.title, expense.category, expense.amount, expense.notes),
        )
        self.connection.commit()

    def update_expense(self, expense_id: int, expense: Expense) -> None:
        self.connection.execute(
            """
            UPDATE expenses
            SET user_id = ?, expense_date = ?, title = ?, category = ?, amount = ?, notes = ?
            WHERE id = ?
            """,
            (expense.user_id, expense.expense_date, expense.title, expense.category, expense.amount, expense.notes, expense_id),
        )
        self.connection.commit()

    def delete_expense(self, expense_id: int) -> None:
        self.connection.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        self.connection.commit()

    def _build_filters(
        self,
        user_id: int | None,
        preset: str,
        start_date: str = "",
        end_date: str = "",
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []

        if user_id is not None:
            clauses.append("expenses.user_id = ?")
            params.append(user_id)

        today = datetime.now().date()
        if preset == "Today":
            clauses.append("expense_date = ?")
            params.append(today.strftime("%Y-%m-%d"))
        elif preset == "Last 4 Days":
            clauses.append("expense_date >= ?")
            params.append((today - timedelta(days=3)).strftime("%Y-%m-%d"))
        elif preset == "Last 7 Days":
            clauses.append("expense_date >= ?")
            params.append((today - timedelta(days=6)).strftime("%Y-%m-%d"))
        elif preset == "Last 30 Days":
            clauses.append("expense_date >= ?")
            params.append((today - timedelta(days=29)).strftime("%Y-%m-%d"))
        elif preset == "Custom Range":
            if start_date:
                clauses.append("expense_date >= ?")
                params.append(start_date)
            if end_date:
                clauses.append("expense_date <= ?")
                params.append(end_date)

        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_clause, params

    def fetch_expenses(
        self,
        user_id: int | None,
        preset: str,
        start_date: str = "",
        end_date: str = "",
    ) -> list[sqlite3.Row]:
        where_clause, params = self._build_filters(user_id, preset, start_date, end_date)
        query = f"""
            SELECT expenses.*, users.username
            FROM expenses
            LEFT JOIN users ON users.id = expenses.user_id
            {where_clause}
            ORDER BY expense_date DESC, expenses.id DESC
        """
        return list(self.connection.execute(query, params))

    def summary(
        self,
        user_id: int | None,
        preset: str,
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, object]:
        rows = self.fetch_expenses(user_id, preset, start_date, end_date)
        total = sum(float(row["amount"]) for row in rows)
        category_totals: dict[str, float] = defaultdict(float)
        daily_totals: dict[str, float] = defaultdict(float)

        for row in rows:
            category_totals[row["category"]] += float(row["amount"])
            daily_totals[row["expense_date"]] += float(row["amount"])

        top_category = max(category_totals.items(), key=lambda item: item[1], default=("None", 0.0))
        average = total / len(rows) if rows else 0.0

        return {
            "count": len(rows),
            "total": total,
            "average": average,
            "top_category": top_category[0],
            "category_totals": dict(category_totals),
            "daily_totals": dict(sorted(daily_totals.items())),
        }

    def close(self) -> None:
        self.connection.close()


class AnimatedCard(tk.Frame):
    def __init__(self, master: tk.Misc, title: str, accent: str) -> None:
        super().__init__(master, bg="#12253A", highlightthickness=0)
        self.configure(width=225, height=102)
        self.pack_propagate(False)

        tk.Label(self, text=title, bg="#12253A", fg="#A9BDD2", font=("Segoe UI Semibold", 10)).pack(
            anchor="w", padx=16, pady=(16, 4)
        )
        self.value_label = tk.Label(self, text="0", bg="#12253A", fg="#F7FBFF", font=("Segoe UI Bold", 23))
        self.value_label.pack(anchor="w", padx=16)
        tk.Frame(self, bg=accent, height=4).pack(fill="x", padx=16, pady=(12, 0))

    def animate_to(self, value: str, numeric_target: float | None = None, prefix: str = "") -> None:
        if numeric_target is None:
            self.value_label.config(text=value)
            return

        steps = 22
        increment = numeric_target / steps if steps else numeric_target
        current = 0.0

        def tick(step: int = 0) -> None:
            nonlocal current
            if step >= steps:
                self.value_label.config(text=value)
                return
            current += increment
            shown = f"{prefix}{current:,.0f}" if prefix else f"{current:,.0f}"
            self.value_label.config(text=shown)
            self.after(15, lambda: tick(step + 1))

        tick()


class ExpenseTrackerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.db = ExpenseDatabase(DB_PATH)
        self.selected_expense_id: int | None = None
        self.chart_canvas: FigureCanvasTkAgg | None = None
        self.chart_figure = Figure(figsize=(7, 4.5), dpi=100, facecolor="#08111B") if Figure else None
        self.user_lookup: dict[str, int] = {}

        self.active_user_var = tk.StringVar()
        self.form_user_var = tk.StringVar()
        self.date_preset_var = tk.StringVar(value="Last 7 Days")
        self.start_date_var = tk.StringVar()
        self.end_date_var = tk.StringVar()

        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.title_var = tk.StringVar()
        self.category_var = tk.StringVar(value=CATEGORIES[0])
        self.amount_var = tk.StringVar()

        self.root.title("Smart Expense Tracker")
        self.root.geometry("1380x820")
        self.root.minsize(1180, 720)
        self.root.configure(bg="#08111B")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Control-s>", lambda _event: self.add_expense())
        self.root.bind("<Control-S>", lambda _event: self.add_expense())

        self._build_styles()
        self._build_layout()
        self._load_users()
        self._animate_intro()
        self.refresh_dashboard()

    def _build_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "App.Treeview",
            background="#0D1A2B",
            fieldbackground="#0D1A2B",
            foreground="#F0F5FA",
            rowheight=34,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "App.Treeview.Heading",
            background="#16304A",
            foreground="#B9CDE0",
            relief="flat",
            font=("Segoe UI Semibold", 10),
        )
        style.map("App.Treeview", background=[("selected", "#244B74")], foreground=[("selected", "#FFFFFF")])
        style.configure(
            "App.TCombobox",
            fieldbackground="#102033",
            background="#102033",
            foreground="#F5FAFF",
            arrowcolor="#F5FAFF",
            bordercolor="#284965",
            lightcolor="#102033",
            darkcolor="#102033",
        )

    def _build_layout(self) -> None:
        self.header = tk.Frame(self.root, bg="#08111B")
        self.header.pack(fill="x", padx=26, pady=(22, 12))

        tk.Label(self.header, text="Smart Expense Tracker", bg="#08111B", fg="#F4FAFF", font=("Georgia", 28, "bold")).pack(
            anchor="w"
        )
        tk.Label(
            self.header,
            text="Multiple users, flexible date filters, database-backed history, and animated analytics in one Python app.",
            bg="#08111B",
            fg="#93A8BF",
            font=("Segoe UI", 11),
        ).pack(anchor="w", pady=(6, 0))

        self.cards_frame = tk.Frame(self.root, bg="#08111B")
        self.cards_frame.pack(fill="x", padx=26, pady=(6, 16))

        self.total_card = AnimatedCard(self.cards_frame, "Total Spend", "#49CDA7")
        self.avg_card = AnimatedCard(self.cards_frame, "Average Expense", "#F6B449")
        self.count_card = AnimatedCard(self.cards_frame, "Transactions", "#7F9CFF")
        self.category_card = AnimatedCard(self.cards_frame, "Top Category", "#F77D6D")

        for idx, card in enumerate([self.total_card, self.avg_card, self.count_card, self.category_card]):
            card.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 14, 0))
            self.cards_frame.grid_columnconfigure(idx, weight=1)

        self.body = tk.Frame(self.root, bg="#08111B")
        self.body.pack(fill="both", expand=True, padx=26, pady=(0, 24))
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=0)
        self.body.grid_columnconfigure(1, weight=1)

        self.form_panel = tk.Frame(self.body, bg="#102033", width=345)
        self.form_panel.grid(row=0, column=0, sticky="nsw", padx=(0, 18))
        self.form_panel.grid_propagate(False)

        self.form_canvas = tk.Canvas(self.form_panel, bg="#102033", highlightthickness=0, bd=0)
        self.form_scrollbar = tk.Scrollbar(self.form_panel, orient="vertical", command=self.form_canvas.yview)
        self.form_content = tk.Frame(self.form_canvas, bg="#102033")
        self.form_content.bind(
            "<Configure>",
            lambda _event: self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all")),
        )
        self.form_window = self.form_canvas.create_window((0, 0), window=self.form_content, anchor="nw")
        self.form_canvas.configure(yscrollcommand=self.form_scrollbar.set)
        self.form_canvas.pack(side="left", fill="both", expand=True)
        self.form_scrollbar.pack(side="right", fill="y")
        self.form_canvas.bind("<Configure>", self._resize_form_canvas)

        self.content_panel = tk.Frame(self.body, bg="#0C1828")
        self.content_panel.grid(row=0, column=1, sticky="nsew")
        self.content_panel.grid_rowconfigure(1, weight=1)
        self.content_panel.grid_columnconfigure(0, weight=1)

        self._build_form_panel()
        self._build_content_panel()

    def _build_form_panel(self) -> None:
        tk.Label(self.form_content, text="Manage Expenses", bg="#102033", fg="#F4FAFF", font=("Segoe UI Bold", 18)).pack(
            anchor="w", padx=20, pady=(20, 4)
        )
        tk.Label(
            self.form_content,
            text="Create separate spending histories for different users and edit any saved record.",
            bg="#102033",
            fg="#91A8C2",
            font=("Segoe UI", 10),
            wraplength=285,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 16))

        tk.Label(self.form_content, text="Expense User", bg="#102033", fg="#B1C5D9", font=("Segoe UI Semibold", 10)).pack(
            anchor="w", padx=20, pady=(4, 6)
        )
        chooser_row = tk.Frame(self.form_content, bg="#102033")
        chooser_row.pack(fill="x", padx=20)

        self.form_user_combo = ttk.Combobox(
            chooser_row, textvariable=self.form_user_var, state="readonly", style="App.TCombobox"
        )
        self.form_user_combo.pack(side="left", fill="x", expand=True)
        self._action_button(chooser_row, "New User", "#385777", self.create_user, compact=True).pack(side="left", padx=(8, 0))

        self._labeled_entry("Date (YYYY-MM-DD)", self.date_var)
        self._labeled_entry("Title", self.title_var)
        self._labeled_combobox("Category", self.category_var, CATEGORIES)
        self._labeled_entry("Amount", self.amount_var)

        tk.Label(self.form_content, text="Notes", bg="#102033", fg="#B1C5D9", font=("Segoe UI Semibold", 10)).pack(
            anchor="w", padx=20, pady=(12, 6)
        )
        self.notes_text = tk.Text(
            self.form_content,
            height=3,
            bg="#0B1726",
            fg="#F1F7FD",
            insertbackground="#F1F7FD",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#28455F",
            font=("Segoe UI", 10),
            wrap="word",
        )
        self.notes_text.pack(fill="x", padx=20)

        self.status_label = tk.Label(
            self.form_content,
            text="Choose a user and add an expense.",
            bg="#102033",
            fg="#71D2B0",
            font=("Segoe UI", 10),
        )
        self.status_label.pack(anchor="w", padx=20, pady=(10, 8))

        buttons = tk.Frame(self.form_content, bg="#102033")
        buttons.pack(fill="x", padx=20, pady=(0, 10))
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=1)
        self._action_button(buttons, "Add Expense", "#39B98D", self.add_expense).grid(row=0, column=0, sticky="ew", pady=(0, 8), padx=(0, 6))
        self._action_button(buttons, "Update Selected", "#476FE7", self.update_expense).grid(row=0, column=1, sticky="ew", pady=(0, 8), padx=(6, 0))
        self._action_button(buttons, "Delete Selected", "#D85B62", self.delete_expense).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self._action_button(buttons, "Clear Form", "#24405E", self.clear_form).grid(row=1, column=1, sticky="ew", padx=(6, 0))

    def _build_content_panel(self) -> None:
        top_bar = tk.Frame(self.content_panel, bg="#0C1828")
        top_bar.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        top_bar.grid_columnconfigure(1, weight=1)

        tk.Label(top_bar, text="Spending Analysis", bg="#0C1828", fg="#F4FAFF", font=("Segoe UI Bold", 16)).grid(
            row=0, column=0, sticky="w", padx=(0, 16)
        )

        filter_row = tk.Frame(top_bar, bg="#0C1828")
        filter_row.grid(row=0, column=1, sticky="e")

        self.active_user_combo = ttk.Combobox(
            filter_row, textvariable=self.active_user_var, state="readonly", width=18, style="App.TCombobox"
        )
        self.active_user_combo.pack(side="left", padx=(0, 8))
        self.active_user_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_dashboard())

        self.preset_combo = ttk.Combobox(
            filter_row, textvariable=self.date_preset_var, values=DATE_PRESETS, state="readonly", width=16, style="App.TCombobox"
        )
        self.preset_combo.pack(side="left", padx=(0, 8))
        self.preset_combo.bind("<<ComboboxSelected>>", self.on_preset_changed)

        self.start_entry = self._mini_entry(filter_row, self.start_date_var)
        self.end_entry = self._mini_entry(filter_row, self.end_date_var)
        self.start_entry.pack(side="left", padx=(0, 6))
        self.end_entry.pack(side="left", padx=(0, 6))
        self._action_button(filter_row, "Apply", "#2E8EAE", self.refresh_dashboard, compact=True).pack(side="left")

        grid_frame = tk.Frame(self.content_panel, bg="#0C1828")
        grid_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        grid_frame.grid_columnconfigure(0, weight=3)
        grid_frame.grid_columnconfigure(1, weight=2)
        grid_frame.grid_rowconfigure(0, weight=1)

        table_panel = tk.Frame(grid_frame, bg="#102033")
        table_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        chart_panel = tk.Frame(grid_frame, bg="#102033")
        chart_panel.grid(row=0, column=1, sticky="nsew")

        tk.Label(table_panel, text="Expense History", bg="#102033", fg="#F4FAFF", font=("Segoe UI Bold", 14)).pack(
            anchor="w", padx=18, pady=(16, 10)
        )

        columns = ("id", "username", "expense_date", "title", "category", "amount", "notes")
        self.tree = ttk.Treeview(table_panel, columns=columns, show="headings", style="App.Treeview")
        headings = {
            "id": "ID",
            "username": "User",
            "expense_date": "Date",
            "title": "Title",
            "category": "Category",
            "amount": "Amount",
            "notes": "Notes",
        }
        widths = {"id": 50, "username": 110, "expense_date": 110, "title": 150, "category": 110, "amount": 100, "notes": 250}
        for name in columns:
            self.tree.heading(name, text=headings[name])
            self.tree.column(name, width=widths[name], anchor="e" if name == "amount" else "w", stretch=name == "notes")

        self.tree.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.tree.bind("<<TreeviewSelect>>", self.load_selected_expense)

        tk.Label(chart_panel, text="Visual Breakdown", bg="#102033", fg="#F4FAFF", font=("Segoe UI Bold", 14)).pack(
            anchor="w", padx=18, pady=(16, 6)
        )
        tk.Label(
            chart_panel,
            text="Filter by user and date range to answer questions like 'How much did User 1 spend in the last 4 days?'",
            bg="#102033",
            fg="#8FA7C0",
            font=("Segoe UI", 10),
            wraplength=360,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 8))

        self.chart_host = tk.Frame(chart_panel, bg="#08111B")
        self.chart_host.pack(fill="both", expand=True, padx=18, pady=(4, 18))

    def _resize_form_canvas(self, event) -> None:
        self.form_canvas.itemconfigure(self.form_window, width=event.width)

    def _mini_entry(self, master: tk.Misc, variable: tk.StringVar) -> tk.Entry:
        return tk.Entry(
            master,
            textvariable=variable,
            width=12,
            bg="#102033",
            fg="#F4FAFF",
            insertbackground="#F4FAFF",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#284965",
            font=("Segoe UI", 10),
            justify="center",
        )

    def _labeled_entry(self, label: str, variable: tk.StringVar) -> None:
        tk.Label(self.form_content, text=label, bg="#102033", fg="#B1C5D9", font=("Segoe UI Semibold", 10)).pack(
            anchor="w", padx=20, pady=(10, 6)
        )
        tk.Entry(
            self.form_content,
            textvariable=variable,
            bg="#0B1726",
            fg="#F1F7FD",
            insertbackground="#F1F7FD",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#28455F",
            font=("Segoe UI", 11),
        ).pack(fill="x", padx=20)

    def _labeled_combobox(self, label: str, variable: tk.StringVar, values: list[str]) -> None:
        tk.Label(self.form_content, text=label, bg="#102033", fg="#B1C5D9", font=("Segoe UI Semibold", 10)).pack(
            anchor="w", padx=20, pady=(10, 6)
        )
        ttk.Combobox(
            self.form_content, textvariable=variable, values=values, state="readonly", style="App.TCombobox"
        ).pack(fill="x", padx=20)

    def _action_button(self, master: tk.Misc, text: str, color: str, command, compact: bool = False) -> tk.Button:
        button = tk.Button(
            master,
            text=text,
            command=command,
            bg=color,
            fg="white",
            activebackground=color,
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=11 if compact else 12,
            pady=8 if compact else 11,
            font=("Segoe UI Semibold", 9 if compact else 10),
            cursor="hand2",
        )
        base = color
        hover = self._blend(color, "#FFFFFF", 0.1)
        button.bind("<Enter>", lambda _event: button.configure(bg=hover, activebackground=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=base, activebackground=base))
        return button

    def _blend(self, source_hex: str, target_hex: str, factor: float) -> str:
        source = tuple(int(source_hex[i : i + 2], 16) for i in (1, 3, 5))
        target = tuple(int(target_hex[i : i + 2], 16) for i in (1, 3, 5))
        mixed = tuple(int(src + (dst - src) * factor) for src, dst in zip(source, target))
        return "#" + "".join(f"{part:02X}" for part in mixed)

    def _animate_intro(self) -> None:
        sections = [self.header, self.cards_frame, self.body]
        for section in sections:
            section.pack_forget()

        def reveal(index: int = 0) -> None:
            if index >= len(sections):
                return

            section = sections[index]
            section.place(x=26, y=40)

            def slide(step: int = 0) -> None:
                progress = min(step / 12, 1)
                current_y = int(40 - (18 * progress))
                section.place(x=26, y=current_y)
                if progress < 1:
                    self.root.after(18, lambda: slide(step + 1))
                else:
                    section.place_forget()
                    if section is self.header:
                        section.pack(fill="x", padx=26, pady=(22, 12))
                    elif section is self.cards_frame:
                        section.pack(fill="x", padx=26, pady=(6, 16))
                    else:
                        section.pack(fill="both", expand=True, padx=26, pady=(0, 24))
                    self.root.after(55, lambda: reveal(index + 1))

            slide()

        reveal()

    def _load_users(self) -> None:
        rows = self.db.fetch_users()
        self.user_lookup = {row["username"]: int(row["id"]) for row in rows}
        user_names = list(self.user_lookup.keys())
        self.form_user_combo.configure(values=user_names)
        self.active_user_combo.configure(values=["All Users", *user_names])

        if user_names and not self.form_user_var.get():
            self.form_user_var.set(user_names[0])
        if not self.active_user_var.get():
            self.active_user_var.set("All Users")
        self.on_preset_changed()

    def create_user(self) -> None:
        username = simpledialog.askstring("Create user", "Enter a new user name:", parent=self.root)
        if username is None:
            return

        clean_name = username.strip()
        if not clean_name:
            messagebox.showerror("Invalid user", "User name cannot be empty.")
            return

        try:
            self.db.ensure_user(clean_name)
        except sqlite3.IntegrityError:
            messagebox.showerror("Duplicate user", "That user already exists.")
            return

        self._load_users()
        self.form_user_var.set(clean_name)
        self.active_user_var.set(clean_name)
        self.status_label.config(text=f"Created user '{clean_name}'.", fg="#71D2B0")
        self.refresh_dashboard()

    def _selected_user_id(self, for_form: bool = False) -> int | None:
        selected = self.form_user_var.get() if for_form else self.active_user_var.get()
        if not selected or selected == "All Users":
            return None
        return self.user_lookup.get(selected)

    def on_preset_changed(self, _event=None) -> None:
        is_custom = self.date_preset_var.get() == "Custom Range"
        state = "normal" if is_custom else "disabled"
        if not is_custom:
            self.start_date_var.set("")
            self.end_date_var.set("")
        self.start_entry.configure(state=state)
        self.end_entry.configure(state=state)
        if _event is not None:
            self.refresh_dashboard()

    def _validate_date(self, value: str, field_name: str) -> str:
        clean = value.strip()
        if not clean:
            return ""
        try:
            datetime.strptime(clean, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{field_name} must use YYYY-MM-DD format.") from exc
        return clean

    def _read_form(self) -> Expense | None:
        user_id = self._selected_user_id(for_form=True)
        if user_id is None:
            messagebox.showerror("Missing user", "Please choose a user before saving an expense.")
            return None

        try:
            expense_date = self._validate_date(self.date_var.get(), "Expense date")
        except ValueError as exc:
            messagebox.showerror("Invalid date", str(exc))
            return None

        title = self.title_var.get().strip()
        if not title:
            messagebox.showerror("Missing title", "Please enter a short title for the expense.")
            return None

        try:
            amount = float(self.amount_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid amount", "Please enter a valid number for the amount.")
            return None

        if amount <= 0:
            messagebox.showerror("Invalid amount", "Amount must be greater than zero.")
            return None

        return Expense(
            user_id=user_id,
            expense_date=expense_date,
            title=title,
            category=self.category_var.get().strip() or "Other",
            amount=round(amount, 2),
            notes=self.notes_text.get("1.0", "end").strip(),
        )

    def add_expense(self) -> None:
        expense = self._read_form()
        if not expense:
            return
        self.db.add_expense(expense)
        self.status_label.config(
            text=f"Saved '{expense.title}' for {self.form_user_var.get()}. Check the table on the right.",
            fg="#71D2B0",
        )
        self.clear_form(reset_status=False)
        self.active_user_var.set(self.form_user_var.get())
        self.refresh_dashboard()

    def update_expense(self) -> None:
        if self.selected_expense_id is None:
            messagebox.showinfo("No selection", "Please select an expense from the table first.")
            return
        expense = self._read_form()
        if not expense:
            return
        self.db.update_expense(self.selected_expense_id, expense)
        self.status_label.config(text=f"Updated expense ID {self.selected_expense_id}.", fg="#71D2B0")
        self.refresh_dashboard()

    def delete_expense(self) -> None:
        if self.selected_expense_id is None:
            messagebox.showinfo("No selection", "Please select an expense to delete.")
            return
        if not messagebox.askyesno("Delete expense", "Do you want to permanently remove the selected expense?"):
            return
        self.db.delete_expense(self.selected_expense_id)
        self.status_label.config(text="Selected expense deleted.", fg="#F6B449")
        self.clear_form(reset_status=False)
        self.refresh_dashboard()

    def clear_form(self, reset_status: bool = True) -> None:
        self.selected_expense_id = None
        self.date_var.set(datetime.now().strftime("%Y-%m-%d"))
        self.title_var.set("")
        self.category_var.set(CATEGORIES[0])
        self.amount_var.set("")
        self.notes_text.delete("1.0", "end")
        if reset_status:
            self.status_label.config(text="Choose a user and add an expense.", fg="#71D2B0")

    def load_selected_expense(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        values = self.tree.item(selection[0], "values")
        self.selected_expense_id = int(values[0])
        self.form_user_var.set(values[1])
        self.date_var.set(values[2])
        self.title_var.set(values[3])
        self.category_var.set(values[4])
        self.amount_var.set(values[5].replace("Rs ", "").replace(",", ""))
        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", values[6])
        self.status_label.config(text=f"Loaded expense ID {self.selected_expense_id} for editing.", fg="#7F9CFF")

    def _current_filter_dates(self) -> tuple[str, str] | None:
        try:
            start_date = self._validate_date(self.start_date_var.get(), "Start date")
            end_date = self._validate_date(self.end_date_var.get(), "End date")
        except ValueError as exc:
            messagebox.showerror("Invalid filter date", str(exc))
            return None

        if self.date_preset_var.get() == "Custom Range" and start_date and end_date and start_date > end_date:
            messagebox.showerror("Invalid range", "Start date cannot be later than end date.")
            return None
        return start_date, end_date

    def refresh_dashboard(self) -> None:
        dates = self._current_filter_dates()
        if dates is None:
            return

        start_date, end_date = dates
        user_id = self._selected_user_id(for_form=False)
        preset = self.date_preset_var.get()

        rows = self.db.fetch_expenses(user_id, preset, start_date, end_date)
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    row["id"],
                    row["username"] or "Unknown",
                    row["expense_date"],
                    row["title"],
                    row["category"],
                    f"Rs {float(row['amount']):,.2f}",
                    row["notes"],
                ),
            )

        summary = self.db.summary(user_id, preset, start_date, end_date)
        self.total_card.animate_to(f"Rs {summary['total']:,.0f}", float(summary["total"]), "Rs ")
        self.avg_card.animate_to(f"Rs {summary['average']:,.0f}", float(summary["average"]), "Rs ")
        self.count_card.animate_to(str(summary["count"]), float(summary["count"]))
        self.category_card.animate_to(str(summary["top_category"]))
        self._draw_charts(summary)

    def _draw_charts(self, summary: dict[str, object]) -> None:
        for child in self.chart_host.winfo_children():
            child.destroy()

        if Figure is None or FigureCanvasTkAgg is None or self.chart_figure is None:
            tk.Label(
                self.chart_host,
                text="Install matplotlib to enable graphs.\n\nRun: pip install -r requirements.txt",
                bg="#08111B",
                fg="#B1C5D9",
                font=("Segoe UI", 11),
                justify="center",
            ).pack(expand=True)
            return

        self.chart_figure.clear()
        ax1 = self.chart_figure.add_subplot(211)
        ax2 = self.chart_figure.add_subplot(212)
        self.chart_figure.subplots_adjust(hspace=0.48, left=0.12, right=0.95, top=0.95, bottom=0.12)

        for axis in (ax1, ax2):
            axis.set_facecolor("#08111B")
            axis.tick_params(colors="#CCD8E4")
            for spine in axis.spines.values():
                spine.set_color("#21374E")

        category_totals = summary["category_totals"]
        if category_totals:
            labels = list(category_totals.keys())
            values = list(category_totals.values())
            palette = ["#49CDA7", "#7F9CFF", "#F6B449", "#F77D6D", "#62D4F4", "#C693FF", "#A4D56D", "#F1A5D3"]
            ax1.pie(
                values,
                labels=labels,
                autopct=lambda pct: f"{pct:.0f}%",
                startangle=110,
                colors=palette[: len(values)],
                textprops={"color": "#F4FAFF", "fontsize": 9},
                wedgeprops={"width": 0.42, "edgecolor": "#08111B"},
            )
            ax1.set_title("Category Distribution", color="#F4FAFF", fontsize=12, pad=10)
        else:
            ax1.text(0.5, 0.5, "No expenses in this filter", ha="center", va="center", color="#92A8BF")
            ax1.set_xticks([])
            ax1.set_yticks([])

        daily_totals = summary["daily_totals"]
        if daily_totals:
            days = list(daily_totals.keys())
            amounts = list(daily_totals.values())
            ax2.bar(days, amounts, color="#7F9CFF", width=0.55)
            ax2.plot(days, amounts, color="#49CDA7", marker="o", linewidth=2.2)
            ax2.set_title("Daily Spending Trend", color="#F4FAFF", fontsize=12, pad=10)
            ax2.set_ylabel("Amount (Rs)", color="#CCD8E4")
            ax2.tick_params(axis="x", rotation=25)
        else:
            ax2.text(0.5, 0.5, "Daily trend will appear here", ha="center", va="center", color="#92A8BF")
            ax2.set_xticks([])
            ax2.set_yticks([])

        self.chart_canvas = FigureCanvasTkAgg(self.chart_figure, master=self.chart_host)
        self.chart_canvas.draw()
        widget = self.chart_canvas.get_tk_widget()
        widget.configure(bg="#08111B", highlightthickness=0, bd=0)
        widget.pack(fill="both", expand=True)

    def on_close(self) -> None:
        self.db.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ExpenseTrackerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
