import os
import io
import csv
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, Response, jsonify
)
from pathlib import Path
from contextlib import contextmanager
from dotenv import load_dotenv
import psycopg2
from psycopg2 import pool as pg_pool
import bcrypt
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

# ---------------- DATABASE CONNECTION ---------------- #

DATABASE_URL = os.getenv("DATABASE_URL")

_connection_pool = None


def _get_pool():
    global _connection_pool
    if _connection_pool is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError(
                "DATABASE_URL environment variable is not configured"
            )
        _connection_pool = pg_pool.ThreadedConnectionPool(
            1,
            int(os.getenv("DB_POOL_MAX", "10")),
            dsn=database_url,
            connect_timeout=10,
            sslmode="require"
        )
    return _connection_pool


def get_db_connection():
    """Kept for compatibility — now hands out a pooled connection
    instead of opening a fresh socket."""
    return _get_pool().getconn()


def _release(conn):
    _get_pool().putconn(conn)


@contextmanager
def db_conn():
    """Preferred helper: always returns the connection to the pool,
    even on error, instead of closing the socket."""
    conn = _get_pool().getconn()
    try:
        yield conn
    finally:
        _release(conn)


# ---------------- CREATE TABLES SAFELY ---------------- #
def initialize_database():
    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    expense_date TEXT,
                    category TEXT,
                    payment TEXT,
                    amount REAL,
                    description TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE,
                    password TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS income (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    amount REAL,
                    date DATE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    category VARCHAR(100),
                    monthly_limit DECIMAL(10,2)
                )
            """)

            # Speeds up "WHERE user_id = %s" lookups on expenses/income
            # as the tables grow — same schema, just an index on top.
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_expenses_user_id ON expenses(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_income_user_id ON income(user_id)")

        conn.commit()

    finally:
        _release(conn)


# Runs once when the app starts (or once per worker under gunicorn),
# instead of on every login/register click as before.
try:
    initialize_database()
except Exception as exc:  # pragma: no cover
    app.logger.warning("Could not initialize database at startup: %s", exc)


# ---------------- AUTH FUNCTIONS ---------------- #
def register_user(username, password):
    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM users WHERE username = %s",
                (username,)
            )

            if cursor.fetchone():
                return False

            hashed_password = bcrypt.hashpw(
                password.encode("utf-8"),
                bcrypt.gensalt()
            ).decode("utf-8")

            cursor.execute(
                """
                INSERT INTO users (username, password)
                VALUES (%s, %s)
                """,
                (username, hashed_password)
            )

        conn.commit()
        return True
    finally:
        _release(conn)


def login_user(username, password):
    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, username, password
                FROM users
                WHERE username = %s
                """,
                (username,)
            )

            user = cursor.fetchone()

            if user and bcrypt.checkpw(
                password.encode("utf-8"),
                user[2].encode("utf-8")
            ):
                return user[0]

            return None

    finally:
        _release(conn)


# ---------------- DATA HELPERS ---------------- #
def load_expenses(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, user_id, expense_date, category, payment, amount, description
                FROM expenses
                WHERE user_id = %s
                ORDER BY expense_date DESC
            """, (user_id,))
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]
    finally:
        _release(conn)


def load_income(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, user_id, amount, date
                FROM income
                WHERE user_id = %s
                ORDER BY date DESC
            """, (user_id,))
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]
    finally:
        _release(conn)


def load_dashboard_data(user_id):
    """Same result as calling load_expenses() + load_income(), but uses
    one pooled connection for both queries instead of two."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, user_id, expense_date, category, payment, amount, description
                FROM expenses
                WHERE user_id = %s
                ORDER BY expense_date DESC
            """, (user_id,))
            expense_rows = cursor.fetchall()
            expense_columns = [desc[0] for desc in cursor.description]
            expenses = [dict(zip(expense_columns, row)) for row in expense_rows]

            cursor.execute("""
                SELECT id, user_id, amount, date
                FROM income
                WHERE user_id = %s
                ORDER BY date DESC
            """, (user_id,))
            income_rows = cursor.fetchall()
            income_columns = [desc[0] for desc in cursor.description]
            income = [dict(zip(income_columns, row)) for row in income_rows]

        return expenses, income
    finally:
        _release(conn)


def insert_expense(user_id, expense_date, category, payment, amount, description):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO expenses
                (user_id, expense_date, category, payment, amount, description)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (user_id, expense_date, category, payment, amount, description))
        conn.commit()
    finally:
        _release(conn)


def insert_income(user_id, amount):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO income
                (user_id, amount, date)
                VALUES (%s, %s, %s)
            """, (user_id, amount, str(datetime.today().date())))
        conn.commit()
    finally:
        _release(conn)


# ---------------- AUTH GUARD ---------------- #
def login_required(view_fn):
    @wraps(view_fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("landing"))
        return view_fn(*args, **kwargs)
    return wrapped


# ---------------- ROUTES ---------------- #
@app.route("/")
def landing():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/auth")
def auth():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    active_tab = request.args.get("tab", "login")
    return render_template("login.html", active_tab=active_tab)


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    user_id = login_user(username, password)
    if user_id:
        session["user_id"] = user_id
        session["username"] = username
        return redirect(url_for("dashboard"))

    flash("✗ Invalid credentials", "error")
    return redirect(url_for("auth", tab="login"))


@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        flash("✗ Username and password are required", "error")
        return redirect(url_for("auth", tab="register"))

    if register_user(username, password):
        flash("✓ Account created — login now", "success")
        return redirect(url_for("auth", tab="login"))

    flash("✗ Username already taken", "error")
    return redirect(url_for("auth", tab="register"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    expenses, income = load_dashboard_data(user_id)

    total_expense = sum(float(e["amount"]) for e in expenses) if expenses else 0.0
    total_income = sum(float(i["amount"]) for i in income) if income else 0.0
    savings = total_income - total_expense

    category_totals = {}
    for e in expenses:
        category_totals[e["category"]] = category_totals.get(e["category"], 0.0) + float(e["amount"])

    return render_template(
        "dashboard.html",
        username=session.get("username"),
        expenses=expenses,
        total_expense=total_expense,
        total_income=total_income,
        savings=savings,
        category_labels=list(category_totals.keys()),
        category_values=list(category_totals.values()),
        has_expenses=bool(expenses),
    )


@app.route("/add-expense", methods=["POST"])
@login_required
def add_expense_route():
    user_id = session["user_id"]
    expense_date = request.form.get("expense_date")
    category = request.form.get("category")
    payment = request.form.get("payment")
    amount = request.form.get("amount", 0)
    description = request.form.get("description", "")

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0

    insert_expense(user_id, expense_date, category, payment, amount, description)
    flash("✓ Expense logged", "success")
    return redirect(url_for("dashboard"))


@app.route("/add-income", methods=["POST"])
@login_required
def add_income_route():
    user_id = session["user_id"]
    amount = request.form.get("amount", 0)

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0

    insert_income(user_id, amount)
    flash("✓ Income recorded", "success")
    return redirect(url_for("dashboard"))


@app.route("/download-csv")
@login_required
def download_csv():
    user_id = session["user_id"]
    expenses = load_expenses(user_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["expense_date", "category", "payment", "amount", "description"])
    for e in expenses:
        writer.writerow([
            e["expense_date"], e["category"], e["payment"], e["amount"], e["description"]
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=expenses.csv"}
    )

    @app.route("/sitemap.xml")
    def sitemap():
        xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url>
            <loc>https://cashflow-expense-tracker.vercel.app/</loc>
        </url>
    </urlset>"""
        return Response(xml, mimetype="application/xml")


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)