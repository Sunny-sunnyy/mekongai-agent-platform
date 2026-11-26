"""Xác thực cục bộ tối giản + lưu lịch sử chat bền vững trong SQLite (0 đồng).

- Bảng `users`: tài khoản nội bộ với băm mật khẩu `scrypt` + muối ngẫu nhiên 16 byte; 3 vai trò:
  `nhan_vien`, `nguoi_duyet`, `quan_tri`.
- Bảng `sessions`: lưu băm SHA-256 của token phiên (`HttpOnly; SameSite=Strict`) kèm hạn dùng.
- Bảng `chat_messages` + `chat_state`: lưu mọi lượt chat và trạng thái cuộc chat theo khoá `(user_id, conv)`,
  cô lập hoàn toàn giữa các người dùng và giữ bền qua khởi động lại máy chủ.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

VAI_TRO_HOP_LE = ("nhan_vien", "nguoi_duyet", "quan_tri")
VAI_TRO_DUYET = ("nguoi_duyet", "quan_tri")
PHIEN_GIAY = 30 * 24 * 3600
_STATE_DIR = Path(os.environ.get("CAPITOL_STATE_DIR") or (Path.home() / ".local" / "share" / "capitol-platform")).expanduser()
DB_MAC_DINH = _STATE_DIR / "chat_lich_su.db"

# Tài khoản mặc định cho test và bàn giao nội bộ (mật khẩu có thể ghi đè qua biến môi trường).
TAI_KHOAN_MAC_DINH = (
    ("u_nhan_vien", "nhan_vien", "Nhân viên", "nhan_vien"),
    ("u_nguoi_duyet", "nguoi_duyet", "Người duyệt", "nguoi_duyet"),
    ("u_quan_tri", "quan_tri", "Quản trị", "quan_tri"),
)


def _bam_mat_khau(mat_khau: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    s = salt if salt is not None else os.urandom(16)
    h = hashlib.scrypt(mat_khau.encode("utf-8"), salt=s, n=16384, r=8, p=1, dklen=32)
    return s, h


def _bam_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mat_khau_mac_dinh() -> str:
    return (os.environ.get("CAPITOL_MAT_KHAU_MAC_DINH") or "capitol-noi-bo-2026").strip() or "capitol-noi-bo-2026"


class KhoBenVung:
    """Kho SQLite dùng chung cho xác thực người dùng và lịch sử khung chat."""

    def __init__(self, db_path: str | Path | None = None, gio=time.time):
        p = Path(db_path or os.environ.get("CAPITOL_CHAT_DB") or DB_MAC_DINH)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p.parent, 0o700)
        except OSError:
            pass
        self.path = p
        self.gio = gio
        self._khoa = threading.Lock()
        self._khoi_tao()

    def _ket_noi(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=10.0, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def _khoi_tao(self) -> None:
        with self._khoa, self._ket_noi() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('nhan_vien', 'nguoi_duyet', 'quan_tri')),
                    salt BLOB NOT NULL,
                    pass_hash BLOB NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS chat_state (
                    user_id TEXT NOT NULL,
                    conv TEXT NOT NULL,
                    org TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL,
                    cuoi TEXT,
                    cau_cuoi TEXT,
                    org_cuoi TEXT,
                    da_chay_json TEXT,
                    PRIMARY KEY (user_id, conv)
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    conv TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    vai TEXT NOT NULL,
                    chu TEXT NOT NULL,
                    dt_json TEXT,
                    ts REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_msg_key ON chat_messages(user_id, conv, seq, id);
                """
            )
            so = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if so == 0:
                now = float(self.gio())
                for uid, uname, dname, role in TAI_KHOAN_MAC_DINH:
                    s, h = _bam_mat_khau(_mat_khau_mac_dinh())
                    c.execute(
                        "INSERT INTO users(id, username, display_name, role, salt, pass_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (uid, uname, dname, role, s, h, now),
                    )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # ---------------- Xác thực người dùng ----------------
    def tao_nguoi_dung(self, username: str, password: str, role: str = "nhan_vien", display_name: str | None = None) -> dict:
        u = (username or "").strip()
        if not u or len(u) > 64 or not password or role not in VAI_TRO_HOP_LE:
            raise ValueError("Thông tin tài khoản hoặc vai trò không hợp lệ.")
        s, h = _bam_mat_khau(password)
        uid = "u_" + secrets.token_hex(8)
        now = float(self.gio())
        dn = (display_name or u).strip()[:80]
        with self._khoa, self._ket_noi() as c:
            c.execute(
                "INSERT INTO users(id, username, display_name, role, salt, pass_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, u, dn, role, s, h, now),
            )
        return {"id": uid, "username": u, "display_name": dn, "role": role}

    def dang_nhap(self, username: str, password: str) -> tuple[dict, str] | None:
        u = (username or "").strip()
        if not u or not isinstance(password, str) or not password:
            return None
        with self._khoa, self._ket_noi() as c:
            r = c.execute(
                "SELECT id, username, display_name, role, salt, pass_hash FROM users WHERE username = ?",
                (u,),
            ).fetchone()
            if not r:
                return None
            _, h = _bam_mat_khau(password, bytes(r["salt"]))
            if not hmac.compare_digest(h, bytes(r["pass_hash"])):
                return None
            now = float(self.gio())
            c.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            tok = secrets.token_urlsafe(32)
            c.execute(
                "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_bam_token(tok), r["id"], now, now + PHIEN_GIAY),
            )
            return {"id": r["id"], "username": r["username"], "display_name": r["display_name"], "role": r["role"]}, tok

    def kiem_phien(self, token: str | None) -> dict | None:
        if not token or not isinstance(token, str):
            return None
        th = _bam_token(token.strip())
        now = float(self.gio())
        with self._khoa, self._ket_noi() as c:
            r = c.execute(
                """
                SELECT u.id, u.username, u.display_name, u.role, s.expires_at
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (th,),
            ).fetchone()
            if not r:
                return None
            if float(r["expires_at"]) <= now:
                c.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
                return None
            return {"id": r["id"], "username": r["username"], "display_name": r["display_name"], "role": r["role"]}

    def dang_xuat(self, token: str | None) -> bool:
        if not token or not isinstance(token, str):
            return False
        th = _bam_token(token.strip())
        with self._khoa, self._ket_noi() as c:
            cur = c.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            return cur.rowcount > 0

    def ds_tai_khoan_goi_y(self) -> list[dict]:
        with self._khoa, self._ket_noi() as c:
            rows = c.execute("SELECT username, display_name, role FROM users ORDER BY created_at, username").fetchall()
            return [{"username": r["username"], "display_name": r["display_name"], "role": r["role"]} for r in rows]

    # ---------------- Lịch sử chat theo (user_id, conv) ----------------
    def them_tin(self, user_id: str, conv: str, vai: str, chu: str, dt=None, ts: float | None = None) -> None:
        if not user_id or not conv:
            return
        t = float(self.gio() if ts is None else ts)
        dt_j = json.dumps(dt, ensure_ascii=False) if dt is not None else None
        with self._khoa, self._ket_noi() as c:
            c.execute(
                """
                INSERT INTO chat_state(user_id, conv, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, conv) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (user_id, conv, t),
            )
            seq_r = c.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM chat_messages WHERE user_id = ? AND conv = ?",
                (user_id, conv),
            ).fetchone()
            seq = int(seq_r[0]) + 1
            c.execute(
                "INSERT INTO chat_messages(user_id, conv, seq, vai, chu, dt_json, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, conv, seq, vai, str(chu or ""), dt_j, t),
            )

    def luu_trang_thai(
        self,
        user_id: str,
        conv: str,
        *,
        cuoi: str | None = None,
        cau_cuoi: str | None = None,
        org_cuoi: str | None = None,
        da_chay: dict | list | None = None,
    ) -> None:
        if not user_id or not conv:
            return
        t = float(self.gio())
        if isinstance(da_chay, dict):
            dc_chuan = [[list(k) if isinstance(k, tuple) else k, v] for k, v in da_chay.items()]
        else:
            dc_chuan = da_chay
        dc_j = json.dumps(dc_chuan, ensure_ascii=False) if dc_chuan is not None else None
        with self._khoa, self._ket_noi() as c:
            c.execute(
                """
                INSERT INTO chat_state(user_id, conv, updated_at, cuoi, cau_cuoi, org_cuoi, da_chay_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, conv) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    cuoi = COALESCE(excluded.cuoi, chat_state.cuoi),
                    cau_cuoi = COALESCE(excluded.cau_cuoi, chat_state.cau_cuoi),
                    org_cuoi = COALESCE(excluded.org_cuoi, chat_state.org_cuoi),
                    da_chay_json = COALESCE(excluded.da_chay_json, chat_state.da_chay_json)
                """,
                (user_id, conv, t, cuoi, cau_cuoi, org_cuoi, dc_j),
            )

    def tai_cuoc_chat(self, user_id: str, conv: str, gioi_han: int = 120) -> dict | None:
        if not user_id or not conv:
            return None
        with self._khoa, self._ket_noi() as c:
            st = c.execute(
                "SELECT updated_at, cuoi, cau_cuoi, org_cuoi, da_chay_json FROM chat_state WHERE user_id = ? AND conv = ?",
                (user_id, conv),
            ).fetchone()
            rows = c.execute(
                """
                SELECT vai, chu, dt_json, ts FROM (
                    SELECT id, seq, vai, chu, dt_json, ts
                    FROM chat_messages
                    WHERE user_id = ? AND conv = ?
                    ORDER BY seq DESC, id DESC
                    LIMIT ?
                ) ORDER BY seq ASC, id ASC
                """,
                (user_id, conv, max(1, int(gioi_han))),
            ).fetchall()
            if not st and not rows:
                return None
            msgs = []
            for r in rows:
                m = {"vai": r["vai"], "noi_dung": r["chu"], "ts": r["ts"]}
                if r["dt_json"]:
                    try:
                        m["dt"] = json.loads(r["dt_json"])
                    except Exception:
                        pass
                msgs.append(m)
            da_chay = {}
            if st and st["da_chay_json"]:
                try:
                    da_chay = json.loads(st["da_chay_json"]) or {}
                except Exception:
                    da_chay = {}
            return {
                "updated_at": float(st["updated_at"]) if st else (msgs[-1]["ts"] if msgs else float(self.gio())),
                "cuoi": st["cuoi"] if st else None,
                "cau_cuoi": st["cau_cuoi"] if st else None,
                "org_cuoi": st["org_cuoi"] if st else None,
                "da_chay": da_chay if isinstance(da_chay, (dict, list)) else {},
                "m": msgs,
            }

    def ds_cuoc_chat(self, user_id: str, gioi_han: int = 20) -> list[dict]:
        if not user_id:
            return []
        with self._khoa, self._ket_noi() as c:
            rows = c.execute(
                """
                SELECT s.conv, s.updated_at,
                       (SELECT m.chu FROM chat_messages m WHERE m.user_id = s.user_id AND m.conv = s.conv AND m.vai = 'nguoi_dung' ORDER BY m.seq ASC LIMIT 1) AS dau,
                       (SELECT COUNT(*) FROM chat_messages m WHERE m.user_id = s.user_id AND m.conv = s.conv) AS so_tin
                FROM chat_state s
                WHERE s.user_id = ?
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (user_id, max(1, int(gioi_han))),
            ).fetchall()
            return [
                {"conv": r["conv"], "updated_at": r["updated_at"], "tieu_de": (r["dau"] or "Cuộc trò chuyện")[:80], "so_tin": r["so_tin"]}
                for r in rows
                if r["so_tin"] > 0
            ]
