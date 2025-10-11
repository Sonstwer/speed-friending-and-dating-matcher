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
TMP_DIR = Path("/tmp/matcher_uploads")
TMP_DIR.mkdir(parents=True, exist_ok=True)

# Seiten nie cachen (CSS/HTML-Änderungen sofort sichtbar)
@app.after_request
def add_no_cache(resp):
    if request.path in ("/", "/ui/build-csv", "/offline/form", "/offline/form/pdf",
                        "/help/mail-merge"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp

# ============================== Helper-Funktionen ===========================
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
        out[col] = _compute_mutual_matches(people, column=col)
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
            safe_label = "".join(c for c in label if c.isalnum() or c in ("-", "_")).strip() or "result"
            zf.writestr(f"{name_prefix}_{safe_label}.csv", s.getvalue())
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
    return _load_people_from_csv(str(path), tuple(cols))

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
            parts.append("(" + ", ".join(meta) + ")")
        out.append(" ".join(parts).strip())
    return "; ".join(out)

def _build_mailmerge_csv_and_template(people: dict, matches_by_label: Dict[str, list], event_name: str):
    per_person = _aggregate_matches_per_person(people, matches_by_label)
    labels = list(matches_by_label.keys())
    has_dating = any(l.lower().startswith("dating") for l in labels)
    has_friend = any(l.lower().startswith("friend") for l in labels)
    dating_key = next((l for l in labels if l.lower().startswith("dating")), None)
    friend_key = next((l for l in labels if l.lower().startswith("friend")), None)

    s = StringIO()
    w = csv.writer(s)
    header = ["To", "Name", "Event"]
    if has_dating: header.append("DatingMatches")
    if has_friend: header.append("FriendshipMatches")
    w.writerow(header)

    for pid, pdata in people.items():
        row = [pdata.get("email",""), pdata.get("name",""), event_name]
        if has_dating:
            d_ids = per_person[pid].get(dating_key or "", [])
            row.append(_format_partner_list(people, d_ids))
        if has_friend:
            f_ids = per_person[pid].get(friend_key or "", [])
            row.append(_format_partner_list(people, f_ids))
        w.writerow(row)

    csv_text = s.getvalue()

    lines = [
        "Empfänger (To): {{To}}",
        "Betreff: Deine Matches für {{Event}}",
        "",
        "Hallo {{Name}},",
        "",
        "hier sind deine Matches für {{Event}}:",
    ]
    if has_dating:
        lines += ["", "💘 Dating-Matches:", "{{DatingMatches}}"]
    if has_friend:
        lines += ["", "🤝 Freundschafts-Matches:", "{{FriendshipMatches}}"]
    lines += ["", "Viel Spaß beim Vernetzen!", "", "--", "Diese Nachricht wurde mit dem Speed Friending & Dating Matcher erstellt."]
    template_text = "\n".join(lines)
    return csv_text, template_text

# ========================= Gemeinsames CSS + Themes ==========================
_BASE_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f7f7f9;
  --fg: #111;
  --muted: #444;
  --card-bg: rgba(255,255,255,0.92);
  --card-border: rgba(0,0,0,0.08);
  --btn-bg: #111;
  --btn-fg: #fff;
  --btn2-bg: #444;
  --bg-image: linear-gradient(135deg,#ff595e 0%,#ffca3a 20%,#8ac926 40%,#1982c4 60%,#6a4c93 80%,#ff595e 100%);
}
:root[data-theme="dark"] {
  --bg: #0c0f12;
  --fg: #eee;
  --muted: #aaa;
  --card-bg: rgba(18,22,27,0.88);
  --card-border: rgba(255,255,255,0.10);
  --btn-bg: #e6e6e6;
  --btn-fg: #111;
  --btn2-bg: #777;
  --bg-image: linear-gradient(135deg,#2a2a2a 0%,#3b3b3b 20%,#2d4a64 40%,#2c2f36 60%,#4b3b66 80%,#2a2a2a 100%);
}
:root[data-theme="contrast"] {
  /* High-Contrast: keine Verlaufsgrafik, maximaler Kontrast */
  --bg: #ffffff;
  --fg: #000000;
  --muted: #000000;
  --card-bg: #ffffff;
  --card-border: #000000;
  --btn-bg: #000000;
  --btn-fg: #ffffff;
  --btn2-bg: #333333;
  --bg-image: none;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]):not([data-theme="contrast"]) {
    --bg: #0c0f12;
    --fg: #eee;
    --muted: #aaa;
    --card-bg: rgba(18,22,27,0.88);
    --card-border: rgba(255,255,255,0.10);
    --btn-bg: #e6e6e6;
    --btn-fg: #111;
    --btn2-bg: #777;
    --bg-image: linear-gradient(135deg,#2a2a2a 0%,#3b3b3b 20%,#2d4a64 40%,#2c2f36 60%,#4b3b66 80%,#2a2a2a 100%);
  }
}
html, body { height: 100%; }
body {
  font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  margin: 2rem;
  min-height: 100vh;
  color: var(--fg);
  background: var(--bg-image), var(--bg);
  background-attachment: fixed;
}
.card { border: 1px solid var(--card-border); border-radius: 12px; padding: 1rem 1.2rem; margin-bottom: 1rem; box-shadow: 0 10px 30px rgba(0,0,0,.12); background: var(--card-bg); backdrop-filter: blur(6px); }
h1, h2, h3, h4 { color: var(--fg); }
.titlebar { display:flex; align-items:center; gap:.75rem; flex-wrap:wrap; justify-content:space-between; }
.links a { text-decoration:none; margin-right:.6rem; font-weight:600; color: var(--fg); }
.links a span { margin-right:.25rem; }
.note { font-size:.9em; color:var(--muted); margin-top:.4rem }
table { border-collapse: collapse; width: 100%; margin-top: .5rem; color: var(--fg); }
th, td { border: 1px solid #e9e9e9; padding: .45rem .6rem; }
th { background: #f7f7f7; text-align: left; }
.grid { display: grid; grid-template-columns: 1fr; gap: 1rem; }
.btn { display: inline-block; padding: .6rem 1rem; border-radius: 8px; background: var(--btn-bg); color: var(--btn-fg); text-decoration: none; border: none; cursor: pointer; }
.btn.secondary { background: var(--btn2-bg); color: #fff; }
.row { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }
input[type="text"], input[type="file"], input[type="email"], input[type="number"] {
  padding: .4rem .6rem; border-radius: 8px; border: 1px solid #ccc; min-width: 260px; background: #fff; color: #000;
}
.switch { display:flex; align-items:center; gap:.5rem; }
"""

_DARKMODE_SCRIPT = """
<script>
(function(){
  const root = document.documentElement;
  try {
    const saved = localStorage.getItem('matcher-theme');
    if (saved === 'light' || saved === 'dark' || saved === 'contrast') root.setAttribute('data-theme', saved);
  } catch(e) {}
  function setTheme(mode){
    if(mode==='light' || mode==='dark' || mode==='contrast'){ root.setAttribute('data-theme', mode); }
    else { root.removeAttribute('data-theme'); }
    try { localStorage.setItem('matcher-theme', mode||'auto'); } catch(e){}
  }
  window.__setTheme = setTheme;
})();
</script>
"""

_THEME_TOGGLE_HTML = """
<div class="switch">
  <label for="themeSel">Theme:</label>
  <select id="themeSel" onchange="__setTheme(this.value)" style="padding:.35rem .5rem; border-radius:8px;">
    <option value="">Auto</option>
    <option value="light">Light</option>
    <option value="dark">Dark</option>
    <option value="contrast">High Contrast</option>
  </select>
</div>
<script>
(function(){
  const sel = document.getElementById('themeSel');
  try {
    const saved = localStorage.getItem('matcher-theme');
    if(saved) sel.value = saved==='auto' ? '' : saved;
  } catch(e){}
})();
</script>
"""

# ================================ HTML: Index ================================
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
          <h1 class="header" style="margin:0;">Speed Friending & Dating Matcher</h1>
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
        """ + _THEME_TOGGLE_HTML + """
      </div>

      <form action="/ui/match" method="post" enctype="multipart/form-data" style="margin-top:.5rem">
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
            <input type="text" name="event" value="Fun Speed Dating and Friending" />
          </label>
        </div>
        <div class="row" style="margin-top:.5rem">
          <button class="btn" type="submit">Auswerten</button>
          <span class="note">CSV: ID,Name,Email,Phone,All,InterestedDating,InterestedFriendship …</span>
        </div>
      </form>
    </div>

    {% if results %}
      <div class="card">
        <h2>Ergebnisse <span style="display:inline-block; padding:.2rem .5rem; border:1px solid var(--card-border); border-radius:999px; margin-left:.5rem; font-size:.85em; background:rgba(0,0,0,.03);">{{ event_name or 'Fun Speed Dating and Friending' }}</span></h2>
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
            <p class="note">Keine Matches für <strong>{{ label }}</strong> gefunden.</p>
          {% endif %}
        {% endfor %}

        <form class="row" style="margin-top:1rem" method="get" action="/api/match/dual">
          <input type="hidden" name="file_token" value="{{ file_token }}">
          <input type="hidden" name="interested-columns" value="{{ interested_columns|join(',') }}">
          <input type="hidden" name="labels" value="{{ labels|join(',') }}">
          {% if event_name %}<input type="hidden" name="event" value="{{ event_name }}">{% endif %}
          <button class="btn secondary" type="submit">ZIP herunterladen</button>
        </form>

        <form class="row" style="margin-top:.6rem" method="get" action="/export/mail-merge">
          <input type="hidden" name="file_token" value="{{ file_token }}">
          <input type="hidden" name="interested-columns" value="{{ interested_columns|join(',') }}">
          <input type="hidden" name="labels" value="{{ labels|join(',') }}">
          {% if event_name %}<input type="hidden" name="event" value="{{ event_name }}">{% endif %}
          <button class="btn" type="submit">Mail-Merge Export (Thunderbird)</button>
          <a class="btn secondary" href="/help/mail-merge" target="_blank" rel="noopener">Hilfe</a>
        </form>
      </div>
    {% endif %}
  </div>
</body>
</html>
"""

# ================================ HTML: CSV-Builder ==========================
_CSV_BUILDER_HTML = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>CSV-Builder – Speed Friending & Dating</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>""" + _BASE_STYLE + """</style>
  """ + _DARKMODE_SCRIPT + """
</head>
<body>
  <div class="card">
    <div class="titlebar">
      <div class="links">
        <a href="/" title="Home"><span>🌈</span>Home</a>
        <a href="/offline/form" title="Offline-Formular"><span>🌈</span>Offline-Formular</a>
        <a href="/help/mail-merge" title="Mail-Merge Hilfe"><span>🌈</span>Mail-Merge Hilfe</a>
        <a href="/example/dual_interest_sample.csv" title="Beispiel-CSV"><span>🌈</span>Sample CSV</a>
      </div>
      """ + _THEME_TOGGLE_HTML + """
    </div>

    <h1 style="margin:0;">CSV-Builder</h1>
    <p class="note">Erfasse Teilnehmerdaten und erzeuge eine CSV mit den Spalten: <code>ID,Name,Email,Phone,All,InterestedDating,InterestedFriendship</code>.</p>

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
        <span class="note">Tipp: IDs fortlaufend vergeben. Interessenslisten mit Semikolon trennen.</span>
      </div>

      <div class="row" style="margin-top:1rem;">
        <button class="btn" type="submit">CSV erzeugen & downloaden</button>
        <button class="btn secondary" type="button" id="evalNow">CSV erzeugen & sofort auswerten</button>
        <label class="row" style="gap:.4rem;">Event:
          <input type="text" id="eventName" placeholder="Eventname" value="Fun Speed Dating and Friending" />
        </label>
        <label class="row" style="gap:.4rem;">Interested Columns:
          <input type="text" id="cols" value="InterestedDating,InterestedFriendship" />
        </label>
        <label class="row" style="gap:.4rem;">Labels:
          <input type="text" id="labs" value="dating,friendship" />
        </label>
      </div>
    </form>
  </div>

  <script>
    const tbody = document.querySelector('#tbl tbody');
    const addRowBtn = document.getElementById('addRow');
    const evalNowBtn = document.getElementById('evalNow');

    function esc(v){
      return String(v == null ? '' : v)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function newRow(data){
      data = data || {};
      const html =
        '<td><input name="id[]" type="number" min="1" value="' + esc(data.id||'') + '" required></td>' +
        '<td><input name="name[]" type="text" value="' + esc(data.name||'') + '" required></td>' +
        '<td><input name="email[]" type="email" value="' + esc(data.email||'') + '" required></td>' +
        '<td><input name="phone[]" type="text" value="' + esc(data.phone||'') + '"></td>' +
        '<td><input name="all[]" type="text" placeholder="2;3" value="' + esc(data.all||'') + '"></td>' +
        '<td><input name="dating[]" type="text" placeholder="2;3" value="' + esc(data.dating||'') + '"></td>' +
        '<td><input name="friend[]" type="text" placeholder="2;3" value="' + esc(data.friend||'') + '"></td>' +
        '<td style="text-align:center;"><button class="btn secondary" type="button" onclick="this.closest(\\'tr\\').remove()">–</button></td>';
      const tr = document.createElement('tr');
      tr.innerHTML = html;
      tbody.appendChild(tr);
    }

    function tableToCSV(){
      const rows = Array.from(tbody.querySelectorAll('tr'));
      const data = rows.map(tr => {
        const tds = tr.querySelectorAll('input');
        return {
          id: tds[0]?.value || '',
          name: tds[1]?.value || '',
          email: tds[2]?.value || '',
          phone: tds[3]?.value || '',
          all: tds[4]?.value || '',
          dating: tds[5]?.value || '',
          friend: tds[6]?.value || '',
        };
      }).filter(r => Object.values(r).some(v => String(v).trim() !== ''));
      const header = ["ID","Name","Email","Phone","All","InterestedDating","InterestedFriendship"];
      const lines = [header.join(",")];
      for (const r of data){
        const row = [r.id, r.name, r.email, r.phone, r.all, r.dating, r.friend].map(v => {
          const s = String(v ?? '');
          return (s.includes(',') || s.includes(';') || s.includes('"')) ? '"' + s.replace(/"/g,'""') + '"' : s;
        }).join(",");
        lines.push(row);
      }
      return lines.join("\\n");
    }

    addRowBtn.addEventListener('click', () => newRow());
    // zwei Beispielzeilen
    newRow({id:1, name:"Alex (they/them)", email:"alex@example.com", phone:"+4311111", all:"2;3", dating:"2", friend:"3"});
    newRow({id:2, name:"Quinn",             email:"quinn@example.com", phone:"+4322222", all:"1;3", dating:"1", friend:""});

    evalNowBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      const csv = tableToCSV();
      if(!csv.trim()){ alert("Bitte mindestens eine Zeile erfassen."); return; }
      const file = new File([csv], "participants.csv", {type:"text/csv"});
      const fd = new FormData();
      fd.append("file", file);
      fd.append("interested-columns", document.getElementById("cols").value || "Interested");
      fd.append("labels", document.getElementById("labs").value || "");
      fd.append("event", document.getElementById("eventName").value || "");

      const res = await fetch("/ui/match", { method:"POST", body:fd });
      const html = await res.text();
      document.open(); document.write(html); document.close();
      window.scrollTo(0,0);
    });
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
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; background: #fafafa; color:#111; }
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
    <a class="btn secondary" href="/offline/form/download" title="Als HTML speichern">Als HTML speichern</a>
    <a class="btn" href="/offline/form/pdf" title="Als PDF herunterladen">Als PDF (A4) herunterladen</a>
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
        <!-- 12 Zeilen -->
        """ + ("\n".join(['<tr><td>&nbsp;</td><td style="height:38px;">&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>']*12)) + """
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
  <style>""" + _BASE_STYLE + """ .card{max-width:980px;margin:auto;} </style>
  """ + _DARKMODE_SCRIPT + """
</head>
<body>
  <div class="card">
    <div class="titlebar">
      <h1 style="margin:0;">Mail-Merge (Thunderbird) – Kurzanleitung</h1>
      """ + _THEME_TOGGLE_HTML + """
    </div>
    <p class="note">Mit dem Export „Mail-Merge“ kannst du deine Matches in einer CSV + E-Mail-Vorlage exportieren und in Thunderbird personalisiert verschicken.</p>

    <h2>Installieren</h2>
    <ol>
      <li>Thunderbird herunterladen: <a href="https://www.thunderbird.net/" target="_blank" rel="noopener">thunderbird.net</a></li>
      <li>Add-on „Mail Merge“ installieren: <a href="https://addons.thunderbird.net/en-US/thunderbird/addon/mail-merge/" target="_blank" rel="noopener">addons.thunderbird.net → Mail Merge</a></li>
    </ol>

    <h2>Export & Versand</h2>
    <ol>
      <li>Auf der Startseite CSV hochladen oder im CSV-Builder erfassen und auswerten.</li>
      <li>„Mail-Merge Export (Thunderbird)“ anklicken ⇒ ZIP speichern & entpacken.</li>
      <li>Thunderbird: neue E-Mail erstellen (noch nicht senden).</li>
      <li>Betreff/Text aus <code>email_template.txt</code> kopieren. Platzhalter: <code>{{Name}}</code>, <code>{{Event}}</code>, <code>{{DatingMatches}}</code>, <code>{{FriendshipMatches}}</code>, <code>{{To}}</code>.</li>
      <li>Im Verfassen-Fenster: <em>Menü</em> → <strong>Mail Merge…</strong></li>
      <li>CSV-Datei <code>mailmerge.csv</code> wählen. Spalte <code>To</code> = Empfänger (oder „Empfänger (To): {{To}}“ händisch kopieren).</li>
      <li>Test mit „Send Later“/„Preview“, dann senden.</li>
    </ol>

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

@app.route("/offline/form/pdf", methods=["GET"])
def offline_form_pdf():
    """
    Erstellt ein sehr einfaches A4-PDF (ReportLab, falls verfügbar).
    Fallback: Liefert die HTML-Version, wenn ReportLab fehlt.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph("Speed Friending & Dating – Offline-Formular", styles['Title']))
        story.append(Spacer(1, 12))
        story.append(Paragraph("Dieses Blatt erklärt kurz das Event und bietet Platz für Notizen & Likes. "
                               "Nach dem Event kannst du deine Angaben online eintragen oder abgeben.", styles['Normal']))
        story.append(Spacer(1, 12))

        story.append(Paragraph("Wie funktioniert’s?", styles['Heading2']))
        bullet = [
            "Du triffst mehrere Personen für kurze Gespräche („Runden“).",
            "Nach jedem Gespräch kannst du ankreuzen, ob du die Person dating-interessant oder freundschaftlich interessant findest.",
            "Am Ende trägst du deine Likes online ein (oder gibst dieses Blatt ab)."
        ]
        for b in bullet:
            story.append(Paragraph("• " + b, styles['Normal']))
        story.append(Spacer(1, 12))

        story.append(Paragraph("Deine Daten", styles['Heading2']))
        datatable = Table([
            ["ID-Nummer", ""],
            ["Name", ""],
            ["Email", ""],
            ["Telefon", ""],
        ], colWidths=[5*cm, 10*cm])
        datatable.setStyle(TableStyle([
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ]))
        story.append(datatable)
        story.append(Spacer(1, 12))

        story.append(Paragraph("Gesprächspartner & Notizen", styles['Heading2']))
        rows = [["ID", "Name / Notizen", "Dating 👍", "Friendship 🤝"]]
        for _ in range(12):
            rows.append(["", "", "", ""])
        t = Table(rows, colWidths=[2*cm, 9*cm, 3*cm, 3*cm])
        t.setStyle(TableStyle([
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ]))
        story.append(t)
        story.append(Spacer(1, 12))

        story.append(Paragraph("Zusammenfassung (IDs mit Semikolon trennen)", styles['Heading2']))
        sumtab = Table([
            ["All (optional)", ""],
            ["InterestedDating", ""],
            ["InterestedFriendship", ""],
        ], colWidths=[6*cm, 9*cm])
        sumtab.setStyle(TableStyle([
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ]))
        story.append(sumtab)
        story.append(Spacer(1, 12))

        story.append(Paragraph("Online-Eingabe: match.sonsti.top → CSV-Builder oder Home (CSV-Upload).", styles['Normal']))

        doc.build(story)
        data = buf.getvalue()
        resp = make_response(data)
        resp.headers["Content-Type"] = "application/pdf"
        resp.headers["Content-Disposition"] = "attachment; filename=offline_form.pdf"
        resp.headers["Content-Length"] = str(len(data))
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    except Exception:
        # Fallback: HTML-Datei anbieten
        return offline_form_download()

# ================================ Mail-Merge Hilfe ==========================
@app.route("/help/mail-merge", methods=["GET"])
def help_mail_merge():
    return render_template_string(_MAIL_MERGE_HELP_HTML)

# ================================ API (ZIP) =================================
@app.route("/api/match/dual", methods=["GET", "POST"])
def api_match_dual():
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
            people = _load_people_from_csv(tmp.name, tuple(cols))
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

# ============================== API: Build CSV ===============================
@app.route("/api/build-csv", methods=["POST"])
def api_build_csv():
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

# ============================ Mail-Merge Export API ==========================
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

    # Matches bilden
    matches_raw = _build_matches(people, cols)
    # Labels den Spalten zuordnen (Reihenfolge beachten)
    matches_by_label = {labels[i]: matches_raw[cols[i]] for i in range(len(cols))}

    mm_csv, mm_template = _build_mailmerge_csv_and_template(people, matches_by_label, event_name)

    # ZIP bauen
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mailmerge.csv", mm_csv)
        zf.writestr("email_template.txt", mm_template)
        # zur Referenz auch die Matches als CSVs mitgeben
        packed = _zip_from_matches(people, matches_by_label, name_prefix="matches")
        zf.writestr("matches_bundle.zip", packed)
    data = mem.getvalue()

    resp = make_response(data)
    resp.headers["Content-Type"] = "application/zip"
    resp.headers["Content-Disposition"] = "attachment; filename=mailmerge_export.zip"
    resp.headers["Content-Length"] = str(len(data))
    resp.headers["Cache-Control"] = "no-cache"
    return resp

# =============================== Beispiel-CSV Route ==========================
_SAMPLE_CSV = """ID,Name,Email,Phone,All,InterestedDating,InterestedFriendship
1,Alex (they/them),alex@example.com,+4311111,"2;3;4","2;3","4"
2,Quinn,quinn@example.com,+4322222,"1;3;5","1","3;5"
3,Jamie,jamie@example.com,+4333333,"1;2;4;6","2;4","1;5"
4,Taylor,taylor@example.com,+4344444,"1;3;7","1","3;6"
5,Jordan,jordan@example.com,+4355555,"2;6;7","6","2;3"
6,Morgan,morgan@example.com,+4366666,"3;5;8","5","1;2"
7,Casey,casey@example.com,+4377777,"4;5;8;9","5;9","6"
8,Rowan,rowan@example.com,+4388888,"6;7;9","","7;9"
9,Skylar,skylar@example.com,+4399999,"7;8;10","10","8"
10,Finley,finley@example.com,+43101010,"9","9",""
"""

@app.route("/example/dual_interest_sample.csv", methods=["GET"])
def example_csv():
    return make_response((_SAMPLE_CSV, 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": "attachment; filename=dual_interest_sample.csv",
        "Cache-Control": "no-cache",
    }))

# ============================== Main (Debug run) ============================
if __name__ == "__main__":
    # Produktion läuft über Gunicorn in systemd
    app.run(host="0.0.0.0", port=5000, debug=False)
