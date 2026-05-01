# =============================================================================
# database.py — Kết nối MySQL, định nghĩa schema và tạo dữ liệu mẫu
#
# Luồng hoạt động khi app khởi động (hàm init_db):
#   1. Kết nối MySQL không chọn DB → tạo database 'parking_ev' nếu chưa có
#   2. Kết nối lại với DB → tạo 7 bảng từ danh sách TABLES
#   3. Nếu bảng users rỗng → gọi seed_data() để tạo dữ liệu mẫu
#
# Dữ liệu mẫu bao gồm:
#   - 5 tài khoản (3 user, 1 admin, 1 director)
#   - 6 phương tiện (mix cả xe xăng và xe điện)
#   - 20 vị trí đỗ (A01-A10 không sạc, B01-B10 có sạc)
#   - 6 trụ sạc (CS01-CS03 chậm, CS04-CS06 nhanh)
#   - ~25 đơn gửi xe và sạc trong 30 ngày qua (để báo cáo có số liệu)
# =============================================================================

import pymysql
import pymysql.cursors
import hashlib
from datetime import datetime, timedelta
from config import (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME,
                    PARKING_RATES, CHARGING_RATES, DEFAULT_WALLET_BALANCE,
                    CHARGING_RATE_PER_HOUR)

# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Chuyển đổi kiểu dữ liệu
#
# PyMySQL trả về cột DATETIME dưới dạng datetime object (Python), không phải string.
# Hàm row_to_dict() đảm bảo tất cả datetime được chuyển thành string trước khi
# dùng trong template Jinja2 (tránh lỗi khi dùng [:16] để cắt chuỗi).
# ─────────────────────────────────────────────────────────────────────────────

def row_to_dict(row):
    """
    Chuyển một row từ MySQL thành dict Python.
    Tự động convert kiểu datetime → string 'YYYY-MM-DD HH:MM:SS'.
    Trả về None nếu row là None (kết quả query không tìm thấy).
    """
    if row is None:
        return None
    d = dict(row)
    return {k: (v.strftime('%Y-%m-%d %H:%M:%S') if isinstance(v, datetime) else v)
            for k, v in d.items()}

def rows_to_dicts(rows):
    """Chuyển danh sách rows từ MySQL thành danh sách dict Python."""
    return [row_to_dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# KẾT NỐI DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    """
    Tạo và trả về một kết nối MySQL mới.
    Mỗi request nên dùng một kết nối riêng → đóng sau khi xong (conn.close()).
    DictCursor: fetchone()/fetchall() trả về dict thay vì tuple.
    autocommit=False: Phải gọi conn.commit() hoặc conn.rollback() tường minh.
    """
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset='utf8mb4',              # Hỗ trợ đầy đủ Unicode (kể cả emoji)
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    return conn

# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Mã hóa mật khẩu
# ─────────────────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """
    Mã hóa mật khẩu bằng SHA-256 (one-way hash).
    Chỉ lưu hash vào DB, không bao giờ lưu plain text.
    Khi đăng nhập: hash(input) so sánh với hash đã lưu.
    """
    return hashlib.sha256(password.encode()).hexdigest()

# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA — Định nghĩa cấu trúc 7 bảng MySQL
#
# Thứ tự tạo bảng quan trọng vì có FOREIGN KEY:
#   1. users            (không phụ thuộc bảng nào)
#   2. vehicles         (phụ thuộc users)
#   3. parking_slots    (độc lập)
#   4. charging_stations(độc lập)
#   5. parking_orders   (phụ thuộc users, vehicles, parking_slots)
#   6. charging_orders  (phụ thuộc users, vehicles, charging_stations)
#   7. payments         (lưu lịch sử thu tiền, tham chiếu đến order)
#
# ENGINE=InnoDB: Bắt buộc để dùng FOREIGN KEY và transaction
# DEFAULT CHARSET=utf8mb4: Hỗ trợ ký tự tiếng Việt đầy đủ
# ─────────────────────────────────────────────────────────────────────────────

TABLES = [
    # ── Bảng người dùng ─────────────────────────────────────────────────────
    # Lưu thông tin tài khoản của tất cả 3 vai trò (user/admin/director).
    # Trường role dùng ENUM để giới hạn gía trị hợp lệ trực tiếp trong DB.
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        full_name     VARCHAR(255) NOT NULL,
        phone         VARCHAR(20)  NOT NULL UNIQUE,    -- Số điện thoại (dùng để đăng nhập)
        email         VARCHAR(255) NOT NULL UNIQUE,    -- Email (cũng dùng để đăng nhập)
        password_hash VARCHAR(64)  NOT NULL,            -- SHA-256 hash, không lưu plain text
        role          ENUM('user','admin','director') NOT NULL DEFAULT 'user',
        balance       INT NOT NULL DEFAULT 0,           -- Số dư ví tiền (VNĐ)
        created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng phương tiện ────────────────────────────────────────────────────
    # Mỗi user có thể đăng ký nhiều xe.
    # Một xe thuộc về chính xác một user (ON DELETE CASCADE: xóa user thì xóa xe).
    # battery_capacity chỉ có ý nghĩa với xe điện (ELECTRIC_TYPES).
    """
    CREATE TABLE IF NOT EXISTS vehicles (
        id               INT AUTO_INCREMENT PRIMARY KEY,
        user_id          INT NOT NULL,
        plate_number     VARCHAR(20) NOT NULL UNIQUE,  -- Biển số xe (unique toàn hệ thống)
        vehicle_type     ENUM('motorcycle','car','e_motorcycle','e_car') NOT NULL,
        brand            VARCHAR(100),
        model            VARCHAR(100),
        color            VARCHAR(50),
        battery_capacity FLOAT,                        -- Dung lượng pin kWh (chỉ EV)
        created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng vị trí đỗ xe ───────────────────────────────────────────────────
    # 20 vị trí cố định chia 2 khu:
    #   Khu A (A01-A10): Không có trụ sạc gần (has_charging=0)
    #   Khu B (B01-B10): Gần trụ sạc điện   (has_charging=1), ưu tiên xe EV
    # slot_type: small=xe máy, large=ô tô, both=cả hai
    """
    CREATE TABLE IF NOT EXISTS parking_slots (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        slot_code    VARCHAR(10) NOT NULL UNIQUE,       -- Ví dụ: A01, B05
        slot_type    ENUM('small','large','both') NOT NULL,
        floor_area   VARCHAR(50) NOT NULL,              -- Vị trí vật lý: Tầng 1, Khu B...
        zone         ENUM('A','B') NOT NULL,
        has_charging TINYINT NOT NULL DEFAULT 0,        -- 1 = gần trụ sạc
        status       ENUM('available','occupied','reserved','maintenance') NOT NULL DEFAULT 'available',
        notes        TEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng trụ sạc điện ───────────────────────────────────────────────────
    # 6 trụ sạc đặt tại Khu B:
    #   CS01-CS03: slow (sạc chậm, 7-11kW, rẻ hơn)
    #   CS04-CS06: fast (sạc nhanh, 50-100kW, đắt hơn)
    """
    CREATE TABLE IF NOT EXISTS charging_stations (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        station_code VARCHAR(20) NOT NULL UNIQUE,
        station_type VARCHAR(20) NOT NULL DEFAULT 'standard',  -- Loại trụ (đơn giản, không phân biệt nhanh/chậm)
        power_kw     DECIMAL(6,1) NOT NULL DEFAULT 7.4,         -- Công suất sạc (kW)
        status       ENUM('available','busy','maintenance') NOT NULL DEFAULT 'available',
        area         VARCHAR(50) DEFAULT 'Khu B'
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng đơn gửi xe ─────────────────────────────────────────────────────
    # Mỗi lần xe vào bãi tạo một record với status='active'.
    # Khi lấy xe: time_out và total_fee được cập nhật, status='completed'.
    # unit_price lưu lại đơn giá tại thời điểm gửi (tránh ảnh hưởng khi đổi giá).
    """
    CREATE TABLE IF NOT EXISTS parking_orders (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     INT NOT NULL,
        vehicle_id  INT NOT NULL,
        slot_id     INT NOT NULL,
        time_in     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- Giờ vào bãi
        time_out    DATETIME,                                       -- Giờ ra (NULL khi đang gửi)
        status      ENUM('active','completed','cancelled') NOT NULL DEFAULT 'active',
        unit_price  INT NOT NULL,               -- Đơn giá/giờ lưu snapshot tại thời điểm tạo
        total_fee   INT DEFAULT 0,              -- Phí cuối cùng (tính khi checkout)
        notes       TEXT,
        FOREIGN KEY (user_id)    REFERENCES users(id),
        FOREIGN KEY (vehicle_id) REFERENCES vehicles(id),
        FOREIGN KEY (slot_id)    REFERENCES parking_slots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng đơn sạc điện ───────────────────────────────────────────────────
    # Tương tự parking_orders nhưng cho dịch vụ sạc.
    # kwh_consumed được nhập khi kết thúc sạc → dùng để tính total_fee.
    """
    CREATE TABLE IF NOT EXISTS charging_orders (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        user_id      INT NOT NULL,
        vehicle_id   INT NOT NULL,
        station_id   INT NOT NULL,
        charge_type  VARCHAR(20) NOT NULL DEFAULT 'standard',
        time_start   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        time_end     DATETIME,                          -- NULL khi đang sạc
        kwh_consumed FLOAT DEFAULT 0,                   -- Số kWh thực tế đã sạc
        total_fee    INT DEFAULT 0,                     -- Phí = reservation + kwh × đơn giá
        status       ENUM('active','completed') NOT NULL DEFAULT 'active',
        FOREIGN KEY (user_id)    REFERENCES users(id),
        FOREIGN KEY (vehicle_id) REFERENCES vehicles(id),
        FOREIGN KEY (station_id) REFERENCES charging_stations(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng thanh toán ─────────────────────────────────────────────────────
    # Ghi lại mỗi lần thu tiền (parking hoặc charging).
    # Dùng cho báo cáo doanh thu của Giám đốc (GROUP BY ngày/tháng).
    # order_type + order_id → xác định đơn nào được thanh toán.
    """
    CREATE TABLE IF NOT EXISTS payments (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        order_type ENUM('parking','charging') NOT NULL,  -- Loại dịch vụ
        order_id   INT NOT NULL,                          -- ID của đơn tương ứng
        amount     INT NOT NULL,                          -- Số tiền thu (VNĐ)
        method     VARCHAR(20) NOT NULL DEFAULT 'wallet', -- Phương thức: wallet, cash...
        paid_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP  -- Thời điểm thanh toán
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng giao dịch ví tiền ──────────────────────────────────────────
    # Ghi lại mỗi lần nạp/rút/trừ tiền ví.
    # tx_type: topup (nạp), withdraw (rút), payment (trừ khi thanh toán dịch vụ)
    """
    CREATE TABLE IF NOT EXISTS wallet_transactions (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     INT NOT NULL,
        tx_type     ENUM('topup','withdraw','payment') NOT NULL,
        amount      INT NOT NULL,                          -- Số tiền (luôn dương)
        balance_after INT NOT NULL,                        -- Số dư sau giao dịch
        description VARCHAR(255),                          -- Mô tả giao dịch
        ref_type    VARCHAR(20),                           -- 'parking' hoặc 'charging' (nếu payment)
        ref_id      INT,                                   -- ID đơn hàng (nếu payment)
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng quản lý thu/chi của bãi xe ─────────────────────────────────
    # Admin ghi nhận các khoản thu/chi hàng ngày.
    # entry_type: 'income' (thu) hoặc 'expense' (chi)
    # category: nhu 'parking', 'charging', 'repair', 'electricity', 'salary', 'other'
    """
    CREATE TABLE IF NOT EXISTS finance_entries (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        entry_type  ENUM('income','expense') NOT NULL,
        category    VARCHAR(50) NOT NULL,
        amount      INT NOT NULL,
        description VARCHAR(255),
        entry_date  DATE NOT NULL,
        created_by  INT,                                   -- Admin ID tạo bút toán
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng yêu cầu nạp tiền qua QR ──────────────────────────────────
    # User tạo yêu cầu nạp → hệ thống tạo QR → user chuyển khoản
    # Admin xác nhận → cộng tiền ví
    # status: pending (chờ) → confirmed (đã duyệt) | rejected (từ chối)
    """
    CREATE TABLE IF NOT EXISTS pending_topups (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     INT NOT NULL,
        amount      INT NOT NULL,
        ref_code    VARCHAR(30) NOT NULL UNIQUE,           -- Mã CK duy nhất: PARKEV-{uid}-{random}
        status      ENUM('pending','confirmed','rejected') NOT NULL DEFAULT 'pending',
        confirmed_by INT,                                  -- Admin ID xác nhận
        confirmed_at DATETIME,
        note        VARCHAR(255),                          -- Ghi chú (lý do từ chối...)
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id)     REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (confirmed_by) REFERENCES users(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ── Bảng đặt lịch trước ─────────────────────────────────────────
    # User đặt chỗ trước, trả tiền ngay, đến check-in trong 10 phút
    # Nếu no-show → phạt 30%, hoàn 70%
    """
    CREATE TABLE IF NOT EXISTS bookings (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        user_id         INT NOT NULL,
        vehicle_id      INT NOT NULL,
        slot_id         INT NOT NULL,
        scheduled_time  DATETIME NOT NULL,
        duration_hours  FLOAT NOT NULL DEFAULT 1,
        total_fee       INT NOT NULL DEFAULT 0,
        penalty_fee     INT NOT NULL DEFAULT 0,
        refund_fee      INT NOT NULL DEFAULT 0,
        status          ENUM('pending','active','completed','no_show','cancelled')
                        NOT NULL DEFAULT 'pending',
        notes           TEXT,
        created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        checkin_at      DATETIME,
        checkout_at     DATETIME,
        FOREIGN KEY (user_id)    REFERENCES users(id),
        FOREIGN KEY (vehicle_id) REFERENCES vehicles(id),
        FOREIGN KEY (slot_id)    REFERENCES parking_slots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

# ─────────────────────────────────────────────────────────────────────────────
# SEED DATA — Tạo dữ liệu mẫu ban đầu
#
# Chỉ chạy một lần khi bảng users rỗng (DB mới).
# Tạo đầy đủ dữ liệu để hệ thống có thể demo ngay lập tức,
# đặc biệt báo cáo Giám đốc cần lịch sử 1-2 tháng mới có số liệu.
# ─────────────────────────────────────────────────────────────────────────────

def seed_data(conn):
    cur = conn.cursor()

    # ── Tài khoản mẫu (5 người, 3 vai trò) ────────────────────────────────────
    # Format: (full_name, phone, email, password, role)
    accounts = [
        ("Nguyen Van An",    "0901234567", "user@demo.com",      "123456", "user"),
        ("Tran Thi Binh",    "0907654321", "user2@demo.com",     "123456", "user"),
        ("Le Van Cuong",     "0912345678", "user3@demo.com",     "123456", "user"),
        ("Pham Quang Dung",  "0900000001", "admin@demo.com",     "123456", "admin"),
    ]
    user_ids = {}   # Lưu mapping email → id để dùng cho bước sau
    for full_name, phone, email, pwd, role in accounts:
        cur.execute(
            "INSERT IGNORE INTO users (full_name,phone,email,password_hash,role,balance) VALUES (%s,%s,%s,%s,%s,%s)",
            (full_name, phone, email, hash_password(pwd), role, DEFAULT_WALLET_BALANCE)
        )
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        if row:
            user_ids[email] = row["id"]
            # Tạo giao dịch nạp tiền ban đầu cho ví
            cur.execute(
                "INSERT IGNORE INTO wallet_transactions (user_id,tx_type,amount,balance_after,description) VALUES (%s,'topup',%s,%s,'Nap tien khi tao tai khoan demo')",
                (row['id'], DEFAULT_WALLET_BALANCE, DEFAULT_WALLET_BALANCE)
            )

    # ── Phương tiện mẫu (6 xe, mix xăng + điện) ───────────────────────────────
    # Mỗi user có 2 xe để demo đủ tình huống gửi + sạc
    vehicles = [
        ("user@demo.com",  "51A-12345", "motorcycle",   "Honda",   "Wave Alpha",  "Do",    None),
        ("user@demo.com",  "51A-67890", "e_car",        "VinFast", "VF 8",        "Trang", 87.7),
        ("user2@demo.com", "51B-11111", "car",          "Toyota",  "Camry",       "Den",   None),
        ("user2@demo.com", "51B-22222", "e_motorcycle", "VinFast", "Klara S",     "Xanh",  4.0),
        ("user3@demo.com", "51C-33333", "e_car",        "Tesla",   "Model 3",     "Bac",   75.0),
        ("user3@demo.com", "51C-44444", "motorcycle",   "Yamaha",  "Exciter 155", "Vang",  None),
    ]
    veh_ids = {}   # Lưu mapping plate_number → id
    for email, plate, vtype, brand, model, color, bat in vehicles:
        uid = user_ids.get(email)
        if uid:
            cur.execute(
                "INSERT IGNORE INTO vehicles (user_id,plate_number,vehicle_type,brand,model,color,battery_capacity) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (uid, plate, vtype, brand, model, color, bat)
            )
            cur.execute("SELECT id FROM vehicles WHERE plate_number=%s", (plate,))
            row = cur.fetchone()
            if row:
                veh_ids[plate] = row["id"]

    # ── 20 Vị trí đỗ xe ────────────────────────────────────────────────────────
    # Khu A (A01-A10): KHÔNG có sạc
    #   A01-A05: small  (xe máy)
    #   A06-A10: large  (ô tô)
    # Khu B (B01-B10): CÓ sạc (gần trụ điện)
    #   B01-B04: small  (xe máy điện)
    #   B05-B10: large  (ô tô điện)
    slots = []
    for i in range(1, 11):
        code  = f"A{i:02d}"
        stype = "small" if i <= 5 else "large"
        slots.append((code, stype, "Tang 1", "A", 0))   # has_charging=0
    for i in range(1, 11):
        code  = f"B{i:02d}"
        stype = "small" if i <= 4 else "large"
        slots.append((code, stype, "Tang 1", "B", 1))   # has_charging=1

    slot_ids = {}   # Lưu mapping slot_code → id
    for code, stype, floor, zone, has_charging in slots:
        cur.execute(
            "INSERT IGNORE INTO parking_slots (slot_code,slot_type,floor_area,zone,has_charging,status) VALUES (%s,%s,%s,%s,%s,'available')",
            (code, stype, floor, zone, has_charging)
        )
        cur.execute("SELECT id FROM parking_slots WHERE slot_code=%s", (code,))
        row = cur.fetchone()
        if row:
            slot_ids[code] = row["id"]

    # ── 6 Trụ sạc tại Khu B ───────────────────────────────────────────────────
    # CS01-CS03: sạc chậm (7.4-11kW) — rẻ hơn
    # CS04-CS06: sạc nhanh (50-100kW) — đắt hơn, nhanh hơn
    stations_data = [
        ("CS01", "slow",  7.4,   "Khu B"),
        ("CS02", "slow",  7.4,   "Khu B"),
        ("CS03", "slow",  11.0,  "Khu B"),
        ("CS04", "fast",  50.0,  "Khu B"),
        ("CS05", "fast",  50.0,  "Khu B"),
        ("CS06", "fast",  100.0, "Khu B"),
    ]
    station_ids = {}
    for code, stype, power, area in stations_data:
        cur.execute(
            "INSERT IGNORE INTO charging_stations (station_code,station_type,power_kw,status,area) VALUES (%s,%s,%s,'available',%s)",
            (code, stype, power, area)
        )
        cur.execute("SELECT id FROM charging_stations WHERE station_code=%s", (code,))
        row = cur.fetchone()
        if row:
            station_ids[code] = row["id"]

    conn.commit()   # Commit dữ liệu cơ bản trước khi tạo lịch sử

    # ── Tạo lịch sử giao dịch (để báo cáo có số liệu) ─────────────────────────
    import math

    slot_list = list(slot_ids.values())   # Danh sách slot_id theo thứ tự tạo

    def make_parking_history(email, plate, slot_idx, days_ago, hours):
        """
        Tạo 1 đơn gửi xe hoàn thành trong quá khứ.
        Tính phí theo công thức: full_days × daily_max + ceil(rem_hours) × per_hour.
        """
        uid = user_ids.get(email)
        vid = veh_ids.get(plate)
        if not uid or not vid or slot_idx >= len(slot_list):
            return
        sid = slot_list[slot_idx]
        cur.execute("SELECT vehicle_type FROM vehicles WHERE id=%s", (vid,))
        row = cur.fetchone()
        if not row:
            return
        vtype = row["vehicle_type"]
        rate  = PARKING_RATES[vtype]
        t_in  = datetime.now() - timedelta(days=days_ago)
        t_out = t_in + timedelta(hours=hours)
        full_days = int(hours // 24)
        rem_h     = hours % 24
        fee = full_days * rate["daily_max"] + min(math.ceil(rem_h) * rate["per_hour"], rate["daily_max"])

        cur.execute(
            """INSERT INTO parking_orders
               (user_id,vehicle_id,slot_id,time_in,time_out,status,unit_price,total_fee)
               VALUES (%s,%s,%s,%s,%s,'completed',%s,%s)""",
            (uid, vid, sid,
             t_in.strftime('%Y-%m-%d %H:%M:%S'),
             t_out.strftime('%Y-%m-%d %H:%M:%S'),
             rate["per_hour"], fee)
        )
        oid = cur.lastrowid   # ID của đơn vừa insert
        # Ghi payment record tương ứng
        cur.execute(
            "INSERT INTO payments (order_type,order_id,amount,paid_at) VALUES ('parking',%s,%s,%s)",
            (oid, fee, t_out.strftime('%Y-%m-%d %H:%M:%S'))
        )

    def make_charging_history(email, plate, station_code, days_ago, kwh, charge_type):
        """
        Tạo 1 đơn sạc hoàn thành trong quá khứ.
        Phí tính theo thời gian: ceil(giờ) × CHARGING_RATE_PER_HOUR
        """
        uid = user_ids.get(email)
        vid = veh_ids.get(plate)
        sid = station_ids.get(station_code)
        if not uid or not vid or not sid:
            return
        # Tính thời gian sạc từ kwh (giả lập: ~7kW/h)
        hours = kwh / 7.0
        t_start = datetime.now() - timedelta(days=days_ago, hours=2)
        t_end   = t_start + timedelta(hours=hours)
        fee = int(math.ceil(hours) * CHARGING_RATE_PER_HOUR)
        cur.execute(
            """INSERT INTO charging_orders
               (user_id,vehicle_id,station_id,charge_type,time_start,time_end,kwh_consumed,total_fee,status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'completed')""",
            (uid, vid, sid, charge_type,
             t_start.strftime('%Y-%m-%d %H:%M:%S'),
             t_end.strftime('%Y-%m-%d %H:%M:%S'),
             kwh, fee)
        )
        oid = cur.lastrowid
        cur.execute(
            "INSERT INTO payments (order_type,order_id,amount,paid_at) VALUES ('charging',%s,%s,%s)",
            (oid, fee, t_end.strftime('%Y-%m-%d %H:%M:%S'))
        )

    # Tạo lịch sử gửi xe: (email, biển số, slot_index, ngày_trước, số_giờ)
    history = [
        ("user@demo.com",  "51A-12345", 0,  2,  3),
        ("user@demo.com",  "51A-12345", 1,  5,  5),
        ("user@demo.com",  "51A-12345", 2,  8,  2),
        ("user@demo.com",  "51A-67890", 3,  1,  6),
        ("user@demo.com",  "51A-67890", 4,  10, 4),
        ("user2@demo.com", "51B-11111", 5,  3,  8),
        ("user2@demo.com", "51B-11111", 6,  12, 10),
        ("user2@demo.com", "51B-22222", 7,  4,  3),
        ("user3@demo.com", "51C-33333", 8,  2,  7),
        ("user3@demo.com", "51C-44444", 9,  6,  5),
        ("user2@demo.com", "51B-11111", 10, 15, 12),
        ("user3@demo.com", "51C-33333", 11, 20, 8),
        ("user@demo.com",  "51A-12345", 0,  25, 4),
        ("user2@demo.com", "51B-22222", 1,  28, 6),
        ("user3@demo.com", "51C-44444", 2,  30, 3),
    ]
    for email, plate, slot_idx, days, hours in history:
        make_parking_history(email, plate, slot_idx, days, hours)

    # Tạo lịch sử sạc xe: (email, biển số, trụ_sạc, ngày_trước, kWh, loại_sạc)
    charging_history = [
        ("user@demo.com",  "51A-67890", "CS01", 1,  20.5, "standard"),
        ("user@demo.com",  "51A-67890", "CS04", 3,  35.0, "standard"),
        ("user2@demo.com", "51B-22222", "CS02", 2,  3.5,  "standard"),
        ("user3@demo.com", "51C-33333", "CS05", 4,  40.0, "standard"),
        ("user3@demo.com", "51C-33333", "CS03", 7,  25.0, "standard"),
        ("user@demo.com",  "51A-67890", "CS06", 10, 50.0, "standard"),
        ("user3@demo.com", "51C-33333", "CS01", 15, 30.0, "standard"),
        ("user@demo.com",  "51A-67890", "CS04", 20, 45.0, "standard"),
        ("user2@demo.com", "51B-22222", "CS02", 22, 2.0,  "standard"),
        ("user3@demo.com", "51C-33333", "CS05", 25, 60.0, "standard"),
    ]
    for email, plate, station_code, days, kwh, ctype in charging_history:
        make_charging_history(email, plate, station_code, days, kwh, ctype)

    conn.commit()
    print("[DB] Seed data loaded successfully.")

# ─────────────────────────────────────────────────────────────────────────────
# KHỞI TẠO DATABASE — Được gọi khi app.py khởi động
# ─────────────────────────────────────────────────────────────────────────────

def init_db():
    """
    Hàm chính để khởi tạo toàn bộ database.
    Gọi từ: app.py → if __name__ == '__main__': init_db()

    Bước 1: Connect MySQL không chỉ định DB → CREATE DATABASE IF NOT EXISTS
    Bước 2: Connect lại với DB → CREATE TABLE IF NOT EXISTS cho 7 bảng
    Bước 3: Nếu bảng users rỗng → seed_data() để tạo dữ liệu demo
    """
    # Bước 1: Tạo database (nếu chưa có)
    conn0 = pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )
    with conn0.cursor() as cur:
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
    conn0.commit()
    conn0.close()

    # Bước 2: Tạo các bảng từ danh sách TABLES
    conn = get_db()
    with conn.cursor() as cur:
        for ddl in TABLES:
            cur.execute(ddl)
        conn.commit()

        # Bước 2.5: Migration — thêm cột balance nếu DB cũ chưa có
        try:
            cur.execute("SELECT balance FROM users LIMIT 1")
        except Exception:
            print("[DB] Migration: Adding 'balance' column to users table...")
            cur.execute(f"ALTER TABLE users ADD COLUMN balance INT NOT NULL DEFAULT 0")
            cur.execute(f"UPDATE users SET balance={DEFAULT_WALLET_BALANCE} WHERE balance=0")
            conn.commit()
            print("[DB] Migration complete.")

        # Bước 2.6: Migration — tạo bảng bookings nếu chưa có
        try:
            cur.execute("SELECT id FROM bookings LIMIT 1")
        except Exception:
            print("[DB] Migration: Creating 'bookings' table...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    id              INT AUTO_INCREMENT PRIMARY KEY,
                    user_id         INT NOT NULL,
                    vehicle_id      INT NOT NULL,
                    slot_id         INT NOT NULL,
                    scheduled_time  DATETIME NOT NULL,
                    duration_hours  FLOAT NOT NULL DEFAULT 1,
                    total_fee       INT NOT NULL DEFAULT 0,
                    penalty_fee     INT NOT NULL DEFAULT 0,
                    refund_fee      INT NOT NULL DEFAULT 0,
                    status          ENUM('pending','active','completed','no_show','cancelled')
                                    NOT NULL DEFAULT 'pending',
                    notes           TEXT,
                    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    checkin_at      DATETIME,
                    checkout_at     DATETIME,
                    FOREIGN KEY (user_id)    REFERENCES users(id),
                    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id),
                    FOREIGN KEY (slot_id)    REFERENCES parking_slots(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            conn.commit()
            print("[DB] Migration: bookings table created.")

        # Bước 2.7: Migration — thêm cột booking_id vào parking_orders
        try:
            cur.execute("SELECT booking_id FROM parking_orders LIMIT 1")
        except Exception:
            print("[DB] Migration: Adding 'booking_id' to parking_orders...")
            cur.execute(
                "ALTER TABLE parking_orders "
                "ADD COLUMN booking_id INT DEFAULT NULL, "
                "ADD COLUMN early_fee INT NOT NULL DEFAULT 0, "
                "ADD COLUMN booking_credit INT NOT NULL DEFAULT 0"
            )
            conn.commit()
            print("[DB] Migration: parking_orders updated.")

        # Bước 3: Kiểm tra và seed nếu DB mới trống
        cur.execute("SELECT COUNT(*) as cnt FROM users")
        if cur.fetchone()["cnt"] == 0:
            seed_data(conn)   # Tạo dữ liệu mẫu

    conn.close()
    print(f"[DB] MySQL database '{DB_NAME}' ready.")

if __name__ == "__main__":
    init_db()
    print("[DB] Done! Run app.py to start the server.")
