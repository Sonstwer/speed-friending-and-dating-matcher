import argparse
from .runner import run_multi

def build_parser():
    p = argparse.ArgumentParser("speed-friending-matcher")
    p.add_argument("--input", required=True, help="Input CSV (participants)")
    p.add_argument("--output", required=True, help="Output file base name (e.g. results.csv)")
    p.add_argument("--matchmaker", default="simple", choices=["simple", "clique"])
    p.add_argument(
        "--interested-columns",
        default="Interested",
        help="Comma-separated like-columns to evaluate (e.g. InterestedDating,InterestedFriendship).",
    )
    p.add_argument(
        "--labels",
        default=None,
        help="Optional comma-separated labels for outputs (same count as interested-columns), e.g. dating,friendship.",
    )
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cols = [c.strip() for c in args.interested_columns.split(",") if c.strip()]
    labels = [l.strip() for l in args.labels.split(",")] if args.labels else cols
    if len(labels) != len(cols):
        parser.error("--labels must have the same number of items as --interested-columns")
    run_multi(args, interested_cols=cols, labels=labels)
