# coding=utf-8
#
# UI-Fix:
# - Paarlisten sind je Person alphabetisch sortiert (IDs), damit keine "letzte Person ohne Anzeige" entsteht.
# - Die UI-Tabelle rendert jetzt für alle Personenzeilen konsistent.
# - people_ids_sorted wird der Vorlage mitgegeben und verwendet.
#
# API/Export bleiben unverändert. Nur Anzeige wurde angepasst.

from __future__ import annotations

import csv
import io
import os
import tempfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from flask import (
    Flask,
    request,
    jsonify,
    render_template_string,
    make_response,
    send_file,
)

app = Flask(__name__)

# ---------- Domänenmodell ----------


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    interests_a: List[str]
    interests_b: List[str]


Pair = Tuple[str, str]  # (id1, id2)


# ---------- CSV I/O ----------


def _load_people_from_csv(path: str, cols: Tuple[str, str, str, str]) -> List[Person]:
    """Erwartet 4 Spalten: id, name, interested_in_a, interested_in_b."""
    id_col, name_col, col_a, col_b = cols
    out: List[Person] = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = str(row.get(id_col, "")).strip()
            name = str(row.get(name_col, "")).strip()
            a_raw = str(row.get(col_a, "") or "")
            b_raw = str(row.get(col_b, "") or "")
            a = [x.strip() for x in a_raw.split(",") if x.strip()]
            b = [x.strip() for x in b_raw.split(",") if x.strip()]
            if not pid:
                # überspringe leere/defekte Zeilen
                continue
            out.append(Person(pid, name, a, b))
    return out


# ---------- Matching-Logik ----------


def _build_matches(people: Sequence[Person]) -> Dict[str, List[str]]:
    """
    Simple "mutual interest": A mag B UND B mag A -> Match.
    Gibt pro Person-ID die Liste der Partner-IDs zurück.
    """
    idx: Dict[str, Person] = {p.id: p for p in people}

    # Erzeuge gerichtete Präferenzkanten
    likes: Dict[str, set] = {}
    for p in people:
        likes[p.id] = set(p.interests_a + p.interests_b)

    # Symmetrische Kanten finden
    matches: Dict[str, List[str]] = {p.id: [] for p in people}
    for a in people:
        for b_id in likes[a.id]:
            if b_id in likes and a.id in likes.get(b_id, set()):
                # füge wechselseitiges Match ein
                if b_id not in matches[a.id]:
                    matches[a.id].append(b_id)
                if a.id not in matches[b_id]:
                    matches[b_id].append(a.id)

    # Sortierung je Person, damit UI stabil ist
    for pid in matches:
        matches[pid].sort(key=lambda x: (x,))
    return matches


def _zip_from_matches(matches: Dict[str, List[str]]) -> List[Pair]:
    """Flache Paarliste aus dem Match-Dict, normalisiert (kleinere ID zuerst)."""
    out: set[Pair] = set()
    for a, partners in matches.items():
        for b in partners:
            a1, b1 = sorted((a, b))
            out.add((a1, b1))
    return sorted(out, key=lambda ab: (ab[0], ab[1]))


def _format_partner_list(pid: str, matches: Dict[str, List[str]], id_to_name: Dict[str, str]) -> List[Tuple[str, str]]:
    """
    Für UI: Liste von (partner_id, partner_name), alphabetisch nach partner_id sortiert.
    Auch wenn es KEINE Matches gibt, liefern wir eine leere Liste, damit im Template
    keine Person "verschwindet".
    """
    partners = matches.get(pid, [])
    partners_sorted = sorted(partners, key=lambda x: (x,))
    return [(p, id_to_name.get(p, p)) for p in partners_sorted]


# ---------- Routen ----------


@app.get("/")
def index():
    # Früher wurde hier ein 404 angezeigt; leite auf das Offline-Formular.
    from flask import redirect

    return redirect("/offline/form", code=302)


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
        matches = _build_matches(people)

        # Stabile Personenliste für Anzeige
        id_to_name = {p.id: p.name for p in people}
        people_ids_sorted = sorted([p.id for p in people], key=lambda x: (x,))

        # Ergebnisse für Anzeige vorbereiten
        results_view: Dict[str, List[Tuple[str, str]]] = {
            pid: _format_partner_list(pid, matches, id_to_name) for pid in people_ids_sorted
        }

        return render_template_string(
            _INDEX_HTML,
            error=None,
            people_ids_sorted=people_ids_sorted,
            id_to_name=id_to_name,
            results=results_view,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.post("/api/match/dual")
def api_match_dual():
    """
    JSON API:
    {
      "cols": ["id","name","interested_a","interested_b"],
      "csv": "<csv-data als string>"
    }
    """
    js = request.get_json(silent=True) or {}
    cols = js.get("cols") or []
    data = js.get("csv") or ""
    if not isinstance(cols, list) or len(cols) != 4 or not isinstance(data, str):
        return jsonify({"error": "Bad request"}), 400

    with tempfile.NamedTemporaryFile(prefix="upload_", suffix=".csv", delete=False) as tmp:
        tmp.write(data.encode("utf-8"))
        tmp.flush()
        tmp_path = tmp.name

    try:
        people = _load_people_from_csv(tmp_path, tuple(cols))  # type: ignore[arg-type]
        matches = _build_matches(people)
        pairs = _zip_from_matches(matches)
        return jsonify(
            {
                "people": [p.__dict__ for p in people],
                "matches": matches,
                "pairs": pairs,
            }
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.post("/export/mail-merge")
def export_mail_merge():
    """
    CSV-Export für Seriendruck:
    Spalten: id, name, partners (kommagetrennt)
    """
    f = request.files.get("file")
    cols = request.form.getlist("cols")
    if not f or len(cols) != 4:
        return "Bad request", 400

    with tempfile.NamedTemporaryFile(prefix="upload_", suffix=".csv", delete=False) as tmp:
        f.stream.seek(0)
        tmp.write(f.read())
        tmp.flush()
        tmp_path = tmp.name

    try:
        people = _load_people_from_csv(tmp_path, tuple(cols))  # type: ignore[arg-type]
        matches = _build_matches(people)
        id_to_name = {p.id: p.name for p in people}
        people_ids_sorted = sorted([p.id for p in people], key=lambda x: (x,))

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "name", "partners"])
        for pid in people_ids_sorted:
            partners = [id_to_name.get(x, x) for x in matches.get(pid, [])]
            writer.writerow([pid, id_to_name.get(pid, pid), ", ".join(partners)])

        csv_bytes = output.getvalue().encode("utf-8")
        return send_file(
            io.BytesIO(csv_bytes),
            mimetype="text/csv",
            as_attachment=True,
            download_name="mail_merge.csv",
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.get("/example/dual_interest_sample.csv")
def example_csv():
    sample = """id,name,interested_a,interested_b
1,Alice,2,3
2,Bob,1,3
3,Carol,1,2
"""
    return make_response(sample, 200, {"Content-Type": "text/csv; charset=utf-8"})


@app.get("/offline/form")
def offline_form():
    return render_template_string(_OFFLINE_FORM_HTML)


@app.get("/offline/form/download")
def offline_form_download():
    pdf_bytes = _OFFLINE_FORM_PDF  # Placeholder: hier könnte ein echtes PDF geladen werden
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True, download_name="offline-form.pdf")


@app.get("/offline/form/pdf")
def offline_form_pdf():
    pdf_bytes = _OFFLINE_FORM_PDF
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf")


@app.get("/help/mail-merge")
def help_mail_merge():
    return render_template_string(_MAIL_MERGE_HELP_HTML)


@app.get("/health")
def health():
    return "ok", 200


# ---------- Inline-Templates ----------

_INDEX_HTML = r"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Matcher – Dual Interests</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"; margin: 1.5rem; }
    h1 { margin: 0 0 1rem 0; font-size: 1.5rem; }
    .muted { opacity: .8 }
    table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
    th, td { border: 1px solid rgba(127,127,127,.35); padding: .5rem .6rem; vertical-align: top; }
    th { text-align: left; }
    code, input, button, select { font: inherit; }
    .error { color: #b00020; font-weight: 600; }
    .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
    .card { border: 1px solid rgba(127,127,127,.35); border-radius: .5rem; padding: 1rem; }
    .heading-strong { font-weight: 700; }
    .contrast { color: CanvasText; } /* dunkles Schema: voller Kontrast */
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .tag { display:inline-block; padding:.1rem .4rem; border-radius:.25rem; border:1px solid rgba(127,127,127,.35); margin:.1rem .2rem 0 0; font-size:.9rem; }
  </style>
</head>
<body>
  <h1 class="heading-strong contrast">Matcher – Dual Interests</h1>

  {% if error %}
    <p class="error">{{ error }}</p>
  {% endif %}

  <form class="card" action="/ui/match" method="post" enctype="multipart/form-data">
    <div class="grid">
      <label>CSV-Datei
        <input type="file" name="file" required />
      </label>
      <label>ID-Spalte
        <input type="text" name="cols" value="id" required />
      </label>
      <label>Name-Spalte
        <input type="text" name="cols" value="name" required />
      </label>
      <label>Interested A
        <input type="text" name="cols" value="interested_a" required />
      </label>
      <label>Interested B
        <input type="text" name="cols" value="interested_b" required />
      </label>
    </div>
    <div style="margin-top: .75rem">
      <button type="submit">Matchen</button>
    </div>
  </form>

  {% if results %}
    <h2>Ergebnisse</h2>
    <table>
      <thead>
        <tr>
          <th class="mono">ID</th>
          <th>Name</th>
          <th>Matches</th>
        </tr>
      </thead>
      <tbody>
        {% for pid in people_ids_sorted %}
          <tr>
            <td class="mono">{{ pid }}</td>
            <td>{{ id_to_name[pid] }}</td>
            <td>
              {% set partners = results.get(pid, []) %}
              {% if partners and partners|length %}
                {% for partner_id, partner_name in partners %}
                  <span class="tag mono">{{ partner_id }}</span> {{ partner_name }}<br/>
                {% endfor %}
              {% else %}
                <span class="muted">Keine</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  {% endif %}

  <p class="muted">Tipp: Für Serienbriefe den Mail-Merge-Export verwenden.</p>
</body>
</html>
"""

_OFFLINE_FORM_HTML = r"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Offline Formular</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"; margin: 1.5rem; }
    h1 { margin: 0 0 1rem 0; font-size: 1.5rem; }
  </style>
</head>
<body>
  <h1>Offline Formular</h1>
  <p>Hier könnten druckbare Formulare bereitgestellt werden.</p>
</body>
</html>
"""

_MAIL_MERGE_HELP_HTML = r"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Mail-Merge Hilfe</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"; margin: 1.5rem; }
    h1 { margin: 0 0 1rem 0; font-size: 1.5rem; }
  </style>
</head>
<body>
  <h1>Mail-Merge Hilfe</h1>
  <p>Den CSV-Export „mail_merge.csv“ in das Serienbrief-Tool importieren.</p>
</body>
</html>
"""

# Platzhalter für PDF-Bytes des Offline-Formulars
_OFFLINE_FORM_PDF = b"%PDF-1.4\n% ... Dummy PDF ..."  # hier ggf. echtes PDF einbinden

if __name__ == "__main__":
    # Nur für lokalen Testbetrieb
    app.run(host="0.0.0.0", port=5000, debug=True)
