import axios from 'axios';
import { getAccessToken, supabase } from './supabase';

const GENERATE_URL = import.meta.env.VITE_GENERATE_URL || '';
const UPLOAD_LOGO_URL = import.meta.env.VITE_UPLOAD_LOGO_URL || '';
const POLL_INTERVAL_MS = 5000;
const MAX_POLL_ATTEMPTS = 400;

export interface GenerateRequest {
  prompt: string;
  fileIDs?: string[];
}

export interface JobStatus {
  jobId: string;
  status: string;
  stageMessage: string;
  downloadUrl: string | null;
  errorMessage: string | null;
}

/**
 * Starts an async generation job and returns the job ID.
 */
export async function startGeneration(req: GenerateRequest): Promise<string> {
  const token = await getAccessToken();
  const response = await axios.post<{ jobId: string }>(
    GENERATE_URL,
    { prompt: req.prompt, fileIDs: req.fileIDs },
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );
  return response.data.jobId;
}

/**
 * Polls the Supabase jobs table until the job completes or fails.
 * Calls onProgress with each status update so the UI can show progress.
 */
export async function pollJobStatus(
  jobId: string,
  onProgress?: (status: JobStatus) => void
): Promise<JobStatus> {
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const userId = user?.id;
  if (!userId) throw new Error('Not authenticated');

  for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
    const { data, error } = await supabase
      .from('jobs')
      .select('status, stage_message, download_url, error_message')
      .eq('id', jobId)
      .eq('user_id', userId)
      .maybeSingle();

    if (error) throw error;
    if (!data) throw new Error('Job not found');

    const status: JobStatus = {
      jobId,
      status: data.status,
      stageMessage: data.stage_message ?? '',
      downloadUrl: data.download_url ?? null,
      errorMessage: data.error_message ?? null,
    };

    onProgress?.(status);

    if (status.status === 'done') return status;
    if (status.status === 'error') throw new Error(status.errorMessage || 'Generation failed');

    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }

  throw new Error('Generation timed out. Please try again.');
}

/**
 * Fetches a presigned PUT URL for uploading a logo to S3.
 * Returns the upload URL and the resulting public URL of the logo.
 */
export async function getLogoUploadUrl(
  fileType: string
): Promise<{ uploadUrl: string; publicUrl: string }> {
  const token = await getAccessToken();
  const response = await axios.post<{ uploadUrl: string; publicUrl: string }>(
    UPLOAD_LOGO_URL,
    { fileType },
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );
  return response.data;
}

/**
 * Deletes the current user's logo from S3 via the upload_logo Lambda.
 */
export async function deleteLogo(): Promise<void> {
  const token = await getAccessToken();
  await axios.delete(UPLOAD_LOGO_URL, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
}

/**
 * Uploads one or more documents directly to Dashscope's Files API
 * via the upload_logo Lambda (multipart/form-data mode).
 * Returns Dashscope file IDs that can be passed to startGeneration as fileIDs.
 */
export async function uploadDocs(files: File[]): Promise<string[]> {
  const token = await getAccessToken();
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file, file.name);
  }

  const response = await axios.post<{ fileIDs: string[] }>(
    UPLOAD_LOGO_URL,
    formData,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'multipart/form-data',
      },
    }
  );

  return response.data.fileIDs;
}
