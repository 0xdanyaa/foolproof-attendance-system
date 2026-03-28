"""
====================================================================
 FOOLPROOF AUTOMATED ATTENDANCE SYSTEM (FAAS)
 app.py — Main Flask Application
====================================================================
 This file contains:
   - Database setup (SQLite3)
   - All URL routes for Teachers and Students
   - Login / Logout / Registration logic
   - Attendance marking logic
   - Auto percentage calculation
   - Report generation

 Beginner tip: A "route" is simply a URL that Flask 'listens' to.
 When you visit /teacher/login, Flask runs the function below it.
====================================================================
"""

# ── Standard library ─────────────────────────────────────────────
import sqlite3                          # Built-in Python SQLite driver
from datetime import date, timedelta    # For date handling
from functools import wraps             # For our login-required decorators
import os                               # For file paths

# ── Third-party (installed via requirements.txt) ─────────────────
from flask import (
    Flask, render_template, request,
    redirect, url_for, session, flash, g
)
from werkzeug.security import generate_password_hash, check_password_hash
# ^ generate_password_hash  → safely hashes a plain password
# ^ check_password_hash     → safely compares a plain password to a hash


# ═══════════════════════════════════════════════════════════════════
#  FLASK APP SETUP
# ═══════════════════════════════════════════════════════════════════
app = Flask(__name__)

# Secret key is required for Flask sessions (cookies that remember who is logged in).
# In production, use a long random string stored in an environment variable.
app.secret_key = 'faas_super_secret_key_change_in_production'

# Path to our SQLite database file
DATABASE = os.path.join(os.path.dirname(__file__), 'database.db')


# ═══════════════════════════════════════════════════════════════════
#  DATABASE HELPERS
# ═══════════════════════════════════════════════════════════════════

def get_db():
    """
    Open (or reuse) a database connection for this request.

    Flask's 'g' object lives for exactly one request — it is a
    convenient place to cache per-request resources like a DB connection.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        # row_factory lets us access columns by name (rec['name'])
        # instead of by index (rec[0])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    """Close the database connection at the end of every request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """
    Create all database tables if they don't already exist.

    The IF NOT EXISTS clause means this function is safe to call
    every time the app starts — it won't overwrite existing data.
    """
    db = get_db()

    # ── teachers table ───────────────────────────────────────────
    db.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            username   TEXT    UNIQUE NOT NULL,
            password   TEXT    NOT NULL,   -- stored as a hash, never plain text
            subject    TEXT,
            created_at TEXT    DEFAULT (date('now'))
        )
    """)

    # ── students table ───────────────────────────────────────────
    db.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            roll_no    TEXT    UNIQUE NOT NULL,
            class_name TEXT,
            email      TEXT,
            username   TEXT    UNIQUE,
            password   TEXT,              -- hashed; NULL if added by teacher only
            created_at TEXT    DEFAULT (date('now'))
        )
    """)

    # ── attendance table ─────────────────────────────────────────
    db.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            teacher_id INTEGER,
            date       TEXT    NOT NULL,   -- stored as 'YYYY-MM-DD'
            status     TEXT    NOT NULL    CHECK(status IN ('Present','Absent')),
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id),
            UNIQUE (student_id, date)      -- prevents duplicate entries for same day
        )
    """)

    db.commit()


# ═══════════════════════════════════════════════════════════════════
#  LOGIN-REQUIRED DECORATORS
#
#  A decorator is a function that wraps another function.
#  We use these to protect routes — if the user is not logged in,
#  they get redirected to the login page automatically.
# ═══════════════════════════════════════════════════════════════════

def teacher_required(f):
    """Decorator: redirect to teacher login if teacher is not in session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'teacher_id' not in session:
            flash('Please log in as a teacher to access this page.', 'warning')
            return redirect(url_for('teacher_login'))
        return f(*args, **kwargs)
    return decorated


def student_required(f):
    """Decorator: redirect to student login if student is not in session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'student_id' not in session:
            flash('Please log in as a student to access this page.', 'warning')
            return redirect(url_for('student_login'))
        return f(*args, **kwargs)
    return decorated


# ═══════════════════════════════════════════════════════════════════
#  HELPER: AUTO-CALCULATE ATTENDANCE PERCENTAGE
# ═══════════════════════════════════════════════════════════════════

def calculate_percentage(present: int, total: int) -> float:
    """
    Return attendance percentage rounded to one decimal place.
    Returns 0.0 if no classes have been recorded yet (avoids division by zero).

    Formula: (present / total) × 100
    """
    if total == 0:
        return 0.0
    return round((present / total) * 100, 1)


def classes_needed_for_75(present: int, total: int) -> int:
    """
    Calculate how many more consecutive classes a student must attend
    to reach ≥75% attendance.

    Approach: keep adding 1 to both present and total until the
    percentage crosses 75%, then return the count added.
    """
    extra = 0
    while True:
        if total + extra == 0:
            break
        pct = ((present + extra) / (total + extra)) * 100
        if pct >= 75:
            break
        extra += 1
        if extra > 1000:          # safety cap — avoids infinite loop
            break
    return extra


# ═══════════════════════════════════════════════════════════════════
#  ROUTE: HOME PAGE
# ═══════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    """Landing page — show portals for teacher and student login."""
    return render_template('index.html')


# ═══════════════════════════════════════════════════════════════════
#  ROUTES: TEACHER AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════

@app.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    """
    GET  → show the teacher login form
    POST → validate credentials and start a teacher session
    """
    # If teacher is already logged in, go straight to the dashboard
    if 'teacher_id' in session:
        return redirect(url_for('teacher_dashboard'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        db = get_db()
        # Fetch the teacher row matching the supplied username
        teacher = db.execute(
            "SELECT * FROM teachers WHERE username = ?", (username,)
        ).fetchone()

        # check_password_hash compares the plain password with the stored hash
        if teacher and check_password_hash(teacher['password'], password):
            # Store just the teacher's ID in the session cookie
            session.clear()
            session['teacher_id']   = teacher['id']
            session['teacher_name'] = teacher['name']
            flash(f"Welcome back, {teacher['name']}! 👋", 'success')
            return redirect(url_for('teacher_dashboard'))
        else:
            flash('Invalid username or password.', 'danger')

    return render_template('teacher_login.html')


@app.route('/teacher/register', methods=['GET', 'POST'])
def teacher_register():
    """Register a new teacher account."""
    if request.method == 'POST':
        name     = request.form['name'].strip()
        subject  = request.form.get('subject', '').strip()
        username = request.form['username'].strip()
        password = request.form['password']
        confirm  = request.form['confirm']

        # Basic server-side validation
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('teacher_register'))

        # Hash the password before storing — NEVER store plain text!
        hashed = generate_password_hash(password)

        db = get_db()
        try:
            db.execute(
                "INSERT INTO teachers (name, username, password, subject) VALUES (?,?,?,?)",
                (name, username, hashed, subject)
            )
            db.commit()
            flash('Account created! Please log in.', 'success')
            return redirect(url_for('teacher_login'))
        except sqlite3.IntegrityError:
            # IntegrityError fires when the UNIQUE constraint on username is violated
            flash('Username already taken. Please choose another.', 'danger')

    return render_template('teacher_register.html')


@app.route('/teacher/logout')
def teacher_logout():
    """Clear the session and redirect to the home page."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


# ═══════════════════════════════════════════════════════════════════
#  ROUTES: STUDENT AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════

@app.route('/student/login', methods=['GET', 'POST'])
def student_login():
    """Student login — only students who self-registered can log in here."""
    if 'student_id' in session:
        return redirect(url_for('student_dashboard'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        db = get_db()
        student = db.execute(
            "SELECT * FROM students WHERE username = ?", (username,)
        ).fetchone()

        if student and student['password'] and check_password_hash(student['password'], password):
            session.clear()
            session['student_id']   = student['id']
            session['student_name'] = student['name']
            flash(f"Welcome, {student['name']}! 🎓", 'success')
            return redirect(url_for('student_dashboard'))
        else:
            flash('Invalid username or password.', 'danger')

    return render_template('student_login.html')


@app.route('/student/register', methods=['GET', 'POST'])
def student_register():
    """Register a new student account (self-registration)."""
    if request.method == 'POST':
        name       = request.form['name'].strip()
        roll_no    = request.form['roll_no'].strip()
        class_name = request.form.get('class_name', '').strip()
        email      = request.form.get('email', '').strip()
        username   = request.form['username'].strip()
        password   = request.form['password']
        confirm    = request.form['confirm']

        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('student_register'))

        hashed = generate_password_hash(password)

        db = get_db()
        try:
            db.execute(
                """INSERT INTO students
                   (name, roll_no, class_name, email, username, password)
                   VALUES (?,?,?,?,?,?)""",
                (name, roll_no, class_name, email, username, hashed)
            )
            db.commit()
            flash('Account created! Please log in.', 'success')
            return redirect(url_for('student_login'))
        except sqlite3.IntegrityError:
            flash('Roll number or username already exists.', 'danger')

    return render_template('student_register.html')


@app.route('/student/logout')
def student_logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


# ═══════════════════════════════════════════════════════════════════
#  ROUTES: TEACHER DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@app.route('/teacher/dashboard')
@teacher_required           # ← the decorator ensures the teacher is logged in
def teacher_dashboard():
    """
    Show key stats:
      - Total students
      - Total classes held (distinct dates in attendance table)
      - How many students were present / absent today
      - Last 10 attendance records
      - List of students below 75% attendance
    """
    db         = get_db()
    today_str  = date.today().isoformat()  # e.g. '2024-05-15'

    # Fetch the logged-in teacher's details
    teacher = db.execute(
        "SELECT * FROM teachers WHERE id = ?", (session['teacher_id'],)
    ).fetchone()

    # Count all registered students
    total_students = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]

    # Count distinct attendance dates (= number of classes held)
    total_classes = db.execute(
        "SELECT COUNT(DISTINCT date) FROM attendance"
    ).fetchone()[0]

    # Present and absent counts for today
    present_today = db.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND status='Present'",
        (today_str,)
    ).fetchone()[0]

    absent_today = db.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND status='Absent'",
        (today_str,)
    ).fetchone()[0]

    # Most recent 10 attendance records (joined with student info)
    recent_records = db.execute("""
        SELECT a.date, a.status, s.name AS student_name, s.roll_no
        FROM   attendance a
        JOIN   students   s ON s.id = a.student_id
        ORDER  BY a.date DESC, a.id DESC
        LIMIT  10
    """).fetchall()

    # Students below 75% attendance — for the alert widget
    all_students = db.execute("SELECT * FROM students").fetchall()
    low_attendance = []
    for s in all_students:
        total  = db.execute(
            "SELECT COUNT(*) FROM attendance WHERE student_id=?", (s['id'],)
        ).fetchone()[0]
        present = db.execute(
            "SELECT COUNT(*) FROM attendance WHERE student_id=? AND status='Present'",
            (s['id'],)
        ).fetchone()[0]
        pct = calculate_percentage(present, total)
        if pct < 75 and total > 0:
            low_attendance.append({'student_name': s['name'], 'percentage': pct})

    return render_template(
        'teacher_dashboard.html',
        teacher        = teacher,
        today          = today_str,
        total_students = total_students,
        total_classes  = total_classes,
        present_today  = present_today,
        absent_today   = absent_today,
        recent_records = recent_records,
        low_attendance = low_attendance,
    )


# ═══════════════════════════════════════════════════════════════════
#  ROUTES: MARK ATTENDANCE
# ═══════════════════════════════════════════════════════════════════

@app.route('/teacher/attendance', methods=['GET', 'POST'])
@teacher_required
def mark_attendance():
    """
    GET  → show all students with their current status for the selected date.
    POST → save each student's Present/Absent status to the database.

    We use INSERT OR REPLACE so that re-submitting the form for the same
    date simply updates the record instead of creating a duplicate.
    """
    db            = get_db()
    today_str     = date.today().isoformat()
    selected_date = request.args.get('date', today_str)   # from URL ?date=

    if request.method == 'POST':
        selected_date = request.form.get('date', today_str)
        students      = db.execute("SELECT * FROM students ORDER BY name").fetchall()
        saved         = 0

        for student in students:
            # The radio button name is  status_<student_id>
            status_key = f'status_{student["id"]}'
            status = request.form.get(status_key)

            if status in ('Present', 'Absent'):
                # INSERT OR REPLACE handles both new records and updates
                db.execute("""
                    INSERT OR REPLACE INTO attendance
                        (student_id, teacher_id, date, status)
                    VALUES (?, ?, ?, ?)
                """, (student['id'], session['teacher_id'], selected_date, status))
                saved += 1

        db.commit()
        flash(f'Attendance saved for {selected_date} — {saved} records updated.', 'success')
        return redirect(url_for('mark_attendance', date=selected_date))

    # GET: fetch all students and their existing status for the selected date
    students = db.execute("SELECT * FROM students ORDER BY name").fetchall()

    # Build a dict {student_id: status} so the template can pre-select radio buttons
    existing_rows = db.execute(
        "SELECT student_id, status FROM attendance WHERE date=?",
        (selected_date,)
    ).fetchall()
    existing = {row['student_id']: row['status'] for row in existing_rows}

    return render_template(
        'mark_attendance.html',
        students      = students,
        selected_date = selected_date,
        existing      = existing,
        today         = today_str,
    )


# ═══════════════════════════════════════════════════════════════════
#  ROUTES: MANAGE STUDENTS (Teacher only)
# ═══════════════════════════════════════════════════════════════════

@app.route('/teacher/students', methods=['GET', 'POST'])
@teacher_required
def manage_students():
    """
    GET  → show list of all students.
    POST → add a new student record (teacher can add students manually,
           separate from student self-registration).
    """
    db = get_db()

    if request.method == 'POST':
        name       = request.form['name'].strip()
        roll_no    = request.form['roll_no'].strip()
        class_name = request.form.get('class_name', '').strip()
        email      = request.form.get('email', '').strip()

        try:
            db.execute(
                "INSERT INTO students (name, roll_no, class_name, email) VALUES (?,?,?,?)",
                (name, roll_no, class_name, email)
            )
            db.commit()
            flash(f'{name} added successfully.', 'success')
        except sqlite3.IntegrityError:
            flash(f'Roll number "{roll_no}" already exists.', 'danger')

        return redirect(url_for('manage_students'))

    students = db.execute(
        "SELECT * FROM students ORDER BY name"
    ).fetchall()

    return render_template('manage_students.html', students=students)


@app.route('/teacher/students/delete/<int:student_id>', methods=['POST'])
@teacher_required
def delete_student(student_id):
    """
    Delete a student and all their attendance records.
    The CASCADE foreign key in the schema handles the attendance rows automatically.
    """
    db = get_db()
    # First, fetch the name for the flash message
    student = db.execute(
        "SELECT name FROM students WHERE id=?", (student_id,)
    ).fetchone()

    if student:
        db.execute("DELETE FROM students WHERE id=?", (student_id,))
        db.commit()
        flash(f'{student["name"]} removed from the system.', 'warning')
    else:
        flash('Student not found.', 'danger')

    return redirect(url_for('manage_students'))


# ═══════════════════════════════════════════════════════════════════
#  ROUTES: ATTENDANCE REPORTS (Teacher only)
# ═══════════════════════════════════════════════════════════════════

@app.route('/teacher/reports')
@teacher_required
def view_reports():
    """
    Generate attendance percentage for every student.

    Supports optional filters:
      - from_date / to_date   → date range
      - class_filter          → filter by class/batch
    """
    db = get_db()

    # Default date range: last 30 days → today
    today        = date.today()
    default_from = (today - timedelta(days=30)).isoformat()
    default_to   = today.isoformat()

    from_date    = request.args.get('from_date',    default_from)
    to_date      = request.args.get('to_date',      default_to)
    class_filter = request.args.get('class_filter', '')

    # All distinct classes for the filter dropdown
    classes = [
        row[0] for row in
        db.execute("SELECT DISTINCT class_name FROM students WHERE class_name IS NOT NULL AND class_name != ''").fetchall()
    ]

    # Fetch students (optionally filtered by class)
    if class_filter:
        students = db.execute(
            "SELECT * FROM students WHERE class_name=? ORDER BY name",
            (class_filter,)
        ).fetchall()
    else:
        students = db.execute("SELECT * FROM students ORDER BY name").fetchall()

    # Total number of distinct class days in the chosen date range
    total_days = db.execute("""
        SELECT COUNT(DISTINCT date) FROM attendance
        WHERE date BETWEEN ? AND ?
    """, (from_date, to_date)).fetchone()[0]

    # Build the report row for each student
    report_data = []
    for s in students:
        present = db.execute("""
            SELECT COUNT(*) FROM attendance
            WHERE student_id=? AND status='Present' AND date BETWEEN ? AND ?
        """, (s['id'], from_date, to_date)).fetchone()[0]

        absent = db.execute("""
            SELECT COUNT(*) FROM attendance
            WHERE student_id=? AND status='Absent' AND date BETWEEN ? AND ?
        """, (s['id'], from_date, to_date)).fetchone()[0]

        total      = present + absent
        percentage = calculate_percentage(present, total)

        report_data.append({
            'student_name': s['name'],
            'roll_no':      s['roll_no'],
            'class_name':   s['class_name'] or '',
            'present':      present,
            'absent':       absent,
            'total':        total,
            'percentage':   percentage,
        })

    # Summary stats for the stat cards
    avg_pct  = (
        round(sum(r['percentage'] for r in report_data) / len(report_data), 1)
        if report_data else 0
    )
    below_75 = sum(1 for r in report_data if r['percentage'] < 75)

    # Detailed day-by-day records for the bottom table
    detail_records = db.execute("""
        SELECT a.date, a.status, s.name AS student_name, s.roll_no
        FROM   attendance a
        JOIN   students   s ON s.id = a.student_id
        WHERE  a.date BETWEEN ? AND ?
        ORDER  BY a.date DESC, s.name
        LIMIT  100
    """, (from_date, to_date)).fetchall()

    return render_template(
        'reports.html',
        report_data    = report_data,
        detail_records = detail_records,
        from_date      = from_date,
        to_date        = to_date,
        class_filter   = class_filter,
        classes        = classes,
        total_days     = total_days,
        avg_pct        = avg_pct,
        below_75       = below_75,
    )


# ═══════════════════════════════════════════════════════════════════
#  ROUTES: STUDENT DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@app.route('/student/dashboard')
@student_required
def student_dashboard():
    """
    Show the logged-in student's own attendance summary:
      - Total classes held
      - Present count
      - Absent count
      - Auto-calculated percentage
      - How many classes needed to reach 75% (if below)
      - Full attendance history with teacher name
    """
    db         = get_db()
    student_id = session['student_id']

    # Fetch student info
    student = db.execute(
        "SELECT * FROM students WHERE id=?", (student_id,)
    ).fetchone()

    if not student:
        # Edge case: student row was deleted while they were logged in
        session.clear()
        flash('Your account was not found. Please log in again.', 'danger')
        return redirect(url_for('student_login'))

    # Attendance counts
    total_classes = db.execute(
        "SELECT COUNT(*) FROM attendance WHERE student_id=?",
        (student_id,)
    ).fetchone()[0]

    present_count = db.execute(
        "SELECT COUNT(*) FROM attendance WHERE student_id=? AND status='Present'",
        (student_id,)
    ).fetchone()[0]

    absent_count = total_classes - present_count

    # Auto-calculate percentage
    attendance_pct = calculate_percentage(present_count, total_classes)

    # Classes still needed to reach 75%
    classes_needed = 0
    if attendance_pct < 75:
        classes_needed = classes_needed_for_75(present_count, total_classes)

    # Full attendance history with teacher name (via LEFT JOIN)
    records = db.execute("""
        SELECT a.date, a.status, t.name AS teacher_name
        FROM   attendance a
        LEFT JOIN teachers t ON t.id = a.teacher_id
        WHERE  a.student_id = ?
        ORDER  BY a.date DESC
    """, (student_id,)).fetchall()

    return render_template(
        'student_dashboard.html',
        student        = student,
        total_classes  = total_classes,
        present_count  = present_count,
        absent_count   = absent_count,
        attendance_pct = attendance_pct,
        classes_needed = classes_needed,
        records        = records,
    )


# ═══════════════════════════════════════════════════════════════════
#  APPLICATION ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

# Use a dedicated app context to initialize the DB before the first request
with app.app_context():
    init_db()  # Creates tables if they don't exist yet

if __name__ == '__main__':
    # debug=True → auto-restarts server on code change + shows detailed error pages
    # NEVER use debug=True in a live production server
    app.run(debug=True, host='0.0.0.0', port=5000)
#=======================================================================
#FOR DEPLOYMENT
#=======================================================================
import os
port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)
