from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
import shutil
import os
import uuid
import zipfile
import re
from pathlib import Path
import javalang
from difflib import SequenceMatcher
import itertools
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session

app = FastAPI(title="Analitzador de Projectes Java")
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Permet que qualsevol web es connecti (ideal per proves locals)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURACIÓ DE LA BASE DE DADES ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./analitzador.db"

# connect_args={"check_same_thread": False} és necessari només per a SQLite
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- DEFINICIÓ DE LES TAULES ---
class AnalisiTasques(Base):
    __tablename__ = "tasques_analisi"
    
    id = Column(String, primary_key=True, index=True) # Aquest serà el nostre task_id
    nom_arxiu = Column(String)
    estat = Column(String, default="en proces") # "en proces", "completat", "error"

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
    motiu = Column(String) # Descripció del per què és sospitós

class CodiAlumne(Base):
    __tablename__ = "codi_alumnes"
    
    id = Column(Integer, primary_key=True, index=True)
    tasca_id = Column(String, ForeignKey("tasques_analisi.id"))
    alumne = Column(String)
    codi = Column(String) # Aquesta columna guardarà tot el text sencer

# Creem les taules físicament a l'arxiu SQLite
Base.metadata.create_all(bind=engine)

# Carpeta on guardarem els ZIPs temporalment
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def extreure_estructura_java(codi_java: str) -> str:
    """
    Converteix el codi Java en una seqüència de tokens estructurals,
    ignorant els noms de variables, cadenes de text i números.
    """
    try:
        tokens = list(javalang.tokenizer.tokenize(codi_java))
        estructura = []
        for token in tokens:
            # Ens quedem només amb paraules clau (if, for, class...), operadors (+, -, =) i separadors ({, }, ;)
            if isinstance(token, (javalang.tokenizer.Keyword, javalang.tokenizer.Operator, javalang.tokenizer.Separator)):
                estructura.append(token.value)
        return "".join(estructura)
    except Exception:
        # Si el codi de l'alumne té errors de sintaxi greus que impedeixen llegir-lo
        return ""

def extreure_zips_recursivament(directori_base: str):
    """
    Busca arxius .zip dins de les subcarpetes, els descomprimeix a la mateixa 
    carpeta on es troben, i esborra el .zip original per evitar bucles.
    """
    arxius_zip_pendents = True
    
    while arxius_zip_pendents:
        arxius_zip_pendents = False
        
        # Busquem qualsevol .zip que hi hagi dins de qualsevol subcarpeta
        for ruta_zip in Path(directori_base).rglob('*.zip'):
            try:
                carpeta_desti = ruta_zip.parent # Ho extraiem a la seva pròpia carpeta
                with zipfile.ZipFile(ruta_zip, 'r') as zip_ref:
                    zip_ref.extractall(carpeta_desti)
                
                # Un cop descomprimit, eliminem l'arxiu .zip intern
                os.remove(ruta_zip)
                
                # Com que hem extret coses noves, potser a dins hi havia UN ALTRE zip
                arxius_zip_pendents = True 
                
            except Exception as e:
                print(f"⚠️ Error al descomprimir l'arxiu intern {ruta_zip.name}: {e}")

def calcular_similitud(codi1: str, codi2: str) -> float:
    """Retorna un percentatge de similitud (0.0 a 1.0) entre dos codis."""
    est1 = extreure_estructura_java(codi1)
    est2 = extreure_estructura_java(codi2)
    
    # Si algun dels codis està buit o no s'ha pogut parsejar, no els podem comparar
    if not est1 or not est2:
        return 0.0
        
    # SequenceMatcher compara seqüències i ens dona el ratio de similitud
    return SequenceMatcher(None, est1, est2).ratio()

def analitzar_indicis_ia(codi_java: str) -> list:
    """
    Analitza el codi d'un alumne buscant patrons sospitosos que indiquin
    que ha fet copiar-enganxar directament d'una IA.
    """
    avisos = []
    
    # 1. Comentaris residuals de conversa
    patrons_conversa = [
        r"here is the (code|solution)",
        r"certainly!?",
        r"as an ai (language)? model",
        r"aquí tienes (el código|la solución)",
        r"por supuesto",
        r"claro, aquí",
        r"```java",  
        r"espero que (te|esto) sirva"
    ]
    
    for patro in patrons_conversa:
        if re.search(patro, codi_java, re.IGNORECASE):
            avisos.append("S'ha trobat llenguatge conversacional de xat bot als comentaris.")
            break 

    # 2. Ús de codi massa avançat pel nivell
    llibreries_avancades = [
        "java.util.stream",
        "java.lang.reflect",
        "java.util.concurrent",
        "CompletableFuture",
        "ExecutorService"
    ]
    
    for lib in llibreries_avancades:
        if lib in codi_java:
            avisos.append(f"Ús de llibreries o estructures avançades inusuals: {lib}")
            
    # 3. Excés de Javadoc
    linies = codi_java.split('\n')
    total_linies = len(linies)
    javadocs = codi_java.count("@param") + codi_java.count("@return")
    
    if total_linies > 0 and (javadocs / total_linies) > 0.05:
        avisos.append("Densitat inusualment alta de documentació Javadoc.")

    return avisos

def processar_arxiu_zip(file_path: str, task_id: str):
    print(f"[Tasca {task_id}] Iniciant el processament de: {file_path}")
    
    extract_dir = os.path.join(UPLOAD_DIR, f"extracted_{task_id}")
    os.makedirs(extract_dir, exist_ok=True)
    
    db = SessionLocal() # Obrim la base de dades al principi
    
    try:
        # 1. Descomprimir
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
            
        extreure_zips_recursivament(extract_dir)
        
        # 2. Llegir arxius
        alumnes_codi = {} 
        ruta_base = Path(extract_dir)
        for arxiu_java in ruta_base.rglob('*.java'): 
            parts_ruta = arxiu_java.relative_to(ruta_base).parts
            if len(parts_ruta) > 0:
                nom_alumne = parts_ruta[0]
                if nom_alumne not in alumnes_codi:
                    alumnes_codi[nom_alumne] = []
                try:
                    with open(arxiu_java, 'r', encoding='utf-8') as f:
                        alumnes_codi[nom_alumne].append(f.read())
                except UnicodeDecodeError:
                    with open(arxiu_java, 'r', encoding='latin-1') as f:
                        alumnes_codi[nom_alumne].append(f.read())

        codi_per_alumne = {}
        
        # 3. Anàlisi IA i GUARDAR CODI A LA BASE DE DADES
        for alumne, arxius in alumnes_codi.items():
            codi_complet = "\n".join(arxius)
            codi_per_alumne[alumne] = codi_complet
            
            # ---> AQUÍ ESTÀ LA MÀGIA QUE FALTAVA <---
            nou_codi = CodiAlumne(tasca_id=task_id, alumne=alumne, codi=codi_complet)
            db.add(nou_codi)
            # ----------------------------------------

            sospites_ia = analitzar_indicis_ia(codi_complet)
            for motiu in sospites_ia:
                nova_alerta_ia = AlertaIA(tasca_id=task_id, alumne=alumne, motiu=motiu)
                db.add(nova_alerta_ia)

        # 4. Anàlisi de Plagi
        parelles = itertools.combinations(codi_per_alumne.keys(), 2)
        for alumne1, alumne2 in parelles:
            similitud = calcular_similitud(codi_per_alumne[alumne1], codi_per_alumne[alumne2])
            percentatge = round(similitud * 100, 2)
            if percentatge > 75.0:
                nou_plagi = ResultatPlagi(
                    tasca_id=task_id, alumne_a=alumne1, alumne_b=alumne2, similitud=percentatge
                )
                db.add(nou_plagi)

        # 5. Finalitzar tasca
        tasca = db.query(AnalisiTasques).filter(AnalisiTasques.id == task_id).first()
        if tasca:
            tasca.estat = "completat"
            
        db.commit() # Guardem absolutament TOT a la base de dades
        print(f"[Tasca {task_id}] Anàlisi complet i codis guardats a la DB.")
        
    except Exception as e:
        print(f"[Tasca {task_id}] Error durant l'anàlisi: {e}")
        db.rollback()
        tasca_error = db.query(AnalisiTasques).filter(AnalisiTasques.id == task_id).first()
        if tasca_error:
            tasca_error.estat = "error"
            db.commit()
    finally:
        db.close()
        # Neteja final
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        if os.path.exists(file_path):
            os.remove(file_path)


@app.post("/upload-zip/")
# Guardem la tasca a la base de dades com a "en procés"

async def upload_zip(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    # 1. Validació: Comprovar que és un arxiu .zip
    if not file.filename.endswith('.zip'):
        raise HTTPException(status_code=400, detail="L'arxiu ha de ser un .zip")

    # 2. Generar un ID únic per aquesta tasca/pujada
    task_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{task_id}_{file.filename}")

    # 3. Guardar l'arxiu al disc dur del servidor
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al guardar l'arxiu: {str(e)}")
    finally:
        file.file.close()
    
    db = SessionLocal()
    nova_tasca = AnalisiTasques(id=task_id, nom_arxiu=file.filename, estat="en proces")
    db.add(nova_tasca)
    db.commit()
    db.close()

    # 4. Enviar la tasca d'anàlisi a segon pla
    background_tasks.add_task(processar_arxiu_zip, file_path, task_id)

    # 5. Retornar una resposta ràpida al Frontend
    return {
        "missatge": "Arxiu rebut correctament. L'anàlisi ha començat.",
        "task_id": task_id,
        "arxiu": file.filename
    }

# Endpoint de prova per comprovar que l'API funciona
@app.get("/")
def read_root():
    return {"Estat": "L'API està funcionant correctament"}

@app.get("/resultats/{task_id}")
def obtenir_resultats(task_id: str):
    db = SessionLocal()
    try:
        tasca = db.query(AnalisiTasques).filter(AnalisiTasques.id == task_id).first()
        if not tasca:
            raise HTTPException(status_code=404, detail="Tasca no trobada.")
        
        if tasca.estat == "en proces":
            return {
                "task_id": tasca.id,
                "estat": tasca.estat,
                "missatge": "L'anàlisi encara s'està executant."
            }
            
        # 1. Recuperem els resultats de plagi
        resultats_bd = db.query(ResultatPlagi).filter(ResultatPlagi.tasca_id == task_id).all()
        llista_plagi = []
        for r in resultats_bd:
            llista_plagi.append({
                "alumne_A": r.alumne_a,
                "alumne_B": r.alumne_b,
                "similitud": r.similitud
            })
        llista_plagi.sort(key=lambda x: x["similitud"], reverse=True)
        
        # 2. NOU: Recuperem les alertes d'IA
        alertes_ia_bd = db.query(AlertaIA).filter(AlertaIA.tasca_id == task_id).all()
        llista_ia = []
        for a in alertes_ia_bd:
            llista_ia.append({
                "alumne": a.alumne,
                "motiu": a.motiu
            })
            
        return {
            "task_id": tasca.id,
            "estat": tasca.estat,
            "arxiu": tasca.nom_arxiu,
            "alertes_plagi": llista_plagi,
            "alertes_ia": llista_ia  # Afegim la nova dada a la resposta
        }
    finally:
        db.close()

@app.get("/comparar")
def obtenir_codi_comparacio(task_id: str, alumne_a: str, alumne_b: str):
    print("\n--- NOVA PETICIÓ DE COMPARACIÓ ---")
    db = SessionLocal()
    try:
        # 1. Obtenim TOTS els codis que s'han guardat per aquesta tasca
        tots_els_codis = db.query(CodiAlumne).filter(CodiAlumne.tasca_id == task_id).all()
        noms_guardats = [c.alumne for c in tots_els_codis]
        
        print(f"Buscant Alumne A: '{alumne_a}'")
        print(f"Buscant Alumne B: '{alumne_b}'")
        print(f"Noms que hi ha realment guardats a la DB: {noms_guardats}")
        
        # Si la llista està buida, vol dir que no s'ha guardat cap codi!
        if len(noms_guardats) == 0:
            raise HTTPException(status_code=404, detail="La base de dades està buida per a aquesta tasca. Mira el terminal.")

        # 2. Cerca intel·ligent: ignorem espais al principi o al final usant .strip()
        codi_a = next((c for c in tots_els_codis if c.alumne.strip() == alumne_a.strip()), None)
        codi_b = next((c for c in tots_els_codis if c.alumne.strip() == alumne_b.strip()), None)

        if not codi_a or not codi_b:
            raise HTTPException(status_code=404, detail="Els noms no coincideixen. Mira el terminal.")

        print("Codi trobat amb èxit! Enviant a la web...")
        return {
            "alumne_A": codi_a.alumne,
            "codi_A": codi_a.codi,
            "alumne_B": codi_b.alumne,
            "codi_B": codi_b.codi
        }
    finally:
        db.close()