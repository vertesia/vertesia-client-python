import unittest

from vertesia_client.openapi.api.accounts_api import AccountsApi
from vertesia_client.openapi.models.account_projects_response import AccountProjectsResponse
from vertesia_client.openapi.models.account_type import AccountType
from vertesia_client.openapi.models.doc_table_csv import DocTableCsv
from vertesia_client.openapi.models.doc_table_response import DocTableResponse
from vertesia_client.openapi.models.issue_token_response import IssueTokenResponse


class GeneratedCompatibilityTest(unittest.TestCase):
    def test_generated_package_imports(self):
        self.assertTrue(AccountsApi)

    def test_unknown_response_fields_are_ignored(self):
        response = IssueTokenResponse.from_dict(
            {
                "token": "issued-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "future_server_field": {"nested": True},
            }
        )
        self.assertEqual(response.token, "issued-token")
        self.assertNotIn("future_server_field", response.to_dict())

    def test_nested_unknown_response_fields_are_ignored(self):
        response = AccountProjectsResponse.from_dict(
            {
                "data": [
                    {
                        "id": "project-1",
                        "name": "Project One",
                        "account": "account-1",
                        "server_added_nested_field": {"ignored": True},
                    }
                ],
                "server_added_top_level_field": "ignored",
            }
        )
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0].id, "project-1")
        self.assertEqual(response.data[0].name, "Project One")
        self.assertEqual(response.data[0].account, "account-1")

    def test_generated_unions_ignore_unknown_response_fields(self):
        response = DocTableResponse.from_dict(
            {
                "format": "csv",
                "data": "name,value\nalpha,1",
                "server_added_field": "ignored",
            }
        )
        self.assertIsInstance(response.actual_instance, DocTableCsv)
        self.assertEqual(response.actual_instance.data, "name,value\nalpha,1")

    def test_unknown_standalone_enum_values_are_preserved(self):
        value = AccountType("future-account-type")
        self.assertEqual(value.value, "future-account-type")
        self.assertEqual(value.name, "UNKNOWN_DEFAULT_OPEN_API")
        self.assertIs(AccountType("future-account-type"), value)

    def test_unknown_inline_enum_values_are_preserved(self):
        response = IssueTokenResponse.from_dict({"token": "issued-token", "token_type": "FutureBearer"})
        self.assertEqual(response.token_type, "FutureBearer")


if __name__ == "__main__":
    unittest.main()
