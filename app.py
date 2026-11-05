import streamlit as st
import sqlite3
import hashlib
import secrets
import io
import os
from datetime import datetime, date, timedelta

import pandas as pd
import plotly.express as px
import qrcode


# ============================================================
# APP CONFIG
# ============================================================

st.set_page_config(
    page_title="Vynx.life | Remote Patient Monitoring",
    page_icon="🩺",
    layout="wide",
)

DB_PATH = "vynx_life.db"

st.markdown(
    """
    <style>
    .main { background-color: #f7f9fc; }
    .block-container { padding-top: 1.5rem; }
    div[data-testid="stMetric"] {
        background: white;
        border: 1px solid #e8edf4;
        padding: 15px;
        border-radius: 12px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            age INTEGER,
            sex TEXT,
            phone TEXT,
            qr_token TEXT UNIQUE NOT NULL,
            pin_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (doctor_id) REFERENCES doctors(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS vitals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            systolic INTEGER,
            diastolic INTEGER,
            pulse INTEGER,
            spo2 REAL,
            temperature REAL,
            glucose REAL,
            notes TEXT,
            flagged INTEGER DEFAULT 0,
            flag_reason TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL,
            vital_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (doctor_id) REFERENCES doctors(id),
            FOREIGN KEY (patient_id) REFERENCES patients(id),
            FOREIGN KEY (vital_id) REFERENCES vitals(id)
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_secret(value):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        value.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return f"{salt}${digest}"


def verify_secret(value, stored_hash):
    try:
        salt, digest = stored_hash.split("$", 1)
        check = hashlib.pbkdf2_hmac(
            "sha256",
            value.encode("utf-8"),
            salt.encode("utf-8"),
            200_000,
        ).hex()
        return secrets.compare_digest(check, digest)
    except Exception:
        return False


def get_doctor(doctor_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM doctors WHERE id = ?",
        (doctor_id,),
    ).fetchone()
    conn.close()
    return row


def get_patient_by_token(token):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM patients WHERE qr_token = ?",
        (token,),
    ).fetchone()
    conn.close()
    return row


def get_patient(patient_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM patients WHERE id = ?",
        (patient_id,),
    ).fetchone()
    conn.close()
    return row


def get_doctor_patients(doctor_id):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM patients
        WHERE doctor_id = ?
        ORDER BY name
        """,
        (doctor_id,),
    ).fetchall()
    conn.close()
    return rows


def get_patient_vitals(patient_id):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM vitals
        WHERE patient_id = ?
        ORDER BY created_at DESC
        """,
        (patient_id,),
    ).fetchall()
    conn.close()
    return pd.DataFrame([dict(row) for row in rows])


def assess_reading(systolic, diastolic, pulse, spo2, temperature, glucose):
    """
    Simple configurable demonstration thresholds.
    These are not a diagnosis and should be reviewed by clinicians.
    """
    flags = []

    if systolic is not None and systolic >= 180:
        flags.append("Systolic BP ≥ 180")
    if diastolic is not None and diastolic >= 120:
        flags.append("Diastolic BP ≥ 120")
    if systolic is not None and systolic < 90:
        flags.append("Systolic BP < 90")

    if pulse is not None and (pulse < 50 or pulse > 120):
        flags.append("Pulse outside 50–120 bpm")

    if spo2 is not None and spo2 < 92:
        flags.append("SpO₂ < 92%")

    if temperature is not None and temperature >= 38.0:
        flags.append("Temperature ≥ 38°C")

    if glucose is not None and glucose >= 250:
        flags.append("Glucose ≥ 250 mg/dL")

    return bool(flags), "; ".join(flags)


def make_qr_image(url):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def add_notification(doctor_id, patient_id, vital_id, message):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO notifications
        (doctor_id, patient_id, vital_id, message, is_read, created_at)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (doctor_id, patient_id, vital_id, message, now_text()),
    )
    conn.commit()
    conn.close()


def get_notifications(doctor_id):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            n.*,
            p.name AS patient_name
        FROM notifications n
        JOIN patients p ON p.id = n.patient_id
        WHERE n.doctor_id = ?
        ORDER BY n.created_at DESC
        LIMIT 100
        """,
        (doctor_id,),
    ).fetchall()
    conn.close()
    return rows


def mark_notification_read(notification_id):
    conn = get_connection()
    conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = ?",
        (notification_id,),
    )
    conn.commit()
    conn.close()


# ============================================================
# SESSION STATE
# ============================================================

if "doctor_id" not in st.session_state:
    st.session_state.doctor_id = None

if "doctor_name" not in st.session_state:
    st.session_state.doctor_name = None


# ============================================================
# PATIENT PORTAL
# ============================================================

query_params = st.query_params
patient_token = query_params.get("patient", None)

if patient_token:
    patient = get_patient_by_token(patient_token)

    st.title("🩺 Vynx.life")
    st.subheader("Patient Vitals Portal")

    if not patient:
        st.error("This patient QR link is invalid or no longer available.")
        st.stop()

    st.write(f"Welcome, **{patient['name']}**")

    if "patient_authenticated" not in st.session_state:
        st.session_state.patient_authenticated = False

    if not st.session_state.patient_authenticated:
        st.info("Enter the PIN provided by your doctor.")

        pin = st.text_input("Patient PIN", type="password")

        if st.button("Unlock patient portal", type="primary"):
            if verify_secret(pin, patient["pin_hash"]):
                st.session_state.patient_authenticated = True
                st.rerun()
            else:
                st.error("Incorrect PIN.")

        st.stop()

    st.success("Patient portal unlocked")

    st.markdown("### Submit your latest reading")

    with st.form("patient_vitals_form"):
        col1, col2 = st.columns(2)

        with col1:
            systolic = st.number_input(
                "Systolic BP (mmHg)",
                min_value=0,
                max_value=300,
                value=0,
            )
            pulse = st.number_input(
                "Pulse (bpm)",
                min_value=0,
                max_value=250,
                value=0,
            )
            temperature = st.number_input(
                "Temperature (°C)",
                min_value=0.0,
                max_value=50.0,
                value=0.0,
                step=0.1,
            )

        with col2:
            diastolic = st.number_input(
                "Diastolic BP (mmHg)",
                min_value=0,
                max_value=200,
                value=0,
            )
            spo2 = st.number_input(
                "SpO₂ (%)",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                step=0.1,
            )
            glucose = st.number_input(
                "Blood glucose (mg/dL)",
                min_value=0.0,
                max_value=1000.0,
                value=0.0,
                step=1.0,
            )

        notes = st.text_area(
            "Notes (optional)",
            placeholder="Symptoms, medication taken, measurement context...",
        )

        submitted = st.form_submit_button(
            "Submit readings",
            type="primary",
        )

    if submitted:
        # Zero is treated as not entered for optional values.
        sys_value = int(systolic) if systolic > 0 else None
        dia_value = int(diastolic) if diastolic > 0 else None
        pulse_value = int(pulse) if pulse > 0 else None
        spo2_value = float(spo2) if spo2 > 0 else None
        temp_value = float(temperature) if temperature > 0 else None
        glucose_value = float(glucose) if glucose > 0 else None

        has_any_value = any(
            value is not None
            for value in [
                sys_value,
                dia_value,
                pulse_value,
                spo2_value,
                temp_value,
                glucose_value,
            ]
        )

        if not has_any_value:
            st.warning("Please enter at least one vital reading.")
        else:
            flagged, reason = assess_reading(
                sys_value,
                dia_value,
                pulse_value,
                spo2_value,
                temp_value,
                glucose_value,
            )

            conn = get_connection()
            cur = conn.cursor()

            cur.execute(
                """
                INSERT INTO vitals (
                    patient_id,
                    systolic,
                    diastolic,
                    pulse,
                    spo2,
                    temperature,
                    glucose,
                    notes,
                    flagged,
                    flag_reason,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    patient["id"],
                    sys_value,
                    dia_value,
                    pulse_value,
                    spo2_value,
                    temp_value,
                    glucose_value,
                    notes,
                    int(flagged),
                    reason,
                    now_text(),
                ),
            )

            vital_id = cur.lastrowid
            conn.commit()
            conn.close()

            message = (
                f"New vital reading submitted by {patient['name']}."
            )

            if flagged:
                message += f" Flagged: {reason}"

            add_notification(
                patient["doctor_id"],
                patient["id"],
                vital_id,
                message,
            )

            st.success("Your readings have been submitted to your doctor.")

            if flagged:
                st.warning(
                    "One or more readings met the app's review thresholds. "
                    "Your doctor can review them. If you feel seriously unwell, "
                    "seek urgent medical care rather than waiting for an app alert."
                )

    st.markdown("---")
    st.caption(
        "Vynx.life is a monitoring and communication prototype. "
        "It does not provide diagnosis or emergency monitoring."
    )

    st.stop()


# ============================================================
# DOCTOR LOGIN / REGISTRATION
# ============================================================

st.title("🩺 Vynx.life")
st.caption("Remote Patient Monitoring & Vitals Tracking")

if st.session_state.doctor_id is None:
    login_tab, register_tab = st.tabs(
        ["Doctor Login", "Create Doctor Account"]
    )

    with login_tab:
        st.subheader("Doctor login")

        email = st.text_input("Email address", key="login_email")
        password = st.text_input(
            "Password",
            type="password",
            key="login_password",
        )

        if st.button("Login", type="primary"):
            conn = get_connection()
            doctor = conn.execute(
                "SELECT * FROM doctors WHERE email = ?",
                (email.strip().lower(),),
            ).fetchone()
            conn.close()

            if doctor and verify_secret(password, doctor["password_hash"]):
                st.session_state.doctor_id = doctor["id"]
                st.session_state.doctor_name = doctor["name"]
                st.rerun()
            else:
                st.error("Invalid email or password.")

    with register_tab:
        st.subheader("Create a doctor account")

        name = st.text_input("Doctor name", key="register_name")
        email_new = st.text_input("Email address", key="register_email")
        password_new = st.text_input(
            "Create password",
            type="password",
            key="register_password",
        )

        if st.button("Create account"):
            if not name.strip() or not email_new.strip() or not password_new:
                st.warning("Please complete all fields.")
            elif len(password_new) < 8:
                st.warning("Use a password with at least 8 characters.")
            else:
                try:
                    conn = get_connection()
                    conn.execute(
                        """
                        INSERT INTO doctors
                        (name, email, password_hash, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            name.strip(),
                            email_new.strip().lower(),
                            hash_secret(password_new),
                            now_text(),
                        ),
                    )
                    conn.commit()
                    conn.close()

                    st.success(
                        "Doctor account created. You can now log in."
                    )
                except sqlite3.IntegrityError:
                    st.error("An account with that email already exists.")

    st.stop()


# ============================================================
# DOCTOR DASHBOARD
# ============================================================

doctor_id = st.session_state.doctor_id
doctor_name = st.session_state.doctor_name

with st.sidebar:
    st.markdown("## Vynx.life")
    st.write(f"Logged in: **Dr. {doctor_name}**")

    if st.button("Refresh dashboard"):
        st.rerun()

    if st.button("Logout"):
        st.session_state.doctor_id = None
        st.session_state.doctor_name = None
        st.rerun()

st.header("Doctor Dashboard")

patients = get_doctor_patients(doctor_id)
notifications = get_notifications(doctor_id)

unread_count = sum(1 for n in notifications if n["is_read"] == 0)

total_readings = 0
flagged_readings = 0

for patient_row in patients:
    patient_df = get_patient_vitals(patient_row["id"])
    if not patient_df.empty:
        total_readings += len(patient_df)
        flagged_readings += int(patient_df["flagged"].fillna(0).sum())

m1, m2, m3, m4 = st.columns(4)

m1.metric("Patients", len(patients))
m2.metric("Total readings", total_readings)
m3.metric("Flagged readings", flagged_readings)
m4.metric("Unread notifications", unread_count)

dashboard_tab, patients_tab, notifications_tab, add_patient_tab = st.tabs(
    [
        "Overview",
        "Patients & Charts",
        "Notifications",
        "Add Patient",
    ]
)


# ============================================================
# OVERVIEW TAB
# ============================================================

with dashboard_tab:
    st.subheader("Recent patient activity")

    activity_rows = []

    for patient_row in patients:
        patient_df = get_patient_vitals(patient_row["id"])

        if not patient_df.empty:
            latest = patient_df.iloc[0]

            activity_rows.append(
                {
                    "Patient": patient_row["name"],
                    "Latest reading": latest["created_at"],
                    "BP": (
                        f"{latest['systolic']}/{latest['diastolic']}"
                        if pd.notna(latest["systolic"])
                        and pd.notna(latest["diastolic"])
                        else "—"
                    ),
                    "Pulse": (
                        latest["pulse"]
                        if pd.notna(latest["pulse"])
                        else "—"
                    ),
                    "SpO₂": (
                        latest["spo2"]
                        if pd.notna(latest["spo2"])
                        else "—"
                    ),
                    "Flagged": (
                        "Review"
                        if int(latest["flagged"] or 0) == 1
                        else "No flag"
                    ),
                }
            )

    if activity_rows:
        st.dataframe(
            pd.DataFrame(activity_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No readings submitted yet.")

    st.markdown("### Readings requiring review")

    flagged_rows = []

    for patient_row in patients:
        patient_df = get_patient_vitals(patient_row["id"])

        if not patient_df.empty:
            flagged_df = patient_df[
                patient_df["flagged"].fillna(0).astype(int) == 1
            ]

            for _, row in flagged_df.iterrows():
                flagged_rows.append(
                    {
                        "Patient": patient_row["name"],
                        "Date/time": row["created_at"],
                        "Reason": row["flag_reason"],
                    }
                )

    if flagged_rows:
        st.dataframe(
            pd.DataFrame(flagged_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.success("No flagged readings currently recorded.")


# ============================================================
# PATIENTS & CHARTS TAB
# ============================================================

with patients_tab:
    st.subheader("Patient history and vital trends")

    if not patients:
        st.info("Add a patient to start tracking readings.")
    else:
        patient_options = {
            f"{p['name']} (Patient ID: {p['id']})": p["id"]
            for p in patients
        }

        selected_patient_label = st.selectbox(
            "Select patient",
            list(patient_options.keys()),
        )

        selected_patient_id = patient_options[selected_patient_label]
        selected_patient = get_patient(selected_patient_id)

        df = get_patient_vitals(selected_patient_id)

        st.markdown(
            f"### {selected_patient['name']}"
        )

        if df.empty:
            st.info("No readings available for this patient.")
        else:
            df["created_at"] = pd.to_datetime(
                df["created_at"],
                errors="coerce",
            )

            df = df.sort_values("created_at")

            chart_metric = st.selectbox(
                "Choose vital for chart",
                [
                    "Blood Pressure",
                    "Pulse",
                    "SpO₂",
                    "Temperature",
                    "Glucose",
                ],
            )

            if chart_metric == "Blood Pressure":
                chart_df = df[
                    df["systolic"].notna()
                    | df["diastolic"].notna()
                ]

                if not chart_df.empty:
                    fig = px.line(
                        chart_df,
                        x="created_at",
                        y=["systolic", "diastolic"],
                        markers=True,
                        title="Blood pressure trend",
                        labels={
                            "created_at": "Date/time",
                            "value": "mmHg",
                            "variable": "Measurement",
                        },
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No blood pressure readings recorded.")

            elif chart_metric == "Pulse":
                chart_df = df[df["pulse"].notna()]

                if not chart_df.empty:
                    fig = px.line(
                        chart_df,
                        x="created_at",
                        y="pulse",
                        markers=True,
                        title="Pulse trend",
                        labels={
                            "created_at": "Date/time",
                            "pulse": "bpm",
                        },
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No pulse readings recorded.")

            elif chart_metric == "SpO₂":
                chart_df = df[df["spo2"].notna()]

                if not chart_df.empty:
                    fig = px.line(
                        chart_df,
                        x="created_at",
                        y="spo2",
                        markers=True,
                        title="SpO₂ trend",
                        labels={
                            "created_at": "Date/time",
                            "spo2": "%",
                        },
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No SpO₂ readings recorded.")

            elif chart_metric == "Temperature":
                chart_df = df[df["temperature"].notna()]

                if not chart_df.empty:
                    fig = px.line(
                        chart_df,
                        x="created_at",
                        y="temperature",
                        markers=True,
                        title="Temperature trend",
                        labels={
                            "created_at": "Date/time",
                            "temperature": "°C",
                        },
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No temperature readings recorded.")

            elif chart_metric == "Glucose":
                chart_df = df[df["glucose"].notna()]

                if not chart_df.empty:
                    fig = px.line(
                        chart_df,
                        x="created_at",
                        y="glucose",
                        markers=True,
                        title="Blood glucose trend",
                        labels={
                            "created_at": "Date/time",
                            "glucose": "mg/dL",
                        },
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No glucose readings recorded.")

            st.markdown("### Reading history")

            display_df = df.copy()
            display_df["created_at"] = display_df["created_at"].dt.strftime(
                "%Y-%m-%d %H:%M"
            )

            st.dataframe(
                display_df[
                    [
                        "created_at",
                        "systolic",
                        "diastolic",
                        "pulse",
                        "spo2",
                        "temperature",
                        "glucose",
                        "notes",
                        "flagged",
                        "flag_reason",
                    ]
                ].sort_values("created_at", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

            csv = display_df.to_csv(index=False).encode("utf-8")

            st.download_button(
                "Download patient history as CSV",
                data=csv,
                file_name=f"vynx_patient_{selected_patient_id}_history.csv",
                mime="text/csv",
            )


# ============================================================
# NOTIFICATIONS TAB
# ============================================================

with notifications_tab:
    st.subheader("Doctor notifications")

    if not notifications:
        st.info("No notifications yet.")
    else:
        for notification in notifications:
            with st.container(border=True):
                col1, col2 = st.columns([5, 1])

                with col1:
                    if notification["is_read"] == 0:
                        st.markdown("🔵 **New notification**")
                    else:
                        st.markdown("Notification")

                    st.write(notification["message"])
                    st.caption(notification["created_at"])

                with col2:
                    if notification["is_read"] == 0:
                        if st.button(
                            "Mark read",
                            key=f"read_{notification['id']}",
                        ):
                            mark_notification_read(notification["id"])
                            st.rerun()


# ============================================================
# ADD PATIENT TAB
# ============================================================

with add_patient_tab:
    st.subheader("Register a patient")

    with st.form("add_patient_form"):
        patient_name = st.text_input("Patient full name")
        patient_age = st.number_input(
            "Age",
            min_value=0,
            max_value=120,
            value=40,
        )
        patient_sex = st.selectbox(
            "Sex",
            ["Prefer not to say", "Female", "Male", "Other"],
        )
        patient_phone = st.text_input("Phone number (optional)")
        patient_pin = st.text_input(
            "Create patient PIN",
            type="password",
            help="Share this PIN securely with the patient.",
        )

        create_patient = st.form_submit_button(
            "Create patient and QR code",
            type="primary",
        )

    if create_patient:
        if not patient_name.strip():
            st.warning("Enter the patient's name.")
        elif len(patient_pin) < 4:
            st.warning("Patient PIN must be at least 4 characters.")
        else:
            token = secrets.token_urlsafe(24)

            conn = get_connection()
            conn.execute(
                """
                INSERT INTO patients (
                    doctor_id,
                    name,
                    age,
                    sex,
                    phone,
                    qr_token,
                    pin_hash,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doctor_id,
                    patient_name.strip(),
                    int(patient_age),
                    patient_sex,
                    patient_phone.strip(),
                    token,
                    hash_secret(patient_pin),
                    now_text(),
                ),
            )
            conn.commit()
            conn.close()

            st.success("Patient registered successfully.")
            st.info(
                "Set the deployed app URL below to create the patient's QR code."
            )

            st.session_state["new_patient_token"] = token
            st.session_state["new_patient_name"] = patient_name.strip()

    if "new_patient_token" in st.session_state:
        app_url = st.text_input(
            "Your deployed Streamlit app URL",
            placeholder="https://your-app-name.streamlit.app",
        )

        if app_url.strip():
            patient_url = (
                app_url.strip().rstrip("/")
                + "?patient="
                + st.session_state["new_patient_token"]
            )

            st.code(patient_url)

            qr_image = make_qr_image(patient_url)

            st.image(
                qr_image,
                caption=f"Patient QR code — {st.session_state['new_patient_name']}",
                width=250,
            )

            buffer = io.BytesIO()
            qr_image.save(buffer, format="PNG")

            st.download_button(
                "Download patient QR code",
                data=buffer.getvalue(),
                file_name="vynx_patient_qr.png",
                mime="image/png",
            )


# ============================================================
# FOOTER
# ============================================================

st.markdown("---")
st.caption(
    "Vynx.life prototype | Patient readings are for clinician review. "
    "This application is not an emergency service."
)
