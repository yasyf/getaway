import click

from getaway import registry


def classify(airline: str, aircraft: str, cabin: str = "business") -> dict:
    ac = aircraft.lower()
    candidates = [
        row
        for row in registry.seat_quality()
        if row["airline"].upper() == airline.upper() and row["cabin"] == cabin
    ]
    best = None
    for row in candidates:
        if row["aircraft_match"].lower() in ac and (
            best is None or len(row["aircraft_match"]) > len(best["aircraft_match"])
        ):
            best = row
    if best is None:
        return {"verdict": "verify", "product": None, "note": None, "matched": None}
    return {
        "verdict": best["verdict"],
        "product": best["product"],
        "note": best.get("note"),
        "matched": best["aircraft_match"],
    }


def list_rows(airline: str | None = None) -> list:
    rows = registry.seat_quality()
    if airline is None:
        return rows
    return [row for row in rows if row["airline"].upper() == airline.upper()]


quality_group = click.Group("quality", help="Business seat-quality classification.")


@quality_group.command("classify")
@click.option("--airline", required=True, help="Operating carrier IATA code.")
@click.option("--aircraft", required=True, help="Segment AircraftName string.")
@click.option("--cabin", default="business", show_default=True)
def _classify(airline: str, aircraft: str, cabin: str) -> None:
    registry.emit(classify(airline, aircraft, cabin))


@quality_group.command("list")
@click.option("--airline", help="Restrict to one carrier.")
def _list(airline: str | None) -> None:
    registry.emit(list_rows(airline))
