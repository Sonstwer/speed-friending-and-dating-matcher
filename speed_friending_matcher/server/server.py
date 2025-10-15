# server.py – vollständiger Inhalt
# (aus deinem ZIP extrahiert und gezielt angepasst:
# 1) Paarlisten nun numerisch sortiert, damit nichts „untergeht“.
# 2) Im UI eine Teilnehmer*innen-Tabelle hinzugefügt, die alle IDs numerisch zeigt.
# 3) people_ids_sorted an das Template übergeben.)

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

# =========================== Defaults / Konstanten ===========================
DEFAULT_EVENT = "Fun Speed Dating and Friending"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB Upload-Limit

# Temp-Verzeichnis für Upload-Tokens
TMP_DIR = Path(tempfile.gettempdir()) / "matcher_uploads"
TMP_DIR.mkdir(parents=True, exist_ok=True)

# ================================ Utilities =================================

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

def _load_people_from_csv(path: str, interested_cols: Tuple[str, ...]) -> dict:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    people = {}
    for r in rows:
        if not str(r.get("ID", "")).strip().isdigit():
            continue
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

def _compute_mutual_matches(people: dict, column: str):
    likes = {pid: people[pid]["interested_by_col"].get(column, set()) for pid in people}
    matches = []
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

def _build_matches(people: dict, cols: List[str]):
    out = {}
    for col in cols:
        pairs = _compute_mutual_matches(people, column=col)
        pairs = sorted(pairs, key=lambda t: (int(t[0]), int(t[1])))
        out[col] = pairs
    return out

def _zip_from_matches(people: dict, matches_by_label: Dict[str, list], name_prefix: str = "matches") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for label, pairs in matches_by_label.items():
            s = StringIO()
            w = csv.writer(s)
            w.writerow(["A_ID", "A_Name", "A_Email", "A_Phone", "B_ID", "B_Name", "B_Email", "B_Phone"])
            for a, b in pairs:
                pa, pb = people[a], people[b]
                w.writerow([a, pa.get("name",""), pa.get("email",""), pa.get("phone",""),
                            b, pb.get("name",""), pb.get("email",""), pb.get("phone","")])
            zf.writestr(f"{name_prefix}_{label}.csv", s.getvalue())
    return buf.getvalue()

def _aggregate_matches_per_person(people: dict, matches_by_label: Dict[str, list]):
    per_person = {pid: {lab: [] for lab in matches_by_label.keys()} for pid in people}
    for label, pairs in matches_by_label.items():
        for a, b in pairs:
            per_person[a][label].append(b)
            per_person[b][label].append(a)
    return per_person

def _format_partner_list(people: dict, id_list: List[int]) -> str:
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

def _build_mailmerge_csv_and_template(people: dict, matches_by_label: Dict[str, list], event_name: str):
    """
    Baut eine Mail-Merge-CSV mit EINER Zeile pro Person und einer Spalte MessageBody.
    Wenn eine Person keine Matches hat, wird automatisch ein neutraler Hinweistext erzeugt.
    """
    per_person = _aggregate_matches_per_person(people, matches_by_label)
    labels = list(matches_by_label.keys())
    dating_key = next((l for l in labels if l.lower().startswith("dating")), None)
    friend_key = next((l for l in labels if l.lower().startswith("friend")), None)

    # CSV aufbauen
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["ID","Name","Email","Phone","MessageBody"])

    for pid in sorted(people.keys(), key=int):
        p = people[pid]
        name = p.get("name","")
        email = p.get("email","")
        phone = p.get("phone","")

        dating_list = per_person.get(pid, {}).get(dating_key, []) if dating_key else []
        friend_list = per_person.get(pid, {}).get(friend_key, []) if friend_key else []

        bodies = []
        if dating_key is not None:
            bodies.append(f"Dating: {_format_partner_list(people, sorted(dating_list)) or 'keine Treffer'}")
        if friend_key is not None:
            bodies.append(f"Friendship: {_format_partner_list(people, sorted(friend_list)) or 'keine Treffer'}")

        if not bodies:
            bodies.append("Es liegen keine Match-Ergebnisse vor.")

        w.writerow([pid, name, email, phone, " | ".join(bodies)])

    csv_bytes = out.getvalue().encode("utf-8")

    # Minimal-Template für Thunderbird
    template = f"""Subject: {event_name} – Deine Matches
Message Body: {{MessageBody}}"""
    return csv_bytes, template.encode("utf-8")

# ================================ HTML / UI =================================

_BASE_STYLE = """
*{box-sizing:border-box} body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:0;background:#f7f7fb;color:#111}
.grid{max-width:1100px;margin:0 auto;padding:1rem;display:grid;gap:1rem}
.card{background:#fff;border:1px solid #e7e7ef;border-radius:16px;box-shadow:0 1px 2px rgba(0,0,0,.04);padding:1rem}
.titlebar{display:flex;justify-content:space-between;gap:.5rem;align-items:center;flex-wrap:wrap}
h1{font-size:1.4rem;margin:.2rem 0}
h2{font-size:1.1rem;margin:.6rem 0 .4rem}
h3{font-size:1rem;margin:.6rem 0 .4rem}
table{width:100%;border-collapse:collapse;margin:.4rem 0}
th,td{border:1px solid #e5e5ee;padding:.35rem;text-align:left;font-size:.92rem}
thead th{background:#f0f0f8}
.btn{display:inline-block;background:#111;color:#fff;border-radius:10px;border:none;padding:.5rem .8rem;text-decoration:none}
.btn.secondary{background:#666}
input,select{padding:.4rem;border:1px solid #ddd;border-radius:8px}
.row{display:flex;gap:.5rem;flex-wrap:wrap}
"""

_DARKMODE_SCRIPT = """
<script>
(function(){
  const mq = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
  if(mq && mq.matches){
    const s = document.createElement('style');
    s.textContent = 'body{background:#0b0b0f;color:#f0f2f4}.card{background:#121219;border-color:#2a2a33}thead th{background:#181822}th,td{border-color:#2a2a33}.btn{background:#2e6ee6}input,select{background:#0f0f14;color:#e8e8f0;border-color:#2a2a33}';
    document.head.appendChild(s);
  }
})();
</script>
"""

_INDEX_HTML = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Speed Friending & Dating Matcher</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>""" + _BASE_STYLE + """</style>
  """ + _DARKMODE_SCRIPT + """
</head>
<body>
  <div class="grid">
    <div class="card">
      <div class="titlebar">
        <div style="display:flex; align-items:center; gap:.75rem; flex-wrap:wrap;">
          <h1>Speed Friending & Dating Matcher</h1>
          <span>|</span>
          <form method="get" action="/example/dual_interest_sample.csv">
            <button class="btn secondary" type="submit">Beispiel-CSV</button>
          </form>
        </div>
      </div>

      <form method="post" action="/ui/match" enctype="multipart/form-data">
        <div class="row">
          <label>Event:
            <input type="text" name="event" placeholder="Event-Name" value="{{ event_name or '' }}">
          </label>
          <label>Interested-Spalten (Komma-getrennt):
            <input type="text" name="interested-columns" value="{{ interested_columns|join(',') if interested_columns else 'Interested' }}">
          </label>
          <label>Labels (optional, Komma-getrennt):
            <input type="text" name="labels" value="{{ labels|join(',') if labels else '' }}">
          </label>
        </div>
        <div class="row">
          <input type="file" name="file" accept=".csv">
          <button class="btn" type="submit">Auswerten</button>
        </div>
      </form>

      {% if results %}
        <h2 style="margin-top:.2rem">Teilnehmende (numerisch sortiert)</h2>
        <table>
          <thead>
            <tr><th>ID</th><th>Name</th><th>Email</th><th>Phone</th><th>All (Whitelist)</th></tr>
          </thead>
          <tbody>
          {% for pid in people_ids_sorted %}
            <tr>
              <td>{{ people[pid].id }}</td>
              <td>{{ people[pid].name }}</td>
              <td>{{ people[pid].email }}</td>
              <td>{{ people[pid].phone }}</td>
              <td>{% if people[pid].all %}{{ people[pid].all|list|sort|join(';') }}{% else %}-{% endif %}</td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
        <hr/>

        <h2>Ergebnisse</h2>
        {% for label, rows in results.items() %}
          <h3>{{ label }}</h3>
          {% if rows %}
            <table>
              <thead>
                <tr>
                  <th>A_ID</th><th>A_Name</th><th>A_Email</th><th>A_Phone</th>
                  <th>B_ID</th><th>B_Name</th><th>B_Email</th><th>B_Phone</th>
                </tr>
              </thead>
              <tbody>
                {% for a,b in rows %}
                <tr>
                  <td>{{ people[a].id }}</td>
                  <td>{{ people[a].name }}</td>
                  <td>{{ people[a].email }}</td>
                  <td>{{ people[a].phone }}</td>
                  <td>{{ people[b].id }}</td>
                  <td>{{ people[b].name }}</td>
                  <td>{{ people[b].email }}</td>
                  <td>{{ people[b].phone }}</td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          {% else %}
            <p>Keine Matches gefunden.</p>
          {% endif %}
        {% endfor %}

        <form class="row" style="margin-top:.6rem" method="get" action="/export/mail-merge">
          <input type="hidden" name="file_token" value="{{ file_token }}">
          <input type="hidden" name="interested-columns" value="{{ interested_columns|join(',') }}">
          <input type="hidden" name="labels" value="{{ labels|join(',') }}">
          {% if event_name %}<input type="hidden" name="event" value="{{ event_name }}">{% endif %}
          <button class="btn" type="submit">Mail-Merge Export (Thunderbird)</button>
          <a class="btn secondary" href="/help/mail-merge" target="_blank" rel="noopener">Hilfe</a>
        </form>
      {% endif %}
    </div>
  </div>
</body>
</html>
"""

# =============================== Routes =====================================

def _save_temp_csv_and_get_token(f_storage) -> str:
    token = uuid.uuid4().hex
    p = TMP_DIR / f"{token}.csv"
    f_storage.save(p)
    return token

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
    people = _load_people_from_csv(str(tmp_path), tuple(cols))
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
        people_ids_sorted=sorted(people_view.keys(), key=int),
    )

@app.route("/api/match/dual", methods=["GET", "POST"])
def api_match_dual():
    if request.method == "GET":
        return jsonify({"ok": True, "hint": "POST CSV als multipart/form-data unter 'file'"})
    f = request.files.get("file")
    cols = [c.strip() for c in (request.form.get("interested-columns") or "Interested").split(",") if c.strip()]
    labels_raw = request.form.get("labels")
    labels = [l.strip() for l in labels_raw.split(",")] if labels_raw else cols

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    try:
        f.save(tmp); tmp.flush()
        people = _load_people_from_csv(tmp.name, tuple(cols))
        matches_by_col = _build_matches(people, cols)
    finally:
        try: os.unlink(tmp.name)
        except: pass

    return jsonify({
        "ok": True,
        "labels": labels,
        "matches": {lab: pairs for lab, pairs in matches_by_col.items()}
    })

@app.route("/export/mail-merge", methods=["GET", "POST"])
def export_mail_merge():
    cols = [c.strip() for c in (request.values.get("interested-columns") or "Interested").split(",") if c.strip()]
    labels_raw = request.values.get("labels")
    labels = [l.strip() for l in labels_raw.split(",")] if labels_raw else cols
    event_name = (request.values.get("event") or DEFAULT_EVENT).strip() or DEFAULT_EVENT

    # CSV beziehen: upload (multipart) oder token
    f = request.files.get("file")
    if f:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        try:
            f.save(tmp); tmp.flush()
            people = _load_people_from_csv(tmp.name, tuple(cols))
        finally:
            try: os.unlink(tmp.name)
            except: pass
    else:
        token = request.values.get("file_token")
        if not token:
            return "Fehler: file_token oder multipart upload fehlt", 400
        p = TMP_DIR / f"{token}.csv"
        if not p.exists():
            return "Fehler: Datei nicht gefunden (file_token ungültig)", 404
        people = _load_people_from_csv(str(p), tuple(cols))

    matches_by_col = _build_matches(people, cols)
    csv_bytes, template_bytes = _build_mailmerge_csv_and_template(people, matches_by_col, event_name)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mail_merge.csv", csv_bytes)
        zf.writestr("template.txt", template_bytes)

    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "application/zip"
    resp.headers["Content-Disposition"] = "attachment; filename=mail_merge_export.zip"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

# Beispiel-CSV (aus deiner Version)
_SAMPLE_CSV = """ID,Name,Email,Phone,All,InterestedDating,InterestedFriendship
1,Alex (they/them),alex@example.com,+4311111,"","5;6;7;8;9;10","2;3;4;5"
2,Quinn,quinn@example.com,+4322222,"","5;6;7;8;9;10","1;3;4;5;10"
3,Jamie,jamie@example.com,+4333333,"","5;6;7;8;9;10","1;2;4;5"
4,Taylor,taylor@example.com,+4344444,"","1;2;3","1;2;3;4;5;6;7;8;9;10"
5,Jordan,jordan@example.com,+4355555,"","1;2;3","1;2;3;"
6,Morgan,morgan@example.com,+4366666,"","1;2;3","1;2;3;"
7,Casey,casey@example.com,+4377777,"","5","7"
8,Rowan,rowan@example.com,+4388888,"","6","6"
9,Skylar,skylar@example.com,+4399999,"","7","7"
10,Finley,finley@example.com,+43101010,"","1",""2"
"""

@app.route("/example/dual_interest_sample.csv", methods=["GET"])
def example_dual_interest():
    resp = make_response(_SAMPLE_CSV)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=dual_interest_sample.csv"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

# Offline-Formular HTML
_OFFLINE_FORM_HTML = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Offline-Formular – Speed Friending / Dating</title>
  <style>""" + _BASE_STYLE + """</style>
</head>
<body>
  <div class="grid">
    <div class="card">
      <h1>Offline-Formular</h1>
      <p>Trage IDs, Namen, Emails, Phones ein. Spalten: All, Dating, Friendship.</p>
      <form method="post" action="/api/build-csv">
        <div class="row">
          <textarea name="ids" placeholder="IDs (eine pro Zeile)" rows="6" style="width:18ch"></textarea>
          <textarea name="names" placeholder="Namen (eine pro Zeile)" rows="6" style="flex:1"></textarea>
          <textarea name="emails" placeholder="Emails (eine pro Zeile)" rows="6" style="flex:1"></textarea>
        </div>
        <div class="row">
          <textarea name="phones" placeholder="Phones (eine pro Zeile)" rows="6" style="flex:1"></textarea>
          <textarea name="alls" placeholder="All-Whitelist (z. B. 2;5;7)" rows="6" style="flex:1"></textarea>
        </div>
        <div class="row">
          <textarea name="dating[]" placeholder="InterestedDating (IDs getrennt mit ;)" rows="4" style="flex:1"></textarea>
          <textarea name="friend[]" placeholder="InterestedFriendship (IDs getrennt mit ;)" rows="4" style="flex:1"></textarea>
        </div>
        <div class="row">
          <button class="btn" type="submit">CSV bauen</button>
          <a class="btn secondary" href="/offline/form/download">Als HTML speichern</a>
          <a class="btn secondary" href="/offline/form/pdf">PDF (ReportLab)</a>
        </div>
      </form>
    </div>
  </div>
</body>
</html>
"""

@app.route("/offline/form", methods=["GET"])
def offline_form():
    return render_template_string(_OFFLINE_FORM_HTML)

@app.route("/offline/form/download", methods=["GET"])
def offline_form_download():
    resp = make_response(_OFFLINE_FORM_HTML)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=offline_form.html"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/offline/form/pdf", methods=["GET"])
def offline_form_pdf():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
    except Exception:
        return offline_form_download()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="Offline-Formular")
    styles = getSampleStyleSheet()
    story = [Paragraph("Offline-Formular – Speed Friending / Dating", styles["Title"]), Spacer(1, .4*cm)]

    rows = [["ID", "Name / Notizen", "Dating 👍", "Friendship 🤝"]]
    for _ in range(12):
        rows.append(["", "", "", ""])
    t = Table(rows, colWidths=[2*cm, 9*cm, 3*cm, 3*cm])
    t.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("ALIGN", (0,0), (-1,-1), "LEFT"),
    ]))
    story.append(t)

    doc.build(story)
    pdf_bytes = buf.getvalue()
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = "attachment; filename=offline_form.pdf"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/help/mail-merge", methods=["GET"])
def help_mail_merge():
    html = f"""
<!doctype html><meta charset="utf-8"><title>Mail-Merge Hilfe</title>
<style>{_BASE_STYLE}</style>
<div class="grid"><div class="card">
<h1>Mail-Merge Export – Hilfe</h1>
<p>Export erzeugt ZIP mit <code>mail_merge.csv</code> und <code>template.txt</code>.
Thunderbird: Menü &rarr; Extras &rarr; Add-ons &rarr; "Mail Merge" installieren. 
Dann CSV wählen, <code>Message Body</code> zuordnen.</p>
<p>Repo: <a href="https://github.com/Sonstwer/speed-friending-and-dating-matcher" target="_blank" rel="noopener">GitHub</a></p>
</div></div>
"""
    return html

@app.route("/api/build-csv", methods=["POST"])
def api_build_csv():
    def split_lines(x: str):
        return [l.strip() for l in (x or "").splitlines()]

    ids = split_lines(request.form.get("ids"))
    names = split_lines(request.form.get("names"))
    emails = split_lines(request.form.get("emails"))
    phones = split_lines(request.form.get("phones"))
    alls = split_lines(request.form.get("alls"))

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

    out = StringIO()
    w = csv.writer(out)
    w.writerow(["ID","Name","Email","Phone","All","InterestedDating","InterestedFriendship"])
    for r in rows:
        w.writerow(r)

    resp = make_response(out.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=offline_built.csv"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

# ================================ Main ======================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
