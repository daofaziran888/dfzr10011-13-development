"""任务分片调度业务规则：状态流转、字段校验与筛选口径都收在这里。

分片调度读取续采占用结果：列表与明细会带出断点所属团队和唯一续采批次，
恢复批次的分片范围由占用仲裁服务在授予时绑定到分片任务上。
"""
from __future__ import annotations

from typing import Any

from app.services.occupation import occupation_service
from app.store import store

MODULE = "shard"
REQUIRED_FIELDS = ["分片编号", "采集计划", "分片范围"]
STATUS_ORDER = ["待分配", "执行中", "已完成", "执行失败"]
ACTION_RULES = {"分配节点": "待分配", "重试分片": "执行中", "标记完成": "执行失败"}
NEGATIVE_ACTIONS = []


def _enrich(row: dict[str, Any]) -> dict[str, Any]:
    """补出断点所属团队与唯一续采批次；未绑定续采批次的分片保持原样。"""
    batch_no = str(row.get("续采批次", "") or "")
    if not batch_no:
        return row
    batch = occupation_service.batch_of(batch_no)
    if batch is None:
        return row
    row["执行团队"] = batch.get("执行团队", row.get("执行团队", ""))
    row["分片范围"] = batch.get("占用范围", row.get("分片范围", ""))
    return row


class ShardService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("分片编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [_enrich(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return _enrich(entry) if entry is not None else None

    def resume_summary(self) -> list[dict[str, Any]]:
        """断点计划归属汇总清单：由续采占用结果数据驱动。"""
        return occupation_service.summary()

    def resume_todos(self) -> list[dict[str, Any]]:
        """断点计划归属待办：随各业务入口更新的断点与占用结果同步变化。"""
        return occupation_service.todos()

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["断点所属团队"] = str(values.get("断点所属团队") or "")
        entry["执行团队"] = str(values.get("执行团队") or "")
        entry["续采批次"] = str(values.get("续采批次") or "")
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分片任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于任务分片调度可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"分片任务已{action}"
