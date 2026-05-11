from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
import json
from pathlib import Path

from database import get_latest_reading, get_daily_forecasts
from templates_env import templates

_CROP_SEASON_DAYS: dict[str, int] = {
    "barley": 240, "wheat": 230, "rapeseed": 250, "fababean": 200,
    "cotton": 180, "sugarbeet": 180, "tobacco": 150, "rice": 150,
    "sweetpotato": 150, "pigeonpea": 180, "soybean": 140, "maize": 130,
    "groundnut": 130, "cassava": 360, "potato": 100, "sunflower": 120,
    "sorghum": 120, "millet": 100, "chickpea": 120, "cowpea": 100,
    "mungbean": 90, "seed_onion": 180,
}

router = APIRouter()

_TE_PATH = Path("decision_engine/profiles/crop_te_map.json")
_PROFILE_PATH = Path("decision_engine/profiles/crop_daily_moisture_profile.json")

def _crop_list() -> list[str]:
    with open(_TE_PATH) as f:
        raw = json.load(f)
    return sorted(k for k in raw if not k.startswith("__"))


@router.get("/forecast", response_class=HTMLResponse)
async def forecast_page(request: Request, field_id: str | None = None):
    fields = request.app.state.config["fields"]
    fid = field_id or fields[0]["id"]
    field_cfg = next((f for f in fields if f["id"] == fid), fields[0])
    latest = get_latest_reading(fid) or {}

    # Daily forecast history — one series per horizon
    daily_30 = get_daily_forecasts(fid, horizon_days=30, n=60)
    daily_60 = get_daily_forecasts(fid, horizon_days=60, n=60)
    daily_90 = get_daily_forecasts(fid, horizon_days=90, n=60)

    # Align dates across horizons for the yield chart
    all_dates = sorted({r["date"] for r in daily_30 + daily_60 + daily_90})
    def _series(rows):
        by_date = {r["date"]: r["yield_forecast_kg_ha"] for r in rows}
        return [by_date.get(d) for d in all_dates]

    chart_dates = all_dates
    chart_day30 = _series(daily_30)
    chart_day60 = _series(daily_60)
    chart_day90 = _series(daily_90)

    # Satellite indices chart — one value per date from horizon=30 rows
    s2_by_date = {r["date"]: r for r in daily_30}
    chart_ndvi = [s2_by_date[d].get("ndvi") for d in all_dates]
    chart_ndwi = [s2_by_date[d].get("ndwi") for d in all_dates]
    chart_ndre = [s2_by_date[d].get("ndre") for d in all_dates]
    has_s2 = any(v is not None for v in chart_ndvi)

    # Build table rows: one row per date (last 14 dates, newest first)
    by_date: dict[str, dict] = {}
    for rows, h in [(daily_30, 30), (daily_60, 60), (daily_90, 90)]:
        for r in rows:
            d = r["date"]
            if d not in by_date:
                by_date[d] = {"date": d, 30: None, 60: None, 90: None,
                              "iot": None, "lai_source": None, "openmeteo": None}
            by_date[d][h] = r["yield_forecast_kg_ha"]
            # Use day30 record as representative for metadata columns
            if h == 30:
                by_date[d]["iot"] = r["iot_readings"]
                by_date[d]["lai_source"] = r["lai_source"]
                by_date[d]["openmeteo"] = bool(r["openmeteo_used"])

    table_rows = [by_date[d] for d in sorted(by_date.keys(), reverse=True)[:14]]

    # Latest values badges
    latest_by_horizon = {h: by_date[max(by_date)][h] for h in (30, 60, 90) if by_date and by_date[max(by_date)][h] is not None} if by_date else {}

    return templates.TemplateResponse(request, "forecast.html", {
        "active_page": "forecast",
        "fields": fields,
        "field_id": fid,
        "crops": _crop_list(),
        "field_cfg": field_cfg,
        "latest": latest,
        "chart_dates": chart_dates,
        "chart_day30": chart_day30,
        "chart_day60": chart_day60,
        "chart_day90": chart_day90,
        "chart_ndvi": chart_ndvi,
        "chart_ndwi": chart_ndwi,
        "chart_ndre": chart_ndre,
        "table_rows": table_rows,
        "latest_by_horizon": latest_by_horizon,
        "has_daily": bool(all_dates),
        "has_s2": has_s2,
    })


@router.get("/forecast/quick", response_class=HTMLResponse)
async def quick_forecast_page(request: Request, field_id: str | None = None):
    fields = request.app.state.config["fields"]
    fid = field_id or fields[0]["id"]
    return templates.TemplateResponse(request, "quick_forecast.html", {
        "active_page": "quick_forecast",
        "fields": fields,
        "field_id": fid,
        "crops": _crop_list(),
        "today": date.today().isoformat(),
    })


@router.post("/forecast/quick/run", response_class=HTMLResponse)
async def run_quick_forecast(
    request: Request,
    latitude:     float  = Form(...),
    longitude:    float  = Form(...),
    crop:         str    = Form(...),
    planting_date: str   = Form(...),
    area_ha:      float  = Form(...),
    elevation:    float  = Form(None),
):
    from decision_engine import yield_forecast
    from services.openmeteo import fetch_season
    from services.sentinel_lai import fetch_season_indices

    plant_date = date.fromisoformat(planting_date)
    today      = datetime.now(timezone.utc).date()
    season_days = (today - plant_date).days
    today_doy   = today.timetuple().tm_yday  # day of year — matches daily_inference

    error = None
    weather, s2_records = {}, []
    try:
        weather = fetch_season(latitude, longitude, plant_date, today)
    except Exception as e:
        error = f"OpenMeteo hatası: {e}"

    try:
        s2_records = fetch_season_indices(latitude, longitude, plant_date, today)
    except Exception as e:
        error = (error or "") + f" Sentinel-2 hatası: {e}"

    elev = elevation or weather.get("elevation") or 0.0

    with open(_TE_PATH) as f:
        raw = json.load(f)
    wav_default = 100.0

    from datetime import timedelta
    season_total    = _CROP_SEASON_DAYS.get(crop, 200)
    days_to_harvest = max(0, season_total - season_days)
    harvest_pct     = min(100, round(season_days / season_total * 100))
    harvest_date    = (plant_date + timedelta(days=season_total)).isoformat()

    max_lai   = max((r["lai"] for r in s2_records), default=None)
    latest_s2 = s2_records[-1] if s2_records else {}

    base_features = {
        "mean_temp":          weather.get("mean_temp") or 18.0,
        "total_precip":       weather.get("total_precip") or 0.0,
        "mean_humidity":      weather.get("mean_humidity") or 60.0,
        "mean_soil_temp":     weather.get("mean_soil_temp") or 16.0,
        "mean_soil_moisture": weather.get("mean_soil_moisture") or 0.25,
        "max_lai":            max_lai or latest_s2.get("lai") or 2.0,
        "latitude":           latitude,
        "longitude":          longitude,
        "elevation":          elev,
        "year":               float(today.year),
        "WAV":                wav_default,
    }

    forecasts = {}
    for h in (30, 60, 90):
        adjusted_doy = today_doy + (days_to_harvest - h)
        features = {**base_features, "season_days": float(max(1, adjusted_doy))}
        try:
            kg_ha = yield_forecast(features, crop, horizon_days=h)
            forecasts[h] = {"kg_ha": round(kg_ha), "total_kg": round(kg_ha * area_ha)}
        except Exception as e:
            forecasts[h] = {"error": str(e)}

    # En uygun horizon: hasata kalan güne en yakın model
    diffs = {h: abs(days_to_harvest - h) for h in (30, 60, 90)}
    recommended_horizon = min(diffs, key=diffs.get)

    chart_dates   = [r["date"] for r in s2_records]
    chart_lai     = [r["lai"]  for r in s2_records]
    chart_ndvi    = [r["ndvi"] for r in s2_records]
    weather_dates = [r["date"]          for r in weather.get("daily", [])]
    weather_temp  = [r["temperature"]   for r in weather.get("daily", [])]
    weather_precip= [r["precipitation"] for r in weather.get("daily", [])]

    return templates.TemplateResponse(request, "partials/quick_result.html", {
        "crop": crop, "area_ha": area_ha,
        "latitude": latitude, "longitude": longitude,
        "planting_date": planting_date, "season_days": season_days,
        "days_to_harvest": days_to_harvest, "harvest_pct": harvest_pct,
        "harvest_date": harvest_date, "recommended_horizon": recommended_horizon,
        "forecasts": forecasts,
        "features": features,
        "latest_s2": latest_s2,
        "s2_scene_count": len(s2_records),
        "chart_dates": chart_dates, "chart_lai": chart_lai, "chart_ndvi": chart_ndvi,
        "weather_dates": weather_dates,
        "weather_temp": weather_temp, "weather_precip": weather_precip,
        "error": error,
    })


@router.post("/forecast/run", response_class=HTMLResponse)
async def run_forecast(
    request: Request,
    field_id: str = Form(...),
    crop: str = Form(...),
    horizon_days: int = Form(30),
    mean_temp: float = Form(...),
    total_precip: float = Form(...),
    mean_humidity: float = Form(...),
    mean_soil_temp: float = Form(...),
    mean_soil_moisture: float = Form(...),
    max_lai: float = Form(...),
    season_days: int = Form(...),
    year: float = Form(...),
    wav: float = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    elevation: float = Form(...),
):
    from decision_engine import yield_forecast

    features = {
        "mean_temp": mean_temp,
        "total_precip": total_precip,
        "mean_humidity": mean_humidity,
        "mean_soil_temp": mean_soil_temp,
        "mean_soil_moisture": mean_soil_moisture,
        "max_lai": max_lai,
        "season_days": float(season_days),
        "latitude": latitude,
        "longitude": longitude,
        "elevation": elevation,
        "year": year,
        "WAV": wav,
    }

    try:
        predicted = yield_forecast(features, crop, horizon_days=horizon_days)
        error = None
    except Exception as e:
        predicted = None
        error = str(e)

    # Reference yields from moisture profile
    with open(_PROFILE_PATH) as f:
        profile = json.load(f)
    ref = profile.get(crop, {})
    drought  = ref.get("yield_drought_kg_ha", 0)
    typical  = ref.get("yield_typical_kg_ha", 0)
    optimal  = ref.get("yield_optimal_kg_ha", 0)

    return templates.TemplateResponse(request, "partials/forecast_result.html", {
        "predicted": predicted,
        "error": error,
        "crop": crop,
        "horizon_days": horizon_days,
        "field_id": field_id,
        "drought": drought,
        "typical": typical,
        "optimal": optimal,
    })
