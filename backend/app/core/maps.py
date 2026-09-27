"""Links to a location in Google Maps."""

from urllib.parse import quote_plus

SEARCH_URL = "https://www.google.com/maps/search/?api=1&query="


def google_maps_url(name: str, address: str | None, map_url: str | None) -> str | None:
    """Where a location opens in Google Maps: its own link if it has one, else
    a search for its name and address (without an address there is nothing
    useful to search for)."""
    if map_url:
        return map_url
    if not address:
        return None
    return SEARCH_URL + quote_plus(f"{name}, {address}")
