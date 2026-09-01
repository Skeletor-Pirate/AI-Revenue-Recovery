-- Runs once, the first time the Postgres container initialises its data volume.
-- Creates a separate database used only by `uv run pytest`, so tests never
-- touch the demo data in `revrec`.
CREATE DATABASE revrec_test OWNER revrec;
