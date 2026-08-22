"""Compatibility import for provider adapters; canonical logic is package-local."""

from app.datahub.canonical_ticker import SH_INDEX_CODES, normalise_ticker, vendor_symbol

__all__ = ["SH_INDEX_CODES", "normalise_ticker", "vendor_symbol"]
