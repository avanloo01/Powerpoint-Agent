import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Cookies from 'js-cookie';
import { generatePresentation } from '../services/api';

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    backgroundColor: '#f5f7fa',
  },
  header: {
    display: 'flex',
    justifyContent: 'flex-end',
    alignItems: 'center',
    padding: '16px 24px',
    backgroundColor: '#ffffff',
    boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
  },
  settingsButton: {
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
    transition: 'background-color 0.2s',
  },
  main: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '40px 24px',
  },
  card: {
    backgroundColor: '#ffffff',
    borderRadius: '16px',
    padding: '48px',
    width: '100%',
    maxWidth: '600px',
    boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
  },
  title: {
    fontSize: '28px',
    fontWeight: '700',
    color: '#111827',
    marginBottom: '8px',
    textAlign: 'center',
  },
  subtitle: {
    fontSize: '15px',
    color: '#6b7280',
    marginBottom: '32px',
    textAlign: 'center',
  },
  textarea: {
    width: '100%',
    minHeight: '120px',
    padding: '14px 16px',
    fontSize: '15px',
    border: '1.5px solid #e5e7eb',
    borderRadius: '10px',
    resize: 'vertical',
    fontFamily: 'inherit',
    lineHeight: '1.5',
    outline: 'none',
    transition: 'border-color 0.2s',
  },
  generateButton: {
    width: '100%',
    marginTop: '16px',
    padding: '14px',
    fontSize: '16px',
    fontWeight: '600',
    color: '#ffffff',
    backgroundColor: '#4f46e5',
    border: 'none',
    borderRadius: '10px',
    cursor: 'pointer',
    transition: 'background-color 0.2s',
  },
  generateButtonDisabled: {
    backgroundColor: '#a5b4fc',
    cursor: 'not-allowed',
  },
  errorBox: {
    marginTop: '16px',
    padding: '12px 16px',
    backgroundColor: '#fef2f2',
    border: '1px solid #fecaca',
    borderRadius: '8px',
    color: '#dc2626',
    fontSize: '14px',
  },
  successBox: {
    marginTop: '16px',
    padding: '12px 16px',
    backgroundColor: '#f0fdf4',
    border: '1px solid #bbf7d0',
    borderRadius: '8px',
    color: '#16a34a',
    fontSize: '14px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  downloadLink: {
    color: '#4f46e5',
    fontWeight: '600',
    textDecoration: 'none',
  },
  hint: {
    marginTop: '12px',
    fontSize: '13px',
    color: '#9ca3af',
    textAlign: 'center',
  },
};

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [downloadUrl, setDownloadUrl] = useState('');

  const apiKey = Cookies.get('qwen_api_key') || '';
  const primaryColor = Cookies.get('primary_color') || '#4f46e5';

  const handleGenerate = async () => {
    if (!prompt.trim()) {
      setError('Please enter a prompt first.');
      return;
    }
    if (!apiKey) {
      setError('No API key found. Please add your Qwen API key in Settings.');
      return;
    }

    setError('');
    setDownloadUrl('');
    setLoading(true);

    try {
      const accentColor = Cookies.get('accent_color') || '#f59e0b';
      const url = await generatePresentation({ prompt, apiKey, primaryColor, accentColor });
      setDownloadUrl(url);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'An unexpected error occurred.';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <button
          style={styles.settingsButton}
          onClick={() => navigate('/settings')}
          aria-label="Open settings"
        >
          ⚙️ Settings
        </button>
      </header>

      <main style={styles.main}>
        <div style={styles.card}>
          <h1 style={{ ...styles.title, color: primaryColor }}>PowerPoint Agent</h1>
          <p style={styles.subtitle}>
            Describe your presentation and let AI do the rest.
          </p>

          <textarea
            style={styles.textarea}
            placeholder="e.g. A 5-slide overview of renewable energy trends in 2025..."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={loading}
            aria-label="Presentation prompt"
          />

          <button
            style={{
              ...styles.generateButton,
              backgroundColor: loading ? '#a5b4fc' : primaryColor,
              cursor: loading ? 'not-allowed' : 'pointer',
            }}
            onClick={handleGenerate}
            disabled={loading}
          >
            {loading ? '⏳ Generating...' : '✨ Generate Presentation'}
          </button>

          {error && <div style={styles.errorBox}>{error}</div>}

          {downloadUrl && (
            <div style={styles.successBox}>
              <span>✅ Your presentation is ready!</span>
              <a
                href={downloadUrl}
                download="presentation.pptx"
                style={styles.downloadLink}
              >
                Download ↓
              </a>
            </div>
          )}

          {!apiKey && (
            <p style={styles.hint}>
              💡 Add your Qwen API key in{' '}
              <span
                style={{ color: primaryColor, cursor: 'pointer', textDecoration: 'underline' }}
                onClick={() => navigate('/settings')}
              >
                Settings
              </span>{' '}
              to get started.
            </p>
          )}
        </div>
      </main>
    </div>
  );
};

export default HomePage;
