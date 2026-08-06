/**
 * Token-based fetch API client for the creator gateway-settings panel.
 *
 * All calls go through `/api/...` — nginx (prod) and the vite dev proxy both
 * strip that prefix and forward to the FastAPI backend. The access token is
 * kept in localStorage and attached as `Authorization: Bearer <token>`.
 * A 401 clears the stored token and throws ApiError so the UI can bounce back
 * to the login page.
 */

const ACCESS_TOKEN_KEY = 'cc_access_token'
const REFRESH_TOKEN_KEY = 'cc_refresh_token'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function setTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, access)
  localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAccessToken()
  const headers: Record<string, string> = {
    ...((options.headers as Record<string, string>) ?? {}),
  }
  const body = options.body
  if (body && !(body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  let resp: Response
  try {
    resp = await fetch(`/api${path}`, { ...options, headers })
  } catch {
    throw new ApiError(0, 'Network error — is the API reachable?')
  }

  if (resp.status === 401) {
    clearTokens()
    throw new ApiError(401, 'Not authenticated')
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`
    try {
      const data = await resp.json()
      if (data && typeof data.detail === 'string') detail = data.detail
    } catch {
      /* keep the status text fallback */
    }
    throw new ApiError(resp.status, detail)
  }
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
}

export interface GatewayField {
  name: string
  label: string
  required: boolean
  secret: boolean
  placeholder: string
  options: string[]
  configured: boolean
}

export interface GatewaySettings {
  gateway: string
  label: string
  description: string
  enabled: boolean
  configured: boolean
  fields: GatewayField[]
}

export interface MessagingSettings {
  allow_messages_from_all_followers: boolean
}

export const api = {
  login(email: string, password: string): Promise<TokenResponse> {
    return request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
  },

  logout(refreshToken: string): Promise<void> {
    return request<void>('/auth/logout', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
  },

  getGatewaySettings(): Promise<GatewaySettings[]> {
    return request<GatewaySettings[]>('/creator/gateway-settings')
  },

  updateGateway(
    gateway: string,
    payload: { enabled: boolean; config: Record<string, string> },
  ): Promise<GatewaySettings> {
    return request<GatewaySettings>(`/creator/gateway-settings/${gateway}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },

  getMessagingSettings(): Promise<MessagingSettings> {
    return request<MessagingSettings>('/creator/messaging-settings')
  },

  updateMessagingSettings(allow: boolean): Promise<MessagingSettings> {
    return request<MessagingSettings>('/creator/messaging-settings', {
      method: 'PUT',
      body: JSON.stringify({ allow_messages_from_all_followers: allow }),
    })
  },
}
