"""Seçilmiş elan üçün CV və məktub hazırlayır.

İş GitHub-un serverində gedir. E-poçt göndərilmir.
"""

import base64
import os
import re

import main
import profiles
import state_sync
import telegram_bot
from playwright.sync_api import sync_playwright


def _ids() -> list[str]:
    raw = os.environ.get("JOB_IDS") or os.environ.get("JOB_ID") or ""
    return [part.strip() for part in raw.replace(",", "\n").splitlines() if part.strip()]


def _browser_kwargs() -> dict:
    launch_kwargs = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled", "--disable-http2"],
    }
    if os.path.exists("/usr/bin/chromium"):
        launch_kwargs["executable_path"] = "/usr/bin/chromium"
    return launch_kwargs


def _find_profile_for_job(job: dict):
    """CV hazırlanarkən əvvəl aktiv (ən təzə) profili, sonra elanın profilini istifadə edir."""
    active = profiles.active_slug()
    if active:
        cache = profiles.profile_file(active)
        if cache.exists():
            return active, cache
    slug = str(job.get("profile_slug") or "")
    if slug:
        cache = profiles.profile_file(slug)
        if cache.exists():
            return slug, cache
    root = profiles.profile_file()
    if root.exists():
        return "", root
    raise SystemExit(f"Bu elanın profili tapılmadı: {active or slug or 'aktiv profil'}")


def prepare(job: dict):
    slug, cache = _find_profile_for_job(job)
    source = profiles.cv_file(slug) if slug else profiles.cv_file()
    if not source.exists():
        source = cache
    profile = main.analyze_cv(str(source if source.exists() else cache), slug=slug)
    tailored = main.tailor_single_job(profile, job)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{job.get('company')}_{job.get('title')}")[:50]
    pdf_file = main.OUTPUT_DIR / f"CV_{safe_name}.pdf"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**_browser_kwargs())
        try:
            main.render_tailored_pdf(browser, profile, tailored, pdf_file)
        finally:
            browser.close()
    encoded = base64.b64encode(pdf_file.read_bytes()).decode()
    try:
        state_sync.patch_job(
            job["id"],
            cv_ready=True,
            pdf_b64=encoded,
            pdf_path=str(pdf_file),
            pdf_url=f"/output/{pdf_file.name}",
            email_subject=tailored.get("email_subject") or f"Müraciət: {job.get('title')}",
            cover_letter=tailored.get("cover_letter") or "",
            status="CV Hazırdır",
        )
    except Exception as exc:
        print(f"! State_sync yazılmadı (yalnız local): {exc}")
    print(f"CV hazırdır: {job.get('company')} — {job.get('title')}")
    return pdf_file


def main_run():
    profiles.restore()
    ids = _ids()
    if not ids:
        raise SystemExit("JOB_IDS boşdur.")
    remote = state_sync.load()
    jobs = {job.get("id"): job for job in remote.get("jobs") or []}
    missing = [job_id for job_id in ids if job_id not in jobs]
    if missing:
        raise SystemExit("Elan tapılmadı: " + ", ".join(missing))
    for job_id in ids:
        pdf_file = prepare(jobs[job_id])
        _deliver(jobs[job_id], pdf_file)


def _deliver(job: dict, pdf_file):
    chat = os.getenv("DELIVER_CHAT", "").strip()
    if not chat:
        return
    message_id = os.getenv("DELIVER_MESSAGE", "").strip()
    message = {"chat": {"id": chat}}
    if message_id:
        message["message_id"] = int(message_id)
    fresh = dict(job)
    fresh["cv_ready"] = True
    fresh["cover_letter"] = fresh.get("cover_letter") or ""
    remote = state_sync.load()
    current = next((item for item in remote.get("jobs") or [] if item.get("id") == job.get("id")), None)
    if current:
        fresh = current
    telegram_bot.deliver_cv_packet(fresh, pdf_file.read_bytes(), message)


def _main():
    try:
        main_run()
    except (SystemExit, Exception) as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        if telegram_bot.report_failure("CV", detail):
            from pathlib import Path
            Path("failure-reported").write_text("1", encoding="utf-8")
        raise


if __name__ == "__main__":
    _main()
