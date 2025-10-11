"""
speed_friending_matcher.server.server
------------------------------------
Flask-Webserver für Speed-Friending/Dating mit:
- /        : HTML-UI (Upload) -> zeigt Match-Tabellen & Download-Button
- /ui/match: POST-Handler für das UI
- /api/match/dual: liefert ZIP (per Upload oder file_token)
Robust: Fallback-Importer/-Matcher, falls Projektmodule fehlen.
"""

import os
import io
import csv
import uuid
import zipfile
import tempfile
from io import StringIO
from pathlib import Path
from typing import Dict, List, Tuple

from flask import (
    Flask, request, jsonify, render_template_string, make_response
)

# ========================== Robust Project Imports ==========================
try:
    from ..importer.csvimporter import CSVImporter as _ProjectCSVImporter
except Exception:
    _ProjectCSVImporter = None

try:
    from ..core.matching.simple_matchmaker import SimpleMatchmaker as _ProjectSimpleMatcher
except Exception:
    _ProjectSimpleMatcher = None


# ============================= Fallback Importer ============================
def _parse_id_list(value: str):
    if not value:
        return set()
    value = value.replace(",", ";")
    out = set()
    for x in value.split(";"):
        x = x.strip()
        if x.isdigit():
            out.add(int(x))
    return out


class _FallbackCSVImporter:
    """Einfacher CSV-Importer:
    Erwartete Spalten (mindestens): ID, Name, Email, Phone, All, <Interested*>
    """
    def __init__(self, path: str):
        self.path = path

    def load(self, interested_cols=("Interested",)):
        with open(self.path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        people = {}
        for r in rows:
            pid = int(r["ID"])
            entry = {
                "id": pid,
                "name": r.get("Name", ""),
                "email": r.get("Email", ""),
                "phone": r.get("Phone", ""),
                "all": _parse_id_list(r.get("All", "")),
            }
            entry["interested_by_col"] = {}
            for col in interested_cols:
                entry["interested_by_col"][col] = _parse_id_list(r.get(col, ""))
            if "Interested" in r and "Interested" not in interested_cols:
                entry["interested_by_col"]["Interested"] = _parse_id_list(r.get("Interested", ""))
            people[pid] = entry
        return people


CSVImporter = _ProjectCSVImporter or _FallbackCSVImporter  # type: ignore


# ============================= Fallback Matcher =============================
def _fallback_compute_for_column(people: dict, column: str) -> List[Tuple[int, int]]:
    """Mutual-Like-Matching für eine Spalte."""
    likes = {pid: people[pid]["interested_by_col"].get(column, set()) for pid in people}
    matches: List[Tuple[int, int]] = []
    for a, targets in likes.items():
        for b in targets:
            if b in likes and a in likes[b] and a < b:
                pa, pb = people[a], people[b]
                if pa.get("all") and b not in pa["all"]:
                    continue
                if pb.get("all") and a not in pb["all"]:
                    continue
                matches.append((a, b))
    return matches


class _FallbackSimpleMatcher:
    def compute_for_column(self, people: dict, column: str):
        return _fallback_compute_for_column(people, column)


if _ProjectSimpleMatcher is not None:
    if hasattr(_ProjectSimpleMatcher, "compute_for_column"):
        SimpleMatcher = _ProjectSimpleMatcher  # type: ignore
    else:
        class _WrappedSimpleMatcher(_ProjectSimpleMatcher):  # type: ignore
            def compute_for_column(self, people: dict, column: str):
                return _fallback_compute_for_column(people, column)
        SimpleMatcher = _WrappedSimpleMatcher
else:
    SimpleMatcher = _FallbackSimpleMatcher


# ================================ Flask App =================================
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB Upload-Limit

# Startseite nie cachen (sonst sieht man CSS-Änderungen nicht)
@app.after_request
def add_no_cache(resp):
    if request.path == "/":
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp

# Temp-Verzeichnis für "file_token"-Workflows (UI -> ZIP)
TMP_DIR = Path("/tmp/matcher_uploads")
TMP_DIR.mkdir(parents=True, exist_ok=True)


# ============================== Helper-Funktionen ===========================
def _build_matches(people: dict, cols: List[str]) -> Dict[str, List[Tuple[int, int]]]:
    matcher = SimpleMatcher()
    out: Dict[str, List[Tuple[int, int]]] = {}
    for col in cols:
        out[col] = matcher.compute_for_column(people, column=col)
    return out


def _zip_from_matches(people: dict, matches_by_label: Dict[str, List[Tuple[int, int]]], name_prefix: str = "matches") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for label, pairs in matches_by_label.items():
            s = StringIO()
            w = csv.writer(s)
            w.writerow(["A_ID", "A_Name", "A_Email", "B_ID", "B_Name", "B_Email"])
            for a, b in pairs:
                pa, pb = people[a], people[b]
                w.writerow([a, pa.get("name",""), pa.get("email",""), b, pb.get("name",""), pb.get("email","")])
            zf.writestr(f"{name_prefix}_{label}.csv", s.getvalue())
    return buf.getvalue()


def _save_temp_csv_and_get_token(file_storage) -> str:
    token = uuid.uuid4().hex
    path = TMP_DIR / f"{token}.csv"
    file_storage.save(path)
    return token


def _load_people_from_token(token: str, cols: List[str]) -> dict:
    path = TMP_DIR / f"{token}.csv"
    if not path.exists():
        raise FileNotFoundError("Invalid or expired file token")
    importer = CSVImporter(str(path))
    people = importer.load(interested_cols=tuple(cols))
    return people


# ================================ HTML UI ===================================
_INDEX_HTML = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Speed Friending & Dating Matcher</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root { --card-bg: rgba(255,255,255,0.92); --card-border: rgba(255,255,255,0.7); }
    body {
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      margin: 2rem;
      min-height: 100vh;
      /* Diagonal verlaufender Regenbogen */
      background: linear-gradient(
        135deg,
        #ff595e 0%,
        #ffca3a 20%,
        #8ac926 40%,
        #1982c4 60%,
        #6a4c93 80%,
        #ff595e 100%
      );
      background-attachment: fixed;
    }
    .card {
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 1rem 1.2rem;
      margin-bottom: 1rem;
      box-shadow: 0 10px 30px rgba(0,0,0,.12);
      background: var(--card-bg);
      backdrop-filter: blur(6px);
    }
    h1 { margin-top: 0; }
    table { border-collapse: collapse; width: 100%; margin-top: .5rem; }
    th, td { border: 1px solid #e9e9e9; padding: .45rem .6rem; }
    th { background: #f7f7f7; text-align: left; }
    .grid { display: grid; grid-template-columns: 1fr; gap: 1rem; }
    .btn { display: inline-block; padding: .6rem 1rem; border-radius: 8px; background: #111; color: #fff; text-decoration: none; border: none; cursor: pointer; }
    .btn.secondary { background: #444; }
    .row { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }
    input[type="text"], input[type="file"] { padding: .4rem .6rem; border-radius: 8px; border: 1px solid #ccc; min-width: 320px; background: #fff; }
    .muted { color: #333; font-size: .9em; }
    .pill { display:inline-block; padding: .2rem .5rem; border:1px solid #ddd; border-radius:999px; margin-left:.5rem; font-size:.85em; background:#fafafa;}
    .header { color:#fff; text-shadow: 0 2px 8px rgba(0,0,0,.35); }
  </style>
</head>
<body>
  <div class="grid">
    <div class="card">
      <h1 class="header">Speed Friending & Dating Matcher</h1>
      <form action="{{ url_for('ui_match') }}" method="post" enctype="multipart/form-data">
        <div class="row" style="margin:.5rem 0">
          <label>CSV-Datei:
            <input type="file" name="file" required />
          </label>
        </div>
        <div class="row">
          <label>Interested Columns
            <input type="text" name="interested-columns" value="InterestedDating,InterestedFriendship" />
          </label>
          <label>Labels
            <input type="text" name="labels" value="dating,friendship" />
          </label>
          <label>Event (optional)
            <input type="text" name="event" placeholder="z.B. ViennaMeetupOct" />
          </label>
        </div>
        <div class="row" style="margin-top:.5rem">
          <button class="btn" type="submit">Auswerten</button>
          <span class="muted">CSV-Format: ID,Name,Email,Phone,All,InterestedDating,InterestedFriendship …</span>
        </div>
      </form>
    </div>

    {% if results %}
      <div class="card">
        <h2>Ergebnisse <span class="pill">{{ event_name or 'matches' }}</span></h2>
        {% for label, rows in results.items() %}
          <h3>{{ label }}</h3>
          {% if rows %}
            <table>
              <thead><tr><th>A_ID</th><th>A_Name</th><th>A_Email</th><th>B_ID</th><th>B_Name</th><th>B_Email</th></tr></thead>
              <tbody>
                {% for a,b in rows %}
                <tr>
                  <td>{{ people[a].id }}</td>
                  <td>{{ people[a].name }}</td>
                  <td>{{ people[a].email }}</td>
                  <td>{{ people[b].id }}</td>
                  <td>{{ people[b].name }}</td>
                  <td>{{ people[b].email }}</td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          {% else %}
            <p class="muted">Keine Matches für <strong>{{ label }}</strong> gefunden.</p>
          {% endif %}
        {% endfor %}

        <form class="row" style="margin-top:1rem" method="get" action="{{ url_for('api_match_dual') }}">
          <input type="hidden" name="file_token" value="{{ file_token }}">
          <input type="hidden" name="interested-columns" value="{{ interested_columns|join(',') }}">
          <input type="hidden" name="labels" value="{{ labels|join(',') }}">
          {% if event_name %}
          <input type="hidden" name="event" value="{{ event_name }}">
          {% endif %}
          <button class="btn secondary" type="submit">ZIP herunterladen</button>
        </form>
      </div>
    {% endif %}
  </div>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    return render_template_string(_INDEX_HTML, results=None)


@app.route("/ui/match", methods=["POST"])
def ui_match():
    f = request.files.get("file")
    if not f:
        return render_template_string(_INDEX_HTML, results=None, error="Keine Datei hochgeladen.")

    cols = [c.strip() for c in (request.form.get("interested-columns") or "Interested").split(",") if c.strip()]
    labels_raw = request.form.get("labels")
    labels = [l.strip() for l in labels_raw.split(",")] if labels_raw else cols
    if len(labels) != len(cols):
        return render_template_string(_INDEX_HTML, results=None, error="labels und interested-columns müssen gleich lang sein.")

    event_name = (request.form.get("event") or "matches").strip()

    token = _save_temp_csv_and_get_token(f)

    tmp_path = TMP_DIR / f"{token}.csv"
    importer = CSVImporter(str(tmp_path))
    people = importer.load(interested_cols=tuple(cols))
    results = _build_matches(people, cols)

    class P: pass
    people_view = {}
    for pid, p in people.items():
        o = P()
        o.id = p["id"]; o.name = p.get("name",""); o.email = p.get("email","")
        o.phone = p.get("phone",""); o.all = p.get("all",set())
        people_view[pid] = o

    return render_template_string(
        _INDEX_HTML,
        results=dict(zip([labels[i] for i in range(len(cols))], [results[c] for c in cols])),
        people=people_view,
        file_token=token,
        interested_columns=cols,
        labels=labels,
        event_name=event_name,
    )


# ================================ API (ZIP) =================================
@app.route("/api/match/dual", methods=["GET", "POST"])
def api_match_dual():
    """
    Upload-Varianten:
      - multipart/form-data mit 'file'
      - ODER Query/Form 'file_token' (verweist auf temporär gespeicherte CSV)
    Weitere Felder:
      - interested-columns: z.B. "InterestedDating,InterestedFriendship"
      - labels: optional; gleiche Anzahl wie interested-columns
      - event: optional; Dateipräfix für ZIP-Inhalte
    """
    cols = [c.strip() for c in (request.values.get("interested-columns") or "Interested").split(",") if c.strip()]
    labels_raw = request.values.get("labels")
    labels = [l.strip() for l in labels_raw.split(",")] if labels_raw else cols
    if len(labels) != len(cols):
        return jsonify({"error": "labels must have same count as interested-columns"}), 400
    name_prefix = (request.values.get("event") or "matches").strip() or "matches"

    people = None
    f = request.files.get("file")
    if f:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        try:
            f.save(tmp)
            tmp.flush()
            importer = CSVImporter(tmp.name)
            people = importer.load(interested_cols=tuple(cols))
        finally:
            try:
                tmp.close()
                os.unlink(tmp.name)
            except Exception:
                pass
    else:
        token = request.values.get("file_token")
        if not token:
            return jsonify({"error": "No CSV provided. Use multipart 'file' or 'file_token' param."}), 400
        try:
            people = _load_people_from_token(token, cols)
        except FileNotFoundError:
            return jsonify({"error": "Invalid or expired file_token"}), 400
        finally:
            try:
                (TMP_DIR / f"{token}.csv").unlink(missing_ok=True)
            except Exception:
                pass

    matches_raw = _build_matches(people, cols)
    matches_by_label = {labels[i]: matches_raw[cols[i]] for i in range(len(cols))}

    data = _zip_from_matches(people, matches_by_label, name_prefix=name_prefix)
    resp = make_response(data)
    resp.headers["Content-Type"] = "application/zip"
    resp.headers["Content-Disposition"] = "attachment; filename=matches.zip"
    resp.headers["Content-Length"] = str(len(data))
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ============================== Main (Debug run) ============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
