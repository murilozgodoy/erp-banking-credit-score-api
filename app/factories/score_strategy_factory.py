from app.strategies import ScoreStrategy, SimpleRuleStrategy, WeightedStrategy


class UnknownStrategyError(Exception):
    pass


class ScoreStrategyFactory:
    """Factory para as ScoreStrategy disponíveis."""

    _registry: dict[str, type[ScoreStrategy]] = {
        SimpleRuleStrategy.name: SimpleRuleStrategy,
        WeightedStrategy.name: WeightedStrategy,
    }

    DEFAULT = WeightedStrategy.name

    @classmethod
    def create(cls, name: str | None = None) -> ScoreStrategy:
        key = (name or cls.DEFAULT).lower()
        strategy_cls = cls._registry.get(key)
        if strategy_cls is None:
            raise UnknownStrategyError(f"Strategy '{name}' desconhecida")
        return strategy_cls()

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def describe(cls) -> list[dict]:
        return [
            {
                "name": name,
                "description": cls._registry[name].description,
                "default": name == cls.DEFAULT,
            }
            for name in cls.available()
        ]
