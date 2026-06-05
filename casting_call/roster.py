import json
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class Roster:
    self_name: str
    members: list  # list of {'canonical': str, 'aliases': list[str]}


def load_roster(path) -> Roster:
    with open(path) as f:
        data = json.load(f)
    return Roster(self_name=data['self'], members=data.get('members', []))


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def match_name(raw: str, roster: Roster, threshold: float) -> str | None:
    """Return the canonical roster name for an OCR'd label, or None.

    'You' (any case) maps to the roster self name. Otherwise fuzzy-match against
    each member's canonical name and aliases; best canonical above threshold wins.
    """
    cleaned = raw.strip()
    if cleaned.lower() == 'you':
        return roster.self_name

    best_name = None
    best_score = 0.0
    for member in roster.members:
        candidates = [member['canonical'], *member.get('aliases', [])]
        score = max(_ratio(cleaned, c) for c in candidates)
        if score > best_score:
            best_score = score
            best_name = member['canonical']

    return best_name if best_score >= threshold else None
