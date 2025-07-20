import os
import sqlite3
import hashlib
import re
from flask import Flask, request, abort, jsonify, redirect, session, render_template_string, send_from_directory
from flask_cors import CORS
from urllib.parse import unquote
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from waitress import serve

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))

ADMIN_KEY = os.getenv("ADMIN_KEY", "super-secret-admin-key")
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB upload limit

# ----------------- Folder Setup -----------------
CATEGORY_FOLDERS = {
    "Movies": "static/media/movies",
    "Series": "static/media/series",
    "Asian": "static/media/asian",  # K-Drama / Asian Series
    "Animation": "static/media/animation",
    "Documentaries": "static/media/documentaries",
    "Wrestling": "static/media/wrestling"
}

for folder in CATEGORY_FOLDERS.values():
    os.makedirs(folder, exist_ok=True)

UPLOAD_FOLDER = "static/media/uploads"
THUMB_FOLDER = "static/media/thumbs"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

# ✅ Ensure default thumbnail exists
DEFAULT_THUMB_PATH = os.path.join(THUMB_FOLDER, "default_thumb.jpg")
if not os.path.exists(DEFAULT_THUMB_PATH):
    try:
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (300, 450), color=(100, 100, 100))  # Gray background
        draw = ImageDraw.Draw(img)
        draw.text((75, 200), "No Image", fill=(255, 255, 255))
        img.save(DEFAULT_THUMB_PATH)
    except ImportError:
        # If Pillow is not installed, create an empty file to prevent 404
        with open(DEFAULT_THUMB_PATH, 'wb') as f:
            pass


# ----------------- Database Initialization -----------------
DB = 'db.sqlite3'

def init_db():
    with sqlite3.connect(DB) as conn:
        # Users Table
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            payments_enabled INTEGER DEFAULT 1)''')

        # Movies Table
        conn.execute('''CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            genre TEXT,
            description TEXT,
            video_path TEXT,
            thumbnail TEXT,
            category TEXT,
            views INTEGER DEFAULT 0,
            search_count INTEGER DEFAULT 0,
            folder TEXT,
            season TEXT,
            episode TEXT)''')

        # Requests Table
        conn.execute('''CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL)''')

        # Complaints Table
        conn.execute('''CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            message TEXT NOT NULL)''')

        # Payments Table
        conn.execute('''CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'success',
            FOREIGN KEY (user_id) REFERENCES users(id))''')

# Initialize the database only if it doesn't exist
if not os.path.exists(DB):
    init_db()

# ----------------- Utilities -----------------
def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def is_valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

def serve_html(name):
    """Serve raw HTML files instead of Jinja templates"""
    try:
        with open(f"{name}.html", encoding="utf-8") as file:
            return render_template_string(file.read())
    except FileNotFoundError:
        return f"{name}.html not found", 404

def is_global_payment_enabled():
    """Check global payment mode (enabled/disabled)"""
    if not os.path.exists("global_payment.txt"):
        return True
    with open("global_payment.txt", "r") as f:
        return f.read().strip() == "enabled"

def has_payment(username):
    """Check if user has payments enabled"""
    if not is_global_payment_enabled():
        return False
    with sqlite3.connect(DB) as conn:
        user = conn.execute("SELECT payments_enabled FROM users WHERE username=?", (username,)).fetchone()
        return user and user[0] == 1

# Allowed file types for upload
ALLOWED_EXTENSIONS = {'mp4', 'mkv', 'avi', 'mov', 'jpg', 'jpeg', 'png'}
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_movie_with_default_thumbnail(movie_data, file=None, thumbnail=None):
    # Check if the thumbnail is missing, and use default if so
    if not thumbnail:
        thumbnail = "static/media/thumbs/default_thumb.jpg"

    # Save movie file to appropriate folder
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(file_path)
    else:
        return "Invalid file type or no file uploaded"

    # Insert movie details into the database
    with sqlite3.connect(DB) as conn:
        conn.execute('''INSERT INTO movies (title, genre, description, video_path, thumbnail, category)
                        VALUES (?, ?, ?, ?, ?, ?)''', 
                        (movie_data['title'], movie_data['genre'], movie_data['description'], 
                         file_path, thumbnail, movie_data['category']))

    return "Movie uploaded successfully!"

# ----------------- Public Routes -----------------
@app.route("/")
def index(): return serve_html("index")

@app.route("/home")
def home():
    if "user" not in session: return redirect("/")
    return serve_html("home")

@app.before_request
def restrict_admin_routes():
    if request.path.startswith("/admin"):
        token = request.headers.get("X-Access-Key")
        if token != ADMIN_KEY:
            abort(403)

@app.route("/movies")
def movies():
    if "user" not in session: return redirect("/")
    return serve_html("movies")

@app.route("/games")
def games():
    if "user" not in session: return redirect("/")
    if not has_payment(session["user"]): return "Payment required", 403
    return serve_html("games")

@app.route("/animation")
def animation():
    if "user" not in session: return redirect("/")
    return serve_html("animation")

@app.route("/documentaries")
def documentaries():
    if "user" not in session: return redirect("/")
    return serve_html("documentaries")

@app.route("/wrestling")
def wrestling():
    if "user" not in session: return redirect("/")
    return serve_html("wrestling")

@app.route("/api/asian")
def api_asian():
    return jsonify(fetch_category("Asian"))

@app.route("/api/wrestling")
def api_wrestling_videos():
    return jsonify(fetch_category("Wrestling"))

@app.route("/admin")
def admin():
    if "user" not in session or not session.get("admin"): return redirect("/")
    return serve_html("admin")

# ----------------- Authentication -----------------
@app.route("/auth", methods=["POST"])
def auth():
    action = request.form.get("action")
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    new_password = request.form.get("new_password", "").strip()

    with sqlite3.connect(DB) as conn:
        cur = conn.cursor()
        if action == "register":
            if not is_valid_email(email): return jsonify({"status": "error", "message": "Invalid email."})
            if len(password) < 6: return jsonify({"status": "error", "message": "Password too short."})
            if cur.execute("SELECT 1 FROM users WHERE username=? OR email=?", (username, email)).fetchone():
                return jsonify({"status": "error", "message": "Username/email exists."})
            is_admin = 1 if cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 else 0
            cur.execute("INSERT INTO users (username, email, password, is_admin) VALUES (?, ?, ?, ?)",
                        (username, email, hash_password(password), is_admin))
            conn.commit()
            session.update({"user": username, "email": email, "admin": is_admin})
            return jsonify({"status": "ok", "redirect": "/admin" if is_admin else "/home"})

        elif action == "login":
            user = cur.execute("SELECT * FROM users WHERE (username=? OR email=?) AND password=?",
                               (username, username, hash_password(password))).fetchone()
            if user:
                session.update({"user": user[1], "email": user[2], "admin": user[4]})
                return jsonify({"status": "ok", "redirect": "/admin" if user[4] else "/home"})
            return jsonify({"status": "error", "message": "Invalid credentials."})

        elif action == "reset":
            if len(new_password) < 6: return jsonify({"status": "error", "message": "Password too short."})
            if cur.execute("UPDATE users SET password=? WHERE (username=? OR email=?)",
                           (hash_password(new_password), username, email)).rowcount:
                conn.commit(); return jsonify({"status": "ok"})
            return jsonify({"status": "error", "message": "User not found."})
    return jsonify({"status": "error", "message": "Unknown action."})

# ----------------- Watch & Download -----------------
@app.route("/watch/<int:movie_id>")
def watch(movie_id):
    if "user" not in session:
        return redirect("/")
    if not has_payment(session["user"]):
        return "Payment required", 403
    with sqlite3.connect(DB) as conn:
        conn.execute("UPDATE movies SET views = views + 1 WHERE id=?", (movie_id,))
        movie = conn.execute("SELECT title, video_path FROM movies WHERE id=?", (movie_id,)).fetchone()
    if not movie:
        return "Not found", 404

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>{movie[0]}</title>
      <style>
        body {{ background:#000; margin:0; display:flex; justify-content:center; align-items:center; height:100vh; }}
        video {{ width:90%; max-height:90vh; border-radius:10px; }}
        a {{ position:fixed; top:10px; left:10px; color:#fff; text-decoration:none; background:#e50914; padding:10px; border-radius:5px; }}
      </style>
    </head>
    <body>
      <a href='/home'>Back</a>
      <video controls autoplay>
        <source src='/{movie[1]}' type='video/mp4'>
        Your browser does not support HTML5 video.
      </video>
    </body>
    </html>
    """
@app.route("/download/<int:movie_id>")
def download(movie_id):
    if "user" not in session: return redirect("/")
    with sqlite3.connect(DB) as conn:
        movie = conn.execute("SELECT video_path FROM movies WHERE id=?", (movie_id,)).fetchone()
    if not movie: return "Not found", 404
    filepath = movie[0]
    return send_from_directory(os.path.dirname(filepath), os.path.basename(filepath), as_attachment=True)

# ----------------- API Endpoints -----------------
@app.route("/api/movies")
def api_movies():
    return jsonify(fetch_category("Movies"))

@app.route("/api/animation")
def api_animation():
    return jsonify(fetch_category("Animation"))

@app.route("/api/documentaries")
def api_documentaries():
    return jsonify(fetch_category("Documentaries"))

@app.route("/api/wrestling")
def api_wrestling():
    return jsonify(fetch_category("Wrestling"))

@app.route("/api/series")
def api_series():
    base_dir = os.path.join("static", "media", "series")
    series_data = []
    if os.path.exists(base_dir):
        for show in os.listdir(base_dir):
            show_path = os.path.join(base_dir, show)
            if os.path.isdir(show_path):
                seasons = []
                for season in sorted(os.listdir(show_path)):
                    season_path = os.path.join(show_path, season)
                    if os.path.isdir(season_path):
                        episodes = []
                        for ep in sorted(os.listdir(season_path)):
                            if ep.lower().endswith(('.mp4', '.mkv')):
                                episodes.append({
                                    "title": os.path.splitext(ep)[0],
                                    "path": f"media/series/{show}/{season}/{ep}".replace("\\", "/")
                                })
                        if episodes:
                            seasons.append({"name": season, "episodes": episodes})
                if seasons:
                    series_data.append({"title": show, "seasons": seasons})
    return jsonify(series=series_data)

def fetch_category(category):
    with sqlite3.connect(DB) as conn:
        rows = conn.execute("SELECT id, title, video_path, thumbnail FROM movies WHERE category=?", (category,)).fetchall()
    return {"items": [{"id": r[0], "title": r[1], "path": r[2], "thumbnail": r[3]} for r in rows]}

# ----------------- Serve Media -----------------
@app.route('/media/<path:filename>')
def serve_media(filename):
    return send_from_directory('static/media', filename)

# ----------------- Admin Upload -----------------
@app.route('/admin/upload', methods=['POST'])
def upload_file():
    # Check if the user is logged in and is an admin
    if "user" not in session or not session.get("admin"):
        return "Forbidden", 403

    title = request.form.get('title')
    category = request.form.get('category')
    folder = request.form.get('folder') or ''
    season = request.form.get('season') or 'Season1'
    episode = request.form.get('episode') or ''

    # Handle multiple video files upload
    videos = request.files.getlist('video')
    thumb = request.files.get('thumb')  # Single thumbnail for all videos

    if not videos or category not in CATEGORY_FOLDERS:
        return "Missing or invalid data", 400

    # Validate thumbnail, use default if not provided
    if thumb and allowed_file(thumb.filename):
        thumb_filename = secure_filename(thumb.filename)
        thumb_path = os.path.join(THUMB_FOLDER, thumb_filename)
        thumb.save(thumb_path)
    else:
        thumb_path = "static/media/thumbs/default_thumb.jpg"  # Fallback to default thumbnail

    # Initialize progress
    total_files = len(videos)
    uploaded_files = 0

    # Process each video file
    for video in videos:
        if not allowed_file(video.filename):
            continue  # Skip invalid files

        video_filename = secure_filename(video.filename)
        if '.' not in video_filename:
            video_filename += '.mp4'

        # Handle Series-specific folder structure
        if category == "Series":
            base_path = CATEGORY_FOLDERS["Series"]
            show_folder = os.path.join(base_path, title)
            season_folder = os.path.join(show_folder, season)
            os.makedirs(season_folder, exist_ok=True)

            episode_name = episode if episode else os.path.splitext(video_filename)[0]
            if not episode_name.lower().endswith('.mp4'):
                episode_name += '.mp4'

            video_path = os.path.join(season_folder, episode_name)
        else:
            folder_path = CATEGORY_FOLDERS.get(category, UPLOAD_FOLDER)
            os.makedirs(folder_path, exist_ok=True)
            video_path = os.path.join(folder_path, video_filename)

        # Save the video file
        video.save(video_path)
        uploaded_files += 1

        # Insert video details into the DB
        with sqlite3.connect(DB) as conn:
            conn.execute("""
                INSERT INTO movies (title, genre, description, video_path, thumbnail, category, folder, season, episode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                title,
                request.form.get('genre'),
                request.form.get('description'),
                video_path.replace("\\", "/"),
                thumb_path.replace("\\", "/"),
                category,
                folder,
                season,
                episode
            ))
            conn.commit()

        # Send progress information (for frontend)
        progress = (uploaded_files / total_files) * 100  # percentage
        print(f"Upload Progress: {progress}%")

    return "Upload successful"

# ----------------- Admin Management APIs -----------------

# Get all users
@app.route("/admin/users")
def get_users():
    if "user" not in session or not session.get("admin"):
        return "Forbidden", 403
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        users = conn.execute("SELECT id, username, payments_enabled FROM users").fetchall()
    return jsonify({"users": [dict(u) for u in users]})

# Toggle user payment access
@app.route("/admin/toggle_user/<int:user_id>", methods=["POST"])
def toggle_user(user_id):
    if "user" not in session or not session.get("admin"):
        return "Forbidden", 403
    with sqlite3.connect(DB) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET payments_enabled = CASE WHEN payments_enabled=1 THEN 0 ELSE 1 END WHERE id=?",
                    (user_id,))
        conn.commit()
    return jsonify({"status": "success"})

# Delete a user
@app.route("/admin/delete_user/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    if "user" not in session or not session.get("admin"):
        return "Forbidden", 403
    with sqlite3.connect(DB) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    return jsonify({"status": "success"})

# Toggle global payment
@app.route("/admin/toggle_global_payment", methods=["POST"])
def toggle_global_payment():
    if "user" not in session or not session.get("admin"):
        return "Forbidden", 403
    if not os.path.exists("global_payment.txt"):
        with open("global_payment.txt", "w") as f:
            f.write("enabled")
    else:
        with open("global_payment.txt", "r+") as f:
            current = f.read().strip()
            f.seek(0)
            f.write("disabled" if current == "enabled" else "enabled")
            f.truncate()
    return jsonify({"status": "success"})

# List movies
@app.route("/admin/movies")
def list_movies():
    if "user" not in session or not session.get("admin"):
        return "Forbidden", 403
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        movies = conn.execute("SELECT id, title, category FROM movies ORDER BY id DESC").fetchall()
    return jsonify({"movies": [dict(m) for m in movies]})

# Delete movie
@app.route("/admin/delete_movie/<int:movie_id>", methods=["POST"])
def delete_movie(movie_id):
    if "user" not in session or not session.get("admin"):
        return "Forbidden", 403
    with sqlite3.connect(DB) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM movies WHERE id=?", (movie_id,))
        conn.commit()
    return jsonify({"status": "success"})

# Most viewed movies
@app.route("/admin/most_viewed")
def most_viewed():
    if "user" not in session or not session.get("admin"):
        return "Forbidden", 403
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        movies = conn.execute("SELECT id, title, views FROM movies ORDER BY views DESC LIMIT 10").fetchall()
    return jsonify({"movies": [dict(m) for m in movies]})

# Payment history
@app.route("/admin/payments")
def payment_history():
    if "user" not in session or not session.get("admin"):
        return "Forbidden", 403
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        payments = conn.execute("""
            SELECT payments.id, users.username, payments.amount, payments.timestamp
            FROM payments
            LEFT JOIN users ON payments.user_id = users.id
            ORDER BY payments.id DESC LIMIT 20
        """).fetchall()
    return jsonify({"payments": [dict(p) for p in payments]})

@app.errorhandler(413)
def too_large(e): return "File too large (max 2GB)", 413

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ----------------- Run -----------------

if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))  # Listen on port 8080 or environment variable PORT
