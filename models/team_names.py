"""Match team names between The Odds API and ESPN, which don't always agree
on naming (e.g. "Bournemouth" vs "AFC Bournemouth", "LA Clippers" vs
"Los Angeles Clippers")."""

from __future__ import annotations

_TWO_WORD_NICKNAMES = {"Trail Blazers"}


def nickname(full_name: str) -> str:
    for two_word in _TWO_WORD_NICKNAMES:
        if full_name.endswith(two_word):
            return two_word
    return full_name.split()[-1]


def build_nickname_index(full_names: list) -> dict:
    """
    {nickname: full_name}, but only for nicknames that uniquely identify one
    team. A nickname shared by multiple teams (e.g. three EPL clubs all
    ending in "United") is dropped rather than silently resolving to
    whichever one happened to be inserted last.
    """
    by_nickname: dict[str, list[str]] = {}
    for name in full_names:
        by_nickname.setdefault(nickname(name), []).append(name)
    return {nick: names[0] for nick, names in by_nickname.items() if len(names) == 1}


def resolve(odds_api_name: str, canonical_names, nickname_index: dict) -> str | None:
    """
    Exact (case-insensitive) match first -- most names already agree between
    data sources (18 of 20 EPL teams match exactly, for instance). Falls
    back to nickname matching only when that nickname is unambiguous.
    """
    lowered = odds_api_name.lower()
    for name in canonical_names:
        if name.lower() == lowered:
            return name
    return nickname_index.get(nickname(odds_api_name))
