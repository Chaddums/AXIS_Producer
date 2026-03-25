-- AXIS Backend — billing, verification, prohibited use tables
-- Run this in Supabase SQL editor after 001_initial.sql

-- Subscriptions (Stripe-linked)
create table if not exists subscriptions (
    id uuid primary key default uuid_generate_v4(),
    team_id uuid references teams(id) on delete cascade not null,
    stripe_customer_id text not null,
    stripe_subscription_id text unique not null,
    tier text not null check (tier in ('team', 'pro')),
    status text not null default 'trialing',
    current_period_end timestamptz,
    seats integer default 1,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_subs_team on subscriptions(team_id);
create index if not exists idx_subs_stripe on subscriptions(stripe_subscription_id);

-- Add stripe_customer_id to users
alter table users add column if not exists stripe_customer_id text;

-- Add workspace config columns to teams
alter table teams add column if not exists workspace_context text default '';
alter table teams add column if not exists output_terminology jsonb default '{}';
alter table teams add column if not exists privacy_preset text default 'standard';

-- Self-attestation records (legal artifact, never delete)
create table if not exists attestations (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid references users(id) not null,
    ip_address text,
    attested_at timestamptz default now()
);

-- Flagged accounts for manual review
create table if not exists flagged_accounts (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid references users(id) not null,
    reason text not null,
    matched_keywords text[] default '{}',
    reviewed boolean default false,
    reviewed_by uuid references users(id),
    reviewed_at timestamptz,
    action_taken text,
    created_at timestamptz default now()
);

create index if not exists idx_flagged_unreviewed on flagged_accounts(reviewed) where not reviewed;

-- Email verification tokens
create table if not exists email_verifications (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid references users(id) on delete cascade not null,
    token text unique not null,
    expires_at timestamptz not null,
    used boolean default false,
    created_at timestamptz default now()
);

create index if not exists idx_email_verify_token on email_verifications(token);

-- Password reset tokens
create table if not exists password_resets (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid references users(id) on delete cascade not null,
    token text unique not null,
    expires_at timestamptz not null,
    used boolean default false,
    created_at timestamptz default now()
);

create index if not exists idx_pw_reset_token on password_resets(token);

-- Syntheses (cross-stream analysis, replaces direct Supabase inserts)
create table if not exists syntheses (
    id uuid primary key default uuid_generate_v4(),
    team_id uuid references teams(id) on delete cascade not null,
    content text not null,
    window_start timestamptz not null,
    window_end timestamptz not null,
    created_at timestamptz default now()
);

create index if not exists idx_syntheses_team on syntheses(team_id, created_at desc);
