import axios, { AxiosError } from "axios"

export const API_V1_PREFIX = "/api/v1"
const API_BASE = import.meta.env.VITE_API_URL ?? ""

export const api = axios.create({
  baseURL: `${API_BASE}${API_V1_PREFIX}`,
  headers: { "Content-Type": "application/json" },
})

/** Absolute URL for a backend path outside /api/v1 (e.g. /media/files/...). */
export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

/** Wire shapes mirroring backend app/core/responses.py. */
export interface SuccessResponse<T> {
  success: true
  data: T
}

export interface ErrorDetail {
  code: string
  message: string
  field?: string | null
}

export interface ErrorResponse {
  success: false
  error: ErrorDetail
  request_id?: string | null
}

export interface PaginationMeta {
  page: number
  per_page: number
  total: number
  total_pages: number
}

export interface PaginatedResponse<T> {
  success: true
  data: T[]
  pagination: PaginationMeta
}

/** Non-2xx responses, with the envelope's error detail preserved. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string | null,
    public field?: string | null,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

api.interceptors.response.use(
  (res) => res,
  (err: AxiosError<ErrorResponse>) => {
    const res = err.response
    const body = res?.data
    if (res && body?.error) {
      return Promise.reject(
        new ApiError(
          res.status,
          body.error.code,
          body.error.message,
          body.request_id,
          body.error.field,
        ),
      )
    }
    return Promise.reject(err)
  },
)

/** The helpers below unwrap the success envelope — callers see only `data`. */

export async function apiGet<T>(path: string): Promise<T> {
  const res = await api.get<SuccessResponse<T>>(path)
  return res.data.data
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await api.post<SuccessResponse<T>>(path, body)
  return res.data.data
}

export async function apiDelete(path: string): Promise<void> {
  await api.delete(path)
}
