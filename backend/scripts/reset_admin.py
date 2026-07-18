"""重置 admin 密码并创建演示用户和部门"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))

from yuxi.utils.auth_utils import AuthUtils
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User


async def setup():
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    await pg_manager.ensure_business_schema()

    async with pg_manager.get_async_session_context() as session:
        from sqlalchemy import select

        # 1. 重置 admin 密码
        result = await session.execute(select(User).filter(User.uid == "admin"))
        admin = result.scalar_one_or_none()
        if admin:
            admin.password_hash = AuthUtils.hash_password("admin123")
            print(f"✓ 已重置 admin 密码: admin123")

        # 2. 清理旧数据（但保留 admin）
        for uid in ["zhangsan", "lisi", "wangwu"]:
            result = await session.execute(select(User).filter(User.uid == uid))
            user = result.scalar_one_or_none()
            if user:
                await session.delete(user)

        # 3. 创建部门
        dept_data = {
            "研发部": "负责产品研发与技术",
            "财务部": "负责财务管理与核算",
            "市场部": "负责市场营销与推广",
        }
        departments = {}
        for name, desc in dept_data.items():
            result = await session.execute(select(Department).filter(Department.name == name))
            dept = result.scalar_one_or_none()
            if dept:
                dept.description = desc
            else:
                dept = Department(name=name, description=desc)
                session.add(dept)
            departments[name] = dept
        await session.flush()

        # 4. 创建演示用户
        demo_users = [
            User(
                username="张三",
                uid="zhangsan",
                password_hash=AuthUtils.hash_password("123456"),
                role="user",
                department_id=departments["研发部"].id,
            ),
            User(
                username="李四",
                uid="lisi",
                password_hash=AuthUtils.hash_password("123456"),
                role="user",
                department_id=departments["财务部"].id,
            ),
            User(
                username="王五",
                uid="wangwu",
                password_hash=AuthUtils.hash_password("123456"),
                role="user",
                department_id=departments["市场部"].id,
            ),
        ]
        for u in demo_users:
            session.add(u)
        await session.flush()

        print("✓ 已创建演示用户:")
        for u in demo_users:
            print(f"  {u.username} (uid={u.uid}, 部门={u.department_id}, 密码=123456)")

    await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(setup())
