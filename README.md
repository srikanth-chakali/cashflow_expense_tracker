# CashFlow — Personal Expense Tracker (Flask edition)

This is your original Streamlit app rebuilt as a plain **Flask + HTML/CSS/JS** website.
Streamlit has been removed completely. The database layer is untouched:
same `DATABASE_URL` env var, same `psycopg2` connection with `sslmode="require"`,
same table schemas (`users`, `expenses`, `income`, `budgets`), same `bcrypt`
password hashing, same auth logic (`register_user` / `login_user`).

## What changed vs. what didn't

**Unchanged:**
- `get_db_connection()`, `initialize_database()`, `register_user()`, `login_user()` — copied over as-is.
- All table/column names.
- bcrypt hashing.
- The entire visual design: colors, fonts (Outfit + DM Mono), glass/blur cards,
  gradient buttons, tab styling, animations (fadeSlideDown, cardPop, loginPop),
  landing page copy, dashboard layout, donut chart look.

**Changed (required to remove Streamlit):**
- `st.session_state` → Flask `session` (signed cookie, needs `SECRET_KEY`).
- `st.rerun()` / widgets → normal HTML forms + redirects.
- Plotly `go.Figure` server-side chart → the same Plotly.js chart, now rendered
  client-side from data Flask passes into the page (visually identical).
- `st.dataframe` → a plain HTML `<table>` styled to look the same.
- Fixed a bug from the original code: `add_expense` / `add_income` referenced a
  global `cursor`/`conn` that was never defined — routes now open a real
  connection via `get_db_connection()` before inserting.

## Project structure

```
cashflow/
├── app.py                  # Flask app + all routes + DB functions
├── requirements.txt
├── .env.example
├── templates/
│   ├── base.html
│   ├── landing.html
│   ├── login.html
│   └── dashboard.html
└── static/
    ├── css/style.css       # full visual design, converted from the Streamlit CSS
    ├── js/                 # (chart script is inline in dashboard.html)
    └── img/logo.png        # <-- put your logo.png here (used as the favicon)
```

## Setup

```bash
cd cashflow
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set DATABASE_URL to your real Postgres connection string
# (the exact same one you used with Streamlit) and a SECRET_KEY

python app.py
```

Then open **http://localhost:5000**.

Drop your existing `logo.png` into `static/img/` (used as the browser tab icon).

## Notes
- Tables are auto-created on first login/register call, same as before
  (`initialize_database()` runs inside `register_user`/`login_user`).
- CSV export now streams straight from Postgres instead of via pandas, so the
  download button behaves identically without needing pandas installed.
- For production, run behind gunicorn (`gunicorn app:app`) instead of the Flask
  dev server, and set `debug=False`.
