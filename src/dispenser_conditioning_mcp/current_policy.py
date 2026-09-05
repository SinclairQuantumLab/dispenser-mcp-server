"""Shared software and SPD3303X current bounds; not a physics model."""

MAX_CONFIGURABLE_LOAD_CURRENT_A = 6.4
SPD_NATIVE_CURRENT_MAX_A = 3.2
SPD_PARALLEL_CURRENT_MAX_A = 2 * SPD_NATIVE_CURRENT_MAX_A


def effective_load_current_limit(operator_limit: float) -> float:
    return min(
        operator_limit, MAX_CONFIGURABLE_LOAD_CURRENT_A, SPD_PARALLEL_CURRENT_MAX_A
    )
