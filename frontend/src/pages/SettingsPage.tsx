import React, { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import Cookies from 'js-cookie';
import { getLogoUploadUrl } from '../services/api';

const COOKIE_OPTIONS = { expires: 365, sameSite: 'strict' as const, secure: true };

/** Only allow https:// and blob: URLs as logo preview sources to prevent XSS. */
function isSafeImageUrl(url: string): boolean {
  return url.startsWith('https://') || url.startsWith('blob:');
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: '100vh',
    backgroundColor: '#f5f7fa',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    padding: '16px 24px',
    backgroundColor: '#ffffff',
    boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
    gap: '12px',
  },
  backButton: {
    background: 'none',
    border: '1px solid #d1d5db',
    borderRadius: '8px',
    padding: '8px 16px',
    cursor: 'pointer',
    fontSize: '14px',
    color: '#374151',
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  headerTitle: {
    fontSize: '18px',
    fontWeight: '600',
    color: '#111827',
  },
  main: {
    maxWidth: '640px',
    margin: '40px auto',
    padding: '0 24px',
    display: 'flex',
    flexDirection: 'column',
    gap: '24px',
  },
  section: {
    backgroundColor: '#ffffff',
    borderRadius: '16px',
    padding: '28px',
    boxShadow: '0 2px 12px rgba(0,0,0,0.06)',
  },
  sectionTitle: {
    fontSize: '16px',
    fontWeight: '600',
    color: '#111827',
    marginBottom: '6px',
  },
  sectionDesc: {
    fontSize: '13px',
    color: '#6b7280',
    marginBottom: '16px',
  },
  input: {
    width: '100%',
    padding: '10px 14px',
    fontSize: '14px',
    border: '1.5px solid #e5e7eb',
    borderRadius: '8px',
    outline: 'none',
    fontFamily: 'inherit',
  },
  colorRow: {
    display: 'flex',
    gap: '16px',
    flexWrap: 'wrap' as const,
  },
  colorGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    flex: 1,
    minWidth: '120px',
  },
  colorLabel: {
    fontSize: '13px',
    color: '#374151',
    fontWeight: '500',
  },
  colorInputWrapper: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    border: '1.5px solid #e5e7eb',
    borderRadius: '8px',
    padding: '6px 12px',
    backgroundColor: '#fff',
  },
  colorSwatch: {
    width: '24px',
    height: '24px',
    borderRadius: '6px',
    border: '1px solid #e5e7eb',
    cursor: 'pointer',
  },
  colorHex: {
    fontSize: '14px',
    color: '#374151',
    fontFamily: 'monospace',
  },
  colorPicker: {
    position: 'absolute',
    opacity: 0,
    width: '24px',
    height: '24px',
    cursor: 'pointer',
  },
  saveButton: {
    marginTop: '16px',
    padding: '10px 24px',
    fontSize: '14px',
    fontWeight: '600',
    color: '#ffffff',
    backgroundColor: '#4f46e5',
    border: 'none',
    borderRadius: '8px',
    cursor: 'pointer',
  },
  logoPreview: {
    width: '80px',
    height: '80px',
    objectFit: 'contain' as const,
    border: '1px solid #e5e7eb',
    borderRadius: '8px',
    marginBottom: '12px',
  },
  uploadArea: {
    border: '2px dashed #d1d5db',
    borderRadius: '10px',
    padding: '24px',
    textAlign: 'center' as const,
    cursor: 'pointer',
    transition: 'border-color 0.2s',
  },
  uploadAreaActive: {
    borderColor: '#4f46e5',
    backgroundColor: '#eef2ff',
  },
  uploadText: {
    fontSize: '14px',
    color: '#6b7280',
  },
  statusBox: {
    marginTop: '10px',
    padding: '10px 14px',
    borderRadius: '8px',
    fontSize: '13px',
  },
  successStatus: {
    backgroundColor: '#f0fdf4',
    border: '1px solid #bbf7d0',
    color: '#16a34a',
  },
  errorStatus: {
    backgroundColor: '#fef2f2',
    border: '1px solid #fecaca',
    color: '#dc2626',
  },
};

const SettingsPage: React.FC = () => {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [apiKey, setApiKey] = useState(Cookies.get('qwen_api_key') || '');
  const [primaryColor, setPrimaryColor] = useState(Cookies.get('primary_color') || '#4f46e5');
  const [accentColor, setAccentColor] = useState(Cookies.get('accent_color') || '#f59e0b');

  // Whether a logo has previously been uploaded (derived from cookie, used as a flag only).
  const [hasStoredLogo, setHasStoredLogo] = useState<boolean>(() => {
    const stored = Cookies.get('logo_url') ?? '';
    return isSafeImageUrl(stored);
  });

  // Data URL produced by FileReader — always starts with "data:image/" because we validate
  // file.type before reading, making it safe to use as an img src.
  const [dataPreviewUrl, setDataPreviewUrl] = useState<string | null>(null);

  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [logoStatus, setLogoStatus] = useState('');
  const [logoError, setLogoError] = useState('');

  const handleFileChange = (file: File | null) => {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setLogoError('Please upload an image file (PNG, JPG, SVG, etc.).');
      return;
    }
    setLogoError('');
    setLogoFile(file);

    // Read as a data URL so the preview src never flows from URL.createObjectURL.
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

  const handleUploadLogo = async () => {
    if (!logoFile) {
      setLogoError('No file selected.');
      return;
    }
    setLogoStatus('Uploading...');
    setLogoError('');
    try {
      const { uploadUrl, publicUrl } = await getLogoUploadUrl(logoFile.type);
      await fetch(uploadUrl, {
        method: 'PUT',
        body: logoFile,
        headers: { 'Content-Type': logoFile.type },
      });
      if (isSafeImageUrl(publicUrl)) {
        Cookies.set('logo_url', publicUrl, COOKIE_OPTIONS);
        setHasStoredLogo(true);
      }
      setLogoStatus('✅ Logo uploaded successfully!');
    } catch {
      setLogoError('Failed to upload logo. Please try again.');
      setLogoStatus('');
    }
  };

  const handleSaveSettings = () => {
    Cookies.set('qwen_api_key', apiKey, COOKIE_OPTIONS);
    Cookies.set('primary_color', primaryColor, COOKIE_OPTIONS);
    Cookies.set('accent_color', accentColor, COOKIE_OPTIONS);
    setSaveStatus('✅ Settings saved!');
    setTimeout(() => setSaveStatus(''), 3000);
  };

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <button
          style={styles.backButton}
          onClick={() => navigate('/')}
          aria-label="Back to home"
        >
          ← Back
        </button>
        <h1 style={styles.headerTitle}>Settings</h1>
      </header>

      <main style={styles.main}>
        {/* Logo Upload */}
        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Company Logo</h2>
          <p style={styles.sectionDesc}>
            Upload your logo to include it in generated presentations. Stored securely in S3.
          </p>

          {/* Show a local data-URL preview when a file is selected */}
          {dataPreviewUrl && (
            <img src={dataPreviewUrl} alt="Logo preview" style={styles.logoPreview} />
          )}
          {/* Show a text indicator (no img) when a logo is already stored */}
          {!dataPreviewUrl && hasStoredLogo && (
            <p style={{ fontSize: '13px', color: '#16a34a', marginBottom: '12px' }}>
              ✅ A logo is already saved. Upload a new file to replace it.
            </p>
          )}

          <div
            style={{
              ...styles.uploadArea,
              ...(dragging ? styles.uploadAreaActive : {}),
            }}
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            role="button"
            tabIndex={0}
            aria-label="Upload logo"
            onKeyDown={(e) => e.key === 'Enter' && fileInputRef.current?.click()}
          >
            <p style={styles.uploadText}>
              {logoFile ? `📎 ${logoFile.name}` : '📁 Click or drag & drop your logo here'}
            </p>
            <p style={{ ...styles.uploadText, fontSize: '12px', marginTop: '4px' }}>
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
            style={{ ...styles.saveButton, marginTop: '12px' }}
            onClick={handleUploadLogo}
            disabled={!logoFile}
          >
            Upload Logo
          </button>

          {logoStatus && (
            <div style={{ ...styles.statusBox, ...styles.successStatus }}>{logoStatus}</div>
          )}
          {logoError && (
            <div style={{ ...styles.statusBox, ...styles.errorStatus }}>{logoError}</div>
          )}
        </section>

        {/* Color Settings */}
        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Brand Colors</h2>
          <p style={styles.sectionDesc}>
            Choose your primary and accent colors for the presentation theme. Saved in your browser.
          </p>

          <div style={styles.colorRow}>
            <div style={styles.colorGroup}>
              <span style={styles.colorLabel}>Primary Color</span>
              <div style={styles.colorInputWrapper}>
                <div style={{ position: 'relative' }}>
                  <div
                    style={{ ...styles.colorSwatch, backgroundColor: primaryColor }}
                  />
                  <input
                    type="color"
                    style={styles.colorPicker}
                    value={primaryColor}
                    onChange={(e) => setPrimaryColor(e.target.value)}
                    aria-label="Select primary color"
                  />
                </div>
                <span style={styles.colorHex}>{primaryColor}</span>
              </div>
            </div>

            <div style={styles.colorGroup}>
              <span style={styles.colorLabel}>Accent Color</span>
              <div style={styles.colorInputWrapper}>
                <div style={{ position: 'relative' }}>
                  <div
                    style={{ ...styles.colorSwatch, backgroundColor: accentColor }}
                  />
                  <input
                    type="color"
                    style={styles.colorPicker}
                    value={accentColor}
                    onChange={(e) => setAccentColor(e.target.value)}
                    aria-label="Select accent color"
                  />
                </div>
                <span style={styles.colorHex}>{accentColor}</span>
              </div>
            </div>
          </div>
        </section>

        {/* API Key */}
        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Qwen AI API Key</h2>
          <p style={styles.sectionDesc}>
            Your API key is stored only in your browser cookies and never sent to our servers.
          </p>
          <input
            type="password"
            style={styles.input}
            placeholder="sk-..."
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            aria-label="Qwen API Key"
            autoComplete="off"
          />
        </section>

        {/* Save Button */}
        <button style={styles.saveButton} onClick={handleSaveSettings}>
          Save Settings
        </button>

        {saveStatus && (
          <div style={{ ...styles.statusBox, ...styles.successStatus }}>{saveStatus}</div>
        )}
      </main>
    </div>
  );
};

export default SettingsPage;
