import os
import sqlite3
import hashlib
import re
import datetime
from flask import Flask, request, jsonify, redirect, session, render_template_string, send_from_directory
from flask_cors import CORS
from urllib.parse import unquote
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from waitress import serve


# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))

# Admin key and upload limits
ADMIN_KEY = os.getenv("ADMIN_KEY", "super-secret-admin-key")
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB upload limit

# Initialize Babel for translations


# Select language based on browser's accept-language header

# Define media category folders
CATEGORY_FOLDERS = {
    "Movies": "static/media/movies",
    "Series": "static/media/series",
    "Asian": "static/media/asian",  # K-Drama / Asian Series
    "Animation": "static/media/animation",
    "Documentaries": "static/media/documentaries",
    "Wrestling": "static/media/wrestling"
}

# Create category folders if they don't exist
for folder in CATEGORY_FOLDERS.values():
    os.makedirs(folder, exist_ok=True)

# Define upload and thumbnail folders
UPLOAD_FOLDER = "static/media/uploads"
THUMB_FOLDER = "static/media/thumbs"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

# Default thumbnail setup
DEFAULT_THUMB_PATH = os.path.join(THUMB_FOLDER, "default_thumb.jpg")
if not os.path.exists(DEFAULT_THUMB_PATH):
    try:
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (300, 450), color=(100, 100, 100))  # Gray background
        draw = ImageDraw.Draw(img)
        draw.text((75, 200), "No Image", fill=(255, 255, 255))
        img.save(DEFAULT_THUMB_PATH)
    except ImportError:
        with open(DEFAULT_THUMB_PATH, 'wb') as f:
            pass

# ----------------- Database Initialization -----------------
DB = 'db.sqlite3'

def init_db():
    """Initialize the database with required tables"""
    with sqlite3.connect(DB) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            payments_enabled INTEGER DEFAULT 1)''')

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

        conn.execute('''CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL)''')

        conn.execute('''CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            message TEXT NOT NULL)''')

        conn.execute('''CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'success',
            FOREIGN KEY (user_id) REFERENCES users(id))''')

        conn.execute('''CREATE TABLE IF NOT EXISTS user_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            description TEXT,
            timestamp TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id))''')

# Initialize the database if it doesn't exist
if not os.path.exists(DB):
    init_db()

# ----------------- Utility Functions -----------------
def hash_password(pw):
    """Hash the password using SHA-256"""
    return hashlib.sha256(pw.encode()).hexdigest()

def is_valid_email(email):
    """Check if the email is valid"""
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

def serve_html(name):
    """Serve raw HTML files instead of Jinja templates"""
    try:
        with open(f"{name}.html", encoding="utf-8") as file:
            return render_template_string(file.read())
    except FileNotFoundError:
        return f"{name}.html not found", 404

def is_global_payment_enabled():
    """Check if global payment mode is enabled"""
    if not os.path.exists("global_payment.txt"):
        return True
    with open("global_payment.txt", "r") as f:
        return f.read().strip() == "enabled"

def has_payment(username):
    """Check if the user has payments enabled"""
    if not is_global_payment_enabled():
        return False
    with sqlite3.connect(DB) as conn:
        user = conn.execute("SELECT payments_enabled FROM users WHERE username=?", (username,)).fetchone()
        return user and user[0] == 1

# Allowed file types for upload
ALLOWED_EXTENSIONS = {'mp4', 'mkv', 'avi', 'mov', 'jpg', 'jpeg', 'png'}
def allowed_file(filename):
    """Check if the file has an allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ----------------- Public Routes -----------------
@app.route("/")
def index():
    """Render the index page"""
    return serve_html("index")

@app.route("/home")
def home():
    """Render the home page if the user is logged in"""
    if "user" not in session:
        return redirect("/")
    return serve_html("home")

@app.route("/movies")
def movies():
    """Render the movies page if the user is logged in"""
    if "user" not in session:
        return redirect("/")
    return serve_html("movies")

# ----------------- Authentication -----------------
@app.route("/auth", methods=["POST"])
def auth():
    """Handle user authentication actions (register, login, reset)"""
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
            log_user_activity(cur.lastrowid, "register", f"User {username} registered")
            session.update({"user": username, "email": email, "admin": is_admin})
            return jsonify({"status": "ok", "redirect": "/admin" if is_admin else "/home"})

        elif action == "login":
            user = cur.execute("SELECT * FROM users WHERE (username=? OR email=?) AND password=?",
                               (username, username, hash_password(password))).fetchone()
            if user:
                log_user_activity(user[0], "login", f"User {username} logged in successfully.")
                session.update({"user": user[1], "email": user[2], "admin": user[4]})
                return jsonify({"status": "ok", "redirect": "/admin" if user[4] else "/home"})
            return jsonify({"status": "error", "message": "Invalid credentials."})

        elif action == "reset":
            if len(new_password) < 6: return jsonify({"status": "error", "message": "Password too short."})
            if cur.execute("UPDATE users SET password=? WHERE (username=? OR email=?)",
                           (hash_password(new_password), username, email)).rowcount:
                conn.commit()
                log_user_activity(cur.lastrowid, "reset_password", f"Password reset for {username}")
                return jsonify({"status": "ok"})
            return jsonify({"status": "error", "message": "User not found."})
    return jsonify({"status": "error", "message": "Unknown action."})

# ----------------- Run the app -----------------
if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))  # Listen on port 8080 or environment variable PORT
