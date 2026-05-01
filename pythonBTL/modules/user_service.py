# =============================================================================
# modules/user_service.py — Dịch vụ quản lý tài khoản và phương tiện
#
# Module này xử lý toàn bộ nghiệp vụ liên quan đến người dùng:
#   - Đăng ký / đăng nhập / đăng xuất
#   - Cập nhật hồ sơ và đổi mật khẩu
#   - Thêm / sửa / xóa phương tiện
#
# Tất cả hàm trả về dict dạng:
#   ok(data)  → {"success": True,  "message": "...", "data": ...}
#   err(msg)  → {"success": False, "message": "...", "data": None}
# Điều này giúp app.py kiểm tra result["success"] nhất quán.
# =============================================================================

import hashlib
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import get_db, row_to_dict, rows_to_dicts
from config import MIN_PASSWORD_LENGTH, ELECTRIC_TYPES, DEFAULT_WALLET_BALANCE

# ─────────────────────────────────────────────────────────────────────────────
# HÀM TIỆN ÍCH — Chuẩn hóa kiểu trả về
# ─────────────────────────────────────────────────────────────────────────────
def ok(data=None, message="Thanh cong"):
    """Trả về kết quả thành công kèm data tùy ý."""
    return {"success": True, "message": message, "data": data}

def err(message="Co loi xay ra"):
    """Trả về lỗi kèm thông báo."""
    return {"success": False, "message": message, "data": None}

def hash_password(p):
    """Mã hóa mật khẩu SHA-256 — không lưu plain text vào DB."""
    return hashlib.sha256(p.encode()).hexdigest()

# =============================================================================
# 1. ĐĂNG KÝ TÀI KHOẢN MỚI
# =============================================================================
#
# Validate trước khi insert:
#   - Không được để trống bất kỳ trường nào
#   - Mật khẩu xác nhận phải khớp
#   - Mật khẩu ≥ MIN_PASSWORD_LENGTH ký tự
#   - Email và SĐT không được trùng với tài khoản đã có

def register_user(full_name, phone, email, password, confirm_password):
    if not all([full_name.strip(), phone.strip(), email.strip(), password]):
        return err("Vui long dien day du thong tin.")
    if password != confirm_password:
        return err("Mat khau xac nhan khong khop.")
    if len(password) < MIN_PASSWORD_LENGTH:
        return err(f"Mat khau phai co it nhat {MIN_PASSWORD_LENGTH} ky tu.")
    phone = phone.strip()
    email = email.strip().lower()

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE email=%s OR phone=%s", (email, phone))
        if cur.fetchone():
            return err("Email hoac so dien thoai da duoc su dung.")
        cur.execute(
            "INSERT INTO users (full_name,phone,email,password_hash,role,balance) VALUES (%s,%s,%s,%s,'user',%s)",
            (full_name.strip(), phone, email, hash_password(password), DEFAULT_WALLET_BALANCE)
        )
        uid = cur.lastrowid
        # Tạo giao dịch nạp tiền khởi đầu cho ví
        if DEFAULT_WALLET_BALANCE > 0:
            cur.execute(
                "INSERT INTO wallet_transactions (user_id,tx_type,amount,balance_after,description) VALUES (%s,'topup',%s,%s,'Nap tien khuyen mai khi dang ky')",
                (uid, DEFAULT_WALLET_BALANCE, DEFAULT_WALLET_BALANCE)
            )
        conn.commit()
        return ok({"user_id": uid}, "Dang ky thanh cong!")
    except Exception as e:
        conn.rollback()
        return err(f"Loi he thong: {e}")
    finally:
        conn.close()

# =============================================================================
# 2. ĐĂNG NHẬP
# =============================================================================
#
# Hỗ trợ đăng nhập bằng email HOẶC số điện thoại (credential).
# So sánh password_hash — không bao giờ lấy mật khẩu ra so sánh trực tiếp.
# Khi thành công: trả về toàn bộ thông tin user (trừ password_hash).

def login(email_or_phone, password):
    if not email_or_phone or not password:
        return err("Vui long nhap tai khoan va mat khau.")
    credential = email_or_phone.strip().lower()
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM users WHERE (email=%s OR phone=%s) AND password_hash=%s",
            (credential, email_or_phone.strip(), hash_password(password))
        )
        row = cur.fetchone()
        if not row:
            return err("Tai khoan hoac mat khau khong dung.")
        user = row_to_dict(row)
        user.pop("password_hash", None)
        return ok(user, "Dang nhap thanh cong!")
    finally:
        conn.close()

# =============================================================================
# 3. CẬP NHẬT HỒ SƠ CÁ NHÂN
# =============================================================================
# Cho phép user sửa họ tên và SĐT.
# Không cho sửa email (dùng để đăng nhập, cần tính ổn định).
# Kiểm tra SĐT mới có trùng với người khác không.

def update_profile(user_id, full_name, phone):
    if not full_name.strip():
        return err("Ho ten khong duoc de trong.")
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE phone=%s AND id!=%s", (phone.strip(), user_id))
        if cur.fetchone():
            return err("So dien thoai da duoc su dung boi tai khoan khac.")
        cur.execute("UPDATE users SET full_name=%s, phone=%s WHERE id=%s",
                    (full_name.strip(), phone.strip(), user_id))
        conn.commit()
        return ok(message="Cap nhat thong tin thanh cong!")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

# =============================================================================
# 4. ĐỔI MẬT KHẨU
# =============================================================================
# Bảo mật 2 lớp:
#   1. Phải biết mật khẩu hiện tại (verify trước khi đổi)
#   2. Mật khẩu mới phải được xác nhận lại (confirm)

def change_password(user_id, old_password, new_password, confirm_new):
    if new_password != confirm_new:
        return err("Mat khau xac nhan khong khop.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return err(f"Mat khau moi phai co it nhat {MIN_PASSWORD_LENGTH} ky tu.")
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE id=%s AND password_hash=%s",
                    (user_id, hash_password(old_password)))
        if not cur.fetchone():
            return err("Mat khau hien tai khong dung.")
        cur.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                    (hash_password(new_password), user_id))
        conn.commit()
        return ok(message="Doi mat khau thanh cong!")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

# =============================================================================
# 5. QUẢN LÝ PHƯƠNG TIỆN
# =============================================================================
#
# Mỗi user có thể đăng ký nhiều xe.
# Biển số là UNIQUE toàn hệ thống (không thể hai user cùng một biển số).
# Xe điện (ELECTRIC_TYPES) bắt buộc nhập battery_capacity để tính thời gian sạc ước tính.
# Không thể xóa xe đang có đơn gửi hoặc sạc chưa hoàn tất.

def get_vehicles(user_id):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT * FROM vehicles WHERE user_id=%s ORDER BY created_at DESC", (user_id,))
        return ok(rows_to_dicts(cur.fetchall()))
    finally:
        conn.close()

def add_vehicle(user_id, plate_number, vehicle_type,
                brand="", model="", color="", battery_capacity=None):
    plate = plate_number.strip().upper()
    if not plate:
        return err("Bien so xe khong duoc de trong.")
    if vehicle_type not in ["motorcycle", "car", "e_motorcycle", "e_car"]:
        return err("Loai xe khong hop le.")
    if vehicle_type in ELECTRIC_TYPES and (battery_capacity is None or battery_capacity <= 0):
        return err("Xe dien phai khai bao dung luong pin (kWh).")
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id FROM vehicles WHERE plate_number=%s", (plate,))
        if cur.fetchone():
            return err("Bien so xe da ton tai trong he thong.")
        cur.execute(
            "INSERT INTO vehicles (user_id,plate_number,vehicle_type,brand,model,color,battery_capacity) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (user_id, plate, vehicle_type, brand.strip(), model.strip(), color.strip(), battery_capacity)
        )
        conn.commit()
        return ok({"vehicle_id": cur.lastrowid}, "Them phuong tien thanh cong!")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def update_vehicle(vehicle_id, user_id, brand="", model="", color="", battery_capacity=None):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM vehicles WHERE id=%s", (vehicle_id,))
        row = cur.fetchone()
        if not row:
            return err("Xe khong ton tai.")
        if row["user_id"] != user_id:
            return err("Ban khong co quyen sua xe nay.")
        cur.execute(
            "UPDATE vehicles SET brand=%s,model=%s,color=%s,battery_capacity=%s WHERE id=%s",
            (brand.strip(), model.strip(), color.strip(), battery_capacity, vehicle_id)
        )
        conn.commit()
        return ok(message="Cap nhat thong tin xe thanh cong!")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def delete_vehicle(vehicle_id, user_id):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM vehicles WHERE id=%s", (vehicle_id,))
        row = cur.fetchone()
        if not row:
            return err("Xe khong ton tai.")
        if row["user_id"] != user_id:
            return err("Ban khong co quyen xoa xe nay.")
        cur.execute("SELECT id FROM parking_orders WHERE vehicle_id=%s AND status='active'", (vehicle_id,))
        if cur.fetchone():
            return err("Xe dang co don gui xe. Khong the xoa.")
        cur.execute("SELECT id FROM charging_orders WHERE vehicle_id=%s AND status='active'", (vehicle_id,))
        if cur.fetchone():
            return err("Xe dang co don sac. Khong the xoa.")
        cur.execute("DELETE FROM vehicles WHERE id=%s", (vehicle_id,))
        conn.commit()
        return ok(message="Da xoa phuong tien.")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def get_user_by_id(user_id):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT id,full_name,phone,email,role,balance,created_at FROM users WHERE id=%s",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return err("Nguoi dung khong ton tai.")
        return ok(row_to_dict(row))
    finally:
        conn.close()

def get_all_users():
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id,full_name,phone,email,role,balance,created_at FROM users ORDER BY created_at DESC")
        return ok(rows_to_dicts(cur.fetchall()))
    finally:
        conn.close()

# =============================================================================
# 6. VÍ TIỀN (WALLET)
# =============================================================================
#
# Mỗi user có 1 ví tiền (cột balance trong bảng users).
# Khi nạp/rút/thanh toán → cập nhật balance và ghi wallet_transactions.
# Khi checkout gửi xe hoặc kết thúc sạc → trừ tiền ví tự động.

def get_wallet_balance(user_id):
    """Lấy số dư ví hiện tại."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT balance FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        return row["balance"] if row else 0
    finally:
        conn.close()

def wallet_topup(user_id, amount):
    """Nạp tiền vào ví. amount là số tiền nạp (>0)."""
    amount = int(amount)
    if amount <= 0:
        return err("So tien nap phai lon hon 0.")
    if amount < 10_000:
        return err("So tien nap toi thieu la 10.000 VND.")
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT balance FROM users WHERE id=%s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row:
            return err("Nguoi dung khong ton tai.")
        new_balance = row["balance"] + amount
        cur.execute("UPDATE users SET balance=%s WHERE id=%s", (new_balance, user_id))
        cur.execute(
            "INSERT INTO wallet_transactions (user_id,tx_type,amount,balance_after,description) VALUES (%s,'topup',%s,%s,%s)",
            (user_id, amount, new_balance, f"Nap tien vao vi")
        )
        conn.commit()
        return ok({"balance": new_balance}, f"Nap {amount:,} VND thanh cong! So du: {new_balance:,} VND")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def wallet_withdraw(user_id, amount):
    """Rút tiền khỏi ví. amount là số tiền rút (>0)."""
    amount = int(amount)
    if amount <= 0:
        return err("So tien rut phai lon hon 0.")
    if amount < 10_000:
        return err("So tien rut toi thieu la 10.000 VND.")
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT balance FROM users WHERE id=%s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row:
            return err("Nguoi dung khong ton tai.")
        if row["balance"] < amount:
            return err(f"So du khong du. Hien co: {row['balance']:,} VND")
        new_balance = row["balance"] - amount
        cur.execute("UPDATE users SET balance=%s WHERE id=%s", (new_balance, user_id))
        cur.execute(
            "INSERT INTO wallet_transactions (user_id,tx_type,amount,balance_after,description) VALUES (%s,'withdraw',%s,%s,%s)",
            (user_id, amount, new_balance, f"Rut tien tu vi")
        )
        conn.commit()
        return ok({"balance": new_balance}, f"Rut {amount:,} VND thanh cong! So du: {new_balance:,} VND")
    except Exception as e:
        conn.rollback()
        return err(f"Loi: {e}")
    finally:
        conn.close()

def wallet_deduct(user_id, amount, description="Thanh toan dich vu", ref_type=None, ref_id=None, conn=None, cur=None):
    """
    Trừ tiền ví khi thanh toán dịch vụ (gửi xe/sạc).
    Gọi từ parking_service — nhận conn/cur đã mở để giữ transaction.
    Nếu không truyền conn/cur, sẽ tự tạo.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_db()
        cur  = conn.cursor()
    try:
        cur.execute("SELECT balance FROM users WHERE id=%s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row:
            return err("Nguoi dung khong ton tai.")
        if row["balance"] < amount:
            return err(f"So du vi khong du. Can: {amount:,} VND, Hien co: {row['balance']:,} VND")
        new_balance = row["balance"] - amount
        cur.execute("UPDATE users SET balance=%s WHERE id=%s", (new_balance, user_id))
        cur.execute(
            "INSERT INTO wallet_transactions (user_id,tx_type,amount,balance_after,description,ref_type,ref_id) VALUES (%s,'payment',%s,%s,%s,%s,%s)",
            (user_id, amount, new_balance, description, ref_type, ref_id)
        )
        if own_conn:
            conn.commit()
        return ok({"balance": new_balance})
    except Exception as e:
        if own_conn:
            conn.rollback()
        return err(f"Loi tru tien vi: {e}")
    finally:
        if own_conn:
            conn.close()

def get_wallet_history(user_id, limit=20):
    """Lấy lịch sử giao dịch ví."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM wallet_transactions WHERE user_id=%s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit)
        )
        return ok(rows_to_dicts(cur.fetchall()))
    finally:
        conn.close()
