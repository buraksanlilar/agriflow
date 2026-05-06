from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
import json
from pathlib import Path

from database import get_latest_reading
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

    return templates.TemplateResponse(request, "forecast.html", {
        "active_page": "forecast",
        "fields": fields,
        "field_id": fid,
        "crops": _crop_list(),
        "field_cfg": field_cfg,
        "latest": latest,
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
