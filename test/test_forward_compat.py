"""A released client must keep decoding responses from a newer server.

A newer server can add a property, an enum value or a union variant without a breaking API change. The union
cases depend on scripts/patch_forward_compat_enums.py, which the Test workflow runs before these tests.
"""

import json
import unittest
import warnings

from vertesia_client.openapi.models.agent_run_response import AgentRunResponse
from vertesia_client.openapi.models.completion_result import CompletionResult
from vertesia_client.openapi.models.event_delivery_target import EventDeliveryTarget
from vertesia_client.openapi.models.mcp_connection_parameter import McpConnectionParameter
from vertesia_client.openapi.models.mcp_secret_parameter import McpSecretParameter
from vertesia_client.openapi.models.model_options import ModelOptions
from vertesia_client.openapi.models.node_definition import NodeDefinition
from vertesia_client.openapi.models.node_definition_branches_inner import NodeDefinitionBranchesInner
from vertesia_client.openapi.models.stateless_execution_options import StatelessExecutionOptions
from vertesia_client.openapi.models.text_result import TextResult
from vertesia_client.openapi.models.timer_task import TimerTask
from vertesia_client.openapi.models.vertex_ai_claude_options import VertexAIClaudeOptions
from vertesia_client.openapi.models.workflow_tasks_history import WorkflowTasksHistory

RUN = {
    "id": "000000000000000000000001",
    "account": "000000000000000000000002",
    "project": "000000000000000000000003",
    "interaction": "sys:GeneralAgent",
    "interactionRef": {
        "id": "sys:GeneralAgent",
        "name": "sys:GeneralAgent",
        "endpoint": "sys:GeneralAgent",
        "status": "code",
        "version": 0,
        "tags": [],
        "updated_at": "1970-01-01T00:00:00.000Z",
        "future_ref_field": 1,
    },
    "run_kind": "agent",
    "run_type": "autonomous",
    "status": "completed",
    "started_by": "user:000000000000000000000004",
    "started_at": "2026-07-28T11:08:41.127Z",
    "created_at": "2026-07-28T11:08:41.128Z",
    "updated_at": "2026-07-29T10:59:13.166Z",
    "future_top_level_field": {"nested": True},
}


class ForwardCompatibilityTest(unittest.TestCase):
    def setUp(self):
        # A raw variant must not make serialization warn.
        catcher = warnings.catch_warnings()
        catcher.__enter__()
        self.addCleanup(catcher.__exit__, None, None, None)
        warnings.simplefilter("error")

    def assert_kept_raw(self, union, body):
        self.assertEqual(union.actual_instance, body)
        self.assertEqual(union.raw, body)
        self.assertEqual(union.to_dict(), body)
        self.assertEqual(json.loads(union.to_json()), body)

    def test_unknown_properties_are_ignored(self):
        run = AgentRunResponse.from_dict(RUN)
        self.assertEqual(run.actual_instance.interaction_ref.name, "sys:GeneralAgent")
        result = CompletionResult.from_dict({"type": "text", "value": "hello", "future": {"a": 1}})
        self.assertIsInstance(result.actual_instance, TextResult)
        self.assertIsNone(result.raw)

    def test_unknown_enum_value_is_kept(self):
        run = AgentRunResponse.from_dict({**RUN, "status": "hibernating"})
        self.assertEqual(run.to_dict()["status"], "hibernating")

    def test_unknown_discriminated_variant_is_kept_raw(self):
        body = {"type": "speech", "value": "gs://bucket/a.ogg", "voice": "alloy"}
        self.assert_kept_raw(CompletionResult.from_dict(body), body)

        future_run = {**RUN, "run_type": "scheduled"}
        self.assert_kept_raw(AgentRunResponse.from_dict(future_run), future_run)

        target = {"type": "slack", "url": "https://hooks.slack.com/x"}
        self.assert_kept_raw(EventDeliveryTarget.from_dict(target), target)

        # Through a parent model. Known WorkflowTask branches used to match all four branches.
        timer = {
            "type": "timer",
            "activityId": "a",
            "scheduled": None,
            "status": "completed",
            "attempts": 1,
            "started": None,
            "completed": None,
            "error": None,
            "result": None,
        }
        update = {"type": "update", "activityId": "b", "update_name": "approve"}
        history = WorkflowTasksHistory.from_dict({"type": "tasks", "tasks": [timer, update]})
        self.assertIsInstance(history.tasks[0].actual_instance, TimerTask)
        self.assertEqual(history.tasks[1].raw, update)
        self.assertEqual(history.to_dict()["tasks"][1], update)

        # Known variants stay strict, and a missing discriminator is still an error.
        for invalid in ({}, {"value": "missing type"}, {"type": "text"}, {"type": 7}, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                CompletionResult.from_dict(invalid)

    def test_model_options_keep_every_received_option(self):
        # Stored options are often merged from several families (a run's over its interaction's).
        gemini = {
            "temperature": 0.2,
            "thinking_budget_tokens": 2048,
            "effort": "high",
            "person_generation": "allow_adult",
        }
        # This one fits no family: Claude's cache_ttl and an image size beside Gemini options.
        mixed = {"temperature": 0.2, "thinking_budget_tokens": 2048, "cache_ttl": "1h", "size": "1024x1024"}
        for options in (
            gemini,
            mixed,
            {"_option_id": "vertexai-gemini", **gemini},
            {"_option_id": "vertexai-gemini", **mixed},
        ):
            execution = StatelessExecutionOptions.from_json(json.dumps({"model": "gemini", "model_options": options}))
            written = json.loads(execution.to_json())["model_options"]
            for key, value in options.items():
                self.assertEqual(written.get(key), value, options)
            # Python models keep unknown properties, so the first branch that writes the set back whole is used.
            self.assertEqual(ModelOptions.from_json(json.dumps(options)).to_dict(), options)

        # A single family stays typed, selected by its _option_id rather than by branch order.
        claude = ModelOptions.from_json('{"_option_id": "vertexai-claude", "temperature": 0.2}')
        self.assertIsInstance(claude.actual_instance, VertexAIClaudeOptions)
        self.assertIsNone(claude.raw)
        secret = McpConnectionParameter.from_json('{"type": "secret", "title": "k"}')
        self.assertIsInstance(secret.actual_instance, McpSecretParameter)

    def test_unknown_undiscriminated_variant_is_kept_raw(self):
        body = {"loop": {"over": "items"}, "node": 7}
        self.assert_kept_raw(NodeDefinitionBranchesInner.from_dict(body), body)

        node = NodeDefinition.from_dict({"type": "agent", "branches": [{"to": "next"}, body]})
        self.assertIsNone(node.branches[0].raw)
        self.assertEqual(node.branches[1].raw, body)
        self.assertEqual(node.to_dict()["branches"][1], body)

        self.assert_kept_raw(McpConnectionParameter.from_dict(["oauth"]), ["oauth"])


if __name__ == "__main__":
    unittest.main()
