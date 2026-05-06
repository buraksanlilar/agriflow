from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
import json
from pathlib import Path
from datetime import datetime

from templates_env import templates

router = APIRouter()

_PROFILE_PATH = Path("decision_engine/profiles/crop_daily_moisture_profile.json")
_TE_PATH = Path("decision_engine/profiles/crop_te_map.json")


def _crop_list():
    with open(_TE_PATH) as f:
        raw = json.load(f)
    return sorted(k for k in raw if not k.startswith("__"))


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, crop: str = "wheat", field_id: str | None = None):
    fields = request.app.state.config["fields"]
    fid = field_id or fields[0]["id"]

    with open(_PROFILE_PATH) as f:
        profile_data = json.load(f)

    cp = profile_data.get(crop, {})
    ref = {
        "optimal": cp.get("yield_optimal_kg_ha", "—"),
        "typical": cp.get("yield_typical_kg_ha", "—"),
        "drought": cp.get("yield_drought_kg_ha", "—"),
    }

    return templates.TemplateResponse(request, "profile.html", {
        "active_page": "profile",
        "fields": fields,
        "field_id": fid,
        "crops": _crop_list(),
        "selected_crop": crop,
        "ref": ref,
        "today_doy": datetime.now().timetuple().tm_yday,
    })


@router.get("/api/profile/{crop}")
async def profile_data(crop: str):
    with open(_PROFILE_PATH) as f:
        profile = json.load(f)
    cp = profile.get(crop)
    if cp is None:
        return JSONResponse({"error": "unknown crop"}, status_code=404)
    return JSONResponse({
        "days":        cp["days"],
        "critical_sm": cp["critical_sm"],
        "low_sm":      cp["low_sm"],
        "optimal_sm":  cp["optimal_sm"],
        "high_sm":     cp["high_sm"],
        "upper_sm":    cp["upper_sm"],
        "max_day":     cp["max_day"],
    })
