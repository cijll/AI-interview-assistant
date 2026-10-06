from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import db
from ..auth import create_access_token, get_current_user, hash_password, public_user, verify_password
from ..ai.interviewer import next_question, opening_question
from ..ai.question_bank import ensure_vector_index, search
from ..ai.report import generate_report
from ..ai.report_pdf import create_report_pdf
from ..ai.report_xlsx import create_report_xlsx
from ..ai.resume_parser import ALLOWED_EXTENSIONS, extract_text, structure_resume
from ..ai.secretary import summarize
from ..ai.termination import evaluate
from ..config import settings


router = APIRouter(prefix="/api")


class SessionCreate(BaseModel):
    target_role: str = Field(min_length=1, max_length=100)
    difficulty: str = Field(default="中等", max_length=20)
    question_limit: int = Field(default=10, ge=1, le=30)
    time_limit_seconds: int | None = Field(default=None, ge=60, le=7200)
    focus_skills: list[str] = Field(default_factory=list, max_length=12)
    resume_text: str = Field(default="", max_length=12000)


class AnswerCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class EndRequest(BaseModel):
    reason: str = Field(default="user_requested_end", max_length=100)


class AuthRequest(BaseModel):
    username: str = Field(min_length=2, max_length=40, pattern=r"^[\w\u4e00-\u9fff-]+$")
    password: str = Field(min_length=6, max_length=128)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "backend", "model": settings.model_name}


@router.post("/auth/register")
def register(payload: AuthRequest) -> dict[str, Any]:
    username = payload.username.strip()
    if len(username) < 2:
        raise HTTPException(status_code=422, detail="用户名至少需要 2 个字符")
    user = db.create_user(username, hash_password(payload.password))
    if user is None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": public_user(user)}


@router.post("/auth/login")
def login(payload: AuthRequest) -> dict[str, Any]:
    user = db.get_user_by_username(payload.username.strip())
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": public_user(user)}


@router.get("/auth/me")
def me(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return public_user(current_user)


@router.post("/resumes/parse")
async def parse_resume(
    file: UploadFile = File(...), current_user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    from pathlib import Path

    original_filename = file.filename or "resume"
    suffix = Path(original_filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="仅支持 PDF、DOCX、TXT 或 MD 简历")
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="简历文件不能超过 8 MB")
    target = settings.upload_dir / f"{db.new_id('upload')}{suffix}"
    target.write_bytes(content)
    try:
        parsed = structure_resume(extract_text(target))
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"简历解析失败：{exc}") from exc
    return db.save_resume(current_user["id"], original_filename, str(target), parsed)


@router.post("/sessions")
def create_session(
    payload: SessionCreate, current_user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    data = payload.model_dump()
    data["user_id"] = current_user["id"]
    # 使界面显示的轮次与后端的全局安全上限保持一致。
    data["question_limit"] = min(int(data["question_limit"]), settings.max_turns)
    opening = opening_question(data)
    return db.create_session(data, opening)


@router.get("/sessions")
def sessions(current_user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return db.list_sessions(current_user["id"])


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    session = db.get_session_for_user(session_id, current_user["id"])
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    session["focus_skills"] = db.loads(session.pop("focus_skills_json"), [])
    session["messages"] = db.list_messages(session_id)
    session["memory"] = db.latest_summary(session_id)
    session["report"] = db.latest_report(session_id)
    return session


@router.get("/sessions/{session_id}/messages")
def messages(session_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    if db.get_session_for_user(session_id, current_user["id"]) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return db.list_messages(session_id)


@router.post("/sessions/{session_id}/messages")
def answer(
    session_id: str,
    payload: AnswerCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    session = db.get_session_for_user(session_id, current_user["id"])
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session["status"] != "ACTIVE":
        raise HTTPException(status_code=409, detail="本次面试已经结束")

    question_limit = min(int(session["question_limit"]), settings.max_turns)
    if int(session["turn_count"]) >= question_limit:
        # 兼容修复前已经达到上限、但仍被标记为 ACTIVE 的会话。
        db.update_session(
            session_id,
            status="COMPLETED",
            ended_at=db.now_iso(),
            current_stage="closing",
        )
        raise HTTPException(status_code=409, detail="已达到预设面试轮次，本次面试已自动结束")

    turn_no = int(session["turn_count"]) + 1
    answer_message = db.add_message(session_id, turn_no, "user", payload.content)
    db.update_session(session_id, turn_count=turn_no, current_stage="interview")

    summary = db.latest_summary(session_id)
    compressed = False
    if turn_no % settings.summary_every_turns == 0:
        summary = summarize(db.list_messages(session_id), summary)
        summary = db.save_summary(session_id, turn_no, summary)
        compressed = True

    fresh_session = db.get_session(session_id) or session
    termination = evaluate(fresh_session)
    if termination["can_end_now"]:
        db.update_session(
            session_id,
            status="COMPLETED",
            ended_at=db.now_iso(),
            current_stage="closing",
        )
        return {
            "message": answer_message,
            "memory": {"summary_version": summary.get("version", 0), "compressed": compressed},
            "termination": termination,
        }

    config = {
        "target_role": session["target_role"],
        "difficulty": session["difficulty"],
        "resume_text": session["resume_text"],
    }
    all_messages = db.list_messages(session_id)
    candidates = search(payload.content, session["target_role"], session["difficulty"])
    question, metadata = next_question(config, summary, all_messages, payload.content, candidates)
    next_message = db.add_message(session_id, turn_no, "assistant", question, metadata)
    return {
        "message": next_message,
        "memory": {"summary_version": summary.get("version", 0), "compressed": compressed},
        "termination": termination,
    }


@router.get("/question-bank/search")
def question_bank_search(
    query: str,
    role: str = "Python 后端开发",
    difficulty: str = "中等",
) -> dict[str, Any]:
    return {"items": search(query, role, difficulty), "query": query}


@router.post("/question-bank/vectorize")
def vectorize_question_bank() -> dict[str, Any]:
    count = ensure_vector_index()
    return {"indexed": count, "mode": "chroma_vector" if count else "fallback_only"}


@router.post("/sessions/{session_id}/end")
def end_session(
    session_id: str,
    payload: EndRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    session = db.get_session_for_user(session_id, current_user["id"])
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session["status"] == "ACTIVE":
        db.update_session(session_id, status="COMPLETED", ended_at=db.now_iso(), current_stage="closing")
    return {"session_id": session_id, "status": "COMPLETED", "reason": payload.reason}


@router.post("/sessions/{session_id}/report")
def report(session_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    session = db.get_session_for_user(session_id, current_user["id"])
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session["status"] == "ACTIVE":
        raise HTTPException(status_code=409, detail="请先结束面试再生成报告")
    report_data = generate_report(db.list_messages(session_id), session)
    return db.save_report(session_id, report_data)


@router.get("/sessions/{session_id}/report.pdf")
def report_pdf(session_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> FileResponse:
    session = db.get_session_for_user(session_id, current_user["id"])
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session["status"] == "ACTIVE":
        raise HTTPException(status_code=409, detail="请先结束面试再导出报告")
    saved_report = db.latest_report(session_id)
    if saved_report is None:
        raise HTTPException(status_code=404, detail="请先生成评估报告")
    output_path = settings.export_dir / f"{session_id}_report_v{saved_report['version']}.pdf"
    create_report_pdf(output_path, saved_report, session)
    return FileResponse(
        output_path,
        media_type="application/pdf",
        filename=f"ai_interview_report_{session_id}.pdf",
    )


@router.get("/sessions/{session_id}/report.xlsx")
def report_xlsx(session_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> FileResponse:
    session = db.get_session_for_user(session_id, current_user["id"])
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session["status"] == "ACTIVE":
        raise HTTPException(status_code=409, detail="请先结束面试再导出报告")
    saved_report = db.latest_report(session_id)
    if saved_report is None:
        raise HTTPException(status_code=404, detail="请先生成评估报告")
    output_path = settings.export_dir / f"{session_id}_report_v{saved_report['version']}.xlsx"
    try:
        create_report_xlsx(output_path, saved_report, session)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"ai_interview_report_{session_id}.xlsx",
    )


@router.get("/reports/{report_id}")
def get_report(report_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute(
            """SELECT reports.* FROM reports JOIN sessions ON sessions.id = reports.session_id
            WHERE reports.id = ? AND sessions.user_id = ?""",
            (report_id, current_user["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    data = db.loads(row["report_json"], {})
    data.update({"id": row["id"], "session_id": row["session_id"], "version": row["version"]})
    return data


@router.get("/history")
def history(current_user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    result = []
    for session in db.list_sessions(current_user["id"]):
        item = dict(session)
        item["report"] = db.latest_report(session["id"])
        result.append(item)
    return result


@router.delete("/sessions/{session_id}")
def delete(session_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if not db.delete_session_for_user(session_id, current_user["id"]):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"deleted": True, "session_id": session_id}
