"""统一 AI 模型客户端。

支持两类能力：
1. hazard_vlm_detection — 现场风险视觉识别
2. LLM 文本生成 — 规范问答、文书生成、验收指引

所有模型调用均走此后端代理，不在客户端暴露 token。
"""
import json
import logging
from typing import Optional
from app.services import model_gateway

logger = logging.getLogger(__name__)


class AICenterClient:
    """兼容原调用方名称的统一模型客户端。"""

    async def detect_hazard(self, image_bytes: bytes, filename: str = "capture.jpg", prompt: Optional[str] = None) -> dict:
        """现场风险识别

        默认调用华工 hazard_vlm_detection，也可在系统设置中更换供应商。

        返回统一格式：
        {
            "has_hazard": True,
            "confidence": 0.85,
            "hazard_type": "安全帽",
            "description": "...",
            "suggestion": "...",
            "scene_related": "...",
            "risk_status": "...",
            "risk_category": "...",
            "top1_risk": "...",
            "evidence": ["..."],
            "uncertainty": "...",
            "recheck_suggestion": ["..."]
        }
        """
        for attempt in range(2):
            try:
                result = await model_gateway.detect_hazard(
                    image_bytes,
                    filename=filename,
                    prompt=prompt,
                )
                return self._normalize_hazard_result(result)
            except Exception as e:
                logger.warning("视觉模型第 %s 次调用失败: %s", attempt + 1, type(e).__name__)
                if attempt == 0:
                    import asyncio
                    await asyncio.sleep(1)

        raise RuntimeError(
            "隐患识别不可用：视觉模型调用失败。"
            "请检查系统设置中的 VLLM 参数或供应商服务状态。"
        )

    def _normalize_hazard_result(self, raw: dict) -> dict:
        """将视觉模型返回结果统一为前端所需格式。"""
        risk_status = raw.get("risk_status", "")
        has_hazard = "存在隐患" in risk_status or raw.get("has_hazard", False)
        top1_risk = raw.get("top1_risk", "")
        evidence = raw.get("evidence", [])
        uncertainty = raw.get("uncertainty", "低")

        confidence = 0.9 if uncertainty == "低" else 0.7 if uncertainty == "中" else 0.5
        suggestion = "；".join(raw.get("recheck_suggestion", [])) if raw.get("recheck_suggestion") else ""
        description = top1_risk
        if evidence:
            description = f"{top1_risk}。证据：{'；'.join(evidence)}"

        result = {
            "has_hazard": has_hazard,
            "confidence": confidence,
            "hazard_type": raw.get("risk_category", ""),
            "description": description,
            "suggestion": suggestion,
            # 保留原始字段兼容旧前端
            "scene_related": raw.get("scene_related", ""),
            "risk_status": risk_status,
            "risk_category": raw.get("risk_category", ""),
            "top1_risk": top1_risk,
            "evidence": evidence,
            "uncertainty": uncertainty,
            "recheck_suggestion": raw.get("recheck_suggestion", []),
            # 自动匹配的规范条款（命中隐患时由调用方填充，这里默认空列表）
            "matched_regulations": [],
        }

        # 命中隐患时自动匹配规范库条款
        if has_hazard:
            try:
                from app.services.regulation_matcher import match_regulations_for_hazard
                result["matched_regulations"] = match_regulations_for_hazard(
                    hazard_category=result.get("risk_category") or result.get("hazard_type") or "",
                    hazard_description=result.get("top1_risk") or result.get("description") or "",
                )
            except Exception as e:
                logger.warning(f"自动匹配规范库条款失败：{e}")

        return result

    async def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """LLM 文本生成（通用对话接口）

        用于：
        - 规范条款智能问答
        - 监理文书内容生成
        - 验收流程指引生成
        - 隐患整改建议生成

        Args:
            messages: OpenAI 格式的消息列表
            temperature: 生成温度
            max_tokens: 最大生成 token 数
        Returns:
            生成的文本内容
        """
        try:
            content, _ = await model_gateway.chat_completion(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return content
        except model_gateway.ModelGatewayError as exc:
            raise RuntimeError(
                f"LLM 文本生成不可用：{exc}"
            ) from exc

    async def generate_regulation_answer(self, question: str, context: str = "") -> str:
        """规范条款智能问答

        Args:
            question: 用户问题（如"钢筋搭接长度规范要求"）
            context: 检索到的规范上下文
        Returns:
            LLM 生成的规范解答
        """
        system_prompt = (
            "你是一位建筑工程监理规范专家，精通国家及地方建筑规范。"
            "请根据用户问题和提供的规范上下文，给出准确、专业的解答。"
            "回答应引用具体规范条款编号，语言简洁明了。"
        )
        user_content = f"问题：{question}\n"
        if context:
            user_content += f"\n规范上下文：\n{context}\n"
        user_content += "\n请给出专业解答："

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        return await self.chat_completion(messages, temperature=0.3, max_tokens=1024)

    async def generate_document_content(
        self,
        doc_type: str,
        hazard_info: dict | None = None,
        inspection_info: dict | None = None,
        extra_context: str = "",
    ) -> str:
        """监理文书内容生成

        Args:
            doc_type: 文书类型（旁站记录、监理日志、整改通知单等）
            hazard_info: 关联隐患信息
            inspection_info: 巡检信息
            extra_context: 额外上下文
        Returns:
            LLM 生成的文书正文
        """
        system_prompt = (
            "你是一位专业监理工程师，擅长撰写各类监理文书。"
            "请根据提供的信息，生成规范、专业的监理文书内容。"
            "内容应包含：施工情况描述、发现问题、处理措施、规范依据等。"
            "语言正式、客观，符合监理文书写作规范。"
        )
        user_content = f"请生成【{doc_type}】的内容。\n"
        if hazard_info:
            user_content += f"\n关联隐患信息：\n{json.dumps(hazard_info, ensure_ascii=False, indent=2)}\n"
        if inspection_info:
            user_content += f"\n巡检信息：\n{json.dumps(inspection_info, ensure_ascii=False, indent=2)}\n"
        if extra_context:
            user_content += f"\n补充信息：{extra_context}\n"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        return await self.chat_completion(messages, temperature=0.5, max_tokens=2048)

    async def generate_acceptance_guide(self, keyword: str, context: str = "") -> dict:
        """验收流程 AI 智能指引生成

        Args:
            keyword: 验收项目关键词（如"钢筋"、"混凝土"）
            context: 检索到的规范上下文
        Returns:
            包含 steps、key_points、common_violations、regulation_basis 的字典
        """
        system_prompt = (
            "你是一位建筑工程验收专家。请根据验收项目关键词，生成详细的验收指引。"
            "输出必须是 JSON 格式，包含以下字段：\n"
            '{"steps": ["验收步骤1", ...], "key_points": ["管控要点1", ...], '
            '"common_violations": ["常见违规1", ...], "regulation_basis": ["规范依据1", ...]}\n'
            "只输出 JSON，不要其他内容。"
        )
        user_content = f"验收项目：{keyword}\n"
        if context:
            user_content += f"规范上下文：\n{context}\n"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        result = await self.chat_completion(messages, temperature=0.3, max_tokens=1024)

        # 尝试解析 JSON
        try:
            # 移除可能的 markdown 代码块标记
            cleaned = result.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            return json.loads(cleaned)
        except (json.JSONDecodeError, IndexError):
            # 解析失败，返回原始文本
            return {
                "steps": [],
                "key_points": [],
                "common_violations": [],
                "regulation_basis": [],
                "raw_response": result,
            }

    async def generate_rectification_suggestion(self, hazard_info: dict) -> str:
        """根据隐患信息生成整改建议

        Args:
            hazard_info: 隐患详情
        Returns:
            整改建议文本
        """
        system_prompt = (
            "你是一位建筑施工安全专家。请根据隐患信息，给出具体的整改建议。"
            "建议应包含：整改措施、整改期限建议、复查要点。"
        )
        user_content = f"隐患信息：\n{json.dumps(hazard_info, ensure_ascii=False, indent=2)}\n\n请给出整改建议："

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        return await self.chat_completion(messages, temperature=0.4, max_tokens=1024)


# 全局单例
ai_client = AICenterClient()
