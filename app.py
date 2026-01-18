import os
import uuid
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------
# Paths / dirs (Render-safe)
# ---------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# En Render debes definir COSAPI_UPLOAD_DIR (ej: /tmp/uploads o /var/data/uploads)
UPLOAD_DIR = os.getenv("COSAPI_UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
AUDIO_DIR = os.path.join(UPLOAD_DIR, "audio")
PHOTO_DIR = os.path.join(UPLOAD_DIR, "photos")

STATIC_DIR = os.path.join(BASE_DIR, "static")
INDEX_HTML = os.path.join(STATIC_DIR, "index.html")

# Crear directorios ANTES de montar StaticFiles (Starlette lo exige)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(PHOTO_DIR, exist_ok=True)

# Import core DESPUÉS de crear dirs (evita crashes al importar)
from core import (
    create_report, update_transcript,
    list_reports, get_report,
    list_reports_by_date
)

app = FastAPI(title="Cosapi - Asistente de Obra por Voz (PoC)")

# Servir UI y uploads (monta el path REAL del env)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/", response_class=HTMLResponse)
def home():
    # Más robusto que open() en serverless/containers
    if not os.path.exists(INDEX_HTML):
        return HTMLResponse(
            content="<h3>No existe static/index.html</h3>",
            status_code=500
        )
    return FileResponse(INDEX_HTML)


@app.post("/api/report")
async def api_create_report(
    audio: UploadFile = File(...),
    photo: UploadFile | None = File(None),
    user_email: str | None = Form(None),
    project_id: str | None = Form(None),
):
    # Guardar audio
    audio_ext = os.path.splitext(audio.filename or "")[1] or ".webm"
    audio_name = f"{uuid.uuid4()}{audio_ext}"
    audio_path = os.path.join(AUDIO_DIR, audio_name)

    audio_bytes = await audio.read()
    with open(audio_path, "wb") as f:
        f.write(audio_bytes)

    # Guardar foto (opcional)
    photo_url = None
    photo_disk_path = None
    if photo:
        photo_ext = os.path.splitext(photo.filename or "")[1] or ".jpg"
        photo_name = f"{uuid.uuid4()}{photo_ext}"
        photo_disk_path = os.path.join(PHOTO_DIR, photo_name)

        photo_bytes = await photo.read()
        with open(photo_disk_path, "wb") as f:
            f.write(photo_bytes)

        # URL renderizable en front (porque montamos /uploads -> UPLOAD_DIR)
        photo_url = f"/uploads/photos/{photo_name}"

    audio_mime = audio.content_type or "audio/webm"

    try:
        rep = create_report(
            audio_path=audio_path,
            audio_mime=audio_mime,
            photo_url=photo_url,
            photo_disk_path=photo_disk_path,
            user_email=user_email,
            project_id=project_id
        )
        return rep
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/report/{report_id}/transcript")
async def api_update_transcript(report_id: str, transcript_text: str = Form(...)):
    if not get_report(report_id):
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    return update_transcript(report_id, transcript_text)


@app.get("/api/reports")
def api_list_reports(limit: int = 50):
    return {"items": list_reports(limit=limit)}


@app.get("/api/report/{report_id}")
def api_get_report(report_id: str):
    rep = get_report(report_id)
    if not rep:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    return rep


@app.get("/resumen-diario", response_class=HTMLResponse)
def resumen_diario(fecha: str):
    items = list_reports_by_date(fecha)

    rows_html = []
    for i, it in enumerate(items, start=1):
        foto = it.get("photo_url")
        foto_html = (
            f'<a href="{foto}" target="_blank">'
            f'<img src="{foto}" style="width:160px;height:110px;object-fit:cover;border-radius:10px;border:1px solid #ddd"/>'
            f'</a>'
            if foto else '<span style="color:#666">Sin foto</span>'
        )

        txt = (it.get("transcript_text") or "").replace("<", "&lt;").replace(">", "&gt;")
        rows_html.append(f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #eee;">{i}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{it.get("project_id") or ""}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{it.get("created_at")}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{foto_html}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;white-space:pre-wrap;">{txt}</td>
        </tr>
        """)

    html = f"""
    <!doctype html>
    <html lang="es">
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>Resumen diario - {fecha}</title>
      <style>
        body{{font-family:system-ui,Segoe UI,Arial; margin:24px;}}
        .top{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
        .btn{{padding:10px 14px;border-radius:10px;border:1px solid #ddd;cursor:pointer;background:#111;color:#fff}}
        table{{width:100%;border-collapse:collapse;margin-top:16px}}
        th{{text-align:left;padding:10px;border-bottom:2px solid #ddd;background:#fafafa}}
      </style>
    </head>
    <body>
      <div class="top">
        <div>
          <h2 style="margin:0;">Resumen diario de liberaciones</h2>
          <div style="color:#666;">Fecha: <b>{fecha}</b> | Total: <b>{len(items)}</b></div>
        </div>
        <button class="btn" onclick="window.print()">Imprimir / Guardar PDF</button>
      </div>

      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Proyecto/Frente</th>
            <th>Hora (UTC)</th>
            <th>Foto</th>
            <th>Descripción / Transcripción</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html) if rows_html else '<tr><td colspan="5" style="padding:12px;color:#666;">No hay liberaciones registradas.</td></tr>'}
        </tbody>
      </table>
    </body>
    </html>
    """
    return html
