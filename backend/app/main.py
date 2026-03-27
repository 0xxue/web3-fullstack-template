import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import settings
from app.database import engine, Base, AsyncSessionLocal
from app.models.admin import Admin
from app.core.security import hash_password
from app.middleware.audit import AuditMiddleware
from app.api import auth, admin, system_settings, audit_log, address, wallet, deposit, collection, multisig_wallet, proposal, payout, transfer, notifications
from app.core.telegram import notifier
from app.services.deposit_scanner import scanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def init_default_admin():
    """首次启动时创建默认超级管理员"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Admin).where(Admin.username == settings.DEFAULT_ADMIN_USERNAME)
        )
        if result.scalar_one_or_none() is None:
            admin_user = Admin(
                username=settings.DEFAULT_ADMIN_USERNAME,
                password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
                role="super_admin",
                is_active=True,
            )
            session.add(admin_user)
            await session.commit()
            logger.info(f"默认超级管理员已创建: {settings.DEFAULT_ADMIN_USERNAME}")
        else:
            logger.info("默认超级管理员已存在，跳过创建")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时：创建表 + 初始化管理员
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await init_default_admin()
    await notifier.start_polling()
    await scanner.start()
    logger.info(f"🚀 {settings.APP_NAME} 启动完成")
    yield
    # 关闭时
    await scanner.stop()
    await notifier.stop_polling()
    await engine.dispose()
    logger.info("服务已关闭")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 请求日志
app.add_middleware(AuditMiddleware)

# 路由
app.include_router(auth.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(system_settings.router, prefix="/api")
app.include_router(audit_log.router, prefix="/api")
app.include_router(address.router, prefix="/api")
app.include_router(wallet.router, prefix="/api")
app.include_router(deposit.router, prefix="/api")
app.include_router(collection.router, prefix="/api")
app.include_router(multisig_wallet.router, prefix="/api")
app.include_router(proposal.router, prefix="/api")
app.include_router(payout.router, prefix="/api")
app.include_router(transfer.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "app": settings.APP_NAME}
