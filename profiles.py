"""Hər namizədin profili, CV-si və vakansiya siyahısı ayrı fayldadır.

profile_Imran_Hajiyev.json
cv_Imran_Hajiyev.pdf
state_Imran_Hajiyev.json
"""

import base64
import json
import re
from pathlib import Path

DIR = Path("profiles")
ACTIVE = DIR / "active.txt"


def slug_from_name(full_name: str) -> str:
    name = re.sub(r"\s+", "_", (full_name or "").strip())
    name = re.sub(r"[^\w\-]", "", name, flags=re.UNICODE).strip("_")
    return (name or "namized")[:80]


def active_slug() -> str:
    if ACTIVE.exists():
        return ACTIVE.read_text(encoding="utf-8").strip()
    return ""


def owns(job: dict, slug: str = "") -> bool:
    """Elan yalnız öz profilinin səhifəsində görünür."""
    slug = slug if slug else active_slug()
    owner = str(job.get("profile_slug") or "")
    if not slug:
        return not owner
    return owner == slug


def set_active(slug: str):
    DIR.mkdir(exist_ok=True)
    ACTIVE.write_text(slug.strip(), encoding="utf-8")


def profile_file(slug: str = "") -> Path:
    slug = slug or active_slug()
    named = DIR / f"profile_{slug}.json" if slug else None
    if named and named.exists():
        return named
    if not slug:
        return Path("profile.json")
    return named


def cv_file(slug: str = "") -> Path:
    slug = slug or active_slug()
    named = DIR / f"cv_{slug}.pdf" if slug else None
    if named and named.exists():
        return named
    if not slug:
        return Path("cv.pdf")
    return named


def state_file(slug: str = "") -> Path:
    slug = slug or active_slug()
    if not slug:
        return Path("state.json")
    DIR.mkdir(exist_ok=True)
    return DIR / f"state_{slug}.json"


def list_profiles() -> list[dict]:
    DIR.mkdir(exist_ok=True)
    people = []
    for path in sorted(DIR.glob("profile_*.json")):
        slug = path.name[len("profile_"):-len(".json")]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        people.append({
            "slug": slug,
            "full_name": data.get("full_name") or slug.replace("_", " "),
        })
    return people


def ensure_state(slug: str):
    path = state_file(slug)
    if not path.exists():
        path.write_text('{"jobs": []}', encoding="utf-8")


def migrate_legacy():
    """Köhnə tək profile.json faylını adlı profilə köçürür, orijinalı silmir."""
    legacy = Path("profile.json")
    if not legacy.exists():
        return
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:
        return
    slug = slug_from_name(data.get("full_name") or "")
    DIR.mkdir(exist_ok=True)
    dest = DIR / f"profile_{slug}.json"
    if not dest.exists():
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    cv_src = Path("cv.pdf")
    cv_dest = DIR / f"cv_{slug}.pdf"
    if cv_src.exists() and not cv_dest.exists():
        cv_dest.write_bytes(cv_src.read_bytes())
    state_src = Path("state.json")
    state_dest = DIR / f"state_{slug}.json"
    if state_src.exists() and not state_dest.exists():
        state_dest.write_bytes(state_src.read_bytes())
    elif not state_dest.exists():
        ensure_state(slug)
    if not active_slug():
        set_active(slug)


def publish():
    """Profilləri şifrəli GitHub vəziyyətinə yazır ki, sayt yenidən açılsa da qalsın."""
    try:
        import state_sync
        if not state_sync.enabled():
            return
        remote = state_sync.load()
    except Exception as exc:
        print(f"Profillər uzağa yazılmadı: {exc}")
        return
    people = {}
    for item in list_profiles():
        slug = item["slug"]
        try:
            profile = json.loads(profile_file(slug).read_text(encoding="utf-8"))
        except Exception:
            continue
        cv = cv_file(slug)
        state_path = DIR / f"state_{slug}.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"jobs": []}
        except Exception:
            state = {"jobs": []}
        for job in state.get("jobs") or []:
            job.pop("raw_text", None)
        people[slug] = {
            "profile": profile,
            "cv_b64": base64.b64encode(cv.read_bytes()).decode() if cv.exists() else "",
            "state": state,
        }
    remote["library"] = {"active": active_slug(), "people": people}
    try:
        state_sync.save(remote)
    except Exception as exc:
        print(f"Profillər uzağa yazılmadı: {exc}")


def restore():
    """Saytın diski boşdursa, profilləri şifrəli vəziyyətdən bərpa edir."""
    try:
        import state_sync
        if state_sync.enabled():
            remote = state_sync.load()
            library = remote.get("library") or {}
            DIR.mkdir(exist_ok=True)
            for slug, person in (library.get("people") or {}).items():
                dest = DIR / f"profile_{slug}.json"
                if not dest.exists() and person.get("profile"):
                    dest.write_text(json.dumps(person["profile"], ensure_ascii=False, indent=2), encoding="utf-8")
                cv_dest = DIR / f"cv_{slug}.pdf"
                if not cv_dest.exists() and person.get("cv_b64"):
                    cv_dest.write_bytes(base64.b64decode(person["cv_b64"]))
                state_dest = DIR / f"state_{slug}.json"
                if not state_dest.exists() and person.get("state"):
                    state_dest.write_text(json.dumps(person["state"], ensure_ascii=False, indent=2), encoding="utf-8")
            chosen = (library.get("active") or "").strip()
            if chosen and (DIR / f"profile_{chosen}.json").exists():
                set_active(chosen)
    except Exception as exc:
        print(f"Profillər bərpa olunmadı: {exc}")
    migrate_legacy()
