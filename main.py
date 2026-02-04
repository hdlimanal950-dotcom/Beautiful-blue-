"""
============================================================
  SMART PUBLISHING ENGINE — Render-Ready Production Server
  Supports: Arabic | English | French
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
    ARTICLES_AR:    str = str(DATA_DIR / "articles_ar.json")
    ARTICLES_EN:    str = str(DATA_DIR / "articles_en.json")
    ARTICLES_FR:    str = str(DATA_DIR / "articles_fr.json")
    LOG_AR:         str = str(DATA_DIR / "log_ar.txt")
    LOG_EN:         str = str(DATA_DIR / "log_en.txt")
    LOG_FR:         str = str(DATA_DIR / "log_fr.txt")

    # --- Health-check keep-alive (Render kills idle free-tier after 15 min) ---
    KEEPALIVE_INTERVAL: int = int(os.getenv("KEEPALIVE_INTERVAL", "540"))  # 9 min


cfg = Config()

# ============================================================
# 🗣️  LANGUAGE REGISTRY — Central language meta
# ============================================================

LANG_META = {
    "ar": {
        "code": "ar",
        "dir": "rtl",
        "label": "العربية",
        "articles_file": cfg.ARTICLES_AR,
        "log_file": cfg.LOG_AR,
        "sections": ["المقدمة", "التفاصيل والأساسيات", "الأساليب والأدوات", "نصائح عملية", "الخلاصة"],
        "related_label": "مقالات ذات صلة قد تهمك",
        "conclusion_template": "في نهاية المطاف، موضوع <strong>{keyword}</strong> من أهم المواضيع التي يجب أن تعنى بها. استمر في البحث والتعلم ولا تتوقف.",
        "intro_prefix": "في هذا المقال سنتحدث عن",
        "tag_prefix": "📌",
        "published_label": "نُشر في",
    },
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
    },
    "fr": {
        "code": "fr",
        "dir": "ltr",
        "label": "Français",
        "articles_file": cfg.ARTICLES_FR,
        "log_file": cfg.LOG_FR,
        "sections": ["Introduction", "Notions Fondamentales", "Methods & Tools", "Conseils Pratiques", "Conclusion"],
        "related_label": "Articles liés qui pourront vous intéresser",
        "conclusion_template": "En conclusion, le sujet de <strong>{keyword}</strong> est l'un des sujets les plus importants à maîtriser. Continuez à apprendre et à explorer — la réussite vient avec la persévérance.",
        "intro_prefix": "Dans cet article, nous allons explorer",
        "tag_prefix": "📌",
        "published_label": "Publié le",
    },
}

# ============================================================
# 📦 SAMPLE ARTICLES — Seed data per language
# ============================================================

SEED_ARTICLES = {
    "ar": [
        {
            "id": 1,
            "title": "كيف تبدأ مدونة ناجحة من الصفر",
            "keyword": "إنشاء مدونة",
            "body": "إنشاء مدونة ناجحة من الصفر ليس أمراً صعباً كما تظن. في البداية تحتاج إلى اختيار موضوع تحب الكتابة فيه. الخطوة الأولى هي البحث عن نيش مربح ومطلوب في السوق. ثم تأتي مرحلة التخطيط الاستراتيجي للمحتوى الذي ستنشره. من المهم أن تكتب محتوى أصيلاً ومفيداً يخدم القارئ فعلياً. استخدم الأدوات المجانية مثل Google Keyword Planner للبحث عن كلمات مفتاحية. الاتساق في النشر هو المفتاح الحقيقي للنجاح على المدى الطويل. كلما نشرت محتوى جيداً بانتظام زاد عدد زوارك تدريجياً. تذكر أن النجاح لا يأتي في يوم واحد فالصبر هو سلاحك الأقوى.",
            "image_url": "https://picsum.photos/seed/ar1/800/400",
            "internal_links": ["https://yoursite.com/seo-guide", "https://yoursite.com/content-strategy"]
        },
        {
            "id": 2,
            "title": "أفضل استراتيجيات التسويق الرقمي في 2025",
            "keyword": "التسويق الرقمي",
            "body": "التسويق الرقمي في عام 2025 أصبح ركيزة أساسية لكل مشروع تجاري. الأعمال التي تعتمد على التسويق الرقمي تنمو أسرع بكثير من غيرها. من أهم الاستراتيجيات تحسين محركات البحث والتسويق عبر وسائل التواصل. كما أن التسويق بالمحتوى يُعدّ من أقوى الأدوات المتاحة في السوق الحالية. إنشاء محتوى فيديو قصير زاد من فعالية التسويق بنسبة كبيرة جداً. استخدم البيانات والتحليلات لفهم جمهورك بشكل أعمق ودقيق. التوظيف الصحيح للذكاء الاصطناعي يمكن أن يعزز حملاتك بشكل ملحوظ. لا تنسى أهمية التفاعل مع الجمهور والرد على التعليقات والرسائل بشكل منتظم.",
            "image_url": "https://picsum.photos/seed/ar2/800/400",
            "internal_links": ["https://yoursite.com/social-media", "https://yoursite.com/analytics"]
        },
        {
            "id": 3,
            "title": "دليل شامل في البرمجة بلغة Python",
            "keyword": "البرمجة بلغة Python",
            "body": "البرمجة بلغة Python هي من أسهل وأقوى لغات البرمجة في العالم اليوم. بدأت البرمجة بلغة Python في التسعينيات لكنها انتشرت بشكل ضخم في العقد الأخير. من أبرز مميزات Python وضوح الصيغة البرمجية البسيطة والسهلة القراءة. يُستخدم Python في مجالات كثيرة من بينها الذكاء الاصطناعي والتحليل البياني. للبدء في تعلم Python يكفيك تثبيت الـ interpreter من الموقع الرسمي للغة. هناك آلاف المكتبات المجانية التي توسّع قدرات اللغة بشكل هائل ومذهل. من المستحسن أن تبدأ بكتابة مشاريع صغيرة قبل أن تنتقل للمشاريع الكبيرة. المجتمع البرمجي حول Python ضخم ومفيد وستجد دعماً كافياً في كل مكان.",
            "image_url": "https://picsum.photos/seed/ar3/800/400",
            "internal_links": ["https://yoursite.com/python-basics", "https://yoursite.com/web-dev"]
        },
    ],
    "en": [
        {
            "id": 1,
            "title": "How to Start a Successful Blog From Scratch",
            "keyword": "Starting a Blog",
            "body": "Starting a successful blog from scratch is not as difficult as you might think. The first step is choosing a niche that you genuinely enjoy writing about. Research is critical — use tools like Google Keyword Planner to find profitable topics. Planning your content calendar before you publish anything saves enormous time in the long run. Always write original content that genuinely solves problems for your readers. Consistency in publishing is the single most important factor for long-term growth. The more quality content you publish on a regular schedule the faster your audience grows. Patience is essential because results in blogging compound over time rather than appearing instantly.",
            "image_url": "https://picsum.photos/seed/en1/800/400",
            "internal_links": ["https://yoursite.com/seo-101", "https://yoursite.com/blogging-tips"]
        },
        {
            "id": 2,
            "title": "Top Digital Marketing Strategies for 2025",
            "keyword": "Digital Marketing",
            "body": "Digital marketing in 2025 has become the backbone of every modern business strategy. Businesses that invest in digital marketing consistently outperform those relying on traditional channels alone. Search engine optimization remains one of the most powerful and cost-effective strategies available today. Content marketing combined with social media distribution creates a compounding growth engine for any brand. Short-form video content has dramatically increased engagement rates across all major platforms worldwide. Data-driven decision making allows marketers to optimize campaigns in real time for maximum impact. Artificial intelligence tools are now automating routine tasks and freeing teams to focus on creative strategy. Never underestimate the power of genuine audience interaction through comments replies and direct messages.",
            "image_url": "https://picsum.photos/seed/en2/800/400",
            "internal_links": ["https://yoursite.com/social-strategy", "https://yoursite.com/data-analytics"]
        },
        {
            "id": 3,
            "title": "A Complete Guide to Python Programming",
            "keyword": "Python Programming",
            "body": "Python programming is widely regarded as one of the easiest and most powerful languages to learn today. Created in the early 1990s Python has exploded in popularity over the past decade across industries. The clean readable syntax of Python makes it a perfect first language for beginners entering the field. Python is used extensively in artificial intelligence machine learning data science web development and automation. Getting started is simple — download the official interpreter from python.org and begin experimenting immediately. The Python ecosystem contains thousands of free libraries that extend its capabilities into virtually every domain. Starting with small projects and gradually increasing complexity is the most effective learning strategy available. The Python community is exceptionally welcoming and you will find comprehensive support and resources everywhere online.",
            "image_url": "https://picsum.photos/seed/en3/800/400",
            "internal_links": ["https://yoursite.com/python-intro", "https://yoursite.com/web-frameworks"]
        },
    ],
    "fr": [
        {
            "id": 1,
            "title": "Comment créer un blog réussi à partir de zéro",
            "keyword": "Création de blog",
            "body": "Créer un blog réussi à partir de zéro n'est pas aussi difficile qu'il n'y paraît. La première étape consiste à choisir un sujet qui vous passionne vraiment en profondeur. La recherche est essentielle — utilisez des outils comme Google Keyword Planner pour identifier des niches rentables. Planifier votre calendrier de contenu avant de publier économise énormément de temps sur le long terme. Écrivez toujours du contenu original qui répond véritablement aux besoins de vos lecteurs cibles. La régularité dans la publication est le facteur le plus important pour une croissance durable et significative. Plus vous publiez du contenu de qualité régulièrement plus votre audience croît rapidement et naturellement. La patience est indispensable car les résultats dans le blogging s'accumulent progressivement au fil du temps.",
            "image_url": "https://picsum.photos/seed/fr1/800/400",
            "internal_links": ["https://yoursite.com/seo-guide-fr", "https://yoursite.com/conseils-blog"]
        },
        {
            "id": 2,
            "title": "Meilleures Stratégies du Marketing Numérique en 2025",
            "keyword": "Marketing Numérique",
            "body": "Le marketing numérique en 2025 est devenu le pilier de toute stratégie commerciale moderne et innovante. Les entreprises qui investissent dans le marketing numérique surperforment régulièrement celles qui restent sur les canaux traditionnels. L'optimisation des moteurs de recherche reste l'une des stratégies les plus puissantes et rentables disponibles actuellement. Le marketing par le contenu associé à la distribution sur les réseaux sociaux crée un moteur de croissance composant. Le contenu vidéo court a considérablement augmenté les taux d'engagement sur toutes les principales plateformes mondiales. La prise de décision basée sur les données permet aux marketeurs d'optimiser les campagnes en temps réel. Les outils d'intelligence artificielle automatisent désormais les tâches routinières libérant les équipes pour la stratégie créative. Ne sous-estimez jamais le pouvoir de l'interaction authentique avec votre audience via les commentaires et les messages.",
            "image_url": "https://picsum.photos/seed/fr2/800/400",
            "internal_links": ["https://yoursite.com/strategie-sociale", "https://yoursite.com/analyse-donnees"]
        },
        {
            "id": 3,
            "title": "Guide Complet de la Programmation Python",
            "keyword": "Programmation Python",
            "body": "La programmation Python est largement considérée comme l'une des langues les plus accessibles et puissantes à apprendre. Créé dans les années 1990 Python a connu une explosion de popularité au cours de la dernière décennie dans tous les secteurs. La syntaxe propre et lisible de Python la rend parfaite comme première langage pour les débutants qui souhaitent se lancer. Python est utilisé massivement en intelligence artificielle apprentissage automatique science des données et développement web. Commencer est simple — téléchargez l'interpréteur officiel depuis python.org et commencez à expérimenter immédiatement. L'écosystème Python contient des milliers de bibliothèques gratuites qui étendent ses capacités dans presque tous les domaines. Commencer par des projets petits puis progressivement augmenter la complexité est la stratégie d'apprentissage la plus efficace. La communauté Python est exceptionnellement accueillante et vous trouverez un soutien complet et des ressources partout en ligne.",
            "image_url": "https://picsum.photos/seed/fr3/800/400",
            "internal_links": ["https://yoursite.com/python-debut", "https://yoursite.com/frameworks-web"]
        },
    ],
}

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
    Builds a complete, SEO-optimised HTML email per language.
    Each language gets its own direction, sections, and wording.
    """

    def __init__(self, article: dict, lang_meta: dict):
        self.a        = article
        self.meta     = lang_meta
        self.title    = article["title"]
        self.keyword  = article["keyword"]
        self.body     = article["body"]
        self.img      = article.get("image_url", "")
        self.links    = article.get("internal_links", [])
        self.dir      = lang_meta["dir"]
        self.lang     = lang_meta["code"]

    # ── Unique content hash (for dedup fingerprint) ──
    @staticmethod
    def content_hash(article: dict) -> str:
        raw = f"{article['id']}:{article['title']}:{article['keyword']}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    # ── Meta tags ──
    def _meta(self) -> str:
        desc = ". ".join(self.body.split(".")[:2]).strip()
        if not desc.endswith("."): desc += "."
        return (
            f'<meta name="description" content="{desc}" />\n'
            f'<meta name="keywords"    content="{self.keyword}" />\n'
            f'<meta name="language"    content="{self.lang}" />\n'
        )

    # ── Featured image ──
    def _image(self) -> str:
        if not self.img: return ""
        return (
            f'<div style="text-align:center;margin-bottom:24px;">'
            f'<img src="{self.img}" alt="{self.title}" '
            f'style="max-width:100%;height:auto;border-radius:10px;box-shadow:0 4px 12px rgba(0,0,0,0.12);" />'
            f'</div>'
        )

    # ── H1 ──
    def _h1(self) -> str:
        align = "center" if self.dir == "rtl" else "left"
        return f'<h1 style="color:#1a1a2e;text-align:{align};line-height:1.3;">{self.title}</h1>'

    # ── Introduction paragraph (keyword in first 100 words) ──
    def _intro(self) -> str:
        words = self.body.split()[:55]
        intro = " ".join(words)
        if self.keyword not in intro:
            intro = f"{self.keyword} — {intro}"
        section_title = self.meta["sections"][0]
        return (
            f'<h2 style="color:#16213e;border-bottom:2px solid #e94560;padding-bottom:6px;">{section_title}</h2>\n'
            f'<p style="line-height:1.9;font-size:15px;color:#444;">{intro}...</p>\n'
        )

    # ── Body split into H2 sections ──
    def _body(self) -> str:
        sentences = [s.strip() for s in self.body.split(".") if s.strip()]
        # skip first 2 (used in intro)
        remaining = sentences[2:]
        sections  = self.meta["sections"][1:]  # skip "Introduction"
        chunk     = 3
        html      = ""
        for i, start in enumerate(range(0, len(remaining), chunk)):
            part  = remaining[start:start + chunk]
            title = sections[i] if i < len(sections) else sections[-1]
            para  = ". ".join(part) + "."
            html += (
                f'<h2 style="color:#16213e;border-bottom:2px solid #e94560;padding-bottom:6px;">{title}</h2>\n'
                f'<p style="line-height:1.9;font-size:15px;color:#444;">{para}</p>\n'
            )
        return html

    # ── Conclusion (keyword repeated) ──
    def _conclusion(self) -> str:
        title = self.meta["sections"][-1]  # "Conclusion" / "الخلاصة" / "Conclusion"
        body  = self.meta["conclusion_template"].format(keyword=self.keyword)
        return (
            f'<h2 style="color:#16213e;border-bottom:2px solid #e94560;padding-bottom:6px;">{title}</h2>\n'
            f'<p style="line-height:1.9;font-size:15px;color:#444;">{body}</p>\n'
        )

    # ── Internal links ──
    def _links(self) -> str:
        if not self.links: return ""
        label = self.meta["related_label"]
        items = "".join(
            f'<li style="margin-bottom:6px;"><a href="{url}" style="color:#e94560;text-decoration:none;">{url.split("/")[-1].replace("-"," ").title()}</a></li>'
            for url in self.links
        )
        return f'<h3 style="color:#533483;">{label}</h3>\n<ul style="line-height:2;">{items}</ul>\n'

    # ── MASTER BUILD ──
    def build(self) -> tuple[str, str]:
        """Returns (subject, full_html)"""
        ts   = datetime.now().strftime("%d/%m/%Y")
        pfx  = self.meta["tag_prefix"]
        pub  = self.meta["published_label"]

        html = f"""<!DOCTYPE html>
<html lang="{self.lang}" dir="{self.dir}">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{self.title}</title>
{self._meta()}
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
  {self._image()}
  {self._h1()}
  {self._intro()}
  {self._body()}
  {self._conclusion()}
  {self._links()}
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
            FileStore.write_json(self.meta["articles_file"], SEED_ARTICLES[self.lang])
            logger.info("[%s] Seeded %d articles.", self.lang.upper(), len(SEED_ARTICLES[self.lang]))

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
_status: dict = {}   # { "ar": { "quota": int, "published_today": int, "pending": int }, … }

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
    if not body or not all(k in body for k in ("title", "keyword", "body")):
        return jsonify({"error": "Missing required fields: title, keyword, body"}), 400

    path     = LANG_META[lang]["articles_file"]
    articles = FileStore.read_json(path) if Path(path).exists() else []
    new_id   = max((a["id"] for a in articles), default=0) + 1
    article  = {
        "id":             new_id,
        "title":          body["title"],
        "keyword":        body["keyword"],
        "body":           body["body"],
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
    logger.info(" Languages: %s", ", ".join(LANG_META.keys()))
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
