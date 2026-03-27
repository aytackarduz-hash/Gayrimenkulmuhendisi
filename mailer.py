import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

EMAIL_PROVIDER = os.environ.get('EMAIL_PROVIDER', 'smtp').strip().lower()
EMAIL_FROM = os.environ.get('EMAIL_FROM', '').strip()
EMAIL_FROM_NAME = os.environ.get('EMAIL_FROM_NAME', 'Nexa CRM').strip()

# SMTP config
SMTP_HOST = os.environ.get('SMTP_HOST', '').strip()
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587') or 587)
SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '').strip()
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '').strip()
SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'true').strip().lower() in ('1', 'true', 'yes')

# Resend config
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '').strip()
RESEND_API_URL = 'https://api.resend.com/emails'


def email_status() -> dict:
    smtp_ok = bool(EMAIL_FROM and SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD)
    resend_ok = bool(EMAIL_FROM and RESEND_API_KEY)
    configured = resend_ok if EMAIL_PROVIDER == 'resend' else smtp_ok
    return {
        'ok': configured,
        'configured': configured,
        'provider': EMAIL_PROVIDER,
        'from': EMAIL_FROM,
        'smtp_ready': smtp_ok,
        'resend_ready': resend_ok,
    }



def _build_html_wrapper(title: str, body_html: str) -> str:
    return f'''<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#0b0f19;font-family:Arial,Helvetica,sans-serif;color:#e5e7eb;">
  <div style="max-width:640px;margin:0 auto;padding:32px 20px;">
    <div style="background:#121826;border:1px solid rgba(255,255,255,.08);border-radius:18px;padding:32px;">
      <div style="font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#c7a34b;margin-bottom:14px;">Nexa CRM</div>
      {body_html}
      <div style="margin-top:28px;padding-top:18px;border-top:1px solid rgba(255,255,255,.08);font-size:12px;color:#9ca3af;line-height:1.7;">
        Bu e-posta otomatik olarak oluşturulmuştur. Ek sorularınız için bu mesaja yanıt verebilir veya bizimle telefon üzerinden iletişime geçebilirsiniz.
      </div>
    </div>
  </div>
</body>
</html>'''



def build_lead_confirmation_email(name: str, phone: str = '', neighborhood: str = '', property_type: str = '', notes: str = '') -> tuple[str, str, str]:
    subject = 'Talebiniz bize ulaştı'
    plain = (
        f'Merhaba {name},\n\n'
        'Talebiniz bize başarıyla ulaştı. En kısa sürede sizinle iletişime geçeceğiz.\n\n'
        + (f'Mahalle: {neighborhood}\n' if neighborhood else '')
        + (f'Mülk Tipi: {property_type}\n' if property_type else '')
        + (f'Telefon: {phone}\n' if phone else '')
        + (f'Notunuz: {notes}\n' if notes else '')
        + '\nTeşekkür ederiz.\nNexa CRM'
    )
    html_body = f'''
      <h1 style="margin:0 0 12px;font-size:28px;line-height:1.2;color:#ffffff;">Merhaba {name},</h1>
      <p style="margin:0 0 18px;font-size:16px;line-height:1.7;color:#d1d5db;">
        Talebiniz bize başarıyla ulaştı. Ekibimiz en kısa sürede sizinle iletişime geçecek.
      </p>
      <div style="background:#0f172a;border:1px solid rgba(255,255,255,.07);border-radius:14px;padding:18px 18px 8px;margin:18px 0;">
        <div style="font-size:14px;color:#f3f4f6;font-weight:bold;margin-bottom:10px;">Talep Özeti</div>
        {f'<p style="margin:0 0 10px;color:#cbd5e1;"><strong>Mahalle:</strong> {neighborhood}</p>' if neighborhood else ''}
        {f'<p style="margin:0 0 10px;color:#cbd5e1;"><strong>Mülk Tipi:</strong> {property_type}</p>' if property_type else ''}
        {f'<p style="margin:0 0 10px;color:#cbd5e1;"><strong>Telefon:</strong> {phone}</p>' if phone else ''}
        {f'<p style="margin:0 0 10px;color:#cbd5e1;"><strong>Notunuz:</strong> {notes}</p>' if notes else ''}
      </div>
      <p style="margin:0;font-size:15px;line-height:1.7;color:#d1d5db;">
        Dilerseniz bu e-postayı yanıtlayarak ek bilgi paylaşabilirsiniz.
      </p>
    '''
    return subject, plain, _build_html_wrapper(subject, html_body)



def _send_via_smtp(to_email: str, subject: str, text_body: str, html_body: str | None = None) -> dict:
    if not (EMAIL_FROM and SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD):
        return {'ok': False, 'error': 'SMTP yapılandırması eksik'}

    msg = MIMEMultipart('alternative') if html_body else MIMEText(text_body, 'plain', 'utf-8')
    if html_body:
        msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    msg['Subject'] = subject
    msg['From'] = formataddr((EMAIL_FROM_NAME, EMAIL_FROM))
    msg['To'] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            if SMTP_USE_TLS:
                server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, [to_email], msg.as_string())
        return {'ok': True, 'provider': 'smtp', 'to': to_email}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'provider': 'smtp'}



def _send_via_resend(to_email: str, subject: str, text_body: str, html_body: str | None = None) -> dict:
    if not (EMAIL_FROM and RESEND_API_KEY):
        return {'ok': False, 'error': 'Resend yapılandırması eksik'}
    payload = {
        'from': formataddr((EMAIL_FROM_NAME, EMAIL_FROM)),
        'to': [to_email],
        'subject': subject,
        'text': text_body,
    }
    if html_body:
        payload['html'] = html_body
    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=15,
        )
        data = resp.json() if resp.content else {}
        if resp.ok:
            return {'ok': True, 'provider': 'resend', 'to': to_email, 'id': data.get('id', '')}
        return {'ok': False, 'error': data.get('message', str(data)), 'provider': 'resend'}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'provider': 'resend'}



def send_transactional_email(to_email: str, subject: str, text_body: str, html_body: str | None = None) -> dict:
    if not to_email:
        return {'ok': False, 'error': 'Alıcı e-posta boş'}
    provider = EMAIL_PROVIDER
    if provider == 'resend':
        return _send_via_resend(to_email, subject, text_body, html_body)
    return _send_via_smtp(to_email, subject, text_body, html_body)


# ── Yardımcı fonksiyonlar ─────────────────────────────────────────
def _trend_meta(trend: str) -> tuple:
    t = (trend or '').lower()
    if 'yüksel' in t or 'artı' in t:
        return '📈', '#22c55e'
    if 'düş' in t or 'azal' in t:
        return '📉', '#ef4444'
    return '➡️', '#f59e0b'

def _score_color(score: int) -> str:
    if score >= 8: return '#22c55e'
    if score >= 6: return '#f59e0b'
    return '#ef4444'

def _impact_icon(impact: str) -> tuple:
    if impact == 'positive': return '✅', '#22c55e'
    if impact == 'negative': return '⚠️', '#ef4444'
    return 'ℹ️', '#94a3b8'


# ── Müşteriye gönderilen değerleme raporu e-postası ───────────────
def build_valuation_report_email(name: str, report: dict) -> tuple:
    neighborhood  = report.get('neighborhood', 'Bölgeniz')
    property_type = report.get('property_type', 'Mülkünüz')
    gen_at        = report.get('generated_at', '')

    pr   = report.get('price_range', {})
    na   = report.get('neighborhood_analysis', {})
    inv  = report.get('investment_score', {})
    mc   = report.get('market_comparison', {})
    kf   = report.get('key_factors', [])
    tips = report.get('valuation_tips', [])
    summ = report.get('executive_summary', '')
    disc = report.get('disclaimer', '')

    trend_icon, trend_color = _trend_meta(na.get('trend', 'stabil'))
    score     = int(inv.get('score', 0))
    score_max = int(inv.get('max', 10))
    score_pct = int((score / score_max) * 100) if score_max else 0
    sc_color  = _score_color(score)

    subject = f"{neighborhood} Gayrimenkul Değerleme Raporunuz Hazır"

    plain = (
        f"Merhaba {name},\n\n"
        f"{neighborhood} bölgesindeki {property_type} için değerleme raporunuz hazır.\n\n"
        f"Özet: {summ}\n\n"
        f"Tahmini Değer: {pr.get('average','')}\n"
        f"Aralık: {pr.get('min','')} — {pr.get('max','')}\n"
        f"Yatırım Skoru: {score}/{score_max} — {inv.get('label','')}\n"
        f"Trend: {na.get('trend','')}\n\n"
        f"Tavsiyeler:\n" + "\n".join(f"  • {t}" for t in tips) + "\n\n"
        f"⚠ {disc}\n\nRapor tarihi: {gen_at}\nNexa CRM"
    )

    def _li(items, color):
        return "".join(
            f'<li style="margin:0 0 7px;padding-left:4px;">'
            f'<span style="color:{color};font-size:13px;">• </span>'
            f'<span style="color:#cbd5e1;font-size:13px;">{i}</span></li>'
            for i in items
        )

    kf_html = ""
    for f in kf:
        icon, icolor = _impact_icon(f.get('impact', 'neutral'))
        kf_html += (
            f'<div style="display:flex;gap:10px;align-items:flex-start;margin-bottom:10px;'
            f'padding:12px;background:#0f172a;border-radius:10px;border:1px solid rgba(255,255,255,.06);">'
            f'<span style="font-size:15px;flex-shrink:0;">{icon}</span>'
            f'<div><div style="font-size:12px;font-weight:700;color:{icolor};margin-bottom:3px;">'
            f'{f.get("factor","")}</div>'
            f'<div style="font-size:12px;color:#94a3b8;line-height:1.55;">{f.get("detail","")}</div></div></div>'
        )

    tips_html = "".join(
        f'<div style="margin-bottom:8px;padding:10px 14px;background:#0f172a;'
        f'border-left:3px solid #c7a34b;border-radius:0 8px 8px 0;font-size:13px;color:#cbd5e1;">{t}</div>'
        for t in tips
    )

    similar = mc.get('similar_neighborhoods', [])
    sim_html = " ".join(
        f'<span style="display:inline-block;background:#1e293b;border:1px solid rgba(255,255,255,.08);'
        f'border-radius:20px;padding:3px 11px;font-size:11px;color:#94a3b8;margin:2px;">{s}</span>'
        for s in similar
    )

    score_bar = (
        f'<div style="background:#1e293b;border-radius:999px;height:7px;margin:8px 0 10px;">'
        f'<div style="background:{sc_color};width:{score_pct}%;height:7px;border-radius:999px;"></div></div>'
    )

    body = f"""
      <h1 style="margin:0 0 6px;font-size:24px;color:#ffffff;">Merhaba {name},</h1>
      <p style="margin:0 0 22px;font-size:14px;color:#94a3b8;line-height:1.6;">
        <strong style="color:#e5e7eb;">{neighborhood}</strong> bölgesindeki
        <strong style="color:#e5e7eb;">{property_type}</strong> için
        yapay zeka destekli değerleme raporunuz aşağıdadır.
      </p>

      <div style="background:linear-gradient(135deg,#1e293b,#0f172a);border:1px solid rgba(199,163,75,.3);
                  border-radius:16px;padding:20px;margin-bottom:18px;">
        <div style="font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:#c7a34b;margin-bottom:10px;">
          Özet Değerlendirme
        </div>
        <p style="margin:0;font-size:14px;color:#e5e7eb;line-height:1.75;">{summ}</p>
      </div>

      <div style="background:#0f172a;border:1px solid rgba(34,197,94,.25);border-radius:16px;
                  padding:22px;margin-bottom:18px;text-align:center;">
        <div style="font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:#86efac;margin-bottom:12px;">
          Tahmini Değer Aralığı
        </div>
        <div style="font-size:32px;font-weight:700;color:#22c55e;letter-spacing:-.5px;margin-bottom:4px;">
          {pr.get('average','—')}
        </div>
        <div style="font-size:13px;color:#64748b;margin-bottom:12px;">
          {pr.get('min','—')} &nbsp;–&nbsp; {pr.get('max','—')}
        </div>
        <div style="padding-top:12px;border-top:1px solid rgba(255,255,255,.06);font-size:12px;color:#475569;">
          m² birim değer: <strong style="color:#94a3b8;">{pr.get('per_sqm_min', pr.get('per_sqm_avg','—'))}</strong>
          &nbsp;–&nbsp; <strong style="color:#94a3b8;">{pr.get('per_sqm_max','—')}</strong>
        </div>
      </div>

      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:18px;">
        <tr>
          <td width="49%" valign="top"
              style="background:#0f172a;border:1px solid rgba(255,255,255,.07);border-radius:14px;padding:16px;">
            <div style="font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#94a3b8;margin-bottom:8px;">
              Yatırım Skoru
            </div>
            <div style="font-size:26px;font-weight:700;color:{sc_color};">
              {score}<span style="font-size:14px;color:#374151;">/{score_max}</span>
            </div>
            <div style="font-size:12px;color:{sc_color};margin-bottom:2px;">{inv.get('label','')}</div>
            {score_bar}
            <div style="font-size:11px;color:#64748b;line-height:1.5;">{inv.get('reasoning','')}</div>
          </td>
          <td width="2%"></td>
          <td width="49%" valign="top"
              style="background:#0f172a;border:1px solid rgba(255,255,255,.07);border-radius:14px;padding:16px;">
            <div style="font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#94a3b8;margin-bottom:8px;">
              Bölge Trendi
            </div>
            <div style="font-size:26px;font-weight:700;color:{trend_color};margin-bottom:4px;">
              {trend_icon} {na.get('trend','').capitalize()}
            </div>
            <div style="font-size:11px;color:#64748b;line-height:1.55;">{na.get('trend_detail','')}</div>
          </td>
        </tr>
      </table>

      <div style="background:#0f172a;border:1px solid rgba(255,255,255,.07);border-radius:14px;
                  padding:18px;margin-bottom:18px;">
        <div style="font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#94a3b8;margin-bottom:12px;">
          Mahalle Analizi
        </div>
        <p style="margin:0 0 14px;font-size:13px;color:#cbd5e1;line-height:1.65;">{na.get('summary','')}</p>
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td width="49%" valign="top">
              <div style="font-size:10px;color:#22c55e;font-weight:700;margin-bottom:7px;">✅ AVANTAJLAR</div>
              <ul style="margin:0;padding:0;list-style:none;">{_li(na.get('pros',[]), '#22c55e')}</ul>
            </td>
            <td width="2%"></td>
            <td width="49%" valign="top">
              <div style="font-size:10px;color:#ef4444;font-weight:700;margin-bottom:7px;">⚠️ DİKKAT</div>
              <ul style="margin:0;padding:0;list-style:none;">{_li(na.get('cons',[]), '#ef4444')}</ul>
            </td>
          </tr>
        </table>
      </div>

      {'<div style="background:#0f172a;border:1px solid rgba(255,255,255,.07);border-radius:14px;padding:18px;margin-bottom:18px;"><div style="font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#94a3b8;margin-bottom:12px;">Piyasa Karşılaştırması</div><p style="margin:0 0 8px;font-size:13px;color:#cbd5e1;line-height:1.6;">' + mc.get("vs_district","") + '</p><p style="margin:0 0 12px;font-size:13px;color:#cbd5e1;line-height:1.6;">' + mc.get("vs_ankara","") + '</p>' + ('<div style="font-size:11px;color:#475569;">Benzer bölgeler: ' + sim_html + '</div>' if similar else '') + '</div>' if mc.get('vs_district') or mc.get('vs_ankara') else ''}

      {'<div style="margin-bottom:18px;"><div style="font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#94a3b8;margin-bottom:10px;">Değeri Etkileyen Faktörler</div>' + kf_html + '</div>' if kf_html else ''}

      {'<div style="margin-bottom:18px;"><div style="font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#94a3b8;margin-bottom:10px;">💡 Uzman Tavsiyeleri</div>' + tips_html + '</div>' if tips_html else ''}

      <div style="background:#0a0f1a;border:1px solid rgba(255,255,255,.05);border-radius:10px;
                  padding:12px;text-align:center;margin-bottom:4px;">
        <div style="font-size:11px;color:#374151;">
          🤖 Gemini AI ile oluşturulmuştur &nbsp;·&nbsp; 📅 {gen_at}
        </div>
        <div style="font-size:10px;color:#374151;margin-top:4px;">{disc}</div>
      </div>

      <p style="margin:18px 0 0;font-size:13px;color:#cbd5e1;line-height:1.7;">
        Raporla ilgili sorularınız için ekibimize ulaşabilirsiniz.
      </p>
    """

    return subject, plain, _build_html_wrapper(subject, body)


# ── Danışmana gönderilen bildirim e-postası ───────────────────────
def build_advisor_valuation_email(
    customer_name: str, customer_phone: str, customer_email: str,
    neighborhood: str, property_type: str, report: dict
) -> tuple:
    pr  = report.get('price_range', {})
    inv = report.get('investment_score', {})
    na  = report.get('neighborhood_analysis', {})
    gen = report.get('generated_at', '')

    trend_icon, trend_color = _trend_meta(na.get('trend', 'stabil'))
    score    = int(inv.get('score', 0))
    sc_color = _score_color(score)

    subject = f"[Nexa CRM] Değerleme Raporu Gönderildi — {customer_name} / {neighborhood}"

    plain = (
        f"Değerleme raporu müşteriye gönderildi.\n\n"
        f"Müşteri: {customer_name} | {customer_phone} | {customer_email}\n"
        f"Mülk: {neighborhood} / {property_type}\n\n"
        f"Tahmini Değer: {pr.get('average','?')}\n"
        f"Aralık: {pr.get('min','?')} — {pr.get('max','?')}\n"
        f"Yatırım Skoru: {score}/10 — {inv.get('label','')}\n"
        f"Trend: {na.get('trend','?')}\n\nRapor Tarihi: {gen}\nNexa CRM"
    )

    body = f"""
      <h1 style="margin:0 0 4px;font-size:20px;color:#ffffff;">✅ Değerleme Raporu Gönderildi</h1>
      <p style="margin:0 0 20px;font-size:12px;color:#64748b;">Aşağıdaki müşteriye rapor iletildi.</p>

      <div style="background:#0f172a;border:1px solid rgba(255,255,255,.07);border-radius:14px;
                  padding:16px;margin-bottom:14px;">
        <div style="font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#94a3b8;margin-bottom:10px;">
          Müşteri Bilgileri
        </div>
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr><td style="padding:3px 0;color:#475569;font-size:12px;width:90px;">Ad Soyad</td>
              <td style="padding:3px 0;color:#e5e7eb;font-size:13px;font-weight:700;">{customer_name}</td></tr>
          <tr><td style="padding:3px 0;color:#475569;font-size:12px;">Telefon</td>
              <td style="padding:3px 0;color:#e5e7eb;font-size:12px;">{customer_phone}</td></tr>
          {'<tr><td style="padding:3px 0;color:#475569;font-size:12px;">E-posta</td><td style="padding:3px 0;color:#e5e7eb;font-size:12px;">' + customer_email + '</td></tr>' if customer_email else ''}
          <tr><td style="padding:3px 0;color:#475569;font-size:12px;">Mahalle</td>
              <td style="padding:3px 0;color:#e5e7eb;font-size:12px;">{neighborhood}</td></tr>
          <tr><td style="padding:3px 0;color:#475569;font-size:12px;">Mülk Tipi</td>
              <td style="padding:3px 0;color:#e5e7eb;font-size:12px;">{property_type}</td></tr>
        </table>
      </div>

      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px;">
        <tr>
          <td width="49%" valign="top" style="background:#0f172a;border:1px solid rgba(34,197,94,.2);
              border-radius:12px;padding:14px;text-align:center;">
            <div style="font-size:9px;color:#86efac;letter-spacing:.12em;text-transform:uppercase;margin-bottom:5px;">
              Ort. Değer
            </div>
            <div style="font-size:16px;font-weight:700;color:#22c55e;">{pr.get('average','—')}</div>
            <div style="font-size:10px;color:#374151;margin-top:3px;">{pr.get('min','—')} – {pr.get('max','—')}</div>
          </td>
          <td width="2%"></td>
          <td width="24%" valign="top" style="background:#0f172a;border:1px solid rgba(255,255,255,.07);
              border-radius:12px;padding:14px;text-align:center;">
            <div style="font-size:9px;color:#94a3b8;letter-spacing:.12em;text-transform:uppercase;margin-bottom:5px;">
              Skor
            </div>
            <div style="font-size:20px;font-weight:700;color:{sc_color};">
              {score}<span style="font-size:11px;color:#374151;">/{inv.get('max',10)}</span>
            </div>
            <div style="font-size:10px;color:{sc_color};">{inv.get('label','')}</div>
          </td>
          <td width="2%"></td>
          <td width="24%" valign="top" style="background:#0f172a;border:1px solid rgba(255,255,255,.07);
              border-radius:12px;padding:14px;text-align:center;">
            <div style="font-size:9px;color:#94a3b8;letter-spacing:.12em;text-transform:uppercase;margin-bottom:5px;">
              Trend
            </div>
            <div style="font-size:20px;color:{trend_color};">{trend_icon}</div>
            <div style="font-size:10px;color:{trend_color};">{na.get('trend','—').capitalize()}</div>
          </td>
        </tr>
      </table>

      <div style="background:#0a0f1a;border-radius:10px;padding:10px;text-align:center;
                  font-size:11px;color:#374151;">
        📅 {gen} &nbsp;·&nbsp; 🤖 Gemini AI
      </div>
    """

    return subject, plain, _build_html_wrapper(subject, body)
