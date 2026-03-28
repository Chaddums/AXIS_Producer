-- AXIS Backend — event status management (resolve/dismiss)
-- Run this in Supabase SQL editor after 002_billing.sql

-- Add status columns to events
alter table events add column if not exists status text default 'open';
alter table events add column if not exists resolved_by text;
alter table events add column if not exists resolved_at timestamptz;

-- Index for filtering by team + status
create index if not exists idx_events_team_status on events(team_id, status);

-- RLS policy: allow team members to update event status
create policy "Team members can update events"
  on events for update
  using (team_id::text = current_setting('request.headers', true)::json->>'x-team-id')
  with check (team_id::text = current_setting('request.headers', true)::json->>'x-team-id');
