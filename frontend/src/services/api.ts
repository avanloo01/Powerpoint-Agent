import axios from 'axios';
import { getAccessToken } from './supabase';

const GENERATE_URL = import.meta.env.VITE_GENERATE_URL || '';
const UPLOAD_URL = import.meta.env.VITE_UPLOAD_LOGO_URL || '';

export interface GenerateRequest {
  prompt: string;
}

/**
 * Calls the generate Lambda and returns a presigned download URL for the PPTX.
 */
export async function generatePresentation(req: GenerateRequest): Promise<string> {
  const token = await getAccessToken();
  const response = await axios.post<{ downloadUrl: string }>(
    GENERATE_URL,
    { prompt: req.prompt, fileIDs: req.fileIDs },
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );
  return response.data.downloadUrl;
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
    UPLOAD_URL,
    { fileType },
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );
  return response.data;
}

export async function uploadDocs(files: File[]): Promise<string[]> {
  const token = await getAccessToken();
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file, file.name));

  const response = await axios.post<{fileIDs: string[]}>(
    UPLOAD_URL,
    formData,
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );
  return response.data.fileIDs;
}
