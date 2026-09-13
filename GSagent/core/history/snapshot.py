"""会话 + 工作区双快照（002-enterprise-cli-upgrade US5 T039，FR-012，R-10）。

SnapshotManager 提供两类快照（对齐 data-model.md Snapshot 实体）：
- **会话快照**（kind=session）：history 序列化 + 会话状态指纹
  （session_fingerprint = history 的 sha256），restore = 重建会话
  （返回 messages 供恢复）。
- **工作区清单快照**（kind=workspace）：受控文件 hash 清单
  （workspace_manifest = {相对路径: sha256}），restore = 校验
  （对比当前文件 hash，返回变更清单——FR-012 恢复校验）。

通用保证：
- 快照落盘为 `{snapshots_dir}/{snapshot_id}.json`，ts 固定宽 UTC ISO。
- 默认保留最近 `keep` 份（R-10：pre/post-turn 自动生成的滚动保留）。
- stdlib 零新依赖（hashlib/uuid/json）。
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# 受控文件清单默认根（worktree/workspace root 由调用方传入）。
# pre/post-turn 自动生成的挂接点见 GSagent/main.py（T041 snapshot 命令）。


def _utc_iso(ts: datetime) -> str:
    """UTC datetime → 固定宽 ISO 字符串（毫秒 + Z，字典序即时间序）。"""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


class SnapshotManager:
    """会话/工作区快照的创建、列表、恢复与滚动裁剪。"""

    def __init__(
        self,
        snapshots_dir: Any,
        *,
        keep: int = 20,
        hash_algo: str = "sha256",
        clock=None,
    ) -> None:
        """snapshots_dir：快照存储目录；keep：默认保留最近 N 份（R-10）；
        hash_algo：清单哈希算法；clock：可注入时间（测试确定性）。"""
        self.snapshots_dir = Path(snapshots_dir)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.keep = max(1, int(keep))
        self.hash_algo = hash_algo
        self._clock = clock or _default_now

    # ---- 创建 ----
    def create_session_snapshot(
        self, session_id: str, messages: Sequence[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """会话快照：history 序列化 + 状态指纹，返回快照记录。"""
        msgs = list(messages)
        rec = {
            "snapshot_id": self._new_id(),
            "kind": "session",
            "ts": _utc_iso(self._clock()),
            "session_id": session_id,
            "session_fingerprint": self._fingerprint(msgs),
            "messages": msgs,
        }
        self._write(rec)
        self._purge()
        return rec

    def create_workspace_snapshot(
        self, workspace_root: Any, paths: Optional[Sequence[str]] = None
    ) -> Dict[str, Any]:
        """工作区清单快照：受控文件 hash 清单（相对路径 → sha256）。

        paths=None 时遍历 root 下全部文件；否则只快照指定相对路径。
        """
        root = Path(workspace_root)
        manifest: Dict[str, str] = {}
        if paths is not None:
            targets = [Path(p) for p in paths]
        else:
            targets = sorted(root.rglob("*"))
        for p in targets:
            full = root / p
            if full.is_file():
                # 相对路径（rglob 产出绝对路径，需 relative_to 归一；显式传参保持原样）
                try:
                    key = p.relative_to(root).as_posix()
                except ValueError:
                    key = Path(p).as_posix()
                manifest[key] = self._hash_file(full)
        rec = {
            "snapshot_id": self._new_id(),
            "kind": "workspace",
            "ts": _utc_iso(self._clock()),
            "workspace_manifest": manifest,
        }
        self._write(rec)
        self._purge()
        return rec

    # ---- 列表 / 恢复 ----
    def list_snapshots(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出快照（最新在前）；kind 过滤 session|workspace。"""
        snaps = []
        for f in self.snapshots_dir.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if kind is None or rec.get("kind") == kind:
                snaps.append(self._public(rec))
        snaps.sort(key=lambda r: r["ts"], reverse=True)
        return snaps

    def restore(
        self, snapshot_id: str, *, workspace_root: Optional[Any] = None
    ) -> Dict[str, Any]:
        """恢复快照。

        - session：返回 {"status":"ok","kind":"session","session_id",...,"messages"}
          （重建会话）。
        - workspace：对照 workspace_root 校验清单，返回
          {"status":"ok"|"changed","kind":"workspace","mismatched":[变更路径]}。
        """
        rec = self._load(snapshot_id)
        if rec["kind"] == "session":
            return {
                "status": "ok",
                "kind": "session",
                "snapshot_id": snapshot_id,
                "session_id": rec.get("session_id", ""),
                "session_fingerprint": rec.get("session_fingerprint", ""),
                "messages": list(rec.get("messages", [])),
            }
        # workspace：hash 校验
        root = Path(workspace_root)
        mismatched = []
        for rel, digest in (rec.get("workspace_manifest") or {}).items():
            full = root / rel
            if not full.is_file() or self._hash_file(full) != digest:
                mismatched.append(rel)  # 相对路径（manifest 键），供用户定位
        return {
            "status": "ok" if not mismatched else "changed",
            "kind": "workspace",
            "snapshot_id": snapshot_id,
            "mismatched": mismatched,
        }

    # ---- 内部 ----
    def _new_id(self) -> str:
        return f"snap-{uuid.uuid4().hex[:12]}"

    def _fingerprint(self, messages: Sequence[Dict[str, Any]]) -> str:
        blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        return hashlib.new(self.hash_algo, blob.encode("utf-8")).hexdigest()

    def _hash_file(self, path: Path) -> str:
        h = hashlib.new(self.hash_algo)
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _path(self, snapshot_id: str) -> Path:
        return self.snapshots_dir / f"{snapshot_id}.json"

    def _write(self, rec: Dict[str, Any]) -> None:
        self._path(rec["snapshot_id"]).write_text(
            json.dumps(rec, ensure_ascii=False), encoding="utf-8"
        )

    def _load(self, snapshot_id: str) -> Dict[str, Any]:
        try:
            return json.loads(self._path(snapshot_id).read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise FileNotFoundError(f"快照不存在: {snapshot_id}") from exc

    def _purge(self) -> None:
        """滚动保留最近 N 份：超出的最旧快照删除（R-10）。"""
        snaps = sorted(
            self.snapshots_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in snaps[self.keep :]:
            stale.unlink(missing_ok=True)

    @staticmethod
    def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
        """对外记录：去掉内部完整数据（messages/manifest 单独按需提供）。"""
        out = {k: v for k, v in rec.items() if k not in ("messages", "workspace_manifest")}
        return out


__all__ = ["SnapshotManager"]
