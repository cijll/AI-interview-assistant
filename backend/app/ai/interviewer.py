from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .model_client import invoke


def mock_opening(target_role: str, difficulty: str) -> str:
    return (
        f"你好，我将围绕“{target_role}”岗位进行{difficulty}难度的模拟面试。"
        "请先用 1 分钟介绍一个你最熟悉的项目，以及你在其中承担的具体工作。"
    )


def mock_followup(answer: str) -> str:
    lowered = answer.lower()
    if any(token in lowered for token in ("%", "提升", "降低", "速度", "性能")):
        return "你刚才提到了性能或指标变化，这个数字是如何测量的？请说明测试环境、优化前后数据，以及你本人负责的部分。"
    if any(token in lowered for token in ("数据库", "mysql", "postgresql", "sqlite", "事务", "索引")):
        return "你提到了数据库设计，为什么选择这个方案？如果数据量和并发继续增长，你会优先优化哪一处？"
    if any(token in lowered for token in ("缓存", "redis", "部署", "接口", "fastapi", "向量")):
        return "你刚才提到这个技术点，能具体讲一次你亲自处理的实现或故障吗？当时有哪些取舍？"
    if len(answer.strip()) < 30:
        return "请把刚才的回答展开成“问题—行动—结果”三部分，并说明你本人完成了哪一部分。"
    return "从你刚才的经历中，我想继续追问一个具体细节：当时为什么这样设计？如果重新做一次，你会改哪里？"


def opening_question(config: dict[str, Any]) -> str:
    role = config["target_role"]
    difficulty = config["difficulty"]
    content = invoke(
        [
            SystemMessage(content="你是严格但友善的模拟面试官，只输出一个自然的开场问题，不要解释规则。"),
            HumanMessage(
                content=f"目标岗位：{role}\n难度：{difficulty}\n简历摘要：{config.get('resume_text', '')[:1500]}"
            ),
        ]
    )
    return content.strip() if content else mock_opening(role, difficulty)


def next_question(
    config: dict[str, Any],
    summary: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    answer: str,
    candidates: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    covered_turn = int(summary.get("covered_turn", 0))
    if covered_turn:
        window = [item for item in recent_messages if int(item.get("turn_no", 0)) > covered_turn]
    else:
        # 第一次摘要前最多只有五轮；保留完整的首批对话，避免过早遗忘。
        window = recent_messages
    recent_dialogue = [
        {"turn_no": item["turn_no"], "role": item["role"], "content": item["content"]}
        for item in window
    ]
    asked = [item["content"] for item in window if item["role"] == "assistant"]
    prompt = (
        f"岗位：{config['target_role']}\n难度：{config['difficulty']}\n"
        f"已知摘要：{summary}\n摘要后的新对话：{recent_dialogue}\n"
        f"用户最新回答：{answer}\n摘要后已问问题：{asked[-8:]}\n题库候选：{candidates or []}"
    )
    content = invoke(
        [
            SystemMessage(
                content=(
                    "你是 AI 模拟面试官。必须抓住用户最新回答中的一个具体技术、数字、经历或模糊点深入追问。"
                    "结合摘要和最近对话，避免重复，下一步只输出一个问题，不要输出评分和多个问题。"
                )
            ),
            HumanMessage(content=prompt),
        ]
    )
    if content:
        return content.strip(), {
            "mode": "bailian",
            "answer_driven": True,
            "question_candidates": candidates or [],
        }
    return mock_followup(answer), {
        "mode": "mock",
        "answer_driven": True,
        "question_candidates": candidates or [],
    }
