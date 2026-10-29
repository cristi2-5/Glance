-- Runs once, on first initialisation of the data volume. The host-side pytest
-- run (TEST_DATABASE_URL) drops and recreates tables per test, so it gets its
-- own database and can never touch the one the api uses.
CREATE DATABASE glance_test;
