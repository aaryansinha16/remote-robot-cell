import type { Job, JobDetail, NewJob, Robot } from './types'

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function describe(body: unknown): string | null {
  if (!body || typeof body !== 'object' || !('detail' in body)) return null
  const { detail } = body as { detail: unknown }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item: { msg?: string }) => (item.msg ?? '').replace(/^Value error, /, ''))
      .filter(Boolean)
      .join(', ')
  }
  return null
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(describe(body) ?? response.statusText, response.status)
  }
  return body as T
}

export const api = {
  createJob: (job: NewJob, idempotencyKey: string) =>
    request<Job>('/jobs', {
      method: 'POST',
      body: JSON.stringify(job),
      headers: { 'Idempotency-Key': idempotencyKey },
    }),
  cancelJob: (jobId: string) => request<Job>(`/jobs/${jobId}/cancel`, { method: 'POST' }),
  jobDetail: (jobId: string) => request<JobDetail>(`/jobs/${jobId}`),
  clearFinished: () => request<{ cleared: number }>('/jobs/finished', { method: 'DELETE' }),
  enroll: (robotId: string) => request<Robot>(`/robots/${robotId}/enroll`, { method: 'POST' }),
  unenroll: (robotId: string) => request<Robot>(`/robots/${robotId}/unenroll`, { method: 'POST' }),
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong'
}

export function newRequestKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}
