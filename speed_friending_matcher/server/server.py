"""
speed_friending_matcher.server.server
------------------------------------
Flask-Webserver für den Matcher mit /api/match/dual (ZIP mit 2 CSVs).
Robust: enthält Fallbacks, falls CSVImporter oder compute_for_column fehlen.
"""

import io
import os
import csv
import zipfile
import tempfile
from io import StringIO

from flask import Flask, request, send_file, jsonify, render_template_string

# --- Versuche Projekt-Module zu importieren; fallback wenn nicht verfügbar ---
try:
    # Originaler Importer-Pfad deines Projekts
    from ..importer.csvimporter import CSVImporter as _ProjectCSVImporter
except Exception:
    _ProjectCSVImporter = None

try:
    from ..core.matching.simple_matchmaker import SimpleMatchmaker as _ProjectSimpleMatcher
except Exception:
    _ProjectSimpleMatcher = None


# ------------------------- Fallback-Importer -------------------------
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
    """Einfacher CSV-Importer: liest ID/Name/Email/Phone/All und beliebige Interested-Spalten."""
    def __init__(self, path: str):
        self.path = path

    def load(self, interested_cols=("Interested",)):
        import csv
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
            # Fallback für alte CSVs
            if "Interested" in r and "Interested" not in interested_cols:
                entry["interested_by_col"]["Interested"] = _parse_id_list(r.get("Interested", ""))
            people[pid] = entry
        return people

# Wähle Projekt-Importer, wenn vorhanden; sonst Fallback
CSVImporter = _ProjectCSVImporter or _FallbackCSVImporter


# ------------------------- Fallback-Matcher -------------------------
def _fallback_compute_for_column(people: dict, column: str):
    """Mutual-Like-Matching für eine Spalte."""
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

class _FallbackSimpleMatcher:
    def compute_for_column(self, people: dict, column: str):
        return _fallback_compute_for_column(people, column)

# Wähle Projekt-Matcher, wenn vorhanden; wenn die Methode fehlt, nutze Fallback
if _ProjectSimpleMatcher is not None:
    # Prüfe, ob compute_for_column existiert; wenn nicht, wrappe mit Fallback
    if hasattr(_ProjectSimpleMatcher, "compute_for_column"):
        SimpleMatcher = _ProjectSimpleMatcher
    else:
        class _WrappedSimpleMatcher(_ProjectSimpleMatcher):  # type: ignore
            def compute_for_column(self, people: dict, column: str):
                return _fallback_compute_for_column(people, column)
        SimpleMatcher = _WrappedSimpleMatcher
else:
    SimpleMatcher = _FallbackSimpleMatcher


# -----------------------------------------------------------------------------
# Flask App
# -----------------------------------------------------------------------------
app = Flask(__name__)

@app.route("/")
def index():
    """Einfache Startseite mit Formular für Datei-Upload."""
    html = """
    <h1>Speed Friending & Dating Matcher</h1>
    <form action="/api/match/dual" method="post" enctype="multipart/form-data">
      <p><label>CSV-Datei: <input type="file" name="file" required></label></p>
      <p><label>Interested Columns (z. B. InterestedDating,InterestedFriendship):<br>
         <input type="text" name="interested-columns"
                value="InterestedDating,InterestedFriendship" style="width:420px"></label></p>
      <p><label>Labels (z. B. dating,friendship):<br>
         <input type="text" name="labels"
                value="dating,friendship" style="width:420px"></label></p>
      <p><button type="submit">Auswerten & ZIP herunterladen</button></p>
    </form>
    <p><small>API: <code>POST /api/match/dual</code> (multipart/form-data)</small></p>
    """
    return render_template_string(html)


# -----------------------------------------------------------------------------
# Neuer API-Endpunkt: /api/match/dual
# -----------------------------------------------------------------------------
@app.route("/api/match/dual", methods=["POST"])
def api_match_dual():
    """
    Erwartet multipart/form-data:
      - file: CSV (Pflicht)
      - interested-columns: z. B. "InterestedDating,InterestedFriendship"
      - labels: optional, gleiche Anzahl wie interested-columns
    Gibt eine ZIP-Datei mit je einer matches_<label>.csv zurück.
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No CSV file uploaded (field name: 'file')"}), 400

    cols = [c.strip() for c in (request.form.get("interested-columns") or "Interested").split(",") if c.strip()]
    labels_raw = request.form.get("labels")
    labels = [l.strip() for l in labels_raw.split(",")] if labels_raw else cols
    if len(labels) != len(cols):
        return jsonify({"error": "labels must have same count as interested-columns"}), 400

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    try:
        f.save(tmp)
        tmp.flush()
        tmp_path = tmp.name
    finally:
        tmp.close()

    try:
        importer = CSVImporter(tmp_path)
        people = importer.load(interested_cols=tuple(cols))
        matcher = SimpleMatcher()

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for col, label in zip(cols, labels):
                matches = matcher.compute_for_column(people, column=col)

                s = StringIO()
                w = csv.writer(s)
                w.writerow(["A_ID", "A_Name", "A_Email", "B_ID", "B_Name", "B_Email"])
                for a, b in matches:
                    pa, pb = people[a], people[b]
                    w.writerow([a, pa.get("name",""), pa.get("email",""), b, pb.get("name",""), pb.get("email","")])

                zf.writestr(f"matches_{label}.csv", s.getvalue())

        zip_buf.seek(0)
        return send_file(
            zip_buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name="matches.zip",
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Debug=false, damit Gunicorn-ähnliches Verhalten
    app.run(host="0.0.0.0", port=5000, debug=False)
