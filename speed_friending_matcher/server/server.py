from flask import Flask, request, send_file, jsonify, Response
import csv, io, zipfile, tempfile, os

app = Flask(__name__)

# ============================================
# === Root-Seite: Index mit Links ============
# ============================================

INDEX_HTML = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>🌈 Speed Friending & Dating Matcher</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { 
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; 
      margin: 2rem; 
      background: linear-gradient(135deg,#ff595e 0%,#ffca3a 20%,#8ac926 40%,#1982c4 60%,#6a4c93 80%,#ff595e 100%);
      background-attachment: fixed;
      color: #000;
    }
    .card { border: 1px solid #eee; border-radius: 12px; padding: 1.5rem 2rem; margin-bottom: 1rem; background: rgba(255,255,255,0.9); box-shadow: 0 1px 6px rgba(0,0,0,.08); }
    h1 { margin-top: 0; }
    .links a { display: inline-block; margin: .5rem 0; font-weight: 600; color: #111; text-decoration: none; }
  </style>
</head>
<body>
  <div class="card">
    <h1>🌈 Speed Friending & Dating Matcher</h1>
    <p>Willkommen! Hier kannst du dein CSV hochladen, Matches anzeigen, oder deine eigenen Dateien generieren.</p>
    <div class="links">
      <a href="/ui/build-csv">🧩 CSV-Builder</a><br/>
      <a href="/example/dual_interest_sample.csv">📄 Beispiel-CSV herunterladen</a><br/>
      <a href="/offline/form">📝 Offline-Formular</a><br/>
      <a href="/help/mail-merge">📧 Mail-Merge Anleitung</a>
    </div>
    <p class="muted">Basierend auf dem <a href="https://github.com/machinekoder/speed-friending-and-dating-matcher" target="_blank">Original-Repository</a>.</p>
  </div>
</body>
</html>"""

# ============================================
# === CSV Builder UI ========================
# ============================================

_CSV_BUILDER_HTML = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>CSV-Builder – Speed Friending & Dating</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem;
           background: linear-gradient(135deg,#ff595e 0%,#ffca3a 20%,#8ac926 40%,#1982c4 60%,#6a4c93 80%,#ff595e 100%);
           background-attachment: fixed; }
    .card { border:1px solid #ccc; border-radius:12px; padding:1.2rem 1.5rem; background:rgba(255,255,255,0.95); }
    table { width:100%; border-collapse:collapse; margin-top:1rem; }
    th,td { border:1px solid #ddd; padding:.4rem .6rem; }
    .btn { background:#111; color:#fff; padding:.5rem 1rem; border-radius:8px; border:none; cursor:pointer; }
    .btn.secondary { background:#444; }
    .row { display:flex; gap:.6rem; flex-wrap:wrap; align-items:center; }
  </style>
</head>
<body>
  <div class="card">
    <h1>🌈 CSV-Builder</h1>
    <p>Erstelle deine Teilnehmerliste direkt hier und lade sie als CSV herunter.</p>
    <form id="csvForm" action="/api/build-csv" method="post">
      <table id="tbl"><thead>
        <tr><th>ID</th><th>Name</th><th>Email</th><th>Phone</th>
        <th>All</th><th>InterestedDating</th><th>InterestedFriendship</th><th></th></tr>
      </thead><tbody></tbody></table>
      <div class="row" style="margin-top:1rem;">
        <button class="btn secondary" type="button" id="addRow">+ Zeile</button>
        <button class="btn" type="submit">CSV erzeugen</button>
      </div>
    </form>
  </div>
<script>
  const tbody = document.querySelector('#tbl tbody');
  function esc(v){return String(v||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');}
  function newRow(d){
    d = d || {};
    let html = ''
      + '<td><input name="id[]" value="'+esc(d.id||'')+'"></td>'
      + '<td><input name="name[]" value="'+esc(d.name||'')+'"></td>'
      + '<td><input name="email[]" value="'+esc(d.email||'')+'"></td>'
      + '<td><input name="phone[]" value="'+esc(d.phone||'')+'"></td>'
      + '<td><input name="all[]" value="'+esc(d.all||'')+'"></td>'
      + '<td><input name="dating[]" value="'+esc(d.dating||'')+'"></td>'
      + '<td><input name="friend[]" value="'+esc(d.friend||'')+'"></td>'
      + '<td><button class="btn secondary" type="button" onclick="this.closest(\\'tr\\').remove()">–</button></td>';
    const tr=document.createElement('tr');tr.innerHTML=html;tbody.appendChild(tr);
  }
  document.getElementById('addRow').addEventListener('click',()=>newRow());
  newRow({id:1,name:"Alex (they/them)",email:"alex@example.com",phone:"+4311111",all:"2;3",dating:"2",friend:"3"});
  newRow({id:2,name:"Riley",email:"riley@example.com",phone:"+4322222",all:"1;3",dating:"1",friend:""});
</script>
</body></html>
"""

@app.route("/")
def index():
    return INDEX_HTML

@app.route("/ui/build-csv")
def ui_build_csv():
    return _CSV_BUILDER_HTML

# ============================================
# === API: CSV Builder erzeugt Datei =========
# ============================================

@app.route("/api/build-csv", methods=["POST"])
def build_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID","Name","Email","Phone","All","InterestedDating","InterestedFriendship"])
    ids = request.form.getlist("id[]")
    for i in range(len(ids)):
        row = [
            request.form.getlist("id[]")[i],
            request.form.getlist("name[]")[i],
            request.form.getlist("email[]")[i],
            request.form.getlist("phone[]")[i],
            request.form.getlist("all[]")[i],
            request.form.getlist("dating[]")[i],
            request.form.getlist("friend[]")[i],
        ]
        writer.writerow(row)
    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="participants.csv")

# ============================================
# === Beispiel-CSV bereitstellen =============
# ============================================

@app.route("/example/dual_interest_sample.csv")
def example_csv():
    path = os.path.join(os.path.dirname(__file__), "../../example/dual_interest_sample.csv")
    return send_file(os.path.abspath(path), as_attachment=True)

# ============================================
# === Offline Formular & Hilfe (statisch) ====
# ============================================

@app.route("/offline/form")
def offline_form():
    return "<h1>Offline Formular</h1><p>Diese Seite erklärt das Event 'Fun Speed Dating and Friending'.<br>Hier kann ein PDF-Formular folgen.</p>"

@app.route("/help/mail-merge")
def mail_merge_help():
    return "<h1>Thunderbird Mail-Merge Hilfe</h1><p>Lade das Add-on <a href='https://addons.thunderbird.net/de/thunderbird/addon/mail-merge/' target='_blank'>Mail Merge</a> herunter, installiere es in Thunderbird, und verwende unsere Export-CSV um personalisierte Mails zu senden.</p>"

# ============================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
PY
