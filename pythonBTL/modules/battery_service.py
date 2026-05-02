# =============================================================================
# battery_service.py — Dịch vụ Pin xe điện: Mua pin & Đổi pin nhanh
#
# Hai dịch vụ tích hợp trong một luồng:
#
#   1. MUA PIN MỚI (new_purchase)
#      Mua pin nguyên hộp cho xe điện, trả một lần, bảo hành 2 năm.
#      Giá: e_motorcycle = 3.500.000đ | e_car = 25.000.000đ
#
#   2. ĐỔI PIN NHANH (swap)
#      Mang pin cạn → trả lại quầy → nhận pin đã sạc sẵn → đi ngay, không chờ.
#      Phí theo % dung lượng pin nhận về:
#        50%  → 150.000đ
#        75%  → 220.000đ
#        100% → 300.000đ
# =============================================================================

from database import get_db, rows_to_dicts, row_to_dict
from modules.user_service import get_wallet_balance

# ─────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH GIÁ
# ─────────────────────────────────────────────────────────────────────────────

# Giá mua pin mới (VNĐ) — theo loại xe điện
BATTERY_PRICES = {
    "e_motorcycle": 3_500_000,
    "e_car":        25_000_000,
}

# Phí đổi pin nhanh (VNĐ) — theo % dung lượng pin nhận về
# Không phân biệt loại xe (xe máy/ô tô điện đều dùng bảng này)
SWAP_PRICES = {
    50:  150_000,   # Pin 50% đầy  → 150.000đ
    75:  220_000,   # Pin 75% đầy  → 220.000đ
    100: 300_000,   # Pin 100% đầy → 300.000đ
}

# Nhãn tiếng Việt cho từng mức đổi
SWAP_LABELS = {
    50:  "50% dung lượng",
    75:  "75% dung lượng",
    100: "Đầy 100%",
}


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def get_battery_catalog():
    """Trả về toàn bộ thông tin giá pin và đổi pin."""
    return {
        "success": True,
        "data": {
            "battery_prices": BATTERY_PRICES,
            "swap_prices":    SWAP_PRICES,
            "swap_labels":    SWAP_LABELS,
        }
    }


def create_battery_order(user_id: int,
                          vehicle_id: int,
                          mode: str,               # 'new_purchase' | 'swap'
                          charge_pct: int = 100,   # Chỉ dùng khi mode='swap': 50|75|100
                          notes: str = "") -> dict:
    """
    Tạo đơn dịch vụ pin (mua mới hoặc đổi nhanh) và trừ tiền ví.

    Args:
        user_id    : ID người dùng
        vehicle_id : ID xe điện
        mode       : 'new_purchase' hoặc 'swap'
        charge_pct : % dung lượng pin nhận khi đổi (50 | 75 | 100)
        notes      : Ghi chú tuỳ chọn

    Returns:
        dict { success, message, data }
    """
    conn = get_db()
    cur  = conn.cursor()
    try:
        # ── 1. Lấy thông tin xe ──────────────────────────────────────────────
        cur.execute(
            "SELECT id, vehicle_type, plate_number, brand, model "
            "FROM vehicles WHERE id=%s AND user_id=%s",
            (vehicle_id, user_id)
        )
        veh = row_to_dict(cur.fetchone())
        if not veh:
            return {"success": False,
                    "message": "Không tìm thấy xe hoặc xe không thuộc tài khoản."}
        if veh["vehicle_type"] not in ("e_motorcycle", "e_car"):
            return {"success": False,
                    "message": "Chỉ xe điện mới có thể sử dụng dịch vụ pin."}

        # ── 2. Tính phí theo mode ────────────────────────────────────────────
        if mode == "new_purchase":
            final_price = BATTERY_PRICES[veh["vehicle_type"]]
            charge_pct  = None  # Không dùng cho mua mới
            desc        = f"Mua pin mới — {veh['plate_number']}"
        elif mode == "swap":
            if charge_pct not in SWAP_PRICES:
                return {"success": False,
                        "message": f"Mức dung lượng không hợp lệ: {charge_pct}%"}
            final_price = SWAP_PRICES[charge_pct]
            desc = (f"Đổi pin nhanh {charge_pct}% — {veh['plate_number']}")
        else:
            return {"success": False, "message": "Loại dịch vụ không hợp lệ."}

        # ── 3. Kiểm tra số dư ví ─────────────────────────────────────────────
        balance = get_wallet_balance(user_id)
        if balance < final_price:
            return {
                "success": False,
                "message": (f"Số dư ví không đủ. "
                            f"Cần {final_price:,}đ, ví còn {balance:,}đ.")
            }

        # ── 4. Trừ ví ────────────────────────────────────────────────────────
        new_balance = balance - final_price
        cur.execute("UPDATE users SET balance=%s WHERE id=%s",
                    (new_balance, user_id))

        cur.execute(
            """INSERT INTO wallet_transactions
               (user_id, tx_type, amount, balance_after, description, ref_type)
               VALUES (%s, 'payment', %s, %s, %s, 'battery')""",
            (user_id, final_price, new_balance, desc)
        )

        # ── 5. Ghi đơn battery_orders ────────────────────────────────────────
        cur.execute(
            """INSERT INTO battery_orders
               (user_id, vehicle_id, vehicle_type, mode,
                final_price, charge_pct, status, notes)
               VALUES (%s, %s, %s, %s, %s, %s, 'completed', %s)""",
            (
                user_id, vehicle_id, veh["vehicle_type"],
                mode, final_price, charge_pct, notes,
            )
        )
        order_id = cur.lastrowid

        # Gán ref_id cho wallet_transaction vừa tạo
        cur.execute(
            "UPDATE wallet_transactions SET ref_id=%s "
            "WHERE user_id=%s AND ref_type='battery' ORDER BY id DESC LIMIT 1",
            (order_id, user_id)
        )

        conn.commit()

        if mode == "new_purchase":
            msg = f"🔋 Mua pin mới thành công! Đã thanh toán {final_price:,}đ."
        else:
            msg = (f"🔄 Đổi pin nhanh {charge_pct}% thành công! "
                   f"Đã thanh toán {final_price:,}đ. Nhận pin tại quầy ngay.")
        return {
            "success": True,
            "message": msg,
            "data": {
                "order_id":    order_id,
                "mode":        mode,
                "final_price": final_price,
                "charge_pct":  charge_pct,
            }
        }

    except Exception as e:
        conn.rollback()
        return {"success": False, "message": f"Lỗi hệ thống: {e}"}
    finally:
        conn.close()


def get_user_battery_orders(user_id: int) -> dict:
    """Lấy lịch sử đơn pin của một user."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT bo.*,
                      v.plate_number, v.brand, v.model
               FROM battery_orders bo
               JOIN vehicles v ON bo.vehicle_id = v.id
               WHERE bo.user_id = %s
               ORDER BY bo.created_at DESC""",
            (user_id,)
        )
        return {"success": True, "data": rows_to_dicts(cur.fetchall())}
    finally:
        conn.close()


def admin_get_all_battery_orders(status_f=None) -> dict:
    """Admin: lấy toàn bộ đơn pin."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        sql = """SELECT bo.*, u.full_name, u.phone,
                        v.plate_number, v.brand, v.model
                 FROM battery_orders bo
                 JOIN users u    ON bo.user_id    = u.id
                 JOIN vehicles v ON bo.vehicle_id = v.id"""
        params = []
        if status_f:
            sql += " WHERE bo.status=%s"
            params.append(status_f)
        sql += " ORDER BY bo.created_at DESC"
        cur.execute(sql, params)
        return {"success": True, "data": rows_to_dicts(cur.fetchall())}
    finally:
        conn.close()
