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
    # Pipedream webhook endpoint (الجسر) - رابط جديد
    PIPEDREAM_WEBHOOK: str = os.getenv("PIPEDREAM_WEBHOOK", "https://eo7yfk2notppj48.m.pipedream.net")
    
    # --- Quota & Timing ---
    QUOTA_MIN:      int = int(os.getenv("QUOTA_MIN",  "10"))
    QUOTA_MAX:      int = int(os.getenv("QUOTA_MAX",  "15"))
    # زيادة مدة النشر إلى 3 ساعات (180 دقيقة) مع تباين بسيط
    INTERVAL_MIN:   int = int(os.getenv("INTERVAL_MIN","170"))   # دقائق (حوالي 2.83 ساعة)
    INTERVAL_MAX:   int = int(os.getenv("INTERVAL_MAX","190"))   # دقائق (حوالي 3.17 ساعة)

    # --- HTTP Retry ---
    HTTP_RETRIES:   int = int(os.getenv("HTTP_RETRIES","3"))
    HTTP_RETRY_WAIT:int = int(os.getenv("HTTP_RETRY_WAIT","5")) # seconds

    # --- File Paths ---
    # استخدام cooking_articles_600.json مباشرة في المجلد الرئيسي
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
        "body": "This is a sample article body with multiple sentences. Each sentence ends with a period. We will format this into proper paragraphs. The article continues with more content about cooking delicious pizza. It includes tips and tricks for perfect dough. Finally, we discuss baking techniques for the best results.",
        "image_url": "https://picsum.photos/seed/pizza/800/400"
    },
    {
        "id": 2,
        "title": "Perfect Chocolate Chip Cookies",
        "keyword": "Cookie Recipe",
        "body": "This article teaches you how to make perfect chocolate chip cookies. First, we discuss ingredient selection. Then, we cover mixing techniques. Finally, we explain baking temperature and timing. Each step is crucial for cookie perfection.",
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
# 🎨 HTML ARTICLE BUILDER — Full SEO + i18n with IMAGES & PARAGRAPHS
# ============================================================

class ArticleBuilder:
    """
    Builds a complete, SEO-optimised HTML email with images and formatted paragraphs.
    Uses the 'body' field from the cooking articles file.
    """

    def __init__(self, article: dict, lang_meta: dict):
        self.a        = article
        self.meta     = lang_meta
        self.title    = article["title"]
        self.keyword  = article.get("keyword", "")
        # استخدام حقل body بدلاً من html
        self.body_content = article.get("body", "")
        self.img      = article.get("image_url", "")
        self.links    = article.get("internal_links", [])
        self.dir      = lang_meta["dir"]
        self.lang     = lang_meta["code"]

    # ── Unique content hash (for dedup fingerprint) ──
    @staticmethod
    def content_hash(article: dict) -> str:
        raw = f"{article['id']}:{article['title']}:{article.get('keyword', '')}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    # ── Responsive Image with Rounded Corners ──
    def _image_html(self) -> str:
        """توليد كود الصورة المتجاوبة مع الزوايا الناعمة"""
        if not self.img:
            return ""
        
        return f'''
        <div style="text-align:center; margin: 25px 0 30px 0;">
            <img src="{self.img}" 
                 alt="{self.title}"
                 style="max-width:100%; 
                        height:auto; 
                        border-radius:12px; 
                        box-shadow:0 6px 20px rgba(0,0,0,0.15);
                        border: 1px solid #f0f0f0;
                        transition: transform 0.3s ease;" 
                 onmouseover="this.style.transform='scale(1.01)'"
                 onmouseout="this.style.transform='scale(1)'">
            <p style="color:#888; font-size:13px; margin-top:8px; font-style:italic;">
                📸 {self.keyword} - Recipe Image
            </p>
        </div>'''

    # ── Format Body into Paragraphs ──
    def _formatted_body(self) -> str:
        """تحويل النص الكتلة إلى فقرات منظمة"""
        if not self.body_content:
            return '<p style="color:#666; font-style:italic;">No content available for this article.</p>'
        
        # تنظيف النص الأساسي
        body = self.body_content.strip()
        
        # استبدال علامات الترقيم المتعددة بعلامات واحدة
        body = body.replace('..', '.').replace('!!', '!').replace('??', '?')
        
        # تقسيم الجمل بناءً على علامات الترقيم الرئيسية
        sentences = []
        temp = ''
        
        for char in body:
            temp += char
            if char in '.!?':
                sentences.append(temp.strip())
                temp = ''
        
        # إضافة الجملة الأخيرة إذا كانت موجودة
        if temp.strip():
            sentences.append(temp.strip())
        
        # تجميع 2-3 جمل في كل فقرة
        paragraphs = []
        current_paragraph = []
        
        for i, sentence in enumerate(sentences):
            current_paragraph.append(sentence)
            
            # إنهاء الفقرة كل 2-3 جمل أو عند انتهاء الجمل
            if len(current_paragraph) >= 3 or i == len(sentences) - 1:
                paragraph_text = ' '.join(current_paragraph)
                paragraphs.append(f'''
                <p style="line-height:1.8; 
                         font-size:16px; 
                         color:#333; 
                         margin-bottom:20px;
                         text-align:justify;">
                    {paragraph_text}
                </p>''')
                current_paragraph = []
        
        return '\n'.join(paragraphs)

    # ── H1 Title ──
    def _h1(self) -> str:
        align = "center" if self.dir == "rtl" else "left"
        return f'''
        <h1 style="color:#1a1a2e;
                   text-align:{align};
                   line-height:1.4;
                   margin: 20px 0 15px 0;
                   font-size:32px;
                   border-bottom: 3px solid #e94560;
                   padding-bottom: 12px;">
            {self.title}
        </h1>'''

    # ── Introduction Section ──
    def _introduction(self) -> str:
        """إنشاء مقدمة المقال مع الكلمة المفتاحية"""
        intro_text = f"In this comprehensive guide, we will explore {self.keyword.lower()}. "
        intro_text += "This article provides detailed instructions, tips, and techniques to help you master this recipe."
        
        return f'''
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 20px;
                    border-radius: 10px;
                    margin: 25px 0;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.1);">
            <h2 style="margin-top:0; color:white;">✨ Introduction</h2>
            <p style="font-size:17px; line-height:1.7;">
                {intro_text}
            </p>
        </div>'''

    # ── Conclusion Section ──
    def _conclusion(self) -> str:
        """إنشاء قسم الخاتمة"""
        conclusion_text = f"Mastering {self.keyword} takes practice and patience. "
        conclusion_text += "Remember to always use fresh ingredients and follow the steps carefully. "
        conclusion_text += "With time, you'll develop your own signature style!"
        
        return f'''
        <div style="background: #f8f9fa;
                    border-left: 4px solid #28a745;
                    padding: 20px;
                    border-radius: 8px;
                    margin: 30px 0;
                    box-shadow: 0 3px 10px rgba(0,0,0,0.05);">
            <h3 style="color:#28a745; margin-top:0;">✅ Key Takeaways</h3>
            <p style="font-size:16px; line-height:1.7; color:#444;">
                {conclusion_text}
            </p>
        </div>'''

    # ── MASTER BUILD ──
    def build(self) -> tuple[str, str]:
        """Returns (subject, full_html)"""
        ts   = datetime.now().strftime("%d %B %Y")
        pfx  = self.meta["tag_prefix"]
        pub  = self.meta["published_label"]

        # بناء المحتوى خطوة بخطوة
        image_html = self._image_html()
        h1_html = self._h1()
        intro_html = self._introduction()
        body_html = self._formatted_body()
        conclusion_html = self._conclusion()
        
        html = f"""<!DOCTYPE html>
<html lang="{self.lang}" dir="{self.dir}">
<head>
    <meta charset="UTF-8"/>
    <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
    <title>{self.title} | Cooking Recipe</title>
    <meta name="description" content="Learn how to make {self.keyword.lower()} with this detailed step-by-step guide. Professional cooking tips and techniques." />
    <meta name="keywords" content="{self.keyword}, recipe, cooking, food, tutorial" />
    <style>
        body {{
            font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            color: #333;
            direction: {self.dir};
            padding: 20px;
            margin: 0;
            line-height: 1.6;
        }}
        .wrap {{
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            padding: 40px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            position: relative;
            overflow: hidden;
        }}
        .wrap::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 5px;
            background: linear-gradient(90deg, #ff6b6b, #4ecdc4, #45b7d1);
        }}
        .tag {{
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #fff;
            padding: 8px 20px;
            border-radius: 25px;
            font-size: 14px;
            font-weight: bold;
            margin-bottom: 20px;
            box-shadow: 0 4px 10px rgba(102, 126, 234, 0.3);
        }}
        .footer {{
            text-align: center;
            color: #777;
            font-size: 13px;
            margin-top: 40px;
            border-top: 2px dashed #eee;
            padding-top: 20px;
        }}
        h1, h2, h3 {{
            margin-top: 0;
            font-weight: 700;
        }}
        @media (max-width: 600px) {{
            .wrap {{
                padding: 25px 20px;
                border-radius: 0;
                margin: 0;
            }}
            h1 {{
                font-size: 26px;
            }}
        }}
        .content-block {{
            animation: fadeIn 0.8s ease-out;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <div class="tag">{pfx} {self.keyword}</div>
        
        {image_html}
        
        <div class="content-block">
            {h1_html}
            {intro_html}
            {body_html}
            {conclusion_html}
        </div>
        
        <div class="footer">
            <p>{pub}: <strong>{ts}</strong> &nbsp;|&nbsp; 
               <span style="color:#667eea;">{self.keyword}</span> &nbsp;|&nbsp; 
               Article ID: {self.content_hash(self.a)}</p>
            <p style="font-size:12px; color:#aaa; margin-top:10px;">
                This recipe was automatically generated with care ❤️
            </p>
        </div>
    </div>
    
    <script>
        // تأثيرات تفاعلية بسيطة
        document.addEventListener('DOMContentLoaded', function() {{
            const paragraphs = document.querySelectorAll('p');
            paragraphs.forEach(p => {{
                p.addEventListener('mouseover', function() {{
                    this.style.backgroundColor = '#f8f9fa';
                    this.style.transition = 'background-color 0.3s ease';
                }});
                p.addEventListener('mouseout', function() {{
                    this.style.backgroundColor = 'transparent';
                }});
            }});
        }});
    </script>
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
        hours = secs / 3600
        logger.info("[%s] ⏳ Next publish in %.1f hours (%d minutes) …", 
                   self.lang.upper(), hours, secs // 60)
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
    if not body or not all(k in body for k in ("title", "body")):
        return jsonify({"error": "Missing required fields: title, body"}), 400

    path     = LANG_META[lang]["articles_file"]
    articles = FileStore.read_json(path) if Path(path).exists() else []
    new_id   = max((a["id"] for a in articles), default=0) + 1
    article  = {
        "id":             new_id,
        "title":          body["title"],
        "keyword":        body.get("keyword", ""),
        "body":           body["body"],  # استخدام حقل body
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
    logger.info(" Publish interval: %d-%d minutes (%.1f-%.1f hours)", 
               cfg.INTERVAL_MIN, cfg.INTERVAL_MAX, 
               cfg.INTERVAL_MIN/60, cfg.INTERVAL_MAX/60)
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
