"""
agents/base.py - Reusable tool-calling agent loop.
Reflection, function schema generation, tool execution, Pydantic validation, and audit logging.
"""
from __future__ import annotations
import inspect, json, logging, uuid
from typing import Any, Callable, get_type_hints
from pydantic import BaseModel
from config import settings
from llm_client import get_client
from tools.audit_log import append_entry

logger = logging.getLogger(__name__)

def function_to_schema(fn: Callable) -> dict:
    """Build OpenAI function schema from function type hints and docstring."""
    sig, hints = inspect.signature(fn), get_type_hints(fn)
    type_map = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
    props, req = {}, []
    for p_name, param in sig.parameters.items():
        if p_name in ("self", "cls"):
            continue
        props[p_name] = {"type": type_map.get(hints.get(p_name, str), "string")}
        if param.default is inspect.Parameter.empty:
            req.append(p_name)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": (inspect.getdoc(fn) or fn.__name__).strip().split("\n")[0],
            "parameters": {"type": "object", "properties": props, "required": req},
        },
    }

def run_agent(
    name: str,
    instruction: str,
    tools: list[Callable],
    user_input: str,
    output_schema: type[BaseModel] | None = None,
    max_steps: int = 8,
) -> dict:
    """Execute tool-calling agent loop up to max_steps; return {run_id, agent, content, data, status, steps}."""
    run_id = str(uuid.uuid4())[:8]
    client = get_client()
    tool_map = {fn.__name__: fn for fn in tools}
    tool_schemas = [function_to_schema(fn) for fn in tools] if tools else None
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": user_input},
    ]

    content, step = "", 0
    for step in range(1, max_steps + 1):
        kwargs: dict[str, Any] = {"model": settings.llm_model, "messages": messages}
        if tool_schemas:
            kwargs["tools"] = tool_schemas
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message
        content = choice.content or ""
        tool_calls = getattr(choice, "tool_calls", None)
        if not tool_calls:
            break

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        assistant_msg["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in tool_calls
        ]
        messages.append(assistant_msg)

        for tc in tool_calls:
            fn_name, args = tc.function.name, {}
            try:
                args = json.loads(tc.function.arguments or "{}")
                if fn_name not in tool_map:
                    raise KeyError(f"Tool '{fn_name}' not available")
                res, outcome = tool_map[fn_name](**args), "success"
            except Exception as e:
                res, outcome = f"Tool execution error: {e}", "failure"

            append_entry(
                agent=name, action=f"tool_call:{fn_name}",
                inputs={"arguments": args, "run_id": run_id},
                outputs={"result": str(res)[:500]}, sources=[],
                outcome=outcome, notes=f"run_id={run_id}",
            )
            messages.append({
                "role": "tool", "tool_call_id": tc.id,
                "content": json.dumps(res, default=str) if isinstance(res, (dict, list)) else str(res),
            })

    parsed_data = None
    if output_schema and content:
        for attempt in range(2):
            try:
                clean = content.strip()
                if "```" in clean:
                    clean = clean.split("```")[1].removeprefix("json").strip()
                data = json.loads(clean)
                if isinstance(data, dict) and output_schema.__name__ in data:
                    data = data[output_schema.__name__]
                parsed_data = output_schema.model_validate(data).model_dump()
                break
            except Exception as e:
                if attempt == 0:
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": f"Schema validation error: {e}. Return raw JSON matching schema."})
                    resp = client.chat.completions.create(model=settings.llm_model, messages=messages)
                    content = resp.choices[0].message.content or ""
                else:
                    logger.warning("Agent %s failed schema validation: %s", name, e)

    status = "success" if (not output_schema or parsed_data is not None) else "partial"
    append_entry(
        agent=name, action="final_answer",
        inputs={"user_input": user_input, "run_id": run_id},
        outputs={"content": content[:500], "data": parsed_data}, sources=[],
        outcome=status, notes=f"run_id={run_id}",
    )
    return {"run_id": run_id, "agent": name, "content": content, "data": parsed_data, "status": status, "steps": step}
