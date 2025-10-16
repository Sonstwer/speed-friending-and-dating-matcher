import csv

def parse_id_list(value: str):
    if not value:
        return set()
    value = value.replace(",", ";")
    out = set()
    for x in value.split(";"):
        x = x.strip()
        if x.isdigit():
            out.add(int(x))
    return out


def load_participants(path, interested_cols=("Interested",)):
    with open(path, newline="", encoding="utf-8") as f:
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
            "all": parse_id_list(r.get("All", "")),
        }
        entry["interested_by_col"] = {}
        for col in interested_cols:
            entry["interested_by_col"][col] = parse_id_list(r.get(col, ""))
        # fallback für alte CSVs
        if "Interested" in r and "Interested" not in interested_cols:
            entry["interested_by_col"]["Interested"] = parse_id_list(r.get("Interested", ""))
        people[pid] = entry
    return people
