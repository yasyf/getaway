CABIN_PREFIX = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}


def cabin_rank(cabin: str) -> int:
    return tuple(CABIN_PREFIX.values()).index(cabin)

CONTINENTS = (
    "North America",
    "South America",
    "Africa",
    "Asia",
    "Europe",
    "Oceania",
)

PRODUCT_VERDICTS = ("suite", "solid", "dated", "barely", "verify")

MILEAGE_BAND = 0.15
DEFAULT_QUOTA_FLOOR = 100

# Presentation (A5): the ranked cut applies only after assess; assess additionally
# surfaces up to NOTABLE_PREFERENCE_STRETCH_LIMIT excellent journeys from beyond it.
PRESENTATION_LIMIT = 6
# Keep in sync with skills/getaway/plan-trip.js's assess prompt literal "up to 2".
NOTABLE_PREFERENCE_STRETCH_LIMIT = 2

# --- getaway v2 journey engine (Phase 2: plan model, compile graph, retrieval) ---

# Retrieval policy (A3): soft-date sweeps pad; expansion runs under budgets, not caps.
SOFT_DATE_SEARCH_PADDING_DAYS = 7
DATE_WIDEN_STEP_DAYS = 7
AUTO_WIDEN_CALL_BUDGET_PER_LEG = 2
SWEEP_PAGE_BUDGET = 3
SEARCH_PAGE_SIZE = 1000
# Completeness states that cut a fresh sweep generation, vs. refresh the current one.
GENERATION_CUTTING_COMPLETENESS = frozenset({"complete", "searched_empty"})
EXPANSION_BUDGET_PER_ENDPOINT = 12
RETURN_EXPANSION_BUDGET_PER_ENDPOINT = 12
# Chain-builder beam: candidate chains cheap-ranked on (miles, cash) are capped here BEFORE any
# /trips expansion spends quota — only survivors expand. Overridable via plan.tuning.beam_width.
COMPOSE_BEAM_WIDTH = 64

# Per-trip search-width knobs; an absent key falls to its constant (the single default source).
# Trip-scoped judgment, never a prefs key. Ranking constants (MILEAGE_BAND, notable/widen) fixed.
TUNING_DEFAULTS = {
    "presentation_limit": PRESENTATION_LIMIT,
    "expansion_budget_per_endpoint": EXPANSION_BUDGET_PER_ENDPOINT,
    "beam_width": COMPOSE_BEAM_WIDTH,
    "sweep_page_budget": SWEEP_PAGE_BUDGET,
    "date_padding_days": SOFT_DATE_SEARCH_PADDING_DAYS,
}
TUNING_KEYS = frozenset(TUNING_DEFAULTS)


def tuned(plan: dict, key: str) -> int:
    """A trip's effective value for a knob: the ``plan.tuning`` override, else the default."""
    return plan.get("tuning", {}).get(key, TUNING_DEFAULTS[key])

# Disjoint stores: prefs.py consumes DISJOINT_TRIP_DOC_KEYS to reject trip-doc keys.
DISJOINT_DURABLE_PREF_KEYS = frozenset(
    {
        "op_ref",
        "awardwallet_op_ref",
        "serpapi_op_ref",
        "home_airport",
        "origin_airports",
        "avoid_transit",
        "avoid_destinations",
        "avoid_airlines",
        "layovers",
        "statuses",
        "status_goals",
        "balances",
        "credits",
        "documents",
    }
)
DISJOINT_TRIP_DOC_KEYS = frozenset(
    {
        "window",
        "cabin",
        "party",
        "regions",
        "vibe",
        "avoid_final_destinations",
        "plan",
        "judgment",
    }
)

# Model routing per node kind (C2): mechanical runners → sonnet low; research → opus xhigh.
ROUTING_RUNNER = {"model": "sonnet", "effort": "low"}
ROUTING_RESEARCH = {"model": "opus", "effort": "xhigh"}
NODE_ROUTING = {
    "sweep": ROUTING_RUNNER,
    "shortlist": ROUTING_RUNNER,
    "expand": ROUTING_RUNNER,
    "rank": ROUTING_RUNNER,
    "finalize": ROUTING_RUNNER,
    "onward": ROUTING_RUNNER,
    # bridge prices onward cash legs deterministically via fli (getaway bridge) — a
    # mechanical runner, not an agent; cabin choice per leg is judgment fed by fit facts.
    "bridge": ROUTING_RUNNER,
    "evidence": ROUTING_RESEARCH,
    "assess": ROUTING_RESEARCH,
    "stays": ROUTING_RESEARCH,
    # scout proposes a discover leg's hub airports; research judgment, zero seats.aero quota.
    "scout": ROUTING_RESEARCH,
}

# Freshness TTL (hours) per node kind; a kind absent here never expires by time.
NODE_TTL_HOURS = {
    "sweep": 24,
    "bridge": 24,
    "expand": 6,
}

EXIT_OK = 0
EXIT_NEGATIVE = 1
EXIT_AUTH = 2
EXIT_STATE = 3
EXIT_NO_DATA = 4
EXIT_USAGE = 64
