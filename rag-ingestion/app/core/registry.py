import logging
from typing import Dict, Type, Any

logger = logging.getLogger(__name__)

class StrategyRegistry:
    """
    Singleton registry for ingestion strategies.
    Enables OCP by allowing dynamic registration without modifying the dispatcher.
    """
    _instance = None
    _strategies: Dict[str, Type[Any]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StrategyRegistry, cls).__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, strategy_key: str):
        """
        Decorator to register a strategy class.
        """
        def wrapper(strategy_class: Type[Any]):
            cls._strategies[strategy_key] = strategy_class
            logger.info(f"[Registry] Strategy registered: {strategy_key} -> {strategy_class.__name__}")
            return strategy_class
        return wrapper

    def get_strategy(self, strategy_key: str) -> Type[Any]:
        """
        Retrieves a strategy class by its key.
        """
        return self._strategies.get(strategy_key)

    def list_strategies(self):
        return list(self._strategies.keys())

# Singleton instance
registry = StrategyRegistry()

def register_strategy(strategy_key: str):
    return StrategyRegistry.register(strategy_key)
