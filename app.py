"""
Fontaine Bros. Digital Asset Manager
=====================================
Supports both local SQLite (dev) and PostgreSQL (production/Railway).

Environment variables:
  DATABASE_URL   – PostgreSQL URL (auto-set by Railway; omit for local SQLite)
  SECRET_KEY     – Flask session secret  (generate a random string)
  TEAM_PASSWORD  – Password to access the app   (default: fontaine)
  UPLOAD_DIR     – Where to store uploaded images (default: ./data/uploads)
  PORT           – Port to listen on (default: 5000)
"""

import os
import io
import re
import uuid
import json
import sqlite3
import zipfile
import tempfile
import threading
import webbrowser
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime
from functools import wraps
from contextlib import contextmanager

# Optional PDF parser (installed via pypdf in requirements.txt)
try:
    from pypdf import PdfReader as _PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

from flask import (Flask, render_template, request, jsonify, send_file,
                   abort, redirect, url_for, session, flash)
from werkzeug.utils import secure_filename

app = Flask(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me-in-production')

DATABASE_URL   = os.environ.get('DATABASE_URL', '')
USE_POSTGRES   = bool(DATABASE_URL)
TEAM_PASSWORD  = os.environ.get('TEAM_PASSWORD', 'fontaine')

APP_DIR        = Path(__file__).parent.resolve()
DB_PATH        = APP_DIR / 'data' / 'projects.db'          # SQLite only
UPLOAD_DIR     = Path(os.environ.get('UPLOAD_DIR',
                       str(APP_DIR / 'data' / 'uploads')))
HEADSHOT_DIR   = UPLOAD_DIR / 'headshots'                  # employee headshots

ALLOWED_EXTS   = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'}
MAX_UPLOAD_MB  = 50

# ── DB placeholder token for each backend ──────────────────────────────────────
P = '%s' if USE_POSTGRES else '?'


# ── DB connection ──────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    if USE_POSTGRES:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        url = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        try:
            yield conn
        finally:
            conn.close()
    else:
        (APP_DIR / 'data').mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = MEMORY")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()


def _sqlite_table_status():
    """Return 'has_tables', 'empty', or 'unreadable' for the SQLite DB file."""
    if not DB_PATH.exists():
        return 'empty'
    try:
        conn = sqlite3.connect(str(DB_PATH))
        n = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='projects'"
        ).fetchone()[0]
        conn.close()
        return 'has_tables' if n > 0 else 'empty'
    except Exception:
        # Can't read the file — leave it alone rather than risk deleting live data.
        return 'unreadable'


def _delete_sqlite_files():
    """Remove the DB file and any leftover journal/wal/shm files."""
    for suffix in ('', '-journal', '-wal', '-shm'):
        p = Path(str(DB_PATH) + suffix)
        try:
            p.unlink()
        except (FileNotFoundError, OSError):
            pass


def init_db():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    HEADSHOT_DIR.mkdir(parents=True, exist_ok=True)

    if not USE_POSTGRES:
        (APP_DIR / 'data').mkdir(parents=True, exist_ok=True)
        # Only wipe the DB if we can CONFIRM it has no tables.
        status = _sqlite_table_status()
        if status == 'empty':
            _delete_sqlite_files()

    with get_db() as db:
        if USE_POSTGRES:
            db.execute('''
                CREATE TABLE IF NOT EXISTS projects (
                    id              SERIAL      PRIMARY KEY,
                    name            TEXT        UNIQUE NOT NULL,
                    description     TEXT        DEFAULT '',
                    resume_description TEXT     DEFAULT '',
                    square_footage  TEXT        DEFAULT '',
                    location        TEXT        DEFAULT '',
                    year_completed  TEXT        DEFAULT '',
                    project_type    TEXT        DEFAULT '',
                    contract_value  TEXT        DEFAULT '',
                    architect       TEXT        DEFAULT '',
                    opm             TEXT        DEFAULT '',
                    status          TEXT        DEFAULT '',
                    client          TEXT        DEFAULT '',
                    sector          TEXT        DEFAULT '',
                    hero_image_id   INTEGER,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS employees (
                    id                SERIAL      PRIMARY KEY,
                    name              TEXT        NOT NULL,
                    title             TEXT        DEFAULT '',
                    work_location     TEXT        DEFAULT '',
                    bio               TEXT        DEFAULT '',
                    education         TEXT        DEFAULT '',
                    affiliations      TEXT        DEFAULT '',
                    ref_info          TEXT        DEFAULT '',
                    headshot_filename TEXT        DEFAULT NULL,
                    created_at        TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS people (
                    id          SERIAL  PRIMARY KEY,
                    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    category    TEXT    NOT NULL,
                    name        TEXT    NOT NULL,
                    role        TEXT    DEFAULT '',
                    employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS images (
                    id           SERIAL   PRIMARY KEY,
                    project_id   INTEGER  NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    filename     TEXT     NOT NULL,
                    original_name TEXT   NOT NULL,
                    uploaded_at  TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
        else:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT    UNIQUE NOT NULL,
                    description     TEXT    DEFAULT '',
                    resume_description TEXT DEFAULT '',
                    square_footage  TEXT    DEFAULT '',
                    location        TEXT    DEFAULT '',
                    year_completed  TEXT    DEFAULT '',
                    project_type    TEXT    DEFAULT '',
                    contract_value  TEXT    DEFAULT '',
                    architect       TEXT    DEFAULT '',
                    opm             TEXT    DEFAULT '',
                    status          TEXT    DEFAULT '',
                    client          TEXT    DEFAULT '',
                    sector          TEXT    DEFAULT '',
                    hero_image_id   INTEGER,
                    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS employees (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    name              TEXT    NOT NULL,
                    title             TEXT    DEFAULT '',
                    work_location     TEXT    DEFAULT '',
                    bio               TEXT    DEFAULT '',
                    education         TEXT    DEFAULT '',
                    affiliations      TEXT    DEFAULT '',
                    ref_info          TEXT    DEFAULT '',
                    headshot_filename TEXT    DEFAULT NULL,
                    created_at        TEXT    DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS people (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id  INTEGER NOT NULL,
                    category    TEXT    NOT NULL,
                    name        TEXT    NOT NULL,
                    role        TEXT    DEFAULT '',
                    employee_id INTEGER,
                    FOREIGN KEY (project_id)  REFERENCES projects(id)  ON DELETE CASCADE,
                    FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS images (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id    INTEGER NOT NULL,
                    filename      TEXT    NOT NULL,
                    original_name TEXT    NOT NULL,
                    uploaded_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
            ''')
        db.commit()


def migrate_db():
    """Add new columns introduced after initial release. Safe to run repeatedly."""
    with get_db() as db:
        if USE_POSTGRES:
            for sql in [
                "ALTER TABLE projects  ADD COLUMN IF NOT EXISTS address TEXT DEFAULT ''",
                "ALTER TABLE projects  ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION",
                "ALTER TABLE projects  ADD COLUMN IF NOT EXISTS lng DOUBLE PRECISION",
                "ALTER TABLE projects  ADD COLUMN IF NOT EXISTS sector TEXT DEFAULT ''",
                "ALTER TABLE projects  ADD COLUMN IF NOT EXISTS resume_description TEXT DEFAULT ''",
                # employees table (may not exist in old DBs)
                """CREATE TABLE IF NOT EXISTS employees (
                    id                SERIAL      PRIMARY KEY,
                    name              TEXT        NOT NULL,
                    title             TEXT        DEFAULT '',
                    work_location     TEXT        DEFAULT '',
                    bio               TEXT        DEFAULT '',
                    education         TEXT        DEFAULT '',
                    affiliations      TEXT        DEFAULT '',
                    ref_info          TEXT        DEFAULT '',
                    headshot_filename TEXT        DEFAULT NULL,
                    created_at        TIMESTAMPTZ DEFAULT NOW()
                )""",
                "ALTER TABLE employees ADD COLUMN IF NOT EXISTS education     TEXT DEFAULT ''",
                "ALTER TABLE employees ADD COLUMN IF NOT EXISTS affiliations   TEXT DEFAULT ''",
                "ALTER TABLE employees ADD COLUMN IF NOT EXISTS ref_info       TEXT DEFAULT ''",
                "ALTER TABLE people ADD COLUMN IF NOT EXISTS employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL",
            ]:
                try:
                    db.execute(sql)
                except Exception:
                    pass
        else:
            for col, typedef in [
                ('address',            "TEXT DEFAULT ''"),
                ('lat',                'REAL'),
                ('lng',                'REAL'),
                ('sector',             "TEXT DEFAULT ''"),
                ('resume_description', "TEXT DEFAULT ''"),
            ]:
                try:
                    db.execute(f'ALTER TABLE projects ADD COLUMN {col} {typedef}')
                except Exception:
                    pass
            # employees table
            try:
                db.execute('''CREATE TABLE IF NOT EXISTS employees (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    name              TEXT    NOT NULL,
                    title             TEXT    DEFAULT '',
                    work_location     TEXT    DEFAULT '',
                    bio               TEXT    DEFAULT '',
                    education         TEXT    DEFAULT '',
                    affiliations      TEXT    DEFAULT '',
                    ref_info          TEXT    DEFAULT '',
                    headshot_filename TEXT    DEFAULT NULL,
                    created_at        TEXT    DEFAULT CURRENT_TIMESTAMP
                )''')
            except Exception:
                pass
            # new employee columns
            for col in ('education', 'affiliations', 'ref_info'):
                try:
                    db.execute(f"ALTER TABLE employees ADD COLUMN {col} TEXT DEFAULT ''")
                except Exception:
                    pass
            # employee_id on people
            try:
                db.execute('ALTER TABLE people ADD COLUMN employee_id INTEGER')
            except Exception:
                pass
        db.commit()


def geocode_address(address):
    """Return (lat, lng) for an address string using OSM Nominatim (free, no key)."""
    if not address or not address.strip():
        return None, None
    try:
        params = urllib.parse.urlencode({'q': address.strip(), 'format': 'json', 'limit': 1})
        url    = f'https://nominatim.openstreetmap.org/search?{params}'
        req    = urllib.request.Request(
            url, headers={'User-Agent': 'FontaineBrosDAM/1.0 nsommer@fontainebros.com'}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        print(f'Geocoding failed for "{address}": {e}')
    return None, None


# ── Auth ───────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if pw == TEAM_PASSWORD:
            session['authenticated'] = True
            session.permanent = True
            return redirect(request.args.get('next') or url_for('index'))
        flash('Incorrect password. Please try again.')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Helpers ────────────────────────────────────────────────────────────────────
def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTS


def upload_dir_for(project_id):
    d = UPLOAD_DIR / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Pages ──────────────────────────────────────────────────────────────────────
SECTOR_CHOICES = ['K-12', 'Colleges & Universities', 'Healthcare', 'Private Schools', 'General']


@app.route('/')
@login_required
def index():
    q      = request.args.get('q', '').strip()
    sector = request.args.get('sector', '').strip()

    with get_db() as db:
        if q:
            like = f'%{q}%'
            sector_clause = f' AND p.sector = {P}' if sector else ''
            params = (like,) * 13 + ((sector,) if sector else ())
            rows = db.execute(f'''
                SELECT DISTINCT p.*, i.filename AS hero_file,
                       (SELECT COUNT(*) FROM images WHERE project_id = p.id) AS image_count
                FROM projects p
                LEFT JOIN people pe ON p.id = pe.project_id
                LEFT JOIN images  i  ON i.id  = p.hero_image_id
                WHERE  (p.name           LIKE {P}
                   OR  p.description    LIKE {P}
                   OR  p.location       LIKE {P}
                   OR  p.architect      LIKE {P}
                   OR  p.opm            LIKE {P}
                   OR  p.project_type   LIKE {P}
                   OR  p.client         LIKE {P}
                   OR  p.year_completed LIKE {P}
                   OR  p.square_footage LIKE {P}
                   OR  p.status         LIKE {P}
                   OR  p.contract_value LIKE {P}
                   OR  pe.name          LIKE {P}
                   OR  pe.role          LIKE {P})
                {sector_clause}
                ORDER BY p.name
            ''', params).fetchall()
        elif sector:
            rows = db.execute(f'''
                SELECT p.*, i.filename AS hero_file,
                       (SELECT COUNT(*) FROM images WHERE project_id = p.id) AS image_count
                FROM projects p
                LEFT JOIN images i ON i.id = p.hero_image_id
                WHERE p.sector = {P}
                ORDER BY p.name
            ''', (sector,)).fetchall()
        else:
            rows = db.execute(f'''
                SELECT p.*, i.filename AS hero_file,
                       (SELECT COUNT(*) FROM images WHERE project_id = p.id) AS image_count
                FROM projects p
                LEFT JOIN images i ON i.id = p.hero_image_id
                ORDER BY p.name
            ''').fetchall()

    projects = []
    for r in rows:
        d = row_to_dict(r)
        if not d.get('hero_file'):
            with get_db() as db2:
                first = db2.execute(
                    f'SELECT filename FROM images WHERE project_id = {P} ORDER BY id LIMIT 1',
                    (d['id'],)
                ).fetchone()
            d['hero_file'] = first['filename'] if first else None
        projects.append(d)

    return render_template('index.html', projects=projects, q=q,
                           sector=sector, sector_choices=SECTOR_CHOICES)


@app.route('/map')
@login_required
def map_view():
    with get_db() as db:
        all_rows = db.execute('SELECT project_type, status FROM projects').fetchall()
        project_types = sorted({r['project_type'] for r in all_rows if r['project_type']})
        statuses      = sorted({r['status']       for r in all_rows if r['status']})

        mapped = db.execute(f'''
            SELECT p.*, i.filename AS hero_file
            FROM   projects p
            LEFT JOIN images i ON i.id = p.hero_image_id
            WHERE  p.lat IS NOT NULL AND p.lng IS NOT NULL
            ORDER BY p.name
        ''').fetchall()

        unmapped = db.execute(f'''
            SELECT id, name, address
            FROM   projects
            WHERE  lat IS NULL OR lng IS NULL
            ORDER BY name
        ''').fetchall()

    pins = []
    for r in mapped:
        d = row_to_dict(r)
        if not d.get('hero_file'):
            with get_db() as db2:
                first = db2.execute(
                    f'SELECT filename FROM images WHERE project_id = {P} ORDER BY id LIMIT 1',
                    (d['id'],)
                ).fetchone()
            d['hero_file'] = first['filename'] if first else None
        pins.append({
            'id':            d['id'],
            'name':          d['name'],
            'address':       d.get('address', ''),
            'lat':           d['lat'],
            'lng':           d['lng'],
            'status':        d.get('status', ''),
            'project_type':  d.get('project_type', ''),
            'client':        d.get('client', ''),
            'year_completed':d.get('year_completed', ''),
            'square_footage':d.get('square_footage', ''),
            'hero_file':     d.get('hero_file'),
            'url':           url_for('project_detail', project_id=d['id']),
        })

    return render_template(
        'map.html',
        pins          = pins,
        unmapped      = [row_to_dict(r) for r in unmapped],
        project_types = project_types,
        statuses      = statuses,
    )


@app.route('/project/<int:project_id>')
@login_required
def project_detail(project_id):
    with get_db() as db:
        p = db.execute(
            f'SELECT * FROM projects WHERE id = {P}', (project_id,)
        ).fetchone()
        if not p:
            abort(404)

        engineers = db.execute(
            f"SELECT pe.* FROM people pe WHERE pe.project_id = {P} AND pe.category = 'engineer' ORDER BY pe.name",
            (project_id,)
        ).fetchall()

        # Join team members with employee data for headshots
        team = db.execute(f"""
            SELECT pe.id, pe.project_id, pe.category, pe.name, pe.role, pe.employee_id,
                   e.headshot_filename, e.title AS emp_title
            FROM   people pe
            LEFT JOIN employees e ON pe.employee_id = e.id
            WHERE  pe.project_id = {P} AND pe.category = 'team_member'
            ORDER BY pe.name
        """, (project_id,)).fetchall()

        images = db.execute(
            f'SELECT * FROM images WHERE project_id = {P} ORDER BY id',
            (project_id,)
        ).fetchall()

    project = row_to_dict(p)

    hero_img = None
    if project.get('hero_image_id'):
        for img in images:
            d = row_to_dict(img)
            if d['id'] == project['hero_image_id']:
                hero_img = d
                break
    if not hero_img and images:
        hero_img = row_to_dict(images[0])

    return render_template(
        'project.html',
        project    = project,
        engineers  = [row_to_dict(e) for e in engineers],
        team       = [row_to_dict(t) for t in team],
        images     = [row_to_dict(i) for i in images],
        hero_img   = hero_img,
    )


# ── Employees ──────────────────────────────────────────────────────────────────
@app.route('/employees')
@login_required
def employees_list():
    with get_db() as db:
        rows = db.execute('SELECT * FROM employees ORDER BY name').fetchall()
    return render_template('employees.html', employees=[row_to_dict(r) for r in rows])


@app.route('/employee/<int:employee_id>')
@login_required
def employee_detail(employee_id):
    with get_db() as db:
        emp = db.execute(
            f'SELECT * FROM employees WHERE id = {P}', (employee_id,)
        ).fetchone()
        if not emp:
            abort(404)

        # Projects this employee has worked on
        project_rows = db.execute(f'''
            SELECT DISTINCT p.id, p.name, p.status, p.project_type,
                   p.year_completed, p.client, p.location,
                   i.filename AS hero_file,
                   pe.role AS team_role
            FROM   people pe
            JOIN   projects p ON p.id = pe.project_id
            LEFT JOIN images i ON i.id = p.hero_image_id
            WHERE  pe.employee_id = {P}
            ORDER BY p.year_completed DESC, p.name
        ''', (employee_id,)).fetchall()

    employee = row_to_dict(emp)
    projects = []
    for r in project_rows:
        d = row_to_dict(r)
        if not d.get('hero_file'):
            with get_db() as db2:
                first = db2.execute(
                    f'SELECT filename FROM images WHERE project_id = {P} ORDER BY id LIMIT 1',
                    (d['id'],)
                ).fetchone()
            d['hero_file'] = first['filename'] if first else None
        projects.append(d)

    return render_template('employee.html', employee=employee, projects=projects)


@app.route('/api/employees/list')
@login_required
def api_employees_list():
    with get_db() as db:
        rows = db.execute(
            'SELECT id, name, title, work_location, headshot_filename FROM employees ORDER BY name'
        ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route('/api/employees/create', methods=['POST'])
@login_required
def create_employee():
    data = request.get_json(force=True) or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify(error='Name is required'), 400

    with get_db() as db:
        if USE_POSTGRES:
            cur = db.execute(
                f'INSERT INTO employees (name, title, work_location) VALUES ({P},{P},{P}) RETURNING id',
                (name, data.get('title', '').strip(), data.get('work_location', '').strip())
            )
            emp_id = cur.fetchone()['id']
        else:
            cur = db.execute(
                f'INSERT INTO employees (name, title, work_location) VALUES ({P},{P},{P})',
                (name, data.get('title', '').strip(), data.get('work_location', '').strip())
            )
            emp_id = cur.lastrowid
        db.commit()

    return jsonify(success=True, id=emp_id,
                   redirect=url_for('employee_detail', employee_id=emp_id))


@app.route('/api/employee/<int:employee_id>/save', methods=['POST'])
@login_required
def save_employee(employee_id):
    data = request.get_json(force=True) or {}
    with get_db() as db:
        emp = db.execute(f'SELECT id FROM employees WHERE id = {P}', (employee_id,)).fetchone()
        if not emp:
            return jsonify(error='Employee not found'), 404
        db.execute(f'''
            UPDATE employees SET
                name          = {P},
                title         = {P},
                work_location = {P},
                bio           = {P},
                education     = {P},
                affiliations  = {P},
                ref_info      = {P}
            WHERE id = {P}
        ''', (
            data.get('name', '').strip(),
            data.get('title', '').strip(),
            data.get('work_location', '').strip(),
            data.get('bio', '').strip(),
            data.get('education', '').strip(),
            data.get('affiliations', '').strip(),
            data.get('ref_info', '').strip(),
            employee_id,
        ))
        db.commit()
    return jsonify(success=True)


@app.route('/api/employee/<int:employee_id>/delete', methods=['DELETE'])
@login_required
def delete_employee(employee_id):
    with get_db() as db:
        emp = db.execute(
            f'SELECT headshot_filename FROM employees WHERE id = {P}', (employee_id,)
        ).fetchone()
        if emp and emp['headshot_filename']:
            path = HEADSHOT_DIR / emp['headshot_filename']
            if path.exists():
                path.unlink()
        db.execute(f'DELETE FROM employees WHERE id = {P}', (employee_id,))
        db.commit()
    return jsonify(success=True)


@app.route('/api/employee/<int:employee_id>/upload_headshot', methods=['POST'])
@login_required
def upload_headshot(employee_id):
    with get_db() as db:
        emp = db.execute(
            f'SELECT * FROM employees WHERE id = {P}', (employee_id,)
        ).fetchone()
        if not emp:
            return jsonify(error='Employee not found'), 404

    f = request.files.get('headshot')
    if not f or not f.filename:
        return jsonify(error='No file provided'), 400
    if not allowed_file(f.filename):
        return jsonify(error='Unsupported file type'), 400

    # Delete old headshot if any
    old = emp['headshot_filename']
    if old:
        old_path = HEADSHOT_DIR / old
        if old_path.exists():
            old_path.unlink()

    ext      = f.filename.rsplit('.', 1)[1].lower()
    filename = f'{uuid.uuid4().hex}.{ext}'
    HEADSHOT_DIR.mkdir(parents=True, exist_ok=True)
    f.save(str(HEADSHOT_DIR / filename))

    with get_db() as db:
        db.execute(
            f'UPDATE employees SET headshot_filename = {P} WHERE id = {P}',
            (filename, employee_id)
        )
        db.commit()

    return jsonify(success=True, filename=filename,
                   url=url_for('serve_headshot', filename=filename))


@app.route('/headshots/<filename>')
@login_required
def serve_headshot(filename):
    path = (HEADSHOT_DIR / filename).resolve()
    try:
        path.relative_to(HEADSHOT_DIR.resolve())
    except ValueError:
        abort(403)
    if not path.is_file():
        abort(404)
    return send_file(str(path))


# ── API: Project CRUD ──────────────────────────────────────────────────────────
@app.route('/api/projects/create', methods=['POST'])
@login_required
def create_project():
    data = request.get_json(force=True) or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify(error='Project name is required'), 400

    with get_db() as db:
        existing = db.execute(
            f'SELECT id FROM projects WHERE name = {P}', (name,)
        ).fetchone()
        if existing:
            return jsonify(error='A project with that name already exists'), 409

        if USE_POSTGRES:
            cur = db.execute(
                f'INSERT INTO projects (name) VALUES ({P}) RETURNING id', (name,)
            )
            project_id = cur.fetchone()['id']
        else:
            cur = db.execute(f'INSERT INTO projects (name) VALUES ({P})', (name,))
            project_id = cur.lastrowid
        db.commit()

    return jsonify(success=True, id=project_id,
                   redirect=url_for('project_detail', project_id=project_id))


@app.route('/api/project/<int:project_id>/save', methods=['POST'])
@login_required
def save_project(project_id):
    data = request.get_json(force=True) or {}
    new_address = data.get('address', '').strip()

    with get_db() as db:
        old = db.execute(f"SELECT address FROM projects WHERE id = {P}", (project_id,)).fetchone()
    old_address = (old['address'] or '') if old else ''

    if new_address and new_address != old_address:
        lat, lng = geocode_address(new_address)
    elif not new_address:
        lat, lng = None, None
    else:
        with get_db() as db:
            row = db.execute(f"SELECT lat, lng FROM projects WHERE id = {P}", (project_id,)).fetchone()
        lat = row['lat'] if row else None
        lng = row['lng'] if row else None

    with get_db() as db:
        db.execute(f'''
            UPDATE projects SET
                description        = {P},
                resume_description = {P},
                square_footage     = {P},
                location       = {P},
                year_completed = {P},
                project_type   = {P},
                contract_value = {P},
                architect      = {P},
                opm            = {P},
                status         = {P},
                client         = {P},
                sector         = {P},
                address        = {P},
                lat            = {P},
                lng            = {P}
            WHERE id = {P}
        ''', (
            data.get('description',        ''),
            data.get('resume_description', ''),
            data.get('square_footage',     ''),
            data.get('location',       ''),
            data.get('year_completed', ''),
            data.get('project_type',   ''),
            data.get('contract_value', ''),
            data.get('architect',      ''),
            data.get('opm',            ''),
            data.get('status',         ''),
            data.get('client',         ''),
            data.get('sector',         ''),
            new_address,
            lat,
            lng,
            project_id,
        ))
        db.commit()

    geocoded = lat is not None
    return jsonify(success=True, geocoded=geocoded, lat=lat, lng=lng)


@app.route('/api/project/<int:project_id>/delete', methods=['DELETE'])
@login_required
def delete_project(project_id):
    with get_db() as db:
        imgs = db.execute(
            f'SELECT filename FROM images WHERE project_id = {P}', (project_id,)
        ).fetchall()
        for img in imgs:
            path = UPLOAD_DIR / str(project_id) / img['filename']
            if path.exists():
                path.unlink()

        db.execute(f'DELETE FROM projects WHERE id = {P}', (project_id,))
        db.commit()

    proj_dir = UPLOAD_DIR / str(project_id)
    if proj_dir.exists():
        try:
            proj_dir.rmdir()
        except OSError:
            pass

    return jsonify(success=True)


# ── API: People ────────────────────────────────────────────────────────────────
@app.route('/api/project/<int:project_id>/people/add', methods=['POST'])
@login_required
def add_person(project_id):
    data = request.get_json(force=True) or {}
    category    = data.get('category', 'team_member')
    employee_id = data.get('employee_id')  # may be None for engineers

    # For team_members, name + role come from the linked employee
    if category == 'team_member' and employee_id:
        with get_db() as db:
            emp = db.execute(
                f'SELECT * FROM employees WHERE id = {P}', (employee_id,)
            ).fetchone()
        if not emp:
            return jsonify(error='Employee not found'), 404
        name = emp['name']
        role = data.get('role', '').strip() or emp['title'] or ''
    else:
        name = data.get('name', '').strip()
        role = data.get('role', '').strip()
        employee_id = None

    if not name:
        return jsonify(error='Name is required'), 400

    with get_db() as db:
        proj = db.execute(f'SELECT id FROM projects WHERE id = {P}', (project_id,)).fetchone()
        if not proj:
            return jsonify(error='Project not found'), 404

        # Prevent duplicate employee on same project
        if employee_id:
            existing = db.execute(
                f"SELECT id FROM people WHERE project_id = {P} AND employee_id = {P}",
                (project_id, employee_id)
            ).fetchone()
            if existing:
                return jsonify(error=f'{name} is already on this project'), 409

        if USE_POSTGRES:
            cur = db.execute(
                f'INSERT INTO people (project_id, category, name, role, employee_id) VALUES ({P},{P},{P},{P},{P}) RETURNING id',
                (project_id, category, name, role, employee_id)
            )
            person_id = cur.fetchone()['id']
        else:
            cur = db.execute(
                f'INSERT INTO people (project_id, category, name, role, employee_id) VALUES ({P},{P},{P},{P},{P})',
                (project_id, category, name, role, employee_id)
            )
            person_id = cur.lastrowid
        db.commit()

    # Return headshot info so the UI can update immediately
    headshot_url = None
    if employee_id and emp:
        hs = emp['headshot_filename']
        if hs:
            headshot_url = url_for('serve_headshot', filename=hs)

    return jsonify(success=True, id=person_id, name=name, role=role,
                   employee_id=employee_id, headshot_url=headshot_url)


@app.route('/api/people/<int:person_id>/delete', methods=['DELETE'])
@login_required
def delete_person(person_id):
    with get_db() as db:
        db.execute(f'DELETE FROM people WHERE id = {P}', (person_id,))
        db.commit()
    return jsonify(success=True)


# ── API: Images ────────────────────────────────────────────────────────────────
@app.route('/api/project/<int:project_id>/upload', methods=['POST'])
@login_required
def upload_images(project_id):
    with get_db() as db:
        proj = db.execute(f'SELECT id FROM projects WHERE id = {P}', (project_id,)).fetchone()
        if not proj:
            return jsonify(error='Project not found'), 404

    files   = request.files.getlist('files')
    saved   = []
    errors  = []

    for f in files:
        if not f or not f.filename:
            continue
        if not allowed_file(f.filename):
            errors.append(f'{f.filename}: unsupported file type')
            continue

        ext      = f.filename.rsplit('.', 1)[1].lower()
        filename = f'{uuid.uuid4().hex}.{ext}'
        dest     = upload_dir_for(project_id) / filename
        f.save(str(dest))

        with get_db() as db:
            if USE_POSTGRES:
                cur = db.execute(
                    f'INSERT INTO images (project_id, filename, original_name) VALUES ({P},{P},{P}) RETURNING id',
                    (project_id, filename, secure_filename(f.filename))
                )
                img_id = cur.fetchone()['id']
                proj = db.execute(f'SELECT hero_image_id FROM projects WHERE id = {P}', (project_id,)).fetchone()
                if not proj['hero_image_id']:
                    db.execute(f'UPDATE projects SET hero_image_id = {P} WHERE id = {P}', (img_id, project_id))
            else:
                cur = db.execute(
                    f'INSERT INTO images (project_id, filename, original_name) VALUES ({P},{P},{P})',
                    (project_id, filename, secure_filename(f.filename))
                )
                img_id = cur.lastrowid
                proj = db.execute(f'SELECT hero_image_id FROM projects WHERE id = {P}', (project_id,)).fetchone()
                if not proj['hero_image_id']:
                    db.execute(f'UPDATE projects SET hero_image_id = {P} WHERE id = {P}', (img_id, project_id))
            db.commit()

        saved.append({'id': img_id, 'filename': filename,
                      'original_name': secure_filename(f.filename),
                      'url': url_for('serve_image', project_id=project_id, filename=filename)})

    return jsonify(saved=saved, errors=errors)


@app.route('/api/image/<int:image_id>/set_hero', methods=['POST'])
@login_required
def set_hero(image_id):
    with get_db() as db:
        img = db.execute(f'SELECT * FROM images WHERE id = {P}', (image_id,)).fetchone()
        if not img:
            return jsonify(error='Image not found'), 404
        db.execute(
            f'UPDATE projects SET hero_image_id = {P} WHERE id = {P}',
            (image_id, img['project_id'])
        )
        db.commit()
    return jsonify(success=True)


@app.route('/api/image/<int:image_id>/delete', methods=['DELETE'])
@login_required
def delete_image(image_id):
    with get_db() as db:
        img = db.execute(f'SELECT * FROM images WHERE id = {P}', (image_id,)).fetchone()
        if not img:
            return jsonify(error='Image not found'), 404

        db.execute(
            f'UPDATE projects SET hero_image_id = NULL WHERE id = {P} AND hero_image_id = {P}',
            (img['project_id'], image_id)
        )
        db.execute(f'DELETE FROM images WHERE id = {P}', (image_id,))
        db.commit()

    path = UPLOAD_DIR / str(img['project_id']) / img['filename']
    if path.exists():
        path.unlink()

    return jsonify(success=True)


# ── Serve uploaded files ────────────────────────────────────────────────────────
@app.route('/uploads/<int:project_id>/<filename>')
@login_required
def serve_image(project_id, filename):
    path = (UPLOAD_DIR / str(project_id) / filename).resolve()
    try:
        path.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        abort(403)
    if not path.is_file():
        abort(404)
    return send_file(str(path))


# ── Resume PDF export ──────────────────────────────────────────────────────────
@app.route('/api/employee/<int:employee_id>/export_resume', methods=['POST'])
@login_required
def export_resume(employee_id):
    """
    Generate a professional resume PDF matching the Fontaine Bros. branded template.

    Layout (measured from the master InDesign template):
      • Right column starts at x=407.5 (66.6 % of 612pt page width)
      • Top 307pt of right column: light-gray background + headshot photo
      • Bottom of right column: maroon panel (#8D0134) with Education /
        Affiliations / References in gold headers + white body text
      • Fontaine logo: 3 stacked maroon rectangles, top-right corner
      • Left column: first-name bold gold (30pt), last-name dark (25pt),
        title spaced-caps maroon; then bio (bold) + Relevant Experience
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.colors import HexColor, white as RL_WHITE
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame,
                                        Paragraph, Spacer, HRFlowable,
                                        KeepTogether, NextPageTemplate, Flowable)
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return jsonify(error='PDF library (reportlab) not installed on this server.'), 503

    # ── Register custom fonts ────────────────────────────────────────────────────
    _FONT_DIR = APP_DIR / 'static' / 'fonts'

    def _load_font(reg_name, filename, fallback=None):
        """Register a font from static/fonts/. Returns (registered_name, ok_bool)."""
        path = _FONT_DIR / filename
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont(reg_name, str(path)))
                return reg_name, True
            except Exception:
                pass
        return fallback or 'Helvetica', False

    # Gotham Black — first name + title
    _GothamBlack,  _HAS_GOTHAM_BLACK  = _load_font('GothamBlack', 'GothamHTF-Black.ttf',  'Helvetica-Bold')
    # Gotham Light — last name
    _GothamLight,  _HAS_GOTHAM_LIGHT  = _load_font('GothamLight', 'Gotham-HTF-Light.ttf', 'Helvetica')
    # Klinic Slab Bold — project name heading
    _KlinicBold,   _HAS_KLINIC_BOLD   = _load_font('KlinicBold',  'KlinicSlabBold.ttf',   'Times-Bold')
    # Klinic Slab Book — project description body
    _KlinicBook,   _HAS_KLINIC_BOOK   = _load_font('KlinicBook',   'KlinicSlabBook.ttf',    'Times-Roman')
    # Klinic Slab Medium — bio text
    _KlinicMedium, _HAS_KLINIC_MEDIUM = _load_font('KlinicMedium', 'KlinicSlabMedium.ttf',  'Times-Roman')
    # Gotham Medium — sidebar body copy
    _GothamMedium, _HAS_GOTHAM_MEDIUM = _load_font('GothamMedium', 'GothamHTF-Medium.ttf',  'Helvetica')

    # Liberation Serif still used for bio text
    try:
        pdfmetrics.registerFont(TTFont('SerifReg',  str(_FONT_DIR / 'LiberationSerif-Regular.ttf')))
        pdfmetrics.registerFont(TTFont('SerifBold', str(_FONT_DIR / 'LiberationSerif-Bold.ttf')))
        _HAS_SERIF = True
    except Exception:
        _HAS_SERIF = False

    # ── Fetch data ──────────────────────────────────────────────────────────────
    data        = request.get_json(force=True) or {}
    project_ids = []
    for x in data.get('project_ids', []):
        try:   project_ids.append(int(x))
        except (ValueError, TypeError): pass

    with get_db() as db:
        emp = db.execute(f'SELECT * FROM employees WHERE id = {P}', (employee_id,)).fetchone()
        if not emp:
            return jsonify(error='Employee not found'), 404
        employee = row_to_dict(emp)

        projects = []
        if project_ids:
            ph = ','.join([P] * len(project_ids))
            rows = db.execute(
                f'SELECT id, name, location, description, resume_description FROM projects '
                f'WHERE id IN ({ph}) ORDER BY name',
                project_ids,
            ).fetchall()
            projects = [row_to_dict(r) for r in rows]

    # ── Dimensions (pixel-perfect from measured template) ──────────────────────
    W, H      = letter       # 612 × 792 pts
    LT_MARG   = 36           # left margin
    BOT_MARG  = 28           # bottom margin (no footer)
    RT_COL_X  = 407.5        # right column x (from pdfplumber measurement)
    RT_PAD    = 14           # inner padding for right column content
    RT_X      = RT_COL_X + RT_PAD
    RT_W      = W - RT_X - RT_PAD
    LT_W      = RT_COL_X - LT_MARG - 12   # left content width (≈ 359 pt)
    PHOTO_H   = 307.0        # photo area height from top of page
    PHOTO_BOT = H - PHOTO_H  # RL y at photo area bottom = 485 pt

    # ── Colour palette ──────────────────────────────────────────────────────────
    GOLD     = HexColor('#b6872d')   # first name, section labels
    MAROON   = HexColor('#8f1838')   # title, sidebar panel, logo
    GRAY_BG  = HexColor('#E8E9EB')   # photo area background
    DARK     = HexColor('#1A1A1A')   # last name, body text
    MED_GRAY = HexColor('#888888')   # footer, rules

    # ── Logo PNG path ────────────────────────────────────────────────────────────
    LOGO_PATH = str(APP_DIR / 'static' / 'fb-logo-mark.png')

    # ── Name parts (first name bold gold; rest of name regular dark) ────────────
    full_name  = employee.get('name', '')
    _parts     = full_name.split(None, 1)
    first_name = (_parts[0] if _parts else '').upper()
    last_name  = (_parts[1]  if len(_parts) > 1 else '').upper()
    title_txt  = (employee.get('title') or '').upper()

    # ── Headshot path ────────────────────────────────────────────────────────────
    headshot_path = None
    if employee.get('headshot_filename'):
        _hp = HEADSHOT_DIR / employee['headshot_filename']
        if _hp.exists():
            headshot_path = str(_hp)

    # ── Canvas helpers ──────────────────────────────────────────────────────────
    def _spaced(cv, text, x, y, font, size, color, tracking=1.2):
        """Draw text with extra letter-spacing; returns x after last char."""
        cv.setFont(font, size)
        cv.setFillColor(color)
        cx = x
        for ch in str(text):
            cv.drawString(cx, y, ch)
            cx += cv.stringWidth(ch, font, size) + tracking
        return cx

    def _wrap(cv, text, x, y, max_w, font, size, color, leading=None):
        """Word-wrap `text` into `max_w` pts; returns y below last line."""
        if leading is None:
            leading = size * 1.3
        cv.setFont(font, size)
        cv.setFillColor(color)
        words = str(text or '').split()
        if not words:
            return y
        line = ''
        for word in words:
            test = (line + ' ' + word).strip()
            if cv.stringWidth(test, font, size) <= max_w:
                line = test
            else:
                if line:
                    cv.drawString(x, y, line)
                    y -= leading
                line = word
        if line:
            cv.drawString(x, y, line)
            y -= leading
        return y

    # ── Sidebar renderer (Education / Affiliations / References) ────────────────
    def _draw_sidebar(cv):
        y = PHOTO_BOT - 20   # start 20pt below the photo/maroon separator

        def _section(label, draw_body_fn):
            nonlocal y
            # Sidebar title — Gotham Black, gold, fill-only
            cx = RT_X
            for ch in label:
                t = cv.beginText(cx, y)
                t.setFont(_GothamBlack, 9)
                t.setFillColor(GOLD)
                t.setTextRenderMode(0)
                t.textLine(ch)
                cv.drawText(t)
                cx += cv.stringWidth(ch, _GothamBlack, 9) + 1.2
            cv._code.append('0 Tr')
            y -= 7
            cv.setStrokeColor(RL_WHITE)
            cv.setLineWidth(0.5)
            cv.line(RT_X, y, W - RT_PAD, y)
            y -= 12
            draw_body_fn()
            y -= 12   # gap after section

        # Education
        edu = [l.strip() for l in (employee.get('education') or '').splitlines()
               if l.strip()]
        if edu:
            def _edu():
                nonlocal y
                for ln in edu:
                    y = _wrap(cv, ln, RT_X, y, RT_W,
                              _GothamMedium, 8.5, RL_WHITE, leading=11)
            _section('EDUCATION', _edu)

        # Affiliations
        aff = [l.strip() for l in (employee.get('affiliations') or '').splitlines()
               if l.strip()]
        if aff:
            def _aff():
                nonlocal y
                for ln in aff:
                    y = _wrap(cv, ln, RT_X, y, RT_W,
                              _GothamMedium, 8.5, RL_WHITE, leading=11)
            _section('AFFILIATIONS', _aff)

        # References — parse blank-line-separated blocks; first line = name (bold)
        ref_raw = (employee.get('ref_info') or '').strip()
        ref_blocks = []
        if ref_raw:
            for blk in re.split(r'\n\s*\n', ref_raw):
                lines = [l.strip() for l in blk.splitlines() if l.strip()]
                if lines:
                    ref_blocks.append(lines)

        if ref_blocks:
            def _refs():
                nonlocal y
                for i, ref_lines in enumerate(ref_blocks):
                    if i:
                        y -= 6   # extra gap between references
                    # First line = person name
                    cv.setFont(_GothamMedium, 8.5)
                    cv.setFillColor(RL_WHITE)
                    cv.drawString(RT_X, y, ref_lines[0])
                    y -= 11
                    for detail in ref_lines[1:]:
                        y = _wrap(cv, detail, RT_X, y, RT_W,
                                  _GothamMedium, 8.5, RL_WHITE, leading=11)
            _section('REFERENCES', _refs)

    # ── Page drawing callback ───────────────────────────────────────────────────
    def draw_page(cv, doc_obj):
        cv.saveState()
        pg = doc_obj.page

        if pg == 1:
            # ── Right column: gray photo background ──────────────────────────
            cv.setFillColor(GRAY_BG)
            cv.rect(RT_COL_X, PHOTO_BOT, W - RT_COL_X, PHOTO_H, fill=1, stroke=0)

            # Headshot — cover-fill: scale to fill the full rectangle, clip overflow
            if headshot_path:
                try:
                    from reportlab.lib.utils import ImageReader as _IR
                    _ir   = _IR(headshot_path)
                    _iw, _ih = _ir.getSize()          # native pixel dimensions
                    _box_w = W - RT_COL_X             # 204.5 pt
                    _box_h = PHOTO_H                  # 307 pt
                    # Scale so image covers the box in both dimensions (like CSS cover)
                    _scale = max(_box_w / _iw, _box_h / _ih)
                    _dw    = _iw * _scale              # drawn width  (≥ box_w)
                    _dh    = _ih * _scale              # drawn height (≥ box_h)
                    # Centre horizontally, anchor to top vertically
                    _dx    = RT_COL_X + (_box_w - _dw) / 2
                    _dy    = PHOTO_BOT                 # top-align (face stays visible)
                    cv.saveState()
                    clip = cv.beginPath()
                    clip.rect(RT_COL_X, PHOTO_BOT, _box_w, _box_h)
                    cv.clipPath(clip, fill=0, stroke=0)
                    cv.drawImage(headshot_path, _dx, _dy,
                                 width=_dw, height=_dh,
                                 mask='auto')
                    cv.restoreState()
                except Exception:
                    pass   # keep gray background on any error

            # ── Right column: maroon sidebar panel ───────────────────────────
            cv.setFillColor(MAROON)
            cv.rect(RT_COL_X, 0, W - RT_COL_X, PHOTO_BOT, fill=1, stroke=0)

            # Thin white separator between photo and maroon panel
            cv.setFillColor(RL_WHITE)
            cv.rect(RT_COL_X, PHOTO_BOT - 1.5, W - RT_COL_X, 3, fill=1, stroke=0)

            # ── Fontaine logo (actual brand mark PNG) ────────────────────────
            import os as _os
            if _os.path.exists(LOGO_PATH):
                # logo viewBox is 124×95 — display at ~52pt wide in upper-right
                _logo_w = 52
                _logo_h = _logo_w * (95 / 124)   # ≈ 39.8 pt
                cv.drawImage(LOGO_PATH,
                             W - RT_PAD - _logo_w,
                             H - _logo_h - 12,
                             width=_logo_w, height=_logo_h,
                             mask='auto', preserveAspectRatio=True)

            # ── Left column: name block ───────────────────────────────────────
            # First name — Gotham Black, gold, fill-only
            t = cv.beginText(LT_MARG, H - 72)
            t.setFont(_GothamBlack, 30)
            t.setFillColor(GOLD)
            t.setTextRenderMode(0)
            t.textLine(first_name)
            cv.drawText(t)
            cv._code.append('0 Tr')

            # Last name — Gotham Light, dark, fill-only
            t_last = cv.beginText(LT_MARG, H - 98)
            t_last.setFont(_GothamLight, 25)
            t_last.setFillColor(DARK)
            t_last.setTextRenderMode(0)
            t_last.textLine(last_name)
            cv.drawText(t_last)
            cv._code.append('0 Tr')

            # Title — Gotham Black, spaced caps, black, fill-only
            if title_txt:
                TITLE_BLACK = HexColor('#000000')
                x_t = LT_MARG + 2
                y_t = H - 114
                for ch in title_txt:
                    t3 = cv.beginText(x_t, y_t)
                    t3.setFont(_GothamBlack, 10)
                    t3.setFillColor(TITLE_BLACK)
                    t3.setTextRenderMode(0)
                    t3.textLine(ch)
                    cv.drawText(t3)
                    x_t += cv.stringWidth(ch, _GothamBlack, 10) + 1.5
                cv._code.append('0 Tr')

            # ── Sidebar content ───────────────────────────────────────────────
            _draw_sidebar(cv)

        else:
            # ── Page 2 + : slim maroon header band ───────────────────────────
            cv.setFillColor(MAROON)
            cv.rect(0, H - 28, W, 28, fill=1, stroke=0)
            cv.setFont(_GothamBlack, 10)
            cv.setFillColor(RL_WHITE)
            cv.drawString(LT_MARG, H - 18, full_name.upper())
            if title_txt:
                cv.setFont(_GothamLight, 9)
                cv.drawRightString(W - LT_MARG, H - 18, title_txt)

        cv.restoreState()

    # ── Custom flowable: letter-spaced section heading + rule ───────────────────
    class SpacedHeading(Flowable):
        """Draws a letter-spaced gold label (extra-heavy) with a gray rule below."""
        def __init__(self, text, avail_width):
            Flowable.__init__(self)
            self.text       = text
            self.width      = avail_width
            self.height     = 9 + 5 + 6   # label + gap + rule + bottom pad

        def draw(self):
            c = self.canv
            c.saveState()
            x = 0
            for ch in self.text:
                t = c.beginText(x, 6)
                t.setFont(_GothamBlack, 9)   # Gotham Black — fill-only, weight carries itself
                t.setFillColor(GOLD)
                t.setTextRenderMode(0)
                t.textLine(ch)
                c.drawText(t)
                x += c.stringWidth(ch, _GothamBlack, 9) + 1.2
            c._code.append('0 Tr')
            c.setStrokeColor(MED_GRAY)
            c.setLineWidth(0.75)
            c.line(0, 3, self.width, 3)
            c.restoreState()

    # ── Paragraph styles ────────────────────────────────────────────────────────
    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    _bio_font = _KlinicMedium   # Klinic Slab Medium for bio
    bio_st  = ps('Bio',  fontSize=9.5, fontName=_bio_font,
                 textColor=DARK,  leading=11, spaceAfter=2)
    pnm_st  = ps('PNm',  fontSize=9,   fontName=_KlinicBold,  # Klinic Slab Bold — project title
                 textColor=DARK,  spaceBefore=8, spaceAfter=2)
    pds_st  = ps('PDs',  fontSize=8.5, fontName=_KlinicBook,  # Klinic Slab Book — project description
                 textColor=DARK,  leading=10,  spaceAfter=2)

    # ── Story (flowing left-column content) ─────────────────────────────────────
    story = [NextPageTemplate('p2')]

    # Bio (bold body paragraph)
    if employee.get('bio'):
        for ln in employee['bio'].splitlines():
            if ln.strip():
                story.append(Paragraph(ln.strip(), bio_st))
        story.append(Spacer(1, 10))

    # "RELEVANT EXPERIENCE" spaced heading + rule
    story.append(SpacedHeading('RELEVANT  EXPERIENCE', LT_W))
    story.append(Spacer(1, 8))

    # Project entries
    if projects:
        for proj in projects:
            block = []
            hdr = proj.get('name', '')
            if proj.get('location'):
                hdr += f' | {proj["location"]}'
            block.append(Paragraph(hdr, pnm_st))
            _proj_desc = (proj.get('resume_description') or proj.get('description') or '').strip()
            if _proj_desc:
                for ln in _proj_desc.splitlines():
                    if ln.strip():
                        block.append(Paragraph(ln.strip(), pds_st))
            block.append(Spacer(1, 3))
            story.append(KeepTogether(block))
    else:
        story.append(Paragraph('No projects selected.', pds_st))

    # ── Build PDF ───────────────────────────────────────────────────────────────
    buf = io.BytesIO()

    # Page 1 frame: left column only, starts below name/title block (y ≈ 666)
    p1_frame = Frame(
        LT_MARG, BOT_MARG,
        LT_W,    H - 126 - BOT_MARG,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    # Page 2+ frame: full width, below slim header band (28 pt + 10 pt gap)
    p2_frame = Frame(
        LT_MARG, BOT_MARG,
        W - 2 * LT_MARG, H - 38 - BOT_MARG,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    tmpl1   = PageTemplate(id='p1', frames=[p1_frame], onPage=draw_page)
    tmpl2   = PageTemplate(id='p2', frames=[p2_frame], onPage=draw_page)
    doc_obj = BaseDocTemplate(
        buf, pagesize=letter, pageTemplates=[tmpl1, tmpl2],
        title=f'{full_name} – Resume',
        author='Fontaine Bros. Construction Management',
    )
    doc_obj.build(story)
    buf.seek(0)

    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', full_name or 'Employee')
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True,
                     download_name=f'{safe_name}_Resume.pdf')


# ── Batch import ───────────────────────────────────────────────────────────────
@app.route('/api/batch/import_projects', methods=['POST'])
@login_required
def batch_import_projects():
    """
    Accept a ZIP file.  Every top-level subfolder becomes a project; every
    image file inside it is uploaded as a project photo.

    Expected ZIP layout:
        Holy Cross Figge Hall/
            photo1.jpg
            photo2.jpg
        Northbridge Elementary/
            img001.jpg
    """
    zf = request.files.get('zipfile')
    if not zf or not zf.filename.lower().endswith('.zip'):
        return jsonify(error='Please upload a .zip file'), 400

    results   = []   # [{name, created, photos, skipped, error}]
    zip_bytes = zf.read()

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            # ── First pass: collect all valid entries with their path parts ──
            all_entries = []
            for entry in z.infolist():
                if entry.is_dir():
                    continue
                parts = Path(entry.filename).parts
                # skip macOS __MACOSX junk and hidden files/folders
                if any(p.startswith('__') or p.startswith('.') for p in parts):
                    continue
                if len(parts) < 2:
                    continue  # bare file at ZIP root, not in any folder
                all_entries.append((parts, entry))

            # ── Detect "wrapper folder" pattern ──────────────────────────────
            # When a folder is zipped on Mac/Windows, it adds a top-level
            # wrapper: myprojects/ProjectA/photo.jpg instead of ProjectA/photo.jpg
            # Detect this by: only ONE unique top-level dir AND some entries
            # are at depth 3+ (meaning there are subfolders inside the wrapper).
            top_level_dirs = {parts[0] for parts, _ in all_entries}
            has_subfolders = any(len(parts) >= 3 for parts, _ in all_entries)

            if len(top_level_dirs) == 1 and has_subfolders:
                # Wrapper folder present — project names are at depth index 1
                project_key_idx = 1
                min_depth       = 3
            else:
                # Normal layout — project names are at depth index 0
                project_key_idx = 0
                min_depth       = 2

            # ── Group entries by project folder ──────────────────────────────
            folders = {}
            for parts, entry in all_entries:
                if len(parts) < min_depth:
                    continue  # loose file in wrapper folder, skip
                proj_name = parts[project_key_idx]
                folders.setdefault(proj_name, []).append(entry)

            for folder_name, entries in sorted(folders.items()):
                project_name = folder_name.strip()
                result = {'name': project_name, 'photos': 0,
                          'skipped': 0, 'created': False, 'error': None}

                # Create project if it doesn't exist
                with get_db() as db:
                    existing = db.execute(
                        f'SELECT id FROM projects WHERE name = {P}', (project_name,)
                    ).fetchone()
                    if existing:
                        project_id = existing['id']
                    else:
                        if USE_POSTGRES:
                            cur = db.execute(
                                f'INSERT INTO projects (name) VALUES ({P}) RETURNING id',
                                (project_name,)
                            )
                            project_id = cur.fetchone()['id']
                        else:
                            cur = db.execute(
                                f'INSERT INTO projects (name) VALUES ({P})', (project_name,)
                            )
                            project_id = cur.lastrowid
                        db.commit()
                        result['created'] = True

                # Upload images
                dest_dir = upload_dir_for(project_id)
                for entry in entries:
                    orig_name = Path(entry.filename).name
                    if not allowed_file(orig_name):
                        result['skipped'] += 1
                        continue
                    ext      = orig_name.rsplit('.', 1)[1].lower()
                    filename = f'{uuid.uuid4().hex}.{ext}'
                    img_data = z.read(entry.filename)
                    (dest_dir / filename).write_bytes(img_data)

                    with get_db() as db:
                        if USE_POSTGRES:
                            cur = db.execute(
                                f'INSERT INTO images (project_id, filename, original_name) VALUES ({P},{P},{P}) RETURNING id',
                                (project_id, filename, secure_filename(orig_name))
                            )
                            img_id = cur.fetchone()['id']
                            proj   = db.execute(
                                f'SELECT hero_image_id FROM projects WHERE id = {P}', (project_id,)
                            ).fetchone()
                            if not proj['hero_image_id']:
                                db.execute(
                                    f'UPDATE projects SET hero_image_id = {P} WHERE id = {P}',
                                    (img_id, project_id)
                                )
                        else:
                            cur = db.execute(
                                f'INSERT INTO images (project_id, filename, original_name) VALUES ({P},{P},{P})',
                                (project_id, filename, secure_filename(orig_name))
                            )
                            img_id = cur.lastrowid
                            proj   = db.execute(
                                f'SELECT hero_image_id FROM projects WHERE id = {P}', (project_id,)
                            ).fetchone()
                            if not proj['hero_image_id']:
                                db.execute(
                                    f'UPDATE projects SET hero_image_id = {P} WHERE id = {P}',
                                    (img_id, project_id)
                                )
                        db.commit()

                    result['photos'] += 1

                results.append(result)

    except zipfile.BadZipFile:
        return jsonify(error='The uploaded file is not a valid ZIP'), 400
    except Exception as e:
        return jsonify(error=f'Import failed: {e}'), 500

    return jsonify(success=True, results=results)


def _parse_resume_text(text):
    """
    Best-effort heuristic extraction of name / title / bio from resume text.
    Returns a dict with keys: name, title, bio.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return {'name': '', 'title': '', 'bio': ''}

    # Name: first substantive line (skip lines that look like contact info)
    contact_re = re.compile(
        r'(\bphone\b|\bemail\b|\blinkedin\b|@|\d{3}[-.\s]\d{3}|\bwww\b|http)',
        re.I
    )
    name  = ''
    title = ''
    for i, line in enumerate(lines[:6]):
        if contact_re.search(line):
            continue
        if not name:
            name = line
        elif not title and len(line) < 80:
            title = line
            break

    # Bio: look for a summary / objective / profile section
    bio = ''
    summary_re = re.compile(
        r'^(summary|professional summary|profile|about|objective|overview)',
        re.I
    )
    for i, line in enumerate(lines):
        if summary_re.match(line):
            # Grab next 1-4 non-heading lines as bio
            bio_lines = []
            for following in lines[i+1:i+8]:
                # Stop if we hit another section header (all-caps or short)
                if following.isupper() or (len(following) < 40 and following.endswith(':')):
                    break
                bio_lines.append(following)
                if len(' '.join(bio_lines)) > 600:
                    break
            bio = ' '.join(bio_lines)
            break

    return {'name': name, 'title': title, 'bio': bio}


@app.route('/api/employees/import_resume', methods=['POST'])
@login_required
def import_resume():
    """
    Accept a PDF resume, extract text, return parsed fields for user review.
    Does NOT create the employee — the frontend shows a confirmation form first.
    """
    if not HAS_PYPDF:
        return jsonify(error='PDF parsing library not available on this server.'), 503

    pdf_file = request.files.get('resume')
    if not pdf_file or not pdf_file.filename.lower().endswith('.pdf'):
        return jsonify(error='Please upload a .pdf file'), 400

    try:
        reader = _PdfReader(io.BytesIO(pdf_file.read()))
        text   = '\n'.join(
            page.extract_text() or '' for page in reader.pages
        )
    except Exception as e:
        return jsonify(error=f'Could not read PDF: {e}'), 400

    parsed = _parse_resume_text(text)
    return jsonify(success=True, **parsed)


# ── InDesign Plugin API ─────────────────────────────────────────────────────────
# These routes are called by the Fontaine DAM InDesign panel.
# They use token-based auth (X-DAM-Token header = TEAM_PASSWORD) instead of
# session cookies so the panel doesn't need a browser login flow.

def _plugin_token_ok():
    """Return True if the request carries a valid plugin token."""
    token = (request.headers.get('X-DAM-Token')
             or request.args.get('token', ''))
    return token == TEAM_PASSWORD


def _plugin_cors(resp):
    """Attach CORS headers so the InDesign CEP panel can reach us."""
    resp.headers['Access-Control-Allow-Origin']  = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-DAM-Token'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return resp


@app.route('/api/plugin/auth', methods=['POST', 'OPTIONS'])
def plugin_auth():
    """Validate team password; return token for subsequent plugin requests."""
    if request.method == 'OPTIONS':
        return _plugin_cors(app.make_response(('', 204)))
    data = request.get_json(silent=True) or {}
    if data.get('password') == TEAM_PASSWORD:
        return _plugin_cors(jsonify(success=True, token=TEAM_PASSWORD))
    return _plugin_cors(jsonify(success=False, error='Invalid password')), 401


@app.route('/api/plugin/projects', methods=['GET', 'OPTIONS'])
def plugin_projects():
    """Return all projects as JSON for the InDesign panel."""
    if request.method == 'OPTIONS':
        return _plugin_cors(app.make_response(('', 204)))
    if not _plugin_token_ok():
        return _plugin_cors(jsonify(error='Unauthorized')), 401
    with get_db() as db:
        rows = db.execute(
            'SELECT id, name, location, sector, year_completed, hero_image_id '
            'FROM projects ORDER BY name'
        ).fetchall()
        projects = []
        for r in rows:
            p = dict(r)
            if p.get('hero_image_id'):
                img = db.execute(
                    f'SELECT filename FROM images WHERE id = {P}',
                    (p['hero_image_id'],)
                ).fetchone()
                if img:
                    p['hero_url'] = f'/uploads/{p["id"]}/{img["filename"]}'
                else:
                    p['hero_url'] = None
            else:
                p['hero_url'] = None
            projects.append(p)
    return _plugin_cors(jsonify(projects=projects))


@app.route('/api/plugin/project/<int:project_id>/photos', methods=['GET', 'OPTIONS'])
def plugin_project_photos(project_id):
    """Return all photos for a project as JSON for the InDesign panel."""
    if request.method == 'OPTIONS':
        return _plugin_cors(app.make_response(('', 204)))
    if not _plugin_token_ok():
        return _plugin_cors(jsonify(error='Unauthorized')), 401
    with get_db() as db:
        rows = db.execute(
            f'SELECT id, filename, original_name FROM images '
            f'WHERE project_id = {P} ORDER BY uploaded_at DESC',
            (project_id,)
        ).fetchall()
        photos = [
            {
                'id':            r['id'],
                'filename':      r['filename'],
                'original_name': r['original_name'],
                'url':           f'/uploads/{project_id}/{r["filename"]}',
            }
            for r in rows
        ]
    return _plugin_cors(jsonify(photos=photos))


@app.after_request
def cors_uploads_for_plugin(response):
    """Allow the InDesign panel to load images from /uploads/ directly."""
    if request.path.startswith('/uploads/'):
        response.headers['Access-Control-Allow-Origin'] = '*'
    return response


# ── Init DB on every startup (works for both `python app.py` and gunicorn) ────
try:
    init_db()
    migrate_db()
except Exception as _init_err:
    print(f'WARNING: DB initialisation error: {_init_err}')


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    print('\n' + '=' * 54)
    print('   Fontaine Bros. Digital Asset Manager')
    print('=' * 54)
    print(f'   DB      : {"PostgreSQL" if USE_POSTGRES else f"SQLite ({DB_PATH})"}')
    print(f'   Uploads : {UPLOAD_DIR}')
    print(f'\n   ✅  http://localhost:{port}')
    print('   Press Ctrl+C to stop.\n')

    if not USE_POSTGRES:
        threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()

    app.run(debug=False, port=port, host='0.0.0.0')
