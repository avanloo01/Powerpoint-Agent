import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getLogoUploadUrl } from '../services/api';
import {
  getCurrentUserSettings,
  supabase,
  upsertCurrentUserSettings,
} from '../services/supabase';

/** Only allow https:// and blob: URLs as logo preview sources to prevent XSS. */
function isSafeImageUrl(url: string): boolean {
  return url.startsWith('https://') || url.startsWith('blob:');
}

const SettingsPage: React.FC = () => {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  const [apiKey, setApiKey] = useState('');
  const [primaryColor, setPrimaryColor] = useState('#4f46e5');
  const [accentColor, setAccentColor] = useState('#f59e0b');
  const [logoUrl, setLogoUrl] = useState('');

  const [dataPreviewUrl, setDataPreviewUrl] = useState<string | null>(null);
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);

  const [authLoading, setAuthLoading] = useState(false);
  const [authStatus, setAuthStatus] = useState('');
  const [authError, setAuthError] = useState('');
  const [saveStatus, setSaveStatus] = useState('');
  const [saveError, setSaveError] = useState('');
  const [logoStatus, setLogoStatus] = useState('');
  const [logoError, setLogoError] = useState('');

  useEffect(() => {
    const hydrate = async () => {
      const {
        data: { user },
      } = await supabase.auth.getUser();

      const loggedIn = Boolean(user);
      setIsLoggedIn(loggedIn);

      if (!loggedIn) {
        setApiKey('');
        setPrimaryColor('#4f46e5');
        setAccentColor('#f59e0b');
        setLogoUrl('');
        return;
      }

      const settings = await getCurrentUserSettings();
      setApiKey(settings?.api_key || '');
      setPrimaryColor(settings?.primary_color || '#4f46e5');
      setAccentColor(settings?.accent_color || '#f59e0b');
      setLogoUrl(settings?.logo_url || '');
    };

    void hydrate();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange(() => {
      void hydrate();
    });

    return () => subscription.unsubscribe();
  }, []);

  const handleFileChange = (file: File | null) => {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setLogoError('Please upload an image file (PNG, JPG, SVG, etc.).');
      return;
    }
    setLogoError('');
    setLogoFile(file);

    const reader = new FileReader();
    reader.onload = (ev) => {
      const result = ev.target?.result;
      if (typeof result === 'string' && result.startsWith('data:image/')) {
        setDataPreviewUrl(result);
      }
    };
    reader.readAsDataURL(file);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    handleFileChange(e.dataTransfer.files[0] || null);
  };

  const handleLogin = async () => {
    setAuthStatus('');
    setAuthError('');
    setAuthLoading(true);
    try {
      const { error } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      if (error) throw error;
      setAuthStatus('Logged in successfully.');
      setPassword('');
    } catch (err: unknown) {
      setAuthError(err instanceof Error ? err.message : 'Unable to log in.');
    } finally {
      setAuthLoading(false);
    }
  };

  const handleSignUp = async () => {
    setAuthStatus('');
    setAuthError('');
    setAuthLoading(true);
    try {
      const { error } = await supabase.auth.signUp({
        email: email.trim(),
        password,
      });
      if (error) throw error;
      setAuthStatus('Account created. Check your email if confirmation is enabled.');
      setPassword('');
    } catch (err: unknown) {
      setAuthError(err instanceof Error ? err.message : 'Unable to create account.');
    } finally {
      setAuthLoading(false);
    }
  };

  const handleLogout = async () => {
    setAuthStatus('');
    setAuthError('');
    try {
      const { error } = await supabase.auth.signOut();
      if (error) throw error;
      setAuthStatus('Logged out successfully.');
      setDataPreviewUrl(null);
      setLogoFile(null);
    } catch (err: unknown) {
      setAuthError(err instanceof Error ? err.message : 'Unable to log out.');
    }
  };

  const handleSaveSettings = async () => {
    setSaveStatus('');
    setSaveError('');
    try {
      await upsertCurrentUserSettings({
        api_key: apiKey.trim(),
        primary_color: primaryColor,
        accent_color: accentColor,
      });
      setSaveStatus('Settings saved.');
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save settings.');
    }
  };

  const handleUploadLogo = async () => {
    if (!logoFile) {
      setLogoError('No file selected.');
      return;
    }

    setLogoStatus('Uploading...');
    setLogoError('');
    try {
      const { uploadUrl, publicUrl } = await getLogoUploadUrl(logoFile.type);
      const response = await fetch(uploadUrl, {
        method: 'PUT',
        body: logoFile,
        headers: { 'Content-Type': logoFile.type },
      });

      if (!response.ok) {
        throw new Error('S3 upload failed.');
      }

      if (isSafeImageUrl(publicUrl)) {
        await upsertCurrentUserSettings({ logo_url: publicUrl });
        setLogoUrl(publicUrl);
      }

      setLogoStatus('Logo uploaded successfully.');
      setLogoFile(null);
    } catch (err: unknown) {
      setLogoError(err instanceof Error ? err.message : 'Failed to upload logo.');
      setLogoStatus('');
    }
  };

  return (
    <div className="min-h-screen bg-slate-100">
      <header className="flex items-center gap-3 bg-white px-6 py-4 shadow-sm">
        <button
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700 transition hover:bg-slate-50"
          onClick={() => navigate('/')}
          aria-label="Back to home"
        >
          Back
        </button>
        <h1 className="text-lg font-semibold text-slate-900">{isLoggedIn ? 'Settings' : 'Login'}</h1>
      </header>

      <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-10">
        {!isLoggedIn && (
          <section className="rounded-2xl bg-white p-7 shadow-lg">
            <h2 className="mb-1 text-base font-semibold text-slate-900">Login</h2>
            <p className="mb-4 text-sm text-slate-500">
              Sign in with your email and password to access your saved API key and brand preferences.
            </p>
            <div className="flex flex-col gap-3">
              <input
                type="email"
                className="w-full rounded-lg border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-slate-400 disabled:bg-slate-100 disabled:text-slate-500"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={authLoading}
                aria-label="Email"
                autoComplete="email"
              />
              <input
                type="password"
                className="w-full rounded-lg border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-slate-400 disabled:bg-slate-100 disabled:text-slate-500"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={authLoading}
                aria-label="Password"
                autoComplete="current-password"
              />
            </div>

            <div className="mt-4 flex gap-3">
              <button
                className="rounded-lg bg-indigo-600 px-6 py-2.5 text-sm font-semibold text-white transition disabled:cursor-not-allowed disabled:bg-indigo-300 hover:bg-indigo-700"
                onClick={handleLogin}
                disabled={authLoading}
              >
                {authLoading ? '⏳ Logging in...' : 'Log In'}
              </button>
              <button
                className="rounded-lg bg-teal-700 px-6 py-2.5 text-sm font-semibold text-white transition disabled:cursor-not-allowed disabled:bg-teal-500 hover:bg-teal-800"
                onClick={handleSignUp}
                disabled={authLoading}
              >
                {authLoading ? '⏳ Signing up...' : 'Sign Up'}
              </button>
            </div>

            {authStatus && (
              <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700">{authStatus}</div>
            )}
            {authError && (
              <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">{authError}</div>
            )}
          </section>
        )}

        {isLoggedIn && (
          <>
            <section className="rounded-2xl bg-white p-7 shadow-lg">
              <h2 className="mb-1 text-base font-semibold text-slate-900">Company Logo</h2>
              <p className="mb-4 text-sm text-slate-500">
                Upload your logo to include it in generated presentations. Stored in S3 and linked to your account.
              </p>

              {dataPreviewUrl && (
                <img
                  src={dataPreviewUrl}
                  alt="Logo preview"
                  className="mb-3 h-20 w-20 rounded-lg border border-slate-200 object-contain"
                />
              )}
              {!dataPreviewUrl && logoUrl && isSafeImageUrl(logoUrl) && (
                <img
                  src={logoUrl}
                  alt="Saved logo"
                  className="mb-3 h-20 w-20 rounded-lg border border-slate-200 object-contain"
                />
              )}

              <div
                className={[
                  'cursor-pointer rounded-xl border-2 border-dashed border-slate-300 p-6 text-center transition',
                  dragging ? 'border-indigo-500 bg-indigo-50' : '',
                ].join(' ')}
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={handleDrop}
                role="button"
                tabIndex={0}
                aria-label="Upload logo"
                onKeyDown={(e) => e.key === 'Enter' && fileInputRef.current?.click()}
              >
                <p className="text-sm text-slate-500">
                  {logoFile ? logoFile.name : 'Click or drag and drop your logo here'}
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  PNG, JPG, SVG supported
                </p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  style={{ display: 'none' }}
                  onChange={(e) => handleFileChange(e.target.files?.[0] || null)}
                />
              </div>

              <button
                className="mt-3 rounded-lg px-6 py-2.5 text-sm font-semibold text-white transition disabled:cursor-not-allowed disabled:opacity-50"
                style={{ backgroundColor: primaryColor }}
                onClick={handleUploadLogo}
                disabled={!logoFile}
              >
                Upload Logo
              </button>

              {logoStatus && (
                <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700">{logoStatus}</div>
              )}
              {logoError && (
                <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">{logoError}</div>
              )}
            </section>

            <section className="rounded-2xl bg-white p-7 shadow-lg">
              <h2 className="mb-1 text-base font-semibold text-slate-900">Brand Colors</h2>
              <p className="mb-4 text-sm text-slate-500">
                Choose your primary and accent colors for the presentation theme.
              </p>

              <div className="flex flex-wrap gap-4">
                <div className="flex min-w-[120px] flex-1 flex-col gap-1.5">
                  <span className="text-sm font-medium text-slate-700">Primary Color</span>
                  <div className="flex items-center gap-2.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5">
                    <div className="relative">
                      <div
                        className="h-6 w-6 rounded-md border border-slate-200"
                        style={{ backgroundColor: primaryColor }}
                      />
                      <input
                        type="color"
                        className="absolute left-0 top-0 h-6 w-6 cursor-pointer opacity-0"
                        value={primaryColor}
                        onChange={(e) => setPrimaryColor(e.target.value)}
                        aria-label="Select primary color"
                      />
                    </div>
                    <span className="font-mono text-sm text-slate-700">{primaryColor}</span>
                  </div>
                </div>

                <div className="flex min-w-[120px] flex-1 flex-col gap-1.5">
                  <span className="text-sm font-medium text-slate-700">Accent Color</span>
                  <div className="flex items-center gap-2.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5">
                    <div className="relative">
                      <div
                        className="h-6 w-6 rounded-md border border-slate-200"
                        style={{ backgroundColor: accentColor }}
                      />
                      <input
                        type="color"
                        className="absolute left-0 top-0 h-6 w-6 cursor-pointer opacity-0"
                        value={accentColor}
                        onChange={(e) => setAccentColor(e.target.value)}
                        aria-label="Select accent color"
                      />
                    </div>
                    <span className="font-mono text-sm text-slate-700">{accentColor}</span>
                  </div>
                </div>
              </div>
            </section>

            <section className="rounded-2xl bg-white p-7 shadow-lg">
              <h2 className="mb-1 text-base font-semibold text-slate-900">Qwen AI API Key</h2>
              <p className="mb-4 text-sm text-slate-500">
                Your API key is saved to Supabase and retrieved server-side during generation.
              </p>
              <input
                type="password"
                className="w-full rounded-lg border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-slate-400"
                placeholder="sk-..."
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                aria-label="Qwen API Key"
                autoComplete="off"
              />
            </section>

            <section className="rounded-2xl bg-white p-7 shadow-lg">
              <h2 className="mb-1 text-base font-semibold text-slate-900">Account</h2>
              <p className="mb-4 text-sm text-slate-500">Save your preferences or log out from this device.</p>
              <button
                className="rounded-lg px-6 py-2.5 text-sm font-semibold text-white transition"
                style={{ backgroundColor: primaryColor }}
                onClick={handleSaveSettings}
              >
                Save Settings
              </button>
              <button
                className="mt-3 block rounded-lg border border-slate-300 px-6 py-2.5 text-sm text-slate-700 transition hover:bg-slate-50"
                onClick={handleLogout}
              >
                Log Out
              </button>

              {saveStatus && (
                <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700">{saveStatus}</div>
              )}
              {saveError && (
                <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">{saveError}</div>
              )}
              {authStatus && (
                <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700">{authStatus}</div>
              )}
              {authError && (
                <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">{authError}</div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
};

export default SettingsPage;
