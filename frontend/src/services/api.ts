import axios from 'axios';
import { getAccessToken } from './supabase';

const API_BASE_URL = import.meta.env.VITE_API_URL || '';

export interface GenerateRequest {
  prompt: string;
}

/**
 * Calls the generate Lambda and returns a presigned download URL for the PPTX.
 */
export async function generatePresentation(req: GenerateRequest): Promise<string> {
  const token = await getAccessToken();
  const response = await axios.post<{ downloadUrl: string }>(
    `${API_BASE_URL}/generate`,
    { prompt: req.prompt },
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
    `${API_BASE_URL}/upload-logo`,
    { fileType },
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );
  return response.data;
}
