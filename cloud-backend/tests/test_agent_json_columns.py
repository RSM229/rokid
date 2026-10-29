import sys
import unittest
from pathlib import Path

from sqlalchemy.dialects import mysql, sqlite

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.agent_draft import (  # noqa: E402
    AgentFlowInstance,
    AgentRequestTrace,
    AgentReviewPlan,
)


class AgentLongJsonColumnTests(unittest.TestCase):
    """锁定 (1406, "Data too long for column \\'plan_json\\'") 的根因。

    一次上传 9 张证据时，plan_json 的 UTF-8 字节数会超过 MySQL TEXT 的
    65535 上限。这些列必须是 MEDIUMTEXT，不能退回 TEXT。
    """

    CASES = (
        (AgentReviewPlan, "plan_json"),
        (AgentReviewPlan, "execution_result_json"),
        (AgentRequestTrace, "request_plan_json"),
        (AgentRequestTrace, "stages_json"),
        (AgentRequestTrace, "result_json"),
        (AgentFlowInstance, "result_json"),
    )

    def test_long_json_columns_are_mediumtext_on_mysql(self):
        dialect = mysql.dialect()
        for model, name in self.CASES:
            with self.subTest(model=model.__name__, column=name):
                column = model.__table__.c[name]
                self.assertEqual(
                    column.type.compile(dialect=dialect),
                    "MEDIUMTEXT",
                    f"{model.__name__}.{name} 退回 TEXT 会再次触发 1406",
                )

    def test_long_json_columns_stay_portable_on_sqlite(self):
        dialect = sqlite.dialect()
        for model, name in self.CASES:
            with self.subTest(model=model.__name__, column=name):
                column = model.__table__.c[name]
                self.assertEqual(column.type.compile(dialect=dialect), "TEXT")


if __name__ == "__main__":
    unittest.main()