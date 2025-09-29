from flask import Flask, request, render_template, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re

app = Flask(__name__, template_folder=".")
CORS(app)  # allow calls from Flutter

ERP_BASE = "http://sue.su.edu.bd:5081/sonargaon_erp"

DAY_ORDER = {
    'Sunday': 0,
    'Monday': 1, 
    'Tuesday': 2,
    'Wednesday': 3,
    'Thursday': 4,
    'Friday': 5,
    'Saturday': 6
}

# ------------------------
# Function: scrape profile
# ------------------------
def scrape_name_and_id(userid: str, password: str):
    """Logs in and scrapes name + ID. Returns (name, sid) or raises ValueError."""
    login_url = f"{ERP_BASE}/"
    profile_url = f"{ERP_BASE}/student/profile/profileList/{userid}"

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; SU-Scraper/1.0)"
    })

    # Step 1: Login
    login_resp = session.post(login_url, data={"email": userid, "password": password}, timeout=15)
    if login_resp.status_code != 200:
        raise ValueError(f"Login failed (status {login_resp.status_code}).")

    # Step 2: Fetch profile
    profile_resp = session.get(profile_url, timeout=15)
    if profile_resp.status_code != 200:
        raise ValueError(f"Could not load profile page (status {profile_resp.status_code}).")

    # Step 3: Parse
    soup = BeautifulSoup(profile_resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        raise ValueError("Could not find profile table. Maybe login failed.")

    # find first data row (not header)
    row = None
    for tr in table.find_all("tr"):
        if tr.find("td"):  # only pick rows with <td>
            row = tr
            break
    if not row:
        raise ValueError("Could not find data row in profile table.")

    cells = row.find_all("td")
    if not cells:
        raise ValueError("Profile row has no cells.")

    raw_name = cells[0].get_text(strip=True)
    m = re.search(r"\(([^)]+)\)", raw_name)
    name = m.group(1) if m else raw_name

    return name, userid


# ------------------------
# Route 1: HTML form (old)
# ------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    name = sid = error = None
    if request.method == "POST":
        userid = request.form["userid"]
        password = request.form["password"]
        try:
            name, sid = scrape_name_and_id(userid, password)
        except ValueError as e:
            error = str(e)
    return render_template("index.html", name=name, sid=sid, error=error)

@app.route("/routine")
def get_routine():
    return render_template("index.html")

# ------------------------
# Route 2: JSON API (new)
# ------------------------
@app.route("/api/login", methods=["POST"])
def api_login():
    """
    JSON in: {"userid":"...", "password":"..."}
    JSON out:
      success -> {"ok": true, "name": "...", "sid": "..."}
      error   -> {"ok": false, "error": "..."}
    """
    data = request.get_json(silent=True) or {}
    userid = data.get("userid", "").strip()
    password = data.get("password", "")

    if not userid or not password:
        return jsonify({"ok": False, "error": "userid and password are required"}), 400

    try:
        name, sid = scrape_name_and_id(userid, password)
        return jsonify({"ok": True, "name": name, "sid": sid})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": "Unexpected server error"}), 500

CLASS_ROUTINE_URL = "http://sue.su.edu.bd:5081/sonargaon_erp/student/semester_course_report/class_routine_report_student/"


def clean_text(text: str) -> str:
    """Replace all whitespace (including non-breaking, invisible, etc.) with a single space and strip ends."""
    return re.sub(r"\s+", " ", text).strip()


def fetch_table(
    semester_id: int,
    year: int,
    student_id: str,
    password: str,
) -> list[dict]:
    payload = {"semester_id": semester_id, "year": year}
    session = requests.Session()
    login_url = "http://sue.su.edu.bd:5081/sonargaon_erp/siteadmin"
    dashboard_url = "http://sue.su.edu.bd:5081/sonargaon_erp/student/backend"
    res = session.post(
        login_url, data={"email": student_id, "password": password, "remember": "1"}
    )
    if res.url != dashboard_url:
        raise ValueError("Login failed. Check your credentials.")

    with open("test.html", "w", encoding="utf-8") as f:
        f.write(res.text)

    # Send POST with cookies if provided
    resp = session.post(CLASS_ROUTINE_URL, data=payload)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    trs = soup.select(".table-responsive tr")
    if not trs:
        raise ValueError("No rows found in the table body.")

    headers = [clean_text(th.get_text(strip=True)) for th in trs[0].find_all("th")]
    print("Headers:", headers)

    data = []  # list of dicts
    for tr in trs[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) != len(headers):
            print("Skipping row due to mismatch in number of cells and headers.")
            continue
        row_data = {
            headers[i]: clean_text(cells[i].get_text(strip=True))
            for i in range(len(headers))
        }
        data.append(row_data)

    return data


@app.route("/api/routine", methods=["POST"])
def api_routine():
    """
      JSON in: {"semester_id": ..., "year": ..., "student_id": "...", "password": "..."}
      JSON out:
        success -> {"data": [
        {
        '(Floor, Room)': '(Floor 04, Room: West-508)',
        'CR Name Mobile': 'Abdul Al Tanvir 01614182179',
        'Campus Building': 'Campus 1(West147(147/I Green Road))',
        'Class Time': '10:00am-11:00am',
        'Course Code': 'CSE425',
        'Course Credit': '3',
        'Course Title': 'Pattern Recognition',
        'Course Type': 'Theory',
        'Day': 'Wednesday',
        'Email': '23kheya@gmail.com',
        'Google Class Link': '',
        'Mobile': '01765298485',
        'SL': '1',
        'Section': 'CSE0225-Eliza(25M1)',
        'Teacher Name': 'Tasnia Haque Kheya'
    },
    ...]
        failure -> {"error": "..."}

    semester_id: int (1=Spring, 2=Summer, 3=Fall)
    year: int (e.g., 2025)
    student_id: str (e.g., "CSE2201025060")
    password: str (e.g., "password")
    """
    data = request.get_json(silent=True) or {}
    required_fields = ["semester_id", "year", "student_id", "password"]
    for field in required_fields:
        if field not in data:
            return {"error": f"Missing field: {field}"}, 400
    try:
        table_data = fetch_table(
            data["semester_id"],
            data["year"],
            data["student_id"],
            data["password"],
        )
        
        # Filter only required fields
        filtered_data = []
        for row in table_data:
            filtered_row = {
                'Room': row['(Floor, Room)'],
                'Time': row['Class Time'],
                'Course': row['Course Title'],
                'Day': row['Day'],
                'Teacher': row['Teacher Name']
            }
            filtered_data.append(filtered_row)
        
        # Sort the filtered data by day and then by time
        # Helper function to parse time strings like "10:00am-11:00am"
        def parse_time(time_str):
            import datetime
            # Extract the start time (before '-')
            start = time_str.split('-')[0].strip()
            # Convert to 24-hour time for sorting
            try:
                return datetime.datetime.strptime(start, "%I:%M%p").time()
            except Exception:
                return datetime.time(0, 0)  # fallback if parsing fails

        sorted_data = sorted(
            filtered_data,
            key=lambda x: (
                DAY_ORDER.get(x['Day'], 999),  # First sort by day
                parse_time(x['Time'])  # Then sort by time
            )
        )
        
        return {"data": sorted_data}, 200
    except ValueError as e:
        return {"error": str(e)}, 400

# ------------------------
# Run server
# ------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
