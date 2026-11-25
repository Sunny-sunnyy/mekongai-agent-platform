# Mẫu biến môi trường cho hệ thống Capitol (bàn giao sạch)

Thư mục này chứa đủ **16 file `.env.example`** tương ứng với cấu hình chạy bộ Capitol, cộng thêm mẫu cho các file bí mật nội bộ đặt trong `CAPITOL_STATE_DIR` (mặc định `~/.local/share/capitol-platform`).

> **Nguyên tắc bảo mật:** Không bao giờ commit file `.env` hoặc `*.khoa.env` chứa giá trị thật lên Git. Quyền file chứa khoá phải là `600` (`chmod 600`).

## 1. Phân loại 16 file trong `env/`

### Nhóm A — Cấu hình không chứa khoá thật (chỉ chép bỏ đuôi `.example`)
1. `agentic-backend.env.example` → `agentic-backend.env`
2. `platform-api.env.example` → `platform-api.env`
3. `temporal.env.example` → `temporal.env`
4. `mcp.env.example` → `mcp.env`

### Nhóm B — Khoá nội bộ tự sinh (script `scripts_sach/khoi_tao_lan_dau.sh` tự tạo ngẫu nhiên, `chmod 600`)
5. `cong_18300.khoa.env.example` → `cong_18300.khoa.env` (`CONG_18300_MA`)
6. `kpi.khoa.env.example` → `kpi.khoa.env` (`KPI_SYS_KEY` = `CHAY_THU_KIEM_CODE_KHOA`)
7. `mcp_shop.khoa.env.example` → `mcp_shop.khoa.env` (`SHOP_MCP_KEY`)
8. `minio.khoa.env.example` → `minio.khoa.env` (`MINIO_ROOT_USER`/`PASSWORD` = `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`)

### Nhóm C — API key dịch vụ ngoài (người nhận tự điền, `chmod 600`)
> **Mặc định chạy `gpt-6-luna` qua OpenAI API:** `OPENAI_API_KEY` là **bắt buộc**; thông tin AWS Bedrock (`bedrock-046-creds.json`) là **tuỳ chọn** (chỉ cần nếu chuyển cấu hình nóng sang Claude 5.5).

9. `agentic-backend.khoa.env.example` → `agentic-backend.khoa.env` (`OPENAI_API_KEY` — **bắt buộc**, tuỳ chọn `SERPER_API_KEY`)
10. `cong_ai.khoa.env.example` → `cong_ai.khoa.env` (`OPENAI_API_KEY` — **bắt buộc**, đồng bộ từ `agentic-backend.khoa.env`)
11. `e2b.khoa.env.example` → `e2b.khoa.env` (`CHAY_THU_E2B_KHOA`, tuỳ chọn nếu dùng khối chạy code/đọc Office từ xa)
12. `eval.khoa.env.example` → `eval.khoa.env` (`OPENAI_API_KEY` — **bắt buộc** khi dùng đánh giá, đúng 1 dòng)
13. `exa.khoa.env.example` → `exa.khoa.env` (`EXA_API_KEY`, tuỳ chọn)
14. `openrouter.khoa.env.example` → `openrouter.khoa.env` (`OPENROUTER_API_KEY`, tuỳ chọn)
15. `platform-api-openai.khoa.env.example` → `platform-api-openai.khoa.env` (`OPENAI_API_KEY` — **bắt buộc**)
16. `qdrant_svc.khoa.env.example` → `qdrant_svc.khoa.env` (`OPENAI_API_KEY`, chỉ dùng nếu bật `qdrant_svc`)

## 2. Các file trong `CAPITOL_STATE_DIR` (thư mục `state_dir_mau/`)

Mặc định `CAPITOL_STATE_DIR=${HOME}/.local/share/capitol-platform` (quyền `700`). Script `khoi_tao_lan_dau.sh` tự sinh các mã nội bộ sau:
- `rag_cong.env` (`RAG_MA`)
- `rag_nap.env` (`RAG_NAP_MA`)
- `tai_len_18402.env` (`TAI_LEN_MA`)
- `van_tay_18402.env` (`VAN_TAY_MA`)
- `xem_18403.env` (`XEM_MA`)
- `minio_eval.env` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
- `tran_ngay/<YYYY-MM-DD>.txt` (trần tiền USD trong ngày cho OpenAI/OpenRouter/E2B)
- `bedrock_046/bedrock-046-creds.json` (**tuỳ chọn** — STS token Amazon Bedrock, chỉ cần nếu chuyển cấu hình nóng `cau_hinh_mo_hinh/` từ mặc định `gpt-6-luna` sang Claude 5.5)
