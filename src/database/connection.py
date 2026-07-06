import os
import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# ========== 数据库初始化幂等性控制 ==========
_db_initialized = False
_db_initialized_lock = asyncio.Lock()

# ========== 同步数据库连接（兼容旧模块） ==========
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./missions.db")

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=3,
    pool_recycle=1800,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 同步初始化数据库（兼容原有调用方式）
from sqlalchemy import text

def init_database():
    global _db_initialized
    if _db_initialized:
        return
    Base.metadata.create_all(bind=engine, checkfirst=True)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.execute(text("PRAGMA cache_size=-64000"))
        conn.commit()
    _db_initialized = True
    print("数据库表初始化成功")

# 同步获取会话（兼容依赖注入）
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ========== 异步数据库连接（适配异步业务模块） ==========
ASYNC_DATABASE_URL = os.getenv("ASYNC_DATABASE_URL", "sqlite+aiosqlite:///./missions.db")

async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    future=True,
    pool_size=5,
    max_overflow=3,
    pool_recycle=1800,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False}
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# 异步获取会话（异步接口依赖注入用）
async def get_async_db():
    db = AsyncSessionLocal()
    try:
        yield db
    finally:
        await db.close()

# 异步获取会话工厂（供业务模块使用）
def get_async_session_local():
    return AsyncSessionLocal

# 异步初始化数据库
async def init_database_async():
    global _db_initialized
    async with _db_initialized_lock:
        if _db_initialized:
            return
        async with async_engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: Base.metadata.create_all(bind=sync_conn, checkfirst=True))
        _db_initialized = True
        print("异步数据库表初始化成功")

# ========== 公共基类 ==========
Base = declarative_base()

# 导入所有数据模型，确保建表时能识别到表结构
from src.database import models