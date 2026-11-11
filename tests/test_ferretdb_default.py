import unittest
from unittest.mock import Mock, patch

from openserverless import main, patcher, quota_checker, user_handlers


class FerretDBDefaultTests(unittest.TestCase):
    def test_all_reconciliation_paths_use_ferretdb(self):
        self.assertEqual(main.ferretdb.__name__, "openserverless.ferretdb")
        self.assertEqual(patcher.ferretdb.__name__, "openserverless.ferretdb")
        self.assertEqual(user_handlers.mdb.__name__, "openserverless.ferretdb")

    def test_mongodb_quota_is_checked_through_postgres(self):
        tenant = {
            "metadata": {"name": "devel"},
            "spec": {
                "namespace": "devel",
                "mongodb": {"enabled": True, "database": "devel", "quota": 100},
            },
        }
        client = Mock()
        client.query_all_pg_database_size.return_value = {"devel_ferretdb": 1}

        with patch.object(quota_checker, "get_wsk_users", side_effect=[[], [tenant], []]), \
             patch.object(quota_checker, "PostgresClient", return_value=client), \
             patch.object(quota_checker, "check_pg_quota") as check, \
             patch.dict(quota_checker.os.environ, {"POSTGRES_ENABLED": "true", "MONGODB_ENABLED": "true"}, clear=False):
            quota_checker.start()

        check.assert_called_once_with(client, {"devel_ferretdb": 1}, [tenant], True)


if __name__ == "__main__":
    unittest.main()
