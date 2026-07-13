import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error('Missing Supabase env vars. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.');
}

export const SETTINGS_TABLE = import.meta.env.VITE_SUPABASE_SETTINGS_TABLE || 'user_settings';

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

// ── Cookie helpers ──────────────────────────────────────────────
const LOGIN_COOKIE = 'ppt-agent-authorized';

export function getLoginCookie(): boolean {
  try {
    return document.cookie
      .split('; ')
      .some((c) => c.startsWith(`${LOGIN_COOKIE}=true`));
  } catch {
    return false;
  }
}

function setLoginCookie(): void {
  try {
    // Expires in 7 days, secure + sameSite for production safety
    document.cookie = `${LOGIN_COOKIE}=true; path=/; max-age=${7 * 24 * 60 * 60}; SameSite=Lax`;
  } catch {
    // Silently fail — the cookie is a cache, not the source of truth
  }
}

function clearLoginCookie(): void {
  try {
    document.cookie = `${LOGIN_COOKIE}=; path=/; max-age=0`;
  } catch {
    // Silently fail
  }
}

// Keep cookie in sync with Supabase auth state changes
supabase.auth.onAuthStateChange((event) => {
  if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED') {
    setLoginCookie();
  } else if (event === 'SIGNED_OUT') {
    clearLoginCookie();
  }
});

// ── Legacy localStorage check (fallback) ───────────────────────
export function hasStoredSession(): boolean {
  try {
    const key = Object.keys(localStorage).find(
      (k) => k.startsWith('sb-') && k.endsWith('-auth-token')
    );
    if (!key) return false;
    const raw = localStorage.getItem(key);
    if (!raw) return false;
    const parsed = JSON.parse(raw);
    return !!(parsed?.access_token);
  } catch {
    return false;
  }
}

export interface UserSettings {
  api_key: string | null;
  api_key_encrypted: string | null;  // base64-encoded bytea; non-null means a key is saved
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
    .select('api_key_encrypted, primary_color, accent_color, logo_url')
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