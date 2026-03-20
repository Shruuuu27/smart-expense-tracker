from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
from pathlib import Path
from typing import Any

from flask import Flask, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text, create_engine, inspect, text
from sqlalchemy.engine import Engine


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
        self.engine = create_engine(self._database_url(db_path), future=True)
        self.metadata = MetaData()
        self.users = Table(
            "users",
            self.metadata,
            Column("id", Integer, primary_key=True),
            Column("username", String(255), nullable=False, unique=True),
            Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
        )
        self.expenses = Table(
            "expenses",
            self.metadata,
            Column("id", Integer, primary_key=True),
            Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE")),
            Column("expense_date", String(20), nullable=False),
            Column("title", String(255), nullable=False),
            Column("category", String(80), nullable=False),
            Column("amount", Float, nullable=False),
            Column("notes", Text, nullable=False, server_default=text("''")),
            Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
        )
        self._bootstrap()

    def _database_url(self, db_path: Path) -> str:
        raw_url = os.getenv("DATABASE_URL", "").strip()
        if raw_url:
            if raw_url.startswith("postgres://"):
                return raw_url.replace("postgres://", "postgresql+psycopg://", 1)
            if raw_url.startswith("postgresql://"):
                return raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
            return raw_url
        return f"sqlite+pysqlite:///{db_path}"

    def _bootstrap(self) -> None:
        self.metadata.create_all(self.engine)
        inspector = inspect(self.engine)
        columns = {column["name"] for column in inspector.get_columns("expenses")}
        with self.engine.begin() as connection:
            if "user_id" not in columns:
                connection.execute(text("ALTER TABLE expenses ADD COLUMN user_id INTEGER"))
            default_user_id = self.ensure_user("User 1")
            connection.execute(text("UPDATE expenses SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": default_user_id})

    def ensure_user(self, username: str) -> int:
        clean_name = username.strip()
        if not clean_name:
            raise ValueError("User name cannot be empty.")

        with self.engine.begin() as connection:
            existing = connection.execute(
                text("SELECT id FROM users WHERE lower(username) = lower(:username)"),
                {"username": clean_name},
            ).mappings().first()
            if existing:
                return int(existing["id"])

            created = connection.execute(
                text("INSERT INTO users (username) VALUES (:username) RETURNING id"),
                {"username": clean_name},
            ).mappings().first()
            return int(created["id"])

    def fetch_users(self) -> list[dict[str, Any]]:
        with self.engine.begin() as connection:
            return list(connection.execute(text("SELECT id, username FROM users ORDER BY lower(username)")).mappings())

    def fetch_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            return connection.execute(text("SELECT id, username FROM users WHERE id = :user_id"), {"user_id": user_id}).mappings().first()

    def fetch_user_by_name(self, username: str) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            return connection.execute(
                text("SELECT id, username FROM users WHERE lower(username) = lower(:username)"),
                {"username": username.strip()},
            ).mappings().first()

    def add_expense(self, expense: Expense) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                INSERT INTO expenses (user_id, expense_date, title, category, amount, notes)
                VALUES (:user_id, :expense_date, :title, :category, :amount, :notes)
                """
                ),
                {
                    "user_id": expense.user_id,
                    "expense_date": expense.expense_date,
                    "title": expense.title,
                    "category": expense.category,
                    "amount": expense.amount,
                    "notes": expense.notes,
                },
            )

    def delete_expense(self, expense_id: int) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM expenses WHERE id = :expense_id"), {"expense_id": expense_id})

    def update_expense(self, expense_id: int, expense: Expense) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                UPDATE expenses
                SET user_id = :user_id, expense_date = :expense_date, title = :title, category = :category, amount = :amount, notes = :notes
                WHERE id = :expense_id
                """
                ),
                {
                    "user_id": expense.user_id,
                    "expense_date": expense.expense_date,
                    "title": expense.title,
                    "category": expense.category,
                    "amount": expense.amount,
                    "notes": expense.notes,
                    "expense_id": expense_id,
                },
            )

    def fetch_expense(self, expense_id: int) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            return connection.execute(text("SELECT * FROM expenses WHERE id = :expense_id"), {"expense_id": expense_id}).mappings().first()

    def _build_filters(
        self,
        user_id: int | None,
        preset: str,
        start_date: str = "",
        end_date: str = "",
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []

        def bind(value: object) -> str:
            params.append(value)
            return f":p{len(params) - 1}"

        if user_id is not None:
            clauses.append(f"expenses.user_id = {bind(user_id)}")

        today = datetime.now().date()
        if preset == "Today":
            clauses.append(f"expense_date = {bind(today.strftime('%Y-%m-%d'))}")
        elif preset == "Last 4 Days":
            clauses.append(f"expense_date >= {bind((today - timedelta(days=3)).strftime('%Y-%m-%d'))}")
        elif preset == "Last 7 Days":
            clauses.append(f"expense_date >= {bind((today - timedelta(days=6)).strftime('%Y-%m-%d'))}")
        elif preset == "Last 30 Days":
            clauses.append(f"expense_date >= {bind((today - timedelta(days=29)).strftime('%Y-%m-%d'))}")
        elif preset == "Custom Range":
            if start_date:
                clauses.append(f"expense_date >= {bind(start_date)}")
            if end_date:
                clauses.append(f"expense_date <= {bind(end_date)}")

        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_clause, params

    def fetch_expenses(
        self,
        user_id: int | None,
        preset: str,
        start_date: str = "",
        end_date: str = "",
    ) -> list[dict[str, Any]]:
        where_clause, params = self._build_filters(user_id, preset, start_date, end_date)
        query = f"""
            SELECT expenses.*, users.username
            FROM expenses
            LEFT JOIN users ON users.id = expenses.user_id
            {where_clause}
            ORDER BY expense_date DESC, expenses.id DESC
        """
        with self.engine.begin() as connection:
            return list(connection.execute(text(query), self._named_params(params)).mappings())

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

    def _named_params(self, params: list[object]) -> dict[str, object]:
        return {f"p{index}": value for index, value in enumerate(params)}


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "expense-tracker-local")
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


def current_user() -> dict[str, Any] | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.fetch_user_by_id(int(user_id))


def require_user():
    user = current_user()
    if user is None:
        return None, redirect(url_for("login", error="Please log in to access your expenses."))
    return user, None


def build_state() -> dict[str, object]:
    user = current_user()
    if user is None:
        raise RuntimeError("build_state called without an authenticated user")

    preset = request.args.get("preset", "Last 7 Days")
    start_date = request.args.get("start", "")
    end_date = request.args.get("end", "")

    if preset not in DATE_PRESETS:
        preset = "Last 7 Days"

    rows = db.fetch_expenses(int(user["id"]), preset, start_date, end_date)
    summary = db.summary(int(user["id"]), preset, start_date, end_date)
    return {
        "current_user": user,
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


@app.get("/login")
def login():
    if current_user() is not None:
        return redirect(url_for("index"))
    return render_template(
        "login.html",
        error=request.args.get("error", ""),
        message=request.args.get("message", ""),
    )


@app.post("/login")
def login_submit():
    username = request.form.get("username", "").strip()
    user = db.fetch_user_by_name(username)
    if user is None:
        return redirect(url_for("login", error="User not found. Create the account first."))

    session["user_id"] = int(user["id"])
    return redirect(url_for("index", message=f"Welcome back, {user['username']}."))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login", message="You have been logged out."))


@app.get("/")
def index():
    user, redirect_response = require_user()
    if redirect_response is not None:
        return redirect_response
    return render_template("index.html", **build_state())


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(BASE_DIR / "static", "manifest.webmanifest", mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return send_from_directory(BASE_DIR / "static", "sw.js", mimetype="application/javascript")


@app.post("/users")
def create_user():
    if current_user() is not None:
        session.clear()
    username = request.form.get("username", "").strip()
    try:
        user_id = db.ensure_user(username)
        session["user_id"] = user_id
        message = f"Account created for {username}."
        return redirect(url_for("index", message=message))
    except ValueError as exc:
        return redirect(url_for("login", error=str(exc)))


@app.post("/expenses")
def save_expense():
    user, redirect_response = require_user()
    if redirect_response is not None:
        return redirect_response

    selected_id = request.form.get("expense_id", "").strip()
    title = request.form.get("title", "").strip()
    category = request.form.get("category", "").strip() or "Other"
    amount_raw = request.form.get("amount", "").strip()
    notes = request.form.get("notes", "").strip()

    try:
        expense_date = parse_date(request.form.get("expense_date", ""), "Expense date")
        if not title:
            raise ValueError("Title is required.")
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")
    except (ValueError, TypeError) as exc:
        return redirect(url_for("index", error=str(exc)))

    expense = Expense(
        user_id=int(user["id"]),
        expense_date=expense_date,
        title=title,
        category=category,
        amount=round(amount, 2),
        notes=notes,
    )
    if selected_id:
        existing = db.fetch_expense(int(selected_id))
        if existing is None or int(existing["user_id"]) != int(user["id"]):
            return redirect(url_for("index", error="You can only edit your own expenses."))
        db.update_expense(int(selected_id), expense)
        message = "Expense updated."
    else:
        db.add_expense(expense)
        message = "Expense saved."

    return redirect(url_for("index", preset="Last 7 Days", message=message))


@app.post("/expenses/<int:expense_id>/delete")
def delete_expense(expense_id: int):
    user, redirect_response = require_user()
    if redirect_response is not None:
        return redirect_response

    expense = db.fetch_expense(expense_id)
    if expense is None or int(expense["user_id"]) != int(user["id"]):
        return redirect(url_for("index", error="You can only delete your own expenses."))
    db.delete_expense(expense_id)
    return redirect(url_for("index", message="Expense deleted."))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=True, host="0.0.0.0", port=port)
