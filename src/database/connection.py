"""统一数据库连接入口。

生产运行默认由 ``config/gateway.yaml`` 切换到 MongoDB。SQLite/SQLAlchemy
仍保留为历史数据迁移来源，以及显式 ``database.backend=sqlite`` 的回退模式。
"""
import asyncio
import os
import threading
from datetime import datetime
from typing import Any, Callable, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

try:
    from pymongo import ASCENDING, DESCENDING, MongoClient
except ImportError:  # pragma: no cover - Ubuntu 安装检查会给出明确错误
    ASCENDING = 1
    DESCENDING = -1
    MongoClient = None


Base = declarative_base()

# SQLite 连接仅作为显式回退和迁移/专项测试使用。
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./missions.db")
ASYNC_DATABASE_URL = os.getenv("ASYNC_DATABASE_URL", "sqlite+aiosqlite:///./missions.db")

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=3,
    pool_recycle=1800,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False},
)
_SQLiteSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    future=True,
    pool_size=5,
    max_overflow=3,
    pool_recycle=1800,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False},
)
_SQLiteAsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

_database_backend = os.getenv("DATABASE_BACKEND", "sqlite").strip().lower()
_mongo_config: dict[str, Any] = {}
_mongo_client = None
_mongo_database = None
_db_initialized = False
_db_initialized_backend: Optional[str] = None
_db_initialized_lock = asyncio.Lock()
_mongo_init_lock = threading.Lock()


def _mongodb_uri(config: dict[str, Any]) -> str:
    mongo = config.get("mongodb", {}) if isinstance(config, dict) else {}
    explicit_uri = os.getenv("MONGODB_URI") or mongo.get("uri")
    if explicit_uri:
        return str(explicit_uri)

    host = os.getenv("MONGODB_HOST", str(mongo.get("host", "127.0.0.1")))
    port = int(os.getenv("MONGODB_PORT", str(mongo.get("port", 27017))))
    username = os.getenv("MONGODB_USERNAME", str(mongo.get("username", "")))
    password = os.getenv("MONGODB_PASSWORD", str(mongo.get("password", "")))
    auth_source = os.getenv("MONGODB_AUTH_SOURCE", str(mongo.get("auth_source", "admin")))
    credentials = ""
    if username:
        credentials = f"{quote_plus(username)}:{quote_plus(password)}@"
    return f"mongodb://{credentials}{host}:{port}/?authSource={quote_plus(auth_source)}"


def configure_database(config: Optional[dict[str, Any]] = None) -> str:
    """应用 YAML/环境变量中的数据库配置，返回最终后端名称。"""
    global _database_backend, _mongo_config, _db_initialized, _db_initialized_backend
    global _mongo_client, _mongo_database

    config = config or {}
    backend = os.getenv("DATABASE_BACKEND", str(config.get("backend", "sqlite")))
    backend = backend.strip().lower()
    if backend in ("mongo", "mongodb"):
        backend = "mongodb"
    elif backend != "sqlite":
        raise ValueError(f"Unsupported database backend: {backend}")

    if _mongo_client is not None and backend != _database_backend:
        _mongo_client.close()
        _mongo_client = None
        _mongo_database = None

    _database_backend = backend
    _mongo_config = dict(config)
    _db_initialized = False
    _db_initialized_backend = None
    return backend


def get_database_backend() -> str:
    return _database_backend


def is_mongodb() -> bool:
    return _database_backend == "mongodb"


def _create_mongo_client():
    global _mongo_client, _mongo_database
    if MongoClient is None:
        raise RuntimeError(
            "MongoDB backend requires pymongo; install requirements-ubuntu.txt"
        )
    if _mongo_client is not None:
        return
    mongo = _mongo_config.get("mongodb", {}) if isinstance(_mongo_config, dict) else {}
    timeout_ms = int(mongo.get("connect_timeout_ms", 5000))
    database_name = os.getenv("MONGODB_DATABASE", str(mongo.get("database", "edge_gateway")))
    _mongo_client = MongoClient(
        _mongodb_uri(_mongo_config),
        serverSelectionTimeoutMS=timeout_ms,
        connectTimeoutMS=timeout_ms,
        appname="edge-gateway",
    )
    _mongo_database = _mongo_client[database_name]


def get_mongo_database():
    if not is_mongodb():
        raise RuntimeError("MongoDB database requested while backend is not mongodb")
    if _mongo_database is None:
        with _mongo_init_lock:
            _create_mongo_client()
    return _mongo_database


def _ensure_mongo_indexes(database) -> None:
    index_specs = {
        "missions": [
            ([('id', ASCENDING)], {'unique': True, 'sparse': True}),
            ([('name', ASCENDING), ('created_at', DESCENDING)], {}),
            ([('status', ASCENDING), ('created_at', DESCENDING)], {}),
            ([('name', ASCENDING), ('execution_conditions.breakpoint_updated_at', DESCENDING)], {}),
        ],
        "mission_platform_snapshot": [
            ([('gateway_sn', ASCENDING)], {'unique': True}),
            ([('updated_at', DESCENDING)], {}),
        ],
        "waypoints": [([('mission_id', ASCENDING), ('sequence', ASCENDING)], {})],
        "device_state": [([('gateway_sn', ASCENDING)], {'unique': True})],
        "device_binding": [([('gateway_sn', ASCENDING)], {'unique': True})],
        "alarms": [
            ([('alarm_id', ASCENDING)], {'unique': True}),
            ([('gateway_sn', ASCENDING), ('create_time', DESCENDING)], {}),
        ],
        "telemetry_data": [([('device_sn', ASCENDING), ('timestamp', DESCENDING)], {})],
        "operation_record": [
            ([('operation_id', ASCENDING)], {'unique': True}),
            ([('device_sn', ASCENDING), ('start_time', DESCENDING)], {}),
        ],
        "media_files": [
            ([('id', ASCENDING)], {'unique': True, 'sparse': True}),
            ([('device_sn', ASCENDING), ('upload_status', ASCENDING), ('created_at', ASCENDING)], {}),
        ],
    }
    for collection_name, specs in index_specs.items():
        collection = database[collection_name]
        for keys, options in specs:
            collection.create_index(keys, **options)


class MongoSyncSession:
    is_mongo = True

    def __init__(self):
        self.database = get_mongo_database()

    def collection(self, name: str):
        return self.database[name]

    def run(self, callback: Callable[[Any], Any]):
        return callback(self.database)

    def commit(self):
        return None

    def rollback(self):
        return None

    def flush(self):
        return None

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class MongoAsyncSession:
    is_mongo = True

    def __init__(self):
        self.database = get_mongo_database()

    async def run(self, callback: Callable[[Any], Any]):
        return await asyncio.to_thread(callback, self.database)

    def begin(self):
        """兼容 ``async with session.begin()``；单机 MongoDB 使用单文档原子写。"""
        return self

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def flush(self):
        return None

    async def close(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
        return False


class _SessionFactoryProxy:
    def __init__(self, asynchronous: bool):
        self.asynchronous = asynchronous

    def __call__(self):
        if is_mongodb():
            return MongoAsyncSession() if self.asynchronous else MongoSyncSession()
        return _SQLiteAsyncSessionLocal() if self.asynchronous else _SQLiteSessionLocal()


# 稳定代理对象可安全地被 ``from ... import SessionLocal`` 提前导入；配置切换后
# 调用仍会落到当前后端。
SessionLocal = _SessionFactoryProxy(asynchronous=False)
AsyncSessionLocal = _SessionFactoryProxy(asynchronous=True)


def is_mongo_session(session: Any) -> bool:
    return bool(getattr(session, "is_mongo", False))


def mongo_model_from_document(model_class, document: Optional[dict]):
    if not document:
        return None
    column_names = {column.name for column in model_class.__table__.columns}
    values = {key: value for key, value in document.items() if key in column_names}
    return model_class(**values)


def mongo_expired_filter(field: str, age_seconds: int, now_value: Optional[float] = None) -> dict:
    now_value = datetime.now().timestamp() if now_value is None else now_value
    seconds_cutoff = int(now_value - age_seconds)
    milliseconds_cutoff = int((now_value - age_seconds) * 1000)
    return {
        "$or": [
            {field: {"$lt": seconds_cutoff}},
            {field: {"$gte": 1_000_000_000_000, "$lt": milliseconds_cutoff}},
        ]
    }


def init_database():
    global _db_initialized, _db_initialized_backend
    if _db_initialized and _db_initialized_backend == _database_backend:
        return
    if is_mongodb():
        database = get_mongo_database()
        database.command("ping")
        _ensure_mongo_indexes(database)
        print(f"MongoDB数据库初始化成功: {database.name}")
    else:
        Base.metadata.create_all(bind=engine, checkfirst=True)
        with engine.connect() as connection:
            connection.execute(text("PRAGMA journal_mode=WAL"))
            connection.execute(text("PRAGMA synchronous=NORMAL"))
            connection.execute(text("PRAGMA cache_size=-64000"))
            connection.commit()
        print("SQLite数据库表初始化成功")
    _db_initialized = True
    _db_initialized_backend = _database_backend


async def init_database_async():
    global _db_initialized, _db_initialized_backend
    async with _db_initialized_lock:
        if _db_initialized and _db_initialized_backend == _database_backend:
            return
        if is_mongodb():
            await asyncio.to_thread(init_database)
        else:
            async with async_engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: Base.metadata.create_all(
                        bind=sync_connection, checkfirst=True
                    )
                )
            _db_initialized = True
            _db_initialized_backend = _database_backend
            print("异步SQLite数据库表初始化成功")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    db = AsyncSessionLocal()
    try:
        yield db
    finally:
        await db.close()


def get_async_session_local():
    return AsyncSessionLocal


def close_database() -> None:
    global _mongo_client, _mongo_database, _db_initialized, _db_initialized_backend
    if _mongo_client is not None:
        _mongo_client.close()
        _mongo_client = None
        _mongo_database = None
    _db_initialized = False
    _db_initialized_backend = None


# 导入全部 SQLAlchemy 模型，供 SQLite 回退模式建表。
from src.database import models  # noqa: E402,F401
