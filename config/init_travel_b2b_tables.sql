-- DataPilot B2B 出行分销 Mock 表（用车 / 酒店 / 门票）
-- 与 ecom_* / 技术质量表共用 datacheck 库，前缀 tb_

USE datacheck;

-- 统一主体：渠道 / 供应商 / 双重角色
CREATE TABLE IF NOT EXISTS tb_parties (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(64) NOT NULL UNIQUE COMMENT '主体编码',
    name VARCHAR(128) NOT NULL COMMENT '主体名称',
    name_en VARCHAR(128) DEFAULT NULL COMMENT '英文名',
    role_type ENUM('channel', 'supplier', 'hybrid') NOT NULL COMMENT '角色: 渠道/供应商/双重',
    party_kind VARCHAR(32) NOT NULL COMMENT '细分: mega_ota/regional_ota/offline_agency/fleet/hotel/ticket/hybrid',
    region_focus VARCHAR(64) DEFAULT NULL COMMENT '主经营区域',
    country_focus VARCHAR(64) DEFAULT NULL COMMENT '主经营国家',
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_role (role_type),
    INDEX idx_kind (party_kind),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='B2B出行-渠道与供应商主体';

-- 主体可经营品类
CREATE TABLE IF NOT EXISTS tb_party_capabilities (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    party_id BIGINT NOT NULL,
    product_type ENUM('car', 'hotel', 'ticket') NOT NULL COMMENT '品类',
    car_tiers VARCHAR(64) DEFAULT NULL COMMENT '用车档位,逗号分隔 economy/comfort/luxury',
    coverage_regions VARCHAR(255) DEFAULT NULL COMMENT '覆盖区域摘要',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_party_type (party_id, product_type),
    INDEX idx_type (product_type),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='B2B出行-主体品类能力';

-- 可售产品（酒店物业/门票/用车价目）
CREATE TABLE IF NOT EXISTS tb_products (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    product_code VARCHAR(64) NOT NULL UNIQUE COMMENT '产品编码',
    product_type ENUM('car', 'hotel', 'ticket') NOT NULL,
    name VARCHAR(160) NOT NULL COMMENT '产品名称',
    name_en VARCHAR(160) DEFAULT NULL,
    country VARCHAR(64) NOT NULL COMMENT '国家/地区',
    city VARCHAR(64) NOT NULL COMMENT '城市',
    region_group VARCHAR(32) NOT NULL COMMENT 'domestic/asia/europe/americas/oceania',
    -- 用车
    car_tier ENUM('economy', 'comfort', 'luxury') DEFAULT NULL COMMENT '用车档位',
    -- 酒店
    hotel_star TINYINT DEFAULT NULL COMMENT '酒店星级 2-5',
    hotel_brand VARCHAR(64) DEFAULT NULL COMMENT '酒店品牌/集团',
    -- 门票
    attraction_name VARCHAR(128) DEFAULT NULL COMMENT '景点名称',
    ticket_category VARCHAR(64) DEFAULT NULL COMMENT '门票类型: theme_park/museum/nature/city_pass',
    base_price DECIMAL(12, 2) NOT NULL COMMENT '基准价(人民币)',
    currency VARCHAR(8) NOT NULL DEFAULT 'CNY',
    status ENUM('on_sale', 'off_sale') NOT NULL DEFAULT 'on_sale',
    supplier_id BIGINT DEFAULT NULL COMMENT '主供应方 party_id',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_type (product_type),
    INDEX idx_geo (country, city),
    INDEX idx_region (region_group),
    INDEX idx_car_tier (car_tier),
    INDEX idx_attraction (attraction_name),
    INDEX idx_supplier (supplier_id),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='B2B出行-可售产品';

-- 订单事实表
CREATE TABLE IF NOT EXISTS tb_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_no VARCHAR(64) NOT NULL UNIQUE COMMENT '平台订单号',
    product_type ENUM('car', 'hotel', 'ticket') NOT NULL COMMENT '品类',
    product_id BIGINT NOT NULL,
    product_name VARCHAR(160) NOT NULL COMMENT '产品名称快照',
    channel_id BIGINT NOT NULL COMMENT '下单渠道 party_id',
    channel_name VARCHAR(128) NOT NULL COMMENT '渠道名称快照',
    channel_kind VARCHAR(32) NOT NULL COMMENT '渠道细分快照',
    supplier_id BIGINT NOT NULL COMMENT '成交供应商 party_id',
    supplier_name VARCHAR(128) NOT NULL COMMENT '供应商名称快照',
    is_self_supply TINYINT(1) NOT NULL DEFAULT 0 COMMENT '渠道=供应商(自营/双重角色成交)',
    country VARCHAR(64) NOT NULL,
    city VARCHAR(64) NOT NULL,
    region_group VARCHAR(32) NOT NULL COMMENT 'domestic/asia/europe/americas/oceania',
    car_tier ENUM('economy', 'comfort', 'luxury') DEFAULT NULL,
    hotel_star TINYINT DEFAULT NULL,
    nights INT DEFAULT NULL COMMENT '酒店间夜数',
    quantity INT NOT NULL DEFAULT 1 COMMENT '数量(票数/用车次数/间数)',
    list_amount DECIMAL(12, 2) NOT NULL COMMENT '挂牌金额',
    deal_amount DECIMAL(12, 2) NOT NULL COMMENT '成交金额',
    gmv DECIMAL(12, 2) NOT NULL COMMENT 'GMV(口径=成交金额)',
    commission_amount DECIMAL(12, 2) NOT NULL DEFAULT 0 COMMENT '平台佣金',
    currency VARCHAR(8) NOT NULL DEFAULT 'CNY',
    status ENUM('quoted', 'paid', 'fulfilled', 'cancelled', 'refunded') NOT NULL DEFAULT 'paid',
    cancel_reason VARCHAR(64) DEFAULT NULL COMMENT '取消/退款原因',
    quote_count INT NOT NULL DEFAULT 1 COMMENT '比价供应商数',
    booked_at DATETIME NOT NULL COMMENT '下单时间',
    service_date DATE NOT NULL COMMENT '出行/入住/用车日期',
    paid_at DATETIME DEFAULT NULL,
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_product_type (product_type),
    INDEX idx_channel (channel_id),
    INDEX idx_supplier (supplier_id),
    INDEX idx_status (status),
    INDEX idx_booked (booked_at),
    INDEX idx_service (service_date),
    INDEX idx_geo (country, city),
    INDEX idx_region (region_group),
    INDEX idx_car_tier (car_tier),
    INDEX idx_self_supply (is_self_supply),
    INDEX idx_product (product_id),
    INDEX idx_workspace (workspace_id),
    INDEX idx_channel_booked (channel_id, booked_at),
    INDEX idx_type_service (product_type, service_date)
) ENGINE=InnoDB COMMENT='B2B出行-订单';

-- 比价报价明细
CREATE TABLE IF NOT EXISTS tb_order_quotes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    order_no VARCHAR(64) NOT NULL,
    supplier_id BIGINT NOT NULL,
    supplier_name VARCHAR(128) NOT NULL,
    quote_amount DECIMAL(12, 2) NOT NULL COMMENT '报价金额',
    is_winner TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否中标成交',
    rank_no INT NOT NULL DEFAULT 1 COMMENT '报价从低到高排名',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_order (order_id),
    INDEX idx_order_no (order_no),
    INDEX idx_supplier (supplier_id),
    INDEX idx_winner (is_winner),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='B2B出行-订单比价明细';
