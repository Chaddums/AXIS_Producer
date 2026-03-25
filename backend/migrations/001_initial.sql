-- AXIS Backend — initial schema
-- Run this in Supabase SQL editor

-- Enable UUID generation
create extension if not exists "uuid-ossp";

-- Users
create table if not exists users (
    id uuid primary key default uuid_generate_v4(),
    email text unique not null,
    password_hash text not null,
    name text not null,
    created_at timestamptz default now(),
    email_verified boolean default false
);

create index if not exists idx_users_email on users(email);

-- Teams
create table if not exists teams (
    id uuid primary key default uuid_generate_v4(),
    name text not null,
    owner_id uuid references users(id) not null,
    workspace_type text default 'custom',
    created_at timestamptz default now()
);

-- Team membership
create table if not exists team_members (
    id uuid primary key default uuid_generate_v4(),
    team_id uuid references teams(id) on delete cascade not null,
    user_id uuid references users(id) on delete cascade not null,
    role text default 'member' check (role in ('owner', 'admin', 'member')),
    joined_at timestamptz default now(),
    unique(team_id, user_id)
);

create index if not exists idx_team_members_user on team_members(user_id);
create index if not exists idx_team_members_team on team_members(team_id);

-- Invite codes
create table if not exists invites (
    id uuid primary key default uuid_generate_v4(),
    team_id uuid references teams(id) on delete cascade not null,
    code text unique not null,
    created_by uuid references users(id) not null,
    used boolean default false,
    used_by uuid references users(id),
    created_at timestamptz default now()
);

create index if not exists idx_invites_code on invites(code);

-- Migrate existing events table: add team_id column if missing
do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'events' and column_name = 'team_id'
    ) then
        alter table events add column team_id uuid references teams(id) on delete cascade;
    end if;
end $$;

-- Events table may use 'ts' (legacy) or 'created_at' — index team_id alone
create index if not exists idx_events_team on events(team_id);

-- Usage tracking (per-team API costs)
create table if not exists usage (
    id uuid primary key default uuid_generate_v4(),
    team_id uuid references teams(id) on delete cascade not null,
    user_id uuid references users(id) not null,
    service text not null,
    tokens_in integer default 0,
    tokens_out integer default 0,
    cost_usd numeric(10, 6) default 0,
    created_at timestamptz default now()
);

create index if not exists idx_usage_team_month on usage(team_id, created_at);

-- Row Level Security
alter table events enable row level security;
alter table usage enable row level security;

-- Drop policies if they exist, then recreate
drop policy if exists "team_events_select" on events;
drop policy if exists "team_events_insert" on events;
drop policy if exists "team_usage_select" on usage;

create policy "team_events_select" on events for select
    using (team_id::text = current_setting('request.headers')::json->>'x-team-id');

create policy "team_events_insert" on events for insert
    with check (team_id::text = current_setting('request.headers')::json->>'x-team-id');

create policy "team_usage_select" on usage for select
    using (team_id::text = current_setting('request.headers')::json->>'x-team-id');
