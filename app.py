from flask import Flask, render_template, request, redirect, url_for, session, Response, send_file, flash
import os, sqlite3, json
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from anpr_engine import detect_plate
from camera import VideoCamera
import pandas as pd
from io import BytesIO
from fpdf import FPDF

app = Flask(__name__)
app.secret_key = "supersecretkey"
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

USERS_FILE = "users.json"
CAMERA_JSON = "cameras.json"
LICENSE_FILE = "license_expiry.json"
ARCHIVE_FILE = "archive_days.json"
PAGE_SIZE = 10

# ------------------ Load/Save Utilities ------------------
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return json.load(f)
    return {}

def load_camera_map():
    if os.path.exists(CAMERA_JSON):
        with open(CAMERA_JSON) as f:
            return json.load(f)
    return {}

def save_camera_map(data):
    with open(CAMERA_JSON, "w") as f:
        json.dump(data, f, indent=4)

def load_retention():
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE) as f:
            return int(json.load(f).get("days", 30))
    return 30

def save_retention(days):
    with open(ARCHIVE_FILE, "w") as f:
        json.dump({"days": days}, f)

def load_license_days_left():
    if os.path.exists(LICENSE_FILE):
        with open(LICENSE_FILE) as f:
            exp = json.load(f).get("expiry")
            expiry = datetime.strptime(exp, "%Y-%m-%d")
            return max(0, (expiry - datetime.now()).days)
    return 0

def load_license_key():
    if os.path.exists(LICENSE_FILE):
        with open(LICENSE_FILE) as f:
            return json.load(f).get("expiry")
    return ""

def save_license_date(date_str):
    with open(LICENSE_FILE, "w") as f:
        json.dump({"expiry": date_str}, f)

# ------------------ Globals ------------------
camera_map = load_camera_map()
stream_url = None

def init_db():
    with sqlite3.connect("database.db") as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            plate TEXT,
            timestamp TEXT,
            camera TEXT,
            confidence REAL,
            region TEXT
        )''')

# ------------------ Routes ------------------

@app.route('/', methods=['GET', 'POST'])
def login():
    users = load_users()
    if request.method == 'POST':
        uname = request.form['username']
        pwd = request.form['password']
        role = request.form['role']

        if uname in users and users[uname]["password"] == pwd and users[uname]["role"] == role:
            session['user'] = uname
            session['role'] = role
            return redirect(url_for("dashboard"))
        return "Invalid credentials"
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM logs")
        vehicle_count = cur.fetchone()[0]
        cur.execute("SELECT plate, timestamp FROM logs ORDER BY id DESC LIMIT 1")
        last = cur.fetchone()
        cur.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 5")
        logs = cur.fetchall()

    return render_template("dashboard.html",
        vehicle_count=vehicle_count,
        last_detected=last[1] if last else "N/A",
        last_plate=last[0] if last else "N/A",
        detection_rate="98%",
        camera_count=len(camera_map),
        logs=logs,
        show_stream=bool(stream_url),
        cameras=list(camera_map.keys()),
        license_days=load_license_days_left(),
        retention_days=load_retention(),
        role=session.get("role"))

@app.route('/select_camera')
def select_camera():
    global stream_url
    name = request.args.get("name")
    stream_url = camera_map.get(name, "")
    return '', 204

@app.route('/video_feed')
def video_feed():
    def generate():
        global stream_url
        cam = VideoCamera(stream_url or "rtsp://your_default_rtsp_here")
        while True:
            frame = cam.get_frame()
            if frame:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n\r\n')
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/save_camera', methods=['POST'])
def save_camera():
    name = request.form.get("camera_name")
    url = request.form.get("camera_url")
    if name and url:
        camera_map[name] = url
        save_camera_map(camera_map)
    return redirect(url_for("settings"))

@app.route('/update_camera', methods=['POST'])
def update_camera():
    old = request.form.get("original_name")
    new_name = request.form.get("camera_name")
    new_url = request.form.get("camera_url")
    if old in camera_map:
        del camera_map[old]
    camera_map[new_name] = new_url
    save_camera_map(camera_map)
    return redirect(url_for("settings"))

@app.route('/delete_camera/<name>')
def delete_camera(name):
    if name in camera_map:
        del camera_map[name]
        save_camera_map(camera_map)
    return redirect(url_for("settings"))

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['image']
    if file:
        fname = secure_filename(file.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
        file.save(path)
        plate = detect_plate(path)
        time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect("database.db") as conn:
            conn.execute("INSERT INTO logs (filename, plate, timestamp, camera, confidence, region) VALUES (?, ?, ?, ?, ?, ?)",
                         (fname, plate, time, "Camera 1", 0.90, "Delhi"))
        return redirect(url_for('dashboard'))
    return "Upload failed"

@app.route('/latest_events')
def latest_events():
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 5")
        logs = cur.fetchall()
    return render_template("events.html", logs=logs)

@app.route('/report')
def report():
    if 'user' not in session:
        return redirect(url_for('login'))

    page = int(request.args.get("page", 1))
    offset = (page - 1) * PAGE_SIZE
    filter_type = request.args.get("filter", "today")
    start = request.args.get("start")
    end = request.args.get("end")
    camera = request.args.get("camera")
    plate = request.args.get("plate")

    query = "SELECT * FROM logs WHERE 1=1"
    count = "SELECT COUNT(*) FROM logs WHERE 1=1"
    params = []

    if filter_type == "today":
        query += " AND timestamp LIKE ?"
        count += " AND timestamp LIKE ?"
        params.append(datetime.now().strftime("%Y-%m-%d") + "%")
    elif filter_type == "week":
        week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        query += " AND timestamp >= ?"
        count += " AND timestamp >= ?"
        params.append(week_start)
    elif filter_type == "month":
        month_start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        query += " AND timestamp >= ?"
        count += " AND timestamp >= ?"
        params.append(month_start)
    elif filter_type == "custom" and start and end:
        query += " AND timestamp BETWEEN ? AND ?"
        count += " AND timestamp BETWEEN ? AND ?"
        params.extend([start, end])

    if camera:
        query += " AND camera = ?"
        count += " AND camera = ?"
        params.append(camera)

    if plate:
        query += " AND plate LIKE ?"
        count += " AND plate LIKE ?"
        params.append(f"%{plate}%")

    query += " ORDER BY id DESC LIMIT ? OFFSET ?"

    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute(count, params)
        total = cur.fetchone()[0]
        cur.execute(query, params + [PAGE_SIZE, offset])
        data = cur.fetchall()

    return render_template("report.html", data=data, page=page, total=total, page_size=PAGE_SIZE,
        camera_list=list(camera_map.keys()), filter=filter_type, start=start, end=end,
        camera=camera, plate=plate,
        export_url=url_for("export_excel", **request.args),
        export_pdf_url=url_for("export_pdf", **request.args))

@app.route('/export_excel')
def export_excel():
    return export_data('excel')

@app.route('/export_pdf')
def export_pdf():
    return export_data('pdf')

def export_data(fmt):
    filter_type = request.args.get("filter", "today")
    start = request.args.get("start")
    end = request.args.get("end")
    camera = request.args.get("camera")
    plate = request.args.get("plate")

    query = "SELECT * FROM logs WHERE 1=1"
    params = []

    if filter_type == "today":
        query += " AND timestamp LIKE ?"
        params.append(datetime.now().strftime("%Y-%m-%d") + "%")
    elif filter_type == "week":
        query += " AND timestamp >= ?"
        params.append((datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
    elif filter_type == "month":
        query += " AND timestamp >= ?"
        params.append((datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    elif filter_type == "custom" and start and end:
        query += " AND timestamp BETWEEN ? AND ?"
        params.extend([start, end])

    if camera:
        query += " AND camera = ?"
        params.append(camera)

    if plate:
        query += " AND plate LIKE ?"
        params.append(f"%{plate}%")

    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        data = cur.fetchall()

    if fmt == 'excel':
        df = pd.DataFrame(data, columns=["ID", "Image", "Plate", "Timestamp", "Camera", "Confidence", "Region"])
        output = BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(output, download_name="anpr_report.xlsx", as_attachment=True)

    elif fmt == 'pdf':
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=8)
        pdf.cell(200, 10, "ANPR Logs Report", ln=True, align="C")
        for row in data:
            pdf.cell(200, 8, txt=str(row), ln=True)
        buffer = BytesIO()
        pdf.output(buffer)
        buffer.seek(0)
        return send_file(buffer, download_name="anpr_report.pdf", as_attachment=True)

@app.route('/settings')
def settings():
    return render_template("settings.html",
        cameras=camera_map,
        archive_days=load_retention(),
        current_key=load_license_key(),
        days_left=load_license_days_left())

@app.route('/settings/archive', methods=['POST'])
def update_archive():
    days = request.form.get("archive_days")
    try:
        save_retention(int(days))
    except:
        pass
    return redirect(url_for("settings"))

@app.route('/settings/license', methods=['POST'])
def update_license():
    key = request.form.get("license_key")
    try:
        datetime.strptime(key, "%Y-%m-%d")
        save_license_date(key)
    except:
        pass
    return redirect(url_for("settings"))

@app.route('/about')
def about():
    return render_template("about.html")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("login"))

# ------------------ Run ------------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True)
