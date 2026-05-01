# =============================================================================
# app.py — Điểm khởi động Flask, định nghĩa toàn bộ URL routes
#
# File này đóng vai trò "bộ điều phối" (controller):
#   - Nhận request từ browser
#   - Gọi service tương ứng để xử lý nghiệp vụ
#   - Trả về template HTML hoặc redirect
#
# Các module nghiệp vụ được tách riêng:
#   modules/user_service.py    — Tài khoản & phương tiện
#   modules/parking_service.py — Gửi xe, lấy xe, sạc
#   modules/report_service.py  — Báo cáo tháng (Giám đốc)
# =============================================================================

import os
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify)
from functools import wraps

from database import init_db         # Hàm khởi tạo MySQL schema + seed data
import json
from config import VEHICLE_TYPES, SECRET_KEY, CHARGING_RATE_PER_HOUR, \
                   VIETQR_BANK_ID, VIETQR_ACCOUNT_NO, VIETQR_ACCOUNT_NAME, VIETQR_TEMPLATE, \
                   PARKING_RATES, SMALL_VEHICLES

# ── Import các hàm nghiệp vụ từ 3 module service ──────────────────────────────
from modules.user_service    import (register_user, login, get_user_by_id,
                                     get_vehicles, add_vehicle, update_vehicle,
                                     delete_vehicle, update_profile, change_password,
                                     get_all_users,
                                     get_wallet_balance, wallet_topup, wallet_withdraw,
                                     get_wallet_history)
from modules.parking_service import (get_available_slots, create_parking_order,
                                     get_active_parking_order, checkout_parking,
                                     get_available_stations, create_charging_order,
                                     end_charging, get_user_history,
                                     get_user_active_charging,
                                     admin_get_active_parking, admin_confirm_checkout,
                                     admin_get_active_charging, admin_confirm_end_charging,
                                     get_all_slots, add_slot, update_slot_status,
                                     get_all_stations, add_station, update_station_status,
                                     get_all_parking_orders, get_all_charging_orders)
from modules.report_service  import get_full_monthly_report
from modules.booking_service import (create_booking, get_user_bookings, get_booking_by_id,
                                      cancel_booking, checkin_booking,
                                      auto_mark_no_show, auto_activate_pending_bookings,
                                      fix_booking_integrity,
                                      admin_get_all_bookings, admin_checkin_booking, admin_cancel_booking)

# ─────────────────────────────────────────────────────────────────────────────
# KHỞI TẠO ỨNG DỤNG FLASK
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY   # Dùng để mã hóa session cookie

# ─────────────────────────────────────────────────────────────────────────────
# DECORATOR KIỂM TRA ĐĂNG NHẬP & PHÂN QUYỀN
#
# @login_required     : Chỉ cho phép người đã đăng nhập
# @role_required(...)  : Chỉ cho phép vai trò cụ thể (user/admin)
#
# Cách dùng:
#   @app.route("/user/dashboard")
#   @role_required("user")           ← chỉ user mới vào được
#   def user_dashboard(): ...
# ─────────────────────────────────────────────────────────────────────────────

def login_required(f):
    """Decorator: Yêu cầu đã đăng nhập. Chưa đăng nhập → redirect /login."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Vui lòng đăng nhập.", "warning")
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper

def role_required(*roles):
    """Decorator: Yêu cầu vai trò cụ thể. Sai vai trò → redirect trang chủ."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                flash("Vui lòng đăng nhập.", "warning")
                return redirect(url_for("login_page"))
            if session.get("role") not in roles:
                flash("Bạn không có quyền truy cập trang này.", "danger")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return wrapper
    return decorator

# =============================================================================
# NHÓM ROUTES: PUBLIC (Không cần đăng nhập)
# =============================================================================

@app.route("/")
def index():
    """
    Trang chủ — Tự động chuyển hướng theo vai trò:
      - Chưa đăng nhập → landing page giới thiệu hệ thống
      - user     → /user/dashboard
      - admin    → /admin/dashboard
    """
    if "user_id" in session:
        role = session.get("role", "user")
        if role == "admin":    return redirect(url_for("admin_dashboard"))
        return redirect(url_for("user_dashboard"))
    return render_template("landing.html")

# ── Đăng nhập ─────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login_page():
    """
    GET  → Hiển thị form đăng nhập
    POST → Xác thực credential (email hoặc SĐT) + mật khẩu
           Thành công → lưu user_id, full_name, role vào session → redirect
           Thất bại   → flash thông báo lỗi
    """
    if "user_id" in session:
        return redirect(url_for("index"))   # Đã đăng nhập thì không cần vào lại
    if request.method == "POST":
        result = login(request.form.get("credential",""),
                       request.form.get("password",""))
        if result["success"]:
            u = result["data"]
            # Lưu thông tin đăng nhập vào session (cookie mã hóa)
            session["user_id"]   = u["id"]
            session["full_name"] = u["full_name"]
            session["role"]      = u["role"]
            return redirect(url_for("index"))
        flash(result["message"], "danger")
    return render_template("auth/login.html")

@app.route("/register", methods=["GET", "POST"])
def register_page():
    """
    GET  → Hiển thị form đăng ký
    POST → Tạo tài khoản user mới (role='user' mặc định)
           Thành công → redirect /login
           Thất bại   → flash thông báo lỗi (email/SĐT trùng, mật khẩu yếu...)
    """
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        result = register_user(
            request.form.get("full_name",""),
            request.form.get("phone",""),
            request.form.get("email",""),
            request.form.get("password",""),
            request.form.get("confirm_password",""),
        )
        if result["success"]:
            flash("Đăng ký thành công! Vui lòng đăng nhập.", "success")
            return redirect(url_for("login_page"))
        flash(result["message"], "danger")
    return render_template("auth/register.html")

@app.route("/logout")
def logout():
    """Xóa toàn bộ session → đăng xuất → redirect trang chủ."""
    session.clear()
    return redirect(url_for("index"))

# =============================================================================
# NHÓM ROUTES: USER — Khách hàng sử dụng bãi đỗ
# =============================================================================

@app.route("/user/dashboard")
@role_required("user")
def user_dashboard():
    """
    Dashboard tổng quan của khách hàng.
    Hiển thị: số xe đã đăng ký, xe đang gửi, tổng chi tiêu, giao dịch gần đây.
    """
    uid     = session["user_id"]
    user    = get_user_by_id(uid)["data"]           # Thông tin cá nhân
    vehs    = get_vehicles(uid)["data"]              # Danh sách xe của user
    history = get_user_history(uid)["data"]          # Lịch sử giao dịch (tổng quan)
    active_parking  = get_active_parking_order(uid)["data"]  # Xe đang gửi trong bãi
    return render_template("user/dashboard.html",
                           user=user, vehicles=vehs,
                           history=history, active_parking=active_parking)

# ── Quản lý phương tiện ───────────────────────────────────────────────────────

@app.route("/user/vehicles")
@role_required("user")
def user_vehicles():
    """Danh sách phương tiện đã đăng ký của user hiện tại."""
    uid  = session["user_id"]
    vehs = get_vehicles(uid)["data"]
    return render_template("user/vehicles.html", vehicles=vehs,
                           vehicle_types=VEHICLE_TYPES)

@app.route("/user/vehicles/add", methods=["POST"])
@role_required("user")
def user_add_vehicle():
    """
    Thêm phương tiện mới cho user.
    Validate: biển số không được trùng, xe điện phải nhập dung lượng pin (kWh).
    """
    uid = session["user_id"]
    bat = request.form.get("battery_capacity")
    bat = float(bat) if bat else None   # Dung lượng pin, chỉ bắt buộc với EV
    result = add_vehicle(
        uid,
        request.form.get("plate_number",""),
        request.form.get("vehicle_type",""),
        request.form.get("brand",""),
        request.form.get("model",""),
        request.form.get("color",""),
        bat,
    )
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("user_vehicles"))

@app.route("/user/vehicles/edit/<int:vid>", methods=["POST"])
@role_required("user")
def user_edit_vehicle(vid):
    """Cập nhật thông tin xe (brand, model, màu, pin). Không cho đổi biển số."""
    uid = session["user_id"]
    bat = request.form.get("battery_capacity")
    bat = float(bat) if bat else None
    result = update_vehicle(vid, uid,
                            request.form.get("brand",""),
                            request.form.get("model",""),
                            request.form.get("color",""),
                            bat)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("user_vehicles"))

@app.route("/user/vehicles/delete/<int:vid>", methods=["POST"])
@role_required("user")
def user_delete_vehicle(vid):
    """
    Xóa xe khỏi tài khoản.
    Không cho xóa nếu xe đang có đơn gửi hoặc đơn sạc active.
    """
    uid = session["user_id"]
    result = delete_vehicle(vid, uid)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("user_vehicles"))

# ── Gửi xe (Parking) ──────────────────────────────────────────────────────────

@app.route("/user/park", methods=["GET", "POST"])
@role_required("user")
def user_park():
    """
    GET  → Hiển thị form chọn xe + grid 20 vị trí đỗ trống.
           Khu A (A01-A10): không sạc | Khu B (B01-B10): có sạc (ưu tiên EV)
    POST → Tạo đơn gửi xe:
           - Kiểm tra xe chưa có đơn active
           - Kiểm tra slot trống và kích thước phù hợp
           - Update slot_status → 'occupied'
           - Thành công → redirect dashboard
    """
    uid  = session["user_id"]
    vehs = get_vehicles(uid)["data"]

    # Lấy pre-selected từ URL (nếu có từ trang Đặt lịch nhảy sang)
    sel_slot = request.args.get("slot_id")
    sel_veh  = request.args.get("vehicle_id")

    if request.method == "POST":
        vid     = int(request.form.get("vehicle_id") or 0)
        slot_id = int(request.form.get("slot_id") or 0)
        notes   = request.form.get("notes","")
        want_charging = request.form.get("want_charging") == "1"
        result  = create_parking_order(uid, vid, slot_id, notes, want_charging)
        flash(result["message"], "success" if result["success"] else "danger")
        if result["success"]:
            return redirect(url_for("user_dashboard"))
        # Nếu lỗi → render lại form
        vtype = ""
        for v in vehs:
            if v["id"] == vid: vtype = v["vehicle_type"]
        slots = get_available_slots(vtype, user_id=uid)["data"]
        return render_template("user/park.html", vehicles=vehs, slots=slots,
                               selected_vid=vid, selected_sid=slot_id,
                               vehicle_types=VEHICLE_TYPES,
                               charging_rate=CHARGING_RATE_PER_HOUR)

    # GET: Hiển thị form
    slots = get_available_slots(user_id=uid)["data"]
    return render_template("user/park.html", vehicles=vehs, slots=slots,
                           selected_vid=int(sel_veh) if sel_veh else None,
                           selected_sid=int(sel_slot) if sel_slot else None,
                           vehicle_types=VEHICLE_TYPES,
                           charging_rate=CHARGING_RATE_PER_HOUR)

@app.route("/api/slots")
@role_required("user")
def api_slots():
    """
    API nội bộ — Lọc vị trí đỗ theo loại xe (AJAX từ frontend).
    Khi user chọn xe, JS gọi endpoint này để cập nhật grid slot phù hợp.
    Params: ?vehicle_type=motorcycle|car|e_motorcycle|e_car
    """
    vtype = request.args.get("vehicle_type","")
    return jsonify(get_available_slots(vtype))

# ── Lấy xe (Checkout) ─────────────────────────────────────────────────────────

@app.route("/user/checkout")
@role_required("user")
def user_checkout():
    """Hiển thị danh sách xe đang gửi của user → để user chọn lấy xe."""
    uid    = session["user_id"]
    orders = get_active_parking_order(uid)["data"]
    return render_template("user/checkout.html", orders=orders)

@app.route("/user/checkout/<int:oid>", methods=["POST"])
@role_required("user")
def do_checkout(oid):
    """
    Xác nhận lấy xe:
      1. Tính phí gửi xe = (giờ đỗ × đơn giá) với mức trần ngày
      2. Cập nhật đơn → status='completed', ghi time_out, total_fee
      3. Giải phóng slot → status='available'
      4. Ghi payment record
    """
    uid    = session["user_id"]
    result = checkout_parking(oid, uid)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("user_history"))

# ── Sạc xe điện ───────────────────────────────────────────────────────────────

@app.route("/user/charge", methods=["GET", "POST"])
@role_required("user")
def user_charge():
    """
    GET  → Hiển thị form sạc:
           - Danh sách xe điện của user (motorcycle/car thường không hiển thị)
           - Danh sách trụ sạc đang sẵn sàng (phân loại slow/fast)
           - Active charging orders (nếu đang có đơn sạc dở)
    POST → Tạo đơn sạc mới:
           - Validate: xe phải là EV, xe chưa có đơn sạc active, trụ sẵn sàng
           - Update station_status → 'busy'
           - Thành công → redirect /user/history
    """
    uid  = session["user_id"]
    # Chỉ lấy xe điện (EV) — chỉ xe điện mới sạc được
    vehs = [v for v in get_vehicles(uid)["data"]
            if v["vehicle_type"] in ("e_motorcycle", "e_car")]

    if request.method == "POST":
        vid        = int(request.form.get("vehicle_id",0))
        station_id = int(request.form.get("station_id",0))
        charge_type = request.form.get("charge_type","slow")
        result = create_charging_order(uid, vid, station_id, charge_type)
        flash(result["message"], "success" if result["success"] else "danger")
        if result["success"]:
            return redirect(url_for("user_history"))

    stations = get_available_stations()["data"]          # Trụ sạc đang trống
    active_charging = get_user_active_charging(uid)      # Đơn sạc đang hoạt động

    return render_template("user/charge.html", vehicles=vehs,
                           stations=stations, active_charging=active_charging)

@app.route("/user/charge/end/<int:oid>", methods=["POST"])
@role_required("user")
def user_end_charge(oid):
    """
    Kết thúc sạc — Không cần nhập kWh, tính phí theo thời gian \u1ef1 động.
    """
    uid = session["user_id"]
    result = end_charging(oid, uid)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("user_history"))

# ── Ví tiền (Wallet) ────────────────────────────────────────────────────────────

@app.route("/user/wallet")
@role_required("user")
def user_wallet():
    """Trang ví tiền: xem số dư, lịch sử giao dịch, nạp/rút."""
    from database import get_db, rows_to_dicts
    uid     = session["user_id"]
    balance = get_wallet_balance(uid)
    history = get_wallet_history(uid)["data"]
    # Lấy yêu cầu nạp tiền đang chờ
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM pending_topups WHERE user_id=%s ORDER BY created_at DESC LIMIT 10",
            (uid,)
        )
        pending = rows_to_dicts(cur.fetchall())
    finally:
        conn.close()
    return render_template("user/wallet.html", balance=balance, history=history, pending=pending)

@app.route("/user/wallet/topup", methods=["POST"])
@role_required("user")
def user_wallet_topup():
    """Tạo yêu cầu nạp tiền → sinh QR → chờ admin duyệt."""
    import random, string
    from database import get_db
    uid    = session["user_id"]
    amount = int(request.form.get("amount", 0) or 0)
    if amount < 10000:
        flash("Số tiền nạp tối thiểu 10.000đ.", "danger")
        return redirect(url_for("user_wallet"))
    if amount > 10_000_000:
        flash("Số tiền nạp tối đa 10.000.000đ.", "danger")
        return redirect(url_for("user_wallet"))
    # Tạo mã chuyển khoản duy nhất
    rand = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    ref_code = f"PARKEV{uid}{rand}"
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO pending_topups (user_id,amount,ref_code) VALUES (%s,%s,%s)",
            (uid, amount, ref_code)
        )
        conn.commit()
        topup_id = cur.lastrowid
    except Exception as e:
        conn.rollback()
        flash(f"Lỗi tạo yêu cầu: {e}", "danger")
        return redirect(url_for("user_wallet"))
    finally:
        conn.close()
    # Redirect đến trang hiển thị QR
    return redirect(url_for("user_wallet_qr", tid=topup_id))

@app.route("/user/wallet/qr/<int:tid>")
@role_required("user")
def user_wallet_qr(tid):
    """Hiển thị QR chuyển khoản cho yêu cầu nạp tiền."""
    from database import get_db, row_to_dict
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT * FROM pending_topups WHERE id=%s AND user_id=%s", (tid, uid))
        topup = row_to_dict(cur.fetchone())
    finally:
        conn.close()
    if not topup:
        flash("Không tìm thấy yêu cầu nạp tiền.", "danger")
        return redirect(url_for("user_wallet"))
    # Tạo URL VietQR
    import urllib.parse
    qr_url = (
        f"https://img.vietqr.io/image/{VIETQR_BANK_ID}-{VIETQR_ACCOUNT_NO}-{VIETQR_TEMPLATE}.png"
        f"?amount={topup['amount']}"
        f"&addInfo={urllib.parse.quote(topup['ref_code'])}"
        f"&accountName={urllib.parse.quote(VIETQR_ACCOUNT_NAME)}"
    )
    return render_template("user/wallet_qr.html",
                           topup=topup, qr_url=qr_url,
                           bank_id=VIETQR_BANK_ID,
                           account_no=VIETQR_ACCOUNT_NO,
                           account_name=VIETQR_ACCOUNT_NAME)

@app.route("/user/wallet/withdraw", methods=["POST"])
@role_required("user")
def user_wallet_withdraw():
    """Rút tiền khỏi ví."""
    uid    = session["user_id"]
    amount = int(request.form.get("amount", 0) or 0)
    result = wallet_withdraw(uid, amount)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("user_wallet"))

# ── Lịch sử giao dịch ─────────────────────────────────────────────────────────

@app.route("/user/history")
@role_required("user")
def user_history():
    """
    Xem lịch sử giao dịch với bộ lọc:
      ?month=3&year=2026  → lọc theo tháng/năm
      ?type=parking       → chỉ xem giao dịch gửi xe
      ?type=charging      → chỉ xem giao dịch sạc
      ?status=completed   → chỉ xem giao dịch đã hoàn tất
    """
    uid     = session["user_id"]
    month   = request.args.get("month", type=int)
    year    = request.args.get("year",  type=int)
    tx_type = request.args.get("type")
    status  = request.args.get("status")
    history = get_user_history(uid, month, year, tx_type, status)["data"]
    return render_template("user/history.html", history=history,
                           filters={"month": month, "year": year,
                                    "type": tx_type, "status": status})


# ── Đặt lịch trước ───────────────────────────────────────────────────────────

@app.route("/user/bookings", methods=["GET", "POST"])
@role_required("user")
def user_bookings():
    """
    GET  → Hiển thị form đặt lịch + danh sách đặt lịch của user
    POST → Tạo đặt lịch mới
    """
    # Tự động xử lý bookings: mark no-show và activate pending đã đến giờ
    auto_mark_no_show()
    auto_activate_pending_bookings()
    uid  = session["user_id"]
    vehs = get_vehicles(uid)["data"]
    slots = get_available_slots()["data"]

    if request.method == "POST":
        vid            = int(request.form.get("vehicle_id", 0))
        slot_id        = int(request.form.get("slot_id", 0))
        scheduled_time = request.form.get("scheduled_time", "")
        duration_hours = float(request.form.get("duration_hours", 1) or 1)
        notes          = request.form.get("notes", "")

        result = create_booking(uid, vid, slot_id, scheduled_time, duration_hours, notes)
        if result["success"]:
            # Redirect sang trang xác nhận đặt lịch
            return redirect(url_for("user_booking_confirm", bid=result["data"]["booking_id"]))
        flash(result["message"], "danger")

    bookings = get_user_bookings(uid)["data"]
    return render_template("user/bookings.html",
                           vehicles=vehs, slots=slots,
                           bookings=bookings,
                           vehicle_types=VEHICLE_TYPES,
                           rates_json=PARKING_RATES,
                           small_types_json=list(SMALL_VEHICLES))


@app.route("/user/bookings/confirm/<int:bid>")
@role_required("user")
def user_booking_confirm(bid):
    """Trang xác nhận sau khi đặt lịch thành công."""
    uid     = session["user_id"]
    auto_mark_no_show()
    booking = get_booking_by_id(bid, uid)["data"]
    if not booking:
        flash("Đặt lịch không tồn tại.", "danger")
        return redirect(url_for("user_bookings"))
    return render_template("user/booking_confirm.html", booking=booking)


@app.route("/user/bookings/cancel/<int:bid>", methods=["POST"])
@role_required("user")
def user_cancel_booking(bid):
    """Huỷ đặt lịch — hoàn tiền 100%."""
    uid    = session["user_id"]
    result = cancel_booking(bid, uid)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("user_bookings"))


@app.route("/user/bookings/checkin/<int:bid>", methods=["POST"])
@role_required("user")
def user_checkin_booking(bid):
    """Check-in cho đặt lịch."""
    uid    = session["user_id"]
    result = checkin_booking(bid, uid)
    flash(result["message"], "success" if result["success"] else "danger")
    if result["success"]:
        return redirect(url_for("user_dashboard"))
    return redirect(url_for("user_bookings"))


@app.route("/api/slots-available")
@role_required("user")
def api_slots_available():
    """API lấy slot đồng thời cho đặt lịch (lọc theo loại xe)."""
    vtype = request.args.get("vehicle_type", "")
    return jsonify(get_available_slots(vtype))

# =============================================================================
# NHÓM ROUTES: ADMIN — Vận hành bãi đỗ hàng ngày
# =============================================================================

@app.route("/admin/dashboard")
@role_required("admin")
def admin_dashboard():
    """
    Tổng quan vận hành bãi đỗ:
      - Số lượng vị trí đỗ: tổng / trống / đang dùng
      - Số trụ sạc: tổng / sẵn sàng / đang sạc
      - Danh sách xe đang trong bãi (mini table)
      - Danh sách đơn sạc đang hoạt động
    """
    active_parking  = admin_get_active_parking()["data"]
    active_charging = admin_get_active_charging()["data"]
    slots           = get_all_slots()["data"]
    stations        = get_all_stations()["data"]
    # Đếm theo trạng thái để hiển thị stat cards
    available_slots = [s for s in slots    if s["status"] == "available"]
    occupied_slots  = [s for s in slots    if s["status"] == "occupied"]
    avail_stations  = [s for s in stations if s["status"] == "available"]
    busy_stations   = [s for s in stations if s["status"] == "busy"]
    return render_template("admin/dashboard.html",
                           active_parking=active_parking,
                           active_charging=active_charging,
                           total_slots=len(slots),
                           available_slots=len(available_slots),
                           occupied_slots=len(occupied_slots),
                           total_stations=len(stations),
                           avail_stations=len(avail_stations),
                           busy_stations=len(busy_stations))

# ── Quản lý vị trí đỗ ────────────────────────────────────────────────────────

@app.route("/admin/slots")
@role_required("admin")
def admin_slots():
    """Danh sách toàn bộ 20 vị trí đỗ (Khu A + Khu B) với trạng thái hiện tại."""
    slots = get_all_slots()["data"]
    return render_template("admin/slots.html", slots=slots)

@app.route("/admin/slots/add", methods=["POST"])
@role_required("admin")
def admin_add_slot():
    """Thêm vị trí đỗ mới (khi mở rộng bãi). Chọn khu, loại xe, có sạc hay không."""
    result = add_slot(
        request.form.get("slot_code",""),
        request.form.get("slot_type","both"),
        request.form.get("floor_area","Tang 1"),
        request.form.get("zone","A"),
        int(request.form.get("has_charging",0)),
        request.form.get("notes",""),
    )
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("admin_slots"))

@app.route("/admin/slots/status/<int:sid>", methods=["POST"])
@role_required("admin")
def admin_slot_status(sid):
    """
    Cập nhật trạng thái vị trí đỗ thủ công.
    Ví dụ: Admin đánh dấu slot đang bảo trì, hoặc giữ chỗ cho VIP.
    Trạng thái: available | occupied | reserved | maintenance
    """
    result = update_slot_status(sid, request.form.get("status","available"))
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("admin_slots"))

# ── Quản lý trụ sạc ───────────────────────────────────────────────────────────

@app.route("/admin/stations")
@role_required("admin")
def admin_stations():
    """Danh sách 6 trụ sạc (CS01-CS06) với trạng thái và thông số kỹ thuật."""
    stations = get_all_stations()["data"]
    return render_template("admin/stations.html", stations=stations)

@app.route("/admin/stations/add", methods=["POST"])
@role_required("admin")
def admin_add_station():
    """Thêm trụ sạc mới. Chọn công suất (kW)."""
    result = add_station(
        request.form.get("station_code",""),
        "standard",
        float(request.form.get("power_kw",7.4) or 7.4),
        request.form.get("area","Khu B"),
    )
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("admin_stations"))

@app.route("/admin/stations/status/<int:sid>", methods=["POST"])
@role_required("admin")
def admin_station_status(sid):
    """
    Cập nhật trạng thái trụ sạc thủ công.
    Ví dụ: Đánh dấu bảo trì khi trụ hỏng, hoặc reset về available khi sửa xong.
    """
    result = update_station_status(sid, request.form.get("status","available"))
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("admin_stations"))

# ── Đơn gửi xe ────────────────────────────────────────────────────────────────

@app.route("/admin/parking-orders")
@role_required("admin")
def admin_parking_orders():
    """
    Xem và quản lý đơn gửi xe.
    Hỗ trợ lọc:
      ?status=active    → Xe đang trong bãi
      ?status=completed → Đã hoàn tất
      ?search=51A       → Tìm theo biển số (partial match)
    Admin có thể cho xe ra bãi từ bảng này.
    """
    search   = request.args.get("search","")
    status_f = request.args.get("status","active")
    if search:
        # Tìm theo biển số (vẫn chỉ trong đơn active)
        orders = admin_get_active_parking(search)["data"]
    else:
        orders = get_all_parking_orders(status_f if status_f else None)["data"]
    return render_template("admin/parking_orders.html", orders=orders,
                           search=search, status_f=status_f)

@app.route("/admin/confirm-checkout/<int:oid>", methods=["POST"])
@role_required("admin")
def admin_checkout(oid):
    """
    Admin xác nhận xe ra bãi (thay mặt user):
      - Tính phí tự động theo thời gian
      - Cập nhật đơn và giải phóng slot
    Dùng khi cần xử lý tình huống đặc biệt (user không tự checkout được).
    """
    result = admin_confirm_checkout(oid)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("admin_parking_orders"))

# ── Đơn sạc ───────────────────────────────────────────────────────────────────

@app.route("/admin/charging-orders")
@role_required("admin")
def admin_charging_orders():
    """
    Xem và quản lý đơn sạc.
    Lọc: ?status=active (đang sạc) | ?status=completed (đã xong)
    """
    status_f = request.args.get("status","active")
    orders = get_all_charging_orders(status_f if status_f else None)["data"]
    return render_template("admin/charging_orders.html", orders=orders,
                           status_f=status_f)

@app.route("/admin/confirm-end-charge/<int:oid>", methods=["POST"])
@role_required("admin")
def admin_end_charge(oid):
    """
    Admin kết thúc phiên sạc thay user:
      - Nhập số kWh đã sạc → tính phí
      - Giải phóng trụ sạc
    Dùng khi xe đã sạc xong nhưng user chưa tự đóng đơn.
    """
    kwh = 0
    result = admin_confirm_end_charging(oid, kwh)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("admin_charging_orders"))

# ── Quản lý người dùng ──────────────────────────────────────────────────────────

@app.route("/admin/users")
@role_required("admin")
def admin_users():
    """
    Danh sách tất cả khách hàng: thông tin + số dư ví + tổng giao dịch.
    """
    from database import get_db, rows_to_dicts
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT u.id, u.full_name, u.phone, u.email, u.role, u.balance, u.created_at,
                   COALESCE(p_total.total, 0) as total_spent
            FROM users u
            LEFT JOIN (
                SELECT COALESCE(po.user_id, co.user_id) as user_id, SUM(p.amount) as total
                FROM payments p
                LEFT JOIN parking_orders po  ON p.order_type='parking'  AND p.order_id=po.id
                LEFT JOIN charging_orders co ON p.order_type='charging' AND p.order_id=co.id
                GROUP BY user_id
            ) p_total ON u.id = p_total.user_id
            WHERE u.role='user'
            ORDER BY total_spent DESC
        """)
        users = rows_to_dicts(cur.fetchall())
    finally:
        conn.close()
    return render_template("admin/users.html", users=users)

# ── Quản lý thu/chi (Finance) ────────────────────────────────────────────────

@app.route("/admin/finance")
@role_required("admin")
def admin_finance():
    """
    Trang quản lý thu/chi:
    - Thu: Tiền khách hàng sạc/đỗ xe (tự động từ payments)
    - Chi: Sửa chữa trụ sạc, điện, lương... (nhập thủ công)
    - Tổng doanh thu theo ngày/tháng/năm
    """
    from database import get_db, rows_to_dicts
    import datetime as dt

    now   = dt.datetime.now()
    year  = request.args.get("year",  now.year,  type=int)
    month = request.args.get("month", now.month, type=int)
    mp    = f"{year:04d}-{month:02d}"

    conn = get_db()
    cur  = conn.cursor()
    try:
        # Thu từ parking + charging (tự động từ payments)
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE DATE_FORMAT(paid_at,'%%Y-%%m')=%s AND order_type='parking'", (mp,)
        )
        income_parking = int(cur.fetchone()["total"] or 0)
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE DATE_FORMAT(paid_at,'%%Y-%%m')=%s AND order_type='charging'", (mp,)
        )
        income_charging = int(cur.fetchone()["total"] or 0)

        # Thu nhập thủ công (income entries)
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM finance_entries WHERE entry_type='income' AND DATE_FORMAT(entry_date,'%%Y-%%m')=%s", (mp,)
        )
        income_manual = int(cur.fetchone()["total"] or 0)

        # Chi
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM finance_entries WHERE entry_type='expense' AND DATE_FORMAT(entry_date,'%%Y-%%m')=%s", (mp,)
        )
        total_expense = int(cur.fetchone()["total"] or 0)

        total_income = income_parking + income_charging + income_manual
        net_revenue  = total_income - total_expense

        # Doanh thu theo ngày
        cur.execute("""
            SELECT DATE_FORMAT(paid_at,'%%d') as day,
                   SUM(CASE WHEN order_type='parking'  THEN amount ELSE 0 END) as parking,
                   SUM(CASE WHEN order_type='charging' THEN amount ELSE 0 END) as charging,
                   SUM(amount) as total
            FROM payments
            WHERE DATE_FORMAT(paid_at,'%%Y-%%m')=%s
            GROUP BY day ORDER BY day
        """, (mp,))
        daily_revenue = [dict(r) for r in cur.fetchall()]

        # Danh sách khoản chi
        cur.execute(
            "SELECT * FROM finance_entries WHERE DATE_FORMAT(entry_date,'%%Y-%%m')=%s ORDER BY entry_date DESC, id DESC",
            (mp,)
        )
        entries = rows_to_dicts(cur.fetchall())

    finally:
        conn.close()

    return render_template("admin/finance.html",
        year=year, month=month,
        income_parking=income_parking,
        income_charging=income_charging,
        income_manual=income_manual,
        total_income=total_income,
        total_expense=total_expense,
        net_revenue=net_revenue,
        daily_revenue=daily_revenue,
        entries=entries,
    )

@app.route("/admin/finance/add", methods=["POST"])
@role_required("admin")
def admin_finance_add():
    """Thêm bút toán thu/chi."""
    from database import get_db
    entry_type  = request.form.get("entry_type", "expense")
    category    = request.form.get("category", "other")
    amount      = int(request.form.get("amount", 0) or 0)
    description = request.form.get("description", "")
    entry_date  = request.form.get("entry_date", "")
    if amount <= 0:
        flash("So tien phai lon hon 0.", "danger")
        return redirect(url_for("admin_finance"))
    if not entry_date:
        import datetime as dt
        entry_date = dt.date.today().isoformat()
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO finance_entries (entry_type,category,amount,description,entry_date,created_by) VALUES (%s,%s,%s,%s,%s,%s)",
            (entry_type, category, amount, description, entry_date, session["user_id"])
        )
        conn.commit()
        label = "Thu" if entry_type == 'income' else "Chi"
        flash(f"Da them but toan {label}: {amount:,} VND", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Loi: {e}", "danger")
    finally:
        conn.close()
    return redirect(url_for("admin_finance"))

# ── Duyệt nạp tiền QR ─────────────────────────────────────────────────────────

@app.route("/admin/topups")
@role_required("admin")
def admin_topups():
    """Danh sách yêu cầu nạp tiền qua QR — admin duyệt hoặc từ chối."""
    from database import get_db, rows_to_dicts
    status_f = request.args.get("status", "pending")
    conn = get_db()
    cur  = conn.cursor()
    try:
        if status_f:
            cur.execute("""
                SELECT pt.*, u.full_name, u.phone, u.email
                FROM pending_topups pt
                JOIN users u ON pt.user_id=u.id
                WHERE pt.status=%s
                ORDER BY pt.created_at DESC
            """, (status_f,))
        else:
            cur.execute("""
                SELECT pt.*, u.full_name, u.phone, u.email
                FROM pending_topups pt
                JOIN users u ON pt.user_id=u.id
                ORDER BY pt.created_at DESC LIMIT 50
            """)
        topups = rows_to_dicts(cur.fetchall())
    finally:
        conn.close()
    return render_template("admin/topups.html", topups=topups, status_f=status_f)

@app.route("/admin/topups/confirm/<int:tid>", methods=["POST"])
@role_required("admin")
def admin_confirm_topup(tid):
    """Admin xác nhận nạp tiền → cộng ví cho user."""
    from database import get_db, row_to_dict
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT * FROM pending_topups WHERE id=%s AND status='pending'", (tid,))
        topup = row_to_dict(cur.fetchone())
        if not topup:
            flash("Yêu cầu không tồn tại hoặc đã được xử lý.", "danger")
            return redirect(url_for("admin_topups"))
        # Cộng tiền ví
        result = wallet_topup(topup["user_id"], topup["amount"])
        if not result["success"]:
            flash(f"Lỗi cộng ví: {result['message']}", "danger")
            return redirect(url_for("admin_topups"))
        # Cập nhật trạng thái
        cur.execute(
            "UPDATE pending_topups SET status='confirmed', confirmed_by=%s, confirmed_at=NOW() WHERE id=%s",
            (session["user_id"], tid)
        )
        conn.commit()
        flash(f"Đã xác nhận nạp {topup['amount']:,}đ cho user #{topup['user_id']}.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Lỗi: {e}", "danger")
    finally:
        conn.close()
    return redirect(url_for("admin_topups"))

@app.route("/admin/topups/reject/<int:tid>", methods=["POST"])
@role_required("admin")
def admin_reject_topup(tid):
    """Admin từ chối yêu cầu nạp tiền."""
    from database import get_db
    note = request.form.get("note", "")
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "UPDATE pending_topups SET status='rejected', confirmed_by=%s, confirmed_at=NOW(), note=%s WHERE id=%s AND status='pending'",
            (session["user_id"], note or "Từ chối bởi admin", tid)
        )
        conn.commit()
        flash("Đã từ chối yêu cầu nạp tiền.", "warning")
    finally:
        conn.close()
    return redirect(url_for("admin_topups"))

# ── Báo cáo tổng hợp (chuyển từ Director sang Admin) ──────────────────────

@app.route("/admin/report")
@role_required("admin")
def admin_report():
    """
    Báo cáo tổng hợp theo tháng:
      - Doanh thu, hoạt động, hiệu suất, khách hàng
    """
    now   = __import__("datetime").datetime.now()
    year  = request.args.get("year",  now.year,  type=int)
    month = request.args.get("month", now.month, type=int)
    report = get_full_monthly_report(year, month)["data"]
    return render_template("admin/report.html",
                           report=report, year=year, month=month)

# =============================================================================
# NHÓM ROUTES: ADMIN — Quản lý đặt lịch
# =============================================================================

@app.route("/admin/bookings")
@role_required("admin")
def admin_bookings():
    """
    Danh sách tất cả đặt lịch.
    Lọc: ?status=pending|active|completed|no_show|cancelled
    Tìm kiếm: ?search=51A
    Admin có thể check-in hoặc huỷ booking.
    """
    auto_mark_no_show()
    status_f     = request.args.get("status", "")
    search_plate = request.args.get("search", "")
    bookings = admin_get_all_bookings(
        status_filter=status_f if status_f else None,
        search_plate=search_plate if search_plate else None
    )["data"]
    return render_template("admin/bookings.html",
                           bookings=bookings, status_f=status_f, search=search_plate,
                           vehicle_types=VEHICLE_TYPES)


@app.route("/admin/bookings/checkin/<int:bid>", methods=["POST"])
@role_required("admin")
def admin_do_checkin_booking(bid):
    """Admin check-in thay user — không bị giới hạn 10 phút."""
    result = admin_checkin_booking(bid)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/bookings/cancel/<int:bid>", methods=["POST"])
@role_required("admin")
def admin_do_cancel_booking(bid):
    """Admin huỷ booking — chọn % hoàn tiền."""
    refund_pct = int(request.form.get("refund_percent", 100))
    result = admin_cancel_booking(bid, refund_percent=refund_pct)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("admin_bookings"))

# =============================================================================
# KHỞI ĐỘNG SERVER
# =============================================================================

if __name__ == "__main__":
    import sys
    # Xử lý encoding UTF-8 cho Windows terminal (tránh lỗi hiển thị emoji)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    # Khởi tạo MySQL database (tạo bảng + seed data nếu chưa có)
    init_db()

    # Sửa dữ liệu booking bị lỗi trạng thái (chạy 1 lần khi khởi động)
    fix_booking_integrity()

    print("\n" + "="*60)
    print("  [SERVER] He thong Bai Do Xe & Sac Dien")
    print("="*60)
    print("  URL: http://localhost:5000")
    print()
    print("  Tai khoan demo:")
    print("  User:     user@demo.com     / 123456")
    print("  Admin:    admin@demo.com    / 123456")
    print("="*60 + "\n")

    # Chạy Flask development server với auto-reload khi thay đổi code
    app.run(debug=True, port=5000)
