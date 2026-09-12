"""Bar positions express sessions; no calendar arithmetic or same-close fills."""


def execution_positions(origin: int, holding_period: int) -> tuple[int, int]:
    return origin + 1, origin + 1 + holding_period
