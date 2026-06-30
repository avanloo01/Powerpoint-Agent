import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { startGeneration, pollJobStatus, uploadDocs, type JobStatus } from '../services/api';
import { getCurrentUserSettings, hasStoredSession, supabase } from '../services/supabase';

const STAGE_EMOJIS: Record<string, string> = {
  pending: '⏳',
  researching: '🔍',
  structuring: '📐',
  building: '🔨',
  done: '✅',
  error: '❌',
};

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [downloadUrl, setDownloadUrl] = useState('');
  const [stageMessage, setStageMessage] = useState('');
  const [isLoggedIn, setIsLoggedIn] = useState(hasStoredSession);
  const [primaryColor, setPrimaryColor] = useState('#C00000');
  const [hasApiKey, setHasApiKey] = useState(false);

  // Document upload state
  const docInputRef = useRef<HTMLInputElement>(null);
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [docFileIDs, setDocFileIDs] = useState<string[]>([]);
  const [uploadingDocs, setUploadingDocs] = useState(false);
  const [docError, setDocError] = useState('');
  const [docDragging, setDocDragging] = useState(false);
  const [docUploadStatus, setDocUploadStatus] = useState('');

  useEffect(() => {
    const hydrate = async () => {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      const loggedIn = Boolean(user);
      setIsLoggedIn(loggedIn);

      if (!loggedIn) {
        setHasApiKey(false);
        setPrimaryColor('#C00000');
        return;
      }

      const settings = await getCurrentUserSettings();
      setHasApiKey(Boolean(settings?.api_key));
      setPrimaryColor(settings?.primary_color || '#C00000');
    };

    void hydrate();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange(() => {
      void hydrate();
    });

    return () => subscription.unsubscribe();
  }, []);

  const handleDocUpload = async (newFiles: File[]) => {
    if (!newFiles.length) return;

    const validFiles = newFiles.filter((f) => {
      const ext = f.name.split('.').pop()?.toLowerCase() || '';
      return ['pdf', 'doc', 'docx', 'txt', 'md', 'csv'].includes(ext);
    });

    if (!validFiles.length) {
      setDocError('Supported formats: PDF, DOCX, TXT, MD, CSV');
      return;
    }

    setDocError('');
    setDocUploadStatus('');
    setUploadingDocs(true);

    try {
      setDocUploadStatus('Uploading documents\u2026');
      const ids = await uploadDocs(validFiles);
      setDocFiles((prev) => [...prev, ...validFiles]);
      setDocFileIDs((prev) => [...prev, ...ids]);
      setDocUploadStatus(`\u2705 ${ids.length} document${ids.length !== 1 ? 's' : ''} uploaded`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Document upload failed.';
      setDocError(msg);
      setDocUploadStatus('');
    } finally {
      setUploadingDocs(false);
    }
  };

  const handleDocDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDocDragging(false);
    void handleDocUpload(Array.from(e.dataTransfer.files));
  };

  const handleRemoveDocs = () => {
    setDocFiles([]);
    setDocFileIDs([]);
    setDocUploadStatus('');
    setDocError('');
    if (docInputRef.current) docInputRef.current.value = '';
  };

  const handleRemoveSingleDoc = (index: number) => {
    setDocFiles((prev) => prev.filter((_, i) => i !== index));
    setDocFileIDs((prev) => prev.filter((_, i) => i !== index));
  };

  const handleGenerate = async () => {
    if (!prompt.trim()) {
      setError('Please enter a prompt first.');
      return;
    }
    if (!isLoggedIn) {
      setError('Please log in from Settings before generating a presentation.');
      return;
    }
    if (!hasApiKey) {
      setError('No API key found for your account. Please add your Qwen API key in Settings.');
      return;
    }

    setError('');
    setDownloadUrl('');
    setStageMessage('');
    setLoading(true);

    try {
      const jobId = await startGeneration({
        prompt,
        fileIDs: docFileIDs.length ? docFileIDs : undefined,
      });
      setStageMessage('⏳ Queuing job\u2026');

      const result = await pollJobStatus(jobId, (status: JobStatus) => {
        const emoji = STAGE_EMOJIS[status.status] || '⏳';
        setStageMessage(`${emoji} ${status.stageMessage}`);
      });

      setDownloadUrl(result.downloadUrl ?? '');
      setStageMessage('Your presentation is ready!');
      // Clear docs after successful generation
      setDocFiles([]);
      setDocFileIDs([]);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'An unexpected error occurred.';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-100">
      <header className="flex items-center justify-end bg-white px-6 py-4 shadow-sm">
        <button
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          onClick={() => navigate('/settings')}
          aria-label="Open settings"
        >
          {isLoggedIn ? 'Settings' : 'Login'}
        </button>
      </header>

      <main className="mx-auto flex min-h-[calc(100vh-72px)] w-full max-w-3xl items-center px-6 py-10">
        <div className="w-full rounded-2xl bg-white p-8 shadow-xl md:p-12">
          <h1 className="mb-2 text-center text-3xl font-bold" style={{ color: primaryColor }}>
            PowerPoint Agent
          </h1>
          <p className="mb-8 text-center text-[15px] text-slate-500">
            Describe your presentation and let AI do the rest.
          </p>

          <textarea
            className="min-h-[120px] w-full resize-y rounded-xl border border-slate-200 px-4 py-3 text-[15px] leading-relaxed outline-none transition focus:border-slate-400"
            placeholder="e.g. A 5-slide overview of renewable energy trends in 2025..."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={loading}
            aria-label="Presentation prompt"
          />

          {/* Document upload */}
          <div className="mt-4">
            <p className="mb-1.5 text-sm font-medium text-slate-700">
              Reference documents <span className="font-normal text-slate-400">(optional)</span>
            </p>
            <div
              className={[
                'cursor-pointer rounded-xl border-2 border-dashed border-slate-300 px-4 py-4 text-center transition',
                docDragging ? 'border-indigo-500 bg-indigo-50' : '',
                uploadingDocs ? 'opacity-60 cursor-not-allowed' : '',
              ].join(' ')}
              onClick={() => !uploadingDocs && docInputRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDocDragging(true); }}
              onDragLeave={() => setDocDragging(false)}
              onDrop={handleDocDrop}
              role="button"
              tabIndex={0}
              aria-label="Upload reference documents"
              onKeyDown={(e) => e.key === 'Enter' && !uploadingDocs && docInputRef.current?.click()}
            >
              {docFiles.length > 0 ? (
                <ul className="space-y-0.5 text-left text-sm text-slate-600">
                  {docFiles.map((f, i) => (
                    <li key={`${f.name}-${i}`} className="flex items-center justify-between gap-2 truncate">
                      <span className="truncate">📄 {f.name}</span>
                      <button
                        type="button"
                        className="shrink-0 text-slate-400 hover:text-red-500"
                        onClick={(e) => { e.stopPropagation(); handleRemoveSingleDoc(i); }}
                        aria-label={`Remove ${f.name}`}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <>
                  <p className="text-sm text-slate-500">Click or drag and drop documents here</p>
                  <p className="mt-0.5 text-xs text-slate-400">PDF, DOCX, TXT, MD, CSV supported</p>
                </>
              )}
              <input
                ref={docInputRef}
                type="file"
                accept=".pdf,.doc,.docx,.txt,.md,.csv"
                multiple
                style={{ display: 'none' }}
                onChange={(e) => void handleDocUpload(Array.from(e.target.files || []))}
              />
            </div>

            {uploadingDocs && (
              <p className="mt-1.5 text-xs text-slate-500">⏳ {docUploadStatus}</p>
            )}
            {!uploadingDocs && docUploadStatus && (
              <div className="mt-1.5 flex items-center justify-between">
                <p className="text-xs text-emerald-600">{docUploadStatus}</p>
                <button
                  type="button"
                  className="ml-3 text-xs text-slate-400 underline hover:text-slate-600"
                  onClick={handleRemoveDocs}
                >
                  Remove
                </button>
              </div>
            )}
            {docError && (
              <p className="mt-1.5 text-xs text-red-500">{docError}</p>
            )}
          </div>

          <button
            className="mt-4 w-full rounded-xl px-4 py-3 text-base font-semibold text-white transition disabled:cursor-not-allowed"
            style={{
              backgroundColor: loading || uploadingDocs ? '#b84040' : primaryColor,
              cursor: loading || uploadingDocs ? 'not-allowed' : 'pointer',
            }}
            onClick={handleGenerate}
            disabled={loading || uploadingDocs}
          >
            {loading ? 'Generating...' : uploadingDocs ? 'Uploading…' : 'Generate Presentation'}
          </button>

          {loading && stageMessage && (
            <div className="mt-4 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-600">
              {stageMessage}
            </div>
          )}

          {error && (
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
              {error}
            </div>
          )}

          {downloadUrl && (
            <div className="mt-4 flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-600">
              <span>Your presentation is ready!</span>
              <a
                href={downloadUrl}
                download="presentation.pptx"
                className="font-semibold text-indigo-600 hover:text-indigo-700"
              >
                Download ↓
              </a>
            </div>
          )}

          {(!isLoggedIn || !hasApiKey) && (
            <p className="mt-3 text-center text-xs text-slate-400">
              {isLoggedIn ? '💡 Add your Qwen API key in ' : '💡 Log in from '}
              <span
                className="cursor-pointer underline"
                style={{ color: primaryColor }}
                onClick={() => navigate('/settings')}
              >
                {isLoggedIn ? 'Settings' : 'Login'}
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
