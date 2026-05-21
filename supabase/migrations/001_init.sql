-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- Scenarios table
create table scenarios (
  id uuid primary key default uuid_generate_v4(),
  title text not null,
  target_language text not null,   -- e.g. 'es-ES'
  base_language text not null,     -- e.g. 'en-US'
  system_prompt text not null,
  difficulty_level int not null check (difficulty_level between 1 and 5)
);

-- Sessions table
create type session_status as enum ('active', 'paused', 'completed');

create table sessions (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references auth.users(id) on delete cascade,
  scenario_id uuid not null references scenarios(id),
  status session_status not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Auto-update updated_at
create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger sessions_updated_at
  before update on sessions
  for each row execute procedure update_updated_at();

-- RLS: users can only see their own sessions
alter table sessions enable row level security;

create policy "user owns session"
  on sessions for all
  using (auth.uid() = user_id);
