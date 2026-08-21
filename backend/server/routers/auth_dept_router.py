"""
部门管理路由
提供部门的增删改查接口，仅超级管理员可访问
"""

import re

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from sqlalchemy import delete as sqlalchemy_delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import APIKey, Department, User
from yuxi.repositories.department_repository import DepartmentRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.repositories.team_repository import TeamRepository
from server.utils.auth_middleware import get_superadmin_user, get_admin_user, get_db
from yuxi.utils.auth_utils import AuthUtils
from yuxi.services.operation_log_service import log_operation
from yuxi.services.user_identity_service import is_valid_phone_number

# 创建路由器
department = APIRouter(prefix="/departments", tags=["department"])


# =============================================================================
# === 请求和响应模型 ===
# =============================================================================


class DepartmentCreate(BaseModel):
    """创建部门请求"""

    name: str
    description: str | None = None
    # 必需的管理员信息
    admin_uid: str
    admin_password: str
    admin_phone: str | None = None


class DepartmentUpdate(BaseModel):
    """更新部门请求"""

    name: str | None = None
    description: str | None = None


class DepartmentResponse(BaseModel):
    """部门响应"""

    id: int
    name: str
    description: str | None = None
    created_at: str
    user_count: int = 0


class TeamCreate(BaseModel):
    """创建团队请求"""

    name: str
    description: str | None = None


class TeamUpdate(BaseModel):
    """更新团队请求"""

    name: str | None = None
    description: str | None = None


class TeamMembersUpdate(BaseModel):
    """设置团队成员请求（勾选的用户 id 全集）"""

    user_ids: list[int]


class TeamResponse(BaseModel):
    """团队响应"""

    id: int
    department_id: int
    name: str
    description: str | None = None
    is_default: bool
    created_at: str
    user_count: int = 0
    department_name: str | None = None


# =============================================================================
# === 部门管理路由 ===
# =============================================================================


@department.get("", response_model=list[DepartmentResponse])
async def get_departments(current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    """获取所有部门列表（管理员可访问）"""
    dept_repo = DepartmentRepository()
    return await dept_repo.list_with_user_count()


@department.get("/{department_id}", response_model=DepartmentResponse)
async def get_department(
    department_id: int, current_user: User = Depends(get_superadmin_user), db: AsyncSession = Depends(get_db)
):
    """获取指定部门详情"""
    result = await db.execute(select(Department).filter(Department.id == department_id))
    department = result.scalar_one_or_none()

    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部门不存在")

    # 获取部门下用户数量
    user_count_result = await db.execute(
        select(func.count(User.id)).filter(User.department_id == department_id, User.is_deleted == 0)
    )
    user_count = user_count_result.scalar()

    return {**department.to_dict(), "user_count": user_count}


@department.post("", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    department_data: DepartmentCreate,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新部门，同时创建该部门的管理员"""
    dept_repo = DepartmentRepository()
    user_repo = UserRepository()

    # 检查部门名称是否已存在
    if await dept_repo.exists_by_name(department_data.name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="部门名称已存在")

    # 验证管理员 uid 格式
    admin_uid = department_data.admin_uid
    if not re.match(r"^[a-zA-Z0-9_]+$", admin_uid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID只能包含字母、数字和下划线",
        )

    if len(admin_uid) < 3 or len(admin_uid) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID长度必须在3-20个字符之间",
        )

    # 检查 uid 是否已存在
    if await user_repo.exists_by_uid(admin_uid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID已存在",
        )

    # 检查手机号是否已存在（如果提供了）
    admin_phone = department_data.admin_phone
    if admin_phone:
        if not is_valid_phone_number(admin_phone):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号格式不正确")
        if await user_repo.exists_by_phone(admin_phone):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="手机号已存在",
            )

    # 创建部门
    new_department = await dept_repo.create(
        {
            "name": department_data.name,
            "description": department_data.description,
        }
    )

    # 创建默认团队
    await TeamRepository().create(
        new_department.id, "默认团队", "系统自动创建的默认团队", is_default=True
    )

    # 创建管理员用户（落到默认团队）
    hashed_password = AuthUtils.hash_password(department_data.admin_password)
    default_team = await TeamRepository().get_default(new_department.id)
    await user_repo.create(
        {
            "username": admin_uid,
            "uid": admin_uid,
            "phone_number": admin_phone,
            "password_hash": hashed_password,
            "role": "admin",
            "department_id": new_department.id,
            "team_id": default_team.id if default_team else None,
        }
    )

    # 记录操作
    await log_operation(
        db, current_user.id, "创建部门", f"创建部门: {department_data.name}，并创建管理员: {admin_uid}", request
    )

    return {**new_department.to_dict(), "user_count": 1}


@department.put("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: int,
    department_data: DepartmentUpdate,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新部门信息"""
    result = await db.execute(select(Department).filter(Department.id == department_id))
    department = result.scalar_one_or_none()

    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部门不存在")

    # 如果要修改名称，检查新名称是否已存在
    if department_data.name and department_data.name != department.name:
        result = await db.execute(select(Department).filter(Department.name == department_data.name))
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="部门名称已存在")
        department.name = department_data.name

    if department_data.description is not None:
        department.description = department_data.description

    await db.commit()
    await db.refresh(department)

    # 记录操作
    await log_operation(db, current_user.id, "更新部门", f"更新部门: {department.name}", request)

    # 获取部门下用户数量
    user_count_result = await db.execute(
        select(func.count(User.id)).filter(User.department_id == department_id, User.is_deleted == 0)
    )
    user_count = user_count_result.scalar()

    return {**department.to_dict(), "user_count": user_count}


@department.delete("/{department_id}", status_code=status.HTTP_200_OK)
async def delete_department(
    department_id: int,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除部门"""
    # 检查部门是否存在
    result = await db.execute(select(Department).filter(Department.id == department_id))
    department = result.scalar_one_or_none()

    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部门不存在")

    if department.id == 1:  # 默认部门的ID为1
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="默认部门不允许删除")

    department_name = department.name
    result = await db.execute(select(User).filter(User.department_id == department_id))
    department_users = result.scalars().all()

    if department_users:
        default_team = await TeamRepository().get_default(1)
        for user in department_users:
            user.department_id = 1  # 将被删除部门的用户移至默认部门
            user.team_id = default_team.id if default_team else None  # 团队同步迁到默认部门默认团队

    await db.execute(sqlalchemy_delete(APIKey).where(APIKey.department_id == department_id))
    await db.delete(department)
    await db.commit()

    # 记录操作
    if department_users:
        detail = f"删除部门: {department_name}，迁移 {len(department_users)} 个用户到默认部门"
    else:
        detail = f"删除部门: {department_name}"
    await log_operation(db, current_user.id, "删除部门", detail, request)

    return {"success": True, "message": "部门已删除"}


# =============================================================================
# === 团队管理路由 ===
# =============================================================================


def _ensure_team_scope(current_user: User, department_id: int) -> None:
    """普通管理员只能管理本部门团队"""
    if current_user.role == "superadmin":
        return
    if current_user.department_id != department_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只能管理本部门团队",
        )


async def _team_response(team) -> dict:
    data = team.to_dict()
    data["user_count"] = await TeamRepository().count_users(team.id)
    return data


@department.get("/{department_id}/teams", response_model=list[TeamResponse])
async def get_department_teams(
    department_id: int, current_user: User = Depends(get_admin_user)
):
    """获取部门下的团队列表（管理员可访问）"""
    _ensure_team_scope(current_user, department_id)
    teams = await TeamRepository().list_by_department(department_id)
    return [await _team_response(team) for team in teams]


@department.post("/{department_id}/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    department_id: int,
    team_data: TeamCreate,
    current_user: User = Depends(get_admin_user),
):
    """创建团队（默认团队由系统自动创建）"""
    _ensure_team_scope(current_user, department_id)
    name = team_data.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="团队名称不能为空")
    team_repo = TeamRepository()
    teams = await team_repo.list_by_department(department_id)
    if any(t.name == name for t in teams):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="部门下已存在同名团队")
    team = await team_repo.create(department_id, name, team_data.description, is_default=False)
    return await _team_response(team)


@department.put("/{department_id}/teams/{team_id}", response_model=TeamResponse)
async def update_team(
    department_id: int,
    team_id: int,
    team_data: TeamUpdate,
    current_user: User = Depends(get_admin_user),
):
    """更新团队名称/描述"""
    _ensure_team_scope(current_user, department_id)
    team_repo = TeamRepository()
    team = await team_repo.get_by_id(team_id)
    if team is None or team.department_id != department_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="团队不存在")
    data = {}
    if team_data.name is not None:
        name = team_data.name.strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="团队名称不能为空")
        teams = await team_repo.list_by_department(department_id)
        if any(t.name == name and t.id != team_id for t in teams):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="部门下已存在同名团队")
        data["name"] = name
    if team_data.description is not None:
        data["description"] = team_data.description
    if data:
        team = await team_repo.update(team_id, data)
    return await _team_response(team)


@department.put("/{department_id}/teams/{team_id}/members", status_code=status.HTTP_200_OK)
async def update_team_members(
    department_id: int,
    team_id: int,
    payload: TeamMembersUpdate,
    current_user: User = Depends(get_admin_user),
):
    """设置团队成员：勾选的用户加入团队，取消勾选的原成员回到本部门默认团队"""
    _ensure_team_scope(current_user, department_id)
    team_repo = TeamRepository()
    team = await team_repo.get_by_id(team_id)
    if team is None or team.department_id != department_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="团队不存在")
    try:
        await team_repo.set_members(team_id, department_id, payload.user_ids)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"success": True, "message": "团队成员已更新"}


@department.delete("/{department_id}/teams/{team_id}", status_code=status.HTTP_200_OK)
async def delete_team(
    department_id: int,
    team_id: int,
    current_user: User = Depends(get_admin_user),
):
    """删除团队（默认团队拒绝；成员自动迁回默认团队）"""
    _ensure_team_scope(current_user, department_id)
    team_repo = TeamRepository()
    team = await team_repo.get_by_id(team_id)
    if team is None or team.department_id != department_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="团队不存在")
    if team.is_default:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="默认团队不允许删除")
    await team_repo.delete(team_id)
    return {"success": True, "message": "团队已删除"}


# 全局团队列表（供知识库权限面板渲染选项与标签）
team = APIRouter(prefix="/teams", tags=["team"])


@team.get("", response_model=list[TeamResponse])
async def get_all_teams(current_user: User = Depends(get_admin_user)):
    """获取全部团队（含部门名）"""
    teams = await TeamRepository().list_with_department()
    result = []
    for team_dict in teams:
        team_dict["user_count"] = await TeamRepository().count_users(team_dict["id"])
        result.append(team_dict)
    return result
