-- Create memories table
create table if not exists memories (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references auth.users(id) on delete cascade,
  session_id uuid not null unique references sessions(id) on delete cascade,
  scenario_title text not null,
  summary text not null,
  grammar_insights jsonb not null,
  vocabulary_learned jsonb not null,
  key_takeaways jsonb not null,
  created_at timestamptz not null default now()
);

-- Index for speedy queries on user dashboard
create index if not exists memories_user_id_idx on memories (user_id);

-- Enable Row Level Security (RLS)
alter table memories enable row level security;

-- Read policy: users can only fetch their own memories (RLS-enforced)
drop policy if exists "Users can read own memories" on memories;
create policy "Users can read own memories"
  on memories for select
  using (auth.uid() = user_id);
