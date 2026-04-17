"""Watchlist emit — writes ranked top-N to disk in JSON + markdown."""

from signal_platform.emit.watchlist import WatchlistPayload, emit_watchlist

__all__ = ["WatchlistPayload", "emit_watchlist"]
