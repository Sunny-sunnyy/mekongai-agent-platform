# Bảng kê phần nặng bàn giao qua Google Drive công ty

Cập nhật & kiểm chứng SHA-256 lúc **19:49, 25/11/2026** (chạy `sha256sum -c SHA256SUMS` trên toàn bộ 7 file `.tar` trong `luu_image/`: **7 / 7 OK**, tổng dung lượng **7.049.789.056 byte ≈ 6,57 GiB**).

> **Nguyên tắc phân tách (theo quyết định bàn giao đã chốt):**
> - **Đẩy lên GitHub (`mekongai-rag`, `mekongai-agent-platform`):** code sạch, `Dockerfile` + `uv.lock`, bộ dữ liệu đầu vào nhẹ (7 file KPI tháng 10 + vài file mẫu RAG nhẹ), và tài liệu hướng dẫn `.md`. Tuyệt đối **0 khoá / `.env`**, **0 file `.tar` nặng**.
> - **Đẩy lên Google Drive công ty:** 7 file `.tar` image Docker gốc của Capitol (`luu_image/`), file `SHA256SUMS` + `BANG_IMAGE.md`, và phụ lục tài liệu có tên/thu nhập thật tách từ báo cáo kiến trúc (nếu có).
> - **Không nén đẩy lên Drive (kho mẫu gốc ~97 GB):** chỉ ghi hướng dẫn nguồn gốc từ Cloudflare R2 (bucket `capitol2`, thông tin xác thực S3/token trao đổi riêng qua kênh bảo mật công ty).

---

## 1. Bảy file `.tar` Docker image gốc (`luu_image/`)

| STT | Tên file `.tar` | Dung lượng (byte) | Dung lượng (~) | SHA-256 (đã kiểm `OK`) | Tag Docker trong file |
|---|---|---:|---:|---|---|
| 1 | `agentic-backend_8505e4dd93a4.tar` | 1.190.840.832 | 1,11 GiB | `08294094679cbeb00570556f22f763a579487f3700d285b91336305c03b0722c` | `capitol-local/agentic-backend:release-4.10.0` (và 2 tag ECR `archive-8505e4dd93a4`, `release-4.10.0-5091ce2`) |
| 2 | `platform-api_e337db773769.tar` | 604.240.384 | 576 MiB | `6395b5ea7353824bf175647ffe857bf7558af6a49fb58e78c10b8415dc7a6502` | `capitol-local/platform-api:release-1.82.0` (và 2 tag ECR `archive-e337db773769`, `release-1.82.0-3c2a9cf`) |
| 3 | `mcp-servers_d2644587fb97.tar` | 709.231.616 | 676 MiB | `999eb270bb7273fa27faa12b69fd3f777e376b6915bb154f01e254be095bfe0e` | `capitol-local/mcp-servers:release-3.16.0` (và 2 tag ECR `archive-d2644587fb97`, `release-3.16.0-76425c4`) |
| 4 | `mcp-fastapi_7fa52b49d93d.tar` | 752.471.552 | 718 MiB | `2b7dc23c56bb3a9fb67c40d39059846234966207548d95109f11130054e4d34e` | `capitol-local/mcp-fastapi:release-3.16.0` (và 2 tag ECR `archive-7fa52b49d93d`, `release-3.16.0-76425c4`) |
| 5 | `artifact-blob-store_4c0e60c32497.tar` | 356.791.296 | 340 MiB | `249663199bdadbfd1b2975e36236d252bcaec405d4f9b27cee800489874278e9` | `capitol-local/artifact-blob-store:release-1.0.2` (và 2 tag ECR `archive-4c0e60c32497`, `release-1.0.2-e4937b4`) |
| 6 | `eval-service_2bc7aa5adbcf.tar` | 539.805.696 | 515 MiB | `209fcbd98e698c05cf97763e32932c23b16f24f32281dea0c694c137d1aef09d` | `capitol-local/eval-service:release-0.18.4` (và 2 tag ECR `archive-2bc7aa5adbcf`, `release-0.18.4-5800297`) |
| 7 | `qdrant-svc_3fe3ffea6c0e.tar` | 2.896.407.552 | 2,70 GiB | `fadd7af854e1ee08091640f2060b69dbd1a33e73c0f11d4e5f09b396c4349c6c` | `capitol-local/qdrant-svc:release-0.32.0` (và 2 tag ECR `archive-3fe3ffea6c0e`, `release-0.32.0-f3fd6ce`) |
| **Tổng** | **7 file `.tar`** | **7.049.789.056** | **6,57 GiB** | **7 / 7 khớp `luu_image/SHA256SUMS`** | — |

Kèm theo cùng thư mục trên Google Drive:
- `luu_image/SHA256SUMS` (678 byte)
- `luu_image/BANG_IMAGE.md` (2.623 byte)

### Lệnh kiểm tra và nạp lại trên máy người nhận

```bash
# 1. Đặt toàn bộ 7 file .tar + SHA256SUMS vào một thư mục (ví dụ ./capitol_images/)
cd capitol_images
sha256sum -c SHA256SUMS
# Mong đợi: cả 7 dòng đều in ": OK"

# 2. Nạp từng image vào Docker cục bộ (tốn ~7-10 GB ổ đĩa)
for f in *.tar; do
  docker load -i "$f"
done

# 3. Kiểm tra lại tag đã nạp
docker images | grep capitol-local
```

---

## 2. Các phần image tự dựng & image công khai (KHÔNG cần đẩy Drive)

- **Image tự dựng (giữ `Dockerfile` + lockfile trên GitHub):** `kpi-backend`, `sandbox-py`, `mcp-exa` (trong `mekongai-agent-platform`) và `mekongai-rag` (trong `mekongai-rag`) — đồng nghiệp tự dựng bằng `docker compose build`.
- **Image công khai Docker Hub:** ghim tag trực tiếp trong `compose` (`postgres`, `redis`, `temporalio/auto-setup`, `minio/minio`, `qdrant/qdrant`, v.v.) — Docker tự tải khi `docker compose up -d`.

---

## 3. Tài liệu & dữ liệu khác liên quan Google Drive / R2

- **Phụ lục báo cáo kiến trúc có tên nhân viên & số tiền thật (nếu tách bản riêng):** theo kết quả rà soát tài liệu kiến trúc Word (cập nhật khi hoàn tất bản tách phụ lục), lưu chung thư mục Drive công ty với 7 file `.tar` trên, không đưa vào kho GitHub công khai/riêng tư ngoài quy ước.
- **Kho mẫu gốc Capitol (~97 GB):** KHÔNG nén đẩy Google Drive. Đồng nghiệp lấy trực tiếp từ Cloudflare R2 (bucket `capitol2`; thông tin xác thực S3/token trao đổi riêng qua kênh bảo mật nội bộ công ty).
