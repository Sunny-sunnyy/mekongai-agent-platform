// [ghi chú kỹ thuật] tách khỏi App.tsx để cửa sổ Trợ lý dùng chung (tránh import vòng)
// tên công cụ KPI ⇒ lời thường (chưa có trong bảng ⇒ hiện tên gốc)
export const TEN_CONG_CU: Record<string, string> = {
  // 11 công cụ của kpi-backend (/api/tools, đọc 12/11) — nhãn rút từ mô tả của chính công cụ; chưa có trong bảng thì hiện tên gốc
  get_team_bonus_table: "lấy bảng thưởng cả đội", get_employee_result: "lấy kết quả 8 KPI của một nhân viên", get_extra_bonus: "lấy thưởng thêm ngoài 8 KPI",
  get_mid_month_gap: "tính còn thiếu bao nhiêu để lên mốc", get_opportunities: "tìm ai có cơ hội lên bậc", explain_kpi: "giải thích một KPI của một nhân viên",
  simulate_kpi_change: "tính kịch bản “nếu…” cho một KPI", get_team_kpi_summary: "tổng hợp một KPI cho cả đội", compare_employees: "so hai nhân viên",
  get_data_issues: "xem cờ kiểm dữ liệu", get_data_provenance: "xem số lấy từ đâu", sql_query: "truy vấn dữ liệu", current_time: "xem giờ hiện tại",
  // [ghi chú kỹ thuật] 5 công cụ TÍNH bằng code của kpi-backend 1e7202d4 (nhãn rút từ mô tả /api/tools đọc 18/11): số lấy lại từ công cụ đọc, AI không gõ số
  calc_sum: "cộng các số bằng code", calc_difference: "trừ hai số bằng code", calc_percent_of: "tính tỉ lệ phần trăm bằng code",
  calc_group_total: "cộng theo nhóm bằng code", calc_period_change: "so hai kỳ bằng code",
};
