"""Planlı skan: uyğun elanı Telegram-a yazır, e-poçtu özü göndərmir.

Düymə basılanda CV və məktub ayrıca təsdiqlənir.
"""

import os
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import main
import profiles
import state_sync
import telegram_bot


def _sources() -> list[str]:
    path = Path("sources.txt")
    if not path.exists():
        raise SystemExit("sources.txt tapılmadı.")
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _browser_kwargs() -> dict:
    launch_kwargs = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled", "--disable-http2"],
    }
    if os.path.exists("/usr/bin/chromium"):
        launch_kwargs["executable_path"] = "/usr/bin/chromium"
    return launch_kwargs


def _build_jobs(raw_jobs: list[dict], scores: dict, browser) -> list[dict]:
    final_jobs = []
    for idx, job in enumerate(raw_jobs):
        info = scores.get(idx, {})
        score = int(info.get("match_score", 0))
        company = (info.get("company") or job.get("company") or "Unknown").strip()
        title = (info.get("title") or job.get("title") or "Unknown").strip()
        llm_email = str(info.get("hr_email", "")).strip()
        hr_email = ""
        if main.is_valid_hr_email(llm_email):
            hr_email = llm_email
        elif job.get("found_emails"):
            hr_email = job["found_emails"][0]
        elif score >= main.MIN_SCORE:
            print(f"  E-poçt yoxlanılır: {company} — {title}")
            hr_email = main.find_hidden_email_on_page(browser, job["job_url"])
        final_jobs.append({
            "id": f"job_{idx}_{int(datetime.now().timestamp())}",
            "source": job["source"],
            "job_url": job["job_url"],
            "company": company,
            "title": title,
            "match_score": score,
            "selected": score >= main.MIN_SCORE,
            "hr_email": hr_email,
            "raw_text": job.get("raw_text") or "",
            "cv_ready": False,
            "pdf_path": "",
            "pdf_url": "",
            "email_subject": "",
            "cover_letter": "",
            "posted_at": job.get("posted_at", ""),
            "salary": job.get("salary") or "",
            "job_urls": job.get("job_urls") or [job["job_url"]],
            "profile_slug": profiles.active_slug(),
            "status": "Uyğundur (Seçilib)" if score >= main.MIN_SCORE else f"Aşağı bal (<{main.MIN_SCORE}%)",
        })
    final_jobs.sort(key=lambda item: item["match_score"], reverse=True)
    return final_jobs


def run():
    profiles.restore()
    if not profiles.profile_file().exists() and not profiles.cv_file().exists():
        raise SystemExit("Profil və ya CV tapılmadı.")
    if not state_sync.enabled():
        raise SystemExit("STATE_FERNET_KEY, GITHUB_REPOSITORY və GitHub tokeni lazımdır.")

    remote = state_sync.load()
    seen = {str(url).rstrip("/") for url in remote.get("seen_urls") or []}
    pending = list(remote.get("jobs") or [])
    print(f"Əvvəlki elanlar: {len(seen)}. Yenidən bildirilməyəcək.")

    profile = main.analyze_cv("cv.pdf")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**_browser_kwargs())
        page = browser.new_page(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        try:
            raw_jobs = main.collect_source_jobs(page, profile, _sources(), seen, log=print)
            print(f"Yeni vakansiya: {len(raw_jobs)}. Qiymətləndirilir...")
            scores = main.score_jobs_batch(profile, raw_jobs)
            fresh = _build_jobs(raw_jobs, scores, browser)
        finally:
            page.close()
            browser.close()

    sent = 0
    for job in fresh:
        urls = [str(url).rstrip("/") for url in (job.get("job_urls") or [job.get("job_url")]) if url]
        if not urls:
            continue
        previous = next((item for item in pending if main.same_vacancy(item, job)), None)
        if previous or any(url in seen for url in urls):
            if previous:
                main._absorb_vacancy(previous, job)
            seen.update(urls)
            continue
        if int(job.get("match_score") or 0) < main.MIN_SCORE:
            seen.update(urls)
            continue
        try:
            delivered = telegram_bot.notify_job(job)
        except Exception as exc:
            print(f"Bildiriş getmədi, növbəti skanda yenidən cəhd ediləcək: {job.get('title', '')[:60]} ({exc})")
            continue
        if not delivered:
            print(f"Bildiriş getmədi, növbəti skanda yenidən cəhd ediləcək: {job.get('title', '')[:60]}")
            continue
        pending.append(job)
        seen.update(urls)
        sent += 1
        state_sync.save({"seen_urls": sorted(seen), "jobs": pending})

    state_sync.save({"seen_urls": sorted(seen), "jobs": pending})
    print(f"Skan bitdi. Telegram bildirişi: {sent}.")


if __name__ == "__main__":
    run()
