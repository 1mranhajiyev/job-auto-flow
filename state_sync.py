"""Şifrəli vakansiya vəziyyətini GitHub-dakı job-state budağında saxlayır.

Açıq repoda fayl görünür, məzmunu isə STATE_FERNET_KEY olmadan oxunmur.
"""

import base64
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request

from cryptography.fernet import Fernet

BRANCH = "job-state"
FILE_PATH = "state.json.enc"
LOCK = threading.Lock()
_token_cache: str | None = None


def _secret(value: str) -> str:
    token = github_token()
    if token and token in value:
        return value.replace(token, "***")
    return value


def github_token() -> str:
    global _token_cache
    if _token_cache is not None:
        return _token_cache
    found = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if not found:
        try:
            found = subprocess.check_output(
                ["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            found = ""
    _token_cache = found
    return found


def enabled() -> bool:
    return bool(
        (os.getenv("STATE_FERNET_KEY") or "").strip()
        and (os.getenv("GITHUB_REPOSITORY") or "").strip()
        and github_token()
    )


def _fernet() -> Fernet:
    key = (os.getenv("STATE_FERNET_KEY") or "").strip().encode()
    return Fernet(key)


def _repo() -> str:
    return (os.getenv("GITHUB_REPOSITORY") or "").strip()


def _request(method: str, url: str, payload: dict | None = None) -> dict | None:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {github_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "job-auto-flow",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(_secret(f"GitHub {exc.code}: {detail}")) from None


def _api(path: str, method: str = "GET", payload: dict | None = None) -> dict | None:
    return _request(method, f"https://api.github.com{path}", payload)


def _ensure_branch():
    if _api(f"/repos/{_repo()}/git/ref/heads/{BRANCH}"):
        return
    main_ref = _api(f"/repos/{_repo()}/git/ref/heads/main")
    if not main_ref:
        raise RuntimeError("main budağı tapılmadı")
    created = _api(
        f"/repos/{_repo()}/git/refs",
        "POST",
        {"ref": f"refs/heads/{BRANCH}", "sha": main_ref["object"]["sha"]},
    )
    if created is None:
        raise RuntimeError("job-state budağı yaradılmadı")


def load() -> dict:
    if not enabled():
        return {"seen_urls": [], "jobs": []}
    with LOCK:
        _ensure_branch()
        item = _api(f"/repos/{_repo()}/contents/{FILE_PATH}?ref={BRANCH}")
        if not item or not item.get("content"):
            return {"seen_urls": [], "jobs": []}
        encoded = "".join(item["content"].split())
        token = base64.b64decode(encoded).decode()
        data = json.loads(_fernet().decrypt(token.encode()))
        data.setdefault("seen_urls", [])
        data.setdefault("jobs", [])
        return data


def save(state: dict):
    if not enabled():
        raise RuntimeError("Şifrəli vəziyyət bağlıdır: STATE_FERNET_KEY və ya GitHub tokeni yoxdur.")
    slim = _slim(state)
    token = _fernet().encrypt(json.dumps(slim, ensure_ascii=False).encode()).decode()
    content = base64.b64encode(token.encode()).decode()
    with LOCK:
        _ensure_branch()
        item = _api(f"/repos/{_repo()}/contents/{FILE_PATH}?ref={BRANCH}")
        payload = {
            "message": "Update pending job state",
            "content": content,
            "branch": BRANCH,
        }
        if item and item.get("sha"):
            payload["sha"] = item["sha"]
        saved = _api(f"/repos/{_repo()}/contents/{FILE_PATH}", "PUT", payload)
        if saved is None:
            raise RuntimeError("Vəziyyət GitHub-a yazılmadı")


def _slim(state: dict) -> dict:
    jobs = []
    for job in state.get("jobs") or []:
        status = str(job.get("status") or "")
        if status.startswith(("Applied", "Dry-Run", "Keçildi")):
            jobs.append({
                "id": job.get("id"),
                "job_url": job.get("job_url"),
                "title": job.get("title"),
                "company": job.get("company"),
                "match_score": job.get("match_score"),
                "status": status,
                "hr_email": job.get("hr_email") or "",
            })
        else:
            item = dict(job)
            item.pop("_store", None)
            jobs.append(item)
    return {
        "seen_urls": sorted({str(url).rstrip("/") for url in (state.get("seen_urls") or []) if url}),
        "jobs": jobs,
    }


def patch_job(job_id: str, **fields) -> dict | None:
    state = load()
    found = None
    for job in state.get("jobs") or []:
        if job.get("id") == job_id:
            job.update(fields)
            found = job
            break
    if not found:
        return None
    save(state)
    return found


def seed_seen_urls(urls: list[str]):
    state = load()
    seen = {str(url).rstrip("/") for url in state.get("seen_urls") or []}
    seen.update(str(url).rstrip("/") for url in urls if url)
    state["seen_urls"] = sorted(seen)
    state.setdefault("jobs", [])
    save(state)
    return len(state["seen_urls"])
