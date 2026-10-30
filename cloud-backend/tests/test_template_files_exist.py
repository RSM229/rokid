"""模板文件存在性回归测试。

form_registry 里声明的 template 文件名，必须在所有会被实际使用到的知识库
目录中真实存在，否则 render_form 运行时会抛 TemplateNotFoundError。

关键背景（2026-10-30 排查）：仓库里存在两份知识库——
  - 仓库根 知识库/（本地开发默认路径，BACKEND_DIR.parent/知识库）
  - cloud-backend/知识库/（容器 bind-mount 下的回退路径）
配置按环境二选一，两份曾长期不同步：生产缺 21监理日志-房建新.docx、
本地开发缺 安全监理日志表格.docx / 验收记录.docx，导致两边各自生成不了
对应文书。本测试同时校验两个候选目录，任一缺失即红，杜绝再次分叉。
"""
import os
import unittest
from pathlib import Path

from app.services import form_registry as fr
from app.services.form_renderer import list_available_template_files
from app.config import BACKEND_DIR


def _candidate_kb_dirs():
    """复刻 config._resolve_knowledge_base_path 的两个候选目录。"""
    repo_root = BACKEND_DIR.parent / "知识库"
    nested = BACKEND_DIR / "知识库"
    return [repo_root, nested]


class TestTemplateFilesExist(unittest.TestCase):
    def test_all_declared_templates_exist_in_configured_kb(self):
        rows = list_available_template_files()
        missing = [
            r["form_code"]
            for r in rows
            if r.get("exists") is False and r.get("reason") != "no_template"
        ]
        self.assertEqual(
            missing,
            [],
            f"当前配置知识库下，以下表单声明了模板但找不到: {missing}",
        )

    def _resolve_folder(self, kb_dir: Path, folder: str) -> Path:
        """复刻 form_renderer 的模板子目录解析逻辑。"""
        if folder == "sheng_tong_biao":
            return kb_dir / "省统表word版"
        if folder == "safety":
            return kb_dir / "第三节安全监理用表" / "安全监理用表"
        return kb_dir

    def test_all_declared_templates_exist_in_every_candidate_kb(self):
        """任一候选知识库目录缺失声明模板都应变红（防两份知识库再次分叉）。"""
        dirs = [d for d in _candidate_kb_dirs() if d.exists()]
        self.assertTrue(dirs, "两个候选知识库目录都不存在，配置解析异常")
        problems = []
        for spec in fr.FORMS:
            if not spec.template:
                continue
            for d in dirs:
                if not (self._resolve_folder(d, spec.folder) / spec.template).exists():
                    problems.append((spec.form_code, str(d)))
        self.assertEqual(
            problems,
            [],
            f"以下 (表单, 知识库目录) 组合缺失模板文件: {problems}",
        )

    def test_registry_schema_code_alignment(self):
        """registry 与 schema 的表单编码集合应一致（缺省映射会静默降级）。"""
        from app.services import provform_schema as ps

        reg_codes = {s.form_code for s in fr.FORMS}
        sch_codes = set(ps.SCHEMAS)
        orphan = sorted(sch_codes - reg_codes)
        if orphan:
            self.fail(f"schema 中存在但 registry 未注册的表单编码: {orphan}")


if __name__ == "__main__":
    unittest.main()
