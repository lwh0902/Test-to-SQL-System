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
        # A generated draft is never silently treated as a customer edit. The UI can
        # display/edit it, then call publish after human review.
        return build_seed_semantic_model(space_id, catalog)

    def load_published(self, space_id: str, *, schema_fingerprint: str = "") -> SemanticModel | None:
        from app.core.database import engine

        try:
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT v.model_json
                    FROM space_semantic_models p
                    JOIN semantic_model_versions v ON v.id = p.published_version_id
                    WHERE p.space_id = :space_id AND v.status = 'published'
                """), {"space_id": space_id}).fetchone()
        except Exception:
            return None
        if not row:
            return None
        raw = row[0]
        try:
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
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
            conn.commit()
        return int(version_id)

    @staticmethod
    def _summary(model: SemanticModel) -> str:
        metric_names = "、".join(x.id for x in model.metrics)
        dimension_names = "、".join(x.id for x in model.dimensions)
        return f"# 业务语义模型 {model.version}\n\n指标：{metric_names}\n\n维度：{dimension_names}\n"


_service = SemanticModelService()


def get_semantic_model_service() -> SemanticModelService:
    return _service
