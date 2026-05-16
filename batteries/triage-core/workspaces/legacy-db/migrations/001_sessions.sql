-- Migration 001 — create sessions tables
-- Run: psql $DATABASE_URL < migrations/001_sessions.sql

CREATE TABLE IF NOT EXISTS legacy_sessions (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    token      VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    token      VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ  NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
