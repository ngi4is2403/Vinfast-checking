# -*- coding: utf-8 -*-
# =============================================================================
# modules/parking_service.py — Dịch vụ gửi xe, lấy xe, sạc điện và vận hành bãi
#
# Module xử lý toàn bộ nghiệp vụ chính của hệ thống:
#
#   [USER]
#   - get_available_slots()    : Lấy danh sách vị trí trống, lọc theo loại xe
#   - create_parking_order()   : Tạo đơn gửi xe (xe vào bãi)
#   - checkout_parking()       : Lấy xe, tính phí và thanh toán
#   - create_charging_order()  : Bắt đầu sạc xe điện
#   - end_charging()           : Kết thúc sạc, tính phí theo kWh
#   - get_user_history()       : Lịch sử giao dịch có bộ lọc
#
#   [ADMIN]
#   - admin_get_active_parking()   : Xem tất cả xe đang trong bãi
#   - admin_confirm_checkout()     : Cho xe ra bãi thay user
#   - admin_get_active_charging()  : Xem tất cả đơn sạc đang hoạt động
#   - admin_confirm_end_charging() : Kết thúc sạc thay user
#   - get_all_slots/stations()     : Danh sách đầy đủ vị trí và trụ sạc
#   - add/update slot/station      : Thêm và đổi trạng thái vị trí/trụ
# =============================================================================

import math
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime
from database import get_db, row_to_dict, rows_to_dicts
from config import PARKING_RATES, CHARGING_RATES, SMALL_VEHICLES, LARGE_VEHICLES, ELECTRIC_TYPES, CHARGING_RATE_PER_HOUR
from modules.user_service import wallet_deduct

# ────────────────────────────────────────────────────────────────────────────────
# HÀM TIỆN ÍCH
# ────────────────────────────────────────────────────────────────────────────────
def ok(data=None, message="Thanh cong"):
    return {"success": True, "message": message, "data": data}

def err(message="Co loi xay ra"):
    return {"success": False, "message": message, "data": None}

def now_str():
    """Trả về thời gian hiện tại dạng string 'YYYY-MM-DD HH:MM:SS' — tương thích MySQL DATETIME."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def to_dt(v):
    """
    Chuẩn hóa giá trị thông gian: chấp nhận cả string lẫn datetime object.
    PyMySQL trả về DATETIME cột dưới dạng datetime object.
    Hàm này giúp các hàm tính phí hoạt động đúng dù DB trả về kiểu nào.
    """
    if v is None:
        return datetime.now()
    if isinstance(v, datetime):
        return v
    return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")

# =============================================================================
# TÍNH PHÍ DỊCH VỤ
# =============================================================================

def calculate_parking_fee(vehicle_type, time_in, time_out=None):
    """
    Tính phí gửi xe theo công thức:
      Phí = số_ngày_đầy × daily_max + min(giờ_lẻ × per_hour, daily_max)

    Ví dụ: Xe máy đỗ 30 giờ:
      = 1 ngày × 25.000 + min(6 × 5.000, 25.000)
      = 25.000 + 25.000 = 50.000đ

    Chấp nhận time_in/time_out là string hoặc datetime (qua to_dt()).
    """
    rate = PARKING_RATES.get(vehicle_type)
    if not rate:
        return 0
    t_in  = to_dt(time_in)
    t_out = to_dt(time_out) if time_out else datetime.now()

    total_h   = max(0, (t_out - t_in).total_seconds() / 3600)
    full_days = int(total_h // 24)   # Số ngày đầy đủ (mỗi ngày tính theo daily_max)
    rem_h     = total_h % 24          # Giờ lẻ (tính lần lượt và không vượt daily_max)

    fee = full_days * rate["daily_max"] + min(math.ceil(rem_h) * rate["per_hour"], rate["daily_max"])
    return int(fee)

def calculate_charging_fee(charge_type, kwh_consumed):
    """
    Tính phí sạc xe điện (legacy — dùng cho seed data cũ):
      Phí = phí_giữ_chỗ + kWh × đơn_giá/kWh
    """
    rate_kwh = CHARGING_RATES.get(charge_type, 0)
    return int(CHARGING_RATES["reservation_fee"] + kwh_consumed * rate_kwh)

def calculate_charging_fee_by_time(time_start, time_end=None):
    """
    Tính phí sạc theo thời gian (logic mới):
      Phí = ceil(số_giờ_sạc) × CHARGING_RATE_PER_HOUR
    Tối thiểu 1 giờ.
    """
    t_start = to_dt(time_start)
    t_end   = to_dt(time_end) if time_end else datetime.now()
    total_h = max(0, (t_end - t_start).total_seconds() / 3600)
    hours   = max(1, math.ceil(total_h))  # Tối thiểu 1 giờ
    return int(hours * CHARGING_RATE_PER_HOUR)

# ────────────────────────────────────────────────────────────────────────────────
# 1. GỬI XE
# ────────────────────────────────────────────────────────────────────────────────

def get_available_slots(vehicle_type=None, user_id=None):
    """
    Lấy vị trí trống. 
    Nếu có user_id: Lấy thêm cả các slot mà user này đang đặt lịch (reserved).
    """
    conn = get_db()
    cur  = conn.cursor()
    try:
        # SQL cơ bản: lấy slot available
        where_clause = "(status='available')"
        params = []
        
        if user_id:
            # Lấy thêm slot đang được user này giữ chỗ (reserved)
            where_clause = "(status='available' OR (status='reserved' AND id IN (SELECT slot_id FROM bookings WHERE user_id=%s AND status='pending')))"
            params.append(user_id)

        query = f"SELECT * FROM parking_slots WHERE {where_clause}"
        
        if vehicle_type in SMALL_VEHICLES:
            query += " AND slot_type IN ('small','both') ORDER BY has_charging DESC, slot_code"
        elif vehicle_type in LARGE_VEHICLES:
            query += " AND slot_type IN ('large','both') ORDER BY has_charging DESC, slot_code"
        else:
            query += " ORDER BY slot_code"

        cur.execute(query, params)
        return ok(rows_to_dicts(cur.fetchall()))
    finally:
        conn.close()

def create_parking_order(user_id, vehicle_id, slot_id, notes="", want_charging=False):
    # ── Validate đầu vào trước khi chạm DB ─────────────────────────────────
    if not vehicle_id:
        return err("Vui long chon phuong tien.")
    if not slot_id:
        return err("Vui long chon vi tri do xe.")
    # ────────────────────────────────────────────────────────────────────────
    conn = get_db()
    cur  = conn.cursor()
    try:
        # Lấy thông tin xe
        cur.execute("SELECT * FROM vehicles WHERE id=%s AND user_id=%s", (vehicle_id, user_id))
        veh = cur.fetchone()
        if not veh:
            return err("Xe khong ton tai hoac khong thuoc ve ban.")
        vtype = veh["vehicle_type"]

        # ── KIỂM TRA ĐẶT LỊCH (Ưu tiên logic Check-in sớm) ────────────────
        cur.execute(
            "SELECT id FROM bookings WHERE vehicle_id=%s AND status='pending'",
            (vehicle_id,)
        )
        bk = cur.fetchone()
        if bk:
            # Nếu xe đang đỗ sớm rồi → báo lỗi thân thiện
            cur.execute("SELECT id FROM parking_orders WHERE vehicle_id=%s AND status='active'", (vehicle_id,))
            if cur.fetchone():
                return err("Xe nay dang trong bai (check-in som). Vui long lay xe khi muon roi bai.")
            # Chưa vào → redirect sang checkin_booking
            from modules.booking_service import checkin_booking
            bid = bk["id"]
            conn = None   # Đặt None để finally không đóng lần 2
            return checkin_booking(bid, user_id)
        # ──────────────────────────────────────────────────────────────────

        # Kiểm tra xe có đơn gửi xe active (đang đỗ thực sự) không
        cur.execute(
            """SELECT po.id, ps.slot_code, ps.zone, po.time_in
               FROM parking_orders po
               JOIN parking_slots ps ON po.slot_id=ps.id
               WHERE po.vehicle_id=%s AND po.status='active'""",
            (vehicle_id,)
        )
        active = cur.fetchone()
        if active:
            return err(
                f"Xe nay dang duoc gui tai {active['slot_code']} (Khu {active['zone']}) "
                f"tu {str(active['time_in'])[:16]}. "
                f"Vui long lay xe truoc khi gui lai."
            )

        # Kiểm tra slot
        cur.execute("SELECT * FROM parking_slots WHERE id=%s", (slot_id,))
        slot = cur.fetchone()
        if not slot:
            return err("Vi tri do khong ton tai.")
        if slot["status"] != "available":
            return err("Vi tri do khong con trong.")

        # Kiểm tra loại xe khớp slot
        slot_type = slot["slot_type"]
        is_small  = vtype in SMALL_VEHICLES
        if slot_type == "small" and not is_small:
            return err("Vi tri nay chi danh cho xe nho (xe may).")
        if slot_type == "large" and is_small:
            return err("Vi tri nay chi danh cho o to.")

        unit_price = PARKING_RATES[vtype]["per_hour"]

        cur.execute(
            "INSERT INTO parking_orders (user_id,vehicle_id,slot_id,time_in,status,unit_price,notes) VALUES (%s,%s,%s,%s,'active',%s,%s)",
            (user_id, vehicle_id, slot_id, now_str(), unit_price, notes)
        )
        order_id = cur.lastrowid
        cur.execute("UPDATE parking_slots SET status='occupied' WHERE id=%s", (slot_id,))

        charging_order_id = None
        # Nếu user chọn sạc và slot có sạc (Khu B)
        if want_charging and slot["has_charging"]:
            # Kiểm tra xe có đơn sạc active không
            cur.execute("SELECT id FROM charging_orders WHERE vehicle_id=%s AND status='active'", (vehicle_id,))
            if not cur.fetchone():
                # Tìm trụ sạc trống (bất kỳ loại nào)
                cur.execute("SELECT * FROM charging_stations WHERE status='available' ORDER BY station_code LIMIT 1")
                station = cur.fetchone()
                if station:
                    cur.execute(
                        "INSERT INTO charging_orders (user_id,vehicle_id,station_id,charge_type,time_start,status) VALUES (%s,%s,%s,%s,%s,'active')",
                        (user_id, vehicle_id, station["id"], station["station_type"], now_str())
                    )
                    charging_order_id = cur.lastrowid
                    cur.execute("UPDATE charging_stations SET status='busy' WHERE id=%s", (station["id"],))

        conn.commit()
        msg = "Tao don gui xe thanh cong!"
        if charging_order_id:
            msg += " Da bat dau sac xe."
        return ok({"order_id": order_id, "charging_order_id": charging_order_id}, msg)
    except Exception as e:
        if conn:
            conn.rollback()
        return err(f"Loi he thong: {e}")
    finally:
        if conn:
            conn.close()

# ────────────────────────────────────────────────────────────────────────────────
# 2. LẤY XE
# ────────────────────────────────────────────────────────────────────────────────

def get_active_parking_order(user_id, vehicle_id=None):
    conn = get_db()
    cur  = conn.cursor()
    try:
        if vehicle_id:
            cur.execute(
                """SELECT po.*, v.plate_number, v.vehicle_type, v.brand, v.model,
                          ps.slot_code, ps.zone, ps.floor_area
                   FROM parking_orders po
                   JOIN vehicles v ON po.vehicle_id=v.id
                   JOIN parking_slots ps ON po.slot_id=ps.id
                   WHERE po.user_id=%s AND po.vehicle_id=%s AND po.status='active'""",
                (user_id, vehicle_id)
            )
        else:
            cur.execute(
                """SELECT po.*, v.plate_number, v.vehicle_type, v.brand, v.model,
                          ps.slot_code, ps.zone, ps.floor_area
                   FROM parking_orders po
                   JOIN vehicles v ON po.vehicle_id=v.id
                   JOIN parking_slots ps ON po.slot_id=ps.id
                   WHERE po.user_id=%s AND po.status='active'""",
                (user_id,)
            )
        return ok(rows_to_dicts(cur.fetchall()))
    finally:
        conn.close()

def checkout_parking(order_id, user_id):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT po.*, v.vehicle_type FROM parking_orders po
               JOIN vehicles v ON po.vehicle_id=v.id
               WHERE po.id=%s AND po.user_id=%s AND po.status='active'""",
            (order_id, user_id)
        )
        order = cur.fetchone()
        if not order:
            return err("Don gui xe khong ton tai hoac da hoan tat.")

        t_out       = now_str()
        slot_id     = order["slot_id"]
        vehicle_id  = order["vehicle_id"]

        # ── Tính trước toàn bộ phí để kiểm tra số dư ────────────────────────
        booking_credit  = int(order.get("booking_credit") or 0)
        linked_booking  = order.get("booking_id")
        parking_fee_raw = calculate_parking_fee(order["vehicle_type"], order["time_in"], t_out)
        bk              = None

        # Tính parking_fee (phần thực thu ngoài booking credit)
        if linked_booking and booking_credit > 0:
            cur.execute("SELECT * FROM bookings WHERE id=%s", (linked_booking,))
            bk = cur.fetchone()
            if bk:
                from datetime import timedelta as td
                time_in_dt   = to_dt(order["time_in"])
                scheduled_dt = to_dt(bk["scheduled_time"])
                expire_dt    = scheduled_dt + td(hours=float(bk["duration_hours"]))
                t_out_dt     = to_dt(t_out)

                early_hours = max(0.0, (min(scheduled_dt, t_out_dt) - time_in_dt).total_seconds() / 3600)
                early_fee   = calculate_parking_fee(
                    order["vehicle_type"],
                    time_in_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    min(scheduled_dt, t_out_dt).strftime("%Y-%m-%d %H:%M:%S")
                ) if early_hours > 0 else 0

                extra_hours = max(0.0, (t_out_dt - expire_dt).total_seconds() / 3600)
                extra_fee   = calculate_parking_fee(
                    order["vehicle_type"],
                    expire_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    t_out
                ) if extra_hours > 0 else 0

                parking_fee = early_fee + extra_fee
            else:
                parking_fee = parking_fee_raw
                early_fee   = 0
                extra_fee   = 0
        else:
            parking_fee = parking_fee_raw
            early_fee   = 0
            extra_fee   = 0

        # Tính phí sạc nếu đang sạc
        charging_fee = 0
        cur.execute(
            "SELECT co.*, cs.station_code FROM charging_orders co "
            "JOIN charging_stations cs ON co.station_id=cs.id "
            "WHERE co.vehicle_id=%s AND co.status='active'",
            (vehicle_id,)
        )
        active_charge = cur.fetchone()
        if active_charge:
            charging_fee = calculate_charging_fee_by_time(active_charge["time_start"], t_out)

        total_fee = parking_fee + charging_fee

        # ── KIỂM TRA SỐ DƯ TRƯỚC — nếu không đủ thì BLOCK checkout ─────────
        if total_fee > 0:
            cur.execute("SELECT balance FROM users WHERE id=%s", (user_id,))
            u = cur.fetchone()
            balance = u["balance"] if u else 0
            if balance < total_fee:
                shortfall = total_fee - balance
                detail = []
                if early_fee > 0:
                    detail.append(f"phi den som: {early_fee:,}d")
                if extra_fee > 0:
                    detail.append(f"phi o them: {extra_fee:,}d")
                if charging_fee > 0:
                    detail.append(f"phi sac: {charging_fee:,}d")
                if booking_credit > 0:
                    detail.append(f"(booking da cover: {booking_credit:,}d)")
                detail_str = " | ".join(detail) if detail else f"phi gui xe: {parking_fee:,}d"
                return err(
                    f"So du vi khong du de lay xe! "
                    f"Can thanh toan: {total_fee:,}d ({detail_str}). "
                    f"So du hien tai: {balance:,}d. "
                    f"Vui long nap them it nhat {shortfall:,}d truoc khi lay xe."
                )
        # ────────────────────────────────────────────────────────────────────

        # ── Ghi DB (đã đủ tiền) ─────────────────────────────────────────────
        if linked_booking and bk:
            cur.execute(
                "UPDATE parking_orders SET early_fee=%s WHERE id=%s",
                (early_fee, order_id)
            )
            cur.execute(
                "UPDATE bookings SET status='completed',checkout_at=%s WHERE id=%s",
                (t_out, linked_booking)
            )

        if active_charge:
            cur.execute(
                "UPDATE charging_orders SET time_end=%s,total_fee=%s,status='completed' WHERE id=%s",
                (t_out, charging_fee, active_charge["id"])
            )
            cur.execute("UPDATE charging_stations SET status='available' WHERE id=%s", (active_charge["station_id"],))
            cur.execute(
                "INSERT INTO payments (order_type,order_id,amount,paid_at) VALUES ('charging',%s,%s,%s)",
                (active_charge["id"], charging_fee, t_out)
            )

        cur.execute(
            "UPDATE parking_orders SET time_out=%s,status='completed',total_fee=%s WHERE id=%s",
            (t_out, parking_fee, order_id)
        )
        cur.execute("UPDATE parking_slots SET status='available' WHERE id=%s", (slot_id,))
        cur.execute(
            "INSERT INTO payments (order_type,order_id,amount,paid_at) VALUES ('parking',%s,%s,%s)",
            (order_id, parking_fee, t_out)
        )

        # Trừ tiền ví
        if total_fee > 0:
            deduct_result = wallet_deduct(
                user_id, total_fee,
                f"Thanh toan gui xe + sac: {total_fee:,} VND",
                ref_type='parking', ref_id=order_id,
                conn=conn, cur=cur
            )
            if not deduct_result["success"]:
                conn.rollback()
                return err(deduct_result["message"])

        conn.commit()

        # Thông báo
        if linked_booking and booking_credit > 0:
            msg = (f"Lay xe thanh cong! "
                   f"Booking da cover: {booking_credit:,}d. "
                   f"Phi them (den som / o qua gio): {parking_fee:,}d.")
            if charging_fee > 0:
                msg += f" Phi sac: {charging_fee:,}d."
        else:
            msg = f"Lay xe thanh cong! Phi gui: {parking_fee:,} VND"
            if charging_fee > 0:
                msg += f" + Phi sac: {charging_fee:,} VND"
            msg += f" = Tong: {total_fee:,} VND (da tru tu vi)"

        return ok({
            "total_fee": total_fee, "parking_fee": parking_fee,
            "charging_fee": charging_fee, "booking_credit": booking_credit,
            "time_out": t_out
        }, msg)

    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────────
# 3. THUÊ SẠC
# ────────────────────────────────────────────────────────────────────────────────

def get_available_stations(charge_type=None):
    conn = get_db()
    cur  = conn.cursor()
    try:
        if charge_type in ("slow", "fast"):
            cur.execute(
                "SELECT * FROM charging_stations WHERE status='available' AND station_type=%s ORDER BY station_code",
                (charge_type,)
            )
        else:
            cur.execute(
                "SELECT * FROM charging_stations WHERE status='available' ORDER BY station_type, station_code"
            )
        return ok(rows_to_dicts(cur.fetchall()))
    finally:
        conn.close()

def create_charging_order(user_id, vehicle_id, station_id, charge_type):
    conn = get_db()
    cur  = conn.cursor()
    try:
        # Kiểm tra xe điện
        cur.execute("SELECT * FROM vehicles WHERE id=%s AND user_id=%s", (vehicle_id, user_id))
        veh = cur.fetchone()
        if not veh:
            return err("Xe khong ton tai hoac khong thuoc ve ban.")
        if veh["vehicle_type"] not in ELECTRIC_TYPES:
            return err("Chi xe dien moi co the su dung dich vu sac.")

        # Kiểm tra đơn sạc active
        cur.execute("SELECT id FROM charging_orders WHERE vehicle_id=%s AND status='active'", (vehicle_id,))
        if cur.fetchone():
            return err("Xe nay dang co don sac chua hoan tat.")

        # Kiểm tra trụ sạc
        cur.execute("SELECT * FROM charging_stations WHERE id=%s", (station_id,))
        station = cur.fetchone()
        if not station:
            return err("Tru sac khong ton tai.")
        if station["status"] != "available":
            return err("Tru sac dang ban hoac bao tri.")
        if station["station_type"] != charge_type:
            return err(f"Tru nay la loai {station['station_type']}, khong phai {charge_type}.")

        cur.execute(
            "INSERT INTO charging_orders (user_id,vehicle_id,station_id,charge_type,time_start,status) VALUES (%s,%s,%s,%s,%s,'active')",
            (user_id, vehicle_id, station_id, charge_type, now_str())
        )
        order_id = cur.lastrowid
        cur.execute("UPDATE charging_stations SET status='busy' WHERE id=%s", (station_id,))
        conn.commit()
        return ok({"order_id": order_id}, "Da tao don sac. Xin moi ket noi xe!")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def end_charging(order_id, user_id, kwh_consumed=0):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT co.*, cs.station_code FROM charging_orders co
               JOIN charging_stations cs ON co.station_id=cs.id
               WHERE co.id=%s AND co.user_id=%s AND co.status='active'""",
            (order_id, user_id)
        )
        order = cur.fetchone()
        if not order:
            return err("Don sac khong ton tai hoac da hoan tat.")

        t_end = now_str()
        # Tính phí theo thời gian (logic mới)
        fee = calculate_charging_fee_by_time(order["time_start"], t_end)

        cur.execute(
            "UPDATE charging_orders SET time_end=%s,kwh_consumed=%s,total_fee=%s,status='completed' WHERE id=%s",
            (t_end, 0, fee, order_id)
        )
        cur.execute("UPDATE charging_stations SET status='available' WHERE id=%s", (order["station_id"],))
        cur.execute(
            "INSERT INTO payments (order_type,order_id,amount,paid_at) VALUES ('charging',%s,%s,%s)",
            (order_id, fee, t_end)
        )

        # Trừ tiền ví
        if fee > 0:
            deduct_result = wallet_deduct(
                user_id, fee,
                f"Thanh toan sac xe: {fee:,} VND",
                ref_type='charging', ref_id=order_id,
                conn=conn, cur=cur
            )
            if not deduct_result["success"]:
                conn.rollback()
                return err(deduct_result["message"])

        conn.commit()
        return ok({"total_fee": fee},
                  f"Sac hoan tat! Phi: {fee:,} VND (da tru tu vi)")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

# ────────────────────────────────────────────────────────────────────────────────
# 4. LỊCH SỬ GIAO DỊCH (User)
# ────────────────────────────────────────────────────────────────────────────────

def get_user_history(user_id, month=None, year=None, tx_type=None, status=None):
    conn = get_db()
    cur  = conn.cursor()
    try:
        parking_orders  = []
        charging_orders = []

        # ── Lịch sử gửi xe ─────────────────────────────────
        if tx_type in (None, "parking"):
            q = """SELECT po.id, 'parking' as tx_type, po.time_in as time_start, po.time_out as time_end,
                          po.status, po.total_fee, v.plate_number, v.vehicle_type, v.brand, v.model,
                          ps.slot_code, ps.zone, po.unit_price
                   FROM parking_orders po
                   JOIN vehicles v ON po.vehicle_id=v.id
                   JOIN parking_slots ps ON po.slot_id=ps.id
                   WHERE po.user_id=%s"""
            p = [user_id]
            if month:                                      # Lọc tháng độc lập
                q += " AND MONTH(po.time_in)=%s"
                p.append(month)
            if year:                                       # Lọc năm độc lập
                q += " AND YEAR(po.time_in)=%s"
                p.append(year)
            if status:
                q += " AND po.status=%s"
                p.append(status)
            q += " ORDER BY po.time_in DESC"
            cur.execute(q, p)
            parking_orders = rows_to_dicts(cur.fetchall())

        # ── Lịch sử sạc xe ─────────────────────────────────
        if tx_type in (None, "charging"):
            q = """SELECT co.id, 'charging' as tx_type, co.time_start, co.time_end,
                          co.status, co.total_fee, v.plate_number, v.vehicle_type, v.brand, v.model,
                          cs.station_code, co.charge_type, co.kwh_consumed
                   FROM charging_orders co
                   JOIN vehicles v ON co.vehicle_id=v.id
                   JOIN charging_stations cs ON co.station_id=cs.id
                   WHERE co.user_id=%s"""
            p = [user_id]
            if month:                                      # Lọc tháng độc lập
                q += " AND MONTH(co.time_start)=%s"
                p.append(month)
            if year:                                       # Lọc năm độc lập
                q += " AND YEAR(co.time_start)=%s"
                p.append(year)
            if status:
                q += " AND co.status=%s"
                p.append(status)
            q += " ORDER BY co.time_start DESC"
            cur.execute(q, p)
            charging_orders = rows_to_dicts(cur.fetchall())

        # ── Tổng chi tiêu theo bộ lọc đang áp dụng ─────────────────
        pq = ("SELECT COALESCE(SUM(p.amount),0) as total FROM payments p "
              "JOIN parking_orders po ON p.order_type='parking' AND p.order_id=po.id "
              "WHERE po.user_id=%s")
        pp = [user_id]
        if month: pq += " AND MONTH(po.time_in)=%s"; pp.append(month)
        if year:  pq += " AND YEAR(po.time_in)=%s";  pp.append(year)
        cur.execute(pq, pp)
        total_parking = cur.fetchone()["total"] or 0

        cq = ("SELECT COALESCE(SUM(p.amount),0) as total FROM payments p "
              "JOIN charging_orders co ON p.order_type='charging' AND p.order_id=co.id "
              "WHERE co.user_id=%s")
        cp = [user_id]
        if month: cq += " AND MONTH(co.time_start)=%s"; cp.append(month)
        if year:  cq += " AND YEAR(co.time_start)=%s";  cp.append(year)
        cur.execute(cq, cp)
        total_charging = cur.fetchone()["total"] or 0

        return ok({
            "parking_orders":  parking_orders,
            "charging_orders": charging_orders,
            "total_parking":   int(total_parking),
            "total_charging":  int(total_charging),
            "total_spent":     int(total_parking) + int(total_charging),
        })
    finally:
        conn.close()

# ────────────────────────────────────────────────────────────────────────────────
# 5. ADMIN — Vận hành bãi
# ────────────────────────────────────────────────────────────────────────────────

def admin_get_active_parking(search_plate=None):
    conn = get_db()
    cur  = conn.cursor()
    try:
        q = """SELECT po.id as order_id, po.time_in, po.unit_price,
                      v.plate_number, v.vehicle_type, v.brand, v.model, v.color,
                      u.full_name as owner_name, u.phone as owner_phone,
                      ps.slot_code, ps.zone, ps.floor_area
               FROM parking_orders po
               JOIN vehicles v ON po.vehicle_id=v.id
               JOIN users u ON po.user_id=u.id
               JOIN parking_slots ps ON po.slot_id=ps.id
               WHERE po.status='active'"""
        p = []
        if search_plate:
            q += " AND v.plate_number LIKE %s"
            p.append(f"%{search_plate.strip().upper()}%")
        q += " ORDER BY po.time_in DESC"
        cur.execute(q, p)
        rows = rows_to_dicts(cur.fetchall())

        now = datetime.now()
        for r in rows:
            t_in = to_dt(r["time_in"])
            r["duration_hours"] = round((now - t_in).total_seconds() / 3600, 1)
            r["estimated_fee"]  = calculate_parking_fee(r["vehicle_type"], r["time_in"])
        return ok(rows)
    finally:
        conn.close()

def admin_confirm_checkout(order_id):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT po.*, v.vehicle_type FROM parking_orders po "
            "JOIN vehicles v ON po.vehicle_id=v.id "
            "WHERE po.id=%s AND po.status='active'", (order_id,)
        )
        order = cur.fetchone()
        if not order:
            return err("Don gui xe khong ton tai hoac da hoan tat.")

        t_out       = now_str()
        parking_fee = calculate_parking_fee(order["vehicle_type"], order["time_in"], t_out)
        vehicle_id  = order["vehicle_id"]
        user_id     = order["user_id"]

        # Tự kết thúc sạc nếu có
        charging_fee = 0
        cur.execute(
            "SELECT co.* FROM charging_orders co WHERE co.vehicle_id=%s AND co.status='active'",
            (vehicle_id,)
        )
        active_charge = cur.fetchone()
        if active_charge:
            charging_fee = calculate_charging_fee_by_time(active_charge["time_start"], t_out)
            cur.execute(
                "UPDATE charging_orders SET time_end=%s,total_fee=%s,status='completed' WHERE id=%s",
                (t_out, charging_fee, active_charge["id"])
            )
            cur.execute("UPDATE charging_stations SET status='available' WHERE id=%s", (active_charge["station_id"],))
            cur.execute(
                "INSERT INTO payments (order_type,order_id,amount,paid_at) VALUES ('charging',%s,%s,%s)",
                (active_charge["id"], charging_fee, t_out)
            )

        total_fee = parking_fee + charging_fee

        cur.execute(
            "UPDATE parking_orders SET time_out=%s,status='completed',total_fee=%s WHERE id=%s",
            (t_out, parking_fee, order_id)
        )
        cur.execute("UPDATE parking_slots SET status='available' WHERE id=%s", (order["slot_id"],))
        cur.execute(
            "INSERT INTO payments (order_type,order_id,amount,paid_at) VALUES ('parking',%s,%s,%s)",
            (order_id, parking_fee, t_out)
        )

        # Trừ tiền ví user
        if total_fee > 0:
            wallet_deduct(
                user_id, total_fee,
                f"Admin checkout: {total_fee:,} VND",
                ref_type='parking', ref_id=order_id,
                conn=conn, cur=cur
            )

        conn.commit()
        msg = f"Xe da ra bai. Phi gui: {parking_fee:,}"
        if charging_fee > 0:
            msg += f" + sac: {charging_fee:,}"
        msg += f" = Tong: {total_fee:,} VND"
        return ok({"total_fee": total_fee}, msg)
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def get_user_active_charging(user_id):
    """Lấy đơn sạc đang active của user (dùng trong route /user/charge)."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT co.id, co.charge_type, co.time_start, cs.station_code
               FROM charging_orders co
               JOIN charging_stations cs ON co.station_id=cs.id
               WHERE co.user_id=%s AND co.status='active'""",
            (user_id,)
        )
        return rows_to_dicts(cur.fetchall())
    finally:
        conn.close()

def admin_get_active_charging():
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT co.id as order_id, co.time_start, co.charge_type,
                      v.plate_number, v.vehicle_type, v.brand, v.model,
                      u.full_name as owner_name, u.phone as owner_phone,
                      cs.station_code, cs.station_type, cs.power_kw
               FROM charging_orders co
               JOIN vehicles v ON co.vehicle_id=v.id
               JOIN users u ON co.user_id=u.id
               JOIN charging_stations cs ON co.station_id=cs.id
               WHERE co.status='active'
               ORDER BY co.time_start DESC"""
        )
        rows = rows_to_dicts(cur.fetchall())
        now  = datetime.now()
        for r in rows:
            t_start = to_dt(r["time_start"])
            r["duration_hours"] = round((now - t_start).total_seconds() / 3600, 1)
        return ok(rows)
    finally:
        conn.close()

def admin_confirm_end_charging(order_id, kwh_consumed=0):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM charging_orders WHERE id=%s AND status='active'", (order_id,)
        )
        order = cur.fetchone()
        if not order:
            return err("Don sac khong ton tai hoac da hoan tat.")

        t_end = now_str()
        fee   = calculate_charging_fee_by_time(order["time_start"], t_end)

        cur.execute(
            "UPDATE charging_orders SET time_end=%s,kwh_consumed=0,total_fee=%s,status='completed' WHERE id=%s",
            (t_end, fee, order_id)
        )
        cur.execute("UPDATE charging_stations SET status='available' WHERE id=%s", (order["station_id"],))
        cur.execute(
            "INSERT INTO payments (order_type,order_id,amount,paid_at) VALUES ('charging',%s,%s,%s)",
            (order_id, fee, t_end)
        )

        # Trừ tiền ví user
        if fee > 0:
            wallet_deduct(
                order["user_id"], fee,
                f"Admin ket thuc sac: {fee:,} VND",
                ref_type='charging', ref_id=order_id,
                conn=conn, cur=cur
            )

        conn.commit()
        return ok({"total_fee": fee}, f"Ket thuc sac. Phi: {fee:,} VND (tru tu vi)")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

# ────────────────────────────────────────────────────────────────────────────────
# 6. ADMIN — Slots & Stations
# ────────────────────────────────────────────────────────────────────────────────

def get_all_slots():
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT * FROM parking_slots ORDER BY zone, slot_code")
        return ok(rows_to_dicts(cur.fetchall()))
    finally:
        conn.close()

def add_slot(slot_code, slot_type, floor_area, zone, has_charging=0, notes=""):
    conn = get_db()
    cur  = conn.cursor()
    try:
        code = slot_code.strip().upper()
        cur.execute("SELECT id FROM parking_slots WHERE slot_code=%s", (code,))
        if cur.fetchone():
            return err("Ma vi tri da ton tai.")
        if zone not in ("A", "B"):
            return err("Khu vuc phai la A hoac B.")
        if slot_type not in ("small", "large", "both"):
            return err("Loai vi tri khong hop le.")
        cur.execute(
            "INSERT INTO parking_slots (slot_code,slot_type,floor_area,zone,has_charging,status,notes) VALUES (%s,%s,%s,%s,%s,'available',%s)",
            (code, slot_type, floor_area, zone, int(has_charging), notes)
        )
        conn.commit()
        return ok({"slot_id": cur.lastrowid}, "Them vi tri do thanh cong!")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def update_slot_status(slot_id, status):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id FROM parking_slots WHERE id=%s", (slot_id,))
        if not cur.fetchone():
            return err("Vi tri do khong ton tai.")
        cur.execute("UPDATE parking_slots SET status=%s WHERE id=%s", (status, slot_id))
        conn.commit()
        return ok(message=f"Da cap nhat trang thai vi tri -> {status}")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def get_all_stations():
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT * FROM charging_stations ORDER BY station_code")
        return ok(rows_to_dicts(cur.fetchall()))
    finally:
        conn.close()

def add_station(station_code, station_type, power_kw, area="Khu B"):
    conn = get_db()
    cur  = conn.cursor()
    try:
        code = station_code.strip().upper()
        cur.execute("SELECT id FROM charging_stations WHERE station_code=%s", (code,))
        if cur.fetchone():
            return err("Ma tru sac da ton tai.")
        if station_type not in ("slow", "fast"):
            return err("Loai tru phai la 'slow' hoac 'fast'.")
        cur.execute(
            "INSERT INTO charging_stations (station_code,station_type,power_kw,status,area) VALUES (%s,%s,%s,'available',%s)",
            (code, station_type, power_kw, area)
        )
        conn.commit()
        return ok({"station_id": cur.lastrowid}, "Them tru sac thanh cong!")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def update_station_status(station_id, status):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id FROM charging_stations WHERE id=%s", (station_id,))
        if not cur.fetchone():
            return err("Tru sac khong ton tai.")
        cur.execute("UPDATE charging_stations SET status=%s WHERE id=%s", (status, station_id))
        conn.commit()
        return ok(message=f"Da cap nhat trang thai tru sac -> {status}")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def get_all_parking_orders(status_filter=None):
    conn = get_db()
    cur  = conn.cursor()
    try:
        q = """SELECT po.id, po.time_in, po.time_out, po.status, po.total_fee, po.unit_price,
                      v.plate_number, v.vehicle_type, v.brand,
                      u.full_name, u.phone,
                      ps.slot_code, ps.zone
               FROM parking_orders po
               JOIN vehicles v ON po.vehicle_id=v.id
               JOIN users u ON po.user_id=u.id
               JOIN parking_slots ps ON po.slot_id=ps.id"""
        p = []
        if status_filter:
            q += " WHERE po.status=%s"
            p.append(status_filter)
        q += " ORDER BY po.time_in DESC LIMIT 100"
        cur.execute(q, p)
        rows = rows_to_dicts(cur.fetchall())
        now = datetime.now()
        for r in rows:
            if r["status"] == "active":
                t_in = to_dt(r["time_in"])
                r["duration_hours"] = round((now - t_in).total_seconds() / 3600, 1)
                r["estimated_fee"]  = calculate_parking_fee(r["vehicle_type"], r["time_in"])
        return ok(rows)
    finally:
        conn.close()

def get_all_charging_orders(status_filter=None):
    conn = get_db()
    cur  = conn.cursor()
    try:
        q = """SELECT co.id, co.time_start, co.time_end, co.status, co.total_fee,
                      co.charge_type, co.kwh_consumed,
                      v.plate_number, v.vehicle_type, v.brand,
                      u.full_name, u.phone,
                      cs.station_code, cs.station_type
               FROM charging_orders co
               JOIN vehicles v ON co.vehicle_id=v.id
               JOIN users u ON co.user_id=u.id
               JOIN charging_stations cs ON co.station_id=cs.id"""
        p = []
        if status_filter:
            q += " WHERE co.status=%s"
            p.append(status_filter)
        q += " ORDER BY co.time_start DESC LIMIT 100"
        cur.execute(q, p)
        rows = rows_to_dicts(cur.fetchall())
        now = datetime.now()
        for r in rows:
            if r["status"] == "active":
                t_start = to_dt(r["time_start"])
                r["duration_hours"] = round((now - t_start).total_seconds() / 3600, 1)
        return ok(rows)
    finally:
        conn.close()
