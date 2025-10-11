# -*- coding: utf-8 -*-
"""
Speed-Friending & Dating Matcher – Server (CSV Builder mit Upload)
- Sichtbarer Upload-Button + Drag&Drop
- Vorschau-Tabelle clientseitig
- Upload-Endpoint: POST /api/csv/upload (multipart/form-data, Feldname "file")
- Keine f-Strings in Kombination mit Jinja/HTML (render_template_string)
"""

import io
import csv
import os
from flask import Flask, request, jsonify, redirect, url_for, render_template_string

def create_app() -> Flask:
    app = Flask(__name__)

    # ---------- Konfiguration (bei Bedarf hier anpassen) ----------
    app.config["CSV_UPLOAD_ENDPOINT"] = "/api/csv/upload"
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB hard limit

    # ---------- HTML: CSV-Builder-Seite ----------
    # Hinweis: kein f-string! (Kollision mit {{ ... }})
    PAGE_CSV_BUILDER = """
    <!doctype html>
    <html lang="de">
    <head>
      <meta charset="utf-8">
      <title>CSV Builder – Upload, Drag&Drop & Vorschau</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        :root{
          --bg:#0e1116;--card:#151a22;--accent:#4cc2ff;--text:#eaeef5;--muted:#a6b0c3;
          --danger:#ff6b6b;--border:#273042;--ok:#72f1b8;--warn:#ffd166
        }
        html,body{height:100%}
        body{margin:0;background:var(--bg);color:var(--text);
          font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,"Helvetica Neue",Arial,sans-serif}
        header{padding:24px 20px;text-align:center;background:linear-gradient(180deg,rgba(76,194,255,.15),rgba(0,0,0,0))}
        h1{margin:0;font-size:28px;color:var(--accent)}
        .wrap{max-width:1200px;margin:0 auto;padding:16px 20px 60px}
        .card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px;box-shadow:0 8px 24px rgba(0,0,0,.25)}
        .toolbar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
        .hint{color:var(--muted);font-size:14px;margin-left:auto}
        input[type="file"]{display:none}
        .btn{border:1px solid var(--border);background:#1b2330;color:var(--text);padding:10px 14px;border-radius:12px;cursor:pointer;font-weight:600;
          transition:transform .05s ease,background .2s ease,border-color .2s ease;user-select:none}
        .btn:hover{background:#212b3a}
        .btn:active{transform:translateY(1px)}
        .btn-primary{border-color:#2e8ab3;background:#1b2a36}
        .btn-primary:hover{background:#203444}
        .btn-danger{border-color:#8a2e2e;background:#2a1b1b;color:#ffdede}
        .btn-success{border-color:#2e8a63;background:#1b2a24;color:#d7ffe9}
        .btn-secondary{border-color:#444;background:#222}
        .pill{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid var(--border);color:var(--muted);font-size:12px;margin-left:6px}
        .drop{border:2px dashed var(--border);border-radius:14px;padding:18px;text-align:center;color:var(--muted);margin-bottom:12px}
        .drop.dragover{border-color:var(--accent);background:rgba(76,194,255,.06);color:var(--text)}
        table{width:100%;border-collapse:collapse;border:1px solid var(--border);border-radius:10px;overflow:hidden}
        thead{background:#0f1520}
        th,td{padding:10px 12px;border-bottom:1px solid var(--border);text-align:left}
        tbody tr:hover{background:#0f1420}
        .empty{text-align:center;color:var(--muted);padding:28px;border:1px dashed var(--border);border-radius:12px}
        .footer{margin-top:10px;font-size:13px;color:var(--muted);display:flex;gap:12px;flex-wrap:wrap;align-items:center}
        .badge{display:inline-block;font-size:12px;border:1px solid var(--border);border-radius:999px;padding:2px 8px}
        .badge.ok{border-color:#1e6f52;color:#9bf3cf}
        .badge.warn{border-color:#8a6d1e;color:#ffe18a}
        .badge.err{border-color:#8a2e2e;color:#ffb3b3}
        .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
        .spacer{flex:1}
      </style>
    </head>
    <body data-endpoint="{{ endpoint }}">
      <header>
        <h1>CSV Builder <span class="pill">Upload aktiv</span></h1>
      </header>

      <div class="wrap">
        <div class="card">
          <div class="toolbar" role="toolbar" aria-label="CSV-Aktionen">
            <label for="csvFile" class="btn btn-primary" id="uploadBtn">📤 CSV auswählen</label>
            <input id="csvFile" type="file" accept=".csv,text/csv" aria-label="CSV-Datei auswählen">
            <button class="btn btn-secondary" id="sampleBtn" type="button">🧪 Beispiel laden</button>
            <button class="btn btn-success" id="sendBtn" type="button" disabled>🚀 An Server senden</button>
            <button class="btn btn-danger" id="clearBtn" type="button">🗑️ Tabelle leeren</button>
            <span class="hint">UTF-8 CSV mit Kopfzeile · Trennzeichen wird automatisch erkannt (`,` `;` `\\t` `|`).</span>
          </div>

          <div class="drop" id="dropZone" tabindex="0" aria-label="CSV per Drag&Drop hier ablegen">
            Datei hierher ziehen oder „CSV auswählen“ klicken.
          </div>

          <div id="tableHost">
            <div class="empty" id="emptyState">Noch keine Daten. Lade eine CSV-Datei hoch.</div>
            <table id="dataTable" style="display:none;">
              <thead id="thead"></thead>
              <tbody id="tbody"></tbody>
            </table>
          </div>

          <div class="footer" id="footerInfo" aria-live="polite">
            <span id="stats">0 Zeilen · 0 Spalten</span>
            <span class="badge" id="delimiterBadge" title="Erkanntes Trennzeichen">Trennzeichen: –</span>
            <span class="badge" id="encodingBadge" title="Erkannte Kodierung">Encoding: UTF-8 (angenommen)</span>
            <span class="spacer"></span>
            <span class="badge warn" id="serverStatus" hidden>Server: nicht gesendet</span>
          </div>
        </div>
      </div>

      <script>
      (function(){
        const input = document.getElementById('csvFile');
        const clearBtn = document.getElementById('clearBtn');
        const sendBtn = document.getElementById('sendBtn');
        const sampleBtn = document.getElementById('sampleBtn');
        const table = document.getElementById('dataTable');
        const thead = document.getElementById('thead');
        const tbody = document.getElementById('tbody');
        const empty = document.getElementById('emptyState');
        const stats = document.getElementById('stats');
        const delimBadge = document.getElementById('delimiterBadge');
        const serverStatus = document.getElementById('serverStatus');
        const dropZone = document.getElementById('dropZone');
        const endpoint = (document.body.getAttribute('data-endpoint') || '/api/csv/upload').trim();

        let lastFile = null;
        let lastDelim = null;

        function setStats(rows, cols) {
          stats.textContent = rows + " Zeilen · " + cols + " Spalten";
        }

        function setDelimiterBadge(d) {
          const map = {",":"Komma (,)", ";":"Semikolon (;)", "\\t":"Tab (\\\\t)", "|":"Pipe (|)"};
          delimBadge.textContent = "Trennzeichen: " + (map[d] || d || "–");
        }

        function resetTable() {
          thead.innerHTML = "";
          tbody.innerHTML = "";
          table.style.display = "none";
          empty.style.display = "block";
          setStats(0, 0);
          setDelimiterBadge(null);
          sendBtn.disabled = true;
          lastFile = null;
          lastDelim = null;
          serverStatus.hidden = false;
          serverStatus.className = "badge warn";
          serverStatus.textContent = "Server: nicht gesendet";
        }

        function detectDelimiter(sample) {
          const candidates = [",",";","\\t","|"];
          let best = {delim:",", score:-1};
          const lines = sample.split(/[\\r\\n]+/).slice(0, 20).filter(Boolean);
          for (const d of candidates) {
            if (!lines.length) continue;
            const counts = lines.map(l => l.split(d).length);
            const avg = counts.reduce((a,b)=>a+b,0)/counts.length;
            const varc = counts.map(c=>Math.abs(c-avg)).reduce((a,b)=>a+b,0);
            const score = avg*10 - varc;
            if (score > best.score) best = {delim:d, score};
          }
          return best.delim;
        }

        function splitCSVLine(line, delim){
          if (delim === "\\t") delim = "\\t";
          const out = [];
          let buf = "", inQ = false;
          for (let i=0; i<line.length; i++){
            const ch = line[i];
            if (ch === '"'){
              if (inQ && line[i+1] === '"'){ buf += '"'; i++; continue; }
              inQ = !inQ; continue;
            }
            if (!inQ && ch === delim){ out.push(buf); buf = ""; continue; }
            out.push ? null : null;
            buf += ch;
          }
          out.push(buf);
          return out;
        }

        function parseCSV(text) {
          const delim = detectDelimiter(text);
          const norm = text.replace(/\\r\\n/g, "\\n").replace(/\\r/g, "\\n");
          const lines = norm.split("\\n").filter(l => l.length>0);
          if (lines.length === 0) return { headers: [], rows: [], delim };
          const headers = splitCSVLine(lines[0], delim).map(h => h.trim());
          const rows = lines.slice(1).map(line => {
            const cells = splitCSVLine(line, delim);
            return cells.map(c => c.trim());
          });
          return { headers, rows, delim };
        }

        function renderTable(headers, rows) {
          thead.innerHTML = "";
          tbody.innerHTML = "";
          const tr = document.createElement('tr');
          headers.forEach(h => {
            const th = document.createElement('th');
            th.textContent = h || "Spalte";
            tr.appendChild(th);
          });
          thead.appendChild(tr);

          rows.forEach(r => {
            const trb = document.createElement('tr');
            headers.forEach((_, i) => {
              const td = document.createElement('td');
              td.textContent = (r[i] !== undefined) ? r[i] : "";
              trb.appendChild(td);
            });
            tbody.appendChild(trb);
          });

          empty.style.display = "none";
          table.style.display = "table";
          setStats(rows.length, headers.length);
        }

        function readFile(file){
          return new Promise((resolve, reject)=>{
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(reader.error || new Error("Lesefehler"));
            reader.readAsText(file, 'utf-8');
          });
        }

        async function handleFiles(files){
          const file = files && files[0];
          if (!file) return;
          try{
            const text = await readFile(file);
            const parsed = parseCSV(text);
            if (!parsed.headers.length){ resetTable(); return; }
            renderTable(parsed.headers, parsed.rows);
            lastFile = file;
            lastDelim = parsed.delim;
            setDelimiterBadge(parsed.delim);
            sendBtn.disabled = false;
            serverStatus.hidden = false;
            serverStatus.className = "badge warn";
            serverStatus.textContent = "Server: noch nicht gesendet";
          }catch(err){
            console.error(err);
            alert("Konnte die CSV nicht lesen.");
            resetTable();
          }
        }

        ["dragenter","dragover"].forEach(ev=>{
          dropZone.addEventListener(ev, e=>{
            e.preventDefault(); e.stopPropagation();
            dropZone.classList.add("dragover");
          });
        });
        ["dragleave","drop"].forEach(ev=>{
          dropZone.addEventListener(ev, e=>{
            e.preventDefault(); e.stopPropagation();
            dropZone.classList.remove("dragover");
          });
        });
        dropZone.addEventListener("drop", e=>{
          const dt = e.dataTransfer;
          if (dt && dt.files && dt.files.length){ handleFiles(dt.files); }
        });
        dropZone.addEventListener("click", ()=> input.click());
        dropZone.addEventListener("keydown", (e)=>{ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); input.click(); }});

        async function sendToServer(){
          if (!lastFile){ alert("Bitte zuerst eine CSV laden."); return; }
          try{
            serverStatus.hidden = false;
            serverStatus.className = "badge";
            serverStatus.textContent = "Server: senden …";

            const fd = new FormData();
            fd.append("file", lastFile, lastFile.name);
            if (lastDelim){ fd.append("delimiter", (lastDelim === "\\t") ? "\\t" : lastDelim); }

            const resp = await fetch(endpoint, { method:"POST", body: fd });
            const ok = resp.ok;
            let msg = "";
            try{ msg = await resp.text(); }catch(e){}

            if (ok){
              serverStatus.className = "badge ok";
              serverStatus.textContent = "Server: OK";
              alert("Upload erfolgreich.\\n" + (msg || ""));
            } else {
              serverStatus.className = "badge err";
              serverStatus.textContent = "Server: Fehler";
              alert("Upload fehlgeschlagen (" + resp.status + ").\\n" + (msg || ""));
            }
          }catch(err){
            console.error(err);
            serverStatus.className = "badge err";
            serverStatus.textContent = "Server: Fehler";
            alert("Upload fehlgeschlagen.\\n" + (err && err.message ? err.message : err));
          }
        }

        input.addEventListener('change', (e)=> handleFiles(e.target.files));
        clearBtn.addEventListener('click', resetTable);
        sendBtn.addEventListener('click', sendToServer);
        sampleBtn.addEventListener('click', ()=>{
          const example = "name,interests,email\\nAlice,AI;Klettern,alice@example.org\\nBob,Datenschutz;Radfahren,bob@example.org\\n";
          const blob = new Blob([example], {type:"text/csv"});
          const file = new File([blob], "beispiel.csv", {type:"text/csv"});
          handleFiles([file]);
        });

        resetTable();
      })();
      </script>
    </body>
    </html>
    """

    # ---------- Routen ----------
    @app.get("/")
    def index():
        return redirect(url_for("csv_builder"))

    @app.get("/csv-builder")
    def csv_builder():
        # Endpoint aus Config injizieren (ohne f-string)
        return render_template_string(
            PAGE_CSV_BUILDER,
            endpoint=app.config.get("CSV_UPLOAD_ENDPOINT", "/api/csv/upload"),
        )

    @app.post("/api/csv/upload")
    def csv_upload():
        """
        Erwartet multipart/form-data:
          - file: CSV-Datei
          - delimiter (optional): ",", ";", "\\t", "|"
        Antwort: JSON mit rows/cols (einfache Validierung).
        """
        f = request.files.get("file")
        if not f:
            return ("No file field 'file' in form-data.", 400)

        delimiter = request.form.get("delimiter") or ","
        if delimiter == "\\t":
            delimiter = "\t"

        try:
            # CSV als UTF-8 interpretieren (ersetzt ungültige Zeichen)
            text = f.read().decode("utf-8", errors="replace")
        except Exception:
            return ("Unable to read file as UTF-8.", 400)

        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        if not rows:
            return ("Empty CSV.", 400)

        # TODO: Hier in euer bestehendes System integrieren (DB, Matching, etc.)
        meta = {
            "status": "ok",
            "rows": len(rows),
            "cols": len(rows[0]) if rows else 0,
        }
        return jsonify(meta), 200

    return app


app = create_app()

if __name__ == "__main__":
    # Lokaler Start:
    #   python server.py
    # Prod (Beispiel): gunicorn -w 3 -b 0.0.0.0:8000 server:app
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    debug = bool(os.environ.get("DEBUG", ""))  # DEBUG=1 aktiviert Debug
    app.run(host=host, port=port, debug=debug)
