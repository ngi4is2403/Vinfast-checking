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
from config import VEHICLE_TYPES, SECRET_KEY

# ── Import các hàm nghiệp vụ từ 3 module service ──────────────────────────────
from modules.user_service    import (register_user, login, get_user_by_id,
                                     get_vehicles, add_vehicle, update_vehicle,
                                     delete_vehicle, update_profile, change_password,
                                     get_all_users)
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

# ─────────────────────────────────────────────────────────────────────────────
# KHỞI TẠO ỨNG DỤNG FLASK
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY   # Dùng để mã hóa session cookie

# ─────────────────────────────────────────────────────────────────────────────
# DECORATOR KIỂM TRA ĐĂNG NHẬP & PHÂN QUYỀN
#
# @login_required     : Chỉ cho phép người đã đăng nhập
# @role_required(...)  : Chỉ cho phép vai trò cụ thể (user/admin/director)
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
      - director → /director/report
    """
    if "user_id" in session:
        role = session.get("role", "user")
        if role == "admin":    return redirect(url_for("admin_dashboard"))
        if role == "director": return redirect(url_for("director_report"))
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

    if request.method == "POST":
        vid     = int(request.form.get("vehicle_id",0))
        slot_id = int(request.form.get("slot_id",0))
        notes   = request.form.get("notes","")
        result  = create_parking_order(uid, vid, slot_id, notes)
        flash(result["message"], "success" if result["success"] else "danger")
        if result["success"]:
            return redirect(url_for("user_dashboard"))
        # Nếu lỗi → render lại form với danh sách slot
        vtype = ""
        for v in vehs:
            if v["id"] == vid:
                vtype = v["vehicle_type"]
        slots = get_available_slots(vtype)["data"]
        return render_template("user/park.html", vehicles=vehs, slots=slots,
                               selected_vid=vid, vehicle_types=VEHICLE_TYPES)

    # GET: Lấy tất cả slot trống (chưa lọc theo loại xe)
    slots = get_available_slots()["data"]
    return render_template("user/park.html", vehicles=vehs, slots=slots,
                           vehicle_types=VEHICLE_TYPES)

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
    Kết thúc sạc — User tự nhập số kWh đã sạc:
      1. Tính phí = phí giữ chỗ + (kWh × đơn giá/kWh)
      2. Cập nhật đơn → completed
      3. Giải phóng trụ sạc → available
      4. Ghi payment record
    """
    uid = session["user_id"]
    kwh = float(request.form.get("kwh_consumed", 0) or 0)
    result = end_charging(oid, uid, kwh)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("user_history"))

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
    """Thêm trụ sạc mới. Chọn loại (slow/fast) và công suất (kW)."""
    result = add_station(
        request.form.get("station_code",""),
        request.form.get("station_type","slow"),
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
    kwh = float(request.form.get("kwh","0") or 0)
    result = admin_confirm_end_charging(oid, kwh)
    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("admin_charging_orders"))

# =============================================================================
# NHÓM ROUTES: DIRECTOR — Tổng giám đốc xem báo cáo
# =============================================================================

@app.route("/director/report")
@role_required("director")
def director_report():
    """
    Báo cáo tổng hợp theo tháng:
      - Doanh thu: tổng, gửi xe, sạc, so sánh tháng trước
      - Biểu đồ cột doanh thu từng ngày (Chart.js)
      - Tỷ lệ lấp đầy bãi đỗ (Khu A / Khu B)
      - Giờ cao điểm, trụ sạc dùng nhiều nhất
      - Thống kê khách: mới, quay lại, top 5 chi tiêu
      - Biểu đồ tròn cơ cấu doanh thu
    Params: ?month=3&year=2026
    """
    now   = __import__("datetime").datetime.now()
    year  = request.args.get("year",  now.year,  type=int)
    month = request.args.get("month", now.month, type=int)
    report = get_full_monthly_report(year, month)["data"]
    return render_template("director/report.html",
                           report=report, year=year, month=month)

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

    print("\n" + "="*60)
    print("  [SERVER] He thong Bai Do Xe & Sac Dien")
    print("="*60)
    print("  URL: http://localhost:5000")
    print()
    print("  Tai khoan demo:")
    print("  User:     user@demo.com     / 123456")
    print("  Admin:    admin@demo.com    / 123456")
    print("  Director: director@demo.com / 123456")
    print("="*60 + "\n")

    # Chạy Flask development server với auto-reload khi thay đổi code
    app.run(debug=True, port=5000)
