"""导出后端接口清单（方法/路径/模块/函数名/权限依赖），供权限矩阵测试用。"""
import re
import json
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# docs/测试产物/ -> 项目根 -> cloud-backend/app/routers
PROJ = os.path.dirname(os.path.dirname(HERE))
ROUTERS_DIR = os.path.join(PROJ, 'cloud-backend', 'app', 'routers')
OUT = os.path.join(HERE, '_api_inventory.json')
print('扫目录:', ROUTERS_DIR)

DECO = re.compile(r"@router\.(get|post|put|delete|patch)\(\s*['\"]([^'\"]*)['\"]")
FN = re.compile(r"async def (\w+)")
PERM = re.compile(r"require_permission\(\s*['\"]([^'\"]+)['\"]")
ROLE = re.compile(r"require_role\(\s*([^)]*?)\)")
CURUSER = re.compile(r"get_current_user")

rows = []
# 模块 → 路由前缀（取自 app/main.py 的 include_router；signature/simulator/local_ai 等挂在 /api 根）
PREFIX = {
    'rtc': '/api/rtc', 'auth': '/api/auth', 'hazard': '/api/hazard',
    'document': '/api/document', 'knowledge': '/api/knowledge', 'knowledge_base': '/api/kb',
    'note': '/api/notes', 'ai_extras': '/api/ai', 'ledger': '/api/ledger',
    'watermark': '/api/watermark', 'simulator': '/api', 'local_ai': '/api/local',
    'work_record': '/api/work-record', 'task': '/api/task', 'knowledge_graph': '/api/kg',
    'project': '/api/project', 'miniprogram_auth': '/api/miniprogram/auth',
    'media': '/api/media', 'signature': '/api', 'inspection': '/api/inspection',
    'ws': '/api/ws', 'acceptance_template': '/api/acceptance-templates',
    'wechat_notice': '/api/wechat', 'omni': '/api/omni', 'model_settings': '/api/model-settings',
    'form_doc': '/api/form-docs', 'work_session': '/api/work-session',
    'provform': '/api/provform', 'meeting': '/api/meeting', 'progress': '/api/progress',
    'parallel': '/api/parallel', 'resources': '/api/resources', 'visa': '/api/visa',
    'equipment': '/api/equipment', 'archive': '/api/archive', 'stats_company': '/api/stats',
    'media_segments': '/api/media',
}
for f in sorted(glob.glob(os.path.join(ROUTERS_DIR, '*.py'))):
    mod = os.path.basename(f)[:-3]
    if mod == '__init__':
        continue
    src = open(f, encoding='utf-8').read()
    pfx = PREFIX.get(mod, f'/api/{mod}')
    for m in DECO.finditer(src):
        method, path = m.group(1).upper(), m.group(2)
        body = src[m.end():m.end() + 3000]
        fnm = FN.search(body)
        head = body[:1500]
        rows.append({
            'method': method,
            'path': path,
            'full_path': pfx + path,
            'module': mod,
            'fn': fnm.group(1) if fnm else '',
            'perm': PERM.findall(head),
            'role': [r.strip() for r in ROLE.findall(head)][:1],
            'auth': bool(CURUSER.search(head)),
        })

json.dump(rows, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('导出接口数:', len(rows))
print('有 require_permission:', sum(1 for r in rows if r['perm']))
print('有 require_role:', sum(1 for r in rows if r['role']))
print('无守卫(仅登录或公开):', sum(1 for r in rows if not r['perm'] and not r['role']))
print('落盘:', OUT)
