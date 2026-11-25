// Chép từ e9db26a (NGUON.md) — sửa cho backend kpi_agent (bước 2b, bản 2).
// Giữ: hình chung ⇄ React Flow, bố cục (thanh trên · trái 3 tab · khung vẽ · bảng khối · dưới danh sách Kiểm), màu dây theo khối nguồn, Kiểm ⇒ Lưu, bảng ô tự sinh.
// Bỏ: theo dõi sự kiện Temporal, tiền token, Duyệt / Từ chối, phát lại, công cụ AI (khối của mình chỉ TÍNH, chạy xong trong một lần gọi).
// 09/11: bấm Chạy = CHẠY THỬ (không thay số trang chính / trợ lý dùng) · lưu = bảng SQLite workflow_version (backend kiểm lại, không tin trang).
// Thứ tự khối / dây không quan trọng ở trang: backend xếp lại theo dây trước khi kiểm (backend/workflow_edit.py normalize).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow, Background, Controls, MiniMap, addEdge, useEdgesState, useNodesState, useReactFlow,
  type Connection, type Edge, type IsValidConnection,
} from "@xyflow/react";
import {
  api, dong, giay, ngayGio,
  type BlockDef, type CheckItem, type ParamDef, type TrialRun, type VersionRow, type Workflow,
} from "./api";
import { KhoiNode, MAU_NHOM, kyHieu, type KhoiRF } from "./KhoiNode";
import { tuBuoc, TT_VN } from "./trangThai";
import { layoutNodes } from "../workflow/layout";

const nodeTypes = { khoi: KhoiNode };
// màu dây theo khối nguồn (đủ tương phản trên nền trắng; lặp lại khi > 9 nguồn)
const MAU_DAY = ["#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed", "#0891b2", "#65a30d", "#b91c1c", "#475569"];
type Ban = { name: string; version_id: number | null; version: string | null; saved_at?: string };
type XacNhan = { tieuDe: string; noiDung: React.ReactNode; nut: string; nguyHiem?: boolean; lam: () => void };

// Vân tay "có nháp": không phụ thuộc thứ tự khối / dây, không gồm toạ độ (kéo khối không phải thay đổi — cùng luật mã băm logic ở backend)
const vanTay = (w: Workflow) => JSON.stringify({
  n: w.workflow,
  k: [...w.nodes].sort((a, b) => a.id.localeCompare(b.id)).map((n) => [n.id, n.type, Object.entries(n.params || {}).filter(([, v]) => v !== undefined).sort()]),
  e: w.edges.map((e) => [e.from_node, e.from_port, e.to_node, e.to_port].join("|")).sort(),
});

export default function App() {
  const rf = useReactFlow<KhoiRF, Edge>();
  const [blocks, setBlocks] = useState<BlockDef[]>([]);
  const theoLoai = useMemo(() => Object.fromEntries(blocks.map((b) => [b.type, b])), [blocks]);
  const [files, setFiles] = useState<string[]>([]);
  const [dsPb, setDsPb] = useState<VersionRow[]>([]);
  const [pbDangDung, setPbDangDung] = useState<string>("");
  const [dsLuot, setDsLuot] = useState<TrialRun[]>([]);
  const [tabTrai, setTabTrai] = useState<"khoi" | "wf" | "luot">("khoi");
  const [tim, setTim] = useState("");

  const [ban, setBan] = useState<Ban>({ name: "kpi_thuong_nvbh", version_id: null, version: null });
  const [nodes, setNodes, onNodesChange] = useNodesState<KhoiRF>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [daLuu, setDaLuu] = useState("");
  const [kiemDs, setKiemDs] = useState<CheckItem[]>([]);
  const [chon, setChon] = useState<string | null>(null);

  const [luot, setLuot] = useState<TrialRun | null>(null);
  const [xn, setXn] = useState<XacNhan | null>(null);
  const [cheDo, setCheDo] = useState<"dung" | "ket_qua">("dung");
  const [bao, setBao] = useState<{ chu: string; loi?: boolean } | null>(null);
  const [ban_ron, setBanRon] = useState(false);
  const ngayChot = useRef("");   // ô ngày chốt trong hộp xác nhận Chạy (đọc lúc bấm — hộp giữ hàm `lam` cũ)

  const thongBao = (chu: string, loi = false) => { setBao({ chu, loi }); setTimeout(() => setBao(null), loi ? 7000 : 3500); };

  // ---------------- hình chung ⇄ React Flow (hình chung v1: dây đúng 4 khoá; label của khối không gửi đi)
  const hinh = useCallback((): Workflow => ({
    workflow: ban.name,
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.loai, params: n.data.params, position: { x: Math.round(n.position.x), y: Math.round(n.position.y) } })),
    edges: edges.map((e) => ({ from_node: e.source, from_port: e.sourceHandle!, to_node: e.target, to_port: e.targetHandle! })),
  }), [ban.name, nodes, edges]);
  const coNhap = daLuu !== "" ? vanTay(hinh()) !== daLuu : nodes.length > 0;

  const napHinh = useCallback((w: Workflow, pb: { id: number | null; version: string | null; saved_at?: string }, bl: Record<string, BlockDef>) => {
    const viTri = layoutNodes(w.nodes, w.edges);   // file gốc không có position ⇒ xếp như trang 2a
    setBan({ name: w.workflow, version_id: pb.id, version: pb.version, saved_at: pb.saved_at });
    setNodes(w.nodes.map((n) => ({ id: n.id, type: "khoi", position: viTri[n.id], data: { label: bl[n.type]?.label || n.type, loai: n.type, params: n.params || {} } })));
    setEdges(w.edges.map((e) => ({ id: `e-${e.from_node}-${e.to_node}-${e.to_port}`, source: e.from_node, target: e.to_node, sourceHandle: e.from_port, targetHandle: e.to_port })));
    setDaLuu(pb.id ? vanTay(w) : "");
    setKiemDs([]); setChon(null);
    setTimeout(() => rf.fitView({ padding: 0.15, duration: 300, minZoom: 0.8, maxZoom: 1 }), 50);
  }, [rf, setNodes, setEdges]);

  // ---------------- tải ban đầu: danh bạ khối + file được phép + workflow đang dùng (mở sẵn để sửa)
  const lamMoiDs = () => {
    api.versions().then((r) => { setDsPb(r.versions); setPbDangDung(r.current); }).catch(() => {});
    api.trialRuns().then(setDsLuot).catch(() => {});
  };
  useEffect(() => {
    Promise.all([api.blocks(), api.current(), api.versions()]).then(([bs, w, v]) => {
      setBlocks(bs); setDsPb(v.versions); setPbDangDung(v.current);
      const bl = Object.fromEntries(bs.map((b) => [b.type, b]));
      const dangDung = v.versions.find((x) => x.workflow_version === w.workflow_version);
      napHinh(w, { id: dangDung?.workflow_version_id ?? null, version: w.workflow_version, saved_at: dangDung?.created_at }, bl);
    }).catch((e) => thongBao("Không tải được: " + e.message, true));
    api.files().then(setFiles).catch(() => {});
    api.trialRuns().then(setDsLuot).catch(() => {});
  }, []);

  const moPhienBan = async (id: number) => {
    const lam = async () => {
      try {
        const w = await api.version(id);
        napHinh(w, { id: w.workflow_version_id, version: w.workflow_version, saved_at: w.created_at }, theoLoai);
        setLuot(null);
      } catch (e) { thongBao("Không mở được phiên bản: " + (e as Error).message, true); }
    };
    if (coNhap && ban.version_id !== id) setXn({ tieuDe: "Bỏ thay đổi chưa lưu?", noiDung: "Workflow đang mở có thay đổi chưa lưu. Mở phiên bản khác thì mất thay đổi đó.", nut: "Bỏ và mở", nguyHiem: true, lam });
    else await lam();
  };
  const moLuot = async (l: TrialRun) => {
    if (l.workflow_version_id !== ban.version_id) await moPhienBan(l.workflow_version_id);
    setLuot(l);
  };

  // ---------------- trạng thái lượt ⇒ khối / dây
  const kq = useMemo(() => (luot ? tuBuoc(luot.steps, luot.ports) : null), [luot]);
  const loiTheoKhoi = useMemo(() => {
    const m: Record<string, number> = {};
    for (const k of kiemDs) if (k.node && k.muc === "loi") m[k.node] = (m[k.node] || 0) + 1;
    return m;
  }, [kiemDs]);

  // mỗi khối NGUỒN một màu; dây mang màu + tên khối nguồn; ô nhận ghi chấm màu từng nguồn (bài học bên kia 04/11: dây chồng nhau dễ lộn)
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

  const nodesVe = useMemo(() => nodes.map((n) => ({
    ...n, data: { ...n.data, block: theoLoai[n.data.loai], tt: kq?.[n.id], vao: vaoTheoO[n.id], soLoi: loiTheoKhoi[n.id] },
  })), [nodes, kq, theoLoai, loiTheoKhoi, vaoTheoO]);
  const edgesVe = useMemo(() => edges.map((e) => {
    const a = kq?.[e.source]?.s, b = kq?.[e.target]?.s;
    const lop = !kq ? "" : a === "xong" && b === "xong" ? "da-chuyen" : a === "loi" || b === "bo" ? "hong" : "";
    const mau = mauNguon[e.source];
    const ten = tenKhoi(e.source);
    // dây công cụ vẽ nét đứt + nhãn "công cụ" (khác dây số liệu) — hình chung điểm 8: loại dây suy từ cổng đích
    const dayCongCu = !!theoLoai[nodes.find((n) => n.id === e.target)?.data.loai || ""]?.tool_inputs?.some((i) => i.name === e.targetHandle);
    // dây công cụ: mọi khối công cụ cùng tên "Công cụ của trợ lý" ⇒ nhãn ghi TÊN công cụ (ô `tool`), không ghi tên khối
    const tenCongCu = String(nodes.find((n) => n.id === e.source)?.data.params?.tool ?? ten);
    const nhan = dayCongCu ? "công cụ: " + tenCongCu : "từ " + (ten.length > 18 ? ten.slice(0, 17) + "…" : ten);
    return { ...e, className: [lop, dayCongCu ? "day-cong-cu" : ""].join(" ").trim(),
      style: { stroke: mau, strokeWidth: 2.2, ...(dayCongCu ? { strokeDasharray: "6 4" } : {}) },
      label: nhan, labelStyle: { fill: mau, fontWeight: 600 },
      labelBgPadding: [4, 2] as [number, number] };
  }), [edges, kq, mauNguon, tenKhoi, nodes, theoLoai]);

  // ---------------- sửa khung vẽ
  // Luật của bộ chạy mình (runner.check_workflow): mỗi cổng vào ĐÚNG một dây, không vòng — chặn ngay khi nối (backend vẫn kiểm lại khi Lưu)
  const hopLe: IsValidConnection<Edge> = useCallback((c) => {
    if (!c.source || !c.target || c.source === c.target) return false;
    const tgt = nodes.find((n) => n.id === c.target);
    const bDich = tgt && theoLoai[tgt.data.loai];
    const oCongCu = !!bDich?.tool_inputs?.some((i) => i.name === c.targetHandle);
    if (!bDich || !(oCongCu || bDich.inputs.some((i) => i.name === c.targetHandle))) return false;
    // cổng dữ liệu: đúng một dây; cổng công cụ: nhiều dây nhưng không nối trùng cùng một khối nguồn.
    // Nguồn sai loại (vd khối tính ⇒ cổng công cụ, công cụ ⇒ cổng dữ liệu) KHÔNG chặn ở đây — máy chủ kiểm khi Kiểm / Lưu và trang hiện lời của máy chủ
    if (edges.some((e) => e.target === c.target && e.targetHandle === c.targetHandle && (!oCongCu || e.source === c.source))) return false;
    const con: Record<string, string[]> = {};   // có đường từ đích về nguồn ⇒ nối thêm thành vòng
    for (const e of edges) (con[e.source] ??= []).push(e.target);
    const den = [c.target], thay = new Set<string>();
    while (den.length) {
      const x = den.pop()!;
      if (x === c.source) return false;
      if (!thay.has(x)) { thay.add(x); den.push(...(con[x] || [])); }
    }
    return true;
  }, [nodes, edges, theoLoai]);
  const onConnect = useCallback((c: Connection) => setEdges((es) => addEdge({ ...c, id: `e-${c.source}-${c.target}-${c.targetHandle}` }, es)), [setEdges]);

  const themKhoi = (loai: string, pos?: { x: number; y: number }) => {
    const b = theoLoai[loai];
    if (!b) return;
    // mã khối = mã gợi ý của danh bạ (= mã trong file gốc ⇒ ráp lại ra đúng mã băm); trùng ⇒ thêm _2, _3
    let id = b.default_id, k = 2;
    while (nodes.some((n) => n.id === id)) id = `${b.default_id}_${k++}`;
    const p = pos || rf.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 - 60 });
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), { id, type: "khoi", position: p, selected: true, data: { label: b.label, loai, params: {} } }]);
    setChon(id);
  };
  const onDrop = (ev: React.DragEvent) => {
    ev.preventDefault();
    const loai = ev.dataTransfer.getData("application/x-khoi");
    if (loai) themKhoi(loai, rf.screenToFlowPosition({ x: ev.clientX - 110, y: ev.clientY - 30 }));
  };
  const suaKhoi = (id: string, f: (d: KhoiRF["data"]) => KhoiRF["data"]) => setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: f(n.data) } : n)));

  const moi = () => {
    const lam = () => { napHinh({ workflow: "workflow_moi", nodes: [], edges: [] }, { id: null, version: null }, theoLoai); setLuot(null); };
    if (coNhap) setXn({ tieuDe: "Bỏ thay đổi chưa lưu?", noiDung: "Tạo workflow mới thì mất thay đổi chưa lưu.", nut: "Bỏ và tạo mới", nguyHiem: true, lam });
    else lam();
  };

  // ---------------- Kiểm / Lưu / Chạy thử
  const kiemNgay = async () => {
    try {
      const r = await api.check(hinh());
      setKiemDs(r.kiem);
      const nLoi = r.kiem.filter((x) => x.muc === "loi").length;
      thongBao(nLoi ? `Kiểm: ${nLoi} lỗi — xem danh sách bên dưới` : "Kiểm sạch", nLoi > 0);
      return nLoi === 0;
    } catch (e) { thongBao("Kiểm lỗi: " + (e as Error).message, true); return false; }
  };
  const luuNgay = async (): Promise<number | null> => {
    if (!(await kiemNgay())) return null;
    setBanRon(true);
    try {
      const w = hinh();
      const r = await api.save(w);
      setBan((b) => ({ ...b, version_id: r.workflow_version_id, version: r.workflow_version, saved_at: new Date().toISOString() }));
      setDaLuu(vanTay(w));
      thongBao(r.is_new ? `Đã lưu phiên bản mới số ${r.workflow_version_id}` : `Không có gì đổi về tính toán — trùng phiên bản số ${r.workflow_version_id}`);
      lamMoiDs();
      return r.workflow_version_id;
    } catch (e) {
      const b = (e as { body?: { kiem?: CheckItem[] } }).body;
      if (b?.kiem) setKiemDs(b.kiem);
      thongBao("Chưa lưu: " + (e as Error).message, true);
      return null;
    } finally { setBanRon(false); }
  };
  const chayNgay = () => {
    ngayChot.current = "";
    setXn({
      tieuDe: coNhap || !ban.version_id ? "Lưu rồi chạy thử?" : "Chạy thử workflow?",
      noiDung: (
        <div>
          <p style={{ marginTop: 0 }}>Chạy thử <b>{coNhap || !ban.version_id ? "bản sẽ lưu ngay bây giờ" : `phiên bản số ${ban.version_id}`}</b> trên máy này. 0 USD (khối chỉ tính, không gọi AI).</p>
          <p className="phu"><b>Chạy thử không thay số</b> đang hiện ở trang chính và trợ lý dùng — chỉ ghi một lượt chạy thử để xem kết quả từng khối.</p>
          <div className="o"><label htmlFor="ngay-chot">Ngày chốt số<span className="bb"> *</span></label>
            <input id="ngay-chot" type="date" onChange={(e) => { ngayChot.current = e.target.value; }} />
            <div className="goi-y">Bắt buộc — ngày chốt là tham số mỗi lượt, không có mặc định.</div></div>
        </div>
      ),
      nut: coNhap || !ban.version_id ? "Lưu rồi chạy thử" : "Chạy thử",
      lam: async () => {
        const ngay = ngayChot.current;
        const id = coNhap || !ban.version_id ? await luuNgay() : ban.version_id;
        if (!id) return;
        setBanRon(true);
        try {
          const r = await api.run(id, ngay);
          setLuot(r);
          thongBao(r.status === "ok" ? "Chạy thử xong — thành công" : "Chạy thử dừng ở khối lỗi", r.status !== "ok");
          lamMoiDs();
        } catch (e) { thongBao("Không chạy được: " + (e as Error).message, true); }
        finally { setBanRon(false); }
      },
    });
  };

  const khoiChon = nodes.find((n) => n.id === chon) || null;
  const bChon = khoiChon ? theoLoai[khoiChon.data.loai] : undefined;
  const dsDanhMuc = blocks.filter((b) => !tim || (b.label + " " + b.description + " " + b.group).toLowerCase().includes(tim.toLowerCase()));
  const nhom = dsDanhMuc.reduce<Record<string, BlockDef[]>>((m, b) => ((m[b.group] ??= []).push(b), m), {});
  const tongGiay = luot ? luot.steps.reduce((s, b) => s + (b.seconds || 0), 0) : null;

  return (
    <div className="app">
      {/* ===================== thanh trên ===================== */}
      <header className="tren">
        <div className="logo"><i>⛭</i>Sửa workflow KPI</div>
        <input className="ten-wf" value={ban.name} aria-label="Tên workflow" onChange={(e) => setBan((b) => ({ ...b, name: e.target.value }))} />
        <span className="the-pb">
          {ban.version_id ? `phiên bản số ${ban.version_id}${ban.version === pbDangDung ? " (đang dùng cho số trang chính)" : ""}` : "chưa lưu lần nào"}
          {coNhap && <> · <b className="nhap">có thay đổi chưa lưu</b></>}
        </span>
        <span className="cach" />
        {luot && <button className={"nut" + (cheDo === "ket_qua" ? " chinh" : "")} onClick={() => setCheDo(cheDo === "ket_qua" ? "dung" : "ket_qua")}>
          {cheDo === "ket_qua" ? "Về màn hình dựng" : "Kết quả chạy thử"}</button>}
        <button className="nut" onClick={moi}>Mới</button>
        <button className="nut" onClick={kiemNgay} disabled={!nodes.length}>Kiểm</button>
        <button className="nut" onClick={luuNgay} disabled={!nodes.length || ban_ron || (!coNhap && !!ban.version_id)}>Lưu</button>
        <button className="nut chinh" onClick={chayNgay} disabled={!nodes.length || ban_ron}>{coNhap || !ban.version_id ? "Lưu rồi chạy thử" : "Chạy thử"}</button>
      </header>

      <div className="giua">
        {/* ===================== cột trái ===================== */}
        <aside className="trai">
          <div className="tabs">
            <button className={tabTrai === "khoi" ? "on" : ""} onClick={() => setTabTrai("khoi")}>Thêm khối</button>
            <button className={tabTrai === "wf" ? "on" : ""} onClick={() => { setTabTrai("wf"); lamMoiDs(); }}>Phiên bản</button>
            <button className={tabTrai === "luot" ? "on" : ""} onClick={() => { setTabTrai("luot"); lamMoiDs(); }}>Chạy thử</button>
          </div>
          <div className="cuon">
            {tabTrai === "khoi" && <>
              <input className="tim" placeholder="Tìm khối…" value={tim} onChange={(e) => setTim(e.target.value)} />
              <div className="phu" style={{ marginBottom: 6 }}>Kéo khối vào khung vẽ, hoặc bấm đúp để đặt giữa màn hình.</div>
              {Object.entries(nhom).map(([g, bs]) => (
                <div className="nhom" key={g}><h4>{g} · {bs.length}</h4>
                  {bs.map((b) => (
                    <div key={b.type} className="dm-khoi" draggable
                      onDragStart={(e) => { e.dataTransfer.setData("application/x-khoi", b.type); e.dataTransfer.effectAllowed = "move"; }}
                      onDoubleClick={() => themKhoi(b.type)} title={b.description}>
                      <div className="ic" style={{ background: MAU_NHOM[b.group] || "var(--g-tinh)" }}>{kyHieu(b)}</div>
                      <div style={{ minWidth: 0 }}><b>{b.label}</b><p>{b.description}</p></div>
                    </div>
                  ))}
                </div>
              ))}
            </>}
            {tabTrai === "wf" && (dsPb.length ? dsPb.map((v) => (
              <div key={v.workflow_version_id} className={"the" + (v.workflow_version_id === ban.version_id ? " on" : "")} onClick={() => moPhienBan(v.workflow_version_id)}>
                <div className="h"><b>Số {v.workflow_version_id} · {v.workflow}</b>{v.workflow_version === pbDangDung && <span className="chip xong">đang dùng</span>}</div>
                <div className="phu so" title={v.workflow_version}>{ngayGio(v.created_at)}</div>
              </div>
            )) : <p className="phu">Chưa có phiên bản.</p>)}
            {tabTrai === "luot" && (dsLuot.length ? dsLuot.map((l) => (
              <div key={l.trial_run_id} className={"the" + (l.trial_run_id === luot?.trial_run_id ? " on" : "")} onClick={() => moLuot(l)}>
                <div className="h"><b>Lượt thử {l.trial_run_id} · phiên bản {l.workflow_version_id}</b><span className={"chip " + (l.status === "ok" ? "xong" : "loi")}>{l.status === "ok" ? "xong" : "dừng"}</span></div>
                <div className="phu so">{ngayGio(l.created_at)} · chốt {l.cutoff}{l.team_total != null ? ` · ${dong(l.team_total)}` : ""}</div>
              </div>
            )) : <p className="phu">Chưa có lượt chạy thử.</p>)}
          </div>
        </aside>

        {/* ===================== khung vẽ ===================== */}
        <section className="khung" onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }} onDrop={onDrop}>
          <ReactFlow<KhoiRF, Edge>
            nodes={nodesVe} edges={edgesVe} nodeTypes={nodeTypes}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} isValidConnection={hopLe}
            onNodeClick={(_, n) => setChon(n.id)} onPaneClick={() => setChon(null)}
            deleteKeyCode={["Delete", "Backspace"]} minZoom={0.2} maxZoom={2} proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{ type: "smoothstep" }}
          >
            <Background gap={22} size={1.2} color="#e7eaf0" />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(n) => MAU_NHOM[theoLoai[(n.data as KhoiRF["data"]).loai]?.group || ""] || "#8a94a6"} />
          </ReactFlow>
          {cheDo === "ket_qua" && luot && kq && (
            <ManKetQua luot={luot} nodes={nodes} xemKhoi={(id) => { setCheDo("dung"); setChon(id); }} />
          )}
          {!nodes.length && <div className="khung-trong"><div>Kéo khối “Đọc bản luật” vào đây để bắt đầu,<br />hoặc mở một phiên bản ở tab “Phiên bản”.</div></div>}
        </section>

        {/* ===================== cột phải ===================== */}
        <aside className="phai"><div className="cuon" style={{ padding: 0 }}>
          {khoiChon && bChon ? (
            <BangKhoi khoi={khoiChon} b={bChon} x={kq?.[khoiChon.id]} edges={edges} nodes={nodes} files={files} sua={suaKhoi} />
          ) : (
            <>
              <div className="muc">
                <h3>Workflow</h3>
                <div className="kv so"><span>Khối</span><span>{nodes.length}</span><span>Dây</span><span>{edges.length}</span>
                  <span>Phiên bản</span><span title={ban.version || ""}>{ban.version_id ? `số ${ban.version_id}` : "chưa lưu"}</span></div>
              </div>
              {luot && (
                <div className="muc">
                  <h3>Lượt chạy thử đang xem</h3>
                  <div className="kv so">
                    <span>Kết quả</span><span><span className={"chip " + (luot.status === "ok" ? "xong" : "loi")}>{luot.status === "ok" ? "xong" : "dừng ở khối lỗi"}</span></span>
                    <span>Ngày chốt</span><span>{luot.cutoff}</span>
                    <span>Máy chạy</span><span>{giay(tongGiay)}</span>
                    <span>Lương kinh doanh cả đội</span><span>{dong(luot.team_total)}</span>
                  </div>
                  {luot.errors.length > 0 && <div className="hop loi" style={{ marginTop: 8 }}>{luot.errors.join("\n")}</div>}
                </div>
              )}
              {!luot && <div className="muc phu">Bấm một khối để sửa cài đặt. Bấm “Chạy thử” để chạy trên máy — khối đổi trạng thái trên khung vẽ; số trang chính không đổi.</div>}
            </>
          )}
        </div></aside>
      </div>

      {/* ===================== dưới: lỗi kiểm + bước của lượt ===================== */}
      <footer className="duoi">
        <div className="ds">
          <h3 style={{ margin: "2px 0 6px", fontSize: 13 }}>Kiểm {kiemDs.length ? `· ${kiemDs.length} lỗi` : ""}</h3>
          <div className="kiem-ds">
            {kiemDs.length ? kiemDs.map((k, i) => (
              <div key={i} className={k.muc} onClick={() => k.node && setChon(k.node)}>{k.muc === "loi" ? "✕ " : "! "}{k.message}{k.suggestion && <span className="phu"> — {k.suggestion}</span>}</div>
            )) : <span className="phu">Bấm “Kiểm” để kiểm trước khi lưu. Máy chủ kiểm lại khi Lưu / Chạy — trang không tự quyết.</span>}
          </div>
        </div>
        <div className="tg">
          {luot && kq ? (
            <table className="bang"><thead><tr><th>Khối</th><th>Trạng thái</th><th className="so">Thời gian</th><th>Kết quả / lỗi</th></tr></thead>
              <tbody>{luot.steps.map((b) => (
                <tr key={b.node} onClick={() => setChon(b.node)} style={{ cursor: "pointer" }}>
                  <td>{tenKhoi(b.node)} <span className="phu">({b.node})</span></td>
                  <td><span className={"chip " + kq[b.node].s}>{TT_VN[kq[b.node].s]}</span></td>
                  <td className="so">{giay(b.seconds)}</td>
                  <td>{kq[b.node].loi || kq[b.node].ra || ""}</td>
                </tr>))}</tbody></table>
          ) : <span className="phu">Chưa có lượt chạy thử để xem. Bấm “Chạy thử”, hoặc chọn một lượt ở tab “Chạy thử” bên trái.</span>}
        </div>
      </footer>

      {xn && (
        <div className="man" onClick={() => setXn(null)}>
          <div className="hop-xn" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h3>{xn.tieuDe}</h3>
            <div>{xn.noiDung}</div>
            <div className="nut-hang">
              <button className="nut" onClick={() => setXn(null)}>Huỷ</button>
              <button className={"nut " + (xn.nguyHiem ? "do" : "chinh")} onClick={() => { const f = xn.lam; setXn(null); f(); }}>{xn.nut}</button>
            </div>
          </div>
        </div>
      )}
      {bao && <div className={"thong-bao" + (bao.loi ? " loi" : "")} role="status">{bao.chu}</div>}
    </div>
  );
}

// ======================= bảng phải: một khối =======================
function BangKhoi(p: {
  khoi: KhoiRF; b: BlockDef; x?: ReturnType<typeof tuBuoc>[string]; edges: Edge[]; nodes: KhoiRF[]; files: string[];
  sua: (id: string, f: (d: KhoiRF["data"]) => KhoiRF["data"]) => void;
}) {
  const { khoi, b, x } = p;
  const ten = (id: string) => p.nodes.find((n) => n.id === id)?.data.label || id;
  const datO = (name: string, v: unknown) => p.sua(khoi.id, (d) => {
    const params = { ...d.params };
    if (v === undefined) delete params[name]; else params[name] = v;
    return { ...d, params };
  });
  const oForm = (q: ParamDef) => {
    const v = khoi.data.params[q.name];
    let input: React.ReactNode;
    if (q.name === "path") {
      // Ô đường file: CHỈ chọn trong danh sách máy chủ cho phép (backend từ chối mọi đường khác — workflow_edit.allowed_files)
      input = (
        <select value={String(v ?? "")} onChange={(e) => datO(q.name, e.target.value || undefined)}>
          <option value="">— chọn file —</option>
          {p.files.map((f) => <option key={f} value={f}>{f}</option>)}
          {v != null && !p.files.includes(String(v)) && <option value={String(v)}>{String(v)} (không được phép)</option>}
        </select>
      );
    } else if ((q.kind || "").includes("phần trăm")) {
      // trống = null (thưởng vượt "thiếu dữ kiện") — gửi null, không gửi chữ rỗng (bộ kiểm từ chối chữ)
      input = <input type="number" value={v == null ? "" : String(v)} placeholder="để trống" onChange={(e) => datO(q.name, e.target.value === "" ? null : Number(e.target.value))} />;
    } else {
      input = <input type="text" value={String(v ?? "")} onChange={(e) => datO(q.name, e.target.value === "" ? undefined : e.target.value)} />;
    }
    return (
      <div className="o" key={q.name}>
        <label title={`Tên gốc: ${q.name}`}>{q.label}{q.required && <span className="bb"> *</span>}</label>
        {input}
        {q.kind && <div className="goi-y">Nhận: {q.kind}</div>}
      </div>
    );
  };
  const vao = p.edges.filter((e) => e.target === khoi.id);
  return (
    <>
      <div className="muc">
        <h3>{khoi.data.label}</h3>
        <div className="phu">{b.group} · mã khối <code>{khoi.id}</code> · loại <code>{b.type}</code></div>
        <div className="phu" style={{ marginTop: 4 }}>{b.description}</div>
        {b.run_params.includes("cutoff") && <div className="phu" style={{ marginTop: 4 }}>Khối này dùng <b>ngày chốt</b> của lượt chạy (hỏi lúc bấm Chạy thử).</div>}
        {b.run_params.includes("question") && <div className="phu" style={{ marginTop: 4 }}>Khối này cần <b>câu hỏi</b> của lượt chạy và gọi mô hình AI (tốn tiền) — <b>chạy thử trên trang chưa hỗ trợ</b>; máy chủ sẽ từ chối và nói lý do.</div>}
        {vao.length > 0 && <div className="phu" style={{ marginTop: 4 }}>Đang nhận: {vao.map((e) => `${ten(e.source)} ⇒ ${[...b.inputs, ...(b.tool_inputs || [])].find((i) => i.name === e.targetHandle)?.label}`).join(" · ")}</div>}
      </div>
      {x && (
        <div className="muc">
          <h3>Trong lượt chạy thử đang xem <span className={"chip " + x.s}>{TT_VN[x.s]}</span></h3>
          <div className="kv so" style={{ marginBottom: 8 }}><span>Máy chạy</span><span>{giay(x.giay)}</span></div>
          {x.s === "loi" ? <><div className="phu">Lỗi (đã che mã nhân viên):</div><div className="hop loi">{x.loi}</div></>
            : <><div className="phu">Kết quả (tóm tắt — không số theo từng nhân viên)</div><div className="hop">{x.ra || "—"}</div></>}
          {x.canhBao.length > 0 && <><div className="phu" style={{ marginTop: 6 }}>Cảnh báo dữ liệu:</div><div className="hop">{x.canhBao.join("\n")}</div></>}
        </div>
      )}
      <div className="muc">
        <h3>Cài đặt</h3>
        {b.params.length ? b.params.map(oForm) : <div className="phu">Khối này không có ô cài đặt — chỉ nhận dây vào.</div>}
      </div>
    </>
  );
}

// ======================= màn hình "Kết quả chạy thử" =======================
function ManKetQua(p: { luot: TrialRun; nodes: KhoiRF[]; xemKhoi: (id: string) => void }) {
  const kq = tuBuoc(p.luot.steps, p.luot.ports);
  const ten = (id: string) => p.nodes.find((n) => n.id === id)?.data.label || id;
  return (
    <div style={{ position: "absolute", inset: 0, background: "var(--giay)", overflow: "auto", zIndex: 5, padding: "24px 32px" }}>
      <div style={{ maxWidth: 860, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0, fontSize: 20 }}>Lượt chạy thử {p.luot.trial_run_id} — phiên bản số {p.luot.workflow_version_id}</h2>
          <span className={"chip " + (p.luot.status === "ok" ? "xong" : "loi")}>{p.luot.status === "ok" ? "xong" : "dừng ở khối lỗi"}</span>
        </div>
        <div className="phu so" style={{ marginTop: 6 }}>Ngày chốt {p.luot.cutoff} · chạy thử — <b>không</b> thay số trang chính / trợ lý dùng</div>
        {p.luot.team_total != null && <div style={{ marginTop: 16, fontSize: 18 }}>Lương kinh doanh cả đội: <b className="so">{dong(p.luot.team_total)}</b></div>}
        {p.luot.errors.length > 0 && <div className="hop loi" style={{ marginTop: 12 }}>Lượt dừng vì lỗi: {p.luot.errors.join("\n")}</div>}
        {p.luot.steps.map((b) => (
          <div key={b.node} style={{ border: "1px solid var(--vien2)", borderRadius: 10, padding: "12px 16px", marginTop: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
              <b style={{ fontSize: 15 }}>{ten(b.node)}</b><span className={"chip " + kq[b.node].s}>{TT_VN[kq[b.node].s]}</span>
              <span className="cach" style={{ flex: 1 }} />
              <button className="nut nho" onClick={() => p.xemKhoi(b.node)}>Xem khối</button>
            </div>
            {kq[b.node].s === "loi" ? <div className="hop loi">{kq[b.node].loi}</div>
              : kq[b.node].s === "bo" ? <div className="phu">Không có — lượt đã dừng trước khi tới bước này.</div>
              : <div>{kq[b.node].ra || "—"}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
