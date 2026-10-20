"""权限目录（Step 3）—— 权限键的**唯一权威清单**。

为什么要独立目录，而不是像以前那样从角色权限矩阵推导？

    Step 2 时 ``ALL_PERMISSIONS = ⋃ ROLE_PERMISSIONS.values()``，而超管的
    「全量」正是取这个并集。Step 3 把权限矩阵交给界面配置之后，运维只要把
    某个权限键从**所有**角色上取消勾选，并集就丢掉这个键 —— 连超管也一起
    失去它，于是**再没有人能把它配回来**，权限系统当场锁死。

    所以超管的真源必须是这份**静态目录**，与可变的矩阵解耦：
        - 界面上的可选权限 = 本目录（不允许凭空造键）
        - 超管的权限      = 本目录全量
        - 其他角色的权限  = 矩阵（DB 可配，代码兜底）

``org_level`` 语义（注意与 ``ORG_LEVEL_PERMISSIONS`` 的细微差别）：

    标记为 ``org_level=True`` 表示**只有公司级角色**（``super_admin`` /
    ``admin``）可以持有，项目级岗位勾选它一律 400。

    **跨两层**的键共两个（公司级＝全公司/任意项目，项目级＝本项目）：
    ``member_manage``（成员管理）与 ``project_edit``（项目信息编辑）。二者
    在本目录里都标 ``org_level=False``，故公司级与项目级岗位皆可被授予。

    ``app.models.user.ORG_LEVEL_PERMISSIONS``（公司级角色的授权范围）仍
    包含它们，两者不矛盾 —— 一个是「谁能持有」，一个是「公司级角色拿什么」。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionInfo:
    """一条权限的元信息（界面按 ``group`` 分组展示）。"""

    key: str
    label: str
    group: str
    # True = 仅公司级角色可持有；项目级岗位勾选即拒绝
    org_level: bool = False
    # 说明文字（界面上做 tooltip，解释这个权限管什么）
    hint: str = ""


PERMISSION_GROUPS: tuple[str, ...] = (
    "现场作业",
    "任务协同",
    "数据档案",
    "智能工具",
    "系统与人员",
)

PERMISSION_CATALOG: tuple[PermissionInfo, ...] = (
    # ---------- 现场作业 ----------
    PermissionInfo("work_record", "工作记录", "现场作业", hint="巡视/旁站/见证/验收等现场记录的填报"),
    PermissionInfo("inspection", "巡视检查", "现场作业", hint="巡视检查与检查看板"),
    PermissionInfo("acceptance", "验收", "现场作业", hint="检验批与分项验收"),
    PermissionInfo("witness", "见证", "现场作业", hint="见证取样与见证记录"),
    PermissionInfo("station", "旁站", "现场作业", hint="旁站监理记录"),
    PermissionInfo("hazard_ai", "隐患识别", "现场作业", hint="AI 隐患识别与隐患台账"),
    # ---------- 任务协同 ----------
    PermissionInfo("task_assign", "任务派发", "任务协同", hint="把任务指派给他人"),
    PermissionInfo("task_manage", "任务管理", "任务协同", hint="管理项目内全部任务"),
    PermissionInfo("task_receive", "任务承接", "任务协同", hint="承接并处理指派给自己的任务"),
    PermissionInfo("todo_center", "待办中心", "任务协同", hint="个人待办聚合"),
    # ---------- 数据档案 ----------
    PermissionInfo("ledger", "台账", "数据档案", hint="影像/隐患/文书三类台账"),
    PermissionInfo("document_approve", "文书审批", "数据档案", hint="监理文书审批与签章"),
    PermissionInfo("acceptance_template", "验收模板", "数据档案", hint="验收模板的维护"),
    PermissionInfo("knowledge_base", "信息库", "数据档案", hint="规范/知识检索"),
    PermissionInfo("kg_manage", "知识图谱管理", "数据档案", hint="知识图谱的维护"),
    # 说明：signature_manage 原先是「孤儿键」—— Web 前端路由用它门控「签章管理」，
    # 但后端矩阵里从来没有这个键，导致**除超管外所有人都进不去该页面**。
    # 这里补进目录，使其可被正常配置（默认授予需要签署文书的总监/专监）。
    PermissionInfo("signature_manage", "签章管理", "数据档案", hint="上传与管理个人签名/印章"),
    # ---------- 智能工具 ----------
    PermissionInfo("dashboard", "工作台", "智能工具", hint="首页工作台与统计"),
    PermissionInfo("local_ai", "本地智能体", "智能工具", hint="本地大模型对话与智能助手"),
    # ---------- 系统与人员 ----------
    PermissionInfo("settings", "系统设置", "系统与人员", hint="项目级系统设置（总监默认持有）"),
    PermissionInfo("user_center", "个人中心", "系统与人员", hint="查看与修改个人信息"),
    PermissionInfo("member_manage", "成员管理", "系统与人员",
                   hint="公司级＝全部项目；项目级＝本项目（唯一跨两层的权限）"),
    PermissionInfo("user_manage", "账号管理", "系统与人员", org_level=True,
                   hint="建号/改信息/改密码/解绑微信"),
    PermissionInfo("user_approve", "人员审批", "系统与人员", org_level=True,
                   hint="审批新注册人员与加入申请"),
    PermissionInfo("role_grant", "角色授权", "系统与人员", org_level=True,
                   hint="授予公司级角色、配置角色权限矩阵"),
    PermissionInfo("project_create", "项目立项", "系统与人员", org_level=True,
                   hint="新建项目"),
    # 2026-10-13：从 org_level 摘出 —— 与 member_manage 同为跨两层键。
    # 公司级＝任意项目；项目级＝本项目。原先标 org_level 导致总监改不了自家
    # 项目信息（前端「项目资料」按钮也不可见），与 update_project 范围闸里
    # 那句「只能改自己所在项目」的意图矛盾。
    PermissionInfo("project_edit", "项目编辑", "系统与人员",
                   hint="修改项目基本信息（名称/地点/参建单位/项目章）；公司级＝任意项目，项目级＝本项目"),
    # Step 5 新增：项目结项是**公司级管理动作**（批量把在岗成员置离场、
    # 项目从各方列表收口），因此与 project_edit 一样标 org_level —— 项目级
    # 岗位勾选即拒绝。默认仅超管持有，可在「角色权限」页改配给公司级管理员。
    PermissionInfo("project_complete", "项目结项", "系统与人员", org_level=True,
                   hint="项目结项（置为已完工并批量收口在岗成员）与恢复"),
)

#: 全部权限键（**超管全量与界面可选集的真源**）。
ALL_PERMISSION_KEYS: list[str] = [info.key for info in PERMISSION_CATALOG]

#: 权限键 -> 元信息。
PERMISSION_MAP: dict[str, PermissionInfo] = {info.key: info for info in PERMISSION_CATALOG}

#: 仅公司级角色可持有的权限（项目级岗位勾选即拒绝）。
ORG_ONLY_PERMISSION_KEYS: frozenset[str] = frozenset(
    info.key for info in PERMISSION_CATALOG if info.org_level
)


def validate_permission_keys(keys: list[str]) -> list[str]:
    """过滤出**不在目录中**的键（用于配置接口的出参校验）。

    保留未知键会让界面出现勾不上的幽灵权限、也会让超管的「全量」失真，
    因此配置接口必须拒绝目录外的键，而不是默默接受。
    """
    known = set(ALL_PERMISSION_KEYS)
    return [key for key in keys if key not in known]
