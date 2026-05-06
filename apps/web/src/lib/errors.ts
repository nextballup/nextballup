import type { ApiErrorBody } from "./contract";

/**
 * Thrown by the API helpers so every caller sees a consistent shape whether
 * the backend returned a structured envelope or the network died mid-flight.
 * UI code inspects `.code` for branching logic ("that was a validation
 * failure") and shows `.message` to the user verbatim.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;
  readonly requestId?: string;
  readonly retryAfterMs?: number;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: Record<string, unknown>,
    requestId?: string,
    retryAfterMs?: number,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
    this.retryAfterMs = retryAfterMs;
  }
}

function parseRetryAfterMs(value: string | null): number | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  const seconds = Number(trimmed);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.round(seconds * 1000);
  }
  const timestamp = Date.parse(trimmed);
  if (Number.isNaN(timestamp)) return undefined;
  return Math.max(0, timestamp - Date.now());
}

export async function toApiError(
  response: Response,
  fallbackMessage = "Request failed",
): Promise<ApiError> {
  let body: ApiErrorBody | null = null;
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    body = null;
  }
  const error = body?.error;
  return new ApiError(
    response.status,
    error?.code ?? `HTTP_${response.status}`,
    error?.message ?? fallbackMessage,
    error?.details,
    body?.request_id,
    response.status === 429
      ? parseRetryAfterMs(response.headers.get("Retry-After"))
      : undefined,
  );
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

export function isEmailVerificationRequiredError(error: ApiError): boolean {
  return (
    error.status === 403 &&
    (error.details?.reason === "email_unverified" ||
      error.message.toLowerCase().includes("email verification"))
  );
}
