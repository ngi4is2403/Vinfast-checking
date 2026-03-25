import os

class Config:
    # Cấu hình cơ sở dữ liệu MySQL
    # Thay đổi user, password, host và database_name phù hợp với hệ thống của bạn
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'mysql+pymysql://root:@localhost/parking_management'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-12345'
