"""团队数据访问层 - Repository"""

from typing import Any

from sqlalchemy import delete as sqlalchemy_delete
from sqlalchemy import func, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, Team, User
from yuxi.storage.postgres.models_knowledge import KnowledgeBasePermission


class TeamRepository:
    """团队数据访问层"""

    async def get_by_id(self, id: int) -> Team | None:
        """根据 ID 获取团队"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(Team).where(Team.id == id))
            return result.scalar_one_or_none()

    async def list_by_department(self, department_id: int) -> list[Team]:
        """获取部门下的所有团队（默认团队排最前）"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(Team)
                .where(Team.department_id == department_id)
                .order_by(Team.is_default.desc(), Team.created_at.desc())
            )
            return list(result.scalars().all())

    async def get_default(self, department_id: int) -> Team | None:
        """获取部门的默认团队"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(Team).where(Team.department_id == department_id, Team.is_default.is_(True))
            )
            return result.scalar_one_or_none()

    async def list_with_department(self) -> list[dict[str, Any]]:
        """获取全部团队，附带部门名（供权限面板渲染选项与标签）"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(Team, Department.name).join(Department, Team.department_id == Department.id)
            )
            teams = []
            for team, dept_name in result.all():
                team_dict = team.to_dict()
                team_dict["department_name"] = dept_name
                teams.append(team_dict)
            return teams

    async def create(
        self, department_id: int, name: str, description: str | None = None, is_default: bool = False
    ) -> Team:
        """创建团队；若部门尚无可设为默认团队，首个团队自动成为默认团队"""
        async with pg_manager.get_async_session_context() as session:
            if not is_default:
                default_exists = (
                    await session.execute(
                        select(Team.id).where(Team.department_id == department_id, Team.is_default.is_(True))
                    )
                ).scalar_one_or_none()
                if default_exists is None:
                    is_default = True
            team = Team(department_id=department_id, name=name, description=description, is_default=is_default)
            session.add(team)
            return team

    async def update(self, id: int, data: dict[str, Any]) -> Team | None:
        """更新团队名称/描述（不迁移部门、不改默认标记）"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(Team).where(Team.id == id))
            team = result.scalar_one_or_none()
            if team is None:
                return None
            for key, value in data.items():
                if key not in {"name", "description"}:
                    continue
                setattr(team, key, value)
            return team

    async def delete(self, id: int) -> bool:
        """删除团队：默认团队拒绝；其余团队先将其成员迁回本部门默认团队再删除"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(Team).where(Team.id == id))
            team = result.scalar_one_or_none()
            if team is None:
                return False
            if team.is_default:
                raise ValueError("默认团队不允许删除")
            default_team = (
                await session.execute(
                    select(Team).where(Team.department_id == team.department_id, Team.is_default.is_(True))
                )
            ).scalar_one_or_none()
            if default_team is not None:
                await session.execute(
                    update(User).where(User.team_id == id).values(team_id=default_team.id)
                )
            # 清理该团队遗留的知识库授权，避免面板出现失效对象
            await session.execute(
                sqlalchemy_delete(KnowledgeBasePermission).where(
                    KnowledgeBasePermission.subject_type == "team",
                    KnowledgeBasePermission.subject_id == str(id),
                )
            )
            await session.delete(team)
            return True

    async def set_members(self, team_id: int, department_id: int, user_ids: list[int]) -> None:
        """按勾选结果重设团队成员：勾选的用户加入该团队，取消勾选的原成员迁回本部门默认团队

        默认团队作为「未分配用户兜底桶」：对它操作时取消勾选不改变归属（本就属于默认团队）。
        user_ids 必须全部属于该部门，防止跨部门把用户塞进错误团队。
        """
        async with pg_manager.get_async_session_context() as session:
            team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
            if team is None or team.department_id != department_id:
                raise ValueError("团队不存在或不属于该部门")
            user_id_set = {int(uid) for uid in user_ids}
            # 目标用户必须都属于该部门
            if user_id_set:
                dept_count = (
                    await session.execute(
                        select(func.count(User.id)).where(
                            User.id.in_(user_id_set),
                            User.department_id == department_id,
                            User.is_deleted == 0,
                        )
                    )
                ).scalar()
                if dept_count != len(user_id_set):
                    raise ValueError("存在不属于该部门的用户")
                await session.execute(update(User).where(User.id.in_(user_id_set)).values(team_id=team.id))
            # 本团队其余原成员迁回本部门默认团队（默认团队即兜底桶，无需处理）
            if not team.is_default:
                default_team = (
                    await session.execute(
                        select(Team).where(Team.department_id == department_id, Team.is_default.is_(True))
                    )
                ).scalar_one_or_none()
                if default_team is not None:
                    revert = update(User).where(User.team_id == team.id)
                    if user_id_set:
                        revert = revert.where(User.id.not_in(user_id_set))
                    await session.execute(revert.values(team_id=default_team.id))

    async def count_users(self, team_id: int) -> int:
        """统计团队用户数量"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count(User.id)).where(User.team_id == team_id, User.is_deleted == 0)
            )
            return result.scalar() or 0
