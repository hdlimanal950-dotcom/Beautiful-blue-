"""
============================================================
  SMART PUBLISHING ENGINE — Render-Ready Production Server
  Supports: English (Cooking Articles)
  Designed for: render.com (Web Service — Free/Paid)
============================================================
"""

import os
import sys
import json
import time
import random
import logging
import threading
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, request
from functools import wraps
from concurrent.futures import ThreadPoolExecutor

# ============================================================
# 📂 ROOT PATH — Works anywhere on Render or locally
# ============================================================
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ============================================================
# 📝 LOGGING — Structured, production-grade
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(DATA_DIR / "system.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("PublishEngine")

# ============================================================
# ⚙️  ENVIRONMENT CONFIG — Render uses ENV variables
# ============================================================

class Config:
    """
    All secrets pulled from environment variables.
    Fallback to .env or hardcoded defaults for local dev.
    """
    # Pipedream webhook endpoint (الجسر)
    PIPEDREAM_WEBHOOK: str = os.getenv("PIPEDREAM_WEBHOOK", "https://eo4qdz87j26q8wo.m.pipedream.net")
    
    # --- Quota & Timing ---
    QUOTA_MIN:      int = int(os.getenv("QUOTA_MIN",  "10"))
    QUOTA_MAX:      int = int(os.getenv("QUOTA_MAX",  "15"))
    INTERVAL_MIN:   int = int(os.getenv("INTERVAL_MIN","60"))   # minutes
    INTERVAL_MAX:   int = int(os.getenv("INTERVAL_MAX","90"))   # minutes

    # --- HTTP Retry ---
    HTTP_RETRIES:   int = int(os.getenv("HTTP_RETRIES","3"))
    HTTP_RETRY_WAIT:int = int(os.getenv("HTTP_RETRY_WAIT","5")) # seconds

    # --- File Paths ---
    # تغيير: استخدام cooking_articles_600.json مباشرة في المجلد الرئيسي
    ARTICLES_EN:    str = str(ROOT_DIR / "cooking_articles_600.json")
    LOG_EN:         str = str(DATA_DIR / "log_en.txt")

    # --- Health-check keep-alive (Render kills idle free-tier after 15 min) ---
    KEEPALIVE_INTERVAL: int = int(os.getenv("KEEPALIVE_INTERVAL", "540"))  # 9 min


cfg = Config()

# ============================================================
# 🗣️  LANGUAGE REGISTRY — Central language meta
# ============================================================

LANG_META = {
    "en": {
        "code": "en",
        "dir": "ltr",
        "label": "English",
        "articles_file": cfg.ARTICLES_EN,
        "log_file": cfg.LOG_EN,
        "sections": ["Introduction", "Core Concepts", "Methods & Tools", "Practical Tips", "Conclusion"],
        "related_label": "Related Articles You Might Enjoy",
        "conclusion_template": "In conclusion, the topic of <strong>{keyword}</strong> is one of the most important areas to focus on. Keep learning and exploring — success comes with consistency.",
        "intro_prefix": "In this article, we will explore",
        "tag_prefix": "📌",
        "published_label": "Published on",
    }
}

# ============================================================
# 📦 SAMPLE ARTICLES — Seed data for cooking articles
# ============================================================

SEED_ARTICLES = [
    {
        "id": 1,
        "title": "Delicious Homemade Pizza Recipe",
        "keyword": "Pizza Recipe",
        "html": "<h1>Delicious Homemade Pizza Recipe</h1><p>Learn how to make amazing homemade pizza with this easy recipe...</p>",
        "image_url": "https://picsum.photos/seed/pizza/800/400"
    },
    {
        "id": 2,
        "title": "Perfect Chocolate Chip Cookies",
        "keyword": "Cookie Recipe",
        "html": "<h1>Perfect Chocolate Chip Cookies</h1><p>The ultimate guide to baking soft and chewy chocolate chip cookies...</p>",
        "image_url": "https://picsum.photos/seed/cookies/800/400"
    }
]

# ============================================================
# 🏗️  DATA LAYER — File I/O with atomic writes
# ============================================================

class FileStore:
    """
    Thread-safe, atomic file I/O.
    Atomic writes prevent corruption if the process crashes mid-write.
    """
    _lock = threading.Lock()

    @staticmethod
    def read_json(path: str) -> list:
        if not Path(path).exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def write_json(path: str, data):
        tmp = path + ".tmp"
        with FileStore._lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)  # atomic on POSIX

    @staticmethod
    def append_line(path: str, line: str):
        with FileStore._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    @staticmethod
    def read_lines(path: str) -> list[str]:
        if not Path(path).exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]

# ============================================================
# 📋 PUBLISH LOG MANAGER — Per-language deduplication
# ============================================================

class PublishLog:
    def __init__(self, log_path: str):
        self.path = log_path
        Path(log_path).touch(exist_ok=True)

    # --- Is this article already published? ---
    def is_published(self, article_id: int) -> bool:
        for line in FileStore.read_lines(self.path):
            if line.startswith(f"ID:{article_id}|"):
                return True
        return False

    # --- Record a successful publish ---
    def mark_published(self, article: dict):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"ID:{article['id']}|TITLE:{article['title']}|STATUS:published|TIME:{ts}"
        FileStore.append_line(self.path, entry)

    # --- How many published today (UTC)? ---
    def count_today(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return sum(1 for l in FileStore.read_lines(self.path) if today in l and "STATUS:published" in l)

# ============================================================
# 🎨 HTML ARTICLE BUILDER — Full SEO + i18n
# ============================================================

class ArticleBuilder:
    """
    Builds a complete, SEO-optimised HTML email.
    Uses the 'html' field from the cooking articles file.
    """

    def __init__(self, article: dict, lang_meta: dict):
        self.a        = article
        self.meta     = lang_meta
        self.title    = article["title"]
        self.keyword  = article.get("keyword", "")
        # تغيير مهم: استخدام حقل html بدلاً من body
        self.html_content = article.get("html", "")
        self.img      = article.get("image_url", "")
        self.links    = article.get("internal_links", [])
        self.dir      = lang_meta["dir"]
        self.lang     = lang_meta["code"]

    # ── Unique content hash (for dedup fingerprint) ──
    @staticmethod
    def content_hash(article: dict) -> str:
        raw = f"{article['id']}:{article['title']}:{article.get('keyword', '')}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    # ── MASTER BUILD ──
    def build(self) -> tuple[str, str]:
        """Returns (subject, full_html)"""
        ts   = datetime.now().strftime("%d/%m/%Y")
        pfx  = self.meta["tag_prefix"]
        pub  = self.meta["published_label"]

        # استخدم HTML الموجود في الملف مباشرة
        article_content = self.html_content if self.html_content else "<p>No content available.</p>"
        
        html = f"""<!DOCTYPE html>
<html lang="{self.lang}" dir="{self.dir}">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{self.title}</title>
<style>
  body {{
    font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif;
    background:#f4f6f8;color:#333;direction:{self.dir};padding:16px;margin:0;
  }}
  .wrap {{
    max-width:780px;margin:0 auto;background:#fff;border-radius:14px;
    padding:32px 28px;box-shadow:0 3px 16px rgba(0,0,0,0.07);
  }}
  .tag {{
    display:inline-block;background:#e94560;color:#fff;
    padding:5px 14px;border-radius:20px;font-size:13px;margin-bottom:18px;
  }}
  .footer {{
    text-align:center;color:#999;font-size:12px;
    margin-top:28px;border-top:1px solid #eee;padding-top:14px;
  }}
  h1,h2,h3 {{margin-top:0;}}
  @media(max-width:600px){{
    .wrap{{padding:18px 14px;border-radius:0;}}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="tag">{pfx} {self.keyword}</div>
  {article_content}
  <div class="footer">
    <p>{pub}: {ts} &nbsp;|&nbsp; {self.keyword} &nbsp;|&nbsp; Hash: {self.content_hash(self.a)}</p>
  </div>
</div>
</body>
</html>"""
        return self.title, html

# ============================================================
# 🌉 HTTP WEBHOOK SENDER — Retry + connection reuse
# ============================================================

class WebhookSender:
    """
    Sends article data to Pipedream webhook via HTTP POST.
    Uses retry logic for transient failures.
    """
    def __init__(self):
        self.webhook_url = cfg.PIPEDREAM_WEBHOOK

    # ── Send with retry loop ──
    def send(self, subject: str, html: str) -> bool:
        """
        Send article to Pipedream webhook.
        Returns True if successful, False otherwise.
        """
        # تحضير البيانات للإرسال
        payload = {
            "subject": subject,
            "html": html,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "smart-publishing-engine"
        }
        
        # تحويل البيانات إلى JSON
        data = json.dumps(payload).encode('utf-8')
        
        for attempt in range(1, cfg.HTTP_RETRIES + 1):
            try:
                # إنشاء طلب HTTP
                req = urllib.request.Request(
                    self.webhook_url,
                    data=data,
                    headers={
                        'Content-Type': 'application/json',
                        'User-Agent': 'SmartPublishingEngine/1.0'
                    },
                    method='POST'
                )
                
                # إرسال الطلب
                with urllib.request.urlopen(req, timeout=30) as response:
                    status = response.getcode()
                    if 200 <= status < 300:
                        logger.info("[WEBHOOK] ✅ Sent: %s (attempt %d, status %d)", 
                                   subject, attempt, status)
                        return True
                    else:
                        logger.warning("[WEBHOOK] ⚠️  HTTP %d for: %s (attempt %d/%d)", 
                                      status, subject, attempt, cfg.HTTP_RETRIES)
                        
            except urllib.error.HTTPError as e:
                logger.warning("[WEBHOOK] ⚠️  HTTP Error %d: %s (attempt %d/%d)", 
                              e.code, e.reason, attempt, cfg.HTTP_RETRIES)
            except urllib.error.URLError as e:
                logger.warning("[WEBHOOK] ⚠️  URL Error: %s (attempt %d/%d)", 
                              e.reason, attempt, cfg.HTTP_RETRIES)
            except ConnectionError as e:
                logger.warning("[WEBHOOK] ⚠️  Connection Error: %s (attempt %d/%d)", 
                              str(e), attempt, cfg.HTTP_RETRIES)
            except TimeoutError as e:
                logger.warning("[WEBHOOK] ⚠️  Timeout Error (attempt %d/%d)", 
                              attempt, cfg.HTTP_RETRIES)
            except Exception as e:
                logger.error("[WEBHOOK] ❌ Unexpected: %s (attempt %d/%d)", 
                           str(e), attempt, cfg.HTTP_RETRIES)
            
            # انتظار قبل إعادة المحاولة
            if attempt < cfg.HTTP_RETRIES:
                wait_time = cfg.HTTP_RETRY_WAIT * attempt
                time.sleep(wait_time)
        
        logger.error("[WEBHOOK] ❌ All %d retries exhausted for: %s", 
                    cfg.HTTP_RETRIES, subject)
        return False

# ============================================================
# 🗂️  LANGUAGE WORKER — One thread per language
# ============================================================

class LanguageWorker:
    """
    Manages the full publish lifecycle for ONE language.
    Runs in its own daemon thread so languages are independent.
    """
    def __init__(self, lang_code: str, sender: WebhookSender):
        self.lang     = lang_code
        self.meta     = LANG_META[lang_code]
        self.sender   = sender
        self.log      = PublishLog(self.meta["log_file"])
        self.quota    = random.randint(cfg.QUOTA_MIN, cfg.QUOTA_MAX)
        self._ensure_seed()

    # ── Seed articles file if missing ──
    def _ensure_seed(self):
        if not Path(self.meta["articles_file"]).exists():
            FileStore.write_json(self.meta["articles_file"], SEED_ARTICLES)
            logger.info("[%s] Seeded %d articles.", self.lang.upper(), len(SEED_ARTICLES))

    # ── Pending articles (not yet published today) ──
    def _pending(self) -> list[dict]:
        return [a for a in FileStore.read_json(self.meta["articles_file"]) if not self.log.is_published(a["id"])]

    # ── Random wait in [INTERVAL_MIN, INTERVAL_MAX] minutes ──
    def _wait(self):
        secs = random.randint(cfg.INTERVAL_MIN, cfg.INTERVAL_MAX) * 60
        logger.info("[%s] ⏳ Next publish in %d min …", self.lang.upper(), secs // 60)
        time.sleep(secs)

    # ── Main loop ──
    def run(self):
        logger.info("[%s] 🚀 Worker started | Quota today: %d", self.lang.upper(), self.quota)
        while True:
            # quota check
            today_count = self.log.count_today()
            if today_count >= self.quota:
                logger.info("[%s] ✅ Daily quota reached (%d/%d). Sleeping 1 h …", self.lang.upper(), today_count, self.quota)
                time.sleep(3600)
                continue

            pending = self._pending()
            if not pending:
                logger.info("[%s] ⚠️  No pending articles. Add more to %s", self.lang.upper(), self.meta["articles_file"])
                time.sleep(300)  # re-check every 5 min
                continue

            article = pending[0]
            builder = ArticleBuilder(article, self.meta)
            subject, html = builder.build()

            if self.sender.send(subject, html):
                self.log.mark_published(article)
            else:
                logger.error("[%s] ❌ Failed to publish: %s. Will retry after interval.", self.lang.upper(), article["title"])

            self._wait()

# ============================================================
# 🌱 KEEP-ALIVE THREAD — Prevents Render free-tier spin-down
# ============================================================

class KeepAliveThread(threading.Thread):
    """
    Pings the app's own /health endpoint every N seconds
    so Render doesn't spin the container down.
    """
    def __init__(self, interval: int, host: str = "127.0.0.1", port: int = 5000):
        super().__init__(daemon=True)
        self.interval = interval
        self.url      = f"http://{host}:{port}/health"

    def run(self):
        import urllib.request
        while True:
            time.sleep(self.interval)
            try:
                urllib.request.urlopen(self.url, timeout=5)
                logger.debug("[KeepAlive] ✔ ping OK")
            except Exception as e:
                logger.warning("[KeepAlive] ⚠️  ping failed: %s", e)

# ============================================================
# 🌐 FLASK APP — Render Web Service entry-point
# ============================================================

app = Flask(__name__)

# -- shared webhook sender --
_sender = WebhookSender()

# -- global status registry (in-memory) --
_status: dict = {}   # { "en": { "quota": int, "published_today": int, "pending": int }, … }

# -- simple bearer-token guard for mutation endpoints --
API_KEY = os.getenv("API_KEY", "")

def require_key(fn):
    @wraps(fn)
    def guard(*a, **kw):
        if API_KEY and request.headers.get("X-API-Key") != API_KEY:
            return jsonify({"error": "Unauthorised"}), 403
        return fn(*a, **kw)
    return guard

# ── Health / Keep-alive ──
@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()}), 200

# ── Status of every language ──
@app.route("/status")
def status():
    out = {}
    for code, meta in LANG_META.items():
        log   = PublishLog(meta["log_file"])
        arts  = FileStore.read_json(meta["articles_file"]) if Path(meta["articles_file"]).exists() else []
        pend  = [a for a in arts if not log.is_published(a["id"])]
        out[code] = {
            "language":         meta["label"],
            "published_today":  log.count_today(),
            "pending":          len(pend),
            "total_articles":   len(arts),
        }
    return jsonify(out), 200

# ── Add article via API ──
@app.route("/articles/<lang>", methods=["POST"])
@require_key
def add_article(lang):
    if lang not in LANG_META:
        return jsonify({"error": f"Unknown language. Choose: {list(LANG_META.keys())}"}), 400
    body = request.get_json(silent=True)
    if not body or not all(k in body for k in ("title", "html")):
        return jsonify({"error": "Missing required fields: title, html"}), 400

    path     = LANG_META[lang]["articles_file"]
    articles = FileStore.read_json(path) if Path(path).exists() else []
    new_id   = max((a["id"] for a in articles), default=0) + 1
    article  = {
        "id":             new_id,
        "title":          body["title"],
        "keyword":        body.get("keyword", ""),
        "html":           body["html"],  # استخدام حقل html
        "image_url":      body.get("image_url", ""),
        "internal_links": body.get("internal_links", []),
    }
    articles.append(article)
    FileStore.write_json(path, articles)
    logger.info("[API] Added article #%d to %s", new_id, lang.upper())
    return jsonify({"status": "added", "id": new_id, "language": lang}), 201

# ── List articles ──
@app.route("/articles/<lang>", methods=["GET"])
def list_articles(lang):
    if lang not in LANG_META:
        return jsonify({"error": f"Unknown language"}), 400
    path = LANG_META[lang]["articles_file"]
    arts = FileStore.read_json(path) if Path(path).exists() else []
    log  = PublishLog(LANG_META[lang]["log_file"])
    for a in arts:
        a["published"] = log.is_published(a["id"])
    return jsonify(arts), 200

# ── Preview HTML for one article ──
@app.route("/preview/<lang>/<int:article_id>")
def preview(lang, article_id):
    if lang not in LANG_META:
        return jsonify({"error": "Unknown language"}), 400
    path = LANG_META[lang]["articles_file"]
    arts = FileStore.read_json(path) if Path(path).exists() else []
    match = [a for a in arts if a["id"] == article_id]
    if not match:
        return jsonify({"error": "Article not found"}), 404
    _, html = ArticleBuilder(match[0], LANG_META[lang]).build()
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}

# ============================================================
# 🚀 STARTUP — Launch workers + keep-alive
# ============================================================

def start_workers():
    """Called once when the process boots (gunicorn worker or dev server)."""
    logger.info("=" * 60)
    logger.info(" SMART PUBLISHING ENGINE — STARTING")
    logger.info(" Language: %s", ", ".join(LANG_META.keys()))
    logger.info(" Articles file: %s", cfg.ARTICLES_EN)
    logger.info(" Webhook URL: %s", cfg.PIPEDREAM_WEBHOOK)
    logger.info("=" * 60)

    sender = WebhookSender()

    for code in LANG_META:
        worker = LanguageWorker(code, sender)
        t = threading.Thread(target=worker.run, daemon=True, name=f"Worker-{code.upper()}")
        t.start()
        logger.info("[BOOT] ✅ Thread started: %s", t.name)

    # Keep-alive for Render free-tier
    port = int(os.getenv("PORT", "5000"))
    ka   = KeepAliveThread(cfg.KEEPALIVE_INTERVAL, port=port)
    ka.start()
    logger.info("[BOOT] ✅ KeepAlive thread started (interval=%ds)", cfg.KEEPALIVE_INTERVAL)

# -- Boot guard: only start once (avoids double-start with gunicorn --preload) --
if not os.getenv("GUNICORN_WORKER_STARTED"):
    os.environ["GUNICORN_WORKER_STARTED"] = "1"
    start_workers()

# ============================================================
# 🛡️  LOCAL DEV ENTRY-POINT
# ============================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
