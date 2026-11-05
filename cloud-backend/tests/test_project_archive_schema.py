from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
import pytest

from app.db import _reconcile_missing_columns
from app.main import app  # noqa: F401
from app.models.knowledge_base import KnowledgeDocument  # noqa: F401


def test_reconcile_adds_source_document_unique_index_idempotently():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        missing_columns = {"source_document_id", "source_document_ref"}
        columns = [
            column for column in KnowledgeDocument.__table__.columns
            if column.name not in missing_columns
        ]
        column_sql = []
        for column in columns:
            sql = f'"{column.name}" {column.type.compile(dialect=conn.dialect)}'
            if column.primary_key:
                sql += " PRIMARY KEY"
            elif not column.nullable:
                sql += " NOT NULL"
            column_sql.append(sql)
        conn.execute(text(
            "CREATE TABLE knowledge_documents (" + ", ".join(column_sql) + ")"
        ))
        _reconcile_missing_columns(conn, {"knowledge_documents"})
        _reconcile_missing_columns(conn, {"knowledge_documents"})

        indexes = inspect(conn).get_indexes("knowledge_documents")
        reconciled_columns = {
            item["name"] for item in inspect(conn).get_columns("knowledge_documents")
        }
        assert {"source_document_id", "source_document_ref"} <= reconciled_columns
        source_indexes = [
            index for index in indexes
            if index.get("unique")
            and index.get("column_names") == ["source_document_id"]
        ]
        assert len(source_indexes) == 1

        conn.execute(text(
            "INSERT INTO knowledge_documents "
            "(kb_id, uploader_id, name, filename, path, size_bytes, created_at, is_deleted, source_document_id) "
            "VALUES (1, 1, 'a', 'a', '/a', 0, CURRENT_TIMESTAMP, 0, 7)"
        ))
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO knowledge_documents "
                "(kb_id, uploader_id, name, filename, path, size_bytes, created_at, is_deleted, source_document_id) "
                "VALUES (1, 1, 'b', 'b', '/b', 0, CURRENT_TIMESTAMP, 0, 7)"
            ))
