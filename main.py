from contextlib import asynccontextmanager
import threading
import yaml
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import init_db
from routers import monitoring, forecast, profile, history


def load_config() -> dict:
    with open("config.yaml") as f:
        return yaml.safe_load(f)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    app.state.config = cfg
    init_db()

    try:
        from services.mqtt_bridge import MQTTBridge
        bridge = MQTTBridge(cfg)
        t = threading.Thread(target=bridge.start, daemon=True, name="mqtt-bridge")
        t.start()
        app.state.bridge = bridge
    except Exception as e:
        print(f"[agriflow] MQTT bridge skipped: {e}")
        app.state.bridge = None

    yield

    if app.state.bridge:
        app.state.bridge.stop()


app = FastAPI(title="AgriFlow", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(monitoring.router)
app.include_router(forecast.router)
app.include_router(profile.router)
app.include_router(history.router)


@app.get("/")
async def root():
    return RedirectResponse(url="/monitoring")
