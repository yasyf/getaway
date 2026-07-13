import datetime as dt
from collections.abc import Callable
from pathlib import Path

import pytest

from getaway import prefs, trips
from getaway.paths import UsageError

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def make(plan: dict) -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        {
            "cabin": "business",
            "window": {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10},
            "plan": {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}], **plan},
        },
    )
    return SLUG


def node_ids(graph: dict) -> list[str]:
    return [n["id"] for n in graph["nodes"]]


def node(graph: dict, node_id: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == node_id)


def test_compile_requires_trip_type(getaway_home: Path) -> None:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, {"plan": {"origins": ["SFO"]}})
    with pytest.raises(UsageError, match="trip_type"):
        trips.compile_graph(SLUG)


def test_one_way_compiles_no_return_or_stays_nodes(getaway_home: Path) -> None:
    graph = trips.compile_graph(make({"trip_type": "one_way"}))
    ids = node_ids(graph)
    assert "sweep:outbound:asia" in ids
    assert "shortlist:outbound" in ids
    assert not any(i.startswith("sweep:return") or i == "shortlist:return" for i in ids)
    assert "stays" not in ids
    assert graph["requires"] == []


def test_round_trip_adds_return_leg_with_lazy_endpoints(getaway_home: Path) -> None:
    graph = trips.compile_graph(make({"trip_type": "round_trip"}))
    assert "sweep:return" in node_ids(graph)
    assert "shortlist:return" in node_ids(graph)
    ret = node(graph, "sweep:return")
    assert ret["inputs"] == ["legs/outbound/shortlist.json"]  # endpoints resolve lazily
    assert ret["endpoint_source"]["from"] == "legs/outbound/shortlist.json"
    assert ret["outputs"] == ["legs/return/sweep.json"]


def test_lodging_adds_stays_node_and_requires_session(getaway_home: Path) -> None:
    graph = trips.compile_graph(make({"trip_type": "round_trip", "lodging": {}}))
    assert graph["requires"] == ["rooms_session"]
    stays = node(graph, "stays")
    assert stays["requires"] == ["rooms_session"]
    assert stays["outputs"] == ["stays.json"]


def test_open_jaw_return_override_rides_the_node(getaway_home: Path) -> None:
    graph = trips.compile_graph(
        make({"trip_type": "round_trip", "return": {"origins": ["KIX"], "dests": ["SFO"]}})
    )
    ret = node(graph, "sweep:return")
    assert ret["endpoint_source"]["override"] == {"origins": ["KIX"], "dests": ["SFO"]}


def test_hybrid_absent_compiles_no_hybrid_nodes(getaway_home: Path) -> None:
    graph = trips.compile_graph(make({"trip_type": "one_way"}))
    assert not any("gateway" in i or i in ("onward", "bridge") for i in node_ids(graph))


def test_hybrid_present_compiles_gateway_onward_bridge(getaway_home: Path) -> None:
    graph = trips.compile_graph(
        make(
            {
                "trip_type": "one_way",
                "hybrid": {"gateways": ["NRT"], "onward_dests": ["OKA"], "max_hybrids": 3},
            }
        )
    )
    for expected in ("shortlist:outbound:gateway", "sweep:outbound:onward", "onward", "bridge"):
        assert expected in node_ids(graph)


def test_compile_is_pure_over_a_session_free_trip(getaway_home: Path) -> None:
    # No artifacts, no checkpoints exist; compile still derives the full graph from the plan alone.
    graph = trips.compile_graph(make({"trip_type": "round_trip", "lodging": {}}))
    assert graph["nodes"]  # never inspected the (absent) rooms session
    assert graph["requires"] == ["rooms_session"]


def test_quota_budget_orders_core_legs_before_expand(getaway_home: Path) -> None:
    graph = trips.compile_graph(make({"trip_type": "round_trip"}))
    budget = graph["quota_budget"]
    ids = [n["id"] for n in budget["nodes"]]
    assert ids.index("sweep:outbound:asia") < ids.index("sweep:return") < ids.index("expand")
    assert budget["total"] == sum(n["quota_cost"] for n in budget["nodes"])


def test_runnable_nodes_carry_argv_command(getaway_home: Path) -> None:
    graph = trips.compile_graph(make({"trip_type": "round_trip"}))
    assert node(graph, "sweep:outbound:asia")["command"] == [
        "getaway",
        "sweep",
        "run",
        SLUG,
        "outbound:asia",
    ]
    assert node(graph, "sweep:return")["command"] == ["getaway", "sweep", "run", SLUG, "return"]
    assert node(graph, "assess")["command"] is None  # agent/fan-out nodes have no single command


def test_node_routing_runners_are_sonnet_research_is_opus(getaway_home: Path) -> None:
    graph = trips.compile_graph(make({"trip_type": "round_trip"}))
    assert node(graph, "sweep:outbound:asia")["routing"] == {"model": "sonnet", "effort": "low"}
    assert node(graph, "assess")["routing"] == {"model": "opus", "effort": "xhigh"}


def test_explain_flags_node_freshness(getaway_home: Path) -> None:
    slug = make({"trip_type": "one_way"})
    graph = trips.explain(slug, now=clock())
    assert all(n["fresh"] is False for n in graph["nodes"])  # nothing has run yet


def test_checkpoints_key_by_node_id(getaway_home: Path) -> None:
    slug = make({"trip_type": "one_way"})
    trips.phase_done(slug, "shortlist:outbound", now=clock())
    assert trips.phase_fresh(slug, "shortlist:outbound", now=clock())
    with pytest.raises(UsageError, match="unknown node id"):
        trips.phase_done(slug, "sweep:return", now=clock())  # no return leg on a one-way plan
