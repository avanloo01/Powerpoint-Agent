create table if not exists public.user_settings (
  user_id uuid primary key references auth.users (id) on delete cascade,
  api_key text not null default '',
  primary_color text not null default '#C00000',
  accent_color text not null default '#A6CAEC',
  logo_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.user_settings enable row level security;

create or replace function public.set_user_settings_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists user_settings_updated_at on public.user_settings;
create trigger user_settings_updated_at
before update on public.user_settings
for each row
execute function public.set_user_settings_updated_at();

create policy "Users can view their own settings"
on public.user_settings
for select
using (auth.uid() = user_id);

create policy "Users can insert their own settings"
on public.user_settings
for insert
with check (auth.uid() = user_id);

create policy "Users can update their own settings"
on public.user_settings
for update
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

grant select, insert, update on public.user_settings to authenticated;
