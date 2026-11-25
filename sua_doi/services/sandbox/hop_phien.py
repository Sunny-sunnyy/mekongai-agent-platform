"""[TRẠNG THÁI 09/11 chiều: BẢN VÒNG 4 — vòng 2 (m4, ensure_ascii) + lần chạy hộp thật 2 (15:06): thư mục phiên đã bị người chạy xoá (phiên đóng) ⇒ trả phien_het_han thay vì NÉM; chờ đã soát diff (hop_phien.py.vong3 = bản đã chạy).]
[CHẠY THỬ — MỤC 63, nhóm kỹ thuật, 09/11/2026] Bộ chuyển HỘP PHIÊN (V2): thay `SandboxManager` (E2B) cho dịch vụ E2BSkillsChatService.
Gắn đè thành `services/sandbox/hop_phien.py` trong 3 hộp ab-* (V5 nhóm kỹ thuật); V1 chọn lớp này khi cờ `CHAY_THU_HOP_PHIEN` bật.

Giao diện chốt với B: chay_thu/khoi_e2b/GIAO_DIEN_V2.md (mục 3 + mục 6). Thiết kế: chay_thu/khoi_e2b/THIET_KE_HOP_PHIEN.md bản 2.
Chiều dữ liệu: lớp này KHÔNG chạy docker, KHÔNG mở mạng — chỉ ghi phiếu vào  <hàng đợi>/vao/…  và đọc trả lời ở  <hàng đợi>/ra/…  (ab-* gắn ra/ CHỈ ĐỌC).
Người chạy phiên (chay_thu/sandbox/nguoi_chay_phien.py, trên WSL) mới là bên chạm hộp. Vì vậy:
- 0 import / gọi `AsyncSandbox` / `e2b_code_interpreter` ở file này (D C12); `E2B_API_KEY` vẫn rỗng (lớp thứ hai).
- Lỗi của hộp / hàng đợi KHÔNG bao giờ thành "thành công rỗng" (D C18): run_code / run_shell trả CodeResult có error (run_shell + raise_on_error thì
  ném); hàm file ném RuntimeError("hộp phiên: <loại>: <lý do>").
Chữ ký từng hàm khớp `services/sandbox/manager.py` [code, AB release-4.10.0] — C rút bằng rg 09/11; khác gốc ghi ở từng hàm.
"""
import asyncio
import base64
import json
import os
import re
import stat
import time
import uuid
from typing import Any, Callable, Optional

from services.sandbox.manager import CodeResult

GOC_HANG = os.environ.get("CHAY_THU_HANG_DOI_PHIEN", "/srv/hang_doi_phien")
MA = re.compile(r"[0-9a-f]{32}")
TRAN_LENH = 120            # người chạy cũng kẹp — đây chỉ để tính hạn chờ
DU_CHO = 30                # chờ hàng + khởi động hộp + ghi trả lời
CHO_MO = 90                # GIAO_DIEN_V2 6.2
TRAN_TRA_LOI = 16 * 1024 * 1024
TRAN_FILE = 10 * 1024 * 1024
ENVS_DUNG = {"INPUT_DIR": "/home/user/input", "OUTPUT_DIR": "/home/user/output", "WORK_DIR": "/home/user/work"}  # services/sandbox/paths.py:29-31


class HopPhienLoiLenh(RuntimeError):
    """run_shell mã thoát ≠ 0 khi raise_on_error (gốc ném CommandExitException của e2b; mọi nơi gọi bắt `Exception` — rg 09/11)."""

    def __init__(self, result: CodeResult):
        self.result = result
        super().__init__(result.error or f"Command exited with code {result.exit_code}")


class _Hop:
    def __init__(self, ma: str):
        self.sandbox_id = ma


class _Dong:
    """tools.py `_make_stream_handler` đọc `msg.line` (getattr) ⇒ phát lại TỪNG DÒNG lúc lệnh xong (không phát dần)."""

    def __init__(self, line: str):
        self.line = line


class HopPhienManager:
    def __init__(self, timeout: int = 300, *, node_type: str | None = None, node_sub_type: str | None = None, node_id: str | None = None,
                 workflow_id: str | None = None, template: object = None, envs: dict[str, str] | None = None,
                 allow_internet_access: bool | None = None, **_bo_qua: Any) -> None:
        # template bỏ qua (image cố định ở người chạy); allow_internet_access bỏ qua (hộp luôn --network none)
        if envs:
            la = {k: v for k, v in envs.items() if ENVS_DUNG.get(k) != v}
            if la:
                raise ValueError(f"hộp phiên chỉ nhận envs {sorted(ENVS_DUNG)} đúng giá trị /home/user/...; lạ: {sorted(la)}")
        self._timeout = timeout
        self._ma: str | None = None
        self._vao = os.path.join(GOC_HANG, "vao")
        self._ra = os.path.join(GOC_HANG, "ra")

    # ---- thuộc tính như gốc --------------------------------------------------------------------------------------------------
    @property
    def sandbox(self) -> _Hop:
        if self._ma is None:
            raise RuntimeError("Sandbox not created. Call create() first.")   # đúng chữ gốc manager.py:418-421
        return _Hop(self._ma)

    @property
    def respin_count(self) -> int:
        return 0

    @property
    def uses_custom_template(self) -> bool:
        return False

    @property
    def template_name(self) -> str:
        return "sandbox-py:v1"

    # ---- hàng đợi ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _ghi_phieu(thu_muc: str, ma_phieu: str, d: dict) -> None:
        if not os.path.isdir(thu_muc) or os.path.islink(thu_muc):
            raise RuntimeError("hộp phiên: loi_hop: thư mục phiên chưa có")   # GIAO_DIEN_V2 6.4 — người chạy tạo, V2 không mkdir
        tam = os.path.join(thu_muc, f".{ma_phieu}.tam")
        f = os.open(tam, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(f, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=True)     # vòng 2: code / lệnh có surrogate lẻ không làm ném ở đây — người chạy tự từ chối
        os.rename(tam, os.path.join(thu_muc, f"{ma_phieu}.json"))

    @staticmethod
    def _doc_tra_loi(duong: str) -> dict | None:
        try:
            f = os.open(duong, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        try:
            st = os.fstat(f)
            if not stat.S_ISREG(st.st_mode) or st.st_size > TRAN_TRA_LOI:
                return {"ok": False, "loi": {"loai": "loi_hop", "ly_do": "phiếu trả lời lạ / quá cỡ"}}
            with os.fdopen(os.dup(f), "rb") as fh:
                b = fh.read(TRAN_TRA_LOI + 1)
        finally:
            os.close(f)
        try:
            d = json.loads(b.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ok": False, "loi": {"loai": "loi_hop", "ly_do": "phiếu trả lời hỏng"}}
        return d if isinstance(d, dict) else {"ok": False, "loi": {"loai": "loi_hop", "ly_do": "phiếu trả lời không phải object"}}

    async def _cho(self, thu_muc_ra: str, ma_phieu: str, han: float) -> dict:
        duong = os.path.join(thu_muc_ra, f"{ma_phieu}.json")
        het = time.monotonic() + han
        while time.monotonic() < het:
            d = await asyncio.to_thread(self._doc_tra_loi, duong)
            if d is not None:
                return d
            await asyncio.sleep(0.25)
        return {"ok": False, "loi": {"loai": "khong_tra_loi", "ly_do": f"không có phiếu trả lời sau {round(han)} s"}}

    async def _gui(self, kieu: str, han_cho: float, **truong: Any) -> dict:
        if self._ma is None:
            raise RuntimeError("Sandbox not created. Call create() first.")
        ma_phieu = uuid.uuid4().hex
        thu_muc_vao = os.path.join(self._vao, self._ma)
        if not os.path.isdir(thu_muc_vao) or os.path.islink(thu_muc_vao):
            # [vòng 4, hộp thật 15:06] người chạy đã đóng phiên (trần phiếu / rảnh / hết hạn) ⇒ xoá vao/<mã> ⇒ trước đây _ghi_phieu NÉM ⇒ run_code
            # ném thay vì trả CodeResult lỗi (C18). Nay trả lỗi rõ như mọi lỗi hàng đợi khác; lệnh CHƯA chạy (chưa ghi phiếu).
            return {"ok": False, "loi": {"loai": "phien_het_han", "ly_do": "thư mục phiên không còn — phiên đã đóng (trần phiếu / rảnh / hết hạn); lệnh CHƯA chạy"}}
        await asyncio.to_thread(self._ghi_phieu, thu_muc_vao, ma_phieu, dict(kieu=kieu, **truong))
        tl = await self._cho(os.path.join(self._ra, self._ma), ma_phieu, han_cho)
        if (tl.get("loi") or {}).get("loai") == "khong_tra_loi":
            # D mục 66 m4: hai công cụ gọi song song ⇒ phiếu này có thể đang chờ sau lệnh khác. Gỡ được phiếu ⇒ người chạy CHƯA đọc ⇒ chắc chắn
            # chưa chạy; không gỡ được (đã bị đọc) ⇒ lệnh CÓ THỂ ĐANG / ĐÃ chạy trong hộp — nói rõ để AI không tưởng là chưa chạy.
            try:
                os.unlink(os.path.join(thu_muc_vao, f"{ma_phieu}.json"))
                them = "phiếu chưa được đọc — đã gỡ, lệnh CHƯA chạy"
            except OSError:
                them = "phiếu đã được người chạy nhận — lệnh CÓ THỂ VẪN ĐANG CHẠY (hoặc đã chạy) trong hộp, đừng coi là chưa chạy"
            tl = {"ok": False, "loi": {"loai": "khong_tra_loi", "ly_do": f"{tl['loi'].get('ly_do', '')}; {them}"}}
        return tl

    @staticmethod
    def _loi_chu(tl: dict) -> str:
        l = tl.get("loi") or {}
        return f"{l.get('loai', 'loi_hop')}: {l.get('ly_do', '')}"

    def _loi_file(self, tl: dict) -> RuntimeError:
        return RuntimeError(f"hộp phiên: {self._loi_chu(tl)}")

    # ---- vòng đời ------------------------------------------------------------------------------------------------------------
    async def create(self, packages: list[str] | None = None) -> "HopPhienManager":
        if packages:
            raise RuntimeError("hộp phiên không có mạng — không cài gói")
        if self._ma is not None:
            return self
        yc = uuid.uuid4().hex
        await asyncio.to_thread(self._ghi_phieu, os.path.join(self._vao, "_mo"), yc, {"kieu": "mo_phien"})
        tl = await self._cho(os.path.join(self._ra, "_mo"), yc, CHO_MO)
        if not tl.get("ok"):
            # hết hạn chờ mà phiếu _mo còn nằm đó ⇒ gỡ để người chạy không mở hộp cho lượt đã bỏ đi
            try:
                os.unlink(os.path.join(self._vao, "_mo", f"{yc}.json"))
            except OSError:
                pass
            raise self._loi_file(tl)
        ma = tl.get("ma_phien")
        if not isinstance(ma, str) or not MA.fullmatch(ma):
            raise RuntimeError("hộp phiên: loi_hop: mã phiên trả về sai khuôn")
        self._ma = ma
        return self

    async def cleanup(self) -> None:
        """Gửi dong_phien; gọi 2 lần không lỗi; không bao giờ ném (như gốc: dọn dẹp không được làm hỏng lượt)."""
        ma, self._ma = self._ma, None
        if ma is None:
            return
        try:
            ma_phieu = uuid.uuid4().hex
            await asyncio.to_thread(self._ghi_phieu, os.path.join(self._vao, ma), ma_phieu, {"kieu": "dong_phien"})
            await self._cho(os.path.join(self._ra, ma), ma_phieu, 30)
        except Exception:   # noqa: BLE001 — phiên tự đóng sau 3 phút rảnh ở người chạy
            pass

    async def start_keepalive(self, interval: int | None = None) -> None:
        return None   # hạn phiên do người chạy giữ (10 phút)

    async def stop_keepalive(self) -> None:
        return None

    async def extend_sandbox_lifetime(self) -> None:
        return None

    # ---- chạy code -----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _han(timeout: Any) -> int:
        try:
            return max(1, min(int(timeout), TRAN_LENH))
        except (TypeError, ValueError):
            return TRAN_LENH

    @staticmethod
    def _cat_note(tl: dict) -> str:
        return "\n[hộp phiên: chữ ra đã bị cắt ở 256 KB]" if tl.get("da_cat") else ""

    async def run_code(self, code: str, language: str = "python", timeout: int = 3600,
                       on_stdout: Optional[Callable[[Any], None]] = None, on_stderr: Optional[Callable[[Any], None]] = None) -> CodeResult:
        lang = (language or "python").lower()
        if lang in ("shell", "bash", "sh"):
            kieu, truong = "shell", {"lenh": code}
        elif lang in ("python", "python3", "py"):
            kieu, truong = "python", {"code": code}
        else:
            return CodeResult(exit_code=1, error=f"ngôn ngữ không hỗ trợ trong hộp phiên: {language}")
        han = self._han(timeout)
        tl = await self._gui(kieu, han + DU_CHO, han_giay=han, **truong)
        out, err = str(tl.get("stdout") or ""), str(tl.get("stderr") or "")
        for cb, chu in ((on_stdout, out), (on_stderr, err)):
            if cb is not None:
                for dong in chu.splitlines():
                    try:
                        cb(_Dong(dong))
                    except Exception:   # noqa: BLE001 — bộ phát tiến độ không được làm hỏng kết quả
                        pass
        if not tl.get("ok"):
            return CodeResult(stdout=out.strip(), stderr=err.strip(), exit_code=int(tl.get("ma_thoat") or 1),
                              error=f"hộp phiên: {self._loi_chu(tl)}")
        mt = int(tl.get("ma_thoat") or 0)
        error = None
        if mt != 0:
            # gốc: "Tên: giá trị\ntraceback" của ngoại lệ Python (manager.py:972-977) ⇒ ở đây: dòng cuối khác rỗng của stderr + toàn stderr
            cuoi = next((d for d in reversed(err.strip().splitlines()) if d.strip()), "")
            error = (f"{cuoi}\n{err.strip()}" if cuoi else f"exit code {mt}")[:20000]
        return CodeResult(stdout=out.strip() + self._cat_note(tl), stderr=err.strip(), exit_code=mt, error=error, output_files=[])

    async def run_shell(self, cmd: str, timeout: int = 30, *, raise_on_error: bool = True, replay_on_respin: bool = False) -> CodeResult:
        han = self._han(timeout)
        tl = await self._gui("shell", han + DU_CHO, lenh=cmd, han_giay=han)
        out, err = str(tl.get("stdout") or "").strip(), str(tl.get("stderr") or "").strip()
        if not tl.get("ok"):
            r = CodeResult(stdout=out, stderr=err, exit_code=int(tl.get("ma_thoat") or 1), error=f"hộp phiên: {self._loi_chu(tl)}")
            if raise_on_error:
                raise HopPhienLoiLenh(r)
            return r
        mt = int(tl.get("ma_thoat") or 0)
        r = CodeResult(stdout=out + self._cat_note(tl), stderr=err, exit_code=mt)
        if mt != 0:
            r.error = f"Command exited with code {mt}"   # như gốc manager.py khi result.error rỗng
            if raise_on_error:
                raise HopPhienLoiLenh(r)
        return r

    # ---- file ----------------------------------------------------------------------------------------------------------------
    async def upload_file(self, path: str, content: bytes) -> str:
        if isinstance(content, str):
            content = content.encode("utf-8")
        content = bytes(content)
        if len(content) > TRAN_FILE:
            raise RuntimeError(f"hộp phiên: tu_choi: file > {TRAN_FILE} byte")
        tl = await self._gui("dat_file", 60 + DU_CHO, duong_dan=path, noi_dung_b64=base64.b64encode(content).decode("ascii"))
        if not tl.get("ok"):
            raise self._loi_file(tl)
        return path

    async def write_file(self, path: str, content: str) -> str:
        return await self.upload_file(path, content.encode("utf-8"))

    async def download_file(self, path: str) -> bytes:
        tl = await self._gui("lay_file", 60 + DU_CHO, duong_dan=path)
        if not tl.get("ok"):
            raise self._loi_file(tl)
        try:
            return base64.b64decode(str(tl.get("b64") or ""), validate=True)
        except ValueError as exc:
            raise RuntimeError("hộp phiên: loi_hop: b64 trả về hỏng") from exc

    async def read_binary_file(self, path: str) -> bytes:
        return await self.download_file(path)

    async def read_file(self, path: str) -> str:
        return (await self.download_file(path)).decode("utf-8")   # NGHIÊM như gốc manager.py:1288 (lỗi ⇒ tools.py trả "ERROR: could not read")

    async def list_files(self, path: str = "/home/user") -> list[str]:
        tl = await self._gui("liet_ke", 60 + DU_CHO, duong_dan=path, de_quy=False)
        if not tl.get("ok"):
            raise self._loi_file(tl)
        return [str(x) for x in tl.get("ds") or []]

    async def list_files_recursive(self, path: str = "/home/user") -> list[str]:
        tl = await self._gui("liet_ke", 60 + DU_CHO, duong_dan=path, de_quy=True)
        if not tl.get("ok"):
            raise self._loi_file(tl)
        return [str(x) for x in tl.get("ds") or []]

    async def install_packages(self, packages: list[str], manager: str = "pip") -> str:
        # GIAO_DIEN_V2 6.1 s1: NÉM (gốc ném khi cài lỗi); công cụ AI tools.py:425-440 bọc ngoại lệ thành chữ cho AI đọc
        raise RuntimeError("hộp phiên không có mạng — không cài được gói; dùng gói có sẵn")
