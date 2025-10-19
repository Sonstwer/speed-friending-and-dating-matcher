# Speed-Friending / Dating Matcher

Produktionsfähiger Flask-Dienst mit Gunicorn + systemd und optionalem Reverse-Proxy.

## Features
- Web-UI: CSV hochladen und Matches anzeigen
- API: `/api/match/dual`
- Beispiel-CSV: `/example/dual_interest_sample.csv`
- Offline-Formular: `/offline/form`
- Health-Check: `/health`

## Projektstruktur
```
.
├─ speed_friending_matcher/
│  └─ server/
│     └─ server.py
├─ wsgi.py
├─ requirements.txt
├─ README.md
├─ .gitignore
├─ deploy/
│  └─ matcher.service
└─ scripts/
   └─ manage.sh (optional)
```

## Installation (Server)
```bash
# Code bereitstellen
mkdir -p /opt/matcher/app
cd /opt/matcher/app
# Repo hier clonen oder ZIP entpacken

# Python venv
python3 -m venv .venv
/opt/matcher/app/.venv/bin/pip install --upgrade pip setuptools wheel
/opt/matcher/app/.venv/bin/pip install -r requirements.txt
```

`requirements.txt` muss mindestens enthalten:
```
flask
gunicorn
```

## WSGI Entrypoint
Datei `wsgi.py` im Projekt-Root:
```python
# coding=utf-8
from speed_friending_matcher.server.server import app as application
```

## systemd Unit
Datei `deploy/matcher.service` lokal bearbeiten und nach `/etc/systemd/system/matcher.service` kopieren:

```ini
[Unit]
Description=Matchmaking – Gunicorn WSGI
After=network.target

[Service]
User=matcher
Group=matcher
WorkingDirectory=/opt/matcher/app
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/matcher/app/.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 wsgi:application
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Aktivieren:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now matcher.service
sudo systemctl status --no-pager -l matcher.service
```

## Reverse Proxy (Caddy Beispiel)
```caddyfile
{
  email admin@example.com
  log {
    output file /var/log/caddy/access.log
  }
}

match.example.com {
  reverse_proxy http://10.0.0.41:5000
  tls /etc/letsencrypt/live/match.example.com/fullchain.pem /etc/letsencrypt/live/match.example.com/privkey.pem
}

match-test.example.com {
  reverse_proxy http://10.0.0.42:5000
  tls /etc/letsencrypt/live/match.example.com/fullchain.pem /etc/letsencrypt/live/match.example.com/privkey.pem
}
```

## Endpunkte
- `/` → 302 auf `/offline/form`
- `/ui` → 302 auf `/offline/form`
- `/offline/form` statische Offline-Seite
- `/ui/match` CSV-Upload (POST, multipart)
- `/api/match/dual` JSON-API
- `/export/mail-merge` CSV-Export der Matches
- `/example/dual_interest_sample.csv` Beispiel
- `/api/build-csv` CSV-Builder
- `/health` Liveness

## CSV Schema
Pflichtspalten: `name, channel_a, channel_b, likes`  
`likes` akzeptiert Komma/Strichpunkt/Zeilen-getrennte Namen.

## Entwicklung
```bash
/opt/matcher/app/.venv/bin/python -m flask --app speed_friending_matcher.server.server run
# oder
python speed_friending_matcher/server/server.py
```

## Lizenz
MIT
