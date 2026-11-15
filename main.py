import os
import re
import json
import time
import smtplib
from datetime import datetime
from pathlib import Path
from email.message import EmailMessage
from urllib.parse import urljoin, urlparse

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
    "support@",
    "noreply",
    "no-reply",
    ".png",
    ".jpg",
    ".svg",
]

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
    return not any(bad in e for bad in PORTAL_EMAIL_BLACKLIST)


def clean_json(raw: str):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
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


def analyze_cv(pdf_path: str = "cv.pdf") -> dict:
    if PROFILE_CACHE.exists():
        print("[1/4] Mövcud profile.json istifadə olunur...")
        return json.loads(PROFILE_CACHE.read_text(encoding="utf-8"))

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
    profile = clean_json(ask_llm(sys_p, usr_p))
    PROFILE_CACHE.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
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


def scrape_sources(browser, profile: dict, sources_file: str = "sources.txt", skip_urls: set = None) -> list[dict]:
    skip_urls = skip_urls or set()
    urls = [
        line.strip()
        for line in Path(sources_file).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    collected = []
    seen_job_urls = set(skip_urls)
    page = browser.new_page(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    roles_str = ", ".join([e.get("role", "") for e in profile.get("experience", []) if e.get("role")])
    skills_str = ", ".join(profile.get("core_skills", [])[:15])

    for src_url in urls:
        print(f"[2/4] Mənbə skan edilir: {src_url}")
        try:
            _, links, _ = extract_page_clean(page, src_url, is_listing=True)
        except Exception as e:
            print(f"  ! Mənbə açılmadı ({src_url}): {e}")
            continue

        if not links:
            print("  -> 0 vakansiya linki tapıldı:")
            continue

        sys_p = "Sən veb məlumat çıxaran parsersən. Yalnız təmiz JSON massivi qaytar."
        usr_p = f"""Bu səhifədən ({src_url}) namizədin profilinə az da olsa uyğun gələn BÜTÜN iş elanlarının linklərini seç (ən çox {MAX_JOBS} ədəd).
Ana səhifə, kateqoriya və ya ümumi siyahı linklərini GÖTÜRMƏ.
Namizədin profili: {profile.get("headline", "")} | Rollar: {roles_str} | Bacarıqlar: {skills_str}.
Yalnız tamamilə aidiyyətsiz sahələri (sürücü, krupye, satıcı, fəhlə, xadimə, aşpaz, kassir və s.) çıxar. Uyğun elan yoxdursa [] qaytar.

JSON formatı:
[
  {{
    "title": "Vəzifə adı",
    "company": "Şirkət adı (varsa)",
    "job_url": "Tam vakansiya URL-i"
  }}
]

LİNKLƏR:
{chr(10).join(links[:350])}
"""
        try:
            listings = clean_json(ask_llm(sys_p, usr_p))[:MAX_JOBS]
            print(f"  -> {len(listings)} vakansiya linki tapıldı:")
        except Exception as e:
            print(f"  ! Siyahı parse xətası: {e}")
            continue

        for idx, item in enumerate(listings, 1):
            j_title_low = item.get("title", "").lower()
            if any(w in j_title_low for w in ["intern", "təcrübə", "staj", "trainee", "junior"]):
                continue
            j_url = item.get("job_url", "").strip()
            clean_j = j_url.split("#")[0].rstrip("/")
            if (
                not clean_j
                or clean_j in seen_job_urls
                or clean_j == src_url.rstrip("/")
                or clean_j.endswith(("glorri.az", "/vacancies", "/vakansiyalar", "/is-elanlari"))
            ):
                continue
            seen_job_urls.add(clean_j)
            print(f"     [{idx}/{len(listings)}] Oxunur: {item.get('title', '')[:45]}")
            try:
                detail_text, _, emails = extract_page_clean(page, clean_j, is_listing=False)
                collected.append({
                    "source": src_url,
                    "job_url": clean_j,
                    "title": item.get("title", "Unknown"),
                    "company": item.get("company", "Unknown"),
                    "found_emails": emails,
                    "raw_text": detail_text[:4500],
                })
            except Exception as e:
                print(f"     ! Səhifə açılmadı ({clean_j}): {e}")

    page.close()
    return collected


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