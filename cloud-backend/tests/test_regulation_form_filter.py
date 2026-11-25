"""验证：query_regulation 不再把项目生成的整改通知单当「规范条文」返回。

复刻生产 user 1 消息 717-718：用户说「刚才那条别管了，查一下规范」，
query_regulation 命中本项目《安全隐患整改通知单_疑似通风管道安装存在
缝隙或未密封》（GDAQ30308）并当规范依据返回全文——表式/业务文书混进
规范检索。

负例保护：真规范检索（「模板支撑验收规范」内置指引）、知识库真表式
（不带整改词）不受影响。
"""
import sys

sys.path.insert(0, ".")

from app.services.agent_tools import is_form_like_doc as is_form_like


def main():
    # 直接用生产实现 is_form_like_doc（防止测试复制品与实现脱节）。
    # ---- 1. 生产现场复刻：整改通知单必须被判为表式 ----
    prod_hit = {
        "name": "安全隐患整改通知单_疑似通风管道安装存在缝隙或未密封",
        "filename": "安全隐患整改通知单_疑似通风管道安装存在缝隙或未密封.pdf",
        "snippet": "安全隐患整改通知 （监理对施工单位项目部） GDAQ30308 工程名称： 沙盛四路等四条道路工程",
    }
    assert is_form_like(prod_hit), "整改通知单应判表式"
    print("1. 整改通知单判表式: True")

    # ---- 2. 其它整改类文书变体 ----
    for name in (
        "安全隐患整改通知回复_脚手架未设置安全防护网",
        "整改回复单_GDAQ20309",
        "隐患整改通知（物料堆放）",
        "复工申请",
    ):
        assert is_form_like({"name": name, "filename": "", "snippet": ""}), name
    print("2. 整改/回复/复工变体全拦: True")

    # ---- 3. 负例：真规范文档不被误伤 ----
    clean_hits = [
        {"name": "建筑施工扣件式钢管脚手架安全技术规范", "filename": "JGJ130.pdf",
         "snippet": "连墙件设置应符合规范要求"},
        {"name": "混凝土结构工程施工质量验收规范", "filename": "GB50204.pdf",
         "snippet": "钢筋安装时，受力钢筋的品种、级别、规格和数量必须符合设计要求"},
        {"name": "建筑施工高处作业安全技术规范", "filename": "JGJ80.pdf",
         "snippet": "临边洞口防护栏杆高度"},
    ]
    for hit in clean_hits:
        assert not is_form_like(hit), f"误伤: {hit['name']}"
    print("3. 真规范不误伤: True")

    # ---- 4. 原有表式文件继续被拦（回归）----
    legacy = [
        {"name": "旁站记录", "filename": "", "snippet": ""},
        {"name": "监理日志", "filename": "", "snippet": ""},
        {"name": "某台账", "filename": "", "snippet": "本表一式三份"},
    ]
    for hit in legacy:
        assert is_form_like(hit), hit["name"]
    print("4. 原表式拦截保持: True")

    print("ALL PASS")


if __name__ == "__main__":
    main()
