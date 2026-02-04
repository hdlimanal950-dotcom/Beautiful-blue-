"""
============================================================
  SMART PUBLISHING ENGINE — Enhanced Version with:
  - Smart article division (H2/H3 sections)
  - Real cooking images from Unsplash
  - Random color themes per article
  - Improved responsive design
  Designed for: render.com (Web Service)
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

# ============================================================
# 📂 ROOT PATH
# ============================================================
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ============================================================
# 📝 LOGGING
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
# ⚙️  CONFIG
# ============================================================

class Config:
    PIPEDREAM_WEBHOOK: str = os.getenv("PIPEDREAM_WEBHOOK", "https://eo7yfk2notppj48.m.pipedream.net")
    
    QUOTA_MIN:      int = int(os.getenv("QUOTA_MIN",  "10"))
    QUOTA_MAX:      int = int(os.getenv("QUOTA_MAX",  "15"))
    INTERVAL_MIN:   int = int(os.getenv("INTERVAL_MIN","170"))
    INTERVAL_MAX:   int = int(os.getenv("INTERVAL_MAX","190"))

    HTTP_RETRIES:   int = int(os.getenv("HTTP_RETRIES","3"))
    HTTP_RETRY_WAIT:int = int(os.getenv("HTTP_RETRY_WAIT","5"))

    ARTICLES_EN:    str = str(ROOT_DIR / "cooking_articles_600.json")
    LOG_EN:         str = str(DATA_DIR / "log_en.txt")

    KEEPALIVE_INTERVAL: int = int(os.getenv("KEEPALIVE_INTERVAL", "540"))

cfg = Config()

# ============================================================
# 🗣️  LANGUAGE META
# ============================================================

LANG_META = {
    "en": {
        "code": "en",
        "dir": "ltr",
        "label": "English",
        "articles_file": cfg.ARTICLES_EN,
        "log_file": cfg.LOG_EN,
        "sections": ["Ingredients", "Instructions", "Tips", "Serving"],
        "tag_prefix": "🍳",
        "published_label": "Published on",
    }
}

# ============================================================
# 🏗️  FILE STORAGE
# ============================================================

class FileStore:
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
            os.replace(tmp, path)

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
# 📋 PUBLISH LOG
# ============================================================

class PublishLog:
    def __init__(self, log_path: str):
        self.path = log_path
        Path(log_path).touch(exist_ok=True)

    def is_published(self, article_id: int) -> bool:
        for line in FileStore.read_lines(self.path):
            if line.startswith(f"ID:{article_id}|"):
                return True
        return False

    def mark_published(self, article: dict):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"ID:{article['id']}|TITLE:{article['title']}|STATUS:published|TIME:{ts}"
        FileStore.append_line(self.path, entry)

    def count_today(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return sum(1 for l in FileStore.read_lines(self.path) if today in l and "STATUS:published" in l)

# ============================================================
# 🎨 ENHANCED ARTICLE BUILDER
# ============================================================

class ArticleBuilder:
    """
    Enhanced article builder with:
    - Smart H2/H3 section division
    - Real Unsplash cooking images
    - Random color themes
    - Professional responsive design
    """

    # Color themes palette
    THEMES = [
        {"name": "sunset", "gradient": "linear-gradient(135deg, #ff6b6b 0%, #feca57 100%)", "accent": "#ff6b6b"},
        {"name": "ocean", "gradient": "linear-gradient(135deg, #667eea 0%, #764ba2 100%)", "accent": "#667eea"},
        {"name": "forest", "gradient": "linear-gradient(135deg, #56ab2f 0%, #a8e063 100%)", "accent": "#56ab2f"},
        {"name": "berry", "gradient": "linear-gradient(135deg, #eb3349 0%, #f45c43 100%)", "accent": "#eb3349"},
        {"name": "sky", "gradient": "linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)", "accent": "#4facfe"},
        {"name": "mint", "gradient": "linear-gradient(135deg, #11998e 0%, #38ef7d 100%)", "accent": "#11998e"},
        {"name": "lavender", "gradient": "linear-gradient(135deg, #a8c0ff 0%, #3f2b96 100%)", "accent": "#a8c0ff"},
        {"name": "peach", "gradient": "linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%)", "accent": "#fcb69f"}
    ]

    def __init__(self, article: dict, lang_meta: dict):
        self.a = article
        self.meta = lang_meta
        self.title = article["title"]
        self.keyword = article.get("keyword", "")
        self.body_content = article.get("body", "")
        self.img = article.get("image_url", "")
        self.dir = lang_meta["dir"]
        self.lang = lang_meta["code"]
        
        # Random theme
        self.theme = random.choice(self.THEMES)
        
        # Generate real cooking image
        self.cooking_image = self._generate_cooking_image()

    @staticmethod
    def content_hash(article: dict) -> str:
        raw = f"{article['id']}:{article['title']}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def _generate_cooking_image(self) -> str:
        """Generate real cooking image URL from Unsplash"""
        food_keywords = [
            "pizza", "pasta", "burger", "salad", "soup", "chicken", "beef",
            "fish", "cake", "cookie", "bread", "rice", "noodles", "tacos",
            "sandwich", "steak", "salmon", "shrimp", "vegetables", "fruit",
            "dessert", "breakfast", "pancakes", "waffles", "eggs", "bacon"
        ]
        
        title_lower = self.title.lower()
        food_term = "food"
        
        for keyword in food_keywords:
            if keyword in title_lower:
                food_term = keyword
                break
        
        random_id = random.randint(1, 10000)
        return f"https://source.unsplash.com/800x500/?{food_term},food,cooking&sig={random_id}"

    def _hero_image(self) -> str:
        """Hero image with gradient overlay"""
        return f'''
        <div style="position: relative; 
                    margin: -40px -40px 35px -40px;
                    height: 450px;
                    border-radius: 16px 16px 0 0;
                    overflow: hidden;">
            <img src="{self.cooking_image}" 
                 alt="{self.title}"
                 style="width: 100%;
                        height: 100%;
                        object-fit: cover;
                        filter: brightness(0.80) contrast(1.1);">
            <div style="position: absolute;
                        bottom: 0;
                        left: 0;
                        right: 0;
                        background: linear-gradient(to top, rgba(0,0,0,0.85) 0%, transparent 100%);
                        padding: 40px;
                        color: white;">
                <div style="background: rgba(255,255,255,0.15);
                           backdrop-filter: blur(10px);
                           padding: 6px 15px;
                           border-radius: 20px;
                           display: inline-block;
                           margin-bottom: 12px;">
                    <span style="font-size: 14px; font-weight: 600;">🍳 {self.keyword}</span>
                </div>
                <h1 style="margin: 0;
                           font-size: 38px;
                           font-weight: 800;
                           line-height: 1.2;
                           text-shadow: 3px 3px 12px rgba(0,0,0,0.6);">
                    {self.title}
                </h1>
            </div>
        </div>'''

    def _smart_sections(self) -> str:
        """Intelligent article division into H2/H3 sections"""
        if not self.body_content:
            return '<p style="color:#666;">Content unavailable.</p>'
        
        # Clean and split
        body = self.body_content.strip().replace('\n\n', '. ').replace('\n', ' ')
        sentences = []
        current = ''
        
        for char in body:
            current += char
            if char in '.!?' and len(current.strip()) > 15:
                sentences.append(current.strip())
                current = ''
        if current.strip():
            sentences.append(current.strip())
        
        total = len(sentences)
        if total < 8:
            return self._simple_paragraphs(sentences)
        
        # Divide into 4 main sections
        chunk = total // 4
        
        sections = [
            {"icon": "📋", "title": "Ingredients & Prep", "sentences": sentences[0:chunk]},
            {"icon": "👨‍🍳", "title": "Cooking Steps", "sentences": sentences[chunk:chunk*2]},
            {"icon": "💡", "title": "Pro Tips", "sentences": sentences[chunk*2:chunk*3]},
            {"icon": "🍽️", "title": "Serving & Storage", "sentences": sentences[chunk*3:]}
        ]
        
        html_out = []
        for section in sections:
            html_out.append(f'''
            <div style="margin: 40px 0;">
                <h2 style="color: {self.theme['accent']};
                           font-size: 28px;
                           font-weight: 700;
                           margin-bottom: 20px;
                           padding-bottom: 12px;
                           border-bottom: 3px solid {self.theme['accent']};">
                    {section['icon']} {section['title']}
                </h2>
            ''')
            
            # Group sentences into paragraphs (3 per paragraph)
            for i in range(0, len(section['sentences']), 3):
                para = ' '.join(section['sentences'][i:i+3])
                
                # Add H3 every 2 paragraphs
                if i > 0 and i % 6 == 0:
                    html_out.append(f'''
                    <h3 style="color: #555;
                               font-size: 20px;
                               margin: 25px 0 15px 0;
                               font-weight: 600;">
                        → Key Point {i//6 + 1}
                    </h3>''')
                
                html_out.append(f'''
                <p style="line-height: 1.95;
                         font-size: 17px;
                         color: #444;
                         margin-bottom: 20px;
                         padding: 18px;
                         background: linear-gradient(to right, #fafafa, #ffffff);
                         border-left: 4px solid {self.theme['accent']};
                         border-radius: 8px;
                         text-align: justify;">
                    {para}
                </p>''')
            
            html_out.append('</div>')
        
        return '\n'.join(html_out)

    def _simple_paragraphs(self, sentences: list) -> str:
        """Fallback for short content"""
        paras = []
        for i in range(0, len(sentences), 3):
            para = ' '.join(sentences[i:i+3])
            paras.append(f'''
            <p style="line-height: 1.9;
                     font-size: 17px;
                     color: #333;
                     margin-bottom: 22px;
                     text-align: justify;">
                {para}
            </p>''')
        return '\n'.join(paras)

    def _intro_box(self) -> str:
        """Introduction callout box"""
        return f'''
        <div style="background: {self.theme['gradient']};
                    color: white;
                    padding: 30px;
                    border-radius: 14px;
                    margin: 35px 0;
                    box-shadow: 0 10px 30px rgba(0,0,0,0.15);">
            <h2 style="margin: 0 0 15px 0; 
                       font-size: 24px;
                       color: white;">
                ✨ What You'll Master
            </h2>
            <p style="font-size: 18px; 
                     line-height: 1.8; 
                     margin: 0;
                     opacity: 0.95;">
                This comprehensive guide covers everything about <strong>{self.keyword}</strong>. 
                Follow our step-by-step instructions, expert tips, and pro techniques 
                for guaranteed delicious results every time you cook.
            </p>
        </div>'''

    def _conclusion_box(self) -> str:
        """Conclusion with tips"""
        tips = [
            "Always use fresh, high-quality ingredients for best results",
            "Don't rush - good cooking takes time and patience",
            "Taste and adjust seasonings throughout the process",
            "Practice makes perfect - each attempt improves your skills",
            "Store leftovers properly in airtight containers"
        ]
        selected = random.sample(tips, 3)
        tips_html = ''.join([f'<li style="margin: 10px 0; line-height: 1.6;">{t}</li>' for t in selected])
        
        return f'''
        <div style="background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
                    border: 2px solid {self.theme['accent']};
                    border-radius: 12px;
                    padding: 30px;
                    margin: 40px 0;">
            <h3 style="color: {self.theme['accent']}; 
                       margin: 0 0 20px 0; 
                       font-size: 24px;
                       font-weight: 700;">
                ✅ Essential Tips for Success
            </h3>
            <ul style="margin: 0 0 20px 0; 
                      padding-left: 25px; 
                      color: #555;
                      font-size: 16px;">
                {tips_html}
            </ul>
            <div style="background: white;
                       padding: 18px;
                       border-radius: 8px;
                       border-left: 4px solid {self.theme['accent']};">
                <p style="margin: 0; 
                         font-style: italic; 
                         color: #666;
                         font-size: 16px;">
                    💬 <strong>Your Turn:</strong> Try this {self.keyword} recipe today 
                    and share your results with us!
                </p>
            </div>
        </div>'''

    def build(self) -> tuple[str, str]:
        """Build complete HTML email"""
        ts = datetime.now().strftime("%B %d, %Y")
        
        html = f"""<!DOCTYPE html>
<html lang="{self.lang}" dir="{self.dir}">
<head>
    <meta charset="UTF-8"/>
    <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
    <title>{self.title} | Delicious Recipe Guide</title>
    <meta name="description" content="Learn how to make {self.keyword.lower()} with this detailed recipe guide. Professional cooking tips and step-by-step instructions." />
    <meta name="keywords" content="{self.keyword}, recipe, cooking, food, how to make" />
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            color: #333;
            padding: 20px;
            margin: 0;
            line-height: 1.6;
        }}
        .container {{
            max-width: 850px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.12);
            position: relative;
        }}
        .container::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 6px;
            background: {self.theme['gradient']};
            border-radius: 16px 16px 0 0;
        }}
        .footer {{
            text-align: center;
            color: #888;
            font-size: 14px;
            margin-top: 50px;
            padding-top: 30px;
            border-top: 2px dashed #e0e0e0;
        }}
        @media (max-width: 600px) {{
            body {{ padding: 0; }}
            .container {{
                border-radius: 0;
                padding: 25px 20px;
            }}
            h1 {{ font-size: 28px !important; }}
            h2 {{ font-size: 22px !important; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        {self._hero_image()}
        {self._intro_box()}
        {self._smart_sections()}
        {self._conclusion_box()}
        
        <div class="footer">
            <p style="margin: 0 0 10px 0; font-size: 15px;">
                <strong>Published:</strong> {ts} &nbsp;•&nbsp; 
                <strong>Category:</strong> {self.keyword}
            </p>
            <p style="margin: 0; color: #aaa; font-size: 13px;">
                Article ID: {self.content_hash(self.a)} &nbsp;•&nbsp; 
                Made with ❤️ by Smart Publishing Engine
            </p>
        </div>
    </div>
</body>
</html>"""
        return self.title, html

# ============================================================
# 🌉 WEBHOOK SENDER
# ============================================================

class WebhookSender:
    def __init__(self):
        self.webhook_url = cfg.PIPEDREAM_WEBHOOK

    def send(self, subject: str, html: str) -> bool:
        payload = {
            "subject": subject,
            "html": html,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        data = json.dumps(payload).encode('utf-8')
        
        for attempt in range(1, cfg.HTTP_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    self.webhook_url,
                    data=data,
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                
                with urllib.request.urlopen(req, timeout=30) as response:
                    if 200 <= response.getcode() < 300:
                        logger.info("[WEBHOOK] ✅ Sent: %s", subject)
                        return True
                        
            except Exception as e:
                logger.warning("[WEBHOOK] ⚠️  Attempt %d/%d failed: %s", 
                              attempt, cfg.HTTP_RETRIES, str(e))
                if attempt < cfg.HTTP_RETRIES:
                    time.sleep(cfg.HTTP_RETRY_WAIT * attempt)
        
        logger.error("[WEBHOOK] ❌ Failed: %s", subject)
        return False

# ============================================================
# 🗂️  LANGUAGE WORKER
# ============================================================

class LanguageWorker:
    def __init__(self, lang_code: str, sender: WebhookSender):
        self.lang = lang_code
        self.meta = LANG_META[lang_code]
        self.sender = sender
        self.log = PublishLog(self.meta["log_file"])
        self.quota = random.randint(cfg.QUOTA_MIN, cfg.QUOTA_MAX)

    def _pending(self) -> list[dict]:
        arts = FileStore.read_json(self.meta["articles_file"])
        return [a for a in arts if not self.log.is_published(a["id"])]

    def _wait(self):
        secs = random.randint(cfg.INTERVAL_MIN, cfg.INTERVAL_MAX) * 60
        hours = secs / 3600
        logger.info("[%s] ⏳ Next in %.2f hours", self.lang.upper(), hours)
        time.sleep(secs)

    def run(self):
        logger.info("[%s] 🚀 Started | Quota: %d", self.lang.upper(), self.quota)
        
        while True:
            if self.log.count_today() >= self.quota:
                logger.info("[%s] ✅ Quota reached. Sleeping...", self.lang.upper())
                time.sleep(3600)
                continue

            pending = self._pending()
            if not pending:
                logger.info("[%s] ⚠️  No articles. Sleeping...", self.lang.upper())
                time.sleep(300)
                continue

            article = pending[0]
            builder = ArticleBuilder(article, self.meta)
            subject, html = builder.build()

            if self.sender.send(subject, html):
                self.log.mark_published(article)

            self._wait()

# ============================================================
# 🌱 KEEP-ALIVE
# ============================================================

class KeepAliveThread(threading.Thread):
    def __init__(self, interval: int, port: int = 5000):
        super().__init__(daemon=True)
        self.interval = interval
        self.url = f"http://127.0.0.1:{port}/health"

    def run(self):
        while True:
            time.sleep(self.interval)
            try:
                urllib.request.urlopen(self.url, timeout=5)
            except:
                pass

# ============================================================
# 🌐 FLASK APP
# ============================================================

app = Flask(__name__)

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/status")
def status():
    out = {}
    for code, meta in LANG_META.items():
        log = PublishLog(meta["log_file"])
        arts = FileStore.read_json(meta["articles_file"])
        pend = [a for a in arts if not log.is_published(a["id"])]
        out[code] = {
            "published_today": log.count_today(),
            "pending": len(pend),
            "total": len(arts)
        }
    return jsonify(out), 200

@app.route("/preview/<int:article_id>")
def preview(article_id):
    arts = FileStore.read_json(cfg.ARTICLES_EN)
    match = [a for a in arts if a["id"] == article_id]
    if not match:
        return jsonify({"error": "Not found"}), 404
    _, html = ArticleBuilder(match[0], LANG_META["en"]).build()
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}

# ============================================================
# 🚀 STARTUP
# ============================================================

def start_workers():
    logger.info("🚀 SMART PUBLISHING ENGINE — Enhanced Version")
    logger.info("📁 Articles: %s", cfg.ARTICLES_EN)
    logger.info("🌉 Webhook: %s", cfg.PIPEDREAM_WEBHOOK)
    
    sender = WebhookSender()
    
    for code in LANG_META:
        worker = LanguageWorker(code, sender)
        t = threading.Thread(target=worker.run, daemon=True)
        t.start()
        logger.info("✅ Worker started: %s", code.upper())
    
    port = int(os.getenv("PORT", "5000"))
    ka = KeepAliveThread(cfg.KEEPALIVE_INTERVAL, port=port)
    ka.start()
    logger.info("✅ KeepAlive started")

if not os.getenv("GUNICORN_WORKER_STARTED"):
    os.environ["GUNICORN_WORKER_STARTED"] = "1"
    start_workers()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
