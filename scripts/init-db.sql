-- Runs once, the first time the Postgres container initialises its data volume.
-- Creates the test databases and enables the `vector` extension (pgvector) in
-- every database the project uses. store.init_db() also enables it defensively.

CREATE DATABASE revrec_test OWNER revrec;
CREATE DATABASE revrec_test_diag OWNER revrec;
CREATE DATABASE revrec_test_rec OWNER revrec;
CREATE DATABASE revrec_test_aud OWNER revrec;

\connect revrec
CREATE EXTENSION IF NOT EXISTS vector;

\connect revrec_test
CREATE EXTENSION IF NOT EXISTS vector;

\connect revrec_test_diag
CREATE EXTENSION IF NOT EXISTS vector;

\connect revrec_test_rec
CREATE EXTENSION IF NOT EXISTS vector;

\connect revrec_test_aud
CREATE EXTENSION IF NOT EXISTS vector;
