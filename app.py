import os
import re
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from playwright.sync_api import sync_playwright
import openpyxl

import main
import telegram_bot

@asynccontextmanager
async def _lifespan(_app):
    telegram_bot.start_poller()
    yield


app = FastAPI(title="Job Auto-Flow Studio", lifespan=_lifespan)

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATE_FILE = BASE_DIR / "state.json"
SOURCES_FILE = BASE_DIR / "sources.txt"
ENV_FILE = BASE_DIR / ".env"

main.OUTPUT_DIR.mkdir(exist_ok=True)
app.mount("/output", StaticFiles(directory=str(main.OUTPUT_DIR)), name="output")

TASK_STATE = {
    "running": False,
    "step": "",
    "progress": 0,
    "logs": [],
    "cancel": False,
}


def log_msg(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    TASK_STATE["logs"].append(f"[{ts}] {msg}")
    if len(TASK_STATE["logs"]) > 120:
        TASK_STATE["logs"] = TASK_STATE["logs"][-120:]
    print(msg)


def load_state() -> dict:
    with telegram_bot.STATE_LOCK:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"jobs": []}


def save_state(state: dict):
    with telegram_bot.STATE_LOCK:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def update_env_var(key: str, value: str):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    updated = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------
# PYDANTIC MODELLƏR
# ---------------------------------------------------------
class ProfileSettingsPayload(BaseModel):
    profile: dict
    sources_text: str
    min_score: int
    max_jobs: int
    dry_run: bool


class ScanPayload(BaseModel):
    rescan_existing: bool = False


class JobUpdatePayload(BaseModel):
    job_id: str
    selected: bool | None = None
    hr_email: str | None = None
    email_subject: str | None = None
    cover_letter: str | None = None


class BulkSelectPayload(BaseModel):
    mode: str  # "all" | "none" | "matched" | "email_only"


class GenerateCVPayload(BaseModel):
    job_id: str | None = None  # None olarsa seçilmiş bütün vakansiyalar üçün yaradır


class SendPayload(BaseModel):
    dry_run: bool


# ---------------------------------------------------------
# ENDPOINT-LƏR
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    html_file = TEMPLATES_DIR / "index.html"
    if not html_file.exists():
        return HTMLResponse("<h1>templates/index.html tapılmadı</h1>", status_code=500)
    return HTMLResponse(html_file.read_text(encoding="utf-8"))


@app.get("/api/state")
def get_state():
    profile = {}
    if main.PROFILE_CACHE.exists():
        profile = json.loads(main.PROFILE_CACHE.read_text(encoding="utf-8"))
    elif Path("cv.pdf").exists():
        profile = main.analyze_cv("cv.pdf")

    sources_text = SOURCES_FILE.read_text(encoding="utf-8") if SOURCES_FILE.exists() else ""
    state = load_state()

    return {
        "profile": profile,
        "sources_text": sources_text,
        "settings": {
            "min_score": main.MIN_SCORE,
            "max_jobs": main.MAX_JOBS,
            "dry_run": main.DRY_RUN,
        },
        "jobs": state.get("jobs", []),
        "task": TASK_STATE,
    }


@app.post("/api/profile/save")
def save_profile_and_settings(payload: ProfileSettingsPayload):
    main.PROFILE_CACHE.write_text(
        json.dumps(payload.profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    SOURCES_FILE.write_text(payload.sources_text.strip() + "\n", encoding="utf-8")

    main.MIN_SCORE = int(payload.min_score)
    main.MAX_JOBS = int(payload.max_jobs)
    main.DRY_RUN = bool(payload.dry_run)

    update_env_var("MIN_MATCH_SCORE", str(main.MIN_SCORE))
    update_env_var("MAX_JOBS_PER_SOURCE", str(main.MAX_JOBS))
    update_env_var("DRY_RUN", "true" if main.DRY_RUN else "false")
    return {"ok": True}


@app.post("/api/profile/upload-cv")
async def upload_cv(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Yalnız PDF faylı qəbul olunur.")
    content = await file.read()
    Path("cv.pdf").write_bytes(content)
    if main.PROFILE_CACHE.exists():
        main.PROFILE_CACHE.unlink()
    profile = main.analyze_cv("cv.pdf")
    return {"ok": True, "profile": profile}


@app.post("/api/profile/reparse")
def reparse_cv():
    if not Path("cv.pdf").exists():
        raise HTTPException(status_code=404, detail="cv.pdf tapılmadı.")
    if main.PROFILE_CACHE.exists():
        main.PROFILE_CACHE.unlink()
    profile = main.analyze_cv("cv.pdf")
    return {"ok": True, "profile": profile}


@app.post("/api/task/cancel")
def cancel_task():
    if TASK_STATE["running"]:
        TASK_STATE["cancel"] = True
        log_msg("! İstifadəçi tərəfindən dayandırma sorğusu göndərildi...")
    return {"ok": True}


# ---------------------------------------------------------
# ADDIM 2: VAKANSİYA SKANI VƏ QİYMƏTLƏNDİRMƏ
# ---------------------------------------------------------
def _run_scan_worker(rescan_existing: bool):
    TASK_STATE.update({"running": True, "step": "scan", "progress": 5, "logs": [], "cancel": False})
    telegram_bot.SCAN_BUSY.set()
    try:
        profile = main.analyze_cv("cv.pdf")
        wb, ws = main.init_excel()
        skip_urls = set() if rescan_existing else main.get_existing_urls(ws)

        urls = [
            line.strip()
            for line in SOURCES_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        raw_jobs = []
        seen_urls = set(skip_urls)

        with sync_playwright() as p:
            launch_kwargs = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--disable-http2"],
            }
            if os.path.exists("/usr/bin/chromium"):
                launch_kwargs["executable_path"] = "/usr/bin/chromium"
            browser = p.chromium.launch(**launch_kwargs)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )

            raw_jobs = main.collect_source_jobs(
                page,
                profile,
                urls,
                seen_urls,
                log=log_msg,
                should_cancel=lambda: TASK_STATE["cancel"],
                on_progress=lambda pct: TASK_STATE.__setitem__("progress", pct),
            )
            page.close()

            TASK_STATE["progress"] = 72
            log_msg(f"Toplam {len(raw_jobs)} yeni vakansiya AI ilə qiymətləndirilir...")
            scores_map = main.score_jobs_batch(profile, raw_jobs)

            TASK_STATE["progress"] = 88
            final_jobs = []
            for idx, j in enumerate(raw_jobs):
                info = scores_map.get(idx, {})
                score = int(info.get("match_score", 0))
                company = (info.get("company") or j.get("company") or "Unknown").strip()
                title = (info.get("title") or j.get("title") or "Unknown").strip()

                llm_email = str(info.get("hr_email", "")).strip()
                hr_email = ""
                if main.is_valid_hr_email(llm_email):
                    hr_email = llm_email
                elif j["found_emails"]:
                    hr_email = j["found_emails"][0]
                elif score >= main.MIN_SCORE:
                    log_msg(f"  E-poçt yoxlanılır: {company} — {title}")
                    hr_email = main.find_hidden_email_on_page(browser, j["job_url"])

                final_jobs.append({
                    "id": f"job_{idx}_{int(datetime.now().timestamp())}",
                    "source": j["source"],
                    "job_url": j["job_url"],
                    "company": company,
                    "title": title,
                    "match_score": score,
                    "selected": score >= main.MIN_SCORE,
                    "hr_email": hr_email,
                    "raw_text": j["raw_text"],
                    "cv_ready": False,
                    "pdf_path": "",
                    "pdf_url": "",
                    "email_subject": "",
                    "cover_letter": "",
                    "posted_at": j.get("posted_at", ""),
                    "salary": j.get("salary") or "",
                    "job_urls": j.get("job_urls") or [j["job_url"]],
                    "status": "Uyğundur (Seçilib)" if score >= main.MIN_SCORE else f"Aşağı bal (<{main.MIN_SCORE}%)",
                })

            browser.close()

        final_jobs.sort(key=lambda x: x["match_score"], reverse=True)
        save_state({"jobs": final_jobs})
        TASK_STATE["progress"] = 100
        log_msg(f"Skan tamamlandı! {len(final_jobs)} vakansiya siyahıya əlavə edildi.")
        telegram_bot.SCAN_BUSY.clear()
        telegram_bot.notify_matches(final_jobs, log=log_msg)
    except Exception as e:
        log_msg(f"XƏTA: {e}")
    finally:
        telegram_bot.SCAN_BUSY.clear()
        TASK_STATE["running"] = False


@app.post("/api/step2/scan")
def start_scan(payload: ScanPayload):
    if TASK_STATE["running"]:
        raise HTTPException(status_code=409, detail="Hazırda başqa proses icra olunur.")
    t = threading.Thread(target=_run_scan_worker, args=(payload.rescan_existing,), daemon=True)
    t.start()
    return {"ok": True}


@app.post("/api/jobs/update")
def update_job(payload: JobUpdatePayload):
    state = load_state()
    for j in state.get("jobs", []):
        if j["id"] == payload.job_id:
            if payload.selected is not None:
                j["selected"] = payload.selected
            if payload.hr_email is not None:
                j["hr_email"] = payload.hr_email.strip()
            if payload.email_subject is not None:
                j["email_subject"] = payload.email_subject
            if payload.cover_letter is not None:
                j["cover_letter"] = payload.cover_letter
            break
    save_state(state)
    return {"ok": True}


@app.post("/api/jobs/bulk-select")
def bulk_select(payload: BulkSelectPayload):
    state = load_state()
    for j in state.get("jobs", []):
        if payload.mode == "all":
            j["selected"] = True
        elif payload.mode == "none":
            j["selected"] = False
        elif payload.mode == "matched":
            j["selected"] = j["match_score"] >= main.MIN_SCORE
        elif payload.mode == "email_only":
            j["selected"] = j["match_score"] >= main.MIN_SCORE and bool(j.get("hr_email"))
    save_state(state)
    return {"ok": True}


# ---------------------------------------------------------
# ADDIM 3: FƏRDİ CV VƏ COVER LETTER HAZIRLANMASI
# ---------------------------------------------------------
def _run_cv_worker(single_job_id: str | None):
    TASK_STATE.update({"running": True, "step": "cv", "progress": 5, "logs": [], "cancel": False})
    try:
        profile = main.analyze_cv("cv.pdf")
        state = load_state()
        jobs = state.get("jobs", [])

        targets = [
            j for j in jobs
            if (single_job_id and j["id"] == single_job_id)
            or (not single_job_id and j.get("selected"))
        ]

        if not targets:
            log_msg("CV hazırlanması üçün heç bir vakansiya seçilməyib.")
            TASK_STATE["progress"] = 100
            return

        with sync_playwright() as p:
            launch_kwargs = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--disable-http2"],
            }
            if os.path.exists("/usr/bin/chromium"):
                launch_kwargs["executable_path"] = "/usr/bin/chromium"
            browser = p.chromium.launch(**launch_kwargs)

            for idx, j in enumerate(targets):
                if TASK_STATE["cancel"]:
                    break
                pct = 10 + int((idx / len(targets)) * 85)
                TASK_STATE["progress"] = pct
                log_msg(f"CV hazırlanır ({idx + 1}/{len(targets)}): {j['company']} — {j['title']}")

                tailored = main.tailor_single_job(profile, j)
                safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{j['company']}_{j['title']}")[:50]
                pdf_file = main.OUTPUT_DIR / f"CV_{safe_name}.pdf"

                main.render_tailored_pdf(browser, profile, tailored, pdf_file)

                j["cv_ready"] = True
                j["pdf_path"] = str(pdf_file)
                j["pdf_url"] = f"/output/{pdf_file.name}"
                j["email_subject"] = tailored.get("email_subject", f"Müraciət: {j['title']}")
                j["cover_letter"] = tailored.get("cover_letter", "")
                j["status"] = "CV Hazırdır"
                save_state(state)

            browser.close()

        TASK_STATE["progress"] = 100
        log_msg(f"Tamamlandı! {len(targets)} vakansiya üçün fərdi PDF CV və məktub hazırlandı.")
    except Exception as e:
        log_msg(f"CV Xətası: {e}")
    finally:
        TASK_STATE["running"] = False


@app.post("/api/step3/generate-cvs")
def generate_cvs(payload: GenerateCVPayload):
    if TASK_STATE["running"]:
        raise HTTPException(status_code=409, detail="Hazırda başqa proses icra olunur.")
    t = threading.Thread(target=_run_cv_worker, args=(payload.job_id,), daemon=True)
    t.start()
    return {"ok": True}


# ---------------------------------------------------------
# ADDIM 4: TƏSDİQ, E-POÇT GÖNDƏRİŞİ VƏ EXCEL HESABAT
# ---------------------------------------------------------
@app.post("/api/step4/send")
def execute_send_and_export(payload: SendPayload):
    main.DRY_RUN = bool(payload.dry_run)
    state = load_state()
    jobs = state.get("jobs", [])

    wb, ws = main.init_excel()
    existing = main.get_existing_urls(ws)

    sent_count = 0
    manual_count = 0
    results_summary = []

    for j in jobs:
        if not j.get("selected"):
            continue

        hr_email = j.get("hr_email", "").strip()
        pdf_path = Path(j["pdf_path"]) if j.get("pdf_path") else None

        if hr_email and pdf_path and pdf_path.exists():
            try:
                status = main.apply_via_email(
                    hr_email,
                    j.get("email_subject") or f"Application: {j['title']}",
                    j.get("cover_letter") or "",
                    pdf_path,
                )
                channel = "email"
                target = hr_email
                sent_count += 1
            except Exception as e:
                status = f"Email Error: {str(e)[:50]}"
                channel = "email"
                target = hr_email
        else:
            status = "CV Ready (Manual Apply)" if (pdf_path and pdf_path.exists()) else "Selected (No CV)"
            channel = "manual"
            target = j["job_url"]
            manual_count += 1

        j["status"] = status
        results_summary.append({"company": j["company"], "title": j["title"], "status": status})

        if j["job_url"].rstrip("/") not in existing:
            ws.append([
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                j["source"],
                j["company"],
                j["title"],
                j["match_score"],
                channel,
                target,
                status,
                j.get("pdf_path") or "-",
                j["job_url"],
            ])
            existing.add(j["job_url"].rstrip("/"))

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 45)

    wb.save(main.EXCEL_PATH)
    save_state(state)

    return {
        "ok": True,
        "sent_count": sent_count,
        "manual_count": manual_count,
        "dry_run": main.DRY_RUN,
        "details": results_summary,
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    expected = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    received = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not expected or received != expected:
        raise HTTPException(status_code=403, detail="Webhook imzası uyğun deyil.")
    update = await request.json()
    threading.Thread(target=telegram_bot.handle_update, args=(update,), daemon=True).start()
    return {"ok": True}


@app.get("/api/export/excel")
def download_excel():
    if not main.EXCEL_PATH.exists():
        wb, _ = main.init_excel()
        wb.save(main.EXCEL_PATH)
    return FileResponse(
        path=str(main.EXCEL_PATH),
        filename="applications.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)