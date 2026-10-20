"""
Unit tests for agents/base.py using mock LLM.
Proves:
1. Tool call runs and result is fed back to model.
2. Max steps is strictly respected.
3. Audit entries are written for every tool call and final answer.
4. Output schema validation and self-correction loop.
"""
from __future__ import annotations
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from pydantic import BaseModel

from agents.base import run_agent, function_to_schema
from tools.audit_log import clear_log, get_all_entries


class SampleOutput(BaseModel):
    summary: str
    ticket_count: int


def add_numbers(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b


def fail_tool() -> str:
    """A tool that always raises an error."""
    raise ValueError("Database connection failed")


class TestBaseAgentLoop(unittest.TestCase):
    def setUp(self):
        clear_log()

    def test_schema_generator(self):
        schema = function_to_schema(add_numbers)
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "add_numbers")
        self.assertEqual(schema["function"]["parameters"]["properties"]["a"]["type"], "integer")
        self.assertIn("a", schema["function"]["parameters"]["required"])

    @patch("agents.base.get_client")
    def test_tool_call_execution_and_feedback(self, mock_get_client):
        """Test tool is executed, result is passed back to model in next message, and audit log records it."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc = SimpleNamespace(
            id="call_123",
            type="function",
            function=SimpleNamespace(name="add_numbers", arguments=json.dumps({"a": 5, "b": 7}))
        )
        resp1 = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Let me add", tool_calls=[tc]))])
        resp2 = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="The sum is 12", tool_calls=None))])

        mock_client.chat.completions.create.side_effect = [resp1, resp2]

        result = run_agent(
            name="CalculatorAgent",
            instruction="You add numbers.",
            tools=[add_numbers],
            user_input="Add 5 and 7",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["content"], "The sum is 12")
        self.assertEqual(result["steps"], 2)

        # Verify second LLM call received tool result
        calls = mock_client.chat.completions.create.call_args_list
        second_call_messages = calls[1].kwargs["messages"]
        tool_msg = next(m for m in second_call_messages if m.get("role") == "tool")
        self.assertEqual(tool_msg["tool_call_id"], "call_123")
        self.assertEqual(tool_msg["content"], "12")

        # Verify audit log recorded tool call and final answer
        entries = get_all_entries()
        tool_entries = [e for e in entries if e["action"] == "tool_call:add_numbers"]
        final_entries = [e for e in entries if e["action"] == "final_answer"]

        self.assertEqual(len(tool_entries), 1)
        self.assertEqual(tool_entries[0]["outcome"], "success")
        self.assertEqual(tool_entries[0]["inputs"]["arguments"], {"a": 5, "b": 7})
        self.assertEqual(tool_entries[0]["outputs"]["result"], "12")

        self.assertEqual(len(final_entries), 1)
        self.assertEqual(final_entries[0]["outcome"], "success")

    @patch("agents.base.get_client")
    def test_tool_error_fed_back_not_swallowed(self, mock_get_client):
        """Test tool error is recorded as failure and fed back to LLM."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc = SimpleNamespace(
            id="call_fail",
            type="function",
            function=SimpleNamespace(name="fail_tool", arguments="{}")
        )
        resp1 = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Attempting", tool_calls=[tc]))])
        resp2 = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Handled error", tool_calls=None))])
        mock_client.chat.completions.create.side_effect = [resp1, resp2]

        result = run_agent(
            name="ErrorAgent",
            instruction="Handle errors.",
            tools=[fail_tool],
            user_input="Run the failing tool",
        )

        self.assertEqual(result["status"], "success")
        # Check tool message sent to LLM
        second_call_messages = mock_client.chat.completions.create.call_args_list[1].kwargs["messages"]
        tool_msg = next(m for m in second_call_messages if m.get("role") == "tool")
        self.assertIn("Tool execution error", tool_msg["content"])

        # Check audit log failure
        entries = get_all_entries()
        fail_entry = next(e for e in entries if e["action"] == "tool_call:fail_tool")
        self.assertEqual(fail_entry["outcome"], "failure")

    @patch("agents.base.get_client")
    def test_max_steps_respected(self, mock_get_client):
        """Test agent stops at max_steps even if LLM keeps requesting tools."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        tc = SimpleNamespace(
            id="call_loop",
            type="function",
            function=SimpleNamespace(name="add_numbers", arguments=json.dumps({"a": 1, "b": 1}))
        )
        infinite_resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Looping", tool_calls=[tc]))])
        mock_client.chat.completions.create.return_value = infinite_resp

        result = run_agent(
            name="LoopingAgent",
            instruction="Loop endlessly.",
            tools=[add_numbers],
            user_input="Keep adding",
            max_steps=3,
        )

        self.assertEqual(result["steps"], 3)
        self.assertEqual(mock_client.chat.completions.create.call_count, 3)

    @patch("agents.base.get_client")
    def test_pydantic_output_validation_retry(self, mock_get_client):
        """Test that invalid schema triggers one retry and validates successfully on second attempt."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        invalid_resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json", tool_calls=None))])
        valid_resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"summary": "Valid data", "ticket_count": 42}), tool_calls=None
        ))])
        mock_client.chat.completions.create.side_effect = [invalid_resp, valid_resp]

        result = run_agent(
            name="SchemaAgent",
            instruction="Return json.",
            tools=[],
            user_input="Provide stats",
            output_schema=SampleOutput,
        )

        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(result["data"])
        self.assertEqual(result["data"]["ticket_count"], 42)


if __name__ == "__main__":
    unittest.main()
