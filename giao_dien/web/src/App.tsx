import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Markdown } from "./Markdown";
import { TEN_CONG_CU } from "./tenCongCu";
import { TroLyChat } from "./TroLyChat";         // [ghi chú kỹ thuật] tab chat với trợ lý dựng workflow
import { DuLieu } from "./DuLieu";   // [ghi chú kỹ thuật] trang "Dữ liệu" rộng cả màn hình — thay hai tab cũ "Dữ liệu KPI" / "Tài liệu" ở cột trái
import { ChonKho, NguonTaiLieu } from "./TaiLieu";   // [ghi chú kỹ thuật] kho tài liệu (app RAG mekongai)   // [ghi chú kỹ thuật] tab tải file Excel KPI
import { ChonDataset } from "./KhoBang";   // [ghi chú kỹ thuật] ô Dataset của khối hỏi kho bảng
import { banDuLieu, useBanDuLieu, useDaGui } from "./banDuLieu";   // [ghi chú kỹ thuật] chạy trên bản dữ liệu vừa tải
import { ChonFileNhan } from "./ChonFileNhan";   // [ghi chú kỹ thuật] nút Chọn file của khối Nhận file
import {
  ReactFlow, Background, BackgroundVariant, Controls, MiniMap, addEdge, useEdgesState, useNodesState, useReactFlow,
  type Connection, type Edge, type IsValidConnection, type OnNodesChange,
} from "@xyflow/react";
import {
  api, DANG_CHAY, giay, LUOT_VN, ms, ngayGio, usd, tenMoHinh, type MoHinhKhoi,
  type BlockDef, type CheckItem, type Cost, type DauVet, type Dem, type GoiKpi, type KetQuaKhoi, type TepLuot, type TruyVan, type ParamDef, type RunDetail, type RunRow, type WfRow, type Workflow, type KpiDungSan, type KpiKhoi, type ChayLaiXem } from "./api";
import { KhoiNode, CongCuNode, MAU_NHOM, kyHieu, oCongCuBat, tenCongCuNgan, CONG_CONG_CU, type KhoiRF, type CongCuRF } from "./KhoiNode";
import { tinh, TT_VN, tomTatRao, tachCauTraLoi, lyDoRaoChan, loiThuong, loiDanhSach, luotBiRaoChan, NHAN_CHUA_KIEM, tenKhoiHien, luotCoTuDuyet } from "./trangThai";

const nodeTypes = { khoi: KhoiNode, congCu: CongCuNode };   // [ghi chú kỹ thuật] congCu = thẻ công cụ ảo (chỉ vẽ)
// màu dây theo khối nguồn (đủ tương phản trên nền trắng; lặp lại khi > 9 nguồn)
const MAU_DAY = ["#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed", "#0891b2", "#65a30d", "#b91c1c", "#475569"];
type Ban = { id: string | null; name: string; description: string; version?: number; saved_at?: string };
type XacNhan = { tieuDe: string; noiDung: React.ReactNode; nut: string; nguyHiem?: boolean; lam: () => void };

const MAU_HOI_DL = /^Hỏi dữ liệu thử \d+/;
const TEN_DO = /^(ĐO — |Mẫu Capitol - )/;   // [người dùng 12/11] workflow / lượt do script đo tự lưu — ẩn mặc định
// (nhóm "Mẫu Capitol" đã gỡ 12/11 theo ý người dùng — tập trung KPI)
export default function App() {
  const rf = useReactFlow<KhoiRF, Edge>();
  const [blocks, setBlocks] = useState<BlockDef[]>([]);
  const theoLoai = useMemo(() => Object.fromEntries(blocks.map((b) => [b.type, b])), [blocks]);
  const [dsWf, setDsWf] = useState<WfRow[]>([]);
  const [dsLuot, setDsLuot] = useState<RunRow[]>([]);
  const [hienDo, setHienDo] = useState(false);
  // [ghi chú kỹ thuật] máy chủ đòi mã mở trang cho mọi /api — chưa có mã thì hiện một màn nhắc rõ thay vì hàng loạt lỗi nhỏ
  // nền sáng / tối: nhớ lựa chọn ở máy này; chưa chọn ⇒ theo cài đặt hệ điều hành
  const [toi, setToi] = useState<boolean>(() => {
    try { const v = localStorage.getItem("xuong_nen"); if (v) return v === "toi"; } catch { /* không đọc được ⇒ theo hệ điều hành */ }
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });
  useEffect(() => { document.documentElement.dataset.theme = toi ? "dark" : "light"; }, [toi]);
  const doiSangToi = () => setToi((x) => { try { localStorage.setItem("xuong_nen", x ? "sang" : "toi"); } catch { /* bỏ qua */ } return !x; });
  const [canMa, setCanMa] = useState(false);
  const [chatMo, setChatMo] = useState({ mo: false, to: false, gan: false });
  useEffect(() => { const h = () => setCanMa(true); window.addEventListener("xuong-can-ma", h); return () => window.removeEventListener("xuong-can-ma", h); }, []);
  // [ghi chú kỹ thuật] khối KPI dựng sẵn: dữ liệu + luật ở máy chủ (một nguồn với
  // script nhóm kỹ thuật); trang chỉ hiện. Khoá ô ở đây là HIỂN THỊ — máy chủ kiểm lại lúc Lưu / Chạy.
  const [kpi, setKpi] = useState<KpiDungSan | null>(null);
  useEffect(() => { api.kpiDungSan().then(setKpi).catch(() => {}); }, []);
  const [tabTrai, setTabTrai] = useState<"khoi" | "wf" | "luot">("khoi");
  const [trang, setTrang] = useState<"xuong" | "du_lieu">("xuong");   // [ghi chú kỹ thuật]
  // [10/11, A] lọc theo ngày + "xem thêm" (trước đây cứng 80 workflow / 60 lượt ⇒ phần cũ bị ẩn)
  const [ngayLoc, setNgayLoc] = useState("");
  const [soWf, setSoWf] = useState(50);
  const [soLuot, setSoLuot] = useState(50);
  const [dem, setDem] = useState<Dem | null>(null);
  const [tim, setTim] = useState("");

  const [ban, setBan] = useState<Ban>({ id: null, name: "Workflow mới", description: "" });
  const [nodes, setNodes, onNodesChange] = useNodesState<KhoiRF>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [daLuu, setDaLuu] = useState("");
  const [kiemDs, setKiemDs] = useState<CheckItem[]>([]);
  const [chon, setChon] = useState<string | null>(null);
  const [dungLai, setDungLai] = useState<Record<string, string>>({});   // [ghi chú kỹ thuật] khối dùng lại kết quả cũ ở lượt đang xem ⇒ phiên nguồn

  const [phien, setPhien] = useState<string | null>(null);
  const [luot, setLuot] = useState<RunDetail | null>(null);
  const [tien, setTien] = useState<Cost | null>(null);
  const [tienLoi, setTienLoi] = useState(false);   // [ghi chú kỹ thuật] lượt đã xong mà 4 lần hỏi tiền đều hỏng
  const [dongHo, setDongHo] = useState<number | null>(null); // null = mốc cuối (trực tiếp); số = đang phát lại / kéo
  const [phat, setPhat] = useState(false);
  const [toc, setToc] = useState(2);
  const [, setNhip] = useState(0);
  const [xn, setXn] = useState<XacNhan | null>(null);
  const [cheDo, setCheDo] = useState<"dung" | "ket_qua">("dung");   // màn hình dựng ↔ màn hình "Kết quả lượt chạy" (người dùng yêu cầu 04/11)
  const [bao, setBao] = useState<{ chu: string; loi?: boolean } | null>(null);
  const [ban_ron, setBanRon] = useState(false);

  const thongBao = (chu: string, loi = false) => { setBao({ chu, loi }); setTimeout(() => setBao(null), loi ? 7000 : 3500); };

  // ---------------- hình chung ⇄ React Flow
  const hinh = useCallback((): Workflow => ({
    id: ban.id, name: ban.name, description: ban.description,
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.loai, params: n.data.params, position: { x: Math.round(n.position.x), y: Math.round(n.position.y) } })),
    // from_port bắt buộc: chấm cổng ra luôn có id = tên cổng; chỉ tự điền khi khối nguồn có ĐÚNG 1 cổng ra (không đoán) — còn lại để trống, máy chủ báo lỗi
    edges: edges.map((e) => {
      const ra = theoLoai[nodes.find((n) => n.id === e.source)?.data.loai ?? ""]?.outputs ?? [];
      return { from_node: e.source, from_port: e.sourceHandle ?? (ra.length === 1 ? ra[0].name : ""), to_node: e.target, to_port: e.targetHandle! };
    }),
    ui: { labels: Object.fromEntries(nodes.map((n) => [n.id, n.data.label])) },
  }), [ban, nodes, edges, theoLoai]);
  const vanTay = (w: Workflow) => JSON.stringify({ n: w.name, d: w.description, k: w.nodes.map((n) => [n.id, n.type, w.ui?.labels?.[n.id] ?? "", n.params]), e: w.edges.map((e) => [e.from_node, e.from_port, e.to_node, e.to_port]) });
  const coNhap = daLuu !== "" ? vanTay(hinh()) !== daLuu : nodes.length > 0;

  const napHinh = useCallback((w: Workflow) => {
    setBan({ id: w.id ?? null, name: w.name, description: w.description || "", version: w.version, saved_at: w.saved_at });
    setNodes(w.nodes.map((n) => ({ id: n.id, type: "khoi", position: n.position, data: { label: w.ui?.labels?.[n.id] ?? n.type, loai: n.type, params: n.params } })));
    setEdges(w.edges.map((e) => ({ id: `e-${e.from_node}-${e.from_port}-${e.to_node}-${e.to_port}`, source: e.from_node, target: e.to_node, sourceHandle: e.from_port, targetHandle: e.to_port })));
    setDaLuu(w.id ? vanTay(w) : "");
    setKiemDs([]); setChon(null);
    // hình chung (yêu cầu vận hành 09/11): dây công cụ chưa hỗ trợ ⇒ báo rõ, không bỏ lặng
    if (w.unsupported_edges) thongBao(`Workflow có ${w.unsupported_edges} dây công cụ (cắm vào cổng Tools) — trang chưa vẽ / sửa được; LƯU từ trang này sẽ BỎ các dây đó.`, true);
    setTimeout(() => rf.fitView({ padding: 0.2, duration: 300 }), 50);
  }, [rf, setNodes, setEdges]);

  // ---------------- tải ban đầu + hỏi định kỳ
  useEffect(() => {
    api.blocks().then(setBlocks).catch((e) => thongBao("Không tải được danh mục khối: " + e.message, true));
  }, []);
  useEffect(() => { api.workflows(ngayLoc, soWf).then(setDsWf).catch(() => {}); api.dem().then(setDem).catch(() => {}); }, [ngayLoc, soWf]);
  useEffect(() => {
    const tai = () => { api.runs(ngayLoc, soLuot).then(setDsLuot).catch(() => {}); api.dem().then(setDem).catch(() => {}); };
    tai();
    const h = setInterval(tai, 2500);
    return () => clearInterval(h);
  }, [ngayLoc, soLuot]);
  const lamMoiDsWf = () => api.workflows(ngayLoc, soWf).then(setDsWf).catch(() => {});
  const tongLoc = (k: "wf" | "luot") => (dem ? (ngayLoc ? dem.ngay.find((d) => d.ngay === ngayLoc)?.[k] ?? 0 : dem[k]) : null);

  // [ghi chú kỹ thuật] gắn / thả cửa sổ Trợ lý ⇒ khung vẽ đổi bề rộng ⇒ canh "vừa khung" lại (sau khi bố cục đổi xong)
  const ganChat = chatMo.mo && chatMo.gan;
  useEffect(() => { const h = setTimeout(() => rf.fitView({ padding: 0.2, duration: 300 }), 120); return () => clearTimeout(h); }, [ganChat]);   // eslint-disable-line react-hooks/exhaustive-deps
  // [ghi chú kỹ thuật] bước ĐANG diễn ra của lượt đang theo dõi, bằng lời thường, cho cửa sổ Trợ lý (chế độ Hỏi KPI). Đọc từ sự kiện trực tiếp sẵn có.
  const buocLuot = useMemo(() => {
    if (!luot || !DANG_CHAY.has(luot.luot.tt)) return null;
    const ten = (id: string | null | undefined) => nodes.find((n) => n.id === id)?.data.label || "khối";
    for (let i = luot.su_kien.length - 1; i >= 0 && i >= luot.su_kien.length - 400; i--) {
      const e = luot.su_kien[i];
      if (e.t !== "processing") { if (e.t === "begin") return `${ten(e.n)} bắt đầu`; continue; }
      if (e.k === "tool_call") {
        let t = "";
        try { t = String((JSON.parse(e.d || "{}") as { tool_name?: string }).tool_name || ""); } catch { /* bỏ qua */ }
        return `${ten(e.n)} đang gọi công cụ: ${TEN_CONG_CU[t] || t || "(không rõ tên)"}`;
      }
      if (e.k === "tool_result") return `${ten(e.n)} đã nhận kết quả công cụ, đang viết câu trả lời`;
      if (e.k === "agent_message") return `${ten(e.n)} đang viết câu trả lời`;
      if (e.k === "update") {
        const loai = nodes.find((n) => n.id === e.n)?.data.loai;
        return loai === "guardrails_node" ? `${ten(e.n)}: đang kiểm số bằng code` : loai === "agent_node" ? `${ten(e.n)} đang nghĩ` : `${ten(e.n)} đang chạy`;
      }
    }
    return null;
  }, [luot, nodes]);
  const moWorkflow = async (id: string, giuLuot = false) => {
    const lam = async () => {
      try { napHinh(await api.workflow(id)); if (!giuLuot) { setPhien(null); setLuot(null); setTien(null); } }
      catch (e) { thongBao("Không mở được workflow: " + (e as Error).message, true); }
    };
    if (coNhap && ban.id !== id) setXn({ tieuDe: "Bỏ thay đổi chưa lưu?", noiDung: "Workflow đang mở có thay đổi chưa lưu. Mở workflow khác thì mất thay đổi đó.", nut: "Bỏ và mở", nguyHiem: true, lam });
    else await lam();
  };

  // [ghi chú kỹ thuật] moKetQua: bấm 1 lượt ĐÃ XONG ở tab "Lượt chạy" ⇒ mở thẳng màn kết quả;
  // lượt đang chạy ⇒ ở lại màn dựng (xem khối chạy dần). Gọi sau khi bấm Chạy giữ như cũ (không truyền moKetQua).
  useEffect(() => {   // [ghi chú kỹ thuật] /?luot=<mã phiên>&wf=<mã workflow> ⇒ mở thẳng lượt đó ở màn Kết quả; mã sai dạng ⇒ bỏ qua
    const q = new URLSearchParams(location.search), s = q.get("luot"), w = q.get("wf"), ma = /^[0-9a-f-]{36}$/;
    if (s && ma.test(s)) { history.replaceState(null, "", location.pathname); moLuot(s, w && ma.test(w) ? w : undefined, true); }
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const moLuot = async (s: string, wf?: string, moKetQua = false) => {
    if (wf && wf !== ban.id) await moWorkflow(wf, true);
    setPhien(s); setDongHo(null); setPhat(false); setTien(null);
    try {
      const r = await api.run(s);
      setLuot(r);
      if (moKetQua) setCheDo(DANG_CHAY.has(r.luot.tt) ? "dung" : "ket_qua");
    } catch (e) { thongBao("Không mở được lượt: " + (e as Error).message, true); }
  };

  // lượt đang chạy: lấy thêm sự kiện mỗi 0,7 giây (gối lùi 5 giây ở máy chủ — giờ ghi có lúc lùi; bỏ trùng theo id)
  const luotRef = useRef(luot); luotRef.current = luot;
  const dangHoi = useRef(false);   // [ghi chú kỹ thuật] chặn hỏi chồng: trả lời về muộn từng ghi đè danh sách đã gộp ⇒ hở một khúc sự kiện
  useEffect(() => {
    if (!phien) return;
    // lấy ĐỦ sự kiện (như F5) — dùng khi lượt vừa xong: sự kiện cuối (termination…, begin muộn) ghi SAU lúc trạng thái lượt đổi
    const layDu = async () => { try { const r = await api.run(phien); if (luotRef.current?.luot.phien === r.luot.phien) setLuot(r); } catch { /* bỏ qua */ } };
    const h = setInterval(async () => {
      const l = luotRef.current;
      if (!l || !DANG_CHAY.has(l.luot.tt) || dangHoi.current) return;
      dangHoi.current = true;
      const conTro = l.su_kien.reduce((m, e) => (e.c > m ? e.c : m), "");
      try {
        const r = await api.run(phien, conTro || undefined);
        const goc = luotRef.current ?? l;   // gộp vào danh sách MỚI NHẤT lúc nhận, không vào bản chụp lúc gửi
        const daCo = new Set(goc.su_kien.map((e) => e.id));
        const moi = r.su_kien.filter((e) => !daCo.has(e.id));
        const suKien = moi.length ? [...goc.su_kien, ...moi].sort((a, b) => ms(a.ts) - ms(b.ts)) : goc.su_kien;
        setLuot({ luot: r.luot, su_kien: suKien });
        if (!DANG_CHAY.has(r.luot.tt)) {
          thongBao(r.luot.tt === "SUCCESS" ? "Lượt chạy xong — thành công" : "Lượt chạy dừng — " + (LUOT_VN[r.luot.tt] || r.luot.tt), r.luot.tt !== "SUCCESS");
          layDu(); setTimeout(layDu, 12000);   // một lần ngay + một lần sau 12 giây (bắt sự kiện ghi muộn)
        }
      } catch { /* thử lại lần sau */ } finally { dangHoi.current = false; }
    }, 700);
    const nhip = setInterval(() => setNhip((x) => x + 1), 250); // đồng hồ chạy mượt
    return () => { clearInterval(h); clearInterval(nhip); };
  }, [phien]);
  // [ghi chú kỹ thuật] huy hiệu "dùng lại": hỏi máy chủ khi lượt đang xem đã kết thúc (lượt thường ⇒ danh sách rỗng; máy chủ cũ chưa có đường ⇒ bỏ qua)
  useEffect(() => {
    if (!phien || !luot || DANG_CHAY.has(luot.luot.tt)) { setDungLai({}); return; }
    api.chayLaiDungLai(phien).then((r) => setDungLai(Object.fromEntries(r.dung_lai.map((x) => [x.khoi, x.tu_phien])))).catch(() => setDungLai({}));
  }, [phien, luot?.luot.tt]);   // eslint-disable-line react-hooks/exhaustive-deps
  // tiền: lấy khi lượt đã xong (node_result ghi gom cuối lượt), và lại mỗi 3 giây khi đang chạy
  useEffect(() => {
    if (!phien || !luot) return;
    if (!DANG_CHAY.has(luot.luot.tt)) {   // [ghi chú kỹ thuật] lượt đã xong: thử lại 4 lần cách 2 giây rồi mới báo — trước: một lần hỏng là "đang tính…" mãi
      let bo = false, lan = 0;
      const thu = () => api.cost(phien).then((x) => { if (!bo) { setTien(x); setTienLoi(false); } })
        .catch(() => { if (bo) return; lan += 1; if (lan < 4) setTimeout(thu, 2000); else setTienLoi(true); });
      setTienLoi(false); thu();
      return () => { bo = true; };
    }
    const h = setInterval(() => api.cost(phien).then(setTien).catch(() => {}), 3000);
    return () => clearInterval(h);
  }, [phien, luot?.luot.tt]);

  // ---------------- trạng thái lượt ⇒ khối / dây
  const mocDau = luot?.su_kien[0] ? ms(luot.su_kien[0].ts) : NaN;
  const mocCuoi = luot ? (DANG_CHAY.has(luot.luot.tt) ? Date.now() : (luot.su_kien.length ? ms(luot.su_kien[luot.su_kien.length - 1].ts) : mocDau)) : NaN;
  const t = dongHo ?? mocCuoi;
  const hinhNay = useMemo(() => hinh(), [hinh]);
  const kq = useMemo(() => (luot ? tinh(luot.su_kien, nodes.map((n) => n.id), hinhNay.edges, t, luot.luot.tt) : null), [luot, nodes, hinhNay, t]);
  const raoChan = useMemo(() => luotBiRaoChan(kq?.st), [kq]);   // [ghi chú kỹ thuật] lượt đang xem bị khối Kiểm số chặn ⇒ chữ AI + lý do mang nhãn, gập
  const tienKhoi = useMemo(() => {
    const m: Record<string, { tien: number | null; ngam: boolean }> = {};
    for (const c of tien?.calls || []) {
      const x = (m[c.node] ??= { tien: 0, ngam: false });
      if (c.call_kind === "side") x.ngam = true;
      if (c.measurable && c.cost_usd != null) x.tien = (x.tien ?? 0) + c.cost_usd;
      else if (!c.measurable && x.tien === 0) x.tien = null;
    }
    return m;
  }, [tien]);
  const loiTheoKhoi = useMemo(() => {
    const m: Record<string, number> = {};
    for (const k of kiemDs) if (k.node && k.muc === "loi") m[k.node] = (m[k.node] || 0) + 1;
    return m;
  }, [kiemDs]);

  // người dùng 04/11 chiều: "lúc 2 đường nối, lúc 1 … dễ lộn" ⇒ mỗi khối NGUỒN một màu; dây mang màu + tên khối nguồn; ô nhận ghi số dây + chấm màu từng nguồn
  const mauNguon = useMemo(() => {
    const m: Record<string, string> = {};
    const nguon = new Set(edges.map((e) => e.source));
    nodes.filter((n) => nguon.has(n.id)).forEach((n, i) => { m[n.id] = MAU_DAY[i % MAU_DAY.length]; });
    return m;
  }, [nodes, edges]);
  const tenKhoi = useCallback((id: string) => nodes.find((n) => n.id === id)?.data.label || "?", [nodes]);
  const vaoTheoO = useMemo(() => {
    const m: Record<string, Record<string, { ten: string; mau: string }[]>> = {};
    for (const e of edges) ((m[e.target] ??= {})[e.targetHandle || ""] ??= []).push({ ten: tenKhoi(e.source), mau: mauNguon[e.source] });
    return m;
  }, [edges, tenKhoi, mauNguon]);

  // [Lần gắn 5 — mẫu "Sổ tài chính"] số thứ tự BƯỚC trên thẻ khối: thứ tự chạy theo dây (khối không có dây vào = bước 1; khối sau = lớn nhất của các
  // khối trước + 1). Đồ thị có vòng / khối rời ⇒ khối chưa xếp được không hiện số (không đoán).
  const sttKhoi = useMemo(() => {
    const vao: Record<string, string[]> = {};
    for (const e of edges) (vao[e.target] ??= []).push(e.source);
    const ra: Record<string, number> = {};
    for (let vong = 0; vong < nodes.length + 1; vong++) {
      let doi = false;
      for (const n of nodes) {
        if (ra[n.id]) continue;
        const truoc = [...new Set(vao[n.id] ?? [])].filter((id) => nodes.some((m) => m.id === id));
        if (truoc.every((id) => ra[id])) { ra[n.id] = truoc.length ? Math.max(...truoc.map((id) => ra[id])) + 1 : 1; doi = true; }
      }
      if (!doi) break;
    }
    return nodes.length > 1 ? ra : {};
  }, [nodes, edges]);
  const nodesVe = useMemo(() => nodes.map((n) => {
    const x = kq?.st[n.id];
    const b = theoLoai[n.data.loai];
    return {
      ...n, data: {
        ...n.data, block: b, tt: x, raoChan, dungLai: dungLai[n.id] ?? null, vao: vaoTheoO[n.id], dur: x?.bd ? (x.kt ?? t) - x.bd - (x.choMs || 0) : null,
        tien: tien ? (tienKhoi[n.id] ? tienKhoi[n.id].tien : undefined) : undefined, tienNgam: tienKhoi[n.id]?.ngam,
        doiChieu: tien?.checks?.[n.id] ? { tong: tien.checks[n.id].tong + tien.checks[n.id].khuon.length,
          khong: tien.checks[n.id].khong_nguon + tien.checks[n.id].khuon.filter((k) => !k.co_trong_ket_qua_cong_cu).length } : undefined,
        soLoi: loiTheoKhoi[n.id], canhBao: b && !b.runnable_here ? b.not_runnable_reason : undefined, stt: sttKhoi[n.id], soCongCu: oCongCuBat(b, n.data.params).length,
      },
    };
  }), [nodes, kq, theoLoai, t, tien, tienKhoi, loiTheoKhoi, vaoTheoO, sttKhoi, dungLai]);
  const edgesVe = useMemo(() => edges.map((e) => {
    const a = kq?.st[e.source]?.s, b = kq?.st[e.target]?.s;
    // trạng thái lượt chạy giữ bằng nét (chạy = gạch động, hỏng = chấm); màu dây = màu khối nguồn
    const lop = !kq ? "" : a === "xong" && (b === "chay" || b === "duyet") ? "dang-chuyen" : a === "xong" && b === "xong" ? "da-chuyen" : a === "loi" || b === "bo" ? "hong" : "";
    const mau = mauNguon[e.source];
    const ten = tenKhoi(e.source);
    return { ...e, className: lop, style: { stroke: mau, strokeWidth: 2.2 },
      label: "từ " + (ten.length > 18 ? ten.slice(0, 17) + "…" : ten), labelStyle: { fill: mau, fontWeight: 600 },
      labelBgPadding: [4, 2] as [number, number] };
  }), [edges, kq, mauNguon, tenKhoi]);

  // [ghi chú kỹ thuật] DÂY CÔNG CỤ HIỂN THỊ: mỗi ô tick công cụ đang bật ⇒ một thẻ nhỏ dưới khối + dây nét đứt vào cổng “Công cụ” ở đáy khối.
  // CHỈ Ở LỚP VẼ: không thêm vào state nodes / edges ⇒ hinh() (Lưu / Chạy / Kiểm / vân tay thay đổi) và máy chủ không bao giờ thấy thẻ / dây này.
  const congCuVe = useMemo(() => {
    const ns: CongCuRF[] = [], es: Edge[] = [];
    for (const n of nodes) {
      const bat = oCongCuBat(theoLoai[n.data.loai], n.data.params);
      const cao = n.measured?.height ?? 230;   // chưa đo xong (lần vẽ đầu) ⇒ cỡ thường gặp; đo xong React Flow báo lại và thẻ về đúng chỗ
      bat.forEach((p, k) => {
        const id = `cc::${n.id}::${p.name}`;
        // [ghi chú kỹ thuật] ô tool_kpi là CẢ BỘ công cụ KPI ⇒ ghi "Bộ tính KPI (N công cụ)" + danh sách lời thường khi rê chuột (trước: "Cụ · KPI")
        const boKpi = p.name === "tool_kpi";
        const ten = boKpi ? `Bộ tính KPI${kpi?.cong_cu?.length ? ` (${kpi.cong_cu.length} công cụ)` : ""}` : tenCongCuNgan(p.label);
        const ds = boKpi && kpi?.cong_cu?.length ? kpi.cong_cu.map((c) => `• ${c.ten} — ${c.mo_ta}`).join("\n") : "";
        ns.push({ id, type: "congCu", position: { x: n.position.x + (k % 3) * 252, y: n.position.y + cao + 44 + Math.floor(k / 3) * 50 },
          data: { ten, khoi: n.id, o: p.name, goc: p.label, ds }, draggable: false, selectable: false, deletable: false, connectable: false, focusable: false,
          // thẻ ảo dựng lại mỗi lần vẽ ⇒ phải tự khai kích thước (bằng CSS .the-cong-cu); thiếu thì React Flow coi là "chưa đo" và ẩn thẻ + không vẽ dây (lần thử đầu 19/11)
          width: 240, height: 42, measured: { width: 240, height: 42 } });
        es.push({ id: `ecc::${n.id}::${p.name}`, source: id, sourceHandle: "ra", target: n.id, targetHandle: CONG_CONG_CU, className: "day-cong-cu", type: "smoothstep",
          selectable: false, deletable: false, focusable: false, reconnectable: false });
      });
    }
    return { ns, es };
  }, [nodes, theoLoai, kpi]);
  const nutVe = useMemo<(KhoiRF | CongCuRF)[]>(() => [...nodesVe, ...congCuVe.ns], [nodesVe, congCuVe]);
  const dayVe = useMemo(() => [...edgesVe, ...congCuVe.es], [edgesVe, congCuVe]);

  // ---------------- sửa khung vẽ
  const hopLe: IsValidConnection<Edge> = useCallback((c) => {
    if (!c.source || !c.target || c.source === c.target) return false;
    const tgt = nodes.find((n) => n.id === c.target);
    const vao = tgt && theoLoai[tgt.data.loai]?.inputs.find((i) => i.name === c.targetHandle);
    if (!vao) return false;
    return !edges.some((e) => e.source === c.source && e.target === c.target && e.targetHandle === c.targetHandle);
  }, [nodes, edges, theoLoai]);
  const onConnect = useCallback((c: Connection) => setEdges((es) => addEdge({ ...c, id: `e-${c.source}-${c.target}-${c.targetHandle}` }, es)), [setEdges]);

  const themKhoi = (loai: string, pos?: { x: number; y: number }) => {
    const b = theoLoai[loai];
    if (!b) return;
    const params: Record<string, unknown> = {};
    const m = b.params.find((p) => p.name === "model");
    if (m) params.model = m.default;
    const id = crypto.randomUUID();
    const p = pos || rf.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 - 60 });
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), { id, type: "khoi", position: p, selected: true, data: { label: b.label, loai, params } }]);
    setChon(id);
  };
  const onDrop = (ev: React.DragEvent) => {
    ev.preventDefault();
    const loai = ev.dataTransfer.getData("application/x-khoi");
    if (loai) themKhoi(loai, rf.screenToFlowPosition({ x: ev.clientX - 110, y: ev.clientY - 30 }));
  };
  const BAT = (v: unknown) => v === true || (typeof v === "string" && ["true", "1", "yes", "on"].includes(v.trim().toLowerCase()));
  // workflow đang mở có dấu hiệu KPI (cùng luật máy chủ dùng: Trợ lý bật công cụ KPI / Kiểm số chế độ code / Giám sát duyệt chế độ KPI)
  const laKpi = nodes.some((n) => (n.data.loai === "agent_node" && BAT(n.data.params.tool_kpi)) || (n.data.loai === "guardrails_node" && n.data.params.che_do_kiem === "code")
    || (n.data.loai === "human_intervention_node" && BAT(n.data.params.che_do_kpi)));
  const presetCua = (loai: string): KpiKhoi | undefined => (laKpi ? kpi?.khoi.find((k) => k.khoi_goc === loai) : undefined);
  const boKpiSan = () => {
    const lam = async () => {
      try { napHinh(await api.kpiBoSan()); setPhien(null); setLuot(null); setTien(null); thongBao("Đã thả Bộ KPI sẵn: sửa câu hỏi ở khối đầu rồi bấm “Lưu rồi chạy”."); }
      catch (e) { thongBao("Không dựng được Bộ KPI sẵn: " + (e as Error).message, true); }
    };
    if (coNhap) setXn({ tieuDe: "Bỏ thay đổi chưa lưu?", noiDung: "Thả Bộ KPI sẵn sẽ thay workflow đang mở.", nut: "Bỏ và thả bộ sẵn", nguyHiem: true, lam });
    else lam();
  };
  const themKhoiKpi = async (ma: string) => {
    try {
      const k = await api.kpiKhoi(ma);
      const id = crypto.randomUUID();
      const p = rf.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 - 60 });
      setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), { id, type: "khoi", position: p, selected: true, data: { label: k.label, loai: k.type, params: k.params } }]);
      setChon(id);
    } catch (e) { thongBao("Không thêm được khối dựng sẵn: " + (e as Error).message, true); }
  };
  const dungMau = (id: string, tenMau: string) => {
    const lam = async () => {
      try {
        const w = await api.workflow(id);
        napHinh({ ...w, id: null, version: undefined, saved_at: undefined, name: tenMau.replace(/^(KPI mẫu|Hỏi dữ liệu thử) \d+ — /, "") + " — bản của tôi" });
        setPhien(null); setLuot(null); setTien(null);
        thongBao("Đã chép mẫu thành bản mới của bạn: sửa câu hỏi nếu cần rồi bấm “Lưu rồi chạy”.");
      } catch (e) { thongBao("Không mở được mẫu: " + (e as Error).message, true); }
    };
    if (coNhap) setXn({ tieuDe: "Bỏ thay đổi chưa lưu?", noiDung: "Dùng mẫu sẽ thay workflow đang mở.", nut: "Bỏ và dùng mẫu", nguyHiem: true, lam });
    else lam();
  };
  const suaKhoi = (id: string, f: (d: KhoiRF["data"]) => KhoiRF["data"]) => setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: f(n.data) } : n)));

  const moi = () => {
    const lam = () => { napHinh({ id: null, name: "Workflow mới", nodes: [], edges: [] }); setPhien(null); setLuot(null); setTien(null); };
    if (coNhap) setXn({ tieuDe: "Bỏ thay đổi chưa lưu?", noiDung: "Tạo workflow mới thì mất thay đổi chưa lưu.", nut: "Bỏ và tạo mới", nguyHiem: true, lam });
    else lam();
  };

  // ---------------- Kiểm / Lưu / Chạy / Duyệt
  const kiemNgay = async () => {
    try {
      const r = await api.check(hinh());
      setKiemDs(r.kiem);
      const nLoi = r.kiem.filter((x) => x.muc === "loi").length;
      thongBao(nLoi ? `Kiểm: ${nLoi} lỗi — xem danh sách bên dưới` : r.kiem.length ? `Kiểm sạch, ${r.kiem.length} cảnh báo` : "Kiểm sạch", nLoi > 0);
      return nLoi === 0;
    } catch (e) { thongBao("Kiểm lỗi: " + (e as Error).message, true); return false; }
  };
  const luuNgay = async (): Promise<string | null> => {
    if (!(await kiemNgay())) return null;
    setBanRon(true);
    try {
      const w = hinh();
      const r = await api.save(w);
      setBan((b) => ({ ...b, id: r.workflow_id, version: r.version, saved_at: new Date().toISOString() }));
      setDaLuu(vanTay({ ...w, id: r.workflow_id }));
      if (r.loi_dan_dien_lai) {
        try { napHinh(await api.workflow(r.workflow_id)); } catch { /* vẫn đã lưu — chỉ không nạp lại được */ }
        thongBao(`Đã lưu phiên bản ${r.version}. Lời dặn của Trợ lý KPI đã được cập nhật theo kỳ hiện hành.`);
      } else thongBao(`Đã lưu phiên bản ${r.version}`);
      lamMoiDsWf();
      return r.workflow_id;
    } catch (e) {
      const b = (e as { body?: { kiem?: CheckItem[]; chi_tiet?: unknown } }).body;
      if (b?.kiem) setKiemDs(b.kiem);
      thongBao("Chưa lưu: " + (e as Error).message, true);
      return null;
    } finally { setBanRon(false); }
  };
  const uocTien = () => {
    const cu = dsLuot.find((l) => l.wf === ban.id && l.tt === "SUCCESS");
    const soAI = nodes.filter((n) => n.data.loai === "agent_node").length;
    return { cu, soAI, uoc: soAI * 0.00025 };
  };
  const chayNgay = async () => {
    const chuaThu = nodes.filter((n) => theoLoai[n.data.loai] && !theoLoai[n.data.loai].runnable_here);
    const { soAI, uoc } = uocTien();
    setXn({
      tieuDe: coNhap ? "Lưu rồi chạy?" : "Chạy workflow?",
      noiDung: (
        <div>
          {banDuLieu.lay() && <p className="canh-bao" style={{ marginTop: 0 }}>Workflow KPI sẽ tính trên bản dữ liệu vừa tải <b>{banDuLieu.lay()!.dv.slice(0, 12)}</b> (ngày chốt {banDuLieu.lay()!.cutoff}) — workflow không phải KPI sẽ bị từ chối; bỏ chọn ở tab Dữ liệu KPI.</p>}
          <p style={{ marginTop: 0 }}>Chạy <b>{coNhap ? "bản sẽ lưu ngay bây giờ" : `phiên bản ${ban.version}`}</b> trên bộ chạy thử Capitol. Có <b>{soAI}</b> khối AI ⇒ <b>gọi AI thật</b>. Khối chạy Claude (mặc định của hệ thống) đi qua Amazon Bedrock và không tính vào trần tiền API; khối chạy mô hình khác hoặc khối chạy code thì tính trần — tốn tiền thật.</p>
          <p className="phu">Phần tính trần: 0 với khối AI chạy Claude; nếu khối AI được đặt chạy mô hình tính trần thì ước khoảng {usd(uoc)} (0,0002–0,0003 USD mỗi khối, theo các lượt đo cũ). Số thật hiện ở bảng tiền sau khi chạy.</p>
          {chuaThu.length > 0 && <div className="canh-bao">Có khối chưa chạy thử trên máy: {chuaThu.map((n) => n.data.label).join(", ")} — lượt chạy có thể lỗi.</div>}
        </div>
      ),
      nut: coNhap ? "Lưu rồi chạy" : "Chạy",
      lam: async () => {
        const id = coNhap || !ban.id ? await luuNgay() : ban.id;
        if (!id) return;
        setBanRon(true);
        try {
          const dv = banDuLieu.lay()?.dv ?? null;
          const r = await api.start(id, dv);
          banDuLieu.ghiLuot(r.session, dv);
          thongBao(`Đã bấm chạy phiên bản ${r.version} — đang theo dõi`);
          await moLuot(r.session);
          api.runs(ngayLoc, soLuot).then(setDsLuot).catch(() => {});
        } catch (e) { thongBao("Không chạy được: " + (e as Error).message, true); }
        finally { setBanRon(false); }
      },
    });
  };
  const duyet = (node: string, quyet: "dong_y" | "tu_choi") => setXn({
    tieuDe: quyet === "dong_y" ? "Duyệt và cho chạy tiếp?" : "Từ chối và dừng lượt?",
    noiDung: quyet === "dong_y" ? "Workflow chạy tiếp các khối sau với đúng dữ liệu khối duyệt nhận được." : "Lượt chạy sẽ dừng ở khối này; các khối sau không chạy.",
    nut: quyet === "dong_y" ? "Duyệt" : "Từ chối", nguyHiem: quyet === "tu_choi",
    lam: async () => {
      try { const r = await api.approve(phien!, node, quyet); thongBao(`${quyet === "dong_y" ? "Đã duyệt" : "Đã từ chối"} (HTTP ${r.http})`); }
      catch (e) { thongBao("Không gửi được: " + (e as Error).message, true); }
    },
  });

  // ---------------- phát lại
  useEffect(() => {
    if (!phat || !luot) return;
    let truoc: number | null = null, raf = 0;
    const buoc = (now: number) => {
      setDongHo((d) => {
        const moi = (d ?? mocDau - 300) + (truoc === null ? 0 : (now - truoc) * toc);
        if (moi >= mocCuoi) { setPhat(false); return null; }
        return moi;
      });
      truoc = now; raf = requestAnimationFrame(buoc);
    };
    raf = requestAnimationFrame(buoc);
    return () => cancelAnimationFrame(raf);
  }, [phat, toc, luot, mocDau, mocCuoi]);

  const khoiChon = nodes.find((n) => n.id === chon) || null;
  const bChon = khoiChon ? theoLoai[khoiChon.data.loai] : undefined;
  const xChon = khoiChon && kq ? kq.st[khoiChon.id] : undefined;
  const dsDanhMuc = blocks.filter((b) => !tim || (b.label + " " + b.description + " " + b.group).toLowerCase().includes(tim.toLowerCase()));
  const nhom = dsDanhMuc.reduce<Record<string, BlockDef[]>>((m, b) => ((m[b.group] ??= []).push(b), m), {});
  const tongMay = kq ? Object.values(kq.st).reduce((s, x) => s + (x.choMs || 0), 0) : 0;
  const tongLuot = kq?.batDau ? (kq.ketThuc ?? t) - kq.batDau : null;

  return (
    <div className={"app" + (chatMo.mo ? (chatMo.gan ? " chat-gan" : chatMo.to ? " chat-to" : " chat-mo") : "")}>
      {/* [Lần gắn 4] Trợ lý = cửa sổ nổi góc dưới phải (luôn gắn để lượt chat đang chờ không mất khi đóng cửa sổ) */}
      {!canMa && <TroLyChat onMo={(id) => moWorkflow(id)} onMoLuot={(s, wf) => moLuot(s, wf, true)} onDoi={lamMoiDsWf}
        onTheoLuot={async (s, wf) => { setCheDo("dung"); await moLuot(s, wf, false); }} onChonKhoi={(id) => { setCheDo("dung"); setChon(id); }}
        ky={kpi?.ky?.period} luotDangXem={phien} buocLuot={buocLuot} khoiDangChay={kq ? nodes.filter((n) => kq.st[n.id]?.s === "chay").map((n) => n.data.label) : []} onMoDong={(mo, to, gan) => setChatMo({ mo, to, gan })} />}
      {canMa && (
        <div className="man" role="alertdialog" aria-modal="true" aria-labelledby="can-ma-td">
          <div className="hop-xn">
            <h3 id="can-ma-td">Mở trang bằng đường dẫn có mã — chỉ cần làm một lần</h3>
            <p>Trang này chạy lượt tốn tiền AI nên chỉ nhận trình duyệt đã được mở bằng đường dẫn có mã.</p>
            <ol style={{ paddingLeft: 20, margin: "8px 0" }}>
              <li>Trên máy này, gõ trong cửa sổ WSL: <code>clip.exe &lt; {"$CAPITOL_STATE_DIR"}/mo_trang_{location.port || "18400"}.txt</code> (mặc định <code>~/.local/share/capitol-platform</code>) — lệnh chép đường dẫn có mã vào bộ nhớ tạm, không in ra màn hình (hoặc hỏi người bật máy chủ).</li>
              <li>Bấm vào thanh địa chỉ của trình duyệt này, Ctrl+V rồi Enter. (Mở bằng lệnh explorer.exe không được: phần mã sau dấu # bị bỏ.)</li>
            </ol>
            <p className="phu">Sau đó trình duyệt này nhớ trong 30 ngày: lần sau mở thẳng trang là vào, kể cả khi máy chủ khởi động lại.</p>
          </div>
        </div>
      )}
      {/* ===================== thanh trên ===================== */}
      <header className="tren">
        <BanDangDung />
        {/* [ghi chú kỹ thuật] menu chéo 3 trang: trang này + 2 màn của trang KPI mekongai (cổng 5183). Tab mới, không gửi referrer, không kèm mã.
            GIỚI HẠN: trang KPI có mã mở riêng ⇒ trình duyệt chưa từng mở trang đó bằng đường có mã sẽ gặp màn nhắc nhập mã của họ. */}
        <nav className="menu" aria-label="Các trang">
          <span className="logo serif" aria-current="page"><i aria-hidden="true" />Xưởng <em>workflow</em></span>
          <button className={"menu-nut" + (trang === "du_lieu" ? " on" : "")} onClick={() => setTrang(trang === "du_lieu" ? "xuong" : "du_lieu")} title="Tải số liệu KPI của tháng và tài liệu để hỏi đáp">Dữ liệu</button>
          <a href="http://127.0.0.1:5183/" target="_blank" rel="noopener noreferrer" title="Mở trang Trợ lý KPI (bảng thưởng, hỏi đáp) ở tab mới. Nếu trang đó nhắc nhập mã: mở bằng đường dẫn có mã của trang KPI một lần.">Trợ lý KPI</a>
          <a href="http://127.0.0.1:5183/?man=theo-doi" target="_blank" rel="noopener noreferrer" title="Mở màn Theo dõi của trang KPI ở tab mới. Nếu trang đó nhắc nhập mã: mở bằng đường dẫn có mã của trang KPI một lần.">Theo dõi</a>
        </nav>
        <input className="ten-wf" style={trang === "du_lieu" ? { display: "none" } : undefined} value={ban.name} aria-label="Tên workflow" onChange={(e) => setBan((b) => ({ ...b, name: e.target.value }))} />
        <span className="the-pb" style={trang === "du_lieu" ? { display: "none" } : undefined} title={ban.id ? `phiên bản ${ban.version ?? "?"} · lưu ${ngayGio(ban.saved_at)}` : undefined}>
          {ban.id ? `phiên bản ${ban.version ?? "?"} · lưu ${ngayGio(ban.saved_at)}` : "chưa lưu lần nào"}
          {coNhap && <> · <b className="nhap">có thay đổi chưa lưu</b></>}
        </span>
        <span className="cach" />
        {luot && (
          <div className="tien-tren so">
            <span className={"chip " + (dongHo !== null && t < mocCuoi ? "RUNNING" : luot.luot.tt)}>{LUOT_VN[luot.luot.tt] || luot.luot.tt}</span>{" "}
            <b>{giay(tongLuot)}</b>
            <div>{tien ? <>tiền {usd(tongTien(tien))}{tien.total.unmeasured_calls ? " + phần không đo được" : ""}{(tien.total.bedrock ?? 0) > 0 || (tien.calls.length === 0 && (tien.so?.usd_bedrock_tham_khao ?? 0) > 0) ? " (Claude: tham khảo, không tính trần)" : ""}</> : tienLoi ? "chưa lấy được tiền — tải lại trang" : "tiền: đang tính…"}</div>
          </div>
        )}
        {luot && <button className={"nut" + (cheDo === "dung" ? " chinh" : "")} style={cheDo === "dung" ? { fontWeight: 700 } : undefined}
          title={cheDo === "dung" ? "Xem sản phẩm cuối của lượt: câu trả lời, bảng, file tải về" : "Quay lại sơ đồ khối"}
          onClick={() => setCheDo(cheDo === "ket_qua" ? "dung" : "ket_qua")}>
          {cheDo === "ket_qua" ? "← Về màn hình dựng" : "Kết quả lượt chạy"}</button>}
        <button className="nut vuong" onClick={doiSangToi} aria-label={toi ? "Đổi sang nền sáng" : "Đổi sang nền tối"} title={toi ? "Đổi sang nền sáng" : "Đổi sang nền tối"}>{toi ? "☀" : "☾"}</button>
        {/* [ghi chú kỹ thuật] ở trang Dữ liệu không hiện các nút dựng workflow (người dùng thường tưởng phải bấm "Lưu rồi chạy" sau khi tải file) */}
        {trang !== "du_lieu" && <>
        <button className="nut" onClick={moi}>Mới</button>
        <button className="nut" onClick={kiemNgay} disabled={!nodes.length}>Kiểm</button>
        <button className="nut" onClick={luuNgay} disabled={!nodes.length || ban_ron || (!coNhap && !!ban.id)}>Lưu</button>
        <button className="nut chinh" onClick={chayNgay} disabled={!nodes.length || ban_ron}>{coNhap || !ban.id ? "Lưu rồi chạy" : "Chạy"}</button>
        </>}
      </header>

      {trang === "du_lieu" && <DuLieu kyDangChay={kpi?.ky?.period} onVe={() => setTrang("xuong")}
        onHoi={(cau) => window.dispatchEvent(new CustomEvent("tro-ly-dien-cau", { detail: cau }))}
        onHoiTaiLieu={(sha, ten) => window.dispatchEvent(new CustomEvent("tro-ly-mo-tai-lieu", { detail: { sha, ten } }))} />}
      <div className="giua" style={trang === "du_lieu" ? { display: "none" } : undefined}>
        {/* ===================== cột trái ===================== */}
        <aside className="trai">
          <div className="tabs">
            <button className={tabTrai === "khoi" ? "on" : ""} onClick={() => setTabTrai("khoi")}>Thêm khối</button>
            <button className={tabTrai === "wf" ? "on" : ""} onClick={() => { setTabTrai("wf"); lamMoiDsWf(); }}>Workflow</button>
            <button className={tabTrai === "luot" ? "on" : ""} onClick={() => setTabTrai("luot")}>Lượt chạy</button>
          </div>
          <div className="dl-dan">Tải số liệu KPI và tài liệu: <button className="lk" onClick={() => setTrang("du_lieu")}>mở trang Dữ liệu</button></div>
          <div className="cuon">
            {tabTrai === "khoi" && <>
              {kpi && (
                <div className="nhom kpi-san">
                  <h4>KPI dựng sẵn · {kpi.khoi.length} khối{kpi.ky?.period ? ` · kỳ ${kpi.ky.period}` : ""}</h4>
                  <button className="nut chinh bo-san" onClick={boKpiSan} disabled={!kpi.san_sang} title={kpi.bo_san.mo_ta}>＋ {kpi.bo_san.ten} — thả 5 khối đã nối</button>
                  <div className="phu" style={{ margin: "4px 0 6px" }}>{kpi.san_sang ? "Lời dặn và công cụ đã điền sẵn và khoá. Bạn chỉ gõ câu hỏi." : "Backend KPI chưa sẵn — thử lại sau vài giây."}</div>
                  {kpi.khoi.map((k) => (
                    <div key={k.ma} className="dm-khoi" onDoubleClick={() => themKhoiKpi(k.ma)} title="Bấm đúp để thêm khối này (đã điền sẵn, ô khoá)">
                      <div className="ic" style={{ background: MAU_NHOM[theoLoai[k.khoi_goc]?.group_key || ""] || "var(--g-tool)" }}>{theoLoai[k.khoi_goc] ? kyHieu(theoLoai[k.khoi_goc]) : "K"}</div>
                      <div style={{ minWidth: 0 }}><b>{k.ten}</b><span className="huy ok">dựng sẵn</span><p>{k.mo_ta}</p></div>
                    </div>
                  ))}
                </div>
              )}
              <input className="tim" placeholder="Tìm khối…" value={tim} onChange={(e) => setTim(e.target.value)} />
              <div className="phu" style={{ marginBottom: 6 }}>Kéo khối vào khung vẽ, hoặc bấm đúp để đặt giữa màn hình.</div>
              {Object.entries(nhom).map(([g, bs]) => (
                <div className="nhom" key={g}><h4>{g} · {bs.length}{g === "Công cụ" ? " khối riêng + 4 công cụ trong Agent AI" : ""}</h4>
                  {g === "Công cụ" && (
                    <div className="dm-khoi" style={{ cursor: "default", borderStyle: "dashed", background: "var(--nhan-nhat)" }} title="Công cụ gọi thẳng (vá 8) — không phải khối riêng">
                      <div className="ic" style={{ background: "var(--nhan)" }}>⚙</div>
                      <div style={{ minWidth: 0 }}>
                        <b>4 công cụ gọi thẳng</b><span className="huy ok">chạy được</span>
                        <p style={{ WebkitLineClamp: "unset" }}>Tra web (Serper) · đọc trang web · giờ hiện tại · tra đơn hàng. Không kéo ra riêng: bấm vào khối <b>Agent AI</b> ⇒ phần Cài đặt ⇒ bật ô “Công cụ: …”. AI tự quyết gọi công cụ nào, khi nào.</p>
                      </div>
                    </div>
                  )}
                  {bs.map((b) => (
                    <div key={b.type} className={"dm-khoi" + (b.runnable_here ? "" : " mo")} draggable
                      onDragStart={(e) => { e.dataTransfer.setData("application/x-khoi", b.type); e.dataTransfer.effectAllowed = "move"; }}
                      onDoubleClick={() => themKhoi(b.type)} title={b.not_runnable_reason || b.description}>
                      <div className="ic" style={{ background: MAU_NHOM[b.group_key] || "var(--g-tool)" }}>{kyHieu(b)}</div>
                      <div style={{ minWidth: 0 }}>
                        <b>{b.label}</b>{b.runnable_here ? <span className="huy ok">chạy được</span> : <span className="huy chua">chưa thử</span>}
                        <p>{b.description}</p>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </>}
            {(tabTrai === "wf" || tabTrai === "luot") && (() => {
              const k = tabTrai === "wf" ? "wf" : "luot";
              const dang = tabTrai === "wf" ? dsWf.length : dsLuot.length;
              const tong = tongLoc(k);
              return (
                <div className="phu" style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", marginBottom: 8 }}>
                  <select value={ngayLoc} onChange={(e) => { setNgayLoc(e.target.value); setSoWf(50); setSoLuot(50); }}>
                    <option value="">Tất cả ngày{dem ? ` (${dem[k]})` : ""}</option>
                    {(dem?.ngay ?? []).filter((d) => d[k] > 0).map((d) => (
                      <option key={d.ngay} value={d.ngay}>{d.ngay.split("-").reverse().slice(0, 2).join("/")} ({d[k]})</option>
                    ))}
                  </select>
                  <span>đang hiện {dang}{tong != null ? ` / ${tong}` : ""}</span>
                  {tong != null && dang < tong && (
                    <button onClick={() => (tabTrai === "wf" ? setSoWf(soWf + 50) : setSoLuot(soLuot + 50))}>Xem thêm</button>
                  )}
                </div>
              );
            })()}
            {tabTrai === "wf" && kpi && kpi.mau.some((x) => x.id) && (
              <div className="nhom kpi-san">
                <h4>Mẫu KPI · {kpi.mau.filter((x) => x.id).length} workflow làm sẵn</h4>
                {kpi.mau.filter((x) => x.id).map((x) => (
                  <div key={x.id!} className="the mau">
                    <div className="h"><b title={x.ten}>{x.ten.replace(/^KPI mẫu /, "")}</b></div>
                    {/^KPI mẫu 8\b/.test(x.ten) && <div className="canh-bao" style={{ fontSize: 12 }}>Đang chờ sửa khối kiểm số: câu hỏi về dữ liệu cần để ý đôi lúc bị chặn nhầm — chạy lại một lần.</div>}
                    {/^KPI mẫu 18\b/.test(x.ten) && <div className="canh-bao" style={{ fontSize: 12 }}>Câu trả lời có thể chưa liệt kê đủ các mốc thưởng — xem bảng mốc ở công văn / bảng thưởng.</div>}
                    <div className="phu">“{x.cau_hoi}”{x.ghi_chu ? ` — ${x.ghi_chu}` : ""}</div>
                    <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                      <button className="nut nho chinh" onClick={() => dungMau(x.id!, x.ten)}>Dùng mẫu này</button>
                      <button className="nut nho" onClick={() => moWorkflow(x.id!)}>Xem mẫu gốc</button>
                    </div>
                  </div>
                ))}
                <h4 style={{ marginTop: 14 }}>Workflow của tổ chức</h4>
              </div>
            )}
            {/* [ghi chú kỹ thuật] mẫu "Hỏi dữ liệu bằng lời" nhóm kỹ thuật (workflow tên "Hỏi dữ liệu thử N — …"): nhóm RIÊNG, tách khỏi Mẫu KPI, ghi rõ dữ liệu thử */}
            {tabTrai === "wf" && dsWf.some((w) => MAU_HOI_DL.test(w.ten)) && (
              <div className="nhom kpi-san thu">
                <h4>Hỏi dữ liệu (thử) · {dsWf.filter((w) => MAU_HOI_DL.test(w.ten)).length} workflow làm sẵn</h4>
                <div className="phu" style={{ marginBottom: 6 }}>Hỏi bằng lời, trợ lý tự viết câu truy vấn trên <b>dữ liệu thử</b> — không phải số tính thưởng.</div>
                {dsWf.filter((w) => MAU_HOI_DL.test(w.ten)).sort((a, b) => a.ten.localeCompare(b.ten, "vi", { numeric: true })).map((w) => (
                  <div key={w.id} className="the mau">
                    <div className="h"><b title={w.ten}>{w.ten.replace(/^Hỏi dữ liệu thử /, "")}</b></div>
                    <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                      <button className="nut nho chinh" onClick={() => dungMau(w.id, w.ten)}>Dùng mẫu này</button>
                      <button className="nut nho" onClick={() => moWorkflow(w.id)}>Xem mẫu gốc</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {tabTrai === "wf" && (dsWf.length ? dsWf.filter((w) => hienDo || !TEN_DO.test(w.ten)).map((w) => (
              <div key={w.id} className={"the" + (w.id === ban.id ? " on" : "")} onClick={() => moWorkflow(w.id)}>
                <div className="h"><b>{w.ten}</b></div>
                <div className="phu">{ngayGio(w.tao)} · {w.so_pb} phiên bản · {w.so_luot} lượt</div>
              </div>
            )) : <p className="phu">Chưa có workflow.</p>)}
            {(tabTrai === "luot" || tabTrai === "wf") && (() => {
              const an = (tabTrai === "luot" ? dsLuot : dsWf).filter((x) => TEN_DO.test(x.ten)).length;
              return an ? <button className="lk phu" style={{ display: "block", marginBottom: 8 }} onClick={() => setHienDo(!hienDo)}>
                {hienDo ? `Ẩn ${an} mục do script đo tạo` : `Hiện cả ${an} mục do script đo tạo (đang ẩn)`}</button> : null;
            })()}
            {tabTrai === "luot" && dsLuot.filter((l) => hienDo || !TEN_DO.test(l.ten)).map((l) => (
              <div key={l.phien} className={"the" + (l.phien === phien ? " on" : "")} onClick={() => moLuot(l.phien, l.wf, true)}>
                <div className="h"><b>{l.ten}</b><span className={"chip " + l.tt}>{LUOT_VN[l.tt] || l.tt}</span></div>
                <div className="phu so">{ngayGio(l.tao)}{l.xong && l.bd ? " · " + giay(ms(l.xong) - ms(l.bd)) : ""}</div>
                {l.loi && <div className="phu" style={{ color: "var(--loi)" }} title={l.loi.slice(0, 300)}>{loiDanhSach(l.loi)}</div>}
              </div>
            ))}
          </div>
        </aside>

        {/* ===================== khung vẽ ===================== */}
        <section className="khung" onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }} onDrop={onDrop}>
          <ReactFlow<KhoiRF | CongCuRF, Edge>
            nodes={nutVe} edges={dayVe} nodeTypes={nodeTypes}
            onNodesChange={onNodesChange as OnNodesChange<KhoiRF | CongCuRF>} onEdgesChange={onEdgesChange} onConnect={onConnect} isValidConnection={hopLe}
            onNodeClick={(_, n) => setChon(n.type === "congCu" ? n.data.khoi : n.id)} onPaneClick={() => setChon(null)}
            deleteKeyCode={["Delete", "Backspace"]} fitView minZoom={0.2} maxZoom={2} proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{ type: "smoothstep" }}
          >
            <Background variant={BackgroundVariant.Lines} gap={24} lineWidth={1} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(n) => (n.type === "congCu" ? "transparent" : MAU_NHOM[theoLoai[(n.data as KhoiRF["data"]).loai]?.group_key || ""] || "#8a94a6")} />
          </ReactFlow>
          {cheDo === "ket_qua" && luot && kq && (
            <ManKetQua key={luot.luot.phien} luot={luot} kq={kq} nodes={nodes} edges={edges} theoLoai={theoLoai} tien={tien} tongLuot={tongLuot} tongCho={tongMay}
              xemKhoi={(id) => { setCheDo("dung"); setChon(id); }} />
          )}
          {!nodes.length && <div className="khung-trong"><div>
            <b className="serif">Bắt đầu từ đâu?</b>
            {kpi ? <>Bấm <button className="lk" style={{ pointerEvents: "auto" }} onClick={boKpiSan}>Bộ KPI sẵn</button> để thả 5 khối đã nối,<br />hoặc mở tab <button className="lk" style={{ pointerEvents: "auto" }} onClick={() => { setTabTrai("wf"); lamMoiDsWf(); }}>Workflow</button> và chọn một “Mẫu KPI”.</>
              : <>Kéo một khối từ cột “Thêm khối” vào đây,<br />hoặc mở một workflow ở tab “Workflow”.</>}
          </div></div>}
        </section>

        {/* ===================== cột phải ===================== */}
        <aside className="phai"><div className="cuon" style={{ padding: 0 }}>
          {khoiChon && bChon ? (
            <BangKhoi khoi={khoiChon} b={bChon} x={xChon} edges={edges} nodes={nodes} tien={tien} sua={suaKhoi} preset={presetCua(khoiChon.data.loai)} kpi={kpi}
              coLuot={!!luot} raoChan={raoChan} dangChay={!!luot && DANG_CHAY.has(luot.luot.tt)} duyet={duyet} dur={nodesVe.find((n) => n.id === khoiChon.id)?.data.dur ?? null}
              chayLai={ban.id && phien && luot && !DANG_CHAY.has(luot.luot.tt) ? { wf: ban.id, phien, mo: (s) => moLuot(s, ban.id ?? undefined, false) } : null} />
          ) : (
            <>
              <div className="muc">
                <h3>Workflow</h3>
                <div className="o"><label>Mô tả</label><textarea value={ban.description} onChange={(e) => setBan((b) => ({ ...b, description: e.target.value }))} style={{ minHeight: 56 }} /></div>
                <div className="kv so"><span>Khối</span><span>{nodes.length}</span><span>Dây</span><span>{edges.length}</span><span>Phiên bản</span><span>{ban.version ?? "chưa lưu"}</span></div>
              </div>
              {luot && kq && (
                <div className="muc">
                  <h3>Lượt chạy đang xem</h3>
                  <div className="kv so">
                    <span>Kết quả</span><span><span className={"chip " + luot.luot.tt}>{LUOT_VN[luot.luot.tt] || luot.luot.tt}</span></span>
                    <span>Bắt đầu</span><span>{ngayGio(luot.luot.tao)}</span>
                    <span>Tổng thời gian</span><span>{giay(tongLuot)}</span>
                    <span>Máy chạy</span><span>{giay(tongLuot != null ? tongLuot - tongMay : null)}</span>
                    <span>Chờ người duyệt</span><span>{giay(tongMay)}</span>
                  </div>
                  {kq.loiLuot && <><div className="phu" style={{ marginTop: 8 }}>Vì sao lượt dừng:</div><div className="hop loi">{loiThuong(kq.loiLuot)}</div>
                    {loiThuong(kq.loiLuot) !== kq.loiLuot && <details><summary className="phu">Lỗi gốc (từ sự kiện của khối)</summary><div className="hop">{kq.loiLuot}</div></details>}</>}
                  {luot.luot.loi && (loiThuong(luot.luot.loi) !== luot.luot.loi
                    ? <>{!(kq.loiLuot && loiThuong(kq.loiLuot) === loiThuong(luot.luot.loi)) && <><div className="phu" style={{ marginTop: 6 }}>Tóm tắt:</div><div className="hop">{loiThuong(luot.luot.loi)}</div></>}
                        <details><summary className="phu">Chữ gốc hệ thống ghi cho lượt</summary><div className="hop">{luot.luot.loi}</div></details></>
                    : <><div className="phu" style={{ marginTop: 6 }}>Capitol báo cho người dùng:</div><div className="hop">{luot.luot.loi}</div></>)}
                </div>
              )}
              {luot && <BangTien loi={tienLoi} tien={tien} nodes={nodes} />}
              {!luot && <div className="muc phu">Bấm một khối để sửa cài đặt. Bấm “Chạy” để chạy thật — trang tự theo dõi lượt chạy, khối đổi trạng thái ngay trên khung vẽ.</div>}
            </>
          )}
        </div></aside>
      </div>

      {/* ===================== dưới: lỗi kiểm + dòng thời gian ===================== */}
      <footer className="duoi" style={trang === "du_lieu" ? { display: "none" } : undefined}>
        <div className="ds">
          <h3 style={{ margin: "2px 0 6px", fontSize: 13 }}>Kiểm {kiemDs.length ? `· ${kiemDs.filter((k) => k.muc === "loi").length} lỗi, ${kiemDs.filter((k) => k.muc !== "loi").length} cảnh báo` : ""}</h3>
          <div className="kiem-ds">
            {kiemDs.length ? kiemDs.map((k, i) => (
              <div key={i} className={k.muc} onClick={() => k.node && setChon(k.node)}>{k.muc === "loi" ? "✕ " : "! "}{k.message}{k.suggestion && <span className="phu"> — {k.suggestion}</span>}</div>
            )) : <span className="phu">Bấm “Kiểm” để kiểm trước khi lưu. Lớp kiểm này của trang; đường lưu của Capitol tự nó không chặn dây sai.</span>}
          </div>
        </div>
        <div className="tg">
          {luot && kq ? <>
            <div className="dk">
              <button className="nut nho" onClick={() => { if (phat) setPhat(false); else { if (dongHo === null) setDongHo(mocDau - 300); setPhat(true); } }} disabled={DANG_CHAY.has(luot.luot.tt)}>{phat ? "⏸ Dừng" : "▶ Phát lại"}</button>
              {[1, 2, 5, 20].map((x) => <button key={x} className={"nut nho" + (toc === x ? " chinh" : "")} onClick={() => setToc(x)}>×{x}</button>)}
              <input type="range" min={0} max={1000} value={mocCuoi > mocDau ? Math.round(((t - mocDau) / (mocCuoi - mocDau)) * 1000) : 1000}
                onChange={(e) => { setPhat(false); const v = +e.target.value; setDongHo(v >= 1000 ? null : mocDau + ((mocCuoi - mocDau) * v) / 1000); }} aria-label="Tua lượt chạy" />
              <span className="phu so">{giay(t - mocDau)} / {giay(mocCuoi - mocDau)}</span>
            </div>
            <div className="gantt">
              {[...nodes].sort((a, b) => (kq.st[a.id]?.bd ?? 9e15) - (kq.st[b.id]?.bd ?? 9e15)).map((n) => {
                const x = kq.st[n.id]; const tong = Math.max(1000, mocCuoi - mocDau);
                const l = x?.bd ? ((x.bd - mocDau) / tong) * 100 : 0, w = x?.bd ? (((x.kt ?? t) - x.bd) / tong) * 100 : 0;
                return [
                  <div key={n.id + "a"} className="nh" onClick={() => setChon(n.id)} style={{ cursor: "pointer" }}><span className={"chip " + x?.s}>{x ? TT_VN[x.s].split(" —")[0] : ""}</span> {tenKhoiHien(n.data.label, luotCoTuDuyet(kq?.st))}</div>,
                  <div key={n.id + "b"} className="ray">{x?.bd && <div className={"thanh " + x.s} style={{ left: l + "%", width: Math.max(0.4, w) + "%" }} />}</div>,
                  <div key={n.id + "c"} className="so">{x?.bd ? giay((x.kt ?? t) - x.bd) : ""}</div>,
                  <div key={n.id + "d"} className="so">{tienKhoi[n.id] ? (tienKhoi[n.id].tien === null ? "không đo" : usd(tienKhoi[n.id].tien)) : ""}</div>,
                ];
              })}
            </div>
          </> : <span className="phu">Chưa có lượt chạy để xem. Bấm “Chạy”, hoặc chọn một lượt ở tab “Lượt chạy” bên trái.</span>}
        </div>
      </footer>

      {xn && (
        <div className="man" onClick={() => setXn(null)}>
          <div className="hop-xn" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h3>{xn.tieuDe}</h3>
            <div>{xn.noiDung}</div>
            <div className="nut-hang">
              <button className="nut" onClick={() => setXn(null)}>Huỷ</button>
              <button className={"nut " + (xn.nguyHiem ? "do" : "chinh")} autoFocus onClick={() => { const f = xn.lam; setXn(null); f(); }}>{xn.nut}</button>
            </div>
          </div>
        </div>
      )}
      {bao && <div className={"thong-bao" + (bao.loi ? " loi" : "")} role="status">{bao.chu}</div>}
    </div>
  );
}

// ======================= "chạy lại từ khối này" (đối chiếu kỹ thuật) =======================
// Bước 1 XEM TRƯỚC (0 đồng): máy chủ tính lại khoá từng khối trên bản đã lưu, báo khối nào chạy mới / dùng lại / đã sửa so với lượt cũ.
// Bước 2 người bấm Chạy ⇒ máy chủ làm LẠI toàn bộ bước kiểm rồi mới chạy (không tin kết quả xem trước) ⇒ mở phiên MỚI như nút Chạy.
function ChayLaiTuKhoi(p: { khoi: string; ten: (id: string) => string; wf: string; phien: string; mo: (s: string) => void }) {
  const [xem, setXem] = useState<ChayLaiXem | null>(null);
  const [loi, setLoi] = useState<{ chu: string; ma?: number } | null>(null);
  const [mot, setMot] = useState(false);
  const [dang, setDang] = useState(false);
  useEffect(() => { setXem(null); setLoi(null); }, [p.khoi, p.phien]);
  const than = { workflow_id: p.wf, phien_nguon: p.phien, khoi: [p.khoi], single_step: mot };
  const baoLoi = (e: unknown) => { const x = e as Error & { status?: number }; setLoi({ chu: x.message, ma: x.status }); };
  const xemTruoc = async () => { setDang(true); setLoi(null); try { setXem(await api.chayLaiXemTruoc(than)); } catch (e) { setXem(null); baoLoi(e); } finally { setDang(false); } };
  const chay = async () => { setDang(true); setLoi(null); try { const r = await api.chayLai(than); setXem(null); p.mo(r.session); } catch (e) { baoLoi(e); } finally { setDang(false); } };
  const ds = (a: string[]) => a.map(p.ten).join(", ") || "—";
  return (
    <div className="muc">
      <h3>Chạy lại từ khối này</h3>
      <div className="phu">Khối này và các khối sau chạy mới; khối phía trước dùng lại kết quả của lượt đang xem (không tốn lại tiền) — nếu chúng chưa bị sửa.</div>
      <label className="phu" style={{ display: "block", marginTop: 6 }}><input type="checkbox" checked={mot} onChange={(e) => { setMot(e.target.checked); setXem(null); }} /> Chỉ chạy lại đúng khối này (khối sau giữ kết quả cũ)</label>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button className="nut nho" disabled={dang} onClick={xemTruoc}>{dang && !xem ? "Đang xem…" : "Xem trước (0 đồng)"}</button>
        {xem && <button className="nut chinh nho" disabled={dang} onClick={chay}>Chạy (giữ chỗ ≤ {xem.muc_toi_da.toFixed(4)} USD)</button>}
      </div>
      {xem && <div className="kv" style={{ marginTop: 8 }}>
        <span>Chạy mới</span><span>{ds(xem.chay_moi)}</span>
        <span>Dùng lại</span><span>{ds(xem.dung_lai)}</span>
      </div>}
      {xem && Object.keys(xem.lech).length > 0 && <div className="canh-bao" style={{ marginTop: 6 }}>{Object.entries(xem.lech).map(([k, v]) => <div key={k}>Khối {p.ten(k)}: {v} ⇒ chạy mới cả khối này.</div>)}</div>}
      {xem && xem.sau_dung_lai_cu.length > 0 && <div className="canh-bao" style={{ marginTop: 6 }}>Các khối sau vẫn dùng kết quả cũ ({ds(xem.sau_dung_lai_cu)}) — có thể không khớp khối vừa chạy lại.</div>}
      {loi && <div className="hop loi" style={{ marginTop: 6 }}>{loi.chu}
        {loi.ma === 409 && <div><button className="nut nho" style={{ marginTop: 6 }} onClick={xemTruoc}>Xem trước lại</button></div>}
        {loi.ma === 503 && /chạy lại cả workflow/i.test(loi.chu) && <div className="phu" style={{ marginTop: 4 }}>Dùng nút “Chạy” ở thanh trên để chạy lại cả workflow.</div>}
      </div>}
    </div>
  );
}

// ======================= bảng phải: một khối =======================
function BangKhoi(p: {
  khoi: KhoiRF; b: BlockDef; x?: ReturnType<typeof tinh>["st"][string]; edges: Edge[]; nodes: KhoiRF[]; tien: Cost | null;
  sua: (id: string, f: (d: KhoiRF["data"]) => KhoiRF["data"]) => void; coLuot: boolean; raoChan?: boolean; dangChay: boolean;
  duyet: (node: string, q: "dong_y" | "tu_choi") => void; dur: number | null;
  chayLai?: { wf: string; phien: string; mo: (s: string) => void } | null;   // [ghi chú kỹ thuật] lượt đang xem đã kết thúc + workflow đã lưu ⇒ hiện nút
  preset?: KpiKhoi; kpi?: KpiDungSan | null;   // [ghi chú kỹ thuật] khối thuộc workflow KPI ⇒ chỉ hiện ô được sửa; ô khoá chỉ để xem
}) {
  const { khoi, b, x } = p;
  const ps = p.preset;
  // [ghi chú kỹ thuật] mô hình THẬT của loại khối này theo file mô hình khối (đọc lại mỗi lần mở một khối); null = khối chạy theo ô model của nó
  const [mhk, setMhk] = useState<MoHinhKhoi | null>(null);
  useEffect(() => { let con = true; api.moHinhKhoi().then((d) => { if (con) setMhk(d); }).catch(() => { if (con) setMhk(null); }); return () => { con = false; }; }, [khoi.id]);
  const mhThat = mhk && mhk.khoi_doi_duoc.includes(b.type) ? (mhk.theo_khoi[b.type] || mhk.mac_dinh || null) : null;
  // [ghi chú kỹ thuật] khối KPI dựng sẵn: đầu thẻ dùng mô tả của khối dựng sẵn; 3 câu ghi đè cho đúng hành vi thật [code]
  const MO_TA_THE: Record<string, string> = {
    kpi_tro_ly: "AI đọc câu hỏi, gọi các công cụ tính thưởng của hệ thống rồi viết câu trả lời — không tự tính số. Trước khi trả, câu trả lời được kiểm số bằng code; sai thì AI được viết lại một lần.",
    kpi_kiem_so: "Kiểm số (AI kiểm, có đáp án): một AI kiểm được đưa nguyên kết quả công cụ của lượt này làm đáp án và so từng con số trong câu trả lời: đúng số, đúng người, đúng khoản. Không đạt hoặc không kiểm được ⇒ DỪNG cả lượt, khối sau không nhận gì. Bộ luật bằng code chạy kèm, chỉ ghi lại để đối chiếu. (Khối Trợ lý KPI đứng trước đã được viết lại một lần trước khi tới đây.)",
    kpi_duyet: "Dừng lại cho người duyệt xem bảng thưởng do phần tính dựng bằng code (không qua AI, không tốn lượt gọi AI). Duyệt ⇒ hệ thống xuất file Excel; từ chối hoặc hết giờ ⇒ lượt dừng, không có file.",
  };
  const moTaThe = ps ? (MO_TA_THE[ps.ma] || ps.mo_ta) : null;
  const ten = (id: string) => p.nodes.find((n) => n.id === id)?.data.label || id;
  const datO = (name: string, v: unknown) => p.sua(khoi.id, (d) => {
    const params = { ...d.params };
    if (v === "" || v === undefined) delete params[name]; else params[name] = v;
    return { ...d, params };
  });
  const coBan = b.params.filter((q) => !q.advanced), nangCao = b.params.filter((q) => q.advanced);
  const goi = (p.tien?.calls || []).filter((c) => c.node === khoi.id);
  const congCu = (p.tien?.tool_calls || []).filter((c) => c.node === khoi.id && c.tool !== "final_result");
  const dep = (s?: string | null) => { try { const j = JSON.parse(s || ""); return typeof j === "object" ? JSON.stringify(j, null, 2) : s; } catch { return s; } };
  const oForm = (q: ParamDef) => {
    const vao = p.edges.filter((e) => e.target === khoi.id && e.targetHandle === q.name);
    const v = khoi.data.params[q.name];
    const kind = (q.kind || "").toLowerCase();
    let input: React.ReactNode;
    if (b.type === "collections_node" && q.name === "datasets") {   // [ghi chú kỹ thuật] chọn dataset của tổ chức (máy chủ kiểm lại lúc Lưu / Chạy)
      input = <ChonDataset v={v} dat={(x) => datO(q.name, x)} />;
    } else if (q.name === "rag_collections") {   // [ghi chú kỹ thuật] chọn kho của tổ chức thay cho gõ tên (máy chủ kiểm lại lúc chạy)
      input = <ChonKho v={String(v ?? "")} dat={(s) => datO(q.name, s)} />;
    } else if (q.choices?.length) {
      input = (
        <select value={String(v ?? q.default ?? "")} onChange={(e) => datO(q.name, e.target.value)}>
          {q.choices.map((c) => typeof c === "string"
            ? <option key={c} value={c}>{c}</option>
            : <option key={c.value} value={c.value} disabled={!c.ok}>{c.value}{c.ok ? "" : ` — ${c.why}`}</option>)}
        </select>
      );
    } else if (kind.includes("multiline") || kind.includes("prompt") || kind.includes("code")) {
      input = <textarea value={String(v ?? "")} placeholder={q.placeholder || String(q.default ?? "")} onChange={(e) => datO(q.name, e.target.value)} />;
    } else if (kind.includes("toggle") || kind.includes("switch") || kind.includes("check") || kind.includes("bool")) {
      const bat = Boolean(v ?? (q.default === true || q.default === "True"));   // 04/11: trước luôn hiện chữ "bật" bất kể trạng thái
      input = <label style={{ fontWeight: 400 }}><input type="checkbox" checked={bat} onChange={(e) => datO(q.name, e.target.checked)} /> {bat ? "đang bật" : "đang tắt"}{v === undefined ? " (mặc định)" : ""}</label>;
    } else if (kind.includes("number") || kind.includes("slider") || kind.includes("int") || kind.includes("float")) {
      input = <input type="number" value={v === undefined ? "" : String(v)} placeholder={q.default != null ? String(q.default) : ""} onChange={(e) => datO(q.name, e.target.value === "" ? "" : Number(e.target.value))} />;
    } else {
      input = <input type="text" value={String(v ?? "")} placeholder={q.placeholder || (q.default != null ? String(q.default) : "")} onChange={(e) => datO(q.name, e.target.value)} />;
    }
    return (
      <div className="o" key={q.name}>
        <label title={(q as ParamDef & { label_goc?: string }).label_goc ? `Tên gốc: ${(q as ParamDef & { label_goc?: string }).label_goc}` : undefined}>{q.label}{q.required && <span className="bb"> *</span>}</label>
        {vao.length > 0 && <div className="day-vao">Đang nhận dây từ: {vao.map((e) => ten(e.source)).join(", ")}{vao.length > 1 ? " — nối theo thứ tự dây, phần rỗng bị bỏ" : ""}{v ? "; chữ gõ dưới đây đứng đầu" : ""}</div>}
        {(vao.length === 0 || q.bindable) && input}
        {q.name === "model" && mhThat && <div className="day-vao">Đang chạy thật: <b>{tenMoHinh(mhThat)}</b> — đặt trong file mô hình khối của hệ thống; ô ở trên không có tác dụng với loại khối này.</div>}
        {q.name === "max_tokens" && mhThat && /^claude-/.test(mhThat) && <div className="day-vao">Khối này đang chạy bằng Claude: số ở ô trên <b>không được gửi đi</b> — cổng AI luôn đặt mức tối đa mà tuyến mô hình cho phép (quyết định “không dùng trần đầu ra”).</div>}
        {q.name === "model" && mhk?.loi && <div className="day-vao" style={{ color: "var(--loi)" }}>{mhk.loi}</div>}
        {q.info && <div className="goi-y">{q.info}</div>}
      </div>
    );
  };
  return (
    <>
      <div className="muc">
        <div className="o" style={{ marginBottom: 6 }}><label>Tên khối</label><input type="text" value={khoi.data.label} onChange={(e) => p.sua(khoi.id, (d) => ({ ...d, label: e.target.value }))} /></div>
        {ps ? <div className="phu" title={b.type}>Khối KPI dựng sẵn · {b.label} · {b.group}</div>
          : <div className="phu">{b.label} · {b.group} · <code>{b.type}</code></div>}
        <div className="phu" style={{ marginTop: 4 }}>{moTaThe || (khoi.data.loai === "guardrails_node" && khoi.data.params.che_do_kiem === "code" ? MO_TA_THE.kpi_kiem_so : b.description)}</div>
        {!b.runnable_here && <div className="canh-bao">{b.not_runnable_reason}</div>}
      </div>
      {p.chayLai && <ChayLaiTuKhoi khoi={khoi.id} ten={ten} {...p.chayLai} />}
      {p.coLuot && x && (
        <div className="muc">
          <h3>Trong lượt đang xem <span className={"chip " + x.s}>{TT_VN[x.s]}</span></h3>
          <div className="kv so" style={{ marginBottom: 8 }}>
            <span>Máy chạy</span><span>{giay(p.dur)}{x.lech ? " (gần đúng — giờ ghi lệch)" : ""}</span>
            {x.choMs > 0 && <><span>Chờ người</span><span>{giay(x.choMs)}</span></>}
          </div>
          {x.s === "duyet" && (
            <>
              <div className="phu">{ps?.ma === "kpi_duyet" ? "Bảng thưởng do phần tính dựng bằng code (không phải chữ AI viết) — đọc rồi bấm Duyệt / Từ chối:" : "Bản tóm tắt AI viết cho người duyệt (khối sau vẫn nhận dữ liệu gốc):"}</div>
              <div className="hop">{x.xemDuyet || "—"}</div>
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button className="nut chinh" onClick={() => p.duyet(khoi.id, "dong_y")}>Duyệt</button>
                <button className="nut do" onClick={() => p.duyet(khoi.id, "tu_choi")}>Từ chối</button>
              </div>
            </>
          )}
          {p.tien?.inputs?.[khoi.id] && <><div className="phu" style={{ marginTop: 6 }}>AI nhận được trong lượt này (câu nhờ thật gửi mô hình = chữ gõ sẵn + phần nối qua dây):</div><div className="hop" style={{ maxHeight: 140 }}>{p.tien.inputs[khoi.id]}</div></>}
          {x.baoDauVao.length > 0 && <><div className="phu" style={{ marginTop: 6 }}>AI tự gọi công cụ “báo đầu vào có vấn đề” (luôn bật; có thể báo nhầm — đọc nội dung):</div><div className="hop">{x.baoDauVao.join("\n")}</div></>}
          {x.nghi && (p.raoChan && ps?.ma === "kpi_tro_ly"
            ? <details style={{ marginTop: 6 }}><summary className="phu"><b>{NHAN_CHUA_KIEM}</b> · phần AI nghĩ của lượt bị chặn; bấm để xem (chỉ để dò lỗi)</summary><div className="hop nghi">{x.nghi}</div></details>
            : <><div className="phu" style={{ marginTop: 6 }}>AI nghĩ (tóm tắt suy nghĩ do mô hình trả):</div><div className="hop nghi">{x.nghi}</div></>)}
          {x.s === "loi" ? (lyDoRaoChan(x.loi)
              ? <><div className="phu" style={{ marginTop: 6 }}>Bị CHẶN — câu trả lời của Trợ lý không qua bước kiểm số, lượt dừng tại đây (khối sau không nhận gì).</div>
                  <details style={{ marginTop: 6 }}><summary className="phu"><b>{NHAN_CHUA_KIEM}</b> · bấm để xem lý do bước kiểm đưa ra (con số nêu trong đó là số KHÔNG được chấp nhận)</summary><div className="hop loi">{lyDoRaoChan(x.loi)}</div></details></>
              : <><div className="hop loi" style={{ marginTop: 6 }}>{loiThuong(x.loi)}</div>
                  {loiThuong(x.loi) !== x.loi && <details><summary className="phu">Lỗi gốc</summary><div className="hop">{x.loi}</div></details>}</>)
            : x.s !== "duyet" && <><div className="phu" style={{ marginTop: 6 }}>{x.s === "chay" ? (ps && ps.ma !== "kpi_tro_ly" ? "Đang chạy…" : "AI đang viết…") : khoi.data.params.output_schema ? "Kết quả (đúng khuôn đầu ra)" : "Kết quả"}</div>
              {(() => {
                const cu = <div className="hop">{tomTatRao(x.ra) || dep(x.ra) || x.song || "—"}</div>;   // hành vi cũ — mọi khối ngoài bộ KPI
                if (!ps || x.s === "chay") return cu;
                // [ghi chú kỹ thuật] khối KPI dựng sẵn — chữ AI hiện qua bộ Markdown sẵn có (nút chữ React, không innerHTML)
                if (ps.ma === "kpi_duyet") return (
                  <>
                    <div className="hop">{x.s === "tc" ? "Lượt dừng ở bước duyệt (người duyệt bấm Từ chối hoặc lượt bị dừng từ ngoài) — không xuất file." : x.s === "xong" ? "Đã DUYỆT — file Excel (nếu có) tải ở “Kết quả lượt chạy”." : TT_VN[x.s]}{x.choMs > 0 ? ` Chờ người: ${giay(x.choMs)}.` : ""}</div>
                    {x.ra && <details style={{ marginTop: 6 }}><summary className="phu">Bảng người duyệt đã xem (do phần tính dựng)</summary><div className="hop"><Markdown chu={x.ra} onMo={khongMo} /></div></details>}
                  </>);
                if (ps.ma === "kpi_ket_qua") { const c = tachCauTraLoi(x.ra); return c ? <><div className="hop"><Markdown chu={c} onMo={khongMo} /></div><div className="phu" style={{ marginTop: 4 }}>Bảng số đầy đủ và file tải về: màn “Kết quả lượt chạy”.</div></> : cu; }
                if (ps.ma === "kpi_tro_ly" && x.ra) return p.raoChan
                  ? <details><summary className="phu"><b>{NHAN_CHUA_KIEM}</b> · câu này đã bị khối Kiểm số chặn; bấm để xem (chỉ để dò lỗi)</summary><div className="hop loi"><Markdown chu={x.ra} onMo={khongMo} /></div></details>
                  : <div className="hop"><Markdown chu={x.ra} onMo={khongMo} /></div>;
                return cu;
              })()}</>}
        </div>
      )}
      {p.coLuot && p.tien?.checks?.[khoi.id] && (() => {
        const c = p.tien!.checks![khoi.id];
        const NG: Record<string, string> = { cong_cu: "kết quả công cụ", dau_vao: "đầu vào khối nhận", loi_dan: "lời dặn (nguồn yếu)" };
        return (
          <div className="muc">
            <h3>Đối chiếu câu trả lời ↔ nguồn (bằng code)</h3>
            {c.tong + c.khuon.length === 0 ? <div className="phu">Đầu ra không có số / ngày / link / mã nào để đối chiếu.</div> : (
              <table className="bang"><thead><tr><th>Ý</th><th>Giá trị</th><th>Nguồn</th></tr></thead><tbody>
                {c.y.map((y, i) => <tr key={i} style={y.nguon ? {} : { color: "var(--loi)", fontWeight: 600 }}><td>{y.kieu}</td><td style={{ wordBreak: "break-all" }}>{y.gia_tri}</td><td>{y.nguon ? NG[y.nguon] : "KHÔNG THẤY"}</td></tr>)}
                {c.khuon.map((k, i) => <tr key={"k" + i} style={k.co_trong_ket_qua_cong_cu ? {} : { color: "var(--loi)", fontWeight: 600 }}><td>link khai ở “{k.truong}”</td><td style={{ wordBreak: "break-all" }}>{k.gia_tri}</td><td>{k.co_trong_ket_qua_cong_cu ? "kết quả tra web" : "KHÔNG có trong kết quả công cụ"}</td></tr>)}
              </tbody></table>
            )}
            <div className="phu" style={{ marginTop: 4 }}>Chỉ kiểm “có xuất hiện trong nguồn”, chưa kiểm gắn đúng đối tượng; số làm tròn (vd “6 triệu” cho 5.740.000) bị báo không khớp.</div>
          </div>
        );
      })()}
      {p.coLuot && congCu.length > 0 && (
        <div className="muc">
          <h3>Công cụ đã gọi ({congCu.length})</h3>
          {congCu.map((c, i) => (
            <div key={i} style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 13 }}><b>{c.tool}</b>{c.internal ? <span className="phu"> · công cụ nội bộ của Capitol</span> : ""}{c.retry ? <span style={{ color: "var(--loi)" }}> · bị bắt gọi lại</span> : ""}</div>
              <div className="phu">Tham số do AI điền:</div><div className="hop" style={{ maxHeight: 90 }}>{dep(c.args) || "—"}</div>
              <div className="phu">Kết quả trả cho AI (rút gọn):</div><div className="hop" style={{ maxHeight: 120 }}>{dep(c.result) ?? "— (chưa có)"}</div>
            </div>
          ))}
        </div>
      )}
      {p.coLuot && goi.length > 0 && (
        <div className="muc">
          <h3>Tiền của khối</h3>
          <table className="bang"><thead><tr><th>Lần gọi</th><th className="so">Vào</th><th className="so">Ra</th><th className="so">Tiền</th></tr></thead>
            <tbody>{goi.map((c, i) => (
              <tr key={i} className={c.call_kind === "side" ? "ngam" : ""}>
                <td>{c.call_kind === "side" ? "ngầm" : c.call_kind === "tool" ? "công cụ" : "chính"}{c.model ? ` · ${c.model}` : ""}{c.why && <div className="phu">{c.why}</div>}</td>
                <td className="so">{c.measurable ? c.tokens_in : "—"}</td><td className="so">{c.measurable ? c.tokens_out : "—"}</td>
                <td className="so">{c.measurable ? usd(c.cost_usd) : "không đo được"}</td>
              </tr>))}</tbody></table>
          {goi.some((c) => c.price_source) && <div className="phu" style={{ marginTop: 4 }}>Giá: {goi.find((c) => c.price_source)?.price_source} (đọc {goi.find((c) => c.price_read_at)?.price_read_at}).</div>}
          {goi.some((c) => c.mien_tran) && <div className="phu" style={{ marginTop: 4 }}>Claude qua Amazon Bedrock: tiền ở trên là giá tham khảo, KHÔNG tính vào trần tiền API (token đọc đệm tính như token thường nên số này dư).</div>}
        </div>
      )}
      <div className="muc">
        <h3>Cài đặt</h3>
        {khoi.data.loai === "file_input_node" && <ChonFileNhan daCo={!!khoi.data.params["file_data"]} onXong={(pb) => datO("file_data", pb)} />}
        {ps && (() => {
          const duoc = b.params.filter((q) => ps.sua_duoc[q.name]);
          const xem = ps.o_khoa.filter((f) => !ps.o_an.includes(f));
          const hien = (f: string) => { const v = khoi.data.params[f]; return v === undefined || v === "" ? "(mặc định)" : typeof v === "boolean" ? (v ? "bật" : "tắt") : String(v); };
          return (
            <>
              <div className="day-vao" style={{ marginBottom: 10 }}>Khối KPI dựng sẵn “{ps.ma === "kpi_kiem_so" ? "Kiểm số (AI kiểm, có đáp án)" : ps.ten}” — {ps.ma === "kpi_kiem_so" ? "một AI kiểm được đưa kết quả công cụ làm đáp án và so từng con số trong câu trả lời; không đạt hoặc không kiểm được là chặn cả câu. Bộ luật bằng code chạy kèm để đối chiếu." : ps.mo_ta}</div>
              {duoc.length ? duoc.map((q) => oForm({ ...q, label: ps.sua_duoc[q.name].nhan, advanced: false })) : <div className="phu" style={{ marginBottom: 8 }}>Khối này không có ô nào cần điền.</div>}
              {xem.includes("system_prompt") && (
                <details className="khoa"><summary>Xem lời dặn (đã điền sẵn, khoá)</summary>
                  <div className="hop">{p.kpi?.loi_dan || String(khoi.data.params.system_prompt ?? "")}</div></details>
              )}
              {xem.includes("tool_kpi") && (
                <details className="khoa"><summary>Xem công cụ ({p.kpi?.cong_cu.length ?? 0} công cụ KPI, khoá)</summary>
                  <div className="hop">{(p.kpi?.cong_cu ?? []).map((c) => <div key={c.ten}><b>{c.ten}</b> — {c.mo_ta}</div>)}</div></details>
              )}
              {xem.filter((f) => f !== "system_prompt" && f !== "tool_kpi").length > 0 && (
                <details className="khoa"><summary>Xem cài đặt đã khoá ({xem.filter((f) => f !== "system_prompt" && f !== "tool_kpi").length})</summary>
                  {/* [ghi chú kỹ thuật] nhãn TRÊN, giá trị DƯỚI (trước: lưới 2 cột, cột giá trị hẹp ⇒ chữ xuống dòng từng ký tự) */}
                  <div className="ds-khoa">{xem.filter((f) => f !== "system_prompt" && f !== "tool_kpi").map((f) => (
                    <div key={f} className="muc-khoa"><div className="nhan-khoa">{f === "max_tokens" ? "Giới hạn độ dài câu trả lời (max_tokens)" : (b.params.find((q) => q.name === f)?.label || f)}</div>
                      <div className="gt-khoa">{f === "model" && mhThat ? `${tenMoHinh(mhThat)} (đặt trong file mô hình khối, áp cho mọi khối loại này)`   /* [ghi chú kỹ thuật] KHÔNG in lại giá trị cũ của ô (gpt-6-luna / 4000) lên mặt người dùng */
                        : f === "max_tokens" && mhThat && /^claude-/.test(mhThat) ? "Không giới hạn riêng — luôn dùng mức tối đa của mô hình." 
                        : (ps.ghi_chu[f] || hien(f))}</div></div>))}</div>
                  {mhk?.loi && xem.includes("model") && <div className="phu" style={{ color: "var(--loi)" }}>{mhk.loi}</div>}</details>
              )}
              <div className="phu" style={{ marginTop: 8 }}>Các ô khoá do hệ thống điền; sửa tay sẽ bị chặn lúc Lưu / Chạy.</div>
            </>
          );
        })()}
        {!ps && coBan.map(oForm)}
        {!ps && nangCao.length > 0 && <details><summary className="phu" style={{ cursor: "pointer", marginBottom: 8 }}>Cài đặt nâng cao ({nangCao.length})</summary>{nangCao.map(oForm)}</details>}
      </div>
    </>
  );
}

// ======================= bảng phải: tiền cả lượt =======================
// [ghi chú kỹ thuật] không có lời gọi nào đo được mà sổ của phiên có tiền (khối Chạy code / làm file) ⇒ lấy số của SỔ thay vì 0
function tongTien(tien: Cost): number {
  const t = tien.total.main + tien.total.side + (tien.total.tool ?? 0);
  return tien.calls.length === 0 && tien.so ? tien.so.usd_tinh_tran + tien.so.usd_bedrock_tham_khao : t;   // [ghi chú kỹ thuật] CỘNG cả hai — cùng nghĩa với lượt thường (tổng đã gồm tiền Claude, Claude chỉ ghi chú là tham khảo)
}

function BangTien({ tien, nodes, loi }: { tien: Cost | null; nodes: KhoiRF[]; loi?: boolean }) {
  if (!tien) return <div className="muc phu">{loi ? "Chưa lấy được tiền của lượt này — tải lại trang để thử lại." : "Tiền: đang tính…"}</div>;
  const ten = (id: string) => nodes.find((n) => n.id === id)?.data.label || id.slice(0, 8);
  const theoKhoi: Record<string, { main: number; side: number; vao: number; ra: number; khongDo: number }> = {};
  for (const c of tien.calls) {
    const x = (theoKhoi[c.node] ??= { main: 0, side: 0, vao: 0, ra: 0, khongDo: 0 });
    if (!c.measurable) { x.khongDo++; continue; }
    if (c.call_kind === "side") x.side += c.cost_usd || 0; else x.main += c.cost_usd || 0;
    x.vao += c.tokens_in || 0; x.ra += c.tokens_out || 0;
  }
  return (
    <div className="muc">
      <h3>Tiền cả lượt: <span className="so">{usd(tongTien(tien))}</span></h3>
      {tien.so && <div className="phu so" style={{ marginBottom: 6 }}>Theo sổ: dịch vụ trả theo lượt {usd(tien.so.usd_tinh_tran)} · trong đó Claude (tham khảo, không tính trần) {usd(tien.so.usd_bedrock_tham_khao)} · token vào / ra {soVn(tien.so.token_vao)} / {soVn(tien.so.token_ra)}</div>}
      <div className="kv so" style={{ marginBottom: 8 }}>
        <span>Gọi chính</span><span>{usd(tien.total.main)}</span>
        <span>Gọi ngầm</span><span className="ngam">{usd(tien.total.side)}</span>
        <span>Công cụ ngoài (Serper)</span><span>{tien.calls.some((c) => c.call_kind === "tool") ? `${tien.calls.filter((c) => c.call_kind === "tool").length} lượt · ${tien.total.unpriced_calls ? "chưa có giá" : usd(tien.total.tool ?? 0)}` : "không dùng"}</span>
        {(tien.total.bedrock ?? 0) > 0 && <><span>Trong đó Claude qua Bedrock</span><span>{usd(tien.total.bedrock)} — tham khảo, không tính trần</span></>}
        <span>Token vào / ra</span><span>{soVn(tien.total.tokens_in)} / {soVn(tien.total.tokens_out)}</span>
      </div>
      <table className="bang"><thead><tr><th>Khối</th><th className="so">Vào / ra</th><th className="so">Tiền</th></tr></thead>
        <tbody>{Object.entries(theoKhoi).map(([id, x]) => (
          <tr key={id}><td>{ten(id)}{x.side > 0 && <div className="phu ngam">ngầm {usd(x.side)}</div>}{x.khongDo > 0 && <div className="phu">+ {x.khongDo} lần gọi không đo được</div>}</td>
            <td className="so">{soVn(x.vao)} / {soVn(x.ra)}</td><td className="so">{x.vao || x.ra ? usd(x.main + x.side) : "—"}</td></tr>))}</tbody></table>
      {tien.total.unmeasured_calls > 0 && <div className="phu" style={{ marginTop: 6 }}>Khối người duyệt có gọi AI viết bản tóm tắt, nhưng Capitol không lưu token ⇒ không cộng được vào tổng.</div>}
    </div>
  );
}

// ======================= màn hình "Kết quả lượt chạy" — cho người dùng cuối (người dùng yêu cầu 04/11) =======================
// Chỉ hiện SẢN PHẨM của lượt: nội dung các khối "Xuất…" (nhóm Đầu ra), theo thứ tự xong. JSON có cấu trúc ⇒ bảng; chữ ⇒ đoạn văn.
// Capitol không có màn hình này trong bộ code (giao diện thật của họ không nằm trong image) — đây là bản của mình.
const TEN_TRUONG: Record<string, string> = {
  viec: "Việc", ma_don: "Mã đơn", uu_tien: "Ưu tiên", ghi_chu_cho_kho: "Ghi chú cho kho", tra_loi: "Trả lời khách", nguon: "Nguồn",
  da_tra_don: "Đã tra đơn", can_nguoi_xu_ly: "Cần người xử lý", loai: "Loại", phong_ban: "Phòng ban", tom_tat: "Tóm tắt", can_tra_web: "Cần tra web",
};
const GIA_TRI_VN: Record<string, string> = { thuong: "thường", cao: "cao", doi_hang: "đổi hàng", hoi_gia: "hỏi giá", khieu_nai_giao_hang: "khiếu nại giao hàng", nhieu_viec: "nhiều việc", khac: "khác", kho: "kho", sale: "sale", cskh: "CSKH" };

function GiaTri({ v }: { v: unknown }) {
  if (v === null || v === undefined || v === "") return <span className="phu">— (không có)</span>;
  if (typeof v === "boolean") return <span>{v ? "có" : "không"}</span>;
  if (Array.isArray(v)) return v.length ? <ul style={{ margin: 0, paddingLeft: 18 }}>{v.map((x, i) => <li key={i}><GiaTri v={x} /></li>)}</ul> : <span className="phu">— (trống)</span>;
  if (typeof v === "object") return <BangJson d={v as Record<string, unknown>} />;
  const s = String(v);
  // [ghi chú kỹ thuật] link kho nội bộ (minio:9000) không mở được từ máy ⇒ không hiện thành link; file tải bằng mục "File tải về" của màn kết quả
  if (/^https?:\/\/minio:9000\//.test(s)) return <span className="phu">(file trong kho nội bộ — tải bằng nút “Tải về” ở màn kết quả)</span>;
  // [ghi chú kỹ thuật] giá trị này có thể do AI / công cụ viết ⇒ KHÔNG tạo liên kết ngoài để bấm (cùng luật với khung chat): hiện là chữ, chọn rồi chép được
  if (/^https?:\/\//.test(s)) return <span className="so" style={{ wordBreak: "break-all", userSelect: "all" }} title="Địa chỉ do lượt chạy trả về — trang không mở liên kết ngoài; chép nếu cần">{s}</span>;
  return <span style={{ whiteSpace: "pre-wrap" }}>{GIA_TRI_VN[s] ?? s}</span>;
}
function BangJson({ d }: { d: Record<string, unknown> }) {
  return (
    <table className="bang" style={{ fontSize: 14 }}><tbody>
      {Object.entries(d).map(([k, v]) => <tr key={k}><th style={{ width: 170, color: "var(--muc2)" }}>{TEN_TRUONG[k] ?? k.replace(/_/g, " ")}</th><td><GiaTri v={v} /></td></tr>)}
    </tbody></table>
  );
}
function NoiDung({ s }: { s: string | null }) {
  if (!s) return <span className="phu">— (chưa có nội dung)</span>;
  // [ghi chú kỹ thuật] link ký sẵn của kho nội bộ (minio:9000 — có mã khoá truy cập + chữ ký trong URL) không mở được từ máy ⇒ không in nguyên link
  if (/^\s*https?:\/\/minio:9000\/\S*\s*$/.test(s)) return <span className="phu">Khối này tạo ra một file — tải ở mục “File tải về” phía trên.</span>;
  try { const j = JSON.parse(s); if (j && typeof j === "object" && !Array.isArray(j)) return <BangJson d={j} />; } catch { /* chữ thường */ }
  return <div style={{ whiteSpace: "pre-wrap", fontSize: 15, lineHeight: 1.6 }}>{s}</div>;
}

// [ghi chú kỹ thuật] kết quả dễ đọc. Chữ AI hiện bằng phần tử React (KHÔNG innerHTML / HTML thô):
// markdown tối giản — **đậm**, dòng "- " / "* " / "1. " thành danh sách, dòng "#" thành tiêu đề nhỏ, còn lại giữ xuống dòng.
function ChuDam({ s }: { s: string }) {
  return <>{s.split(/(\*\*[^*\n]+\*\*)/g).map((x, i) => (x.startsWith("**") && x.endsWith("**") && x.length > 4 ? <b key={i}>{x.slice(2, -2)}</b> : <span key={i}>{x}</span>))}</>;
}
function ChuMd({ s, co = 15 }: { s: string; co?: number }) {
  const khoi: React.ReactNode[] = [];
  let ds: string[] = [];
  const dongDs = () => { if (ds.length) { khoi.push(<ul key={khoi.length} style={{ margin: "4px 0", paddingLeft: 22 }}>{ds.map((x, i) => <li key={i}><ChuDam s={x} /></li>)}</ul>); ds = []; } };
  for (const dong of s.replace(/\r\n?/g, "\n").split("\n")) {
    const m = dong.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (m) { ds.push(m[1]); continue; }
    dongDs();
    const h = dong.match(/^\s*#{1,6}\s+(.*)$/);
    if (h) khoi.push(<div key={khoi.length} style={{ fontWeight: 700, marginTop: 8 }}><ChuDam s={h[1]} /></div>);
    else if (dong.trim()) khoi.push(<div key={khoi.length}><ChuDam s={dong} /></div>);
    else khoi.push(<div key={khoi.length} style={{ height: 8 }} />);
  }
  dongDs();
  return <div style={{ fontSize: co, lineHeight: 1.6 }}>{khoi}</div>;
}
const vnd = (x: number | null | undefined) => (typeof x === "number" ? x.toLocaleString("vi-VN") + " đ" : "—");
function DuLieuGoc({ v }: { v: unknown }) {
  return (
    <details style={{ marginTop: 8 }}>
      <summary className="phu" style={{ cursor: "pointer" }}>Xem dữ liệu gốc</summary>
      <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12, maxHeight: 420, overflow: "auto", background: "var(--giay2, #f6f6f4)", padding: 10, borderRadius: 6 }}>
        {JSON.stringify(v, null, 2)}
      </pre>
    </details>
  );
}
// [ghi chú kỹ thuật] câu trả lời to (chữ có chân, bảng markdown thành bảng thật), 3 ô số, bảng thưởng gập
// (nhân viên × KPI, số canh phải, chữ số đều cột), nhãn "tạm tính, chưa duyệt" / "giám sát đã duyệt lượt này".
const soVn = (x: unknown) => (typeof x === "number" ? x.toLocaleString("vi-VN") : "—");
const khongMo = () => { /* màn kết quả không mở workflow từ câu trả lời */ };

// ======================= [ghi chú kỹ thuật] tải file từ màn kết quả =======================
function luuVeMay(b: Blob, ten: string) {
  const u = URL.createObjectURL(b), a = document.createElement("a");
  a.href = u; a.download = ten; a.style.display = "none"; document.body.appendChild(a); a.click();
  setTimeout(() => { a.remove(); URL.revokeObjectURL(u); }, 10_000);   // [ghi chú kỹ thuật] giữ thẻ tải trong trang vài giây rồi mới gỡ (gỡ ngay ⇒ có trình duyệt không kịp bắt đầu tải)
}
/** Bản Excel của một file .csv / .tsv của lượt: máy chủ dựng (GET …&dang=xlsx). Không dựng được ⇒ hiện lý do máy chủ trả, KHÔNG tải file lỗi về máy. */
async function taiBanExcel(phien: string, f: TepLuot, baoLoi: (s: string | null) => void) {
  baoLoi(null);
  try {
    const r = await fetch(api.linkTai(phien, f.id) + "&dang=xlsx", { credentials: "same-origin" });
    if (!r.ok) { const j = await r.json().catch(() => ({})); baoLoi(`Chưa tạo được bản Excel của “${f.ten}”: ${(j as { loi?: string }).loi || `máy chủ trả mã ${r.status}`} Bản gốc vẫn tải được.`); return; }
    luuVeMay(await r.blob(), f.ten.replace(/\.(csv|tsv)$/i, "") + ".xlsx");
  } catch (e) { baoLoi(`Chưa tạo được bản Excel của “${f.ten}”: ${(e as Error).message}`); }
}
/** Chữ Markdown của khối Xuất Markdown: giá trị đã đọc (ketQua) nếu là chữ, không thì chữ thô của sự kiện. Link kho nội bộ / JSON ⇒ null (để nhánh cũ xử lý). */
function chuMdCua(k: KetQuaKhoi | undefined, tho: string | null | undefined): string | null {
  const s = typeof k?.v === "string" ? k.v : (!k && typeof tho === "string" ? tho : null);
  if (!s || !s.trim() || /^\s*https?:\/\/minio:9000\//.test(s) || /^\s*[{\[]/.test(s)) return null;
  return s;
}
function KetQuaMd({ chu, ten }: { chu: string; ten: string }) {
  const tenTep = (ten.replace(/[\\/:*?"<>|\n\r]+/g, " ").trim().slice(0, 80) || "ket_qua") + ".md";
  return (
    <>
      <div className="kq-tra-loi"><Markdown chu={chu} onMo={khongMo} /></div>
      <div style={{ marginTop: 10 }}>
        <button className="nut nho" onClick={() => luuVeMay(new Blob([chu], { type: "text/markdown;charset=utf-8" }), tenTep)} title="Tải đúng nội dung đang hiện ở trên dưới dạng file Markdown (.md)">Tải bản .md</button>
        <span className="phu" style={{ marginLeft: 8, fontSize: 12 }}>File chữ thuần, mở bằng Notepad / Word đều được.</span>
      </div>
      <NguonTaiLieu chu={chu} />
    </>
  );
}
// [ghi chú kỹ thuật] thanh trên: đang tính trên bản nào (mặc định = bản lúc bật máy chủ KPI, không hiện gì)
function BanDangDung() {
  const b = useBanDuLieu();
  if (!b) return null;
  return (
    <span className="canh-bao" style={{ marginRight: 8 }} title={b.dv}>
      Đang dùng bản dữ liệu {b.dv.slice(0, 12)} · ngày chốt {b.cutoff}{" "}
      <button className="nut" onClick={() => banDuLieu.dat(null)}>Bỏ</button>
    </span>
  );
}
function KetQuaKpi({ g, phien }: { g: GoiKpi; phien?: string }) {
  const gui = useDaGui(phien || "");   // [ghi chú kỹ thuật] bản trang đã gửi khi bấm chạy lượt này (undefined = không biết)
  const lech = typeof gui === "string" && g.data_version !== gui;
  const bt = g.bang_thuong ?? {};
  const ls = Array.isArray(bt.lines) ? bt.lines : [];
  const nv = Array.isArray(bt.employees) ? bt.employees : [];
  const ks = Array.isArray(bt.kpis) ? bt.kpis : [];
  const dat = g.kiem?.dat;
  const o: Record<string, number> = {};
  for (const l of ls) o[l.employee_id + "\u0000" + l.kpi] = (o[l.employee_id + "\u0000" + l.kpi] ?? 0) + (typeof l.amount === "number" ? l.amount : 0);
  const coBang = nv.length > 0 && ks.length > 0 && ls.length > 0;
  return (
    <div className="kq-kpi">
      {g.xuat_excel && <div className="canh-bao" style={{ marginBottom: 8 }}>
        {g.xuat_excel.nhap || g.duyet?.kieu === "tu_duyet_qua_han"
          ? "File Excel NHÁP (tự duyệt khi quá hạn — chưa có người duyệt) đã được hệ thống xuất — xem mục “File kết quả”."
          : "File Excel đã được hệ thống xuất ở bước duyệt — xem mục “File kết quả”."}{" "}
        <span className="phu">Câu bên dưới do AI viết TRƯỚC bước duyệt; AI không tự tạo file nên có thể nói chưa xuất được.</span></div>}
      <div className="kq-tra-loi"><Markdown chu={g.cau_tra_loi} onMo={khongMo} /></div>
      <div className="kq-nhan bang">
        <b className="serif">Bảng lương kinh doanh (8 KPI) do hệ thống tính</b>
        {g.duyet?.kieu === "tu_duyet_qua_han"
          ? <span className="nhan-tam" title="Không ai bấm Duyệt trong hạn chờ nên hệ thống tự cho đi tiếp. Hệ thống tính thưởng KHÔNG ghi bảng này là đã duyệt; file kèm là bản nháp.">tự duyệt khi quá hạn{typeof g.duyet.cho_giay === "number" ? ` (${g.duyet.cho_giay} giây)` : ""} — chưa có người duyệt</span>
          : g.duyet
          ? <span className="nhan-tam da" title="Lượt này đã đi qua khối Giám sát duyệt và được bấm Duyệt — người duyệt xem bảng, không đọc trọn câu của AI">giám sát đã duyệt bảng này{g.duyet.luc ? `, ${ngayGio(g.duyet.luc)}` : ""}</span>
          : <span className="nhan-tam" title="Lượt này chưa có người bấm Duyệt ở khối Giám sát duyệt">tạm tính, chưa duyệt</span>}
      </div>
      <div className="kq-so">
        {typeof bt.team_total === "number" && (
          <div><span title="Tổng lương kinh doanh 8 KPI của cả đội — không gồm thưởng thêm">Tổng lương kinh doanh cả đội</span><b className="serif so">{vnd(bt.team_total)}</b>
            <small>{bt.period ? `kỳ ${bt.period}` : ""}{bt.cutoff ? `, số tính tới ${bt.cutoff}` : ""}</small></div>)}
        {nv.length > 0 && (
          <div><span>Nhân viên trong bảng</span><b className="serif so">{nv.length}</b><small>{ks.length} KPI, {ls.length} dòng thưởng</small></div>)}
        <div className={dat ? "dat" : dat === false ? "hong" : ""}><span>{g.kiem?.nguon === "ai" ? "Kiểm số bằng AI" : "Kiểm số bằng code"}</span><b className="serif">{dat ? "Đạt" : dat === false ? "Không đạt" : "—"}</b>
          <small>{g.kiem?.ly_do ? g.kiem.ly_do.replace(/^(không )?đạt\s*—\s*/, "") : ""}</small></div>
      </div>
      {coBang && (
        <details className="kq-gap">
          <summary>Bảng lương kinh doanh theo nhân viên — không gồm thưởng thêm ({nv.length} người × {ks.length} KPI, đơn vị: đồng)</summary>
          <div className="kq-cuon">
            <table className="bang tien">
              <thead><tr><th>Nhân viên</th>{ks.map((k) => <th key={k} className="so">{k}</th>)}<th className="so">Tổng</th></tr></thead>
              <tbody>{nv.map((e) => (
                <tr key={e}><td>{e}</td>{ks.map((k) => <td key={k} className="so">{o[e + "\u0000" + k] ? soVn(o[e + "\u0000" + k]) : <span className="phu">0</span>}</td>)}
                  <td className="so"><b>{soVn(bt.salary?.[e])}</b></td></tr>))}</tbody>
              <tfoot><tr><td>Cả đội</td>{ks.map((k) => <td key={k} className="so">{soVn(bt.kpi_total?.[k])}</td>)}<td className="so">{soVn(bt.team_total)}</td></tr></tfoot>
            </table>
          </div>
        </details>
      )}
      {ls.length > 0 && (
        <details className="kq-gap">
          <summary>Từng dòng thưởng và lý do ({ls.length} dòng)</summary>
          <div className="kq-cuon">
            <table className="bang tien">
              <thead><tr><th>Nhân viên</th><th>KPI</th><th className="so">% đạt</th><th>Mốc</th><th className="so">Tiền (đồng)</th><th>Vì sao</th></tr></thead>
              <tbody>
                {ls.map((l, i) => (
                  <tr key={i}>
                    <td>{l.employee_id}</td><td>{l.kpi}</td>
                    <td className="so">{typeof l.pct === "number" ? l.pct.toLocaleString("vi-VN", { maximumFractionDigits: 2 }) + " %" : "—"}</td>
                    <td>{l.reached_tier ?? <span className="phu">chưa đạt</span>}</td>
                    <td className="so">{soVn(l.amount)}</td>
                    <td className="phu ly-do">{l.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {lech && <div className="canh-bao" style={{ color: "var(--loi)" }}>Khối Kiểm số báo bản dữ liệu {g.data_version ? g.data_version.slice(0, 12) : "lúc bật"} — KHÁC bản trang đã chọn khi chạy ({gui!.slice(0, 12)}). Số trên đây là của bản Kiểm số báo.</div>}
      <div className="phu kq-chan">Bản dữ liệu {g.data_version ? g.data_version.slice(0, 12) : "lúc bật máy chủ KPI"}{g.kiem?.phien_ban ? `, bộ kiểm ${g.kiem.phien_ban}` : ""}</div>
      <DuLieuGoc v={g} />
    </div>
  );
}
function KetQuaDep({ k, phien }: { k: KetQuaKhoi; phien?: string }) {
  if (k.kieu === "kpi") return <KetQuaKpi g={k.v as GoiKpi} phien={phien} />;
  if (k.kieu === "dict") return <><BangJson d={k.v as Record<string, unknown>} /><DuLieuGoc v={k.v} /></>;
  if (k.kieu === "list") return <><GiaTri v={k.v} /><DuLieuGoc v={k.v} /></>;
  return <><NoiDung s={typeof k.v === "string" ? k.v : null} /><NguonTaiLieu chu={typeof k.v === "string" ? k.v : null} /></>;
}

// [ghi chú kỹ thuật] "Dấu vết lượt chạy": gập sẵn, chỉ gọi máy chủ khi mở. Mô hình / token / tiền lấy TỪ BẢN GHI
// lần gọi (không từ cấu hình khối); mô hình ≠ gpt-6-luna ⇒ cờ đỏ. Mức suy nghĩ: bản ghi không có ⇒ ghi rõ, kèm cấu hình trong mã.
function DauVetLuot({ phien }: { phien: string }) {
  const [d, setD] = useState<DauVet | null>(null);
  const [loi, setLoi] = useState("");
  const mo = (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    if ((e.currentTarget as HTMLDetailsElement).open && !d) api.dauVet(phien).then(setD).catch((x) => setLoi((x as Error).message));
  };
  const gio = (s?: string | null) => (s ? ngayGio(s) : "—");
  return (
    <details onToggle={mo} className="kq-the">
      <summary className="serif" style={{ cursor: "pointer", fontWeight: 700, fontSize: 17 }}>Dấu vết lượt chạy (mô hình, công cụ, kiểm, duyệt — lấy từ bản ghi)</summary>
      {loi && <div className="hop loi" style={{ marginTop: 8 }}>Không lấy được dấu vết: {loi}</div>}
      {!d && !loi && <div className="phu" style={{ marginTop: 8 }}>Đang đọc…</div>}
      {d && <>
        <div className="phu" style={{ marginTop: 8, fontSize: 13 }}>
          {d.tong.so_lan_goi} lần gọi mô hình · tiền {usd(d.tong.tien_usd)}{d.tong.khong_do_duoc ? ` · ${d.tong.khong_do_duoc} lần gọi KHÔNG đo được tiền` : ""}
          {d.tong.co_do.length ? <b style={{ color: "var(--do, #b91c1c)" }}> · ⚠ mô hình KHÔNG có trong bảng cổng AI ở: {d.tong.co_do.join(", ")}</b>
            : ` · mọi lần gọi dùng mô hình có trong bảng cổng AI${(() => { const t = [...new Set(d.khoi.flatMap((k) => k.ai?.mo_hinh_ban_ghi ?? []))]; return t.length ? ": " + t.join(", ") : ""; })()}`}
          {(d.tong.hong_mem ?? []).length > 0 && <b style={{ color: "var(--do, #b91c1c)" }}> · ⚠ khối báo xong nhưng công cụ lỗi / tự báo không làm được: {d.tong.hong_mem!.join(", ")} — đừng tin kết quả các khối này</b>}
        </div>
        {d.khoi.map((k) => (
          <div key={k.id} style={{ borderTop: "1px solid var(--vien2)", marginTop: 10, paddingTop: 8, fontSize: 13 }}>
            <div><b>{k.ten}</b> <span className="phu">· {k.loai_goc || k.loai} · {k.trang_thai}{k.giay != null ? ` · ${k.giay.toLocaleString("vi-VN")} s` : ""}</span>
              {(k.so_cong_cu_loi ?? 0) > 0 && <b style={{ color: "var(--do, #b91c1c)" }}> · ⚠ công cụ lỗi {k.so_cong_cu_loi} / {k.so_cong_cu} lần</b>}
              {k.tu_bao_khong_lam_duoc && <b style={{ color: "var(--do, #b91c1c)" }}> · ⚠ khối tự báo KHÔNG LÀM ĐƯỢC</b>}</div>
            {k.ai && <div style={{ marginTop: 4 }}>
              Mô hình (bản ghi lần gọi): <b style={{ color: k.ai.co_do ? "var(--do, #b91c1c)" : undefined }}>{k.ai.mo_hinh_ban_ghi.join(", ") || "— (không có lần gọi ghi lại)"}</b>
              {k.ai.co_do && <b style={{ color: "var(--do, #b91c1c)" }}> ⚠ {k.ai.ly_do_co}</b>}
              <span className="phu"> · ô của khối: {k.ai.mo_hinh_cau_hinh || "—"}{k.ai.mo_hinh_file ? ` · file mô hình khối hiện đặt: ${k.ai.mo_hinh_file}` : ""}</span><br />
              {k.ai.so_lan_goi} lần gọi · token vào {k.ai.token_vao.toLocaleString("vi-VN")} / ra {k.ai.token_ra.toLocaleString("vi-VN")} / suy nghĩ {k.ai.token_suy_nghi.toLocaleString("vi-VN")} · tiền {usd(k.ai.tien_usd)}
              {k.ai.so_lan_khong_do_duoc ? <b> · {k.ai.so_lan_khong_do_duoc} lần không đo được</b> : null}<br />
              <span className="phu">Mức suy nghĩ: {k.ai.muc_suy_nghi}</span>
              {k.ai.ghi_chu.map((g, i) => <div key={i} className="phu">· {g}</div>)}
            </div>}
            {k.cong_cu && <details style={{ marginTop: 4 }}>
              <summary style={{ cursor: "pointer" }}>Công cụ đã gọi ({k.so_cong_cu}{(k.so_cong_cu ?? 0) > k.cong_cu.length ? `, hiện ${k.cong_cu.length}` : ""})</summary>
              <ol style={{ margin: "4px 0", paddingLeft: 22 }}>
                {k.cong_cu.map((c, i) => <li key={i} style={{ marginTop: 3 }}>
                  <code>{c.ten}</code>{c.thu_lai ? " (thử lại)" : ""}{c.loi ? <b style={{ color: "var(--do, #b91c1c)" }}> · lỗi</b> : null}{c.giay != null ? <span className="phu"> · {c.giay} s</span> : null}
                  <div className="phu" style={{ wordBreak: "break-word" }}>tham số: {c.tham_so || "—"}</div>
                  <div className="phu" style={{ wordBreak: "break-word" }}>kết quả: {c.ket_qua || "—"}</div>
                </li>)}
              </ol>
            </details>}
            {k.rao && <div style={{ marginTop: 4 }}>
              Kiểm {k.rao.so_lan} lần.{" "}
              {k.rao.dau && <>Lần đầu: <b style={{ color: k.rao.dau.dat ? "var(--xanh, #15803d)" : "var(--do, #b91c1c)" }}>{k.rao.dau.dat ? "đạt" : "không đạt"}</b> — {k.rao.dau.ly_do}. </>}
              {k.rao.cuoi && k.rao.so_lan > 1 && <>Lần cuối: <b style={{ color: k.rao.cuoi.dat ? "var(--xanh, #15803d)" : "var(--do, #b91c1c)" }}>{k.rao.cuoi.dat ? "đạt" : "không đạt"}</b> — {k.rao.cuoi.ly_do}.</>}
            </div>}
            {k.duyet && <div style={{ marginTop: 4 }}>
              Chờ từ {gio(k.duyet.cho_tu)}{k.duyet.han_giay ? ` (hạn ${k.duyet.han_giay} s)` : ""} · quyết lúc {gio(k.duyet.quyet_luc)}
              {k.duyet.cho_giay != null ? ` · chờ ${k.duyet.cho_giay.toLocaleString("vi-VN")} s` : ""}{k.duyet.ket_qua ? ` · kết quả: ${k.duyet.ket_qua}` : ""}
              <div className="phu">Qua: {k.duyet.qua}. {k.duyet.ai}.</div>
            </div>}
          </div>
        ))}
      </>}
    </details>
  );
}

// [ghi chú kỹ thuật] workflow "Hỏi dữ liệu bằng lời": mọi câu SQL lượt đã gọi (chữ đơn cách, chỉ đọc) + bảng kết quả công cụ trả về.
// Dữ liệu lấy từ bản ghi lần gọi công cụ — trang KHÔNG chạy lại câu nào. Ô bảng là chữ thường của React (không HTML).
function TruyVanLuot({ d, tenKhoi }: { d: TruyVan; tenKhoi: (id: string) => string }) {
  return (
    <div className="kq-the">
      <div className="kq-nhan"><span className="nhan-tam thu">{d.nhan}</span></div>
      <details className="kq-gap" open style={{ borderTop: 0, marginTop: 0, paddingTop: 0 }}>
        <summary>Xem câu truy vấn đã chạy ({d.lan.length} câu{d.con_nua ? ", còn câu khác chưa hiện" : ""})</summary>
        {d.lan.map((l, i) => {
          const k = l.ket_qua;
          return (
            <div key={i} style={{ marginTop: 12 }}>
              <div className="phu so">Câu {i + 1}, khối “{tenKhoi(l.khoi)}”{l.luc ? `, ${ngayGio(l.luc)}` : ""}</div>
              <pre className="sql"><code>{l.sql}</code></pre>
              {k.loi && <div className="hop loi" style={{ marginTop: 6 }}>Câu này bị từ chối hoặc lỗi: {k.loi}</div>}
              {!k.co && !k.loi && <div className="phu" style={{ marginTop: 6 }}>{k.ghi_chu}{k.chu ? ` ${k.chu}` : ""}</div>}
              {k.co && (
                <details className="kq-gap" open={d.lan.length === 1}>
                  <summary>Bảng kết quả ({soVn(k.so_dong)} dòng{k.da_cat ? ", đã cắt bớt" : ""})</summary>
                  {k.ghi_chu && <div className="phu" style={{ marginTop: 6 }}>{k.ghi_chu}</div>}
                  <div className="kq-cuon">
                    <table className="bang tien">
                      <thead><tr>{(k.cot ?? []).map((c, j) => <th key={j} className={(k.dong ?? []).some((r) => typeof r[j] === "number") ? "so" : undefined}>{c}</th>)}</tr></thead>
                      <tbody>{(k.dong ?? []).map((r, ri) => (
                        <tr key={ri}>{r.map((v, j) => <td key={j} className={typeof v === "number" ? "so" : undefined}>
                          {v === null ? <span className="phu">(trống)</span> : typeof v === "number" ? v.toLocaleString("vi-VN", { maximumFractionDigits: 4 }) : String(v)}</td>)}</tr>))}</tbody>
                    </table>
                  </div>
                </details>
              )}
            </div>
          );
        })}
      </details>
    </div>
  );
}

function ManKetQua(p: {
  luot: RunDetail; kq: ReturnType<typeof tinh>; nodes: KhoiRF[]; edges: Edge[]; theoLoai: Record<string, BlockDef>; tien: Cost | null;
  tongLuot: number | null; tongCho: number; xemKhoi: (id: string) => void;
}) {
  // undefined = ĐANG TẢI (chưa có câu trả lời của máy chủ) · null = tải lỗi · object = đã có. [người dùng 12/11] không in gói thô trong lúc chờ.
  const [ketQua, setKetQua] = useState<Record<string, KetQuaKhoi> | null | undefined>(undefined);
  const phien = p.luot.luot.phien, ttLuot = p.luot.luot.tt;
  // lấy khi lượt mở / đổi trạng thái (xong, chờ duyệt…); lỗi ⇒ giữ cách hiện cũ (NoiDung) — không chặn màn hình
  useEffect(() => { api.ketQua(phien).then(setKetQua).catch(() => setKetQua(null)); }, [phien, ttLuot]);
  const [truyVan, setTruyVan] = useState<TruyVan | null>(null);
  // [ghi chú kỹ thuật] chỉ hỏi máy chủ khi workflow có khối Agent bật công cụ hỏi dữ liệu bằng SQL
  const coSql = p.nodes.some((n) => n.data.loai === "agent_node" && [true, "true", "True", 1].includes(n.data.params.tool_sql_query as never));
  useEffect(() => { setTruyVan(null); if (coSql) api.truyVan(phien).then(setTruyVan).catch(() => setTruyVan(null)); }, [phien, ttLuot, coSql]);
  const [tep, setTep] = useState<TepLuot[]>([]);
  useEffect(() => { api.tepLuot(phien).then(setTep).catch(() => setTep([])); }, [phien, ttLuot]);
  const [loiTep, setLoiTep] = useState<string | null>(null);   // [ghi chú kỹ thuật] lý do không dựng được bản Excel (file quá lớn…) — hiện cạnh danh sách file
  const ra = p.nodes.filter((n) => p.theoLoai[n.data.loai]?.group_key === "output")
    .sort((a, b) => (p.kq.st[a.id]?.kt ?? 9e15) - (p.kq.st[b.id]?.kt ?? 9e15));
  const tt = p.luot.luot.tt;
  const tongTien = p.tien ? p.tien.total.main + p.tien.total.side + (p.tien.total.tool ?? 0) : null;
  const khongNguon = p.tien?.checks ? Object.values(p.tien.checks).reduce((s, c) => s + c.khong_nguon, 0) : 0;
  const duyet = p.nodes.filter((n) => n.data.loai === "human_intervention_node");
  // [ghi chú kỹ thuật] STOPPED KHÔNG có nghĩa "người duyệt từ chối": Capitol ghi "Workflow was stopped by user" cho MỌI lượt dừng
  // (persist_results.py:314-316), kể cả khi khối duyệt bị từ chối. Dấu thật duy nhất trang có: khối duyệt đã có awaiting_intervention mà sau đó
  // không có sự kiện xong / lỗi của chính khối đó ⇒ lượt dừng Ở bước duyệt (từ chối hoặc dừng từ ngoài — không phân biệt được).
  const dungODuyet = (() => {
    if (tt !== "STOPPED") return null;
    for (const n of duyet) {
      const ev = p.luot.su_kien.filter((e) => e.n === n.id);
      const cho = ev.map((e) => e.t).lastIndexOf("awaiting_intervention");
      if (cho >= 0 && !ev.slice(cho + 1).some((e) => ["execution_successful", "execution_failed", "end"].includes(e.t))) return n.data.label || n.id.slice(0, 8);
    }
    return null;
  })();
  // [ghi chú kỹ thuật] NHÃN "do AI viết" ở tầng THẺ KẾT QUẢ: thẻ nào có khối Agent đứng TRƯỚC (theo dây của workflow — cấu hình, không
  // phải dữ liệu lượt) thì MỌI cách hiện nội dung (gói KPI, bảng, chữ thô khi không lấy được /ket-qua) đều mang nhãn; lượt đã duyệt vẫn mang nhãn.
  const bat = (v: unknown) => v === true || v === 1 || (typeof v === "string" && ["true", "1", "yes", "on"].includes(v.trim().toLowerCase()));
  const nhanAi = (id: string, laGoiKpi: boolean): string | null => {
    const da = new Set<string>(), hang = [id];
    while (hang.length) { const k = hang.pop()!; for (const e of p.edges) if (e.target === k && !da.has(e.source)) { da.add(e.source); hang.push(e.source); } }
    const ag = p.nodes.filter((n) => da.has(n.id) && n.data.loai === "agent_node");
    // [ghi chú kỹ thuật] không xác định được khối đứng trước (thẻ không có dây vào trong sơ đồ đang mở — vd workflow bị sửa sau lượt) ⇒ MẶC ĐỊNH CÓ nhãn chung
    if (!da.size && !laGoiKpi) return "Câu trả lời có thể do AI viết — chưa có người đọc duyệt.";
    if (laGoiKpi || ag.some((n) => bat(n.data.params.tool_kpi))) return "Lời giải thích của AI — chưa có người đọc duyệt; số chính thức là bảng dưới.";
    if (ag.some((n) => bat(n.data.params.tool_sql_query))) return "Câu trả lời do AI viết — chưa có người đọc duyệt; số lấy từ câu truy vấn bên dưới.";
    const aiKhac = p.nodes.some((n) => da.has(n.id) && p.theoLoai[n.data.loai]?.group_key === "agent");
    // khối đầu ra tự gọi AI (trình chiếu / bảng tính / văn bản / biểu đồ) cũng mang nhãn
    const tuNo = ["pptx_chat_node", "xlsx_chat_node", "docx_chat_node", "d3_data_visualization_node", "network_graph_output_node"]
      .includes(p.nodes.find((n) => n.id === id)?.data.loai ?? "");
    return ag.length || aiKhac || tuNo ? "Câu trả lời do AI viết — chưa có người đọc duyệt." : null;
  };
  return (
    <div className="kq">
      <div className="kq-trong">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h2 className="serif">{p.luot.luot.ten}</h2>
          <span className={"chip " + tt}>{LUOT_VN[tt] || tt}</span>
        </div>
        <div className="phu so" style={{ marginTop: 6 }}>
          Bắt đầu {ngayGio(p.luot.luot.tao)} · tổng {giay(p.tongLuot)}{p.tongCho > 0 ? ` (chờ người duyệt ${giay(p.tongCho)})` : ""}
          {tongTien !== null ? ` · tiền ${usd(tongTien)}` : ""}{p.tien?.total.unmeasured_calls ? " + phần không đo được" : ""}{(p.tien?.total.bedrock ?? 0) > 0 ? ` (trong đó ${usd(p.tien?.total.bedrock)} Claude qua Bedrock — tham khảo, không tính trần)` : ""}
        </div>
        {p.kq.loiLuot && <div className="hop loi" style={{ marginTop: 12 }}>Lượt dừng: {loiThuong(p.kq.loiLuot)}</div>}
        {tt === "STOPPED" && <div className="canh-bao" style={{ marginTop: 12 }}>{dungODuyet
          ? `Lượt dừng khi đang chờ duyệt ở “${dungODuyet}” — người duyệt bấm Từ chối hoặc lượt bị dừng từ ngoài (Capitol ghi cùng một câu cho hai trường hợp); các kết quả sau bước duyệt không được tạo.`
          : /stopped by user/i.test(p.luot.luot.loi || "") ? "Người bấm dừng — lượt dừng giữa chừng, các khối chưa chạy không có kết quả." : "Lượt đã dừng — các khối chưa chạy không có kết quả."}</div>}
        {khongNguon > 0 && <div className="canh-bao" style={{ marginTop: 12 }}>Có {khongNguon} ý trong câu trả lời của AI KHÔNG tìm thấy nguồn (bộ đối chiếu bằng code) — kiểm trước khi dùng.</div>}
        {duyet.map((n) => p.kq.st[n.id]?.s === "duyet" && <div key={n.id} className="canh-bao" style={{ marginTop: 12 }}>Đang chờ duyệt ở “{n.data.label}” — <a href="#" onClick={(e) => { e.preventDefault(); p.xemKhoi(n.id); }}>mở để Duyệt / Từ chối</a>.</div>)}
        {tep.length > 0 && (() => {
          // [ghi chú kỹ thuật] tách file ĐẦU VÀO (khối Nhận file) khỏi file KẾT QUẢ — trước: gộp chung dưới "File tải về" ⇒ tưởng file đưa vào là kết quả
          const khoiCua = (f: TepLuot) => p.nodes.find((n) => n.id === f.node);
          const laVao = (f: TepLuot) => khoiCua(f)?.data.loai === "file_input_node";
          const kq = tep.filter((f) => !laVao(f)), vao = tep.filter(laVao);
          const dong = (f: TepLuot) => {
            const khoi = khoiCua(f), bang = /\.(csv|tsv)$/i.test(f.ten);
            return (
              <li key={f.id} style={{ marginTop: 6 }}>
                {bang && <><button className="nut nho" onClick={() => taiBanExcel(phien, f, setLoiTep)} title="Bản Excel dựng từ đúng file này: mỗi cột một cột, bấm đúp là mở đúng; không khoá">Tải bản Excel (.xlsx)</button>{" "}</>}
                <a href={api.linkTai(phien, f.id)} download={f.ten}>{bang ? "Tải bản gốc" : "Tải về"}: {f.ten}</a>
                {khoi ? <span className="phu"> · từ khối “{khoi.data.label}”</span> : null}
              </li>
            );
          };
          return (
            <div className="kq-the">
              <b className="serif" style={{ fontSize: 17 }}>File kết quả ({kq.length})</b>
              {kq.length ? <ul style={{ margin: "8px 0 0", paddingLeft: 20 }}>{kq.map(dong)}</ul>
                : <div className="phu" style={{ marginTop: 6 }}>Lượt này không tạo file; kết quả là phần chữ bên dưới{ra.some((n) => n.data.loai === "markdown_output_node") ? " (có nút “Tải bản .md”)" : ""}.</div>}
              {vao.length > 0 && <>
                <b className="serif" style={{ fontSize: 17, display: "block", marginTop: 14 }}>File bạn đã đưa vào ({vao.length})</b>
                <ul style={{ margin: "8px 0 0", paddingLeft: 20 }}>{vao.map(dong)}</ul>
              </>}
              {loiTep && <div className="canh-bao" style={{ marginTop: 8 }}>{loiTep}</div>}
              <div className="phu" style={{ marginTop: 6, fontSize: 12 }}>Tải về máy rồi mở bằng ứng dụng của máy — trang không mở file ngay trên trình duyệt (an toàn). File bảng dạng .csv có thêm bản Excel để bấm đúp là ra đúng cột.</div>
            </div>
          );
        })()}
        {ra.length === 0 && <p className="phu" style={{ marginTop: 20 }}>Workflow này không có khối “Xuất…” nào — không có sản phẩm để hiện.</p>}
        {ra.map((n) => {
          const x = p.kq.st[n.id];
          return (
            <div key={n.id} className="kq-the">
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                <b className="serif" style={{ fontSize: 18 }}>{!duyet.length && /đã duyệt/i.test(n.data.label) ? "Kết quả (lượt này không có bước duyệt)" : tenKhoiHien(n.data.label, luotCoTuDuyet(p.kq.st))}</b>
                {x && <span className={"chip " + x.s}>{TT_VN[x.s]}</span>}
                <span className="cach" style={{ flex: 1 }} />
                <button className="nut nho" onClick={() => p.xemKhoi(n.id)}>Xem chi tiết khối</button>
              </div>
              {x?.s !== "bo" && x?.s !== "loi" && x?.s !== "chay" && x?.s !== "cho" && (() => {
                const nh = nhanAi(n.id, ketQua?.[n.id]?.kieu === "kpi");
                return nh ? <div className="kq-nhan"><span className="nhan-tam ai">{nh}</span></div> : null;
              })()}
              {x?.s === "bo" ? <div className="phu">Không có — lượt đã dừng trước khi tới bước này.</div>
                : x?.s === "loi" ? <div className="hop loi">{x.loi}</div>
                : x?.s === "chay" || x?.s === "cho" ? <div className="phu">Đang làm…</div>
                : ketQua === undefined ? <div className="phu">Đang tải kết quả…</div>
                : n.data.loai === "markdown_output_node" && chuMdCua(ketQua?.[n.id], x?.ra) ? <KetQuaMd chu={chuMdCua(ketQua?.[n.id], x?.ra)!} ten={n.data.label} />   /* [ghi chú kỹ thuật] dựng Markdown + nút tải .md */
                : ketQua?.[n.id] ? <KetQuaDep k={ketQua[n.id]} phien={phien} />
                : /^\s*\{\s*['"]cau_tra_loi['"]/.test(x?.ra ?? "") ? <div className="phu">Chưa đọc được kết quả đã xử lý của khối này — bấm lại lượt này trong danh sách.</div>
                : <NoiDung s={x?.ra ?? null} />}
            </div>
          );
        })}
        {truyVan && truyVan.lan.length > 0 && <TruyVanLuot d={truyVan} tenKhoi={(id) => p.nodes.find((n) => n.id === id)?.data.label || id.slice(0, 8)} />}
        <DauVetLuot key={phien} phien={phien} />
        <div className="phu" style={{ marginTop: 20 }}>Màn hình này chỉ hiện sản phẩm cuối (các khối “Xuất…”). Muốn xem AI nhận gì, gọi công cụ gì, tốn bao nhiêu từng khối: bấm “Về màn hình dựng”.</div>
      </div>
    </div>
  );
}
