from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
import shutil
import os
import uuid
import zipfile
from pathlib import Path
import javalang
from difflib import SequenceMatcher
import itertools
import re

from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session

app = FastAPI(title="Analitzador de Projectes Multillenguatge")

# Activar CORS per la web
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Carpeta d'arxius temporals
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- BASE DE DADES ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./analitzador.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class AnalisiTasques(Base):
    __tablename__ = "tasques_analisi"
    id = Column(String, primary_key=True, index=True)
    nom_arxiu = Column(String)
    estat = Column(String, default="en proces") 

class ResultatPlagi(Base):
    __tablename__ = "resultats_plagi"
    id = Column(Integer, primary_key=True, index=True)
    tasca_id = Column(String, ForeignKey("tasques_analisi.id"))
    alumne_a = Column(String)
    alumne_b = Column(String)
    similitud = Column(Float)

class AlertaIA(Base):
    __tablename__ = "alertes_ia"
    id = Column(Integer, primary_key=True, index=True)
    tasca_id = Column(String, ForeignKey("tasques_analisi.id"))
    alumne = Column(String)
    motiu = Column(String)

class CodiAlumne(Base):
    __tablename__ = "codi_alumnes"
    id = Column(Integer, primary_key=True, index=True)
    tasca_id = Column(String, ForeignKey("tasques_analisi.id"))
    alumne = Column(String)
    codi = Column(String) 

Base.metadata.create_all(bind=engine)

# --- FUNCIONS AUXILIARS ---
def extreure_zips_recursivament(directori_base: str):
    arxius_zip_pendents = True
    while arxius_zip_pendents:
        arxius_zip_pendents = False
        for ruta_zip in Path(directori_base).rglob('*.zip'):
            try:
                carpeta_desti = ruta_zip.parent 
                with zipfile.ZipFile(ruta_zip, 'r') as zip_ref:
                    zip_ref.extractall(carpeta_desti)
                os.remove(ruta_zip)
                arxius_zip_pendents = True 
            except Exception:
                pass

def extreure_estructura(codi: str, llenguatge: str) -> str:
    """Tokenitzador que s'adapta al llenguatge triat a la web."""
    if llenguatge == "Java":
        try:
            tokens = list(javalang.tokenizer.tokenize(codi))
            estructura = [t.value for t in tokens if isinstance(t, (javalang.tokenizer.Keyword, javalang.tokenizer.Operator, javalang.tokenizer.Separator))]
            return "".join(estructura)
        except Exception:
            pass 
            
    # PLA B (Universal per PHP i HTML/CSS): Només text estructural
    text_net = re.sub(r'".*?"|\'.*?\'', '""', codi) 
    tokens = re.findall(r'[a-zA-Z_]+|[{}();=+\-*/<>]', text_net)
    return "".join(tokens)

def calcular_similitud(codi1: str, codi2: str, llenguatge: str) -> float:
    est1 = extreure_estructura(codi1, llenguatge)
    est2 = extreure_estructura(codi2, llenguatge)
    if not est1 or not est2: 
        return 0.0
    return SequenceMatcher(None, est1, est2).ratio()

def analitzar_indicis_ia(codi: str, llenguatge: str) -> list:
    avisos = []
    # 1. Indicis generals de XatBot per a qualsevol idioma
    patrons_conversa = [r"here is the", r"certainly!?", r"aquí tienes", r"```(java|php|html|css)"]
    for patro in patrons_conversa:
        if re.search(patro, codi, re.IGNORECASE):
            avisos.append("S'ha trobat llenguatge conversacional de xat bot.")
            break 

    # 2. Indicis específics per llenguatge
    if llenguatge == "Java":
        if "java.util.stream" in codi or "CompletableFuture" in codi:
            avisos.append("Ús de llibreries Java molt avançades (Streams/Threads).")
    elif llenguatge == "PHP":
        if "ReflectionClass" in codi or "PDO" in codi or "namespace" in codi:
            avisos.append("Ús d'estructures PHP avançades (Objectes/PDO/Namespaces).")
    elif llenguatge == "HTML/CSS":
        if "display: grid" in codi or "<section>" in codi:
            avisos.append("Ús d'etiquetes semàntiques perfectes o CSS Grid avançat.")

    return avisos

# --- FUNCIO PRINCIPAL (SEGON PLA) ---
def processar_arxiu_zip(file_path: str, task_id: str, llenguatge: str):
    print(f"[Tasca {task_id}] Iniciant anàlisi de {llenguatge}")
    extract_dir = os.path.join(UPLOAD_DIR, f"extracted_{task_id}")
    os.makedirs(extract_dir, exist_ok=True)
    db = SessionLocal() 
    
    try:
        # 1. Descomprimir
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        extreure_zips_recursivament(extract_dir)
        
        # 2. Buscar arxius segons l'idioma seleccionat
        alumnes_codi = {} 
        ruta_base = Path(extract_dir)
        
        extensions = []
        if llenguatge == "Java": extensions = ['*.java']
        elif llenguatge == "PHP": extensions = ['*.php']
        elif llenguatge == "HTML/CSS": extensions = ['*.html', '*.css']
            
        for ext in extensions:
            for arxiu in ruta_base.rglob(ext): 
                if not arxiu.is_file():
                    continue  # Si és una carpeta, la ignorem i passem al següent
                parts_ruta = arxiu.relative_to(ruta_base).parts
                if len(parts_ruta) > 0:
                    nom_alumne = parts_ruta[0]
                    if nom_alumne not in alumnes_codi:
                        alumnes_codi[nom_alumne] = []
                    try:
                        with open(arxiu, 'r', encoding='utf-8') as f:
                            alumnes_codi[nom_alumne].append(f.read())
                    except UnicodeDecodeError:
                        with open(arxiu, 'r', encoding='latin-1') as f:
                            alumnes_codi[nom_alumne].append(f.read())

        codi_per_alumne = {}
        
        # 3. Guardar codi i buscar IA
        for alumne, arxius in alumnes_codi.items():
            codi_complet = "\n".join(arxius)
            codi_per_alumne[alumne] = codi_complet
            
            nou_codi = CodiAlumne(tasca_id=task_id, alumne=alumne, codi=codi_complet)
            db.add(nou_codi)

            sospites_ia = analitzar_indicis_ia(codi_complet, llenguatge)
            for motiu in sospites_ia:
                nova_alerta_ia = AlertaIA(tasca_id=task_id, alumne=alumne, motiu=motiu)
                db.add(nova_alerta_ia)

        # 4. Calcular Plagi
        parelles = itertools.combinations(codi_per_alumne.keys(), 2)
        for alumne1, alumne2 in parelles:
            similitud = calcular_similitud(codi_per_alumne[alumne1], codi_per_alumne[alumne2], llenguatge)
            percentatge = round(similitud * 100, 2)
            if percentatge > 75.0:
                nou_plagi = ResultatPlagi(tasca_id=task_id, alumne_a=alumne1, alumne_b=alumne2, similitud=percentatge)
                db.add(nou_plagi)

        # 5. Finalitzar tasca
        tasca = db.query(AnalisiTasques).filter(AnalisiTasques.id == task_id).first()
        if tasca: tasca.estat = "completat"
        db.commit() 
        print(f"[Tasca {task_id}] Anàlisi complet de {llenguatge}.")
        
    except Exception as e:
        print(f"[Tasca {task_id}] Error: {e}")
        db.rollback()
        tasca_error = db.query(AnalisiTasques).filter(AnalisiTasques.id == task_id).first()
        if tasca_error:
            tasca_error.estat = "error"
            db.commit()
    finally:
        db.close()
        if os.path.exists(extract_dir): shutil.rmtree(extract_dir)
        if os.path.exists(file_path): os.remove(file_path)

# --- ENDPOINTS (RUTES API) ---
@app.post("/upload-zip/")
async def upload_zip(
    background_tasks: BackgroundTasks, 
    file: UploadFile = File(...),
    llenguatge: str = Form(...) # Rebem l'idioma del Frontend
):
    if not file.filename.endswith('.zip'):
        raise HTTPException(status_code=400, detail="Ha de ser un .zip")

    task_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{task_id}_{file.filename}")

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
    finally:
        file.file.close()

    db = SessionLocal()
    nova_tasca = AnalisiTasques(id=task_id, nom_arxiu=file.filename, estat="en proces")
    db.add(nova_tasca)
    db.commit()
    db.close()

    # Enviem l'arxiu i l'idioma a processar
    background_tasks.add_task(processar_arxiu_zip, file_path, task_id, llenguatge)
    return {"missatge": "Rebut.", "task_id": task_id}

@app.get("/resultats/{task_id}")
def obtenir_resultats(task_id: str):
    db = SessionLocal()
    try:
        tasca = db.query(AnalisiTasques).filter(AnalisiTasques.id == task_id).first()
        if not tasca: raise HTTPException(status_code=404, detail="Tasca no trobada.")
        if tasca.estat == "en proces" or tasca.estat == "error":
            return {"task_id": tasca.id, "estat": tasca.estat}
            
        resultats_bd = db.query(ResultatPlagi).filter(ResultatPlagi.tasca_id == task_id).all()
        llista_plagi = [{"alumne_A": r.alumne_a, "alumne_B": r.alumne_b, "similitud": r.similitud} for r in resultats_bd]
        llista_plagi.sort(key=lambda x: x["similitud"], reverse=True)
        
        alertes_ia_bd = db.query(AlertaIA).filter(AlertaIA.tasca_id == task_id).all()
        llista_ia = [{"alumne": a.alumne, "motiu": a.motiu} for a in alertes_ia_bd]
            
        return {"task_id": tasca.id, "estat": tasca.estat, "arxiu": tasca.nom_arxiu, "alertes_plagi": llista_plagi, "alertes_ia": llista_ia}
    finally:
        db.close()

@app.get("/comparar")
def obtenir_codi_comparacio(task_id: str, alumne_a: str, alumne_b: str):
    db = SessionLocal()
    try:
        tots_els_codis = db.query(CodiAlumne).filter(CodiAlumne.tasca_id == task_id).all()
        if len(tots_els_codis) == 0: raise HTTPException(status_code=404, detail="DB buida.")

        codi_a = next((c for c in tots_els_codis if c.alumne.strip() == alumne_a.strip()), None)
        codi_b = next((c for c in tots_els_codis if c.alumne.strip() == alumne_b.strip()), None)

        if not codi_a or not codi_b: raise HTTPException(status_code=404, detail="Noms no coincideixen.")

        return {"alumne_A": codi_a.alumne, "codi_A": codi_a.codi, "alumne_B": codi_b.alumne, "codi_B": codi_b.codi}
    finally:
        db.close()