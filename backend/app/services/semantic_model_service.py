"""Read and publish semantic models in the DataPilot system DB only."""

from __future__ import annotations

from app.agents.semantic_catalog import SemanticCatalog
import json
from datetime import datetime

from sqlalchemy import text

from app.agents.semantic_model import SemanticModel, build_seed_semantic_model


class SemanticModelService:
    def load_or_build(self, space_id: str, catalog: SemanticCatalog) -> SemanticModel:
        published = self.load_published(space_id, schema_fingerprint=catalog.schema_fingerprint)
        if published is not None:
            return published
        model = build_seed_semantic_model(space_id, catalog)
        try:
            self._upsert_rows(model)
        except Exception:
            pass
        return model

    def load_published(self, space_id: str, *, schema_fingerprint: str = "") -> SemanticModel | None:
        from app.core.database import engine

        version = ""
        raw_json = None
        try:
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT v.model_json, v.version, v.schema_fingerprint
                    FROM space_semantic_models p
                    JOIN semantic_model_versions v ON v.id = p.published_version_id
                    WHERE p.space_id = :space_id AND v.status = 'published'
                """), {"space_id": space_id}).fetchone()
                if row:
                    raw_json, version = row[0], str(row[1] or "")
                    if schema_fingerprint and row[2] and str(row[2]) != schema_fingerprint:
                        return None
                from_rows = self._load_rows(conn, space_id, version)
                if from_rows is not None:
                    if schema_fingerprint and from_rows.schema_fingerprint and from_rows.schema_fingerprint != schema_fingerprint:
                        return None
                    return from_rows
        except Exception:
            return None
        if raw_json is None:
            return None
        try:
            payload = json.loads(raw_json) if isinstance(raw_json, str) else dict(raw_json)
            model = SemanticModel.from_dict(payload)
        except Exception:
            return None
        if schema_fingerprint and model.schema_fingerprint and model.schema_fingerprint != schema_fingerprint:
            return None
        return model

    def publish(self, model: SemanticModel, *, summary_md: str = "") -> int:
        """Publish an immutable version after the caller completed model validation."""
        from app.core.database import engine

        payload = json.dumps(model.to_dict(), ensure_ascii=False)
        summary = summary_md or self._summary(model)
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO semantic_model_versions
                    (space_id, version, schema_fingerprint, model_json, summary_md, provenance, status)
                VALUES (:space_id, :version, :schema_fingerprint, CAST(:model_json AS JSON), :summary_md, :provenance, 'published')
                ON DUPLICATE KEY UPDATE
                    model_json=VALUES(model_json), summary_md=VALUES(summary_md), provenance=VALUES(provenance), status='published'
            """), {
                "space_id": model.space_id, "version": model.version,
                "schema_fingerprint": model.schema_fingerprint, "model_json": payload,
                "summary_md": summary, "provenance": model.provenance,
            })
            version_id = conn.execute(text("""
                SELECT id FROM semantic_model_versions
                WHERE space_id=:space_id AND version=:version
            """), {"space_id": model.space_id, "version": model.version}).scalar_one()
            conn.execute(text("""
                INSERT INTO space_semantic_models (space_id, published_version_id)
                VALUES (:space_id, :version_id)
                ON DUPLICATE KEY UPDATE published_version_id=VALUES(published_version_id)
            """), {"space_id": model.space_id, "version_id": version_id})
            self._upsert_rows(model, conn=conn)
            conn.commit()
        return int(version_id)

    def _upsert_rows(self, model: SemanticModel, conn=None) -> None:
        from app.core.database import engine

        if conn is not None:
            self._write_rows(conn, model)
            return
        with engine.connect() as owned:
            self._write_rows(owned, model)
            owned.commit()

    def _write_rows(self, conn, model: SemanticModel) -> None:
        params = {"space_id": model.space_id, "version": model.version}
        conn.execute(text("DELETE FROM semantic_entities WHERE space_id=:space_id AND version=:version"), params)
        conn.execute(text("DELETE FROM semantic_metrics WHERE space_id=:space_id AND version=:version"), params)
        conn.execute(text("DELETE FROM semantic_dimensions WHERE space_id=:space_id AND version=:version"), params)
        for entity in model.entities:
            conn.execute(text("""
                INSERT INTO semantic_entities
                  (space_id, version, entity_id, table_name, grain, time_field, description, aliases_json)
                VALUES (:space_id, :version, :entity_id, :table_name, :grain, :time_field, :description, :aliases_json)
            """), {
                "space_id": model.space_id, "version": model.version, "entity_id": entity.id,
                "table_name": entity.table, "grain": entity.grain, "time_field": entity.time_field,
                "description": entity.description,
                "aliases_json": json.dumps(list(entity.aliases), ensure_ascii=False),
            })
        for metric in model.metrics:
            conn.execute(text("""
                INSERT INTO semantic_metrics
                  (space_id, version, metric_id, entity_id, aggregation, field_name, description,
                   aliases_json, allowed_dimensions_json, default_filters_json, status)
                VALUES (:space_id, :version, :metric_id, :entity_id, :aggregation, :field_name, :description,
                        :aliases_json, :allowed_dimensions_json, :default_filters_json, :status)
            """), {
                "space_id": model.space_id, "version": model.version, "metric_id": metric.id,
                "entity_id": metric.entity, "aggregation": metric.aggregation, "field_name": metric.field,
                "description": metric.description,
                "aliases_json": json.dumps(list(metric.aliases), ensure_ascii=False),
                "allowed_dimensions_json": json.dumps(list(metric.allowed_dimensions), ensure_ascii=False),
                "default_filters_json": json.dumps(
                    [{"field": x.field, "op": x.op, "value": x.value} for x in metric.default_filters],
                    ensure_ascii=False,
                ),
                "status": metric.status,
            })
        for dimension in model.dimensions:
            conn.execute(text("""
                INSERT INTO semantic_dimensions
                  (space_id, version, dimension_id, entity_id, field_name, description, aliases_json)
                VALUES (:space_id, :version, :dimension_id, :entity_id, :field_name, :description, :aliases_json)
            """), {
                "space_id": model.space_id, "version": model.version, "dimension_id": dimension.id,
                "entity_id": dimension.entity, "field_name": dimension.field,
                "description": dimension.description,
                "aliases_json": json.dumps(list(dimension.aliases), ensure_ascii=False),
            })

    def _load_rows(self, conn, space_id: str, version: str) -> SemanticModel | None:
        from app.agents.semantic_model import SemanticDimension, SemanticEntity, SemanticFilter, SemanticMetric

        version_clause = "AND version = :version" if version else ""
        params = {"space_id": space_id, "version": version}
        if not version:
            row = conn.execute(text(
                "SELECT version FROM semantic_entities WHERE space_id=:space_id ORDER BY version DESC LIMIT 1"
            ), {"space_id": space_id}).fetchone()
            if not row:
                return None
            version = str(row[0])
            params["version"] = version
            version_clause = "AND version = :version"
        entities = []
        for row in conn.execute(text(
            f"SELECT entity_id, table_name, grain, time_field, description, aliases_json "
            f"FROM semantic_entities WHERE space_id=:space_id {version_clause}"
        ), params).fetchall():
            aliases = row[5]
            if isinstance(aliases, str):
                aliases = json.loads(aliases)
            entities.append(SemanticEntity(
                str(row[0]), str(row[1]), str(row[2] or ""), str(row[3] or ""), str(row[4] or ""),
                tuple(aliases or ()),
            ))
        dimensions = []
        for row in conn.execute(text(
            f"SELECT dimension_id, entity_id, field_name, description, aliases_json "
            f"FROM semantic_dimensions WHERE space_id=:space_id {version_clause}"
        ), params).fetchall():
            aliases = row[4]
            if isinstance(aliases, str):
                aliases = json.loads(aliases)
            dimensions.append(SemanticDimension(
                str(row[0]), str(row[1]), str(row[2]), str(row[3] or ""), tuple(aliases or ()),
            ))
        metrics = []
        for row in conn.execute(text(
            f"SELECT metric_id, entity_id, aggregation, field_name, description, aliases_json, "
            f"allowed_dimensions_json, default_filters_json, status "
            f"FROM semantic_metrics WHERE space_id=:space_id {version_clause}"
        ), params).fetchall():
            aliases = row[5]
            allowed = row[6]
            filters = row[7]
            if isinstance(aliases, str):
                aliases = json.loads(aliases)
            if isinstance(allowed, str):
                allowed = json.loads(allowed)
            if isinstance(filters, str):
                filters = json.loads(filters)
            metrics.append(SemanticMetric(
                str(row[0]), str(row[1]), str(row[2]), str(row[3] or ""),
                tuple(SemanticFilter(str(x.get("field") or ""), str(x.get("op") or "="), x.get("value")) for x in (filters or []) if isinstance(x, dict)),
                tuple(allowed or ()),
                str(row[4] or ""),
                tuple(aliases or ()),
                str(row[8] or "confirmed"),
            ))
        if not entities or not metrics:
            return None
        fingerprint = ""
        try:
            fp_row = conn.execute(text(
                "SELECT schema_fingerprint FROM semantic_model_versions WHERE space_id=:space_id AND version=:version"
            ), params).fetchone()
            if fp_row:
                fingerprint = str(fp_row[0] or "")
        except Exception:
            fingerprint = ""
        return SemanticModel(
            space_id=space_id, version=version, schema_fingerprint=fingerprint,
            entities=tuple(entities), dimensions=tuple(dimensions), metrics=tuple(metrics),
            provenance="row_store", status="published",
        )

    def unpublished_metric_ids(self, space_id: str, metric_ids: list[str]) -> list[str]:
        wanted = [str(item) for item in metric_ids if item]
        if not wanted:
            return []
        from app.core.database import engine

        blocked: list[str] = []
        with engine.connect() as conn:
            for metric_id in wanted:
                row = conn.execute(
                    text(
                        "SELECT status FROM semantic_metrics "
                        "WHERE space_id=:space_id AND metric_id=:metric_id "
                        "ORDER BY version DESC LIMIT 1"
                    ),
                    {"space_id": space_id, "metric_id": metric_id},
                ).fetchone()
                if row is None or str(row[0] or "") != "confirmed":
                    blocked.append(metric_id)
        return blocked

    @staticmethod
    def _summary(model: SemanticModel) -> str:
        metric_names = "、".join(x.id for x in model.metrics)
        dimension_names = "、".join(x.id for x in model.dimensions)
        return f"# 业务语义模型 {model.version}\n\n指标：{metric_names}\n\n维度：{dimension_names}\n"


_service = SemanticModelService()


def get_semantic_model_service() -> SemanticModelService:
    return _service
