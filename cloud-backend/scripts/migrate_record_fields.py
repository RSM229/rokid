"""62字段修复升级：只增加列/签认表，显式--yes才执行；不猜测业务数据。"""
import argparse
import asyncio
import importlib
import json
import pkgutil
import sys
from pathlib import Path
from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import Base, engine, _reconcile_missing_columns
import app.models

for module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module('app.models.' + module.name)

TABLES = ('projects', 'work_records', 'parallel_inspections', 'documents', 'document_signoffs')


def preflight(conn):
    inspector = inspect(conn)
    existing = set(inspector.get_table_names())
    if not {"users", "organizations"}.issubset(existing):
        raise RuntimeError("数据库尚未初始化；本脚本仅升级已有系统，不能替代init_db")
    changes = []
    for name in TABLES:
        table = Base.metadata.tables[name]
        if name not in existing:
            changes.append({'table': name, 'action': 'CREATE TABLE'})
            continue
        present = {c['name'] for c in inspector.get_columns(name)}
        for column in table.columns:
            if column.name not in present:
                safe = column.nullable or column.server_default is not None
                changes.append({'table': name, 'column': column.name, 'action': 'ADD COLUMN' if safe else 'BLOCKED: non-null without default'})
    return changes


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.yes and args.dry_run:
        parser.error('--yes和--dry-run不能同时使用')
    async with engine.begin() as conn:
        plan = await conn.run_sync(preflight)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if not args.yes:
            print('只读预检，未修改数据库。备份后使用 --yes 执行。')
        else:
            if any(c['action'].startswith('BLOCKED') for c in plan):
                raise RuntimeError('存在不能安全自动补齐的列，停止升级')
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[Base.metadata.tables[n] for n in TABLES]))
            await conn.run_sync(lambda c: _reconcile_missing_columns(c, TABLES))
            # 只补可推导的日期；施工人数、单位、结论和签章保持待补填。
            conn_dialect = conn.dialect.name
            if conn_dialect in ('mysql', 'sqlite'):
                await conn.execute(text('UPDATE work_records SET record_date = DATE(COALESCE(station_start_time, inspection_start_time, created_at)) WHERE record_date IS NULL'))
            print('升级完成。缺失的单位、人数、工序、参验人员仍须真实补填。')
            print(json.dumps(await conn.run_sync(preflight), ensure_ascii=False))
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
