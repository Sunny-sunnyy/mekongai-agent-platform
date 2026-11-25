# MekongAI Agent Platform — Nền tảng Trợ lý AI chạy theo Quy trình & Trợ lý KPI

Kho mã nguồn bàn giao cho **nền tảng trợ lý AI chạy theo quy trình** (dựng lại trên nền Capitol AI, bổ sung giao diện kéo–thả + khung chat tiếng Việt, cổng kiểm soát chi phí AI, hộp chạy code cách ly, cổng kết nối tài liệu RAG và **Trợ lý tính thưởng KPI**).

- **Kho chính (nền tảng & Trợ lý KPI):** `https://github.com/hieumekongai/mekongai-agent-platform`
- **Kho liên kết (dịch vụ đọc & tìm tài liệu RAG mức 1):** `https://github.com/hieumekongai/mekongai-rag`

> **Lưu ý khi cài đặt từ máy trống:** Bộ tài liệu trong kho được viết cho **cả đồng nghiệp lẫn coding agent** (Claude Code, Gemini CLI, Codex…) để tự kiểm tra và dựng từng bước trên máy chưa cài sẵn gì (**Windows + WSL2 Ubuntu + Docker Desktop, RAM ≥ 16 GB**, kèm ghi chú cho Linux/macOS). Hãy cài và bật **kho `mekongai-rag` trước**, sau đó mới dựng kho `mekongai-agent-platform`.

---

## 1. Kiến trúc tổng quan

Hệ thống tách làm **4 lớp** giao tiếp qua mạng nội bộ Docker và `127.0.0.1`:

```
Trình duyệt người dùng (http://127.0.0.1:18400)
   │
   ▼
[1] Máy chủ giao diện & điều phối (Python HTTP + React/Vite)
   ├── Khung chat tiếng Việt (định tuyến câu hỏi vào đúng quy trình dựng sẵn)
   ├── Khung vẽ workflow (kéo–thả khối theo DAG — đồ thị một chiều không vòng)
   ├── Trang Dữ liệu (tải Excel KPI tháng, nạp tài liệu vào kho)
   └── Sổ chi phí & giữ chỗ hạn mức ngày
   │
   ├──▶ [2] Hộp gác cổng (Caddy :18300 / :18301 / :18233) ──▶ Nền tảng chạy quy trình Capitol
   │       ├── agentic-backend (API + Worker + Worker Temporal)
   │       ├── platform-api (hồ sơ tổ chức, danh mục khối) + DynamoDB Local
   │       ├── PostgreSQL 16 + Redis 7 + Temporal 1.4.1 + MinIO + OTEL
   │       └── Công cụ MCP (mcp-fastapi, mcp-servers, mcp-exa, mcp-shop)
   │
   ├──▶ [3] Cổng AI tập trung (capitol-cong-ai :8090 nội bộ)
   │       └── Đọc bảng định tuyến mô hình, giữ khoá API, ghi sổ từng lời gọi
   │       └── Mặc định chạy `gpt-6-luna` qua OpenAI API (tuỳ chọn chuyển sang Claude Sonnet 5.5 / Opus 5.5 qua AWS Bedrock) + OpenRouter (định tuyến khung chat)
   │
   ├──▶ [4] Các dịch vụ chuyên biệt
   │       ├── kpi_agent (đóng băng bản 252dc30a) + cổng chuyển tiếp tải file (:18402) + xem bảng (:18403)
   │       ├── Cổng RAG (:19453) ──▶ Dịch vụ mekongai-rag (:1945)
   │       └── Nơi chạy code AI viết: hộp kín trên máy (sandbox-py) hoặc máy ảo E2B
```

**Nguyên tắc cốt lõi:**
- **AI chọn việc và viết câu trả lời; chương trình quyết định và tính toán.** Mọi con số thưởng KPI do chương trình Python (`kpi_agent`) tính theo file luật và file dữ liệu; câu trả lời KPI chỉ hiển thị sau khi qua lớp kiểm số có đáp án.
- **Một lối vào, một lối ra.** Lệnh vào nền tảng quy trình đi qua hộp gác cổng; lời gọi mô hình AI đi qua Cổng AI để ghi sổ và kiểm soát chi phí.

---

## 2. Cấu trúc thư mục chính của kho bàn giao

| Thành phần | Nội dung chính |
|---|---|
| `README.md` | Tài liệu tổng quan và bản đồ dẫn đường nhanh (file này) |
| `docs/CAI_DAT_MAY_TRONG.md` | Hướng dẫn từng bước dựng từ máy trống (Google Drive 7 `.tar`, build `Dockerfile`, điền 16 `.env`, khởi tạo) |
| `docs/HUONG_DAN_SU_DUNG_VA_VAN_HANH.md` | Cách bật/tắt, dùng trang `18400`, Trợ lý KPI (`252dc30a`), cổng RAG `19453`, quy trình nhánh Git |
| `docs/GIOI_HAN_DA_BIET_VA_R2.md` | Giới hạn đã biết của bản 1, trạng thái kiểm tĩnh, nguồn dữ liệu thô Cloudflare R2 (`capitol2`) |
| `docs/BAO_CAO_HE_THONG.docx` | Tài liệu kiến trúc hệ thống bản Word (đã tách phụ lục tên thật & thu nhập sang Drive công ty) |
| `docs/HUONG_DAN_TACH_PHU_LUC_BAO_CAO_WORD.md` | Hướng dẫn tách và quản lý phần phụ lục nhạy cảm của báo cáo Word trên Google Drive |
| `kpi_agent/` | Mã nguồn Trợ lý tính thưởng KPI tự đứng được (đóng băng bản `252dc30a`), `pyproject.toml`, `scripts/with_env.sh` |
| `sample_data/thang_10/` | 7 file dữ liệu đầu vào KPI tháng 10/2026 (~16,4 MB) dùng để chạy thử ngay |
| `giao_dien/` | Máy chủ trang web (`may_chu.py`), giao diện React/Vite (`web/`), hộp gác cổng, sổ tiền |
| `cong_ai/` & `cau_hinh_mo_hinh/` | Cổng AI tập trung, bảng định tuyến mô hình, cấu hình mô hình khối & nơi chạy code |
| `sandbox/` & `va_e2b/` | Bộ chạy code cách ly trong hộp Docker nội bộ và tích hợp máy ảo E2B |
| `va_rag/` & `va_mot_o_chat/` | Cổng trung gian RAG (`19453`) nối sang `mekongai-rag` (`1945`), bộ định tuyến khung chat & đọc tài liệu |
| `env_mau/` & `docker_build/` | 16 file `.env.example` (+ `state_dir_mau/`), `Dockerfile` + lockfile (`kpi-backend`, `mcp-exa`, `sandbox-py`) |
| `scripts_sach/` | Bộ compose chuẩn và các script `khoi_tao_lan_dau.sh`, `bat_bo_sach.sh`, `tat_bo_sach.sh` có bước kiểm tự động |
| `luu_image/SHA256SUMS` | Mã băm SHA-256 đối chiếu 7 file `.tar` image gốc trên Google Drive (~6,57 GiB) |

---

## 3. Đường dẫn tài liệu nhanh

1. **Cài mới từ máy trống:** đọc tuần tự [docs/CAI_DAT_MAY_TRONG.md](docs/CAI_DAT_MAY_TRONG.md) (sau khi đã dựng xong kho `https://github.com/hieumekongai/mekongai-rag`).
2. **Bật hệ thống & sử dụng hằng ngày:** xem [docs/HUONG_DAN_SU_DUNG_VA_VAN_HANH.md](docs/HUONG_DAN_SU_DUNG_VA_VAN_HANH.md).
3. **Hiểu sâu kiến trúc & các lớp kiểm soát:** mở [docs/BAO_CAO_HE_THONG.docx](docs/BAO_CAO_HE_THONG.docx) (xem thêm ghi chú tách phụ lục bảo mật tại [docs/HUONG_DAN_TACH_PHU_LUC_BAO_CAO_WORD.md](docs/HUONG_DAN_TACH_PHU_LUC_BAO_CAO_WORD.md)).
4. **Kiểm tra giới hạn bản 1 & nguồn dữ liệu R2:** xem [docs/GIOI_HAN_DA_BIET_VA_R2.md](docs/GIOI_HAN_DA_BIET_VA_R2.md).
