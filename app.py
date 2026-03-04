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
import uuid
import sqlite3
import threading
import webbrowser
from pathlib import Path
from datetime import datetime
from functools import wraps
from contextlib import contextmanager

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


def init_db():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        if USE_POSTGRES:
            db.execute('''
                CREATE TABLE IF NOT EXISTS projects (
                    id              SERIAL      PRIMARY KEY,
                    name            TEXT        UNIQUE NOT NULL,
                    description     TEXT        DEFAULT '',
                    square_footage  TEXT        DEFAULT '',
                    location        TEXT        DEFAULT '',
                    year_completed  TEXT        DEFAULT '',
                    project_type    TEXT        DEFAULT '',
                    contract_value  TEXT        DEFAULT '',
                    architect       TEXT        DEFAULT '',
                    opm             TEXT        DEFAULT '',
                    status          TEXT        DEFAULT '',
                    client          TEXT        DEFAULT '',
                    hero_image_id   INTEGER,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS people (
                    id          SERIAL  PRIMARY KEY,
                    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    category    TEXT    NOT NULL,
                    name        TEXT    NOT NULL,
                    role        TEXT    DEFAULT ''
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
                    square_footage  TEXT    DEFAULT '',
                    location        TEXT    DEFAULT '',
                    year_completed  TEXT    DEFAULT '',
                    project_type    TEXT    DEFAULT '',
                    contract_value  TEXT    DEFAULT '',
                    architect       TEXT    DEFAULT '',
                    opm             TEXT    DEFAULT '',
                    status          TEXT    DEFAULT '',
                    client          TEXT    DEFAULT '',
                    hero_image_id   INTEGER,
                    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS people (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id  INTEGER NOT NULL,
                    category    TEXT    NOT NULL,
                    name        TEXT    NOT NULL,
                    role        TEXT    DEFAULT '',
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
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
    if USE_POSTGRES:
        return dict(row)
    return dict(row)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTS


def upload_dir_for(project_id):
    d = UPLOAD_DIR / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Pages ──────────────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def index():
    q = request.args.get('q', '').strip()
    with get_db() as db:
        if q:
            like = f'%{q}%'
            rows = db.execute(f'''
                SELECT DISTINCT p.*, i.filename AS hero_file,
                       (SELECT COUNT(*) FROM images WHERE project_id = p.id) AS image_count
                FROM projects p
                LEFT JOIN people pe ON p.id = pe.project_id
                LEFT JOIN images  i  ON i.id  = p.hero_image_id
                WHERE  p.name           LIKE {P}
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
                   OR  pe.role          LIKE {P}
                ORDER BY p.name
            ''', (like,) * 13).fetchall()
        else:
            rows = db.execute(f'''
                SELECT p.*, i.filename AS hero_file,
                       (SELECT COUNT(*) FROM images WHERE project_id = p.id) AS image_count
                FROM projects p
                LEFT JOIN images i ON i.id = p.hero_image_id
                ORDER BY p.name
            ''').fetchall()

    # If no hero set, pick the first uploaded image
    projects = []
    for r in rows:
        d = row_to_dict(r)
        if not d.get('hero_file'):
            # fetch first image for this project
            with get_db() as db2:
                first = db2.execute(
                    f'SELECT filename FROM images WHERE project_id = {P} ORDER BY id LIMIT 1',
                    (d['id'],)
                ).fetchone()
            d['hero_file'] = first['filename'] if first else None
        projects.append(d)

    return render_template('index.html', projects=projects, q=q)


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
            f"SELECT * FROM people WHERE project_id = {P} AND category = 'engineer' ORDER BY name",
            (project_id,)
        ).fetchall()

        team = db.execute(
            f"SELECT * FROM people WHERE project_id = {P} AND category = 'team_member' ORDER BY name",
            (project_id,)
        ).fetchall()

        images = db.execute(
            f'SELECT * FROM images WHERE project_id = {P} ORDER BY id',
            (project_id,)
        ).fetchall()

    project = row_to_dict(p)

    # Find hero image
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
    with get_db() as db:
        db.execute(f'''
            UPDATE projects SET
                description    = {P},
                square_footage = {P},
                location       = {P},
                year_completed = {P},
                project_type   = {P},
                contract_value = {P},
                architect      = {P},
                opm            = {P},
                status         = {P},
                client         = {P}
            WHERE id = {P}
        ''', (
            data.get('description',    ''),
            data.get('square_footage', ''),
            data.get('location',       ''),
            data.get('year_completed', ''),
            data.get('project_type',   ''),
            data.get('contract_value', ''),
            data.get('architect',      ''),
            data.get('opm',            ''),
            data.get('status',         ''),
            data.get('client',         ''),
            project_id,
        ))
        db.commit()
    return jsonify(success=True)


@app.route('/api/project/<int:project_id>/delete', methods=['DELETE'])
@login_required
def delete_project(project_id):
    with get_db() as db:
        # Remove uploaded files
        imgs = db.execute(
            f'SELECT filename FROM images WHERE project_id = {P}', (project_id,)
        ).fetchall()
        for img in imgs:
            path = UPLOAD_DIR / str(project_id) / img['filename']
            if path.exists():
                path.unlink()

        db.execute(f'DELETE FROM projects WHERE id = {P}', (project_id,))
        db.commit()

    # Try to remove the project upload dir
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
    name = data.get('name', '').strip()
    if not name:
        return jsonify(error='Name is required'), 400

    with get_db() as db:
        p = db.execute(f'SELECT id FROM projects WHERE id = {P}', (project_id,)).fetchone()
        if not p:
            return jsonify(error='Project not found'), 404

        if USE_POSTGRES:
            cur = db.execute(
                f'INSERT INTO people (project_id, category, name, role) VALUES ({P},{P},{P},{P}) RETURNING id',
                (project_id, data.get('category', 'team_member'), name, data.get('role', '').strip())
            )
            person_id = cur.fetchone()['id']
        else:
            cur = db.execute(
                f'INSERT INTO people (project_id, category, name, role) VALUES ({P},{P},{P},{P})',
                (project_id, data.get('category', 'team_member'), name, data.get('role', '').strip())
            )
            person_id = cur.lastrowid
        db.commit()

    return jsonify(success=True, id=person_id)


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
                # Auto-set hero if none set yet
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

        # Unset hero if this was it
        db.execute(
            f'UPDATE projects SET hero_image_id = NULL WHERE id = {P} AND hero_image_id = {P}',
            (img['project_id'], image_id)
        )
        db.execute(f'DELETE FROM images WHERE id = {P}', (image_id,))
        db.commit()

    # Delete file
    path = UPLOAD_DIR / str(img['project_id']) / img['filename']
    if path.exists():
        path.unlink()

    return jsonify(success=True)


# ── Serve uploaded images ──────────────────────────────────────────────────────
@app.route('/uploads/<int:project_id>/<filename>')
@login_required
def serve_image(project_id, filename):
    path = (UPLOAD_DIR / str(project_id) / filename).resolve()
    # Security check
    try:
        path.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        abort(403)
    if not path.is_file():
        abort(404)
    return send_file(str(path))


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
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
