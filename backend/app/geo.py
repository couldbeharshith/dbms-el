"""Optional coarse location (GeoIP). Only called when the user turns on
`location_enabled` in Settings; the result is cached for 24h. Returns
(lon, lat) for ST_MakePoint, or None on any failure."""

import time

import httpx

from backend.app import config

_TTL_SEC = 24 * 3600
_cache: dict[str, object] = {"point": None, "ts": 0.0}


async def get_location() -> tuple[float, float] | None:
    now = time.time()
    if _cache["point"] is not None and now - _cache["ts"] < _TTL_SEC:
        return _cache["point"]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "http://ip-api.com/json/", params={"fields": "status,lat,lon"}
            )
            data = r.json()
        if data.get("status") != "success":
            return None
        point = (float(data["lon"]), float(data["lat"]))
    except Exception:
        return None
    _cache["point"], _cache["ts"] = point, now
    return point
