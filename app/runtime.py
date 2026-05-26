"""Per-process singletons for ruleset + Enricher.

Initialised at FastAPI lifespan startup; FastAPI dependencies expose
them to request handlers. The pattern mirrors `app.db._pool` — one
instance per process, accessed via lifecycle helpers.
"""

from pathlib import Path

from fastapi import Request

from app.config import Settings
from app.enrich import Enricher
from app.rules import RuleSet, load_rules

_RULES_YAML_PATH = Path(__file__).parent / "rules.yaml"


def init_runtime(settings: Settings) -> tuple[RuleSet, Enricher]:
    """Load + validate rules.yaml; construct the Enricher. Called once
    in main.py lifespan; the returned objects are stored on app.state."""
    ruleset = load_rules(_RULES_YAML_PATH)
    enricher = Enricher(data_dir=settings.enrichment_data_dir)
    return ruleset, enricher


def get_ruleset(request: Request) -> RuleSet:
    """FastAPI dependency: return the per-process ruleset."""
    ruleset: RuleSet = request.app.state.ruleset
    return ruleset


def get_enricher(request: Request) -> Enricher:
    """FastAPI dependency: return the per-process Enricher."""
    enricher: Enricher = request.app.state.enricher
    return enricher
