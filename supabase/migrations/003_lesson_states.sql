-- Lesson state table for lightweight resume checkpoints
create table lesson_states (
  id uuid primary key default uuid_generate_v4(),
  session_id uuid not null unique references sessions(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  introduced_items jsonb not null default '[]'::jsonb,
  last_item jsonb,
  pause_at timestamptz,
  interrupted_turn_text text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger lesson_states_updated_at
  before update on lesson_states
  for each row execute procedure update_updated_at();

alter table lesson_states enable row level security;

create policy "user owns lesson state"
  on lesson_states for all
  using (auth.uid() = user_id);
