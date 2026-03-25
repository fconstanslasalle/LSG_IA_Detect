from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
import shutil
import os
import uuid
import zipfile
from pathlib import Path

app = FastAPI(title="Analitzador de Projectes Java")

# Carpeta on guardarem els ZIPs temporalment
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

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
    # TODO: 4. Cercar plagi (Comparació AST).
    
    # 5. Neteja: Esborrem els arxius temporals per no omplir el disc
    shutil.rmtree(extract_dir)
    os.remove(file_path)
    print(f"[Tasca {task_id}] Neteja d'arxius temporals completada.")

# Endpoint de prova per comprovar que l'API funciona
@app.get("/")
def read_root():
    return {"Estat": "L'API està funcionant correctament"}