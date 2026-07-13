CABIN_PREFIX = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}

CONTINENTS = (
    "North America",
    "South America",
    "Africa",
    "Asia",
    "Europe",
    "Oceania",
)

PRODUCT_VERDICTS = ("suite", "solid", "dated", "barely", "verify")

CASH_CUTOFF_MINUTES = 240
MILEAGE_BAND = 0.15
EXPANSION_BUFFER_FACTOR = 2
EXPANSION_BUFFER_CAP = 12
DEFAULT_QUOTA_FLOOR = 100

PHASE_TTL_HOURS = {
    "sweep": 24,
    "onward": 24,
    "bridge": 24,
    "expand": 6,
    "evidence.verify": 168,
    "evidence.cash": 24,
}

# Judgment factors that get their own Evidence-phase collector, keyed factor_id -> collector.
EVIDENCE_COLLECTORS = {
    "seat_quality": "verify",
    "cash_anomaly": "cash",
    "destination_context": "context",
    "transit_risk": "transit",
    "return_viability": "return",
}

EXIT_OK = 0
EXIT_NEGATIVE = 1
EXIT_AUTH = 2
EXIT_STATE = 3
EXIT_NO_DATA = 4
EXIT_USAGE = 64
