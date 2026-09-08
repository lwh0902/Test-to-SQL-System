"""B2B 出行分销 Mock 数据（用车 / 酒店 / 门票）

业务规律：
1. 暑假（7-8 月）订单量暴涨（门票/酒店更明显）
2. 寒假（1-2 月）轻度上涨
3. 周末用车需求更高
4. 渠道结构：大 OTA 量大，线下旅行社多但单量分散

异常埋点：
A1 指定线下渠道近 14 天几乎 0 单
A2 暑假 service/booked 量级抬升
A3 上海迪士尼一日票取消/退款率显著偏高
"""

from __future__ import annotations

import os
import random
import sys
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.database import engine

random.seed(20260812)

TARGET_ORDERS = 35000
DAYS = 365
WORKSPACE = "default"

# 异常主体/产品标记
DEAD_CHANNEL_CODE = "CH_OFF_HZ_SUNSHINE"  # 近 14 天几乎无单
HIGH_CANCEL_TICKET_CODE = "TKT_SH_DISNEY_1D"  # 高取消门票

CITIES = [
    # domestic
    ("中国", "北京", "domestic", 1.0),
    ("中国", "上海", "domestic", 1.15),
    ("中国", "成都", "domestic", 0.85),
    ("中国", "三亚", "domestic", 1.1),
    ("中国", "西安", "domestic", 0.8),
    ("中国", "杭州", "domestic", 0.95),
    ("中国", "丽江", "domestic", 0.9),
    ("中国", "广州", "domestic", 0.95),
    ("中国", "深圳", "domestic", 1.0),
    ("中国", "重庆", "domestic", 0.85),
    ("中国香港", "香港", "asia", 1.25),
    # asia
    ("日本", "东京", "asia", 1.35),
    ("日本", "大阪", "asia", 1.2),
    ("韩国", "首尔", "asia", 1.1),
    ("泰国", "曼谷", "asia", 0.85),
    ("新加坡", "新加坡", "asia", 1.3),
    ("马来西亚", "吉隆坡", "asia", 0.8),
    # europe
    ("法国", "巴黎", "europe", 1.4),
    ("英国", "伦敦", "europe", 1.45),
    ("意大利", "罗马", "europe", 1.25),
    ("西班牙", "巴塞罗那", "europe", 1.2),
    # americas / oceania
    ("美国", "纽约", "americas", 1.5),
    ("美国", "洛杉矶", "americas", 1.35),
    ("澳大利亚", "悉尼", "oceania", 1.3),
]

HOTEL_BRANDS = [
    "希尔顿", "万豪", "洲际", "凯悦", "雅高", "华住", "锦江", "亚朵",
    "全季", "如家", "桔子", "丽思卡尔顿", "香格里拉", "本地精品",
]

TICKET_ATTRACTIONS = [
    # (code_suffix, name, city_country_match, category, base_price, is_high_cancel)
    ("SH_DISNEY_1D", "上海迪士尼乐园一日票", ("中国", "上海"), "theme_park", 719, True),
    ("SH_DISNEY_2D", "上海迪士尼乐园两日票", ("中国", "上海"), "theme_park", 1299, False),
    ("HK_DISNEY_1D", "香港迪士尼乐园一日票", ("中国香港", "香港"), "theme_park", 639, False),
    ("BJ_UNIVERSAL_1D", "北京环球影城一日票", ("中国", "北京"), "theme_park", 528, False),
    ("TYO_DISNEY_1D", "东京迪士尼乐园一日票", ("日本", "东京"), "theme_park", 890, False),
    ("TYO_SEA_1D", "东京迪士尼海洋一日票", ("日本", "东京"), "theme_park", 890, False),
    ("OSK_USJ_1D", "大阪环球影城一日票", ("日本", "大阪"), "theme_park", 860, False),
    ("SEL_LOTTE_1D", "首尔乐天世界门票", ("韩国", "首尔"), "theme_park", 420, False),
    ("BKK_SAFARI", "曼谷safari世界门票", ("泰国", "曼谷"), "theme_park", 280, False),
    ("SG_USS", "新加坡环球影城门票", ("新加坡", "新加坡"), "theme_park", 680, False),
    ("PAR_DISNEY_1D", "巴黎迪士尼乐园一日票", ("法国", "巴黎"), "theme_park", 980, False),
    ("LON_LONDON_EYE", "伦敦眼门票", ("英国", "伦敦"), "city_pass", 320, False),
    ("NYC_MET", "纽约大都会博物馆门票", ("美国", "纽约"), "museum", 260, False),
    ("LA_UNIVERSAL", "洛杉矶环球影城门票", ("美国", "洛杉矶"), "theme_park", 920, False),
    ("SYD_OPERA", "悉尼歌剧院导览票", ("澳大利亚", "悉尼"), "city_pass", 310, False),
    ("BJ_PALACE", "故宫博物院门票", ("中国", "北京"), "museum", 60, False),
    ("XA_TERRACOTTA", "兵马俑门票", ("中国", "西安"), "museum", 120, False),
    ("LJ_YULONG", "玉龙雪山门票", ("中国", "丽江"), "nature", 100, False),
    ("SY_WZH", "三亚蜈支洲岛门票", ("中国", "三亚"), "nature", 144, False),
    ("CD_PANDA", "成都大熊猫繁育研究基地门票", ("中国", "成都"), "nature", 55, False),
    ("HZ_WST", "杭州西湖游船票", ("中国", "杭州"), "city_pass", 55, False),
    ("GZ_CHIMELONG", "广州长隆欢乐世界门票", ("中国", "广州"), "theme_park", 250, False),
    ("SZ_WINDOW", "深圳世界之窗门票", ("中国", "深圳"), "theme_park", 180, False),
    ("ROM_COLOSSEUM", "罗马斗兽场门票", ("意大利", "罗马"), "museum", 350, False),
    ("BCN_SAGRADA", "圣家堂门票", ("西班牙", "巴塞罗那"), "museum", 280, False),
]

CANCEL_REASONS = ["行程变更", "天气原因", "供应商无位", "客人未出行", "价格原因", "产品下架", "其他"]


def d(v: float) -> Decimal:
    return Decimal(str(round(v, 2)))


def ensure_tables(conn) -> None:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sql_path = os.path.join(root, "config", "init_travel_b2b_tables.sql")
    with open(sql_path, encoding="utf-8") as f:
        raw = f.read()
    # 去掉 USE 语句，当前 engine 已指向 datacheck
    statements = []
    buf = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        if s.upper().startswith("USE "):
            continue
        buf.append(line)
        if s.endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    if buf:
        statements.append("\n".join(buf))
    for stmt in statements:
        conn.execute(text(stmt))
    conn.commit()


def build_parties() -> list[dict]:
    parties: list[dict] = []

    mega = [
        ("CH_KLOOK", "Klook", "Klook", "mega_ota", "亚太", "新加坡"),
        ("CH_AGODA", "Agoda", "Agoda", "mega_ota", "亚太", "泰国"),
        ("CH_TRIP", "携程商旅", "Trip.com Business", "mega_ota", "中国", "中国"),
        ("CH_FLIGGY", "飞猪企业购", "Fliggy Biz", "mega_ota", "中国", "中国"),
        ("CH_MEITUAN", "美团旅行企业", "Meituan Travel", "mega_ota", "中国", "中国"),
        ("CH_GETYOURGUIDE", "GetYourGuide", "GetYourGuide", "mega_ota", "欧洲", "德国"),
        ("CH_VIATOR", "Viator", "Viator", "mega_ota", "全球", "美国"),
        ("CH_KKDAY", "KKday", "KKday", "mega_ota", "亚太", "中国台湾"),
    ]
    for code, name, name_en, kind, region, country in mega:
        parties.append(
            {
                "code": code,
                "name": name,
                "name_en": name_en,
                "role_type": "channel",
                "party_kind": kind,
                "region_focus": region,
                "country_focus": country,
            }
        )

    regional = [
        ("CH_REG_TUNIU", "途牛企业频道", "regional_ota", "中国", "中国"),
        ("CH_REG_MAFENGWO", "马蜂窝商家", "regional_ota", "中国", "中国"),
        ("CH_REG_QUNAR_B", "去哪儿商旅", "regional_ota", "中国", "中国"),
        ("CH_REG_LY", "同程商旅", "regional_ota", "中国", "中国"),
        ("CH_REG_JALAN", "Jalan网", "regional_ota", "日本", "日本"),
        ("CH_REG_YANOLJA", "Yanolja企业", "regional_ota", "韩国", "韩国"),
        ("CH_REG_TRAZY", "Trazy", "regional_ota", "韩国", "韩国"),
        ("CH_REG_KLOOK_CN", "Klook中国渠道包", "regional_ota", "中国", "中国"),
        ("CH_REG_HZ_TRAVEL", "杭州本地游精选", "regional_ota", "华东", "中国"),
        ("CH_REG_CD_PANDA", "成都熊猫假期线上", "regional_ota", "西南", "中国"),
        ("CH_REG_SEA_PASS", "东南亚通票站", "regional_ota", "东南亚", "新加坡"),
        ("CH_REG_EU_CITY", "欧洲城市通", "regional_ota", "欧洲", "法国"),
    ]
    for code, name, kind, region, country in regional:
        parties.append(
            {
                "code": code,
                "name": name,
                "name_en": None,
                "role_type": "channel",
                "party_kind": kind,
                "region_focus": region,
                "country_focus": country,
            }
        )

    offline = [
        ("CH_OFF_BJ_GOLDEN", "北京金桥旅行社", "华北", "中国"),
        ("CH_OFF_SH_ORIENT", "上海东方假期门店", "华东", "中国"),
        ("CH_OFF_GZ_PEARL", "广州珠江国旅", "华南", "中国"),
        ("CH_OFF_SZ_BAY", "深圳湾国际旅行社", "华南", "中国"),
        ("CH_OFF_CD_JIUZHAI", "成都九寨假期", "西南", "中国"),
        ("CH_OFF_KM_SPRING", "昆明春城国旅", "西南", "中国"),
        ("CH_OFF_XA_SILK", "西安丝路行", "西北", "中国"),
        ("CH_OFF_HZ_WESTLAKE", "杭州西湖假期", "华东", "中国"),
        (DEAD_CHANNEL_CODE, "杭州阳光假期门店", "华东", "中国"),  # A1
        ("CH_OFF_SY_SANYA", "三亚椰风旅行社", "海南", "中国"),
        ("CH_OFF_HK_HARBOR", "香港维港旅行社", "港澳", "中国香港"),
        ("CH_OFF_TYO_SAKURA", "东京樱花地接社", "日本", "日本"),
        ("CH_OFF_OSK_KANSAI", "大阪关西地接", "日本", "日本"),
        ("CH_OFF_SEL_HAN", "首尔汉江旅行社", "韩国", "韩国"),
        ("CH_OFF_BKK_SMILE", "曼谷微笑地接", "泰国", "泰国"),
        ("CH_OFF_SG_LION", "新加坡狮城商旅", "新加坡", "新加坡"),
        ("CH_OFF_PAR_SEINE", "巴黎塞纳旅行社", "法国", "法国"),
        ("CH_OFF_LON_THAMES", "伦敦泰晤士商旅", "英国", "英国"),
        ("CH_OFF_NYC_APPLE", "纽约苹果假期", "美国", "美国"),
        ("CH_OFF_LA_STAR", "洛杉矶星光旅行社", "美国", "美国"),
        ("CH_OFF_SYD_HARBOR", "悉尼港湾旅行社", "澳大利亚", "澳大利亚"),
        ("CH_OFF_CQ_FOG", "重庆山城假期", "西南", "中国"),
    ]
    for code, name, region, country in offline:
        parties.append(
            {
                "code": code,
                "name": name,
                "name_en": None,
                "role_type": "channel",
                "party_kind": "offline_agency",
                "region_focus": region,
                "country_focus": country,
            }
        )

    # 纯供应商
    fleets = [
        ("SUP_FLEET_BJ", "北京安途车队", "fleet", "华北", "中国", "economy,comfort,luxury"),
        ("SUP_FLEET_SH", "上海申程专车", "fleet", "华东", "中国", "economy,comfort,luxury"),
        ("SUP_FLEET_GZ", "广州南粤车队", "fleet", "华南", "中国", "economy,comfort"),
        ("SUP_FLEET_SZ", "深圳湾专车", "fleet", "华南", "中国", "comfort,luxury"),
        ("SUP_FLEET_CD", "成都天府出行", "fleet", "西南", "中国", "economy,comfort,luxury"),
        ("SUP_FLEET_SY", "三亚椰岛车队", "fleet", "海南", "中国", "economy,comfort,luxury"),
        ("SUP_FLEET_HK", "香港港岛专车", "fleet", "港澳", "中国香港", "comfort,luxury"),
        ("SUP_FLEET_TYO", "东京都会租车", "fleet", "日本", "日本", "economy,comfort,luxury"),
        ("SUP_FLEET_OSK", "大阪关西车队", "fleet", "日本", "日本", "economy,comfort"),
        ("SUP_FLEET_SEL", "首尔汉江专车", "fleet", "韩国", "韩国", "economy,comfort,luxury"),
        ("SUP_FLEET_BKK", "曼谷暹罗车队", "fleet", "泰国", "泰国", "economy,comfort"),
        ("SUP_FLEET_SG", "新加坡滨海专车", "fleet", "新加坡", "新加坡", "comfort,luxury"),
        ("SUP_FLEET_PAR", "巴黎塞纳车队", "fleet", "法国", "法国", "economy,comfort,luxury"),
        ("SUP_FLEET_LON", "伦敦黑色出租车合作方", "fleet", "英国", "英国", "comfort,luxury"),
        ("SUP_FLEET_NYC", "纽约曼哈顿车队", "fleet", "美国", "美国", "economy,comfort,luxury"),
        ("SUP_FLEET_LA", "洛杉矶阳光车队", "fleet", "美国", "美国", "economy,comfort,luxury"),
        ("SUP_FLEET_SYD", "悉尼港湾车队", "fleet", "澳大利亚", "澳大利亚", "economy,comfort"),
        ("SUP_FLEET_GLOBAL_ECO", "环球经济用车联盟", "fleet", "全球", "全球", "economy"),
        ("SUP_FLEET_GLOBAL_LUX", "环球豪华用车联盟", "fleet", "全球", "全球", "luxury"),
        ("SUP_FLEET_CN_ECO", "国内经济拼车网络", "fleet", "中国", "中国", "economy"),
    ]
    for code, name, kind, region, country, tiers in fleets:
        parties.append(
            {
                "code": code,
                "name": name,
                "name_en": None,
                "role_type": "supplier",
                "party_kind": kind,
                "region_focus": region,
                "country_focus": country,
                "_caps": [("car", tiers)],
            }
        )

    hotels = [
        ("SUP_HTL_HUAZHU", "华住供应中心", "hotel", "中国", "中国", "hotel"),
        ("SUP_HTL_JINJIANG", "锦江酒店供应", "hotel", "中国", "中国", "hotel"),
        ("SUP_HTL_ATOUR", "亚朵供应", "hotel", "中国", "中国", "hotel"),
        ("SUP_HTL_MARRIOTT_APAC", "万豪亚太供应", "hotel", "亚太", "新加坡", "hotel"),
        ("SUP_HTL_HILTON_CN", "希尔顿中国供应", "hotel", "中国", "中国", "hotel"),
        ("SUP_HTL_IHG", "洲际酒店集团供应", "hotel", "全球", "英国", "hotel"),
        ("SUP_HTL_ACCOR", "雅高供应", "hotel", "全球", "法国", "hotel"),
        ("SUP_HTL_LOCAL_JP", "日本本地酒店联盟", "hotel", "日本", "日本", "hotel"),
        ("SUP_HTL_LOCAL_KR", "韩国本地酒店联盟", "hotel", "韩国", "韩国", "hotel"),
        ("SUP_HTL_LOCAL_SEA", "东南亚酒店联盟", "hotel", "东南亚", "泰国", "hotel"),
        ("SUP_HTL_LOCAL_EU", "欧洲城市酒店联盟", "hotel", "欧洲", "法国", "hotel"),
        ("SUP_HTL_LOCAL_US", "北美酒店分销", "hotel", "北美", "美国", "hotel"),
        ("SUP_HTL_LOCAL_AU", "澳新酒店供应", "hotel", "大洋洲", "澳大利亚", "hotel"),
        ("SUP_HTL_SANYA", "三亚滨海酒店联合体", "hotel", "海南", "中国", "hotel"),
        ("SUP_HTL_BOUTIQUE", "全球精品酒店集合", "hotel", "全球", "全球", "hotel"),
        ("SUP_HTL_BUDGET_CN", "国内经济型酒店池", "hotel", "中国", "中国", "hotel"),
        ("SUP_HTL_LUX_GLOBAL", "全球奢华酒店供应", "hotel", "全球", "全球", "hotel"),
        ("SUP_HTL_HK_MACAU", "港澳酒店供应", "hotel", "港澳", "中国香港", "hotel"),
    ]
    for code, name, kind, region, country, ptype in hotels:
        parties.append(
            {
                "code": code,
                "name": name,
                "name_en": None,
                "role_type": "supplier",
                "party_kind": kind,
                "region_focus": region,
                "country_focus": country,
                "_caps": [("hotel", None)],
            }
        )

    tickets = [
        ("SUP_TKT_DISNEY_SH", "上海迪士尼票务", "ticket", "华东", "中国", "ticket"),
        ("SUP_TKT_DISNEY_HK", "香港迪士尼票务", "ticket", "港澳", "中国香港", "ticket"),
        ("SUP_TKT_UNIVERSAL_BJ", "北京环球票务", "ticket", "华北", "中国", "ticket"),
        ("SUP_TKT_DISNEY_TYO", "东京迪士尼票务代理", "ticket", "日本", "日本", "ticket"),
        ("SUP_TKT_USJ", "大阪环球票务", "ticket", "日本", "日本", "ticket"),
        ("SUP_TKT_CN_SCENIC", "国内景区联票中心", "ticket", "中国", "中国", "ticket"),
        ("SUP_TKT_SEA", "东南亚景点票务", "ticket", "东南亚", "新加坡", "ticket"),
        ("SUP_TKT_EU_MUSEUM", "欧洲博物馆票务", "ticket", "欧洲", "法国", "ticket"),
        ("SUP_TKT_US_PARK", "北美乐园票务", "ticket", "北美", "美国", "ticket"),
        ("SUP_TKT_AU", "澳新景点票务", "ticket", "大洋洲", "澳大利亚", "ticket"),
        ("SUP_TKT_GLOBAL_PASS", "全球景点通票供应", "ticket", "全球", "全球", "ticket"),
        ("SUP_TKT_KR", "韩国景点票务", "ticket", "韩国", "韩国", "ticket"),
        ("SUP_TKT_LOCAL_GUIDE", "地接票务集合", "ticket", "全球", "全球", "ticket"),
    ]
    for code, name, kind, region, country, _ in tickets:
        parties.append(
            {
                "code": code,
                "name": name,
                "name_en": None,
                "role_type": "supplier",
                "party_kind": kind,
                "region_focus": region,
                "country_focus": country,
                "_caps": [("ticket", None)],
            }
        )

    # 双重角色：既是渠道又是供应商
    hybrids = [
        ("HY_KLOOK_SELF", "Klook自营履约", "hybrid", "亚太", "新加坡", [("ticket", None), ("car", "economy,comfort"), ("hotel", None)]),
        ("HY_AGODA_STAY", "Agoda自营酒店包", "hybrid", "亚太", "泰国", [("hotel", None)]),
        ("HY_TRIP_GROUND", "携程地面服务", "hybrid", "中国", "中国", [("car", "economy,comfort,luxury"), ("ticket", None)]),
        ("HY_TUNIU_PKG", "途牛打包供应", "hybrid", "中国", "中国", [("hotel", None), ("ticket", None)]),
        ("HY_LOCAL_JP", "日本地接双栖", "hybrid", "日本", "日本", [("car", "economy,comfort"), ("ticket", None), ("hotel", None)]),
        ("HY_LOCAL_SEA", "东南亚地接双栖", "hybrid", "东南亚", "泰国", [("car", "economy,comfort"), ("ticket", None)]),
        ("HY_EU_CITY", "欧洲城市通双栖", "hybrid", "欧洲", "法国", [("ticket", None), ("hotel", None)]),
        ("HY_OFF_SH_ORIENT", "上海东方假期自营", "hybrid", "华东", "中国", [("ticket", None), ("car", "economy,comfort"), ("hotel", None)]),
        ("HY_SANYA_COMBO", "三亚一站式", "hybrid", "海南", "中国", [("hotel", None), ("car", "economy,comfort,luxury"), ("ticket", None)]),
        ("HY_HK_HARBOR", "香港维港双栖", "hybrid", "港澳", "中国香港", [("ticket", None), ("hotel", None), ("car", "comfort,luxury")]),
    ]
    for code, name, kind, region, country, caps in hybrids:
        parties.append(
            {
                "code": code,
                "name": name,
                "name_en": None,
                "role_type": "hybrid",
                "party_kind": kind,
                "region_focus": region,
                "country_focus": country,
                "_caps": caps,
            }
        )

    return parties


def build_products(party_id_by_code: dict[str, int]) -> list[dict]:
    products: list[dict] = []

    # 用车：每城 × 三档
    car_supplier_pool = [
        c
        for c, pid in party_id_by_code.items()
        if c.startswith("SUP_FLEET") or c.startswith("HY_")
    ]
    tier_price = {"economy": (90, 220), "comfort": (180, 480), "luxury": (420, 1300)}
    for country, city, region, mult in CITIES:
        for tier, (lo, hi) in tier_price.items():
            base = random.uniform(lo, hi) * mult
            # 选一个主供应
            local = [c for c in car_supplier_pool if city[:2] in c or country[:2] in c]
            code_sup = random.choice(local or car_supplier_pool)
            products.append(
                {
                    "product_code": f"CAR_{city}_{tier}".upper().replace(" ", ""),
                    "product_type": "car",
                    "name": f"{city}{ {'economy':'经济','comfort':'舒适','luxury':'豪华'}[tier] }型用车",
                    "name_en": f"{city} {tier} car",
                    "country": country,
                    "city": city,
                    "region_group": region,
                    "car_tier": tier,
                    "hotel_star": None,
                    "hotel_brand": None,
                    "attraction_name": None,
                    "ticket_category": None,
                    "base_price": float(d(base)),
                    "supplier_id": party_id_by_code[code_sup],
                }
            )

    # 酒店：每城 6-8 家
    hotel_suppliers = [c for c in party_id_by_code if c.startswith("SUP_HTL") or c.startswith("HY_")]
    for country, city, region, mult in CITIES:
        n = random.randint(6, 8)
        for i in range(n):
            star = random.choices([2, 3, 4, 5], weights=[10, 35, 35, 20])[0]
            brand = random.choice(HOTEL_BRANDS)
            base = {2: 180, 3: 320, 4: 680, 5: 1400}[star] * mult * random.uniform(0.85, 1.2)
            sup = random.choice(hotel_suppliers)
            products.append(
                {
                    "product_code": f"HTL_{city}_{i+1:02d}".upper().replace(" ", ""),
                    "product_type": "hotel",
                    "name": f"{city}{brand}{star}星酒店",
                    "name_en": f"{brand} {city}",
                    "country": country,
                    "city": city,
                    "region_group": region,
                    "car_tier": None,
                    "hotel_star": star,
                    "hotel_brand": brand,
                    "attraction_name": None,
                    "ticket_category": None,
                    "base_price": float(d(base)),
                    "supplier_id": party_id_by_code[sup],
                }
            )

    # 门票
    ticket_sup_map = {
        "theme_park": [c for c in party_id_by_code if "DISNEY" in c or "UNIVERSAL" in c or "USJ" in c or c.startswith("SUP_TKT") or c.startswith("HY_")],
        "museum": [c for c in party_id_by_code if c.startswith("SUP_TKT") or c.startswith("HY_")],
        "nature": [c for c in party_id_by_code if c.startswith("SUP_TKT") or c.startswith("HY_")],
        "city_pass": [c for c in party_id_by_code if c.startswith("SUP_TKT") or c.startswith("HY_")],
    }
    for suffix, name, (country, city), cat, price, _high in TICKET_ATTRACTIONS:
        region = next((r for c, ci, r, _m in CITIES if c == country and ci == city), "domestic")
        pool = ticket_sup_map.get(cat) or list(party_id_by_code)
        # 尽量匹配地区
        prefer = [c for c in pool if any(x in c for x in [city[:2], country[:2], "GLOBAL", "CN", "SEA", "EU", "US", "AU", "KR", "JP"])]
        sup = random.choice(prefer or pool)
        code = f"TKT_{suffix}"
        products.append(
            {
                "product_code": code,
                "product_type": "ticket",
                "name": name,
                "name_en": suffix.replace("_", " "),
                "country": country,
                "city": city,
                "region_group": region,
                "car_tier": None,
                "hotel_star": None,
                "hotel_brand": None,
                "attraction_name": name.split("门票")[0].split("一日票")[0].split("两日票")[0],
                "ticket_category": cat,
                "base_price": float(price),
                "supplier_id": party_id_by_code[sup],
            }
        )

    return products


def season_multiplier(day: datetime, product_type: str) -> float:
    m = day.month
    # A2 暑假
    if m in (7, 8):
        if product_type == "ticket":
            return random.uniform(3.0, 4.2)
        if product_type == "hotel":
            return random.uniform(2.4, 3.5)
        return random.uniform(1.6, 2.2)
    # 寒假
    if m in (1, 2):
        if product_type == "ticket":
            return random.uniform(1.6, 2.2)
        if product_type == "hotel":
            return random.uniform(1.4, 1.9)
        return random.uniform(1.1, 1.4)
    # 国庆/春节附近简单抬升
    if m == 10 and day.day <= 7:
        return random.uniform(2.0, 2.8)
    return 1.0


def channel_weight(kind: str) -> float:
    return {
        "mega_ota": 8.0,
        "regional_ota": 3.5,
        "offline_agency": 1.2,
        "hybrid": 2.5,
    }.get(kind, 1.0)


def generate() -> None:
    print("Generating travel B2B mock data...")
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    start_day = (now - timedelta(days=DAYS - 1)).date()

    parties_def = build_parties()

    with engine.connect() as conn:
        ensure_tables(conn)

        for t in (
            "tb_order_quotes",
            "tb_orders",
            "tb_products",
            "tb_party_capabilities",
            "tb_parties",
        ):
            conn.execute(text(f"TRUNCATE TABLE {t}"))
        conn.commit()

        # parties
        party_id_by_code: dict[str, int] = {}
        party_rows: dict[int, dict] = {}
        for p in parties_def:
            conn.execute(
                text(
                    """
                    INSERT INTO tb_parties
                    (code, name, name_en, role_type, party_kind, region_focus, country_focus, workspace_id)
                    VALUES
                    (:code, :name, :name_en, :role_type, :party_kind, :region_focus, :country_focus, :ws)
                    """
                ),
                {
                    "code": p["code"],
                    "name": p["name"],
                    "name_en": p.get("name_en"),
                    "role_type": p["role_type"],
                    "party_kind": p["party_kind"],
                    "region_focus": p.get("region_focus"),
                    "country_focus": p.get("country_focus"),
                    "ws": WORKSPACE,
                },
            )
            pid = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
            party_id_by_code[p["code"]] = int(pid)
            party_rows[int(pid)] = {**p, "id": int(pid)}

            caps = p.get("_caps")
            if not caps:
                # 渠道默认可售全品类
                if p["role_type"] in ("channel", "hybrid"):
                    caps = [("car", "economy,comfort,luxury"), ("hotel", None), ("ticket", None)]
                else:
                    caps = []
            for ptype, tiers in caps:
                conn.execute(
                    text(
                        """
                        INSERT INTO tb_party_capabilities
                        (party_id, product_type, car_tiers, coverage_regions, workspace_id)
                        VALUES (:pid, :ptype, :tiers, :cov, :ws)
                        """
                    ),
                    {
                        "pid": pid,
                        "ptype": ptype,
                        "tiers": tiers,
                        "cov": p.get("region_focus"),
                        "ws": WORKSPACE,
                    },
                )
        conn.commit()
        print(f"  ✓ {len(party_id_by_code)} parties")

        # products
        products = build_products(party_id_by_code)
        product_ids: list[int] = []
        product_rows: dict[int, dict] = {}
        high_cancel_product_id = None
        for pr in products:
            conn.execute(
                text(
                    """
                    INSERT INTO tb_products
                    (product_code, product_type, name, name_en, country, city, region_group,
                     car_tier, hotel_star, hotel_brand, attraction_name, ticket_category,
                     base_price, supplier_id, workspace_id)
                    VALUES
                    (:code, :ptype, :name, :name_en, :country, :city, :region,
                     :car_tier, :hotel_star, :hotel_brand, :attraction, :tcat,
                     :price, :sid, :ws)
                    """
                ),
                {
                    "code": pr["product_code"],
                    "ptype": pr["product_type"],
                    "name": pr["name"],
                    "name_en": pr.get("name_en"),
                    "country": pr["country"],
                    "city": pr["city"],
                    "region": pr["region_group"],
                    "car_tier": pr.get("car_tier"),
                    "hotel_star": pr.get("hotel_star"),
                    "hotel_brand": pr.get("hotel_brand"),
                    "attraction": pr.get("attraction_name"),
                    "tcat": pr.get("ticket_category"),
                    "price": pr["base_price"],
                    "sid": pr.get("supplier_id"),
                    "ws": WORKSPACE,
                },
            )
            pid = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar())
            product_ids.append(pid)
            product_rows[pid] = {**pr, "id": pid}
            if pr["product_code"] == HIGH_CANCEL_TICKET_CODE:
                high_cancel_product_id = pid
        conn.commit()
        print(f"  ✓ {len(product_ids)} products")
        if high_cancel_product_id is None:
            # fallback: find disney sh
            for pid, pr in product_rows.items():
                if "上海迪士尼" in pr["name"] and "一日" in pr["name"]:
                    high_cancel_product_id = pid
                    break

        # 渠道 / 供应商池
        channels = [
            r
            for r in party_rows.values()
            if r["role_type"] in ("channel", "hybrid")
        ]
        suppliers_by_type: dict[str, list[dict]] = {"car": [], "hotel": [], "ticket": []}
        # reload capabilities
        cap_rows = conn.execute(
            text("SELECT party_id, product_type FROM tb_party_capabilities")
        ).fetchall()
        caps_map: dict[int, set[str]] = {}
        for party_id, ptype in cap_rows:
            caps_map.setdefault(int(party_id), set()).add(ptype)
        for r in party_rows.values():
            if r["role_type"] not in ("supplier", "hybrid"):
                continue
            for pt in caps_map.get(r["id"], set()):
                suppliers_by_type.setdefault(pt, []).append(r)

        # 预计算每天目标单量，归一到 TARGET_ORDERS
        day_targets: list[tuple[datetime, int]] = []
        raw_weights = []
        for i in range(DAYS):
            day = datetime.combine(start_day + timedelta(days=i), datetime.min.time()) + timedelta(hours=10)
            # 用混合品类平均季节因子做日权重
            w = (
                season_multiplier(day, "car")
                + season_multiplier(day, "hotel")
                + season_multiplier(day, "ticket")
            ) / 3.0
            if day.weekday() >= 5:
                w *= 1.15
            raw_weights.append(w)
            day_targets.append((day, 0))
        total_w = sum(raw_weights)
        assigned = 0
        for i, w in enumerate(raw_weights):
            n = int(round(TARGET_ORDERS * w / total_w))
            day_targets[i] = (day_targets[i][0], n)
            assigned += n
        # 修正差额
        idx = 0
        while assigned > TARGET_ORDERS:
            d0, n0 = day_targets[idx % DAYS]
            if n0 > 0:
                day_targets[idx % DAYS] = (d0, n0 - 1)
                assigned -= 1
            idx += 1
        while assigned < TARGET_ORDERS:
            d0, n0 = day_targets[idx % DAYS]
            day_targets[idx % DAYS] = (d0, n0 + 1)
            assigned += 1
            idx += 1

        dead_channel_id = party_id_by_code[DEAD_CHANNEL_CODE]
        dead_cutoff = now - timedelta(days=14)

        # 产品按类型分组
        products_by_type = {"car": [], "hotel": [], "ticket": []}
        for pid, pr in product_rows.items():
            products_by_type[pr["product_type"]].append(pid)

        order_count = 0
        quote_count = 0
        batch_orders = []
        batch_quotes = []

        type_weights = [0.40, 0.35, 0.25]  # car / hotel / ticket
        type_names = ["car", "hotel", "ticket"]

        ch_weights = [channel_weight(c["party_kind"]) for c in channels]

        for day, n_orders in day_targets:
            for _ in range(n_orders):
                ptype = random.choices(type_names, weights=type_weights)[0]
                # 暑假再抬门票占比
                if day.month in (7, 8) and random.random() < 0.15:
                    ptype = "ticket"

                pid = random.choice(products_by_type[ptype])
                pr = product_rows[pid]

                # 渠道选择
                ch = random.choices(channels, weights=ch_weights)[0]
                # A1: 近 14 天死渠道几乎不下单
                if ch["id"] == dead_channel_id and day >= dead_cutoff:
                    if random.random() > 0.02:  # 仅 2% 残留
                        # 换成其他渠道
                        ch = random.choices(
                            [c for c in channels if c["id"] != dead_channel_id],
                            weights=[channel_weight(c["party_kind"]) for c in channels if c["id"] != dead_channel_id],
                        )[0]

                # 供应商候选
                sup_pool = suppliers_by_type.get(ptype) or [
                    r for r in party_rows.values() if r["role_type"] in ("supplier", "hybrid")
                ]
                # 地理偏好
                prefer = [
                    s
                    for s in sup_pool
                    if (s.get("country_focus") in (pr["country"], "全球", pr["city"]))
                    or (s.get("region_focus") and s["region_focus"] in (pr["region_group"], pr["country"], "全球", "亚太", "中国"))
                ]
                pool = prefer or sup_pool
                n_quotes = random.choices([2, 3, 4, 5], weights=[15, 45, 30, 10])[0]
                n_quotes = min(n_quotes, len(pool))
                quoted = random.sample(pool, n_quotes)

                base = float(pr["base_price"])
                # 季节溢价
                season = season_multiplier(day, ptype)
                if ptype == "hotel":
                    nights = random.choices([1, 2, 3, 4, 5], weights=[40, 30, 15, 10, 5])[0]
                    qty = 1
                    list_amount = base * nights * random.uniform(0.95, 1.15) * (0.85 + 0.15 * season / 3)
                elif ptype == "ticket":
                    nights = None
                    qty = random.choices([1, 2, 3, 4], weights=[50, 30, 15, 5])[0]
                    list_amount = base * qty * random.uniform(0.95, 1.1)
                else:
                    nights = None
                    qty = 1
                    list_amount = base * random.uniform(0.9, 1.2) * (0.9 + 0.1 * season / 2)
                    if day.weekday() >= 5:
                        list_amount *= 1.1

                # 生成报价
                quote_vals = []
                for s in quoted:
                    noise = random.uniform(0.92, 1.12)
                    # hybrid 自营略贵或略便宜
                    if s["role_type"] == "hybrid":
                        noise *= random.uniform(0.97, 1.05)
                    quote_vals.append((s, list_amount * noise))
                quote_vals.sort(key=lambda x: x[1])
                winner, deal = quote_vals[0]
                # 10% 概率不是最低价中标（商务关系）
                if len(quote_vals) > 1 and random.random() < 0.1:
                    winner, deal = random.choice(quote_vals[: min(3, len(quote_vals))])

                is_self = 1 if winner["id"] == ch["id"] else 0
                # hybrid 渠道有时自成交
                if ch["role_type"] == "hybrid" and random.random() < 0.18:
                    # 若渠道具备该品类能力则自营
                    if ptype in caps_map.get(ch["id"], set()):
                        winner = ch
                        deal = list_amount * random.uniform(0.98, 1.08)
                        is_self = 1
                        # 确保 quotes 含 winner
                        if all(s["id"] != ch["id"] for s, _ in quote_vals):
                            quote_vals.append((ch, deal))
                            quote_vals.sort(key=lambda x: x[1])

                deal_amount = float(d(deal))
                gmv = deal_amount
                commission = float(d(deal_amount * random.uniform(0.06, 0.14)))

                # 状态 / 取消
                cancel_prob = 0.06
                if ptype == "ticket":
                    cancel_prob = 0.08
                if high_cancel_product_id and pid == high_cancel_product_id:
                    cancel_prob = 0.32  # A3
                if day.month in (7, 8) and ptype == "hotel":
                    cancel_prob = min(0.12, cancel_prob + 0.02)

                status_roll = random.random()
                cancel_reason = None
                if status_roll < cancel_prob * 0.65:
                    status = "cancelled"
                    cancel_reason = random.choice(CANCEL_REASONS)
                elif status_roll < cancel_prob:
                    status = "refunded"
                    cancel_reason = random.choice(CANCEL_REASONS)
                elif status_roll < cancel_prob + 0.55:
                    status = "fulfilled"
                else:
                    status = "paid"

                booked_at = day + timedelta(
                    hours=random.randint(0, 12),
                    minutes=random.randint(0, 59),
                    seconds=random.randint(0, 59),
                )
                # service_date：用车当天~+3，酒店 +0~+14，门票 +0~+30
                if ptype == "car":
                    lead = random.randint(0, 3)
                elif ptype == "hotel":
                    lead = random.randint(0, 14)
                else:
                    lead = random.randint(0, 30)
                service_date = (booked_at + timedelta(days=lead)).date()
                paid_at = booked_at + timedelta(minutes=random.randint(1, 90)) if status in ("paid", "fulfilled", "refunded") else None

                order_no = f"TB{booked_at.strftime('%y%m%d')}{uuid.uuid4().hex[:10].upper()}"

                batch_orders.append(
                    {
                        "order_no": order_no,
                        "product_type": ptype,
                        "product_id": pid,
                        "product_name": pr["name"],
                        "channel_id": ch["id"],
                        "channel_name": ch["name"],
                        "channel_kind": ch["party_kind"],
                        "supplier_id": winner["id"],
                        "supplier_name": winner["name"],
                        "is_self_supply": is_self,
                        "country": pr["country"],
                        "city": pr["city"],
                        "region_group": pr["region_group"],
                        "car_tier": pr.get("car_tier"),
                        "hotel_star": pr.get("hotel_star"),
                        "nights": nights,
                        "quantity": qty,
                        "list_amount": float(d(list_amount)),
                        "deal_amount": deal_amount,
                        "gmv": gmv if status in ("paid", "fulfilled", "refunded") else 0.0,
                        "commission_amount": commission if status in ("paid", "fulfilled") else 0.0,
                        "status": status,
                        "cancel_reason": cancel_reason,
                        "quote_count": len(quote_vals),
                        "booked_at": booked_at.strftime("%Y-%m-%d %H:%M:%S"),
                        "service_date": service_date.isoformat(),
                        "paid_at": paid_at.strftime("%Y-%m-%d %H:%M:%S") if paid_at else None,
                        "ws": WORKSPACE,
                        "_quotes": quote_vals,
                        "_winner_id": winner["id"],
                    }
                )
                order_count += 1

                if len(batch_orders) >= 500:
                    quote_count += _flush_orders(conn, batch_orders)
                    batch_orders = []

        if batch_orders:
            quote_count += _flush_orders(conn, batch_orders)

        conn.commit()

        # 校验
        stats = conn.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM tb_parties) AS parties,
                  (SELECT COUNT(*) FROM tb_products) AS products,
                  (SELECT COUNT(*) FROM tb_orders) AS orders,
                  (SELECT COUNT(*) FROM tb_order_quotes) AS quotes,
                  (SELECT ROUND(SUM(gmv),2) FROM tb_orders WHERE status IN ('paid','fulfilled')) AS gmv
                """
            )
        ).mappings().first()

        # 异常抽样打印
        dead_recent = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM tb_orders
                WHERE channel_id = :cid AND booked_at >= :cut
                """
            ),
            {"cid": dead_channel_id, "cut": dead_cutoff.strftime("%Y-%m-%d %H:%M:%S")},
        ).scalar()
        dead_old = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM tb_orders
                WHERE channel_id = :cid AND booked_at < :cut
                """
            ),
            {"cid": dead_channel_id, "cut": dead_cutoff.strftime("%Y-%m-%d %H:%M:%S")},
        ).scalar()
        disney_cancel = conn.execute(
            text(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(status IN ('cancelled','refunded')) AS cancels
                FROM tb_orders
                WHERE product_id = :pid
                """
            ),
            {"pid": high_cancel_product_id},
        ).mappings().first()
        summer = conn.execute(
            text(
                """
                SELECT
                  SUM(MONTH(booked_at) IN (7,8)) AS summer_orders,
                  SUM(MONTH(booked_at) IN (3,4)) AS spring_orders
                FROM tb_orders
                """
            )
        ).mappings().first()

    print(f"  ✓ {stats['orders']} orders")
    print(f"  ✓ {stats['quotes']} quotes")
    print(f"  ✓ GMV(paid/fulfilled) = {stats['gmv']}")
    print("Anomaly checks:")
    print(f"  A1 dead channel recent14d={dead_recent}, before={dead_old}")
    if disney_cancel:
        cancels = int(disney_cancel["cancels"] or 0)
        total = int(disney_cancel["total"] or 0)
        rate = cancels * 100.0 / max(total, 1)
        print(f"  A3 disney cancel {cancels}/{total} = {rate:.1f}%")
    if summer:
        print(
            f"  A2 summer_orders={int(summer['summer_orders'] or 0)} "
            f"vs spring(3-4)={int(summer['spring_orders'] or 0)}"
        )
    print("Done!")


def _flush_orders(conn, batch_orders: list[dict]) -> int:
    qcount = 0
    for o in batch_orders:
        quotes = o.pop("_quotes")
        winner_id = o.pop("_winner_id")
        conn.execute(
            text(
                """
                INSERT INTO tb_orders (
                  order_no, product_type, product_id, product_name,
                  channel_id, channel_name, channel_kind,
                  supplier_id, supplier_name, is_self_supply,
                  country, city, region_group, car_tier, hotel_star,
                  nights, quantity, list_amount, deal_amount, gmv, commission_amount,
                  status, cancel_reason, quote_count, booked_at, service_date, paid_at, workspace_id
                ) VALUES (
                  :order_no, :product_type, :product_id, :product_name,
                  :channel_id, :channel_name, :channel_kind,
                  :supplier_id, :supplier_name, :is_self_supply,
                  :country, :city, :region_group, :car_tier, :hotel_star,
                  :nights, :quantity, :list_amount, :deal_amount, :gmv, :commission_amount,
                  :status, :cancel_reason, :quote_count, :booked_at, :service_date, :paid_at, :ws
                )
                """
            ),
            o,
        )
        oid = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar())
        # ranks
        ranked = sorted(quotes, key=lambda x: x[1])
        for rank, (sup, amt) in enumerate(ranked, 1):
            conn.execute(
                text(
                    """
                    INSERT INTO tb_order_quotes
                    (order_id, order_no, supplier_id, supplier_name, quote_amount, is_winner, rank_no, workspace_id, created_at)
                    VALUES
                    (:oid, :ono, :sid, :sname, :amt, :win, :rank, :ws, :ts)
                    """
                ),
                {
                    "oid": oid,
                    "ono": o["order_no"],
                    "sid": sup["id"],
                    "sname": sup["name"],
                    "amt": float(d(amt)),
                    "win": 1 if sup["id"] == winner_id else 0,
                    "rank": rank,
                    "ws": WORKSPACE,
                    "ts": o["booked_at"],
                },
            )
            qcount += 1
    conn.commit()
    return qcount


if __name__ == "__main__":
    generate()
