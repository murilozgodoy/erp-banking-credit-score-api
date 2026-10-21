import pytest

from app.factories.score_strategy_factory import (
    ScoreStrategyFactory,
    UnknownStrategyError,
)
from app.strategies.simple_rule_strategy import SimpleRuleStrategy
from app.strategies.weighted_strategy import WeightedStrategy


def test_create_default_returns_weighted():
    s = ScoreStrategyFactory.create()
    assert isinstance(s, WeightedStrategy)


def test_create_simple():
    s = ScoreStrategyFactory.create("simple")
    assert isinstance(s, SimpleRuleStrategy)


def test_create_weighted_case_insensitive():
    s = ScoreStrategyFactory.create("WEIGHTED")
    assert isinstance(s, WeightedStrategy)


def test_unknown_strategy_raises():
    with pytest.raises(UnknownStrategyError):
        ScoreStrategyFactory.create("naoexiste")


def test_available_returns_registered_strategies():
    names = ScoreStrategyFactory.available()
    assert "simple" in names
    assert "weighted" in names


def test_describe_lists_strategies_with_default():
    described = {d["name"]: d for d in ScoreStrategyFactory.describe()}
    assert set(described) == {"simple", "weighted"}
    assert described["weighted"]["default"] is True
    assert described["simple"]["default"] is False
    assert described["simple"]["description"] == SimpleRuleStrategy.description
