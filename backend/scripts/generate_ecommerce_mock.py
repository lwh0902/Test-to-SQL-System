"""电商 Mock 数据生成 - 带业务规律和异常埋点

业务规律：
1. 周末订单量上涨 30%
2. 渠道流量差异：search 流量大但转化低
3. 某商品退款率异常升高
4. 最近 3 天转化率下降
5. 大促日（每月 15 号）GMV 上涨 80%
"""

import random
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from faker import Faker
from sqlalchemy import text

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import engine

fake = Faker("zh_CN")
random.seed(42)

CATEGORIES = ["手机", "电脑", "配件", "家居", "服饰", "食品"]
BRANDS = {
    "手机": ["苹果", "华为", "小米", "OPPO"],
    "电脑": ["联想", "戴尔", "苹果", "华为"],
    "配件": ["苹果", "小米", "华为", "绿联"],
    "家居": ["宜家", "网易严选", "小米"],
    "服饰": ["优衣库", "ZARA", "Nike"],
    "食品": ["三只松鼠", "百草味", "良品铺子"],
}
PRICE_RANGE = {
    "手机": (2000, 8999), "电脑": (3000, 12000), "配件": (29, 599),
    "家居": (49, 2999), "服饰": (59, 1999), "食品": (9, 199),
}
CHANNELS = ["direct", "search", "social", "ads", "email"]
PAY_CHANNELS = ["alipay", "wechat", "card"]
REFUND_REASONS = ["质量问题", "不喜欢", "发货慢", "商品损坏", "其他"]
SOURCE_CHANNELS_WEIGHT = [30, 25, 20, 15, 10]  # direct 流量最大


def gen_products(n=80):
    products = []
    for _ in range(n):
        cat = random.choice(CATEGORIES)
        brand = random.choice(BRANDS[cat])
        low, high = PRICE_RANGE[cat]
        price = round(random.uniform(low, high), 2)
        cost = round(price * random.uniform(0.3, 0.7), 2)
        name = f"{brand} {fake.word()} {cat}"
        products.append((name, cat, brand, price, cost))
    return products


def gen_users(n=500):
    users = []
    for _ in range(n):
        ch = random.choices(CHANNELS, weights=SOURCE_CHANNELS_WEIGHT)[0]
        users.append({
            "nickname": fake.name(),
            "phone": fake.phone_number(),
            "gender": random.choice(["male", "female", "unknown"]),
            "age_group": random.choices(["18-24", "25-34", "35-44", "45+"], weights=[20, 40, 25, 15])[0],
            "channel": ch,
            "workspace_id": "default",
        })
    return users


def generate():
    print("Generating ecommerce mock data...")

    # 1. 商品
    products = gen_products(80)
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE ecom_products"))
        for name, cat, brand, price, cost in products:
            conn.execute(text("""
                INSERT INTO ecom_products (name, category, brand, price, cost_price, workspace_id)
                VALUES (:name, :cat, :brand, :price, :cost, 'default')
            """), {"name": name, "cat": cat, "brand": brand, "price": price, "cost": cost})
        conn.commit()
    print(f"  ✓ {len(products)} products")

    # 2. 用户
    users = gen_users(500)
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE ecom_users"))
        for u in users:
            conn.execute(text("""
                INSERT INTO ecom_users (nickname, phone, gender, age_group, register_channel, workspace_id)
                VALUES (:nickname, :phone, :gender, :age_group, :channel, :workspace_id)
            """), u)
        conn.commit()
    print(f"  ✓ {len(users)} users")

    # 3. 生成 30 天数据
    now = datetime.now()
    order_count = 0
    item_count = 0
    traffic_count = 0
    refund_count = 0

    product_ids = list(range(1, len(products) + 1))
    user_ids = list(range(1, len(users) + 1))

    # 异常埋点：商品 #3 退款率高
    high_refund_product = 3
    # 异常埋点：渠道 'ads' 转化率低

    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE ecom_order_items"))
        conn.execute(text("TRUNCATE TABLE ecom_orders"))
        conn.execute(text("TRUNCATE TABLE ecom_traffic_events"))
        conn.execute(text("TRUNCATE TABLE ecom_refunds"))

        for day_offset in range(30):
            day = now - timedelta(days=29 - day_offset)
            day_str = day.strftime("%Y-%m-%d")
            is_weekend = day.weekday() >= 5
            # 每月 15 号大促
            is_promo = day.day == 15

            # 订单量基础：100-150 / 天
            base_orders = random.randint(100, 150)
            if is_weekend:
                base_orders = int(base_orders * 1.3)  # 周末上涨 30%
            if is_promo:
                base_orders = int(base_orders * 1.8)  # 大促日上涨 80%

            # 异常埋点：最近 3 天转化率下降 → 减少订单量
            if day_offset >= 27:
                base_orders = int(base_orders * 0.7)

            # 流量事件
            for _ in range(base_orders * random.randint(4, 6)):
                uid = random.choice(user_ids)
                ch = random.choices(CHANNELS, weights=SOURCE_CHANNELS_WEIGHT)[0]
                evt = random.choices(
                    ["page_view", "add_to_cart", "checkout", "pay_click"],
                    weights=[50, 25, 15, 10]
                )[0]
                device = random.choices(["ios", "android", "pc", "h5"], weights=[25, 30, 35, 10])[0]
                hour = random.choices(range(24), weights=[
                    1, 1, 1, 1, 1, 2, 3, 5, 8, 10, 10, 10,
                    12, 10, 8, 8, 10, 12, 15, 15, 12, 8, 5, 2
                ])[0]

                ts = day.replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59))
                conn.execute(text("""
                    INSERT INTO ecom_traffic_events (user_id, event_type, source_channel, device_type, workspace_id, created_at)
                    VALUES (:uid, :evt, :ch, :device, 'default', :ts)
                """), {"uid": uid, "evt": evt, "ch": ch, "device": device, "ts": ts})
                traffic_count += 1

            # 订单
            for _ in range(base_orders):
                uid = random.choice(user_ids)
                ch = random.choices(CHANNELS, weights=SOURCE_CHANNELS_WEIGHT)[0]

                # 决定状态（大部分已完成）
                status = random.choices(
                    ["pending", "paid", "shipped", "completed", "cancelled", "refunded"],
                    weights=[2, 3, 5, 70, 10, 10]
                )[0]

                # 选 1-3 个商品
                n_items = random.choices([1, 2, 3], weights=[60, 30, 10])[0]
                selected_products = random.sample(product_ids, n_items)
                total = Decimal("0")

                for pid in selected_products:
                    p = products[pid - 1]
                    qty = random.randint(1, 3)
                    price = Decimal(str(p[3]))
                    total += price * qty

                discount = total * Decimal(str(round(random.uniform(0, 0.15), 2)))
                pay_amount = total - discount

                order_no = f"ORD{uuid.uuid4().hex[:12].upper()}"
                pay_ch = random.choice(PAY_CHANNELS)
                hour = random.choices(range(24), weights=[
                    1, 1, 1, 1, 1, 2, 3, 5, 8, 10, 10, 10,
                    12, 10, 8, 8, 10, 12, 15, 15, 12, 8, 5, 2
                ])[0]
                ts = day.replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59))

                conn.execute(text("""
                    INSERT INTO ecom_orders (order_no, user_id, status, total_amount, pay_amount, discount_amount,
                        pay_channel, pay_time, source_channel, is_promotion, workspace_id, created_at)
                    VALUES (:ono, :uid, :status, :total, :pay, :disc, :pch, :pts, :ch, :promo, 'default', :ts)
                """), {
                    "ono": order_no, "uid": uid, "status": status,
                    "total": float(total), "pay": float(pay_amount), "disc": float(discount),
                    "pch": pay_ch if status != "pending" else None,
                    "pts": ts if status != "pending" else None,
                    "ch": ch, "promo": 1 if is_promo else 0, "ts": ts,
                })
                order_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
                order_count += 1

                # 订单明细
                for pid in selected_products:
                    p = products[pid - 1]
                    qty = random.randint(1, 3)
                    subtotal = Decimal(str(p[3])) * qty
                    conn.execute(text("""
                        INSERT INTO ecom_order_items (order_id, product_id, product_name, category, price, quantity, subtotal, workspace_id)
                        VALUES (:oid, :pid, :pname, :cat, :price, :qty, :sub, 'default')
                    """), {
                        "oid": order_id, "pid": pid, "pname": p[0], "cat": p[1],
                        "price": float(p[3]), "qty": qty, "sub": float(subtotal),
                    })
                    item_count += 1

                    # 退款（商品 #3 退款率高）
                    refund_prob = 0.15 if pid == high_refund_product else 0.05
                    if status == "completed" and random.random() < refund_prob:
                        reason = random.choice(REFUND_REASONS)
                        refund_amt = subtotal * Decimal(str(round(random.uniform(0.5, 1.0), 2)))
                        conn.execute(text("""
                            INSERT INTO ecom_refunds (order_id, order_item_id, user_id, product_id, refund_amount, reason, status, workspace_id, created_at)
                            VALUES (:oid, :oitemid, :uid, :pid, :ramt, :reason, 'completed', 'default', :ts)
                        """), {
                            "oid": order_id, "oitemid": 0, "uid": uid, "pid": pid,
                            "ramt": float(refund_amt), "reason": reason, "ts": ts + timedelta(days=random.randint(1, 5)),
                        })
                        refund_count += 1

        conn.commit()

    print(f"  ✓ {order_count} orders")
    print(f"  ✓ {item_count} order items")
    print(f"  ✓ {traffic_count} traffic events")
    print(f"  ✓ {refund_count} refunds")
    print("Done!")


if __name__ == "__main__":
    generate()
