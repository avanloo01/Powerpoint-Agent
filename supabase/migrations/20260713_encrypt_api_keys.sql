-- ============================================================================
-- Migration: Encrypt API keys at rest
--
-- Replaces the plaintext api_key column with an encrypted version using pgcrypto.
-- A BEFORE trigger auto-encrypts on INSERT/UPDATE and clears the plaintext column.
-- The encryption key lives in a service_role-only server_secrets table.
--
-- DEPLOYMENT STEP (manual, one-time):
--   After applying this migration, insert the actual encryption key:
--     INSERT INTO server_secrets (key_name, secret_value)
--     VALUES ('api_key_encryption', '<your-256-bit-random-key>');
--
--   This same key must be set as the ENCRYPTION_KEY env var on all Lambdas.
-- ============================================================================

-- 1. Enable pgcrypto extension ------------------------------------------------
create extension if not exists pgcrypto with schema extensions;

-- 2. Server-side secrets table (only service_role can access) -----------------
create table if not exists server_secrets (
  key_name     text primary key,
  secret_value text not null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

alter table server_secrets enable row level security;

-- Only the service_role (bypasses RLS) can read secrets.
-- Authenticated users have zero access.
create policy "Service role can manage secrets"
on server_secrets
for all
using (false)
with check (false);

revoke all on server_secrets from public, anon, authenticated;

-- The service_role needs read access for the decryption view's subquery
-- and the trigger function's SECURITY DEFINER context.
grant select on server_secrets to service_role;

-- 3. Add encrypted API key column ---------------------------------------------
alter table public.user_settings
  add column if not exists api_key_encrypted bytea;

-- 4. Encryption function (SECURITY DEFINER to read server_secrets) ------------
create or replace function public.encrypt_user_api_key()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  enc_key text;
begin
  -- If no plaintext key is being set, leave the encrypted column untouched
  if new.api_key is null or new.api_key = '' then
    -- If the encrypted column already has a value, keep it; the user didn't change the key
    return new;
  end if;

  -- Read the encryption key (only service_role can access this table,
  -- but this function runs as SECURITY DEFINER with owner's privileges)
  select secret_value into enc_key
  from server_secrets
  where key_name = 'api_key_encryption';

  if enc_key is null then
    raise exception 'Encryption key not found in server_secrets. Run the deployment step to insert it.';
  end if;

  -- Encrypt and store in the encrypted column; clear the plaintext column
  new.api_key_encrypted := pgp_sym_encrypt(new.api_key, enc_key);
  new.api_key := '';

  return new;
end;
$$;

-- 5. Trigger: fire BEFORE insert or update of api_key -------------------------
drop trigger if exists encrypt_api_key_trigger on public.user_settings;
create trigger encrypt_api_key_trigger
  before insert or update of api_key on public.user_settings
  for each row
  execute function public.encrypt_user_api_key();

-- 6. Decryption view (for backend Lambdas to query via PostgREST) -----------
-- PostgREST doesn't support inline function calls in ?select=, so we expose a
-- view that decrypts the key. RLS ensures only the owning user can see their row.
create or replace view public.user_settings_decrypted as
select
  user_id,
  primary_color,
  accent_color,
  logo_url,
  created_at,
  updated_at,
  case
    when api_key_encrypted is not null then
      convert_from(
        pgp_sym_decrypt(
          api_key_encrypted,
          (select secret_value from server_secrets where key_name = 'api_key_encryption')
        ),
        'utf8'
      )
    else null
  end as api_key
from public.user_settings;

-- Grant access: only service_role can use the decryption view.
-- Authenticated users should query user_settings.api_key_encrypted directly
-- (to check if a key exists) — they cannot decrypt it.
grant select on public.user_settings_decrypted to service_role;

-- 7. Grant authenticated users continued access to the table
-- (They can read api_key_encrypted but it will be binary — they can't decrypt it)
-- The existing broad grant (select, insert, update) from the original migration
-- already covers all columns. We just need to ensure api_key_encrypted is selectable.
grant select (api_key_encrypted) on public.user_settings to authenticated;

-- 8. Migrate existing plaintext keys (run AFTER inserting the encryption key) --
-- This must be done manually after the encryption key is inserted:
--
--   BEGIN;
--   -- Temporarily drop the trigger so we can do a direct UPDATE
--   DROP TRIGGER IF EXISTS encrypt_api_key_trigger ON public.user_settings;
--   UPDATE public.user_settings
--     SET api_key_encrypted = pgp_sym_encrypt(
--           api_key,
--           (SELECT secret_value FROM server_secrets WHERE key_name = 'api_key_encryption')
--         ),
--         api_key = ''
--     WHERE api_key IS NOT NULL AND api_key != '';
--   -- Recreate the trigger
--   CREATE TRIGGER encrypt_api_key_trigger
--     BEFORE INSERT OR UPDATE OF api_key ON public.user_settings
--     FOR EACH ROW
--     EXECUTE FUNCTION public.encrypt_user_api_key();
--   COMMIT;
--
-- After all rows are migrated, the plaintext api_key column can be dropped
-- in a future cleanup migration.
