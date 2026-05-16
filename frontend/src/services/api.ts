import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || '';

export interface GenerateRequest {
  prompt: string;
  apiKey: string;
  primaryColor: string;
  accentColor: string;
}

/**
 * Calls the generate Lambda and returns a presigned download URL for the PPTX.
 */
export async function generatePresentation(req: GenerateRequest): Promise<string> {
  const response = await axios.post<{ downloadUrl: string }>(
    `${API_BASE_URL}/generate`,
    {
      prompt: req.prompt,
      apiKey: req.apiKey,
      primaryColor: req.primaryColor,
      accentColor: req.accentColor,
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
  const response = await axios.post<{ uploadUrl: string; publicUrl: string }>(
    `${API_BASE_URL}/upload-logo`,
    { fileType }
  );
  return response.data;
}
