"""[VÁ 8 chay_thu, 04/11/2026 — nhóm kỹ thuật, User yêu cầu "đầu ra có cấu trúc, pydantic BaseModel"] Khuôn đầu ra cho khối Agent.

Capitol: khối agent chỉ ra CHỮ — không có ô khuôn đầu ra (họ chỉ dùng output_type cho bước nội bộ, vd bản tóm tắt khối duyệt
digest.py:199-201). Workflow phải dặn bằng lời "trả đúng 3 dòng" — AI thêm một câu là khối sau đọc sai.
Cải tiến: ô "Khuôn đầu ra" nhận JSON Schema đơn giản ⇒ dựng pydantic model ⇒ truyền `output_type` cho pydantic-ai: mô hình BẮT BUỘC trả
đúng khuôn (sai thì pydantic-ai bắt làm lại, theo ô `retries`), kết quả là JSON chuẩn đưa sang cổng ra.

Khuôn nhận (con của JSON Schema — đủ cho việc thường gặp, dễ đọc với người không viết code):
  {"type": "object", "properties": {"loai": {"type": "string", "enum": ["doi_hang", "hoi_gia"], "description": "…"},
                                    "phong_ban": {"type": "array", "items": {"type": "string", "enum": ["kho", "sale", "cskh"]}},
                                    "ma_don": {"type": ["string", "null"]}}, "required": ["loai", "phong_ban"]}
  Rút gọn cũng được: {"loai": ["doi_hang", "hoi_gia"], "tom_tat": "string", "so_luong": "integer"}
"""

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, create_model

_SIMPLE = {"string": str, "integer": int, "number": float, "boolean": bool, "object": dict}


class OutputSchemaError(ValueError):
    """Khuôn đầu ra sai — báo rõ cho người dựng workflow, không chạy AI."""


def _type_of(name: str, spec: Any):
    if isinstance(spec, list):                      # dạng rút gọn: danh sách giá trị cho phép
        if not spec:
            raise OutputSchemaError(f"trường '{name}': danh sách giá trị trống")
        return Literal[tuple(spec)], None
    if isinstance(spec, str):                       # dạng rút gọn: tên kiểu
        if spec not in _SIMPLE:
            raise OutputSchemaError(f"trường '{name}': kiểu '{spec}' không hỗ trợ (dùng string / integer / number / boolean / object)")
        return _SIMPLE[spec], None
    if not isinstance(spec, dict):
        raise OutputSchemaError(f"trường '{name}': khai báo phải là object, danh sách hoặc tên kiểu")
    desc = spec.get("description")
    if spec.get("enum"):
        return Literal[tuple(spec["enum"])], desc
    t = spec.get("type", "string")
    nullable = False
    if isinstance(t, list):
        nullable = "null" in t
        t = next((x for x in t if x != "null"), "string")
    if t == "array":
        inner, _ = _type_of(f"{name}[]", spec.get("items") or "string")
        typ = list[inner]
    elif t == "object" and isinstance(spec.get("properties"), dict):
        # Bản 2 (chiều 04/11): object CÓ khai báo trường (vd phần tử trong danh sách "viec" của phiếu kho) trước đây thành dict trơn ⇒
        # mọi luật bên trong (enum, required, kiểu) bị bỏ, khối vẫn SUCCESS dù JSON sai khuôn (bước 19, D tìm gốc). Dựng model con đệ quy.
        typ = _model_from("Obj_" + "".join(ch if ch.isalnum() else "_" for ch in name), spec)
    elif t in _SIMPLE:
        typ = _SIMPLE[t]
    else:
        raise OutputSchemaError(f"trường '{name}': kiểu '{t}' không hỗ trợ")
    return (Optional[typ] if nullable else typ), desc


def build_output_model(schema_text: Any) -> Optional[type[BaseModel]]:
    """Ô trống ⇒ None (giữ hành vi gốc: đầu ra chữ). Khuôn sai ⇒ OutputSchemaError (khối báo lỗi trước khi gọi AI)."""
    if schema_text in (None, "", {}) or (isinstance(schema_text, str) and not schema_text.strip()):
        return None
    try:
        spec = json.loads(schema_text) if isinstance(schema_text, str) else schema_text
    except json.JSONDecodeError as e:
        raise OutputSchemaError(f"Khuôn đầu ra không phải JSON hợp lệ: {e.msg} (dòng {e.lineno})") from e
    if not isinstance(spec, dict):
        raise OutputSchemaError("Khuôn đầu ra phải là một object JSON")
    return _model_from("StructuredOutput", spec)


def _model_from(model_name: str, spec: dict) -> type[BaseModel]:
    """Một object của khuôn ⇒ pydantic model (dùng cho tầng trên và cho object lồng)."""
    props = spec.get("properties") if isinstance(spec.get("properties"), dict) else {k: v for k, v in spec.items() if k not in ("type", "required", "title", "description")}
    if not props:
        raise OutputSchemaError("Khuôn đầu ra chưa có trường nào")
    required = set(spec.get("required", props.keys()))
    fields = {}
    for name, p in props.items():
        if not name.isidentifier():
            raise OutputSchemaError(f"tên trường '{name}' chỉ được gồm chữ không dấu, số, gạch dưới")
        typ, desc = _type_of(name, p)
        fields[name] = (typ, Field(... if name in required else None, description=desc))
    return create_model(model_name, **fields)


def output_to_text(output: Any) -> str:
    """Kết quả agent ⇒ chữ cho cổng ra. Có khuôn ⇒ JSON chuẩn (giữ dấu tiếng Việt), không còn repr của Python."""
    if isinstance(output, BaseModel):
        return json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
    return str(output)
