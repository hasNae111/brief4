import os, asyncio, joblib
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

async def db_query(query, params=(), fetch=False, fetchone=False, commit=False):
    def _run():
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(query, params)
        res = None
        if fetch:
            res = cur.fetchall()
        elif fetchone:
            res = cur.fetchone()
        if commit:
            conn.commit()
        cur.close()
        conn.close()
        return res
    return await asyncio.to_thread(_run)

app = FastAPI()

from fastapi.responses import RedirectResponse

@app.get("/")
async def root():
    return RedirectResponse("/login")
templates = Jinja2Templates(directory="templates")
model = None

@app.on_event("startup")
async def load_model():
    global model
    # charge le modèle (assure-toi que ml/model.pkl existe)
    model = joblib.load("ml/model.pkl")

# ---------- Auth routes ----------
@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
async def register(request: Request,
                   username: str = Form(...),
                   email: str = Form(...),
                   password: str = Form(...),
                   confirm_password: str = Form(...)):
    if password != confirm_password:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Mots de passe différents"})
    # check existence
    exists = await db_query("SELECT id FROM medecins WHERE username=%s OR email=%s", (username, email), fetchone=True)
    if exists:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Nom d'utilisateur ou email déjà utilisé"})
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await db_query("INSERT INTO medecins (username,email,password) VALUES (%s,%s,%s)",
                   (username, email, hashed), commit=True)
    return RedirectResponse(url="/login", status_code=303)

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = await db_query("SELECT id, password, username FROM medecins WHERE username=%s", (username,), fetchone=True)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Identifiants invalides"})
    if bcrypt.checkpw(password.encode(), user['password'].encode()):
        resp = RedirectResponse(url="/home", status_code=303)
        resp.set_cookie(key="doctor_id", value=str(user['id']), httponly=True, max_age=3600)
        return resp
    return templates.TemplateResponse("login.html", {"request": request, "error": "Identifiants invalides"})

@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("doctor_id")
    return resp

# ---------- Home ----------
@app.get("/home")
async def home(request: Request):
    doc_id = request.cookies.get("doctor_id")
    if not doc_id:
        return RedirectResponse(url="/login")
    doc = await db_query("SELECT username FROM medecins WHERE id=%s", (int(doc_id),), fetchone=True)
    return templates.TemplateResponse("home.html", {"request": request, "username": doc['username'] if doc else None})

# ---------- Patients ----------
@app.get("/add")
async def add_patient_form(request: Request):
    if not request.cookies.get("doctor_id"):
        return RedirectResponse("/login")
    return templates.TemplateResponse("add_patient.html", {"request": request})

@app.post("/submet")
async def submit_patient(request: Request,
                         name: str = Form(...),
                         age: int = Form(...),
                         sex: str = Form(...),
                         glucose: float = Form(...),
                         bmi: float = Form(...),
                         bloodpressure: float = Form(...),
                         pedigree: float = Form(...)):
    doc_id = request.cookies.get("doctor_id")
    if not doc_id:
        return RedirectResponse("/login")
    # Préparer entrée modèle (ordre attendu: glucose, bmi, bloodpressure, pedigree, age)
    features = [[float(glucose), float(bmi), float(bloodpressure), float(pedigree)]]
    pred = int(model.predict(features)[0])
    # Insérer patient et prédiction
    created = await db_query("""
        INSERT INTO patients (doctorid, name, age, sex, glucose, bmi, bloodpressure, pedigree, result)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (int(doc_id), name, age, sex, glucose, bmi, bloodpressure, pedigree, pred), fetchone=True, commit=True)
    patient_id = created['id']
    await db_query("INSERT INTO predictions (patientid, result) VALUES (%s,%s)", (patient_id, pred), commit=True)
    return templates.TemplateResponse("add_patient.html", {"request": request, "prediction": pred})

@app.get("/patients")
async def patients_page(request: Request):
    doc_id = request.cookies.get("doctor_id")
    if not doc_id:
        return RedirectResponse("/login")
    patients = await db_query("SELECT * FROM patients WHERE doctorid=%s ORDER BY created_at DESC", (int(doc_id),), fetch=True)
    total = len(patients)
    diabetics = sum(1 for p in patients if p['result'] == 1)
    percent = round((diabetics / total * 100), 2) if total > 0 else 0
    return templates.TemplateResponse("patients.html", {"request": request, "patients": patients, "percent": percent})

@app.get("/delete/{pid}")
async def delete_patient(pid: int, request: Request):
    doc_id = request.cookies.get("doctor_id")
    if not doc_id:
        return RedirectResponse("/login")
    owner = await db_query("SELECT doctorid FROM patients WHERE id=%s", (pid,), fetchone=True)
    if not owner or owner['doctorid'] != int(doc_id):
        return RedirectResponse("/patients")
    # supprimer prédictions puis patient
    await db_query("DELETE FROM predictions WHERE patientid=%s", (pid,), commit=True)
    await db_query("DELETE FROM patients WHERE id=%s", (pid,), commit=True)
    return RedirectResponse("/patients", status_code=303)
