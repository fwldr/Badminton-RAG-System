"""审计日志路由：查询 + CSV 导出（管理鉴权）。"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query

from app.api.deps import admin_rate_limit, require_admin_access
from app.api.errors import ok
from app.db.repos import AuditRepo

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin_access)])


@router.get("/logs", summary="审计日志列表（分页）")
async def list_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """返回审计日志分页结果与总数。"""
    logs = AuditRepo.query(limit=limit, offset=offset)
    return ok({"total": AuditRepo.count(), "logs": logs})


@router.get("/logs/export", summary="审计日志导出 CSV")
async def export_logs() -> object:
    """全量导出为 CSV（utf-8-sig BOM，Excel 打开中文正常）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "时间", "IP", "问题", "回答", "来源", "耗时ms"])
    for row in AuditRepo.export_all():
        writer.writerow(
            [
                row["id"],
                row["created_at"],
                row["client_ip"],
                row["question"],
                row["answer"],
                row["sources_json"],
                row["latency_ms"],
            ]
        )
    data = "\ufeff" + buffer.getvalue()  # BOM
    from fastapi.responses import Response

    return Response(
        content=data.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit_logs.csv"'},
    )
