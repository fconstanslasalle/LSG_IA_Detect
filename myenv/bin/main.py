from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
import shutil
import os
import uuid
import zipfile
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


def processar_arxiu_zip(file_path: str, task_id: str):
    print(f"[Tasca {task_id}] Iniciant el processament de: {file_path}")
    
    # Creem una carpeta temporal única per a aquesta extracció
    extract_dir = os.path.join(UPLOAD_DIR, f"extracted_{task_id}")
    os.makedirs(extract_dir, exist_ok=True)
    
    # 1. Descomprimir el ZIP
    try:
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        print(f"[Tasca {task_id}] Arxiu descomprimit correctament.")
        # NOU PAS: Descomprimim qualsevol sub-zip que els alumnes hagin posat a dins
        extreure_zips_recursivament(extract_dir)
    except zipfile.BadZipFile:
        print(f"[Tasca {task_id}] Error: L'arxiu no és un ZIP vàlid.")
        return # Parem aquí si falla

    # 2. Llegir els arxius .java i agrupar-los per alumne
    # Guardarem les dades així: { "Carpeta_Alumne_1": ["codi_arxiu1", "codi_arxiu2"] }
    alumnes_codi = {} 
    
    ruta_base = Path(extract_dir)
    
    # rglob('*.java') busca tots els arxius Java a qualsevol subcarpeta recursivament
    for arxiu_java in ruta_base.rglob('*.java'): 
        # Extraiem el nom de la carpeta principal (que assumim és el nom de l'alumne)
        parts_ruta = arxiu_java.relative_to(ruta_base).parts
        if len(parts_ruta) > 0:
            nom_alumne = parts_ruta[0]
            
            if nom_alumne not in alumnes_codi:
                alumnes_codi[nom_alumne] = []
                
            # Llegim el codi font
            try:
                with open(arxiu_java, 'r', encoding='utf-8') as f:
                    contingut = f.read()
                    alumnes_codi[nom_alumne].append(contingut)
            except UnicodeDecodeError:
                # Pla B per si algun alumne guarda en un altre format (com Windows-1252)
                with open(arxiu_java, 'r', encoding='latin-1') as f:
                    contingut = f.read()
                    alumnes_codi[nom_alumne].append(contingut)

    print(f"[Tasca {task_id}] S'han processat els projectes de {len(alumnes_codi)} alumnes.")
    
    # Aquí ja tenim tot el codi de la classe a la memòria, llest per analitzar!
    # TODO: 3. Buscar indicis d'IA (Heurística).
    # ... (Això va just després del bucle on llegim els arxius de cada alumne) ...
    
    print(f"[Tasca {task_id}] Iniciant l'anàlisi de plagi entre {len(alumnes_codi)} alumnes...")
    
    # Unim tot el codi de cada alumne en un sol text gran per avaluar el projecte sencer
    codi_per_alumne = {}
    for alumne, arxius in alumnes_codi.items():
        codi_per_alumne[alumne] = "\n".join(arxius)

    resultats_plagi = []
    
    # itertools.combinations crea totes les parelles possibles d'alumnes sense repetir
    parelles = itertools.combinations(codi_per_alumne.keys(), 2)
    
    for alumne1, alumne2 in parelles:
        similitud = calcular_similitud(codi_per_alumne[alumne1], codi_per_alumne[alumne2])
        percentatge = round(similitud * 100, 2)
        
        # Només ens interessen les similituds altes (per exemple, més del 75%)
        if percentatge > 75.0:
            resultats_plagi.append({
                "alumne_A": alumne1,
                "alumne_B": alumne2,
                "similitud": percentatge
            })
            print(f"⚠️ Alerta Plagi: {alumne1} i {alumne2} tenen una similitud del {percentatge}%")

    db = SessionLocal()
    try:
        # 1. Guardem tots els casos de plagi trobats
        for resultat in resultats_plagi:
            nou_plagi = ResultatPlagi(
                tasca_id=task_id,
                alumne_a=resultat["alumne_A"],
                alumne_b=resultat["alumne_B"],
                similitud=resultat["similitud"]
            )
            db.add(nou_plagi)
        
        # 2. Actualitzem l'estat de la tasca principal a "completat"
        tasca = db.query(AnalisiTasques).filter(AnalisiTasques.id == task_id).first()
        if tasca:
            tasca.estat = "completat"
            
        db.commit()
        print(f"[Tasca {task_id}] Resultats guardats a la base de dades correctament.")
    except Exception as e:
        print(f"[Tasca {task_id}] Error al guardar a la DB: {e}")
        db.rollback()
    finally:
        db.close()
    
    # 5. Neteja: Esborrem els arxius temporals per no omplir el disc
    shutil.rmtree(extract_dir)
    os.remove(file_path)
    print(f"[Tasca {task_id}] Neteja d'arxius temporals completada.")


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
    """
    Aquest endpoint permet consultar l'estat d'una tasca d'anàlisi 
    i obtenir-ne els resultats si ja ha acabat.
    """
    db = SessionLocal()
    try:
        # 1. Busquem si la tasca existeix a la base de dades
        tasca = db.query(AnalisiTasques).filter(AnalisiTasques.id == task_id).first()
        
        if not tasca:
            raise HTTPException(status_code=404, detail="Tasca no trobada. Comprova l'ID.")
        
        # 2. Si encara està treballant en segon pla, avisem l'usuari
        if tasca.estat == "en proces":
            return {
                "task_id": tasca.id,
                "estat": tasca.estat,
                "missatge": "L'anàlisi encara s'està executant. Si us plau, espera uns instants."
            }
            
        # 3. Si ja ha acabat, busquem quins alumnes s'han copiat
        resultats_bd = db.query(ResultatPlagi).filter(ResultatPlagi.tasca_id == task_id).all()
        
        # Formatem les dades perquè siguin fàcils de llegir per al Frontend
        llista_plagi = []
        for r in resultats_bd:
            llista_plagi.append({
                "alumne_A": r.alumne_a,
                "alumne_B": r.alumne_b,
                "similitud": r.similitud
            })
            
        # Ordenem la llista de més similitud a menys per veure els casos greus primer
        llista_plagi.sort(key=lambda x: x["similitud"], reverse=True)
            
        return {
            "task_id": tasca.id,
            "estat": tasca.estat,
            "arxiu": tasca.nom_arxiu,
            "alertes_plagi": llista_plagi
        }
    finally:
        db.close() # És molt important tancar sempre la connexió
