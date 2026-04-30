# =============================================================================
# modules/report_service.py — Dịch vụ báo cáo tháng cho Tổng giám đốc
#
# Module này tổng hợp dữ liệu từ nhiều bảng để tạo báo cáo đa chiều:
#
#   get_monthly_revenue()   → Doanh thu trong tháng (gửi xe + sạc), theo ngày
#   get_monthly_activity()  → Lượt gửi/lấy xe, phiên sạc, kWh tiêu thụ
#   get_occupancy_stats()   → Hiệu suất bãi đỗ: tỷ lệ lấp đầy, giờ cao điểm, top slot/trụ
#   get_customer_stats()    → Khách mới, quay lại, top 5 chi tiêu nhiều nhất
#   get_full_monthly_report() → Gộp cả 4 món trên vào 1 dict — gọi trong route /director/report
#
# Tất cả hàm nhận (year, month) và trả về dict ok(data)/err(msg).
# Query dùng DATE_FORMAT(field,'%%Y-%%m') để lọc theo tháng trong MySQL.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import calendar
from database import get_db, row_to_dict, rows_to_dicts

def ok(data=None, message="Thanh cong"):
    return {"success": True, "message": message, "data": data}

def _month_str(year, month):
    """Chuyển năm + tháng thành cưỡi '2026-03' — dùng cho DATE_FORMAT filter trong SQL."""
    return f"{year:04d}-{month:02d}"

def _prev_month(year, month):
    """Trả về (year, month) của tháng trước — dùng để so sánh tăng trưởng."""
    return (year - 1, 12) if month == 1 else (year, month - 1)

def _pct(current, previous):
    """Tính phần trăm thay đổi giữa 2 giá trị. Trả về None nếu previous = 0 (để tránh chia 0)."""
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)

# =============================================================================
# 1. DOANH THU THEO THÁNG
# =============================================================================
#
# Tổng hợp từ bảng payments:
#   - Doanh thu gửi xe: SUM(amount) WHERE order_type='parking'
#   - Doanh thu sạc xe: SUM(amount) WHERE order_type='charging'
#   - Doanh thu mỗi ngày: GROUP BY ngày → dùng cho biểu đồ cột (Chart.js)
#   - So sánh với tháng trước → tính % thay đổi
# =============================================================================

def get_monthly_revenue(year, month):
    conn = get_db()
    cur  = conn.cursor()
    try:
        mp = _month_str(year, month)

        cur.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM payments "
            "WHERE order_type='parking' AND DATE_FORMAT(paid_at,'%%Y-%%m')=%s", (mp,)
        )
        parking_rev = int(cur.fetchone()["total"] or 0)

        cur.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM payments "
            "WHERE order_type='charging' AND DATE_FORMAT(paid_at,'%%Y-%%m')=%s", (mp,)
        )
        charging_rev = int(cur.fetchone()["total"] or 0)
        total_rev    = parking_rev + charging_rev

        # Theo ngày
        cur.execute(
            """SELECT DATE_FORMAT(paid_at,'%%d') as day,
                      SUM(CASE WHEN order_type='parking'  THEN amount ELSE 0 END) as parking,
                      SUM(CASE WHEN order_type='charging' THEN amount ELSE 0 END) as charging,
                      SUM(amount) as total
               FROM payments
               WHERE DATE_FORMAT(paid_at,'%%Y-%%m')=%s
               GROUP BY day ORDER BY day""",
            (mp,)
        )
        daily_revenue = [dict(r) for r in cur.fetchall()]

        # Tháng trước
        py, pm = _prev_month(year, month)
        prev_mp = _month_str(py, pm)
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE DATE_FORMAT(paid_at,'%%Y-%%m')=%s",
            (prev_mp,)
        )
        prev_rev = int(cur.fetchone()["total"] or 0)

        return ok({
            "year": year, "month": month,
            "parking_revenue":    parking_rev,
            "charging_revenue":   charging_rev,
            "total_revenue":      total_rev,
            "daily_revenue":      daily_revenue,
            "prev_month_revenue": prev_rev,
            "revenue_change_pct": _pct(total_rev, prev_rev),
        })
    finally:
        conn.close()

# =============================================================================
# 2. HOẠT ĐỘNG THEO THÁNG
# =============================================================================
#
# Đếm lượt giao dịch:
#   - parking_checkins   : Số lượt xe vào bãi (cả active + completed)
#   - parking_checkouts  : Số lượt xe đã hoàn tất (lấy xe trong tháng)
#   - charging_sessions  : Số phiên sạc hoàn thành
#   - total_kwh          : Tổng kWh đã sạc trong tháng
#   - active_customers   : Số khách hàng có phát sinh giao dịch (UNION để khử trùng)
# =============================================================================

def get_monthly_activity(year, month):
    conn = get_db()
    cur  = conn.cursor()
    try:
        mp = _month_str(year, month)

        cur.execute(
            "SELECT COUNT(*) as cnt FROM parking_orders WHERE DATE_FORMAT(time_in,'%%Y-%%m')=%s", (mp,)
        )
        checkins = int(cur.fetchone()["cnt"])

        cur.execute(
            "SELECT COUNT(*) as cnt FROM parking_orders "
            "WHERE status='completed' AND DATE_FORMAT(time_out,'%%Y-%%m')=%s", (mp,)
        )
        checkouts = int(cur.fetchone()["cnt"])

        cur.execute(
            "SELECT COUNT(*) as cnt FROM charging_orders "
            "WHERE status='completed' AND DATE_FORMAT(time_end,'%%Y-%%m')=%s", (mp,)
        )
        charging_sessions = int(cur.fetchone()["cnt"])

        cur.execute(
            "SELECT COALESCE(SUM(kwh_consumed),0) as total_kwh FROM charging_orders "
            "WHERE status='completed' AND DATE_FORMAT(time_end,'%%Y-%%m')=%s", (mp,)
        )
        total_kwh = round(float(cur.fetchone()["total_kwh"] or 0), 2)

        cur.execute(
            """SELECT COUNT(DISTINCT user_id) as cnt FROM (
               SELECT user_id FROM parking_orders WHERE DATE_FORMAT(time_in,'%%Y-%%m')=%s
               UNION
               SELECT user_id FROM charging_orders WHERE DATE_FORMAT(time_start,'%%Y-%%m')=%s
            ) AS combined""",
            (mp, mp)
        )
        active_customers = int(cur.fetchone()["cnt"])

        cur.execute(
            """SELECT COUNT(DISTINCT vehicle_id) as cnt FROM (
               SELECT vehicle_id FROM parking_orders WHERE DATE_FORMAT(time_in,'%%Y-%%m')=%s
               UNION
               SELECT vehicle_id FROM charging_orders WHERE DATE_FORMAT(time_start,'%%Y-%%m')=%s
            ) AS comb2""",
            (mp, mp)
        )
        active_vehicles = int(cur.fetchone()["cnt"])

        py, pm = _prev_month(year, month)
        prev_mp = _month_str(py, pm)
        cur.execute(
            "SELECT COUNT(*) as cnt FROM parking_orders WHERE DATE_FORMAT(time_in,'%%Y-%%m')=%s",
            (prev_mp,)
        )
        prev_checkins = int(cur.fetchone()["cnt"])

        return ok({
            "year": year, "month": month,
            "parking_checkins":      checkins,
            "parking_checkouts":     checkouts,
            "charging_sessions":     charging_sessions,
            "total_kwh":             total_kwh,
            "active_customers":      active_customers,
            "active_vehicles":       active_vehicles,
            "prev_parking_checkins": prev_checkins,
            "checkins_change_pct":   _pct(checkins, prev_checkins),
        })
    finally:
        conn.close()

# =============================================================================
# 3. HIỆU SUẤT BÃI ĐỖ
# =============================================================================
#
# Tỷ lệ lấp đầy = tổng số đơn – tổng vị trí × số ngày trong tháng
# top_slots    : 5 vị trí được dùng nhiều nhất
# peak_hours   : Giờ cao điểm (nhóm theo giờ trong ngày bằng HOUR())
# top_stations : Trụ sạc sử dụng nhiều nhất
# =============================================================================

def get_occupancy_stats(year, month):
    conn = get_db()
    cur  = conn.cursor()
    try:
        mp = _month_str(year, month)

        cur.execute("SELECT COUNT(*) as cnt FROM parking_slots")
        total_slots = int(cur.fetchone()["cnt"])
        cur.execute("SELECT COUNT(*) as cnt FROM parking_slots WHERE zone='A'")
        total_zone_a = int(cur.fetchone()["cnt"])
        cur.execute("SELECT COUNT(*) as cnt FROM parking_slots WHERE zone='B'")
        total_zone_b = int(cur.fetchone()["cnt"])

        cur.execute(
            """SELECT ps.slot_code, ps.zone, COUNT(po.id) as usage_count
               FROM parking_slots ps
               LEFT JOIN parking_orders po ON ps.id=po.slot_id
                   AND DATE_FORMAT(po.time_in,'%%Y-%%m')=%s
               GROUP BY ps.id ORDER BY usage_count DESC""",
            (mp,)
        )
        slot_usage = [dict(r) for r in cur.fetchall()]
        top_slots  = slot_usage[:5]

        total_orders  = sum(s["usage_count"] for s in slot_usage)
        days_in_month = calendar.monthrange(year, month)[1]
        occupancy_rate = round(
            (total_orders / max(total_slots * days_in_month, 1)) * 100, 1
        )

        zone_a_usage = sum(s["usage_count"] for s in slot_usage if s["zone"] == "A")
        zone_b_usage = sum(s["usage_count"] for s in slot_usage if s["zone"] == "B")

        cur.execute(
            "SELECT COUNT(*) as cnt FROM charging_orders WHERE DATE_FORMAT(time_start,'%%Y-%%m')=%s",
            (mp,)
        )
        total_charging_sessions = int(cur.fetchone()["cnt"])

        cur.execute(
            """SELECT cs.station_code, cs.station_type, COUNT(co.id) as usage_count
               FROM charging_stations cs
               LEFT JOIN charging_orders co ON cs.id=co.station_id
                   AND DATE_FORMAT(co.time_start,'%%Y-%%m')=%s
               GROUP BY cs.id ORDER BY usage_count DESC LIMIT 3""",
            (mp,)
        )
        top_stations = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """SELECT HOUR(time_in) as hour, COUNT(*) as cnt
               FROM parking_orders
               WHERE DATE_FORMAT(time_in,'%%Y-%%m')=%s
               GROUP BY hour ORDER BY cnt DESC LIMIT 3""",
            (mp,)
        )
        peak_hours = [dict(r) for r in cur.fetchall()]

        return ok({
            "year": year, "month": month,
            "total_slots":             total_slots,
            "total_zone_a":            total_zone_a,
            "total_zone_b":            total_zone_b,
            "occupancy_rate":          occupancy_rate,
            "total_orders":            total_orders,
            "zone_a_usage":            zone_a_usage,
            "zone_b_usage":            zone_b_usage,
            "top_slots":               top_slots,
            "total_charging_sessions": total_charging_sessions,
            "top_stations":            top_stations,
            "peak_hours":              peak_hours,
        })
    finally:
        conn.close()

# =============================================================================
# 4. THỐNG KÊ KHÁCH HÀNG
# =============================================================================
#
# new_customers     : Khách hàng mới trong tháng (created_at trong tháng)
# returning_customers: Khách có giao dịch trong tháng NàY và đã từng mào trước tháng này
#   → Dùng subquery INTERSECT-equivalent bằng WHERE IN (để tương thích MySQL 5/8)
# top_customers     : JOIN payments + orders + users → tổng tiền/user trong tháng
# charge_ratio      : % khách gửi xe có dùng thêm dịch vụ sạc
# =============================================================================

def get_customer_stats(year, month):
    conn = get_db()
    cur  = conn.cursor()
    try:
        mp = _month_str(year, month)

        cur.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE role='user' AND DATE_FORMAT(created_at,'%%Y-%%m')=%s",
            (mp,)
        )
        new_customers = int(cur.fetchone()["cnt"])

        cur.execute("SELECT COUNT(*) as cnt FROM users WHERE role='user'")
        total_customers = int(cur.fetchone()["cnt"])

        cur.execute(
            """SELECT COUNT(DISTINCT user_id) as cnt FROM (
               SELECT user_id FROM parking_orders WHERE DATE_FORMAT(time_in,'%%Y-%%m')=%s
               UNION
               SELECT user_id FROM charging_orders WHERE DATE_FORMAT(time_start,'%%Y-%%m')=%s
            ) AS comb""",
            (mp, mp)
        )
        active_customers = int(cur.fetchone()["cnt"])

        # Khách quay lại
        cur.execute(
            """SELECT COUNT(DISTINCT a.uid) as cnt FROM (
               SELECT user_id as uid FROM parking_orders WHERE DATE_FORMAT(time_in,'%%Y-%%m')=%s
               UNION
               SELECT user_id FROM charging_orders WHERE DATE_FORMAT(time_start,'%%Y-%%m')=%s
            ) AS a
            WHERE a.uid IN (
               SELECT DISTINCT user_id FROM parking_orders WHERE DATE_FORMAT(time_in,'%%Y-%%m') < %s
               UNION
               SELECT DISTINCT user_id FROM charging_orders WHERE DATE_FORMAT(time_start,'%%Y-%%m') < %s
            )""",
            (mp, mp, mp, mp)
        )
        returning_customers = int(cur.fetchone()["cnt"])

        # Top 5 khách chi tiêu nhiều nhất
        cur.execute(
            """SELECT u.full_name, u.phone,
                      COALESCE(SUM(p.amount),0) as total_spent
               FROM payments p
               LEFT JOIN parking_orders po  ON p.order_type='parking'  AND p.order_id=po.id
               LEFT JOIN charging_orders co ON p.order_type='charging' AND p.order_id=co.id
               JOIN users u ON COALESCE(po.user_id, co.user_id)=u.id
               WHERE DATE_FORMAT(p.paid_at,'%%Y-%%m')=%s
               GROUP BY u.id
               ORDER BY total_spent DESC
               LIMIT 5""",
            (mp,)
        )
        top_customers = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT COUNT(DISTINCT user_id) as cnt FROM charging_orders WHERE DATE_FORMAT(time_start,'%%Y-%%m')=%s",
            (mp,)
        )
        charging_users = int(cur.fetchone()["cnt"])
        cur.execute(
            "SELECT COUNT(DISTINCT user_id) as cnt FROM parking_orders WHERE DATE_FORMAT(time_in,'%%Y-%%m')=%s",
            (mp,)
        )
        parking_users = int(cur.fetchone()["cnt"])
        charge_ratio  = round(charging_users / max(parking_users, 1) * 100, 1)

        return ok({
            "year": year, "month": month,
            "new_customers":        new_customers,
            "total_customers":      total_customers,
            "active_customers":     active_customers,
            "returning_customers":  returning_customers,
            "top_customers":        top_customers,
            "parking_users":        parking_users,
            "charging_users":       charging_users,
            "charge_ratio":         charge_ratio,
        })
    finally:
        conn.close()

# =============================================================================
# 5. BÁO CÁO TỔNG HỢP — Entry point cho route /director/report
# =============================================================================
#
# Gọi cả 4 hàm trên và gộp kết quả vào 1 dict lớn.
# Template director/report.html truy cập: report.revenue, report.activity,
#   report.occupancy, report.customers
# =============================================================================

def get_full_monthly_report(year, month):
    rev      = get_monthly_revenue(year, month)
    activity = get_monthly_activity(year, month)
    occ      = get_occupancy_stats(year, month)
    cust     = get_customer_stats(year, month)
    return ok({
        "revenue":   rev["data"]      if rev["success"]      else {},
        "activity":  activity["data"] if activity["success"] else {},
        "occupancy": occ["data"]      if occ["success"]      else {},
        "customers": cust["data"]     if cust["success"]     else {},
    }, "Bao cao thang tong hop")
