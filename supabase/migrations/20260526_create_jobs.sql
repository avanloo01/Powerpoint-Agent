-- Jobs table: tracks the state of each agent-loop run.
-- The backend writes to this table via the service-role key (bypasses RLS).
-- The frontend reads it via the user's JWT (RLS: users see only their own rows).
-- Supabase Realtime is enabled so the frontend gets live status pushes.

create table if not exists public.jobs (
  id             uuid        primary key default gen_random_uuid(),
  user_id        uuid        not null references auth.users(id) on delete cascade,
  status         text        not null default 'pending',
  stage_message  text,
  research_md    text,
  structure_md   text,
  pptx_code      text,
  download_url   text,
  error_message  text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

alter table public.jobs enable row level security;

-- Reuse the updated_at trigger function created in the first migration.
create trigger jobs_updated_at
  before update on public.jobs
  for each row
  execute function public.set_user_settings_updated_at();

create policy "Users can view their own jobs"
  on public.jobs for select
  using (auth.uid() = user_id);

-- The service-role key bypasses RLS for INSERT/PATCH from Lambda.
grant select on public.jobs to authenticated;

-- Opt the table into the realtime publication so the frontend
-- receives live UPDATE events via supabase.channel().
alter publication supabase_realtime add table public.jobs;
