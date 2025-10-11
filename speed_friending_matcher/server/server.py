"""
speed_friending_matcher.server.server
------------------------------------
Flask-Webserver für Speed-Friending/Dating mit:
- /                      : HTML-UI (Upload) -> Tabellen & Downloads
- /ui/match              : POST-Handler für das UI
- /api/match/dual        : ZIP-Download (per Upload oder file_token)  [kein Link in UI]
- /ui/build-csv          : CSV-Builder-UI (Zeilen erfassen)
- /api/build-csv         : erzeugt CSV aus Formulardaten
- /offline/form          : druckbare Offline-Formularseite
- /offline/form/download : Offline-Formular als HTML-Datei
- /export/mail-merge     : ZIP mit mailmerge.csv + email_template.txt + README.txt
- /help/mail-merge       : Hilfeseite zu Thunderbird + Mail Merge Add-on
- /example/dual_interest_sample.csv : Beispiel-CSV
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

# ========================== Projekt-Imports (robust) =========================
try:
    from ..importer.csvimporter import CSVImporter as _ProjectCSVImporter
except Exception:
    _ProjectCSVImporter = None

try:
    from ..core.matching.simple_matchmaker import SimpleMatchmaker as _ProjectSimpleMatcher
except Exception:
    _ProjectSimpleMatcher = None

# =========================== Defaults / Konstanten ===========================
DEFAULT_EVENT = "Fun Speed Dating and Friending"

# ============================= Fallback Importer =============================
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

# Seiten nie cachen (CSS/HTML-Änderungen sofort)
@app.after_request
def add_no_cache(resp):
    if request.path in ("/", "/ui/build-csv", "/offline/form", "/help/mail-merge"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp

# Temp-Verzeichnis für "file_token"-Workflows
TMP_DIR = Path("/tmp/matcher_uploads")
TMP_DIR.mkdir(parents=True, exist_ok=True)


# ============================== Helper-Funktionen ===========================
def _build_matches(people: dict, cols: List[str]) -> Dict[str, List[Tuple[int, int]]]:
    matcher = SimpleMatcher()
    out: Dict[str, List[Tuple[int, int]]] = {}
    for col in cols:
        out[col] = matcher.compute_for_column(people, column=col)
    return out


def _zip_from_matches(
    people: dict,
    matches_by_label: Dict[str, List[Tuple[int, int]]],
    name_prefix: str = "matches"
) -> bytes:
    """Erzeugt ZIP (mit Telefon-Nummern)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for label, pairs in matches_by_label.items():
            s = StringIO()
            w = csv.writer(s)
            w.writerow(["A_ID", "A_Name", "A_Email", "A_Phone",
                        "B_ID", "B_Name", "B_Email", "B_Phone"])
            for a, b in pairs:
                pa, pb = people[a], people[b]
                w.writerow([
                    a, pa.get("name",""), pa.get("email",""), pa.get("phone",""),
                    b, pb.get("name",""), pb.get("email",""), pb.get("phone","")
                ])
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


def _aggregate_matches_per_person(
    people: dict,
    matches_by_label: Dict[str, List[Tuple[int, int]]]
) -> Dict[int, Dict[str, List[int]]]:
    """Liefert je Person eine Struktur { label: [ids...] }."""
    per_person: Dict[int, Dict[str, List[int]]] = {pid: {lab: [] for lab in matches_by_label.keys()} for pid in people}
    for label, pairs in matches_by_label.items():
        for a, b in pairs:
            per_person[a][label].append(b)
            per_person[b][label].append(a)
    return per_person


def _format_partner_list(people: dict, id_list: List[int]) -> str:
    """Schönformatierte Liste: 'Name (Email, Phone)' durch '; ' getrennt."""
    out = []
    for pid in id_list:
        p = people.get(pid, {})
        name = p.get("name","")
        email = p.get("email","")
        phone = p.get("phone","")
        parts = [name]
        meta = []
        if email: meta.append(email)
        if phone: meta.append(phone)
        if meta:
            parts.append(f"({', '.join(meta)})")
        out.append(" ".join(parts).strip())
    return "; ".join(out)


def _build_mailmerge_csv_and_template(
    people: dict,
    matches_by_label: Dict[str, List[Tuple[int, int]]],
    event_name: str
) -> Tuple[str, str]:
    """
    Erzeugt:
      - mailmerge.csv (als String)
      - email_template.txt (als String)  -> benutzt {{Feldnamen}} aus CSV
    CSV-Spalten:
      To, Name, Event, DatingMatches, FriendshipMatches
    """
    per_person = _aggregate_matches_per_person(people, matches_by_label)
    labels = list(matches_by_label.keys())
    has_dating = any(l.lower().startswith("dating") for l in labels)
    has_friend = any(l.lower().startswith("friend") for l in labels)

    # CSV
    s = StringIO()
    w = csv.writer(s)
    header = ["To", "Name", "Event"]
    if has_dating:
        header.append("DatingMatches")
    if has_friend:
        header.append("FriendshipMatches")
    w.writerow(header)

    for pid, pdata in people.items():
        row = [pdata.get("email",""), pdata.get("name",""), event_name]
        if has_dating:
            d_ids = per_person[pid].get(next((l for l in labels if l.lower().startswith("dating")), ""), [])
            row.append(_format_partner_list(people, d_ids))
        if has_friend:
            f_ids = per_person[pid].get(next((l for l in labels if l.lower().startswith("friend")), ""), [])
            row.append(_format_partner_list(people, f_ids))
        w.writerow(row)

    csv_text = s.getvalue()

    # E-Mail-Template: Empfängerzeile + Betreff + Body
    body_lines = [
        "Empfänger (To): {{To}}",
        "Betreff: Deine Matches für {{Event}}",
        "",
        "Hallo {{Name}},",
        "",
        "hier sind deine Matches für {{Event}}:",
    ]
    if has_dating:
        body_lines += ["", "💘 Dating-Matches:", "{{DatingMatches}}"]
    if has_friend:
        body_lines += ["", "🤝 Freundschafts-Matches:", "{{FriendshipMatches}}"]
    body_lines += [
        "",
        "Viel Spaß beim Vernetzen!",
        "",
        "--",
        "Diese Nachricht wurde mit dem Speed Friending & Dating Matcher erstellt."
    ]
    template_text = "\n".join(body_lines)

    return csv_text, template_text


# ================================ HTML: Index ================================
_INDEX_HTML = f"""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Speed Friending & Dating Matcher</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {{ --card-bg: rgba(255,255,255,0.92); --card-border: rgba(255,255,255,0.7); }}
    body {{
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      margin: 2rem;
      min-height: 100vh;
      background: linear-gradient(135deg,#ff595e 0%,#ffca3a 20%,#8ac926 40%,#1982c4 60%,#6a4c93 80%,#ff595e 100%);
      background-attachment: fixed;
    }}
    .card {{ border: 1px solid var(--card-border); border-radius: 12px; padding: 1rem 1.2rem; margin-bottom: 1rem; box-shadow: 0 10px 30px rgba(0,0,0,.12); background: var(--card-bg); backdrop-filter: blur(6px); }}
    h1 {{ margin: 0; }}
    .titlebar {{ display:flex; align-items:center; gap:.75rem; flex-wrap:wrap; }}
    .links a {{ text-decoration:none; margin-right:.6rem; font-weight:600; }}
    .links a span {{ margin-right:.25rem; }}
    .note {{ font-size:.9em; color:#222; margin-top:.4rem }}
    .note a {{ color:#000; text-decoration:underline; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: .5rem; }}
    th, td {{ border: 1px solid #e9e9e9; padding: .45rem .6rem; }}
    th {{ background: #f7f7f7; text-align: left; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 1rem; }}
    .btn {{ display: inline-block; padding: .6rem 1rem; border-radius: 8px; background: #111; color: #fff; text-decoration: none; border: none; cursor: pointer; }}
    .btn.secondary {{ background: #444; }}
    .row {{ display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }}
    input[type="text"], input[type="file"] {{ padding: .4rem .6rem; border-radius: 8px; border: 1px solid #ccc; min-width: 320px; background: #fff; }}
    .muted {{ color: #333; font-size: .9em; }}
    .pill {{ display:inline-block; padding: .2rem .5rem; border:1px solid #ddd; border-radius:999px; margin-left:.5rem; font-size:.85em; background:#fafafa;}}
    .header {{ color:#000; text-shadow: none; }}
  </style>
</head>
<body>
  <div class="grid">
    <div class="card">
      <div class="titlebar">
        <h1 class="header">Speed Friending & Dating Matcher</h1>
        <div class="links">
          <a href="/" title="Home"><span>🌈</span>Home</a>
          <a href="/ui/build-csv" title="CSV-Builder"><span>🌈</span>CSV-Builder</a>
          <a href="/offline/form" title="Offline-Formular"><span>🌈</span>Offline-Formular</a>
          <a href="/help/mail-merge" title="Mail Merge Hilfe"><span>🌈</span>Mail-Merge Hilfe</a>
          <a href="https://github.com/Sonstwer/speed-friending-and-dating-matcher" target="_blank" rel="noopener"><span>🌈</span>GitHub (Fork)</a>
          <a href="https://github.com/machinekoder/speed-friending-and-dating-matcher" target="_blank" rel="noopener"><span>🌈</span>Original</a>
          <a href="/example/dual_interest_sample.csv" title="Beispiel-CSV herunterladen"><span>🌈</span>Sample CSV</a>
        </div>
      </div>
      <p class="note">Hinweis: Dieses Projekt basiert auf dem <a href="https://github.com/machinekoder/speed-friending-and-dating-matcher" target="_blank" rel="noopener">ursprünglichen Repository von machinekoder</a>. Diese Instanz enthält erweiterte UI-Funktionen.</p>

      <form action="{{{{ url_for('ui_match') }}}}" method="post" enctype="multipart/form-data" style="margin-top:.5rem">
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
            <input type="text" name="event" value="{DEFAULT_EVENT}" />
          </label>
        </div>
        <div class="row" style="margin-top:.5rem">
          <button class="btn" type="submit">Auswerten</button>
          <span class="muted">CSV: ID,Name,Email,Phone,All,InterestedDating,InterestedFriendship …</span>
        </div>
      </form>
    </div>

    {{% if results %}}
      <div class="card">
        <h2>Ergebnisse <span class="pill">{{{{ event_name or '{DEFAULT_EVENT}' }}}}</span></h2>
        {{% for label, rows in results.items() %}}
          <h3>{{{{ label }}}}</h3>
          {{% if rows %}}
            <table>
              <thead>
                <tr>
                  <th>A_ID</th><th>A_Name</th><th>A_Email</th><th>A_Phone</th>
                  <th>B_ID</th><th>B_Name</th><th>B_Email</th><th>B_Phone</th>
                </tr>
              </thead>
              <tbody>
                {{% for a,b in rows %}}
                <tr>
                  <td>{{{{ people[a].id }}}}</td>
                  <td>{{{{ people[a].name }}}}</td>
                  <td>{{{{ people[a].email }}}}</td>
                  <td>{{{{ people[a].phone }}}}</td>
                  <td>{{{{ people[b].id }}}}</td>
                  <td>{{{{ people[b].name }}}}</td>
                  <td>{{{{ people[b].email }}}}</td>
                  <td>{{{{ people[b].phone }}}}</td>
                </tr>
                {{% endfor %}}
              </tbody>
            </table>
          {{% else %}}
            <p class="muted">Keine Matches für <strong>{{{{ label }}}}</strong> gefunden.</p>
          {{% endif %}}
        {{% endfor %}}

        <form class="row" style="margin-top:1rem" method="get" action="{{{{ url_for('api_match_dual') }}}}">
          <input type="hidden" name="file_token" value="{{{{ file_token }}}}">
          <input type="hidden" name="interested-columns" value="{{{{ interested_columns|join(',') }}}}">
          <input type="hidden" name="labels" value="{{{{ labels|join(',') }}}}">
          {{% if event_name %}}
          <input type="hidden" name="event" value="{{{{ event_name }}}}">
          {{% endif %}}
          <button class="btn secondary" type="submit">ZIP herunterladen</button>
        </form>

        <form class="row" style="margin-top:.6rem" method="get" action="{{{{ url_for('export_mail_merge') }}}}">
          <input type="hidden" name="file_token" value="{{{{ file_token }}}}">
          <input type="hidden" name="interested-columns" value="{{{{ interested_columns|join(',') }}}}">
          <input type="hidden" name="labels" value="{{{{ labels|join(',') }}}}">
          {{% if event_name %}}
          <input type="hidden" name="event" value="{{{{ event_name }}}}">
          {{% endif %}}
          <button class="btn" type="submit">Mail-Merge Export (Thunderbird)</button>
          <a class="btn secondary" href="/help/mail-merge" target="_blank" rel="noopener">Hilfe</a>
        </form>
      </div>
    {{% endif %}}
  </div>
</body>
</html>
"""

# ================================ HTML: CSV Builder ==========================
_CSV_BUILDER_HTML = f"""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>CSV-Builder – Speed Friending & Dating</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {{ --card-bg: rgba(255,255,255,0.92); --card-border: rgba(255,255,255,0.7); }}
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; min-height: 100vh;
      background: linear-gradient(135deg,#ff595e 0%,#ffca3a 20%,#8ac926 40%,#1982c4 60%,#6a4c93 80%,#ff595e 100%); background-attachment: fixed; }}
    .card {{ border:1px solid var(--card-border); border-radius:12px; padding:1rem 1.2rem; margin-bottom:1rem; background:var(--card-bg); box-shadow:0 10px 30px rgba(0,0,0,.12); backdrop-filter: blur(6px); }}
    h1 {{ margin:0; color:#000; }}
    .row {{ display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; }}
    table {{ border-collapse: collapse; width: 100%; margin-top:.75rem;}}
    th, td {{ border:1px solid #e9e9e9; padding:.45rem .6rem; }}
    th {{ background:#f7f7f7; }}
    input {{ width:100%; box-sizing:border-box; padding:.35rem .5rem; }}
    .btn {{ display:inline-block; padding:.55rem 1rem; border-radius:8px; background:#111; color:#fff; border:none; cursor:pointer; text-decoration:none; }}
    .btn.secondary {{ background:#444; }}
    .muted {{ color:#333; font-size:.9em; }}
    .links a {{ text-decoration:none; margin-right:.6rem; font-weight:600; }}
    .links a span {{ margin-right:.25rem; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="row" style="justify-content:space-between;">
      <h1>CSV-Builder</h1>
      <div class="links">
        <a href="/" title="Home"><span>🌈</span>Home</a>
        <a href="/offline/form" title="Offline-Formular"><span>🌈</span>Offline-Formular</a>
        <a href="/help/mail-merge" title="Mail-Merge Hilfe"><span>🌈</span>Mail-Merge Hilfe</a>
        <a href="/example/dual_interest_sample.csv" title="Beispiel-CSV"><span>🌈</span>Sample CSV</a>
      </div>
    </div>

    <p class="muted">Erfasse Teilnehmerdaten und erzeuge eine CSV mit den Spalten: <code>ID,Name,Email,Phone,All,InterestedDating,InterestedFriendship</code>.</p>

    <form id="csvForm" action="/api/build-csv" method="post">
      <table id="tbl">
        <thead>
          <tr>
            <th style="width:70px;">ID</th>
            <th>Name</th>
            <th>Email</th>
            <th>Phone</th>
            <th style="width:140px;">All (z.B. 2;3)</th>
            <th style="width:180px;">InterestedDating</th>
            <th style="width:200px;">InterestedFriendship</th>
            <th style="width:60px;">✖</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>

      <div class="row" style="margin-top:.75rem;">
        <button class="btn secondary" type="button" id="addRow">+ Zeile</button>
        <span class="muted">Tipp: IDs fortlaufend vergeben. Interessenslisten mit Semikolon trennen.</span>
      </div>

      <div class="row" style="margin-top:1rem;">
        <button class="btn" type="submit">CSV erzeugen & downloaden</button>
      </div>
    </form>
  </div>

  <script>
    const tbody = document.querySelector('#tbl tbody');
    const addRowBtn = document.getElementById('addRow');

    function newRow(data={{}}) {{
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><input name="id[]" type="number" min="1" value="\${data.id||''}" required></td>
        <td><input name="name[]" type="text" value="\${data.name||''}" required></td>
        <td><input name="email[]" type="email" value="\${data.email||''}" required></td>
        <td><input name="phone[]" type="text" value="\${data.phone||''}"></td>
        <td><input name="all[]" type="text" placeholder="2;3" value="\${data.all||''}"></td>
        <td><input name="dating[]" type="text" placeholder="2;3" value="\${data.dating||''}"></td>
        <td><input name="friend[]" type="text" placeholder="2;3" value="\${data.friend||''}"></td>
        <td style="text-align:center;"><button class="btn secondary" type="button" onclick="this.closest('tr').remove()">–</button></td>
      `;
      tbody.appendChild(tr);
    }}

    addRowBtn.addEventListener('click', () => newRow());
    newRow({{id:1, name:"Alex (they/them)", email:"alex@example.com", phone:"+4311111", all:"2;3", dating:"2", friend:"3"}});
    newRow({{id:2, name:"Quinn",             email:"quinn@example.com", phone:"+4322222", all:"1;3", dating:"1", friend:""}});
  </script>
</body>
</html>
"""

# ================================ HTML: Offline Formular =====================
_OFFLINE_FORM_HTML = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Offline-Formular – Speed Friending & Dating</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    @media print {
      .no-print { display: none !important; }
      body { background: #fff !important; }
      .page { box-shadow: none !important; border: none !important; }
    }
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; background: #fafafa; }
    .actions { display:flex; gap:.6rem; margin-bottom:1rem; }
    .btn { display:inline-block; padding:.55rem 1rem; border-radius:8px; background:#111; color:#fff; border:none; cursor:pointer; text-decoration:none; }
    .btn.secondary { background:#444; }
    .page { background:#fff; padding:1.2rem 1.4rem; border:1px solid #e5e5e5; border-radius:10px; max-width: 900px; margin:auto; box-shadow: 0 6px 24px rgba(0,0,0,.08); }
    h1, h2 { margin:.2rem 0; color:#000; }
    p { color:#222; }
    table { border-collapse: collapse; width: 100%; margin-top:.75rem; }
    th, td { border:1px solid #ddd; padding:.45rem .6rem; vertical-align:top; }
    th { background:#f7f7f7; text-align:left; }
    .tiny { font-size:.9em; color:#333; }
    .two { display:grid; grid-template-columns: 1fr 1fr; gap: .75rem; }
    .mb { margin-bottom: .75rem; }
    .mt { margin-top: .75rem; }
    .muted { color:#444; }
    .foot { margin-top:1rem; font-size:.9em; color:#333; }
    code { background:#f2f2f2; padding:.05rem .3rem; border-radius:4px; }
  </style>
</head>
<body>
  <div class="no-print actions">
    <a class="btn" href="/" title="Zurück">← Zurück</a>
    <a class="btn secondary" href="/offline/form/download" title="Als Datei speichern">Als Datei herunterladen</a>
    <button class="btn" onclick="window.print()">Drucken</button>
  </div>

  <div class="page">
    <h1>Speed Friending & Dating – Offline-Formular</h1>
    <p class="tiny muted">Dieses Blatt erklärt kurz das Event und bietet Platz für deine Notizen & Likes. Nach dem Event kannst du deine Angaben in die Online-Seite übertragen oder abgeben.</p>

    <h2>Wie funktioniert’s?</h2>
    <ol>
      <li>Du triffst mehrere Personen für kurze Gespräche („Runden“).</li>
      <li>Nach jedem Gespräch kannst du ankreuzen, ob du die Person <strong>dating-interessant</strong> oder <strong>freundschaftlich interessant</strong> findest (oder beides).</li>
      <li>Am Ende trägst du deine Likes online ein (oder gibst dieses Blatt beim Orga-Team ab).</li>
    </ol>

    <div class="two mb">
      <div>
        <strong>Deine Daten</strong>
        <table class="mt">
          <tr><th style="width:160px;">ID-Nummer</th><td>&nbsp;</td></tr>
          <tr><th>Name</th><td>&nbsp;</td></tr>
          <tr><th>Email</th><td>&nbsp;</td></tr>
          <tr><th>Telefon</th><td>&nbsp;</td></tr>
        </table>
      </div>
      <div>
        <strong>Hinweise</strong>
        <ul class="mt">
          <li>Datenschutz: Gib nur Daten an, mit denen du dich wohl fühlst.</li>
          <li>Likes bitte als <code>IDs</code> notieren (siehe Tabelle unten).</li>
          <li><em>All</em> (optional): Wenn gesetzt, werden nur Matches aus dieser Liste gebildet.</li>
        </ul>
      </div>
    </div>

    <h2>Gesprächspartner & Notizen</h2>
    <p class="muted tiny">Trage hier die IDs deiner Gesprächspartner ein. Markiere 👍 für Dating und 🤝 für Freundschaft.</p>
    <table>
      <thead>
        <tr>
          <th style="width:70px;">ID</th>
          <th>Name / Notizen</th>
          <th style="width:140px;">Dating 👍</th>
          <th style="width:180px;">Friendship 🤝</th>
        </tr>
      </thead>
      <tbody>
        {% for _ in range(12) %}
        <tr>
          <td>&nbsp;</td>
          <td style="height:38px;">&nbsp;</td>
          <td>&nbsp;</td>
          <td>&nbsp;</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>

    <h2 class="mt">Zusammenfassung (IDs mit Semikolon trennen)</h2>
    <table>
      <tr><th style="width:240px;">All (optional)</th><td>&nbsp;</td></tr>
      <tr><th>InterestedDating</th><td>&nbsp;</td></tr>
      <tr><th>InterestedFriendship</th><td>&nbsp;</td></tr>
    </table>

    <p class="foot">Online-Eingabe: <strong>match.sonsti.top</strong> → „CSV-Builder“ oder „Home“ (CSV-Upload). Format: <code>ID,Name,Email,Phone,All,InterestedDating,InterestedFriendship</code>.</p>
  </div>
</body>
</html>
"""

# ================================ HTML: Mail-Merge Hilfe =====================
_MAIL_MERGE_HELP_HTML = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Mail-Merge Hilfe – Thunderbird</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; }
    .card { border:1px solid #e5e5e5; border-radius:12px; padding:1rem 1.2rem; background:#fff; box-shadow: 0 6px 24px rgba(0,0,0,.06); max-width: 980px; }
    h1 { margin-top:0; }
    ol li { margin:.35rem 0; }
    code { background:#f6f6f6; padding:.05rem .35rem; border-radius:6px; }
    .btn { display:inline-block; padding:.55rem 1rem; border-radius:8px; background:#111; color:#fff; text-decoration:none; margin-right:.5rem; }
    .muted { color:#333; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Mail-Merge (Thunderbird) – Kurzanleitung</h1>
    <p class="muted">Mit dem Export „Mail-Merge“ kannst du deine Matches in einer CSV + E-Mail-Vorlage exportieren und in Thunderbird personalisiert verschicken.</p>
    <h2>Installieren</h2>
    <ol>
      <li>Thunderbird herunterladen: <a href="https://www.thunderbird.net/" target="_blank" rel="noopener">thunderbird.net</a></li>
      <li>Add-on „Mail Merge“ installieren: <a href="https://addons.thunderbird.net/en-US/thunderbird/addon/mail-merge/" target="_blank" rel="noopener">addons.thunderbird.net → Mail Merge</a></li>
    </ol>

    <h2>Export & Versand</h2>
    <ol>
      <li>Auf der Startseite CSV hochladen und auswerten.</li>
      <li>„Mail-Merge Export (Thunderbird)“ anklicken ⇒ ZIP speichern & entpacken.</li>
      <li>Thunderbird: neue E-Mail erstellen (noch nicht senden).</li>
      <li>Betreff/Text aus <code>email_template.txt</code> kopieren. Platzhalter sehen z.B. so aus: <code>{{Name}}</code>, <code>{{Event}}</code>, <code>{{DatingMatches}}</code>, <code>{{FriendshipMatches}}</code>, <code>{{To}}</code>.</li>
      <li>Im Verfassen-Fenster: <em>Menü</em> → <strong>Mail Merge…</strong></li>
      <li>CSV-Datei <code>mailmerge.csv</code> auswählen. Spalte <code>To</code> wird als Empfänger verwendet (oder du kopierst die Zeile „Empfänger (To): {{To}}“ händisch).</li>
      <li>Test mit „<em>Send Later</em>“ oder „<em>Preview</em>“ machen, dann senden.</li>
    </ol>

    <p>Hinweis: Das Template nutzt die Spaltennamen der CSV in doppelten geschweiften Klammern. Du kannst Text frei anpassen.</p>

    <p><a class="btn" href="/" title="Zurück">← Zurück</a></p>
  </div>
</body>
</html>
"""

# ================================== Routes ==================================
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

    event_name = (request.form.get("event") or DEFAULT_EVENT).strip() or DEFAULT_EVENT

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


@app.route("/ui/build-csv", methods=["GET"])
def ui_build_csv():
    return render_template_string(_CSV_BUILDER_HTML)


# ================================ Offline Formular ==========================
@app.route("/offline/form", methods=["GET"])
def offline_form():
    return render_template_string(_OFFLINE_FORM_HTML)


@app.route("/offline/form/download", methods=["GET"])
def offline_form_download():
    resp = make_response(_OFFLINE_FORM_HTML.encode("utf-8"))
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=offline_form.html"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ================================ Mail-Merge Export ==========================
@app.route("/export/mail-merge", methods=["GET", "POST"])
def export_mail_merge():
    """
    Liefert ZIP mit:
      - mailmerge.csv      (Empfänger + personalisierte Felder)
      - email_template.txt (Empfängerzeile + Betreff + Body mit {{Platzhaltern}})
      - README.txt         (Kurz-Anleitung)
    Parameter wie /api/match/dual:
      - file oder file_token
      - interested-columns und labels
      - event (optional)
    """
    cols = [c.strip() for c in (request.values.get("interested-columns") or "Interested").split(",") if c.strip()]
    labels_raw = request.values.get("labels")
    labels = [l.strip() for l in labels_raw.split(",")] if labels_raw else cols
    if len(labels) != len(cols):
        return jsonify({"error": "labels must have same count as interested-columns"}), 400
    event_name = (request.values.get("event") or DEFAULT_EVENT).strip() or DEFAULT_EVENT

    # CSV laden (Upload oder Token)
    people = None
    f = request.files.get("file")
    if f:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        try:
            f.save(tmp); tmp.flush()
            importer = CSVImporter(tmp.name)
            people = importer.load(interested_cols=tuple(cols))
        finally:
            try:
                tmp.close(); os.unlink(tmp.name)
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

    # Matches berechnen
    matches_raw = _build_matches(people, cols)
    matches_by_label = {labels[i]: matches_raw[cols[i]] for i in range(len(cols))}

    # CSV + Template bauen
    csv_text, template_text = _build_mailmerge_csv_and_template(people, matches_by_label, event_name)

    # README
    readme = f"""Mail-Merge Export – Speed Friending & Dating

Dateien:
- mailmerge.csv        → für Thunderbird Mail Merge
- email_template.txt   → Empfängerzeile + Betreff + E-Mail-Body mit {{Platzhaltern}}

Kurzanleitung:
1) Thunderbird installieren: https://www.thunderbird.net/
2) Add-on „Mail Merge“ installieren:
   https://addons.thunderbird.net/en-US/thunderbird/addon/mail-merge/
3) Neue E-Mail verfassen, Betreff & Text aus email_template.txt übernehmen.
   (Oben steht zusätzlich: "Empfänger (To): {{To}}", falls du die Adresse manuell übernehmen willst.)
4) Menü „Mail Merge…“ öffnen, mailmerge.csv wählen (Spalte „To“ = Empfänger).
5) Optional zuerst „Send Later“ / „Preview“, dann senden.

CSV-Spalten:
- To, Name, Event, ggf. DatingMatches, ggf. FriendshipMatches
Platzhalter im Template:
- {{To}} {{Name}} {{Event}} {{DatingMatches}} {{FriendshipMatches}}
"""

    # ZIP zurückgeben
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mailmerge.csv", csv_text)
        zf.writestr("email_template.txt", template_text)
        zf.writestr("README.txt", readme)
    data = buf.getvalue()

    resp = make_response(data)
    resp.headers["Content-Type"] = "application/zip"
    resp.headers["Content-Disposition"] = "attachment; filename=mail_merge_export.zip"
    resp.headers["Content-Length"] = str(len(data))
    resp.headers["Cache-Control"] = "no-cache"
    return resp


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
    name_prefix = (request.values.get("event") or DEFAULT_EVENT).strip() or DEFAULT_EVENT

    people = None
    f = request.files.get("file")
    if f:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        try:
            f.save(tmp); tmp.flush()
            importer = CSVImporter(tmp.name)
            people = importer.load(interested_cols=tuple(cols))
        finally:
            try:
                tmp.close(); os.unlink(tmp.name)
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


# =============================== API: Build CSV ==============================
@app.route("/api/build-csv", methods=["POST"])
def api_build_csv():
    """Erzeugt eine CSV aus den Formulardaten des CSV-Builders."""
    ids = request.form.getlist("id[]")
    names = request.form.getlist("name[]")
    emails = request.form.getlist("email[]")
    phones = request.form.getlist("phone[]")
    alls = request.form.getlist("all[]")
    datings = request.form.getlist("dating[]")
    friends = request.form.getlist("friend[]")

    rows = []
    n = max(len(ids), len(names), len(emails))
    for i in range(n):
        r = [
            (ids[i] if i < len(ids) else "").strip(),
            (names[i] if i < len(names) else "").strip(),
            (emails[i] if i < len(emails) else "").strip(),
            (phones[i] if i < len(phones) else "").strip(),
            (alls[i] if i < len(alls) else "").strip(),
            (datings[i] if i < len(datings) else "").strip(),
            (friends[i] if i < len(friends) else "").strip(),
        ]
        if any(r):
            rows.append(r)

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["ID","Name","Email","Phone","All","InterestedDating","InterestedFriendship"])
    for r in rows:
        w.writerow(r)

    data = buf.getvalue().encode("utf-8")
    resp = make_response(data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=participants.csv"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# =============================== Beispiel-CSV ================================
_SAMPLE_CSV = """ID,Name,Email,Phone,All,InterestedDating,InterestedFriendship
1,Alex (they/them),alex@example.com,+4311111,"2;3","2","3"
2,Quinn,quinn@example.com,+4322222,"1;3","1",""
3,Sasha,sasha@example.com,+4333333,"1;2","","1;2"
4,River,river@example.com,+4344444,"2;3","2;3","1"
5,Phoenix,phoenix@example.com,+4355555,"","4","1;3"
6,Morgan,morgan@example.com,+4366666,"2;7","7","2"
7,Arden,arden@example.com,+4377777,"6;8","6","8"
8,Skye,skye@example.com,+4388888,"7;9","","7;9"
9,Rowan,rowan@example.com,+4399999,"8;10","10","8"
10,Avery,avery@example.com,+43101010,"","9","3;5"
"""

@app.route("/example/dual_interest_sample.csv", methods=["GET"])
def example_csv():
    return make_response((_SAMPLE_CSV, 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": "attachment; filename=dual_interest_sample.csv",
        "Cache-Control": "no-cache",
    }))


# =============================== Help: Mail-Merge ============================
@app.route("/help/mail-merge", methods=["GET"])
def help_mail_merge():
    return render_template_string(_MAIL_MERGE_HELP_HTML)


# ============================== Main (Debug run) ============================
if __name__ == "__main__":
    # Produktion läuft über Gunicorn in systemd
    app.run(host="0.0.0.0", port=5000, debug=False)
