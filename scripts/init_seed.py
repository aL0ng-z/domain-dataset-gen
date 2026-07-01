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

from app.database import async_session_factory  # noqa: E402
from app.models.config import ChunkProfile, ExportProfile, ParserProfile, TaskPolicy  # noqa: E402
from app.models.project import Project, ProjectMember  # noqa: E402
from app.models.prompt_template import PromptTemplate  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402

MINERU_LOCAL_OPTIONS = {
    "model_path": "models/MinerU2.5-Pro-2604-1.2B",
    "device_map": "auto",
    "render_dpi": "160",
    "image_analysis": False,
}
MINERU_LOCAL_SERVICE_OPTIONS = {
    "base_url": "http://127.0.0.1:9010",
    "backend": "vlm-auto-engine",
    "auto_start": True,
    "startup_timeout_seconds": "30",
    "poll_interval_seconds": "1",
    "max_wait_seconds": "1800",
    "image_analysis": False,
}
PADDLEOCR_LOCAL_SERVICE_OPTIONS = {
    "base_url": "http://127.0.0.1:9020/layout-parsing",
    "vlm_base_url": "http://127.0.0.1:9021",
    "auto_start": True,
    "startup_timeout_seconds": "90",
    "parse_timeout_seconds": "1800",
    "use_layout_detection": True,
    "visualize": False,
}


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
            "请从以下文本中抽取知识点：\n\n**章节路径**: {{heading_path}}\n\n**文本内容**:\n{{content}}"
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
            "请基于以下文本生成问答对：\n\n**章节路径**: {{heading_path}}\n\n**文本内容**:\n{{content}}"
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
            "请基于以下文本生成评测用例：\n\n**章节路径**: {{heading_path}}\n\n**文本内容**:\n{{content}}"
        ),
    },
]


async def ensure_mineru_local_profiles(db) -> int:
    projects = (await db.execute(select(Project))).scalars().all()
    added = 0
    for project in projects:
        local_profiles = (
            (
                "mineru_local",
                "MinerU2.5-Pro（本地模型）",
                MINERU_LOCAL_OPTIONS,
            ),
            (
                "mineru_local_service",
                "MinerU（本地部署服务 / MLX）",
                MINERU_LOCAL_SERVICE_OPTIONS,
            ),
            (
                "paddleocr_local_service",
                "PaddleOCR-VL（本地部署服务 / MLX）",
                PADDLEOCR_LOCAL_SERVICE_OPTIONS,
            ),
        )
        for parser_name, name, parser_options in local_profiles:
            existing = (
                await db.execute(
                    select(ParserProfile).where(
                        ParserProfile.project_id == project.id,
                        ParserProfile.parser_name == parser_name,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(
                    ParserProfile(
                        project_id=project.id,
                        name=name,
                        parser_name=parser_name,
                        parser_options=parser_options,
                    )
                )
                added += 1
    return added


async def seed():
    async with async_session_factory() as db:
        # Check if already seeded
        result = await db.execute(select(User).where(User.username == "admin"))
        if result.scalar_one_or_none():
            added = await ensure_mineru_local_profiles(db)
            await db.commit()
            print(f"Database already seeded. Added {added} missing local parser profile(s).")
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
        parser_pymupdf = ParserProfile(
            project_id=project.id,
            name="PyMuPDF4LLM（本地）",
            parser_name="pymupdf4llm",
            is_default=True,
        )
        parser_mineru = ParserProfile(
            project_id=project.id,
            name="MinerU（API）",
            parser_name="mineru",
            parser_options={"base_url": "https://mineru.net/api/v4/extract/task", "model_version": "vlm"},
        )
        parser_mineru_local = ParserProfile(
            project_id=project.id,
            name="MinerU2.5-Pro（本地模型）",
            parser_name="mineru_local",
            parser_options=MINERU_LOCAL_OPTIONS,
        )
        parser_mineru_local_service = ParserProfile(
            project_id=project.id,
            name="MinerU（本地部署服务 / MLX）",
            parser_name="mineru_local_service",
            parser_options=MINERU_LOCAL_SERVICE_OPTIONS,
        )
        parser_paddle = ParserProfile(
            project_id=project.id,
            name="PaddleOCR（API）",
            parser_name="paddleocr",
            parser_options={"base_url": "https://bea4c9v5r2i52ba7.aistudio-app.com/layout-parsing"},
        )
        parser_paddle_local_service = ParserProfile(
            project_id=project.id,
            name="PaddleOCR-VL（本地部署服务 / MLX）",
            parser_name="paddleocr_local_service",
            parser_options=PADDLEOCR_LOCAL_SERVICE_OPTIONS,
        )
        chunk_profile = ChunkProfile(
            project_id=project.id,
            name="混合标题递归（默认）",
            is_default=True,
        )
        export_profile = ExportProfile(
            project_id=project.id,
            name="SFT JSONL（默认）",
            format="sft_jsonl",
            is_default=True,
        )
        task_policy = TaskPolicy(
            project_id=project.id,
            name="默认任务策略",
            task_type="parse",
            is_default=True,
        )
        db.add_all(
            [
                parser_pymupdf,
                parser_mineru,
                parser_mineru_local,
                parser_mineru_local_service,
                parser_paddle,
                parser_paddle_local_service,
                chunk_profile,
                export_profile,
                task_policy,
            ]
        )

        # Seed prompt templates
        for tmpl_data in SEED_TEMPLATES:
            tmpl = PromptTemplate(project_id=project.id, is_default=True, **tmpl_data)
            db.add(tmpl)

        await db.commit()
        print("Seed completed successfully.")


if __name__ == "__main__":
    asyncio.run(seed())
