"""首装初始账号种子。

初始超级管理员的账号与密码全部从环境变量读取，不使用任何内置凭据，缺失即报错退出；
脚本也不把密码写入日志或标准输出。示例部门与人员仅在显式传入 ``--with-demo-data``
时创建，其共用密码同样取自环境变量。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from sqlalchemy import func, select


APP_ROOT = Path(__file__).resolve().parents[1]
for import_path in (APP_ROOT, APP_ROOT / "package"):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

SUPERADMIN_NAME_ENV = "YUXI_SUPER_ADMIN_NAME"
SUPERADMIN_UID_ENV = "YUXI_SUPER_ADMIN_UID"
SUPERADMIN_PASSWORD_ENV = "YUXI_SUPER_ADMIN_PASSWORD"
DEMO_USER_PASSWORD_ENV = "YUXI_DEMO_USER_PASSWORD"


class DepartmentSeed(TypedDict):
    name: str
    description: str
    prefix: str
    normal_count: int


DEMO_DEPARTMENTS: list[DepartmentSeed] = [
    {"name": "研发部", "description": "负责产品研发与技术平台建设", "prefix": "dev", "normal_count": 5},
    {"name": "产品部", "description": "负责产品规划、需求分析与项目推进", "prefix": "prod", "normal_count": 5},
    {"name": "运营部", "description": "负责业务运营、用户支持与内容维护", "prefix": "ops", "normal_count": 4},
]


class SeedError(Exception):
    pass


def load_project_env() -> None:
    load_dotenv(APP_ROOT / ".env", override=False)
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)


def read_superadmin_credentials() -> tuple[str, str, str]:
    """读取初始超级管理员账号，返回 (显示名, 登录标识, 密码)。"""
    name = (os.getenv(SUPERADMIN_NAME_ENV) or "").strip()
    uid = (os.getenv(SUPERADMIN_UID_ENV) or "").strip()
    password = (os.getenv(SUPERADMIN_PASSWORD_ENV) or "").strip()
    missing = [
        env_name
        for env_name, value in (
            (SUPERADMIN_NAME_ENV, name),
            (SUPERADMIN_UID_ENV, uid),
            (SUPERADMIN_PASSWORD_ENV, password),
        )
        if not value
    ]
    if missing:
        raise SeedError(
            "初始超级管理员账号未配置（缺少 " + "、".join(missing) + "）。"
            "本脚本不使用内置凭据，请在 .env 中设置后重试：\n"
            f"  {SUPERADMIN_NAME_ENV}=系统管理员\n"
            f"  {SUPERADMIN_UID_ENV}=admin\n"
            f"  {SUPERADMIN_PASSWORD_ENV}=<openssl rand -hex 16 的输出>"
        )
    return name, uid, password


def read_demo_password() -> str:
    password = (os.getenv(DEMO_USER_PASSWORD_ENV) or "").strip()
    if not password:
        raise SeedError(
            f"创建示例部门与人员需要设置 {DEMO_USER_PASSWORD_ENV}（示例账号共用该密码，请勿使用生产环境的真实密码）。"
        )
    return password


async def ensure_uninitialized(session) -> None:
    from yuxi.storage.postgres.models_business import User

    user_count = await session.scalar(select(func.count(User.id)))
    if user_count:
        raise SeedError(f"系统已初始化：users 表已有 {user_count} 个用户，脚本已退出。")

    superadmin_count = await session.scalar(select(func.count(User.id)).where(User.role == "superadmin"))
    if superadmin_count:
        raise SeedError("系统已初始化：已存在超级管理员，脚本已退出。")


async def build_demo_users(session, *, password: str) -> list:
    """创建示例部门与人员，仅供本地联调使用。"""
    from yuxi.storage.postgres.models_business import Department, User
    from yuxi.utils.auth_utils import AuthUtils

    password_hash = AuthUtils.hash_password(password)
    departments: dict[str, Department] = {}
    for department_seed in DEMO_DEPARTMENTS:
        department = Department(
            name=department_seed["name"],
            description=department_seed["description"],
        )
        session.add(department)
        departments[department_seed["prefix"]] = department
    await session.flush()

    users: list[User] = []
    for department_seed in DEMO_DEPARTMENTS:
        department = departments[department_seed["prefix"]]
        for index in range(1, 3):
            users.append(
                User(
                    username=f"{department_seed['name']}管理员{index}",
                    uid=f"{department_seed['prefix']}_admin_{index}",
                    password_hash=password_hash,
                    role="admin",
                    department_id=department.id,
                )
            )
        for index in range(1, department_seed["normal_count"] + 1):
            users.append(
                User(
                    username=f"{department_seed['name']}用户{index}",
                    uid=f"{department_seed['prefix']}_user_{index:02d}",
                    password_hash=password_hash,
                    role="user",
                    department_id=department.id,
                )
            )
    return users


async def seed_initial_users(*, with_demo_data: bool) -> str:
    """创建超级管理员，返回其登录标识。"""
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import User
    from yuxi.utils.auth_utils import AuthUtils
    from yuxi.utils.datetime_utils import utc_now_naive

    superadmin_name, superadmin_uid, superadmin_password = read_superadmin_credentials()
    demo_password = read_demo_password() if with_demo_data else None

    try:
        pg_manager.initialize()
        await pg_manager.create_business_tables()
        await pg_manager.ensure_business_schema()

        async with pg_manager.get_async_session_context() as session:
            await ensure_uninitialized(session)

            users = [
                User(
                    username=superadmin_name,
                    uid=superadmin_uid,
                    password_hash=AuthUtils.hash_password(superadmin_password),
                    role="superadmin",
                    last_login=utc_now_naive(),
                )
            ]
            if demo_password is not None:
                users.extend(await build_demo_users(session, password=demo_password))

            session.add_all(users)
    finally:
        await pg_manager.close()

    return superadmin_uid


def main() -> int:
    parser = argparse.ArgumentParser(description="创建初始超级管理员（示例部门与人员需显式开启）")
    parser.add_argument(
        "--with-demo-data",
        action="store_true",
        help=f"同时创建 3 个示例部门、6 个部门管理员与 14 个普通用户（密码取自 {DEMO_USER_PASSWORD_ENV}）",
    )
    args = parser.parse_args()

    load_project_env()
    try:
        superadmin_uid = asyncio.run(seed_initial_users(with_demo_data=args.with_demo_data))
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"初始化种子用户失败：{exc}", file=sys.stderr)
        return 1

    print(f"初始化完成：已创建超级管理员 {superadmin_uid}。")
    if args.with_demo_data:
        print(f"已创建 3 个示例部门、6 个部门管理员与 14 个普通用户，密码取自 {DEMO_USER_PASSWORD_ENV}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
