// [ghi chú kỹ thuật] Tab "Dữ liệu KPI": tải 1 file Excel KPI lên backend KPI qua máy chủ trang (/api/kpi/tai-len) ⇒ hộp chuyển tiếp
// (127.0.0.1:18402) ⇒ kpi-backend. Trang KHÔNG giữ khoá; tên file KHÔNG gửi đi (chỉ byte file). Lỗi chỉ hiện ở đây — không vào lời nhắc Agent.
// [ghi chú kỹ thuật] + nút "Dùng bản này cho các lượt chạy" (banDuLieu.ts; máy chủ kiểm lại hết lúc chạy — ban_du_lieu.py) + RAW cũng bắt buộc ngày chốt,
// điền sẵn ngày chốt của bản đang chạy (nhóm phát triển xác nhận 16/11: thiếu ngày chốt ⇒ backend tự lấy ngày khác ⇒ ra bản khác dù cùng file).
import { useEffect, useState } from "react";
import { banDuLieu, useBanDuLieu } from "./banDuLieu";
import { GhepCot } from "./GhepCot";   // [ghi chú kỹ thuật] màn ghép cột cho file RAW bố cục khác mẫu

type KetQua = {
  data_version?: string; run_id?: number; period?: string; cutoff?: string; team_total?: number;
  da_co_san?: boolean; data_flags?: string[]; refused?: string; loi?: string;
};

const TRAN = 20 * 1024 * 1024;
const LOAI = [
  { v: "raw", ten: "RAW đơn hàng (số chốt)" },
  { v: "tracking", ten: "Tracking (sổ theo dõi giữa tháng)" },
  { v: "chi_tieu", ten: "Chỉ tiêu nhân viên" },
];

const CAN_NGAY_CHOT = new Set(["raw", "tracking"]);

export function TaiDuLieuKpi({ ngayChotDangChay, kyDangChay }: { ngayChotDangChay?: string; kyDangChay?: string }) {
  const [loai, setLoai] = useState("raw");
  const [ky, setKy] = useState("");   // [ghi chú kỹ thuật] không ghi cứng 2026-10 — điền từ kỳ của bản đang chạy (effect dưới), người dùng sửa được
  const [ngayChot, setNgayChot] = useState("");
  const [tep, setTep] = useState<File | null>(null);
  const [dang, setDang] = useState(false);
  const [kq, setKq] = useState<KetQua | null>(null);
  const [baoLoi, setBaoLoi] = useState("");
  const [canGhep, setCanGhep] = useState(false);   // [ghi chú kỹ thuật] lõi từ chối vì bố cục khác mẫu ⇒ mời ghép cột (không phải lỗi đỏ)
  const [moGhep, setMoGhep] = useState(false);
  const dangDung = useBanDuLieu();
  // điền sẵn một lần khi biết ngày chốt của bản đang chạy (người dùng vẫn sửa được)
  useEffect(() => { if (ngayChotDangChay && !ngayChot) setNgayChot(ngayChotDangChay); }, [ngayChotDangChay]);
  useEffect(() => { if (kyDangChay && /^\d{4}-\d{2}$/.test(kyDangChay) && !ky) setKy(kyDangChay); }, [kyDangChay]);   // eslint-disable-line react-hooks/exhaustive-deps   // eslint-disable-line react-hooks/exhaustive-deps

  const taiLen = async () => {
    setKq(null); setBaoLoi(""); setCanGhep(false);
    if (!tep) return setBaoLoi("Chưa chọn file.");
    if (!tep.name.toLowerCase().endsWith(".xlsx")) return setBaoLoi("Chỉ nhận file Excel .xlsx.");
    if (tep.size > TRAN) return setBaoLoi("File lớn hơn 20 MB — không tải được.");
    if (!/^\d{4}-\d{2}$/.test(ky)) return setBaoLoi("Kỳ phải có dạng năm-tháng (YYYY-MM).");
    if (CAN_NGAY_CHOT.has(loai) && !ngayChot) return setBaoLoi("File RAW / Tracking cần ngày chốt.");
    const q = new URLSearchParams({ loai, ky });
    if (ngayChot) q.set("ngay_chot", ngayChot);
    setDang(true);
    try {
      const r = await fetch("/api/kpi/tai-len?" + q.toString(), {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: await tep.arrayBuffer(),
      });
      const d = (await r.json()) as KetQua;
      // [ghi chú kỹ thuật] lõi trả 422 "… ghép cột trước (…bo-cuc ⇒ ghep-thu ⇒ ghep-xac-nhan)" cho file RAW bố cục chưa biết ⇒ mở lối vào màn ghép
      if (r.status === 422 && loai === "raw" && /ghép cột trước/.test(d.refused || "")) setCanGhep(true);
      else if (!r.ok || d.refused || d.loi) setBaoLoi(d.refused || d.loi || `Tải lên không thành công (mã ${r.status}).`);
      else setKq(d);
    } catch {
      setBaoLoi("Không gửi được file tới máy chủ trang.");
    } finally {
      setDang(false);
    }
  };

  return (
    <div>
      <div className="phu" style={{ marginBottom: 8 }}>
        Tải file Excel KPI lên để tính thưởng trên dữ liệu mới. File đi thẳng vào backend KPI (chỉ trên máy này), không qua kho file của Capitol.
      </div>
      <label className="phu">Loại file</label>
      <select value={loai} onChange={(e) => setLoai(e.target.value)} style={{ width: "100%", marginBottom: 6 }}>
        {LOAI.map((x) => <option key={x.v} value={x.v}>{x.ten}</option>)}
      </select>
      <label className="phu">Kỳ (năm-tháng)</label>
      <input className="tim" value={ky} onChange={(e) => setKy(e.target.value.trim())} placeholder="YYYY-MM" />
      <label className="phu">Ngày chốt {CAN_NGAY_CHOT.has(loai) ? "(bắt buộc)" : "(tuỳ chọn)"}</label>
      <input className="tim" type="date" value={ngayChot} onChange={(e) => setNgayChot(e.target.value)} />
      <div className="phu">Điền sẵn ngày chốt của bản đang chạy — tải file giữa tháng thì sửa lại cho đúng ngày của file.</div>
      <input type="file" accept=".xlsx" onChange={(e) => setTep(e.target.files?.[0] || null)} style={{ margin: "6px 0" }} />
      <button className="nut chinh" onClick={taiLen} disabled={dang}>{dang ? "Đang tải…" : "Tải lên"}</button>
      {baoLoi && <div className="phu" style={{ color: "var(--loi)", marginTop: 8 }}>{baoLoi}</div>}
      {canGhep && !moGhep && (
        <div className="canh-bao" style={{ marginTop: 8 }}>
          File này có bố cục khác mẫu máy đã biết (tên cột hoặc sheet khác) nên chưa tính được. Ghép cột một lần là dùng được.{" "}
          <button className="nut chinh" onClick={() => setMoGhep(true)}>Ghép cột cho file này</button>
        </div>
      )}
      {moGhep && tep && (
        <GhepCot tep={tep} ky={ky} ngayChot={ngayChot} onDong={() => setMoGhep(false)}
                 onLuu={() => { setMoGhep(false); setCanGhep(false); void taiLen(); }} />
      )}
      {kq && (
        <div className="the" style={{ marginTop: 8, cursor: "default" }}>
          <div className="h"><b>{kq.da_co_san ? "File này đã có sẵn" : "Đã nhận bản dữ liệu mới"}</b></div>
          <div className="phu">Kỳ {kq.period} · ngày chốt {kq.cutoff}</div>
          <div className="phu">Tổng lương kinh doanh cả đội (8 KPI, không gồm thưởng thêm): <b>{(kq.team_total ?? 0).toLocaleString("vi-VN")} đồng</b> (tạm tính)</div>
          <div className="phu so">Mã bản dữ liệu: {kq.data_version} · ngày chốt {kq.cutoff}</div>
          {kq.data_version && (dangDung?.dv === kq.data_version
            ? <div className="canh-bao">Đang dùng bản này cho các lượt chạy workflow KPI (nút Chạy + ô hỏi trợ lý). Tải lại trang ⇒ về bản đang chạy.
                {" "}<button className="nut" onClick={() => banDuLieu.dat(null)}>Bỏ chọn</button></div>
            : <div style={{ marginTop: 6 }}>
                <button className="nut chinh" onClick={() => banDuLieu.dat({ dv: kq.data_version!, period: kq.period, cutoff: kq.cutoff })}>Dùng bản này cho các lượt chạy</button>
                <div className="phu">Chưa bấm thì workflow KPI vẫn tính trên bản đang chạy. Bản khác ngày chốt với bản đang chạy sẽ bị máy chủ từ chối (chưa hỗ trợ).</div>
              </div>)}
          {(kq.data_flags || []).length > 0 && (
            <ul className="phu" style={{ paddingLeft: 16, margin: "6px 0 0" }}>{kq.data_flags!.map((f, i) => <li key={i}>{f}</li>)}</ul>
          )}
        </div>
      )}
    </div>
  );
}
