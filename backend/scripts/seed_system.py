"""Seed 脚本 - 初始化系统数据

- 创建默认用户（密码明文，生产环境用 bcrypt）
- 创建分析空间
- 从 YAML 导入指标模板到数据库
"""

import os
import sys
import json
import hashlib
import uuid
from datetime import datetime

import yaml
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.database import engine


def generate_id() -> str:
    return uuid.uuid4().hex[:16]


def seed_users():
    """创建默认用户"""
    users = [
        {"username": "admin", "display_name": "管理员", "role": "admin"},
        {"username": "tester", "display_name": "测试人员", "role": "tester"},
        {"username": "developer", "display_name": "开发人员", "role": "developer"},
        {"username": "product", "display_name": "产品经理", "role": "product"},
        {"username": "operator", "display_name": "运营人员", "role": "operator"},
    ]
    # 简单 hash（生产用 bcrypt）
    default_password = hashlib.sha256("datapilot123".encode()).hexdigest()

    with engine.connect() as conn:
        for u in users:
            conn.execute(text("""
                INSERT IGNORE INTO auth_users (username, password_hash, display_name, role)
                VALUES (:username, :password, :display_name, :role)
            """), {
                "username": u["username"],
                "password": default_password,
                "display_name": u["display_name"],
                "role": u["role"],
            })
        conn.commit()
    print(f"  ✓ {len(users)} users seeded")


def seed_spaces():
    """创建分析空间"""
    spaces = [
        {
            "id": "tech_quality",
            "name": "技术质量分析",
            "description": "扫描成功率、API 性能、错误分布等技术质量指标",
            "icon": "bug",
            "dataset_id": "tech_quality",
            "sort_order": 1,
        },
        {
            "id": "ecommerce",
            "name": "电商经营分析",
            "description": "销售额、订单数、转化率等电商经营指标",
            "icon": "shopping",
            "dataset_id": "ecommerce",
            "sort_order": 2,
        },
    ]

    with engine.connect() as conn:
        for s in spaces:
            conn.execute(text("""
                INSERT IGNORE INTO analysis_spaces (id, name, description, icon, dataset_id, sort_order)
                VALUES (:id, :name, :description, :icon, :dataset_id, :sort_order)
            """), s)
        conn.commit()
    print(f"  ✓ {len(spaces)} spaces seeded")


def seed_metrics():
    """从 YAML 文件导入指标模板到数据库"""
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    seeds_dir = os.path.join(base, "config", "metric_seeds")

    seed_files = {
        "tech_quality": os.path.join(base, "config", "metrics.yml"),
        "ecommerce": os.path.join(seeds_dir, "ecommerce.yml"),
    }

    total = 0
    with engine.connect() as conn:
        for space_id, path in seed_files.items():
            if not os.path.exists(path):
                print(f"  ⚠ {path} not found, skipping")
                continue

            with open(path) as f:
                metrics_config = yaml.safe_load(f)

            count = 0
            for i, (key, config) in enumerate(metrics_config.items(), 1):
                conn.execute(text("""
                    INSERT INTO metric_templates (space_id, metric_key, name, description, config, sort_order)
                    VALUES (:space_id, :metric_key, :name, :description, :config, :sort_order)
                    ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description), config=VALUES(config)
                """), {
                    "space_id": space_id,
                    "metric_key": key,
                    "name": config["name"],
                    "description": config["description"],
                    "config": json.dumps(config, ensure_ascii=False),
                    "sort_order": i,
                })
                count += 1
            conn.commit()
            print(f"  ✓ {count} metrics seeded into {space_id} space")
            total += count
    print(f"  ✓ Total: {total} metrics")


def main():
    print("Seeding DataPilot system data...")
    seed_users()
    seed_spaces()
    seed_metrics()
    print("Done!")


if __name__ == "__main__":
    main()
