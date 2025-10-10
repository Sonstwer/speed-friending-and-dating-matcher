def compute_matches_for_column(people: dict, column: str):
    """Return list of (a,b) where a<b and both liked each other in given like-column."""
    likes = {pid: people[pid]["interested_by_col"].get(column, set()) for pid in people}
    matches = []
    for a, targets in likes.items():
        for b in targets:
            if b in likes and a in likes[b] and a < b:
                pa, pb = people[a], people[b]
                if pa["all"] and b not in pa["all"]:
                    continue
                if pb["all"] and a not in pb["all"]:
                    continue
                matches.append((a, b))
    return matches
