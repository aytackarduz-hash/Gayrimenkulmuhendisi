# Nexa CRM

## Dosya Yapısı (Düz)

```
Nexa-RealEstate/
├── app.py
├── wa_cloud.py
├── a.py
├── admin.html
├── crm.html
├── site.html
├── requirements.txt
├── runtime.txt          ← Python 3.11.9 sabitler
├── render.yaml
├── service-account.json ← .gitignore'da, commit etme
└── .gitignore
```

## Render Deploy

render.yaml otomatik konfig eder. Dashboard'da:
- Root Directory: **(boş bırak)**
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT`

## Env Variables

```
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
WA_PHONE_NUMBER_ID
WA_ACCESS_TOKEN
WA_VERIFY_TOKEN
WA_ADVISOR_PHONE
FIREBASE_SERVICE_ACCOUNT   ← service-account.json içeriği (JSON string)
```
