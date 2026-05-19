import base64
import os
import pathlib
import secrets
import time
import unittest
import urllib.request

from vertesia_client import Client
from vertesia_client.openapi.exceptions import ApiException
from vertesia_client.openapi.models.bulk_operation_payload import BulkOperationPayload
from vertesia_client.openapi.models.complex_search_payload import ComplexSearchPayload
from vertesia_client.openapi.models.content_object_status import ContentObjectStatus
from vertesia_client.openapi.models.content_source import ContentSource
from vertesia_client.openapi.models.create_content_object_payload import CreateContentObjectPayload
from vertesia_client.openapi.models.create_data_store_payload import CreateDataStorePayload
from vertesia_client.openapi.models.get_upload_url_payload import GetUploadUrlPayload
from vertesia_client.openapi.models.interaction_create_payload import InteractionCreatePayload
from vertesia_client.openapi.models.interaction_execution_payload import InteractionExecutionPayload
from vertesia_client.openapi.models.interaction_status import InteractionStatus
from vertesia_client.openapi.models.interaction_visibility import InteractionVisibility
from vertesia_client.openapi.models.prompt_role import PromptRole
from vertesia_client.openapi.models.prompt_segment_def import PromptSegmentDef
from vertesia_client.openapi.models.prompt_segment_def_template import PromptSegmentDefTemplate
from vertesia_client.openapi.models.prompt_segment_def_type import PromptSegmentDefType
from vertesia_client.openapi.models.prompt_template_create_payload import PromptTemplateCreatePayload
from vertesia_client.openapi.models.template_type import TemplateType

DEFAULT_STUDIO_URL = "http://localhost:8091/api/v1"
DEFAULT_ZENO_URL = "http://localhost:8092/api/v1"
DEFAULT_STS_URL = "http://localhost:8093"
INTAKE_TIMEOUT_SECONDS = 180
INTAKE_POLL_SECONDS = 3
TEST_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    "wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Al//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EFBABAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


def load_dotenv():
    current = pathlib.Path.cwd()
    for directory in (current, *current.parents):
        env_file = directory / ".env"
        if env_file.exists():
            for raw_line in env_file.read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line.removeprefix("export ").strip()
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("\"'")
                os.environ.setdefault(key, value)
            return


def env(name, fallback):
    return os.environ.get(name) or fallback


def unique_name(prefix):
    return f"{prefix}-{secrets.token_hex(16)}"


def optional(call, *statuses):
    try:
        return call(), True
    except ApiException as exc:
        if exc.status in statuses:
            return None, False
        raise


def valid_live_api_key(api_key):
    return api_key.strip().startswith("sk-") and len(api_key.strip()) > len("sk-")


load_dotenv()


class DotEnvTest(unittest.TestCase):
    def test_dotenv_example_does_not_enable_live_tests(self):
        data = pathlib.Path(".env.example").read_text()
        for raw_line in data.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line.removeprefix("export ").strip()
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key == "VERTESIA_LIVE_TESTS":
                self.assertNotEqual(value, "1")
            if key == "VERTESIA_API_KEY":
                self.assertFalse(valid_live_api_key(value))


@unittest.skipUnless(os.environ.get("VERTESIA_LIVE_TESTS") == "1", "live tests are opt-in")
class LiveIntegrationTest(unittest.TestCase):
    created_prompts = []
    created_interactions = []
    created_objects = []
    created_data_stores = []

    @classmethod
    def setUpClass(cls):
        api_key = os.environ.get("VERTESIA_API_KEY", "").strip()
        if not valid_live_api_key(api_key):
            raise AssertionError("VERTESIA_LIVE_TESTS=1 requires VERTESIA_API_KEY to be an sk- secret key")
        cls.client = Client(
            api_key=api_key,
            server_url=env("VERTESIA_STUDIO_URL", DEFAULT_STUDIO_URL),
            store_url=env("VERTESIA_ZENO_URL", DEFAULT_ZENO_URL),
            token_server_url=env("VERTESIA_STS_URL", DEFAULT_STS_URL),
        )

    @classmethod
    def tearDownClass(cls):
        client = getattr(cls, "client", None)
        if client is None:
            return
        for object_id in cls.created_objects:
            try:
                client.objects.delete_object(object_id)
            except Exception:
                pass
        for store_id in cls.created_data_stores:
            try:
                client.data.delete_data_store(store_id)
            except Exception:
                pass
        for interaction_id in cls.created_interactions:
            try:
                client.interactions.delete_interaction(interaction_id)
            except Exception:
                pass
        for prompt_id in cls.created_prompts:
            try:
                client.prompt_templates.delete_prompt(prompt_id)
            except Exception:
                pass

    def resolve_project_id(self):
        project_id = os.environ.get("VERTESIA_PROJECT_ID")
        if project_id:
            return project_id
        projects = self.client.accounts.list_account_projects()
        if not projects or not projects.data:
            self.skipTest("no projects available on this account")
        return projects.data[0].id

    def image_content_type_id(self):
        configured_type_id = os.environ.get("VERTESIA_CONTENT_OBJECT_TYPE_ID", "").strip()
        if configured_type_id:
            return configured_type_id

        types = self.client.content_object_types.list_content_object_types(
            name="Default",
            layout=True,
            var_schema=True,
            limit=10,
        )
        if types and types[0].id:
            return types[0].id

        self.fail("image intake tests require VERTESIA_CONTENT_OBJECT_TYPE_ID or a Default content object type")

    def test_read_endpoints(self):
        account = self.client.accounts.get_current_account()
        self.assertTrue(account)

        project = self.client.projects.get_project(self.resolve_project_id())
        self.assertTrue(project)

        self.assertIsNotNone(self.client.prompt_templates.list_prompts(limit=10))
        self.assertIsNotNone(self.client.interactions.list_interactions(limit=10))

        objects = self.client.objects.search_objects(ComplexSearchPayload(limit=10))
        self.assertTrue(hasattr(objects, "results"))

        self.assertIsNotNone(self.client.data.list_data_stores())
        self.assertIsNotNone(self.client.workflow_definitions.list_workflow_definitions())

    def test_prompt_interaction_lifecycle(self):
        prompt, permitted = optional(
            lambda: self.client.prompt_templates.create_prompt(
                PromptTemplateCreatePayload(
                    name=unique_name("python-openapi-prompt"),
                    role=PromptRole.USER,
                    content="Answer with a short greeting for <%= name %>.",
                    content_type=TemplateType.JST,
                )
            ),
            403,
        )
        if not permitted:
            self.skipTest("prompt creation is not permitted for this test principal")
        self.assertTrue(prompt and prompt.id)
        self.created_prompts.append(prompt.id)

        rendered = self.client.prompt_templates.render_prompt(prompt.id, {"name": "Vertesia"})
        self.assertTrue(rendered and rendered.rendered)

        interaction = self.client.interactions.create_interaction(
            InteractionCreatePayload(
                status=InteractionStatus.DRAFT,
                prompts=[
                    PromptSegmentDef(
                        type=PromptSegmentDefType.TEMPLATE,
                        template=PromptSegmentDefTemplate(prompt.id),
                    )
                ],
                name=unique_name("python-openapi-interaction"),
                visibility=InteractionVisibility.PRIVATE,
                tags=["integration-test", "openapi-python"],
            )
        )
        self.assertTrue(interaction and interaction.id)
        self.created_interactions.append(interaction.id)

        response = self.client.interactions.execute_interaction_without_preload_content(
            interaction.id,
            InteractionExecutionPayload(data={"name": "Vertesia"}),
        )
        self.assertGreaterEqual(response.status, 200)
        self.assertLess(response.status, 300)

        self.client.interactions.delete_interaction(interaction.id)
        self.created_interactions.remove(interaction.id)
        self.client.prompt_templates.delete_prompt(prompt.id)
        self.created_prompts.remove(prompt.id)

    def test_data_store_crud(self):
        created, permitted = optional(
            lambda: self.client.data.create_data_store(
                CreateDataStorePayload(
                    name=unique_name("python-openapi-data-store"),
                    description="Created by Python OpenAPI integration tests",
                    tags=["integration-test", "openapi-python"],
                )
            ),
            403,
        )
        if not permitted:
            self.skipTest("data store creation is not permitted for this test principal")
        self.assertTrue(created and created.id)
        self.created_data_stores.append(created.id)

        retrieved = self.client.data.get_data_store(created.id)
        self.assertEqual(retrieved.id, created.id)
        schema_response = self.client.data.get_data_store_schema_without_preload_content(created.id)
        self.assertEqual(schema_response.status, 200)

        self.client.data.delete_data_store(created.id)
        self.created_data_stores.remove(created.id)

    def test_image_intake(self):
        object_type_id = self.image_content_type_id()
        file_name = unique_name("python-openapi-image") + ".jpg"

        upload = self.client.files.get_file_upload_url(GetUploadUrlPayload(name=file_name, mime_type="image/jpeg"))
        self.assertTrue(upload and upload.url and upload.id)

        request = urllib.request.Request(
            upload.url,
            data=TEST_JPEG,
            method="PUT",
            headers={"Content-Type": "image/jpeg"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            self.assertGreaterEqual(response.status, 200)
            self.assertLess(response.status, 300)

        created = self.client.objects.create_object(
            CreateContentObjectPayload(
                type=object_type_id,
                name=file_name,
                content=ContentSource(source=upload.id, type="image/jpeg", name=file_name),
                security={
                    "content:read": ["project:*"],
                    "content:write": ["project:*"],
                    "content:delete": ["project:*"],
                },
                tags=["integration-test", "openapi-python", "file-intake"],
                properties={"test_type": "openapi-python-image-intake"},
            )
        )
        self.assertTrue(created and created.id)
        self.created_objects.append(created.id)

        deadline = time.time() + INTAKE_TIMEOUT_SECONDS
        while time.time() < deadline:
            latest = self.client.objects.get_object(created.id)
            status = latest.status.value if hasattr(latest.status, "value") else latest.status
            if status in {ContentObjectStatus.COMPLETED.value, ContentObjectStatus.READY.value}:
                source = self.client.objects.get_object_content_source(latest.id)
                self.assertTrue(source and source.source)
                return
            if status == ContentObjectStatus.FAILED.value:
                self.fail("object intake failed")
            time.sleep(INTAKE_POLL_SECONDS)
        self.fail("timed out waiting for object intake")

    def test_request_shapes(self):
        try:
            response = self.client.bulk_operations.run_bulk_content_operation_without_preload_content(
                BulkOperationPayload(name="update", ids=[], params={})
            )
        except ApiException as exc:
            self.assertIn(exc.status, {400, 403, 404, 409})
            return
        self.assertIn(response.status, {200, 400, 403, 404, 409})


if __name__ == "__main__":
    unittest.main()
