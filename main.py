import os
import re
import json
import time
import smtplib
from datetime import date, datetime, timedelta
from pathlib import Path
from email.message import EmailMessage
import urllib.request
from urllib.parse import quote_plus, urljoin, urlparse

from dotenv import load_dotenv
from pypdf import PdfReader
from openai import OpenAI
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import openpyxl
from openpyxl.styles import Font, PatternFill

load_dotenv()

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    timeout=25.0,
)
MODEL = os.getenv("LLM_MODEL_NAME", "gemini-3.5-flash-lite")
MIN_SCORE = int(os.getenv("MIN_MATCH_SCORE", "75"))
MAX_JOBS = int(os.getenv("MAX_JOBS_PER_SOURCE", "15"))
MAX_VACANCY_AGE_DAYS = int(os.getenv("MAX_VACANCY_AGE_DAYS", "30"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

EXCEL_PATH = Path("applications.xlsx")
PROFILE_CACHE = Path("profile.json")
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

PORTAL_EMAIL_BLACKLIST = [
    "boss@boss.az",
    "info@glorri",
    "info@jobsearch",
    "info@hellojob",
    "info@smartjob",
    "info@busy.az",
    "info@ejob.az",
    "info@work",
    "info@hrin",
    "info@azerweb",
    "info@azvak",
    "info@humanpro",
    "support@",
    "noreply",
    "no-reply",
    ".png",
    ".jpg",
    ".svg",
]

# Vakansiya saytlarının öz domenləri. Bu domenlərdən gələn e-poçtlar
# (info@work.az və s.) saytın öz e-poçtudur, şirkət e-poçtu deyil.
PORTAL_DOMAINS = (
    "glorri.az",
    "hellojob.az",
    "smartjob.az",
    "busy.az",
    "ejob.az",
    "jobsearch.az",
    "work.az",
    "boss.az",
    "hrin.az",
    "azerweb.az",
    "azvak.az",
    "humanpro.az",
)


def _is_portal_domain(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in PORTAL_DOMAINS)

DOMAIN_KEYWORDS = [
    "data", "analitik", "analyst", "hesabat", "report", "bi", "power bi",
    "tableau", "sql", "python", "maliyyə", "financial", "risk", "kredit",
    "credit", "dwh", "etl", "database", "verilən", "statist", "biznes",
    "business", "strateji", "planlama", "audit", "erp", "crm",
]

IGNORE_URL_PARTS = [
    "/login", "/register", "/signup", "/signin", "/about", "/contact",
    "/faq", "/privacy", "/terms", "/blog", "/news", "/companies",
    "/sirketler", "/kateqoriyalar", "/categories", "facebook.com",
    "instagram.com", "linkedin.com", "t.me", "youtube.com", "wa.me",
]


def is_valid_hr_email(email: str) -> bool:
    e = email.strip().lower()
    if "@" not in e or "." not in e.split("@")[-1]:
        return False
    if _is_portal_domain(e.split("@")[-1]):
        return False
    return not any(bad in e for bad in PORTAL_EMAIL_BLACKLIST)


def clean_json(raw: str):
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()
    if not raw:
        raise json.JSONDecodeError("Boş model cavabı", "", 0)

    # LLM bəzən izahlı mətn + JSON, və ya JSON-dan sonra əlavə mətn qaytarır.
    # Hər `{` və ya `[` mövqeyindən ilk tam JSON dəyərini çıxarmağa çalışırıq.
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", raw):
        try:
            return decoder.raw_decode(raw[match.start():])[0]
        except json.JSONDecodeError:
            continue
    return json.loads(raw)


def ask_llm(system_prompt: str, user_prompt: str) -> str:
    candidates = [
        MODEL,
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.5-flash",
        "gemini-flash-latest",
    ]
    models = list(dict.fromkeys(m for m in candidates if m))
    last_err = None

    for attempt in range(5):
        m = models[attempt % len(models)]
        try:
            resp = client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )
            time.sleep(2.0)
            return resp.choices[0].message.content
        except Exception as e:
            last_err = e
            wait = 5 * (attempt + 1)
            print(f"  ! {m} cavab vermədi ({type(e).__name__}), {wait} san sonra digər model yoxlanılır...")
            time.sleep(wait)
    raise last_err


# ---------------------------------------------------------
# 1. CV ANALİZİ
# ---------------------------------------------------------
def extract_pdf_links(reader: PdfReader) -> list[str]:
    uris = []
    for page in reader.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot in annots:
            obj = annot.get_object()
            action = obj.get("/A")
            if action and "/URI" in action:
                uris.append(str(action["/URI"]))
    return list(dict.fromkeys(uris))


def _profile_cache() -> Path:
    import profiles
    return profiles.profile_file()


def parse_cv_pdf(pdf_path: str) -> dict:
    print(f"[1/4] {pdf_path} analiz edilir...")
    reader = PdfReader(pdf_path)
    cv_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    pdf_links = extract_pdf_links(reader)

    sys_p = "Sən peşəkar HR və ATS analitikisən. Yalnız təmiz JSON qaytar."
    usr_p = f"""Aşağıdakı CV mətnini və PDF-dən çıxarılan hiperlinkləri analiz et.
VACİB DÜZƏLİŞ QAYDALARI:
1. Hiperlinklərdən LinkedIn və GitHub URL-lərini müvafiq sahələrə yaz.
2. 'ACHIEVEMENTS' bölməsindəki nailiyyətləri müvafiq şirkətlərin (GoldenPay OJSC, Kapital Bank, Alas/Div Academy, Azercell) 'highlights' siyahısına birləşdir (təkrarları sil).
3. 'reducing manual reporting time/effort by 115%' kimi riyazi qeyri-mümkün ifadəni 'reducing report preparation time by 96% (from 6 hours to 15 minutes)' kimi düzəlt.
4. 'GoldenPay OSJC' yazısını 'GoldenPay OJSC' kimi düzəlt.
5. Bütün tarixləri 'MM/YYYY - MM/YYYY' və ya 'MM/YYYY - Current' formatında tire ilə yaz.
6. 'education' sahəsini sadə mətn siyahısı (list of strings) kimi qaytar.

JSON strukturu:
{{
  "full_name": "",
  "email": "",
  "phone": "",
  "linkedin": "",
  "github": "",
  "location": "Baku, Azerbaijan",
  "headline": "",
  "years_experience": 3,
  "core_skills": [],
  "experience": [
    {{"company": "", "role": "", "period": "", "highlights": []}}
  ],
  "education": [],
  "languages": ["Turkish (C1)", "English (B2)"]
}}

PDF HİPERLİNKLƏR:
{pdf_links}

CV MƏTNİ:
{cv_text}
"""
    return clean_json(ask_llm(sys_p, usr_p))


def analyze_cv(pdf_path: str = "cv.pdf", force: bool = False, slug: str = "") -> dict:
    import profiles
    cache = profiles.profile_file(slug) if slug else _profile_cache()
    if cache.exists() and not force:
        print(f"[1/4] Mövcud {cache.name} istifadə olunur...")
        return json.loads(cache.read_text(encoding="utf-8"))
    source = Path(pdf_path)
    if not source.exists():
        fallback = profiles.cv_file(slug)
        if fallback.exists():
            source = fallback
    profile = parse_cv_pdf(str(source))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


# ---------------------------------------------------------
# 2. MƏNBƏ SAYTLARDAN GENİŞLƏNDİRİLMİŞ VAKANSİYA TOPLANMASI
# ---------------------------------------------------------
def collect_links_from_soup(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    found = []
    base_domain = urlparse(base_url).netloc.replace("www.", "")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full_href = urljoin(base_url, href)
        if not full_href.startswith("http"):
            continue
        parsed = urlparse(full_href)
        if base_domain not in parsed.netloc:
            continue
        low_url = full_href.lower()
        if any(bad in low_url for bad in IGNORE_URL_PARTS):
            continue
        clean_path = parsed.path.rstrip("/")
        if not clean_path or clean_path in ("/vacancies", "/vakansiyalar", "/is-elanlari"):
            continue
        txt = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if len(txt) < 3 or len(txt) > 160:
            continue
        found.append((txt, full_href))
    return found


def rank_and_dedup_links(raw_links: list[tuple[str, str]]) -> list[str]:
    seen = set()
    priority = []
    others = []
    for txt, href in raw_links:
        norm = href.split("#")[0].rstrip("/")
        if norm in seen:
            continue
        seen.add(norm)
        line = f"{txt} -> {norm}"
        haystack = f" {txt.lower()} {norm.lower()} "
        if any(kw in haystack for kw in DOMAIN_KEYWORDS):
            priority.append(line)
        else:
            others.append(line)
    return priority + others[:180]


def search_glorri_keywords(page, base_url: str) -> list[tuple[str, str]]:
    """jobs.glorri.az daxili axtarış qutusundan açar sözlərlə bütün uyğun vakansiyaları çıxarır."""
    collected = []
    keywords = ["Data", "Analitik", "Hesabat", "BI", "SQL"]
    for kw in keywords:
        try:
            search_inp = page.locator("input[placeholder*='açar söz'], input[placeholder*='Vakansiya']")
            if search_inp.count() == 0:
                break
            search_inp.first.click(timeout=3000)
            search_inp.first.fill(kw)
            search_btn = page.locator("button").filter(has_text=re.compile(r"^Axtar$|^Search$", re.I))
            if search_btn.count() > 0:
                search_btn.first.click(timeout=3000)
            else:
                page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
            for _ in range(3):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(400)
            soup = BeautifulSoup(page.content(), "html.parser")
            collected.extend(collect_links_from_soup(soup, base_url))
        except Exception:
            continue
    return collected


def extract_page_clean(page, url: str, is_listing: bool = False) -> tuple[str, list[str], list[str]]:
    for attempt in range(2):
        try:
            page.goto(url, wait_until="commit", timeout=45000)
            page.wait_for_timeout(3500)
            break
        except Exception:
            if attempt == 1:
                raise
            page.wait_for_timeout(2000)

    if is_listing:
        # SPA portallarda vakansiya linklərinin yüklənməsini gözləyirik
        try:
            page.wait_for_selector("a[href*='vacanc'], a[href*='vakansiya'], a[href*='elan']", timeout=5000)
        except Exception:
            pass

        scroll_count = 6
        for _ in range(scroll_count):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            page.wait_for_timeout(500)
            more_btn = page.locator("button, a").filter(has_text=re.compile(r"Daha çox|Load more|Hamısına bax", re.I))
            if more_btn.count() > 0:
                try:
                    more_btn.first.click(timeout=2000)
                    page.wait_for_timeout(1200)
                except Exception:
                    pass

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "svg", "noscript"]):
        tag.decompose()

    raw_links = collect_links_from_soup(soup, url) if is_listing else []
    if is_listing and "glorri.az" in url:
        raw_links.extend(search_glorri_keywords(page, url))

    ranked_links = rank_and_dedup_links(raw_links) if is_listing else []

    mailto_emails = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            addr = href.split(":", 1)[1].split("?")[0].strip()
            if is_valid_hr_email(addr):
                mailto_emails.append(addr)

    text = soup.get_text("\n", strip=True)
    norm_text = re.sub(r"\s*@\s*", "@", text)
    regex_emails = [
        e for e in re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", norm_text)
        if is_valid_hr_email(e)
    ]
    found_emails = list(dict.fromkeys(mailto_emails + regex_emails))
    return text, ranked_links, found_emails


def fold_text(value: str) -> str:
    return (
        (value or "")
        .lower()
        .replace("i̇", "i")
        .replace("ı", "i")
        .replace("ə", "e")
        .replace("ö", "o")
        .replace("ü", "u")
        .replace("ğ", "g")
        .replace("ş", "s")
        .replace("ç", "c")
    )


MONTHS = {
    "yanvar": 1, "yan": 1, "january": 1, "jan": 1,
    "fevral": 2, "fev": 2, "february": 2, "feb": 2,
    "mart": 3, "mar": 3, "march": 3,
    "aprel": 4, "apr": 4, "april": 4,
    "may": 5,
    "iyun": 6, "iyn": 6, "june": 6, "jun": 6,
    "iyul": 7, "iyl": 7, "july": 7, "jul": 7,
    "avqust": 8, "avq": 8, "august": 8, "aug": 8,
    "sentyabr": 9, "sen": 9, "september": 9, "sep": 9,
    "oktyabr": 10, "okt": 10, "october": 10, "oct": 10,
    "noyabr": 11, "noy": 11, "november": 11, "nov": 11,
    "dekabr": 12, "dek": 12, "december": 12, "dec": 12,
}
AZ_MONTH_NAMES = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avqust", "sentyabr", "oktyabr", "noyabr", "dekabr",
]
PUBLISH_MARKERS = (
    "paylasilib", "derc olunub", "tarixinde derc", "elanin qoyulma",
    "elave olunub", "yerlesdirilib",
)
DEADLINE_MARKERS = (
    "son tarix", "bitme tarixi", "son muraciet", "deadline",
)
GENERIC_LINK_TITLES = {
    "muraciet et", "apply", "vakansiyaya bax", "daha etrafli", "etrafli",
}


def month_number(token: str) -> int | None:
    return MONTHS.get(fold_text(token).strip("."))


def _safe_date(year: int, month: int, day: int) -> date | None:
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def posted_date(text: str, today: date | None = None) -> date | None:
    """Elanın açılış tarixini tapır. Son müraciət tarixini götürmür."""
    today = today or date.today()
    folded = fold_text(re.sub(r"\s+", " ", text or ""))
    occupied: list[tuple[int, int]] = []
    found: list[tuple[date, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(not (end <= left or start >= right) for left, right in occupied)

    def add(found_date: date | None, start: int, end: int):
        if not found_date or overlaps(start, end):
            return
        if not (today.year - 1) <= found_date.year <= today.year + 1:
            return
        occupied.append((start, end))
        found.append((found_date, start))

    for match in re.finditer(r"\bbu gun\b", folded):
        add(today, match.start(), match.end())
    for match in re.finditer(r"\bdunen\b", folded):
        add(today - timedelta(days=1), match.start(), match.end())
    for match in re.finditer(r"\b(\d{1,2})\s+gun evvel\b", folded):
        days = int(match.group(1))
        if 1 <= days <= 60:
            add(today - timedelta(days=days), match.start(), match.end())
    for match in re.finditer(r"\b(\d{1,2})\s+gun\b(?!\s+qal)", folded):
        days = int(match.group(1))
        if 1 <= days <= 45:
            add(today - timedelta(days=days), match.start(), match.end())

    for match in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", folded):
        add(_safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3))), match.start(), match.end())
    for match in re.finditer(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", folded):
        add(_safe_date(int(match.group(3)), int(match.group(2)), int(match.group(1))), match.start(), match.end())
    for match in re.finditer(r"\b(\d{1,2})\s+([a-z]{3,12})\s+(\d{4})\b", folded):
        month = month_number(match.group(2))
        add(_safe_date(int(match.group(3)), month, int(match.group(1))) if month else None, match.start(), match.end())
    for match in re.finditer(r"\b([a-z]{3,12})\s+(\d{1,2}),?\s+(\d{4})\b", folded):
        month = month_number(match.group(1))
        add(_safe_date(int(match.group(3)), month, int(match.group(2))) if month else None, match.start(), match.end())
    month_alt = (
        "yanvar|fevral|mart|aprel|may|iyun|iyul|avqust|sentyabr|oktyabr|noyabr|dekabr|"
        "noy|okt|dek|sen|avq|fev|yan|apr|iyn|iyl|mar|jan|feb|jun|jul|aug|sep|oct|nov"
    )
    for match in re.finditer(rf"\b(\d{{1,2}})\s+({month_alt})\b", folded):
        month = month_number(match.group(2))
        year = today.year
        parsed = _safe_date(year, month, int(match.group(1))) if month else None
        if parsed and parsed > today + timedelta(days=2):
            parsed = _safe_date(year - 1, month, int(match.group(1)))
        add(parsed, match.start(), match.end())

    publish_at = [match.start() for marker in PUBLISH_MARKERS for match in re.finditer(re.escape(marker), folded)]
    deadline_at = [match.start() for marker in DEADLINE_MARKERS for match in re.finditer(re.escape(marker), folded)]

    candidates = []
    for found_date, index in found:
        if found_date > today + timedelta(days=1):
            continue
        if any(0 <= index - marker <= 40 for marker in deadline_at):
            continue
        candidates.append((found_date, index))
    if not candidates:
        return None

    nearest = None
    for found_date, index in candidates:
        for marker in publish_at:
            distance = index - marker
            if -35 <= distance <= 80 and (nearest is None or abs(distance) < nearest[0]):
                nearest = (abs(distance), found_date)
    if nearest:
        return nearest[1]
    # Səhifənin əvvəlindəki tarix elanın öz tarixidir. Sonda gələn oxşar elanlar daha köhnə ola bilər.
    return min(candidates, key=lambda item: item[1])[0]


def format_az_date(value: date) -> str:
    return f"{value.day} {AZ_MONTH_NAMES[value.month - 1]} {value.year}"


def is_recent_vacancy(value: date, today: date | None = None) -> bool:
    today = today or date.today()
    return value >= today - timedelta(days=MAX_VACANCY_AGE_DAYS)


def cv_search_keywords(profile: dict) -> list[str]:
    blob = " ".join([
        profile.get("headline") or "",
        " ".join((item.get("role") or "") for item in profile.get("experience") or []),
        " ".join(profile.get("core_skills") or []),
    ])
    folded = fold_text(blob)
    rules = [
        ("data analyst", ["Data Analitik", "Data Analyst"]),
        ("data analit", ["Data Analitik"]),
        ("report", ["Hesabat"]),
        ("hesabat", ["Hesabat"]),
        ("power bi", ["Power BI"]),
        ("sql", ["SQL"]),
        ("financial", ["Maliyyə"]),
        ("maliyye", ["Maliyyə"]),
        ("python", ["Python"]),
        ("tableau", ["Tableau"]),
        ("business anal", ["Business Analyst"]),
        ("biznes anal", ["Biznes analitik"]),
        ("analit", ["Analitik"]),
    ]
    ordered = []
    for needle, words in rules:
        if needle in folded:
            ordered.extend(words)
    if not ordered:
        headline = (profile.get("headline") or "").split("|")[0].strip()
        if headline:
            ordered.append(headline)
    unique = []
    seen = set()
    for word in ordered:
        key = fold_text(word)
        if key in seen:
            continue
        seen.add(key)
        unique.append(word)
    return unique[:6]


def canonical_portal(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    if "hellojob.az" in host:
        return "https://www.hellojob.az/vakansiyalar"
    if "glorri.az" in host:
        return "https://jobs.glorri.az/"
    if "smartjob.az" in host:
        return "https://smartjob.az/vakansiyalar"
    if "busy.az" in host:
        return "https://busy.az/vacancies"
    if "ejob.az" in host:
        return "https://ejob.az/is-elanlari/"
    if "jobsearch.az" in host:
        return "https://jobsearch.az/vacancies"
    if "work.az" in host:
        return "https://www.work.az/vakansiyalar"
    if "boss.az" in host:
        return "https://boss.az/search/vacancies"
    if "hrin.az" in host:
        return "https://hrin.az/vakansiyalar"
    if "azerweb.az" in host:
        return "https://azerweb.az/vakansiyalar"
    if "azvak.az" in host:
        return "https://azvak.az/"
    if "humanpro.az" in host:
        return "https://humanpro.az/az/vakansiyalar"
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def unique_portals(urls: list[str]) -> list[str]:
    seen = set()
    portals = []
    for url in urls:
        portal = canonical_portal(url)
        if portal in seen:
            continue
        seen.add(portal)
        portals.append(portal)
    return portals


def is_job_link(href: str, page_url: str) -> bool:
    if not href.startswith("http"):
        return False
    parsed = urlparse(href)
    host = parsed.netloc.lower().replace("www.", "")
    base_host = urlparse(page_url).netloc.lower().replace("www.", "")
    if base_host not in host and host not in base_host:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if not parts or parts[-1].lower() in ("undefined", "create"):
        return False
    low = parsed.path.lower()
    if any(bad in low for bad in ("/login", "/register", "/add-vacancy", "/signup")):
        return False
    if "hellojob.az" in host:
        return parts[0] == "vakansiya"
    if "ejob.az" in host:
        return len(parts) >= 2 and parts[0] == "is-elani" and parts[1][:1].isdigit()
    if "humanpro.az" in host:
        return "vakansiya" in parts
    if "glorri.az" in host:
        return len(parts) >= 3 and parts[0] == "vacancies"
    if "smartjob.az" in host:
        return len(parts) >= 2 and parts[0] == "vakansiyalar"
    if "busy.az" in host:
        return parts[0] == "vacancy"
    if "jobsearch.az" in host:
        return len(parts) >= 2 and parts[0] == "vacancies"
    return len(parts) >= 2 and parts[0] in ("vacancies", "vakansiyalar", "vakansiya", "vacancy", "is-elani")


def clean_listing_title(text: str) -> str:
    title = re.split(
        r"\b\d{1,2}\s+(?:yanvar|fevral|mart|aprel|may|iyun|iyul|avqust|sentyabr|oktyabr|noyabr|dekabr)\b|\b\d{1,2}[./]\d{1,2}[./]\d{4}\b|\bbax[ıi]ş\b|\bbu gün\b|\bdünən\b|\b\d{1,2}\s+gün\b",
        text or "",
        maxsplit=1,
        flags=re.I,
    )[0]
    title = re.sub(r"\s+(?:razılaşma(?:\s+ilə)?|razılasma(?:\s+ile)?)\s+\d+\s*$", "", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip(" -–,|")[:140]


def anchor_title(anchor, href: str) -> str:
    for tag in anchor.find_all(["h1", "h2", "h3", "h4"]):
        heading = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
        if 3 <= len(heading) <= 140 and fold_text(heading) not in GENERIC_LINK_TITLES:
            return heading
    text = clean_listing_title(anchor.get_text(" ", strip=True))
    if text and fold_text(text) not in GENERIC_LINK_TITLES and len(text) >= 3:
        return text
    node = anchor
    for _ in range(6):
        node = getattr(node, "parent", None)
        if not node:
            break
        for tag in node.find_all(["h1", "h2", "h3", "h4"], recursive=False):
            heading = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
            if 3 <= len(heading) <= 140 and fold_text(heading) not in GENERIC_LINK_TITLES:
                return heading
    slug = urlparse(href).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"-\d{5,}.*$", "", slug)
    slug = re.sub(r"^\d+-", "", slug)
    return (slug.replace("-", " ").strip() or "Vakansiya")[:140]


def card_text(anchor) -> str:
    """Kartın tarixini də götürür. Başlıq tək olanda bir səviyyə yuxarı çıxır."""
    node = anchor
    best = ""
    for _ in range(6):
        node = getattr(node, "parent", None)
        if not node:
            break
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if len(text) < 20:
            continue
        if len(text) > 900:
            break
        best = text
        if posted_date(text):
            return text[:700]
    return (best or re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)))[:700]


def collect_job_links(soup: BeautifulSoup, page_url: str) -> list[dict]:
    found = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"].strip()).split("#")[0].rstrip("/")
        if not is_job_link(href, page_url) or href in seen:
            continue
        seen.add(href)
        found.append({
            "title": anchor_title(anchor, href),
            "company": "Unknown",
            "job_url": href,
            "card": card_text(anchor)[:700],
        })
    return found


def _open_page(page, url: str):
    for attempt in range(2):
        try:
            page.goto(url, wait_until="commit", timeout=45000)
            page.wait_for_timeout(2200)
            return
        except Exception:
            if attempt == 1:
                raise
            page.wait_for_timeout(1200)


def _scroll_listing(page, rounds: int = 3):
    for _ in range(rounds):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(350)


def _submit_search(page, keyword: str):
    selectors = [
        "input[name='keyword']",
        "input[name='keywords']",
        "input[name='q']",
        "input[placeholder*='açar söz']",
        "input[placeholder*='Açar söz']",
        "input[placeholder*='axtarış']",
        "input[placeholder*='Axtarış']",
    ]
    for selector in selectors:
        field = page.locator(selector)
        if field.count() == 0:
            continue
        try:
            field.first.click(timeout=2500)
            field.first.fill(keyword)
            button = page.locator("button, input[type='submit']").filter(
                has_text=re.compile(r"Axtar|Search", re.I)
            )
            if button.count() > 0:
                button.first.click(timeout=2500)
            else:
                field.first.press("Enter")
            page.wait_for_timeout(1800)
            return
        except Exception:
            continue


def _search_page(page, portal: str, keyword: str):
    host = urlparse(portal).netloc.lower()
    query = quote_plus(keyword)
    if "hellojob.az" in host:
        _open_page(page, f"https://www.hellojob.az/vakansiyalar?q={query}")
    elif "glorri.az" in host:
        _open_page(page, f"https://jobs.glorri.az/?keyword={query}")
        _submit_search(page, keyword)
    elif "ejob.az" in host:
        _open_page(page, "https://ejob.az/is-elanlari/")
        _submit_search(page, keyword)
    elif "work.az" in host:
        _open_page(page, f"https://www.work.az/vakansiyalar?search={query}")
    elif "boss.az" in host:
        _open_page(page, f"https://boss.az/search/vacancies?searchTerm={query}&sortBy=DATE")
    elif "hrin.az" in host:
        _open_page(page, f"https://hrin.az/vakansiyalar?q={query}")
    elif "humanpro.az" in host:
        _open_page(page, f"https://humanpro.az/az/vakansiyalar?keyword={query}")
    elif "azvak.az" in host:
        _open_page(page, "https://azvak.az/")
        field = page.locator("input[placeholder*='Vəzifə'], input[placeholder*='vezife']")
        if field.count():
            try:
                field.first.click(timeout=2500)
                field.first.fill(keyword)
                field.first.press("Enter")
                page.wait_for_timeout(1500)
            except Exception:
                pass
    else:
        _open_page(page, portal)
        before = page.url
        _submit_search(page, keyword)
        if "undefined" in page.url:
            _open_page(page, before)
    _scroll_listing(page)


def _level_blocked(title: str) -> bool:
    folded = fold_text(title)
    return any(word in folded for word in ("intern", "tecrube", "staj", "trainee", "junior"))


def select_job_links(profile: dict, portal: str, links: list[dict]) -> list[dict]:
    if len(links) <= MAX_JOBS:
        return links
    roles = ", ".join(item.get("role", "") for item in profile.get("experience", []) if item.get("role"))
    skills = ", ".join(profile.get("core_skills", [])[:15])
    lines = [f"{item['title']} -> {item['job_url']}" for item in links[:350]]
    system = "Sən veb məlumat çıxaran parsersən. Yalnız təmiz JSON massivi qaytar."
    user = f"""Bu axtarış nəticəsindən ({portal}) namizədin profilinə uyğun konkret vakansiya linklərini seç (ən çox {MAX_JOBS * 2} ədəd).
Ana səhifə, kateqoriya və ümumi siyahı linklərini GÖTÜRMƏ.
Namizəd: {profile.get("headline", "")} | Rollar: {roles} | Bacarıqlar: {skills}.
Intern, Təcrübəçi, Stajyer, Trainee, Junior və aidiyyətsiz sahələri ÇIXAR.

JSON:
[{{"title": "Vəzifə adı", "company": "Şirkət adı", "job_url": "Tam URL"}}]

LİNKLƏR:
{chr(10).join(lines)}
"""
    try:
        picked = clean_json(ask_llm(system, user))
    except Exception:
        return links[:MAX_JOBS]
    by_url = {item["job_url"]: item for item in links}
    chosen = []
    for row in picked:
        url = str(row.get("job_url", "")).split("#")[0].rstrip("/")
        base = by_url.get(url)
        if not base:
            continue
        chosen.append({
            **base,
            "title": row.get("title") or base["title"],
            "company": row.get("company") or "Unknown",
        })
    return chosen[: MAX_JOBS * 2] or links[:MAX_JOBS]


LOOSE_SEARCH_HOSTS = ("humanpro.az", "work.az", "azvak.az", "ejob.az")


def extract_salary(text: str) -> str:
    """Elanın öz mətnində yazılmış maaşı qaytarır. Sayt menyusundakı «Maaş» sözünü götürmür."""
    lead = vacancy_lead(text)
    pattern = re.compile(
        r"(?:əmək\s*haqq[ıi]|emek\s*haqq[ıi]|maaş|maas|salary)\s*[:\-–—]?\s*([^\n|]{1,90})",
        re.I,
    )
    found = []
    for match in pattern.finditer(lead):
        value = re.sub(r"\s+", " ", match.group(1)).strip(" .-|")
        value = re.split(r"\s+\(|\s{2,}", value, maxsplit=1)[0].strip(" .")
        folded = fold_text(value)
        if not value or "kalkulyator" in folded:
            continue
        if salary_signature(value) == () and "razi" not in folded:
            continue
        if not re.search(r"\d", value) and "razi" not in folded:
            continue
        value = re.sub(r"\s+araligindadir\.?$", "", value, flags=re.I)
        found.append(value[:70])
    if not found:
        return ""

    def rank(value: str):
        folded = fold_text(value)
        return (int("azn" in folded or "₼" in value or "razi" in folded), len(value))

    return sorted(found, key=rank)[-1]


def salary_signature(value: str) -> tuple[str, ...]:
    """Rəqəmsal maaşı müqayisə üçün saxlayır. Razılaşma və boş dəyər eyni elanı ayırmır."""
    folded = fold_text(value or "")
    if not folded or "razi" in folded:
        return ()
    nums = tuple(re.findall(r"\d+", folded))
    if nums in {("0", "500"), ("0",)}:
        return ()
    return nums


def vacancy_identity(job: dict) -> tuple | None:
    company = fold_text(re.sub(r"\s+", " ", job.get("company") or "")).strip()
    title = fold_text(re.sub(r"\s+", " ", job.get("title") or "")).strip()
    if not title or company in ("", "unknown", "namelum"):
        return None
    return (company, title, salary_signature(job.get("salary") or ""))


def same_vacancy(left: dict, right: dict) -> bool:
    key = vacancy_identity(left)
    return bool(key) and key == vacancy_identity(right)


def _absorb_vacancy(base: dict, extra: dict):
    urls = []
    for url in (base.get("job_urls") or [base.get("job_url")]) + (extra.get("job_urls") or [extra.get("job_url")]):
        clean = (url or "").rstrip("/")
        if clean and clean not in urls:
            urls.append(clean)
    base["job_urls"] = urls
    if urls:
        base["job_url"] = urls[0]
    if len(extra.get("raw_text") or "") > len(base.get("raw_text") or ""):
        base["raw_text"] = extra["raw_text"]
    if len(salary_signature(extra.get("salary") or "")) > len(salary_signature(base.get("salary") or "")):
        base["salary"] = extra["salary"]
    if not base.get("found_emails") and extra.get("found_emails"):
        base["found_emails"] = extra["found_emails"]
    if not base.get("posted_at") and extra.get("posted_at"):
        base["posted_at"] = extra["posted_at"]


def merge_same_vacancies(jobs: list[dict]) -> list[dict]:
    """Şirkət, vəzifə və maaş eyni olan elanları birləşdirir. Linklər job_urls-da qalır."""
    merged = []
    for job in jobs:
        item = dict(job)
        item["salary"] = item.get("salary") or extract_salary(item.get("raw_text") or "")
        item["job_urls"] = [
            (url or "").rstrip("/")
            for url in (item.get("job_urls") or [item.get("job_url")])
            if url
        ]
        placed = False
        for base in merged:
            if same_vacancy(base, item):
                _absorb_vacancy(base, item)
                placed = True
                break
        if not placed:
            merged.append(item)
    return merged


def vacancy_lead(text: str) -> str:
    """Oxşar elanlar blokundan əvvəlki hissə. Oradakı köhnə tarixlər bu elana aid deyil."""
    return re.split(
        r"oxşar vakansiya|oxsar vakansiya|bənzər vakansiya|benzer vakansiya|similar vacanc",
        text or "",
        maxsplit=1,
        flags=re.I,
    )[0]


def _keyword_hit(text: str, keyword: str) -> bool:
    folded = fold_text(text)
    tokens = [part for part in re.split(r"[^a-z0-9]+", fold_text(keyword)) if len(part) >= 3]
    if not tokens:
        return True
    return any(token in folded for token in tokens)


def _http_json(url: str):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _plain_html(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True))


def _specific_job_url(url: str) -> str:
    if not str(url).startswith("http"):
        return ""
    path = urlparse(url).path.rstrip("/").lower()
    if path in ("", "/home", "/index", "/index.html", "/az", "/en"):
        return ""
    return url


def _ejob_id(url: str) -> int:
    match = re.search(r"/is-elani/(\d+)", url)
    return int(match.group(1)) if match else 0


def _busy_links(keywords: list[str], today: date) -> list[dict]:
    found = []
    seen = set()
    for keyword in keywords:
        try:
            data = _http_json(
                "https://busy.az/api/bff/api/vacancies"
                f"?page=1&per_page=24&keyword={quote_plus(keyword)}"
            )
        except Exception:
            continue
        for row in data.get("vacancies") or []:
            job_id = row.get("id")
            slug = row.get("slug") or "vakansiya"
            if not job_id or job_id in seen:
                continue
            opened = posted_date((row.get("created_at") or row.get("published") or "")[:10], today)
            if not opened or not is_recent_vacancy(opened, today):
                continue
            seen.add(job_id)
            company = ((row.get("company") or {}).get("title") or "Unknown").strip()
            found.append({
                "title": row.get("job_title") or "Vakansiya",
                "company": company,
                "job_url": f"https://busy.az/vacancy/{job_id}/{slug}",
                "card": (row.get("created_at") or "")[:10],
                "known_date": opened,
            })
    found.sort(key=lambda item: item["known_date"], reverse=True)
    return found[:MAX_JOBS]


def _azerweb_categories(keyword: str) -> list[str]:
    folded = fold_text(keyword)
    categories = []
    if any(word in folded for word in ("maliyye", "hesabat", "muhasib")):
        categories.append("muhasibat-ve-maliyye")
    if any(word in folded for word in ("data", "sql", "power", "python", "analit")):
        categories.append("it-ve-texnologiya")
    if folded in ("analitik", "analit") or "market" in folded:
        categories.append("marketinq")
    return list(dict.fromkeys(categories)) or ["it-ve-texnologiya"]


def _azerweb_links(keywords: list[str], today: date) -> list[dict]:
    found = []
    seen = set()
    for keyword in keywords:
        for slug in _azerweb_categories(keyword):
            for page_no in (1, 2):
                try:
                    data = _http_json(f"https://azerweb.az/api/professions/{slug}/?page={page_no}")
                except Exception:
                    continue
                for row in data.get("items") or []:
                    title = row.get("title") or ""
                    job_id = row.get("id")
                    if not job_id or job_id in seen or not _keyword_hit(title, keyword):
                        continue
                    opened = posted_date(row.get("posted_date") or "", today)
                    if not opened or not is_recent_vacancy(opened, today):
                        continue
                    seen.add(job_id)
                    found.append({
                        "title": title,
                        "company": row.get("company") or "Unknown",
                        "job_url": "",
                        "card": row.get("posted_date") or "",
                        "known_date": opened,
                        "kind": row.get("kind") or "partner",
                        "remote_id": job_id,
                        "category": slug,
                    })
    found.sort(key=lambda item: item["known_date"], reverse=True)
    detailed = []
    for item in found[:MAX_JOBS]:
        try:
            payload = _http_json(
                f"https://azerweb.az/api/announcements/{item['kind']}/{item['remote_id']}/"
            )
        except Exception:
            continue
        link = _specific_job_url(payload.get("apply_url") or "") or _specific_job_url(payload.get("original_url") or "")
        if not link:
            link = f"https://azerweb.az/vakansiyalar/{item['category']}#{item['remote_id']}"
        email = (payload.get("apply_email") or "").strip()
        detailed.append({
            **item,
            "title": payload.get("title") or item["title"],
            "company": payload.get("company") or item["company"],
            "job_url": link.split("?")[0].rstrip("/"),
            "detail_text": _plain_html(payload.get("description") or ""),
            "found_emails": [email] if email and is_valid_hr_email(email) else [],
        })
    return detailed


def collect_source_jobs(page, profile: dict, source_urls: list[str], skip_urls: set | None = None, log=print, should_cancel=None, on_progress=None) -> list[dict]:
    """Hər mənbədə CV sözləri ilə axtarış edir və son 30 gündən köhnə elanları atır."""
    skip_urls = {url.rstrip("/") for url in (skip_urls or set())}
    should_cancel = should_cancel or (lambda: False)
    keywords = cv_search_keywords(profile)
    portals = unique_portals(source_urls)
    today = date.today()
    cutoff = today - timedelta(days=MAX_VACANCY_AGE_DAYS)
    log(f"CV axtarış sözləri: {', '.join(keywords)}")
    log(f"Tarix şərti: {format_az_date(cutoff)} və sonra açılan elanlar")

    collected = []
    seen = set(skip_urls)
    for index, portal in enumerate(portals):
        if should_cancel():
            break
        if on_progress:
            on_progress(10 + int((index / max(len(portals), 1)) * 55))
        host = urlparse(portal).netloc.replace("www.", "")
        log(f"Mənbə ({index + 1}/{len(portals)}): {host}")
        found = []
        found_urls = set()
        if "busy.az" in host:
            log("  Axtarış: vakansiya API")
            try:
                found = _busy_links(keywords, today)
            except Exception as exc:
                log(f"  ! Axtarış açılmadı: {exc}")
            log(f"    -> {len(found)} son {MAX_VACANCY_AGE_DAYS} günün elanı")
        elif "azerweb.az" in host:
            log("  Axtarış: kateqoriya API")
            try:
                found = _azerweb_links(keywords, today)
            except Exception as exc:
                log(f"  ! Axtarış açılmadı: {exc}")
            log(f"    -> {len(found)} son {MAX_VACANCY_AGE_DAYS} günün elanı")
        else:
            loose = any(name in host for name in LOOSE_SEARCH_HOSTS)
            for keyword in keywords:
                if should_cancel():
                    break
                log(f"  Axtarış: {keyword}")
                try:
                    _search_page(page, portal, keyword)
                    soup = BeautifulSoup(page.content(), "html.parser")
                    for tag in soup(["script", "style", "svg", "noscript"]):
                        tag.decompose()
                    links = collect_job_links(soup, page.url or portal)
                except Exception as exc:
                    log(f"  ! Axtarış açılmadı ({keyword}): {exc}")
                    continue
                fresh = []
                for item in links:
                    if item["job_url"] in found_urls:
                        continue
                    if loose and not _keyword_hit(f"{item.get('title', '')} {item.get('card', '')}", keyword):
                        continue
                    found_urls.add(item["job_url"])
                    fresh.append(item)
                found.extend(fresh)
                log(f"    -> {len(fresh)} yeni link")

        if not found:
            log("  -> uyğun link tapılmadı")
            continue

        skipped_old = 0
        recent = []
        for item in found:
            card_date = item.get("known_date") or posted_date(item.get("card") or "", today)
            item["known_date"] = card_date
            if card_date and not is_recent_vacancy(card_date, today):
                skipped_old += 1
                seen.add(item["job_url"].rstrip("/"))
                continue
            recent.append(item)
        if skipped_old:
            log(f"    -> {skipped_old} köhnə kart açılmadan atıldı")

        if "ejob.az" in host:
            recent.sort(key=lambda item: _ejob_id(item["job_url"]), reverse=True)
            chosen = recent[:8]
        elif len(recent) <= MAX_JOBS:
            chosen = recent
        else:
            chosen = select_job_links(profile, portal, recent)

        kept_here = 0
        old_streak = 0
        for item in chosen:
            if should_cancel() or kept_here >= MAX_JOBS:
                break
            title = item.get("title") or "Vakansiya"
            job_url = (item.get("job_url") or "").rstrip("/")
            if not job_url or job_url in seen or _level_blocked(title):
                if job_url:
                    seen.add(job_url)
                continue
            seen.add(job_url)
            if item.get("detail_text"):
                detail_text = item["detail_text"]
                emails = item.get("found_emails") or []
            else:
                log(f"    Oxunur: {title[:50]}")
                try:
                    detail_text, _, emails = extract_page_clean(page, job_url, is_listing=False)
                except Exception as exc:
                    log(f"    ! Səhifə xətası: {exc}")
                    continue
            if item.get("detail_text") or "busy.az" in host:
                opened = item.get("known_date") or posted_date(vacancy_lead(detail_text), today)
            else:
                opened = posted_date(vacancy_lead(detail_text), today) or item.get("known_date")
            if not opened:
                log(f"    Keçildi (tarix yoxdur): {title[:50]}")
                continue
            if not is_recent_vacancy(opened, today):
                log(f"    Keçildi ({format_az_date(opened)}): {title[:50]}")
                old_streak += 1
                if "ejob.az" in host and old_streak >= 2:
                    log("    -> köhnə arxiv başladı, qalan eJob elanları açılmır")
                    break
                continue
            old_streak = 0
            kept_here += 1
            collected.append({
                "source": portal,
                "job_url": job_url,
                "title": title,
                "company": item.get("company") or "Unknown",
                "found_emails": emails,
                "raw_text": detail_text[:4500],
                "posted_at": format_az_date(opened),
            })
        log(f"  -> {kept_here} son {MAX_VACANCY_AGE_DAYS} günün elanı saxlanıldı")
    merged = merge_same_vacancies(collected)
    if len(merged) != len(collected):
        log(f"Eyni elanlar birləşdirildi: {len(collected)} → {len(merged)}")
    return merged


def scrape_sources(browser, profile: dict, sources_file: str = "sources.txt", skip_urls: set = None) -> list[dict]:
    urls = [
        line.strip()
        for line in Path(sources_file).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    page = browser.new_page(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    try:
        return collect_source_jobs(page, profile, urls, skip_urls)
    finally:
        page.close()


# ---------------------------------------------------------
# 3. QİYMƏTLƏNDİRMƏ, FƏRDİ PDF CV VƏ YALNIZ E-POÇTLA MÜRACİƏT
# ---------------------------------------------------------
def score_jobs_batch(profile: dict, jobs: list[dict]) -> dict[int, dict]:
    if not jobs:
        return {}

    all_scores = {}
    chunk_size = 12
    for start in range(0, len(jobs), chunk_size):
        chunk = jobs[start:start + chunk_size]
        compact_jobs = [
            {
                "id": start + i,
                "title": j["title"],
                "company": j["company"],
                "job_url": j["job_url"],
                "found_emails": j["found_emails"],
                "snippet": j["raw_text"][:4500],
            }
            for i, j in enumerate(chunk)
        ]

        sys_p = "Sən HR və ATS qiymətləndirmə sistemisən. Yalnız təmiz JSON massivi qaytar."
        usr_p = f"""Namizəd profilinə əsasən aşağıdakı vakansiyaların hər birinə 0-100 arası uyğunluq balı ver. VACİB SƏVİYYƏ FİLTRİ: Namizəd 3+ il təcrübəli Middle/Senior mütəxəssisdir. Əgər vakansiya Intern, Təcrübəçi, Stajyer, Trainee, Junior və ya sırf Mühasib (Chief Accountant / mühasibat uçotu) vəzifəsidirsə, açar sözlər uyğun gəlsə belə maksimum 35 bal ver.
Əgər vakansiya təsvirində və ya found_emails siyahısında HR / şirkət e-poçtu varsa hr_email sahəsinə həmin e-poçtu yaz, yoxdursa boş saxla ("").

JSON formatı:
[
  {{
    "id": 0,
    "company": "Dəqiqləşdirilmiş şirkət adı",
    "title": "Dəqiqləşdirilmiş vəzifə adı",
    "match_score": 0,
    "hr_email": ""
  }}
]

NAMİZƏD XÜLASƏSİ:
Vəzifə: {profile.get('headline')} | Təcrübə: {profile.get('years_experience')} il
Bacarıqlar: {', '.join(profile.get('core_skills', []))}
Keçmiş rollar: {[e.get('role') for e in profile.get('experience', [])]}

VAKANSİYALAR:
{json.dumps(compact_jobs, ensure_ascii=False)}
"""
        results = clean_json(ask_llm(sys_p, usr_p))
        for r in results:
            all_scores[int(r["id"])] = r
    return all_scores


def tailor_single_job(profile: dict, job: dict) -> dict:
    sys_p = (
        "Sən executive karyera məsləhətçisi və ATS mütəxəssisisən. "
        "Namizədin real təcrübəsini UYDURMADAN vakansiya təsvirindəki (description) "
        "tələblərə və açar sözlərə uyğunlaşdır. Yalnız təmiz JSON qaytar."
    )
    usr_p = f"""Vakansiya təsvirini dərindən analiz et və namizədin BÜTÜN CV məzmununu həmin təsvirə uyğunlaşdır.

VACİB QAYDALAR:
1. Vahid dil: Vakansiya təsvirinin dilini müəyyən et (Azərbaycan və ya İngilis dili). CV-nin bütün bölmə başlıqları, xülasəsi, iş təcrübəsi bəndləri və cover letter EYNİ dildə olmalıdır.
2. Description-a uyğun iş təcrübəsi: Vakansiyada tələb olunan əsas bacarıqları namizədin real iş təcrübəsi bəndlərində önə çək.
3. Rəqəm və fakt dəqiqliyi: Heç bir saxta rəqəm uydurma. '115%' yazma, real göstəricini ('96%' / '6 saatdan 15 dəqiqəyə') işlət.
4. Səliqə: CV 1 tam A4 səhifəsinə sığsın. Bəndlərin əvvəlinə '•' və ya '-' işarəsi QOYMA.

JSON formatı:
{{
  "language": "az|en",
  "headers": {{
    "summary": "PEŞƏKAR XÜLASƏ və ya PROFESSIONAL SUMMARY",
    "skills": "ƏSAS BACARIQLAR və ya KEY SKILLS",
    "experience": "İŞ TƏCRÜBƏSİ və ya EXPERIENCE",
    "education": "TƏHSİL və ya EDUCATION",
    "languages": "DİLLƏR və ya LANGUAGES"
  }},
  "email_subject": "",
  "cover_letter": "HR-a göndəriləcək peşəkar məktub (vakansiyanın dilində)",
  "tailored_summary": "Vakansiya təsvirinə uyğunlaşdırılmış 3-4 cümləlik güclü peşəkar xülasə",
  "highlighted_skills": ["Vakansiya təsvirindəki açar sözlərə uyğun 12-15 bacarıq/alət"],
  "tailored_experience": [
    {{
      "company": "",
      "role": "",
      "period": "",
      "highlights": ["Vakansiya təsvirinə uyğunlaşdırılmış təmiz bəndlər"]
    }}
  ],
  "education_text": "Təhsil məlumatı (vakansiyanın dilində)",
  "languages_text": "Dil bilikləri (vakansiyanın dilində)"
}}

NAMİZƏD PROFİLİ:
{json.dumps(profile, ensure_ascii=False)}

VAKANSİYA TƏSVİRİ:
Başlıq: {job.get('title')} | Şirkət: {job.get('company')}
Mətn:
{job.get('raw_text', '')}
"""
    return clean_json(ask_llm(sys_p, usr_p))


def render_tailored_pdf(browser, profile: dict, tailored: dict, pdf_out: Path):
    headers = tailored.get("headers") or {}
    experiences = tailored.get("tailored_experience") or profile.get("experience", [])

    exp_html = ""
    for item in experiences:
        cleaned_bullets = [re.sub(r"^[•\-\*]\s*", "", str(h).strip()) for h in item.get("highlights", []) if str(h).strip()]
        bullets = "".join(f"<li>{h}</li>" for h in cleaned_bullets)
        exp_html += f"""
        <div class="job">
            <div class="job-head">
                <div><strong>{item.get('role', '')}</strong> — {item.get('company', '')}</div>
                <span>{item.get('period', '')}</span>
            </div>
            <ul>{bullets}</ul>
        </div>
        """

    skills_str = " • ".join(tailored.get("highlighted_skills") or profile.get("core_skills", []))
    edu_text = tailored.get("education_text") or "<br>".join(str(e) for e in profile.get("education", []))
    lang_text = tailored.get("languages_text") or " • ".join(profile.get("languages", []))

    contact_parts = [
        profile.get("location", "Baku, Azerbaijan"),
        profile.get("email", ""),
        profile.get("phone", ""),
        profile.get("linkedin", ""),
        profile.get("github", ""),
    ]
    contact_line = " | ".join(p.strip() for p in contact_parts if p and p.strip())

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 22px 30px; }}
  body {{ font-family: 'DejaVu Sans', Arial, sans-serif; color: #1f2937; margin: 0; font-size: 9.2pt; line-height: 1.36; }}
  h1 {{ margin: 0 0 3px 0; font-size: 18pt; color: #0f172a; letter-spacing: 0.3px; }}
  .contact {{ font-size: 8.6pt; color: #475569; margin-bottom: 10px; border-bottom: 1.5px solid #1d4ed8; padding-bottom: 6px; }}
  h2 {{ font-size: 10pt; text-transform: uppercase; color: #1d4ed8; margin: 9px 0 4px 0; letter-spacing: 0.6px; border-bottom: 0.5px solid #cbd5e1; padding-bottom: 2px; }}
  p {{ margin: 3px 0 6px 0; }}
  .job {{ margin-bottom: 7px; }}
  .job-head {{ display: flex; justify-content: space-between; align-items: baseline; font-size: 9.5pt; color: #0f172a; }}
  .job-head span {{ color: #64748b; font-size: 8.6pt; font-weight: 600; }}
  ul {{ margin: 2px 0 0 16px; padding: 0; }}
  li {{ margin-bottom: 2px; }}
  .bottom-grid {{ display: flex; justify-content: space-between; gap: 16px; margin-top: 4px; }}
  .bottom-col {{ flex: 1; }}
</style>
</head>
<body>
  <h1>{profile.get('full_name', '')}</h1>
  <div class="contact">{contact_line}</div>

  <h2>{headers.get('summary', 'PROFESSIONAL SUMMARY')}</h2>
  <p>{tailored.get('tailored_summary', '')}</p>

  <h2>{headers.get('skills', 'KEY SKILLS')}</h2>
  <p>{skills_str}</p>

  <h2>{headers.get('experience', 'EXPERIENCE')}</h2>
  {exp_html}

  <div class="bottom-grid">
    <div class="bottom-col">
      <h2>{headers.get('education', 'EDUCATION')}</h2>
      <p>{edu_text}</p>
    </div>
    <div class="bottom-col">
      <h2>{headers.get('languages', 'LANGUAGES')}</h2>
      <p>{lang_text}</p>
    </div>
  </div>
</body>
</html>
"""
    page = browser.new_page()
    page.set_content(html_doc)
    page.pdf(path=str(pdf_out), format="A4", print_background=True)
    page.close()


def apply_via_email(to_email: str, subject: str, body: str, pdf_path: Path) -> str:
    if DRY_RUN:
        return f"Dry-Run (Email Ready -> {to_email})"

    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_EMAIL")
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with open(pdf_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=pdf_path.name)

    with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587"))) as server:
        server.starttls()
        server.login(os.getenv("SMTP_EMAIL"), os.getenv("SMTP_PASSWORD"))
        server.send_message(msg)
    return f"Applied (Email Sent -> {to_email})"


def find_hidden_email_on_page(browser, job_url: str) -> str:
    """Səhifədəki 'Müraciət et' düyməsinə kliklədikdə modalda açılan HR e-poçtu varsa tapır."""
    page = browser.new_page(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    try:
        page.goto(job_url, wait_until="commit", timeout=35000)
        page.wait_for_timeout(2500)

        apply_btn = page.locator("button, a, [role='button']").filter(has_text=re.compile(r"Müraciət|Apply", re.I))
        if apply_btn.count() > 0:
            try:
                apply_btn.first.click(timeout=4000)
                page.wait_for_timeout(2000)
            except Exception:
                pass

        mailto_hrefs = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href^="mailto:"]')).map(a => a.href.replace(/^mailto:/i, '').split('?')[0])"""
        )
        body_text = re.sub(r"\s*@\s*", "@", page.inner_text("body"))
        candidates = mailto_hrefs + re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", body_text)
        for c in candidates:
            if is_valid_hr_email(c):
                return c.strip()
        return ""
    except Exception:
        return ""
    finally:
        page.close()


# ---------------------------------------------------------
# 4. EXCEL HESABAT SİSTEMİ
# ---------------------------------------------------------
def init_excel():
    if EXCEL_PATH.exists():
        wb = openpyxl.load_workbook(EXCEL_PATH)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Applications"
        headers = [
            "Tarix", "Mənbə", "Şirkət", "Vəzifə", "Uyğunluq %",
            "Kanal", "Hədəf (Email/Link)", "Status", "CV Faylı", "Vakansiya URL"
        ]
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1E3A8A")
    return wb, ws


def get_existing_urls(ws) -> set:
    urls = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if len(row) >= 10 and row[9]:
            urls.add(str(row[9]).strip().rstrip("/"))
    return urls


def run_pipeline():
    profile = analyze_cv("cv.pdf")
    wb, ws = init_excel()
    existing_urls = get_existing_urls(ws)

    with sync_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled", "--disable-http2"],
        }
        if os.path.exists("/usr/bin/chromium"):
            launch_kwargs["executable_path"] = "/usr/bin/chromium"
        browser = p.chromium.launch(**launch_kwargs)

        jobs = scrape_sources(browser, profile, "sources.txt", existing_urls)
        print(f"[3/4] {len(jobs)} yeni vakansiya toplu qiymətləndirilir (DRY_RUN={DRY_RUN})...")

        scores_map = score_jobs_batch(profile, jobs)

        for idx, job in enumerate(jobs):
            info = scores_map.get(idx, {})
            company = (info.get("company") or job.get("company") or "Unknown").strip()
            title = (info.get("title") or job.get("title") or "Unknown").strip()
            score = int(info.get("match_score", 0))
            job_url = job["job_url"]

            safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{company}_{title}")[:50]
            pdf_file = OUTPUT_DIR / f"CV_{safe_name}.pdf"

            if score >= MIN_SCORE:
                tailored = tailor_single_job(profile, job)
                render_tailored_pdf(browser, profile, tailored, pdf_file)

                llm_email = str(info.get("hr_email", "")).strip()
                hr_email = ""
                if is_valid_hr_email(llm_email):
                    hr_email = llm_email
                elif job["found_emails"]:
                    hr_email = job["found_emails"][0]
                else:
                    hr_email = find_hidden_email_on_page(browser, job_url)

                if hr_email:
                    channel = "email"
                    target = hr_email
                    status = apply_via_email(
                        hr_email,
                        tailored.get("email_subject", f"Application: {title}"),
                        tailored.get("cover_letter", ""),
                        pdf_file,
                    )
                else:
                    channel = "manual"
                    target = job_url
                    status = "CV Ready (Manual Apply)"

                cv_col = str(pdf_file)
            else:
                channel = "-"
                target = job_url
                status = f"Skipped (<{MIN_SCORE}%)"
                cv_col = "-"

            ws.append([
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                job["source"],
                company,
                title,
                score,
                channel,
                target,
                status,
                cv_col,
                job_url,
            ])
            existing_urls.add(job_url)
            print(f"  + [{score}%] {company} — {title} -> {status}")

        browser.close()

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 45)

    wb.save(EXCEL_PATH)
    print(f"[4/4] Tamamlandı! Hesabat {EXCEL_PATH} faylına yazıldı.")


if __name__ == "__main__":
    run_pipeline()