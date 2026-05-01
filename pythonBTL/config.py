# =============================================================================
# config.py — Cấu hình toàn bộ hệ thống ParkEV
#
# File này chứa tất cả hằng số và cài đặt dùng chung cho cả ứng dụng.
# Khi cần thay đổi giá, kết nối DB, hay thêm loại xe mới → chỉ cần sửa ở đây.
# =============================================================================

import os

# ─────────────────────────────────────────────────────────────────────────────
# FLASK — Khóa bí mật cho session
# Flask dùng SECRET_KEY để mã hóa cookie session (đăng nhập).
# Có thể đặt biến môi trường SECRET_KEY để override khi deploy production.
# ─────────────────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "parking_secret_key_2024")

# ─────────────────────────────────────────────────────────────────────────────
# KẾT NỐI MySQL (XAMPP / phpMyAdmin)
#
# Lưu ý quan trọng trên Windows:
#   - Dùng "127.0.0.1" thay vì "localhost" để tránh lỗi socket PyMySQL
#   - XAMPP mặc định: user=root, password rỗng
#   - Database "parking_ev" sẽ tự động được tạo khi chạy app lần đầu
# ─────────────────────────────────────────────────────────────────────────────
DB_HOST     = "127.0.0.1"
DB_PORT     = 3306
DB_USER     = "root"
DB_PASSWORD = ""            # Nếu MySQL có password, sửa tại đây
DB_NAME     = "parking_ev"  # Tên database — tự động tạo nếu chưa có

# ─────────────────────────────────────────────────────────────────────────────
# BẢNG GIÁ GỬI XE (VNĐ)
#
# Cấu trúc mỗi loại xe:
#   per_hour   : Tiền tính theo mỗi giờ đỗ (làm tròn lên)
#   daily_max  : Mức trần/ngày — sau 24h không tính thêm
#
# Ví dụ: Xe máy đỗ 30 tiếng = 1 ngày × 25.000 + 6h × 5.000 = 55.000đ
# ─────────────────────────────────────────────────────────────────────────────
PARKING_RATES = {
    "motorcycle":   {"per_hour": 5_000,  "daily_max": 25_000},   # Xe máy xăng
    "car":          {"per_hour": 20_000, "daily_max": 120_000},  # Ô tô xăng
    "e_motorcycle": {"per_hour": 8_000,  "daily_max": 40_000},   # Xe máy điện
    "e_car":        {"per_hour": 25_000, "daily_max": 150_000},  # Ô tô điện
}

# ─────────────────────────────────────────────────────────────────────────────
# BẢNG GIÁ SẠC XE ĐIỆN (VNĐ)
#
# Phí sạc = Phí giữ chỗ (1 lần) + Số kWh × Đơn giá
#   reservation_fee : Thu khi bắt đầu sạc, trả trước
#   slow            : Trụ sạc chậm ≤ 11kW (tiết kiệm hơn)
#   fast            : Trụ sạc nhanh 50-100kW (tiện nhưng đắt hơn)
# ─────────────────────────────────────────────────────────────────────────────
CHARGING_RATES = {
    "reservation_fee": 10_000,   # Phí giữ chỗ trụ sạc (đ/lượt) — dùng cho seed data cũ
    "slow":            8_000,    # Đơn giá sạc chậm   (đ/kWh) — legacy
    "fast":            12_000,   # Đơn giá sạc nhanh  (đ/kWh) — legacy
}

# ─────────────────────────────────────────────────────────────────────────────
# BẢNG GIÁ SẠC MỚI — Tính theo thời gian (VNĐ/giờ)
# Khi user chọn vị trí Khu B có sạc → phí sạc = ceil(giờ) × rate
# Không phân biệt sạc nhanh/chậm, tính đơn giản theo giờ
# ─────────────────────────────────────────────────────────────────────────────
CHARGING_RATE_PER_HOUR = 15_000   # 15.000đ mỗi giờ sạc

# ─────────────────────────────────────────────────────────────────────────────
# VÍ TIỀN — Số dư mặc định khi tạo tài khoản mới (để demo)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_WALLET_BALANCE = 500_000   # 500.000đ cho tài khoản demo

# ─────────────────────────────────────────────────────────────────────────────
# VIETQR — Thông tin ngân hàng để tạo mã QR nạp tiền
# ─────────────────────────────────────────────────────────────────────────────
# Danh sách mã ngân hàng: https://api.vietqr.io/v2/banks
# Ví dụ: MB, VCB, TCB, TPB, ACB, BIDV, VPB, STB...
# ★ THAY BẰNG THÔNG TIN NGÂN HÀNG CỦA BẠN ★
VIETQR_BANK_ID    = "MB"                        # Mã ngân hàng (MBBank)
VIETQR_ACCOUNT_NO = "0388888888"                # Số tài khoản
VIETQR_ACCOUNT_NAME = "NGUYEN VAN A"            # Tên chủ tài khoản
VIETQR_TEMPLATE   = "compact2"                  # Template QR (compact, compact2, qr_only)

# ─────────────────────────────────────────────────────────────────────────────
# LOẠI XE — Dùng để hiển thị tên thân thiện trong giao diện
# ─────────────────────────────────────────────────────────────────────────────
VEHICLE_TYPES = {
    "motorcycle":   "Xe máy",
    "car":          "Ô tô",
    "e_motorcycle": "Xe máy điện",
    "e_car":        "Ô tô điện",
}

# Tập hợp các loại xe điện — dùng để kiểm tra đủ điều kiện sạc
ELECTRIC_TYPES = {"e_motorcycle", "e_car"}

# Phân nhóm theo kích thước — dùng để lọc vị trí đỗ phù hợp
# Slot loại "small" chỉ nhận SMALL_VEHICLES, slot "large" chỉ nhận LARGE_VEHICLES
SMALL_VEHICLES = {"motorcycle", "e_motorcycle"}   # Xe nhỏ → vị trí nhỏ
LARGE_VEHICLES = {"car", "e_car"}                 # Xe lớn → vị trí lớn

# ─────────────────────────────────────────────────────────────────────────────
# TRẠNG THÁI HỆ THỐNG
#
# Dùng khi validate input hoặc render badge màu trong template.
# Trạng thái được lưu dưới dạng chuỗi trong MySQL ENUM.
# ─────────────────────────────────────────────────────────────────────────────
SLOT_STATUS    = ["available", "occupied", "reserved", "maintenance"]
                 # Trống       Đang dùng  Đã giữ      Bảo trì
STATION_STATUS = ["available", "busy", "maintenance"]
                 # Sẵn sàng   Đang sạc  Bảo trì
ORDER_STATUS   = ["active", "completed", "cancelled"]
                 # Đang hoạt  Hoàn tất  Đã hủy

# ─────────────────────────────────────────────────────────────────────────────
# PHÂN QUYỀN — 3 vai trò trong hệ thống
#   user     : Khách hàng — gửi xe, sạc, xem lịch sử cá nhân
#   admin    : Admin bãi đỗ — quản lý vị trí, trụ sạc, xác nhận xe ra/vào
#   director : Tổng giám đốc — xem báo cáo doanh thu, không làm nghiệp vụ
# ─────────────────────────────────────────────────────────────────────────────
ROLES = ["user", "admin", "director"]

# ─────────────────────────────────────────────────────────────────────────────
# QUY TẮC MẬT KHẨU
# ─────────────────────────────────────────────────────────────────────────────
MIN_PASSWORD_LENGTH = 6   # Mật khẩu phải có ít nhất 6 ký tự
