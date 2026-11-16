"""Telegram bildirişi və telefondan təsdiq.

80% və yuxarı uyğunluqda mesaj gedir. Altındakı düymələr:
Göndər — CV hazırlayır və e-poçtu göndərir (test rejimində yalnız yoxlayır).
Keç — elanı siyahıdan çıxarır.
"""

import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import main

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
STATE_LOCK = threading.Lock()
ACTION_LOCK = threading.Lock()
SCAN_BUSY = threading.Event()

_poller_started = False
_offset = 0


def token() -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def chat_id() -> str:
    return (os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def enabled() -> bool:
    return bool(token() and chat_id())


def report_failure(process: str, detail: str) -> bool:
    """Proses alınmayanda xətanı JobFlow söhbətinə yazır."""
    text = f"⚠️ <b>{_html(process)} alınmadı</b>\n{_html(str(detail))[:700]}"
    if not enabled():
        print(f"{process} alınmadı: {detail}")
        return False
    try:
        _api("sendMessage", {
            "chat_id": chat_id(),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        return True
    except Exception as exc:
        print(f"Xəta bildirişi getmədi: {exc}")
        return False


def _api(method: str, payload: dict | None = None, timeout: int = 20) -> dict:
    url = f"https://api.telegram.org/bot{token()}/{method}"
    data = json.dumps(payload or {}).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except Exception as exc:
        secret = token()
        message = str(exc).replace(secret, "***") if secret else str(exc)
        raise RuntimeError(message) from None
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or "Telegram xətası")
    return body


def _html(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _state_path() -> Path:
    import profiles
    return profiles.state_file()


def read_state() -> dict:
    path = _state_path()
    with STATE_LOCK:
        if not path.exists():
            return {"jobs": []}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"jobs": []}


def write_state(state: dict):
    path = _state_path()
    with STATE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _job_message(job: dict) -> str:
    email = job.get("hr_email") or "e-poçt yoxdur, əl ilə müraciət"
    posted = job.get("posted_at") or ""
    mode = "Test rejimi: məktub göndərilmir." if main.DRY_RUN else "Göndər düyməsi müraciəti təsdiqləyir."
    lines = [
        f"<b>{job.get('match_score', 0)}% uyğunluq</b>",
        _html(job.get("company") or "Şirkət"),
        _html(job.get("title") or "Vakansiya"),
    ]
    if job.get("salary"):
        lines.append(_html(job["salary"]))
    if posted:
        lines.append(_html(posted))
    lines.append(_html(email))
    urls = []
    for url in job.get("job_urls") or [job.get("job_url") or ""]:
        clean = (url or "").strip()
        if clean and clean not in urls:
            urls.append(clean)
    if len(urls) <= 1:
        href = _html(urls[0] if urls else "").replace('"', "&quot;")
        lines.append(f'<a href="{href}">Elanı aç</a>')
    else:
        links = []
        for url in urls:
            href = _html(url).replace('"', "&quot;")
            links.append(f'<a href="{href}">{_html(url)}</a>')
        lines.append(", ".join(links))
    lines.append(mode)
    return "\n".join(lines)


def _keyboard(job_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "Göndər ✅", "callback_data": f"go:{job_id}"},
            {"text": "Keç ❌", "callback_data": f"skip:{job_id}"},
        ]]
    }


def notify_job(job: dict) -> bool:
    if int(job.get("match_score") or 0) < main.MIN_SCORE:
        return False
    if not token() or not chat_id():
        return False
    _api("sendMessage", {
        "chat_id": chat_id(),
        "text": _job_message(job),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": _keyboard(job["id"]),
    })
    return True


def notify_matches(jobs: list[dict], log=print) -> int:
    """Həddi keçən hər elan üçün ayrı mesaj göndərir."""
    if not token():
        log("Telegram bağlı deyil: TELEGRAM_BOT_TOKEN boşdur.")
        return 0
    if not chat_id():
        log("Telegram chat ID boşdur. Bota /start yazın, ID-ni .env-ə əlavə edin.")
        return 0
    sent = 0
    for job in jobs:
        if int(job.get("match_score") or 0) < main.MIN_SCORE:
            continue
        try:
            if notify_job(job):
                sent += 1
        except Exception as exc:
            log(f"Telegram mesajı getmədi ({job.get('title', '')[:40]}): {exc}")
    if sent:
        log(f"Telegram: {sent} bildiriş göndərildi.")
    else:
        log("Telegram: həddi keçən yeni elan yoxdur.")
    return sent


def _find_job(job_id: str) -> dict | None:
    for job in read_state().get("jobs", []):
        if job.get("id") == job_id:
            return job
    import state_sync
    if not state_sync.enabled():
        return None
    try:
        remote = state_sync.load()
    except Exception as exc:
        print(f"Uzaq siyahı açılmadı: {exc}")
        return None
    for job in remote.get("jobs") or []:
        if job.get("id") == job_id:
            return job
    return None


def _patch_local(job_id: str, owner: str = "", **fields) -> dict | None:
    import profiles
    path = _state_path()
    with STATE_LOCK:
        if not path.exists():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        found = None
        kept = []
        active = profiles.active_slug()
        for job in state.get("jobs", []):
            if job.get("id") != job_id:
                kept.append(job)
                continue
            job.update(fields)
            job_owner = owner or str(job.get("profile_slug") or "")
            if active and job_owner and job_owner != active:
                found = job
                continue
            if job_owner:
                job["profile_slug"] = job_owner
            kept.append(job)
            found = job
        if found:
            state["jobs"] = kept
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return found if found and profiles.owns(found, active) else None


def _patch_job(job_id: str, **fields) -> dict | None:
    import state_sync
    remote = None
    if state_sync.enabled():
        try:
            remote = state_sync.patch_job(job_id, **fields)
        except Exception as exc:
            print(f"Uzaq siyahı yenilənmədi: {exc}")
    owner = str((remote or {}).get("profile_slug") or "")
    local = _patch_local(job_id, owner, **fields)
    return remote or local


def _answer(callback_id: str, text: str):
    try:
        _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})
    except Exception:
        pass


def _edit(message: dict, text: str, job_id: str | None = None):
    payload = {
        "chat_id": message["chat"]["id"],
        "message_id": message["message_id"],
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": _keyboard(job_id) if job_id else {"inline_keyboard": []},
    }
    try:
        _api("editMessageText", payload)
    except Exception:
        pass


def _record_excel(job: dict, channel: str, target: str, status: str):
    wb, ws = main.init_excel()
    existing = main.get_existing_urls(ws)
    url = (job.get("job_url") or "").rstrip("/")
    if url in existing:
        return
    ws.append([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        job.get("source") or "",
        job.get("company") or "",
        job.get("title") or "",
        job.get("match_score") or 0,
        channel,
        target,
        status,
        job.get("pdf_path") or "-",
        job.get("job_url") or "",
    ])
    for col in ws.columns:
        width = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(width + 4, 45)
    wb.save(main.EXCEL_PATH)


def _ensure_cv(job: dict) -> dict:
    pdf = Path(job["pdf_path"]) if job.get("pdf_path") else None
    if job.get("cv_ready") and pdf and pdf.exists() and job.get("cover_letter"):
        return job
    slug = str(job.get("profile_slug") or "")
    import profiles
    pdf = profiles.cv_file(slug)
    profile = main.analyze_cv(str(pdf if pdf.exists() else "cv.pdf"), slug=slug)
    from playwright.sync_api import sync_playwright

    tailored = main.tailor_single_job(profile, job)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{job.get('company')}_{job.get('title')}")[:50]
    pdf_file = main.OUTPUT_DIR / f"CV_{safe_name}.pdf"
    launch_kwargs = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled", "--disable-http2"],
    }
    if os.path.exists("/usr/bin/chromium"):
        launch_kwargs["executable_path"] = "/usr/bin/chromium"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_kwargs)
        try:
            main.render_tailored_pdf(browser, profile, tailored, pdf_file)
        finally:
            browser.close()
    return _patch_job(
        job["id"],
        cv_ready=True,
        pdf_path=str(pdf_file),
        pdf_url=f"/output/{pdf_file.name}",
        email_subject=tailored.get("email_subject") or f"Müraciət: {job.get('title')}",
        cover_letter=tailored.get("cover_letter") or "",
        status="CV Hazırdır",
    ) or job


def _approve(job_id: str, message: dict):
    job = _find_job(job_id)
    if not job:
        _edit(message, "Bu elan siyahıda tapılmadı.")
        return
    status = str(job.get("status") or "")
    if status.startswith(("Applied", "Dry-Run", "Keçildi", "CV hazırdır")):
        _edit(message, _job_message(job) + f"\n\n<b>{_html(status)}</b>")
        return
    if os.getenv("RENDER"):
        failed = _dispatch_workflow("apply_job.yml", {
            "job_id": job_id,
            "chat_id": message["chat"]["id"],
            "message_id": message["message_id"],
        })
        if failed:
            _edit(message, _job_message(job) + f"\n\n<b>{_html(failed)}</b>", job_id)
            return
        _edit(message, _job_message(job) + "\n\nCV hazırlanır. Bu, bir neçə dəqiqə çəkə bilər.")
        return
    _edit(message, _job_message(job) + "\n\nCV hazırlanır...")
    try:
        job = _ensure_cv(job)
    except Exception as exc:
        failed = _patch_job(job_id, status=f"CV xətası: {str(exc)[:80]}") or job
        _edit(message, _job_message(failed) + f"\n\n<b>CV hazırlanmadı:</b> {_html(str(exc)[:180])}", job_id)
        return
    email = (job.get("hr_email") or "").strip()
    pdf = Path(job["pdf_path"]) if job.get("pdf_path") else None
    if email and pdf and pdf.exists():
        try:
            result = main.apply_via_email(
                email,
                job.get("email_subject") or f"Müraciət: {job.get('title')}",
                job.get("cover_letter") or "",
                pdf,
            )
        except Exception as exc:
            result = f"Email xətası: {str(exc)[:80]}"
        channel, target = "email", email
    else:
        result = "CV hazırdır, e-poçt yoxdur. Elanı əl ilə göndərin."
        channel, target = "manual", job.get("job_url") or ""
    updated = _patch_job(job_id, status=result, selected=True) or job
    _record_excel(updated, channel, target, result)
    retry = job_id if result.startswith("Email xətası") else None
    _edit(message, _job_message(updated) + f"\n\n<b>{_html(result)}</b>", retry)


def _skip(job_id: str, message: dict):
    job = _patch_job(job_id, selected=False, status="Keçildi (Telegram)")
    if not job:
        _edit(message, "Bu elan siyahıda tapılmadı.")
        return
    _edit(message, _job_message(job) + "\n\n<b>Keçildi.</b>")


def _handle_callback(query: dict):
    message = query.get("message") or {}
    sender = str((query.get("from") or {}).get("id") or "")
    chat = str((message.get("chat") or {}).get("id") or "")
    if not chat_id():
        _answer(query.get("id") or "", "Əvvəl .env-də TELEGRAM_CHAT_ID yazın.")
        return
    if chat != chat_id() and sender != chat_id():
        _answer(query.get("id") or "", "Bu bot başqa söhbətə bağlıdır.")
        return
    data = str(query.get("data") or "")
    action, _, job_id = data.partition(":")
    if action not in ("go", "skip") or not job_id:
        _answer(query.get("id") or "", "Köhnə düymə.")
        return
    if SCAN_BUSY.is_set():
        _answer(query.get("id") or "", "Skan gedir. Bitəndən sonra yenidən basın.")
        return
    if not ACTION_LOCK.acquire(blocking=False):
        _answer(query.get("id") or "", "Başqa elan hazırlanır.")
        return
    try:
        _answer(query.get("id") or "", "Qəbul olundu.")
        if action == "skip":
            _skip(job_id, message)
        else:
            _approve(job_id, message)
    finally:
        ACTION_LOCK.release()


def _command(text: str) -> str:
    word = (text or "").strip().split(" ", 1)[0]
    return word.split("@", 1)[0].lower()


def _reply(chat: str, text: str):
    try:
        _api("sendMessage", {"chat_id": chat, "text": text})
    except Exception:
        pass


def _dispatch_workflow(workflow: str, inputs: dict | None = None) -> str | None:
    """Uğurda None, əks halda Telegramda göstəriləcək xəta mətnini qaytarır."""
    import state_sync

    repo = (os.getenv("GITHUB_REPOSITORY") or "").strip()
    if not repo or not state_sync.github_token():
        return "Bunu başlatmaq üçün GitHub tokeni lazımdır."
    payload = {"ref": "main"}
    if inputs:
        payload["inputs"] = {key: str(value) for key, value in inputs.items()}
    try:
        state_sync._api(f"/repos/{repo}/actions/workflows/{workflow}/dispatches", "POST", payload)
    except Exception as exc:
        detail = str(exc)
        if "403" in detail or "401" in detail:
            return (
                "Token bu işi başlada bilmir. GitHub tokenində Actions icazəsi "
                "Read and write olmalıdır. Sonra həmin tokeni Render-də GITHUB_TOKEN kimi yenilə."
            )
        return f"Başlamadı: {detail[:180]}"
    return None


def _request_scan(sources: list[str] | None = None) -> str:
    import state_sync

    repo = (os.getenv("GITHUB_REPOSITORY") or "").strip()
    if not repo or not state_sync.github_token():
        return "Skanı başlatmaq üçün GitHub tokeni lazımdır."
    try:
        listed = state_sync._api(f"/repos/{repo}/actions/workflows/job_scanner.yml/runs?per_page=5")
    except Exception:
        listed = None
    for run in (listed or {}).get("workflow_runs") or []:
        if run.get("status") in ("queued", "in_progress", "waiting", "pending", "requested"):
            return "Skan artıq gedir. Bitəndə elanlar bura düşəcək."
    inputs = {"sources": "\n".join(sources)} if sources else None
    failed = _dispatch_workflow("job_scanner.yml", inputs)
    if failed:
        return failed.replace("Başlamadı", "Skan başlamadı").replace("Bunu başlatmaq", "Skanı başlatmaq")
    return "Skan başladı. Uyğun elanlar bitəndə bura gələcək."


def _handle_message(message: dict):
    text = (message.get("text") or "").strip()
    chat = str((message.get("chat") or {}).get("id") or "")
    command = _command(text)
    if command not in ("/start", "/scan", "/run"):
        return
    if chat_id() and chat != chat_id():
        return
    if command in ("/scan", "/run"):
        _reply(chat, _request_scan())
        return
    if chat_id():
        reply = (
            f"Bu söhbətə {main.MIN_SCORE}% və yuxarı uyğun elanlar gələcək.\n"
            "Skanı başlatmaq üçün /scan yaz."
        )
    else:
        reply = f"Söhbət ID: {chat}\nBunu .env faylında TELEGRAM_CHAT_ID kimi yazın və proqramı yenidən başladın."
    _reply(chat, reply)


def handle_update(update: dict):
    if "callback_query" in update:
        _handle_callback(update["callback_query"])
    elif "message" in update:
        _handle_message(update["message"])


def _poll_once():
    global _offset
    body = _api("getUpdates", {"offset": _offset, "timeout": 25}, timeout=35)
    for update in body.get("result") or []:
        _offset = int(update["update_id"]) + 1
        handle_update(update)


def _poll_loop():
    while True:
        if not token():
            time.sleep(15)
            continue
        try:
            _poll_once()
        except Exception as exc:
            print(f"Telegram sorğusu dayandı: {exc}")
            time.sleep(5)


def _listen():
    webhook = (os.getenv("TELEGRAM_WEBHOOK_URL") or "").strip()
    if webhook:
        _api("setWebhook", {
            "url": webhook,
            "secret_token": (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip(),
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": False,
        })
        print("Telegram webhook qoşuldu.")
    else:
        _api("deleteWebhook", {"drop_pending_updates": False})
        threading.Thread(target=_poll_loop, name="telegram-poller", daemon=True).start()
    try:
        _api("setMyCommands", {"commands": [
            {"command": "scan", "description": "Vakansiya skanını başlat"},
        ]})
    except Exception:
        pass
    return


def start_poller():
    global _poller_started
    if _poller_started or not token():
        return
    _poller_started = True
    try:
        _listen()
    except Exception as exc:
        print(f"Telegram dinləyicisi açılmadı: {exc}")
        report_failure("Telegram", str(exc))
