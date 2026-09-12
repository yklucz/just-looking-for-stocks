"""Binary event probabilities imply LONG/FLAT, never a negative-return forecast."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class LongFlatStrategy:
    threshold: float = 0.5

    def __post_init__(self) -> None:
        if not math.isfinite(self.threshold) or not 0 <= self.threshold <= 1:
            raise ValueError("Invalid decision threshold")

    def generate_signal(self, probability: float) -> str:
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Probability must be finite and within [0, 1]")
        return "LONG" if probability >= self.threshold else "FLAT"
