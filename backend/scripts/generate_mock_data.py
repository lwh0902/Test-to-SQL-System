"""
Mock 数据生成脚本

生成规则：
- users: 500 条，真实中国姓名和手机号
- scan_records: 8000 条，30 天分布，最近 2 天 OCR_TIMEOUT 突增
- feature_events: 6000 条，30 天分布
- api_logs: 10000 条，30 天分布，某接口 500 增多

异常埋点：
1. 最近 2 天 OCR_TIMEOUT 错误占比从 ~5% 升到 ~25%
2. Android 12 成功率从 ~92% 降到 ~70%
3. 每天 20:00-23:00 失败率比白天高 ~15%
4. 最近 2 天 /api/v2/verify 接口 500 从 ~2% 升到 ~15%
"""

import random
from datetime import datetime, timedelta

import pymysql
from faker import Faker

# 配置
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "datacheck",
    "charset": "utf8mb4",
}

FAKE = Faker("zh_CN")

# 真实中国姓名库
SURNAMES = [
    "王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴",
    "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗",
    "梁", "宋", "郑", "谢", "韩", "唐", "冯", "于", "董", "萧",
]
GIVEN_NAMES = [
    "伟", "芳", "娜", "秀英", "敏", "静", "强", "磊", "洋", "勇",
    "艳", "杰", "娟", "涛", "明", "超", "秀兰", "霞", "平", "刚",
    "鑫", "浩", "宇", "婷", "欣", "佳", "思远", "子涵", "雨泽", "梓萱",
    "一诺", "艺涵", "子轩", "紫萱", "浩然", "诗涵", "沐阳", "若曦", "逸飞", "天佑",
]

SCAN_TYPES = ["id_card", "bank_card", "face", "ocr"]
SCAN_TYPE_WEIGHTS = [35, 20, 25, 20]

DEVICE_TYPES = ["ios", "android", "web"]
DEVICE_TYPE_WEIGHTS = [30, 50, 20]

IOS_VERSIONS = ["15.0", "15.5", "16.0", "16.5", "17.0", "17.2", "17.5", "18.0"]
ANDROID_VERSIONS = ["10", "11", "12", "13", "14"]
WEB_VERSIONS = ["Chrome 120", "Chrome 121", "Safari 17", "Firefox 122"]

ERROR_TYPES = [
    "OCR_TIMEOUT", "LOW_QUALITY", "FACE_MISMATCH", "ID_CARD_EXPIRED",
    "BLUR_DETECTED", "LIGHT_TOO_DARK", "LIGHT_TOO_BRIGHT", "SYSTEM_ERROR",
]
ERROR_TYPE_WEIGHTS = [5, 25, 20, 10, 15, 10, 10, 5]

APP_VERSIONS = ["3.1.0", "3.2.0", "3.2.1", "3.3.0", "3.3.1", "3.4.0"]

FEATURE_NAMES = ["liveness_detection", "id_ocr", "bank_card_ocr", "face_compare", "risk_assessment"]
EVENT_TYPES_MAP = {
    "liveness_detection": ["liveness_started", "liveness_completed", "liveness_failed"],
    "id_ocr": ["id_uploaded", "ocr_completed", "ocr_failed"],
    "bank_card_ocr": ["card_uploaded", "card_ocr_completed", "card_ocr_failed"],
    "face_compare": ["face_uploaded", "compare_completed", "compare_failed"],
    "risk_assessment": ["risk_check_started", "risk_check_completed", "risk_check_failed"],
}

API_NAMES = [
    "/api/v2/scan", "/api/v2/verify", "/api/v2/ocr", "/api/v2/face",
    "/api/v2/bankcard", "/api/v1/health", "/api/v1/report",
]
API_METHODS = {
    "/api/v2/scan": "POST", "/api/v2/verify": "POST",
    "/api/v2/ocr": "POST", "/api/v2/face": "POST",
    "/api/v2/bankcard": "POST", "/api/v1/health": "GET",
    "/api/v1/report": "GET",
}

ROLES = ["admin", "tester", "product", "developer"]
ROLE_WEIGHTS = [5, 40, 30, 25]

WORKSPACE_IDS = ["default", "ws_tech", "ws_finance"]


def generate_phone():
    prefixes = ["130", "131", "132", "133", "135", "136", "137", "138", "139",
                "150", "151", "152", "155", "156", "157", "158", "159",
                "180", "181", "182", "183", "185", "186", "187", "188", "189"]
    return random.choice(prefixes) + "".join([str(random.randint(0, 9)) for _ in range(8)])


def generate_id_card():
    area_codes = ["110101", "310101", "440103", "500103", "330102", "510104",
                  "420102", "320102", "370102", "610102"]
    area = random.choice(area_codes)
    birth = f"{random.randint(1970, 2002)}{random.randint(1, 12):02d}{random.randint(1, 28):02d}"
    seq = f"{random.randint(0, 999):03d}"
    base = area + birth + seq
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_chars = "10X98765432"
    check = check_chars[sum(int(b) * w for b, w in zip(base, weights)) % 11]
    return base + check


def generate_name():
    return random.choice(SURNAMES) + random.choice(GIVEN_NAMES)


def generate_users(count=500):
    users = []
    seen_phones = set()
    for _ in range(count):
        phone = generate_phone()
        while phone in seen_phones:
            phone = generate_phone()
        seen_phones.add(phone)

        users.append({
            "name": generate_name(),
            "phone": phone,
            "id_card": generate_id_card() if random.random() < 0.7 else None,
            "role": random.choices(ROLES, weights=ROLE_WEIGHTS, k=1)[0],
            "workspace_id": random.choices(WORKSPACE_IDS, weights=[60, 25, 15], k=1)[0],
            "status": "active" if random.random() < 0.95 else "inactive",
            "created_at": FAKE.date_time_between(start_date="-60d", end_date="-10d"),
        })
    return users


def is_anomaly_period(dt):
    """判断是否在异常时间段"""
    now = datetime.now()
    # 最近 2 天
    is_recent_2_days = (now - dt).days <= 2
    # 晚间高峰 20:00-23:00
    is_night_peak = 20 <= dt.hour <= 23
    return is_recent_2_days, is_night_peak


def generate_scan_records(users, count=8000):
    records = []
    now = datetime.now()
    start = now - timedelta(days=30)

    for _ in range(count):
        user = random.choice(users)

        # 时间分布：工作日多一些，周末少一些
        dt = FAKE.date_time_between(start_date=start, end_date=now)
        while dt.weekday() >= 5 and random.random() < 0.4:
            dt = FAKE.date_time_between(start_date=start, end_date=now)

        is_recent, is_night = is_anomaly_period(dt)

        scan_type = random.choices(SCAN_TYPES, weights=SCAN_TYPE_WEIGHTS, k=1)[0]
        device_type = random.choices(DEVICE_TYPES, weights=DEVICE_TYPE_WEIGHTS, k=1)[0]

        if device_type == "ios":
            os_version = random.choice(IOS_VERSIONS)
        elif device_type == "android":
            os_version = random.choice(ANDROID_VERSIONS)
        else:
            os_version = random.choice(WEB_VERSIONS)

        # 计算成功率（含异常逻辑）
        base_success_rate = 0.92
        if is_recent and scan_type == "ocr":
            base_success_rate -= 0.20  # 最近2天OCR成功率骤降
        if is_night:
            base_success_rate -= 0.12  # 晚间失败率升高
        if device_type == "android" and os_version == "12":
            base_success_rate -= 0.22  # Android 12 成功率低

        is_success = random.random() < base_success_rate
        error_type = None
        duration_ms = random.randint(200, 3000) if is_success else random.randint(500, 8000)

        if not is_success:
            if is_recent and scan_type == "ocr" and random.random() < 0.55:
                error_type = "OCR_TIMEOUT"  # 最近2天OCR_TIMEOUT突增
            else:
                error_type = random.choices(ERROR_TYPES, weights=ERROR_TYPE_WEIGHTS, k=1)[0]
            if is_night and random.random() < 0.3:
                error_type = random.choice(["LIGHT_TOO_DARK", "LOW_QUALITY"])

        records.append({
            "user_id": user["id"],
            "scan_type": scan_type,
            "status": "success" if is_success else "failed",
            "error_type": error_type,
            "device_type": device_type,
            "device_os_version": os_version,
            "app_version": random.choice(APP_VERSIONS),
            "duration_ms": duration_ms,
            "datasource_id": "default",
            "workspace_id": user["workspace_id"],
            "created_at": dt,
        })
    return records


def generate_feature_events(users, count=6000):
    events = []
    now = datetime.now()
    start = now - timedelta(days=30)

    for _ in range(count):
        user = random.choice(users)
        dt = FAKE.date_time_between(start_date=start, end_date=now)

        feature = random.choice(FEATURE_NAMES)
        event_type = random.choice(EVENT_TYPES_MAP[feature])

        is_success = not event_type.endswith("failed")
        error_code = None
        if not is_success:
            error_code = f"ERR_{random.randint(1000, 9999)}"

        events.append({
            "user_id": user["id"],
            "event_type": event_type,
            "feature_name": feature,
            "is_success": 1 if is_success else 0,
            "error_code": error_code,
            "datasource_id": "default",
            "workspace_id": user["workspace_id"],
            "created_at": dt,
        })
    return events


def generate_api_logs(users, count=10000):
    logs = []
    now = datetime.now()
    start = now - timedelta(days=30)

    for _ in range(count):
        user = random.choice(users)
        dt = FAKE.date_time_between(start_date=start, end_date=now)

        api_name = random.choice(API_NAMES)
        method = API_METHODS[api_name]

        is_recent, is_night = is_anomaly_period(dt)

        # 异常埋点：最近2天 /api/v2/verify 500增多
        if is_recent and api_name == "/api/v2/verify":
            is_error = random.random() < 0.15
        else:
            is_error = random.random() < 0.03

        if is_night:
            is_error = is_error or random.random() < 0.05

        status_code = 200
        error_message = None
        if is_error:
            status_code = random.choices([500, 502, 503, 429, 400], weights=[40, 20, 15, 15, 10], k=1)[0]
            error_messages = {
                500: "Internal Server Error", 502: "Bad Gateway",
                503: "Service Unavailable", 429: "Too Many Requests",
                400: "Bad Request",
            }
            error_message = error_messages[status_code]

        response_time = random.randint(50, 500)
        if is_error:
            response_time = random.randint(1000, 10000)
        if api_name in ["/api/v2/scan", "/api/v2/face"]:
            response_time += random.randint(100, 800)

        logs.append({
            "user_id": user["id"],
            "api_name": api_name,
            "method": method,
            "status_code": status_code,
            "response_time_ms": response_time,
            "is_error": 1 if is_error else 0,
            "error_message": error_message,
            "client_ip": f"192.168.{random.randint(1, 254)}.{random.randint(1, 254)}",
            "datasource_id": "default",
            "workspace_id": user["workspace_id"],
            "created_at": dt,
        })
    return logs


def insert_batch(cursor, table, rows):
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_str = ", ".join(cols)
    sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})"
    values = [tuple(row[c] for c in cols) for row in rows]
    cursor.executemany(sql, values)


def main():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # 清空旧数据
    for table in ["api_logs", "feature_events", "scan_records", "users"]:
        cursor.execute(f"TRUNCATE TABLE {table}")

    # 生成 users
    print("Generating users...")
    users = generate_users(500)
    insert_batch(cursor, "users", users)
    conn.commit()

    # 获取带 id 的 user 列表
    cursor.execute("SELECT id, workspace_id FROM users")
    user_rows = cursor.fetchall()
    users_with_id = []
    for row, user in zip(user_rows, users):
        user["id"] = row[0]
        users_with_id.append(user)

    # 生成 scan_records
    print("Generating scan_records...")
    records = generate_scan_records(users_with_id, 8000)
    insert_batch(cursor, "scan_records", records)
    conn.commit()

    # 生成 feature_events
    print("Generating feature_events...")
    events = generate_feature_events(users_with_id, 6000)
    insert_batch(cursor, "feature_events", events)
    conn.commit()

    # 生成 api_logs
    print("Generating api_logs...")
    logs = generate_api_logs(users_with_id, 10000)
    insert_batch(cursor, "api_logs", logs)
    conn.commit()

    # 统计
    for table in ["users", "scan_records", "feature_events", "api_logs"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  {table}: {count} rows")

    cursor.close()
    conn.close()
    print("Done!")


if __name__ == "__main__":
    main()
