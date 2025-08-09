from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from database import get_connection

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/")
def root():
    return {"message": "Bienvenue sur DiabetoWeb!"}

@app.get("/testdb")
def test_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM medecins;")
    rows = cur.fetchall()
    conn.close()
    return {"medecins": rows}
