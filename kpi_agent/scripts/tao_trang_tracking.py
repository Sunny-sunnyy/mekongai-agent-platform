"""Sinh trang theo dõi KPI (.html tĩnh tự chứa) từ ĐÚNG số backend KPI tính — bản 1. 0 AI, 0 mạng.

Chạy từ gốc repo:
  .venv/bin/python -m kpi_agent.scripts.tao_trang_tracking --ngay-chot 2026-10-31 \
      [--trang "sample_data/thang_10/saved_resource.html"] [--ten-that] [--ra kpi_agent/data/trang_tracking.html]

- Dựng backend như lúc bật (workflow đường RAW, kho SQLite TẠM — không ghi vào kho thật của trang User) rồi đọc `queries.team_board` + Thưởng thêm.
- Mặc định chỉ mã che NV01–NV14; `--ten-that` mới hiện tên (lấy từ RAW, chỉ trên máy — như luật file xuất). File ra mặc định trong `kpi_agent/data/`
  (Git bỏ qua) — KHÔNG commit file sinh ra.
- `--trang`: so số thực hiện THÔ từng ô với trang theo dõi của đội (sai số 0,0005 đơn vị trang — `backend/tracking_view.py`) ⇒ ô lệch có ⚠ + lý do (`config/lech_trang_<kỳ>.yaml`).
- Dữ liệu thiếu / backend từ chối / tên trang không ghép được ⇒ dừng, KHÔNG ghi file."""
import argparse
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from kpi_agent.backend import tracking_view

REPO = Path(__file__).resolve().parents[2]
RAW_WORKFLOW = REPO / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml"


def backend_numbers(cutoff: date) -> tuple[dict, dict, dict]:
    """(bảng đội, thưởng thêm, meta) của backend bật với ngày chốt `cutoff` — đúng đường lifespan của main (một nguồn số với trang / Capitol)."""
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main, queries
    # main đọc ngày chốt / workflow lúc import ⇒ đặt thẳng biến module (đặt biến môi trường thì vô tác dụng khi main đã được nạp trước — vd trong
    # bộ pytest — và trang sẽ lặng lẽ ra số Tracking 20/10); kho SQLite TẠM để không ghi lượt chạy vào kho thật. Trả lại như cũ sau khi đọc xong.
    saved = (main.CUTOFF, main.WORKFLOW, os.environ.get("KPI_STORE"))
    with tempfile.TemporaryDirectory(prefix="kpi_trang_") as tmp:
        main.CUTOFF, main.WORKFLOW = cutoff, RAW_WORKFLOW
        os.environ["KPI_STORE"] = str(Path(tmp) / "store.sqlite")
        try:
            with TestClient(main.app) as client:
                app = client.app
                if app.state.ctx.cutoff != cutoff or runner_name(app) != "kpi_thuong_nvbh_raw":
                    raise RuntimeError(f"backend bật sai bản (ngày chốt {app.state.ctx.cutoff}, workflow {runner_name(app)})")
                board, raw_actual, extra, meta = _read(app, main, queries)
        finally:
            main.CUTOFF, main.WORKFLOW = saved[0], saved[1]
            if saved[2] is None:
                os.environ.pop("KPI_STORE", None)
            else:
                os.environ["KPI_STORE"] = saved[2]
    if isinstance(extra, dict):
        meta["files_sha256"][extra["source"]["rules_file"]] = extra["source"]["rules_sha256"]
    return board, extra, {**meta, "raw_actual": raw_actual}


def runner_name(app) -> str:
    return app.state.workflow.get("workflow", "")


def _read(app, main, queries) -> tuple[dict, dict, dict | str, dict]:
    """(bảng đội, số thực hiện thô, thưởng thêm, meta) từ backend đang bật."""
    ctx = app.state.ctx
    board = queries.team_board(ctx)
    raw_actual = tracking_view.raw_actuals(ctx)
    measures = tracking_view.raw_measures(ctx)
    meta = {"period": ctx.period, "cutoff": ctx.cutoff.isoformat(), "status_label": ctx.status_label_full,
            "files_sha256": dict(app.state.files_sha256), "data_version": app.state.data_version,
            "generated_at": datetime.now().strftime("%H:%M %d/%m/%Y"), "to_real": dict(ctx.to_real),
            "raw_path": main._workflow_files(app.state.workflow)[2],   # file đơn hàng workflow đọc (tên NV cho --trang / --ten-that)
            "kpi_ranks": tracking_view.kpi_ranks(measures)}
    return board, raw_actual, main._extra_bonus(app), meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ngay-chot", required=True, type=date.fromisoformat)
    parser.add_argument("--trang", help="file trang theo dõi của đội (saved_resource.html) để đánh dấu ô lệch")
    parser.add_argument("--ten-that", action="store_true", help="hiện tên thật (chỉ trên máy) thay mã che")
    parser.add_argument("--ra", default=str(REPO / "kpi_agent/data/trang_tracking.html"))
    args = parser.parse_args(argv)
    from kpi_agent.backend.tracking_page import TrackingPageError, render
    try:
        board, extra, meta = backend_numbers(args.ngay_chot)
        if isinstance(extra, str):
            raise TrackingPageError(f"thưởng thêm không tính được — {extra}")
        raw_path = meta["raw_path"]
        reasons = tracking_view.known_reasons(meta["period"])
        deviations = tracking_view.page_deviations(args.trang, raw_path, meta["raw_actual"], extra, meta["to_real"], reasons) if args.trang else {}
        names = None
        if args.ten_that:
            from kpi_agent.core.load_raw import raw_employee_names
            real_names = raw_employee_names(str(raw_path))
            names = {masked: real_names.get(real, masked) for masked, real in meta["to_real"].items()}
        page = render(board, extra, meta, deviations, names, meta["kpi_ranks"])
    except (TrackingPageError, ValueError, RuntimeError, KeyError, OSError) as error:   # OSError: file trang / RAW không mở được
        print(f"KHÔNG sinh trang — {error}", file=sys.stderr)
        return 1
    Path(args.ra).write_text(page, encoding="utf-8")
    print(f"đã ghi {args.ra} · {len(board['employees'])} NV · 8 KPI {board['team_total']:,} đ · thưởng thêm {extra['team_total']:,} đ · ô lệch trang {len(deviations)}"
          .replace(",", "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
