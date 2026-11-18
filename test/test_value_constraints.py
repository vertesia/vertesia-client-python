"""A released client must not reject a response because a newer server loosened a constraint.

The spec's value constraints (a pattern, a numeric range, a string length, a list size) describe what the server
accepts today. The server can widen them in a later release, for example a maximum raised from 10 to 15, and a
released client still has to decode, and write back unchanged, values using the new room. Types are still checked.
These cases depend on scripts/patch_forward_compat_enums.py, which the Test workflow runs before these tests.
"""

import unittest

from pydantic import ValidationError

from scripts.patch_forward_compat_enums import patch_value_constraints
from vertesia_client.openapi.models.agentic_view_search_configuration import AgenticViewSearchConfiguration
from vertesia_client.openapi.models.alter_table_payload import AlterTablePayload
from vertesia_client.openapi.models.audit_adoption_filter import AuditAdoptionFilter
from vertesia_client.openapi.models.interaction_execution_configuration import InteractionExecutionConfiguration
from vertesia_client.openapi.models.model_options import ModelOptions
from vertesia_client.openapi.models.stateless_execution_options import StatelessExecutionOptions
from vertesia_client.openapi.models.vertex_ai_gemini_omni_video_options import VertexAIGeminiOmniVideoOptions


class ValueConstraintsTest(unittest.TestCase):
    def assert_round_trip(self, model, body):
        decoded = model.from_dict(body)
        self.assertEqual(decoded.to_dict(), body)
        return decoded

    def test_number_above_the_declared_maximum(self):
        # duration_seconds declares 3 to 15.
        options = {"_option_id": "vertexai-gemini-omni-video", "duration_seconds": 20}
        decoded = self.assert_round_trip(VertexAIGeminiOmniVideoOptions, options)
        self.assertEqual(decoded.duration_seconds, 20)
        self.assert_round_trip(VertexAIGeminiOmniVideoOptions, {"duration_seconds": 1})

        # In a union and a parent: the tagged branch keeps the value instead of losing the options.
        union = ModelOptions.from_dict(options)
        self.assertIsInstance(union.actual_instance, VertexAIGeminiOmniVideoOptions)
        self.assertIsNone(union.raw)
        execution = {"model": "veo", "model_options": options}
        self.assert_round_trip(StatelessExecutionOptions, execution)

    def test_string_that_does_not_match_the_declared_pattern(self):
        # inference_profile declares a 24-digit hexadecimal ObjectId.
        self.assert_round_trip(InteractionExecutionConfiguration, {"inference_profile": "profile:default"})

    def test_string_longer_than_the_declared_maximum(self):
        configuration = {"interaction": "x" * 201, "instructions": "y" * 4001}
        self.assert_round_trip(AgenticViewSearchConfiguration, configuration)

    def test_lists_outside_the_declared_sizes(self):
        self.assert_round_trip(AlterTablePayload, {"changes": []})
        categories = ["content", "workflow", "security", "billing", "system", "external", "future"]
        self.assert_round_trip(AuditAdoptionFilter, {"eventCategories": categories, "resourceTypes": ["r" * 129]})

    def test_types_are_still_checked(self):
        for body in ({"duration_seconds": "20"}, {"duration_seconds": 2.5}):
            with self.assertRaises(ValidationError, msg=body):
                VertexAIGeminiOmniVideoOptions.from_dict(body)
        with self.assertRaises(ValidationError):
            InteractionExecutionConfiguration.from_dict({"inference_profile": 7})
        with self.assertRaises(ValidationError):
            AgenticViewSearchConfiguration.from_dict({"instructions": ["a"]})

    def test_request_models_leave_value_constraints_to_the_server(self):
        # A request the client builds is not checked against this version's constraints either: the server
        # validates it against its own, which can be wider, and answers 400 with the reason otherwise.
        options = VertexAIGeminiOmniVideoOptions(duration_seconds=20)
        options.duration_seconds = 30
        self.assertEqual(options.to_dict(), {"duration_seconds": 30})
        with self.assertRaises(ValidationError):
            options.duration_seconds = "30"


# Shapes of openapi-generator's pydantic v2 models (model_generic.mustache).
GENERATED = """class Example(BaseModel):
    name: Annotated[str, Field(min_length=1, strict=True, max_length=66)] = Field(description="Nom — affiché")
    tags: Optional[Annotated[List[Annotated[str, Field(strict=True, max_length=128)]], Field(max_length=16)]] = None
    limit: Optional[Union[Annotated[float, Field(le=1.5, strict=True)], Annotated[int, Field(gt=0)]]] = None
    id: StrictStr
    __properties: ClassVar[List[str]] = ["name", "tags", "limit", "id"]

    @field_validator('name')
    def name_validate_regular_expression(cls, value):
        \"\"\"Validates the regular expression\"\"\"
        if value is None:
            return value

        if not isinstance(value, str):
            value = str(value)

        if not re.match(r"^[a-z]+$", value):
            raise ValueError(r"must validate the regular expression /^[a-z]+$/")
        return value

    @field_validator('id')
    def id_validate_regular_expression(cls, value):
        \"\"\"Validates the regular expression\"\"\"
        if not isinstance(value, str):
            value = str(value)

        if not re.match(r"^[0-9a-f]{24}$", value):
            raise ValueError(r"must validate the regular expression /^[0-9a-f]{24}$/")
        return value

    @field_validator('kind')
    def kind_validate_enum(cls, value):
        \"\"\"Validates the enum\"\"\"
        if value not in set(['a']):
            raise ValueError("must be one of enum values ('a')")
        return value

    model_config = ConfigDict(
        validate_assignment=True,
    )
"""


class PatchValueConstraintsTest(unittest.TestCase):
    def test_removes_value_constraints_and_keeps_types(self):
        patched = patch_value_constraints(GENERATED)
        self.assertIn('name: Annotated[str, Field(strict=True)] = Field(description="Nom — affiché")\n', patched)
        self.assertIn("tags: Optional[Annotated[List[Annotated[str, Field(strict=True)]], Field()]] = None\n", patched)
        self.assertIn(
            "limit: Optional[Union[Annotated[float, Field(strict=True)], Annotated[int, Field()]]] = None\n",
            patched,
        )
        self.assertNotIn("regular_expression", patched)
        # Other validators stay, and the class keeps its layout.
        self.assertIn(
            '__properties: ClassVar[List[str]] = ["name", "tags", "limit", "id"]\n\n    @field_validator(\'kind\')\n',
            patched,
        )
        self.assertIn("        return value\n\n    model_config = ConfigDict(\n", patched)
        compile(patched, "example.py", "exec")
        # The Test workflow re-runs the script on already patched sources.
        self.assertEqual(patch_value_constraints(patched), patched)

    def test_source_without_constraints_is_left_unchanged(self):
        source = 'class Example(BaseModel):\n    id: StrictStr = Field(description="x")\n'
        self.assertEqual(patch_value_constraints(source), source)


if __name__ == "__main__":
    unittest.main()
