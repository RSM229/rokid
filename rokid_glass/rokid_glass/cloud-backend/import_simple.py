import os
import asyncio
from pathlib import Path
from docx import Document
import sys

sys.path.insert(0, '/app')

from app.db import async_session
from app.models.regulation_chunk import RegulationChunk
from sqlalchemy import text

async def import_kb():
    kb_dir = Path("/app/知识库")
    files = []
    
    for root, dirs, dirfiles in os.walk(kb_dir):
        for f in dirfiles:
            if f.endswith('.docx'):
                files.append(Path(root) / f)
    
    print(f"找到 {len(files)} 个 docx 文件")
    
    for file_path in files:
        try:
            doc = Document(str(file_path))
            paragraphs = []
            for p in doc.paragraphs:
                if p.text.strip():
                    paragraphs.append(p.text.strip())
            text_content = '\n'.join(paragraphs)
            
            if not text_content:
                continue
            
            file_name = file_path.name.replace('.docx', '')
            category = "省统表"
            if "安全" in str(file_path):
                category = "安全监理用表"
            elif "巡视" in file_name:
                category = "巡视记录"
            elif "验收" in file_name:
                category = "验收记录"
            elif "旁站" in file_name:
                category = "旁站记录"
            elif "见证" in file_name:
                category = "见证记录"
            elif "隐患" in file_name:
                category = "安全隐患"
            
            async with async_session() as session:
                result = await session.execute(
                    text("SELECT id FROM regulation_chunks WHERE doc_name = :name AND category = :cat"),
                    {"name": file_name, "cat": category}
                )
                if result.fetchone():
                    print(f"⏭️ 跳过: {file_name}")
                    continue
                
                chunk = RegulationChunk(
                    doc_name=file_name,
                    category=category,
                    chunk_index=0,
                    content=text_content[:5000]
                )
                session.add(chunk)
                await session.commit()
                print(f"✅ 导入: [{category}] {file_name} ({len(text_content)} 字符)")
                
        except Exception as e:
            print(f"❌ 失败: {file_path.name} - {e}")

asyncio.run(import_kb())
