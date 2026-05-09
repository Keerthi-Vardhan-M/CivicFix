-- Run this in Supabase SQL Editor to set up the database

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE issues (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  location_name TEXT NOT NULL,
  location_lat FLOAT NOT NULL,
  location_lng FLOAT NOT NULL,
  description TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('roads','streetlight','water','garbage','electricity','footpath','traffic','safety','other')),
  severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 10),
  severity_reason TEXT,
  summary TEXT,
  affected_population TEXT,
  urgency TEXT,
  department TEXT,
  dept_email TEXT,
  dept_twitter TEXT,
  dept_phone TEXT,
  complaint_subject TEXT,
  complaint_body TEXT,
  status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','acknowledged','in_progress','resolved','rejected')),
  report_count INTEGER NOT NULL DEFAULT 1,
  email_sent BOOLEAN DEFAULT FALSE,
  tweet_url TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER issues_updated_at
BEFORE UPDATE ON issues
FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Index for geo queries
CREATE INDEX idx_issues_location ON issues (location_lat, location_lng);
CREATE INDEX idx_issues_category ON issues (category);
CREATE INDEX idx_issues_status ON issues (status);

-- Enable Row Level Security (open for hackathon)
ALTER TABLE issues ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all" ON issues FOR ALL USING (true) WITH CHECK (true);
