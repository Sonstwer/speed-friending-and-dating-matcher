from pathlib import Path
from .importers.csv_importer import load_participants
from .matchers.simple import compute_matches_for_column
from .exporters.csv_exporter import write_matches_csv


def run_multi(args, interested_cols, labels):
    """
    Run matching for each interested-column and write separate outputs:
    results_dating.csv, results_friendship.csv, ...
    """
    people = load_participants(args.input, interested_cols=tuple(interested_cols))
    base = Path(args.output)
    stem, suf = base.stem, base.suffix or ".csv"
    for col, label in zip(interested_cols, labels):
        if args.matchmaker == "simple":
            matches = compute_matches_for_column(people, col)
        else:
            raise NotImplementedError("clique not wired here")
        out = base.with_name(f"{stem}_{label}{suf}")
        write_matches_csv(out, matches, people)
