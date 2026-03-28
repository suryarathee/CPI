"""
patterns/base.py
----------------
Abstract base class for all OHLCV pattern strategies.

Every concrete pattern must inherit from BasePattern and implement the
`evaluate()` method, which receives rolling window lists and returns a
boolean indicating whether the pattern has fired.
"""

from abc import ABC, abstractmethod


class BasePattern(ABC):
    """
    Strategy interface for OHLCV chart-pattern detection.

    Attributes
    ----------
    name : str
        Human-readable identifier used as context when triggering the
        Gemini Multi-Agent Swarm.
    """

    def __init__(self, name: str):
        self.name: str = name

    @abstractmethod
    def evaluate(
        self,
        opens: list,
        highs: list,
        lows: list,
        closes: list,
        volumes: list,
    ) -> bool:
        """
        Evaluate whether the pattern is present in the supplied OHLCV data.

        Parameters
        ----------
        opens   : list  Rolling window of open prices  (newest last).
        highs   : list  Rolling window of high prices  (newest last).
        lows    : list  Rolling window of low prices   (newest last).
        closes  : list  Rolling window of close prices (newest last).
        volumes : list  Rolling window of volumes      (newest last).

        Returns
        -------
        bool
            True  → pattern detected, trigger downstream action.
            False → pattern not present, continue streaming.
        """
        ...
