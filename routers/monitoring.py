from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse

from database import get_latest_reading, get_sparkline, get_latest_decision
from templates_env import templates

router = APIRouter()

ALARM_DOT_COLOR = {
    "NORMAL":   "#767586",
    "IRRIGATE": "#904900",
    "CRITICAL": "#ba1a1a",
    "STOP":     "#4648d4",
}


def _sensor_ctx(field_id: str) -> dict:
    reading = get_latest_reading(field_id) or {}
    decision = get_latest_decision(field_id) or {}

    def sparkline(col):
        vals = get_sparkline(field_id, col)
        return vals if vals else [50.0] * 8

    return {
        "field_id":    field_id,
        "air_temp":    reading.get("air_temp"),
        "air_humidity":reading.get("air_humidity"),
        "soil_moisture":reading.get("soil_moisture"),
        "soil_temp":   reading.get("soil_temp"),
        "timestamp":   reading.get("timestamp", "—"),
        "alarm":       decision.get("alarm", "NORMAL"),
        "volume_litres": decision.get("volume_litres", 0.0),
        "pump_status": decision.get("pump_status", "OFF"),
        "last_decision_ts": decision.get("timestamp", "—"),
        "sparklines": {
            "air_temp":     sparkline("air_temp"),
            "air_humidity": sparkline("air_humidity"),
            "soil_moisture":sparkline("soil_moisture"),
            "soil_temp":    sparkline("soil_temp"),
        },
        "alarm_dot_color": ALARM_DOT_COLOR,
        "alarm_levels": ["NORMAL", "IRRIGATE", "CRITICAL", "STOP"],
    }


def _fields(request: Request) -> list[dict]:
    return request.app.state.config["fields"]


@router.get("/monitoring", response_class=HTMLResponse)
async def monitoring_page(request: Request, field_id: str | None = None):
    fields = _fields(request)
    fid = field_id or fields[0]["id"]
    ctx = _sensor_ctx(fid)
    return templates.TemplateResponse(
        request, "monitoring.html",
        {"active_page": "monitoring", "fields": fields, **ctx},
    )


@router.get("/monitoring/sensors/{field_id}", response_class=HTMLResponse)
async def sensor_cards_partial(field_id: str, request: Request):
    ctx = _sensor_ctx(field_id)
    return templates.TemplateResponse(request, "partials/sensor_cards.html", ctx)


@router.get("/monitoring/irrigation/{field_id}", response_class=HTMLResponse)
async def irrigation_partial(field_id: str, request: Request):
    ctx = _sensor_ctx(field_id)
    return templates.TemplateResponse(request, "partials/irrigation_card.html", ctx)


@router.post("/monitoring/pump/{field_id}", response_class=HTMLResponse)
async def toggle_pump(field_id: str, request: Request):
    decision = get_latest_decision(field_id) or {}
    current = decision.get("pump_status", "OFF")
    new_status = "OFF" if current == "ON" else "ON"

    try:
        cfg = request.app.state.config
        mqtt_cfg = cfg["mqtt"]
        import json
        import paho.mqtt.publish as publish
        publish.single(
            f"farm/{field_id}/pump",
            json.dumps({"field_id": field_id, "pump": new_status, "manual": True}),
            hostname=mqtt_cfg["broker"],
            port=mqtt_cfg.get("port", 1883),
        )
    except Exception:
        pass

    ctx = _sensor_ctx(field_id)
    ctx["pump_status"] = new_status
    return templates.TemplateResponse(request, "partials/irrigation_card.html", ctx)
