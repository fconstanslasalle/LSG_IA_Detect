from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
import shutil
import os
import uuid

app = FastAPI(title="Analitzador de Projectes Java")

# Carpeta on guardarem els ZIPs temporalment
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def processar_arxiu_zip(file_path: str, task_id: str):
    """
    Aquesta funció s'executarà en segon pla.
    Aquí és on posarem la lògica de descomprimir i analitzar el codi Java.
    """
    print(f"[Tasca {task_id}] Iniciant el processament de: {file_path}")
    
    # TODO: 1. Descomprimir el ZIP.
    # TODO: 2. Buscar indicis d'IA i plagi.
    # TODO: 3. Guardar els resultats a la base de dades.
    # TODO: 4. Netejar (esborrar) els arxius temporals.
    
    print(f"[Tasca {task_id}] Processament finalitzat.")

@app.post("/upload-zip/")
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