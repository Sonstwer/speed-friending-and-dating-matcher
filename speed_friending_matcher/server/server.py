# coding=utf-8
"""
Flask-Server für Speed-Friending-/Dating-Matching.
Änderungen:
- Root-/ui-Redirects auf /offline/form
- /health Endpoint für Liveness
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from flask import (
    Flask,
    request,
    jsonify,
    render_template_string,
    make_response,
    send_file,
)

# ------------------------------------------------------------------------------
# App
# ------------------------------------------------------------------------------

app = Flask(__name__)

@app.get("/")
def index_root():
    from flask import redirect
    return redirect("/offline/form", code=302)

@app.get("/ui")
def index_ui():
    from flask import redirect
    return redirect("/offline/form", code=302)

@app.get("/health")
def health():
    return "ok", 200

# ------------------------------------------------------------------------------
# Matching-Logik (vereinfachte, stabile Variante)
# ------------------------------------------------------------------------------

@dataclass
class Person:
    name: str
    channel_a: Optional[str]
    channel_b: Optional[str]
    likes: List[str]

def _norm(s: Optional[str]) -> str:
    return (s or "").strip()

def _parse_likes(cell: str) -> List[str]:
    # akzeptiert "a,b,c" oder Zeilen mit Trennzeichen
    if not cell:
        return []
    parts = re.split(r"[;\n,]+", cell)
    return [p.strip() for p in parts if p.strip()]

def _load_people_from_csv(path: str, columns: Tuple[str, str, str, str]) -> List[Person]:
    col_name, col_a, col_b, col_likes = columns
    out: List[Person] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        # versucht BOM sicher zu lesen
        raw = f.read()
        if raw and raw[0] == "\ufeff":
            raw = raw.lstrip("\ufeff")
        f2 = io.StringIO(raw)
        reader = csv.DictReader(f2)
        for row in reader:
            out.append(
                Person(
                    name=_norm(row.get(col_name, "")),
                    channel_a=_norm(row.get(col_a, "")),
                    channel_b=_norm(row.get(col_b, "")),
                    likes=_parse_likes(row.get(col_likes, "")),
                )
            )
    return out

def _simple_matches(people: List[Person]) -> List[Tuple[str, str]]:
    # gegenseitiges Like -> Match
    idx: Dict[str, Person] = {p.name: p for p in people if p.name}
    matches: set[Tuple[str, str]] = set()
    for p in people:
        for liked in p.likes:
            q = idx.get(liked)
            if not q:
                continue
            if p.name in q.likes:
                pair = tuple(sorted((p.name, q.name)))
                matches.add(pair)
    return sorted(matches)

# ------------------------------------------------------------------------------
# Minimal UI als Inline-Templates
# ------------------------------------------------------------------------------

_INDEX_HTML = r"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8"/>
  <title>Matcher</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu, Cantarell, Noto Sans, Helvetica, Arial, sans-serif; margin:2rem; line-height:1.4}
    .err{color:#b00020}
    .ok{color:#0b6}
    input,button,select{font-size:1rem;padding:.5rem}
    .box{border:1px solid #ddd; border-radius:12px; padding:1rem; margin:.5rem 0}
    table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #ddd;padding:.5rem;text-align:left}
    code{background:#f8f8f8;padding:0 .25rem;border-radius:4px}
  </style>
</head>
<body>
<h1>Speed-Matching</h1>

{% if error %}
  <p class="err">{{ error }}</p>
{% endif %}

<form class="box" action="/ui/match" method="post" enctype="multipart/form-data">
  <p><b>CSV hochladen</b> (<code>name</code>, <code>channel_a</code>, <code>channel_b</code>, <code>likes</code>)</p>
  <p><input type="file" name="file" required></p>

  <p>Spaltennamen:</p>
  <p>
    <label>Name:&nbsp;<input type="text" name="cols" value="name"></label>
    <label>A:&nbsp;<input type="text" name="cols" value="channel_a"></label>
    <label>B:&nbsp;<input type="text" name="cols" value="channel_b"></label>
    <label>Likes:&nbsp;<input type="text" name="cols" value="likes"></label>
  </p>

  <p><button type="submit">Matchen</button></p>
</form>

<div class="box">
  <p><a href="/example/dual_interest_sample.csv">Beispiel-CSV</a> ·
     <a href="/offline/form">Offline-Formular</a> ·
     <a href="/help/mail-merge">Mail-Merge Hilfe</a> ·
     <a href="/api/build-csv">CSV Builder</a>
  </p>
  <p>Health: <code>/health</code></p>
</div>

{% if results %}
  <h2>Matches</h2>
  <table>
    <thead><tr><th>Person A</th><th>Person B</th></tr></thead>
    <tbody>
    {% for a,b in results %}
      <tr><td>{{ a }}</td><td>{{ b }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
{% endif %}

</body>
</html>
"""

_CSV_BUILDER_HTML = r"""<!doctype html>
<html lang="de">
<head><meta charset="utf-8"/><title>CSV-Builder</title></head>
<body>
  <h1>CSV-Builder</h1>
  <p>Erwartete Spalten: <code>name, channel_a, channel_b, likes</code></p>
</body>
</html>
"""

_OFFLINE_FORM_HTML = r"""<!doctype html>
<html lang="de">
<head><meta charset="utf-8"/><title>Offline-Formular</title></head>
<body>
  <h1>Offline-Formular</h1>
  <p>Druckbares Formular für Vor-Ort-Erfassung.</p>
</body>
</html>
"""

_MAIL_MERGE_HELP_HTML = r"""<!doctype html>
<html lang="de">
<head><meta charset="utf-8"/><title>Mail-Merge Hilfe</title></head>
<body>
  <h1>Mail-Merge Hilfe</h1>
  <p>Kurzanleitung zum Seriendruck für Match-E-Mails.</p>
</body>
</html>
"""

# ------------------------------------------------------------------------------
# HTTP Routen
# ------------------------------------------------------------------------------

@app.post("/ui/match")
def ui_match():
    f = request.files.get("file")
    cols = request.form.getlist("cols")
    if not f:
        return render_template_string(_INDEX_HTML, results=None, error="Keine Datei hochgeladen.")
    if len(cols) != 4:
        return render_template_string(_INDEX_HTML, results=None, error="labels und interested-columns müssen gleich lang sein.")

    with tempfile.NamedTemporaryFile(prefix="upload_", suffix=".csv", delete=False) as tmp:
        f.stream.seek(0)
        tmp.write(f.read())
        tmp.flush()
        tmp_path = tmp.name

    try:
        people = _load_people_from_csv(tmp_path, tuple(cols))  # type: ignore[arg-type]
        results = _simple_matches(people)
        return render_template_string(
            _INDEX_HTML,
            results=results,
            error=None,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

@app.get("/api/match/dual")
@app.post("/api/match/dual")
def api_match_dual():
    """
    JSON:
    {
      "columns": ["name","channel_a","channel_b","likes"],
      "rows": [
        {"name":"A", "channel_a":"", "channel_b":"", "likes":"B"},
        {"name":"B", "channel_a":"", "channel_b":"", "likes":"A"}
      ]
    }
    """
    payload = request.get_json(silent=True) or {}
    columns = payload.get("columns") or ["name","channel_a","channel_b","likes"]
    rows = payload.get("rows") or []
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in columns})
    buf.seek(0)

    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", newline="") as tf:
        tf.write(buf.read())
        tf.flush()
        people = _load_people_from_csv(tf.name, tuple(columns))  # type: ignore[arg-type]
    matches = _simple_matches(people)
    return jsonify({"matches": matches})

@app.get("/export/mail-merge")
@app.post("/export/mail-merge")
def export_mail_merge():
    # Platzhalter: Liefert CSV der Matches
    payload = request.get_json(silent=True) or {}
    columns = payload.get("columns") or ["name","channel_a","channel_b","likes"]
    rows = payload.get("rows") or []
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in columns})
    buf.seek(0)
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", newline="") as tf:
        tf.write(buf.read())
        tf.flush()
        people = _load_people_from_csv(tf.name, tuple(columns))  # type: ignore[arg-type]
    matches = _simple_matches(people)

    out = io.StringIO()
    ww = csv.writer(out)
    ww.writerow(["person_a","person_b"])
    for a,b in matches:
        ww.writerow([a,b])
    out.seek(0)
    return send_file(
        io.BytesIO(out.read().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="matches.csv",
    )

@app.get("/example/dual_interest_sample.csv")
def example_dual_interest():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["name","channel_a","channel_b","likes"])
    w.writerow(["Alice","A","B","Bob, Carol"])
    w.writerow(["Bob","A","B","Alice"])
    w.writerow(["Carol","A","B",""])
    out.seek(0)
    return make_response(out.read(), 200, {"Content-Type":"text/csv; charset=utf-8"})

@app.get("/offline/form")
def offline_form():
    return render_template_string(_OFFLINE_FORM_HTML)

@app.get("/offline/form/download")
def offline_form_download():
    pdf = io.BytesIO(b"%PDF-1.4\n% dummy\n")
    return send_file(pdf, mimetype="application/pdf", as_attachment=True, download_name="offline-form.pdf")

@app.get("/offline/form/pdf")
def offline_form_pdf():
    return send_file(io.BytesIO(b"%PDF-1.4\n% dummy\n"), mimetype="application/pdf", download_name="offline-form.pdf")

@app.get("/help/mail-merge")
def help_mail_merge():
    return render_template_string(_MAIL_MERGE_HELP_HTML)

@app.post("/api/build-csv")
def api_build_csv():
    payload = request.get_json(silent=True) or {}
    headers = payload.get("headers") or ["name","channel_a","channel_b","likes"]
    rows = payload.get("rows") or []
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(headers)
    for r in rows:
        w.writerow([r.get(h, "") for h in headers])
    out.seek(0)
    resp = make_response(out.read(), 200)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    return resp

# ------------------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # Entwicklung
    app.run(host="0.0.0.0", port=5000, debug=True)
