-- DataPilot 电商业务表

USE datacheck;

-- 用户表
CREATE TABLE IF NOT EXISTS ecom_users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    nickname VARCHAR(64) NOT NULL COMMENT '昵称',
    phone VARCHAR(20) DEFAULT NULL COMMENT '手机号',
    gender ENUM('male', 'female', 'unknown') DEFAULT 'unknown',
    age_group ENUM('18-24', '25-34', '35-44', '45+') DEFAULT '25-34',
    register_channel VARCHAR(32) DEFAULT 'organic' COMMENT '注册渠道: organic, paid_search, social, referral',
    first_order_at DATETIME DEFAULT NULL COMMENT '首次下单时间',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_channel (register_channel),
    INDEX idx_created (created_at),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='电商用户';

-- 商品表
CREATE TABLE IF NOT EXISTS ecom_products (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(128) NOT NULL COMMENT '商品名称',
    category VARCHAR(64) NOT NULL COMMENT '分类: 手机, 电脑, 配件, 家居, 服饰, 食品',
    brand VARCHAR(64) DEFAULT NULL COMMENT '品牌',
    price DECIMAL(10, 2) NOT NULL COMMENT '售价',
    cost_price DECIMAL(10, 2) DEFAULT NULL COMMENT '成本价',
    status ENUM('on_sale', 'off_sale') DEFAULT 'on_sale',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_category (category),
    INDEX idx_brand (brand),
    INDEX idx_status (status),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='商品';

-- 订单表
CREATE TABLE IF NOT EXISTS ecom_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_no VARCHAR(64) NOT NULL UNIQUE COMMENT '订单号',
    user_id BIGINT NOT NULL,
    status ENUM('pending', 'paid', 'shipped', 'completed', 'cancelled', 'refunded') NOT NULL DEFAULT 'pending',
    total_amount DECIMAL(12, 2) NOT NULL COMMENT '订单总额',
    pay_amount DECIMAL(12, 2) DEFAULT NULL COMMENT '实付金额',
    discount_amount DECIMAL(12, 2) DEFAULT 0 COMMENT '优惠金额',
    pay_channel VARCHAR(32) DEFAULT NULL COMMENT '支付渠道: alipay, wechat, card',
    pay_time DATETIME DEFAULT NULL COMMENT '支付时间',
    source_channel VARCHAR(32) DEFAULT 'direct' COMMENT '流量渠道: direct, search, social, ads, email',
    is_promotion TINYINT(1) DEFAULT 0 COMMENT '是否大促订单',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_status (status),
    INDEX idx_pay_channel (pay_channel),
    INDEX idx_source_channel (source_channel),
    INDEX idx_created (created_at),
    INDEX idx_workspace (workspace_id),
    INDEX idx_is_promotion (is_promotion)
) ENGINE=InnoDB COMMENT='订单';

-- 订单明细表
CREATE TABLE IF NOT EXISTS ecom_order_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    product_name VARCHAR(128) NOT NULL COMMENT '商品名称快照',
    category VARCHAR(64) NOT NULL COMMENT '分类快照',
    price DECIMAL(10, 2) NOT NULL COMMENT '单价快照',
    quantity INT NOT NULL DEFAULT 1,
    subtotal DECIMAL(12, 2) NOT NULL COMMENT '小计',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_order (order_id),
    INDEX idx_product (product_id),
    INDEX idx_category (category),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='订单明细';

-- 流量事件表
CREATE TABLE IF NOT EXISTS ecom_traffic_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT DEFAULT NULL,
    event_type ENUM('page_view', 'add_to_cart', 'checkout', 'pay_click') NOT NULL,
    page_url VARCHAR(255) DEFAULT NULL,
    source_channel VARCHAR(32) DEFAULT 'direct' COMMENT '流量渠道',
    device_type ENUM('ios', 'android', 'pc', 'h5') DEFAULT 'pc',
    utm_source VARCHAR(64) DEFAULT NULL,
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_event_type (event_type),
    INDEX idx_source_channel (source_channel),
    INDEX idx_created (created_at),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='流量事件';

-- 退款表
CREATE TABLE IF NOT EXISTS ecom_refunds (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    order_item_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    refund_amount DECIMAL(12, 2) NOT NULL,
    reason VARCHAR(128) DEFAULT NULL COMMENT '退款原因: 质量问题, 不喜欢, 发货慢, 商品损坏, 其他',
    status ENUM('pending', 'approved', 'rejected', 'completed') DEFAULT 'pending',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_order (order_id),
    INDEX idx_product (product_id),
    INDEX idx_reason (reason),
    INDEX idx_created (created_at),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='退款';
