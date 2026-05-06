import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from database import get_history, get_chart_series
from templates_env import templates

router = APIRouter()

PER_PAGE = 50


def _default_range():
    end = datetime.utcnow()
    start = end - timedelta(days=7)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


@router.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    field_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
):
    fields = request.app.state.config["fields"]
    fid = field_id or fields[0]["id"]
    df_default, dt_default = _default_range()
    df = date_from or df_default
    dt = date_to or dt_default

    rows, total = get_history(fid, df + "T00:00:00", dt + "T23:59:59", page, PER_PAGE)
    total_pages = max(1, -(-total // PER_PAGE))

    return templates.TemplateResponse(request, "history.html", {
        "active_page": "history",
        "fields": fields,
        "field_id": fid,
        "date_from": df,
        "date_to": dt,
        "rows": rows,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@router.get("/history/table", response_class=HTMLResponse)
async def history_table_partial(
    request: Request,
    field_id: str = Query(...),
    date_from: str = Query(...),
    date_to: str = Query(...),
    page: int = 1,
):
    rows, total = get_history(
        field_id, date_from + "T00:00:00", date_to + "T23:59:59", page, PER_PAGE
    )
    total_pages = max(1, -(-total // PER_PAGE))
    return templates.TemplateResponse(request, "partials/history_table.html", {
        "field_id": field_id,
        "date_from": date_from,
        "date_to": date_to,
        "rows": rows,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@router.get("/history/export")
async def export_csv(
    field_id: str = Query(...),
    date_from: str = Query(...),
    date_to: str = Query(...),
):
    rows, _ = get_history(field_id, date_from + "T00:00:00", date_to + "T23:59:59", 1, 100_000)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "timestamp","air_temp","air_humidity","soil_temp","soil_moisture","alarm","volume_litres","pump_status"
    ])
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    filename = f"agriflow_{field_id}_{date_from}_{date_to}.csv"
    return StreamingResponse(
        io.BytesIO(buf.read().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/api/history/chart/{field_id}")
async def history_chart(
    field_id: str,
    date_from: str = Query(...),
    date_to: str = Query(...),
):
    rows = get_chart_series(field_id, date_from + "T00:00:00", date_to + "T23:59:59")
    return JSONResponse(rows)
