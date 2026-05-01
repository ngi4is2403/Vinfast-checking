"""
modules/booking_service.py  —  Dịch vụ đặt lịch trước bãi đỗ xe ParkEV
==========================================================================

Luồng nghiệp vụ:
  1. User chọn xe + slot + giờ hẹn + số giờ thuê  →  trả phí NGAY
  2. Đến SỚM (trước scheduled_time)  →  check-in được, phí đến sớm tính thêm khi out
  3. Đến ĐÚNG/MUỘN (trong khung giờ) →  check-in, booking credit đã cover toàn bộ
  4. Hết khung giờ mà không check-in →  no-show, mất trắng phí booking

Quy tắc huỷ:
  - Huỷ trước scheduled_time  →  phạt 30%,  hoàn 70%
  - Huỷ trong khung giờ       →  phạt 50%,  hoàn 50%
  - Sau hết giờ               →  không huỷ được (đã no-show)
"""

import math
from datetime import datetime, timedelta

from database import get_db, row_to_dict, rows_to_dicts
from config   import PARKING_RATES, SMALL_VEHICLES

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _ok(data=None, message="Thành công"):
    return {"success": True,  "message": message, "data": data}

def _err(message="Có lỗi xảy ra"):
    return {"success": False, "message": message, "data": None}

def _now():
    return datetime.now()

def _to_dt(v):
    if isinstance(v, datetime): return v
    return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")

def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def _calc_fee(vehicle_type, hours):
    """Tính phí đỗ xe theo số giờ thực tế."""
    rate = PARKING_RATES.get(vehicle_type)
    if not rate or hours <= 0:
        return 0
    full_days = int(hours // 24)
    rem_h     = hours % 24
    fee = full_days * rate["daily_max"] + min(math.ceil(rem_h) * rate["per_hour"], rate["daily_max"])
    return max(int(fee), 0)

def _calc_fee_between(vehicle_type, dt_start, dt_end):
    """Tính phí đỗ xe giữa 2 mốc thời gian."""
    hours = max(0.0, (_to_dt(dt_end) - _to_dt(dt_start)).total_seconds() / 3600)
    return _calc_fee(vehicle_type, hours)

def _enrich(row, now=None, conn=None):
    """Thêm các trường tính toán vào booking row."""
    if not row: return row
    now       = now or _now()
    sched     = _to_dt(row["scheduled_time"])
    expire_at = sched + timedelta(hours=float(row["duration_hours"]))
    row["expire_at"]           = _fmt(expire_at)
    row["is_before_scheduled"] = now < sched
    row["is_in_window"]        = sched <= now <= expire_at
    row["is_expired"]          = now > expire_at
    row["secs_to_scheduled"]   = max(0, int((sched     - now).total_seconds()))
    row["secs_in_window"]      = max(0, int((expire_at - now).total_seconds()))
    # Phí phạt nếu huỷ ngay bây giờ
    pct = 50 if row["is_in_window"] else 30
    row["cancel_penalty_pct"]  = pct
    row["cancel_penalty_amt"]  = int(row["total_fee"] * pct / 100)
    row["cancel_refund_amt"]   = row["total_fee"] - row["cancel_penalty_amt"]
    # Detect early parking order (đến sớm, parking_order active, booking pending)
    row["has_early_order"] = False
    row["early_order_id"]  = None
    if row.get("status") == "pending" and row["is_before_scheduled"] and conn:
        try:
            c = conn.cursor()
            c.execute(
                "SELECT id FROM parking_orders "
                "WHERE vehicle_id=%s AND slot_id=%s AND status='active' "
                "AND notes LIKE %s LIMIT 1",
                (row.get("vehicle_id"), row.get("slot_id"),
                 f"%[Den som] Dat lich #{row['id']}%")
            )
            early = c.fetchone()
            if early:
                row["has_early_order"] = True
                row["early_order_id"]  = early["id"]
        except Exception:
            pass
    return row

def _refund_to_wallet(cur, user_id, amount, description, booking_id):
    """Hoàn tiền vào ví và ghi wallet_transaction."""
    if amount <= 0: return
    cur.execute("SELECT balance FROM users WHERE id=%s", (user_id,))
    row     = cur.fetchone()
    new_bal = (row["balance"] if row else 0) + amount
    cur.execute("UPDATE users SET balance=%s WHERE id=%s", (new_bal, user_id))
    cur.execute(
        "INSERT INTO wallet_transactions "
        "(user_id,tx_type,amount,balance_after,description,ref_type,ref_id) "
        "VALUES (%s,'topup',%s,%s,%s,'booking',%s)",
        (user_id, amount, new_bal, description, booking_id)
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1. TẠO ĐẶT LỊCH
# ──────────────────────────────────────────────────────────────────────────────

def create_booking(user_id, vehicle_id, slot_id, scheduled_time_str, duration_hours, notes=""):
    """
    Tạo đặt lịch mới và trừ phí ngay từ ví.
    Validate: thời gian phải sau hiện tại, slot còn trống, xe không bận.
    """
    conn = get_db()
    cur  = conn.cursor()
    try:
        # ── Parse & validate thời gian ──
        try:
            sched = datetime.strptime(scheduled_time_str[:16], "%Y-%m-%dT%H:%M")
        except Exception:
            return _err("Định dạng thời gian không hợp lệ (dùng YYYY-MM-DDTHH:MM).")

        now = _now()
        if sched <= now:
            return _err("Thời gian đặt lịch phải sau thời điểm hiện tại.")

        duration_hours = float(duration_hours)
        if duration_hours < 0.5:
            return _err("Thời gian thuê tối thiểu là 30 phút.")
        if duration_hours > 72:
            return _err("Thời gian thuê tối đa là 72 giờ.")

        # ── Lấy xe ──
        cur.execute("SELECT * FROM vehicles WHERE id=%s AND user_id=%s", (vehicle_id, user_id))
        veh = cur.fetchone()
        if not veh:
            return _err("Xe không tồn tại hoặc không thuộc về bạn.")
        vtype = veh["vehicle_type"]

        # ── Kiểm tra xe không bận ──
        cur.execute(
            "SELECT id FROM parking_orders WHERE vehicle_id=%s AND status='active'",
            (vehicle_id,)
        )
        if cur.fetchone():
            return _err("Xe này đang có đơn gửi xe chưa hoàn tất.")
        cur.execute(
            "SELECT id FROM bookings WHERE vehicle_id=%s AND status IN ('pending','active')",
            (vehicle_id,)
        )
        if cur.fetchone():
            return _err("Xe này đã có lịch đặt chưa hoàn tất.")

        # ── Kiểm tra slot ──
        cur.execute("SELECT * FROM parking_slots WHERE id=%s", (slot_id,))
        slot = cur.fetchone()
        if not slot:
            return _err("Vị trí đỗ không tồn tại.")
        if slot["status"] != "available":
            return _err("Vị trí đỗ đã có người đặt hoặc đang bận.")

        # Kiểm tra loại xe phù hợp slot
        is_small = vtype in SMALL_VEHICLES
        stype    = slot["slot_type"]
        if stype == "small" and not is_small:
            return _err("Vị trí này chỉ dành cho xe máy.")
        if stype == "large" and is_small:
            return _err("Vị trí này chỉ dành cho ô tô.")

        # ── Tính phí ──
        total_fee = _calc_fee(vtype, duration_hours)
        if total_fee <= 0:
            total_fee = PARKING_RATES[vtype]["per_hour"]

        # ── Kiểm tra số dư ──
        cur.execute("SELECT balance FROM users WHERE id=%s", (user_id,))
        u = cur.fetchone()
        if not u or u["balance"] < total_fee:
            bal = u["balance"] if u else 0
            return _err(f"Số dư ví không đủ. Cần {total_fee:,}đ, có {bal:,}đ.")

        # ── Trừ ví ──
        new_bal = u["balance"] - total_fee
        cur.execute("UPDATE users SET balance=%s WHERE id=%s", (new_bal, user_id))
        cur.execute(
            "INSERT INTO wallet_transactions "
            "(user_id,tx_type,amount,balance_after,description,ref_type) "
            "VALUES (%s,'payment',%s,%s,%s,'booking')",
            (user_id, total_fee, new_bal,
             f"Dat lich cho do {slot['slot_code']} - {duration_hours}h")
        )

        # ── Tạo booking ──
        cur.execute(
            "INSERT INTO bookings "
            "(user_id,vehicle_id,slot_id,scheduled_time,duration_hours,total_fee,status,notes) "
            "VALUES (%s,%s,%s,%s,%s,%s,'pending',%s)",
            (user_id, vehicle_id, slot_id,
             _fmt(sched), duration_hours, total_fee, notes)
        )
        booking_id = cur.lastrowid

        # ── Giữ slot ──
        cur.execute("UPDATE parking_slots SET status='reserved' WHERE id=%s", (slot_id,))
        conn.commit()

        expire_at = sched + timedelta(hours=duration_hours)
        return _ok(
            {
                "booking_id":     booking_id,
                "total_fee":      total_fee,
                "slot_code":      slot["slot_code"],
                "scheduled_time": _fmt(sched),
                "expire_at":      _fmt(expire_at),
                "duration_hours": duration_hours,
            },
            f"Dat lich thanh cong! Da tru {total_fee:,}d tu vi."
        )
    except Exception as e:
        conn.rollback()
        return _err(f"Loi he thong: {e}")
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 2. CHECK-IN
# ──────────────────────────────────────────────────────────────────────────────

def checkin_booking(booking_id, user_id):
    """
    Check-in cho đặt lịch.

    Đến SỚM (trước scheduled_time):
      → Tạo parking_order THÔNG THƯỜNG (không gắn booking, tính tiền bình thường)
      → Booking vẫn giữ trạng thái 'pending'
      → Khi lấy xe (checkout), hệ thống tự kích hoạt booking nếu đã đến giờ

    Đến ĐÚNG GIỜ / MUỘN (trong khung giờ):
      → Booking check-in thật sự, tạo parking_order có booking_credit
      → Booking chuyển sang 'active'

    Hết khung giờ: không cho check-in.
    """
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT b.*, v.vehicle_type, v.plate_number, ps.slot_code
               FROM bookings b
               JOIN vehicles v  ON b.vehicle_id=v.id
               JOIN parking_slots ps ON b.slot_id=ps.id
               WHERE b.id=%s AND b.user_id=%s AND b.status='pending'""",
            (booking_id, user_id)
        )
        bk = cur.fetchone()
        if not bk:
            return _err("Dat lich khong ton tai hoac khong the check-in.")

        now       = _now()
        sched     = _to_dt(bk["scheduled_time"])
        expire_at = sched + timedelta(hours=float(bk["duration_hours"]))

        if now > expire_at:
            return _err("Da het khung gio dat lich. Khong the check-in nua.")

        vtype      = bk["vehicle_type"]
        unit_price = PARKING_RATES[vtype]["per_hour"]

        if now < sched:
            # ── ĐẾN SỚM: parking thông thường, booking vẫn pending ──────────
            # Kiểm tra xe đã có parking_order active chưa (tránh tạo trùng)
            cur.execute(
                "SELECT id FROM parking_orders WHERE vehicle_id=%s AND status='active'",
                (bk["vehicle_id"],)
            )
            if cur.fetchone():
                mins = int((sched - now).total_seconds() / 60)
                return _err(
                    f"Xe nay da duoc gui (check-in som). "
                    f"Con {mins} phut nua den gio hen ({str(sched)[:16]}). "
                    f"Lay xe khi muon roi bai."
                )
            cur.execute(
                "INSERT INTO parking_orders "
                "(user_id,vehicle_id,slot_id,time_in,status,unit_price,notes) "
                "VALUES (%s,%s,%s,%s,'active',%s,%s)",
                (user_id, bk["vehicle_id"], bk["slot_id"],
                 _fmt(now), unit_price,
                 f"[Den som] Dat lich #{booking_id}")
            )
            parking_order_id = cur.lastrowid
            cur.execute("UPDATE parking_slots SET status='occupied' WHERE id=%s", (bk["slot_id"],))
            # Ghi lại ID order sớm vào notes để checkout biết
            cur.execute(
                "UPDATE bookings SET notes=CONCAT(IFNULL(notes,''),' [early_order:%s]') WHERE id=%s",
                (parking_order_id, booking_id)
            )
            conn.commit()
            mins = int((sched - now).total_seconds() / 60)
            return _ok(
                {"parking_order_id": parking_order_id, "early": True},
                f"Den som {mins} phut! Xe vao bai, tinh phi binh thuong. "
                f"Booking #{booking_id} van giu cho tu {str(bk['scheduled_time'])[:16]}."
            )
        else:
            # ── ĐÚNG GIỜ / MUỘN: Booking check-in thật sự ──────────────────
            cur.execute(
                "INSERT INTO parking_orders "
                "(user_id,vehicle_id,slot_id,time_in,status,unit_price,notes,booking_id,booking_credit) "
                "VALUES (%s,%s,%s,%s,'active',%s,%s,%s,%s)",
                (user_id, bk["vehicle_id"], bk["slot_id"],
                 _fmt(now), unit_price,
                 f"Dat lich #{booking_id}",
                 booking_id, bk["total_fee"])
            )
            parking_order_id = cur.lastrowid
            cur.execute("UPDATE parking_slots SET status='occupied' WHERE id=%s", (bk["slot_id"],))
            cur.execute(
                "UPDATE bookings SET status='active', checkin_at=%s WHERE id=%s",
                (_fmt(now), booking_id)
            )
            conn.commit()
            if now > sched:
                mins = int((now - sched).total_seconds() / 60)
                msg = f"Check-in thanh cong (den muon {mins} phut, con trong khung gio). Booking da cover toan bo phi."
            else:
                msg = "Check-in thanh cong! Dung gio hen. Booking cover toan bo phi."
            return _ok({"parking_order_id": parking_order_id, "early": False}, msg)

    except Exception as e:
        conn.rollback()
        return _err(f"Loi: {e}")
    finally:
        conn.close()



# ──────────────────────────────────────────────────────────────────────────────
# 3. HUỶ ĐẶT LỊCH
# ──────────────────────────────────────────────────────────────────────────────

def cancel_booking(booking_id, user_id):
    """
    Huỷ đặt lịch — chỉ khi còn pending.
    Phí phạt theo thời điểm huỷ:
      Trước scheduled_time → phạt 30%, hoàn 70%
      Trong khung giờ     → phạt 50%, hoàn 50%
    """
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM bookings WHERE id=%s AND user_id=%s AND status='pending'",
            (booking_id, user_id)
        )
        bk = cur.fetchone()
        if not bk:
            return _err("Khong tim thay dat lich hoac khong the huy.")

        now       = _now()
        sched     = _to_dt(bk["scheduled_time"])
        expire_at = sched + timedelta(hours=float(bk["duration_hours"]))

        if now > expire_at:
            return _err("Khung gio da het, khong the huy. He thong se tu xu ly.")

        total_fee   = bk["total_fee"]
        penalty_pct = 0.50 if now >= sched else 0.30
        penalty     = int(total_fee * penalty_pct)
        refund      = total_fee - penalty
        phase       = "trong gio dat" if now >= sched else "truoc gio hen"

        _refund_to_wallet(
            cur, user_id, refund,
            f"Hoan tien huy dat lich #{booking_id} ({phase}, phat {int(penalty_pct*100)}%)",
            booking_id
        )
        cur.execute(
            "UPDATE bookings SET status='cancelled', penalty_fee=%s, refund_fee=%s WHERE id=%s",
            (penalty, refund, booking_id)
        )
        cur.execute("UPDATE parking_slots SET status='available' WHERE id=%s", (bk["slot_id"],))
        conn.commit()
        return _ok(
            {"refund": refund, "penalty": penalty},
            f"Da huy dat lich. Phat {int(penalty_pct*100)}% ({penalty:,}d). Hoan {refund:,}d vao vi."
        )
    except Exception as e:
        conn.rollback()
        return _err(f"Loi: {e}")
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 4. TỰ ĐỘNG ĐÁNH DẤU NO-SHOW
# ──────────────────────────────────────────────────────────────────────────────

def auto_mark_no_show():
    """
    Tự động đánh dấu no_show các booking pending đã hết khung giờ.
    Không hoàn tiền (đã hết hạn mà không check-in và không huỷ).
    """
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT * FROM bookings WHERE status='pending'")
        now     = _now()
        expired = [
            b for b in (cur.fetchall() or [])
            if _to_dt(b["scheduled_time"]) + timedelta(hours=float(b["duration_hours"])) < now
        ]
        for bk in expired:
            cur.execute(
                "UPDATE bookings SET status='no_show', penalty_fee=%s, refund_fee=0 WHERE id=%s",
                (bk["total_fee"], bk["id"])
            )
            cur.execute(
                "UPDATE parking_slots SET status='available' WHERE id=%s",
                (bk["slot_id"],)
            )
        if expired:
            conn.commit()
        return _ok({"processed": len(expired)})
    except Exception as e:
        conn.rollback()
        return _err(f"Loi auto no-show: {e}")
    finally:
        conn.close()


def auto_activate_pending_bookings():
    """
    Gọi mỗi lần user vào trang bookings.
    Tìm các booking pending đã đến scheduled_time và có early parking_order active
    → Auto-checkout phần đến sớm (chỉ tính từ time_in đến scheduled_time)
    → Auto-activate booking (chuyển sang active, tạo parking_order mới gắn booking_credit)
    """
    from modules.user_service import wallet_deduct
    conn = get_db()
    cur  = conn.cursor()
    try:
        now = _now()
        # Booking pending đã qua scheduled_time, chưa hết expire_at
        cur.execute("""
            SELECT b.*, v.vehicle_type
            FROM bookings b
            JOIN vehicles v ON b.vehicle_id=v.id
            WHERE b.status='pending'
              AND b.scheduled_time <= %s
        """, (_fmt(now),))
        due = cur.fetchall() or []

        activated = 0
        for bk in due:
            sched     = _to_dt(bk["scheduled_time"])
            expire_at = sched + timedelta(hours=float(bk["duration_hours"]))
            if now > expire_at:
                continue  # no_show handled by auto_mark_no_show

            vtype = bk["vehicle_type"]

            # Tìm early parking_order có liên kết
            cur.execute(
                "SELECT * FROM parking_orders "
                "WHERE vehicle_id=%s AND slot_id=%s AND status='active' "
                "AND notes LIKE %s LIMIT 1",
                (bk["vehicle_id"], bk["slot_id"],
                 f"%[Den som] Dat lich #{bk['id']}%")
            )
            early = cur.fetchone()

            if early:
                # Tính phí phần đến sớm: từ time_in đến scheduled_time
                early_hours = max(0.0, (sched - _to_dt(early["time_in"])).total_seconds() / 3600)
                early_fee   = _calc_fee(vtype, early_hours)

                # Auto-checkout early parking_order tại scheduled_time
                cur.execute(
                    "UPDATE parking_orders SET time_out=%s, status='completed', total_fee=%s WHERE id=%s",
                    (_fmt(sched), early_fee, early["id"])
                )
                cur.execute(
                    "INSERT INTO payments (order_type,order_id,amount,paid_at) VALUES ('parking',%s,%s,%s)",
                    (early["id"], early_fee, _fmt(sched))
                )
                # Trừ tiền phần đến sớm từ ví
                if early_fee > 0:
                    deduct_result = wallet_deduct(
                        bk["user_id"], early_fee,
                        f"Phi gui xe som (truoc dat lich #{bk['id']}): {early_fee:,}d",
                        ref_type='parking', ref_id=early["id"],
                        conn=conn, cur=cur
                    )

            # Tạo parking_order mới gắn booking_credit (từ scheduled_time)
            unit_price = PARKING_RATES[vtype]["per_hour"]
            cur.execute(
                "INSERT INTO parking_orders "
                "(user_id,vehicle_id,slot_id,time_in,status,unit_price,notes,booking_id,booking_credit) "
                "VALUES (%s,%s,%s,%s,'active',%s,%s,%s,%s)",
                (bk["user_id"], bk["vehicle_id"], bk["slot_id"],
                 _fmt(sched), unit_price,
                 f"[Auto-activate] Dat lich #{bk['id']}",
                 bk["id"], bk["total_fee"])
            )
            # Slot đã occupied (từ early check-in hoặc tự đảo sang occupied nếu chưa)
            cur.execute("UPDATE parking_slots SET status='occupied' WHERE id=%s", (bk["slot_id"],))
            cur.execute(
                "UPDATE bookings SET status='active', checkin_at=%s WHERE id=%s",
                (_fmt(sched), bk["id"])
            )
            activated += 1

        if activated > 0:
            conn.commit()
        return _ok({"activated": activated})
    except Exception as e:
        conn.rollback()
        return _err(f"Loi auto-activate: {e}")
    finally:
        conn.close()


def fix_booking_integrity():
    """
    Sửa các vấn đề toàn vẹn dữ liệu Booking. Gọi khi server khởi động.
    1. Booking 'active' nhưng chưa đến giờ hẹn → reset về 'pending'
    2. Booking 'pending' đã qua expire_at      → đánh dấu 'no_show'
    3. Slot 'reserved' không có booking pending → trả về 'available'
    4. Slot 'occupied' không có parking_order active → trả về 'available'
    """
    conn = get_db()
    cur  = conn.cursor()
    try:
        now = _now()

        # 1. Booking 'active' sai (chưa đến giờ hẹn)
        cur.execute("""
            SELECT b.id, b.slot_id FROM bookings b
            WHERE b.status='active' AND b.scheduled_time > %s
        """, (_fmt(now),))
        for b in (cur.fetchall() or []):
            cur.execute("UPDATE bookings SET status='pending', checkin_at=NULL WHERE id=%s", (b["id"],))
            cur.execute("UPDATE parking_orders SET status='cancelled' WHERE booking_id=%s AND status='active'", (b["id"],))
            cur.execute("UPDATE parking_slots SET status='reserved' WHERE id=%s", (b["slot_id"],))

        # 2. Booking 'pending' đã hết hạn → no_show
        cur.execute("""
            SELECT b.id, b.slot_id FROM bookings b
            WHERE b.status='pending'
              AND DATE_ADD(b.scheduled_time, INTERVAL b.duration_hours HOUR) < %s
        """, (_fmt(now),))
        for b in (cur.fetchall() or []):
            cur.execute("UPDATE bookings SET status='no_show' WHERE id=%s", (b["id"],))
            cur.execute("UPDATE parking_slots SET status='available' WHERE id=%s", (b["slot_id"],))

        # 3. Slot 'reserved' mồ côi (không có booking pending)
        cur.execute("""
            SELECT id FROM parking_slots WHERE status='reserved'
            AND id NOT IN (SELECT slot_id FROM bookings WHERE status='pending')
        """)
        for s in (cur.fetchall() or []):
            cur.execute("UPDATE parking_slots SET status='available' WHERE id=%s", (s["id"],))

        # 4. Slot 'occupied' mồ côi (không có parking_order active)
        cur.execute("""
            SELECT id FROM parking_slots WHERE status='occupied'
            AND id NOT IN (SELECT slot_id FROM parking_orders WHERE status='active')
        """)
        for s in (cur.fetchall() or []):
            cur.execute("UPDATE parking_slots SET status='available' WHERE id=%s", (s["id"],))

        conn.commit()
        return _ok({"message": "Integrity check OK"})
    except Exception as e:
        conn.rollback()
        return _err(f"Loi fix integrity: {e}")
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 5. TRUY VẤN
# ──────────────────────────────────────────────────────────────────────────────

def get_user_bookings(user_id, status=None):
    """Danh sách đặt lịch của user, kèm trường tính toán."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        q = """SELECT b.*, v.plate_number, v.vehicle_type, v.brand, v.model,
                      ps.slot_code, ps.zone, ps.floor_area, ps.has_charging,
                      u.balance as user_balance
               FROM bookings b
               JOIN vehicles      v  ON b.vehicle_id=v.id
               JOIN parking_slots ps ON b.slot_id=ps.id
               JOIN users         u  ON b.user_id=u.id
               WHERE b.user_id=%s"""
        p = [user_id]
        if status:
            q += " AND b.status=%s"; p.append(status)
        q += " ORDER BY b.created_at DESC"
        cur.execute(q, p)
        now  = _now()
        rows = [_enrich(row_to_dict(r), now, conn) for r in (cur.fetchall() or [])]
        return _ok(rows)
    finally:
        conn.close()



def get_booking_by_id(booking_id, user_id):
    """Chi tiết 1 booking của user."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT b.*, v.plate_number, v.vehicle_type, v.brand, v.model,
                      ps.slot_code, ps.zone, ps.floor_area, ps.has_charging
               FROM bookings b
               JOIN vehicles      v  ON b.vehicle_id=v.id
               JOIN parking_slots ps ON b.slot_id=ps.id
               WHERE b.id=%s AND b.user_id=%s""",
            (booking_id, user_id)
        )
        row = cur.fetchone()
        return _ok(_enrich(row_to_dict(row)))
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 6. ADMIN
# ──────────────────────────────────────────────────────────────────────────────

def admin_get_all_bookings(status_filter=None, search_plate=None):
    """Admin xem tất cả đặt lịch."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        q = """SELECT b.id, b.scheduled_time, b.duration_hours, b.total_fee,
                      b.penalty_fee, b.refund_fee, b.status, b.notes,
                      b.created_at, b.checkin_at, b.checkout_at,
                      v.plate_number, v.vehicle_type, v.brand, v.model,
                      ps.slot_code, ps.zone, ps.has_charging,
                      u.full_name as owner_name, u.phone as owner_phone,
                      u.email as owner_email, u.id as owner_id
               FROM bookings b
               JOIN vehicles      v  ON b.vehicle_id=v.id
               JOIN parking_slots ps ON b.slot_id=ps.id
               JOIN users         u  ON b.user_id=u.id
               WHERE 1=1"""
        p = []
        if status_filter:
            q += " AND b.status=%s"; p.append(status_filter)
        if search_plate:
            q += " AND v.plate_number LIKE %s"
            p.append(f"%{search_plate.strip().upper()}%")
        q += " ORDER BY b.created_at DESC LIMIT 200"
        cur.execute(q, p)
        now  = _now()
        rows = [_enrich(row_to_dict(r), now) for r in (cur.fetchall() or [])]
        return _ok(rows)
    finally:
        conn.close()


def admin_checkin_booking(booking_id):
    """Admin check-in thay user — không giới hạn thời gian."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT b.*, v.vehicle_type, v.plate_number
               FROM bookings b JOIN vehicles v ON b.vehicle_id=v.id
               WHERE b.id=%s AND b.status='pending'""",
            (booking_id,)
        )
        bk = cur.fetchone()
        if not bk:
            return _err("Dat lich khong ton tai hoac khong o trang thai pending.")

        now        = _now()
        unit_price = PARKING_RATES[bk["vehicle_type"]]["per_hour"]
        cur.execute(
            "INSERT INTO parking_orders "
            "(user_id,vehicle_id,slot_id,time_in,status,unit_price,notes,booking_id,booking_credit) "
            "VALUES (%s,%s,%s,%s,'active',%s,%s,%s,%s)",
            (bk["user_id"], bk["vehicle_id"], bk["slot_id"],
             _fmt(now), unit_price,
             f"[Admin] Dat lich #{booking_id}",
             booking_id, bk["total_fee"])
        )
        cur.execute("UPDATE parking_slots SET status='occupied' WHERE id=%s", (bk["slot_id"],))
        cur.execute(
            "UPDATE bookings SET status='active', checkin_at=%s WHERE id=%s",
            (_fmt(now), booking_id)
        )
        conn.commit()
        return _ok(message=f"Admin da check-in booking #{booking_id} (xe {bk['plate_number']}).")
    except Exception as e:
        conn.rollback()
        return _err(f"Loi: {e}")
    finally:
        conn.close()


def admin_cancel_booking(booking_id, refund_percent=100):
    """Admin huỷ booking — chọn % hoàn tiền tuỳ ý."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM bookings WHERE id=%s AND status='pending'", (booking_id,)
        )
        bk = cur.fetchone()
        if not bk:
            return _err("Khong tim thay dat lich hoac khong the huy.")

        pct     = max(0, min(100, int(refund_percent)))
        refund  = int(bk["total_fee"] * pct / 100)
        penalty = bk["total_fee"] - refund

        _refund_to_wallet(
            cur, bk["user_id"], refund,
            f"Admin huy dat lich #{booking_id} (hoan {pct}%)",
            booking_id
        )
        cur.execute(
            "UPDATE bookings SET status='cancelled', penalty_fee=%s, refund_fee=%s WHERE id=%s",
            (penalty, refund, booking_id)
        )
        cur.execute("UPDATE parking_slots SET status='available' WHERE id=%s", (bk["slot_id"],))
        conn.commit()
        return _ok(message=f"Da huy #{booking_id}. Hoan {refund:,}d ({pct}%) vao vi khach.")
    except Exception as e:
        conn.rollback()
        return _err(f"Loi: {e}")
    finally:
        conn.close()
