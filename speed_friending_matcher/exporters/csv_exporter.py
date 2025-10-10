import csv

def write_matches_csv(path, matches, people):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["A_ID", "A_Name", "A_Email", "B_ID", "B_Name", "B_Email"])
        for a, b in matches:
            pa, pb = people[a], people[b]
            w.writerow([a, pa["name"], pa["email"], b, pb["name"], pb["email"]])
