from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
import json
from pathlib import Path

from database import get_latest_reading, get_daily_forecasts
from templates_env import templates

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

    # Align dates across horizons for the chart
    all_dates = sorted({r["date"] for r in daily_30 + daily_60 + daily_90})
    def _series(rows):
        by_date = {r["date"]: r["yield_forecast_kg_ha"] for r in rows}
        return [by_date.get(d) for d in all_dates]

    chart_dates   = all_dates
    chart_day30   = _series(daily_30)
    chart_day60   = _series(daily_60)
    chart_day90   = _series(daily_90)

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
        "table_rows": table_rows,
        "latest_by_horizon": latest_by_horizon,
        "has_daily": bool(all_dates),
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
