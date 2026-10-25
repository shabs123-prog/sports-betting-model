"""CLI entrypoint: python -m scripts.run_daily [sport]"""
import sys

from tips.generate_tips import run

if __name__ == "__main__":
    sport = sys.argv[1] if len(sys.argv) > 1 else "nba"
    run(sport=sport)
