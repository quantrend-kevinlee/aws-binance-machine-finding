"""Champion management modules for DC Machine."""

from .state_manager import ChampionStateManager
from .evaluator import ChampionEvaluator
from .event_logger import ChampionEventLogger

__all__ = ['ChampionStateManager', 'ChampionEvaluator', 'ChampionEventLogger']