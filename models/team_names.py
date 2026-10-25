"""Match team names between The Odds API and balldontlie, which don't always
agree on city prefixes (e.g. "LA Clippers" vs "Los Angeles Clippers")."""

from __future__ import annotations

_TWO_WORD_NICKNAMES = {"Trail Blazers"}


def nickname(full_name: str) -> str:
    for two_word in _TWO_WORD_NICKNAMES:
        if full_name.endswith(two_word):
            return two_word
    return full_name.split()[-1]


def build_nickname_index(full_names: list) -> dict:
    """{nickname: full_name} for a list of canonical (balldontlie) full names."""
    return {nickname(name): name for name in full_names}


def resolve(odds_api_name: str, nickname_index: dict) -> str | None:
    return nickname_index.get(nickname(odds_api_name))
