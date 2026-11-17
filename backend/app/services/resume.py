"""断点续采恢复业务规则：状态流转、字段校验与筛选口径都收在这里。

断点记录除业务输入外，还维护所属团队、续采批次、占用状态与断点历史；
恢复权限校验、断点位置占用仲裁与续采占用结果统一交给 occupation 服务。
"""
from __future__ import annotations

from typing import Any

from app.services.occupation import TRANSFER_MODES, occupation_service
from app.store import store

MODULE = "resume"
REQUIRED_FIELDS = ["断点编号", "采集计划", "断点位置", "所属团队"]
STATUS_ORDER = ["已记录", "恢复中", "已恢复", "已放弃"]
# 动作 -> 允许执行时的当前状态；None 表示不限状态（由占用仲裁自行判断）。
ACTION_RULES = {
    "启动续采": ["已记录", "恢复中"],
    "重试续采": ["已记录"],
    "失败释放": ["恢复中"],
    "完成批次": ["恢复中"],
    "转移归属": None,
    "放弃恢复": ["已记录"],
}
NEGATIVE_ACTIONS = ["失败释放"]


class ResumeService:
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
            rows = [row for row in rows if keyword in str(row.get("断点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int, team: str | None = None) -> dict[str, Any] | None:
        """读取断点明细；越权团队只能看到归属和处理状态。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        if team and not occupation_service.can_operate(entry, team):
            return occupation_service.readonly_view(entry)
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ["断点编号", "采集计划", "断点位置", "已采集量", "剩余量", "恢复策略", "恢复耗时", "记录状态", "所属团队"]:
            entry[field] = str(values.get(field) or "").strip()
        if not entry["已采集量"]:
            entry["已采集量"] = str(entry["断点位置"])
        entry["续采批次"] = ""
        entry["占用状态"] = "空闲"
        entry["断点历史"] = []
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"断点记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于断点续采恢复可执行范围"
        team = str(values.get("操作团队") or "").strip()
        allowed_from = ACTION_RULES[action]
        if allowed_from is not None and entry.get("status") not in allowed_from:
            return None, f"断点记录当前状态「{entry.get('status')}」不允许执行「{action}」"

        if action in ("启动续采", "重试续采"):
            batch, message, _granted = occupation_service.grant(entry, team)
            if batch is None:
                return occupation_service.readonly_view(entry) if not occupation_service.can_operate(entry, team) else None, message
            return entry, message
        if action == "失败释放":
            used = _to_int(values.get("本次已用"))
            batch, message = occupation_service.release_unused(entry, team, used)
            if batch is None:
                return None, message
            entry["abnormal"] = True
            return entry, message
        if action == "完成批次":
            batch, message = occupation_service.complete(entry, team)
            if batch is None:
                return None, message
            entry["abnormal"] = False
            return entry, message
        if action == "转移归属":
            new_team = str(values.get("新团队") or "").strip()
            mode = str(values.get("交接方式") or "").strip()
            result, message = occupation_service.transfer(entry, new_team=new_team, mode=mode, operator_team=team)
            return result, message
        # 放弃恢复：仅允许在尚未授予批次时放弃，已占用位置必须先失败释放。
        entry["status"] = "已放弃"
        entry["pending"] = False
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"断点记录已{action}"


def _to_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0
