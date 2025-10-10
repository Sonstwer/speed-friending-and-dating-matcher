"""
speed_friending_matcher.server.server
------------------------------------
Startet den Flask-Webserver für Speed-Friending/Dating Matcher
und bietet einen API-Endpunkt zum Erzeugen von Matches aus CSV-Dateien.
"""

import io
import os
import csv
import zipfile
import tempfile

from io import StringIO
from flask import Flask, request, send_file, jsonify, render_template_string

from ..importer.csvimporter import CSVImporter
from ..core.matching.simple_matchmaker import SimpleMatchmaker


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
                value="InterestedDating,InterestedFriendship" style="width:400px"></label></p>
      <p><label>Labels (z. B. dating,friendship):<br>
         <input type="text" name="labels"
                value="dating,friendship" style="width:400px"></label></p>
      <p><button type="submit">Auswerten & ZIP herunterladen</button></p>
    </form>
    <p><small>API-Endpunkt: <code>/api/match/dual</code></small></p>
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

    cols = [
        c.strip()
        for c in (request.form.get("interested-columns") or "Interested").split(",")
        if c.strip()
    ]
    labels_raw = request.form.get("labels")
    labels = [l.strip() for l in labels_raw.split(",")] if labels_raw else cols
    if len(labels) != len(cols):
        return jsonify({"error": "labels must have same count as interested-columns"}), 400

    # Datei temporär speichern
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
        matcher = SimpleMatchmaker()

        # ZIP im Speicher erzeugen
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for col, label in zip(cols, labels):
                matches = matcher.compute_for_column(people, column=col)
                s = StringIO()
                w = csv.writer(s)
                w.writerow(["A_ID", "A_Name", "A_Email", "B_ID", "B_Name", "B_Email"])
                for a, b in matches:
                    pa, pb = people[a], people[b]
                    w.writerow(
                        [
                            a,
                            pa.get("name", ""),
                            pa.get("email", ""),
                            b,
                            pb.get("name", ""),
                            pb.get("email", ""),
                        ]
                    )
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
# Main entry point (for manual running)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
