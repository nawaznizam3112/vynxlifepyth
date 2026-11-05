
import streamlit as st
import sqlite3
import hashlib
import secrets
import qrcode
import io
import re
import os
import pandas as pd
import plotly.express as px

from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode


# =========================================================
# VYNX.LIFE RPM v1.0
# SQLite version — no Supabase required
# =========================================================

st.set_page_config(
    page_title="Vynx.life RPM",
    page_icon="💙",
    layout="wide",
)

DB_FILE = "vynx_life.db"

st.markdown("""
<style>
.stApp { background: #f4f7fb; }

[data-testid="stSidebar"] {
    background: #10233f;
}

[data-testid="stSidebar"] * {
    color: white !important;
}

.hero {
    background: linear-gradient(120deg,#10233f,#176b87);
    color: white;
    padding: 24px;
    border-radius: 18px;
    margin-bottom: 20px;
}

.hero h1 {
    color: white;
    margin: 0;
}

.hero p {
    color: #dbeafe;
    margin-bottom: 0;
}

div[data-testid="stMetric"] {
    background: white;
    border: 1px solid #e5eaf1;
    padding: 15px;
    border-radius: 14px;
}
</style>
""", unsafe_allow_html=True)


# =========================================================
# DATABASE
# =========================================================

def connect():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS doctors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        clinic TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        patient_code TEXT NOT NULL,
        age INTEGER,
        sex TEXT,
        phone TEXT,
        conditions TEXT,
        qr_token TEXT UNIQUE NOT NULL,
        pin_hash TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        UNIQUE(doctor_id, patient_code)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        systolic INTEGER,
        diastolic INTEGER,
        pulse INTEGER,
        spo2 REAL,
        temperature REAL,
        weight REAL,
        glucose REAL,
        notes TEXT,
        recorded_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        reading_id INTEGER,
        alert_type TEXT,
        message TEXT,
        severity TEXT,
        reviewed INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()


init_db()


# =========================================================
# HELPERS
# =========================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def hash_password(password):
    salt = secrets.token_hex(16)

    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        200000,
    ).hex()

    return f"{salt}${hashed}"


def check_password(password, stored):
    try:
        salt, expected = stored.split("$")

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt.encode(),
            200000,
        ).hex()

        return secrets.compare_digest(actual, expected)

    except Exception:
        return False


def query(sql, params=()):
    conn = connect()
    result = conn.execute(sql, params).fetchall()
    conn.close()
    return result


def execute(sql, params=()):
    conn = connect()
    cur = conn.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def get_patient(token):
    rows = query(
        "SELECT * FROM patients WHERE qr_token=? AND active=1",
        (token,),
    )

    return dict(rows[0]) if rows else None


def get_doctor_patients(doctor_id):
    rows = query(
        "SELECT * FROM patients WHERE doctor_id=? ORDER BY created_at DESC",
        (doctor_id,),
    )

    return [dict(row) for row in rows]


def get_readings(patient_id, limit=500):
    rows = query(
        """
        SELECT * FROM readings
        WHERE patient_id=?
        ORDER BY recorded_at DESC
        LIMIT ?
        """,
        (patient_id, limit),
    )

    return [dict(row) for row in rows]


def get_alerts(patient_id):
    rows = query(
        """
        SELECT * FROM alerts
        WHERE patient_id=?
        ORDER BY created_at DESC
        """,
        (patient_id,),
    )

    return [dict(row) for row in rows]


def create_qr(url):
    qr = qrcode.QRCode(
        box_size=8,
        border=3,
    )

    qr.add_data(url)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="#10233f",
        back_color="white",
    )

    output = io.BytesIO()
    image.save(output, format="PNG")

    return output.getvalue()


def make_alerts(reading):
    alerts = []

    # Illustrative prototype thresholds only.
    # These are not diagnostic rules.

    if reading["systolic"] >= 180:
        alerts.append((
            "High systolic BP",
            "Systolic BP is 180 mmHg or higher.",
            "urgent",
        ))
    elif reading["systolic"] >= 140:
        alerts.append((
            "Elevated systolic BP",
            "Systolic BP is 140 mmHg or higher.",
            "warning",
        ))

    if reading["diastolic"] >= 120:
        alerts.append((
            "High diastolic BP",
            "Diastolic BP is 120 mmHg or higher.",
            "urgent",
        ))
    elif reading["diastolic"] >= 90:
        alerts.append((
            "Elevated diastolic BP",
            "Diastolic BP is 90 mmHg or higher.",
            "warning",
        ))

    if reading["spo2"] < 90:
        alerts.append((
            "Low SpO₂",
            "SpO₂ is below 90%.",
            "urgent",
        ))
    elif reading["spo2"] < 94:
        alerts.append((
            "Reduced SpO₂",
            "SpO₂ is below 94%.",
            "warning",
        ))

    if reading["pulse"] < 50 or reading["pulse"] > 120:
        alerts.append((
            "Pulse outside default range",
            "Pulse is below 50 or above 120 bpm.",
            "warning",
        ))

    return alerts


def save_reading(patient_id, reading):
    reading_id = execute(
        """
        INSERT INTO readings (
            patient_id, systolic, diastolic, pulse, spo2,
            temperature, weight, glucose, notes, recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            patient_id,
            reading["systolic"],
            reading["diastolic"],
            reading["pulse"],
            reading["spo2"],
            reading["temperature"],
            reading["weight"],
            reading["glucose"],
            reading["notes"],
            now(),
        ),
    )

    for alert_type, message, severity in make_alerts(reading):
        execute(
            """
            INSERT INTO alerts (
                patient_id, reading_id, alert_type,
                message, severity, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                patient_id,
                reading_id,
                alert_type,
                message,
                severity,
                now(),
            ),
        )


# =========================================================
# SESSION
# =========================================================

if "doctor" not in st.session_state:
    st.session_state.doctor = None

if "patient" not in st.session_state:
    st.session_state.patient = None

if "new_patient" not in st.session_state:
    st.session_state.new_patient = None


# =========================================================
# HEADER
# =========================================================

st.markdown("""
<div class="hero">
    <h1>💙 Vynx.life</h1>
    <p>Remote Patient Monitoring & Vital Signs Tracking</p>
</div>
""", unsafe_allow_html=True)


# =========================================================
# PATIENT PORTAL
# =========================================================

token = st.query_params.get("patient")

if token:

    if st.session_state.patient:
        patient = st.session_state.patient

    else:
        patient = get_patient(token)

        if not patient:
            st.error("Invalid or inactive patient QR link.")
            st.stop()

        st.subheader("Patient login")

        st.write(
            f"Welcome, **{patient['name']}**. "
            "Enter your 6-digit PIN."
        )

        pin = st.text_input(
            "PIN",
            type="password",
            max_chars=6,
        )

        if st.button("Access my health records", type="primary"):

            if not re.fullmatch(r"\d{6}", pin or ""):
                st.error("Enter a valid 6-digit PIN.")
                st.stop()

            if check_password(pin, patient["pin_hash"]):
                st.session_state.patient = patient
                st.rerun()

            else:
                st.error("Incorrect PIN.")

        st.stop()

    patient = st.session_state.patient

    st.subheader(f"Hello, {patient['name']}")

    st.caption(
        "Record your readings below. "
        "Your linked doctor can view them."
    )

    with st.form("patient_vitals"):

        st.subheader("Record vital signs")

        c1, c2 = st.columns(2)

        with c1:
            systolic = st.number_input(
                "Systolic BP (mmHg)",
                50, 300, 120,
            )

            diastolic = st.number_input(
                "Diastolic BP (mmHg)",
                30, 200, 80,
            )

            pulse = st.number_input(
                "Pulse (bpm)",
                20, 250, 75,
            )

        with c2:
            spo2 = st.number_input(
                "SpO₂ (%)",
                50.0, 100.0, 98.0,
            )

            temperature = st.number_input(
                "Temperature (°C)",
                30.0, 45.0, 36.8,
            )

            weight = st.number_input(
                "Weight (kg)",
                1.0, 400.0, 70.0,
            )

        glucose = st.number_input(
            "Glucose (mg/dL; 0 if not measured)",
            0.0, 1000.0, 0.0,
        )

        notes = st.text_area("Notes")

        submitted = st.form_submit_button(
            "Save readings",
            type="primary",
            use_container_width=True,
        )

    if submitted:

        reading = {
            "systolic": int(systolic),
            "diastolic": int(diastolic),
            "pulse": int(pulse),
            "spo2": float(spo2),
            "temperature": float(temperature),
            "weight": float(weight),
            "glucose": float(glucose) if glucose > 0 else None,
            "notes": notes.strip() or None,
        }

        save_reading(patient["id"], reading)

        st.success("Your readings have been saved.")

        if make_alerts(reading):
            st.warning(
                "One or more readings have been flagged. "
                "Follow your clinician's instructions. "
                "This is not an emergency monitoring service."
            )

        st.rerun()

    st.divider()

    st.subheader("My health history")

    readings = get_readings(patient["id"])

    if readings:
        df = pd.DataFrame(readings)

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )

        df = df.sort_values("recorded_at")

        fig = px.line(
            df,
            x="recorded_at",
            y=["systolic", "diastolic"],
            markers=True,
            title="Blood pressure trend",
        )

        st.plotly_chart(fig, use_container_width=True)

    else:
        st.info("No readings recorded yet.")

    if st.button("Log out"):
        st.session_state.patient = None
        st.rerun()

    st.stop()


# =========================================================
# DOCTOR PORTAL
# =========================================================

st.sidebar.title("Vynx.life")
st.sidebar.caption("Doctor control panel")

page = st.sidebar.radio(
    "Navigation",
    [
        "Doctor Login",
        "Doctor Registration",
        "Dashboard",
        "Register Patient",
        "Patient Records",
        "Alerts",
    ],
)


# =========================================================
# DOCTOR REGISTRATION
# =========================================================

if page == "Doctor Registration":

    st.subheader("Create doctor account")

    with st.form("doctor_registration"):

        name = st.text_input("Doctor name")
        email = st.text_input("Email")
        clinic = st.text_input("Clinic name")

        password = st.text_input(
            "Password",
            type="password",
        )

        confirm = st.text_input(
            "Confirm password",
            type="password",
        )

        submitted = st.form_submit_button(
            "Create account",
            type="primary",
        )

    if submitted:

        if not name.strip() or not email.strip():
            st.error("Name and email are required.")
            st.stop()

        if len(password) < 8:
            st.error("Password must contain at least 8 characters.")
            st.stop()

        if password != confirm:
            st.error("Passwords do not match.")
            st.stop()

        try:
            execute(
                """
                INSERT INTO doctors (
                    name, email, password_hash, clinic, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    name.strip(),
                    email.strip().lower(),
                    hash_password(password),
                    clinic.strip(),
                    now(),
                ),
            )

            st.success(
                "Doctor account created. "
                "You can now log in."
            )

        except sqlite3.IntegrityError:
            st.error("This email is already registered.")

    st.stop()


# =========================================================
# DOCTOR LOGIN
# =========================================================

if page == "Doctor Login":

    st.subheader("Doctor login")

    with st.form("doctor_login"):

        email = st.text_input("Email")

        password = st.text_input(
            "Password",
            type="password",
        )

        submitted = st.form_submit_button(
            "Sign in",
            type="primary",
        )

    if submitted:

        doctors = query(
            "SELECT * FROM doctors WHERE email=?",
            (email.strip().lower(),),
        )

        if doctors and check_password(
            password,
            doctors[0]["password_hash"],
        ):

            st.session_state.doctor = dict(doctors[0])

            st.success("Login successful.")
            st.rerun()

        else:
            st.error("Incorrect email or password.")

    st.info(
        "New doctor? Select Doctor Registration "
        "from the sidebar."
    )

    st.stop()


# =========================================================
# AUTHENTICATION
# =========================================================

doctor = st.session_state.doctor

if not doctor:
    st.warning("Please sign in first.")
    st.stop()

doctor_id = doctor["id"]

st.sidebar.success(f"Doctor: {doctor['name']}")

if st.sidebar.button("Sign out"):
    st.session_state.doctor = None
    st.rerun()

patients = get_doctor_patients(doctor_id)


# =========================================================
# DASHBOARD
# =========================================================

if page == "Dashboard":

    st.subheader("Doctor dashboard")

    total_readings = 0
    latest_readings = []

    for patient in patients:

        readings = get_readings(patient["id"], limit=1)

        if readings:
            latest = readings[0]
            latest["patient_name"] = patient["name"]
            latest["patient_code"] = patient["patient_code"]

            latest_readings.append(latest)
            total_readings += 1

    alert_count = 0

    for patient in patients:
        alert_count += len([
            a for a in get_alerts(patient["id"])
            if not a["reviewed"]
        ])

    c1, c2, c3 = st.columns(3)

    c1.metric("Registered patients", len(patients))
    c2.metric("Patients with readings", total_readings)
    c3.metric("Unreviewed alerts", alert_count)

    st.divider()

    st.subheader("Latest readings")

    if latest_readings:

        df = pd.DataFrame(latest_readings)

        columns = [
            "patient_name",
            "patient_code",
            "systolic",
            "diastolic",
            "pulse",
            "spo2",
            "temperature",
            "recorded_at",
        ]

        st.dataframe(
            df[columns],
            use_container_width=True,
            hide_index=True,
        )

    else:
        st.info("No readings have been submitted yet.")

    st.divider()

    st.subheader("Registered patients")

    if patients:

        df = pd.DataFrame(patients)

        st.dataframe(
            df[
                [
                    "name",
                    "patient_code",
                    "age",
                    "sex",
                    "phone",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )


# =========================================================
# REGISTER PATIENT
# =========================================================

elif page == "Register Patient":

    st.subheader("Register patient")

    with st.form("register_patient"):

        name = st.text_input("Patient name")
        patient_code = st.text_input("Patient ID")

        c1, c2 = st.columns(2)

        with c1:
            age = st.number_input(
                "Age",
                0, 120, 40,
            )

            sex = st.selectbox(
                "Sex",
                ["Not specified", "Male", "Female", "Other"],
            )

        with c2:
            phone = st.text_input("Phone")

            conditions = st.text_area(
                "Relevant conditions",
            )

        pin = st.text_input(
            "Create 6-digit patient PIN",
            type="password",
            max_chars=6,
        )

        confirm_pin = st.text_input(
            "Confirm PIN",
            type="password",
            max_chars=6,
        )

        submitted = st.form_submit_button(
            "Register patient",
            type="primary",
        )

    if submitted:

        if not name.strip() or not patient_code.strip():
            st.error("Patient name and ID are required.")
            st.stop()

        if not re.fullmatch(r"\d{6}", pin or ""):
            st.error("PIN must contain exactly 6 digits.")
            st.stop()

        if pin != confirm_pin:
            st.error("PINs do not match.")
            st.stop()

        token = secrets.token_urlsafe(32)

        try:

            patient_id = execute(
                """
                INSERT INTO patients (
                    doctor_id, name, patient_code, age,
                    sex, phone, conditions, qr_token,
                    pin_hash, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doctor_id,
                    name.strip(),
                    patient_code.strip(),
                    int(age),
                    sex,
                    phone.strip(),
                    conditions.strip(),
                    token,
                    hash_password(pin),
                    now(),
                ),
            )

            st.session_state.new_patient = {
                "id": patient_id,
                "name": name.strip(),
                "token": token,
            }

            st.success("Patient registered.")

        except sqlite3.IntegrityError:
            st.error(
                "Patient ID already exists for this doctor."
            )

    new_patient = st.session_state.new_patient

    if new_patient:

        st.divider()

        st.subheader("Patient QR code")

        st.write(f"Patient: **{new_patient['name']}**")

        app_url = st.text_input(
            "Your deployed app URL",
            value="",
            placeholder="https://your-app.streamlit.app",
        )

        if app_url.strip():

            patient_url = (
                app_url.rstrip("/")
                + "/?"
                + urlencode({"patient": new_patient["token"]})
            )

            qr_bytes = create_qr(patient_url)

            st.image(qr_bytes, width=260)

            st.download_button(
                "Download QR code",
                data=qr_bytes,
                file_name="vynx_patient_qr.png",
                mime="image/png",
            )

            st.code(patient_url)

        st.warning(
            "Keep the QR code private. "
            "It is linked to this patient's account."
        )


# =========================================================
# PATIENT RECORDS
# =========================================================

elif page == "Patient Records":

    st.subheader("Patient records")

    if not patients:
        st.info("No patients registered.")
        st.stop()

    options = {
        f"{p['name']} ({p['patient_code']})": p
        for p in patients
    }

    selected = st.selectbox(
        "Select patient",
        list(options.keys()),
    )

    patient = options[selected]

    readings = get_readings(patient["id"])

    if readings:

        df = pd.DataFrame(readings)

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )

        metric = st.selectbox(
            "Vital sign trend",
            [
                "systolic",
                "diastolic",
                "pulse",
                "spo2",
                "temperature",
                "weight",
                "glucose",
            ],
        )

        chart_df = df.dropna(
            subset=[metric]
        ).sort_values("recorded_at")

        if not chart_df.empty:

            fig = px.line(
                chart_df,
                x="recorded_at",
                y=metric,
                markers=True,
                title=f"{metric.title()} trend",
            )

            st.plotly_chart(
                fig,
                use_container_width=True,
            )

        st.download_button(
            "Download CSV report",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name=f"{patient['patient_code']}_report.csv",
            mime="text/csv",
        )

    else:
        st.info("No readings recorded.")


# =========================================================
# ALERTS
# =========================================================

elif page == "Alerts":

    st.subheader("Patient alerts")

    all_alerts = []

    for patient in patients:

        for alert in get_alerts(patient["id"]):

            alert["patient_name"] = patient["name"]
            alert["patient_code"] = patient["patient_code"]

            all_alerts.append(alert)

    if not all_alerts:
        st.success("No alerts recorded.")
        st.stop()

    df = pd.DataFrame(all_alerts)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )

    pending = [
        a for a in all_alerts
        if not a["reviewed"]
    ]

    if pending:

        options = {
            f"{a['patient_name']} | {a['alert_type']} | {a['created_at']}": a
            for a in pending
        }

        selected = st.selectbox(
            "Select alert to review",
            list(options.keys()),
        )

        alert = options[selected]

        if st.button(
            "Mark as reviewed",
            type="primary",
        ):

            # Confirm alert belongs to a patient
            # linked to the current doctor.
            owned_ids = {
                p["id"] for p in patients
            }

            if alert["patient_id"] not in owned_ids:
                st.error("Unauthorized.")
                st.stop()

            execute(
                "UPDATE alerts SET reviewed=1 WHERE id=?",
                (alert["id"],),
            )

            st.success("Alert reviewed.")
            st.rerun()

    else:
        st.info("All alerts have been reviewed.")


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "Vynx.life RPM v1.0 | SQLite prototype"
)

st.caption(
    "This software is not an emergency service and does "
    "not diagnose or provide treatment advice. "
    "Readings and alerts require clinical review."
)
