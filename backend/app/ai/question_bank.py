from __future__ import annotations

import hashlib
import re
from typing import Any

from langchain_openai import OpenAIEmbeddings

from ..config import settings


SEED_QUESTIONS = [
    {
        "id": "python-001",
        "question": "Python 中可变对象和不可变对象有什么区别？在实际项目中踩过什么坑？",
        "role": "Python 后端开发",
        "skill": "Python",
        "difficulty": "基础",
        "tags": ["Python", "可变对象", "基础"],
        "followups": ["函数默认参数为什么不建议使用可变对象？"],
        "rubric": ["解释对象可变性", "给出实际例子"],
    },
    {
        "id": "fastapi-001",
        "question": "FastAPI 的依赖注入解决了什么问题？你在哪些场景使用过？",
        "role": "Python 后端开发",
        "skill": "FastAPI",
        "difficulty": "中等",
        "tags": ["FastAPI", "接口", "依赖注入"],
        "followups": ["如何在依赖中处理数据库会话和鉴权？"],
        "rubric": ["理解依赖复用", "能结合项目说明"],
    },
    {
        "id": "database-001",
        "question": "你如何判断一个数据库索引是否真正提升了查询性能？",
        "role": "Python 后端开发",
        "skill": "数据库",
        "difficulty": "中等",
        "tags": ["数据库", "索引", "性能"],
        "followups": ["索引可能在哪些情况下反而拖慢系统？"],
        "rubric": ["会使用执行计划", "考虑写入成本和选择性"],
    },
    {
        "id": "redis-001",
        "question": "缓存击穿、缓存穿透和缓存雪崩有什么区别？",
        "role": "Python 后端开发",
        "skill": "Redis",
        "difficulty": "中等",
        "tags": ["Redis", "缓存", "高并发"],
        "followups": ["如果热点 Key 突然失效，你会怎样保护数据库？"],
        "rubric": ["区分三类问题", "给出工程解决方案"],
    },
    {
        "id": "rag-001",
        "question": "RAG 系统中如何选择文本切分大小和重叠长度？",
        "role": "AI 应用开发",
        "skill": "RAG",
        "difficulty": "中等",
        "tags": ["RAG", "向量检索", "Chunk"],
        "followups": ["如何评价召回结果是否相关？"],
        "rubric": ["解释上下文完整性", "考虑 Token 成本和召回率"],
    },
    {
        "id": "system-001",
        "question": "如果接口流量突然增长十倍，你会先观察哪些指标并如何定位瓶颈？",
        "role": "后端开发",
        "skill": "系统设计",
        "difficulty": "困难",
        "tags": ["并发", "性能", "系统设计"],
        "followups": ["怎样区分应用、数据库和网络瓶颈？"],
        "rubric": ["有监控思路", "能够分层定位"],
    },
]


def seed(conn: Any) -> None:
    for item in SEED_QUESTIONS:
        conn.execute(
            """
            INSERT OR IGNORE INTO question_bank
            (id, question, role, skill, difficulty, question_type, tags_json,
             followups_json, rubric_json, reference_answer, source, created_at)
            VALUES (?, ?, ?, ?, ?, 'technical', ?, ?, ?, '', 'seed', datetime('now'))
            """,
            (
                item["id"],
                item["question"],
                item["role"],
                item["skill"],
                item["difficulty"],
                __import__("json").dumps(item["tags"], ensure_ascii=False),
                __import__("json").dumps(item["followups"], ensure_ascii=False),
                __import__("json").dumps(item["rubric"], ensure_ascii=False),
            ),
        )


def _tokens(text: str) -> set[str]:
    latin = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]*", text.lower())
    chinese = [
        word
        for word in (
            "数据库",
            "缓存",
            "接口",
            "性能",
            "并发",
            "索引",
            "事务",
            "向量检索",
            "系统设计",
            "依赖注入",
            "项目",
            "测试",
        )
        if word in text
    ]
    return set(latin + chinese)


def lexical_search(answer: str, role: str, difficulty: str, limit: int = 4) -> list[dict[str, Any]]:
    from .. import db

    query_tokens = _tokens(answer + " " + role)
    with db.connection() as conn:
        seed(conn)
        rows = conn.execute("SELECT * FROM question_bank").fetchall()
    candidates = []
    for row in rows:
        tags = db.loads(row["tags_json"], [])
        document = f"{row['question']} {row['role']} {row['skill']} {' '.join(tags)}"
        overlap = len(query_tokens & _tokens(document))
        role_bonus = 2 if row["role"] in role or role in row["role"] else 0
        difficulty_bonus = 1 if row["difficulty"] == difficulty else 0
        candidates.append(
            {
                "id": row["id"],
                "question": row["question"],
                "skill": row["skill"],
                "difficulty": row["difficulty"],
                "followups": db.loads(row["followups_json"], []),
                "rubric": db.loads(row["rubric_json"], []),
                "score": overlap + role_bonus + difficulty_bonus,
                "retrieval_mode": "metadata_and_keyword",
            }
        )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:limit]


def _embedding_client() -> OpenAIEmbeddings | None:
    if not settings.dashscope_api_key:
        return None
    return OpenAIEmbeddings(
        model="text-embedding-v4",
        api_key=settings.dashscope_api_key,
        base_url=settings.bailian_base_url,
        check_embedding_ctx_length=False,
    )


def ensure_vector_index() -> int:
    try:
        import chromadb
    except ImportError:
        return 0
    embeddings = _embedding_client()
    if embeddings is None:
        return 0
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    collection = client.get_or_create_collection("interview_questions")
    existing = collection.count()
    if existing >= len(SEED_QUESTIONS):
        return existing
    texts = [
        f"{item['role']} {item['difficulty']} {item['skill']} {' '.join(item['tags'])} {item['question']}"
        for item in SEED_QUESTIONS
    ]
    vectors = embeddings.embed_documents(texts)
    collection.upsert(
        ids=[item["id"] for item in SEED_QUESTIONS],
        documents=texts,
        embeddings=vectors,
        metadatas=[
            {"role": item["role"], "skill": item["skill"], "difficulty": item["difficulty"]}
            for item in SEED_QUESTIONS
        ],
    )
    return collection.count()


def vector_search(answer: str, limit: int = 4) -> list[dict[str, Any]]:
    try:
        import chromadb
    except ImportError:
        return []
    embeddings = _embedding_client()
    if embeddings is None:
        return []
    try:
        ensure_vector_index()
        client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        collection = client.get_collection("interview_questions")
        result = collection.query(
            query_embeddings=[embeddings.embed_query(answer)],
            n_results=min(limit, collection.count()),
            include=["metadatas", "documents", "distances"],
        )
        output = []
        for question_id, document, metadata, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            item = next(seed_item for seed_item in SEED_QUESTIONS if seed_item["id"] == question_id)
            output.append(
                {
                    **item,
                    "score": round(1 / (1 + float(distance)), 4),
                    "retrieval_mode": "chroma_vector",
                    "document_hash": hashlib.sha256(document.encode()).hexdigest()[:12],
                    "metadata": metadata,
                }
            )
        return output
    except Exception:
        return []


def search(answer: str, role: str, difficulty: str, limit: int = 4) -> list[dict[str, Any]]:
    return vector_search(answer, limit) or lexical_search(answer, role, difficulty, limit)
