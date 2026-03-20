import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "expenses.db"
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
        self.db_path = db_path
        self._bootstrap()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _bootstrap(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
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
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(expenses)")}
            if "user_id" not in columns:
                connection.execute("ALTER TABLE expenses ADD COLUMN user_id INTEGER")
            default_user_id = self.ensure_user("User 1")
            connection.execute("UPDATE expenses SET user_id = ? WHERE user_id IS NULL", (default_user_id,))
            connection.commit()

    def ensure_user(self, username: str) -> int:
        clean_name = username.strip()
        if not clean_name:
            raise ValueError("User name cannot be empty.")

        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM users WHERE lower(username) = lower(?)",
                (clean_name,),
            ).fetchone()
            if existing:
                return int(existing["id"])

            cursor = connection.execute("INSERT INTO users (username) VALUES (?)", (clean_name,))
            connection.commit()
            return int(cursor.lastrowid)

    def fetch_users(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute("SELECT id, username FROM users ORDER BY lower(username)"))

    def add_expense(self, expense: Expense) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO expenses (user_id, expense_date, title, category, amount, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (expense.user_id, expense.expense_date, expense.title, expense.category, expense.amount, expense.notes),
            )
            connection.commit()

    def delete_expense(self, expense_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            connection.commit()

    def update_expense(self, expense_id: int, expense: Expense) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE expenses
                SET user_id = ?, expense_date = ?, title = ?, category = ?, amount = ?, notes = ?
                WHERE id = ?
                """,
                (expense.user_id, expense.expense_date, expense.title, expense.category, expense.amount, expense.notes, expense_id),
            )
            connection.commit()

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
        with self.connect() as connection:
            return list(connection.execute(query, params))

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
        chart_max = max(daily_totals.values(), default=0)

        return {
            "count": len(rows),
            "total": total,
            "average": average,
            "top_category": top_category[0],
            "category_totals": dict(sorted(category_totals.items(), key=lambda item: item[1], reverse=True)),
            "daily_totals": dict(sorted(daily_totals.items())),
            "chart_max": chart_max,
        }


app = Flask(__name__)
app.config["SECRET_KEY"] = "expense-tracker-local"
db = ExpenseDatabase(DB_PATH)


def parse_date(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        return ""
    try:
        datetime.strptime(clean, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format.") from exc
    return clean


def build_state() -> dict[str, object]:
    selected_user = request.args.get("user", "all")
    preset = request.args.get("preset", "Last 7 Days")
    start_date = request.args.get("start", "")
    end_date = request.args.get("end", "")

    if preset not in DATE_PRESETS:
        preset = "Last 7 Days"

    users = db.fetch_users()
    user_lookup = {str(row["id"]): row["username"] for row in users}
    user_id = int(selected_user) if selected_user.isdigit() and selected_user in user_lookup else None

    rows = db.fetch_expenses(user_id, preset, start_date, end_date)
    summary = db.summary(user_id, preset, start_date, end_date)
    return {
        "users": users,
        "selected_user": selected_user,
        "selected_user_name": user_lookup.get(selected_user, "All Users"),
        "preset": preset,
        "start_date": start_date,
        "end_date": end_date,
        "expenses": rows,
        "summary": summary,
        "categories": CATEGORIES,
        "presets": DATE_PRESETS,
        "error": request.args.get("error", ""),
        "message": request.args.get("message", ""),
    }


@app.get("/")
def index():
    return render_template("index.html", **build_state())


@app.post("/users")
def create_user():
    username = request.form.get("username", "").strip()
    try:
        db.ensure_user(username)
        message = f"User '{username}' created."
        return redirect(url_for("index", message=message))
    except ValueError as exc:
        return redirect(url_for("index", error=str(exc)))


@app.post("/expenses")
def save_expense():
    selected_id = request.form.get("expense_id", "").strip()
    user_id_value = request.form.get("user_id", "").strip()
    title = request.form.get("title", "").strip()
    category = request.form.get("category", "").strip() or "Other"
    amount_raw = request.form.get("amount", "").strip()
    notes = request.form.get("notes", "").strip()

    try:
        user_id = int(user_id_value)
        expense_date = parse_date(request.form.get("expense_date", ""), "Expense date")
        if not title:
            raise ValueError("Title is required.")
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")
    except (ValueError, TypeError) as exc:
        return redirect(url_for("index", error=str(exc)))

    expense = Expense(
        user_id=user_id,
        expense_date=expense_date,
        title=title,
        category=category,
        amount=round(amount, 2),
        notes=notes,
    )
    if selected_id:
        db.update_expense(int(selected_id), expense)
        message = "Expense updated."
    else:
        db.add_expense(expense)
        message = "Expense saved."

    return redirect(url_for("index", user=user_id, preset="Last 7 Days", message=message))


@app.post("/expenses/<int:expense_id>/delete")
def delete_expense(expense_id: int):
    db.delete_expense(expense_id)
    return redirect(url_for("index", message="Expense deleted."))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=True, host="0.0.0.0", port=port)
