# Matching Software for Speed Friending and Dating Events

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

This application helps organizers of speed-friending and speed-dating events manage participant matching quickly and transparently.  
Originally developed for a community event in Vienna, it now provides a flexible and open-source basis for similar projects.

---

## Features
- Import participant data (CSV)
- Generate compatible pairings or group “cliques”
- Export results to text or Excel
- Optional web interface via Flask
- Clean modular design (importer/exporter/matchmaker)

---

## Installation (Development)

```bash
git clone https://github.com/Sonstwer/speed-friending-and-dating-matcher.git
cd speed-friending-and-dating-matcher
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the CLI:

```bash
python -m speed_friending_matcher -i csv:example/sample.csv -o todo:result.txt
```

Run the built-in development server:

```bash
python -m speed_friending_matcher -s
```

---

## Command Line Options

```
usage: speed_friending_matcher [-h] -i INPUT -o OUTPUT [-m MATCHMAKER] [-s]

Matchmaking application for speed-friending events

optional arguments:
  -h, --help            show this help message and exit
  -i INPUT, --input INPUT
                        Input plugin, e.g. csv:participants.csv
  -o OUTPUT, --output OUTPUT
                        Output plugin, e.g. todo:results.txt
  -m MATCHMAKER, --matchmaker MATCHMAKER
                        Matchmaker: simple | clique
  -s, --server          Start local webserver with web GUI
```

Example:
```bash
speed_friending_matcher -i csv:example/sample.csv -o todo:output.txt
```

---

## Importer Plugins
| Name | Description |
|------|--------------|
| `csv:<file>.csv` | Import participant data from a CSV file |

## Exporter Plugins
| Name | Example | Description |
|------|----------|-------------|
| `todo` | `todo:output.txt[:template.txt]` | Simple TODO-list output |
| `onexlsx` | `onexlsx:output.xlsx` | Single-sheet Excel export |
| `clique` | `clique:output.txt[:header.txt][:template.txt]` | Export clique results |
| `graph` | `graph:output.png` | GraphViz visualization export |

## Matchmakers
- **simple:** one-to-one mutual match
- **clique:** find groups (“cliques”) of mutual interest

---

## Production Deployment (Gunicorn + systemd)

### Requirements
- Linux host  
- Python 3.11+  
- Reverse proxy (e.g. Caddy or Nginx)

### Installation
```bash
# Create and prepare directory
sudo mkdir -p /opt/matcher/app
sudo chown -R matcher:matcher /opt/matcher
cd /opt/matcher/app

# Deploy code (via Git or ZIP)
python3 -m venv .venv
/opt/matcher/app/.venv/bin/pip install --upgrade pip setuptools wheel
/opt/matcher/app/.venv/bin/pip install -r requirements.txt
```

### WSGI entrypoint
`wsgi.py` must contain exactly:
```python
from speed_friending_matcher.server.server import app as application
```

### systemd Unit
`/etc/systemd/system/matcher.service`
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

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now matcher.service
sudo systemctl status --no-pager -l matcher.service
```

---

## Reverse Proxy Example (Caddy)

```caddyfile
match.example.com {
    reverse_proxy http://10.0.0.41:5000
    tls /etc/letsencrypt/live/match.example.com/fullchain.pem /etc/letsencrypt/live/match.example.com/privkey.pem
}

match-test.example.com {
    reverse_proxy http://10.0.0.42:5000
    tls /etc/letsencrypt/live/match.example.com/fullchain.pem /etc/letsencrypt/live/match.example.com/privkey.pem
}
```

---

## Development Notes
- No more `configure` or `start` imports in WSGI.  
  `app` is the only exported symbol.  
- Do **not** commit virtual environments.  
  Add `.venv/` to `.gitignore`.  
- Use a reverse proxy for TLS and HTTP redirect handling.

---

## License
MIT License.  
See [LICENSE](LICENSE) for details.
