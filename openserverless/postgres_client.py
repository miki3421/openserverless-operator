# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
import logging, json,  os
import pg8000.dbapi

class PostgresClient:
    
    def __init__(self, hostname, port, admin_username, password):
        self.hostname   = hostname
        self.port   = port        
        self.username   = admin_username
        self.password   = password

    def get_connection(self, db_name=None):
        """
        Builds a PGDB Connection, optionally to the specified database name.
        return: predefined connection for this PostgresDB client
        """
        kwargs = {
            "host": self.hostname,
            "port": int(self.port),
            "user": self.username,
            "password": self.password,
        }
        if db_name:
            kwargs["database"] = db_name
        return pg8000.dbapi.connect(**kwargs)

    def get_connection_to_db(self, db_name):
        """
        Builds a PGDB Connection to the specified database name
        return: predefined connection for this PostgresDB client
        """
        return self.get_connection(db_name)

    def _list_user_schemas(self, conn):
        """
        Returns the list of non system schema names visible on the given connection.
        """
        cur = conn.cursor()
        try:
            cur.execute("SELECT nspname FROM pg_catalog.pg_namespace where nspname not in ('information_schema','pg_catalog','pg_toast');")
            return [record[0] for record in cur.fetchall()]
        finally:
            cur.close()

    def query_all_pg_database_size(self):
        """
        Queries the configured Postgres database to retrieve all the existing database size in MB.
        return: a dictionary mapping the PG database name to the used space in bytes
        """
        pg_dbsize_list = {}
        conn = self.get_connection()
        try:
            logging.info("Querying all Postgres database used space")

            # Open a cursor to perform database operations
            cur = conn.cursor()
            try:
                cur.execute("SELECT datname as db_name, pg_database_size(datname) as db_usage FROM pg_database;")
                for record in cur.fetchall():
                    logging.info(record)
                    pg_dbsize_list[record[0]]=record[1]
            finally:
                cur.close()
        finally:
            conn.close()

        return pg_dbsize_list

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf-8")) > 63:
            raise ValueError("Invalid PostgreSQL identifier")
        return '"' + value.replace('"', '""') + '"'

    def revoke_access_from_db(self, pg_username, pg_db_name):
        """Suspend a quota-exceeded login, including its existing sessions.

        Owners retain implicit DDL and grant authority. ACL-only read-only mode
        cannot cap their writes. NOLOGIN plus session termination is required.
        The role owns the same objects and retains its original ACLs for reset.
        """
        conn = None
        try:
            role = self._identifier(pg_username)
            conn = self.get_connection_to_db(pg_db_name)
            cur = conn.cursor()
            try:
                cur.execute(f"ALTER ROLE {role} NOLOGIN")
                # Commit the login barrier before terminating any sessions;
                # reconnecting must not race with an uncommitted role change.
                conn.commit()
                cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE usesysid = (SELECT oid FROM pg_roles WHERE rolname = %s) "
                            "AND pid <> pg_backend_pid()", (pg_username,))
                if not all(row[0] for row in cur.fetchall()):
                    raise RuntimeError("Could not terminate all tenant sessions")
                conn.commit()
                cur.execute("SELECT count(*) FROM pg_stat_activity "
                            "WHERE usesysid = (SELECT oid FROM pg_roles WHERE rolname = %s) "
                            "AND pid <> pg_backend_pid()", (pg_username,))
                if cur.fetchone()[0]:
                    raise RuntimeError("Tenant sessions remain active")
                return True
            finally:
                cur.close()
        except Exception:
            logging.error("Could not suspend PostgreSQL tenant for quota")
            return False
        finally:
            if conn:
                conn.close()

    def grant_access_on_db(self, pg_username, pg_db_name):
        """Restore a suspended login without broadening its original grants."""
        conn = None
        try:
            role = self._identifier(pg_username)
            conn = self.get_connection_to_db(pg_db_name)
            cur = conn.cursor()
            try:
                cur.execute(f"ALTER ROLE {role} LOGIN")
                conn.commit()
                return True
            finally:
                cur.close()
        except Exception:
            logging.error("Could not restore PostgreSQL tenant after quota")
            return False
        finally:
            if conn:
                conn.close()
