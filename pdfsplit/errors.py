"""Exceptions raised by the planning engine (§11)."""


class NoOutlineError(Exception):
    """Raised when a source PDF has no usable bookmark outline (§5.1).

    Outline-less books are out of scope by design — the tool fails loudly rather
    than guessing chapter boundaries from heuristics.
    """


class LevelNotFoundError(Exception):
    """Raised when the requested ``--level`` has no entries (§5.3).

    The message names each available level with its entry count so the caller can
    pick a valid one.
    """

    def __init__(self, requested: int, level_counts: dict[int, int]):
        self.requested = requested
        self.level_counts = dict(level_counts)
        available = ", ".join(
            f"level {lvl} ({cnt} entries)" for lvl, cnt in sorted(level_counts.items())
        )
        if available:
            msg = f"level {requested} not found; available: {available}"
        else:
            msg = f"level {requested} not found; no levels available"
        super().__init__(msg)
