"""Governed per-space vocabulary used by the semantic query parser.

The model is intentionally data-only: it names approved business objects, while
the physical catalog remains the authority for table/column existence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.agents.semantic_catalog import SemanticCatalog


@dataclass(frozen=True)
class SemanticFilter:
    field: str
    op: str = "="
    value: Any = None


@dataclass(frozen=True)
class SemanticEntity:
    id: str
    table: str
    grain: str
    time_field: str = ""
    description: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticDimension:
    id: str
    entity: str
    field: str
    description: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticMetric:
    id: str
    entity: str
    aggregation: str
    field: str = ""
    default_filters: tuple[SemanticFilter, ...] = ()
    allowed_dimensions: tuple[str, ...] = ()
    description: str = ""
    aliases: tuple[str, ...] = ()
    status: str = "confirmed"


@dataclass(frozen=True)
class SemanticModel:
    space_id: str
    version: str
    schema_fingerprint: str
    entities: tuple[SemanticEntity, ...]
    dimensions: tuple[SemanticDimension, ...]
    metrics: tuple[SemanticMetric, ...]
    provenance: str = "catalog_inferred"
    status: str = "published"

    def entity(self, entity_id: str) -> SemanticEntity | None:
        return next((x for x in self.entities if x.id == entity_id), None)

    def metric(self, metric_id: str) -> SemanticMetric | None:
        return next((x for x in self.metrics if x.id == metric_id), None)

    def dimension(self, dimension_id: str) -> SemanticDimension | None:
        return next((x for x in self.dimensions if x.id == dimension_id), None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def prompt_summary(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "entities": [asdict(x) for x in self.entities],
            "metrics": [
                {
                    "id": x.id, "entity": x.entity, "aggregation": x.aggregation,
                    "field": x.field, "allowed_dimensions": list(x.allowed_dimensions),
                    "description": x.description, "aliases": list(x.aliases),
                }
                for x in self.metrics
            ],
            "dimensions": [asdict(x) for x in self.dimensions],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SemanticModel":
        return cls(
            space_id=str(raw["space_id"]), version=str(raw["version"]),
            schema_fingerprint=str(raw.get("schema_fingerprint") or ""),
            entities=tuple(SemanticEntity(**x) for x in raw.get("entities") or []),
            dimensions=tuple(SemanticDimension(**x) for x in raw.get("dimensions") or []),
            metrics=tuple(
                SemanticMetric(
                    **{
                        **x,
                        "default_filters": tuple(SemanticFilter(**f) for f in x.get("default_filters") or []),
                        "allowed_dimensions": tuple(x.get("allowed_dimensions") or []),
                        "aliases": tuple(x.get("aliases") or []),
                    }
                )
                for x in raw.get("metrics") or []
            ),
            provenance=str(raw.get("provenance") or "catalog_inferred"),
            status=str(raw.get("status") or "published"),
        )


def build_seed_semantic_model(space_id: str, catalog: SemanticCatalog) -> SemanticModel:
    """Create a safe draft from catalog; ship an explicit B2B seed for this space."""
    names = set(catalog.table_map)
    if space_id == "travel_b2b" and "tb_orders" in names:
        dims = (
            SemanticDimension("product_type", "order", "product_type", "用车、酒店、门票品类", ("品类",)),
            SemanticDimension("product_name", "order", "product_name", "产品名称", ("产品",)),
            SemanticDimension("channel_name", "order", "channel_name", "下单渠道名称", ("渠道",)),
            SemanticDimension("supplier_name", "order", "supplier_name", "成交供应商名称", ("供应商",)),
            SemanticDimension("region_group", "order", "region_group", "区域分组", ("地区", "区域")),
            SemanticDimension("country", "order", "country", "国家", ()),
            SemanticDimension("city", "order", "city", "城市", ("城市",)),
            SemanticDimension("status", "order", "status", "订单状态", ("状态",)),
            SemanticDimension("car_tier", "order", "car_tier", "车型档次", ()),
        )
        dim_ids = tuple(d.id for d in dims)
        return SemanticModel(
            space_id=space_id, version="seed-travel-b2b-v1", schema_fingerprint=catalog.schema_fingerprint,
            entities=(SemanticEntity("order", "tb_orders", "一行一订单", "booked_at", "B2B出行订单", ("订单",)),),
            dimensions=dims,
            metrics=(
                SemanticMetric("gmv", "order", "sum", "gmv", (SemanticFilter("status", "in", ["paid", "fulfilled"]),), dim_ids, "已支付或履约订单的GMV", ("成交额", "交易额")),
                SemanticMetric("order_count", "order", "count", "id", (), dim_ids, "订单数量", ("订单数", "订单量")),
                SemanticMetric("cancel_rate", "order", "rate", "id", (SemanticFilter("status", "in", ["cancelled", "refunded"]),), dim_ids, "取消或退款订单占全部订单的比例", ("取消率", "退款率")),
            ),
            provenance="seeded_from_business_context",
        )
    entities, dimensions, metrics = [], [], []
    for table in catalog.tables:
        entity_id = table.name.removeprefix("tb_").removesuffix("s") or table.name
        cols = {c.name for c in table.columns}
        time_field = next((x for x in ("created_at", "updated_at", "date", "time") if x in cols), "")
        entities.append(SemanticEntity(entity_id, table.name, f"一行一{entity_id}", time_field, table.comment))
        for col in table.columns:
            if col.role in {"enum_dimension", "status"}:
                dimensions.append(SemanticDimension(f"{entity_id}.{col.name}", entity_id, col.name, col.comment))
            if col.role in {"amount", "continuous_numeric"}:
                metrics.append(SemanticMetric(f"{entity_id}.{col.name}_sum", entity_id, "sum", col.name, description=col.comment, status="ai_inferred"))
    return SemanticModel(space_id, "catalog-draft-v1", catalog.schema_fingerprint, tuple(entities), tuple(dimensions), tuple(metrics), status="draft")
