import os

# ── Lokal fallback credential'lar (import'lardan ÖNCE set edilmeli) ──────────
# mailer.py ve wa_cloud.py modül yüklenirken env'i okur,
# bu yüzden setdefault'lar her import'tan önce çalışmalı.
os.environ.setdefault("EMAIL_PROVIDER",   "smtp")
os.environ.setdefault("EMAIL_FROM",       "yigitnarinofficial@gmail.com")
os.environ.setdefault("EMAIL_FROM_NAME",  "Nexa CRM")
os.environ.setdefault("SMTP_HOST",        "smtp.gmail.com")
os.environ.setdefault("SMTP_PORT",        "587")
os.environ.setdefault("SMTP_USE_TLS",     "true")
os.environ.setdefault("SMTP_USERNAME",    "yigitnarinofficial@gmail.com")
os.environ.setdefault("SMTP_PASSWORD",    "gqzmkricuzhiwozh")
os.environ.setdefault("ENABLE_CUSTOMER_EMAIL_AUTOMATION", "true")
# GEMINI_API_KEY → Render Dashboard > Environment Variables

import time
import requests
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, send_file, send_from_directory, request as flask_request
from bs4 import BeautifulSoup
from flask_cors import CORS
from wa_cloud import send_whatsapp, send_whatsapp_template, wa_status, verify_webhook_token
from mailer import (
    send_transactional_email, build_lead_confirmation_email, email_status,
    build_valuation_report_email, build_advisor_valuation_email,
)
from valuation import generate_valuation_report, valuation_status as gemini_status

# ── Firebase Admin SDK ──────────────────────────────────────────
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, firestore as admin_firestore, auth as fb_auth
from google.cloud.firestore_v1.base_query import FieldFilter

app = Flask(__name__)
CORS(app)

# ================================================================
# AYARLAR
# ================================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8462430471:AAEM_AjKYLKKVFpBsxGDkNmN91H77XHS81g")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "6183709337")
SERVICE_ACCOUNT    = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "service-account.json")


# WhatsApp Cloud API — Meta
# WA_PHONE_NUMBER_ID : Meta Business → WhatsApp → Phone Number ID
# WA_ACCESS_TOKEN    : System User permanent token
# WA_ADVISOR_PHONE   : Danışmanın WA numarası (bildirim alacak)
WA_ADVISOR_PHONE   = os.environ.get("WA_ADVISOR_PHONE", "905324514008")
CUSTOMER_WA_TEMPLATE_NAME = os.environ.get("CUSTOMER_WA_TEMPLATE_NAME", "").strip()
ENABLE_CUSTOMER_EMAIL_AUTOMATION = os.environ.get("ENABLE_CUSTOMER_EMAIL_AUTOMATION", "true").strip().lower() in ("1", "true", "yes")
ENABLE_CUSTOMER_WA_AUTOMATION    = os.environ.get("ENABLE_CUSTOMER_WA_AUTOMATION", "false").strip().lower() in ("1", "true", "yes")

# Değerleme raporu — yeni
VALUATION_WA_TEMPLATE_NAME = os.environ.get("VALUATION_WA_TEMPLATE_NAME", "").strip()
ADVISOR_EMAIL               = os.environ.get("ADVISOR_EMAIL", "").strip()

# İlan hedef URL
TARGET_URL = "https://www.cb.com.tr/ilanlar?officeid=372&officeuserid=18631"

# Ankara koordinatları (fallback)
ANKARA_LAT = 39.9334
ANKARA_LNG = 32.8597
DIKMEN_LAT = 39.8854
DIKMEN_LNG = 32.8514

ANKARA_SEMTLER = [
    "Dikmen", "Çukurambar", "Birlik Mahallesi", "Çayyolu",
    "Oran", "Angora Evleri", "Beysukent",
    "Kızılay", "Tunalı", "Ayrancı", "Gaziosmanpaşa", "GOP",
    "Kavaklidere", "Kavaklıdere", "Çankaya",
    "Balgat", "Emek", "Bahçelievler", "Öveçler",
    "Güvenevler", "Yıldız", "Çetin Emeç", "Mustafa Kemal",
    "Aziziye", "Naci Çakır",
    "Keçiören", "Mamak", "Altındağ", "Sincan",
    "Etimesgut", "Gölbaşı", "Pursaklar", "Yenimahalle",
    "Bağlıca", "Batıkent", "Eryaman",
]

# ── Ankara mahalle/semt koordinat sözlüğü (Nominatim fallback önce bunları dener) ──
# Geocoding başarısız olduğunda bile ilanlar doğru konuma düşer.
ANKARA_COORDS: dict = {
    "DİKMEN":           (39.8854, 32.8514),
    "ÇUKURAMBAR":       (39.9038, 32.8106),
    "BİRLİK MAHALLESİ":(39.9150, 32.8010),
    "ÇAYYOLU":          (39.8586, 32.7361),
    "ORAN":             (39.8771, 32.8233),
    "ANGORA EVLERİ":    (39.8640, 32.7790),
    "BEYSUKENT":        (39.8530, 32.7080),
    "KIZILAY":          (39.9208, 32.8541),
    "TUNALI":           (39.9068, 32.8613),
    "AYRANCI":          (39.9010, 32.8620),
    "GAZİOSMANPAŞA":    (39.9100, 32.8440),
    "GOP":              (39.9100, 32.8440),
    "KAVAKLIDERESİ":    (39.9040, 32.8640),
    "KAVAKLİDERE":      (39.9040, 32.8640),
    "ÇANKAYA":          (39.9033, 32.8597),
    "BALGAT":           (39.8922, 32.8108),
    "EMEK":             (39.9220, 32.7970),
    "BAHÇELİEVLER":     (39.9240, 32.8050),
    "ÖVEÇLEREVLERİ":    (39.8700, 32.8390),
    "ÖVEÇLER":          (39.8700, 32.8390),
    "GÜVENEVLERİ":      (39.9060, 32.8350),
    "GÜVENEVLER":       (39.9060, 32.8350),
    "YILDIZ":           (39.9100, 32.8220),
    "ÇETİN EMEÇ":       (39.8810, 32.8160),
    "MUSTAFA KEMAL":    (39.9180, 32.7850),
    "AZİZİYE":          (39.8770, 32.8360),
    "NACİ ÇAKIR":       (39.8800, 32.8540),
    "KEÇİÖREN":         (39.9750, 32.8640),
    "MAMAK":            (39.9320, 32.9380),
    "ALTINDAĞ":         (39.9540, 32.8780),
    "SİNCAN":           (39.9730, 32.5820),
    "ETİMESGUT":        (39.9490, 32.6890),
    "GÖLBAŞI":          (39.7890, 32.8040),
    "PURSAKLAR":        (40.0310, 32.8960),
    "YENİMAHALLE":      (39.9680, 32.8270),
    "BAĞLICA":          (39.9580, 32.7310),
    "BATIKENT":         (39.9690, 32.7250),
    "ERYAMAN":          (39.9810, 32.6680),
    "İNCEK":            (39.8200, 32.7900),
    "KONUTKENT":        (39.8700, 32.7450),
    "ÜMİTKÖY":          (39.8680, 32.7250),
    "ÇAYYOLU":          (39.8586, 32.7361),
    "KORU":             (39.8770, 32.7590),
    "KARŞIYAKA":        (39.9210, 32.8700),
    "DEMETEVLER":       (39.9780, 32.8010),
    "KALABA":           (39.9480, 32.9100),
    "HİPODROM":         (39.9380, 32.8640),
    "ULUS":             (39.9440, 32.8540),
    "SIHHIYE":          (39.9310, 32.8540),
    "SIHHİYE":          (39.9310, 32.8540),
    "BEŞTEPE":          (39.9330, 32.8040),
    "YUKARIDİKMEN":     (39.8780, 32.8490),
    "YUKARI DİKMEN":    (39.8780, 32.8490),
    "AŞAĞIDİKMEN":      (39.8920, 32.8550),
    "AŞAĞI DİKMEN":     (39.8920, 32.8550),
}

# ================================================================
# FİREBASE ADMIN — başlatma
# ================================================================
_fb_initialized = False
db_admin = None

def init_firebase_admin():
    global _fb_initialized, db_admin
    if _fb_initialized:
        return
    try:
        import json as _json

        # Render'da FIREBASE_SERVICE_ACCOUNT env var'ı JSON string içerir.
        # Lokal'de ise service-account.json dosya yoludur.
        # İkisini de destekle:
        sa_value = SERVICE_ACCOUNT.strip()

        if os.path.exists(sa_value):
            # Dosya yolu → klasik yöntem
            cred = credentials.Certificate(sa_value)
            print("✅ Firebase Admin bağlandı (dosya)")
        elif sa_value.startswith("{"):
            # JSON string içeriği → dict'e parse et
            sa_dict = _json.loads(sa_value)
            cred = credentials.Certificate(sa_dict)
            print("✅ Firebase Admin bağlandı (env JSON)")
        else:
            print(f"⚠️  Firebase service account bulunamadı — "
                  f"FIREBASE_SERVICE_ACCOUNT ortam değişkeni JSON string ya da geçerli dosya yolu olmalı")
            return

        firebase_admin.initialize_app(cred)
        db_admin = admin_firestore.client()
        _fb_initialized = True
    except Exception as e:
        print(f"❌ Firebase Admin hatası: {e}")


# ================================================================
# TELEGRAM
# ================================================================
def send_telegram(text: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=10)
        return resp.ok
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")
        return False


# ================================================================
# SAYFA ROUTE'LARI
# ================================================================

@app.route("/")
def home():
    """Web sitesi — site.html"""
    try:
        return send_file("site.html")
    except Exception as e:
        return f"site.html bulunamadı: {e}", 404


@app.route("/crm")
def crm():
    """CRM paneli — crm.html"""
    try:
        return send_file("crm.html")
    except Exception as e:
        return f"crm.html bulunamadı: {e}", 404


# ================================================================
# PORTFÖY — Google Drive HTML Sunumu
# ================================================================

_drive_lock = threading.Lock()
_portfoy_status: dict = {"error": None, "running": False, "last_attempt": 0}


def _run_drive_to_html():
    """drive_to_html.main() fonksiyonunu doğrudan çağırır. Hataları açıkça loglar."""
    try:
        import drive_to_html as _d2h

        # Kritik env kontrolleri — boşsa anlamlı hata ver
        sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "").strip()
        folder = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
        if not sa:
            raise EnvironmentError(
                "GOOGLE_SERVICE_ACCOUNT env var tanımlı değil! "
                "Render Dashboard → Environment → bu değişkeni JSON string olarak ekle."
            )
        if not folder:
            raise EnvironmentError(
                "GDRIVE_FOLDER_ID env var tanımlı değil! "
                "Google Drive klasör URL'sinden ID'yi al ve Render'a ekle."
            )

        _d2h.main()
        return True, None
    except Exception as e:
        import traceback
        msg = str(e)
        print(f"❌ drive_to_html hatası: {msg}")
        traceback.print_exc()
        return False, msg


def _trigger_portfoy_bg():
    """
    sunum.html yoksa veya force=True ise arka planda üretim başlatır.
    _drive_lock alınamazsa (zaten çalışıyor) sessizce çıkar.
    """
    if not _drive_lock.acquire(blocking=False):
        return  # zaten çalışıyor

    _portfoy_status["running"] = True
    _portfoy_status["last_attempt"] = time.time()

    def _bg():
        try:
            ok, err = _run_drive_to_html()
            if ok:
                print("✅ Portföy HTML üretildi")
                _portfoy_status["error"] = None
            else:
                print(f"⚠️  Portföy HTML üretimi başarısız: {err}")
                _portfoy_status["error"] = err
        finally:
            _portfoy_status["running"] = False
            _drive_lock.release()

    threading.Thread(target=_bg, daemon=True).start()


@app.route("/portfoy")
def portfoy():
    """Proje portföy sayfası — drive_to_html tarafından üretilen sunum.html"""
    sunum = Path("sunum.html")

    if sunum.exists():
        return send_file("sunum.html")

    # sunum.html yok — üretimi tetikle (eğer çalışmıyorsa)
    _trigger_portfoy_bg()

    err_msg = _portfoy_status.get("error")
    running = _portfoy_status.get("running", False)

    if err_msg:
        # Hata varsa kullanıcıya göster
        err_html = f"""
  <div style="background:#1a0a0a;border:1px solid #7f1d1d;border-radius:8px;padding:20px 28px;max-width:600px;text-align:left;">
    <p style="color:#f87171;font-size:.8rem;margin-bottom:8px;letter-spacing:.1em">HATA DETAYI</p>
    <pre style="color:#fca5a5;font-size:.72rem;white-space:pre-wrap;word-break:break-all">{err_msg}</pre>
  </div>
  <p style="font-size:.65rem;color:#555;margin-top:8px">Render Dashboard → Environment Variables kontrol edin</p>"""
        refresh_tag = '<meta http-equiv="refresh" content="30;url=/portfoy"/>'
    else:
        err_html = ""
        refresh_tag = '<meta http-equiv="refresh" content="15;url=/portfoy"/>'

    status_text = "Üretim çalışıyor…" if running else "Başlatılıyor…"

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8"/>
{refresh_tag}
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Portföy Hazırlanıyor — Nexa CRM</title>
<style>
  body{{background:#080810;color:#ede9df;font-family:"DM Mono",monospace;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;flex-direction:column;gap:20px;padding:20px}}
  .loader{{width:40px;height:40px;border:2px solid rgba(201,168,76,.2);border-top-color:#c9a84c;border-radius:50%;animation:spin 1s linear infinite}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
  p{{color:#9b9aac;font-size:.78rem;letter-spacing:.15em;text-transform:uppercase;margin:0}}
  a{{color:#c9a84c;font-size:.7rem;letter-spacing:.1em;text-decoration:none}}
  a:hover{{text-decoration:underline}}
</style>
</head>
<body>
  <div class="loader"></div>
  <p>Portföy hazırlanıyor — {status_text}</p>
  <p style="font-size:.68rem;color:#555">Google Drive taranıyor · ilk yükleme ~60-90 sn sürebilir</p>
  {err_html}
  <a href="/portfoy">↻ Yenile</a>
  <a href="/" style="color:#555">← Ana Sayfa</a>
</body>
</html>""", 202


@app.route("/portfoy/assets/<path:filename>")
def portfoy_assets(filename):
    """sunum_assets/ klasöründeki görseller, PDF'ler ve videolar."""
    assets_dir = Path("sunum_assets").resolve()
    return send_from_directory(assets_dir, filename)


@app.route("/api/drive/refresh", methods=["POST"])
def drive_refresh():
    """
    Drive'ı yeniden tara ve sunum.html'yi güncelle.
    Admin / danışman tarafından tetiklenir.
    """
    if not _drive_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Zaten çalışıyor, lütfen bekleyin."}), 429

    _portfoy_status["running"] = True
    _portfoy_status["last_attempt"] = time.time()

    def _bg():
        try:
            ok, err = _run_drive_to_html()
            _portfoy_status["error"] = None if ok else err
            _portfoy_status["running"] = False
            if not ok:
                print(f"❌ Drive refresh hatası: {err}")
        finally:
            _drive_lock.release()
            _portfoy_status["running"] = False

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"ok": True, "message": "Drive yenileme başlatıldı."})


def _refresh_portfoy_bg():
    """Sunucu başlarken portföy HTML'ini arka planda üret."""
    def _delayed():
        time.sleep(5)   # Firebase + diğer init tamamlansın
        _trigger_portfoy_bg()
    threading.Thread(target=_delayed, daemon=True).start()


# ================================================================
# API — İLAN SCRAPER
# ================================================================

import re as _re
import math as _math
import random as _random

_coord_cache: dict = {}
_last_nominatim_call: float = 0.0
_TR_MAP = str.maketrans("çğışöüÇĞİŞÖÜ", "cgisouCGISOu")
_jitter_counter: int = 0


def _normalize(text: str) -> str:
    """Türkçe karakterleri ASCII'ye çevirip büyük harfe dönüştürür."""
    return text.translate(_TR_MAP).upper()


# ── Ankara mahalle/semt koordinat sözlüğü ─────────────────────────────────────
# Nominatim'e gerek kalmadan yaygın semtleri doğru konuma düşürür.
ANKARA_COORDS: dict = {
    "DIKMEN":            (39.8854, 32.8514),
    "YUKARI DIKMEN":     (39.8780, 32.8490),
    "ASAGI DIKMEN":      (39.8920, 32.8550),
    "CUKURAMBAR":        (39.9038, 32.8106),
    "BIRLIK MAHALLESI":  (39.9150, 32.8010),
    "CAYYOLU":           (39.8586, 32.7361),
    "ORAN":              (39.8771, 32.8233),
    "ANGORA EVLERI":     (39.8640, 32.7790),
    "BEYSUKENT":         (39.8530, 32.7080),
    "KIZILAY":           (39.9208, 32.8541),
    "TUNALI":            (39.9068, 32.8613),
    "TUNAL":             (39.9068, 32.8613),
    "AYRANCI":           (39.9010, 32.8620),
    "GAZIOSMANPASA":     (39.9100, 32.8440),
    "GOP":               (39.9100, 32.8440),
    "KAVAKLIDERE":       (39.9040, 32.8640),
    "CANKAYA":           (39.9033, 32.8597),
    "BALGAT":            (39.8922, 32.8108),
    "EMEK":              (39.9220, 32.7970),
    "BAHCELIEVLER":      (39.9240, 32.8050),
    "OVECLER":           (39.8700, 32.8390),
    "GUVENEVLER":        (39.9060, 32.8350),
    "YILDIZ":            (39.9100, 32.8220),
    "CETIN EMEC":        (39.8810, 32.8160),
    "MUSTAFA KEMAL":     (39.9180, 32.7850),
    "AZIZIYE":           (39.8770, 32.8360),
    "NACI CAKIR":        (39.8800, 32.8540),
    "KECOREN":           (39.9750, 32.8640),
    "MAMAK":             (39.9320, 32.9380),
    "ALTINDAG":          (39.9540, 32.8780),
    "SINCAN":            (39.9730, 32.5820),
    "ETIMESGUT":         (39.9490, 32.6890),
    "GOLBASI":           (39.7890, 32.8040),
    "PURSAKLAR":         (40.0310, 32.8960),
    "YENIMAHALLE":       (39.9680, 32.8270),
    "BAGLICA":           (39.9580, 32.7310),
    "BATIKENT":          (39.9690, 32.7250),
    "ERYAMAN":           (39.9810, 32.6680),
    "INCEK":             (39.8200, 32.7900),
    "KONUTKENT":         (39.8700, 32.7450),
    "UMITKOY":           (39.8680, 32.7250),
    "KORU":              (39.8770, 32.7590),
    "KARSIYAKA":         (39.9210, 32.8700),
    "DEMETEVLER":        (39.9780, 32.8010),
    "KALABA":            (39.9480, 32.9100),
    "ULUS":              (39.9440, 32.8540),
    "SIHHIYE":           (39.9310, 32.8540),
    "BESTEPE":           (39.9330, 32.8040),
    "OSTIM":             (39.9620, 32.9090),
    "GIMAT":             (39.9560, 32.8830),
    "ELVANKENT":         (39.9440, 32.7010),
    "SUSUZKÖY":          (39.9900, 32.7400),
}


def _lookup_hardcoded(text: str):
    """
    Metin içinde ANKARA_COORDS'tan eşleşme arar.
    En uzun eşleşmenin koordinatını döner.
    """
    norm = _normalize(text)
    best = None
    best_len = 0
    for k, v in ANKARA_COORDS.items():
        if k in norm and len(k) > best_len:
            best = v
            best_len = len(k)
    return best


def _jittered(lat: float, lng: float) -> tuple:
    """
    Aynı koordinata düşen birden fazla marker'ın üst üste yığılmaması için
    ~30-70 m arası rastgele ofset ekler (altın açı dağılımı).
    """
    global _jitter_counter
    _jitter_counter += 1
    angle = (_jitter_counter * 137.508) % 360
    r = _random.uniform(0.0003, 0.0007)
    return (
        round(lat + r * _math.sin(_math.radians(angle)), 6),
        round(lng + r * _math.cos(_math.radians(angle)), 6),
    )


def geocode_query(query: str):
    """
    Nominatim ile geocode. bounded=0 (daha geniş tarama) + Ankara doğrulama.
    Sonuç Ankara'nın ~60 km dışındaysa geçersiz sayar.
    """
    global _last_nominatim_call
    if not query:
        return None
    key = query.lower().strip()
    if key in _coord_cache:
        return _coord_cache[key]

    elapsed = time.time() - _last_nominatim_call
    if elapsed < 1.2:
        time.sleep(1.2 - elapsed)

    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query,
                "format": "json",
                "limit": 5,
                "countrycodes": "tr",
                # bounded=0 bırak — viewbox öneri olarak kullan, zorunlu değil
                "viewbox": "32.3,40.2,33.4,39.5",
            },
            headers={"User-Agent": "NexaCRM/2.0 (contact@nexacrm.com)"},
            timeout=8,
        )
        _last_nominatim_call = time.time()
        data = resp.json()

        # Ankara bölgesi: lat 39.5–40.2 / lon 32.3–33.4
        for item in data:
            lat = float(item["lat"])
            lon = float(item["lon"])
            if 39.5 <= lat <= 40.2 and 32.3 <= lon <= 33.4:
                _coord_cache[key] = (lat, lon)
                return lat, lon

    except Exception as e:
        print(f"  Geocode hatası '{query[:50]}': {e}")

    _coord_cache[key] = None
    return None


def get_listing_coords(title: str, loc: str) -> tuple:
    """
    İlan için koordinat bulur. Strateji:
    1. Başlık + loc içinde sabit sözlükten mahalle eşleşmesi (hızlı, offline)
    2. Nominatim: mahalle + Çankaya/Ankara
    3. loc'tan temizlenmiş parçalarla Nominatim
    4. Dikmen fallback (jitter ile — marker'lar üst üste yığılmaz)
    """
    combined = f"{title} {loc}"

    # ── 1. Sabit sözlük ───────────────────────────────────────────────────
    coords = _lookup_hardcoded(combined)
    if coords:
        print(f"     📍 Sabit sözlük → {coords}")
        return _jittered(*coords)

    # ── 2. Nominatim — başlıktan çıkarılan mahalle ────────────────────────
    # ANKARA_SEMTLER listesinde en uzun eşleşeni bul
    norm_combined = _normalize(combined)
    best_semt = None
    best_len = 0
    for s in ANKARA_SEMTLER:
        sn = _normalize(s)
        if sn in norm_combined and len(sn) > best_len:
            best_semt = s
            best_len = len(sn)

    if best_semt:
        for q in [
            f"{best_semt}, Çankaya, Ankara",
            f"{best_semt}, Ankara",
        ]:
            coords = geocode_query(q)
            if coords:
                print(f"     🌐 Nominatim semt: {best_semt} → {coords}")
                return _jittered(*coords)

    # ── 3. loc'tan temizlenmiş parçalar ──────────────────────────────────
    # "Mah.", "Cad.", "No:5" gibi gürültüyü temizle, "Ankara" kelimesini at
    loc_clean = _re.sub(
        r"\b(Mah\.|Mah\b|Mahallesi|Cad\.|Caddesi|Sok\.|Sokak|Blv\.|Bulvarı|No:\s*\d+[\w/]*|\d+\s*/\s*\d+)\b",
        "", loc, flags=_re.IGNORECASE
    )
    parts = [
        p.strip() for p in _re.split(r"[,/]", loc_clean)
        if p.strip() and len(p.strip()) > 2
        and _normalize(p.strip()) not in ("ANKARA", "TR", "TURKIYE", "TÜRKIYE")
    ]
    for part in parts:
        coords = geocode_query(f"{part}, Ankara, Türkiye")
        if coords:
            print(f"     🌐 Nominatim loc: {part} → {coords}")
            return _jittered(*coords)

    # ── 4. Fallback ───────────────────────────────────────────────────────
    print(f"     ⚠️  Koordinat bulunamadı → Dikmen fallback | {title[:35]}")
    return _jittered(DIKMEN_LAT, DIKMEN_LNG)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}


def clean_text(element) -> str:
    return element.get_text(strip=True) if element else ""


def fetch_real_estate_data() -> list:
    print(f"📡 İstek gönderiliyor: {TARGET_URL}")
    try:
        response = requests.get(TARGET_URL, headers=HEADERS, timeout=20)
        if response.status_code != 200:
            print(f"❌ Bağlantı Hatası: {response.status_code}")
            return []

        soup = BeautifulSoup(response.content, "html.parser")
        listings = []
        cards = soup.select(".cb-list-item")
        print(f"🔎 Bulunan İlan Sayısı: {len(cards)}")

        for card in cards:
            try:
                title_el = card.select_one(".cb-list-item-info h2")
                title = clean_text(title_el)
                if not title:
                    continue

                price_el = card.select_one(".feature-item .text-primary")
                price = clean_text(price_el)

                link_el = card.select_one(".cb-list-img-container a")
                link = link_el["href"] if link_el else "#"
                if link and not link.startswith("http"):
                    link = "https://www.cb.com.tr" + link

                img_el = card.select_one(".cb-list-img-container img")
                img_url = "https://via.placeholder.com/400x300"
                if img_el:
                    img_url = img_el.get("src") or img_el.get("data-src") or img_url

                region_el = card.select_one('span[itemprop="addressRegion"]')
                street_el = card.select_one('span[itemprop="streetAddress"]')
                region = clean_text(region_el)
                street = clean_text(street_el)
                loc = f"{region}, {street}" if region and street else "Ankara"

                rooms = area = ""
                for feat in card.select(".feature-item"):
                    text = clean_text(feat)
                    if "m2" in text or "m²" in text:
                        area = text
                    elif "+" in text:
                        rooms = text

                lat, lng = get_listing_coords(title, loc)
                listings.append({
                    "title": title, "price": price, "loc": loc,
                    "img": img_url, "link": link, "rooms": rooms, "area": area,
                    "type": "Kiralık" if "Kiralık" in title else "Satılık",
                    "lat": lat, "lng": lng,
                })
            except Exception as e:
                print(f"⚠️ İlan parse hatası: {e}")
                continue

        print(f"✅ Toplam işlenen: {len(listings)} ilan")
        return listings
    except Exception as e:
        print(f"❌ Kritik Hata: {e}")
        return []


@app.route("/ilanlar")
def ilanlar():
    """İlanlar sayfası — ilanlar.html"""
    try:
        return send_file("ilanlar.html")
    except Exception as e:
        return f"ilanlar.html bulunamadı: {e}", 404


@app.route("/admin")
def admin():
    """Admin paneli — admin.html"""
    try:
        return send_file("admin.html")
    except Exception as e:
        return f"admin.html bulunamadı: {e}", 404


# ================================================================
# ================================================================
# ADMIN AUTH  — Firebase ID Token doğrulaması
# ================================================================

def _require_admin():
    """
    Firebase JS SDK'dan gelen idToken'ı doğrular.
    Başarılıysa (decoded_token, None), başarısızsa (None, hata_mesajı) döner.
    """
    if not _fb_initialized:
        print("⚠️  _require_admin: Firebase başlatılmamış")
        return None, "Firebase bağlı değil"
    auth_header = flask_request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        print(f"⚠️  _require_admin: Token başlığı eksik — {flask_request.path}")
        return None, "Token eksik"
    id_token = auth_header[7:]
    if not id_token or len(id_token) < 20:
        return None, "Token geçersiz (çok kısa)"
    try:
        decoded = fb_auth.verify_id_token(id_token)
        print(f"✅ Admin doğrulandı: {decoded.get('email','?')} — {flask_request.path}")
        return decoded, None
    except fb_auth.ExpiredIdTokenError:
        print("⚠️  _require_admin: Token süresi dolmuş")
        return None, "Oturum süresi doldu"
    except fb_auth.InvalidIdTokenError as e:
        print(f"⚠️  _require_admin: Geçersiz token — {e}")
        return None, "Geçersiz token"
    except Exception as e:
        print(f"❌ _require_admin beklenmedik hata: {type(e).__name__}: {e}")
        return None, f"Doğrulama hatası: {type(e).__name__}"


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    # Client tarafında token silindiği için backend'de yapılacak bir şey yok
    return jsonify({"ok": True})


# ================================================================
# WHATSAPP CLOUD API ROUTES
# ================================================================

@app.route("/api/wa/status", methods=["GET"])
def whatsapp_status():
    """Meta Graph API üzerinden WA phone number durumunu kontrol eder."""
    return jsonify(wa_status())


@app.route("/api/email/status", methods=["GET"])
def customer_email_status():
    """Transactional e-posta yapılandırma durumunu döner."""
    return jsonify(email_status())


@app.route("/api/wa/webhook", methods=["GET"])
def whatsapp_webhook_verify():
    """
    Meta webhook doğrulaması (GET).
    Meta Business → WhatsApp → Configuration → Webhook URL olarak kaydedin.
    Verify Token: WA_VERIFY_TOKEN env variable ile eşleşmeli.
    """
    mode      = flask_request.args.get("hub.mode")
    token     = flask_request.args.get("hub.verify_token")
    challenge = flask_request.args.get("hub.challenge")

    if mode == "subscribe" and verify_webhook_token(token):
        print("✅ WhatsApp webhook doğrulandı")
        return challenge, 200

    print(f"❌ Webhook doğrulama başarısız. Token: {token}")
    return "Forbidden", 403


@app.route("/api/wa/webhook", methods=["POST"])
def whatsapp_webhook_receive():
    """
    Meta'dan gelen mesaj/durum bildirimlerini alır (POST).
    Gelen mesajları Firestore wa_inbound koleksiyonuna kaydeder.
    """
    data = flask_request.get_json(silent=True) or {}

    try:
        entries = data.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # Gelen mesajlar
                for msg in value.get("messages", []):
                    from_phone = msg.get("from", "")
                    msg_type   = msg.get("type", "")
                    body       = msg.get("text", {}).get("body", "") if msg_type == "text" else f"[{msg_type}]"
                    timestamp  = msg.get("timestamp", "")
                    print(f"📥 WA gelen mesaj: {from_phone} → {body[:80]}")

                    if _fb_initialized:
                        db_admin.collection("wa_inbound").add({
                            "from":      from_phone,
                            "type":      msg_type,
                            "body":      body,
                            "timestamp": timestamp,
                            "raw":       msg,
                            "receivedAt": datetime.now(timezone.utc).isoformat(),
                        })

                # Mesaj durum güncellemeleri (sent/delivered/read/failed)
                for status in value.get("statuses", []):
                    msg_id     = status.get("id", "")
                    wa_status_ = status.get("status", "")
                    recipient  = status.get("recipient_id", "")
                    print(f"📊 WA durum: {msg_id} → {wa_status_} ({recipient})")

                    if _fb_initialized and msg_id:
                        # wa_message_log'daki kaydı güncelle
                        docs = (db_admin.collection("wa_message_log")
                                .where("messageId", "==", msg_id).limit(1).stream())
                        for doc in docs:
                            doc.reference.update({
                                "deliveryStatus": wa_status_,
                                "statusUpdatedAt": datetime.now(timezone.utc).isoformat(),
                            })

    except Exception as e:
        print(f"Webhook işleme hatası: {e}")

    # Meta her zaman 200 bekler
    return jsonify({"status": "ok"}), 200


@app.route("/api/wa/send", methods=["POST"])
def whatsapp_send():
    """
    Admin panelinden manuel WA mesajı göndermek için.
    Body: { phone: "905324514008", message: "..." }
    Korumalı endpoint — Firebase ID token gerektirir.
    """
    token, err = _require_admin()
    if err:
        return jsonify({"ok": False, "error": err}), 401

    body    = flask_request.get_json(silent=True) or {}
    phone   = body.get("phone", "")
    message = body.get("message", "")

    if not phone or not message:
        return jsonify({"ok": False, "error": "phone ve message zorunlu"}), 400

    result = send_whatsapp(phone, message)

    if result["ok"] and _fb_initialized:
        db_admin.collection("wa_message_log").add({
            "phone":     phone,
            "message":   message[:200],
            "messageId": result.get("message_id", ""),
            "source":    "admin_manual",
            "status":    "sent",
            "sentAt":    datetime.now(timezone.utc).isoformat(),
        })

    return jsonify(result)


# ================================================================
# BLOG API
# ================================================================

def _serialize_post(doc):
    """Firestore dokümanını JSON-safe dict'e çevirir."""
    d = doc.to_dict()
    d["id"] = doc.id
    for field in ["createdAt", "updatedAt"]:
        val = d.get(field)
        if val is None:
            d[field] = ""
        elif hasattr(val, "isoformat"):
            try:
                d[field] = val.isoformat()
            except Exception:
                d[field] = str(val)
        else:
            d[field] = str(val)
    return d


@app.route("/api/blog/posts", methods=["GET"])
def get_blog_posts():
    """Herkese açık — site.html buradan çeker."""
    if not _fb_initialized:
        return jsonify({"ok": False, "data": []}), 503
    try:
        query = (db_admin.collection("blogs")
                 .where(filter=FieldFilter("published", "==", True))
                 .limit(24))
        posts = [_serialize_post(doc) for doc in query.stream()]
        posts.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return jsonify({"ok": True, "data": posts})
    except Exception as e:
        print(f"get_blog_posts hatası: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/blog/all", methods=["GET"])
def get_all_blog_posts():
    """Admin paneli için — tüm yazılar."""
    token, err = _require_admin()
    if err:
        return jsonify({"ok": False, "error": err}), 401
    if not _fb_initialized:
        return jsonify({"ok": False, "data": []}), 503
    try:
        posts = [_serialize_post(doc) for doc in db_admin.collection("blogs").stream()]
        posts.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return jsonify({"ok": True, "data": posts})
    except Exception as e:
        print(f"get_all_blog_posts hatası: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/blog/posts", methods=["POST"])
def create_blog_post():
    token, err = _require_admin()
    if err:
        print(f"❌ Auth hatası: {err}")
        return jsonify({"ok": False, "error": err}), 401
    if not _fb_initialized:
        return jsonify({"ok": False, "error": "Firebase bağlı değil"}), 503

    data = flask_request.json or {}
    print(f"📝 Blog oluşturma isteği: {data.get('title', '(başlıksız)')}")

    now  = datetime.now(timezone.utc)
    post = {
        "title":     data.get("title", "").strip(),
        "summary":   data.get("summary", "").strip(),
        "content":   data.get("content", "").strip(),
        "image":     data.get("image", "").strip(),
        "category":  data.get("category", "Genel").strip(),
        "readTime":  data.get("readTime", "3 dk").strip(),
        "published": bool(data.get("published", True)),
        "createdAt": now,
        "updatedAt": now,
    }
    if not post["title"]:
        return jsonify({"ok": False, "error": "Başlık zorunlu"}), 400

    try:
        result = db_admin.collection("blogs").add(post)
        # result → (DatetimeWithNanoseconds, DocumentReference)
        doc_ref = result[1] if isinstance(result, tuple) else result
        doc_id  = doc_ref.id
        print(f"✅ Blog oluşturuldu: {doc_id}")
        return jsonify({"ok": True, "id": doc_id})
    except Exception as e:
        import traceback
        print(f"❌ create_blog_post hatası: {e}")
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/blog/posts/<post_id>", methods=["PUT"])
def update_blog_post(post_id):
    token, err = _require_admin()
    if err:
        return jsonify({"ok": False, "error": err}), 401
    if not _fb_initialized:
        return jsonify({"ok": False, "error": "Firebase bağlı değil"}), 503
    data    = flask_request.json or {}
    allowed = ["title", "summary", "content", "image", "category", "readTime", "published"]
    update  = {k: data[k] for k in allowed if k in data}
    update["updatedAt"] = datetime.now(timezone.utc)
    try:
        db_admin.collection("blogs").document(post_id).update(update)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/blog/posts/<post_id>", methods=["DELETE"])
def delete_blog_post(post_id):
    token, err = _require_admin()
    if err:
        return jsonify({"ok": False, "error": err}), 401
    if not _fb_initialized:
        return jsonify({"ok": False, "error": "Firebase bağlı değil"}), 503
    try:
        db_admin.collection("blogs").document(post_id).delete()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ================================================================
# API — İLAN SCRAPER
# ================================================================

_listings_cache = {"data": [], "ts": 0}
_listings_lock = threading.Lock()

def _refresh_listings_bg():
    def _run():
        data = fetch_real_estate_data()
        with _listings_lock:
            _listings_cache["data"] = data
            _listings_cache["ts"]   = time.time()
    threading.Thread(target=_run, daemon=True).start()

@app.route("/api/listings", methods=["GET"])
def get_listings():
    now = time.time()
    if now - _listings_cache["ts"] < 300 and _listings_cache["data"]:
        return jsonify({"success": True, "data": _listings_cache["data"]})
    _refresh_listings_bg()
    return jsonify({"success": True, "data": _listings_cache["data"]})


# ── CB İlan Detay Önizleme ────────────────────────────────────────────────────
@app.route("/api/listing/preview", methods=["GET"])
def listing_preview():
    """
    a.py / scrape_detail() mantığıyla CB ilan detay sayfasını scrape eder.
    Query : ?url=https://www.cb.com.tr/...
    Return: {ok, title, price, location, rooms, sqm, type, status,
             cb_url, images:[str,...], features:[{label,value},...],
             description, agent:{name,img,office}}
    """
    import re as _re
    from urllib.parse import urlparse

    BASE = "https://www.cb.com.tr"

    cb_url = flask_request.args.get("url", "").strip()
    parsed = urlparse(cb_url)
    if parsed.scheme not in ("http", "https") or \
       parsed.netloc not in ("www.cb.com.tr", "cb.com.tr"):
        return jsonify({"ok": False, "error": "Sadece cb.com.tr URL desteklenir"}), 400

    try:
        resp = requests.get(cb_url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return jsonify({"ok": False, "error": f"HTTP {resp.status_code}"}), 502

        soup = BeautifulSoup(resp.content, "lxml" if __import__("importlib").util.find_spec("lxml") else "html.parser")

        # ── Başlık ──────────────────────────────────────────────────────────
        title = clean_text(soup.select_one("h1") or soup.select_one("h2")) or "İlan Detayı"

        # ── Fiyat ───────────────────────────────────────────────────────────
        price = ""
        for sel in [".feature-item .text-primary",
                    ".price-box .price",
                    "[class*='price']",
                    ".cb-detail-header .price"]:
            el = soup.select_one(sel)
            if el:
                price = _re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
                if price:
                    break
        if not price:
            for row in soup.select("table tr"):
                cells = row.find_all("td")
                if len(cells) >= 2 and "Fiyat" in cells[0].get_text():
                    price = clean_text(cells[1])
                    break

        # ── Lokasyon ────────────────────────────────────────────────────────
        location = ""
        hdr = soup.select_one(".cb-detail-header")
        if hdr:
            parts = [clean_text(s) for s in hdr.select("p .text-secondary") if clean_text(s)]
            location = " / ".join(parts)
        if not location:
            r_el = soup.select_one('[itemprop="addressRegion"]')
            s_el = soup.select_one('[itemprop="streetAddress"]')
            location = " / ".join(clean_text(e) for e in [r_el, s_el] if e and clean_text(e))

        # ── İlan tipi / durumu ──────────────────────────────────────────────
        url_l = cb_url.lower()
        status = "Kiralık" if "kiralik" in url_l else "Satılık"
        path_parts = cb_url.rstrip("/").split("/")
        prop_type = path_parts[-2].replace("-", " ").title() if len(path_parts) >= 2 else "—"
        badge = soup.select_one(".price-box .badge")
        if badge:
            status = clean_text(badge)

        # ── Görseller — a.py scrape_detail() mantığı ────────────────────────
        images = []
        seen_srcs = set()

        def _add_img(src):
            src = src.strip()
            if not src or "placeholder" in src or "icon" in src.lower():
                return
            if src.startswith("/"):
                src = BASE + src
            # Thumbnail URL'lerini yüksek çözünürlüklü versiyona yükselt
            # CB formatı: _410X261.jpg → _1000X664.jpg
            import re as _rx
            src_hires = _rx.sub(r'_\d+X\d+(\.[a-z]+)$', r'_1000X664\1', src, flags=_rx.IGNORECASE)
            # Görsel zaten listede mi? Dosya adını karşılaştır
            fname = src_hires.split("/")[-1].split("_")[0]
            if fname in seen_srcs:
                return
            seen_srcs.add(fname)
            images.append(src_hires)

        # 1) Bilinen slider seçicileri (öncelik sırasıyla)
        for sel in [
            "#cb-item-gallery .carousel-item img",
            "div.swiper-slide img",
            "div.slick-slide img",
            ".detail-slider img",
            ".stock-slider img",
            ".cb-detail-slider img",
            "figure img",
        ]:
            found = soup.select(sel)
            if found:
                for img in found:
                    src = (img.get("src") or img.get("data-src") or
                           img.get("data-lazy") or "").strip()
                    if src:
                        _add_img(src)
                if images:
                    break

        # 2) Slider bulunamazsa: media.cb / StockMedia img'leri
        if not images:
            for img in soup.find_all("img"):
                src = (img.get("src") or img.get("data-src") or "").strip()
                if "media.cb" in src or "StockMedia" in src:
                    _add_img(src)

        # 3) og:image fallback
        if not images:
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                images.append(og["content"])

        # ── Özellik tablosu — a.py gibi çoklu yöntem ────────────────────────
        feats = []
        seen = set()
        SKIP = {"portföy no", "portföy kategorisi"}

        # a) Tablo satırları
        for row in soup.select("table tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                k = clean_text(cells[0]).rstrip(":").strip()
                v = clean_text(cells[1]).strip()
                if k and v and len(k) < 50 and k.lower() not in seen and k.lower() not in SKIP:
                    feats.append({"label": k, "value": v})
                    seen.add(k.lower())

        # b) dt / dd çiftleri
        for dt, dd in zip(soup.find_all("dt"), soup.find_all("dd")):
            k, v = clean_text(dt), clean_text(dd)
            if k and v and k.lower() not in seen:
                feats.append({"label": k, "value": v})
                seen.add(k.lower())

        # c) cb-checkbox-list özellik kartları (İç / Dış özellikler)
        for card in soup.select(".card.no-radius"):
            sec_el = card.select_one(".card-header h3")
            sec = clean_text(sec_el) if sec_el else "Özellik"
            for li in card.select(".cb-checkbox-list .property"):
                b_el = li.select_one("b")
                k = clean_text(b_el).rstrip(":") if b_el else ""
                if b_el:
                    b_el.extract()
                v = li.get_text(strip=True)
                combined = (k + " " + v).strip().rstrip(":") if k else v
                if combined and combined.lower() not in seen:
                    feats.append({"label": k if k else sec, "value": v if k else combined})
                    seen.add(combined.lower())

        # d) li içinde ":" olan feature satırları
        for li in soup.select("ul.features li, .property-features li, .cb-features li"):
            txt = clean_text(li)
            if ":" in txt and len(txt) < 80:
                parts = txt.split(":", 1)
                k, v = parts[0].strip(), parts[1].strip()
                if k and v and k.lower() not in seen:
                    feats.append({"label": k, "value": v})
                    seen.add(k.lower())

        feats = feats[:20]

        # ── Oda / m² ────────────────────────────────────────────────────────
        rooms = sqm = ""
        for f in feats:
            lbl = f["label"].lower()
            if not rooms and ("oda" in lbl or "room" in lbl):
                rooms = f["value"]
            if not sqm and ("m²" in lbl or "m2" in lbl or "alan" in lbl
                            or "brüt" in lbl or "metre" in lbl):
                sqm = f["value"]

        # .feature-item (header'daki hızlı bilgiler)
        for fi in soup.select(".cb-detail-header .features .feature-item, .feature-item"):
            txt = clean_text(fi)
            if not rooms and "+" in txt:
                rooms = txt
            if not sqm and "m" in txt.lower() and any(c.isdigit() for c in txt):
                sqm = txt

        # Regex fallback
        page_text = soup.get_text(" ", strip=True)
        if not rooms:
            m = _re.search(r"(\d+\+\d+|\d+\+0)", page_text)
            if m:
                rooms = m.group(1)
        if not sqm:
            m = _re.search(r"(\d+)\s*m[²2]", page_text)
            if m:
                sqm = m.group(1) + " m²"

        # ── Açıklama ────────────────────────────────────────────────────────
        description = ""
        for sel in [".description", ".ilan-aciklama", ".detail-description",
                    "#aciklama", "[itemprop='description']", ".cb-detail-content p"]:
            el = soup.select_one(sel)
            if el:
                description = el.get_text(" ", strip=True)[:600]
                break

        # ── Danışman ────────────────────────────────────────────────────────
        agent = {
            "name":   "Erdoğan Işık",
            "img":    "https://media.cb.com.tr/OfficeUserImages/3830/ERDOgAN-IsIK_HTKB8N5P81_75X75.jpg",
            "office": "CB Çizgi",
        }

        a_link = soup.select_one("a[href*='/danismanlar/']")
        if a_link:
            agent["name"] = clean_text(a_link)

        pro = soup.select_one(".cb-professional")
        if pro:
            n_el = pro.select_one("h4") or pro.select_one(".name")
            if n_el:
                agent["name"] = clean_text(n_el)

        img_el = (soup.select_one("img[src*='OfficeUser']") or
                  (pro.select_one("img") if pro else None))
        if img_el:
            src = img_el.get("src", "")
            agent["img"] = BASE + src if src.startswith("/") else src

        off_link = soup.select_one("a[href*='/ofisler/']")
        if off_link:
            agent["office"] = clean_text(off_link)

        return jsonify({
            "ok":          True,
            "title":       title,
            "price":       price,
            "location":    location,
            "rooms":       rooms,
            "sqm":         sqm,
            "type":        prop_type,
            "status":      status,
            "cb_url":      cb_url,
            "images":      images,
            "features":    feats,
            "description": description,
            "agent":       agent,
        })

    except Exception as e:
        print(f"❌ listing/preview hatası: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ================================================================
# API — CRM / TELEGRAM / FOLLOWUP
# ================================================================

@app.route("/api/telegram/notify", methods=["POST"])
def telegram_notify():
    """Lead kaydedilince anında Telegram bildirimi."""
    data = flask_request.json or {}
    name     = data.get("name", "İsimsiz")
    phone    = data.get("phone", "-")
    email    = data.get("email", "-")
    source   = data.get("source", "CRM")
    msg_     = data.get("message", "")
    stage    = data.get("stage", "")
    category = data.get("category", "")

    text = (
        f"🔔 <b>Yeni Lead!</b>\n\n"
        f"👤 <b>{name}</b>\n"
        f"📞 {phone}\n"
        f"📧 {email}\n"
        f"🌐 Kaynak: {source}\n"
        + (f"📂 Kategori: {category}\n" if category else "")
        + (f"📊 Aşama: {stage}\n" if stage else "")
        + (f"💬 {msg_}\n" if msg_ else "")
        + f"\n⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    ok = send_telegram(text)
    return jsonify({"ok": ok})



# ================================================================
# LEAD STATE MACHINE
# ================================================================

LEAD_STAGES = [
    "new_lead",        # Form gönderildi, henüz işlem yok
    "report_sent",     # Otomatik rapor gönderildi
    "contacted",       # Danışman ilk teması kurdu
    "appointment",     # Randevu alındı
    "closed_won",      # Anlaşma yapıldı
    "closed_lost",     # Lead kaybedildi
]


def _log_lead_event(lead_id: str, event_type: str, payload: dict):
    """Lead event timeline'a kayıt yazar."""
    if not _fb_initialized:
        return
    try:
        db_admin.collection("leads").document(lead_id).collection("events").add({
            "type":      event_type,
            "payload":   payload,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        print(f"_log_lead_event hatası: {e}")


def _write_notification_log(lead_id: str, channel: str, status: str, detail: str = ""):
    """Bildirim gönderim logunu notifications koleksiyonuna yazar."""
    if not _fb_initialized:
        return
    try:
        db_admin.collection("notifications").add({
            "leadId":    lead_id,
            "channel":   channel,
            "status":    status,
            "detail":    detail,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        print(f"_write_notification_log hatası: {e}")


def _result_ok(result) -> bool:
    """bool veya dict sonuçlarını ortak başarı kontrolüne çevirir."""
    if isinstance(result, dict):
        return bool(result.get("ok"))
    return bool(result)


def _send_with_retry(fn, *args, retries=3, delay=2, **kwargs):
    """Fonksiyonu retries kez dener. (True, None) veya (False, hata) döner."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            result = fn(*args, **kwargs)
            if _result_ok(result):
                return True, None
            if isinstance(result, dict) and result.get("error"):
                last_err = result.get("error")
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(delay)
    return False, str(last_err or "Bilinmeyen hata")


@app.route("/api/lead/state", methods=["POST"])
def update_lead_state():
    """Lead aşamasını günceller ve event log'a yazar."""
    if not _fb_initialized:
        return jsonify({"ok": False, "error": "Firebase bağlı değil"}), 503

    data      = flask_request.json or {}
    lead_id   = data.get("leadId")
    new_stage = data.get("newStage")

    if not lead_id or not new_stage:
        return jsonify({"ok": False, "error": "leadId ve newStage zorunlu"}), 400
    if new_stage not in LEAD_STAGES:
        return jsonify({"ok": False, "error": f"Geçersiz stage. Geçerliler: {LEAD_STAGES}"}), 400

    try:
        ref = db_admin.collection("leads").document(lead_id)
        doc = ref.get()
        if not doc.exists:
            return jsonify({"ok": False, "error": "Lead bulunamadı"}), 404

        old_stage = doc.to_dict().get("status", "")
        now_iso   = datetime.now(timezone.utc).isoformat()

        ref.update({
            "status":         new_stage,
            "stageChangedAt": now_iso,
            "updatedAt":      now_iso,
        })
        _log_lead_event(lead_id, "stage_change", {
            "from":  old_stage,
            "to":    new_stage,
            "actor": data.get("actorEmail", "system"),
            "note":  data.get("note", ""),
        })
        print(f"✅ Lead aşaması güncellendi: {lead_id} → {new_stage}")
        return jsonify({"ok": True, "leadId": lead_id, "newStage": new_stage})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _send_valuation_async(
    lead_id: str, name: str, phone: str, email: str,
    neighborhood: str, property_type: str, rooms: str, sqm: str, notes: str,
):
    """
    Arka planda çalışır (threading.Thread).
    1. Gemini raporu üret
    2. Müşteriye e-posta (tam HTML rapor)
    3. Müşteriye WhatsApp template
    4. Danışmana Telegram
    5. Danışmana WhatsApp
    6. Danışmana e-posta
    7. Firestore güncelle
    """
    print(f"🔄 Değerleme raporu üretiliyor: {lead_id} | {neighborhood} / {property_type}")

    # ── 1. Gemini raporu üret ──────────────────────────────────────────────
    gemini_result = generate_valuation_report(
        name=name,
        neighborhood=neighborhood or "Ankara",
        property_type=property_type or "Konut",
        rooms=rooms,
        sqm=sqm,
        notes=notes,
    )

    if not gemini_result.get("ok"):
        err_msg = gemini_result.get("error", "Bilinmeyen hata")
        print(f"❌ Gemini raporu üretilemedi: {err_msg}")
        send_telegram(
            f"⚠️ <b>Değerleme Raporu Üretilemedi</b>\n\n"
            f"👤 {name} | 📞 {phone}\n"
            f"📍 {neighborhood} / {property_type}\n"
            f"🔗 Lead: <code>{lead_id}</code>\n"
            f"❌ Hata: {err_msg}"
        )
        if _fb_initialized and lead_id:
            try:
                db_admin.collection("leads").document(lead_id).update({
                    "valuationError":  err_msg,
                    "valuationFailed": True,
                    "valuationAt":     datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                print(f"Firestore hata güncelleme hatası: {e}")
        return

    report = gemini_result["report"]
    pr  = report.get("price_range", {})
    inv = report.get("investment_score", {})
    na  = report.get("neighborhood_analysis", {})
    channels = {}

    # ── 2. Müşteriye e-posta ───────────────────────────────────────────────
    if email:
        try:
            subj, text_b, html_b = build_valuation_report_email(name=name, report=report)
            res = send_transactional_email(email, subj, text_b, html_b)
            channels["customer_email_valuation"] = "sent" if res.get("ok") else f"failed: {res.get('error','')}"
            print(f"{'✅' if res.get('ok') else '❌'} Müşteri değerleme e-postası: {email}")
        except Exception as e:
            channels["customer_email_valuation"] = f"exception: {e}"
            print(f"❌ Müşteri e-posta hatası: {e}")
    else:
        channels["customer_email_valuation"] = "skipped_no_email"

    # ── 3. Müşteriye WhatsApp template ────────────────────────────────────
    if phone and VALUATION_WA_TEMPLATE_NAME:
        try:
            components = [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": name},
                    {"type": "text", "text": neighborhood or "bölgeniz"},
                    {"type": "text", "text": pr.get("average", "")},
                ],
            }]
            wa_res = send_whatsapp_template(
                phone=phone,
                template_name=VALUATION_WA_TEMPLATE_NAME,
                language_code="tr",
                components=components,
            )
            channels["customer_wa_valuation"] = "sent" if wa_res.get("ok") else f"failed: {wa_res.get('error','')}"
            print(f"{'✅' if wa_res.get('ok') else '❌'} Müşteri değerleme WA: {phone}")
        except Exception as e:
            channels["customer_wa_valuation"] = f"exception: {e}"
    else:
        channels["customer_wa_valuation"] = "skipped_no_template" if not VALUATION_WA_TEMPLATE_NAME else "skipped_no_phone"

    # ── 4. Danışmana Telegram ──────────────────────────────────────────────
    t_icon = "📈" if na.get("trend") == "yükselen" else ("📉" if na.get("trend") == "düşen" else "➡️")
    advisor_tg = (
        f"📊 <b>Değerleme Raporu Gönderildi!</b>\n\n"
        f"👤 <b>{name}</b>\n"
        f"📞 {phone}\n"
        + (f"📧 {email}\n" if email else "")
        + f"📍 {neighborhood} / {property_type}\n\n"
        f"💰 <b>Tahmini Değer:</b> {pr.get('average','?')}\n"
        f"   {pr.get('min','?')} — {pr.get('max','?')}\n"
        f"   m²: {pr.get('per_sqm_min','?')} – {pr.get('per_sqm_max','?')}\n\n"
        f"⭐ <b>Skor:</b> {inv.get('score','?')}/{inv.get('max',10)} — {inv.get('label','')}\n"
        f"{t_icon} <b>Trend:</b> {na.get('trend','?').capitalize()}\n\n"
        f"✅ Rapor müşteriye e-posta"
        + (" + WA" if channels.get("customer_wa_valuation") == "sent" else "")
        + " ile iletildi.\n"
        f"🔗 Lead: <code>{lead_id}</code>"
    )
    ok_tg, err_tg = _send_with_retry(send_telegram, advisor_tg)
    channels["advisor_telegram_valuation"] = "sent" if ok_tg else f"failed: {err_tg}"

    # ── 5. Danışmana WhatsApp ──────────────────────────────────────────────
    advisor_wa_msg = (
        f"📊 *Değerleme Raporu Gönderildi!*\n\n"
        f"👤 *{name}*\n📞 {phone}\n"
        f"📍 {neighborhood} / {property_type}\n\n"
        f"💰 *{pr.get('average','?')}*\n"
        f"   {pr.get('min','?')} — {pr.get('max','?')}\n\n"
        f"⭐ Skor: {inv.get('score','?')}/{inv.get('max',10)} ({inv.get('label','')})\n"
        f"{t_icon} Trend: {na.get('trend','?')}\n\n"
        f"✅ Rapor müşteriye iletildi.\nLead: {lead_id}"
    )
    wa_adv = send_whatsapp(WA_ADVISOR_PHONE, advisor_wa_msg)
    channels["advisor_wa_valuation"] = "sent" if wa_adv.get("ok") else f"failed: {wa_adv.get('error','')}"

    # ── 6. Danışmana e-posta ───────────────────────────────────────────────
    if ADVISOR_EMAIL:
        try:
            subj_a, txt_a, html_a = build_advisor_valuation_email(
                customer_name=name,
                customer_phone=phone,
                customer_email=email,
                neighborhood=neighborhood,
                property_type=property_type,
                report=report,
            )
            res_a = send_transactional_email(ADVISOR_EMAIL, subj_a, txt_a, html_a)
            channels["advisor_email_valuation"] = "sent" if res_a.get("ok") else f"failed: {res_a.get('error','')}"
            print(f"{'✅' if res_a.get('ok') else '❌'} Danışman bildirim e-postası: {ADVISOR_EMAIL}")
        except Exception as e:
            channels["advisor_email_valuation"] = f"exception: {e}"
    else:
        channels["advisor_email_valuation"] = "skipped_no_advisor_email"

    # ── 7. Firestore güncelle ──────────────────────────────────────────────
    if _fb_initialized and lead_id:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            db_admin.collection("leads").document(lead_id).update({
                "valuationReport":   report,
                "valuationSentAt":   now_iso,
                "valuationChannels": channels,
                "updatedAt":         now_iso,
            })
            _log_lead_event(lead_id, "valuation_report_sent", {
                "actor":     "grok_auto",
                "channels":  channels,
                "price_avg": pr.get("average", ""),
                "score":     inv.get("score", ""),
            })
            for ch, st in channels.items():
                _write_notification_log(lead_id, ch,
                    "sent" if st == "sent" else "failed",
                    st if st != "sent" else "")
            print(f"✅ Firestore valuation güncellendi: {lead_id}")
        except Exception as e:
            print(f"❌ Firestore güncelleme hatası: {e}")

    print(f"🏁 _send_valuation_async tamamlandı: {lead_id} | {channels}")


@app.route("/api/lead/report", methods=["POST"])
def send_lead_report():
    """
    Form gönderiminden hemen sonra tetiklenir.
    Danışmana Telegram + WhatsApp bildirimi gönderir.
    İsteğe bağlı olarak müşteriye onay e-postası ve WhatsApp template mesajı gönderir.
    Body: { leadId, name, phone, email?, neighborhood?, property_type?, notes? }
    """
    data    = flask_request.json or {}
    lead_id = data.get("leadId", "")
    name    = data.get("name", "İsimsiz")
    phone   = data.get("phone", "-")
    email   = data.get("email", "")
    neigh   = data.get("neighborhood", "")
    ptype   = data.get("property_type", "")
    rooms   = data.get("rooms", "")
    sqm     = data.get("area", "")    # site.html'de alan adı "area"
    notes   = data.get("notes", "")

    result = {"ok": True, "channels": {}}

    advisor_msg = (
        f"📋 <b>Yeni Değerleme Talebi!</b>\n\n"
        f"👤 <b>{name}</b>\n"
        f"📞 {phone}\n"
        + (f"📧 {email}\n" if email else "")
        + (f"📍 Mahalle: {neigh}\n" if neigh else "")
        + (f"🏠 Mülk Tipi: {ptype}\n" if ptype else "")
        + (f"💬 Not: {notes}\n" if notes else "")
        + f"\n⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        + f"🔗 Lead ID: <code>{lead_id}</code>"
    )

    ok_tg, err_tg = _send_with_retry(send_telegram, advisor_msg)
    result["channels"]["telegram"] = "sent" if ok_tg else f"failed: {err_tg}"

    wa_msg = (
        f"📋 *Yeni Değerleme Talebi!*\n\n"
        f"👤 *{name}*\n"
        f"📞 {phone}\n"
        + (f"📧 {email}\n" if email else "")
        + (f"📍 Mahalle: {neigh}\n" if neigh else "")
        + (f"🏠 Mülk Tipi: {ptype}\n" if ptype else "")
        + (f"💬 Not: {notes}\n" if notes else "")
        + f"\n⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        + f"🔗 Lead: {lead_id}"
    )
    wa_result = send_whatsapp(WA_ADVISOR_PHONE, wa_msg)
    result["channels"]["whatsapp"] = "sent" if wa_result["ok"] else f"skipped: {wa_result.get('error','')}"

    email_result = {"ok": False, "error": "disabled"}
    if ENABLE_CUSTOMER_EMAIL_AUTOMATION and email:
        subject, text_body, html_body = build_lead_confirmation_email(
            name=name,
            phone=phone,
            neighborhood=neigh,
            property_type=ptype,
            notes=notes,
        )
        email_result = send_transactional_email(email, subject, text_body, html_body)
        result["channels"]["customer_email"] = "sent" if email_result.get("ok") else f"skipped: {email_result.get('error','')}"
    elif email:
        result["channels"]["customer_email"] = "disabled"
    else:
        result["channels"]["customer_email"] = "missing_email"

    customer_wa_result = {"ok": False, "error": "disabled"}
    if ENABLE_CUSTOMER_WA_AUTOMATION and CUSTOMER_WA_TEMPLATE_NAME and phone:
        customer_wa_result = send_whatsapp_template(
            phone,
            CUSTOMER_WA_TEMPLATE_NAME,
            "tr",
            [{"type": "body", "parameters": [{"type": "text", "text": name}]}],
        )
        result["channels"]["customer_whatsapp"] = "sent" if customer_wa_result.get("ok") else f"skipped: {customer_wa_result.get('error','')}"
    else:
        result["channels"]["customer_whatsapp"] = "disabled"

    if _fb_initialized and lead_id:
        _write_notification_log(lead_id, "telegram", "sent" if ok_tg else "failed", err_tg or "")
        _write_notification_log(lead_id, "whatsapp", "sent" if wa_result["ok"] else "skipped", wa_result.get("error", ""))
        if email:
            _write_notification_log(lead_id, "customer_email", "sent" if email_result.get("ok") else "skipped", email_result.get("error", ""))
        if phone and CUSTOMER_WA_TEMPLATE_NAME:
            _write_notification_log(lead_id, "customer_whatsapp", "sent" if customer_wa_result.get("ok") else "skipped", customer_wa_result.get("error", ""))

        if ok_tg or wa_result["ok"]:
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                db_admin.collection("leads").document(lead_id).update({
                    "status":         "report_sent",
                    "reportSentAt":   now_iso,
                    "stageChangedAt": now_iso,
                    "updatedAt":      now_iso,
                    "automation": {
                        "advisorTelegram": ok_tg,
                        "advisorWhatsapp": wa_result["ok"],
                        "customerEmail": email_result.get("ok", False),
                        "customerWhatsapp": customer_wa_result.get("ok", False),
                    }
                })
                _log_lead_event(lead_id, "stage_change", {
                    "from":  "new_lead",
                    "to":    "report_sent",
                    "actor": "system",
                    "note":  "Otomatik rapor gönderildi (Telegram + WhatsApp Cloud API)",
                })
                if email_result.get("ok"):
                    _log_lead_event(lead_id, "customer_email_sent", {
                        "actor": "system",
                        "email": email,
                        "template": "lead_confirmation",
                    })
                if customer_wa_result.get("ok"):
                    _log_lead_event(lead_id, "customer_whatsapp_sent", {
                        "actor": "system",
                        "phone": phone,
                        "template": CUSTOMER_WA_TEMPLATE_NAME,
                    })
            except Exception as e:
                print(f"Lead güncelleme hatası: {e}")

    if not ok_tg and not wa_result["ok"] and not email_result.get("ok") and not customer_wa_result.get("ok"):
        print(f"❌ Rapor hiçbir kanaldan gönderilemedi! Lead: {lead_id}")
        result["ok"] = False

    # ── Arka planda Grok değerleme raporu üret ve gönder ──────────────────
    if name and (email or phone):
        threading.Thread(
            target=_send_valuation_async,
            args=(lead_id, name, phone, email, neigh, ptype, rooms, sqm, notes),
            daemon=True,
        ).start()
        result["valuation"] = "queued"
        print(f"🔄 Değerleme thread başlatıldı: {lead_id}")
    else:
        result["valuation"] = "skipped_missing_contact"

    return jsonify(result)


@app.route("/api/valuation/quick", methods=["POST"])
def valuation_quick():
    """
    Senkron değerleme — site.html formu için anlık rapor döner.
    Grok web arama ile 30-90s içinde yanıt verir.
    Body: { name, neighborhood, property_type, rooms?, area?, notes? }
    Returns: { ok, report: {...} } | { ok: false, error }
    """
    data = flask_request.json or {}
    result = generate_valuation_report(
        name          = data.get("name", ""),
        neighborhood  = data.get("neighborhood", "Ankara"),
        property_type = data.get("property_type", "Konut"),
        rooms         = data.get("rooms", ""),
        sqm           = data.get("area", ""),
        notes         = data.get("notes", ""),
    )
    return jsonify(result)


@app.route("/api/lead/events/<lead_id>", methods=["GET"])
def get_lead_events(lead_id):
    """Lead'e ait tüm event timeline'ını döner."""
    if not _fb_initialized:
        return jsonify({"ok": False, "data": []}), 503
    try:
        events = []
        for doc in (db_admin.collection("leads").document(lead_id)
                    .collection("events").order_by("createdAt").stream()):
            d = doc.to_dict()
            d["id"] = doc.id
            events.append(d)
        return jsonify({"ok": True, "data": events})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/lead/stages", methods=["GET"])
def get_lead_stages():
    """Geçerli stage listesini döner (frontend için)."""
    return jsonify({"ok": True, "stages": LEAD_STAGES})

@app.route("/api/followup/schedule", methods=["POST"])
def schedule_followup():
    if not _fb_initialized:
        return jsonify({"ok": False, "error": "Firebase bağlı değil"}), 503

    data = flask_request.json or {}
    uid = data.get("uid")
    if not uid:
        return jsonify({"ok": False, "error": "uid gerekli"}), 400

    now = datetime.now(timezone.utc)
    followup_data = {
        "contactId":    data.get("contactId", ""),
        "contactName":  data.get("contactName", ""),
        "contactPhone": data.get("contactPhone", ""),
        "contactEmail": data.get("contactEmail", ""),
        "notes": {
            "week1": data.get("notes", {}).get("week1", "1. hafta takip görüşmesi"),
            "week2": data.get("notes", {}).get("week2", "2. hafta durum değerlendirmesi"),
            "week3": data.get("notes", {}).get("week3", "3. hafta kapanış fırsatı"),
        },
        "startDate":  now.isoformat(),
        "week1Date":  (now + timedelta(days=7)).isoformat(),
        "week2Date":  (now + timedelta(days=14)).isoformat(),
        "week3Date":  (now + timedelta(days=21)).isoformat(),
        "sent":  {"week1": False, "week2": False, "week3": False},
        "done":      False,
        "createdAt": now.isoformat()
    }

    try:
        ref = (db_admin.collection("users").document(uid)
               .collection("followups").add(followup_data))
        doc_id = ref[1].id

        name = followup_data["contactName"]
        text = (
            f"🚀 <b>Takip Planı Başlatıldı!</b>\n\n"
            f"👤 <b>{name}</b>\n"
            f"📞 {followup_data['contactPhone']}\n\n"
            f"📅 <b>Takvim:</b>\n"
            f"  • 1. Hafta: {(now + timedelta(days=7)).strftime('%d.%m.%Y')} → {followup_data['notes']['week1']}\n"
            f"  • 2. Hafta: {(now + timedelta(days=14)).strftime('%d.%m.%Y')} → {followup_data['notes']['week2']}\n"
            f"  • 3. Hafta: {(now + timedelta(days=21)).strftime('%d.%m.%Y')} → {followup_data['notes']['week3']}\n"
            f"\n⏰ {now.strftime('%d.%m.%Y %H:%M')}"
        )
        send_telegram(text)
        return jsonify({"ok": True, "id": doc_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/followup/update", methods=["POST"])
def update_followup():
    if not _fb_initialized:
        return jsonify({"ok": False, "error": "Firebase bağlı değil"}), 503

    data = flask_request.json or {}
    uid         = data.get("uid")
    followup_id = data.get("followupId")
    notes       = data.get("notes", {})

    if not uid or not followup_id:
        return jsonify({"ok": False, "error": "uid ve followupId gerekli"}), 400

    try:
        ref = (db_admin.collection("users").document(uid)
               .collection("followups").document(followup_id))
        update_data = {}
        for week in ["week1", "week2", "week3"]:
            if week in notes:
                update_data[f"notes.{week}"] = notes[week]
        update_data["updatedAt"] = datetime.now(timezone.utc).isoformat()
        ref.update(update_data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/followup/cancel", methods=["POST"])
def cancel_followup():
    if not _fb_initialized:
        return jsonify({"ok": False, "error": "Firebase bağlı değil"}), 503

    data        = flask_request.json or {}
    uid         = data.get("uid")
    followup_id = data.get("followupId")

    if not uid or not followup_id:
        return jsonify({"ok": False, "error": "uid ve followupId gerekli"}), 400

    try:
        ref = (db_admin.collection("users").document(uid)
               .collection("followups").document(followup_id))
        ref.update({"done": True, "cancelledAt": datetime.now(timezone.utc).isoformat()})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/followup/list", methods=["POST"])
def list_followups():
    if not _fb_initialized:
        return jsonify({"ok": False, "data": []}), 503

    data       = flask_request.json or {}
    uid        = data.get("uid")
    contact_id = data.get("contactId")

    if not uid:
        return jsonify({"ok": False, "error": "uid gerekli"}), 400

    try:
        query = (db_admin.collection("users").document(uid)
                 .collection("followups").where(filter=FieldFilter("done", "==", False)))
        if contact_id:
            query = query.where(filter=FieldFilter("contactId", "==", contact_id))

        result = []
        for doc in query.stream():
            d = doc.to_dict()
            d["id"] = doc.id
            result.append(d)

        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ================================================================
# SCHEDULER — Hatırlatma & Haftalık Takip
# ================================================================

def check_reminders():
    if not _fb_initialized or db_admin is None:
        return
    try:
        for user_doc in db_admin.collection("users").stream():
            uid = user_doc.id
            for rem in (db_admin.collection("users").document(uid)
                        .collection("reminders")
                        .where(filter=FieldFilter("done", "==", False))
                        .where(filter=FieldFilter("telegramSent", "==", False))
                        .stream()):
                r = rem.to_dict()
                due = r.get("dueDate", "")
                if not due:
                    continue
                try:
                    due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
                except Exception:
                    try:
                        due_dt = datetime.strptime(due[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except Exception:
                        continue

                if due_dt <= datetime.now(timezone.utc):
                    name   = r.get("contactName", "Müşteri")
                    text_  = r.get("text", "Hatırlatma")
                    phone_ = r.get("contactPhone", "")
                    msg = (
                        f"⏰ <b>Hatırlatma!</b>\n\n"
                        f"👤 <b>{name}</b>" + (f" — {phone_}" if phone_ else "") + "\n"
                        f"📝 {text_}\n\n"
                        f"📅 {due_dt.strftime('%d.%m.%Y')}"
                    )
                    if send_telegram(msg):
                        rem.reference.update({"telegramSent": True})
                        print(f"📨 Hatırlatma gönderildi: {name}")
    except Exception as e:
        print(f"check_reminders hatası: {e}")


def check_followups():
    if not _fb_initialized or db_admin is None:
        return
    try:
        now = datetime.now(timezone.utc)
        for user_doc in db_admin.collection("users").stream():
            uid = user_doc.id
            for f_doc in (db_admin.collection("users").document(uid)
                          .collection("followups")
                          .where(filter=FieldFilter("done", "==", False))
                          .stream()):
                f = f_doc.to_dict()
                name  = f.get("contactName", "Müşteri")
                phone = f.get("contactPhone", "")
                notes = f.get("notes", {})
                sent  = f.get("sent", {})
                updates = {}

                for week_key, date_key in [
                    ("week1", "week1Date"),
                    ("week2", "week2Date"),
                    ("week3", "week3Date"),
                ]:
                    if sent.get(week_key):
                        continue
                    due_str = f.get(date_key, "")
                    if not due_str:
                        continue
                    try:
                        due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                    except Exception:
                        continue

                    if due_dt <= now:
                        week_num = week_key.replace("week", "")
                        note_text = notes.get(week_key, f"{week_num}. hafta takip")
                        msg = (
                            f"📆 <b>{week_num}. Hafta Takip Bildirimi</b>\n\n"
                            f"👤 <b>{name}</b>"
                            + (f"\n📞 {phone}" if phone else "") + "\n\n"
                            f"📝 <i>{note_text}</i>\n\n"
                            f"⏰ {now.strftime('%d.%m.%Y %H:%M')}"
                        )
                        if send_telegram(msg):
                            updates[f"sent.{week_key}"] = True
                            print(f"📨 {week_num}. hafta takip gönderildi: {name}")

                if updates:
                    new_sent = {**sent, **{k.split(".")[1]: v for k, v in updates.items()}}
                    if all(new_sent.get(w, False) for w in ["week1", "week2", "week3"]):
                        updates["done"] = True
                        updates["completedAt"] = now.isoformat()
                        send_telegram(
                            f"✅ <b>Takip Tamamlandı!</b>\n\n"
                            f"👤 <b>{name}</b> için 3 haftalık takip süreci tamamlandı.\n"
                            f"⏰ {now.strftime('%d.%m.%Y %H:%M')}"
                        )
                    f_doc.reference.update(updates)
    except Exception as e:
        print(f"check_followups hatası: {e}")


def start_scheduler():
    def loop():
        while True:
            try:
                check_reminders()
                check_followups()
            except Exception as e:
                print(f"Scheduler hatası: {e}")
            time.sleep(60)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("⏱️  Scheduler başladı (60s) — Hatırlatmalar + Haftalık Takipler")


# ================================================================
# BAŞLAT / BOOTSTRAP
# ================================================================
_bootstrap_done = False


def bootstrap_app():
    global _bootstrap_done
    if _bootstrap_done:
        return
    init_firebase_admin()
    start_scheduler()
    _refresh_listings_bg()    # ← sunucu başlarken ilanları önceden çek
    _refresh_portfoy_bg()     # ← portföy HTML'ini arka planda üret
    _bootstrap_done = True


bootstrap_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Unified Sunucu Başlatıldı: http://0.0.0.0:{port}")
    print(f"   🌐 Web Sitesi : http://0.0.0.0:{port}/")
    print(f"   📊 CRM Paneli : http://0.0.0.0:{port}/crm")
    print(f"   🔧 Admin Panel: http://0.0.0.0:{port}/admin")
    app.run(host="0.0.0.0", port=port, debug=False)
