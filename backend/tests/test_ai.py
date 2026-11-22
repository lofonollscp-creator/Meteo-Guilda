import httpx
import pytest

from meteoguilda import ai, openmeteo


def fake_openmeteo():
    t = [f"2026-11-22T{h:02d}:00" for h in range(24)]
    h = {"time": t}
    for m, off in [("ecmwf_ifs025", 0), ("icon_seamless", 1), ("gfs_seamless", 0.5)]:
        h[f"temperature_2m_{m}"] = [10 + off] * 24
        h[f"precipitation_{m}"] = [0.1] * 24
        h[f"wind_speed_10m_{m}"] = [5 + 4 * off] * 24
        h[f"wind_gusts_10m_{m}"] = [9 + 4 * off] * 24
        h[f"cloud_cover_{m}"] = [50] * 24
        h[f"freezing_level_height_{m}"] = [2500] * 24
    return {"latitude": 43, "longitude": -5, "elevation": 800, "hourly": h}


def test_summarize_confidence():
    from datetime import datetime, timezone
    s = openmeteo.summarize(fake_openmeteo(), now=datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc))
    st = s["steps"][0]
    assert st["temperature_2m"]["mean"] == 10.5
    assert st["wind_speed_10m"]["max"] - st["wind_speed_10m"]["min"] == 4  # spread 4 -> media
    assert st["confidence"] == "media"
    assert st["precipitation"]["mean"] == 0.3  # suma 3 h


def test_numbers_validation():
    ctx = ai.build_context(openmeteo.summarize(fake_openmeteo()), [])
    ok = ai.AIForecast(resumen="Hará unos 10 °C con rachas de 40 km/h.", confianza="media")
    assert ai.check(ok, ctx) == []
    bad = ai.AIForecast(resumen="Habrá 35 °C y rachas de 120 km/h.", confianza="alta")
    assert len(ai.check(bad, ctx)) == 2


def test_rules_fallback():
    ctx = ai.build_context(openmeteo.summarize(fake_openmeteo()), [])
    fc = ai.rules_forecast(ctx)
    assert fc.confianza in {"alta", "media", "baja"} and "°C" in fc.resumen


async def test_generate_falls_back_between_models(monkeypatch):
    ctx = ai.build_context(openmeteo.summarize(fake_openmeteo()), [])
    monkeypatch.setenv("MG_MODELS", "a:free,b:free,c:free")
    good = '{"resumen":"Día estable, unos 10 °C.","confianza":"media"}'
    hallu = '{"resumen":"Habrá 40 °C de máxima.","confianza":"alta"}'

    def handler(req: httpx.Request):
        import json
        model = json.loads(req.content)["model"]
        if model == "a:free":
            return httpx.Response(429)
        content = hallu if model == "b:free" else good
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        res = await ai.generate(ctx, c, api_key="k")
    assert res and res[1] == "c:free"


def test_summarize_skips_past_hours():
    from datetime import datetime, timezone
    d = fake_openmeteo()
    d["utc_offset_seconds"] = 3600
    # 10:30 UTC = 11:30 local -> primer tramo empieza a las 11:00
    s = openmeteo.summarize(d, now=datetime(2026, 11, 22, 10, 30, tzinfo=timezone.utc))
    assert s["steps"][0]["time"] == "2026-11-22T11:00"
