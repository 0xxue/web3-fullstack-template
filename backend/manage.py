"""
管理员命令行工具 — 仅限程序员使用

用法:
  python manage.py create-admin <username> <password> <role>
  python manage.py set-super-admin <username>
  python manage.py bind-google <username> <google_email>
  python manage.py reset-password <username> <new_password>
  python manage.py list-admins
"""
import sys
import asyncio

from sqlalchemy import select
from app.database import AsyncSessionLocal, engine
from app.models.admin import Admin
from app.core.security import hash_password


async def create_admin(username: str, password: str, role: str):
    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(Admin).where(Admin.username == username))
        if existing.scalar_one_or_none():
            print(f"错误: 用户名 '{username}' 已存在")
            return
        admin = Admin(
            username=username,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )
        session.add(admin)
        await session.commit()
        print(f"管理员已创建: {username} (角色: {role})")


async def set_super_admin(username: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Admin).where(Admin.username == username))
        admin = result.scalar_one_or_none()
        if admin is None:
            print(f"错误: 用户 '{username}' 不存在")
            return
        admin.role = "super_admin"
        session.add(admin)
        await session.commit()
        print(f"已将 '{username}' 设为超级管理员")


async def bind_google(username: str, google_email: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Admin).where(Admin.username == username))
        admin = result.scalar_one_or_none()
        if admin is None:
            print(f"错误: 用户 '{username}' 不存在")
            return
        admin.google_email = google_email
        session.add(admin)
        await session.commit()
        print(f"已将 Google 邮箱 '{google_email}' 绑定到 '{username}'")


async def reset_password(username: str, new_password: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Admin).where(Admin.username == username))
        admin = result.scalar_one_or_none()
        if admin is None:
            print(f"错误: 用户 '{username}' 不存在")
            return
        admin.password_hash = hash_password(new_password)
        session.add(admin)
        await session.commit()
        print(f"已重置 '{username}' 的密码")


async def list_admins():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Admin).order_by(Admin.id))
        admins = result.scalars().all()
        if not admins:
            print("暂无管理员")
            return
        print(f"{'ID':<5} {'用户名':<20} {'角色':<15} {'Google邮箱':<30} {'状态':<8}")
        print("-" * 80)
        for a in admins:
            status = "启用" if a.is_active else "禁用"
            email = a.google_email or "-"
            print(f"{a.id:<5} {a.username:<20} {a.role:<15} {email:<30} {status:<8}")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    try:
        if cmd == "create-admin" and len(sys.argv) == 5:
            role = sys.argv[4]
            if role not in ("super_admin", "operator", "signer", "viewer"):
                print("错误: 角色必须是 super_admin / operator / signer / viewer")
                return
            await create_admin(sys.argv[2], sys.argv[3], role)
        elif cmd == "set-super-admin" and len(sys.argv) == 3:
            await set_super_admin(sys.argv[2])
        elif cmd == "bind-google" and len(sys.argv) == 4:
            await bind_google(sys.argv[2], sys.argv[3])
        elif cmd == "reset-password" and len(sys.argv) == 4:
            await reset_password(sys.argv[2], sys.argv[3])
        elif cmd == "list-admins":
            await list_admins()
        else:
            print(__doc__)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
