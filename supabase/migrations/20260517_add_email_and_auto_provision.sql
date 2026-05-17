-- Add email column so user records are identifiable without joining auth.users
alter table public.user_settings
  add column if not exists email text;

-- ─────────────────────────────────────────────────────────────────────────────
-- Auto-provision a user_settings row when a user signs up via email/password.
-- Passwords are managed entirely by Supabase auth (auth.users) and are NEVER
-- stored in this table.
-- ─────────────────────────────────────────────────────────────────────────────

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.user_settings (user_id, email)
  values (new.id, new.email)
  on conflict (user_id) do update
    set email = excluded.email;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
