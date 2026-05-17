import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error('Missing Supabase env vars. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.');
}

export const SETTINGS_TABLE = import.meta.env.VITE_SUPABASE_SETTINGS_TABLE || 'user_settings';

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

export interface UserSettings {
  api_key: string | null;
  primary_color: string | null;
  accent_color: string | null;
  logo_url: string | null;
}

export async function getAccessToken(): Promise<string> {
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session?.access_token) {
    throw new Error('You must be logged in to continue.');
  }
  return session.access_token;
}

export async function getCurrentUserSettings(): Promise<UserSettings | null> {
  const {
    data: { user },
    error: userError,
  } = await supabase.auth.getUser();

  if (userError) throw userError;
  if (!user) return null;

  const { data, error } = await supabase
    .from(SETTINGS_TABLE)
    .select('api_key, primary_color, accent_color, logo_url')
    .eq('user_id', user.id)
    .maybeSingle();

  if (error) throw error;
  return (data as UserSettings | null) ?? null;
}

export async function upsertCurrentUserSettings(payload: Partial<UserSettings>): Promise<void> {
  const {
    data: { user },
    error: userError,
  } = await supabase.auth.getUser();

  if (userError) throw userError;
  if (!user) throw new Error('You must be logged in to save settings.');

  const row = {
    user_id: user.id,
    ...payload,
  };

  const { error } = await supabase
    .from(SETTINGS_TABLE)
    .upsert(row, { onConflict: 'user_id' });

  if (error) throw error;
}