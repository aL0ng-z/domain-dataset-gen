"""Initialize database with admin user, default project, and seed data."""
import asyncio
import os
import sys

# Change to apps/api so pydantic-settings picks up apps/api/.env
_orig_dir = os.getcwd()
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps", "api"))

# Adjust path for running from project root
sys.path.insert(0, os.getcwd())

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import async_session_factory, engine, Base  # noqa: E402
from app.models import *  # noqa: E402, F401, F403
from app.services.auth_service import AuthService  # noqa: E402


SEED_TEMPLATES = [
    {
        "task_type": "knowledge_extraction",
        "name": "知识点抽取（默认）",
        "system_prompt": (
            "你是一个专业的领域知识抽取助手。你的任务是从给定的技术文本中抽取结构化的知识点。\n"
            "请以 JSON 格式输出，包含以下字段：\n"
            "- title: 知识点标题\n"
            "- content: 知识点详细内容\n"
            "- key_concepts: 关键概念列表\n"
            "- prerequisites: 前置知识（如有）"
        ),
        "user_prompt_template": (
            "请从以下文本中抽取知识点：\n\n"
            "**章节路径**: {{heading_path}}\n\n"
            "**文本内容**:\n{{content}}"
        ),
    },
    {
        "task_type": "qa_generation",
        "name": "问答对生成（默认）",
        "system_prompt": (
            "你是一个专业的问答对生成助手。根据给定的技术文本，生成高质量的问答对。\n"
            "请以 JSON 格式输出，包含以下字段：\n"
            "- question: 问题\n"
            "- answer: 答案\n"
            "- difficulty: 难度（easy/medium/hard）\n"
            "- evidence: 答案在原文中的依据"
        ),
        "user_prompt_template": (
            "请基于以下文本生成问答对：\n\n"
            "**章节路径**: {{heading_path}}\n\n"
            "**文本内容**:\n{{content}}"
        ),
    },
    {
        "task_type": "benchmark_case",
        "name": "评测用例生成（默认）",
        "system_prompt": (
            "你是一个专业的评测用例生成助手。根据给定的技术文本，生成可用于评测 LLM 领域知识的测试用例。\n"
            "请以 JSON 格式输出，包含以下字段：\n"
            "- question: 评测问题\n"
            "- reference_answer: 参考答案\n"
            "- scoring_rubric: 评分标准\n"
            "- evidence: 原文依据"
        ),
        "user_prompt_template": (
            "请基于以下文本生成评测用例：\n\n"
            "**章节路径**: {{heading_path}}\n\n"
            "**文本内容**:\n{{content}}"
        ),
    },
]


async def seed():
    async with async_session_factory() as db:
        # Check if already seeded
        result = await db.execute(select(User).where(User.username == "admin"))
        if result.scalar_one_or_none():
            print("Database already seeded. Skipping.")
            return

        # Create admin user
        auth_service = AuthService(db)
        admin = await auth_service.register("admin", "admin@localhost", "admin123", "admin")
        print(f"Created admin user: admin / admin123 (ID: {admin.id})")

        # Create default project
        project = Project(name="压气机知识抽取", description="压气机设计领域知识抽取与数据集生产", created_by=admin.id)
        db.add(project)
        await db.flush()

        # Add admin as project member
        member = ProjectMember(project_id=project.id, user_id=admin.id, role="admin")
        db.add(member)
        print(f"Created project: {project.name} (ID: {project.id})")

        # Seed config profiles
        parser_profile = ParserProfile(
            project_id=project.id, name="PyMuPDF4LLM（默认）", parser_name="pymupdf4llm", is_default=True,
        )
        chunk_profile = ChunkProfile(
            project_id=project.id, name="混合标题递归（默认）", is_default=True,
        )
        export_profile = ExportProfile(
            project_id=project.id, name="SFT JSONL（默认）", format="sft_jsonl", is_default=True,
        )
        task_policy = TaskPolicy(
            project_id=project.id, name="默认任务策略", task_type="parse", is_default=True,
        )
        db.add_all([parser_profile, chunk_profile, export_profile, task_policy])

        # Seed prompt templates
        for tmpl_data in SEED_TEMPLATES:
            tmpl = PromptTemplate(project_id=project.id, is_default=True, **tmpl_data)
            db.add(tmpl)

        await db.commit()
        print("Seed completed successfully.")


if __name__ == "__main__":
    asyncio.run(seed())
